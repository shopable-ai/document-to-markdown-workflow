#!/usr/bin/env python3
"""Validate human-reviewed recognition-route quality against benchmark evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CONTRACT = "recognition-route-quality-assessment"
VERSION = "0.1.0"
DIMENSIONS = (
    "heading_hierarchy",
    "paragraph_segmentation",
    "table_of_contents",
    "lists",
    "header_footer",
    "punctuation",
    "cross_page_continuity",
    "anomalous_content_preservation",
)
VERDICTS = {"pass", "fail", "needs-review", "not-applicable"}


class QualityValidationError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityValidationError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QualityValidationError(f"JSON root must be an object: {path}")
    return value


def record_key(value: dict[str, Any]) -> tuple[str, int, str]:
    route = value.get("route")
    page = value.get("page")
    image_sha = value.get("image_sha256")
    if not isinstance(route, str) or not isinstance(page, int) or not isinstance(image_sha, str):
        raise QualityValidationError("every benchmark/assessment record needs route, integer page and image_sha256")
    return route, page, image_sha


def validate(benchmark: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if benchmark.get("contract") != "recognition-route-benchmark":
        errors.append("benchmark contract mismatch")
    if assessment.get("contract") != CONTRACT or assessment.get("version") != VERSION:
        errors.append("assessment contract/version mismatch")
    source_fingerprint = benchmark.get("source_fingerprint")
    if assessment.get("source_fingerprint") != source_fingerprint:
        errors.append("assessment source_fingerprint does not match benchmark")

    raw_records = benchmark.get("records")
    reviews = assessment.get("assessments")
    if not isinstance(raw_records, list) or not isinstance(reviews, list):
        raise QualityValidationError("benchmark records and assessment assessments must be arrays")

    benchmark_records: dict[tuple[str, int, str], dict[str, Any]] = {}
    for record in raw_records:
        if not isinstance(record, dict):
            errors.append("benchmark contains a non-object record")
            continue
        try:
            key = record_key(record)
        except QualityValidationError as exc:
            errors.append(str(exc))
            continue
        if key in benchmark_records:
            errors.append(f"duplicate benchmark record: {key}")
        benchmark_records[key] = record

    reviewed: set[tuple[str, int, str]] = set()
    passed_reviews = 0
    for review in reviews:
        if not isinstance(review, dict):
            errors.append("assessment contains a non-object review")
            continue
        try:
            key = record_key(review)
        except QualityValidationError as exc:
            errors.append(str(exc))
            continue
        if key in reviewed:
            errors.append(f"duplicate assessment record: {key}")
            continue
        reviewed.add(key)
        record = benchmark_records.get(key)
        if record is None:
            errors.append(f"assessment has no matching benchmark record: {key}")
            continue
        dimensions = review.get("dimensions")
        if not isinstance(dimensions, dict):
            errors.append(f"dimensions missing for {key}")
            continue
        review_failed = False
        for dimension in DIMENSIONS:
            item = dimensions.get(dimension)
            if not isinstance(item, dict):
                errors.append(f"dimension {dimension} missing for {key}")
                review_failed = True
                continue
            verdict = item.get("verdict")
            evidence = item.get("evidence")
            note = item.get("note")
            if verdict not in VERDICTS:
                errors.append(f"invalid {dimension} verdict for {key}: {verdict}")
                review_failed = True
            if not isinstance(evidence, list) or not evidence or not all(isinstance(v, str) and v.strip() for v in evidence):
                errors.append(f"dimension {dimension} needs non-empty evidence for {key}")
                review_failed = True
            if not isinstance(note, str) or not note.strip():
                errors.append(f"dimension {dimension} needs a review note for {key}")
                review_failed = True
            if verdict in {"fail", "needs-review"}:
                review_failed = True
        for counter in ("silent_omission_count", "hallucinated_structure_count"):
            value = review.get(counter)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"{counter} must be a non-negative integer for {key}")
                review_failed = True
            elif value:
                review_failed = True
        advertising = review.get("advertising_policy")
        if not isinstance(advertising, dict):
            errors.append(f"advertising_policy missing for {key}")
            review_failed = True
        else:
            if advertising.get("mode") != "preserve-only":
                errors.append(f"advertising mode must be preserve-only for {key}")
                review_failed = True
            if advertising.get("changed_or_deleted_visible_text") is not False:
                errors.append(f"visible text was not proven unchanged for {key}")
                review_failed = True
        expected_status = "needs-review" if review_failed else "passed"
        if review.get("status") != expected_status:
            errors.append(f"review status for {key} must be {expected_status}")
        if not review_failed:
            passed_reviews += 1

    reviewable = {
        key
        for key, record in benchmark_records.items()
        if record.get("status") in {"complete", "partial", "passed", "needs-review"}
        and (record.get("structured_markdown") or record.get("structure_evidence"))
    }
    missing_reviews = sorted(reviewable - reviewed)
    if missing_reviews:
        errors.append(f"reviewable benchmark records missing assessment: {missing_reviews}")

    expected_pages = set(benchmark.get("pages") or [])
    apple_pages = {
        page
        for route, page, _ in reviewed
        if route == "apple-vision-ocr"
    }
    if expected_pages - apple_pages:
        errors.append(f"Apple OCR plus structure pages missing review: {sorted(expected_pages - apple_pages)}")

    candidate_routes = {route for route, _, _ in benchmark_records}
    if not any("small" in route for route in candidate_routes):
        warnings.append("small vision model has no real benchmark record")
    if not any("30b" in route for route in candidate_routes):
        warnings.append("30B vision model has no real benchmark record")

    selection = assessment.get("route_selection")
    if not isinstance(selection, dict):
        errors.append("route_selection is required")
    else:
        if selection.get("selected_default") not in {None, "none", "calibration-pending"} and warnings:
            errors.append("cannot select a default while required candidate routes are missing")
        if not isinstance(selection.get("rule"), str) or not selection["rule"].strip():
            errors.append("route_selection.rule is required")
        if not isinstance(selection.get("fallback"), list) or not selection["fallback"]:
            errors.append("route_selection.fallback must be a non-empty array")

    status = "failed" if errors else ("needs-review" if warnings or passed_reviews < len(reviewed) else "passed")
    return {
        "contract": "recognition-route-quality-validation",
        "version": VERSION,
        "status": status,
        "source_fingerprint": source_fingerprint,
        "benchmark_record_count": len(benchmark_records),
        "assessment_record_count": len(reviewed),
        "passed_assessment_count": passed_reviews,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = validate(load_object(args.benchmark), load_object(args.assessment))
    except QualityValidationError as exc:
        result = {"contract": "recognition-route-quality-validation", "version": VERSION, "status": "failed", "errors": [str(exc)], "warnings": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
