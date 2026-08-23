#!/usr/bin/env python3
"""Import isolated current-conversation vision results into a real package.

Raw fallback JSON stays immutable. This adapter validates it against the
current render and source fingerprint, optionally applies a separately
recorded human-review projection, archives stale canonical evidence, and
writes the three resumable per-page outputs required by Workflow A.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))

from mac96_pdf_vision_resume import (  # noqa: E402
    append_jsonl,
    archive_stale_page_projections,
    load_json,
    validate_resume_stage_gate,
    write_vision_result_preserving_prior,
)
from mac96_pdf_vision_runner import (  # noqa: E402
    clean_model_markdown,
    digest,
    now,
    parse_pages,
    pdf_page_count,
    reader_image_decision,
    validate_page_markdown_checkpoint,
    write_json,
    write_page_outputs,
)

REQUIRED_ANALYSIS_FIELDS = (
    "page_content_type",
    "layout_dependency",
    "semantic_visuals",
    "ignored_elements",
    "reader_image_policy",
)


def review_items(path: Path | None, pages: list[int]) -> dict[int, dict[str, Any]]:
    if path is None:
        return {}
    review = load_json(path.expanduser().resolve())
    if review.get("status") != "passed" or review.get("manual_visual_review") != "passed":
        raise RuntimeError("online fallback review must record passed manual_visual_review")
    if not set(pages).issubset(set(review.get("pages") or [])):
        raise RuntimeError("online fallback review does not cover every imported page")
    items = review.get("items")
    if not isinstance(items, list):
        raise RuntimeError("online fallback review items must be a list")
    indexed = {item.get("page"): item for item in items if isinstance(item, dict)}
    missing = sorted(set(pages) - set(indexed))
    if missing:
        raise RuntimeError(f"online fallback review is missing page items: {missing}")
    return {int(page): item for page, item in indexed.items() if isinstance(page, int)}


def metric_or_unknown(value: Any) -> int | str:
    return value if isinstance(value, int) else "Unknown"


def canonical_result(
    *,
    raw: dict[str, Any],
    raw_path: Path,
    page: int,
    image: Path,
    source_fingerprint: str,
    review_item: dict[str, Any] | None,
) -> dict[str, Any]:
    if raw.get("page") != page:
        raise RuntimeError(f"fallback page mismatch for page {page}: {raw.get('page')}")
    if raw.get("provider") != "current-conversation":
        raise RuntimeError(f"fallback provider must be current-conversation for page {page}")
    if raw.get("model_call_count") != 1:
        raise RuntimeError(f"fallback page {page} must record exactly one model call")
    analysis = {
        "page_content_type": raw.get("page_content_type"),
        "layout_dependency": raw.get("layout_dependency"),
        "semantic_visuals": raw.get("semantic_visuals"),
        "ignored_elements": raw.get("ignored_elements"),
        "reader_image_policy": raw.get("reader_image_policy"),
        "relationships": raw.get("relationships", []),
        "tables_or_charts": raw.get("tables_or_charts", []),
        "unknown_forms": raw.get("unknown_forms", []),
        "uncertainties": raw.get("uncertainties", []),
        "visual_review_notes": raw.get("visual_review_notes"),
    }
    missing = [key for key in REQUIRED_ANALYSIS_FIELDS if analysis.get(key) is None]
    if missing:
        raise RuntimeError(f"fallback page {page} is missing fields: {missing}")
    markdown = clean_model_markdown(str(raw.get("markdown") or ""))
    if review_item:
        if review_item.get("corrected_markdown") is not None:
            markdown = clean_model_markdown(str(review_item["corrected_markdown"]))
        if review_item.get("remaining_uncertainties") is not None:
            analysis["uncertainties"] = review_item["remaining_uncertainties"]
        if review_item.get("remaining_unknown_forms") is not None:
            analysis["unknown_forms"] = review_item["remaining_unknown_forms"]
    if not markdown:
        raise RuntimeError(f"fallback page {page} returned empty Markdown")
    requested_at = raw.get("requested_at")
    finished_at = raw.get("finished_at") or now()
    result = {
        "contract": "vision-page-analysis",
        "version": "0.2.0",
        "input_asset_id": f"page-p{page:03d}",
        "source_fingerprint": source_fingerprint,
        "image_sha256": digest(image),
        "page_or_region": f"page-{page}",
        "prompt_version": "current-conversation-online-vision-v1",
        "transport": "current-conversation",
        "target": "current-conversation",
        "model": str(raw.get("model") or "Unknown"),
        "mode_requested": "full",
        "pass_strategy_requested": "single",
        "pass_strategy_used": "single",
        "model_call_count": 1,
        "requested_at": requested_at,
        "finished_at": finished_at,
        "status": "complete",
        "text": {
            "status": "complete",
            "source": "current-conversation",
            "markdown": markdown,
            "diagnostics": [],
        },
        "visual": {
            "status": "complete",
            "source": "current-conversation",
            "visual_analysis": analysis,
            "diagnostics": [],
        },
        "requests": [{
            "pass": "full",
            "requested_at": requested_at,
            "finished_at": finished_at,
            "prompt_version": "current-conversation-online-vision-v1",
            "status": "complete",
            "metrics": {
                "prompt_eval_count": metric_or_unknown(raw.get("prompt_eval_count")),
                "eval_count": metric_or_unknown(raw.get("eval_count")),
            },
        }],
        "run_attempts": [{
            "attempt": 1,
            "status": "complete",
            "model_call_count": 1,
            "diagnostics": [],
        }],
        "fallback_reason": "mac96-unreachable",
        "raw_fallback_evidence": raw_path.as_posix(),
        "manual_review": review_item or None,
        "diagnostics": ["Imported from an isolated current-conversation one-page visual fallback."],
    }
    decision = reader_image_decision(result)
    requested_action = (analysis.get("reader_image_policy") or {}).get("action")
    if decision["action"] != requested_action:
        raise RuntimeError(
            f"fallback page {page} reader image policy is contradictory: "
            f"requested={requested_action} safe={decision['action']}"
        )
    return result


def import_pages(args: argparse.Namespace) -> Path:
    source = args.source.expanduser().resolve()
    target = args.package.expanduser().resolve()
    if not target.is_dir():
        raise FileNotFoundError(target)
    fingerprint = load_json(target / "evidence/source-fingerprint.json")
    recorded_fingerprint = fingerprint.get("sha256")
    inventory = load_json(target / "evidence/source-inventory.json")
    if source.is_file():
        source_fingerprint = digest(source)
        total_pages = pdf_page_count(source)
        source_live_check = "verified"
    else:
        source_fingerprint = recorded_fingerprint
        total_pages = (inventory.get("page_count_or_dimensions") or {}).get("page_count")
        source_live_check = "unavailable-used-recorded-fingerprint-and-existing-render"
    if not isinstance(source_fingerprint, str) or len(source_fingerprint) != 64:
        raise RuntimeError("package does not contain a usable recorded source fingerprint")
    if not isinstance(total_pages, int) or total_pages < 1:
        raise RuntimeError("package does not contain a usable recorded total page count")
    if recorded_fingerprint != source_fingerprint:
        raise RuntimeError("package source fingerprint does not match source PDF")
    pages = parse_pages(args.pages, total_pages)
    gate = validate_resume_stage_gate(target, pages, args.prior_gate)
    reviews = review_items(args.review, pages)
    imported: list[int] = []
    for page in pages:
        image = target / "assets" / f"page-{page:03d}.png"
        raw_path = target / "evidence" / f"fallback-online-vision-p{page:03d}.json"
        if not image.is_file() or not raw_path.is_file():
            raise RuntimeError(f"page {page} is missing its render or raw fallback JSON")
        raw = load_json(raw_path)
        result = canonical_result(
            raw=raw,
            raw_path=raw_path.relative_to(target),
            page=page,
            image=image,
            source_fingerprint=source_fingerprint,
            review_item=reviews.get(page),
        )
        analysis_path = target / "evidence" / f"vision-page-analysis-p{page:03d}.json"
        if analysis_path.is_file():
            archive_stale_page_projections(target, page)
        write_vision_result_preserving_prior(analysis_path, result)
        write_page_outputs(
            target,
            page=page,
            image=image,
            source_fingerprint=source_fingerprint,
            host="current-conversation",
            model=result["model"],
            result=result,
            markdown=result["text"]["markdown"],
            extraction_mode="full+single",
        )
        validate_page_markdown_checkpoint(target, expected_pages=[page])
        write_json(target / "evidence" / f"progress-p{page:03d}.json", {
            "page": page,
            "status": "complete",
            "model": result["model"],
            "target": "current-conversation",
            "created_at": now(),
            "vision_metrics": {
                "model_call_count": 1,
                "wall_seconds": "Unknown",
                "prompt_eval_count": metric_or_unknown(raw.get("prompt_eval_count")),
                "eval_count": metric_or_unknown(raw.get("eval_count")),
            },
            "model_call_count": 1,
            "projection": "isolated-online-fallback+projection",
            "raw_evidence": raw_path.relative_to(target).as_posix(),
            "evidence": analysis_path.relative_to(target).as_posix(),
        })
        imported.append(page)
    append_jsonl(target / "evidence/runtime-ledger.jsonl", {
        "event": "online-fallback-import-complete",
        "finished_at": now(),
        "pages": imported,
        "model_calls": {"vision": len(imported), "language_repair": 0},
        "wall_seconds": "Unknown",
        "prompt_eval_count": "Unknown",
        "eval_count": "Unknown",
        "stage_gate": gate.get("scope") if gate else None,
        "raw_evidence_preserved": True,
        "source_live_check": source_live_check,
    })
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--prior-gate", type=Path)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args(argv)
    try:
        target = import_pages(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "complete", "package": str(target), "pages": args.pages}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
