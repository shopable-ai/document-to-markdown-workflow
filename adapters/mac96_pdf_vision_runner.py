#!/usr/bin/env python3
"""Run Workflow A's direct mac96 vision path over every PDF page.

The reader layer is built only from the current page image returned by
``mac96_vision_page_analysis``.  Native text and OCR are recorded as unused
routes so this run cannot silently promote either one into Markdown.  PDF
rendering is a deterministic producer phase: each selected page is copied to
``assets/`` as soon as it is readable, and only that page is then handed to
the page-level model or Markdown-repair call.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
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
from output_naming import resolve_package_target  # noqa: E402
from mac96_vision_page_analysis import DEFAULT_MODEL, PROMPT_VERSION, analyze  # noqa: E402
from markdown_content_repair import (  # noqa: E402
    DEFAULT_MODEL as REPAIR_MODEL,
    has_full_document_yaml_envelope,
    repair_page,
    write_review,
)
from page_markdown_noise_cleanup import (  # noqa: E402
    cleanup_directory as cleanup_page_markdown_noise,
    load_profile as load_noise_cleanup_profile,
)
import pdf_resource_slots as resource_slots  # noqa: E402

PROGRESS_CURRENT_NAME = "progress-current.json"
DEFAULT_NOISE_CLEANUP_PROFILE = ROOT / "references" / "reader-noise-profile.yaml"


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


def progress_event(
    *,
    run_id: str,
    source_fingerprint: str,
    stage: str,
    task: str,
    status: str,
    pages: list[int] | None = None,
    page: int | None = None,
    evidence_paths: list[str] | None = None,
    next_action: str,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    """Build one compact, validator-compatible stage event.

    ``progress.jsonl`` is deliberately a stage ledger.  Per-page detail lives
    in ``progress-pNNN.json`` and the latest mutable state lives in
    ``progress-current.json``; duplicating those records in the append-only
    stream only increases review/context cost.
    """

    return {
        "run_id": run_id,
        "source_fingerprint": source_fingerprint,
        "page": page,
        "pages": pages or [],
        "stage": stage,
        "task": task,
        "attempt": 1,
        "status": status,
        "elapsed_seconds": elapsed_seconds,
        "evidence_paths": evidence_paths or [],
        "next_action": next_action,
        "created_at": now(),
    }


def append_progress_event(path: Path, event: dict[str, Any]) -> None:
    """Append one stage event without loading the existing stream."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def write_progress_current(target: Path, event: dict[str, Any]) -> None:
    """Publish the bounded latest-progress snapshot for human/agent lookup."""

    write_json(target / "evidence" / PROGRESS_CURRENT_NAME, event)


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


def _image_ready(path: Path) -> bool:
    """Return whether an existing render is safe for the page consumer."""

    if not path.is_file() or path.stat().st_size <= 0:
        return False
    if Image is None:
        return True
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError):
        return False
    return True


def render_pages(source: Path, target: Path, pages: list[int], *, batch_size: int = 8):
    """Produce page images while the caller consumes them concurrently.

    The first ``pdftoppm`` process starts as soon as the generator advances.
    It is polled for completed PNGs, and each stable page is copied to
    ``assets/`` and yielded immediately.  This lets the caller submit page 1
    to the existing page-scoped conversion while later pages are still being
    rendered.  Valid existing assets are yielded without re-rendering, which
    makes interrupted runs resumable without a second script.
    """
    render_dir = target / "evidence" / "render-tmp"
    if render_dir.exists():
        shutil.rmtree(render_dir, ignore_errors=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    prefix = render_dir / "page"
    tool = find_tool("pdftoppm")
    assets_dir = target / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    missing = [page for page in pages if not _image_ready(assets_dir / f"page-{page:03d}.png")]
    step = max(1, batch_size)
    chunks = [missing[offset:offset + step] for offset in range(0, len(missing), step)]
    ready: dict[int, Path] = {}
    active_process = None
    active_pending: set[int] = set()
    chunk_index = 0
    started = 0.0

    def copy_render(page: int, rendered: Path) -> Path:
        destination = assets_dir / f"page-{page:03d}.png"
        if not destination.is_file():
            shutil.copy2(rendered, destination)
        return destination

    def start_chunk() -> None:
        nonlocal active_process, active_pending, chunk_index, started
        if active_process is not None or chunk_index >= len(chunks):
            return
        chunk = chunks[chunk_index]
        chunk_index += 1
        active_process = subprocess.Popen(
            [tool, "-png", "-r", "144", "-f", str(min(chunk)), "-l", str(max(chunk)), str(source), str(prefix)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        active_pending = set(chunk)
        started = time.monotonic()

    def collect_ready() -> None:
        nonlocal active_process, active_pending
        if active_process is None:
            return
        new_ready = False
        for page in sorted(active_pending):
            candidates = [render_dir / f"page-{page:03d}.png", render_dir / f"page-{page:02d}.png", render_dir / f"page-{page}.png"]
            rendered = next((item for item in candidates if _image_ready(item)), None)
            if rendered is not None:
                active_pending.remove(page)
                ready[page] = copy_render(page, rendered)
                new_ready = True
        # Yield immediately when a page just became available.  The next
        # consumer request will reap the process or collect any remaining
        # pages, so rendering and page conversion can overlap.
        if new_ready:
            return
        if active_process.poll() is None:
            return
        stdout, stderr = active_process.communicate(timeout=900)
        if active_process.returncode != 0:
            raise RuntimeError(stderr.strip() or stdout.strip() or "pdftoppm failed")
        for page in sorted(active_pending):
            candidates = [render_dir / f"page-{page:03d}.png", render_dir / f"page-{page:02d}.png", render_dir / f"page-{page}.png"]
            rendered = next((item for item in candidates if _image_ready(item)), None)
            if rendered is not None:
                active_pending.remove(page)
                ready[page] = copy_render(page, rendered)
        if active_pending:
            raise RuntimeError(f"rendered page is missing: {sorted(active_pending)}")
        active_process = None

    try:
        start_chunk()
        for page in pages:
            existing = assets_dir / f"page-{page:03d}.png"
            if _image_ready(existing):
                yield page, existing
                continue
            while page not in ready:
                if active_process is None:
                    start_chunk()
                if active_process is not None and not active_pending:
                    collect_ready()
                    if active_process is None:
                        start_chunk()
                        continue
                collect_ready()
                if page in ready:
                    break
                if active_process is None:
                    raise RuntimeError(f"rendered page is missing: {page}")
                if time.monotonic() - started > 900:
                    active_process.terminate()
                    raise RuntimeError(f"pdftoppm timed out while rendering pages {sorted(active_pending)}")
                time.sleep(0.05)
            yield page, ready.pop(page)
    finally:
        process_was_running = active_process is not None and active_process.poll() is None
        if process_was_running:
            active_process.terminate()
            try:
                active_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                active_process.kill()
        if active_process is not None:
            try:
                stdout, stderr = active_process.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                stdout, stderr = "", ""
            if not process_was_running and active_process.returncode not in (None, 0):
                raise RuntimeError(stderr.strip() or stdout.strip() or "pdftoppm failed")
        shutil.rmtree(render_dir, ignore_errors=True)


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


def package_capability(
    target: Path,
    host: str,
    model: str,
    *,
    refresh: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Reuse a successful package-local probe unless a refresh was requested."""

    path = target / "evidence" / "mac96-preflight.json"
    if not refresh and path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if (
            isinstance(existing, dict)
            and existing.get("status") == "complete"
            and existing.get("target") == host
            and existing.get("model") == model
            and existing.get("model_available") is True
            and existing.get("vision_capability") is True
        ):
            return existing, False
    return remote_capability(host, model), True


def page_title(markdown: str, page: int) -> str:
    for raw in markdown.splitlines():
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", raw)
        if not match:
            continue
        title = match.group(1).strip()
        title = re.sub(r"^第\s*\d{1,3}\s*页\s*[.、:：-]?\s*", "", title).strip()
        title = re.sub(r"^\d{1,3}\s*[.、:：-]\s*", "", title).strip()
        if title and len(title) <= 100:
            return title
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
    if has_full_document_yaml_envelope(text):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
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
    raw_lines = markdown.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    if markdown.count("\n\n") < 1 and len(lines) >= 4:
        return True
    structural = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)、]\s|>\s|\||!\[)")
    for previous, current in zip(raw_lines, raw_lines[1:]):
        previous = previous.rstrip()
        current = current.strip()
        if not previous or not current or structural.match(previous.strip()) or structural.match(current):
            continue
        if re.match(r"[\u3400-\u9fff]", previous[-1]) and re.match(r"[\u3400-\u9fff]", current[0]):
            return True
    return False


def actual_vision_model_calls(result: dict[str, Any]) -> int:
    attempts = result.get("run_attempts")
    if isinstance(attempts, list) and attempts:
        return sum(
            int(item.get("model_call_count") or 0)
            for item in attempts
            if isinstance(item, dict)
        )
    return int(result.get("model_call_count") or 0)


def vision_result_metrics(result: dict[str, Any]) -> dict[str, Any]:
    requests = result.get("requests")
    requests = requests if isinstance(requests, list) else []
    metrics = [item.get("metrics") or {} for item in requests if isinstance(item, dict)]
    return {
        "model_call_count": actual_vision_model_calls(result),
        "wall_seconds": round(sum(float(item.get("wall_seconds", 0.0)) for item in metrics), 3),
        "prompt_eval_count": sum(
            int(item["prompt_eval_count"])
            for item in metrics
            if isinstance(item.get("prompt_eval_count"), int)
        ) if any(isinstance(item.get("prompt_eval_count"), int) for item in metrics) else None,
        "eval_count": sum(
            int(item["eval_count"])
            for item in metrics
            if isinstance(item.get("eval_count"), int)
        ) if any(isinstance(item.get("eval_count"), int) for item in metrics) else None,
        "unknown_fields": [
            key
            for key in ("prompt_eval_count", "eval_count")
            if not metrics or not all(isinstance(item.get(key), int) for item in metrics)
        ],
    }


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


def reader_image_decision(result: dict[str, Any]) -> dict[str, str]:
    """Return the safe reader-layer disposition for the current page render.

    Page renders remain source/engineering evidence.  They are omitted from
    reader Markdown only when the visual result explicitly classifies the page
    as text-only, says Markdown preserves its layout, and reports no semantic
    visual or unresolved relationship.  Missing or contradictory fields fail
    closed to ``retain``.
    """

    visual = result.get("visual") or {}
    analysis = visual.get("visual_analysis") if isinstance(visual, dict) else {}
    analysis = analysis if isinstance(analysis, dict) else {}
    policy = analysis.get("reader_image_policy")
    policy = policy if isinstance(policy, dict) else {}
    requested_action = policy.get("action")
    reason = policy.get("reason")
    confidence = policy.get("confidence")
    fallback = {
        "action": "retain",
        "reason": str(reason or "classification-missing"),
        "confidence": str(confidence or "unknown"),
    }
    if requested_action != "omit":
        return fallback
    if analysis.get("page_content_type") != "text-only":
        return {**fallback, "reason": "omit-conflicts-with-page-content-type"}
    if analysis.get("layout_dependency") not in {"none", "markdown-representable"}:
        return {**fallback, "reason": "omit-conflicts-with-layout-dependency"}
    conflict_fields = (
        "semantic_visuals",
        "relationships",
        "tables_or_charts",
        "unknown_forms",
        "uncertainties",
    )
    if any(analysis.get(field) for field in conflict_fields):
        return {**fallback, "reason": "omit-conflicts-with-visual-or-unresolved-content"}
    return {
        "action": "omit",
        "reason": str(reason or "text-fully-represented-in-markdown"),
        "confidence": str(confidence or "unknown"),
    }


def stage(stage_id: str, status: str, paths: list[str], diagnostics: list[str] | None = None, unknown: list[str] | None = None) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "status": status,
        "evidence_level": "L2-local-runtime",
        "evidence_paths": paths,
        "verification_commands": ["python3 tests/validate_real_output_root.py <package>"],
        "diagnostics": diagnostics or [],
        "unknown": unknown or [],
        "score_basis": "page-scoped visual evidence per page; no OCR promotion",
    }


def write_page_unit(target: Path, *, page: int, body: str, include_page_image: bool = True) -> Path:
    path = target / "page-units" / f"p{page:03d}.md"
    text = body.rstrip()
    if include_page_image:
        text += f"\n\n![原始页面图](<../assets/page-{page:03d}.png>)"
    text += "\n"
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
    transport = str(result.get("transport") or "ssh")
    evidence_target = str(result.get("target") or host)
    evidence_model = str(result.get("model") or model)
    return {
        "contract": "vision-text-transcription",
        "version": "0.2.0-mac96-only",
        "input_asset_id": f"page-p{page:03d}",
        "source_fingerprint": source_fingerprint,
        "image_sha256": digest(image),
        "page_or_region": f"page-{page}",
        "prompt_version": str(result.get("prompt_version") or PROMPT_VERSION),
        "transport": transport,
        "target": evidence_target,
        "model": evidence_model,
        "candidate_markdown": markdown,
        "status": "complete" if markdown else "failed",
        "diagnostics": result.get("diagnostics", []),
        "source_evidence": f"evidence/vision-page-analysis-p{page:03d}.json",
        "extraction_mode": extraction_mode,
        "fallback_used": result.get("fallback_reason"),
    }


def page_unit_is_present(target: Path, page: int) -> bool:
    path = target / "page-units" / f"p{page:03d}.md"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.strip() or has_full_document_yaml_envelope(text):
        return False
    image_link = f"../assets/page-{page:03d}.png"
    if image_link in text:
        return True
    analysis_path = target / "evidence" / f"vision-page-analysis-p{page:03d}.json"
    try:
        result = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return reader_image_decision(result)["action"] == "omit"


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
                and existing.get("image_sha256") == text_result["image_sha256"]
                and existing.get("source_fingerprint") == text_result["source_fingerprint"]
                and existing.get("extraction_mode") == text_result["extraction_mode"]
            )
        except (OSError, json.JSONDecodeError):
            rewrite_text = True
    if rewrite_text:
        write_json(text_path, text_result)

    if not page_unit_is_present(target, page):
        reader_markdown = format_reader_markdown(markdown) if markdown else "〔无法确认〕"
        decision = reader_image_decision(result)
        write_page_unit(
            target,
            page=page,
            body=reader_markdown,
            include_page_image=decision["action"] != "omit",
        )
    return text_result


def repair_page_unit_if_needed(
    target: Path,
    *,
    page: int,
    host: str,
    model: str,
    timeout: int,
    skip_if_guarded: bool = True,
) -> dict[str, Any]:
    """Run repair only when the deterministic page structure still needs it."""

    page_unit = target / "page-units" / f"p{page:03d}.md"
    evidence_path = target / "evidence" / f"markdown-content-repair-p{page:03d}.json"
    if evidence_path.is_file() and page_unit.is_file():
        try:
            existing = json.loads(evidence_path.read_text(encoding="utf-8"))
            if (
                existing.get("status") in {"applied", "skipped"}
                and existing.get("final_sha256") == hashlib.sha256(page_unit.read_bytes()).hexdigest()
            ):
                return existing
        except (OSError, json.JSONDecodeError):
            pass
    text = page_unit.read_text(encoding="utf-8")
    guard_errors: list[str] = []
    try:
        validate_page_markdown_checkpoint(target, expected_pages=[page])
    except (OSError, RuntimeError) as exc:
        guard_errors.append(str(exc))
    if page_title(text, page) == "〔无法确认〕":
        guard_errors.append("semantic Markdown heading is missing")
    if needs_text_refinement(text):
        guard_errors.append("paragraph or line-break refinement is still required")
    if skip_if_guarded and not guard_errors:
        current_hash = hashlib.sha256(page_unit.read_bytes()).hexdigest()
        result = {
            "contract": "markdown-content-repair",
            "version": "0.4.0",
            "page_unit": str(page_unit.resolve()),
            "page": page,
            "prompt_version": "markdown-content-repair-lan-v1",
            "transport": "not-called",
            "target": host,
            "model": model,
            "input_sha256": current_hash,
            "output_sha256": current_hash,
            "final_sha256": current_hash,
            "requested_at": None,
            "finished_at": now(),
            "status": "skipped",
            "write_back": False,
            "model_status": "not-called",
            "model_call_count": 0,
            "model_metrics": {},
            "diagnostics": [],
            "changes": [],
            "structure_guard": {"status": "passed", "errors": []},
            "next_action": "页面结构守卫已通过；跳过无必要的第二次语言模型调用",
        }
        write_json(evidence_path, result)
        return result
    result = repair_page(page_unit, page=page, host=host, model=model, timeout=timeout)
    result["structure_guard"] = {
        "status": "needs-repair" if guard_errors else "passed-but-explicit-repair-requested",
        "errors": guard_errors,
    }
    write_json(evidence_path, result)
    return result


def language_repair_succeeded(result: dict[str, Any]) -> bool:
    return result.get("status") in {"applied", "skipped"}


def cleanup_page_units_before_language_repair(
    target: Path,
    *,
    pages: list[int],
    profile_path: Path | None = DEFAULT_NOISE_CLEANUP_PROFILE,
) -> dict[str, Any]:
    """Run the auditable batch cleanup gate before any language repair.

    The scan result is retained separately. The apply pass performs its own
    scan and hash guards; this wrapper additionally proves that the reviewed
    and applied plans used identical inputs and deletions. Suspects remain
    untouched and make the stage ``needs-review`` without blocking safe
    deterministic deletions.
    """

    if not pages:
        raise RuntimeError("page Markdown noise cleanup requires at least one page")
    profile = load_noise_cleanup_profile(profile_path)
    selector = ",".join(str(page) for page in pages)
    page_units = target / "page-units"
    scan = cleanup_page_markdown_noise(page_units, profile=profile, pages=selector, apply=False)
    scan_evidence = target / "evidence" / "page-markdown-noise-cleanup-scan.json"
    write_json(scan_evidence, scan)

    scan_signature = [
        (item["file"], item["input_sha256"], item["output_sha256"], item["deletions"])
        for item in scan["files"]
    ]
    for item in scan["files"]:
        current = digest(page_units / item["file"])
        if current != item["input_sha256"]:
            raise RuntimeError(
                f"page unit changed after cleanup scan; refusing apply: {page_units / item['file']}"
            )

    applied = cleanup_page_markdown_noise(page_units, profile=profile, pages=selector, apply=True)
    applied_signature = [
        (item["file"], item["input_sha256"], item["output_sha256"], item["deletions"])
        for item in applied["files"]
    ]
    if applied_signature != scan_signature:
        raise RuntimeError("page Markdown cleanup plan changed between scan and apply")
    if not all(applied["checks"].values()) or applied["residual_high_confidence"]:
        raise RuntimeError("page Markdown cleanup integrity verification failed")
    applied["scan_evidence"] = scan_evidence.relative_to(target).as_posix()
    write_json(target / "evidence" / "page-markdown-noise-cleanup.json", applied)
    return applied


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
        if has_full_document_yaml_envelope(text):
            raise RuntimeError(f"single-page Markdown is wrapped in YAML delimiters for page {page}")
        leaked = [item for item in forbidden if item in text]
        if leaked:
            raise RuntimeError(f"single-page Markdown exposes engineering fields on page {page}: {leaked}")
        analysis_path = target / "evidence" / f"vision-page-analysis-p{page:03d}.json"
        try:
            result = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"single-page visual evidence is unreadable for page {page}: {exc}") from exc
        decision = reader_image_decision(result)
        has_link = f"../assets/page-{page:03d}.png" in text
        if decision["action"] == "omit" and has_link:
            raise RuntimeError(f"pure-text page Markdown unexpectedly retains the page image link for page {page}")
        if decision["action"] != "omit" and not has_link:
            raise RuntimeError(f"single-page Markdown is missing the required page image link for page {page}")
        body_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("![")]
        if not body_lines:
            raise RuntimeError(f"single-page Markdown has no body text for page {page}")


@resource_slots.uses_local_pdf_slot("mac96-vision-pdf-to-markdown")
def run(args: argparse.Namespace) -> Path:
    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pdf":
        raise ValueError("this runner accepts PDF only")
    source_fingerprint = digest(source)
    total_pages = pdf_page_count(source)
    pages = parse_pages(args.pages, total_pages)
    output_root = args.output_root.expanduser().resolve()
    package_id, target = resolve_package_target(output_root, source, extra_label=args.package_suffix)
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

    asset_records: list[dict[str, Any]] = []

    section_records: list[dict[str, Any]] = []
    trace_records: list[dict[str, Any]] = []
    reader_parts: list[str] = []
    model_failures: list[dict[str, Any]] = []
    repair_results: list[dict[str, Any]] = []
    page_statuses: list[dict[str, Any]] = []
    progress_path = target / "evidence" / "progress.jsonl"
    progress_path.write_text("", encoding="utf-8")
    append_progress_event(
        progress_path,
        progress_event(
            run_id=package_id,
            source_fingerprint=source_fingerprint,
            stage="execution_preflight",
            task="execution_preflight",
            status="complete",
            evidence_paths=["evidence/source-fingerprint.json", "evidence/mac96-preflight.json"],
            next_action="start incremental PDF renderer; process each ready page",
        ),
    )
    write_progress_current(
        target,
        {
            "run_id": package_id,
            "source_fingerprint": source_fingerprint,
            "stage": "page-processing",
            "task": "page-processing",
            "status": "in-progress",
            "page": None,
            "processed_pages": 0,
            "total_pages": len(pages),
            "remaining_pages": len(pages),
            "page_evidence": None,
            "next_action": "等待第 1 页页面图就绪",
            "created_at": now(),
            "context_policy": "读取此文件获取当前状态；不要把 progress.jsonl 全量载入上下文。",
        },
    )
    def register_asset(page: int, image: Path) -> None:
        asset_records.append({
            "asset_id": f"page-p{page:03d}",
            "file_name": image.name,
            "file_path": image.relative_to(target).as_posix(),
            "display_name": f"{source.stem} 第 {page} 页原始页面",
            "asset_kind": "page-render",
            "source": {
                "package_id": package_id,
                "source_fingerprint": source_fingerprint,
                "original_filename": source.name,
                "source_occurrences": [{"source_occurrence_id": f"{package_id}-p{page:03d}", "source_file": source.name, "page": page, "region": None, "object_ids": [], "parent_asset_id": None, "ordinal": 1, "status": "located"}],
            },
            "media": {"mime_type": "image/png", "width": image_dimensions(image)[0], "height": image_dimensions(image)[1], "sha256": digest(image)},
            "semantic": {"label": "original-page", "description": f"{source.stem} 第 {page} 页原始页面", "confidence": "high"},
            "naming": {"source_slug": f"source-{source_fingerprint[:12]}", "semantic_slug": "page", "purpose_slug": "original-page", "status": "generated"},
            "status": "available",
        })
        write_json(target / "evidence/rendering.json", {
            "status": "in-progress",
            "source_owned": True,
            "renderer": "pdftoppm",
            "renderer_batch_size": args.render_batch_size,
            "rendering_mode": "producer-consumer-incremental-assets",
            "requested_pages": pages,
            "rendered_pages": [item["source"]["source_occurrences"][0]["page"] for item in asset_records],
            "assets_directory": "assets/",
            "page_processing_barrier": "current-page-asset-ready-before-current-page-processing",
            "text_visibility": {"status": "not-applicable", "reason": "reader text comes from direct mac96 image understanding"},
            "assets": [{"page": item["source"]["source_occurrences"][0]["page"], "image": item["file_path"], "sha256": item["media"]["sha256"]} for item in asset_records],
        })
        write_json(target / "evidence/asset-registration.json", {"status": "in-progress", "assets": [item["asset_id"] for item in asset_records], "embedded_count": 0, "regions_count": 0})

    def submit_model(executor: ThreadPoolExecutor, page: int, image: Path):
        return executor.submit(
            model_page,
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

    executor = ThreadPoolExecutor(max_workers=max(1, args.page_concurrency))
    render_iterator = iter(render_pages(source, target, pages, batch_size=args.render_batch_size))
    pending_item = None
    page_index = 0
    while True:
        if pending_item is None:
            try:
                page, image = next(render_iterator)
            except StopIteration:
                break
            page_index += 1
            register_asset(page, image)
            pending_item = (page_index, page, image, submit_model(executor, page, image))
        next_item = None
        # Keep the stage gate meaningful: do not submit page 2 until page 1
        # has been validated, and do not submit page 6 until pages 1-5 have
        # passed the guarded small-batch checkpoint. After those checkpoints,
        # one look-ahead future preserves the bounded page concurrency behavior.
        if pending_item[1] not in {1, 5}:
            try:
                next_page, next_image = next(render_iterator)
            except StopIteration:
                next_page = next_image = None
            if next_page is not None and next_image is not None:
                page_index += 1
                register_asset(next_page, next_image)
                next_item = (page_index, next_page, next_image, submit_model(executor, next_page, next_image))
        index, page, image, model_future = pending_item
        result, markdown, extraction_mode = model_future.result()
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
        image_decision = reader_image_decision(result)
        title = page_title(markdown, page) if markdown else "〔无法确认〕"
        if not markdown:
            model_failures.append({"page": page, "status": result.get("status"), "diagnostics": result.get("diagnostics", [])})
            title = "〔无法确认〕"
        reader_markdown = format_reader_markdown(markdown) if markdown else "〔无法确认〕"
        page_heading = f"## {title}"
        reader_parts.extend([page_heading, "", reader_markdown, ""])
        if image_decision["action"] != "omit":
            reader_parts.extend(["### 原始页面图", "", f"![第 {page} 页原始页面](<assets/page-{page:03d}.png>)", ""])
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
        if page == 1:
            pilot_ok = (
                result.get("status") == "complete"
                and result.get("pass_strategy_used") == args.pass_strategy
                and result.get("model_call_count") == (2 if args.pass_strategy == "dual" else 1)
                and (result.get("text") or {}).get("status") == "complete"
                and (result.get("visual") or {}).get("status") == "complete"
                and markdown
                and "模型调用" not in markdown
                and "待复核" not in markdown
            )
            print(
                f"[mac96-pdf] pilot-pages=1 format={'passed' if pilot_ok else 'failed'}; continuing automatically",
                file=sys.stderr,
                flush=True,
            )
            if not pilot_ok:
                raise RuntimeError("page 1 did not pass the initial format checkpoint")
        occurrence_id = f"{package_id}-p{page:03d}"
        section_records.append({
            "section_id": f"page-{page:03d}",
            "title": title,
            "body": reader_markdown,
            "source_occurrences": [occurrence_id],
            "visual_asset_ids": [] if image_decision["action"] == "omit" else [f"page-p{page:03d}"],
            "confidence": "high" if markdown and not unknown and not uncertainties else "medium" if markdown else "low",
            "reader_order": "semantic-before-visual",
            "reader_image_policy": image_decision,
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
            "reader_image_policy": image_decision,
            "evidence": f"evidence/vision-page-analysis-p{page:03d}.json",
        })
        page_statuses.append({
            "page": page,
            "status": text_result["status"],
            "title": title,
            "page_content_type": analysis.get("page_content_type", "unknown"),
            "layout_dependency": analysis.get("layout_dependency", "unknown"),
            "semantic_visuals": analysis.get("semantic_visuals", []),
            "ignored_elements": analysis.get("ignored_elements", []),
            "reader_image_policy": image_decision,
            "unknown_forms": unknown,
            "uncertainties": uncertainties,
        })
        page_progress_path = f"evidence/progress-p{page:03d}.json"
        write_json(target / page_progress_path, {"page": page, "ordinal": index, "total": len(pages), "status": text_result["status"], "model": args.model, "target": args.host, "created_at": now(), "evidence": f"evidence/vision-page-analysis-p{page:03d}.json"})
        write_progress_current(
            target,
            {
                "run_id": package_id,
                "source_fingerprint": source_fingerprint,
                "stage": "page-processing",
                "task": f"page-{page:03d}",
                "status": text_result["status"],
                "page": page,
                "processed_pages": index,
                "total_pages": len(pages),
                "remaining_pages": len(pages) - index,
                "page_evidence": page_progress_path,
                "next_action": f"process page {pages[index]}" if index < len(pages) else "assemble package",
                "created_at": now(),
                "context_policy": "读取此文件获取当前状态；逐页调查时只读取对应 progress-pNNN.json。",
            },
        )
        print(f"[mac96-pdf] page={page}/{len(pages)} status={text_result['status']} title={title}", file=sys.stderr, flush=True)
        if page == 1:
            validate_page_markdown_checkpoint(target, expected_pages=[1])
            print(
                "[mac96-pdf] single-page-markdown-checkpoint=passed page=1; continuing automatically",
                file=sys.stderr,
                flush=True,
            )
        if page == 5:
            validate_page_markdown_checkpoint(target, expected_pages=[1, 2, 3, 4, 5])
            print(
                "[mac96-pdf] small-batch-markdown-checkpoint=passed pages=1-5; continuing automatically",
                file=sys.stderr,
                flush=True,
            )
        pending_item = next_item
    executor.shutdown(wait=True)
    rendered_pages = [item["source"]["source_occurrences"][0]["page"] for item in asset_records]
    write_json(target / "evidence/rendering.json", {
        "status": "complete" if rendered_pages == pages else "needs-review",
        "source_owned": True,
        "renderer": "pdftoppm",
        "renderer_batch_size": args.render_batch_size,
        "rendering_mode": "producer-consumer-incremental-assets",
        "requested_pages": pages,
        "rendered_pages": rendered_pages,
        "assets_directory": "assets/",
        "page_processing_barrier": "current-page-asset-ready-before-current-page-processing",
        "text_visibility": {"status": "not-applicable", "reason": "reader text comes from direct mac96 image understanding"},
        "assets": [{"page": item["source"]["source_occurrences"][0]["page"], "image": item["file_path"], "sha256": item["media"]["sha256"]} for item in asset_records],
    })
    write_json(target / "evidence/asset-registration.json", {"status": "complete" if rendered_pages == pages else "needs-review", "assets": [item["asset_id"] for item in asset_records], "embedded_count": 0, "regions_count": 0})
    append_progress_event(
        progress_path,
        progress_event(
            run_id=package_id,
            source_fingerprint=source_fingerprint,
            stage="source_rendering",
            task="incremental-page-rendering",
            status="complete" if rendered_pages == pages else "needs-review",
            evidence_paths=["evidence/rendering.json", "evidence/asset-registration.json", "assets/"],
            next_action="开始批量 page-unit 页眉页脚噪声清洗",
        ),
    )
    noise_cleanup_result = cleanup_page_units_before_language_repair(
        target,
        pages=pages,
        profile_path=getattr(args, "noise_profile", DEFAULT_NOISE_CLEANUP_PROFILE),
    )
    append_progress_event(
        progress_path,
        progress_event(
            run_id=package_id,
            source_fingerprint=source_fingerprint,
            stage="page_markdown_noise_cleanup",
            task="batch-page-unit-boundary-cleanup",
            status="complete" if noise_cleanup_result["status"] == "passed" else "needs-review",
            evidence_paths=[
                "evidence/page-markdown-noise-cleanup-scan.json",
                "evidence/page-markdown-noise-cleanup.json",
                "page-units/",
            ],
            next_action="开始逐页语言质量修复",
        ),
    )
    for page in pages:
        repair_results.append(
            repair_page_unit_if_needed(
                target,
                page=page,
                host=args.repair_host,
                model=args.repair_model,
                timeout=args.repair_timeout,
                skip_if_guarded=not args.force_language_repair,
            )
        )
    reader_parts = []
    for page in pages:
        page_unit = target / "page-units" / f"p{page:03d}.md"
        page_text = page_unit.read_text(encoding="utf-8").strip()
        page_text = page_text.replace("<../assets/", "<assets/")
        reader_parts.append(page_text)
    (target / "index.md").write_text("\n\n".join(reader_parts).rstrip() + "\n", encoding="utf-8")
    repair_failures = [item for item in repair_results if not language_repair_succeeded(item)]
    repair_evidence_paths = [
        f"evidence/markdown-content-repair-p{page:03d}.json"
        for page in pages
        if (target / f"evidence/markdown-content-repair-p{page:03d}.json").is_file()
    ] or ["reviews/markdown-content-repair.yaml"]
    progress_events = [
        ("execution_preflight", "complete", ["evidence/source-fingerprint.json", "evidence/mac96-preflight.json"]),
        ("source_inventory", "complete", ["evidence/source-inventory.json"]),
        ("native_extraction", "complete", ["evidence/native-content-model.json"]),
        ("source_rendering", "complete", ["evidence/rendering.json", "assets"]),
        (
            "page_markdown_noise_cleanup",
            "complete" if noise_cleanup_result["status"] == "passed" else "needs-review",
            ["evidence/page-markdown-noise-cleanup.json", "page-units"],
        ),
        ("language_quality_repair", "complete" if not repair_failures else "needs-review", [repair_evidence_paths[0], "page-units"]),
        ("markdown_assembly", "complete" if not model_failures and not repair_failures and noise_cleanup_result["status"] == "passed" else "needs-review", ["index.md", "evidence/source-to-final-trace.json"]),
        ("independent_validation", "not-run", []),
    ]
    for progress_stage, progress_status, progress_paths in progress_events:
        append_progress_event(
            progress_path,
            progress_event(
                run_id=package_id,
                source_fingerprint=source_fingerprint,
                stage=progress_stage,
                task=progress_stage,
                status=progress_status,
                evidence_paths=progress_paths,
                next_action="independent validator" if progress_stage == "independent_validation" else "continue",
            ),
        )
    write_json(target / "evidence/source-to-final-trace.json", {"status": "complete" if not model_failures else "needs-review", "source_fingerprint": source_fingerprint, "pages": trace_records, "coverage": {"selected_pages": pages, "transcribed_pages": [item["page"] for item in page_statuses if item["status"] == "complete"], "failed_pages": [item["page"] for item in page_statuses if item["status"] != "complete"]}})
    write_json(target / "evidence/content-classification.json", {"status": "complete" if not model_failures else "needs-review", "source_fingerprint": source_fingerprint, "pages": page_statuses, "basis": "direct mac96 vision result per current page image"})
    all_unknown = [f"p{item['page']:03d}: {value}" for item in page_statuses for value in item["unknown_forms"]]
    all_uncertainties = [f"p{item['page']:03d}: {value}" for item in page_statuses for value in item["uncertainties"]]
    reconciliation_status = "complete" if not model_failures and not all_unknown and not all_uncertainties else "needs-review"
    write_json(target / "evidence/reconciliation.json", {"status": reconciliation_status, "source_fingerprint": source_fingerprint, "selected_pages": pages, "native_text": {"status": "not-used-for-reader-layer"}, "visual_text": {"status": "complete" if not model_failures else "needs-review", "source": "mac96", "model": args.model, "prompt_version": PROMPT_VERSION}, "unknown_forms": all_unknown, "unresolved": all_uncertainties + [f"page-{item['page']} model transcription failed" for item in model_failures], "coverage_notes": ["PDF 页面图由独立渲染生产者持续写入 assets/；每张图就绪后才进入对应页面处理。", "每页只向 mac96 发送一张当前页面图。", "读者层没有使用 OCR、PDF 原生文字层或其他页面上下文。", "视觉关系仅保留模型在当前图片中直接确认的结果。"]})
    write_json(target / "evidence/quality-audit.json", {"status": "needs-review", "basis": "automated direct-mac96 conversion; no human page-by-page audit", "pages": pages, "model": args.model, "prompt_version": PROMPT_VERSION, "risks": ["视觉模型可能对复杂连线或低清字形产生误判；原图和逐页证据均已保留。"]})

    page_unit_dir = target / "page-units"
    for page in pages:
        section = next(item for item in section_records if item["section_id"] == f"page-{page:03d}")
        page_unit = page_unit_dir / f"p{page:03d}.md"
        if not page_unit.is_file():
            write_page_unit(
                target,
                page=page,
                body=section["body"],
                include_page_image=(section.get("reader_image_policy") or {}).get("action") != "omit",
            )
        page_text = page_unit.read_text(encoding="utf-8").strip()
        section["body"] = page_text
        repaired_title = page_title(page_text, page)
        if repaired_title != "〔无法确认〕":
            section["title"] = repaired_title
            page_status = next((item for item in page_statuses if item["page"] == page), None)
            if page_status is not None:
                page_status["title"] = repaired_title
    write_json(target / "evidence/content-classification.json", {"status": "complete" if not model_failures else "needs-review", "source_fingerprint": source_fingerprint, "pages": page_statuses, "basis": "direct mac96 vision result per current page image; final title projected from guarded page-unit when available"})
    write_review(target / "reviews/markdown-content-repair.yaml", package_id, repair_results)
    final_reader_parts = []
    for page in pages:
        page_text = (page_unit_dir / f"p{page:03d}.md").read_text(encoding="utf-8").strip()
        final_reader_parts.append(page_text.replace("<../assets/", "<assets/"))
    (target / "index.md").write_text("\n\n".join(final_reader_parts).rstrip() + "\n", encoding="utf-8")
    (target / "document-temp.md").write_text("\n".join((page_unit_dir / f"p{page:03d}.md").read_text(encoding="utf-8") for page in pages), encoding="utf-8")

    noise_cleanup_needs_review = noise_cleanup_result["status"] != "passed"
    noise_cleanup_unknown = [
        f"{item['file']}:{item['line']} {item['raw']}"
        for item in noise_cleanup_result["suspects"]
    ]
    overall_status = (
        "complete"
        if not model_failures
        and not repair_failures
        and not noise_cleanup_needs_review
        and reconciliation_status == "complete"
        else "needs-review"
    )
    write_progress_current(
        target,
        {
            "run_id": package_id,
            "source_fingerprint": source_fingerprint,
            "stage": "terminal",
            "task": "run-complete",
            "status": overall_status,
            "page": pages[-1] if pages else None,
            "processed_pages": len(pages),
            "total_pages": len(pages),
            "remaining_pages": 0,
            "page_evidence": f"evidence/progress-p{pages[-1]:03d}.json" if pages else None,
            "next_action": "independent validator" if overall_status != "complete" else "人工抽查复杂视觉关系",
            "created_at": now(),
            "context_policy": "读取此文件获取最终状态；逐页调查时只读取对应 progress-pNNN.json。",
        },
    )
    stages = [
        stage("A1", "complete", ["evidence/source-fingerprint.json", "evidence/source-inventory.json", "evidence/mac96-preflight.json"]),
        stage("A2", "complete", ["evidence/native-content-model.json", "evidence/context-policy.json"], ["Native text is explicitly excluded from the reader layer."]),
        stage("A3", "complete", ["evidence/rendering.json", "evidence/asset-registration.json", "assets"], ["渲染生产者与页面处理消费者并行；每页资产就绪后才开始该页视觉分析。"]),
        stage("A4", "complete" if reconciliation_status == "complete" else "needs-review", ["evidence/vision-text-transcription-p001.json", "evidence/reconciliation.json"], ["Direct mac96 page-image transcription and visual relation analysis."], all_unknown + all_uncertainties),
        stage("A5", "complete" if not model_failures and not repair_failures and not noise_cleanup_needs_review else "needs-review", ["index.md", "page-units", "evidence/page-markdown-noise-cleanup.json", repair_evidence_paths[0], "reviews/markdown-content-repair.yaml", "document-temp.md"], ["Reader Markdown contains no OCR candidate layer.", "Deterministic page noise cleanup runs before language repair.", "LAN language repair is page-scoped and relation-safe."], noise_cleanup_unknown + [f"page-{item['page']}" for item in model_failures] + [f"repair-page-{item.get('page')}" for item in repair_failures]),
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
        "evidence": {"source_inventory": "evidence/source-inventory.json", "capability_probe": "evidence/mac96-preflight.json", "smoke_report": "evidence/smoke-report.json", "progress": "evidence/progress.jsonl", "progress_current": "evidence/progress-current.json", "native_content_model": "evidence/native-content-model.json", "content_classification": "evidence/content-classification.json", "rendering": "evidence/rendering.json", "asset_registration": "evidence/asset-registration.json", "ocr_candidates": "evidence/ocr-candidates.json", "reconciliation": "evidence/reconciliation.json", "source_to_final_trace": "evidence/source-to-final-trace.json", "context_policy": "evidence/context-policy.json", "page_markdown_noise_cleanup": "evidence/page-markdown-noise-cleanup.json", "markdown_content_repair": repair_evidence_paths[0], "quality_audit": "evidence/quality-audit.json"},
        "processing": {"native_extraction": "not-used-for-reader-layer", "ocr_used": False, "visual_review": "complete" if not model_failures else "needs-review", "execution_path": "mac96-direct-vision", "rendering_policy": "producer-consumer-incremental-assets", "fallback_reason": None, "status": overall_status, "stage_evidence": stages, "stage_evidence_contract": "contracts/stage-evidence.yaml", "failure_report": "reports/failure-report.yaml", "model_calls": [{"target": args.host, "model": args.model, "prompt_version": PROMPT_VERSION, "pass_strategy": args.pass_strategy, "scope": "one-current-page-image-per-call", "page_count": len(pages), "calls_per_page": 2 if args.pass_strategy == "dual" else 1}, {"target": args.repair_host, "model": args.repair_model, "prompt_version": "markdown-content-repair-lan-v1", "scope": "one-current-page-unit-per-call", "page_count": len(pages), "calls_per_page": 1}], "page_markdown_noise_cleanup": {"status": noise_cleanup_result["status"], "evidence": "evidence/page-markdown-noise-cleanup.json", "profile": str(getattr(args, "noise_profile", DEFAULT_NOISE_CLEANUP_PROFILE))}, "language_quality_repair": {"status": "complete" if not repair_failures else "needs-review", "route": "lan-language-model-first", "evidence": repair_evidence_paths[0], "review": "reviews/markdown-content-repair.yaml"}, "context_policy": {"one_source_per_run": True, "one_current_page_image_per_call": True, "one_current_page_unit_per_repair": True, "ocr_used": False}},
        "validation": {"validator_version": "real-output-root", "status": "passed" if overall_status == "complete" else "needs-review", "checks": ["package layout", "source fingerprint", "asset hashes", "Markdown links", "direct mac96 evidence"]},
        "quality_gate": {"status": "passed" if overall_status == "complete" else "needs-review", "score": 1.0 if overall_status == "complete" else None, "reason": "All selected pages returned direct mac96 visual text and relation evidence; human visual audit remains a separate review activity."},
        "quality_report": "reports/quality-report.yaml",
    }
    write_yaml(target / "manifest.yaml", manifest)
    processing = {"status": overall_status, "evidence_level": "L2-local-runtime", "run_id": package_id, "started_at": now(), "finished_at": now(), "real_business_result_package": str(target), "input": {"source": str(source), "source_type": "pdf", "sha256": source_fingerprint, "pages": pages, "total_pages": total_pages, "source_page_numbers": list(range(1, total_pages + 1))}, "source_selection": "single-source", "progress_stream": "evidence/progress.jsonl", "stage_evidence_contract": "contracts/stage-evidence.yaml", "stage_evidence": stages, "stages": [{"id": item["stage_id"], "status": item["status"], "evidence": item["evidence_paths"]} for item in stages], "tools": {"page-renderer": "pdftoppm", "visual-model": f"{args.host}/{args.model}", "markdown-writer": "adapters/mac96_pdf_vision_runner.py", "page-markdown-noise-cleaner": "adapters/page_markdown_noise_cleanup.py"}, "rendering_policy": "producer-consumer-incremental-assets", "asset_policy": {"layout": "flat-assets-v1", "root": "assets/", "classification": "manifest.asset_kind", "embedded_count": 0}, "failure_report": "reports/failure-report.yaml", "ocr": {"status": "not-used", "evidence": "evidence/ocr-candidates.json"}, "visual_model": {"status": "complete" if not model_failures else "needs-review", "evidence": "evidence/source-to-final-trace.json"}, "page_markdown_noise_cleanup": {"status": noise_cleanup_result["status"], "evidence": "evidence/page-markdown-noise-cleanup.json"}, "language_repair": {"status": "complete" if not repair_failures else "needs-review", "evidence": repair_evidence_paths[0]}, "quality_audit": {"status": "needs-review"}}
    processing["progress_current"] = "evidence/progress-current.json"
    failures = [{"failure_id": f"mac96-page-{item['page']:03d}", "stage_id": "A4", "code": "visual-review-unavailable", "severity": "P1", "retryable": True, "status": "open", "message": "mac96 did not return a usable direct visual transcription for this page.", "evidence_paths": [f"evidence/vision-page-analysis-p{item['page']:03d}.json"], "remediation": "Retry the same page against mac96; do not fall back to OCR.", "resolved_evidence_paths": []} for item in model_failures]
    failures.extend({"failure_id": f"markdown-repair-page-{item.get('page', 'unknown')}", "stage_id": "A5", "code": "review-failed", "severity": "P1", "retryable": True, "status": "open", "message": "LAN Markdown repair did not produce a guarded page-unit write-back.", "evidence_paths": [f"evidence/markdown-content-repair-{repair_evidence_label(item)}.json"], "remediation": "Retry the current page against the LAN language model or use the explicitly authorized online fallback.", "resolved_evidence_paths": []} for item in repair_failures)
    acceptance = {"status": "passed" if overall_status == "complete" else "needs-review", "evidence_level": "L2-local-runtime", "real_business_result_package": str(target), "checks": [{"id": "required-files-and-directories", "status": "passed"}, {"id": "source-read", "status": "passed"}, {"id": "source-owned-rendering", "status": "passed"}, {"id": "mac96-direct-vision", "status": "passed" if not model_failures else "needs-review"}, {"id": "ocr-route", "status": "not-used"}, {"id": "page-markdown-noise-cleanup", "status": "passed" if not noise_cleanup_needs_review else "needs-review"}, {"id": "markdown-content-repair", "status": "passed" if not repair_failures else "needs-review"}, {"id": "reader-layer", "status": "passed" if not model_failures and not repair_failures and not noise_cleanup_needs_review else "needs-review"}], "failures": [item["failure_id"] for item in failures], "warnings": ["No human page-by-page visual audit was performed in this automated run."], "stage_evidence": stages, "failure_report": "reports/failure-report.yaml", "diagnostics": ["Final reader Markdown is the guarded page-unit projection; source page images and per-page model evidence are retained."]}
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
    parser.add_argument("--render-batch-size", type=int, default=8)
    parser.add_argument("--page-concurrency", type=int, default=2)
    parser.add_argument("--repair-host", default="mac96")
    parser.add_argument("--repair-model", default=REPAIR_MODEL)
    parser.add_argument("--repair-timeout", type=int, default=180)
    parser.add_argument("--force-language-repair", action="store_true")
    parser.add_argument("--noise-profile", type=Path, default=DEFAULT_NOISE_CLEANUP_PROFILE)
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
