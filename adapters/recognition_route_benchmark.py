#!/usr/bin/env python3
"""Benchmark Apple Vision OCR and register existing page-route evidence.

The command never downloads models or invokes a remote model.  It runs local
Apple Vision OCR for explicitly supplied page renders, builds a deterministic
Markdown projection, and indexes separately produced visual-model evidence so
all routes can be scored against the same image SHA later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocr_markdown_structure import PROMPT_VERSION as STRUCTURE_PROMPT_VERSION
from ocr_markdown_structure import StructureError, reconstruct


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "recognition-route-benchmark"
VERSION = "0.1.0"
APPLE_PROMPT_VERSION = "apple-vision-vnrecognize-accurate-zh-hans-en-us-v1"
TIME_VALUE_RE = re.compile(r"^(real|user|sys)\s+([0-9.]+)$", re.MULTILINE)
RESOURCE_VALUE_RE = re.compile(r"^\s*(\d+)\s+(.+)$", re.MULTILINE)


class BenchmarkError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_page_image(value: str) -> tuple[int, Path]:
    try:
        raw_page, raw_path = value.split("=", 1)
        page = int(raw_page)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("page image must be PAGE=/absolute/path.png") from exc
    path = Path(raw_path).expanduser().resolve()
    if page < 1 or not path.is_file():
        raise argparse.ArgumentTypeError(f"page image is invalid: {value}")
    return page, path


def parse_existing_route(value: str) -> tuple[str, int, Path]:
    try:
        route, raw_page, raw_path = value.split(",", 2)
        page = int(raw_page)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("existing route must be ROUTE,PAGE,/absolute/evidence.json") from exc
    path = Path(raw_path).expanduser().resolve()
    if not route.strip() or page < 1 or not path.is_file():
        raise argparse.ArgumentTypeError(f"existing route is invalid: {value}")
    return route.strip(), page, path


def parse_time(stderr: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {key: float(value) for key, value in TIME_VALUE_RE.findall(stderr)}
    for value, label in RESOURCE_VALUE_RE.findall(stderr):
        if label in {"maximum resident set size", "peak memory footprint"}:
            metrics[label.replace(" ", "_")] = int(value)
    return metrics


def run_apple_vision(image: Path, script: Path, *, timeout: int) -> tuple[dict[str, Any], dict[str, Any], str]:
    command = ["/usr/bin/time", "-lp", "swift", str(script), str(image)]
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return {"status": "unavailable", "error": f"timeout: {exc}"}, {"wall_seconds": round(time.monotonic() - started, 3)}, ""
    metrics = parse_time(result.stderr)
    metrics["wall_seconds"] = round(time.monotonic() - started, 3)
    if result.returncode != 0:
        return {
            "status": "unavailable",
            "error": result.stderr.strip()[-1000:] or f"exit {result.returncode}",
        }, metrics, result.stdout
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "failed", "error": f"invalid JSON: {exc}"}, metrics, result.stdout
    return payload, metrics, result.stdout


def existing_route_record(
    route: str,
    page: int,
    path: Path,
    *,
    image: Path | None,
    source_fingerprint: str,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    expected_image_sha = digest(image) if image else None
    recorded_image_sha = payload.get("image_sha256")
    image_match = (
        recorded_image_sha == expected_image_sha
        if isinstance(recorded_image_sha, str) and expected_image_sha
        else "Unknown"
    )
    source_match = payload.get("source_fingerprint") == source_fingerprint
    text = payload.get("text") if isinstance(payload.get("text"), dict) else {}
    structured_markdown = text.get("markdown", "")
    if not isinstance(structured_markdown, str) or not structured_markdown.strip():
        legacy_markdown = payload.get("candidate_markdown")
        structured_markdown = legacy_markdown if isinstance(legacy_markdown, str) else ""
    model = payload.get("model", "Unknown")
    route_model_match: bool | str = "Unknown"
    if "30b" in route.lower():
        route_model_match = isinstance(model, str) and "30b" in model.lower()
    elif "small" in route.lower():
        route_model_match = isinstance(model, str) and model != "Unknown" and "30b" not in model.lower()
    diagnostics = payload.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        diagnostics = [str(diagnostics)]
    if route_model_match is False:
        diagnostics = [*diagnostics, f"declared route {route} does not match recorded model {model}"]
    return {
        "route": route,
        "stage": "text-recognition-and-markdown-structure-reconstruction",
        "page": page,
        "status": "failed" if route_model_match is False else payload.get("status", "unknown"),
        "source_fingerprint": source_fingerprint,
        "image": str(image) if image else None,
        "image_sha256": expected_image_sha,
        "recorded_image_sha256": recorded_image_sha,
        "image_sha256_match": image_match,
        "source_fingerprint_match": source_match,
        "engine": "mac96-ollama-vision",
        "model": model,
        "route_model_match": route_model_match,
        "parameters": {"temperature": 0, "pass_strategy": payload.get("pass_strategy_used")},
        "prompt_version": payload.get("prompt_version", "Unknown"),
        "metrics": metrics,
        "raw_output": str(path),
        "structured_markdown": structured_markdown,
        "quality_assessment": "not-run",
        "diagnostics": diagnostics,
    }


def run(args: argparse.Namespace) -> Path:
    output = args.output_root.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("raw", "markdown", "evidence"):
        (output / directory).mkdir(exist_ok=True)
    page_images = dict(args.page_image)
    if len(page_images) != len(args.page_image):
        raise BenchmarkError("duplicate page image declarations are not allowed")
    script = args.apple_vision_script.expanduser().resolve()
    if not script.is_file():
        raise BenchmarkError(f"Apple Vision script does not exist: {script}")

    records: list[dict[str, Any]] = []
    for page, image in sorted(page_images.items()):
        requested_at = now()
        payload, metrics, raw_stdout = run_apple_vision(image, script, timeout=args.timeout)
        raw_path = output / "raw" / f"apple-vision-p{page:03d}.json"
        if raw_stdout.strip():
            raw_path.write_text(raw_stdout.rstrip() + "\n", encoding="utf-8")
        else:
            write_json(raw_path, payload)
        record: dict[str, Any] = {
            "route": "apple-vision-ocr",
            "stage": "text-recognition",
            "page": page,
            "status": payload.get("status", "failed"),
            "source_fingerprint": args.source_fingerprint,
            "image": str(image),
            "image_sha256": digest(image),
            "engine": payload.get("engine", "Apple Vision VNRecognizeTextRequest"),
            "model": "not-applicable",
            "parameters": {
                "recognition_level": "accurate",
                "recognition_languages": ["zh-Hans", "en-US"],
                "uses_language_correction": True,
            },
            "prompt_version": APPLE_PROMPT_VERSION,
            "requested_at": requested_at,
            "finished_at": now(),
            "metrics": metrics,
            "raw_output": str(raw_path),
            "quality_assessment": "not-run",
            "diagnostics": [],
        }
        if payload.get("status") == "complete":
            try:
                structure = reconstruct(payload, page=page)
            except StructureError as exc:
                record["status"] = "failed"
                record["diagnostics"].append(str(exc))
            else:
                markdown_path = output / "markdown" / f"apple-vision-deterministic-p{page:03d}.md"
                structure_path = output / "evidence" / f"ocr-markdown-structure-p{page:03d}.json"
                markdown_path.write_text(structure["markdown"], encoding="utf-8")
                write_json(structure_path, structure)
                record["structure_route"] = "ocr-plus-deterministic-bbox-structure"
                record["structure_prompt_version"] = STRUCTURE_PROMPT_VERSION
                record["structure_status"] = structure["status"]
                record["structured_markdown"] = str(markdown_path)
                record["structure_evidence"] = str(structure_path)
                record["candidate_accounting"] = structure["candidate_accounting"]
                record["advertising_policy"] = structure["advertising_policy"]
        else:
            record["diagnostics"].append(str(payload.get("error") or "Apple Vision OCR failed"))
        records.append(record)

    for route, page, path in args.existing_route:
        records.append(
            existing_route_record(
                route,
                page,
                path,
                image=page_images.get(page),
                source_fingerprint=args.source_fingerprint,
            )
        )

    pages = sorted(page_images)
    report = {
        "contract": CONTRACT,
        "version": VERSION,
        "run_id": args.run_id,
        "created_at": now(),
        "status": "needs-review",
        "source_fingerprint": args.source_fingerprint,
        "pages": pages,
        "routes": sorted({record["route"] for record in records}),
        "records": records,
        "quality_dimensions": [
            "heading_hierarchy",
            "paragraph_segmentation",
            "table_of_contents",
            "lists",
            "header_footer",
            "punctuation",
            "cross_page_continuity",
            "anomalous_content_preservation",
        ],
        "quality_assessment": "not-run",
        "advertising_policy": {"mode": "preserve-only", "treatment": "not-run"},
        "diagnostics": [
            "Raw execution evidence is recorded; human quality scoring is still required.",
            "This command does not invoke, download, install, start, stop, or restart remote models.",
        ],
    }
    path = output / "benchmark-run.json"
    write_json(path, report)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--page-image", action="append", type=parse_page_image, required=True)
    parser.add_argument("--existing-route", action="append", type=parse_existing_route, default=[])
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--apple-vision-script",
        type=Path,
        default=ROOT / "adapters/apple_vision_ocr.swift",
    )
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}", args.source_fingerprint):
        print(json.dumps({"status": "failed", "error": "source fingerprint must be SHA-256"}))
        return 2
    try:
        report = run(args)
    except (OSError, json.JSONDecodeError, BenchmarkError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "needs-review", "report": str(report)}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
