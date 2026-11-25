#!/usr/bin/env python3
"""Convert scanned text PDFs with local Apple Vision OCR.

The runner is deliberately local-only: it renders one PDF page at a time,
invokes ``VNRecognizeTextRequest``, reconstructs conservative Markdown from
the OCR bounding boxes, and reuses the existing page-unit assembly and
independent validation surfaces.  It never contacts Ollama or an online
provider.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import lan_pdf_vision_to_markdown as package
    import ocr_markdown_structure as structure
    import pdf_disk_guard as disk_guard
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import lan_pdf_vision_to_markdown as package
    from . import ocr_markdown_structure as structure
    from . import pdf_disk_guard as disk_guard
    from . import pdf_resource_slots as resource_slots


ROOT = Path(__file__).resolve().parents[1]
APPLE_VISION_SCRIPT = ROOT / "adapters" / "apple_vision_ocr.swift"
INDEPENDENT_VALIDATOR = ROOT / "adapters" / "lan_pdf_vision_to_markdown.py"
ROUTE = package.APPLE_VISION_ROUTE
DEFAULT_MAX_JOBS = 4
FAST_PNG_ENV = "WORKFLOW_APPLE_VISION_FAST_PNG"


class OcrRunFailure(RuntimeError):
    """A handled Apple Vision conversion failure."""


def _relative(run_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(run_root.resolve()))


def _run_apple_vision(image: Path, timeout_seconds: int) -> tuple[dict[str, Any], float]:
    swift = shutil.which("swift")
    if not swift:
        raise OcrRunFailure("找不到 swift；无法运行本机 Apple Vision OCR")
    if not APPLE_VISION_SCRIPT.is_file():
        raise OcrRunFailure(f"缺少 Apple Vision OCR 适配器：{APPLE_VISION_SCRIPT}")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [swift, str(APPLE_VISION_SCRIPT), str(image)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrRunFailure(f"Apple Vision OCR 执行失败：{exc}") from exc
    wall_seconds = time.monotonic() - started
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OcrRunFailure(
            f"Apple Vision OCR 返回无法解析；exit={completed.returncode}"
        ) from exc
    if completed.returncode != 0 or payload.get("status") != "complete":
        raise OcrRunFailure(
            str(payload.get("error") or f"Apple Vision OCR exit={completed.returncode}")
        )
    if not isinstance(payload.get("candidates"), list):
        raise OcrRunFailure("Apple Vision OCR 缺少 candidates")
    return payload, wall_seconds


def default_jobs() -> int:
    cpu_total = os.cpu_count() or 1
    return max(1, min(DEFAULT_MAX_JOBS, cpu_total // 2 or 1))


def _blank_structure(page: int) -> dict[str, Any]:
    return {
        "contract": structure.CONTRACT,
        "version": structure.VERSION,
        "prompt_version": structure.PROMPT_VERSION,
        "created_at": structure.now(),
        "page": page,
        "status": "passed",
        "input_candidate_count": 0,
        "mapped_candidate_count": 0,
        "candidate_accounting": {"unmapped_indices": [], "duplicate_indices": []},
        "markdown": "",
        "structure_inventory": {
            "page_kind": "image-only-no-recognized-text",
            "heading_count": 0,
            "paragraph_count": 0,
            "list_item_count": 0,
            "toc_entry_count": 0,
            "starts_with_continuation": False,
            "ends_with_continuation": False,
            "cross_page_continuity": "complete",
            "header_footer_candidate_indices": [],
            "low_confidence_candidate_indices": [],
        },
        "advertising_policy": {
            "mode": "preserve-only",
            "classification_stage": "not-run",
            "applied_action": "preserve",
        },
        "blocks": [],
        "diagnostics": ["No text candidate was recognized; the source page image is retained."],
    }


def _convert_page(
    *,
    source: Path,
    source_sha256: str,
    run_root: Path,
    page: int,
    dpi: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    page_started = time.monotonic()
    paths = package.page_paths(run_root, page)
    render = package.render_or_reuse_page(
        source, page, dpi, paths["asset"], fast_png=os.environ.get(FAST_PNG_ENV) == "1",
    )
    raw_path = run_root / "evidence" / f"apple-vision-p{page:03d}.json"
    structure_path = run_root / "evidence" / f"ocr-markdown-structure-p{page:03d}.json"
    try:
        payload, wall_seconds = _run_apple_vision(paths["asset"], timeout_seconds)
        package.write_json(raw_path, payload)
        projected = (
            structure.reconstruct(payload, page=page)
            if payload.get("candidates")
            else _blank_structure(page)
        )
        package.write_json(structure_path, projected)
        accounting = projected.get("candidate_accounting") or {}
        review_resolution: dict[str, Any] | None = None
        if projected.get("status") == "needs-review":
            if accounting.get("unmapped_indices") or accounting.get("duplicate_indices"):
                raise OcrRunFailure("OCR 候选映射不完整，不能保留为可复核页面")
            review_resolution = {
                "status": "passed",
                "method": "preserve-all-ocr-candidates-and-retain-source-page-image",
                "diagnostics": list(projected.get("diagnostics") or []),
                "source_image_retained": True,
                "candidate_accounting_complete": True,
            }
        elif projected.get("status") != "passed":
            raise OcrRunFailure(
                "OCR 结构失败：" + "；".join(projected.get("diagnostics") or [])
            )
        markdown = f"<!-- page:{page} -->\n\n{projected.get('markdown') or ''}"
        markdown = package.project_page_image_reference(
            markdown,
            page,
            package.page_unit_image_reference(page),
        )
        validation = package.validate_page_markdown(
            markdown,
            page,
            reader_image_action="retain",
            expected_image_reference=package.page_unit_image_reference(page),
        )
        if not validation["passed"]:
            raise OcrRunFailure("；".join(validation["errors"]))
        package._write_page_text(paths["markdown"], markdown)
        request_evidence = {
            "route": ROUTE,
            "engine": payload.get("engine"),
            "recognition_level": "accurate",
            "languages": payload.get("languages"),
            "uses_language_correction": True,
            "image_path": str(paths["asset"]),
            "image_sha256": package.sha256_file(paths["asset"]),
            "remote_model_calls": [],
        }
        package.write_json(paths["request"], request_evidence)
        metrics = {
            "route": ROUTE,
            "render_wall_seconds": round(float(render.get("wall_seconds") or 0.0), 3),
            "ocr_wall_seconds": round(wall_seconds, 3),
            "request_wall_seconds": round(wall_seconds, 3),
            "total_page_wall_seconds": round(time.monotonic() - page_started, 3),
            "candidate_count": len(payload.get("candidates") or []),
            "mean_confidence": round(
                sum(float(item.get("confidence") or 0) for item in payload.get("candidates") or [])
                / max(1, len(payload.get("candidates") or [])),
                6,
            ),
        }
        if "renderer" in render:
            metrics["renderer"] = render["renderer"]
            for key in ("raster_wall_seconds", "png_encode_wall_seconds"):
                if key in render:
                    metrics[key] = round(render[key], 3)
        package.write_json(paths["metrics"], metrics)
        result = {
            "page": page,
            "page_state": "converted",
            "status": "passed",
            "route": ROUTE,
            "source_sha256": source_sha256,
            "model": "not-applicable",
            "asset": render,
            "markdown": {
                "path": str(paths["markdown"]),
                "size_bytes": paths["markdown"].stat().st_size,
                "ocr_sha256": package.sha256_file(paths["markdown"]),
                "current_sha256": package.sha256_file(paths["markdown"]),
            },
            "reader_image": {
                "action": "retain",
                "decision_source": "conversion-default",
                "reason_codes": ["pending-explicit-classification"],
                "asset_present": True,
                "semantic_text_coverage": "unknown",
                "layout_dependency": "unknown",
                "page_unit_reference": package.page_unit_image_reference(page),
                "document_reference": package.merged_page_image_reference(page),
                "metrics": {},
            },
            "review_resolution": review_resolution,
            "request_evidence_path": _relative(run_root, paths["request"]),
            "ocr_evidence_path": _relative(run_root, raw_path),
            "ocr_evidence_sha256": package.sha256_file(raw_path),
            "structure_evidence_path": _relative(run_root, structure_path),
            "structure_evidence_sha256": package.sha256_file(structure_path),
            "metrics_path": _relative(run_root, paths["metrics"]),
            "validation": validation,
            "finished_at": package.utc_now(),
        }
        package.write_json(paths["result"], result)
        return result
    except disk_guard.DiskFloorReached:
        raise
    except Exception as exc:
        result = {
            "page": page,
            "page_state": "failed",
            "status": "failed",
            "route": ROUTE,
            "source_sha256": source_sha256,
            "model": "not-applicable",
            "asset": render,
            "error": str(exc),
            "finished_at": package.utc_now(),
        }
        package.write_json(paths["result"], result)
        return result


def _load_page_metric_seconds(run_root: Path, page: int) -> float | None:
    metrics_path = package.page_paths(run_root, page)["metrics"]
    if not metrics_path.is_file():
        return None
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    for key in ("total_page_wall_seconds", "request_wall_seconds"):
        value = metrics.get(key)
        if value is not None:
            return float(value)
    return None


def _process_page(
    *,
    source: Path,
    source_sha256: str,
    run_root: Path,
    page: int,
    dpi: int,
    timeout_seconds: int,
    resume: bool,
) -> tuple[int, dict[str, Any]]:
    result_path = package.page_paths(run_root, page)["result"]
    if resume and result_path.is_file():
        existing_result = json.loads(result_path.read_text(encoding="utf-8"))
        if existing_result.get("status") == "passed":
            result = package.validate_saved_page(source_sha256, run_root, page)
        else:
            result = _convert_page(
                source=source,
                source_sha256=source_sha256,
                run_root=run_root,
                page=page,
                dpi=dpi,
                timeout_seconds=timeout_seconds,
            )
    else:
        result = _convert_page(
            source=source,
            source_sha256=source_sha256,
            run_root=run_root,
            page=page,
            dpi=dpi,
            timeout_seconds=timeout_seconds,
        )
    return page, result


def _initial_summary(source: Path, source_sha256: str, run_root: Path, total_pages: int) -> dict[str, Any]:
    return {
        "status": "running",
        "source": {"path": str(source), "sha256": source_sha256},
        "total_pages": total_pages,
        "passed_pages": 0,
        "failed_pages": [],
        "request": {
            "route": ROUTE,
            "engine": "Apple Vision VNRecognizeTextRequest",
            "model": "not-applicable",
            "recognition_level": "accurate",
            "languages": ["zh-Hans", "en-US"],
            "uses_language_correction": True,
            "remote_model_calls": [],
            "used_pdf_native_text": False,
        },
        "final_markdown": None,
        "run_directory": str(run_root),
        "validation_report_path": str(run_root / "reports" / "validation-result.json"),
    }


def _run_independent_validation(source: Path, run_root: Path, selector: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(INDEPENDENT_VALIDATOR),
        "validate",
        "--source",
        str(source),
        "--run-root",
        str(run_root),
        "--pages",
        selector,
        "--require-final",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    report_path = run_root / "reports" / "validation-result.json"
    if not report_path.is_file():
        raise OcrRunFailure(
            f"独立验收未生成报告；exit={completed.returncode}; stderr={completed.stderr.strip()}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if completed.returncode != 0 or report.get("status") != "passed":
        raise OcrRunFailure("独立验收失败：" + "；".join(report.get("errors") or []))
    return report


@resource_slots.uses_local_pdf_slot("apple-vision-ocr-pdf-to-markdown")
def run(
    *,
    source: Path,
    run_root: Path,
    page_selector: str,
    resume: bool,
    dpi: int,
    timeout_seconds: int,
    progress_every: int,
    jobs: int,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    run_root = run_root.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise OcrRunFailure(f"源文件不是可读 PDF：{source}")
    if not 150 <= dpi <= 250:
        raise OcrRunFailure("DPI 必须位于 150-250")
    if jobs < 1:
        raise OcrRunFailure("jobs 必须大于等于 1")
    existing = run_root.exists() and any(run_root.iterdir())
    if existing and not resume:
        raise OcrRunFailure(f"运行目录非空；继续时必须显式使用 --resume：{run_root}")
    for directory in (
        run_root / "assets",
        run_root / "page-units",
        run_root / "evidence",
        run_root / "reports",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    total_pages = package.get_pdf_page_count(source)
    pages = package.parse_page_selector(page_selector, total_pages)
    source_sha256 = package.sha256_file(source)
    package.write_json(
        run_root / "summary.json",
        _initial_summary(source, source_sha256, run_root, total_pages),
    )
    logger = package.configure_logger(run_root)
    logger.info(
        "RUN_START pages=%s route=%s resume=%s jobs=%d",
        page_selector,
        ROUTE,
        resume,
        jobs,
    )
    passed: list[int] = []
    failed: list[int] = []
    timings: list[float] = []
    package.write_progress(
        run_root,
        phase="apple-vision-ocr",
        current_page=None,
        total_pages=total_pages,
        passed_count=0,
        failed_pages=[],
        next_step=f"process page {pages[0]}",
    )
    completed_count = 0
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        pending: dict[Future[tuple[int, dict[str, Any]]], int] = {}
        page_iter = iter(pages)

        def submit_next() -> None:
            try:
                next_page = next(page_iter)
            except StopIteration:
                return
            future = executor.submit(
                _process_page,
                source=source,
                source_sha256=source_sha256,
                run_root=run_root,
                page=next_page,
                dpi=dpi,
                timeout_seconds=timeout_seconds,
                resume=resume,
            )
            pending[future] = next_page

        for _ in range(min(jobs, len(pages))):
            submit_next()

        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                page = pending.pop(future)
                try:
                    _, result = future.result()
                except disk_guard.DiskFloorReached:
                    raise
                except Exception as exc:
                    result = {"status": "failed", "error": str(exc)}
                completed_count += 1
                if result.get("status") == "passed":
                    passed.append(page)
                    metric_seconds = _load_page_metric_seconds(run_root, page)
                    if metric_seconds is not None:
                        timings.append(metric_seconds)
                else:
                    failed.append(page)
                    logger.error("PAGE_FAILED page=%d error=%s", page, result.get("error"))
                passed.sort()
                failed.sort()
                remaining = len(pages) - completed_count
                next_step = (
                    f"process remaining {remaining} pages"
                    if remaining > 0
                    else ("inspect failed pages" if failed else "assemble Markdown")
                )
                package.write_progress(
                    run_root,
                    phase="apple-vision-ocr",
                    current_page=page,
                    total_pages=total_pages,
                    passed_count=len(passed),
                    failed_pages=failed,
                    next_step=next_step,
                    performance_values=timings,
                )
                if (
                    completed_count % max(1, progress_every) == 0
                    or completed_count == len(pages)
                    or result.get("status") != "passed"
                ):
                    logger.info(
                        "RUN_PROGRESS current=%d processed=%d/%d passed=%d failed=%s jobs=%d",
                        page,
                        completed_count,
                        len(pages),
                        len(passed),
                        failed,
                        jobs,
                    )
                submit_next()
    if failed:
        summary = package._summary(
            source=source,
            source_sha256=source_sha256,
            run_root=run_root,
            total_pages=total_pages,
            status="failed",
        )
        package.write_json(run_root / "summary.json", summary)
        return {"status": "failed", "completed_pages": passed, "failed_pages": failed}

    cleanup = package.run_page_cleanup(run_root=run_root, pages=pages)
    classification = package.classify_reader_images(run_root=run_root, pages=pages)
    repair = package.repair_page_image_references(run_root=run_root, pages=pages)
    heading = package._load_heading_repair_module().run_heading_repair(
        run_root=run_root,
        use_model=False,
    )
    if heading.get("status") != "passed":
        raise OcrRunFailure("确定性标题修复仍需复核")
    validation = _run_independent_validation(source, run_root, page_selector)
    return {
        "status": "passed",
        "route": ROUTE,
        "source_sha256": source_sha256,
        "total_pages": total_pages,
        "completed_pages": passed,
        "failed_pages": [],
        "cleanup": cleanup,
        "reader_image_classification": classification,
        "image_reference_repair": repair,
        "heading_repair": heading,
        "validation": validation,
        "run_root": str(run_root),
        "final_markdown": str(run_root / "index.md"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--pages", default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--jobs", type=int, default=default_jobs())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(
            source=args.source,
            run_root=args.run_root,
            page_selector=args.pages,
            resume=args.resume,
            dpi=args.dpi,
            timeout_seconds=args.timeout_seconds,
            progress_every=args.progress_every,
            jobs=args.jobs,
        )
    except disk_guard.DiskFloorReached as exc:
        print(json.dumps({"status": "paused", "reason": disk_guard.PAUSE_REASON,
                          "disk_pause": exc.details}, ensure_ascii=False))
        return disk_guard.PAUSE_EXIT
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
