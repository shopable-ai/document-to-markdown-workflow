#!/usr/bin/env python3
"""Reconcile an explicit TOC continuation across two adjacent page records.

The adapter is intentionally derivational: it never edits either per-page
structure record.  It emits one traceable merge proposal only when both page
boundary flags and both block shapes agree.  Ambiguous boundaries remain
needs-review instead of being guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT = "cross-page-markdown-reconciliation"
VERSION = "0.1.0"
CASE_RE = re.compile(r"^\s*-?\s*案例\s*\d+\s*[：:]")
CHAPTER_RE = re.compile(r"^\s*#{1,6}\s*第[一二三四五六七八九十百零〇0-9]+[章节篇部卷]")


class ReconciliationError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _boundary_block(payload: dict[str, Any], *, position: str) -> dict[str, Any] | None:
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return None
    block = blocks[-1] if position == "last" else blocks[0]
    return block if isinstance(block, dict) else None


def _without_list_marker(markdown: str) -> str:
    return re.sub(r"^\s*-\s*", "", markdown, count=1).strip()


def reconcile(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    previous_path: Path,
    current_path: Path,
) -> dict[str, Any]:
    previous_page = previous.get("page")
    current_page = current.get("page")
    if not isinstance(previous_page, int) or not isinstance(current_page, int):
        raise ReconciliationError("both records must contain integer page values")
    if current_page != previous_page + 1:
        raise ReconciliationError("records must describe adjacent pages")

    previous_inventory = previous.get("structure_inventory")
    current_inventory = current.get("structure_inventory")
    previous_block = _boundary_block(previous, position="last")
    current_block = _boundary_block(current, position="first")
    diagnostics: list[str] = []
    status = "needs-review"
    merged_block: dict[str, Any] | None = None

    flags_agree = bool(
        isinstance(previous_inventory, dict)
        and previous_inventory.get("ends_with_continuation") is True
        and isinstance(current_inventory, dict)
        and current_inventory.get("starts_with_continuation") is True
    )
    blocks_agree = bool(
        previous_block
        and current_block
        and previous_block.get("kind") == "toc-entry"
        and current_block.get("kind") == "toc-entry"
        and previous_block.get("page_reference_detected") is False
        and current_block.get("page_reference_detected") is True
    )

    if not flags_agree:
        diagnostics.append("adjacent records do not expose matching continuation flags")
    elif not blocks_agree:
        diagnostics.append("boundary blocks are not an unambiguous TOC continuation pair")
    else:
        previous_markdown = str(previous_block.get("markdown", "")).strip()
        current_markdown = str(current_block.get("markdown", "")).strip()
        if not CASE_RE.match(previous_markdown):
            diagnostics.append("previous boundary block is not an explicit numbered case")
        elif CASE_RE.match(current_markdown) or CHAPTER_RE.match(current_markdown):
            diagnostics.append("current boundary block starts a new entry instead of continuing")
        else:
            merged_markdown = previous_markdown + _without_list_marker(current_markdown)
            merged_block = {
                "kind": "toc-entry",
                "markdown": merged_markdown,
                "source_blocks": [
                    {
                        "page": previous_page,
                        "source_candidate_indices": previous_block.get(
                            "source_candidate_indices", []
                        ),
                    },
                    {
                        "page": current_page,
                        "source_candidate_indices": current_block.get(
                            "source_candidate_indices", []
                        ),
                    },
                ],
                "operation": "replace-boundary-pair-in-derived-document-only",
            }
            status = "passed"

    return {
        "contract": CONTRACT,
        "version": VERSION,
        "created_at": now(),
        "status": status,
        "pages": [previous_page, current_page],
        "source_records": [
            {"path": str(previous_path), "sha256": digest(previous_path)},
            {"path": str(current_path), "sha256": digest(current_path)},
        ],
        "source_records_mutated": False,
        "merged_block": merged_block,
        "advertising_policy": {
            "mode": "preserve-only",
            "classification_stage": "not-run",
            "applied_action": "preserve",
        },
        "diagnostics": diagnostics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        previous_path = args.previous.expanduser().resolve()
        current_path = args.current.expanduser().resolve()
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        current = json.loads(current_path.read_text(encoding="utf-8"))
        result = reconcile(
            previous,
            current,
            previous_path=previous_path,
            current_path=current_path,
        )
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ReconciliationError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
