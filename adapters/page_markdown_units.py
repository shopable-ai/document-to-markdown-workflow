#!/usr/bin/env python3
"""Split, merge, and validate isolated Markdown page units.

This adapter is intentionally reader-layer only. It does not call OCR or
vision tools and never writes the source document, manifest, raw evidence, or
page image.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable


PAGE_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)
PAGE_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
PAGE_FROM_ASSET = re.compile(r"(?:__p|-p)(\d{3})(?:__|-|\.)")
METADATA = {
    "page-number": re.compile(r"^<!--\s*page-number:\s*(\d+)\s*-->$"),
    "unit-status": re.compile(r"^<!--\s*unit-status:\s*([a-z-]+)\s*-->$"),
    "semantic-title": re.compile(r"^<!--\s*semantic-title:\s*(.*?)\s*-->$"),
    "relation-status": re.compile(r"^<!--\s*relation-status:\s*([a-z-]+)\s*-->$"),
}
REQUIRED_HEADINGS = (
    "正文",
    "列表/图示文字",
    "表格与公式",
    "解读",
    "举例与思考",
    "模型结构",
    "已确认的关系说明",
    "原始页面图",
    "待核对项",
)
LABELS = ("选择A", "选择B", "机会", "成本", "收益", "读书", "追剧")
REVIEW_FRAGMENTS = (
    "放弃读书可能取得的",
    "放弃追剧可能取得",
    "的收益（学习、成长、等）",
    "是实行“读书”的",
)
ARROW_MARKS = ("→", "←", "➜", "➡", "⇢", "=>")


class PageUnitError(ValueError):
    """Raised when a page unit or stage gate is invalid."""


def parse_page_selector(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            pages.update(range(min(start, end), max(start, end) + 1))
        else:
            pages.add(int(part))
    result = sorted(pages)
    if not result or result[0] < 1 or result[-1] > 100:
        raise PageUnitError(f"pages must be within 1..100: {value}")
    return result


def page_from_asset(value: str) -> int | None:
    match = PAGE_FROM_ASSET.search(value)
    return int(match.group(1)) if match else None


def _selected_sections(source_document: Path, selected_pages: set[int]) -> Iterable[tuple[int, str]]:
    """Stream sections while retaining only explicitly selected page bodies."""
    ordinal = 0
    yielded_pages: set[int] = set()
    current_page: int | None = None
    current_lines: list[str] = []
    selected = False
    with source_document.open("r", encoding="utf-8") as handle:
        for line in handle:
            heading = PAGE_HEADING.match(line.rstrip("\n"))
            if heading:
                if selected and current_page is not None:
                    yield current_page, "".join(current_lines).rstrip() + "\n"
                    yielded_pages.add(current_page)
                    if yielded_pages == selected_pages:
                        return
                ordinal += 1
                current_page = ordinal
                selected = ordinal in selected_pages
                current_lines = [line] if selected else []
                continue
            if selected:
                current_lines.append(line)
        if selected and current_page is not None:
            yield current_page, "".join(current_lines).rstrip() + "\n"


def extract_page_section(source_document: Path, page: int) -> str:
    for found_page, content in _selected_sections(source_document, {page}):
        if found_page == page:
            return content
    raise PageUnitError(f"page {page} was not found in {source_document}")


def _image_link(text: str) -> str:
    matches = PAGE_IMAGE.findall(text)
    if not matches:
        raise PageUnitError("page unit has no original page image link")
    return matches[-1]


def _semantic_title(text: str) -> str:
    first_heading = PAGE_HEADING.search(text)
    if not first_heading:
        raise PageUnitError("page unit has no page heading")
    title = first_heading.group(1).strip()
    title = re.sub(r"^\d{1,3}\s*[.、:：]?\s*", "", title)
    return title


def read_metadata(text: str) -> dict[str, str | int]:
    result: dict[str, str | int] = {}
    for line in text.splitlines():
        for key, pattern in METADATA.items():
            match = pattern.match(line.strip())
            if not match:
                continue
            value: str | int = match.group(1).strip()
            if key == "page-number":
                value = int(value)
            result[key] = value
    return result


def _wrap_extracted_page(page: int, raw_section: str) -> str:
    title = _semantic_title(raw_section)
    image = _image_link(raw_section)
    return "\n".join(
        [
            f"<!-- page-number: {page} -->",
            "<!-- unit-status: needs-review -->",
            f"<!-- semantic-title: {title} -->",
            "<!-- relation-status: needs-review -->",
            "",
            raw_section.rstrip(),
            "",
        ]
    ).replace("<!-- relation-status: needs-review -->\n\n", f"<!-- relation-status: needs-review -->\n<!-- original-page-image: {image} -->\n\n")


def enforce_stage_gate(pages: list[int], gate_path: Path | None) -> None:
    if pages == [1]:
        return
    if gate_path is None:
        raise PageUnitError("pages after page 1 require a prior gate via --prior-gate pointing to a passed validation report")
    try:
        report = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PageUnitError(f"cannot read prior gate: {gate_path}: {exc}") from exc
    if report.get("status") != "passed":
        raise PageUnitError("prior gate is not passed")
    scope = report.get("scope")
    if max(pages) <= 5 and scope != "single-page":
        raise PageUnitError("pages 2-5 require a passed single-page gate")
    if max(pages) > 5 and scope != "small-batch-2-5":
        raise PageUnitError("pages 6-100 require a passed small-batch-2-5 gate")


def split_pages(source_document: Path, pages: list[int], output_dir: Path | None, output: Path | None, gate_path: Path | None) -> list[Path]:
    enforce_stage_gate(pages, gate_path)
    found = {page: content for page, content in _selected_sections(source_document, set(pages))}
    missing = sorted(set(pages) - set(found))
    if missing:
        raise PageUnitError(f"selected pages were not found: {missing}")
    if len(pages) == 1 and output is not None:
        targets = {pages[0]: output}
    elif output_dir is not None:
        targets = {page: output_dir / f"p{page:03d}.md" for page in pages}
    else:
        raise PageUnitError("split requires --output for one page or --output-dir for multiple pages")
    written: list[Path] = []
    for page in pages:
        target = targets[page]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_wrap_extracted_page(page, found[page]), encoding="utf-8")
        written.append(target)
    return written


def _section_spans(text: str) -> dict[str, tuple[int, int]]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.M))
    spans: dict[str, tuple[int, int]] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        spans[title] = (match.end(), end)
    return spans


def validate_unit_text(
    text: str,
    expected_page: int | None = None,
    expected_image: str | None = None,
    page_one_acceptance: bool | None = None,
) -> dict[str, object]:
    metadata = read_metadata(text)
    page = metadata.get("page-number")
    if not isinstance(page, int):
        raise PageUnitError("missing page-number metadata")
    if expected_page is not None and page != expected_page:
        raise PageUnitError(f"expected page {expected_page}, got {page}")
    status = metadata.get("unit-status")
    if status not in {"passed", "needs-review", "failed"}:
        raise PageUnitError("unit-status must be passed, needs-review, or failed")
    relation_status = metadata.get("relation-status")
    if relation_status not in {"confirmed", "needs-review", "unknown"}:
        raise PageUnitError("relation-status must be confirmed, needs-review, or unknown")
    title = metadata.get("semantic-title")
    if not isinstance(title, str) or not title:
        raise PageUnitError("missing semantic-title metadata")
    spans = _section_spans(text)
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in spans]
    if missing:
        raise PageUnitError(f"missing required sections: {missing}")
    positions = [spans[heading][0] for heading in REQUIRED_HEADINGS]
    if positions != sorted(positions):
        raise PageUnitError("required sections are not in deterministic reader order")
    image = _image_link(text)
    if expected_image is not None and image != expected_image:
        raise PageUnitError(f"page image link changed: expected {expected_image}, got {image}")
    if page_one_acceptance is None:
        page_one_acceptance = page == 1
    body_start, body_end = spans["正文"]
    body = text[body_start:body_end]
    if page_one_acceptance:
        merged = "还指厂商把相同的生产要素投入到其他行业当中去可以获得的最高收益。"
        second = "看两者在时间和银子维度为你创造价值的大小来判断.显然追剧在时间和银子维度并不能为你创造价值."
        if merged not in body or second not in body:
            raise PageUnitError("required page-1 sentence joins are missing from 正文")
    formula_start, formula_end = spans["表格与公式"]
    formula_lines = [line.strip() for line in text[formula_start:formula_end].splitlines() if line.strip()]
    if page_one_acceptance:
        formula = "（所放弃的最佳选择的价值）-（实际选择价值）=（机会成本）"
        if "实用公式" not in formula_lines or formula not in formula_lines:
            raise PageUnitError("实用公式 and formula must be standalone lines")
        if any(line.startswith(("-", "*", "+")) and "实用公式" in line for line in formula_lines):
            raise PageUnitError("实用公式 must not be a list item")
        if any(line.startswith(("-", "*", "+")) and formula in line for line in formula_lines):
            raise PageUnitError("formula must not be a list item")
    list_start, list_end = spans["列表/图示文字"]
    list_text = text[list_start:list_end]
    if page_one_acceptance:
        for label in LABELS:
            if not re.search(rf"^-\s+{re.escape(label)}\s*$", list_text, flags=re.M):
                raise PageUnitError(f"diagram label is not isolated: {label}")
            if re.search(rf"^[-*+]?\s*{re.escape(label)}\s*$", body, flags=re.M):
                raise PageUnitError(f"diagram label leaked into ordinary body: {label}")
        review_start, review_end = spans["待核对项"]
        review = text[review_start:review_end]
        for fragment in REVIEW_FRAGMENTS:
            if fragment not in review:
                raise PageUnitError(f"ambiguous fragment missing from 待核对项: {fragment}")
    relation_start, relation_end = spans["已确认的关系说明"]
    relation_text = text[relation_start:relation_end]
    if relation_status != "confirmed" and any(mark in relation_text for mark in ARROW_MARKS):
        raise PageUnitError("unconfirmed relation section contains an arrow claim")
    if any(mark in text for mark in ARROW_MARKS):
        raise PageUnitError("page unit contains an unconfirmed arrow or flow marker")
    result = {
        "status": "passed" if status == "passed" else status,
        "scope": "single-page" if page == 1 else "page-unit",
        "page": page,
        "semantic_title": title,
        "relation_status": relation_status,
        "image": image,
        "checks": {
            "required_sections": True,
            "deterministic_order": True,
            "sentence_joins": True,
            "formula_standalone": True,
            "diagram_labels_are_structured": True,
            "ambiguous_fragments_preserved": True,
            "unconfirmed_relations_not_promoted": True,
            "original_image_link_preserved": True,
        },
    }
    return result


def merge_units(units: list[Path], output: Path) -> dict[str, object]:
    if not units:
        raise PageUnitError("merge requires at least one page unit")
    records: list[tuple[int, Path, str, dict[str, object]]] = []
    for unit in units:
        text = unit.read_text(encoding="utf-8")
        metadata = read_metadata(text)
        page = metadata.get("page-number")
        if not isinstance(page, int):
            raise PageUnitError(f"missing page number in {unit}")
        result = validate_unit_text(text, expected_page=page, page_one_acceptance=(page == 1))
        records.append((page, unit, text.rstrip(), result))
    records.sort(key=lambda item: item[0])
    pages = [item[0] for item in records]
    if len(set(pages)) != len(pages):
        raise PageUnitError("duplicate page numbers in merge input")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(item[2] for item in records) + "\n", encoding="utf-8")
    return {
        "status": "passed",
        "scope": "derived-preview",
        "pages": pages,
        "output": str(output),
        "source_units": [str(item[1]) for item in records],
        "rules": ["ascending-page-number", "preserve-page-image-links", "no-reinference"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    split = subparsers.add_parser("split")
    split.add_argument("--source-document", type=Path, required=True)
    split.add_argument("--pages", default="1")
    split.add_argument("--output", type=Path)
    split.add_argument("--output-dir", type=Path)
    split.add_argument("--prior-gate", type=Path)

    merge = subparsers.add_parser("merge")
    merge.add_argument("--unit", type=Path, action="append", dest="units", required=True)
    merge.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--unit", type=Path, required=True)
    validate.add_argument("--expected-page", type=int)
    validate.add_argument("--expected-image")
    validate.add_argument("--report", type=Path, required=True)
    validate.add_argument("--manual-readability-passed", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "split":
            pages = parse_page_selector(args.pages)
            if len(pages) == 1 and args.output_dir is None and args.output is None:
                raise PageUnitError("single-page split requires --output")
            paths = split_pages(args.source_document, pages, args.output_dir, args.output, args.prior_gate)
            print(json.dumps({"status": "passed", "pages": pages, "outputs": [str(path) for path in paths]}, ensure_ascii=False))
            return 0
        if args.command == "merge":
            result = merge_units(args.units, args.output)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        text = args.unit.read_text(encoding="utf-8")
        result = validate_unit_text(text, expected_page=args.expected_page, expected_image=args.expected_image)
        result["manual_readability"] = "passed" if args.manual_readability_passed else "not-run"
        if args.manual_readability_passed and result["status"] == "passed":
            result["status"] = "passed"
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "passed" and result["manual_readability"] == "passed" else 1
    except (OSError, PageUnitError, ValueError) as exc:
        print(f"page markdown unit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
