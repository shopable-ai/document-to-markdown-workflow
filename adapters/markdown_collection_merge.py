#!/usr/bin/env python3
"""Build a reader-facing collection from page-unit Markdown and page renders."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Iterable


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*(?:<([^>]+)>|([^\s)]+))\s*\)")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$", re.MULTILINE)
LEADING_NUMBER_RE = re.compile(r"^\s*(\d{1,3})(?:[.、:：]|\s+)")
TRAILING_COUNT_RE = re.compile(r"\s+\d{1,3}\s*/\s*100\s*$")
HOST_METADATA = {".shopme", ".docsignore"}


class CollectionMergeError(RuntimeError):
    """Raised when collection inputs or output invariants are unsafe."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def page_number(path: Path) -> int:
    match = re.fullmatch(r"p(\d{3})\.md", path.name)
    if not match:
        raise CollectionMergeError(f"invalid page-unit filename: {path.name}")
    return int(match.group(1))


def selected_pages(selector: str, available: Iterable[int]) -> list[int]:
    available_set = set(available)
    if selector == "all":
        pages = sorted(available_set)
    else:
        pages = set()
        for item in selector.split(","):
            item = item.strip()
            if not item:
                continue
            if "-" in item:
                start_text, end_text = item.split("-", 1)
                try:
                    start, end = int(start_text), int(end_text)
                except ValueError as exc:
                    raise CollectionMergeError(f"invalid page selector: {item}") from exc
                if start > end:
                    raise CollectionMergeError(f"invalid page range: {item}")
                pages.update(range(start, end + 1))
            else:
                try:
                    pages.add(int(item))
                except ValueError as exc:
                    raise CollectionMergeError(f"invalid page selector: {item}") from exc
        pages = sorted(pages)

    missing = [page for page in pages if page not in available_set]
    if missing:
        raise CollectionMergeError(f"selected page-units are missing: {missing}")
    if not pages:
        raise CollectionMergeError("no page-units selected")
    return pages


def clean_title(raw_title: str, page: int) -> str:
    title = raw_title.replace("**", "").strip()
    title = TRAILING_COUNT_RE.sub("", title).strip()
    number_match = LEADING_NUMBER_RE.match(title)
    if number_match and int(number_match.group(1)) == page:
        title = title[number_match.end() :].strip()
    title = re.sub(r"\s+", " ", title)
    return title or f"第 {page} 页"


def extract_title(text: str, page: int) -> str:
    match = HEADING_RE.search(text)
    if not match:
        return f"第 {page} 页"
    return clean_title(match.group(1), page)


def load_category_map(path: Path, pages: list[int]) -> tuple[list[str], dict[int, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionMergeError(f"cannot read category map: {path}") from exc

    categories = payload.get("categories")
    assignments = payload.get("assignments")
    if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
        raise CollectionMergeError("category map must contain a string list named categories")
    if not isinstance(assignments, dict):
        raise CollectionMergeError("category map must contain an object named assignments")

    category_by_page: dict[int, str] = {}
    for page in pages:
        value = assignments.get(f"{page:03d}", assignments.get(str(page)))
        if not isinstance(value, str) or value not in categories:
            raise CollectionMergeError(f"missing or invalid category assignment for page {page:03d}")
        category_by_page[page] = value
    assigned_pages = {
        int(key)
        for key in assignments
        if str(key).isdigit() and int(key) in pages
    }
    if assigned_pages != set(pages):
        raise CollectionMergeError("category map must assign every selected page exactly once")
    return categories, category_by_page


def resolve_image_path(unit_path: Path, source_assets: Path, raw_reference: str) -> Path:
    reference = Path(raw_reference)
    if reference.is_absolute() or raw_reference.startswith(("http:", "https:", "file:", "data:")):
        raise CollectionMergeError(f"unsupported image reference in {unit_path.name}: {raw_reference}")
    resolved = (unit_path.parent / reference).resolve()
    assets_root = source_assets.resolve()
    try:
        resolved.relative_to(assets_root)
    except ValueError as exc:
        raise CollectionMergeError(
            f"image reference escapes source assets in {unit_path.name}: {raw_reference}"
        ) from exc
    if not resolved.is_file():
        raise CollectionMergeError(f"missing image referenced by {unit_path.name}: {raw_reference}")
    return resolved


def rewrite_images(
    unit_path: Path,
    text: str,
    source_assets: Path,
    destination_assets: Path,
    copied_assets: dict[str, str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        raw_reference = match.group(2) or match.group(3)
        assert raw_reference is not None
        source_image = resolve_image_path(unit_path, source_assets, raw_reference)
        target_name = source_image.name
        target_path = destination_assets / target_name
        if target_name in copied_assets:
            existing_source = copied_assets[target_name]
            if sha256(source_image) != existing_source:
                raise CollectionMergeError(f"conflicting asset basename: {target_name}")
        else:
            copied_assets[target_name] = sha256(source_image)
            if target_path.exists() and sha256(target_path) != copied_assets[target_name]:
                raise CollectionMergeError(f"destination asset differs: {target_path}")
            if not target_path.exists():
                shutil.copy2(source_image, target_path)
        return f"![{alt}](assets/{target_name})"

    return IMAGE_RE.sub(replace, text)


def write_if_same_or_new(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise CollectionMergeError(f"destination file differs: {path}")
        return
    path.write_bytes(encoded)


def validate_destination_shape(destination: Path) -> None:
    if not destination.exists():
        return
    legacy_units = destination / "page-units"
    current_units = destination / "单页内容"
    if legacy_units.exists() and not current_units.exists():
        raise CollectionMergeError(
            "legacy collection layout detected: page-units/ belongs to the previous "
            "single-index layout; regenerate the collection from source page-units/ and assets/"
        )
    unexpected = [
        child
        for child in destination.iterdir()
        if child.name not in {"index.md", "total.md", "单页内容", "assets"} | HOST_METADATA
    ]
    if unexpected:
        names = ", ".join(child.name for child in unexpected)
        raise CollectionMergeError(f"destination contains unexpected entries: {names}")


def render_index(
    title: str,
    pages: list[int],
    titles: dict[int, str],
    categories: list[str],
    category_by_page: dict[int, str],
) -> str:
    index_title = f"{title}｜索引"
    lines = [
        "---",
        f"title: {index_title}",
        "document_type: collection_index",
        f"model_count: {len(pages)}",
        "---",
        "",
        f"# {index_title}",
        "",
        "> 本文件是本次真实转换结果的阅读入口；完整合并正文位于 [`total.md`](total.md)。",
        "",
        "## 使用说明",
        "",
        "- `index.md` 是索引文件，负责导航和分类，不重复保存模型正文。",
        f"- `total.md` 是合并全文，按原始页面顺序保存 {len(pages)} 个页面单元及其页面图链接。",
        "- 分类是索引视图，每个模型只进入一个主分类。",
        "",
        "## 内容边界",
        "",
        "- 页面正文中的 `Unknown`、`无法确认` 和其他待核对内容保持原样。",
        "",
        "## 按主题分类",
        "",
    ]
    for category in categories:
        category_pages = [page for page in pages if category_by_page[page] == category]
        if not category_pages:
            continue
        lines.extend([f"### {category}", ""])
        for page in category_pages:
            lines.append(
                f"- [{page:02d} {titles[page]}](total.md#model-{page:03d})"
            )
        lines.append("")

    lines.extend(["## 按原始顺序", ""])
    for page in pages:
        lines.append(
            f"- [{page:02d} {titles[page]}](total.md#model-{page:03d})"
            f" · [单页内容](单页内容/p{page:03d}.md)"
        )
    lines.append("")
    return "\n".join(lines)


def render_total(
    title: str,
    pages: list[int],
    units: dict[int, str],
) -> str:
    total_title = f"{title}｜合并全文"
    sections = [
        "---",
        f"title: {total_title}",
        "document_type: merged_reader_document",
        f"model_count: {len(pages)}",
        "---",
        "",
        f"# {total_title}",
        "",
    ]
    for index, page in enumerate(pages):
        if index:
            sections.extend(["", "---", ""])
        sections.extend([f'<a id="model-{page:03d}"></a>', ""])
        sections.append(units[page].rstrip())
    sections.append("")
    return "\n".join(sections)


def merge_collection(
    *,
    source_page_units: Path,
    source_assets: Path,
    destination_root: Path,
    destination_dir_name: str,
    pages_selector: str,
    category_map: Path,
    title: str,
) -> dict[str, object]:
    if not source_page_units.is_dir():
        raise CollectionMergeError(f"source page-units directory not found: {source_page_units}")
    if not source_assets.is_dir():
        raise CollectionMergeError(f"source assets directory not found: {source_assets}")
    if not destination_root.is_dir():
        raise CollectionMergeError(f"destination root not found: {destination_root}")

    available_paths = sorted(source_page_units.glob("p*.md"))
    available = [page_number(path) for path in available_paths]
    pages = selected_pages(pages_selector, available)
    categories, category_by_page = load_category_map(category_map, pages)

    destination = destination_root / destination_dir_name
    validate_destination_shape(destination)
    destination.mkdir(parents=True, exist_ok=True)
    destination_assets = destination / "assets"
    destination_assets.mkdir(exist_ok=True)
    destination_units = destination / "单页内容"
    destination_units.mkdir(exist_ok=True)

    units: dict[int, str] = {}
    titles: dict[int, str] = {}
    copied_assets: dict[str, str] = {}
    page_paths = {page_number(path): path for path in available_paths}
    for page in pages:
        unit_path = page_paths[page]
        original = unit_path.read_text(encoding="utf-8")
        target_unit = destination_units / unit_path.name
        write_if_same_or_new(target_unit, original)
        titles[page] = extract_title(original, page)
        units[page] = rewrite_images(
            unit_path,
            original,
            source_assets,
            destination_assets,
            copied_assets,
        )

    total_text = render_total(title, pages, units)
    index_text = render_index(
        title,
        pages,
        titles,
        categories,
        category_by_page,
    )
    write_if_same_or_new(destination / "total.md", total_text)
    write_if_same_or_new(destination / "index.md", index_text)

    expected_assets = sorted(copied_assets)
    actual_assets = sorted(
        path.name for path in destination_assets.iterdir() if path.is_file()
    )
    unexpected_assets = sorted(set(actual_assets) - set(expected_assets))
    if unexpected_assets:
        raise CollectionMergeError(
            "destination assets contains unexpected files: " + ", ".join(unexpected_assets)
        )
    expected_units = {f"p{page:03d}.md" for page in pages}
    actual_units = {
        path.name for path in destination_units.iterdir() if path.is_file()
    }
    unexpected_units = sorted(actual_units - expected_units)
    if unexpected_units:
        raise CollectionMergeError(
            "destination 单页内容 contains unexpected files: "
            + ", ".join(unexpected_units)
        )

    return {
        "destination": str(destination),
        "index": str(destination / "index.md"),
        "total": str(destination / "total.md"),
        "single_page_content": str(destination_units),
        "page_units": str(destination_units),
        "pages": len(pages),
        "assets": len(expected_assets),
        "asset_names": expected_assets,
        "categories": categories,
        "status": "merged",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-page-units", type=Path, required=True)
    parser.add_argument("--source-assets", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--destination-dir-name", required=True)
    parser.add_argument("--category-map", type=Path, required=True)
    parser.add_argument("--pages", default="all")
    parser.add_argument("--title", default="查理芒格的100个思维模型")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = merge_collection(
            source_page_units=args.source_page_units,
            source_assets=args.source_assets,
            destination_root=args.destination_root,
            destination_dir_name=args.destination_dir_name,
            pages_selector=args.pages,
            category_map=args.category_map,
            title=args.title,
        )
    except CollectionMergeError as exc:
        print(f"failed: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
