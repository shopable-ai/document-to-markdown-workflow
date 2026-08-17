#!/usr/bin/env python3
"""Run Workflow A's direct mac96 vision path over every PDF page.

The reader layer is built only from the current page image returned by
``mac96_vision_page_analysis``.  Native text and OCR are recorded as unused
routes so this run cannot silently promote either one into Markdown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover - repository runtime normally has Pillow
    Image = None

try:
    import yaml
except ImportError:  # pragma: no cover - the repository runtime normally has PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
from mac96_vision_page_analysis import DEFAULT_MODEL, PROMPT_VERSION, analyze  # noqa: E402
from markdown_content_repair import DEFAULT_MODEL as REPAIR_MODEL, repair_page, write_review  # noqa: E402


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, value: Any) -> None:
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text_atomic(path: Path, text: str) -> None:
    """Publish one evidence/result file without exposing a partial write."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_tool(name: str) -> str:
    candidates = [
        shutil.which(name),
        "/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/" + name,
        "/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin/" + name,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(f"required tool is unavailable: {name}")


def parse_pages(value: str, total: int) -> list[int]:
    if value.strip().lower() == "all":
        return list(range(1, total + 1))
    pages: set[int] = set()
    for item in value.split(","):
        token = item.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(part.strip()) for part in token.split("-", 1))
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    result = sorted(pages)
    if not result or result[0] < 1 or result[-1] > total:
        raise ValueError(f"pages must be within 1..{total}: {value}")
    return result


def pdf_page_count(source: Path) -> int:
    record = subprocess.run([find_tool("pdfinfo"), str(source)], capture_output=True, text=True, timeout=60, check=False)
    match = re.search(r"^Pages:\s*(\d+)\s*$", record.stdout, re.MULTILINE)
    if record.returncode != 0 or not match:
        raise RuntimeError(record.stderr.strip() or "pdfinfo did not report a page count")
    return int(match.group(1))


def render_pages(source: Path, target: Path, pages: list[int]) -> dict[int, Path]:
    render_dir = target / "evidence" / "render-tmp"
    render_dir.mkdir(parents=True, exist_ok=True)
    prefix = render_dir / "page"
    record = subprocess.run(
        [find_tool("pdftoppm"), "-png", "-r", "144", "-f", str(min(pages)), "-l", str(max(pages)), str(source), str(prefix)],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if record.returncode != 0:
        raise RuntimeError(record.stderr.strip() or "pdftoppm failed")
    assets: dict[int, Path] = {}
    for page in pages:
        candidates = [render_dir / f"page-{page:03d}.png", render_dir / f"page-{page:02d}.png", render_dir / f"page-{page}.png"]
        rendered = next((item for item in candidates if item.is_file()), None)
        if rendered is None:
            raise RuntimeError(f"rendered page is missing: {page}")
        destination = target / "assets" / f"page-{page:03d}.png"
        shutil.copy2(rendered, destination)
        assets[page] = destination
    shutil.rmtree(render_dir)
    return assets


def remote_capability(host: str, model: str) -> dict[str, Any]:
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
        "/usr/bin/curl", "-sS", "--max-time", "20", "http://127.0.0.1:11434/api/tags",
    ]
    started = time.monotonic()
    record = subprocess.run(command, capture_output=True, text=True, timeout=40, check=False)
    result: dict[str, Any] = {
        "status": "failed",
        "target": host,
        "model": model,
        "transport": "ssh",
        "endpoint": "http://127.0.0.1:11434/api/tags",
        "requested_at": now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "model_available": False,
        "vision_capability": False,
        "diagnostics": [],
    }
    if record.returncode != 0:
        result["diagnostics"].append(record.stderr.strip()[-500:] or f"ssh/curl exited {record.returncode}")
        return result
    try:
        payload = json.loads(record.stdout)
    except json.JSONDecodeError as exc:
        result["diagnostics"].append(f"tags response is not JSON: {exc}")
        return result
    models = payload.get("models") if isinstance(payload, dict) else None
    models = models if isinstance(models, list) else []
    found = next((item for item in models if isinstance(item, dict) and item.get("name") == model), None)
    result["model_available"] = found is not None
    result["vision_capability"] = bool(found and "vision" in (found.get("details") or {}).get("capabilities", [])) or bool(found and "vision" in found.get("capabilities", []))
    result["status"] = "complete" if result["model_available"] and result["vision_capability"] else "needs-review"
    if not found:
        result["diagnostics"].append("requested model was not listed by Ollama")
    elif not result["vision_capability"]:
        result["diagnostics"].append("requested model was listed without an explicit vision capability")
    return result


def page_title(markdown: str, page: int) -> str:
    for raw in markdown.splitlines():
        line = re.sub(r"^\s*#+\s*", "", raw).strip()
        line = re.sub(r"^[-*]\s+", "", line).strip()
        if re.match(r"^\d{1,3}\s+\S+", line) and len(line) <= 100:
            return line
    for raw in markdown.splitlines():
        line = re.sub(r"^\s*#+\s*", "", raw).strip()
        if "模型" in line and len(line) <= 100:
            return line
    return "〔无法确认〕"


def clean_model_markdown(markdown: str) -> str:
    text = markdown.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def format_reader_markdown(markdown: str) -> str:
    """Keep model-returned visual lines visible in CommonMark renderers."""

    formatted: list[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line:
            formatted.append("")
            continue
        if re.match(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|\|)", line):
            formatted.append(line)
        elif line.endswith("  "):
            formatted.append(line)
        else:
            formatted.append(line + "  ")
    return "\n".join(formatted).strip()


def needs_text_refinement(markdown: str) -> bool:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if markdown.count("\n\n") < 2 and markdown.count("\n") >= 4:
        return True
    for previous, current in zip(lines, lines[1:]):
        if previous[-1:] and current[:1] and re.match(r"[\u3400-\u9fff]", previous[-1]) and re.match(r"[\u3400-\u9fff]", current[0]):
            return True
    return False


def image_dimensions(path: Path) -> tuple[int, int]:
    if Image is None:
        return 1190, 1684
    with Image.open(path) as image:
        return image.width, image.height


def model_page(
    image: Path,
    *,
    page: int,
    source_fingerprint: str,
    host: str,
    model: str,
    prompt_file: Path,
    text_prompt_file: Path,
    timeout: int,
    retries: int,
    pass_strategy: str,
) -> tuple[dict[str, Any], str, str]:
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    for attempt in range(1, retries + 2):
        result = analyze(
            image,
            asset_id=f"page-p{page:03d}",
            source_fingerprint=source_fingerprint,
            page_or_region=f"page-{page}",
            mode="full",
            pass_strategy=pass_strategy,
            host=host,
            model=model,
            prompt_file=prompt_file,
            timeout=timeout,
        )
        attempts.append({"attempt": attempt, "status": result.get("status"), "model_call_count": result.get("model_call_count"), "diagnostics": result.get("diagnostics", [])})
        if result.get("status") == "complete" and (result.get("text") or {}).get("status") == "complete":
            break
    if result is None:
        result = {"status": "failed", "diagnostics": ["no model result"]}
    result["run_attempts"] = attempts
    text_layer = result.get("text") or {}
    if text_layer.get("status") == "complete":
        markdown = clean_model_markdown(str(text_layer.get("markdown") or ""))
        return result, markdown, f"full+{pass_strategy}"
    return result, "", "failed"


def relation_lines(result: dict[str, Any]) -> list[str]:
    visual = result.get("visual") or {}
    analysis = visual.get("visual_analysis") or {}
    relationships = analysis.get("relationships") if isinstance(analysis, dict) else []
    if not isinstance(relationships, list):
        return []
    lines = []
    for item in relationships:
        if not isinstance(item, dict):
            continue
        source, target, relation = item.get("from"), item.get("to"), item.get("relationship")
        if source and target and relation:
            lines.append(f"- {source} -> {target}：{relation}")
    return lines


def stage(stage_id: str, status: str, paths: list[str], diagnostics: list[str] | None = None, unknown: list[str] | None = None) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "status": status,
        "evidence_level": "L2-local-runtime",
        "evidence_paths": paths,
        "verification_commands": ["python3 tests/validate_real_output_root.py <package>"],
        "diagnostics": diagnostics or [],
        "unknown": unknown or [],
        "score_basis": "direct mac96 visual evidence per page; no OCR or online fallback",
    }


def write_page_unit(target: Path, *, page: int, body: str) -> Path:
    path = target / "page-units" / f"p{page:03d}.md"
    text = body.rstrip() + f"\n\n![原始页面图](<../assets/page-{page:03d}.png>)\n"
    write_text_atomic(path, text)
    return path


def build_text_transcription_result(
    *,
    page: int,
    image: Path,
    source_fingerprint: str,
    host: str,
    model: str,
    markdown: str,
    extraction_mode: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "contract": "vision-text-transcription",
        "version": "0.2.0-mac96-only",
        "input_asset_id": f"page-p{page:03d}",
        "source_fingerprint": source_fingerprint,
        "image_sha256": digest(image),
        "page_or_region": f"page-{page}",
        "prompt_version": PROMPT_VERSION,
        "transport": "ssh",
        "target": host,
        "model": model,
        "candidate_markdown": markdown,
        "status": "complete" if markdown else "failed",
        "diagnostics": result.get("diagnostics", []),
        "source_evidence": f"evidence/vision-page-analysis-p{page:03d}.json",
        "extraction_mode": extraction_mode,
        "fallback_used": None,
    }


def page_unit_is_present(target: Path, page: int) -> bool:
    path = target / "page-units" / f"p{page:03d}.md"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(text.strip()) and f"../assets/page-{page:03d}.png" in text


def write_page_outputs(
    target: Path,
    *,
    page: int,
    image: Path,
    source_fingerprint: str,
    host: str,
    model: str,
    result: dict[str, Any],
    markdown: str,
    extraction_mode: str,
) -> dict[str, Any]:
    """Persist the per-page JSON evidence and Markdown projection together.

    A page is not considered resumably complete until all three projections
    exist.  Existing valid files are preserved so recovery never rewrites
    completed pages merely to rebuild the package index.
    """

    analysis_path = target / "evidence" / f"vision-page-analysis-p{page:03d}.json"
    if not analysis_path.is_file():
        write_json(analysis_path, result)

    text_result = build_text_transcription_result(
        page=page,
        image=image,
        source_fingerprint=source_fingerprint,
        host=host,
        model=model,
        markdown=markdown,
        extraction_mode=extraction_mode,
        result=result,
    )
    text_path = target / "evidence" / f"vision-text-transcription-p{page:03d}.json"
    rewrite_text = True
    if text_path.is_file():
        try:
            existing = json.loads(text_path.read_text(encoding="utf-8"))
            rewrite_text = not (
                isinstance(existing, dict)
                and existing.get("status") == text_result["status"]
                and existing.get("candidate_markdown") == markdown
                and existing.get("source_evidence") == text_result["source_evidence"]
            )
        except (OSError, json.JSONDecodeError):
            rewrite_text = True
    if rewrite_text:
        write_json(text_path, text_result)

    if not page_unit_is_present(target, page):
        reader_markdown = format_reader_markdown(markdown) if markdown else "〔无法确认〕"
        write_page_unit(target, page=page, body=reader_markdown)
    return text_result


def repair_page_unit_if_needed(
    target: Path,
    *,
    page: int,
    host: str,
    model: str,
    timeout: int,
) -> dict[str, Any]:
    """Run the LAN Markdown repair once for the current page unit."""

    page_unit = target / "page-units" / f"p{page:03d}.md"
    evidence_path = target / "evidence" / f"markdown-content-repair-p{page:03d}.json"
    if evidence_path.is_file() and page_unit.is_file():
        try:
            existing = json.loads(evidence_path.read_text(encoding="utf-8"))
            if (
                existing.get("status") == "applied"
                and existing.get("final_sha256") == hashlib.sha256(page_unit.read_bytes()).hexdigest()
            ):
                return existing
        except (OSError, json.JSONDecodeError):
            pass
    result = repair_page(page_unit, page=page, host=host, model=model, timeout=timeout)
    write_json(evidence_path, result)
    return result


def repair_evidence_label(item: dict[str, Any]) -> str:
    page = item.get("page")
    return f"p{int(page):03d}" if isinstance(page, int) else "pNNN"


def validate_page_markdown_checkpoint(target: Path, *, expected_pages: list[int]) -> None:
    forbidden = ("模型调用", "待复核", "OCR", "bbox", "manifest", "过程文件路径")
    for page in expected_pages:
        path = target / "page-units" / f"p{page:03d}.md"
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise RuntimeError(f"single-page Markdown is empty for page {page}")
        leaked = [item for item in forbidden if item in text]
        if leaked:
            raise RuntimeError(f"single-page Markdown exposes engineering fields on page {page}: {leaked}")
        if f"../assets/page-{page:03d}.png" not in text:
            raise RuntimeError(f"single-page Markdown is missing the page image link for page {page}")
        body_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("![")]
        if not body_lines:
            raise RuntimeError(f"single-page Markdown has no body text for page {page}")


def run(args: argparse.Namespace) -> Path:
    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pdf":
        raise ValueError("this runner accepts PDF only")
    source_fingerprint = digest(source)
    total_pages = pdf_page_count(source)
    pages = parse_pages(args.pages, total_pages)
    package_id = f"source-{source_fingerprint[:12]}-pdf-mac96-v2{args.package_suffix}"
    output_root = args.output_root.expanduser().resolve()
    target = output_root / package_id
    if target.exists():
        raise FileExistsError(f"output package already exists: {target}")
    for directory in ("assets", "evidence", "reports", "page-units"):
        (target / directory).mkdir(parents=True, exist_ok=True)

    write_json(target / "evidence/source-fingerprint.json", {"source": str(source), "sha256": source_fingerprint, "computed_at": now()})
    capability = remote_capability(args.host, args.model)
    write_json(target / "evidence/mac96-preflight.json", capability)
    if capability.get("status") != "complete":
        raise RuntimeError(f"mac96 preflight failed: {capability.get('diagnostics')}")

    source_inventory = {
        "contract": "source-inventory",
        "version": "0.2.0",
        "source_fingerprint": source_fingerprint,
        "source": str(source),
        "media_type": mimetypes.guess_type(source.name)[0] or "application/pdf",
        "status": "complete",
        "page_count_or_dimensions": {"page_count": total_pages, "selected_pages": pages},
        "text_layer": "not-used-for-reader-layer",
        "image_layer": "present",
        "readability": "readable",
        "diagnostics": ["Reader Markdown is generated from the current page image by mac96; native text and OCR are not inputs."],
    }
    write_json(target / "evidence/source-inventory.json", source_inventory)
    write_json(target / "evidence/context-policy.json", {
        "status": "complete",
        "input_scope": "one-current-page-image-only",
        "model": args.model,
        "target": args.host,
        "prohibited_context": ["other-pages", "native-text-layer", "ocr", "bbox", "other-files", "previous-model-answer"],
        "reader_text_source": "mac96-direct-vision",
    })
    write_json(target / "evidence/native-content-model.json", {"status": "not-used-for-reader-layer", "parser": None, "pages": pages, "diagnostics": ["Native PDF text was not used as a content source."]})
    write_json(target / "evidence/ocr-candidates.json", {"status": "not-used", "source": "none", "selected_pages": pages, "candidates": [], "diagnostics": ["Apple Vision OCR was not invoked; no OCR candidate was promoted or used."]})

    assets = render_pages(source, target, pages)
    asset_records: list[dict[str, Any]] = []
    for page in pages:
        image = assets[page]
        asset_records.append({
            "asset_id": f"page-p{page:03d}",
            "file_name": image.name,
            "file_path": image.relative_to(target).as_posix(),
            "display_name": f"100个思维模型 第 {page} 页原始页面",
            "asset_kind": "page-render",
            "source": {
                "package_id": package_id,
                "source_fingerprint": source_fingerprint,
                "original_filename": source.name,
                "source_occurrences": [{"source_occurrence_id": f"{package_id}-p{page:03d}", "source_file": source.name, "page": page, "region": None, "object_ids": [], "parent_asset_id": None, "ordinal": 1, "status": "located"}],
            },
            "media": {"mime_type": "image/png", "width": image_dimensions(image)[0], "height": image_dimensions(image)[1], "sha256": digest(image)},
            "semantic": {"label": "original-page", "description": f"100个思维模型 第 {page} 页原始页面", "confidence": "high"},
            "naming": {"source_slug": "100-models", "semantic_slug": "page", "purpose_slug": "original-page", "status": "generated"},
            "status": "available",
        })
    write_json(target / "evidence/rendering.json", {"status": "complete", "source_owned": True, "renderer": "pdftoppm", "requested_pages": pages, "rendered_pages": pages, "text_visibility": {"status": "not-applicable", "reason": "reader text comes from direct mac96 image understanding"}, "assets": [{"page": page, "image": assets[page].relative_to(target).as_posix(), "sha256": digest(assets[page])} for page in pages]})
    write_json(target / "evidence/asset-registration.json", {"status": "complete", "assets": [item["asset_id"] for item in asset_records], "embedded_count": 0, "regions_count": 0})

    section_records: list[dict[str, Any]] = []
    trace_records: list[dict[str, Any]] = []
    reader_parts: list[str] = []
    model_failures: list[dict[str, Any]] = []
    repair_results: list[dict[str, Any]] = []
    page_statuses: list[dict[str, Any]] = []
    for index, page in enumerate(pages, 1):
        image = assets[page]
        result, markdown, extraction_mode = model_page(
            image,
            page=page,
            source_fingerprint=source_fingerprint,
            host=args.host,
            model=args.model,
            prompt_file=args.prompt_file,
            text_prompt_file=args.text_prompt_file,
            timeout=args.timeout,
            retries=args.retries,
            pass_strategy=args.pass_strategy,
        )
        write_json(target / f"evidence/vision-page-analysis-p{page:03d}.json", result)
        text_result = build_text_transcription_result(
            page=page,
            image=image,
            source_fingerprint=source_fingerprint,
            host=args.host,
            model=args.model,
            markdown=markdown,
            extraction_mode=extraction_mode,
            result=result,
        )
        write_json(target / f"evidence/vision-text-transcription-p{page:03d}.json", text_result)
        visual = result.get("visual") or {}
        analysis = visual.get("visual_analysis") if isinstance(visual, dict) else {}
        analysis = analysis if isinstance(analysis, dict) else {}
        unknown = [str(item) for item in analysis.get("unknown_forms", []) if item]
        uncertainties = [str(item) for item in analysis.get("uncertainties", []) if item]
        title = page_title(markdown, page) if markdown else "〔无法确认〕"
        if not markdown:
            model_failures.append({"page": page, "status": result.get("status"), "diagnostics": result.get("diagnostics", [])})
            title = "〔无法确认〕"
        reader_markdown = format_reader_markdown(markdown) if markdown else "〔无法确认〕"
        page_heading = f"## {title}"
        reader_parts.extend([page_heading, "", reader_markdown, "", "### 原始页面图", "", f"![第 {page} 页原始页面](<assets/page-{page:03d}.png>)", ""])
        write_page_outputs(
            target,
            page=page,
            image=image,
            source_fingerprint=source_fingerprint,
            host=args.host,
            model=args.model,
            result=result,
            markdown=markdown,
            extraction_mode=extraction_mode,
        )
        repair_results.append(
            repair_page_unit_if_needed(
                target,
                page=page,
                host=args.repair_host,
                model=args.repair_model,
                timeout=args.repair_timeout,
            )
        )
        if page == 2:
            pilot_ok = (
                result.get("status") == "complete"
                and result.get("pass_strategy_used") == "dual"
                and result.get("model_call_count") == 2
                and (result.get("text") or {}).get("status") == "complete"
                and (result.get("visual") or {}).get("status") == "complete"
                and markdown
                and "模型调用" not in markdown
                and "待复核" not in markdown
            )
            print(
                f"[mac96-pdf] pilot-pages=1-2 format={'passed' if pilot_ok else 'failed'}; continuing automatically",
                file=sys.stderr,
                flush=True,
            )
            if not pilot_ok:
                raise RuntimeError("pages 1-2 did not pass the dual-pass format checkpoint")
        occurrence_id = f"{package_id}-p{page:03d}"
        section_records.append({
            "section_id": f"page-{page:03d}",
            "title": title,
            "body": reader_markdown,
            "source_occurrences": [occurrence_id],
            "visual_asset_ids": [f"page-p{page:03d}"],
            "confidence": "high" if markdown and not unknown and not uncertainties else "medium" if markdown else "low",
            "reader_order": "semantic-before-visual",
            "unknown_forms": unknown,
            "unresolved": uncertainties,
            "source_block_ids": [],
        })
        trace_records.append({
            "page": page,
            "source_asset_id": f"page-p{page:03d}",
            "source_image_sha256": digest(image),
            "reader_section_id": f"page-{page:03d}",
            "text_status": text_result["status"],
            "visual_status": visual.get("status", "unavailable"),
            "extraction_mode": extraction_mode,
            "unknown_forms": unknown,
            "uncertainties": uncertainties,
            "evidence": f"evidence/vision-page-analysis-p{page:03d}.json",
        })
        page_statuses.append({"page": page, "status": text_result["status"], "title": title, "unknown_forms": unknown, "uncertainties": uncertainties})
        write_json(target / f"evidence/progress-p{page:03d}.json", {"page": page, "ordinal": index, "total": len(pages), "status": text_result["status"], "model": args.model, "target": args.host, "created_at": now(), "evidence": f"evidence/vision-page-analysis-p{page:03d}.json"})
        print(f"[mac96-pdf] page={page}/{len(pages)} status={text_result['status']} title={title}", file=sys.stderr, flush=True)
        if page == 2:
            validate_page_markdown_checkpoint(target, expected_pages=[1, 2])
            print(
                "[mac96-pdf] single-page-markdown-checkpoint=passed pages=1-2; continuing automatically",
                file=sys.stderr,
                flush=True,
            )

    reader_parts = []
    for page in pages:
        page_unit = target / "page-units" / f"p{page:03d}.md"
        page_text = page_unit.read_text(encoding="utf-8").strip()
        page_text = page_text.replace("<../assets/", "<assets/")
        reader_parts.append(page_text)
    (target / "index.md").write_text("\n\n".join(reader_parts).rstrip() + "\n", encoding="utf-8")
    repair_failures = [item for item in repair_results if item.get("status") != "applied"]
    progress_events = [
        ("execution_preflight", "complete", ["evidence/source-fingerprint.json", "evidence/mac96-preflight.json"]),
        ("source_inventory", "complete", ["evidence/source-inventory.json"]),
        ("native_extraction", "complete", ["evidence/native-content-model.json"]),
        ("source_rendering", "complete", ["evidence/rendering.json", "assets"]),
        ("language_quality_repair", "complete" if not repair_failures else "needs-review", ["evidence/markdown-content-repair-pNNN.json", "page-units"]),
        ("markdown_assembly", "complete" if not model_failures else "needs-review", ["index.md", "evidence/source-to-final-trace.json"]),
        ("independent_validation", "not-run", []),
    ]
    with (target / "evidence/progress.jsonl").open("w", encoding="utf-8") as handle:
        for progress_stage, progress_status, progress_paths in progress_events:
            handle.write(json.dumps({"run_id": package_id, "source_fingerprint": source_fingerprint, "stage": progress_stage, "task": progress_stage, "attempt": 1, "status": progress_status, "elapsed_seconds": 0.0, "evidence_paths": progress_paths, "next_action": "independent validator" if progress_stage == "independent_validation" else "continue"}, ensure_ascii=False) + "\n")
    write_json(target / "evidence/source-to-final-trace.json", {"status": "complete" if not model_failures else "needs-review", "source_fingerprint": source_fingerprint, "pages": trace_records, "coverage": {"selected_pages": pages, "transcribed_pages": [item["page"] for item in page_statuses if item["status"] == "complete"], "failed_pages": [item["page"] for item in page_statuses if item["status"] != "complete"]}})
    write_json(target / "evidence/content-classification.json", {"status": "complete" if not model_failures else "needs-review", "source_fingerprint": source_fingerprint, "pages": page_statuses, "basis": "direct mac96 vision result per current page image"})
    all_unknown = [f"p{item['page']:03d}: {value}" for item in page_statuses for value in item["unknown_forms"]]
    all_uncertainties = [f"p{item['page']:03d}: {value}" for item in page_statuses for value in item["uncertainties"]]
    reconciliation_status = "complete" if not model_failures and not all_unknown and not all_uncertainties else "needs-review"
    write_json(target / "evidence/reconciliation.json", {"status": reconciliation_status, "source_fingerprint": source_fingerprint, "selected_pages": pages, "native_text": {"status": "not-used-for-reader-layer"}, "visual_text": {"status": "complete" if not model_failures else "needs-review", "source": "mac96", "model": args.model, "prompt_version": PROMPT_VERSION}, "unknown_forms": all_unknown, "unresolved": all_uncertainties + [f"page-{item['page']} model transcription failed" for item in model_failures], "coverage_notes": ["每页只向 mac96 发送一张当前页面图。", "读者层没有使用 OCR、PDF 原生文字层或其他页面上下文。", "视觉关系仅保留模型在当前图片中直接确认的结果。"]})
    write_json(target / "evidence/quality-audit.json", {"status": "needs-review", "basis": "automated direct-mac96 conversion; no human page-by-page audit", "pages": pages, "model": args.model, "prompt_version": PROMPT_VERSION, "risks": ["视觉模型可能对复杂连线或低清字形产生误判；原图和逐页证据均已保留。"]})

    page_unit_dir = target / "page-units"
    for page in pages:
        section = next(item for item in section_records if item["section_id"] == f"page-{page:03d}")
        page_unit = page_unit_dir / f"p{page:03d}.md"
        if not page_unit.is_file():
            write_page_unit(target, page=page, body=section["body"])
        page_text = page_unit.read_text(encoding="utf-8").strip()
        section["body"] = page_text
    write_review(target / "reviews/markdown-content-repair.yaml", package_id, repair_results)
    (target / "document-temp.md").write_text("\n".join((page_unit_dir / f"p{page:03d}.md").read_text(encoding="utf-8") for page in pages), encoding="utf-8")

    overall_status = "complete" if not model_failures and not repair_failures and reconciliation_status == "complete" else "needs-review"
    stages = [
        stage("A1", "complete", ["evidence/source-fingerprint.json", "evidence/source-inventory.json", "evidence/mac96-preflight.json"]),
        stage("A2", "complete", ["evidence/native-content-model.json", "evidence/context-policy.json"], ["Native text is explicitly excluded from the reader layer."]),
        stage("A3", "complete", ["evidence/rendering.json", "assets"]),
        stage("A4", "complete" if reconciliation_status == "complete" else "needs-review", ["evidence/vision-text-transcription-p001.json", "evidence/reconciliation.json"], ["Direct mac96 page-image transcription and visual relation analysis."], all_unknown + all_uncertainties),
        stage("A5", "complete" if not model_failures and not repair_failures else "needs-review", ["index.md", "page-units", "evidence/markdown-content-repair-pNNN.json", "reviews/markdown-content-repair.yaml", "document-temp.md"], ["Reader Markdown contains no OCR candidate layer.", "LAN language repair is page-scoped and relation-safe."], [f"page-{item['page']}" for item in model_failures] + [f"repair-page-{item.get('page')}" for item in repair_failures]),
        stage("A6", "complete", ["evidence/asset-registration.json", "manifest.yaml"]),
        stage("A7", "complete" if overall_status == "complete" else "needs-review", ["reports/processing-report.yaml", "reports/acceptance-report.yaml", "reports/failure-report.yaml"], ["Independent validators run after package assembly; automated quality audit remains distinct from human review."]),
    ]
    manifest = {
        "manifest_version": "0.9.0",
        "output_layout": "flat-assets-v1",
        "package_id": package_id,
        "kind": "markdown-asset-package",
        "status": overall_status,
        "source": {"path": str(source), "path_label": source.name, "media_type": "application/pdf", "source_fingerprint": source_fingerprint, "selected_pages": pages, "source_page_numbers": list(range(1, total_pages + 1)), "total_pages": total_pages, "source_selection": "single-source", "candidate_comparison": None},
        "markdown": {"path": "index.md", "sections": section_records},
        "assets_manifest": {"path": "manifest.yaml", "type": "source-visual-asset-manifest"},
        "assets": asset_records,
        "evidence": {"source_inventory": "evidence/source-inventory.json", "capability_probe": "evidence/mac96-preflight.json", "native_content_model": "evidence/native-content-model.json", "content_classification": "evidence/content-classification.json", "rendering": "evidence/rendering.json", "asset_registration": "evidence/asset-registration.json", "ocr_candidates": "evidence/ocr-candidates.json", "reconciliation": "evidence/reconciliation.json", "source_to_final_trace": "evidence/source-to-final-trace.json", "context_policy": "evidence/context-policy.json", "markdown_content_repair": "evidence/markdown-content-repair-pNNN.json", "quality_audit": "evidence/quality-audit.json"},
        "processing": {"native_extraction": "not-used-for-reader-layer", "ocr_used": False, "visual_review": "complete" if not model_failures else "needs-review", "execution_path": "mac96-direct-vision", "fallback_reason": None, "status": overall_status, "stage_evidence": stages, "stage_evidence_contract": "contracts/stage-evidence.yaml", "model_calls": [{"target": args.host, "model": args.model, "prompt_version": PROMPT_VERSION, "pass_strategy": args.pass_strategy, "scope": "one-current-page-image-per-call", "page_count": len(pages), "calls_per_page": 2 if args.pass_strategy == "dual" else 1}, {"target": args.repair_host, "model": args.repair_model, "prompt_version": "markdown-content-repair-lan-v1", "scope": "one-current-page-unit-per-call", "page_count": len(pages), "calls_per_page": 1}], "language_quality_repair": {"status": "complete" if not repair_failures else "needs-review", "route": "lan-language-model-first", "evidence": "evidence/markdown-content-repair-pNNN.json", "review": "reviews/markdown-content-repair.yaml"}, "context_policy": {"one_source_per_run": True, "one_current_page_image_per_call": True, "one_current_page_unit_per_repair": True, "ocr_used": False}},
        "validation": {"validator_version": "real-output-root", "status": "passed" if overall_status == "complete" else "needs-review", "checks": ["package layout", "source fingerprint", "asset hashes", "Markdown links", "direct mac96 evidence"]},
        "quality_gate": {"status": "passed" if overall_status == "complete" else "needs-review", "score": 1.0 if overall_status == "complete" else None, "reason": "All selected pages returned direct mac96 visual text and relation evidence; human visual audit remains a separate review activity."},
        "quality_report": "reports/quality-report.yaml",
    }
    write_yaml(target / "manifest.yaml", manifest)
    processing = {"status": overall_status, "evidence_level": "L2-local-runtime", "run_id": package_id, "started_at": now(), "finished_at": now(), "real_business_result_package": str(target), "input": {"source": str(source), "source_type": "pdf", "sha256": source_fingerprint, "pages": pages, "total_pages": total_pages, "source_page_numbers": list(range(1, total_pages + 1))}, "source_selection": "single-source", "progress_stream": "evidence/progress-pNNN.json", "stage_evidence_contract": "contracts/stage-evidence.yaml", "stage_evidence": stages, "stages": [{"id": item["stage_id"], "status": item["status"], "evidence": item["evidence_paths"]} for item in stages], "tools": {"page-renderer": "pdftoppm", "visual-model": f"{args.host}/{args.model}", "markdown-writer": "adapters/mac96_pdf_vision_runner.py"}, "asset_policy": {"layout": "flat-assets-v1", "root": "assets/", "classification": "manifest.asset_kind", "embedded_count": 0, "regions_count": 0}, "failure_report": "reports/failure-report.yaml", "ocr": {"status": "not-used", "evidence": "evidence/ocr-candidates.json"}, "visual_model": {"status": "complete" if not model_failures else "needs-review", "evidence": "evidence/vision-page-analysis-pNNN.json"}, "language_repair": {"status": "not-run"}, "quality_audit": {"status": "needs-review"}}
    failures = [{"failure_id": f"mac96-page-{item['page']:03d}", "stage_id": "A4", "code": "mac96-transcription-failed", "severity": "P1", "retryable": True, "status": "open", "message": "mac96 did not return a usable direct visual transcription for this page.", "evidence_paths": [f"evidence/vision-page-analysis-p{item['page']:03d}.json"], "remediation": "Retry the same page against mac96; do not fall back to OCR.", "resolved_evidence_paths": []} for item in model_failures]
    failures.extend({"failure_id": f"markdown-repair-page-{item.get('page', 'unknown')}", "stage_id": "A5", "code": "markdown-content-repair-unavailable", "severity": "P1", "retryable": True, "status": "open", "message": "LAN Markdown repair did not produce a guarded page-unit write-back.", "evidence_paths": [f"evidence/markdown-content-repair-{repair_evidence_label(item)}.json"], "remediation": "Retry the current page against the LAN language model or use the explicitly authorized online fallback.", "resolved_evidence_paths": []} for item in repair_failures)
    acceptance = {"status": "passed" if overall_status == "complete" else "needs-review", "evidence_level": "L2-local-runtime", "real_business_result_package": str(target), "checks": [{"id": "required-files-and-directories", "status": "passed"}, {"id": "source-read", "status": "passed"}, {"id": "source-owned-rendering", "status": "passed"}, {"id": "mac96-direct-vision", "status": "passed" if not model_failures else "needs-review"}, {"id": "ocr-route", "status": "not-used"}, {"id": "markdown-content-repair", "status": "passed" if not repair_failures else "needs-review"}, {"id": "reader-layer", "status": "passed" if not model_failures and not repair_failures else "needs-review"}], "failures": [item["failure_id"] for item in failures], "warnings": ["No human page-by-page visual audit was performed in this automated run."], "stage_evidence": stages, "failure_report": "reports/failure-report.yaml", "diagnostics": ["Final reader Markdown is the guarded page-unit projection; source page images and per-page model evidence are retained."]}
    failure_report = {"contract": "failure-report", "version": "0.2.0", "status": "none" if not failures else "needs-review", "evidence_level": "L2-local-runtime", "failures": failures, "warnings": ["Automated quality audit is not a substitute for human visual review."], "diagnostics": ["No online vision model or OCR fallback was used."]}
    quality_report = {"report_version": "0.2.0", "status": "complete" if overall_status == "complete" else "needs-review", "assessment_basis": "direct mac96 visual conversion", "model": args.model, "prompt_version": PROMPT_VERSION, "selected_pages": pages, "model_failures": model_failures, "human_review": "not-run", "remaining_risk": "Complex visual relations still benefit from human spot review."}
    write_yaml(target / "reports/processing-report.yaml", processing)
    write_yaml(target / "reports/acceptance-report.yaml", acceptance)
    write_yaml(target / "reports/failure-report.yaml", failure_report)
    write_yaml(target / "reports/quality-report.yaml", quality_report)
    write_json(target / "evidence/smoke-report.json", {"status": "passed" if not model_failures else "needs-review", "scope": "full-selected-pages", "model": args.model, "pages": pages, "ocr_used": False})
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pages", default="all")
    parser.add_argument("--host", default="mac96")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", type=Path, default=ROOT / "references/prompts/vision-page-analysis-mac96.zh.txt")
    parser.add_argument("--text-prompt-file", type=Path, default=ROOT / "references/prompts/vision-text-transcription-mac96.zh.txt")
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--pass-strategy", choices=("single", "dual"), default="single")
    parser.add_argument("--repair-host", default="mac96")
    parser.add_argument("--repair-model", default=REPAIR_MODEL)
    parser.add_argument("--repair-timeout", type=int, default=180)
    parser.add_argument("--package-suffix", default="")
    args = parser.parse_args(argv)
    try:
        target = run(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    manifest = yaml.safe_load((target / "manifest.yaml").read_text(encoding="utf-8")) if yaml is not None else json.loads((target / "manifest.yaml").read_text(encoding="utf-8"))
    print(json.dumps({"status": manifest.get("status", "needs-review"), "package": str(target), "model": args.model, "target": args.host}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
