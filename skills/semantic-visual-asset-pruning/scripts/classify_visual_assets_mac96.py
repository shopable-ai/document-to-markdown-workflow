#!/usr/bin/env python3
"""Classify page images on mac96 without changing the Markdown asset package."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

try:
    from adapters import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - direct script execution from repository root
    import sys

    ROOT_FOR_IMPORT = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(ROOT_FOR_IMPORT / "adapters"))
    import pdf_resource_slots as resource_slots


DECISIONS = {"retain", "crop-required", "candidate-remove", "needs-review"}
REASONS = {
    "cover",
    "table-of-contents",
    "text-covered",
    "diagram",
    "table",
    "flow",
    "color-coded-structure",
    "relation-dense-layout",
    "mixed-text-visual",
    "needs-review",
}
CONFIDENCE = {"high", "medium", "low", "unknown"}
REMOVABLE_REASONS = {"cover", "table-of-contents", "text-covered"}
LEGACY_DECISION_ALIASES = {
    "omit": "candidate-remove",
    "cover": "candidate-remove",
    "table-of-contents": "candidate-remove",
    "text-covered": "candidate-remove",
}
PROMPT_VERSION = "semantic-visual-asset-classification-mac96-v2"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROMPT = ROOT / "references/prompts/semantic-visual-asset-classification-mac96.txt"
PLAIN_TEXT_UNCERTAINTY_MARKERS = ("needs-review", "unknown", "![", "```")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(part.strip()) for part in token.split("-", 1))
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise ValueError(f"invalid page number: {token}")
            pages.add(page)
    if not pages:
        raise ValueError("--pages must contain at least one page")
    return sorted(pages)


def _load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _langgraph_source_fingerprint(root: Path) -> str | None:
    summary = _load_json_mapping(root / "summary.json")
    source = summary.get("source")
    if isinstance(source, dict) and isinstance(source.get("sha256"), str):
        return source["sha256"]
    value = summary.get("source_sha256")
    return value if isinstance(value, str) else None


def load_package(root: Path) -> tuple[dict, dict[int, dict]]:
    """Load either a Workflow A manifest package or a simple LangGraph run."""
    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        fingerprint = _langgraph_source_fingerprint(root)
        assets: list[dict[str, Any]] = []
        page_map: dict[int, dict] = {}
        for image in sorted((root / "assets").glob("page-[0-9][0-9][0-9].png")):
            match = re.fullmatch(r"page-(\d{3})\.png", image.name)
            if not match:
                continue
            page = int(match.group(1))
            result = _load_json_mapping(root / "evidence" / f"page-result-p{page:03d}.json")
            recorded_asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
            relative = image.relative_to(root).as_posix()
            asset = {
                "asset_id": f"page-p{page:03d}",
                "asset_kind": "page-render",
                "file_path": relative,
                "media": {"sha256": recorded_asset.get("sha256")},
                "source": {"source_occurrences": [{"page": page}]},
            }
            page_unit = root / "page-units" / f"p{page:03d}.md"
            body = page_unit.read_text(encoding="utf-8") if page_unit.is_file() else ""
            title_match = re.search(r"^#{1,6}\s+(.+?)\s*$", body, re.MULTILINE)
            section = {
                "section_id": f"page-{page}",
                "title": title_match.group(1) if title_match else "",
                "body": body,
            }
            assets.append(asset)
            page_map[page] = {"asset": asset, "section": section}
        manifest = {
            "package_kind": "langgraph-simple",
            "source": {"source_fingerprint": fingerprint},
            "assets": assets,
        }
        return manifest, page_map

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    sections = {
        int(section["section_id"].split("-")[-1]): section
        for section in (manifest.get("markdown") or {}).get("sections", [])
        if isinstance(section, dict) and re.fullmatch(r"page-\d+", str(section.get("section_id", "")))
    }
    assets: dict[int, dict] = {}
    for asset in manifest.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("asset_kind") != "page-render":
            continue
        occurrences = ((asset.get("source") or {}).get("source_occurrences") or [])
        pages = [item.get("page") for item in occurrences if isinstance(item, dict) and isinstance(item.get("page"), int)]
        if pages:
            assets[pages[0]] = asset
    return manifest, {page: {"asset": asset, "section": sections.get(page)} for page, asset in assets.items()}


def _visual_analysis_from_mapping(value: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        value.get("visual_analysis"),
        (value.get("visual") or {}).get("visual_analysis")
        if isinstance(value.get("visual"), dict)
        else None,
        (value.get("result") or {}).get("visual_analysis")
        if isinstance(value.get("result"), dict)
        else None,
    ]
    result = value.get("result")
    if isinstance(result, dict) and isinstance(result.get("visual"), dict):
        candidates.append(result["visual"].get("visual_analysis"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


def load_existing_visual_analysis(root: Path, page: int) -> tuple[dict[str, Any] | None, str | None]:
    """Read only bounded, page-level JSON evidence; never inspect raw responses."""
    paths = [
        root / "evidence" / f"page-result-p{page:03d}.json",
        root / "evidence" / f"visual-p{page:03d}.json",
        root / "evidence" / f"vision-page-analysis-p{page:03d}.json",
    ]
    for path in paths:
        value = _load_json_mapping(path)
        analysis = _visual_analysis_from_mapping(value)
        if analysis is not None:
            return analysis, path.relative_to(root).as_posix()
    return None, None


def load_page_result(root: Path, page: int) -> tuple[dict[str, Any] | None, str | None]:
    path = root / "evidence" / f"page-result-p{page:03d}.json"
    value = _load_json_mapping(path)
    return (value or None), (path.relative_to(root).as_posix() if value else None)


def _nonempty(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value not in {None, "", False}


def _section_body(section: dict[str, Any] | None) -> str:
    return str((section or {}).get("body") or "")


def _body_has_plain_text_only_signals(body: str) -> bool:
    lowered = body.lower()
    if any(marker in lowered for marker in PLAIN_TEXT_UNCERTAINTY_MARKERS):
        return False
    if "<!-- image" in lowered:
        return False
    if body.count("|") > 0:
        return False
    if body.count("**") > 0:
        return False
    if len(re.findall(r"^#{1,6}\s+", body, re.MULTILINE)) > 1:
        return False
    if re.search(r"^\s*\|.*\|\s*$", body, re.MULTILINE):
        return False
    return True


def classify_page_result_markdown_heuristic(
    page_result: dict[str, Any] | None,
    section: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Never delete from Markdown alone; only return a bounded needs-review hint."""
    if not isinstance(page_result, dict) or page_result.get("status") != "passed":
        return None
    body = _section_body(section).strip()
    if not body or not _body_has_plain_text_only_signals(body):
        return None
    cleanup = page_result.get("cleanup")
    changes = cleanup.get("changes") if isinstance(cleanup, dict) else None
    if isinstance(changes, list) and any(
        change.get("reason") not in {"repeated-header", "repeated-footer"}
        for change in changes
        if isinstance(change, dict)
    ):
        return None
    return {
        "decision": "needs-review",
        "reason_codes": ["needs-review"],
        "confidence": "low",
        "semantic_text_coverage": "unknown",
        "visual_increment": "unknown",
        "rationale": "Markdown 仅显示为纯文字页；该信号不能反向证明原图无视觉增量，需查看原始页面图或完整结构化视觉分析",
    }


def classify_existing_visual_analysis(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """Reuse complete structured evidence; return None when mac96 fallback is required."""
    policy = analysis.get("reader_image_policy")
    required = {
        "page_content_type",
        "layout_dependency",
        "semantic_visuals",
        "relationships",
        "tables_or_charts",
        "unknown_forms",
    }
    if not isinstance(policy, dict) or not required.issubset(analysis):
        return None
    action = policy.get("action")
    coverage = policy.get("semantic_text_coverage", analysis.get("semantic_text_coverage"))
    increment = policy.get("visual_increment", analysis.get("visual_increment"))
    reason = str(policy.get("reason") or "").strip()
    reasons = policy.get("reason_codes")
    if not isinstance(reasons, list) or not reasons:
        reasons = [reason] if reason else []
    normalized_reasons = [
        "text-covered" if item in {"text-only", "markdown-covered", "omit-text-only"} else item
        for item in reasons
    ]
    complex_fields = (
        "semantic_visuals",
        "relationships",
        "tables_or_charts",
        "unknown_forms",
        "uncertainties",
    )
    has_complex = any(_nonempty(analysis.get(field)) for field in complex_fields)
    page_type = analysis.get("page_content_type")
    layout = analysis.get("layout_dependency")

    if action in {"retain", "crop-required", "needs-review"}:
        decision = action
        fallback_reason = "diagram" if has_complex else "needs-review"
        return {
            "decision": decision,
            "reason_codes": [item for item in normalized_reasons if item in REASONS]
            or [fallback_reason],
            "confidence": policy.get("confidence") if policy.get("confidence") in CONFIDENCE else "unknown",
            "semantic_text_coverage": coverage if coverage in {"complete", "partial", "unknown"} else "unknown",
            "visual_increment": increment if increment in {"none", "present", "unknown"} else ("present" if has_complex else "unknown"),
            "rationale": str(policy.get("rationale") or reason or "结构化视觉字段要求保留")[:1000],
        }

    if action not in {"omit", "candidate-remove"}:
        return None
    if coverage not in {"complete", "partial", "unknown"} or increment not in {"none", "present", "unknown"}:
        return None
    if (
        has_complex
        or page_type != "text-only"
        or layout not in {"none", "markdown-representable"}
    ):
        return {
            "decision": "needs-review",
            "reason_codes": ["needs-review"],
            "confidence": "unknown",
            "semantic_text_coverage": coverage,
            "visual_increment": increment,
            "rationale": "reader_image_policy 与复杂视觉或布局字段冲突",
        }
    if not normalized_reasons or any(item not in REMOVABLE_REASONS for item in normalized_reasons):
        return None
    if coverage != "complete" or increment != "none":
        return {
            "decision": "needs-review",
            "reason_codes": ["needs-review", *normalized_reasons],
            "confidence": "unknown",
            "semantic_text_coverage": coverage,
            "visual_increment": increment,
            "rationale": "结构化字段未同时证明文字覆盖完整且无视觉增量",
        }
    return {
        "decision": "candidate-remove",
        "reason_codes": normalized_reasons,
        "confidence": policy.get("confidence") if policy.get("confidence") in CONFIDENCE else "unknown",
        "semantic_text_coverage": coverage,
        "visual_increment": increment,
        "rationale": str(policy.get("rationale") or reason or "结构化视觉字段满足删除候选门")[:1000],
    }


def prompt_for(page: int, section: dict | None) -> str:
    title = (section or {}).get("title", "")
    body = re.sub(r"\s+", " ", str((section or {}).get("body", ""))).strip()
    body = body[:1200]
    return (
        f"{PROMPT_VERSION}\n"
        f"当前页码: {page}\n"
        f"Markdown 标题: {title}\n"
        f"Markdown 文字层摘录: {body}\n"
        "请按系统提示检查图片，并严格返回 JSON。"
    )


def parse_model_payload(payload: Any) -> dict:
    """Validate a model object and fail closed to needs-review."""
    if isinstance(payload, dict) and isinstance(payload.get("response"), str):
        raw = payload["response"].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fenced = re.search(r"\{.*\}", raw, re.DOTALL)
            if fenced:
                try:
                    payload = json.loads(fenced.group(0))
                except json.JSONDecodeError:
                    payload = {}
            else:
                payload = {}
    if not isinstance(payload, dict):
        payload = {}
    decision = payload.get("decision")
    reasons = payload.get("reason_codes")
    if isinstance(decision, str):
        decision = LEGACY_DECISION_ALIASES.get(decision, decision)
        if decision == "candidate-remove" and (not isinstance(reasons, list) or not reasons):
            raw_reason = payload.get("decision")
            if isinstance(raw_reason, str) and raw_reason in REMOVABLE_REASONS:
                reasons = [raw_reason]
    confidence = payload.get("confidence")
    coverage = payload.get("semantic_text_coverage")
    increment = payload.get("visual_increment")
    if decision not in DECISIONS or not isinstance(reasons, list) or not reasons or any(item not in REASONS for item in reasons):
        return {
            "decision": "needs-review",
            "reason_codes": ["needs-review"],
            "confidence": "unknown",
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
            "rationale": "模型响应缺少可验证的分类字段",
        }
    if confidence not in CONFIDENCE:
        confidence = "unknown"
    if coverage not in {"complete", "partial", "unknown"}:
        coverage = "unknown"
    if increment not in {"none", "present", "unknown"}:
        increment = "unknown"
    if decision == "candidate-remove" and (
        not set(reasons).issubset(REMOVABLE_REASONS)
        or coverage != "complete"
        or increment != "none"
    ):
        decision = "needs-review"
        reasons = ["needs-review", *[item for item in reasons if item in REASONS and item != "needs-review"]]
    if decision == "crop-required" and (
        increment != "present"
        or not set(reasons).intersection(
            {"mixed-text-visual", "diagram", "table", "flow", "color-coded-structure", "relation-dense-layout"}
        )
    ):
        decision = "needs-review"
        reasons = ["needs-review", *[item for item in reasons if item in REASONS and item != "needs-review"]]
    return {
        "decision": decision,
        "reason_codes": reasons,
        "confidence": confidence,
        "semantic_text_coverage": coverage,
        "visual_increment": increment,
        "rationale": str(payload.get("rationale") or "").strip()[:1000],
    }


@resource_slots.uses_visual_model_slot("mac96-visual-asset-classification")
def classify_remote(
    image: Path,
    system_prompt: str,
    prompt: str,
    host: str,
    model: str,
    timeout: int,
) -> dict:
    def failed(rationale: str) -> dict[str, Any]:
        return {
            "status": "needs-review",
            "decision": "needs-review",
            "reason_codes": ["needs-review"],
            "confidence": "unknown",
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
            "rationale": rationale,
        }

    request = {
        "model": model,
        "system": system_prompt,
        "prompt": prompt,
        "images": [base64.b64encode(image.read_bytes()).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    command = [
        "ssh",
        host,
        "/usr/bin/curl",
        "-sS",
        "http://127.0.0.1:11434/api/generate",
        "-H",
        "Content-Type:application/json",
        "--data-binary",
        "@-",
    ]
    try:
        result = subprocess.run(command, input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return failed(f"mac96 transport failed: {exc}")
    if result.returncode != 0:
        return failed(f"mac96 curl failed with exit {result.returncode}: {result.stderr.decode('utf-8', 'replace')[-500:]}")
    try:
        raw = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return failed(f"mac96 response is not JSON: {exc}")
    parsed = parse_model_payload(raw)
    parsed["status"] = "complete" if parsed["decision"] != "needs-review" else "needs-review"
    return parsed


def _counts(records: list[dict]) -> dict[str, int]:
    return {
        "candidate_remove": sum(item.get("decision") == "candidate-remove" for item in records),
        "crop_required": sum(item.get("decision") == "crop-required" for item in records),
        "retained": sum(item.get("decision") == "retain" for item in records),
        "needs_review": sum(item.get("decision") == "needs-review" for item in records),
        "existing_visual_analysis": sum(item.get("classification_source") == "existing-visual-analysis" for item in records),
        "page_result_markdown_heuristic": sum(item.get("classification_source") == "page-result-markdown-heuristic" for item in records),
        "mac96_fallback": sum(item.get("classification_source") == "mac96-fallback" for item in records),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--pages", required=True, help="page numbers/ranges, e.g. 23-27")
    parser.add_argument("--output", default="evidence/visual-asset-classification-mac96.json")
    parser.add_argument("--host", default="mac96")
    parser.add_argument("--model", default="qwen3-vl:30b-a3b-instruct")
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse hash-matching completed decisions and persist progress after each page",
    )
    args = parser.parse_args()
    root = args.package_root.expanduser().resolve()
    pages = parse_pages(args.pages)
    manifest, page_map = load_package(root)
    prompt_path = args.prompt_file.expanduser().resolve()
    system_prompt = prompt_path.read_text(encoding="utf-8")
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    existing_records: dict[int, dict[str, Any]] = {}
    if args.resume and output.is_file():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if (
            previous.get("source_fingerprint")
            != (manifest.get("source") or {}).get("source_fingerprint")
            or previous.get("prompt_sha256") != sha256(prompt_path)
            or previous.get("target")
            != {"transport": "ssh", "host": args.host, "model": args.model}
        ):
            raise ValueError("existing classification report does not match source, prompt, or target")
        existing_records = {
            int(item["page"]): item
            for item in previous.get("decisions") or []
            if isinstance(item, dict) and isinstance(item.get("page"), int)
        }

    def report_for(status: str) -> dict[str, Any]:
        return {
            "schema": "visual-asset-classification-mac96",
            "version": "0.2.0",
            "status": status,
            "mode": "candidate-only",
            "package_kind": manifest.get("package_kind", "workflow-a-manifest"),
            "source_fingerprint": (manifest.get("source") or {}).get("source_fingerprint"),
            "prompt_version": PROMPT_VERSION,
            "prompt_path": str(prompt_path),
            "prompt_sha256": sha256(prompt_path),
            "target": {"transport": "ssh", "host": args.host, "model": args.model},
            "selected_pages": pages,
            "completed_pages": [item.get("page") for item in records],
            "counts": _counts(records),
            "decisions": records,
            "apply": {"allowed": False, "reason": "候选报告不授权删除；须另行获得用户批准并显式调用 pruning --apply"},
            "diagnostics": diagnostics,
        }

    records: list[dict] = []
    diagnostics: list[str] = []
    for page in pages:
        entry = page_map.get(page)
        if not entry:
            records.append({"page": page, "decision": "needs-review", "reason_codes": ["needs-review"], "semantic_text_coverage": "unknown", "visual_increment": "unknown", "classification_source": "missing-asset-map", "rationale": "manifest 没有 page-render 来源资产"})
            diagnostics.append(f"page {page} has no registered page-render asset")
            _write_report(output, report_for("running"))
            continue
        asset = entry["asset"]
        image = root / str(asset.get("file_path", ""))
        base = {
            "page": page,
            "asset_id": asset.get("asset_id"),
            "file_path": asset.get("file_path"),
            "sha256": sha256(image) if image.is_file() else None,
            "source_fingerprint": (manifest.get("source") or {}).get("source_fingerprint"),
            "status": "needs-review",
        }
        if not image.is_file():
            base.update({"decision": "needs-review", "reason_codes": ["needs-review"], "semantic_text_coverage": "unknown", "visual_increment": "unknown", "classification_source": "missing-asset", "rationale": "来源页面图不存在"})
            diagnostics.append(f"missing asset file: {asset.get('file_path')}")
        else:
            cached = existing_records.get(page)
            if (
                cached
                and cached.get("sha256") == base["sha256"]
                and cached.get("decision") in DECISIONS
            ):
                base.update(cached)
                base["reused"] = True
                records.append(base)
                _write_report(output, report_for("running"))
                continue
            analysis, evidence_path = load_existing_visual_analysis(root, page)
            existing = classify_existing_visual_analysis(analysis) if analysis else None
            if existing is not None:
                base.update(existing)
                base["status"] = "complete" if existing["decision"] != "needs-review" else "needs-review"
                base["classification_source"] = "existing-visual-analysis"
                base["visual_evidence_path"] = evidence_path
            else:
                page_result, page_result_path = load_page_result(root, page)
                heuristic = classify_page_result_markdown_heuristic(page_result, entry.get("section"))
                if heuristic is not None and heuristic.get("decision") == "candidate-remove":
                    base.update(heuristic)
                    base["status"] = "complete"
                    base["classification_source"] = "page-result-markdown-heuristic"
                    base["visual_evidence_path"] = page_result_path
                else:
                    base.update(
                        classify_remote(
                            image,
                            system_prompt,
                            prompt_for(page, entry.get("section")),
                            args.host,
                            args.model,
                            args.timeout,
                        )
                    )
                    base["classification_source"] = "mac96-fallback"
                    base["visual_evidence_path"] = evidence_path
        records.append(base)
        _write_report(output, report_for("running"))
    status = "complete" if not diagnostics and all(item.get("decision") in {"retain", "crop-required", "candidate-remove"} for item in records) else "needs-review"
    report = report_for(status)
    _write_report(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
