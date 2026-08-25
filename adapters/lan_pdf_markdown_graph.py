#!/usr/bin/env python3
"""Run the independent LAN PDF-to-Markdown LangGraph."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, TypedDict

try:
    import lan_markdown_heading_repair as heading
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import lan_markdown_heading_repair as heading

try:
    from output_naming import (
        package_timestamp,
        readable_package_stem,
        resolve_package_target,
    )
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import (
        package_timestamp,
        readable_package_stem,
        resolve_package_target,
    )

try:
    import pdf_task_guard as task_guard
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_task_guard as task_guard

try:
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_resource_slots as resource_slots

try:
    import pdf_route_detection as page_router
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_route_detection as page_router


class _LazyVisionModule:
    """Keep the visual adapter completely unloaded on the native-only route."""

    _module: Any | None = None

    def __getattr__(self, name: str) -> Any:
        if self._module is None:
            self._module = importlib.import_module("lan_pdf_vision_to_markdown")
        return getattr(self._module, name)


vision = _LazyVisionModule()


THINKING_TOOLS_SOURCE = Path("/Users/a0000/Downloads/100个思维工具详解.pdf")
THINKING_TOOLS_SHA256 = "40f729e0d2713570946e7a096a7d0a14b1d40b5d884f000ea4a7c64234859adf"
THINKING_TOOLS_TOTAL_PAGES = 1860
THINKING_TOOLS_SIZE_BYTES = 21407375
DEFAULT_RUNS_PARENT = Path(__file__).resolve().parents[1] / "runs"
DEFAULT_SMOKE_LABEL = "100-thinking-tools-langgraph-smoke"
NATIVE_FORMAT_VERSION = "0.6.0-pure-native-langgraph"
NATIVE_EVIDENCE_LEVELS = ("compact", "diagnostic", "full")
DEFAULT_DPI = 220
VISUAL_ASSET_CLASSIFIER_PAGE_TIMEOUT_SECONDS = 300
VISUAL_ASSET_CLASSIFIER_STAGE_OVERHEAD_SECONDS = 60
INTERNAL_STAGE_ROOT = ".stages"
NATIVE_SMOKE_STAGE = "native-smoke"
NATIVE_BATCH_STAGE = "native-pages"
ROOT = Path(__file__).resolve().parents[1]
LAN_ADAPTER = ROOT / "adapters" / "lan_pdf_to_markdown.py"
BATCH_ADAPTER = ROOT / "adapters" / "run_pdf_batches.py"
VISUAL_ASSET_CLASSIFIER = (
    ROOT
    / "skills"
    / "semantic-visual-asset-pruning"
    / "scripts"
    / "classify_visual_assets_mac96.py"
)
VISUAL_ASSET_PRUNER = (
    ROOT
    / "skills"
    / "semantic-visual-asset-pruning"
    / "scripts"
    / "prune_visual_assets.py"
)
BUNDLED_PYTHON = Path(
    "/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
)


class GraphState(TypedDict):
    """Only compact orchestration facts; page content never enters graph state."""

    source_path: str
    source_sha256: str
    run_root: str
    current_phase: str
    completed_pages: list[int]
    failed_pages: list[int]
    file_paths: dict[str, str]
    file_hashes: dict[str, str]
    final_status: str
    selected_pages: list[int]
    total_pages: int
    started_epoch: float


@dataclass(frozen=True)
class GraphSettings:
    source: Path
    run_root: Path
    pages: str
    evidence_level: str
    resume: bool
    dpi: int
    timeout_seconds: int
    progress_every: int
    confirmed_noise: tuple[str, ...]
    use_heading_model: bool
    prune_visual_assets: bool = True


@dataclass(frozen=True)
class NativeGraphSettings:
    source: Path
    run_root: Path
    pages: str
    smoke_pages: str | None
    batch_size: int
    timeout_seconds: int
    resume: bool = False


def _visual_asset_classification_stage_timeout(
    *, base_timeout_seconds: int, remaining_page_count: int
) -> int:
    """Allow the sequential classifier its per-page timeout plus stage overhead."""

    if remaining_page_count <= 0:
        return base_timeout_seconds
    return max(
        base_timeout_seconds,
        (
            remaining_page_count * VISUAL_ASSET_CLASSIFIER_PAGE_TIMEOUT_SECONDS
            + VISUAL_ASSET_CLASSIFIER_STAGE_OVERHEAD_SECONDS
        ),
    )


def _failure_state(
    state: GraphState, settings: GraphSettings, phase: str, exc: Exception
) -> dict[str, Any]:
    report_path = settings.run_root / "reports" / "validation-result.json"
    vision.write_json(
        report_path,
        {
            "status": "failed",
            "phase": phase,
            "error": str(exc),
            "source_sha256": state["source_sha256"],
        },
    )
    try:
        total_pages = vision.get_pdf_page_count(settings.source)
        vision.write_progress(
            settings.run_root,
            phase=phase,
            current_page=state["completed_pages"][-1] if state["completed_pages"] else None,
            total_pages=total_pages,
            passed_count=len(state["completed_pages"]),
            failed_pages=state["failed_pages"],
            next_step="inspect validation report and resume from page facts",
        )
    except Exception:
        pass
    vision.append_log_event(
        settings.run_root, "RUN_FAILED", phase=phase, error_type=type(exc).__name__
    )
    progress_path = settings.run_root / "evidence" / "progress-current.json"
    return {
        "current_phase": phase,
        "file_paths": {
            **state["file_paths"],
            "validation_report": str(report_path),
            "progress": str(progress_path),
        },
        "file_hashes": {
            **state["file_hashes"],
            **(
                {"validation_report": vision.sha256_file(report_path)}
                if report_path.is_file()
                else {}
            ),
            **(
                {"progress": vision.sha256_file(progress_path)}
                if progress_path.is_file()
                else {}
            ),
        },
        "final_status": "failed",
    }


def build_graph(settings: GraphSettings):
    """Build without a checkpointer; resume is always revalidated from page files."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "LangGraph 未安装。请在你选择的项目环境中安装 langgraph 后重试；"
            "本脚本不会修改全局 Python 环境。"
        ) from exc

    def visual_conversion(state: GraphState) -> dict[str, Any]:
        try:
            result = vision.run_visual_conversion(
                source=settings.source,
                run_root=settings.run_root,
                page_selector=settings.pages,
                evidence_level=settings.evidence_level,
                resume=settings.resume,
                dpi=settings.dpi,
                timeout_seconds=settings.timeout_seconds,
                progress_every=settings.progress_every,
            )
        except Exception as exc:
            return _failure_state(state, settings, "visual-conversion", exc)
        summary_path = settings.run_root / "summary.json"
        progress_path = settings.run_root / "evidence" / "progress-current.json"
        failed = list(result.get("failed_pages") or [])
        return {
            "current_phase": "visual-conversion",
            "completed_pages": list(result.get("completed_pages") or []),
            "failed_pages": failed,
            "file_paths": {
                **state["file_paths"],
                "summary": str(summary_path),
                "progress": str(progress_path),
            },
            "file_hashes": {
                **state["file_hashes"],
                **({"summary": vision.sha256_file(summary_path)} if summary_path.is_file() else {}),
                **({"progress": vision.sha256_file(progress_path)} if progress_path.is_file() else {}),
            },
            "final_status": "failed" if failed else "running",
        }

    def page_cleanup(state: GraphState) -> dict[str, Any]:
        try:
            result = vision.run_page_cleanup(
                run_root=settings.run_root,
                pages=state["completed_pages"],
                confirmed_noise=list(settings.confirmed_noise),
            )
        except Exception as exc:
            return _failure_state(state, settings, "page-cleanup", exc)
        hashes = {
            f"page_unit_p{page:03d}": vision.sha256_file(
                settings.run_root / "page-units" / f"p{page:03d}.md"
            )
            for page in result["pages"]
        }
        progress_path = settings.run_root / "evidence" / "progress-current.json"
        return {
            "current_phase": "page-cleanup",
            "file_paths": {
                **state["file_paths"],
                "page_units": str(settings.run_root / "page-units"),
                "progress": str(progress_path),
            },
            "file_hashes": {
                **state["file_hashes"],
                **hashes,
                **(
                    {"progress": vision.sha256_file(progress_path)}
                    if progress_path.is_file()
                    else {}
                ),
            },
            "final_status": "running",
        }

    def merge_document(state: GraphState) -> dict[str, Any]:
        try:
            result = vision.merge_document(
                run_root=settings.run_root, pages=state["completed_pages"]
            )
        except Exception as exc:
            return _failure_state(state, settings, "merge-document-temp", exc)
        progress_path = settings.run_root / "evidence" / "progress-current.json"
        return {
            "current_phase": "merge-document-temp",
            "file_paths": {
                **state["file_paths"],
                "document_temp": result["path"],
                "progress": str(progress_path),
            },
            "file_hashes": {
                **state["file_hashes"],
                "document_temp": result["sha256"],
                **(
                    {"progress": vision.sha256_file(progress_path)}
                    if progress_path.is_file()
                    else {}
                ),
            },
            "final_status": "running",
        }

    def verify_merge_coverage(state: GraphState) -> dict[str, Any]:
        try:
            report = _visual_check_merge_coverage(
                run_root=settings.run_root,
                selected_pages=state["completed_pages"],
                markdown_path=settings.run_root / "document-temp.md",
            )
            report_path = settings.run_root / "reports" / "merge-coverage.json"
            _native_write_json(report_path, report)
            if report["status"] != "passed":
                raise ValueError(
                    "merged Markdown page coverage failed: "
                    f"expected={report['expected_page_count']} "
                    f"actual={report['actual_page_count']}"
                )
        except Exception as exc:
            return _failure_state(state, settings, "merge-coverage-validation", exc)
        return {
            "current_phase": "merge-coverage-validation",
            "file_paths": {
                **state["file_paths"],
                "merge_coverage_report": str(report_path),
            },
            "file_hashes": {
                **state["file_hashes"],
                "merge_coverage_report": vision.sha256_file(report_path),
            },
            "final_status": "running",
        }

    def heading_audit_and_validation(state: GraphState) -> dict[str, Any]:
        try:
            vision.append_log_event(
                settings.run_root, "PHASE_START", phase="heading-audit-and-validation"
            )
            repair = heading.run_heading_repair(
                run_root=settings.run_root,
                timeout_seconds=settings.timeout_seconds,
                use_model=settings.use_heading_model,
            )
            final_coverage_path = settings.run_root / "reports" / "final-coverage.json"
            if repair.get("status") == "passed":
                final_coverage = _native_check_merge_coverage(
                    run_root=settings.run_root,
                    selected_pages=state["completed_pages"],
                    markdown_path=settings.run_root / "index.md",
                    require_exact_content=False,
                )
            else:
                final_coverage = {
                    "format_version": NATIVE_FORMAT_VERSION,
                    "status": "not-run",
                    "reason": "heading-repair-not-passed",
                    "expected_page_count": len(state["completed_pages"]),
                }
            _native_write_json(final_coverage_path, final_coverage)
            if repair.get("status") == "passed" and final_coverage["status"] != "passed":
                raise ValueError(
                    "final index page coverage failed: "
                    f"expected={final_coverage['expected_page_count']} "
                    f"actual={final_coverage['actual_page_count']}"
                )
            validation = vision.validate_run(
                source=settings.source,
                run_root=settings.run_root,
                pages=state["completed_pages"],
                require_final=True,
            )
        except Exception as exc:
            return _failure_state(state, settings, "heading-audit-and-validation", exc)
        index_path = settings.run_root / "index.md"
        heading_report = settings.run_root / "reports" / "heading-repair.json"
        validation_report = settings.run_root / "reports" / "validation-result.json"
        summary_path = settings.run_root / "summary.json"
        progress_path = settings.run_root / "evidence" / "progress-current.json"
        final_status = (
            "passed"
            if repair.get("status") == "passed" and validation.get("status") == "passed"
            else "needs-review"
        )
        final_file_paths = {"index": str(index_path)} if final_status == "passed" else {}
        final_file_hashes = (
            {"index": vision.sha256_file(index_path)}
            if final_status == "passed" and index_path.is_file()
            else {}
        )
        return {
            "current_phase": "heading-audit-and-validation",
            "file_paths": {
                **state["file_paths"],
                **final_file_paths,
                "heading_report": str(heading_report),
                "final_coverage_report": str(final_coverage_path),
                "validation_report": str(validation_report),
                "summary": str(summary_path),
                "progress": str(progress_path),
            },
            "file_hashes": {
                **state["file_hashes"],
                **final_file_hashes,
                **(
                    {"heading_report": vision.sha256_file(heading_report)}
                    if heading_report.is_file()
                    else {}
                ),
                **(
                    {"final_coverage_report": vision.sha256_file(final_coverage_path)}
                    if final_coverage_path.is_file()
                    else {}
                ),
                **(
                    {"validation_report": vision.sha256_file(validation_report)}
                    if validation_report.is_file()
                    else {}
                ),
                **(
                    {"summary": vision.sha256_file(summary_path)}
                    if summary_path.is_file()
                    else {}
                ),
                **(
                    {"progress": vision.sha256_file(progress_path)}
                    if progress_path.is_file()
                    else {}
                ),
            },
            "final_status": final_status,
        }

    def visual_asset_pruning(state: GraphState) -> dict[str, Any]:
        if not settings.prune_visual_assets:
            return {
                "current_phase": "visual-asset-pruning-skipped",
                "final_status": state["final_status"],
            }
        try:
            pages = ",".join(str(page) for page in state["completed_pages"])
            classification_path = (
                settings.run_root
                / "evidence"
                / "visual-asset-classification-mac96.json"
            )
            plan_path = (
                settings.run_root / "evidence" / "visual-asset-pruning-plan.json"
            )
            result_path = (
                settings.run_root / "evidence" / "visual-asset-pruning.json"
            )
            vision.append_log_event(
                settings.run_root, "PHASE_START", phase="visual-asset-pruning"
            )
            report = (
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.is_file()
                else {}
            )
            reusable = (
                report.get("status") == "passed"
                and classification_path.is_file()
                and plan_path.is_file()
                and vision.sha256_file(classification_path)
                == report.get("classification_report_sha256")
                and vision.sha256_file(plan_path) == report.get("plan_sha256")
                and len(list((settings.run_root / "assets").glob("page-*.png")))
                == (report.get("counts") or {}).get("images_after")
            )
            if not reusable:
                classified_pages: set[int] = set()
                if classification_path.is_file():
                    try:
                        classification = json.loads(
                            classification_path.read_text(encoding="utf-8")
                        )
                        classified_pages = {
                            int(page)
                            for page in classification.get("completed_pages", [])
                        }
                    except (OSError, ValueError, TypeError):
                        classified_pages = set()
                remaining_page_count = sum(
                    page not in classified_pages for page in state["completed_pages"]
                )
                classify = subprocess.run(
                    [
                        sys.executable,
                        str(VISUAL_ASSET_CLASSIFIER),
                        str(settings.run_root),
                        "--pages",
                        pages,
                        "--resume",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=_visual_asset_classification_stage_timeout(
                        base_timeout_seconds=settings.timeout_seconds,
                        remaining_page_count=remaining_page_count,
                    ),
                )
                if classify.returncode not in {0, 1} or not classification_path.is_file():
                    raise ValueError(
                        "visual asset classification failed: "
                        f"exit={classify.returncode} stderr={classify.stderr[-1000:]}"
                    )

                preview = subprocess.run(
                    [
                        sys.executable,
                        str(VISUAL_ASSET_PRUNER),
                        str(settings.run_root),
                        "--classification-report",
                        "evidence/visual-asset-classification-mac96.json",
                        "--plan",
                        "evidence/visual-asset-pruning-plan.json",
                        "--delete-source-files",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=settings.timeout_seconds,
                )
                if preview.returncode != 0 or not plan_path.is_file():
                    raise ValueError(
                        "visual asset pruning preview failed: "
                        f"exit={preview.returncode} stderr={preview.stderr[-1000:]}"
                    )

                applied = subprocess.run(
                    [
                        sys.executable,
                        str(VISUAL_ASSET_PRUNER),
                        str(settings.run_root),
                        "--apply-plan",
                        "evidence/visual-asset-pruning-plan.json",
                        "--delete-source-files",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=settings.timeout_seconds,
                )
                if applied.returncode != 0 or not result_path.is_file():
                    raise ValueError(
                        "visual asset pruning apply failed: "
                        f"exit={applied.returncode} stderr={applied.stderr[-1000:]}"
                    )
                report = json.loads(result_path.read_text(encoding="utf-8"))
            if report.get("status") != "passed":
                raise ValueError("visual asset pruning result did not pass")
            counts = report.get("counts") or {}
            vision.write_progress(
                settings.run_root,
                phase="visual-asset-pruning",
                current_page=state["completed_pages"][-1],
                total_pages=state["total_pages"],
                passed_count=len(state["completed_pages"]),
                failed_pages=[],
                next_step="reader delivery ready; review retained and needs-review pages",
            )
            vision.append_log_event(
                settings.run_root,
                "PHASE_FINISHED",
                phase="visual-asset-pruning",
                deleted=counts.get("deleted", 0),
                retained=counts.get("retained", 0),
                needs_review=counts.get("needs_review", 0),
            )
        except Exception as exc:
            return _failure_state(state, settings, "visual-asset-pruning", exc)
        progress_path = settings.run_root / "evidence" / "progress-current.json"
        return {
            "current_phase": "visual-asset-pruning",
            "file_paths": {
                **state["file_paths"],
                "visual_asset_classification": str(classification_path),
                "visual_asset_pruning_plan": str(plan_path),
                "visual_asset_pruning_report": str(result_path),
                "progress": str(progress_path),
            },
            "file_hashes": {
                **state["file_hashes"],
                "visual_asset_classification": vision.sha256_file(classification_path),
                "visual_asset_pruning_plan": vision.sha256_file(plan_path),
                "visual_asset_pruning_report": vision.sha256_file(result_path),
                "progress": vision.sha256_file(progress_path),
            },
            "final_status": "passed",
        }

    def continue_or_end(state: GraphState):
        return END if state["final_status"] in {"failed", "needs-review"} else "continue"

    builder = StateGraph(GraphState)
    builder.add_node("visual_conversion", visual_conversion)
    builder.add_node("page_cleanup", page_cleanup)
    builder.add_node("merge_document", merge_document)
    builder.add_node("merge_coverage_validation", verify_merge_coverage)
    builder.add_node("heading_audit_and_validation", heading_audit_and_validation)
    builder.add_node("visual_asset_pruning", visual_asset_pruning)
    builder.add_edge(START, "visual_conversion")
    builder.add_conditional_edges(
        "visual_conversion",
        continue_or_end,
        {"continue": "page_cleanup", END: END},
    )
    builder.add_conditional_edges(
        "page_cleanup",
        continue_or_end,
        {"continue": "merge_document", END: END},
    )
    builder.add_conditional_edges(
        "merge_document",
        continue_or_end,
        {"continue": "merge_coverage_validation", END: END},
    )
    builder.add_conditional_edges(
        "merge_coverage_validation",
        continue_or_end,
        {"continue": "heading_audit_and_validation", END: END},
    )
    builder.add_conditional_edges(
        "heading_audit_and_validation",
        continue_or_end,
        {"continue": "visual_asset_pruning", END: END},
    )
    builder.add_edge("visual_asset_pruning", END)
    return builder.compile()


@resource_slots.uses_local_pdf_slot("lan-pdf-markdown-graph-visual")
def run_graph(settings: GraphSettings) -> GraphState:
    source = settings.source.expanduser().resolve()
    run_root = settings.run_root.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise vision.RunFailure(f"源文件不是可读 PDF：{source}")
    source_hash = vision.sha256_file(source)
    initial: GraphState = {
        "source_path": str(source),
        "source_sha256": source_hash,
        "run_root": str(run_root),
        "current_phase": "not-started",
        "completed_pages": [],
        "failed_pages": [],
        "file_paths": {},
        "file_hashes": {},
        "final_status": "running",
        "selected_pages": [],
        "total_pages": vision.get_pdf_page_count(source),
        "started_epoch": time.time(),
    }
    graph = build_graph(settings)
    return graph.invoke(initial)


def _pdfinfo_facts(source: Path) -> dict[str, Any]:
    executable = shutil.which("pdfinfo")
    if not executable:
        raise vision.RunFailure("找不到 pdfinfo；未安装或修改任何依赖")
    completed = subprocess.run(
        [executable, str(source)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise vision.RunFailure(f"pdfinfo 失败：{completed.stderr.strip()}")

    def match_text(pattern: str) -> str | None:
        match = re.search(pattern, completed.stdout, re.MULTILINE)
        return match.group(1).strip() if match else None

    pages = match_text(r"^Pages:\s+(\d+)\s*$")
    size = match_text(r"^File size:\s+(\d+)\s+bytes\s*$")
    return {
        "pages": int(pages) if pages else None,
        "encrypted": match_text(r"^Encrypted:\s+(.+?)\s*$"),
        "page_size": match_text(r"^Page size:\s+(.+?)\s*$"),
        "file_size_bytes": int(size) if size else source.stat().st_size,
        "pdf_version": match_text(r"^PDF version:\s+(.+?)\s*$"),
    }


def _assert_source_facts(
    *,
    source: Path,
    expected_sha256: str | None,
    expected_total_pages: int | None,
    expected_size_bytes: int | None,
    expected_pdf_version: str | None,
    expected_page_size_contains: str | None,
) -> dict[str, Any]:
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise vision.RunFailure(f"源文件不是可读 PDF：{source}")
    source_sha256 = vision.sha256_file(source)
    facts = _pdfinfo_facts(source)
    errors: list[str] = []
    if expected_sha256 and source_sha256 != expected_sha256:
        errors.append(f"SHA-256 不一致：actual={source_sha256}")
    if expected_total_pages and facts.get("pages") != expected_total_pages:
        errors.append(f"页数不一致：actual={facts.get('pages')}")
    if expected_size_bytes and facts.get("file_size_bytes") != expected_size_bytes:
        errors.append(f"文件大小不一致：actual={facts.get('file_size_bytes')}")
    if expected_pdf_version and facts.get("pdf_version") != expected_pdf_version:
        errors.append(f"PDF 版本不一致：actual={facts.get('pdf_version')}")
    page_size = str(facts.get("page_size") or "")
    if expected_page_size_contains and expected_page_size_contains not in page_size:
        errors.append(f"页面尺寸不一致：actual={page_size}")
    if str(facts.get("encrypted") or "").lower() not in {"no", "false"}:
        errors.append(f"PDF 加密状态不允许：actual={facts.get('encrypted')}")
    if errors:
        raise vision.RunFailure("；".join(errors))
    return {"path": str(source), "sha256": source_sha256, **facts}


def _active_same_source_processes(source: Path) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        return []
    own = {os.getpid(), os.getppid()}
    scripts = (
        "lan_pdf_markdown_graph.py",
        "lan_pdf_vision_to_markdown.py",
        "lan_pdf_to_markdown.py",
        "run_pdf_batches.py",
    )
    source_text = str(source)
    matches: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_raw, _, command = stripped.partition(" ")
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        if pid in own:
            continue
        if source_text in command and any(script in command for script in scripts):
            matches.append({"pid": pid, "command": command[:500]})
    return matches


def _timestamp_run_root(parent: Path, label: str) -> Path:
    parent = parent.expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    stem = f"{readable_package_stem(label, fallback='pdf', max_chars=48)}-{package_timestamp()}"
    run_root = parent / stem
    ordinal = 1
    while run_root.exists():
        ordinal += 1
        run_root = parent / f"{stem}-{ordinal:02d}"
    return run_root


def _safe_run_label(source: Path) -> str:
    return readable_package_stem(source.stem, fallback="pdf", max_chars=48)


def _default_run_root(parent: Path, source: Path) -> Path:
    return resolve_package_target(parent, source)[1]


def _internal_stage_root(run_root: Path, stage: str) -> Path:
    return run_root / INTERNAL_STAGE_ROOT / stage


def _resolve_run_root(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    require_source_message: str,
) -> Path:
    if args.source is None:
        parser.error(require_source_message)
    if args.auto_run_root and args.run_root is not None:
        parser.error("--auto-run-root cannot be used with --run-root")
    run_root = args.run_root
    if args.auto_run_root:
        if args.run_label:
            run_root = _timestamp_run_root(args.runs_parent, args.run_label)
        else:
            run_root = _default_run_root(args.runs_parent, args.source)
    if run_root is None:
        parser.error("--run-root is required unless --auto-run-root is used")
    return run_root


def _emit_run_root(run_root: Path, *, mode: str) -> None:
    print(
        f"RUN_ROOT mode={mode} path={run_root}",
        file=sys.stderr,
        flush=True,
    )


def _last_history_record(run_root: Path) -> dict[str, Any]:
    history = run_root / "test-history.jsonl"
    if not history.is_file():
        raise vision.RunFailure(f"缺少运行历史：{history}")
    records = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        raise vision.RunFailure(f"运行历史为空：{history}")
    return json.loads(records[-1])


def _smoke_pages(selector: str, total_pages: int) -> list[int]:
    if selector == "all":
        selector = "1-3"
    pages = vision.parse_page_selector(selector, total_pages)
    if len(pages) > 3:
        raise vision.RunFailure(f"smoke 最多允许 3 页，实际 {len(pages)} 页")
    return pages


def _default_native_smoke_pages(selected_pages: list[int]) -> list[int]:
    if len(selected_pages) <= 5:
        return list(selected_pages)
    midpoint = selected_pages[(len(selected_pages) - 1) // 2]
    candidates = [
        selected_pages[0],
        selected_pages[1],
        selected_pages[2],
        midpoint,
        selected_pages[-1],
    ]
    result: list[int] = []
    for page in candidates:
        if page not in result:
            result.append(page)
    return result


@resource_slots.uses_local_pdf_slot("lan-pdf-markdown-smoke")
def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    source = (
        THINKING_TOOLS_SOURCE
        if args.thinking_tools_smoke and args.source is None
        else args.source
    )
    if source is None:
        raise vision.RunFailure("smoke 模式需要 --source，或使用 --thinking-tools-smoke")
    source = source.expanduser().resolve()
    expected_sha256 = args.expected_source_sha256
    expected_total_pages = args.expected_total_pages
    expected_size_bytes = args.expected_size_bytes
    expected_pdf_version = args.expected_pdf_version
    expected_page_size_contains = args.expected_page_size_contains
    if args.thinking_tools_smoke:
        expected_sha256 = expected_sha256 or THINKING_TOOLS_SHA256
        expected_total_pages = expected_total_pages or THINKING_TOOLS_TOTAL_PAGES
        expected_size_bytes = expected_size_bytes or THINKING_TOOLS_SIZE_BYTES
        expected_pdf_version = expected_pdf_version or "1.6"
        expected_page_size_contains = expected_page_size_contains or "A4"
    source_facts = _assert_source_facts(
        source=source,
        expected_sha256=expected_sha256,
        expected_total_pages=expected_total_pages,
        expected_size_bytes=expected_size_bytes,
        expected_pdf_version=expected_pdf_version,
        expected_page_size_contains=expected_page_size_contains,
    )
    active = _active_same_source_processes(source)
    if active:
        raise vision.RunFailure(f"存在相同源文件的活动转换进程，拒绝重复启动：{active}")
    pages = _smoke_pages("1-3" if args.pages == "all" else args.pages, int(source_facts["pages"]))
    page_selector = ",".join(str(page) for page in pages)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root is not None
        else _timestamp_run_root(args.runs_parent, args.smoke_label)
    )
    if run_root.exists() and not run_root.is_dir():
        raise vision.RunFailure(f"smoke 运行目录不是目录：{run_root}")
    if run_root.is_dir() and any(run_root.iterdir()):
        raise vision.RunFailure(f"smoke 运行目录必须全新或为空：{run_root}")
    settings = GraphSettings(
        source=source,
        run_root=run_root,
        pages=page_selector,
        evidence_level=args.evidence_level,
        resume=False,
        dpi=args.dpi,
        timeout_seconds=args.timeout_seconds,
        progress_every=args.progress_every,
        confirmed_noise=tuple(args.confirmed_noise),
        use_heading_model=not args.no_heading_model,
        prune_visual_assets=False,
    )
    first = run_graph(settings)
    second: GraphState | None = None
    skipped_pages: list[int] = []
    if first["final_status"] == "passed" and args.verify_resume_skip:
        second_settings = GraphSettings(
            source=source,
            run_root=run_root,
            pages=page_selector,
            evidence_level=args.evidence_level,
            resume=True,
            dpi=args.dpi,
            timeout_seconds=args.timeout_seconds,
            progress_every=args.progress_every,
            confirmed_noise=tuple(args.confirmed_noise),
            use_heading_model=not args.no_heading_model,
            prune_visual_assets=False,
        )
        second = run_graph(second_settings)
        history = _last_history_record(run_root)
        skipped_pages = sorted(int(page) for page in history.get("skipped_pages", []))
        if second["final_status"] != "passed" or skipped_pages != pages:
            raise vision.RunFailure(
                f"二次恢复跳过验证失败：status={second['final_status']} skipped={skipped_pages}"
            )
    return {
        "status": second["final_status"] if second is not None else first["final_status"],
        "mode": "thinking-tools-smoke" if args.thinking_tools_smoke else "smoke",
        "run_root": str(run_root),
        "source": source_facts,
        "selected_pages": pages,
        "total_pages": source_facts["pages"],
        "first_run": first,
        "resume_verification": {
            "enabled": bool(args.verify_resume_skip),
            "skipped_pages": skipped_pages,
            "status": second["final_status"] if second is not None else None,
        },
    }


def _native_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _native_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _native_atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _native_parse_pages(selector: str, total_pages: int) -> list[int]:
    if selector.strip().lower() == "all":
        return list(range(1, total_pages + 1))
    selected: set[int] = set()
    for token in selector.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start, end = int(start_raw), int(end_raw)
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {token}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    pages = sorted(selected)
    if not pages or pages[0] < 1 or pages[-1] > total_pages:
        raise ValueError(f"selected pages outside PDF range: {selector}")
    return pages


def _native_pdf_facts(source: Path) -> dict[str, Any]:
    # ``os.access`` is only a permission probe and can report false negatives
    # under macOS privacy/ACL boundaries.  The actual readers below are the
    # authority: pdfinfo verifies the PDF and _native_sha256 opens the source.
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"source is not a readable PDF: {source}")
    executable = shutil.which("pdfinfo")
    if not executable:
        raise ValueError("pdfinfo is unavailable")
    completed = subprocess.run(
        [executable, str(source)], capture_output=True, text=True, check=False, timeout=60
    )
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "pdfinfo failed")

    def fact(pattern: str) -> str | None:
        match = re.search(pattern, completed.stdout, re.MULTILINE)
        return match.group(1).strip() if match else None

    pages_raw = fact(r"^Pages:\s+(\d+)\s*$")
    if not pages_raw:
        raise ValueError("pdfinfo did not report a page count")
    return {
        "path": str(source),
        "sha256": _native_sha256(source),
        "total_pages": int(pages_raw),
        "file_size_bytes": source.stat().st_size,
        "page_size": fact(r"^Page size:\s+(.+?)\s*$"),
        "pdf_version": fact(r"^PDF version:\s+(.+?)\s*$"),
        "encrypted": fact(r"^Encrypted:\s+(.+?)\s*$"),
    }


def _native_progress(
    run_root: Path,
    *,
    source_facts: dict[str, Any],
    selected_pages: list[int],
    stage: str,
    status: str,
    completed_stages: list[dict[str, str]],
    started_epoch: float,
) -> Path:
    path = run_root / "evidence" / "progress-current.json"
    _native_write_json(
        path,
        {
            "format_version": NATIVE_FORMAT_VERSION,
            "status": status,
            "source": source_facts["path"],
            "source_sha256": source_facts["sha256"],
            "selected_pages": selected_pages,
            "total_pages": source_facts["total_pages"],
            "current_stage": stage,
            "completed_stages": completed_stages,
            "elapsed_seconds": round(time.time() - started_epoch, 3),
            "model_calls": [],
            "visual_model": "not-run",
            "rendering": "not-run",
        },
    )
    return path


def _native_command(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout_seconds
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"timeout after {timeout_seconds}s",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def _native_last_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _classify_native_page_route(evidence: dict[str, Any]) -> dict[str, Any]:
    raw_images = evidence.get("images") if isinstance(evidence.get("images"), dict) else {}
    decision = page_router.decide_page_route(
        page=int(evidence.get("page") or 0),
        native_text=str(evidence.get("text") or ""),
        images=page_router.PageImageStats(
            image_count=int(raw_images.get("image_count") or 0),
            total_area_ratio=float(raw_images.get("total_area_ratio") or 0.0),
            max_area_ratio=float(raw_images.get("max_area_ratio") or 0.0),
        ),
        bbox_metrics=page_router.bbox_metrics_from_native_evidence(evidence),
        table_count=evidence.get("table_count_pdfplumber_find_tables"),
        rendered_structure=(
            evidence.get("rendered_structure")
            if isinstance(evidence.get("rendered_structure"), dict)
            else None
        ),
    )
    return {
        **decision,
        "fallback_reason": decision["reason_codes"][0] if decision["needs_review"] else None,
        "native_quality": "usable" if decision["route"] != "visual-only" else "weak",
        "model_called": False,
    }


def _build_native_smoke_report(
    *,
    smoke_pages: list[int],
    total_pages: int,
    page_routes: list[dict[str, Any]],
) -> dict[str, Any]:
    route_counts = {
        route: sum(1 for item in page_routes if item.get("route") == route)
        for route in page_router.PAGE_ROUTES
    }
    model_calls = sum(1 for item in page_routes if item.get("model_called"))
    status = (
        "passed"
        if route_counts["native-plus-visual"] == 0
        and route_counts["visual-only"] == 0
        and not any(item.get("needs_review") for item in page_routes)
        else "needs-review"
    )
    return {
        "format_version": NATIVE_FORMAT_VERSION,
        "status": status,
        "smoke_pages": smoke_pages,
        "total_pages": total_pages,
        "route_counts": route_counts,
        "model_calls": model_calls,
        "page_routes": page_routes,
        "next_step": (
            "continue native-primary full conversion"
            if status == "passed"
            else "switch only failed or weak pages to vision fallback after native smoke review"
        ),
    }


def _native_failure(
    state: GraphState, settings: NativeGraphSettings, stage: str, exc: Exception
) -> dict[str, Any]:
    facts = _native_pdf_facts(settings.source)
    settings.run_root.mkdir(parents=True, exist_ok=True)
    report = settings.run_root / "reports" / "validation-result.json"
    _native_write_json(
        report,
        {
            "format_version": NATIVE_FORMAT_VERSION,
            "status": "failed",
            "stage": stage,
            "error": str(exc),
            "source_sha256": facts["sha256"],
        },
    )
    _native_progress(
        settings.run_root,
        source_facts=facts,
        selected_pages=state.get("selected_pages", []),
        stage=stage,
        status="failed",
        completed_stages=[],
        started_epoch=state.get("started_epoch", time.time()),
    )
    return {
        "current_phase": stage,
        "failed_pages": state.get("selected_pages", []),
        "file_paths": {**state.get("file_paths", {}), "validation_report": str(report)},
        "final_status": "failed",
    }


def _native_needs_review(
    state: GraphState,
    settings: NativeGraphSettings,
    stage: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    facts = _native_pdf_facts(settings.source)
    settings.run_root.mkdir(parents=True, exist_ok=True)
    validation_path = settings.run_root / "reports" / "validation-result.json"
    _native_write_json(
        validation_path,
        {
            "format_version": NATIVE_FORMAT_VERSION,
            "status": "needs-review",
            "stage": stage,
            "source_sha256": facts["sha256"],
            "native_smoke_report": report,
        },
    )
    progress_path = _native_progress(
        settings.run_root,
        source_facts=facts,
        selected_pages=state.get("selected_pages", []),
        stage=stage,
        status="needs-review",
        completed_stages=[{"stage": stage, "status": "needs-review"}],
        started_epoch=state.get("started_epoch", time.time()),
    )
    summary_path = settings.run_root / "summary.json"
    _native_write_json(
        summary_path,
        {
            "format_version": NATIVE_FORMAT_VERSION,
            "status": "needs-review",
            "source": str(settings.source),
            "source_sha256": facts["sha256"],
            "selected_pages": state.get("selected_pages", []),
            "total_pages": facts["total_pages"],
            "native_smoke_report": report,
            "progress": str(progress_path),
            "next_step": report.get("next_step"),
        },
    )
    return {
        "current_phase": stage,
        "failed_pages": [],
        "file_paths": {
            **state.get("file_paths", {}),
            "validation_report": str(validation_path),
            "progress": str(progress_path),
            "summary": str(summary_path),
            "native_smoke_report": str(settings.run_root / "reports" / "native-smoke-report.json"),
        },
        "final_status": "needs-review",
    }


def _native_batch_root(result: dict[str, Any]) -> Path:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    summary_root = summary.get("run_root")
    if summary_root:
        return Path(str(summary_root)).expanduser().resolve()
    run_root = result.get("run_root")
    if run_root:
        return Path(str(run_root)).expanduser().resolve()
    output = result.get("output")
    if output:
        output_path = Path(str(output)).expanduser().resolve()
        return output_path.parent if output_path.suffix else output_path
    markdown = summary.get("markdown")
    if markdown:
        return Path(str(markdown)).expanduser().resolve().parent
    checkpoint = result.get("checkpoint")
    if checkpoint:
        return Path(str(checkpoint)).expanduser().resolve().parent
    raise ValueError("batch output did not identify its run root")


def _native_smoke_batch_root_if_usable(
    result: dict[str, Any] | None,
    *,
    source_facts: dict[str, Any],
    smoke_pages: list[int],
) -> Path | None:
    if not isinstance(result, dict):
        return None
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if summary.get("stage"):
        return None
    try:
        batch_root = _native_batch_root(result)
    except ValueError:
        return None
    expected_source = str(Path(str(source_facts["path"])).expanduser().resolve())
    actual_source = str(Path(str(summary.get("source") or "")).expanduser().resolve())
    if (
        actual_source != expected_source
        or summary.get("source_sha256") != source_facts["sha256"]
        or summary.get("selected_pages") != smoke_pages
        or summary.get("total_pages") != source_facts["total_pages"]
        or summary.get("model_calls") not in (None, [])
        or summary.get("visual_model") != "not-run"
        or summary.get("rendering") != "not-run"
    ):
        return None
    for page in smoke_pages:
        evidence_candidates = (
            _native_page_evidence(batch_root, page),
            batch_root
            / "pages"
            / f"page-{page:03d}"
            / f"page-{page:03d}"
            / "evidence"
            / f"page-{page:03d}.json",
        )
        evidence_path = next(
            (candidate for candidate in evidence_candidates if candidate.is_file()),
            None,
        )
        if evidence_path is None:
            return None
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if evidence.get("source_sha256") != source_facts["sha256"]:
            return None
    return batch_root


def _native_resume_decision(
    *,
    run_root: Path,
    source_facts: dict[str, Any],
    selected_pages: list[int],
) -> dict[str, Any]:
    smoke_report_path = run_root / "reports" / "native-smoke-report.json"
    smoke_roots = [
        _internal_stage_root(run_root, NATIVE_SMOKE_STAGE),
        run_root / "smoke-run",
    ]
    batch_roots = [
        run_root,
        _internal_stage_root(run_root, NATIVE_BATCH_STAGE),
        run_root / "batch-run",
    ]
    smoke_report = None
    try:
        smoke_report = json.loads(smoke_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    smoke_reusable = bool(
        isinstance(smoke_report, dict)
        and smoke_report.get("status") == "passed"
        and smoke_report.get("total_pages") == source_facts["total_pages"]
        and isinstance(smoke_report.get("smoke_pages"), list)
        and set(smoke_report["smoke_pages"]).issubset(set(selected_pages))
    )
    units = run_root / "page-units"
    backup = run_root / "backups" / "page-units-pre-cleanup"
    reusable_batch = None
    batch_summaries: list[Path] = []
    for candidate in batch_roots:
        summary_path = candidate / "summary.json"
        if summary_path.is_file():
            batch_summaries.append(summary_path)
        else:
            batch_summaries.extend(sorted(candidate.glob("*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    for summary_path in batch_summaries:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            summary.get("status") == "passed"
            and summary.get("source_sha256") == source_facts["sha256"]
            and summary.get("selected_pages") == selected_pages
            and all((units / f"p{page:03d}.md").is_file() for page in selected_pages)
            and all((backup / f"p{page:03d}.md").is_file() for page in selected_pages)
        ):
            reusable_batch = summary_path.parent
            break
    return {
        "smoke": "reuse" if smoke_reusable and any(path.is_dir() for path in smoke_roots) else "rerun",
        "conversion": "reuse-full" if reusable_batch else "restart-full",
        "batch_root": reusable_batch,
        "smoke_report": smoke_report_path if smoke_reusable else None,
    }


def _native_page_evidence(batch_root: Path, page: int) -> Path:
    legacy_path = (
        batch_root
        / "pages"
        / f"page-{page:03d}"
        / "evidence"
        / f"page-{page:03d}.json"
    )
    nested_path = (
        batch_root
        / "pages"
        / f"page-{page:03d}"
        / f"page-{page:03d}"
        / "evidence"
        / f"page-{page:03d}.json"
    )
    return nested_path if nested_path.is_file() and not legacy_path.is_file() else legacy_path


def _native_create_page_units(
    *, run_root: Path, batch_root: Path, selected_pages: list[int]
) -> dict[str, Any]:
    units = run_root / "page-units"
    backup = run_root / "backups" / "page-units-pre-cleanup"
    assets = run_root / "assets"
    units.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    if any(assets.iterdir()):
        raise ValueError("native no-model assets directory must remain empty")
    hashes: dict[str, str] = {}
    for page in selected_pages:
        evidence_path = _native_page_evidence(batch_root, page)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence.get("status") != "passed" or not str(evidence.get("text") or "").strip():
            raise ValueError(f"page {page} has no passed native text evidence")
        page_text = str(evidence["text"]).rstrip("\n")
        markdown = f"<!-- page: {page} -->\n\n## 第 {page} 页\n\n{page_text}\n"
        path = units / f"p{page:03d}.md"
        backup_path = backup / path.name
        _native_atomic_text(path, markdown)
        _native_atomic_text(backup_path, markdown)
        hashes[path.name] = _native_sha256(path)
    return {
        "page_units": str(units),
        "pre_cleanup_backup": str(backup),
        "assets": str(assets),
        "page_hashes": hashes,
    }


def _native_cleanup_page_units(
    *, run_root: Path, selected_pages: list[int], total_pages: int
) -> dict[str, Any]:
    units = run_root / "page-units"
    source_lines_by_page: dict[int, list[str]] = {}
    for page in selected_pages:
        path = units / f"p{page:03d}.md"
        source_lines_by_page[page] = path.read_text(encoding="utf-8").splitlines()

    boundary_counts: Counter[str] = Counter()
    for page, lines in source_lines_by_page.items():
        content = [
            line.strip()
            for line in lines
            if line.strip()
            and not line.lstrip().startswith("<!-- page:")
            and not re.fullmatch(r"##\s+第\s*\d+\s*页", line.strip())
            and not re.fullmatch(rf"第\s*{page}\s*页，共\s*{total_pages}\s*页", line.strip())
        ]
        for value in content[:3] + content[-3:]:
            if 3 <= len(value) <= 80 and not value.endswith(("。", "！", "？", ".", "!", "?")):
                boundary_counts[value] += 1
    repeat_threshold = max(3, (len(selected_pages) + 9) // 10)
    repeated_noise = {
        value for value, count in boundary_counts.items() if count >= repeat_threshold
    }

    changes: list[dict[str, Any]] = []
    for page, lines in source_lines_by_page.items():
        keep: list[str] = []
        nonempty_indices = [index for index, line in enumerate(lines) if line.strip()]
        tail = set(nonempty_indices[-3:])
        head = set(nonempty_indices[:5])
        for index, line in enumerate(lines):
            stripped = line.strip()
            reason: str | None = None
            if "\f" in line:
                cleaned = line.replace("\f", "")
                if not cleaned.strip():
                    reason = "form-feed-control"
                else:
                    line = cleaned
                    stripped = line.strip()
            if reason is None and index in tail and re.fullmatch(
                rf"第\s*{page}\s*页，共\s*{total_pages}\s*页", stripped
            ):
                reason = "source-footer-page-counter"
            if reason is None and index in tail and re.fullmatch(r"\d{1,4}", stripped):
                reason = "isolated-boundary-page-number"
            if reason is None and index in (head | tail) and stripped in repeated_noise:
                reason = "repeated-boundary-noise"
            if reason:
                changes.append(
                    {
                        "page": page,
                        "line": index + 1,
                        "reason": reason,
                        "text_sha256": hashlib.sha256(stripped.encode("utf-8")).hexdigest(),
                    }
                )
            else:
                keep.append(line)
        while keep and not keep[-1].strip():
            keep.pop()
        output = "\n".join(keep) + "\n"
        if not output.strip():
            raise ValueError(f"cleanup emptied page {page}")
        _native_atomic_text(units / f"p{page:03d}.md", output)

    report = {
        "format_version": NATIVE_FORMAT_VERSION,
        "status": "passed",
        "mode": "deterministic-delete-only",
        "selected_pages": selected_pages,
        "repeated_noise": sorted(repeated_noise),
        "deleted_line_count": len(changes),
        "changes": changes,
        "checks": {
            "pre_cleanup_backup_exists": all(
                (run_root / "backups" / "page-units-pre-cleanup" / f"p{page:03d}.md").is_file()
                for page in selected_pages
            ),
            "all_page_units_nonempty": all(
                (units / f"p{page:03d}.md").read_text(encoding="utf-8").strip()
                for page in selected_pages
            ),
            "only_complete_lines_deleted": True,
            "body_rewrite": False,
        },
    }
    path = run_root / "reports" / "page-cleanup.json"
    _native_write_json(path, report)
    return {**report, "path": str(path)}


def _native_merge(run_root: Path, selected_pages: list[int]) -> dict[str, Any]:
    units = run_root / "page-units"
    merged = "\n\n".join(
        (units / f"p{page:03d}.md").read_text(encoding="utf-8").rstrip()
        for page in selected_pages
    ) + "\n"
    path = run_root / "document-temp.md"
    _native_atomic_text(path, merged)
    return {"path": str(path), "sha256": _native_sha256(path), "bytes": path.stat().st_size}


def _native_check_merge_coverage(
    *,
    run_root: Path,
    selected_pages: list[int],
    markdown_path: Path,
    require_exact_content: bool,
) -> dict[str, Any]:
    """Verify that every selected page appears exactly once and in order."""

    def comparable_markdown(text: str) -> str:
        # Page units and the merged document use different relative roots for
        # the same deterministic page-image reference.
        return text.replace("../assets/page-", "assets/page-")

    units = run_root / "page-units"
    expected_names = [f"p{page:03d}.md" for page in selected_pages]
    actual_names = sorted(
        path.name
        for path in units.glob("p*.md")
        if re.fullmatch(r"p\d+\.md", path.name)
    ) if units.is_dir() else []
    missing_page_units = [name for name in expected_names if name not in actual_names]
    unexpected_page_units = [name for name in actual_names if name not in expected_names]

    unit_marker_mismatches: list[dict[str, Any]] = []
    expected_parts: list[str] = []
    for page, name in zip(selected_pages, expected_names):
        path = units / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        markers = [
            int(value)
            for value in re.findall(r"<!--\s*page\s*:\s*(\d+)\s*-->", text)
        ]
        if markers != [page]:
            unit_marker_mismatches.append(
                {"page": page, "path": str(path), "actual_markers": markers}
            )
        expected_parts.append(text.rstrip())
    expected_text = "\n\n".join(expected_parts) + "\n" if expected_parts else ""

    markdown_exists = markdown_path.is_file() and markdown_path.stat().st_size > 0
    actual_text = markdown_path.read_text(encoding="utf-8") if markdown_exists else ""
    actual_sequence = [
        int(value)
        for value in re.findall(r"<!--\s*page\s*:\s*(\d+)\s*-->", actual_text)
    ]
    marker_counts = Counter(actual_sequence)
    selected_set = set(selected_pages)
    missing_pages = [page for page in selected_pages if marker_counts[page] == 0]
    duplicate_pages = [page for page in selected_pages if marker_counts[page] > 1]
    unexpected_pages = sorted(page for page in marker_counts if page not in selected_set)
    checks = {
        "page_unit_set_matches": not missing_page_units and not unexpected_page_units,
        "page_unit_markers_match": not unit_marker_mismatches,
        "merged_markdown_exists": markdown_exists,
        "merged_page_count_matches": len(actual_sequence) == len(selected_pages),
        "merged_page_sequence_matches": actual_sequence == selected_pages,
        "merged_content_matches_page_units": (
            comparable_markdown(actual_text) == comparable_markdown(expected_text)
            if require_exact_content
            else True
        ),
    }
    return {
        "format_version": NATIVE_FORMAT_VERSION,
        "status": "passed" if all(checks.values()) else "failed",
        "markdown_path": str(markdown_path),
        "expected_page_count": len(selected_pages),
        "actual_page_count": len(actual_sequence),
        "expected_page_sequence": selected_pages,
        "actual_page_sequence": actual_sequence,
        "missing_pages": missing_pages,
        "duplicate_pages": duplicate_pages,
        "unexpected_pages": unexpected_pages,
        "missing_page_units": missing_page_units,
        "unexpected_page_units": unexpected_page_units,
        "unit_marker_mismatches": unit_marker_mismatches,
        "checks": checks,
    }


def _visual_check_merge_coverage(
    *, run_root: Path, selected_pages: list[int], markdown_path: Path
) -> dict[str, Any]:
    """Verify visual merge coverage without rejecting deterministic reconciliation."""
    return _native_check_merge_coverage(
        run_root=run_root,
        selected_pages=selected_pages,
        markdown_path=markdown_path,
        require_exact_content=False,
    )


def _native_visible_body(line: str) -> str:
    return re.sub(r"^\s{0,3}#{1,6}[ \t]+", "", line, count=1)


def _native_heading_audit_repair(
    *, run_root: Path, selected_pages: list[int]
) -> dict[str, Any]:
    units = run_root / "page-units"
    source = run_root / "document-temp.md"
    expected = "\n\n".join(
        (units / f"p{page:03d}.md").read_text(encoding="utf-8").rstrip()
        for page in selected_pages
    ) + "\n"
    if source.read_text(encoding="utf-8") != expected:
        raise ValueError("document-temp.md is not the deterministic page-unit merge")

    changes: list[dict[str, Any]] = []
    final_pages: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for page in selected_pages:
        text = (units / f"p{page:03d}.md").read_text(encoding="utf-8")
        lines = text.splitlines()
        repaired: list[str] = []
        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if (
                stripped
                and not heading.ATX_RE.match(line)
                and not heading.PROTECTED_RE.match(line)
                and heading.PLAIN_CHAPTER_RE.fullmatch(line)
            ):
                repaired.append(f"### {line}")
                changes.append(
                    {
                        "page": page,
                        "line": line_number,
                        "from_level": 0,
                        "to_level": 3,
                        "rule": "deterministic-visible-chapter-line",
                    }
                )
            else:
                repaired.append(line)
        repaired_text = "\n".join(repaired).rstrip() + "\n"
        for item in heading.find_heading_candidates(page, repaired_text):
            unresolved.append(item)
        final_pages.append(repaired_text.rstrip())

    final_text = "\n\n".join(final_pages) + "\n"
    before_lines = source.read_text(encoding="utf-8").splitlines()
    after_lines = final_text.splitlines()
    body_preserved = len(before_lines) == len(after_lines) and all(
        _native_visible_body(before).strip() == _native_visible_body(after).strip()
        for before, after in zip(before_lines, after_lines)
    )
    before_markers = re.findall(r"<!--\s*page\s*:\s*(\d+)\s*-->", source.read_text(encoding="utf-8"))
    after_markers = re.findall(r"<!--\s*page\s*:\s*(\d+)\s*-->", final_text)
    markers_preserved = before_markers == after_markers == [str(page) for page in selected_pages]
    status = "passed" if body_preserved and markers_preserved and not unresolved else "needs-review"
    index = run_root / "index.md"
    if status == "passed":
        _native_atomic_text(index, final_text)
    report = {
        "format_version": NATIVE_FORMAT_VERSION,
        "status": status,
        "source_path": str(source),
        "source_sha256": _native_sha256(source),
        "index_path": str(index) if status == "passed" else None,
        "index_sha256": _native_sha256(index) if status == "passed" else None,
        "model_called": False,
        "model": None,
        "candidate_count": len(changes) + len(unresolved),
        "changed": changes,
        "unresolved": unresolved,
        "validation": {
            "heading_prefix_only": body_preserved,
            "page_markers_preserved": markers_preserved,
            "visible_body_preserved": body_preserved,
        },
    }
    path = run_root / "reports" / "heading-repair.json"
    _native_write_json(path, report)
    return {**report, "path": str(path)}


_TABLE_RENDER_CODE = r'''
from pathlib import Path
import hashlib, json, re, sys
import pdfplumber

source=Path(sys.argv[1]); units=Path(sys.argv[2]); pages=json.loads(sys.argv[3]); output=Path(sys.argv[4])
rendered=[]
start_re=re.compile(r'<!-- native-table:p\d{3}-t\d{2}:start -->')
end_re=re.compile(r'<!-- native-table:p\d{3}-t\d{2}:end -->')
def strip_existing(text):
    lines=text.splitlines(); kept=[]; inside=False
    for line in lines:
        if start_re.fullmatch(line.strip()): inside=True; continue
        if end_re.fullmatch(line.strip()): inside=False; continue
        if not inside: kept.append(line)
    return '\n'.join(kept).rstrip()+'\n'
def cell(value):
    return ''.join(str(value or '').splitlines()).strip().replace('|','\\|')
with pdfplumber.open(source) as pdf:
    for page_number in pages:
        path=units/f'p{page_number:03d}.md'; text=strip_existing(path.read_text(encoding='utf-8'))
        blocks=[]
        for table_index, table in enumerate(pdf.pages[page_number-1].find_tables(),1):
            rows=[[cell(value) for value in (row or [])] for row in (table.extract() or [])]
            width=max((len(row) for row in rows),default=0)
            if not rows or not width: continue
            rows=[row+['']*(width-len(row)) for row in rows]
            marker=f'p{page_number:03d}-t{table_index:02d}'
            table_lines=[f'<!-- native-table:{marker}:start -->','| '+' | '.join(rows[0])+' |','| '+' | '.join(['---']*width)+' |']
            table_lines.extend('| '+' | '.join(row)+' |' for row in rows[1:])
            table_lines.append(f'<!-- native-table:{marker}:end -->')
            blocks.append('\n'.join(table_lines))
            rendered.append({'page':page_number,'table':table_index,'marker':marker,'rows':len(rows),'columns':width})
        final=text.rstrip()+(('\n\n'+'\n\n'.join(blocks)) if blocks else '')+'\n'
        temporary=path.with_name('.'+path.name+'.table.tmp'); temporary.write_text(final,encoding='utf-8'); temporary.replace(path)
result={'status':'passed','rendered_table_count':len(rendered),'tables':rendered,'page_unit_hashes':{f'p{page:03d}.md':hashlib.sha256((units/f'p{page:03d}.md').read_bytes()).hexdigest() for page in pages}}
output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
'''


def _native_render_tables(
    *, source: Path, run_root: Path, selected_pages: list[int], timeout_seconds: int
) -> dict[str, Any]:
    pre_table = run_root / "backups" / "document-temp-pre-table-render.md"
    document_temp = run_root / "document-temp.md"
    if document_temp.is_file() and not pre_table.exists():
        _native_atomic_text(pre_table, document_temp.read_text(encoding="utf-8"))
    report_path = run_root / "reports" / "native-table-render.json"
    command = [
        str(BUNDLED_PYTHON),
        "-c",
        _TABLE_RENDER_CODE,
        str(source),
        str(run_root / "page-units"),
        json.dumps(selected_pages),
        str(report_path),
    ]
    record = _native_command(command, timeout_seconds)
    if record["returncode"] != 0 or not report_path.is_file():
        raise ValueError(f"native Markdown table rendering failed: {record['stderr']}")
    result = json.loads(report_path.read_text(encoding="utf-8"))
    if result.get("status") != "passed":
        raise ValueError("native Markdown table rendering did not pass")
    return {**result, "path": str(report_path)}


_TABLE_VALIDATOR_CODE = r'''
from collections import Counter
from pathlib import Path
import json, re, sys
import pdfplumber

source=Path(sys.argv[1]); batch_root=Path(sys.argv[2]); units=Path(sys.argv[3]); pages=json.loads(sys.argv[4]); output=Path(sys.argv[5])
detected=0; preserved=0; failures=[]; fonts=Counter(); sizes=Counter()
with pdfplumber.open(source) as pdf:
    for page_number in pages:
        page=pdf.pages[page_number-1]
        legacy_path=batch_root/'pages'/f'page-{page_number:03d}'/'evidence'/f'page-{page_number:03d}.json'
        nested_path=batch_root/'pages'/f'page-{page_number:03d}'/f'page-{page_number:03d}'/'evidence'/f'page-{page_number:03d}.json'
        evidence_path=legacy_path if legacy_path.is_file() else nested_path
        evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
        rendered_text=(units/f'p{page_number:03d}.md').read_text(encoding='utf-8')
        raw=''.join(rendered_text.split())
        for char in page.chars:
            fonts[str(char.get('fontname') or 'unknown')]+=1
            size=char.get('size')
            if isinstance(size,(int,float)): sizes[f'{float(size):.2f}']+=1
        for table_index, table in enumerate(page.find_tables(),1):
            detected += 1
            marker=f'<!--native-table:p{page_number:03d}-t{table_index:02d}:start-->'
            cells=[]
            for row in table.extract() or []:
                for cell in row or []:
                    value=''.join(str(cell or '').split())
                    if value: cells.append(value)
            missing=[value for value in cells if value not in raw]
            if not missing and marker in raw:
                preserved += 1
            else:
                failures.append({'page':page_number,'table':table_index,'marker_present':marker in raw,'missing_cell_hashes':[__import__('hashlib').sha256(value.encode()).hexdigest() for value in missing]})
result={'status':'passed' if detected==preserved else 'failed','detected_table_count':detected,'rendered_table_count':preserved,'table_failures':failures,'font_usage':dict(fonts.most_common()),'font_size_usage':dict(sizes.most_common())}
output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
'''


def _native_validate(
    *, state: GraphState, settings: NativeGraphSettings, heading_report: dict[str, Any]
) -> dict[str, Any]:
    run_root = settings.run_root
    source_facts = _native_pdf_facts(settings.source)
    selected_pages = state["selected_pages"]
    batch_root = Path(state["file_paths"]["batch_root"])
    batch_summary = json.loads((batch_root / "summary.json").read_text(encoding="utf-8"))
    line_ids: list[str] = []
    missing_line_ids: list[str] = []
    physical_line_count = 0
    page_hashes_match = True
    model_calls: list[Any] = []
    page_routes: list[dict[str, Any]] = []
    for page in selected_pages:
        evidence = json.loads(_native_page_evidence(batch_root, page).read_text(encoding="utf-8"))
        evidence["page"] = page
        physical_line_count += int(evidence.get("line_count") or 0)
        actual_ids = list(evidence.get("nonempty_line_ids") or [])
        expected_ids = [
            f"p{page}-native-l{index:04d}"
            for index, line in enumerate(str(evidence.get("text") or "").splitlines(), 1)
            if line.strip()
        ]
        missing_line_ids.extend(sorted(set(expected_ids) - set(actual_ids)))
        line_ids.extend(actual_ids)
        page_hashes_match = page_hashes_match and evidence.get("source_sha256") == source_facts["sha256"]
        model_calls.extend(evidence.get("model_calls") or [])
        page_routes.append(_classify_native_page_route(evidence))
    id_counts = Counter(line_ids)
    duplicate_line_ids = sorted(value for value, count in id_counts.items() if count > 1)
    route_counts = dict(Counter(item["route"] for item in page_routes))

    table_report_path = run_root / "reports" / "native-table-font-validation.json"
    table_command = [
        str(BUNDLED_PYTHON),
        "-c",
        _TABLE_VALIDATOR_CODE,
        str(settings.source),
        str(batch_root),
        str(run_root / "page-units"),
        json.dumps(selected_pages),
        str(table_report_path),
    ]
    table_record = _native_command(table_command, settings.timeout_seconds)
    if table_record["returncode"] != 0 or not table_report_path.is_file():
        raise ValueError(f"independent table/font validation failed: {table_record['stderr']}")
    table_report = json.loads(table_report_path.read_text(encoding="utf-8"))

    expected_pages = _native_parse_pages(settings.pages, source_facts["total_pages"])
    page_units = sorted(
        path
        for path in (run_root / "page-units").glob("p*.md")
        if re.fullmatch(r"p\d+\.md", path.name)
    )
    assets = sorted((run_root / "assets").iterdir())
    relevant_processes: list[dict[str, Any]] = []
    process_probe = subprocess.run(
        ["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False
    )
    for line in process_probe.stdout.splitlines():
        if str(settings.source) not in line:
            continue
        if any(name in line for name in ("lan_pdf_to_markdown.py", "run_pdf_batches.py", "lan_pdf_vision_to_markdown.py")):
            pid_raw, _, command = line.strip().partition(" ")
            relevant_processes.append({"pid": int(pid_raw), "command": command[:500]})

    cleanup_report = json.loads((run_root / "reports" / "page-cleanup.json").read_text(encoding="utf-8"))
    merge_coverage = json.loads(
        (run_root / "reports" / "merge-coverage.json").read_text(encoding="utf-8")
    )
    final_coverage = json.loads(
        (run_root / "reports" / "final-coverage.json").read_text(encoding="utf-8")
    )
    actual_full_coverage = selected_pages == list(range(1, source_facts["total_pages"] + 1))
    checks = {
        "source_sha256_matches": batch_summary.get("source_sha256") == source_facts["sha256"] and page_hashes_match,
        "selected_pages_cover_requested_pdf": selected_pages == expected_pages,
        "full_pdf_coverage_required_and_met": (
            actual_full_coverage if settings.pages.strip().lower() == "all" else True
        ),
        "page_unit_count_matches": len(page_units) == len(selected_pages),
        "merged_document_page_sequence_matches": merge_coverage.get("status") == "passed",
        "final_index_page_sequence_matches": final_coverage.get("status") == "passed",
        "missing_line_ids_empty": not missing_line_ids,
        "duplicate_line_ids_empty": not duplicate_line_ids,
        "detected_tables_equal_rendered_tables": table_report.get("detected_table_count") == table_report.get("rendered_table_count"),
        "heading_audit_passed": heading_report.get("status") == "passed",
        "cleanup_passed": cleanup_report.get("status") == "passed",
        "batch_status_passed": batch_summary.get("status") == "passed",
        "batch_size_one": batch_summary.get("batch_size") == 1,
        "model_calls_empty": not model_calls and not batch_summary.get("model_calls"),
        "route_records_complete": len(page_routes) == len(selected_pages),
        "model_calls_match_native_only_policy": len(model_calls) == 0,
        "visual_model_not_run": batch_summary.get("visual_model") == "not-run",
        "rendering_not_run": batch_summary.get("rendering") == "not-run" and not assets,
        "old_summary_not_reused": batch_summary.get("old_summary_reused") is False,
        "no_child_processes_remaining": not relevant_processes,
        "pre_repair_file_saved": (run_root / "document-temp.md").is_file(),
        "pre_cleanup_pages_saved": (run_root / "backups" / "page-units-pre-cleanup").is_dir(),
    }
    status = "passed" if all(checks.values()) else "failed"
    validation = {
        "format_version": NATIVE_FORMAT_VERSION,
        "status": status,
        "checks": checks,
        "source_sha256": source_facts["sha256"],
        "selected_pages": selected_pages,
        "total_pages": source_facts["total_pages"],
        "actual_full_pdf_coverage": actual_full_coverage,
        "merged_page_count": merge_coverage.get("actual_page_count"),
        "final_page_count": final_coverage.get("actual_page_count"),
        "merge_coverage_report": str(run_root / "reports" / "merge-coverage.json"),
        "final_coverage_report": str(run_root / "reports" / "final-coverage.json"),
        "native_text_physical_lines": physical_line_count,
        "native_text_nonempty_line_ids": len(line_ids),
        "missing_line_ids": missing_line_ids,
        "duplicate_line_ids": duplicate_line_ids,
        "table_count_pdfplumber_find_tables": table_report.get("detected_table_count"),
        "rendered_table_count": table_report.get("rendered_table_count"),
        "model_calls": model_calls,
        "page_routes": page_routes,
        "route_counts": route_counts,
        "visual_model": "not-run",
        "rendering": "not-run",
        "background_processes": relevant_processes,
        "assets": [],
        "font_usage": table_report.get("font_usage", {}),
        "font_size_usage": table_report.get("font_size_usage", {}),
    }
    validation_path = run_root / "reports" / "validation-result.json"
    _native_write_json(validation_path, validation)
    summary = {
        **validation,
        "run_root": str(run_root),
        "source": str(settings.source),
        "batch_size": 1,
        "markdown": str(run_root / "index.md"),
        "route_counts": route_counts,
        "progress": str(run_root / "evidence" / "progress-current.json"),
        "plan": str(run_root / "plan.json"),
        "evidence": str(run_root / "evidence"),
        "native_extraction_elapsed_seconds": batch_summary.get("elapsed_seconds"),
        "full_conversion_elapsed_seconds": round(
            (float(batch_summary.get("elapsed_seconds") or 0.0) if settings.resume else 0.0)
            + (time.time() - state["started_epoch"]),
            3,
        ),
        "old_summary_reused": False,
    }
    _native_write_json(run_root / "summary.json", summary)
    return {"summary": summary, "validation_path": str(validation_path)}


def build_native_graph(settings: NativeGraphSettings):
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:
        raise RuntimeError("LangGraph is not available in the selected local Python runtime") from exc

    def smoke_pages_stage(state: GraphState) -> dict[str, Any]:
        try:
            facts = _native_pdf_facts(settings.source)
            selected = state["selected_pages"]
            smoke_root = _internal_stage_root(settings.run_root, NATIVE_SMOKE_STAGE)
            smoke_pages = (
                _native_parse_pages(settings.smoke_pages, facts["total_pages"])
                if settings.smoke_pages
                else _default_native_smoke_pages(selected)
            )
            resume_decision = _native_resume_decision(
                run_root=settings.run_root,
                source_facts=facts,
                selected_pages=selected,
            )
            if settings.resume and resume_decision["smoke"] == "reuse":
                report_path = Path(resume_decision["smoke_report"])
                progress = _native_progress(
                    settings.run_root,
                    source_facts=facts,
                    selected_pages=selected,
                    stage="page-native-smoke-resume-reuse",
                    status="running",
                    completed_stages=[{"stage": "page-native-smoke", "status": "passed-resumed"}],
                    started_epoch=state["started_epoch"],
                )
                return {
                    "current_phase": "page-native-smoke-resume-reuse",
                    "file_paths": {
                        **state["file_paths"],
                        "native_smoke_report": str(report_path),
                        "progress": str(progress),
                    },
                    "file_hashes": {
                        **state["file_hashes"],
                        "native_smoke_report": _native_sha256(report_path),
                    },
                    "final_status": "running",
                }
            runtime_python = Path(sys.executable)
            smoke_root.mkdir(parents=True, exist_ok=True)
            command = [
                str(runtime_python),
                str(BATCH_ADAPTER),
                "--source",
                str(settings.source),
                "--output-root",
                str(smoke_root),
                "--resume-package",
                str(smoke_root),
                "--pages",
                ",".join(str(page) for page in smoke_pages),
                "--batch-size",
                "1",
                "--context-mode",
                "independent",
            ]
            record = _native_command(command, settings.timeout_seconds)
            result = _native_last_json(record["stdout"])
            smoke_batch_root = _native_smoke_batch_root_if_usable(
                result,
                source_facts=facts,
                smoke_pages=smoke_pages,
            )
            _native_write_json(
                settings.run_root / "evidence" / "native-smoke-command.json",
                {
                    **record,
                    "stdout": record["stdout"][-100000:],
                    "stderr": record["stderr"][-100000:],
                    "parsed_result": result,
                    "bounded_smoke_usable": smoke_batch_root is not None,
                },
            )
            if smoke_batch_root is None:
                raise ValueError(f"native smoke failed: returncode={record['returncode']} result={result}")
            page_routes = []
            for page in smoke_pages:
                evidence = json.loads(
                    _native_page_evidence(smoke_batch_root, page).read_text(encoding="utf-8")
                )
                evidence["page"] = page
                page_routes.append(_classify_native_page_route(evidence))
            report = _build_native_smoke_report(
                smoke_pages=smoke_pages,
                total_pages=facts["total_pages"],
                page_routes=page_routes,
            )
            report_path = settings.run_root / "reports" / "native-smoke-report.json"
            _native_write_json(report_path, report)
            progress = _native_progress(
                settings.run_root,
                source_facts=facts,
                selected_pages=selected,
                stage="page-native-smoke",
                status=report["status"],
                completed_stages=[{"stage": "page-native-smoke", "status": report["status"]}],
                started_epoch=state["started_epoch"],
            )
            if report["status"] != "passed":
                return _native_needs_review(state, settings, "page-native-smoke", report)
            return {
                "current_phase": "page-native-smoke",
                "file_paths": {
                    **state["file_paths"],
                    "native_smoke_report": str(report_path),
                    "native_smoke_batch_root": str(smoke_batch_root),
                    "progress": str(progress),
                },
                "file_hashes": {
                    **state["file_hashes"],
                    "native_smoke_report": _native_sha256(report_path),
                },
                "final_status": "running",
            }
        except Exception as exc:
            return _native_failure(state, settings, "page-native-smoke", exc)

    def convert_pages(state: GraphState) -> dict[str, Any]:
        try:
            facts = _native_pdf_facts(settings.source)
            selected = _native_parse_pages(settings.pages, facts["total_pages"])
            # The full native page packages are part of the user-facing result,
            # so write them directly to ``run_root/pages/page-NNN``.  Hidden
            # stage roots remain supported above only for resuming older runs.
            batch_root = settings.run_root
            if settings.batch_size != 1:
                raise ValueError("native LangGraph requires batch_size=1")
            settings.run_root.mkdir(parents=True, exist_ok=True)
            started_epoch = state["started_epoch"]
            resume_decision = _native_resume_decision(
                run_root=settings.run_root,
                source_facts=facts,
                selected_pages=selected,
            )
            if settings.resume and resume_decision["conversion"] == "reuse-full":
                batch_root = Path(resume_decision["batch_root"])
                raw_backup = settings.run_root / "backups" / "page-units-pre-cleanup"
                units = settings.run_root / "page-units"
                for page in selected:
                    source_unit = raw_backup / f"p{page:03d}.md"
                    _native_atomic_text(
                        units / source_unit.name,
                        source_unit.read_text(encoding="utf-8"),
                    )
                progress = _native_progress(
                    settings.run_root,
                    source_facts=facts,
                    selected_pages=selected,
                    stage="page-native-conversion-resume-skip",
                    status="running",
                    completed_stages=[{"stage": "page-native-conversion", "status": "passed-resumed"}],
                    started_epoch=started_epoch,
                )
                return {
                    "current_phase": "page-native-conversion-resume-skip",
                    "completed_pages": selected,
                    "failed_pages": [],
                    "selected_pages": selected,
                    "total_pages": facts["total_pages"],
                    "file_paths": {
                        **state["file_paths"],
                        "page_units": str(units),
                        "pre_cleanup_backup": str(raw_backup),
                        "assets": str(settings.run_root / "assets"),
                        "batch_root": str(batch_root),
                        "progress": str(progress),
                    },
                    "file_hashes": state["file_hashes"],
                    "final_status": "running",
                }
            plan = {
                "format_version": NATIVE_FORMAT_VERSION,
                "status": "running",
                "graph": [
                    "page-native-smoke",
                    "page-native-conversion",
                    "page-cleanup",
                    "merge-original-markdown",
                    "merge-coverage-validation",
                    "heading-audit-repair-validation",
                ],
                "source": facts,
                "selected_pages": selected,
                "batch_size": 1,
                "scripts": {
                    "page_adapter": str(LAN_ADAPTER),
                    "batch_adapter": str(BATCH_ADAPTER),
                    "graph": str(Path(__file__).resolve()),
                },
                "model_policy": "no-model",
                "visual_policy": "not-run",
                "rendering_policy": "not-run",
                "output_layout": {
                    "assets": "assets/ (empty because rendering is forbidden)",
                    "page_units": "page-units/pNNN.md",
                    "raw_merge": "document-temp.md",
                    "final": "index.md",
                },
                "old_summary_reused": False,
            }
            _native_write_json(settings.run_root / "plan.json", plan)
            # The graph runtime already provides LangGraph and PyYAML.  The
            # batch adapter deliberately selects the bundled PDF runtime for
            # each child page so pdfplumber remains available without adding
            # or installing dependencies.
            runtime_python = Path(sys.executable)
            batch_root.mkdir(parents=True, exist_ok=True)
            command = [
                str(runtime_python),
                str(BATCH_ADAPTER),
                "--source",
                str(settings.source),
                "--output-root",
                str(batch_root),
                "--resume-package",
                str(batch_root),
                "--pages",
                settings.pages,
                "--batch-size",
                "1",
                "--context-mode",
                "independent",
            ]
            record = _native_command(command, settings.timeout_seconds)
            result = _native_last_json(record["stdout"])
            _native_write_json(
                settings.run_root / "evidence" / "batch-command.json",
                {
                    **record,
                    "stdout": record["stdout"][-100000:],
                    "stderr": record["stderr"][-100000:],
                    "parsed_result": result,
                },
            )
            if record["returncode"] != 0 or not result or result.get("status") != "passed":
                raise ValueError(f"batch conversion failed: returncode={record['returncode']} result={result}")
            batch_root = _native_batch_root(result)
            layout = _native_create_page_units(
                run_root=settings.run_root, batch_root=batch_root, selected_pages=selected
            )
            progress = _native_progress(
                settings.run_root,
                source_facts=facts,
                selected_pages=selected,
                stage="page-native-conversion",
                status="running",
                completed_stages=[
                    {"stage": "page-native-smoke", "status": "passed"},
                    {"stage": "page-native-conversion", "status": "passed"},
                ],
                started_epoch=started_epoch,
            )
            return {
                "current_phase": "page-native-conversion",
                "completed_pages": selected,
                "failed_pages": [],
                "selected_pages": selected,
                "total_pages": facts["total_pages"],
                "file_paths": {
                    **state["file_paths"],
                    **layout,
                    "batch_root": str(batch_root),
                    "progress": str(progress),
                },
                "file_hashes": state["file_hashes"],
                "final_status": "running",
            }
        except Exception as exc:
            return _native_failure(state, settings, "page-native-conversion", exc)

    def cleanup_pages(state: GraphState) -> dict[str, Any]:
        try:
            report = _native_cleanup_page_units(
                run_root=settings.run_root,
                selected_pages=state["selected_pages"],
                total_pages=state["total_pages"],
            )
            if report["status"] != "passed":
                raise ValueError("page cleanup did not pass")
            table_render = _native_render_tables(
                source=settings.source,
                run_root=settings.run_root,
                selected_pages=state["selected_pages"],
                timeout_seconds=settings.timeout_seconds,
            )
            facts = _native_pdf_facts(settings.source)
            progress = _native_progress(
                settings.run_root,
                source_facts=facts,
                selected_pages=state["selected_pages"],
                stage="page-cleanup",
                status="running",
                completed_stages=[
                    {"stage": "page-native-smoke", "status": "passed"},
                    {"stage": "page-native-conversion", "status": "passed"},
                    {"stage": "page-cleanup", "status": "passed"},
                ],
                started_epoch=state["started_epoch"],
            )
            return {
                "current_phase": "page-cleanup",
                "file_paths": {
                    **state["file_paths"],
                    "cleanup_report": report["path"],
                    "table_render_report": table_render["path"],
                    "progress": str(progress),
                },
                "final_status": "running",
            }
        except Exception as exc:
            return _native_failure(state, settings, "page-cleanup", exc)

    def merge_pages(state: GraphState) -> dict[str, Any]:
        try:
            merged = _native_merge(settings.run_root, state["selected_pages"])
            facts = _native_pdf_facts(settings.source)
            progress = _native_progress(
                settings.run_root,
                source_facts=facts,
                selected_pages=state["selected_pages"],
                stage="merge-original-markdown",
                status="running",
                completed_stages=[
                    {"stage": "page-native-smoke", "status": "passed"},
                    {"stage": "page-native-conversion", "status": "passed"},
                    {"stage": "page-cleanup", "status": "passed"},
                    {"stage": "merge-original-markdown", "status": "passed"},
                ],
                started_epoch=state["started_epoch"],
            )
            return {
                "current_phase": "merge-original-markdown",
                "file_paths": {**state["file_paths"], "document_temp": merged["path"], "progress": str(progress)},
                "file_hashes": {**state["file_hashes"], "document_temp": merged["sha256"]},
                "final_status": "running",
            }
        except Exception as exc:
            return _native_failure(state, settings, "merge-original-markdown", exc)

    def verify_merge_coverage(state: GraphState) -> dict[str, Any]:
        try:
            report = _native_check_merge_coverage(
                run_root=settings.run_root,
                selected_pages=state["selected_pages"],
                markdown_path=settings.run_root / "document-temp.md",
                require_exact_content=True,
            )
            report_path = settings.run_root / "reports" / "merge-coverage.json"
            _native_write_json(report_path, report)
            if report["status"] != "passed":
                raise ValueError(
                    "merged Markdown page coverage failed: "
                    f"expected={report['expected_page_count']} "
                    f"actual={report['actual_page_count']} "
                    f"missing={report['missing_pages']} "
                    f"duplicates={report['duplicate_pages']}"
                )
            facts = _native_pdf_facts(settings.source)
            progress = _native_progress(
                settings.run_root,
                source_facts=facts,
                selected_pages=state["selected_pages"],
                stage="merge-coverage-validation",
                status="running",
                completed_stages=[
                    {"stage": "page-native-smoke", "status": "passed"},
                    {"stage": "page-native-conversion", "status": "passed"},
                    {"stage": "page-cleanup", "status": "passed"},
                    {"stage": "merge-original-markdown", "status": "passed"},
                    {"stage": "merge-coverage-validation", "status": "passed"},
                ],
                started_epoch=state["started_epoch"],
            )
            return {
                "current_phase": "merge-coverage-validation",
                "file_paths": {
                    **state["file_paths"],
                    "merge_coverage_report": str(report_path),
                    "progress": str(progress),
                },
                "file_hashes": {
                    **state["file_hashes"],
                    "merge_coverage_report": _native_sha256(report_path),
                },
                "final_status": "running",
            }
        except Exception as exc:
            return _native_failure(state, settings, "merge-coverage-validation", exc)

    def audit_validate(state: GraphState) -> dict[str, Any]:
        try:
            repair = _native_heading_audit_repair(
                run_root=settings.run_root, selected_pages=state["selected_pages"]
            )
            if repair["status"] != "passed":
                raise ValueError(f"heading audit needs review: {repair.get('unresolved')}")
            final_coverage = _native_check_merge_coverage(
                run_root=settings.run_root,
                selected_pages=state["selected_pages"],
                markdown_path=settings.run_root / "index.md",
                require_exact_content=False,
            )
            final_coverage_path = settings.run_root / "reports" / "final-coverage.json"
            _native_write_json(final_coverage_path, final_coverage)
            if final_coverage["status"] != "passed":
                raise ValueError(
                    "final index page coverage failed: "
                    f"expected={final_coverage['expected_page_count']} "
                    f"actual={final_coverage['actual_page_count']} "
                    f"missing={final_coverage['missing_pages']} "
                    f"duplicates={final_coverage['duplicate_pages']}"
                )
            validation = _native_validate(state=state, settings=settings, heading_report=repair)
            if validation["summary"]["status"] != "passed":
                raise ValueError(f"independent validation failed: {validation['summary']['checks']}")
            facts = _native_pdf_facts(settings.source)
            progress = _native_progress(
                settings.run_root,
                source_facts=facts,
                selected_pages=state["selected_pages"],
                stage="heading-audit-repair-validation",
                status="passed",
                completed_stages=[
                    {"stage": "page-native-smoke", "status": "passed"},
                    {"stage": "page-native-conversion", "status": "passed"},
                    {"stage": "page-cleanup", "status": "passed"},
                    {"stage": "merge-original-markdown", "status": "passed"},
                    {"stage": "merge-coverage-validation", "status": "passed"},
                    {"stage": "heading-audit-repair-validation", "status": "passed"},
                ],
                started_epoch=state["started_epoch"],
            )
            summary_path = settings.run_root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["progress"] = str(progress)
            _native_write_json(summary_path, summary)
            return {
                "current_phase": "heading-audit-repair-validation",
                "file_paths": {
                    **state["file_paths"],
                    "index": str(settings.run_root / "index.md"),
                    "heading_report": repair["path"],
                    "final_coverage_report": str(final_coverage_path),
                    "validation_report": validation["validation_path"],
                    "summary": str(summary_path),
                    "progress": str(progress),
                },
                "file_hashes": {
                    **state["file_hashes"],
                    "index": _native_sha256(settings.run_root / "index.md"),
                    "final_coverage_report": _native_sha256(final_coverage_path),
                    "summary": _native_sha256(summary_path),
                    "progress": _native_sha256(progress),
                },
                "final_status": "passed",
            }
        except Exception as exc:
            return _native_failure(state, settings, "heading-audit-repair-validation", exc)

    def continue_or_end(state: GraphState):
        return END if state["final_status"] in {"failed", "needs-review"} else "continue"

    builder = StateGraph(GraphState)
    builder.add_node("page_native_smoke", smoke_pages_stage)
    builder.add_node("page_native_conversion", convert_pages)
    builder.add_node("page_cleanup", cleanup_pages)
    builder.add_node("merge_original_markdown", merge_pages)
    builder.add_node("merge_coverage_validation", verify_merge_coverage)
    builder.add_node("heading_audit_repair_validation", audit_validate)
    builder.add_edge(START, "page_native_smoke")
    builder.add_conditional_edges("page_native_smoke", continue_or_end, {"continue": "page_native_conversion", END: END})
    builder.add_conditional_edges("page_native_conversion", continue_or_end, {"continue": "page_cleanup", END: END})
    builder.add_conditional_edges("page_cleanup", continue_or_end, {"continue": "merge_original_markdown", END: END})
    builder.add_conditional_edges("merge_original_markdown", continue_or_end, {"continue": "merge_coverage_validation", END: END})
    builder.add_conditional_edges("merge_coverage_validation", continue_or_end, {"continue": "heading_audit_repair_validation", END: END})
    builder.add_edge("heading_audit_repair_validation", END)
    return builder.compile()


@resource_slots.uses_local_pdf_slot("lan-pdf-markdown-graph-native")
def run_native_graph(settings: NativeGraphSettings) -> GraphState:
    source = settings.source.expanduser().resolve()
    run_root = settings.run_root.expanduser().resolve()
    settings = NativeGraphSettings(
        source=source,
        run_root=run_root,
        pages=settings.pages,
        smoke_pages=settings.smoke_pages,
        batch_size=settings.batch_size,
        timeout_seconds=settings.timeout_seconds,
        resume=settings.resume,
    )
    facts = _native_pdf_facts(source)
    selected = _native_parse_pages(settings.pages, facts["total_pages"])
    if run_root.exists() and any(run_root.iterdir()) and not settings.resume:
        raise ValueError(f"run root must be new or empty before native graph start: {run_root}")
    active = _active_same_source_processes(source)
    if active:
        raise RuntimeError(f"active same-source conversion found: {active}")
    initial: GraphState = {
        "source_path": str(source),
        "source_sha256": facts["sha256"],
        "run_root": str(run_root),
        "current_phase": "not-started",
        "completed_pages": [],
        "failed_pages": [],
        "file_paths": {},
        "file_hashes": {},
        "final_status": "running",
        "selected_pages": selected,
        "total_pages": facts["total_pages"],
        "started_epoch": time.time(),
    }
    try:
        with task_guard.acquire_task_lock(
            source=source,
            source_sha256=facts["sha256"],
            # Lock at the package parent so the graph-level lock does not
            # collide with its own page-batch child lock inside ``run_root``.
            output_root=run_root.parent,
            pages=settings.pages,
            entrypoint="lan_pdf_markdown_graph.py",
        ):
            return build_native_graph(settings).invoke(initial)
    except KeyboardInterrupt:
        task_guard.write_cancelled_run_state(
            summary_paths=[run_root / "summary.json"],
            progress_paths=[run_root / "evidence" / "progress-current.json"],
            reason="keyboard-interrupt",
            source=str(source),
            selected_pages=selected,
            total_pages=facts["total_pages"],
        )
        raise


@resource_slots.uses_local_pdf_slot("lan-pdf-markdown-graph-hybrid")
def run_page_hybrid_graph(
    *,
    source: Path,
    run_root: Path,
    pages: str,
    batch_size: int,
    timeout_seconds: int,
    dpi: int,
    resume: bool,
) -> GraphState:
    """Run the canonical native-first page router in one exact run root."""

    try:
        from adapters import run_pdf_batches as hybrid_batch
    except ImportError:  # pragma: no cover - direct adapter execution
        import run_pdf_batches as hybrid_batch

    source = source.expanduser().resolve()
    run_root = run_root.expanduser().resolve()
    facts = _native_pdf_facts(source)
    selected = _native_parse_pages(pages, facts["total_pages"])
    if batch_size != 1:
        raise ValueError("page-hybrid graph requires --batch-size 1")
    if run_root.exists() and any(run_root.iterdir()) and not resume:
        raise ValueError(f"run root must be new or explicitly resumed: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with task_guard.acquire_task_lock(
        source=source,
        source_sha256=facts["sha256"],
        output_root=run_root,
        pages=pages,
        entrypoint="lan_pdf_markdown_graph.py:page-hybrid",
    ):
        _code, result = hybrid_batch.run_page_batches(
            source=source,
            output_root=run_root.parent,
            pages=pages,
            batch_size=1,
            timeout=timeout_seconds,
            context_mode="independent",
            context_window=1,
            resume_package=run_root,
            hybrid=True,
            visual_dpi=dpi,
        )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    completion = (
        summary.get("page_completion")
        if isinstance(summary.get("page_completion"), dict)
        else {}
    )
    completed_pages = list(completion.get("completed_pages") or [])
    failed_pages = list(completion.get("incomplete_pages") or [])
    return {
        "source_path": str(source),
        "source_sha256": facts["sha256"],
        "run_root": str(run_root),
        "current_phase": "page-hybrid-validation",
        "completed_pages": completed_pages,
        "failed_pages": failed_pages,
        "file_paths": {
            "page_units": str(run_root / "page-units"),
            "page_routes": str(run_root / "evidence" / "page-routes"),
            "page_completion_report": str(run_root / "reports" / "page-completion.json"),
            "index": str(run_root / "index.md"),
            "summary": str(run_root / "summary.json"),
            "progress": str(run_root / "progress.json"),
            "validation_report": str(run_root / "acceptance.json"),
        },
        "file_hashes": {},
        "final_status": str(result.get("status") or summary.get("status") or "failed"),
        "selected_pages": selected,
        "total_pages": facts["total_pages"],
        "started_epoch": started,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--pages", default="all")
    parser.add_argument(
        "--evidence-level", choices=NATIVE_EVIDENCE_LEVELS, default="compact"
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--confirmed-noise", action="append", default=[])
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="使用现有批处理器逐页执行纯原生提取；不导入视觉适配器、不渲染、不调用模型",
    )
    parser.add_argument(
        "--visual-only",
        action="store_true",
        help="显式整页视觉路线；默认路线是页面级 native-first 混合处理",
    )
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument(
        "--smoke-pages",
        help="记录已动态验证的试运行页面；必须包含在 --pages 范围内",
    )
    parser.add_argument(
        "--no-heading-model",
        action="store_true",
        help="标题异常只审计不调用模型；存在异常时最终状态为 needs-review",
    )
    parser.add_argument(
        "--no-visual-asset-pruning",
        action="store_true",
        help="跳过终端视觉资产判定与派生页面 PNG 清理；默认执行 fail-closed 后置阶段",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="创建全新时间戳运行目录，执行最多 3 页，并二次验证恢复跳过",
    )
    parser.add_argument(
        "--thinking-tools-smoke",
        action="store_true",
        help="使用已知《100个思维工具详解.pdf》事实运行 1-3 页 smoke",
    )
    parser.add_argument("--runs-parent", type=Path, default=DEFAULT_RUNS_PARENT)
    parser.add_argument(
        "--auto-run-root",
        action="store_true",
        help="正常生成时自动创建新的时间戳运行目录；不得与 --run-root 同用",
    )
    parser.add_argument(
        "--run-label",
        help="配合 --auto-run-root 使用的运行目录标签；默认由源文件名生成",
    )
    parser.add_argument("--smoke-label", default=DEFAULT_SMOKE_LABEL)
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--expected-total-pages", type=int)
    parser.add_argument("--expected-size-bytes", type=int)
    parser.add_argument("--expected-pdf-version")
    parser.add_argument("--expected-page-size-contains")
    parser.add_argument(
        "--no-verify-resume-skip",
        dest="verify_resume_skip",
        action="store_false",
        help="只运行一次 smoke，不执行二次跳过验证",
    )
    parser.set_defaults(verify_resume_skip=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.no_model:
            if args.visual_only:
                parser.error("--no-model and --visual-only are mutually exclusive")
            if args.smoke or args.thinking_tools_smoke:
                parser.error("--no-model full/native route cannot be combined with visual smoke modes")
            run_root = _resolve_run_root(
                parser,
                args,
                require_source_message="--source is required with --no-model",
            )
            _emit_run_root(run_root, mode="native-no-model")
            result = run_native_graph(
                NativeGraphSettings(
                    source=args.source,
                    run_root=run_root,
                    pages=args.pages,
                    smoke_pages=args.smoke_pages,
                    batch_size=args.batch_size,
                    timeout_seconds=args.timeout_seconds,
                    resume=args.resume,
                )
            )
        elif args.smoke or args.thinking_tools_smoke:
            result = run_smoke(args)
        else:
            run_root = _resolve_run_root(
                parser,
                args,
                require_source_message="--source is required outside smoke mode",
            )
            if args.visual_only:
                _emit_run_root(run_root, mode="visual-only")
                settings = GraphSettings(
                    source=args.source,
                    run_root=run_root,
                    pages=args.pages,
                    evidence_level=args.evidence_level,
                    resume=args.resume,
                    dpi=args.dpi,
                    timeout_seconds=args.timeout_seconds,
                    progress_every=args.progress_every,
                    confirmed_noise=tuple(args.confirmed_noise),
                    use_heading_model=not args.no_heading_model,
                    prune_visual_assets=not args.no_visual_asset_pruning,
                )
                result = run_graph(settings)
            else:
                _emit_run_root(run_root, mode="page-hybrid")
                result = run_page_hybrid_graph(
                    source=args.source,
                    run_root=run_root,
                    pages=args.pages,
                    batch_size=args.batch_size,
                    timeout_seconds=args.timeout_seconds,
                    dpi=args.dpi,
                    resume=args.resume,
                )
    except Exception as exc:
        print(f"ERROR handled=true {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("final_status", result.get("status")) == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
