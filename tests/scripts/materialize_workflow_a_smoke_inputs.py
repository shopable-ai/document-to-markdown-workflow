#!/usr/bin/env python3
"""Materialize source-independent Workflow A smoke inputs.

The generated files are test inputs only.  This module never invokes the
production runner and never creates a Markdown/business output package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import shutil
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _presentation_slide_parts(source: Path) -> list[str]:
    """Return slide part names in source presentation order."""
    with zipfile.ZipFile(source) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))

    rel_targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall(f"{{{REL_NS}}}Relationship")
    }
    slide_list = presentation.find(f"{{{P_NS}}}sldIdLst")
    if slide_list is None:
        raise ValueError(f"{source} has no presentation slide list")

    parts: list[str] = []
    for slide_id in slide_list:
        rel_id = slide_id.attrib.get(f"{{{R_NS}}}id")
        target = rel_targets.get(rel_id or "")
        if not target:
            continue
        parts.append(posixpath.normpath(posixpath.join("ppt", target)))
    if not parts:
        raise ValueError(f"{source} has no readable slide relationships")
    return parts


def _slide_number(part_name: str) -> int:
    stem = Path(part_name).stem
    if not stem.startswith("slide") or not stem[5:].isdigit():
        raise ValueError(f"unsupported slide part name: {part_name}")
    return int(stem[5:])


def _pdf_page_count(source: Path) -> int:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency-light hosts
        raise RuntimeError("pypdf is required to materialize PDF smoke inputs") from exc
    return len(PdfReader(str(source)).pages)


def _discover_source_pair() -> tuple[Path, Path]:
    """Find a fixture-local matching PPTX/PDF pair without semantic names."""
    candidates: list[tuple[Path, Path]] = []
    for pptx in ROOT.glob("tests/fixtures/**/*.pptx"):
        pdf = pptx.with_suffix(".pdf")
        if pdf.is_file():
            candidates.append((pptx, pdf))
    if not candidates:
        raise FileNotFoundError(
            "no fixture-local PPTX/PDF pair found; pass --pptx-source and --pdf-source"
        )
    # Prefer a selected/minimal pair, then the shortest stable path.  This is
    # a discovery rule, not a source-title or source-page rule.
    candidates.sort(
        key=lambda pair: (
            "selected" not in pair[0].parts,
            len(pair[0].parts),
            str(pair[0]),
        )
    )
    return candidates[0]


def _resolve_sources(pptx_source: Path | None, pdf_source: Path | None) -> tuple[Path, Path]:
    if (pptx_source is None) != (pdf_source is None):
        raise ValueError("--pptx-source and --pdf-source must be supplied together")
    if pptx_source is None:
        pair = _discover_source_pair()
    else:
        pair = (pptx_source.expanduser().resolve(), pdf_source.expanduser().resolve())
    for source in pair:
        if not source.is_file():
            raise FileNotFoundError(f"source does not exist: {source}")
    return pair


def select_pptx(source: Path, output: Path, positions: list[int]) -> list[str]:
    """Retain ordinal slide positions from the current PPTX source."""
    parts = _presentation_slide_parts(source)
    if not positions or max(positions) > len(parts):
        raise ValueError(f"source PPTX has {len(parts)} slides; requested {positions}")

    # Reuse the fixture-only OOXML selector while supplying dynamically
    # discovered slide part numbers.
    sys.path.insert(0, str(SCRIPT_DIR))
    from extract_selected_pptx_slides import build

    selected_parts = [parts[position - 1] for position in positions]
    build(source, output, {_slide_number(part) for part in selected_parts})
    return selected_parts


def select_pdf(source: Path, output: Path, positions: list[int]) -> None:
    """Retain ordinal PDF pages from the current PDF source."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency-light hosts
        raise RuntimeError("pypdf is required to materialize PDF smoke inputs") from exc

    reader = PdfReader(str(source))
    if not positions or max(positions) > len(reader.pages):
        raise ValueError(f"source PDF has {len(reader.pages)} pages; requested {positions}")
    writer = PdfWriter()
    for position in positions:
        writer.add_page(reader.pages[position - 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        writer.write(handle)


def _first_slide_part(source: Path) -> str:
    return _presentation_slide_parts(source)[0]


def make_layout_anomaly(source: Path, output: Path) -> dict[str, str]:
    """Create an off-canvas first-slide variant while preserving OOXML evidence."""
    with zipfile.ZipFile(source) as source_zip:
        entries = {info.filename: source_zip.read(info.filename) for info in source_zip.infolist()}

    slide_name = _first_slide_part(source)
    root = ET.fromstring(entries[slide_name])
    transform = root.find(f".//{{{A_NS}}}xfrm")
    if transform is None:
        raise RuntimeError(f"{slide_name} has no shape transform to mutate")

    offset = transform.find(f"{{{A_NS}}}off")
    if offset is not None:
        offset.set("x", "-100000000")
        offset.set("y", "-100000000")
        mutation = "first-shape-off-canvas"
    else:
        extent = transform.find(f"{{{A_NS}}}ext")
        if extent is None:
            raise RuntimeError(f"{slide_name} has no transform offset or extent")
        extent.set("cx", "100000000")
        extent.set("cy", "100000000")
        mutation = "first-shape-oversized"

    entries[slide_name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as output_zip:
        for name, data in entries.items():
            output_zip.writestr(name, data)
    return {"slide_part": slide_name, "mutation": mutation}


def materialize(
    output: Path,
    *,
    pptx_source: Path | None = None,
    pdf_source: Path | None = None,
) -> dict:
    """Materialize the six matrix inputs from the supplied current sources."""
    pptx, pdf = _resolve_sources(pptx_source, pdf_source)
    pptx_parts = _presentation_slide_parts(pptx)
    pdf_pages = _pdf_page_count(pdf)
    if len(pptx_parts) < 2 or pdf_pages < 2:
        raise ValueError("smoke matrix requires at least two PPTX slides and two PDF pages")

    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    first_two_positions = [1, 2]

    selected_pptx = select_pptx(pptx, output / "pptx-single-page.pptx", [1])
    select_pptx(pptx, output / "pptx-two-pages.pptx", first_two_positions)
    select_pdf(pdf, output / "pdf-single-page.pdf", [1])
    select_pdf(pdf, output / "pdf-two-pages.pdf", first_two_positions)

    candidate_dir = output / "same-name-candidates"
    candidate_dir.mkdir(exist_ok=True)
    shutil.copy2(pptx, candidate_dir / "same-name.pptx")
    shutil.copy2(pdf, candidate_dir / "same-name.pdf")

    anomaly_dir = output / "pptx-layout-anomaly-pdf-candidates"
    anomaly_dir.mkdir(exist_ok=True)
    anomaly = make_layout_anomaly(pptx, anomaly_dir / "layout-anomaly.pptx")
    shutil.copy2(pdf, anomaly_dir / "layout-anomaly.pdf")

    record = {
        "record_version": "1.0.0",
        "inputs": {
            "pptx": {"path": str(pptx), "sha256": sha256(pptx), "slide_count": len(pptx_parts)},
            "pdf": {"path": str(pdf), "sha256": sha256(pdf), "page_count": pdf_pages},
        },
        "selection": {
            "ordinal_pages": {"single": [1], "pair": first_two_positions},
            "pptx_single_parts": selected_pptx,
            "pptx_pair_parts": pptx_parts[:2],
        },
        "candidate_relation": "same-logical-document-only-when-explicitly-declared",
        "layout_anomaly": anomaly,
        "generated_files_are_test_inputs_not_production_outputs": True,
    }
    (output / "materialization-record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize Workflow A smoke matrix inputs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pptx-source", type=Path)
    parser.add_argument("--pdf-source", type=Path)
    args = parser.parse_args()
    record = materialize(
        args.output,
        pptx_source=args.pptx_source,
        pdf_source=args.pdf_source,
    )
    print(
        json.dumps(
            {
                "status": "materialized-test-inputs",
                "output": str(args.output.expanduser().resolve()),
                "pptx_sha256": record["inputs"]["pptx"]["sha256"],
                "pdf_sha256": record["inputs"]["pdf"]["sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
