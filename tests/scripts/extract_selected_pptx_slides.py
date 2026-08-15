#!/usr/bin/env python3
"""Create a small PPTX fixture by retaining selected slides from a source deck.

This is a test-fixture utility, not the production PPTX parser. It keeps the
original OOXML structure for the selected slides and leaves shared masters,
layouts, themes, and media available to those slides.
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
ET.register_namespace("p", P_NS)
ET.register_namespace("r", R_NS)


def rel_targets(data: bytes) -> dict[str, str]:
    root = ET.fromstring(data)
    return {
        item.attrib["Id"]: item.attrib["Target"]
        for item in root.findall(f"{{{REL_NS}}}Relationship")
    }


def slide_number(target: str) -> int | None:
    match = re.fullmatch(r"slides/slide(\d+)\.xml", target)
    return int(match.group(1)) if match else None


def update_presentation(data: bytes, kept_numbers: set[int], relationships: dict[str, str]) -> bytes:
    root = ET.fromstring(data)
    slide_list = root.find(f"{{{P_NS}}}sldIdLst")
    if slide_list is None:
        raise ValueError("presentation.xml has no p:sldIdLst")

    kept = []
    for slide_id in list(slide_list):
        rel_id = slide_id.attrib.get(f"{{{R_NS}}}id")
        target = relationships.get(rel_id or "")
        if target and slide_number(target) in kept_numbers:
            kept.append(slide_id)
    if len(kept) != len(kept_numbers):
        raise ValueError(f"expected {len(kept_numbers)} selected slides, found {len(kept)}")

    slide_list[:] = kept
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def update_app(data: bytes, count: int) -> bytes:
    """Keep the fixture metadata honest when the source has an app.xml slide count."""
    root = ET.fromstring(data)
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] == "Slides":
            item.text = str(count)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build(source: Path, output: Path, kept_numbers: set[int]) -> None:
    with zipfile.ZipFile(source, "r") as src:
        entries = {info.filename: src.read(info.filename) for info in src.infolist()}

    rel_path = "ppt/_rels/presentation.xml.rels"
    relationships = rel_targets(entries[rel_path])
    entries["ppt/presentation.xml"] = update_presentation(
        entries["ppt/presentation.xml"], kept_numbers, relationships
    )
    if "docProps/app.xml" in entries:
        entries["docProps/app.xml"] = update_app(entries["docProps/app.xml"], len(kept_numbers))

    # Remove slide and notes parts that cannot be reached from the retained slides.
    kept_slide_names = {f"ppt/slides/slide{n}.xml" for n in kept_numbers}
    kept_rel_names = {f"ppt/slides/_rels/slide{n}.xml.rels" for n in kept_numbers}
    kept_note_names = {f"ppt/notesSlides/notesSlide{n}.xml" for n in kept_numbers}
    kept_note_rel_names = {f"ppt/notesSlides/_rels/notesSlide{n}.xml.rels" for n in kept_numbers}
    for name in list(entries):
        if name.startswith("ppt/slides/slide") and name.endswith(".xml") and name not in kept_slide_names:
            del entries[name]
        elif name.startswith("ppt/slides/_rels/slide") and name.endswith(".xml.rels") and name not in kept_rel_names:
            del entries[name]
        elif name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml") and name not in kept_note_names:
            del entries[name]
        elif name.startswith("ppt/notesSlides/_rels/notesSlide") and name.endswith(".xml.rels") and name not in kept_note_rel_names:
            del entries[name]

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for name, data in entries.items():
            dst.writestr(name, data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--slides", nargs="+", type=int, required=True)
    args = parser.parse_args()
    if len(set(args.slides)) != len(args.slides):
        parser.error("--slides must not contain duplicates")
    build(args.source, args.output, set(args.slides))


if __name__ == "__main__":
    main()
