#!/usr/bin/env python3
"""Explicit loader for the archived Workflow A compatibility adapter.

The active runner must not import this module.  Compatibility and recovery
tools use it only when they intentionally opt into the archived adapter.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
ARCHIVED_RUNNER = ROOT / "archive/legacy-adapters/workflow_a_runner.py"
_MODULE: ModuleType | None = None


def load() -> ModuleType:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    if not ARCHIVED_RUNNER.is_file():
        raise ImportError(f"archived legacy runner is missing: {ARCHIVED_RUNNER}")
    spec = importlib.util.spec_from_file_location("workflow_a_archived_legacy_runner", ARCHIVED_RUNNER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load archived legacy runner: {ARCHIVED_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def __getattr__(name: str):
    return getattr(load(), name)


__all__ = [
    "append_progress_event",
    "build_markdown",
    "classify_content",
    "evaluate_smoke_pages",
    "mac96_semantic_usable",
    "merge_ocr_body_candidates",
    "parse_pages",
    "select_ocr_title",
    "title_quality_flags",
    "write_json",
    "write_render_fontconfig",
    "write_smoke_report",
]
