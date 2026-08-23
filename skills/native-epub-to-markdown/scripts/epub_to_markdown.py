#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET


CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


@dataclass
class TocEntry:
    title: str
    anchor_id: str
    document_path: str


@dataclass
class DocumentRecord:
    document_path: str
    toc_title: str | None
    markdown_characters: int
    markdown_lines: int
    image_count: int


class ConversionError(RuntimeError):
    pass


class RenderContext:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.assets_dir = output_root / "assets"
        self.asset_map: dict[str, str] = {}
        self.used_asset_names: set[str] = set()

    def register_asset(self, document_path: str, href: str) -> str:
        resolved = resolve_path(document_path, href)
        existing = self.asset_map.get(resolved)
        if existing:
            return existing
        stem = Path(PurePosixPath(resolved).name).stem
        suffix = Path(PurePosixPath(resolved).name).suffix
        candidate = slugify_filename(stem) + suffix.lower()
        if not candidate:
            candidate = "asset" + suffix.lower()
        base = candidate
        counter = 2
        while candidate in self.used_asset_names:
            candidate = f"{Path(base).stem}-{counter}{Path(base).suffix}"
            counter += 1
        self.used_asset_names.add(candidate)
        self.asset_map[resolved] = candidate
        return candidate


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify_filename(value: str) -> str:
    cleaned = re.sub(r"\s+", "-", value.strip())
    cleaned = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "asset"


def collapse_whitespace(value: str) -> str:
    value = value.replace("\r", "").replace("\n", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    return value.strip()


def resolve_path(base_path: str, relative_href: str) -> str:
    if relative_href.startswith("#"):
        return relative_href
    combined = str((PurePosixPath(base_path).parent / relative_href).as_posix())
    return posixpath.normpath(combined)


def container_opf_path(epub: ZipFile) -> PurePosixPath:
    try:
        container_xml = epub.read("META-INF/container.xml")
    except KeyError as exc:
        raise ConversionError("missing META-INF/container.xml") from exc
    root = ET.fromstring(container_xml)
    rootfile = root.find(".//c:rootfile", CONTAINER_NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise ConversionError("missing OPF rootfile in container.xml")
    return PurePosixPath(rootfile.get("full-path", ""))


def opf_metadata(package_root: ET.Element) -> dict[str, object]:
    metadata = package_root.find("opf:metadata", OPF_NS)
    if metadata is None:
        raise ConversionError("missing metadata in OPF")

    def text_list(path: str) -> list[str]:
        return [
            collapse_whitespace(node.text or "")
            for node in metadata.findall(path, OPF_NS)
            if collapse_whitespace(node.text or "")
        ]

    title = collapse_whitespace(
        metadata.findtext("dc:title", default="", namespaces=OPF_NS)
    )
    if not title:
        title = "Untitled EPUB"
    return {
        "title": title,
        "creators": text_list("dc:creator"),
        "publisher": collapse_whitespace(
            metadata.findtext("dc:publisher", default="", namespaces=OPF_NS)
        ),
        "language": collapse_whitespace(
            metadata.findtext("dc:language", default="", namespaces=OPF_NS)
        ),
        "identifiers": text_list("dc:identifier"),
        "modified": collapse_whitespace(
            metadata.findtext(
                "opf:meta[@property='dcterms:modified']",
                default="",
                namespaces=OPF_NS,
            )
        ),
    }


def manifest_items(
    opf_path: PurePosixPath, package_root: ET.Element
) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for item in package_root.findall("opf:manifest/opf:item", OPF_NS):
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or not href:
            continue
        manifest[item_id] = {
            "path": str((opf_path.parent / href).as_posix()),
            "href": href,
            "media_type": item.get("media-type", ""),
            "properties": item.get("properties", ""),
        }
    if not manifest:
        raise ConversionError("empty manifest in OPF")
    return manifest


def spine_documents(
    package_root: ET.Element, manifest: dict[str, dict[str, str]]
) -> list[str]:
    documents: list[str] = []
    for itemref in package_root.findall("opf:spine/opf:itemref", OPF_NS):
        item_id = itemref.get("idref")
        if not item_id or item_id not in manifest:
            continue
        item = manifest[item_id]
        if item["media_type"] == "application/xhtml+xml":
            documents.append(item["path"])
    if not documents:
        raise ConversionError("empty XHTML spine in OPF")
    return documents


def toc_entries(epub: ZipFile, manifest: dict[str, dict[str, str]]) -> list[TocEntry]:
    nav_item = next(
        (item for item in manifest.values() if "nav" in item["properties"].split()),
        None,
    )
    if nav_item is None:
        return []
    root = ET.fromstring(epub.read(nav_item["path"]))
    entries: list[TocEntry] = []
    toc_nav = None
    for nav in root.findall(".//x:nav", XHTML_NS):
        nav_type = nav.get("{http://www.idpf.org/2007/ops}type") or nav.get("type", "")
        if nav_type == "toc":
            toc_nav = nav
            break
    if toc_nav is None:
        toc_nav = root.find(".//x:nav", XHTML_NS)
    if toc_nav is None:
        return []
    counter = 1
    for anchor in toc_nav.findall(".//x:a", XHTML_NS):
        href = anchor.get("href", "")
        title = collapse_whitespace("".join(anchor.itertext()))
        if not href or not title:
            continue
        document_path = resolve_path(nav_item["path"], href.split("#", 1)[0])
        entries.append(
            TocEntry(
                title=title,
                anchor_id=f"toc-{counter:03d}",
                document_path=document_path,
            )
        )
        counter += 1
    return entries


def load_xml(epub: ZipFile, path: str) -> ET.Element:
    return ET.fromstring(epub.read(path))


def inline_text(
    element: ET.Element,
    document_path: str,
    context: RenderContext,
) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        tag = local_name(child)
        if tag == "br":
            parts.append("\n")
        elif tag == "a":
            text = collapse_whitespace(inline_text(child, document_path, context))
            href = normalize_anchor_href(child.get("href", ""))
            if text and href:
                parts.append(f"[{text}]({href})")
            else:
                parts.append(text)
        elif tag == "img":
            asset_name = context.register_asset(document_path, child.get("src", ""))
            parts.append(f"![{Path(asset_name).stem}](assets/{asset_name})")
        elif tag == "image":
            href = child.get(XLINK_HREF) or child.get("href", "")
            asset_name = context.register_asset(document_path, href)
            parts.append(f"![{Path(asset_name).stem}](assets/{asset_name})")
        elif tag == "i":
            text = collapse_whitespace(inline_text(child, document_path, context))
            parts.append(f"*{text}*" if text else "")
        elif tag == "sub":
            text = collapse_whitespace(inline_text(child, document_path, context))
            parts.append(f"<sub>{text}</sub>" if text else "")
        else:
            parts.append(inline_text(child, document_path, context))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def normalize_anchor_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("#"):
        return href
    if "#" in href:
        return "#" + href.split("#", 1)[1]
    return href


def block_images(
    element: ET.Element,
    document_path: str,
    context: RenderContext,
) -> list[str]:
    images: list[str] = []
    for child in element.iter():
        tag = local_name(child)
        href = ""
        if tag == "img":
            href = child.get("src", "")
        elif tag == "image":
            href = child.get(XLINK_HREF) or child.get("href", "")
        if href:
            asset_name = context.register_asset(document_path, href)
            images.append(f"![{Path(asset_name).stem}](assets/{asset_name})")
    return images


def paragraph_as_heading(classes: set[str], text: str) -> str | None:
    if not text:
        return None
    if "font-1em30" in classes and len(text) <= 120:
        return f"## {text}"
    if "font-1em10" in classes and "start-2em" in classes and len(text) <= 120:
        return f"### {text}"
    return None


def render_block(
    element: ET.Element,
    document_path: str,
    context: RenderContext,
) -> list[str]:
    tag = local_name(element)
    if tag in {"body", "div"}:
        blocks: list[str] = []
        for child in element:
            blocks.extend(render_block(child, document_path, context))
        return blocks
    if tag == "svg":
        return block_images(element, document_path, context)
    if tag == "hr":
        return ["---"]
    if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
        text = collapse_whitespace(inline_text(element, document_path, context))
        return [f"{'#' * int(tag[1])} {text}"] if text else []
    if tag != "p":
        text = collapse_whitespace(inline_text(element, document_path, context))
        return [text] if text else []

    classes = set((element.get("class") or "").split())
    anchor_id = element.get("id", "").strip()
    images = block_images(element, document_path, context)
    raw_text = inline_text(element, document_path, context)
    text = collapse_whitespace(raw_text)

    if images and not text:
        blocks = images
    else:
        heading = paragraph_as_heading(classes, text)
        if heading:
            blocks = [heading]
        elif text:
            blocks = [text]
        else:
            blocks = []
            if anchor_id:
                return [f"<a id=\"{anchor_id}\"></a>"]
    if anchor_id:
        return [f"<a id=\"{anchor_id}\"></a>"] + blocks
    return blocks


def local_name(element: ET.Element) -> str:
    return element.tag.split("}", 1)[-1]


def render_document(
    epub: ZipFile,
    document_path: str,
    toc_map: dict[str, TocEntry],
    context: RenderContext,
) -> tuple[list[str], DocumentRecord]:
    root = load_xml(epub, document_path)
    body = root.find("x:body", XHTML_NS)
    if body is None:
        return [], DocumentRecord(document_path, None, 0, 0, 0)

    blocks: list[str] = [f"<!-- source: {document_path} -->"]
    toc_entry = toc_map.get(document_path)
    if toc_entry is not None:
        blocks.append(f"<a id=\"{toc_entry.anchor_id}\"></a>")
        blocks.append(f"## {toc_entry.title}")
    body_blocks = render_block(body, document_path, context)
    blocks.extend(body_blocks)
    cleaned = compact_blocks(blocks)
    joined = "\n\n".join(cleaned).strip()
    record = DocumentRecord(
        document_path=document_path,
        toc_title=toc_entry.title if toc_entry else None,
        markdown_characters=len(joined),
        markdown_lines=len(joined.splitlines()) if joined else 0,
        image_count=sum(1 for item in cleaned if item.startswith("![")),
    )
    return cleaned, record


def compact_blocks(blocks: Iterable[str]) -> list[str]:
    compacted: list[str] = []
    previous_blank = False
    for block in blocks:
        cleaned = block.strip()
        is_blank = not cleaned
        if is_blank and previous_blank:
            continue
        if not is_blank:
            compacted.append(cleaned)
        previous_blank = is_blank
    return compacted


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_output_root(output_root: Path, overwrite: bool) -> None:
    if output_root.exists() and not overwrite:
        raise ConversionError(f"output root already exists: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    for name in ("assets", "evidence", "reports"):
        target = output_root / name
        if target.exists() and overwrite:
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "index.md"
    if index_path.exists() and overwrite:
        index_path.unlink()


def copy_assets(epub: ZipFile, context: RenderContext) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for source_path, asset_name in sorted(context.asset_map.items()):
        destination = context.assets_dir / asset_name
        destination.write_bytes(epub.read(source_path))
        copied.append({"source_path": source_path, "asset_name": asset_name})
    return copied


def convert_epub(source: Path, output_root: Path, overwrite: bool) -> dict[str, object]:
    prepare_output_root(output_root, overwrite)
    source_sha256 = sha256_file(source)

    try:
        epub = ZipFile(source)
    except BadZipFile as exc:
        raise ConversionError(f"invalid EPUB zip: {source}") from exc

    with epub:
        opf_path = container_opf_path(epub)
        package_root = load_xml(epub, str(opf_path))
        metadata = opf_metadata(package_root)
        manifest = manifest_items(opf_path, package_root)
        spine = spine_documents(package_root, manifest)
        toc = toc_entries(epub, manifest)
        toc_map = {entry.document_path: entry for entry in toc}

        context = RenderContext(output_root)
        records: list[DocumentRecord] = []
        all_blocks: list[str] = [
            f"# {metadata['title']}",
            f"- source: `{source}`",
            f"- sha256: `{source_sha256}`",
        ]
        if metadata["creators"]:
            all_blocks.append(
                f"- creators: {', '.join(str(item) for item in metadata['creators'])}"
            )
        if metadata["publisher"]:
            all_blocks.append(f"- publisher: {metadata['publisher']}")
        if metadata["language"]:
            all_blocks.append(f"- language: {metadata['language']}")
        if metadata["identifiers"]:
            all_blocks.append(
                f"- identifiers: {', '.join(str(item) for item in metadata['identifiers'])}"
            )
        if toc:
            all_blocks.append("## 目录")
            all_blocks.extend(
                f"- [{entry.title}](#{entry.anchor_id})" for entry in toc
            )

        for document_path in spine:
            blocks, record = render_document(epub, document_path, toc_map, context)
            if blocks:
                all_blocks.extend(["---", *blocks])
            records.append(record)

        assets = copy_assets(epub, context)

    markdown = "\n\n".join(compact_blocks(all_blocks)).strip() + "\n"
    (output_root / "index.md").write_text(markdown, encoding="utf-8")
    write_json(
        output_root / "evidence" / "source-summary.json",
        {
            "source": str(source),
            "source_sha256": source_sha256,
            "opf_path": str(opf_path),
            "generated_at": now_iso(),
            "metadata": metadata,
            "spine_document_count": len(spine),
            "toc_entry_count": len(toc),
            "asset_count": len(assets),
        },
    )
    write_json(
        output_root / "evidence" / "spine-documents.json",
        [
            {
                "document_path": record.document_path,
                "toc_title": record.toc_title,
                "markdown_characters": record.markdown_characters,
                "markdown_lines": record.markdown_lines,
                "image_count": record.image_count,
            }
            for record in records
        ],
    )
    write_json(output_root / "evidence" / "copied-assets.json", assets)
    write_json(
        output_root / "reports" / "conversion-summary.json",
        {
            "status": "passed",
            "generated_at": now_iso(),
            "package_root": str(output_root),
            "index_md": str(output_root / "index.md"),
            "asset_count": len(assets),
            "spine_document_count": len(spine),
            "toc_entry_count": len(toc),
        },
    )
    return {
        "status": "passed",
        "output_root": str(output_root),
        "index_md": str(output_root / "index.md"),
        "asset_count": len(assets),
        "spine_document_count": len(spine),
        "toc_entry_count": len(toc),
        "source_sha256": source_sha256,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a readable EPUB package into Markdown and assets"
    )
    parser.add_argument("--source", required=True, help="Absolute path to the source EPUB")
    parser.add_argument(
        "--output-root",
        required=True,
        help="Directory that will receive index.md, assets/, evidence/ and reports/",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing index.md, assets/, evidence/ and reports/ under output-root",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    if source.suffix.lower() != ".epub":
        parser.error("source must be a .epub file")

    try:
        result = convert_epub(source, output_root, overwrite=args.overwrite)
    except ConversionError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
