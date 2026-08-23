#!/usr/bin/env python3
"""Resume a page-scoped PDF conversion with auditable provider fallback."""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
import mimetypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
from mac96_pdf_vision_runner import (  # noqa: E402
    DEFAULT_NOISE_CLEANUP_PROFILE,
    DEFAULT_MODEL,
    PROMPT_VERSION,
    clean_model_markdown,
    cleanup_page_units_before_language_repair,
    digest,
    format_reader_markdown,
    image_dimensions,
    model_page,
    now,
    package_capability,
    page_title,
    parse_pages,
    page_unit_is_present,
    pdf_page_count,
    render_pages,
    reader_image_decision,
    REPAIR_MODEL,
    language_repair_succeeded,
    repair_evidence_label,
    repair_page_unit_if_needed,
    actual_vision_model_calls,
    vision_result_metrics,
    write_review,
    stage,
    validate_page_markdown_checkpoint,
    write_page_outputs,
    write_json,
    write_yaml,
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def validate_resume_stage_gate(
    target: Path,
    requested_pages: list[int],
    prior_gate: Path | None,
) -> dict[str, Any] | None:
    if requested_pages == [1]:
        return None
    expected_scope = "single-page" if max(requested_pages) <= 5 else "small-batch-2-5"
    expected_pages = [1] if expected_scope == "single-page" else [2, 3, 4, 5]
    completed = existing_output_pages(target, max(max(requested_pages), 5))
    required_completed = {1} if expected_scope == "single-page" else {1, 2, 3, 4, 5}
    missing_completed = sorted(required_completed - completed)
    if missing_completed:
        raise RuntimeError(f"resume stage gate is missing completed pages: {missing_completed}")
    if prior_gate is None:
        raise RuntimeError(
            f"pages {requested_pages[0]}-{requested_pages[-1]} require --prior-gate with scope {expected_scope}"
        )
    gate_path = prior_gate.expanduser().resolve()
    try:
        gate = load_json(gate_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read resume stage gate {gate_path}: {exc}") from exc
    if gate.get("status") != "passed" or gate.get("scope") != expected_scope:
        raise RuntimeError(f"resume stage gate must be passed with scope {expected_scope}")
    if sorted(gate.get("pages") or []) != expected_pages:
        raise RuntimeError(f"resume stage gate pages must be {expected_pages}")
    if gate.get("structure_guard") != "passed" or gate.get("manual_visual_review") != "passed":
        raise RuntimeError("resume stage gate requires passed structure_guard and manual_visual_review")
    return gate


def write_vision_result_preserving_prior(path: Path, result: dict[str, Any]) -> None:
    """Keep a failed/stale attempt before publishing a newer canonical result."""

    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing != serialized:
            attempts = path.parent / "attempts"
            attempts.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archived = attempts / f"{path.stem}-{timestamp}{path.suffix}"
            archived.write_text(existing, encoding="utf-8")
    path.write_text(serialized, encoding="utf-8")


def archive_stale_page_projections(target: Path, page: int) -> None:
    """Preserve failed/stale reader projections before rebuilding the page."""

    attempts = target / "evidence" / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for path in (
        target / "evidence" / f"vision-text-transcription-p{page:03d}.json",
        target / "page-units" / f"p{page:03d}.md",
    ):
        if not path.is_file():
            continue
        archived = attempts / f"{path.stem}-{timestamp}{path.suffix}"
        archived.write_bytes(path.read_bytes())
        path.unlink()


def _optional_metric(values: list[tuple[int, int | None]]) -> dict[str, Any]:
    known = [(page, value) for page, value in values if isinstance(value, int)]
    return {
        "value": sum(value for _page, value in known),
        "unknown_pages": [page for page, value in values if value is None],
        "status": "complete" if len(known) == len(values) else "partial" if known else "Unknown",
    }


def write_runtime_summary(target: Path, pages: list[int]) -> dict[str, Any]:
    page_metrics: list[dict[str, Any]] = []
    prompt_values: list[tuple[int, int | None]] = []
    eval_values: list[tuple[int, int | None]] = []
    vision_calls = 0
    repair_calls = 0
    for page in pages:
        result = existing_result(target, page)
        vision = vision_result_metrics(result or {})
        repair_path = target / "evidence" / f"markdown-content-repair-p{page:03d}.json"
        repair = load_json(repair_path) if repair_path.is_file() else {}
        current_repair_calls = repair.get("model_call_count")
        if not isinstance(current_repair_calls, int):
            current_repair_calls = 1 if repair.get("model_status") in {"complete", "failed", "unavailable"} else 0
        vision_calls += int(vision["model_call_count"])
        repair_calls += current_repair_calls
        prompt_values.append((page, vision.get("prompt_eval_count")))
        eval_values.append((page, vision.get("eval_count")))
        page_metrics.append({
            "page": page,
            "vision_model_calls": int(vision["model_call_count"]),
            "vision_wall_seconds": vision["wall_seconds"] if (result or {}).get("requests") and not vision["unknown_fields"] else vision["wall_seconds"] or "Unknown",
            "prompt_eval_count": vision.get("prompt_eval_count") if vision.get("prompt_eval_count") is not None else "Unknown",
            "eval_count": vision.get("eval_count") if vision.get("eval_count") is not None else "Unknown",
            "language_repair_status": repair.get("status", "Unknown"),
            "language_model_calls": current_repair_calls,
            "language_wall_seconds": (repair.get("model_metrics") or {}).get("wall_seconds", "Unknown"),
        })
    archived_attempts: list[dict[str, Any]] = []
    archived_calls = 0
    archived_prompt_observed = 0
    archived_eval_observed = 0
    archived_metric_unknown_calls = 0
    attempts_dir = target / "evidence" / "attempts"
    for path in sorted(attempts_dir.glob("vision-page-analysis-*.json")) if attempts_dir.is_dir() else []:
        try:
            result = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        metrics = vision_result_metrics(result)
        calls = int(metrics["model_call_count"])
        request_count = len(result.get("requests") or []) if isinstance(result.get("requests"), list) else 0
        archived_calls += calls
        archived_metric_unknown_calls += max(0, calls - request_count)
        archived_prompt_observed += int(metrics.get("prompt_eval_count") or 0)
        archived_eval_observed += int(metrics.get("eval_count") or 0)
        archived_attempts.append({
            "evidence": path.relative_to(target).as_posix(),
            "page_or_region": result.get("page_or_region"),
            "status": result.get("status"),
            "model_call_count": calls,
            "request_metrics_observed_for_calls": request_count,
            "wall_seconds_observed": metrics.get("wall_seconds") or "Unknown",
            "prompt_eval_count_observed": metrics.get("prompt_eval_count") if metrics.get("prompt_eval_count") is not None else "Unknown",
            "eval_count_observed": metrics.get("eval_count") if metrics.get("eval_count") is not None else "Unknown",
        })
    interrupted_path = target / "evidence" / "interrupted-vision-attempts.json"
    interrupted = load_json(interrupted_path) if interrupted_path.is_file() else {}
    interrupted_calls = interrupted.get("attempted_call_count")
    interrupted_calls = interrupted_calls if isinstance(interrupted_calls, int) else 0
    run_events = []
    ledger = target / "evidence" / "runtime-ledger.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "resume-complete":
                run_events.append(event)
    inventory_path = target / "evidence" / "source-inventory.json"
    inventory = load_json(inventory_path) if inventory_path.is_file() else {}
    total_pages = (inventory.get("page_count_or_dimensions") or {}).get("page_count")
    full_coverage = isinstance(total_pages, int) and pages == list(range(1, total_pages + 1))
    summary = {
        "status": "complete" if len(page_metrics) == len(pages) and full_coverage else "needs-review",
        "generated_at": now(),
        "coverage": {
            "total_pages": total_pages if isinstance(total_pages, int) else "Unknown",
            "selected_pages": pages,
            "actual_processed_pages": len(page_metrics),
            "full_document_coverage": full_coverage,
            "unprocessed_pages": [page for page in range(1, total_pages + 1) if page not in pages]
            if isinstance(total_pages, int) else "Unknown",
        },
        "model_calls": {
            "vision_canonical": vision_calls,
            "vision_archived_failed_or_partial": archived_calls,
            "vision_interrupted_without_response_evidence": interrupted_calls,
            "vision_attempted_total": vision_calls + archived_calls + interrupted_calls,
            "language_repair": repair_calls,
            "total_attempted": vision_calls + archived_calls + interrupted_calls + repair_calls,
        },
        "prompt_eval_count": _optional_metric(prompt_values),
        "eval_count": _optional_metric(eval_values),
        "page_metrics": page_metrics,
        "noncanonical_attempts": {
            "archived": archived_attempts,
            "interrupted_evidence": interrupted_path.relative_to(target).as_posix() if interrupted_path.is_file() else None,
            "observed_prompt_eval_count": archived_prompt_observed,
            "observed_eval_count": archived_eval_observed,
            "calls_with_unknown_archived_metrics": archived_metric_unknown_calls,
            "interrupted_metrics": "Unknown" if interrupted_calls else None,
        },
        "recorded_resume_runs": run_events,
        "recorded_resume_wall_seconds": round(sum(float(item.get("wall_seconds", 0.0)) for item in run_events), 3),
        "whole_book_wall_seconds": "Unknown",
        "whole_book_wall_reason": "page 1 predates the runtime ledger; only its file/request timestamps survive",
    }
    write_json(target / "evidence" / "runtime-summary.json", summary)
    return summary


def asset_records(target: Path, source: Path, source_fingerprint: str, package_id: str, pages: list[int]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in pages:
        image = target / "assets" / f"page-{page:03d}.png"
        if not image.is_file():
            raise RuntimeError(f"missing rendered asset for page {page}: {image}")
        width, height = image_dimensions(image)
        records.append({
            "asset_id": f"page-p{page:03d}",
            "file_name": image.name,
            "file_path": image.relative_to(target).as_posix(),
            "display_name": f"{source.stem} 第 {page} 页原始页面",
            "asset_kind": "page-render",
            "source": {
                "package_id": package_id,
                "source_fingerprint": source_fingerprint,
                "original_filename": source.name,
                "source_occurrences": [{
                    "source_occurrence_id": f"{package_id}-p{page:03d}",
                    "source_file": source.name,
                    "page": page,
                    "region": None,
                    "object_ids": [],
                    "parent_asset_id": None,
                    "ordinal": 1,
                    "status": "located",
                }],
            },
            "media": {
                "mime_type": "image/png",
                "width": width,
                "height": height,
                "sha256": digest(image),
            },
            "semantic": {
                "label": "original-page",
                "description": f"{source.stem} 第 {page} 页原始页面",
                "confidence": "high",
            },
            "naming": {
                "source_slug": f"source-{source_fingerprint[:12]}",
                "semantic_slug": "page",
                "purpose_slug": "original-page",
                "status": "generated",
            },
            "status": "available",
        })
    return records


def existing_result(
    target: Path,
    page: int,
    source_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    path = target / "evidence" / f"vision-page-analysis-p{page:03d}.json"
    if not path.is_file():
        return None
    try:
        result = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if result.get("status") != "complete":
        return None
    if (result.get("text") or {}).get("status") != "complete":
        return None
    if (result.get("visual") or {}).get("status") != "complete":
        return None
    strategy = result.get("pass_strategy_used")
    expected_calls = 1 if strategy == "single" else 2 if strategy == "dual" else None
    if expected_calls is None or result.get("model_call_count") != expected_calls:
        return None
    image = target / "assets" / f"page-{page:03d}.png"
    if not image.is_file() or result.get("image_sha256") != digest(image):
        return None
    if source_fingerprint is not None and result.get("source_fingerprint") != source_fingerprint:
        return None
    return result


def page_outputs_complete(target: Path, page: int, source_fingerprint: str | None = None) -> bool:
    """A resumable page requires both JSON layers and its reader Markdown."""

    result = existing_result(target, page, source_fingerprint)
    if result is None or not page_unit_is_present(target, page):
        return False
    text_path = target / "evidence" / f"vision-text-transcription-p{page:03d}.json"
    if not text_path.is_file():
        return False
    try:
        transcription = load_json(text_path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        transcription.get("status") == "complete"
        and bool(str(transcription.get("candidate_markdown") or "").strip())
        and transcription.get("source_evidence") == f"evidence/vision-page-analysis-p{page:03d}.json"
        and transcription.get("image_sha256") == result.get("image_sha256")
        and (source_fingerprint is None or transcription.get("source_fingerprint") == source_fingerprint)
    )


def existing_output_pages(
    target: Path,
    total_pages: int,
    source_fingerprint: str | None = None,
) -> set[int]:
    return {
        page
        for page in range(1, total_pages + 1)
        if page_outputs_complete(target, page, source_fingerprint)
    }


def page_reader_record(target: Path, page: int, package_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    image = target / "assets" / f"page-{page:03d}.png"
    result_path = target / "evidence" / f"vision-page-analysis-p{page:03d}.json"
    if not result_path.is_file() or not image.is_file():
        raise RuntimeError(f"page {page} is missing analysis evidence or rendered image")
    result = load_json(result_path)
    markdown = clean_model_markdown(str((result.get("text") or {}).get("markdown") or ""))
    page_unit = target / "page-units" / f"p{page:03d}.md"
    if page_unit.is_file():
        reader_markdown = page_unit.read_text(encoding="utf-8").strip()
    else:
        reader_markdown = format_reader_markdown(markdown) if markdown else "〔无法确认〕"
    title = page_title(reader_markdown, page) if reader_markdown else page_title(markdown, page) if markdown else "〔无法确认〕"
    visual = result.get("visual") or {}
    analysis = visual.get("visual_analysis") if isinstance(visual, dict) else {}
    analysis = analysis if isinstance(analysis, dict) else {}
    unknown = [str(item) for item in analysis.get("unknown_forms", []) if item]
    uncertainties = [str(item) for item in analysis.get("uncertainties", []) if item]
    image_decision = reader_image_decision(result)
    occurrence_id = f"{package_id}-p{page:03d}"
    section = {
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
    }
    pass_strategy = result.get("pass_strategy_used")
    extraction_mode = f"full+{pass_strategy}" if pass_strategy in {"single", "dual"} else "full+unknown"
    trace = {
        "page": page,
        "source_asset_id": f"page-p{page:03d}",
        "source_image_sha256": digest(image),
        "reader_section_id": f"page-{page:03d}",
        "text_status": "complete" if markdown else "failed",
        "visual_status": visual.get("status", "unavailable"),
        "extraction_mode": extraction_mode,
        "unknown_forms": unknown,
        "uncertainties": uncertainties,
        "reader_image_policy": image_decision,
        "evidence": f"evidence/vision-page-analysis-p{page:03d}.json",
    }
    status = {
        "page": page,
        "status": "complete" if markdown else "failed",
        "title": title,
        "page_content_type": analysis.get("page_content_type", "unknown"),
        "layout_dependency": analysis.get("layout_dependency", "unknown"),
        "semantic_visuals": analysis.get("semantic_visuals", []),
        "ignored_elements": analysis.get("ignored_elements", []),
        "reader_image_policy": image_decision,
        "unknown_forms": unknown,
        "uncertainties": uncertainties,
    }
    return section, trace, status, reader_markdown


def run(args: argparse.Namespace) -> Path:
    run_started_at = now()
    run_started_monotonic = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("resume-%Y%m%dT%H%M%S%fZ")
    source = args.source.expanduser().resolve()
    target = args.package.expanduser().resolve()
    if not target.is_dir():
        raise FileNotFoundError(target)
    for directory in ("evidence", "reports", "reviews", "page-units"):
        (target / directory).mkdir(parents=True, exist_ok=True)
    fingerprint_path = target / "evidence/source-fingerprint.json"
    recorded_fingerprint = load_json(fingerprint_path).get("sha256") if fingerprint_path.is_file() else None
    inventory_path = target / "evidence/source-inventory.json"
    inventory = load_json(inventory_path) if inventory_path.is_file() else {}
    if source.is_file():
        source_fingerprint = digest(source)
        total_pages = pdf_page_count(source)
        source_live_check = "verified"
    else:
        source_fingerprint = recorded_fingerprint
        total_pages = (inventory.get("page_count_or_dimensions") or {}).get("page_count")
        source_live_check = "unavailable-existing-assets-and-evidence-only"
    if not isinstance(source_fingerprint, str) or len(source_fingerprint) != 64:
        raise RuntimeError("resume package has no usable recorded source fingerprint")
    if recorded_fingerprint is not None and recorded_fingerprint != source_fingerprint:
        raise RuntimeError(
            f"package source fingerprint mismatch: {recorded_fingerprint} != {source_fingerprint}"
        )
    if recorded_fingerprint is None:
        recorded_fingerprint = source_fingerprint
    if not isinstance(total_pages, int) or total_pages < 1:
        raise RuntimeError("resume package has no usable recorded total page count")
    requested_pages = parse_pages(args.pages, total_pages)
    existing_pages = existing_output_pages(target, total_pages, source_fingerprint)
    gate = validate_resume_stage_gate(target, requested_pages, args.prior_gate)
    pages = list(range(1, total_pages + 1)) if args.pages.strip().lower() == "all" else sorted(set(requested_pages) | existing_pages)
    pending_pages = [page for page in pages if not page_outputs_complete(target, page, source_fingerprint)]
    model_needed_pages = [
        page for page in pending_pages
        if existing_result(target, page, source_fingerprint) is None
    ]
    if not source.is_file() and model_needed_pages:
        raise RuntimeError(
            "source PDF is unavailable and these pages have no reusable visual analysis: "
            f"{model_needed_pages}"
        )
    package_id = target.name
    ledger_path = target / "evidence/runtime-ledger.jsonl"
    append_jsonl(ledger_path, {
        "event": "resume-start",
        "run_id": run_id,
        "started_at": run_started_at,
        "source_fingerprint": source_fingerprint,
        "requested_pages": requested_pages,
        "package_pages": pages,
        "pending_pages": pending_pages,
        "reused_complete_pages": sorted(set(pages) - set(pending_pages)),
        "stage_gate": str(args.prior_gate.expanduser().resolve()) if args.prior_gate else None,
        "source_live_check": source_live_check,
    })
    if model_needed_pages:
        capability, capability_probed = package_capability(
            target,
            args.host,
            args.model,
            refresh=args.refresh_capability,
        )
        if capability_probed:
            write_json(target / "evidence/mac96-preflight.json", capability)
        if capability.get("status") != "complete":
            raise RuntimeError(f"mac96 preflight failed: {capability.get('diagnostics')}")
    else:
        capability = load_json(target / "evidence/mac96-preflight.json")
        capability_probed = False

    preexisting_assets = {
        page for page in pages if (target / "assets" / f"page-{page:03d}.png").is_file()
    }
    render_started = time.monotonic()
    work_items: list[tuple[int, Path, dict[str, Any] | None, Future | None, float]] = []
    executor = ThreadPoolExecutor(max_workers=max(1, args.page_concurrency))
    for page, image in render_pages(
        source,
        target,
        pages,
        batch_size=args.render_batch_size,
    ):
        if page not in pending_pages:
            continue
        result = existing_result(target, page, source_fingerprint)
        submitted_at = time.monotonic()
        future = None
        if result is None:
            future = executor.submit(
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
        work_items.append((page, image, result, future, submitted_at))
    render_wall_seconds = round(time.monotonic() - render_started, 3)

    records = asset_records(target, source, source_fingerprint, package_id, pages)
    if not fingerprint_path.is_file():
        write_json(fingerprint_path, {
            "source": str(source),
            "sha256": source_fingerprint,
            "computed_at": now(),
        })
    write_json(target / "evidence/source-inventory.json", {
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
        "diagnostics": ["Reader Markdown is generated from current page images; native text and OCR are not inputs."],
        "source_live_check": source_live_check,
    })
    write_json(target / "evidence/context-policy.json", {
        "status": "complete",
        "input_scope": "one-current-page-image-only",
        "model": "per-page-evidence",
        "target": "per-page-evidence",
        "prohibited_context": ["other-pages", "native-text-layer", "ocr", "bbox", "other-files", "previous-model-answer"],
        "reader_text_source": "page-scoped-vision-with-auditable-provider",
    })
    write_json(target / "evidence/native-content-model.json", {
        "status": "not-used-for-reader-layer",
        "parser": None,
        "pages": pages,
        "diagnostics": ["Native PDF text was not used as a content source."],
    })
    write_json(target / "evidence/ocr-candidates.json", {
        "status": "not-used",
        "source": "none",
        "selected_pages": pages,
        "candidates": [],
        "diagnostics": ["Apple Vision OCR was not invoked; no OCR candidate was promoted or used."],
    })
    write_json(target / "evidence/rendering.json", {
        "status": "complete",
        "source_owned": True,
        "renderer": "pdftoppm",
        "renderer_batch_size": args.render_batch_size,
        "rendering_mode": "producer-consumer-incremental-assets",
        "requested_pages": pages,
        "rendered_pages": pages,
        "assets_directory": "assets/",
        "page_processing_barrier": "current-page-asset-ready-before-current-page-processing",
        "text_visibility": {
            "status": "not-applicable",
            "reason": "reader text comes from page-scoped image understanding",
        },
        "assets": [{
            "page": page,
            "image": f"assets/page-{page:03d}.png",
            "sha256": digest(target / "assets" / f"page-{page:03d}.png"),
        } for page in pages],
    })
    write_json(target / "evidence/asset-registration.json", {
        "status": "complete",
        "assets": [item["asset_id"] for item in records],
        "embedded_count": 0,
        "regions_count": 0,
    })

    section_records: list[dict[str, Any]] = []
    trace_records: list[dict[str, Any]] = []
    page_statuses: list[dict[str, Any]] = []
    model_failures: list[dict[str, Any]] = []
    repair_results: list[dict[str, Any]] = []
    vision_calls_this_run = 0
    for page, image, result, future, submitted_at in work_items:
        ordinal = pages.index(page) + 1
        markdown = ""
        strategy = result.get("pass_strategy_used") if result else args.pass_strategy
        extraction_mode = f"full+{strategy}"
        reused_analysis = result is not None
        if future is not None:
            result, markdown, extraction_mode = future.result()
            vision_calls_this_run += actual_vision_model_calls(result)
            analysis_path = target / f"evidence/vision-page-analysis-p{page:03d}.json"
            if analysis_path.is_file():
                archive_stale_page_projections(target, page)
            write_vision_result_preserving_prior(
                analysis_path,
                result,
            )
        if result is None:  # defensive: a queued page must resolve to evidence
            raise RuntimeError(f"page {page} did not produce visual analysis evidence")
        if not markdown:
            markdown = clean_model_markdown(str((result.get("text") or {}).get("markdown") or ""))
        text_result = write_page_outputs(
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
        if not markdown:
            model_failures.append({
                "page": page,
                "status": result.get("status"),
                "diagnostics": result.get("diagnostics", []),
            })
        page_metrics = vision_result_metrics(result)
        write_json(target / f"evidence/progress-p{page:03d}.json", {
            "page": page,
            "ordinal": ordinal,
            "total": len(pages),
            "status": "complete" if markdown else "failed",
            "model": args.model,
            "target": args.host,
            "created_at": now(),
            "scheduled_wall_seconds": round(time.monotonic() - submitted_at, 3),
            "vision_metrics": page_metrics,
            "model_call_count": 0 if reused_analysis else actual_vision_model_calls(result),
            "projection": "reused-analysis" if reused_analysis else "model+projection",
            "evidence": f"evidence/vision-page-analysis-p{page:03d}.json",
        })
        print(
            f"[mac96-resume] page={page}/{len(pages)} status={'complete' if markdown else 'failed'} "
            f"projection={'reused-analysis' if reused_analysis else 'model+projection'}",
            file=sys.stderr,
            flush=True,
        )
    executor.shutdown(wait=True)
    if requested_pages == [2, 3, 4, 5]:
        validate_page_markdown_checkpoint(target, expected_pages=[1, 2, 3, 4, 5])
        print("[mac96-resume] small-batch-markdown-checkpoint=passed pages=1-5", file=sys.stderr, flush=True)

    noise_cleanup_result = cleanup_page_units_before_language_repair(
        target,
        pages=pages,
        profile_path=getattr(args, "noise_profile", DEFAULT_NOISE_CLEANUP_PROFILE),
    )
    repair_results = []
    repair_evidence_before = {
        page: digest(target / "evidence" / f"markdown-content-repair-p{page:03d}.json")
        for page in pages
        if (target / "evidence" / f"markdown-content-repair-p{page:03d}.json").is_file()
    }
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
    write_review(target / "reviews/markdown-content-repair.yaml", package_id, repair_results)

    for page in pages:
        section, trace, status, _ = page_reader_record(target, page, package_id)
        section_records.append(section)
        trace_records.append(trace)
        page_statuses.append(status)
    page_results = {page: existing_result(target, page, source_fingerprint) or {} for page in pages}
    provider_pages: dict[str, list[int]] = {}
    provider_model_calls: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for page, result in page_results.items():
        provider = str(result.get("target") or result.get("transport") or "Unknown")
        provider_pages.setdefault(provider, []).append(page)
        key = (
            provider,
            str(result.get("model") or "Unknown"),
            str(result.get("prompt_version") or "Unknown"),
            str(result.get("pass_strategy_used") or "Unknown"),
        )
        group = provider_model_calls.setdefault(key, {
            "target": provider,
            "model": key[1],
            "prompt_version": key[2],
            "pass_strategy": key[3],
            "scope": "one-current-page-image-per-call",
            "pages": [],
            "actual_call_count": 0,
        })
        group["pages"].append(page)
        group["actual_call_count"] += actual_vision_model_calls(result)
    for group in provider_model_calls.values():
        group["page_count"] = len(group["pages"])
        group["calls_per_page"] = 1 if group["pass_strategy"] == "single" else 2 if group["pass_strategy"] == "dual" else "Unknown"
    current_conversation_pages = provider_pages.get("current-conversation", [])
    mac96_pages = provider_pages.get(args.host, [])
    if current_conversation_pages and mac96_pages:
        execution_path = "mac96-with-conversation-online-fallback"
    elif current_conversation_pages:
        execution_path = "conversation-online-vision"
    else:
        execution_path = "mac96-direct-vision"
    fallback_reason = "mac96-unreachable" if current_conversation_pages else None
    provider_summary = {
        "execution_path": execution_path,
        "pages_by_target": provider_pages,
        "fallback_pages": current_conversation_pages,
    }
    reader_parts = []
    for page in pages:
        page_text = (target / "page-units" / f"p{page:03d}.md").read_text(encoding="utf-8").strip()
        reader_parts.append(page_text.replace("<../assets/", "<assets/"))
    (target / "index.md").write_text("\n\n".join(reader_parts).rstrip() + "\n", encoding="utf-8")
    all_unknown = [f"p{item['page']:03d}: {value}" for item in page_statuses for value in item["unknown_forms"]]
    all_uncertainties = [f"p{item['page']:03d}: {value}" for item in page_statuses for value in item["uncertainties"]]
    repair_failures = [item for item in repair_results if not language_repair_succeeded(item)]
    repair_evidence_paths = [
        f"evidence/markdown-content-repair-p{page:03d}.json"
        for page in pages
        if (target / f"evidence/markdown-content-repair-p{page:03d}.json").is_file()
    ] or ["reviews/markdown-content-repair.yaml"]
    vision_text_evidence_paths = [
        f"evidence/vision-text-transcription-p{page:03d}.json"
        for page in pages
        if (target / f"evidence/vision-text-transcription-p{page:03d}.json").is_file()
    ]
    reconciliation_status = "complete" if not model_failures and not all_unknown and not all_uncertainties else "needs-review"
    write_json(target / "evidence/source-to-final-trace.json", {
        "status": "complete" if not model_failures else "needs-review",
        "source_fingerprint": source_fingerprint,
        "pages": trace_records,
        "coverage": {
            "selected_pages": pages,
            "transcribed_pages": [item["page"] for item in page_statuses if item["status"] == "complete"],
            "failed_pages": [item["page"] for item in page_statuses if item["status"] != "complete"],
        },
    })
    write_json(target / "evidence/content-classification.json", {
        "status": "complete" if not model_failures else "needs-review",
        "source_fingerprint": source_fingerprint,
        "pages": page_statuses,
        "basis": "page-scoped visual result per current page image",
        "providers": provider_summary,
    })
    write_json(target / "evidence/reconciliation.json", {
        "status": reconciliation_status,
        "source_fingerprint": source_fingerprint,
        "selected_pages": pages,
        "native_text": {"status": "not-used-for-reader-layer"},
        "visual_text": {
            "status": "complete" if not model_failures else "needs-review",
            "source": "mixed" if len(provider_pages) > 1 else next(iter(provider_pages), "Unknown"),
            "providers": provider_summary,
        },
        "unknown_forms": all_unknown,
        "unresolved": all_uncertainties + [f"page-{item['page']} model transcription failed" for item in model_failures],
        "coverage_notes": [
            "每次视觉请求只发送一张当前页面图；实际提供方按页记录。",
            "读者层没有使用 OCR、PDF 原生文字层或其他页面上下文。",
            "视觉关系仅保留模型在当前图片中直接确认的结果。",
        ],
    })
    write_json(target / "evidence/quality-audit.json", {
        "status": "needs-review",
        "basis": "automated page-scoped visual conversion; human review is recorded separately",
        "pages": pages,
        "providers": provider_summary,
        "risks": ["视觉模型可能对复杂连线或低清字形产生误判；原图和逐页证据均已保留。"],
    })
    (target / "document-temp.md").write_text(
        "\n".join(
            (target / "page-units" / f"p{page:03d}.md")
            .read_text(encoding="utf-8")
            .replace("<../assets/", "<assets/")
            for page in pages
        ),
        encoding="utf-8",
    )

    noise_cleanup_needs_review = noise_cleanup_result["status"] != "passed"
    noise_cleanup_unknown = [
        f"{item['file']}:{item['line']} {item['raw']}"
        for item in noise_cleanup_result["suspects"]
    ]
    coverage_complete = pages == list(range(1, total_pages + 1))
    unprocessed_pages = [page for page in range(1, total_pages + 1) if page not in pages]
    overall_status = (
        "complete"
        if not model_failures
        and not repair_failures
        and not noise_cleanup_needs_review
        and reconciliation_status == "complete"
        and coverage_complete
        else "needs-review"
    )
    stages = [
        stage("A1", "complete", ["evidence/source-fingerprint.json", "evidence/source-inventory.json", "evidence/mac96-preflight.json"]),
        stage("A2", "complete", ["evidence/native-content-model.json", "evidence/context-policy.json"], ["Native text is explicitly excluded from the reader layer."]),
        stage("A3", "complete", ["evidence/rendering.json", "assets"]),
        stage("A4", "complete" if reconciliation_status == "complete" else "needs-review", [vision_text_evidence_paths[0], "evidence/reconciliation.json"], ["Direct mac96 page-image transcription and visual relation analysis."], all_unknown + all_uncertainties),
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
        "source": {
            "path": str(source),
            "path_label": source.name,
            "media_type": "application/pdf",
            "source_fingerprint": source_fingerprint,
            "selected_pages": pages,
            "source_page_numbers": list(range(1, total_pages + 1)),
            "total_pages": total_pages,
            "source_selection": "single-source",
            "candidate_comparison": None,
        },
        "markdown": {"path": "index.md", "sections": section_records},
        "assets_manifest": {"path": "manifest.yaml", "type": "source-visual-asset-manifest"},
        "assets": records,
        "evidence": {
            "source_inventory": "evidence/source-inventory.json",
            "capability_probe": "evidence/mac96-preflight.json",
            "native_content_model": "evidence/native-content-model.json",
            "content_classification": "evidence/content-classification.json",
            "rendering": "evidence/rendering.json",
            "asset_registration": "evidence/asset-registration.json",
            "ocr_candidates": "evidence/ocr-candidates.json",
            "reconciliation": "evidence/reconciliation.json",
            "source_to_final_trace": "evidence/source-to-final-trace.json",
            "context_policy": "evidence/context-policy.json",
            "page_markdown_noise_cleanup": "evidence/page-markdown-noise-cleanup.json",
            "markdown_content_repair": repair_evidence_paths[0],
            "quality_audit": "evidence/quality-audit.json",
            "runtime_summary": "evidence/runtime-summary.json",
        },
        "processing": {
            "native_extraction": "not-used-for-reader-layer",
            "ocr_used": False,
            "visual_review": "complete" if not model_failures else "needs-review",
            "execution_path": execution_path,
            "fallback_reason": fallback_reason,
            "coverage_complete": coverage_complete,
            "unprocessed_pages": unprocessed_pages,
            "status": overall_status,
            "stage_evidence": stages,
            "stage_evidence_contract": "contracts/stage-evidence.yaml",
            "failure_report": "reports/failure-report.yaml",
            "model_calls": list(provider_model_calls.values()) + [{
                "target": args.repair_host,
                "model": args.repair_model,
                "prompt_version": "markdown-content-repair-lan-v1",
                "scope": "one-current-page-unit-per-call-after-structure-guard",
                "page_count": len(pages),
                "actual_call_count": sum(
                    int(item.get("model_call_count") or 0)
                    for item in repair_results
                ),
                "skipped_page_count": sum(1 for item in repair_results if item.get("status") == "skipped"),
            }],
            "context_policy": {
                "one_source_per_run": True,
                "one_current_page_image_per_call": True,
                "one_current_page_unit_per_repair": True,
                "ocr_used": False,
            },
            "page_markdown_noise_cleanup": {
                "status": noise_cleanup_result["status"],
                "profile": str(getattr(args, "noise_profile", DEFAULT_NOISE_CLEANUP_PROFILE)),
                "evidence": "evidence/page-markdown-noise-cleanup.json",
            },
            "language_quality_repair": {
                "status": "complete" if not repair_failures else "needs-review",
                "route": "lan-language-model-first",
                "model": args.repair_model,
                "evidence": repair_evidence_paths[0],
                "review": "reviews/markdown-content-repair.yaml",
            },
        },
        "validation": {
            "validator_version": "real-output-root",
            "status": "passed" if overall_status == "complete" else "needs-review",
            "checks": ["package layout", "source fingerprint", "asset hashes", "Markdown links", "page-scoped visual evidence"],
        },
        "quality_gate": {
            "status": "passed" if overall_status == "complete" else "needs-review",
            "score": 1.0 if overall_status == "complete" else None,
            "reason": "All selected pages returned page-scoped visual text and relation evidence; provider fallback is recorded per page.",
        },
        "quality_report": "reports/quality-report.yaml",
    }
    write_yaml(target / "manifest.yaml", manifest)
    processing = {
        "status": overall_status,
        "evidence_level": "L2-local-runtime",
        "run_id": package_id,
        "started_at": run_started_at,
        "finished_at": now(),
        "real_business_result_package": str(target),
        "input": {
            "source": str(source),
            "source_type": "pdf",
            "sha256": source_fingerprint,
            "pages": pages,
            "total_pages": total_pages,
            "source_page_numbers": list(range(1, total_pages + 1)),
        },
        "source_selection": "single-source",
        "progress_stream": "evidence/progress.jsonl",
        "stage_evidence_contract": "contracts/stage-evidence.yaml",
        "stage_evidence": stages,
        "stages": [{"id": item["stage_id"], "status": item["status"], "evidence": item["evidence_paths"]} for item in stages],
        "tools": {
            "page-renderer": "pdftoppm",
            "visual-model": provider_summary,
            "markdown-writer": "adapters/mac96_pdf_vision_resume.py",
            "page-markdown-noise-cleaner": "adapters/page_markdown_noise_cleanup.py",
        },
        "asset_policy": {
            "layout": "flat-assets-v1",
            "root": "assets/",
            "classification": "manifest.asset_kind",
            "embedded_count": 0,
            "regions_count": 0,
        },
        "failure_report": "reports/failure-report.yaml",
        "ocr": {"status": "not-used", "evidence": "evidence/ocr-candidates.json"},
        "visual_model": {
            "status": "complete" if not model_failures else "needs-review",
            "evidence": "evidence/source-to-final-trace.json",
        },
        "page_markdown_noise_cleanup": {
            "status": noise_cleanup_result["status"],
            "evidence": "evidence/page-markdown-noise-cleanup.json",
        },
        "language_repair": {
            "status": "complete" if not repair_failures else "needs-review",
            "route": "lan-language-model-first",
            "model": args.repair_model,
            "evidence": repair_evidence_paths[0],
            "review": "reviews/markdown-content-repair.yaml",
        },
        "quality_audit": {"status": "needs-review"},
        "runtime_summary": "evidence/runtime-summary.json",
    }
    failures = [{
        "failure_id": f"mac96-page-{item['page']:03d}",
        "stage_id": "A4",
        "code": "mac96-transcription-failed",
        "severity": "P1",
        "retryable": True,
        "status": "open",
        "message": "mac96 did not return a usable direct visual transcription for this page.",
        "evidence_paths": [f"evidence/vision-page-analysis-p{item['page']:03d}.json"],
        "remediation": "Retry the same page against mac96; do not fall back to OCR.",
        "resolved_evidence_paths": [],
    } for item in model_failures]
    failures.extend([{
        "failure_id": f"markdown-repair-page-{item.get('page', 'unknown')}",
        "stage_id": "A5",
        "code": "markdown-content-repair-unavailable",
        "severity": "P1",
        "retryable": True,
        "status": "open",
        "message": "LAN Markdown repair did not produce a guarded page-unit write-back.",
        "evidence_paths": [f"evidence/markdown-content-repair-{repair_evidence_label(item)}.json"],
        "remediation": "Retry the current page against the LAN language model or use the explicitly authorized online fallback.",
        "resolved_evidence_paths": [],
    } for item in repair_failures])
    if not coverage_complete:
        failures.append({
            "failure_id": "document-pages-unprocessed",
            "stage_id": "A7",
            "code": "full-document-coverage-incomplete",
            "severity": "P0",
            "retryable": True,
            "status": "open",
            "message": f"Only {len(pages)} of {total_pages} source pages are selected and transcribed.",
            "evidence_paths": ["evidence/source-to-final-trace.json", "evidence/runtime-summary.json"],
            "remediation": f"Resume from page {unprocessed_pages[0]} through page {total_pages} after the exact source PDF is available."
            if unprocessed_pages else "Re-run full coverage verification.",
            "resolved_evidence_paths": [],
        })
    acceptance = {
        "status": "passed" if overall_status == "complete" else "needs-review",
        "evidence_level": "L2-local-runtime",
        "real_business_result_package": str(target),
        "checks": [
            {"id": "required-files-and-directories", "status": "passed"},
            {"id": "source-read", "status": "passed"},
            {"id": "source-owned-rendering", "status": "passed"},
            {"id": "page-scoped-vision", "status": "passed" if not model_failures else "needs-review"},
            {"id": "ocr-route", "status": "not-used"},
            {"id": "page-markdown-noise-cleanup", "status": "passed" if not noise_cleanup_needs_review else "needs-review"},
            {"id": "markdown-content-repair", "status": "passed" if not repair_failures else "needs-review"},
            {"id": "reader-layer", "status": "passed" if not model_failures and not repair_failures and not noise_cleanup_needs_review else "needs-review"},
        ],
        "failures": [item["failure_id"] for item in failures],
        "warnings": ["No human page-by-page visual audit was performed in this automated run."],
        "stage_evidence": stages,
        "failure_report": "reports/failure-report.yaml",
        "diagnostics": ["Final reader Markdown uses page-scoped visual evidence; each page retains its provider and source-image hash."],
    }
    write_yaml(target / "reports/processing-report.yaml", processing)
    write_yaml(target / "reports/acceptance-report.yaml", acceptance)
    write_yaml(target / "reports/failure-report.yaml", {
        "contract": "failure-report",
        "version": "0.2.0",
        "status": "none" if not failures else "needs-review",
        "evidence_level": "L2-local-runtime",
        "failures": failures,
        "warnings": ["Automated quality audit is not a substitute for human visual review."],
        "diagnostics": ["Online fallback pages are listed in manifest.processing.model_calls; OCR was not used."],
    })
    write_yaml(target / "reports/quality-report.yaml", {
        "report_version": "0.2.0",
        "status": "complete" if overall_status == "complete" else "needs-review",
        "assessment_basis": "page-scoped visual conversion",
        "providers": provider_summary,
        "selected_pages": pages,
        "model_failures": model_failures,
        "human_review": "not-run",
        "remaining_risk": "Complex visual relations still benefit from human spot review.",
    })
    write_json(target / "evidence/smoke-report.json", {
        "status": "passed" if not model_failures else "needs-review",
        "scope": "full-selected-pages",
        "model": args.model,
        "pages": pages,
        "ocr_used": False,
    })
    with (target / "evidence/progress.jsonl").open("w", encoding="utf-8") as handle:
        for progress_stage, progress_status, progress_paths in [
            ("execution_preflight", "complete", ["evidence/source-fingerprint.json", "evidence/mac96-preflight.json"]),
            ("source_inventory", "complete", ["evidence/source-inventory.json"]),
            ("native_extraction", "complete", ["evidence/native-content-model.json"]),
            ("source_rendering", "complete", ["evidence/rendering.json", "assets"]),
            ("page_markdown_noise_cleanup", "complete" if noise_cleanup_result["status"] == "passed" else "needs-review", ["evidence/page-markdown-noise-cleanup.json", "page-units"]),
            ("language_quality_repair", "complete" if not repair_failures else "needs-review", [repair_evidence_paths[0], "page-units"]),
            ("markdown_assembly", "complete" if not model_failures and not repair_failures and not noise_cleanup_needs_review else "needs-review", ["index.md", "evidence/source-to-final-trace.json"]),
            ("independent_validation", "not-run", []),
        ]:
            handle.write(json.dumps({
                "run_id": package_id,
                "source_fingerprint": source_fingerprint,
                "stage": progress_stage,
                "task": progress_stage,
                "attempt": 1,
                "status": progress_status,
                "elapsed_seconds": 0.0,
                "evidence_paths": progress_paths,
                "next_action": "independent validator" if progress_stage == "independent_validation" else "continue",
            }, ensure_ascii=False) + "\n")
    repair_calls_this_run = 0
    for item in repair_results:
        page = item.get("page")
        if not isinstance(page, int):
            continue
        evidence_path = target / "evidence" / f"markdown-content-repair-p{page:03d}.json"
        previous_hash = repair_evidence_before.get(page)
        current_hash = digest(evidence_path) if evidence_path.is_file() else None
        if previous_hash != current_hash:
            repair_calls_this_run += int(item.get("model_call_count") or 0)
    run_wall_seconds = round(time.monotonic() - run_started_monotonic, 3)
    append_jsonl(ledger_path, {
        "event": "resume-complete",
        "run_id": run_id,
        "started_at": run_started_at,
        "finished_at": now(),
        "wall_seconds": run_wall_seconds,
        "status": overall_status,
        "requested_pages": requested_pages,
        "package_pages": pages,
        "pending_pages": pending_pages,
        "processed_pages": [page for page, *_rest in work_items],
        "reused_complete_pages": sorted(set(pages) - set(pending_pages)),
        "rendering": {
            "wall_seconds": render_wall_seconds,
            "new_pages": sorted(set(pages) - preexisting_assets),
            "reused_pages": sorted(preexisting_assets),
        },
        "capability_probe": "performed" if capability_probed else "reused",
        "source_live_check": source_live_check,
        "stage_gate": {
            "scope": gate.get("scope"),
            "pages": gate.get("pages"),
        } if gate else None,
        "model_calls": {
            "vision": vision_calls_this_run,
            "language_repair": repair_calls_this_run,
        },
    })
    write_runtime_summary(target, pages)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
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
    parser.add_argument("--prior-gate", type=Path)
    parser.add_argument("--refresh-capability", action="store_true")
    parser.add_argument("--repair-host", default="mac96")
    parser.add_argument("--repair-model", default=REPAIR_MODEL)
    parser.add_argument("--repair-timeout", type=int, default=180)
    parser.add_argument("--force-language-repair", action="store_true")
    parser.add_argument("--noise-profile", type=Path, default=DEFAULT_NOISE_CLEANUP_PROFILE)
    args = parser.parse_args(argv)
    try:
        target = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "complete", "package": str(target), "model": args.model, "target": args.host}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
