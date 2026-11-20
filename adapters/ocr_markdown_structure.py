#!/usr/bin/env python3
"""Deterministically project line-level OCR evidence into conservative Markdown.

This adapter is deliberately narrower than a language model.  It keeps every
OCR candidate accounted for, uses only explicit numbering and bbox-derived
visual prominence, and records every structural decision for later review.
It never classifies or removes advertising-like text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT = "ocr-markdown-structure"
VERSION = "0.3.0"
PROMPT_VERSION = "deterministic-bbox-structure-v4"
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+[章节篇部卷]\s*[：:]?")
EXPLICIT_LIST_RE = re.compile(
    r"^(?:\d{1,4}[.、]|[（(]?[一二三四五六七八九十百零〇]+[）)、.]|[-*•·])\s*"
)
CASE_RE = re.compile(r"^案例\s*\d+\s*[：:]")
PAGE_NUMBER_RE = re.compile(r"^[.．…·⋯,:\uFF1A，\s]*\d{1,4}$")
TRAILING_PAGE_RE = re.compile(
    r"(?:[/／]|[.．…·⋯,:：，]+|[?？])\s*\d{1,4}\s*$"
)
STRONG_TERMINAL_RE = re.compile(r"[。！？!?；;]$|[”’」』）)]$")
INTERNAL_SENTENCE_END_RE = re.compile(r"[。！？!?；;]\s*\S")
PARAGRAPH_GAP_FACTOR = 3.5


class StructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    index: int
    text: str
    confidence: float | None
    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _number(value: Any, *, field: str, index: int) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StructureError(f"candidate {index} has invalid bbox.{field}")
    return float(value)


def parse_candidates(payload: dict[str, Any]) -> list[Candidate]:
    raw = payload.get("candidates")
    if payload.get("status") != "complete" or not isinstance(raw, list):
        raise StructureError("OCR payload must have status=complete and a candidates array")
    candidates: list[Candidate] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise StructureError(f"candidate {index} must be an object")
        text = item.get("text")
        bbox = item.get("bbox")
        if not isinstance(text, str) or not text.strip():
            raise StructureError(f"candidate {index} has empty text")
        if not isinstance(bbox, dict):
            raise StructureError(f"candidate {index} has no bbox")
        confidence = item.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
        ):
            raise StructureError(f"candidate {index} has invalid confidence")
        candidates.append(
            Candidate(
                index=index,
                text=text.strip(),
                confidence=float(confidence) if confidence is not None else None,
                x=_number(bbox.get("x"), field="x", index=index),
                y=_number(bbox.get("y"), field="y", index=index),
                width=_number(bbox.get("width"), field="width", index=index),
                height=_number(bbox.get("height"), field="height", index=index),
            )
        )
    if not candidates:
        raise StructureError("OCR payload contains no text candidates")
    return candidates


def group_rows(candidates: list[Candidate]) -> list[list[Candidate]]:
    median_height = statistics.median(item.height for item in candidates)
    tolerance = max(0.006, median_height * 0.6)
    rows: list[list[Candidate]] = []
    for candidate in sorted(candidates, key=lambda item: (-item.center_y, item.x, item.index)):
        if rows:
            center = statistics.mean(item.center_y for item in rows[-1])
            row_height = max(item.height for item in rows[-1])
            if abs(candidate.center_y - center) <= max(tolerance, row_height * 0.55):
                rows[-1].append(candidate)
                rows[-1].sort(key=lambda item: (item.x, item.index))
                continue
        rows.append([candidate])
    return rows


def _row_text(row: list[Candidate]) -> str:
    return " ".join(item.text for item in row)


def _row_indices(row: list[Candidate]) -> list[int]:
    return [item.index for item in row]


def _row_height(row: list[Candidate]) -> float:
    return max(item.height for item in row)


def _row_center(row: list[Candidate]) -> float:
    return statistics.mean(item.center_y for item in row)


def _toc_page(rows: list[list[Candidate]]) -> bool:
    if any(_row_text(row).strip() == "目录" for row in rows[:4]):
        return True
    has_chapter = any(CHAPTER_RE.match(_row_text(row).strip()) for row in rows)
    has_case = any(CASE_RE.match(_row_text(row).strip()) for row in rows)
    if has_chapter and has_case:
        return True
    if rows and has_case and _is_page_reference_row(rows[0]):
        return True
    page_reference_rows = 0
    for row in rows:
        if len(row) >= 2 and row[-1].x >= 0.65 and PAGE_NUMBER_RE.fullmatch(row[-1].text):
            page_reference_rows += 1
        elif TRAILING_PAGE_RE.search(_row_text(row)):
            page_reference_rows += 1
    return page_reference_rows >= 3


def _is_page_reference_row(row: list[Candidate]) -> bool:
    return bool(
        (len(row) >= 2 and row[-1].x >= 0.65 and PAGE_NUMBER_RE.fullmatch(row[-1].text))
        or TRAILING_PAGE_RE.search(_row_text(row))
    )


def _is_page_reference_only(row: list[Candidate]) -> bool:
    return all(PAGE_NUMBER_RE.fullmatch(item.text) for item in row)


def _join_toc_rows(rows: list[list[Candidate]]) -> str:
    """Join OCR-wrapped TOC rows without inventing lexical content.

    Chinese wrapped lines are contiguous.  A row containing only the detached
    right-column page number gets a separating space so the number cannot be
    mistaken for part of the preceding word.
    """
    joined = ""
    for row in rows:
        text = _row_text(row).strip()
        separator = " " if joined and _is_page_reference_only(row) else ""
        joined += separator + text
    return joined


def _heading_level(
    row: list[Candidate],
    *,
    median_height: float,
    max_height: float,
    first_heading_seen: bool,
) -> int | None:
    text = _row_text(row).strip()
    height = _row_height(row)
    if text == "目录":
        return 1
    if CHAPTER_RE.match(text):
        return 2
    # A tall OCR box can still be a wrapped line of prose. A nearly full-width
    # line containing a sentence ending followed by more text is not a title
    # solely because this box is taller than its neighbors.
    if len(row) == 1 and row[0].width >= 0.70 and INTERNAL_SENTENCE_END_RE.search(text):
        return None
    if len(text) <= 40 and height >= max(median_height * 1.55, max_height * 0.82):
        return 1 if not first_heading_seen else 2
    if len(text) <= 50 and height >= median_height * 1.3:
        return 2
    return None


def _block(kind: str, markdown: str, rows: list[list[Candidate]], **extra: Any) -> dict[str, Any]:
    indices = [item.index for row in rows for item in row]
    raw_text = [item.text for row in rows for item in row]
    return {
        "kind": kind,
        "markdown": markdown,
        "source_candidate_indices": indices,
        "source_text": raw_text,
        **extra,
    }


def reconstruct(payload: dict[str, Any], *, page: int) -> dict[str, Any]:
    candidates = parse_candidates(payload)
    rows = group_rows(candidates)
    median_height = statistics.median(item.height for item in candidates)
    max_height = max(item.height for item in candidates)
    toc_page = _toc_page(rows)
    blocks: list[dict[str, Any]] = []
    pending_paragraph: list[list[Candidate]] = []
    first_heading_seen = False

    def flush_paragraph() -> None:
        nonlocal pending_paragraph
        if not pending_paragraph:
            return
        joined = "".join(_row_text(row) for row in pending_paragraph)
        blocks.append(_block("paragraph", joined, pending_paragraph))
        pending_paragraph = []

    starts_with_continuation = bool(
        toc_page
        and rows
        and _row_text(rows[0]).strip() != "目录"
        and not CHAPTER_RE.match(_row_text(rows[0]).strip())
        and not CASE_RE.match(_row_text(rows[0]).strip())
        and _is_page_reference_row(rows[0])
    )
    ends_with_continuation = False
    previous_plain_center: float | None = None
    row_index = 0
    while row_index < len(rows):
        row = rows[row_index]
        text = _row_text(row).strip()

        # A chapter remains a heading even when its page number appears on the
        # same OCR row (for example "第一章…/1").
        heading_level = _heading_level(
            row,
            median_height=median_height,
            max_height=max_height,
            first_heading_seen=first_heading_seen,
        )
        if heading_level is not None:
            flush_paragraph()
            blocks.append(_block("heading", f"{'#' * heading_level} {text}", [row], level=heading_level))
            first_heading_seen = True
            previous_plain_center = None
            row_index += 1
            continue

        if toc_page and CASE_RE.match(text):
            flush_paragraph()
            entry_rows = [row]
            page_reference_detected = _is_page_reference_row(row)
            lookahead = row_index + 1
            while not page_reference_detected and lookahead < len(rows):
                next_row = rows[lookahead]
                next_text = _row_text(next_row).strip()
                if next_text == "目录" or CHAPTER_RE.match(next_text) or CASE_RE.match(next_text):
                    break
                entry_rows.append(next_row)
                page_reference_detected = _is_page_reference_row(next_row)
                lookahead += 1
            entry_text = _join_toc_rows(entry_rows)
            blocks.append(
                _block(
                    "toc-entry",
                    f"- {entry_text}",
                    entry_rows,
                    page_reference_detected=page_reference_detected,
                    wrapped_row_count=len(entry_rows),
                )
            )
            if lookahead == len(rows) and not page_reference_detected:
                ends_with_continuation = True
            previous_plain_center = None
            row_index = lookahead
            continue

        if toc_page and _is_page_reference_row(row):
            flush_paragraph()
            blocks.append(
                _block(
                    "toc-entry",
                    f"- {text}",
                    [row],
                    page_reference_detected=True,
                    wrapped_row_count=1,
                )
            )
            previous_plain_center = None
            row_index += 1
            continue

        if EXPLICIT_LIST_RE.match(text) or CASE_RE.match(text):
            flush_paragraph()
            if re.match(r"^\d{1,4}[.]\s*", text):
                markdown = text
            else:
                markdown = f"- {text}"
            blocks.append(_block("list-item", markdown, [row]))
            previous_plain_center = None
            row_index += 1
            continue

        center = _row_center(row)
        if pending_paragraph:
            previous_text = _row_text(pending_paragraph[-1])
            gap = abs((previous_plain_center or center) - center)
            if STRONG_TERMINAL_RE.search(previous_text) or gap > median_height * PARAGRAPH_GAP_FACTOR:
                flush_paragraph()
        pending_paragraph.append(row)
        previous_plain_center = center
        row_index += 1
    flush_paragraph()

    mapped = [index for block in blocks for index in block["source_candidate_indices"]]
    duplicate_indices = sorted({index for index in mapped if mapped.count(index) > 1})
    unmapped_indices = sorted(set(range(len(candidates))) - set(mapped))
    markdown = "\n\n".join(block["markdown"] for block in blocks).rstrip() + "\n"
    low_confidence = [
        item.index
        for item in candidates
        if item.confidence is None or item.confidence < 0.5
    ]
    header_footer_candidates = [
        item.index for item in candidates if item.y >= 0.94 or item.y + item.height <= 0.06
    ]
    status = "passed"
    diagnostics: list[str] = []
    if unmapped_indices or duplicate_indices or not markdown.strip():
        status = "failed"
        diagnostics.append("candidate accounting or Markdown output is incomplete")
    elif low_confidence:
        status = "needs-review"
        diagnostics.append("low-confidence OCR candidates remain visible and require review")
    if status != "failed" and (starts_with_continuation or ends_with_continuation):
        status = "needs-review"
        diagnostics.append(
            "page starts or ends with a TOC continuation; cross-page reconciliation is required"
        )

    raw_fingerprint_input = json.dumps(
        [{"index": item.index, "text": item.text} for item in candidates],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "contract": CONTRACT,
        "version": VERSION,
        "prompt_version": PROMPT_VERSION,
        "created_at": now(),
        "page": page,
        "status": status,
        "input_candidate_count": len(candidates),
        "mapped_candidate_count": len(mapped),
        "candidate_accounting": {
            "unmapped_indices": unmapped_indices,
            "duplicate_indices": duplicate_indices,
        },
        "raw_text_sha256": sha256_bytes(raw_fingerprint_input),
        "markdown_sha256": sha256_bytes(markdown.encode("utf-8")),
        "markdown": markdown,
        "structure_inventory": {
            "page_kind": "table-of-contents" if toc_page else "text-page",
            "heading_count": sum(block["kind"] == "heading" for block in blocks),
            "paragraph_count": sum(block["kind"] == "paragraph" for block in blocks),
            "list_item_count": sum(block["kind"] == "list-item" for block in blocks),
            "toc_entry_count": sum(block["kind"] == "toc-entry" for block in blocks),
            "starts_with_continuation": starts_with_continuation,
            "ends_with_continuation": ends_with_continuation,
            "cross_page_continuity": (
                "needs-review" if starts_with_continuation or ends_with_continuation else "complete"
            ),
            "header_footer_candidate_indices": header_footer_candidates,
            "low_confidence_candidate_indices": low_confidence,
        },
        "advertising_policy": {
            "mode": "preserve-only",
            "classification_stage": "not-run",
            "applied_action": "preserve",
        },
        "blocks": blocks,
        "diagnostics": diagnostics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-json", type=Path, required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.ocr_json.expanduser().resolve().read_text(encoding="utf-8"))
        result = reconstruct(payload, page=args.page)
        output_md = args.output_md.expanduser().resolve()
        evidence = args.evidence.expanduser().resolve()
        output_md.parent.mkdir(parents=True, exist_ok=True)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(result["markdown"], encoding="utf-8")
        evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, StructureError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "page": args.page, "markdown": str(output_md), "evidence": str(evidence)}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
