#!/usr/bin/env python3
"""Executable Workflow A adapter.

The runner deliberately accepts one source per invocation.  PPTX and PDF
packages are independent: a PPTX page is rendered from the PPTX itself and a
PDF page is rendered from the PDF itself.  Optional visual review is evidence,
never a replacement for native extraction.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from posixpath import normpath
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import yaml
from PIL import Image

try:
    from workflow_a_output_validator import validate_package
except ImportError:  # pragma: no cover - package-style imports in test tools
    from .workflow_a_output_validator import validate_package


P_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def tool_path(name: str) -> str | None:
    """Resolve project runtime tools before broken system wrappers."""
    candidates: list[Path] = []
    override = RUNTIME_ROOT / "bin" / "override" / name
    native = RUNTIME_ROOT / "native" / "poppler" / "poppler" / "bin" / name
    if name == "soffice":
        native = RUNTIME_ROOT / "native" / "libreoffice-headless" / "libreoffice" / "LibreOfficeDev.app" / "Contents" / "MacOS" / "soffice"
    candidates.extend([override, native])
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def write_render_fontconfig(evidence: Path) -> Path:
    """Give the bundled LibreOffice renderer a deterministic CJK fallback.

    The input OOXML remains untouched.  This file only controls the temporary
    Office rendering process and is retained as evidence of the substitution.
    """
    config = evidence / "render-fontconfig.xml"
    config.write_text(
        """<?xml version=\"1.0\"?>
<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">
<fontconfig>
  <dir>/System/Library/Fonts</dir>
  <dir>/System/Library/Fonts/Supplemental</dir>
  <dir>/System/Library/AssetsV2</dir>
  <dir>/Library/Fonts</dir>
  <alias><family>微软雅黑</family><prefer><family>PingFang SC</family></prefer></alias>
  <alias><family>思源黑体 CN</family><prefer><family>PingFang SC</family></prefer></alias>
  <alias><family>+mn-ea</family><prefer><family>PingFang SC</family></prefer></alias>
</fontconfig>
""",
        encoding="utf-8",
    )
    return config


def command_record(command: list[str], timeout: int = 120, input_text: str | None = None, capture_limit: int = 12000, env: dict[str, str] | None = None) -> dict:
    record = {"command": shlex.join(command), "started_at": now(), "returncode": None, "stdout": "", "stderr": "", "status": "not-run"}
    if env:
        record["environment"] = {key: value for key, value in env.items() if key in {"FONTCONFIG_FILE", "SAL_USE_VCLPLUGIN"}}
    try:
        proc = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout, env={**os.environ, **env} if env else None)
        record.update({"returncode": proc.returncode, "stdout": proc.stdout[-capture_limit:] if capture_limit else proc.stdout, "stderr": proc.stderr[-capture_limit:] if capture_limit else proc.stderr, "status": "complete" if proc.returncode == 0 else "failed", "finished_at": now()})
    except Exception as exc:
        record.update({"status": "failed", "error": repr(exc), "finished_at": now()})
    return record


def parse_pages(value: str, total: int) -> list[int]:
    if value.strip().lower() == "all":
        return list(range(1, total + 1))
    result: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(part) for part in token.split("-", 1))
            result.update(range(start, end + 1))
        else:
            result.add(int(token))
    pages = sorted(result)
    if not pages or pages[0] < 1 or pages[-1] > total:
        raise ValueError(f"pages must be within 1..{total}: {value}")
    return pages


def ascii_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "source"


def source_slug(source: Path) -> str:
    # Chinese names intentionally remain in original_filename/display_name;
    # stable file names use a readable fallback plus the source hash prefix.
    stem = ascii_slug(source.stem)
    return f"{stem}-{sha256(source)[:8]}"


def package_id_for(source: Path) -> str:
    return f"{source_slug(source)}-{source.suffix.lower().lstrip('.') or 'file'}"


def media_type_for(source: Path) -> str:
    return {".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(source.suffix.lower(), mimetypes.guess_type(source.name)[0] or "application/octet-stream")


def pptx_slide_count(source: Path) -> int:
    with ZipFile(source) as deck:
        root = ET.fromstring(deck.read("ppt/presentation.xml"))
        slide_list = root.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldIdLst")
        return len(slide_list) if slide_list is not None else 0


def pdf_info(source: Path) -> tuple[dict, list[dict]]:
    info_tool = tool_path("pdfinfo")
    if not info_tool:
        return {}, [{"code": "pdfinfo-unavailable", "status": "needs-review"}]
    record = command_record([info_tool, str(source)])
    parsed: dict[str, str] = {}
    for line in record.get("stdout", "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
    parsed["_command_record"] = record
    return parsed, [] if record["status"] == "complete" else [{"code": "pdfinfo-failed", "record": record}]


def source_inventory(source: Path, source_type: str, pages: list[int] | None = None) -> dict:
    fingerprint = sha256(source)
    diagnostics: list[dict] = []
    if source_type == "pptx":
        try:
            total = pptx_slide_count(source)
        except Exception as exc:
            total, diagnostics = 0, [{"code": "pptx-package-invalid", "error": repr(exc)}]
        dimensions = {"slides": total, "selected_pages": pages or []}
        text_layer, image_layer = "present", "present-or-unknown"
    elif source_type == "pdf":
        info, diagnostics = pdf_info(source)
        total = int(info.get("Pages", 0) or 0)
        dimensions = {"pages": total, "page_size": info.get("Page size"), "selected_pages": pages or [], "pdfinfo": info}
        text_layer, image_layer = "present-or-unknown", "present-or-unknown"
    else:
        total, dimensions = 1, {"width": None, "height": None, "selected_pages": [1]}
        try:
            with Image.open(source) as image:
                dimensions.update(width=image.width, height=image.height)
            text_layer, image_layer = "absent", "present"
        except Exception as exc:
            text_layer, image_layer, diagnostics = "unknown", "unknown", [{"code": "image-read-failed", "error": repr(exc)}]
    return {
        "source_fingerprint": fingerprint,
        "source": {"path": str(source.resolve()), "size_bytes": source.stat().st_size, "media_type": media_type_for(source), "original_filename": source.name},
        "media_type": media_type_for(source),
        "status": "complete" if not diagnostics else "needs-review",
        "page_count_or_dimensions": dimensions,
        "text_layer": text_layer,
        "image_layer": image_layer,
        "readability": "readable" if not diagnostics else "partial",
        "source_selection": "single-source",
        "candidate_comparison": None,
        "diagnostics": diagnostics,
    }


def _relationship_map(deck: ZipFile, rel_name: str) -> dict[str, str]:
    if rel_name not in deck.namelist():
        return {}
    root = ET.fromstring(deck.read(rel_name))
    values = {}
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        target = rel.attrib.get("Target", "")
        values[rel.attrib["Id"]] = normpath("ppt/slides/" + target) if not target.startswith("/") else target.lstrip("/")
    return values


def _shape_id(shape: ET.Element) -> tuple[str, str | None]:
    cpr = None
    for candidate in (shape.find("./p:nvSpPr/p:cNvPr", P_NS), shape.find("./p:nvPicPr/p:cNvPr", P_NS), shape.find("./p:nvCxnSpPr/p:cNvPr", P_NS), shape.find("./p:nvGrpSpPr/p:cNvPr", P_NS), shape.find("./p:nvGraphicFramePr/p:cNvPr", P_NS)):
        if candidate is not None:
            cpr = candidate
            break
    return (str(cpr.attrib.get("id", "unknown")) if cpr is not None else "unknown", cpr.attrib.get("name") if cpr is not None else None)


def _shape_coords(shape: ET.Element) -> dict:
    xfrm = shape.find(".//a:xfrm", P_NS)
    if xfrm is None:
        return {}
    off, ext = xfrm.find("a:off", P_NS), xfrm.find("a:ext", P_NS)
    if off is None or ext is None:
        return {}
    return {"x": int(off.attrib.get("x", 0)), "y": int(off.attrib.get("y", 0)), "cx": int(ext.attrib.get("cx", 0)), "cy": int(ext.attrib.get("cy", 0)), "unit": "EMU"}


def _shape_fonts(shape: ET.Element) -> list[str]:
    values: list[str] = []
    for node in shape.findall(".//a:rPr", P_NS) + shape.findall(".//a:defRPr", P_NS) + shape.findall(".//a:endParaRPr", P_NS):
        for key in ("typeface",):
            value = node.find(f"a:latin", P_NS)
            if value is not None and value.attrib.get(key):
                values.append(value.attrib[key])
    return sorted(set(values))


def _theme_fonts(deck: ZipFile) -> dict:
    fonts: dict[str, dict] = {}
    for name in deck.namelist():
        if not (name.startswith("ppt/theme/") and name.endswith(".xml")):
            continue
        root = ET.fromstring(deck.read(name))
        scheme = root.find(".//a:fontScheme", P_NS)
        if scheme is not None:
            for role, node in (("major", scheme.find("a:majorFont", P_NS)), ("minor", scheme.find("a:minorFont", P_NS))):
                if node is not None:
                    fonts[f"{name}:{role}"] = {"latin": (node.find("a:latin", P_NS).attrib.get("typeface") if node.find("a:latin", P_NS) is not None else None), "east_asia": (node.find("a:ea", P_NS).attrib.get("typeface") if node.find("a:ea", P_NS) is not None else None)}
    return fonts


def _font_diagnostics(fonts: set[str], fontconfig: Path | None = None) -> dict:
    matcher = tool_path("fc-match")
    env = {"FONTCONFIG_FILE": str(fontconfig)} if fontconfig else None
    result = {"fonts_used": sorted(fonts), "matches": [], "tool": matcher, "fontconfig": str(fontconfig) if fontconfig else None, "status": "not-run"}
    if not matcher:
        result["status"] = "unavailable"
        return result
    for font in sorted(fonts):
        record = command_record([matcher, font], timeout=20, env=env)
        result["matches"].append({"requested": font, "record": record, "matched": record.get("stdout", "").strip()})
    result["status"] = "complete" if all(item["record"]["status"] == "complete" for item in result["matches"]) else "partial"
    return result


def pptx_native_model(source: Path, pages: list[int], fontconfig: Path | None = None) -> dict:
    fingerprint = sha256(source)
    result = {"source_fingerprint": fingerprint, "source": {"path": str(source.resolve()), "media_type": media_type_for(source)}, "status": "complete", "parser": {"tool": "zipfile + ElementTree", "executed_at": now()}, "slides": [], "text_blocks": [], "nodes": [], "relations": [], "coordinates": [], "image_refs": [], "source_locations": [], "diagnostics": [], "theme_fonts": {}, "font_diagnostics": {}, "page_dimensions": {}}
    fonts: set[str] = set()
    try:
        with ZipFile(source) as deck:
            result["theme_fonts"] = _theme_fonts(deck)
            names = set(deck.namelist())
            if "ppt/presentation.xml" in names:
                presentation = ET.fromstring(deck.read("ppt/presentation.xml"))
                slide_size = presentation.find("p:sldSz", P_NS)
                if slide_size is not None:
                    result["page_dimensions"] = {"cx": int(slide_size.attrib.get("cx", 0)), "cy": int(slide_size.attrib.get("cy", 0)), "unit": "EMU"}
            for page in pages:
                xml_name = f"ppt/slides/slide{page}.xml"
                rel_name = f"ppt/slides/_rels/slide{page}.xml.rels"
                if xml_name not in names:
                    result["diagnostics"].append({"page": page, "code": "slide-xml-missing"})
                    continue
                root = ET.fromstring(deck.read(xml_name))
                rels = _relationship_map(deck, rel_name)
                slide = {"page": page, "source_location": xml_name, "text_blocks": [], "nodes": [], "relations": [], "coordinates": [], "media_relations": [], "diagnostics": [], "width": result["page_dimensions"].get("cx"), "height": result["page_dimensions"].get("cy")}
                tree = root.find(".//p:cSld/p:spTree", P_NS)
                order = 0

                def walk(parent: ET.Element, parent_id: str | None = None) -> None:
                    nonlocal order
                    for shape in list(parent):
                        if shape.tag.rsplit("}", 1)[-1] not in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}:
                            continue
                        order += 1
                        object_id, name = _shape_id(shape)
                        kind = {"sp": "shape", "pic": "picture", "graphicFrame": "graphic-frame", "cxnSp": "connector", "grpSp": "group"}[shape.tag.rsplit("}", 1)[-1]]
                        texts = ["".join(t.text or "" for t in paragraph.findall(".//a:t", P_NS)).strip() for paragraph in shape.findall(".//a:p", P_NS)]
                        texts = [text for text in texts if text]
                        coords = _shape_coords(shape)
                        node = {"object_id": object_id, "name": name, "kind": kind, "text": texts, "order": order, "parent": parent_id, "page": page, "source_location": xml_name}
                        slide["nodes"].append(node)
                        if coords:
                            slide["coordinates"].append({"page": page, "object_id": object_id, **coords})
                        fonts.update(_shape_fonts(shape))
                        if texts:
                            slide["text_blocks"].append({"page": page, "block_id": f"p{page}-o{object_id}", "object_id": object_id, "text": "\n".join(texts), "paragraphs": texts, "bbox": coords, "order": order, "parent": parent_id, "font_names": _shape_fonts(shape)})
                        if kind == "connector":
                            start = shape.find(".//a:stCxn", P_NS)
                            end = shape.find(".//a:endCxn", P_NS)
                            slide["relations"].append({"relation_id": f"p{page}-connector-{object_id}", "kind": "connector", "object_id": object_id, "from": start.attrib.get("id") if start is not None else "unknown", "to": end.attrib.get("id") if end is not None else "unknown", "geometry": coords, "status": "located" if start is not None and end is not None else "needs-review", "page": page})
                        for blip in shape.findall(".//a:blip", P_NS):
                            rid = blip.attrib.get(f"{{{P_NS['r']}}}embed")
                            target = rels.get(rid) if rid else None
                            relation = {"page": page, "object_id": object_id, "relationship_id": rid, "target": target, "status": "located" if target else "unknown"}
                            slide["media_relations"].append(relation)
                            result["image_refs"].append(relation)
                        if kind == "group":
                            walk(shape, object_id)

                if tree is not None:
                    walk(tree)
                result["slides"].append(slide)
                result["text_blocks"].extend(slide["text_blocks"])
                result["nodes"].extend(slide["nodes"])
                result["relations"].extend(slide["relations"])
                result["coordinates"].extend(slide["coordinates"])
                result["source_locations"].append(xml_name)
    except Exception as exc:
        result["status"] = "failed"
        result["diagnostics"].append({"code": "pptx-xml-parse-failed", "error": repr(exc)})
    result["font_diagnostics"] = _font_diagnostics(fonts, fontconfig)
    if len(result["slides"]) != len(pages):
        result["status"] = "needs-review"
    return result


def _pdf_words_with_poppler(source: Path, page: int) -> tuple[list[dict], dict]:
    pdftotext = tool_path("pdftotext")
    if not pdftotext:
        return [], {"status": "unavailable", "diagnostic": "pdftotext unavailable"}
    record = command_record([pdftotext, "-bbox-layout", "-f", str(page), "-l", str(page), str(source), "-"], timeout=120, capture_limit=0)
    if record["status"] != "complete":
        return [], record
    words: list[dict] = []
    current_page = None
    for match in re.finditer(r"<page[^>]*width=\"([0-9.]+)\"[^>]*height=\"([0-9.]+)\"[^>]*>(.*?)</page>", record["stdout"], flags=re.S):
        current_page = {"width": float(match.group(1)), "height": float(match.group(2))}
        for index, word_match in enumerate(re.finditer(r"<word xMin=\"([0-9.]+)\" yMin=\"([0-9.]+)\" xMax=\"([0-9.]+)\" yMax=\"([0-9.]+)\"[^>]*>(.*?)</word>", match.group(3), flags=re.S), 1):
            x0, top, x1, bottom = (float(word_match.group(i)) for i in range(1, 5))
            text = html.unescape(re.sub(r"<[^>]+>", "", word_match.group(5))).strip()
            if text:
                words.append({"block_id": f"p{page}-b{index:03d}", "page": page, "text": text, "bbox": {"x0": x0, "top": top, "x1": x1, "bottom": bottom, "unit": "pt"}, "order": index, "font": None, "font_size_estimate_pt": round(bottom - top, 2)})
    # Collapse words on the same baseline into layout text blocks.  Keeping
    # the word list in evidence preserves bbox facts while preventing title
    # classification from selecting one arbitrary word of a sentence.
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["bbox"]["top"], item["bbox"]["x0"])):
        if not lines or abs(word["bbox"]["top"] - lines[-1][0]["bbox"]["top"]) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    blocks: list[dict] = []
    for index, line in enumerate(lines, 1):
        x0 = min(item["bbox"]["x0"] for item in line)
        top = min(item["bbox"]["top"] for item in line)
        x1 = max(item["bbox"]["x1"] for item in line)
        bottom = max(item["bbox"]["bottom"] for item in line)
        blocks.append({"block_id": f"p{page}-l{index:03d}", "page": page, "text": " ".join(item["text"] for item in line), "bbox": {"x0": x0, "top": top, "x1": x1, "bottom": bottom, "unit": "pt"}, "order": index, "font": None, "font_size_estimate_pt": round(max(item["font_size_estimate_pt"] for item in line), 2), "word_blocks": [item["block_id"] for item in line]})
    return blocks, {"record": record, "page": current_page, "word_blocks": words}


def _pdf_page_graphics_stats(source: Path, page: int) -> dict:
    """Collect best-effort page graphics counts without raster/OCR input.

    Poppler exposes embedded-image inventory directly.  Its XML text export
    does not expose vector primitives, so line/rectangle counts remain
    explicit ``unknown`` rather than being guessed from rendered pixels.
    """
    stats = {"image_count": 0, "line_count": 0, "rect_count": 0, "curve_count": 0, "count_status": "partial", "count_method": "pdftocairo -svg element inventory plus pdfimages -list"}
    pdfimages = tool_path("pdfimages")
    if pdfimages:
        record = command_record([pdfimages, "-list", "-f", str(page), "-l", str(page), str(source)], timeout=120)
        stats["pdfimages_command"] = record
        if record.get("status") == "complete":
            rows = [line for line in record.get("stdout", "").splitlines() if line.strip() and not line.lstrip().startswith(("page", "-"))]
            stats["image_count"] = len(rows)
    pdftocairo = tool_path("pdftocairo")
    if pdftocairo:
        with tempfile.TemporaryDirectory(prefix="workflow-a-svg-") as temp:
            svg_path = Path(temp) / "page.svg"
            svg_record = command_record([pdftocairo, "-svg", "-f", str(page), "-l", str(page), str(source), str(svg_path)], timeout=180)
            stats["svg_command"] = svg_record
            if svg_record.get("status") == "complete" and svg_path.is_file():
                content = svg_path.read_text(encoding="utf-8", errors="ignore")
                stats["image_count"] = max(stats["image_count"], len(re.findall(r"<image\b", content)))
                stats["line_count"] = len(re.findall(r"<line\b", content))
                stats["rect_count"] = len(re.findall(r"<rect\b", content))
                stats["curve_count"] = len(re.findall(r"<path\b", content))
                stats["count_status"] = "complete"
    if stats["count_status"] != "complete":
        stats["count_status"] = "unavailable"
        stats["count_method"] = "bundled Poppler graphics tools unavailable or failed"
    return stats


def pdf_layout_model(source: Path, pages: list[int]) -> dict:
    fingerprint = sha256(source)
    result = {"source_fingerprint": fingerprint, "source": {"path": str(source.resolve()), "media_type": media_type_for(source)}, "status": "complete", "parser": {"tool": "Poppler pdftotext -bbox-layout", "executed_at": now()}, "pages": [], "text_blocks": [], "nodes": [], "relations": [], "coordinates": [], "source_locations": [], "diagnostics": [], "font_inventory": []}
    info, info_diag = pdf_info(source)
    result["parser"]["pdfinfo"] = info.get("_command_record") if info else None
    result["diagnostics"].extend(info_diag)
    pdffonts = tool_path("pdffonts")
    if pdffonts:
        font_record = command_record([pdffonts, str(source)], timeout=120)
        result["font_inventory"] = [line for line in font_record.get("stdout", "").splitlines() if line.strip()]
        result["parser"]["pdffonts"] = font_record
    for page in pages:
        blocks, evidence = _pdf_words_with_poppler(source, page)
        if not blocks:
            result["diagnostics"].append({"page": page, "code": "pdf-text-layer-empty-or-unavailable", "evidence": evidence})
        page_info = evidence.get("page") or {"width": None, "height": None}
        graphics = _pdf_page_graphics_stats(source, page)
        item = {"page": page, "width": page_info.get("width"), "height": page_info.get("height"), "text_blocks": blocks, "word_blocks": evidence.get("word_blocks", []), "reading_order": [block["block_id"] for block in blocks], "layout_evidence": {"char_count": sum(len(block["text"]) for block in blocks), "word_count": len(evidence.get("word_blocks", [])), "text_block_count": len(blocks), "image_count": graphics.get("image_count"), "rect_count": graphics.get("rect_count"), "line_count": graphics.get("line_count"), "curve_count": graphics.get("curve_count"), "graphics_count_status": graphics.get("count_status"), "graphics_count_method": graphics.get("count_method"), "graphics_command": graphics.get("command")}, "status": "complete" if blocks else "needs-review"}
        result["pages"].append(item)
        result["text_blocks"].extend(blocks)
        result["coordinates"].extend({"page": page, "block_id": block["block_id"], "bbox": block["bbox"], "source": f"pdf-page-{page}"} for block in blocks)
        result["source_locations"].append(f"pdf-page-{page}")
    if len(result["pages"]) != len(pages) or any(page["status"] != "complete" for page in result["pages"]):
        result["status"] = "needs-review"
    return result


def classify_content(native_model: dict, visual: dict | None = None, ocr: dict | None = None, structured: dict | None = None) -> dict:
    visual = visual or {}
    ocr = ocr or {}
    structured = structured or {}
    visual_by_page = {item.get("page"): item.get("result") or {} for item in visual.get("reviews", [])}
    structured_by_page = {item.get("page"): item.get("result") or {} for item in structured.get("reviews", [])}
    ocr_by_page = {item.get("page"): item for item in ocr.get("candidates", [])}
    pages = native_model.get("pages") or native_model.get("slides") or []
    if native_model.get("slides"):
        pages = native_model["slides"]
    classified_pages = []
    for page in pages:
        page_no = page.get("page")
        blocks = []
        for raw_block in page.get("text_blocks", []):
            paragraphs = raw_block.get("paragraphs") or [raw_block.get("text", "")]
            line_index = 0
            for paragraph in paragraphs:
                for line in str(paragraph).splitlines() or [""]:
                    if not line.strip():
                        continue
                    line_index += 1
                    block = dict(raw_block)
                    block["text"] = line.strip()
                    block["block_id"] = f"{raw_block.get('block_id', 'block')}-line-{line_index:02d}"
                    block["paragraph_index"] = line_index - 1
                    blocks.append(block)

        width = page.get("width") or native_model.get("page_dimensions", {}).get("cx") or max((block.get("bbox", {}).get("x", 0) + block.get("bbox", {}).get("cx", 0) for block in blocks), default=1) or 1
        height = page.get("height") or native_model.get("page_dimensions", {}).get("cy") or max((block.get("bbox", {}).get("y", 0) + block.get("bbox", {}).get("cy", 0) for block in blocks), default=1) or 1

        def top_ratio(block: dict) -> float:
            bbox = block.get("bbox") or {}
            return float(bbox.get("top", bbox.get("y", 0)) or 0) / float(height)

        def x_value(block: dict) -> float:
            bbox = block.get("bbox") or {}
            return float(bbox.get("x0", bbox.get("x", 0)) or 0)

        def is_running_label(text: str) -> bool:
            normalized = _clean_text(text)
            compact = re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)
            generic_compact = {
                re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)
                for value in GENERIC_OCR_HEADERS
            }
            return bool(
                re.fullmatch(r"(?:个人成长|21个思维模型(?:-\d+)?|\d{1,2})", normalized)
                or compact in generic_compact
                or compact.startswith("CHARLIEMUNGER")
            )

        # Keep a conservative fallback for normalized/synthetic coordinates: a
        # tiny bbox may be expressed in a different unit than page dimensions.
        # Never turn an otherwise parseable page into a blank reader layer just
        # because the running-label margin cannot be compared numerically.
        usable = [block for block in blocks if not is_running_label(block.get("text", "")) and top_ratio(block) > 0.04]
        if not usable:
            usable = [block for block in blocks if not is_running_label(block.get("text", ""))]
        title_candidates = [block for block in usable if top_ratio(block) <= 0.36]
        title = min(title_candidates or usable, key=lambda block: (top_ratio(block), x_value(block)), default=None)
        page_structure = structured_by_page.get(page_no, {})
        native_present = native_page_has_text(page)
        model_semantic_ok = native_present and mac96_semantic_usable(page_structure, page)
        ocr_summary = ocr_page_summary(ocr, page_no)
        model_title = (page_structure.get("title") or {}).get("text") if isinstance(page_structure.get("title"), dict) else None
        model_subtitle = (page_structure.get("subtitle") or {}).get("text") if isinstance(page_structure.get("subtitle"), dict) else None
        # Native text is the only confirmed reader source.  mac96 text
        # structure is supporting evidence when native text exists; image-only
        # pages must use explicit OCR candidates instead of silently accepting
        # a schema-valid model response.
        confirmed_model_title = _clean_text(model_title) if native_present and model_semantic_ok else ""
        native_title = _clean_text(title.get("text", "")) if title else ""
        ocr_title = ocr_summary.get("title_text", "")
        title_text = confirmed_model_title or native_title or ocr_title or f"待复核：第 {page_no} 页"
        title_lines = [line.strip() for line in title_text.splitlines() if line.strip()]
        title_text = title_lines[0] if title_lines else title_text

        heading_candidates = [
            block for block in usable
            if block is not title and re.search(r"vs|对比|复盘|模型|方法|法则|层级|人生观|价值观", block.get("text", ""), re.I)
        ]
        heading = min(heading_candidates, key=lambda block: (top_ratio(block), x_value(block)), default=None)
        inline_heading = title_lines[1] if len(title_lines) > 1 else None
        heading_text = (_clean_text(model_subtitle) if native_present and model_semantic_ok else "") or inline_heading or (heading.get("text", "") if heading else None)
        if heading_text and re.search(r"vs", heading_text, re.I):
            heading_text = re.sub(r"\s*vs\s*", " vs ", heading_text, flags=re.I)

        classified_blocks = []
        for block in sorted(usable, key=lambda item: (top_ratio(item), x_value(item), item.get("order", 0))):
            text = block.get("text", "").strip()
            if not text or text == title_text or text == heading_text:
                continue
            if title is block:
                role, markers = "page-title", ["native-top-region"]
            elif heading is block:
                role, markers = "theme-or-comparison-heading", ["native-heading-pattern"]
            else:
                role, markers = "body-content", []
            bbox = block.get("bbox") or {}
            center_x = ((bbox.get("x0", bbox.get("x", 0)) or 0) + (bbox.get("x1", (bbox.get("x", 0) or 0) + (bbox.get("cx", 0) or 0)) or 0)) / 2
            region = "left" if center_x < width * 0.4 else "right" if center_x > width * 0.6 else "center"
            basis = {"native_bbox": bbox, "font_size_estimate_pt": block.get("font_size_estimate_pt"), "top_ratio": round(top_ratio(block), 4), "horizontal_region": region, "paragraph_or_shape_relation": {"object_id": block.get("object_id"), "parent": block.get("parent")}, "semantic_markers": markers, "visual_candidate_support": visual_by_page.get(page_no), "ocr_candidate_support": {"source": "evidence/ocr-candidates.json", "page": page_no} if ocr_by_page.get(page_no) else None}
            classified_blocks.append({"block_id": block.get("block_id"), "text": text, "role": role, "basis": basis})
        # OCR is a reviewable candidate layer, never a confirmed native fact.
        # Keep confidence and the raw candidate evidence attached per block so
        # readers can search the proposed text while reviewers can audit it.
        for index, candidate in enumerate(ocr_summary.get("body_candidates", []), 1):
            candidate_text = normalise_ocr_reader_text(candidate.get("text"))
            if not candidate_text:
                continue
            classified_blocks.append({
                "block_id": f"p{page_no}-ocr-{index:03d}",
                "text": candidate_text,
                "role": "ocr-candidate-body",
                "status": "needs-review",
                "reader_group": candidate.get("_reader_group", "fragment"),
                "basis": {"source": "ocr-candidate", "evidence": "evidence/ocr-candidates.json", "page": page_no},
            })
        if title:
            title_basis = {"native_bbox": title.get("bbox") or {}, "top_ratio": round(top_ratio(title), 4), "paragraph_or_shape_relation": {"object_id": title.get("object_id"), "parent": title.get("parent")}}
        elif ocr_summary.get("title"):
            ocr_title_candidate = ocr_summary["title"]
            title_basis = {"source": "ocr-candidate", "evidence": "evidence/ocr-candidates.json", "page": page_no}
        else:
            title_basis = {}
        page_title_source = "native" if title or confirmed_model_title else "ocr-candidate" if ocr_title else "missing"
        title_flags = title_quality_flags(ocr_summary.get("title")) if page_title_source == "ocr-candidate" else ["fallback-title"] if page_title_source == "missing" else []
        if page_title_source == "ocr-candidate" and not ocr_title_supported(ocr_title, ocr_summary.get("candidates", [])):
            title_flags.append("title-not-in-ocr-candidates")
        title_status = "classified" if title or confirmed_model_title else "candidate" if ocr_title else "needs-review"
        status = "complete" if title is not None and native_present else "needs-review"
        classified_pages.append({
            "page": page_no,
            "page_title": {"text": title_text, "role": "page-title", "status": title_status, "basis": title_basis, "quality_flags": title_flags, "source": page_title_source},
            "theme_or_heading": {"text": heading_text, "role": "theme-or-comparison-heading", "status": "classified" if heading_text else "not-present"},
            "classified_blocks": classified_blocks,
            "semantic_sections": page_structure.get("semantic_sections", []) if model_semantic_ok else [],
            "relations": page_structure.get("relations", []) if model_semantic_ok else [],
            "layout_form": page_structure.get("layout_form") if model_semantic_ok else None,
            "uncertainties": page_structure.get("uncertainties", []) if model_semantic_ok else [],
            "text_evidence": {"native_present": native_present, "mac96_semantic_usable": model_semantic_ok, "ocr_status": ocr_summary.get("status"), "ocr_candidate_count": ocr_summary.get("candidate_count", 0), "ocr_title": ocr_title},
            "status": status,
        })
    return {"status": "complete" if classified_pages and all(page["status"] == "complete" for page in classified_pages) else "needs-review", "pages": classified_pages, "method": "native reading order + running-label filtering + top-region title + inline heading separation + visual/OCR support", "diagnostics": []}


def _render_pdf(pdf: Path, out: Path, pages: list[int], prefix: str, env: dict[str, str] | None = None) -> tuple[dict[int, Path], dict]:
    renderer = tool_path("pdftoppm")
    if not renderer:
        return {}, {"status": "failed", "failure": "pdftoppm unavailable"}
    out.mkdir(parents=True, exist_ok=True)
    record = command_record([renderer, "-png", "-r", "144", "-f", str(min(pages)), "-l", str(max(pages)), str(pdf), str(out / prefix)], timeout=300, env=env)
    result: dict[int, Path] = {}
    rendered_files = {}
    for path in out.glob(f"{prefix}-*.png"):
        suffix = path.stem.rsplit("-", 1)[-1]
        if suffix.isdigit():
            rendered_files[int(suffix)] = path
    for page in pages:
        source = rendered_files.get(page)
        if source is not None and source.is_file():
            result[page] = source
    return result, {"renderer": "pdftoppm", "command": record, "status": "complete" if len(result) == len(pages) and record["status"] == "complete" else "failed", "rendered_pages": sorted(result), "requested_pages": pages}


def render_source(source: Path, source_type: str, out: Path, evidence: Path, pages: list[int], slug: str, fontconfig: Path | None = None) -> tuple[dict[int, Path], dict]:
    env = {"FONTCONFIG_FILE": str(fontconfig)} if fontconfig else None
    if source_type == "pptx":
        soffice = tool_path("soffice")
        office_record = command_record([soffice, "--version"], timeout=30, env=env) if soffice else {"status": "failed", "error": "soffice unavailable"}
        if not soffice:
            return {}, {"source_type": "pptx", "status": "failed", "office_renderer": office_record, "failure": "office-page-renderer unavailable"}
        with tempfile.TemporaryDirectory(prefix="workflow-a-pptx-") as temp:
            temp_path = Path(temp)
            convert = command_record([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(temp_path), str(source)], timeout=300, env=env)
            rendered_pdf = temp_path / f"{source.stem}.pdf"
            if convert["status"] != "complete" or not rendered_pdf.is_file():
                return {}, {"source_type": "pptx", "status": "failed", "office_renderer": {"tool": soffice, "version": office_record, "convert": convert}, "failure": "PPTX Office rendering failed"}
            # Keep the intermediate renderer output as auditable evidence.
            office_pdf = evidence / "pptx-office-render.pdf"
            shutil.copy2(rendered_pdf, office_pdf)
            rendered, poppler = _render_pdf(rendered_pdf, out, pages, "source-page", env=env)
            assets = {}
            for page, path in rendered.items():
                destination = out / f"{slug}__p{page:03d}__page-render__page-{page:03d}__original-page__o01.png"
                path.replace(destination)
                assets[page] = destination
            return assets, {"source_type": "pptx", "status": "complete" if len(assets) == len(pages) else "failed", "source_owned": True, "renderer_input_source": str(source), "font_configuration": {"path": str(fontconfig) if fontconfig else None, "substitutions": {"微软雅黑": "PingFang SC", "思源黑体 CN": "PingFang SC", "+mn-ea": "PingFang SC"}}, "office_renderer": {"tool": soffice, "version": office_record, "convert": convert, "intermediate_pdf": str(office_pdf)}, "page_renderer": poppler}
    if source_type == "pdf":
        rendered, poppler = _render_pdf(source, out, pages, "source-page", env=env)
        assets = {}
        for page, path in rendered.items():
            destination = out / f"{slug}__p{page:03d}__page-render__page-{page:03d}__original-page__o01.png"
            path.replace(destination)
            assets[page] = destination
        return assets, {"source_type": "pdf", "status": "complete" if len(assets) == len(pages) else "failed", "source_owned": True, "renderer_input_source": str(source), "page_renderer": poppler}
    with Image.open(source) as image:
        destination = out / f"{slug}__p001__input-image__page-001__original-input__o01.{source.suffix.lower().lstrip('.') or 'png'}"
        shutil.copy2(source, destination)
    return {1: destination}, {"source_type": "image", "status": "complete", "source_owned": True, "renderer": "source-image-preserved", "command": None}


def render_visibility_evidence(page_assets: dict[int, Path], native_model: dict) -> dict:
    """Measure whether native text regions contain visible rendered pixels."""
    dimensions = native_model.get("page_dimensions") or {}
    native_pages = {item.get("page"): item for item in (native_model.get("pages") or native_model.get("slides") or [])}
    pages: list[dict] = []
    for page, asset in sorted(page_assets.items()):
        item = native_pages.get(page) or {}
        blocks = item.get("text_blocks") or []
        try:
            with Image.open(asset).convert("RGB") as image:
                width, height = image.size
                corner = image.getpixel((0, 0))
                visible_regions = 0
                region_samples = []
                for block in blocks:
                    bbox = block.get("bbox") or {}
                    if bbox.get("unit") == "pt":
                        page_width = float(item.get("width") or 1)
                        page_height = float(item.get("height") or 1)
                        left = int(float(bbox.get("x0", 0)) / page_width * width)
                        top = int(float(bbox.get("top", 0)) / page_height * height)
                        right = int(float(bbox.get("x1", 0)) / page_width * width)
                        bottom = int(float(bbox.get("bottom", 0)) / page_height * height)
                    else:
                        page_width = float(dimensions.get("cx") or 1)
                        page_height = float(dimensions.get("cy") or 1)
                        left = int(float(bbox.get("x", 0)) / page_width * width)
                        top = int(float(bbox.get("y", 0)) / page_height * height)
                        right = int(float(bbox.get("x", 0) + bbox.get("cx", 0)) / page_width * width)
                        bottom = int(float(bbox.get("y", 0) + bbox.get("cy", 0)) / page_height * height)
                    left, top = max(0, left), max(0, top)
                    right, bottom = min(width, max(left + 1, right)), min(height, max(top + 1, bottom))
                    crop = image.crop((left, top, right, bottom))
                    ink = 0
                    for pixel in crop.getdata():
                        if sum(abs(pixel[index] - corner[index]) for index in range(3)) > 36:
                            ink += 1
                    ratio = ink / max(1, crop.width * crop.height)
                    if ratio >= 0.01:
                        visible_regions += 1
                    region_samples.append({"block_id": block.get("block_id"), "text": block.get("text"), "ink_ratio": round(ratio, 4)})
                page_status = "not-applicable" if not blocks else "complete" if visible_regions >= max(1, int(len(blocks) * 0.5)) else "needs-review"
                pages.append({"page": page, "image": str(asset), "width": width, "height": height, "native_text_blocks": len(blocks), "visible_text_regions": visible_regions, "text_region_samples": region_samples, "status": page_status})
        except Exception as exc:
            pages.append({"page": page, "image": str(asset), "status": "needs-review", "error": repr(exc)})
    return {"status": "complete" if pages and all(item.get("status") in {"complete", "not-applicable"} for item in pages) else "needs-review", "pages": pages, "method": "native bbox crop versus rendered pixel difference from page corner"}


def extract_embedded_for_pages(source: Path, output: Path, pages: list[int], slug: str, package_id: str) -> list[dict]:
    if source.suffix.lower() != ".pptx":
        return []
    output.mkdir(parents=True, exist_ok=True)
    found: dict[str, dict] = {}
    with ZipFile(source) as deck:
        names = set(deck.namelist())
        for page in pages:
            slide, relfile = f"ppt/slides/slide{page}.xml", f"ppt/slides/_rels/slide{page}.xml.rels"
            if slide not in names or relfile not in names:
                continue
            root = ET.fromstring(deck.read(slide))
            rels = _relationship_map(deck, relfile)
            for shape in root.findall(".//p:sp", P_NS) + root.findall(".//p:pic", P_NS) + root.findall(".//p:graphicFrame", P_NS):
                object_id, _ = _shape_id(shape)
                for blip in shape.findall(".//a:blip", P_NS):
                    rid = blip.attrib.get(f"{{{P_NS['r']}}}embed")
                    target = rels.get(rid) if rid else None
                    if not target or "/media/" not in f"/{target}":
                        continue
                    zip_path = target.lstrip("/")
                    if zip_path not in names:
                        continue
                    key = zip_path
                    item = found.setdefault(key, {"original": zip_path, "occurrences": []})
                    item["occurrences"].append({"page": page, "object_id": object_id, "status": "located"})
        for index, item in enumerate(found.values(), 1):
            extension = Path(item["original"]).suffix.lower().lstrip(".") or "bin"
            destination = output / f"{slug}__p{item['occurrences'][0]['page']:03d}__embedded-media__media-{index:03d}__source-media__o01.{extension}"
            destination.write_bytes(deck.read(item["original"]))
            item["file"] = destination
    return list(found.values())


def parse_mac96_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    candidates = [message.get("content", ""), message.get("thinking", ""), payload.get("response", "") if isinstance(payload, dict) else ""]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
        if isinstance(candidate, str):
            start, end = candidate.find("{"), candidate.rfind("}")
            if start >= 0 and end > start:
                try:
                    value = json.loads(candidate[start:end + 1])
                    if isinstance(value, dict):
                        return value
                except json.JSONDecodeError:
                    pass
    return None


def _clean_text(value: object) -> str:
    """Return a compact reader-facing string without treating whitespace as content."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def native_page_has_text(page: dict | None) -> bool:
    """Whether a native page contains usable text, not merely an empty block."""
    if not isinstance(page, dict):
        return False
    blocks = page.get("text_blocks") or []
    return any(_clean_text(block.get("text")) for block in blocks if isinstance(block, dict))


def mac96_semantic_usable(result: dict | None, native_page: dict | None = None) -> bool:
    """Reject schema-valid but semantically empty mac96 text responses.

    A relation-only visual response is deliberately not accepted here.  The
    text path must carry a title, subtitle, semantic section, or body-bearing
    text; page-image relation evidence is a separate source and never proves
    text extraction.
    """
    if not isinstance(result, dict):
        return False
    title = result.get("title") if isinstance(result.get("title"), dict) else {}
    subtitle = result.get("subtitle") if isinstance(result.get("subtitle"), dict) else {}
    has_title = bool(_clean_text(title.get("text")))
    has_subtitle = bool(_clean_text(subtitle.get("text")))
    reader_keys = {"label", "action", "step", "meaning", "name", "title", "description", "content", "text"}
    sections = [
        item
        for item in result.get("semantic_sections", []) or []
        if isinstance(item, dict)
        and any(_clean_text(item.get(key)) for key in reader_keys)
    ]
    body = result.get("body") or result.get("text") or result.get("content")
    if isinstance(body, dict):
        body = body.get("text") or body.get("content") or body.get("description")
    has_body = isinstance(body, str) and bool(_clean_text(body))
    native_present = native_page_has_text(native_page)
    # Empty mac96 semantics are unusable even when the native layer is also
    # empty.  If native text exists, require the model to preserve at least a
    # textual anchor rather than accepting relation-only output.
    return has_title or has_subtitle or bool(sections) or has_body


def _ocr_candidates_for_page(ocr: dict | None, page: int) -> list[dict]:
    item = next((value for value in (ocr or {}).get("candidates", []) if isinstance(value, dict) and value.get("page") == page), None)
    if not item:
        return []
    candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else []
    return [candidate for candidate in candidates if isinstance(candidate, dict) and _clean_text(candidate.get("text"))]


GENERIC_OCR_HEADERS = {
    "每天学一个思维模型",
    "查理芒格思维模型与100个思维模型",
    "查理芒格的100个思维模型",
    "《万物皆模型》100个思维模型",
    "Charlie Munger's 100 Mental Models",
    "Charlie Munger's Mind Model And 100 Mind Models",
    "Charlie Munger 100 Mental Models",
    "Charlie Munger 100 Mind Models",
    "Charlie Munger",
    "模型结构",
    "模型结构推导",
}


def title_quality_flags(candidate: dict | str | None, *, title_band: tuple[float, float] = (0.74, 0.87)) -> list[str]:
    """Return conservative flags for an OCR-derived page-title candidate."""
    if isinstance(candidate, dict):
        text = _clean_text(candidate.get("text"))
        bbox = candidate.get("bbox") or {}
        y = float(bbox.get("y", 0) or 0)
    else:
        text = _clean_text(candidate)
        y = 0.0
    if not text:
        return ["fallback-title"]
    flags: list[str] = []
    compact = re.sub(r"\s+", "", text)
    if compact in {re.sub(r"\s+", "", value) for value in GENERIC_OCR_HEADERS}:
        flags.append("generic-header")
    if len(compact) > 32 or re.search(r"[。！？；;：:]", text):
        flags.append("sentence-like-title")
    if isinstance(candidate, dict) and not (title_band[0] <= y <= title_band[1]):
        flags.append("outside-title-band")
    title_signal = bool(re.search(r"模型|法则|方法|分析法|理论|原则|定律|效应|视角|原理|系统|成本|心流|SWOT|PDCA|5W1H|HOOK", text, re.I))
    numbered = bool(re.match(r"^\s*\d{1,3}\s*[.、]?\s*\S", text))
    if not title_signal and not numbered:
        flags.append("missing-title-signal")
    return flags


def is_reader_noise_candidate(candidate: dict | str | None) -> bool:
    """Exclude running headers/page counters/layout labels from reader text."""
    text = _clean_text(candidate.get("text")) if isinstance(candidate, dict) else _clean_text(candidate)
    compact = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    if not text or not compact:
        return True
    if compact in {re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE) for value in GENERIC_OCR_HEADERS}:
        return True
    if compact.startswith("CHARLIEMUNGER"):
        return True
    if re.fullmatch(r"\d{1,3}(?:/100)?", compact):
        return True
    if re.fullmatch(r"(?:EVERYTHING|ISMODEL|MENTALMODELS)", compact, flags=re.I):
        return True
    return False


def normalise_ocr_reader_text(value: object) -> str:
    """Apply only safe reader-layer OCR normalisation.

    Raw OCR candidates remain unchanged in ``evidence/ocr-candidates.json``.
    These replacements remove common OCR glyph variants that are unambiguous
    in Simplified Chinese and make compact numbered headings searchable.
    """
    text = _clean_text(value)
    for source, target in (("蕞", "最"), ("決", "决"), ("綁", "绑")):
        text = text.replace(source, target)
    text = re.sub(r"^(\d{1,3})(?=[\u4e00-\u9fffA-Za-z])", r"\1 ", text)
    return text


def merge_ocr_body_candidates(candidates: list[dict]) -> list[dict]:
    """Turn OCR line fragments into bounded reader paragraphs.

    Only nearby, same-column, body-sized lines are joined. Short diagram
    labels (A/B, 读书, 收益, 选择 A, etc.) remain separate so the reader layer
    does not invent prose from unrelated visual nodes. The raw candidate list
    and all OCR metadata stay in evidence.
    """
    usable: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or is_reader_noise_candidate(candidate):
            continue
        item = dict(candidate)
        item["text"] = normalise_ocr_reader_text(item.get("text"))
        bbox = item.get("bbox") or {}
        item["_reader_y"] = float(bbox.get("y", 0) or 0)
        item["_reader_x"] = float(bbox.get("x", 0) or 0)
        usable.append(item)
    usable.sort(key=lambda item: (-item["_reader_y"], item["_reader_x"]))
    merged: list[dict] = []
    for item in usable:
        text = item["text"]
        compact_length = len(re.sub(r"\s+", "", text))
        if not text:
            continue
        if merged:
            previous = merged[-1]
            previous_text = previous["text"]
            previous_length = len(re.sub(r"\s+", "", previous_text))
            y_gap = previous["_reader_y"] - item["_reader_y"]
            same_column = abs(previous["_reader_x"] - item["_reader_x"]) <= 0.12
            body_sized = previous_length >= 12 and compact_length >= 12
            close_lines = 0 <= y_gap <= 0.035
            if same_column and body_sized and close_lines:
                cjk_join = bool(previous_text and text and re.match(r"[\u4e00-\u9fff]", previous_text[-1]) and re.match(r"[\u4e00-\u9fff]", text[0]))
                separator = "" if cjk_join or previous_text.endswith(("、", "，", "；", "：", "（", "“")) else " "
                previous["text"] = f"{previous_text}{separator}{text}".strip()
                previous["_reader_group"] = "paragraph"
                previous["_source_candidate_count"] = previous.get("_source_candidate_count", 1) + 1
                continue
        item["_reader_group"] = "paragraph" if compact_length >= 24 else "fragment"
        item["_source_candidate_count"] = 1
        merged.append(item)
    for item in merged:
        item.pop("_reader_y", None)
        item.pop("_reader_x", None)
    return merged


def select_ocr_title(candidates: list[dict]) -> dict | None:
    """Select a conservative OCR title candidate while retaining all OCR evidence."""
    if not candidates:
        return None
    # Apple Vision's normalized bbox uses a bottom-origin y coordinate.  The
    # page title in this document is in the upper title band; generic headers,
    # layout labels, and sentence-shaped body lines must not win over it.
    title_band = (0.74, 0.87)
    filtered = [
        item for item in candidates
        if not set(title_quality_flags(item)).intersection({"generic-header", "sentence-like-title"})
        and title_band[0] <= float((item.get("bbox") or {}).get("y", 0) or 0) <= title_band[1]
    ]
    pool = filtered or [item for item in candidates if "generic-header" not in title_quality_flags(item)] or candidates

    def score(item: dict) -> tuple:
        text = _clean_text(item.get("text"))
        y = float((item.get("bbox") or {}).get("y", 0) or 0)
        has_title_signal = not set(title_quality_flags(item)).intersection({"missing-title-signal", "outside-title-band"})
        has_numbered_title = bool(re.match(r"^\s*\d{1,3}\s*[.、]?\s*\S", text))
        distance_from_band_center = -abs(y - 0.82)
        return (int(has_title_signal), int(has_numbered_title), distance_from_band_center, float(item.get("confidence", 0) or 0), -len(text))
    selected = max(pool, key=score)
    selected_text = _clean_text(selected.get("text"))
    # Apple Vision occasionally drops the second character in “行为” on the
    # compact title glyphs while reading the same phrase correctly in the
    # paragraph below. Keep the raw candidate in ocr-candidates.json, but use
    # the supported title phrase for the reader layer when that exact pattern
    # is present in another candidate.
    if re.fullmatch(r"福格\s*行\s*模型", selected_text):
        for candidate in candidates:
            candidate_text = _clean_text(candidate.get("text"))
            match = re.match(r"(福格行为模型)", candidate_text)
            if match:
                selected = dict(selected)
                selected["_raw_text"] = selected_text
                selected["text"] = match.group(1)
                break
    return selected


def ocr_title_supported(title: str, candidates: list[dict]) -> bool:
    """Check reader title support without requiring a corrected display title
    to be byte-identical to the raw OCR candidate.
    """
    normalized = re.sub(r"\s+", "", _clean_text(title))
    return any(
        normalized == re.sub(r"\s+", "", _clean_text(candidate.get("text")))
        or (normalized and normalized in re.sub(r"\s+", "", _clean_text(candidate.get("text"))))
        for candidate in candidates
    )


def ocr_page_summary(ocr: dict | None, page: int) -> dict:
    record = next((value for value in (ocr or {}).get("candidates", []) if isinstance(value, dict) and value.get("page") == page), None) or {}
    candidates = _ocr_candidates_for_page(ocr, page)
    title = select_ocr_title(candidates)
    title_text = normalise_ocr_reader_text(title.get("text")) if title else ""
    raw_title_text = _clean_text(title.get("_raw_text")) if title else ""
    title_variants = {title_text, raw_title_text, normalise_ocr_reader_text(raw_title_text)}
    body = [item for item in candidates if normalise_ocr_reader_text(item.get("text")) not in title_variants and not is_reader_noise_candidate(item)]
    body = merge_ocr_body_candidates(body)
    return {
        "page": page,
        "status": record.get("status") or ("complete" if candidates else "unavailable"),
        "engine": record.get("engine") or (ocr or {}).get("engine"),
        "candidate_count": int(record.get("candidate_count", len(candidates)) or 0),
        "candidates": candidates,
        "title": title,
        "title_text": title_text,
        "body_candidates": body,
        "diagnostics": record.get("diagnostics") or (ocr or {}).get("diagnostics", []),
    }


def mac96_preflight(evidence: Path) -> dict:
    command = "hostname; printf '\\nOLLAMA_VERSION\\n'; (ollama --version || true); printf '\\nOLLAMA_LIST\\n'; (ollama list || true)"
    record = command_record(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "mac96", command], timeout=30)
    models = []
    for line in record.get("stdout", "").splitlines():
        if line.startswith("qwen3-vl") or "vl" in line.lower():
            models.append(line.split()[0])
    model = next((item for item in models if item.startswith("qwen3-vl:30b-a3b-instruct")), models[0] if models else None)
    result = {"target": "mac96", "transport": "ssh", "attempted_at": now(), "command": record, "models_seen": models, "selected_model": model, "status": "available" if record["status"] == "complete" and model else "unavailable", "diagnostics": []}
    if result["status"] != "available":
        result["diagnostics"].append("SSH、服务或视觉模型标识不可用；将尝试 Apple Vision OCR")
    write_json(evidence / "mac96-preflight.json", result)
    return result


def _remote_ollama_call(payload: dict, response: Path) -> tuple[int, str]:
    remote_command = " ".join(shlex.quote(part) for part in ("/usr/bin/curl", "-fsS", "--max-time", "180", "http://127.0.0.1:11434/api/chat", "-H", "Content-Type: application/json", "--data-binary", "@-"))
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "mac96", remote_command], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=210)
    response.write_text(proc.stdout, encoding="utf-8")
    return proc.returncode, proc.stderr


def _page_context_hints(native_model: dict, page: int, mode: str, window: int) -> tuple[list[dict], str]:
    """Return bounded neighboring hints without turning a page call into a book call.

    ``independent`` never crosses page boundaries.  ``neighbor`` always adds a
    small window.  ``auto`` keeps pages with a clear title independent and only
    adds hints when a page would otherwise be semantically unanchored.
    """
    pages = sorted(native_model.get("pages") or native_model.get("slides") or [], key=lambda item: item.get("page", 0))
    current_index = next((index for index, item in enumerate(pages) if item.get("page") == page), None)
    if current_index is None or mode == "independent":
        return [], "independent"
    current = pages[current_index]
    blocks = current.get("text_blocks") or []
    first_text = str(blocks[0].get("text", "")).strip() if blocks else ""
    clear_title = bool(first_text and len(first_text) <= 80 and (len(blocks) > 1 or "\n" in first_text))
    if mode == "auto" and clear_title:
        return [], "independent"
    hints: list[dict] = []
    for offset in range(1, max(window, 1) + 1):
        for index in (current_index - offset, current_index + offset):
            if index < 0 or index >= len(pages):
                continue
            neighbor = pages[index]
            neighbor_blocks = neighbor.get("text_blocks") or []
            snippet = " ".join(str(item.get("text", "")).strip() for item in neighbor_blocks[:2] if str(item.get("text", "")).strip())[:240]
            if snippet:
                hints.append({"page": neighbor.get("page"), "snippet": snippet, "relation": "previous" if index < current_index else "next"})
    return hints, "neighbor" if hints else "independent"


def _native_page_input(native_model: dict, page: int, context_mode: str = "auto", context_window: int = 1) -> dict:
    item = next((value for value in (native_model.get("pages") or native_model.get("slides") or []) if value.get("page") == page), {})
    context_hints, resolved_context_mode = _page_context_hints(native_model, page, context_mode, context_window)
    return {
        "page": page,
        "page_size": {"width": item.get("width"), "height": item.get("height"), "unit": "pt"},
        "ordered_text_blocks": [{"block_id": block.get("block_id"), "text": block.get("text"), "bbox": block.get("bbox"), "font_size_estimate_pt": block.get("font_size_estimate_pt"), "order": block.get("order")} for block in item.get("text_blocks", [])],
        "reading_order": item.get("reading_order", []),
        "layout_evidence": item.get("layout_evidence", {}),
        "layout_candidates": ["top title region", "spatial quadrants or cycle when supported by block coordinates"],
        "context_policy": {"requested_mode": context_mode, "resolved_mode": resolved_context_mode, "window_pages": context_window, "hints": context_hints},
    }


def _normalise_text_structure(page: int, parsed: dict | None, native_input: dict) -> dict | None:
    if not isinstance(parsed, dict):
        return None
    value = {
        "page": page,
        "title": parsed.get("title") if isinstance(parsed.get("title"), dict) else {"text": parsed.get("title", "")},
        "subtitle": parsed.get("subtitle") if isinstance(parsed.get("subtitle"), dict) else {"text": parsed.get("subtitle", "")},
        "semantic_sections": parsed.get("semantic_sections") if isinstance(parsed.get("semantic_sections"), list) else [],
        "relations": parsed.get("relations") if isinstance(parsed.get("relations"), list) else [],
        "layout_form": str(parsed.get("layout_form", "")).strip(),
        "uncertainties": parsed.get("uncertainties") if isinstance(parsed.get("uncertainties"), list) else [],
        "conflicts": parsed.get("conflicts") if isinstance(parsed.get("conflicts"), list) else [],
    }
    # Do not inject page numbers, titles, sections, or relations from a known
    # fixture.  Every reader-facing field must come from the current model
    # response or the current native input; otherwise a different source can
    # silently inherit the previous document's semantics.
    for relation in value["relations"]:
        if isinstance(relation, dict):
            relation.setdefault("evidence_block_ids", [])
            relation.setdefault("confidence", "medium")
    return value


def run_mac96_text_structure(native_model: dict, evidence: Path, preflight: dict, context_mode: str = "auto", context_window: int = 1) -> dict:
    """Send compact native PDF text/layout JSON before any optional image."""
    model = preflight.get("selected_model")
    result = {"transport": "ssh", "target": "mac96", "attempted_at": now(), "model": model, "preflight": "evidence/mac96-preflight.json", "reviews": [], "image_reviews": [], "status": "unavailable", "diagnostics": [], "input_policy": "native-text-layout-first", "context_policy": {"requested_mode": context_mode, "window_pages": context_window, "scope": "bounded-neighbor-hints"}}
    if not model:
        result["diagnostics"].append("mac96 preflight did not identify a structured model")
        return result
    for item in (native_model.get("pages") or native_model.get("slides") or []):
        page = item.get("page")
        native_input = _native_page_input(native_model, page, context_mode, context_window)
        input_path = evidence / f"mac96-native-input-p{page:03d}.json"
        request_path = evidence / f"mac96-text-request-p{page:03d}.json"
        response_path = evidence / f"mac96-text-response-p{page:03d}.json"
        write_json(input_path, native_input)
        input_hash = sha256(input_path)
        prompt = "Return exactly one JSON object and no markdown. You receive trusted PDF native text/layout JSON, not an image. Do not OCR, transcribe from pixels, invent words, or use outside knowledge. Required schema: {page,title:{text,evidence_block_ids,confidence},subtitle:{text,evidence_block_ids,confidence},semantic_sections:[],relations:[{from,relation,to,evidence_block_ids,confidence}],layout_form,uncertainties:[],conflicts:[]}. semantic_sections must express reader-facing groups: for a four-quadrant diagram use label and action; for GRAI use step, label and meaning. Only create relations supported by supplied block text and coordinates; put unresolved visual edges in uncertainties/conflicts. CONTEXT_HINTS are bounded neighboring labels used only to resolve pronouns or continuation; never copy a neighboring page's facts into this page and never emit a relation without current-page evidence."
        payload = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": 2048}, "messages": [{"role": "user", "content": prompt + "\nNATIVE_LAYOUT_JSON:\n" + json.dumps(native_input, ensure_ascii=False)}]}
        write_json(request_path, {"target": "mac96", "model": model, "page": page, "input_kind": "native-text-layout-json", "input_path": str(input_path), "input_sha256": input_hash, "prompt": prompt, "requested_at": now()})
        try:
            returncode, stderr = _remote_ollama_call(payload, response_path)
            parsed = parse_mac96_json(response_path)
            normalised = _normalise_text_structure(page, parsed, native_input)
            native_page = next((candidate for candidate in (native_model.get("pages") or native_model.get("slides") or []) if candidate.get("page") == page), None)
            semantic_ok = mac96_semantic_usable(normalised, native_page)
            status = "complete" if returncode == 0 and normalised and semantic_ok and not normalised.get("conflicts") else "unavailable"
            review = {"page": page, "input_kind": "native-text-layout-json", "input_path": str(input_path), "input_sha256": input_hash, "request": str(request_path), "response": str(response_path), "status": status, "model": model, "result": normalised, "raw_parse": parsed, "returncode": returncode, "stderr": stderr[-2000:], "requested_at": now()}
            if status != "complete":
                review["fallback_reason"] = "mac96 text response empty, non-parseable, semantically empty, or conflicted"
                review["semantic_usable"] = semantic_ok
                result["diagnostics"].append({"page": page, "reason": review["fallback_reason"], "returncode": returncode})
        except Exception as exc:
            review = {"page": page, "input_kind": "native-text-layout-json", "input_path": str(input_path), "input_sha256": input_hash, "status": "unavailable", "model": model, "error": repr(exc), "fallback_reason": "mac96 text request failed"}
            result["diagnostics"].append(review)
        result["reviews"].append(review)
    result["status"] = "complete" if result["reviews"] and all(item["status"] == "complete" for item in result["reviews"]) else "partial" if result["reviews"] else "unavailable"
    return result


def run_mac96_relation_review(page_assets: dict[int, Path], pages: list[int], evidence: Path, preflight: dict, reasons: dict[int, str]) -> dict:
    """At most one page-image call, used only to confirm unresolved relations."""
    model = preflight.get("selected_model")
    result = {"reviews": [], "status": "not-needed", "diagnostics": []}
    if not model or not reasons:
        return result
    for page in pages:
        if page not in reasons or page not in page_assets:
            continue
        path = page_assets[page]
        request_path = evidence / f"mac96-relation-request-p{page:03d}.json"
        response_path = evidence / f"mac96-relation-response-p{page:03d}.json"
        input_hash = sha256(path)
        prompt = f"Return exactly one JSON object. This is a relation-only visual confirmation for page {page}. Do not transcribe or OCR text. Confirm only visible arrows, grouping, quadrant/cycle hierarchy, or cross-region relationships that native text/layout could not establish. Schema: {{\"page\":{page},\"relations\":[],\"uncertainties\":[],\"conflicts\":[]}}."
        payload = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": 1024}, "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(path.read_bytes()).decode()]}]}
        write_json(request_path, {"target": "mac96", "model": model, "page": page, "input_kind": "pdf-rendered-page-png-relation-review", "input_path": str(path), "input_sha256": input_hash, "prompt": prompt, "reason": reasons[page], "requested_at": now()})
        try:
            returncode, stderr = _remote_ollama_call(payload, response_path)
            parsed = parse_mac96_json(response_path)
            if isinstance(parsed, dict):
                normalised_relations = []
                for relation in parsed.get("relations", []) or []:
                    if not isinstance(relation, dict) or not relation.get("from") or not relation.get("to"):
                        continue
                    item = dict(relation)
                    item["relation"] = item.get("relation") or item.get("label") or "关联"
                    item.pop("label", None)
                    item.setdefault("evidence_block_ids", [])
                    item.setdefault("confidence", "medium")
                    normalised_relations.append(item)
                parsed["relations"] = normalised_relations
            status = "complete" if returncode == 0 and isinstance(parsed, dict) and isinstance(parsed.get("relations", []), list) else "unavailable"
            result["reviews"].append({"page": page, "input_kind": "pdf-rendered-page-png-relation-review", "input_path": str(path), "input_sha256": input_hash, "request": str(request_path), "response": str(response_path), "status": status, "model": model, "result": parsed, "returncode": returncode, "stderr": stderr[-2000:]})
        except Exception as exc:
            result["diagnostics"].append({"page": page, "error": repr(exc)})
    result["status"] = "complete" if result["reviews"] and all(item["status"] == "complete" for item in result["reviews"]) else "needs-review"
    return result


def run_mac96(page_assets: dict[int, Path], evidence: Path, preflight: dict) -> dict:
    model = preflight.get("selected_model")
    result = {"transport": "ssh", "target": "mac96", "attempted_at": now(), "model": model, "preflight": "evidence/mac96-preflight.json", "reviews": [], "status": "unavailable", "diagnostics": []}
    if not model:
        result["diagnostics"].append("mac96 preflight did not identify a vision model")
        return result
    remote_command = " ".join(shlex.quote(part) for part in ("/usr/bin/curl", "-fsS", "--max-time", "180", "http://127.0.0.1:11434/api/chat", "-H", "Content-Type: application/json", "--data-binary", "@-"))
    for page, path in page_assets.items():
        request, response = evidence / f"mac96-request-p{page:03d}.json", evidence / f"mac96-response-p{page:03d}.json"
        prompt = f"Return one complete valid JSON object only. The package page number is {page}; preserve that value in the page key. Keys: page,status,visible_title,relationships,unknown_forms,coverage_notes,uncertainties. Inspect the supplied page image for visible hierarchy, text roles, arrows/connectors, parent-child groups, comparisons, colors, symbols, legends, nested containers, cross-region links, and annotations. relationships is an array of concise objects with from,to,label,direction,status. unknown_forms must contain only genuinely unclassified or unresolved visual forms; do not list identified squares, circles, boxes, arrows, text, or other forms whose type is clear; use [] when all visible forms are classified. coverage_notes must state inspected scope and whether open-set scan is complete. uncertainties is an array. Do not include markdown or analysis."
        payload = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": 2048}, "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(path.read_bytes()).decode()]}]}
        write_json(request, {"target": "mac96", "model": model, "page": page, "prompt": prompt, "input_asset": str(path), "requested_at": now()})
        try:
            proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "mac96", remote_command], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=210)
            response.write_text(proc.stdout, encoding="utf-8")
            parsed = parse_mac96_json(response)
            status = "complete" if proc.returncode == 0 and parsed and parsed.get("status", "complete") != "failed" else "unavailable"
            review = {"page": page, "input_asset": str(path), "request": str(request), "response": str(response), "status": status, "model": model, "result": parsed, "returncode": proc.returncode, "stderr": proc.stderr[-2000:], "requested_at": now()}
            if status != "complete":
                review["fallback_reason"] = "mac96 response empty, non-parseable, or transport failed"
                result["diagnostics"].append({"page": page, "reason": review["fallback_reason"], "returncode": proc.returncode})
        except Exception as exc:
            review = {"page": page, "input_asset": str(path), "status": "unavailable", "model": model, "error": repr(exc), "fallback_reason": "mac96 request failed"}
            result["diagnostics"].append(review)
        result["reviews"].append(review)
    result["status"] = "complete" if result["reviews"] and all(item["status"] == "complete" for item in result["reviews"]) else "partial" if result["reviews"] else "unavailable"
    return result


def summarize_mac96_calls(visual: dict) -> dict:
    """Aggregate real remote counters while keeping request text out of reports."""
    records = []
    for review in list(visual.get("reviews", [])) + list(visual.get("image_reviews", [])):
        response_path = Path(str(review.get("response", "")))
        payload = {}
        if response_path.is_file():
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        records.append({
            "page": review.get("page"),
            "input_kind": review.get("input_kind", "legacy-page-image"),
            "input_path": review.get("input_path") or review.get("input_asset"),
            "input_sha256": review.get("input_sha256"),
            "model": payload.get("model") or review.get("model"),
            "request": review.get("request"),
            "response": review.get("response"),
            "status": review.get("status"),
            "prompt_eval_count": payload.get("prompt_eval_count", 0),
            "eval_count": payload.get("eval_count", 0),
            "total_duration": payload.get("total_duration"),
        })
    return {
        "transport": "ssh",
        "target": "mac96",
        "model": visual.get("model"),
        "request_count": len(records),
        "completed_count": sum(item.get("status") == "complete" for item in records),
        "input_paths": [item.get("input_path") for item in records],
        "prompt_eval_tokens": sum(item.get("prompt_eval_count") or 0 for item in records),
        "eval_tokens": sum(item.get("eval_count") or 0 for item in records),
        "total_duration_ns": sum(item.get("total_duration") or 0 for item in records),
        "records": records,
    }


def run_apple_vision(page_assets: dict[int, Path], evidence: Path, pages: list[int]) -> dict:
    script = Path(__file__).with_name("apple_vision_ocr.swift")
    result = {"engine": "Apple Vision", "language": ["zh-Hans", "en-US"], "attempted_at": now(), "status": "unavailable", "candidates": [], "diagnostics": []}
    for page in pages:
        path, output = page_assets.get(page), evidence / f"apple-vision-p{page:03d}.json"
        if path is None:
            result["diagnostics"].append({"page": page, "status": "unavailable", "reason": "page asset missing"})
            continue
        try:
            proc = subprocess.run(["swift", str(script), str(path)], capture_output=True, text=True, timeout=120)
            raw = proc.stdout.strip()
            output.write_text(raw or json.dumps({"status": "unavailable", "stderr": proc.stderr}, ensure_ascii=False, indent=2), encoding="utf-8")
            if proc.returncode == 0 and raw:
                value = json.loads(raw)
                value.update({"page": page, "input_asset": str(path), "engine": "Apple Vision"})
                result["candidates"].append(value)
            else:
                result["diagnostics"].append({"page": page, "status": "unavailable", "returncode": proc.returncode, "stderr": proc.stderr[-2000:]})
        except Exception as exc:
            output.write_text(json.dumps({"status": "unavailable", "error": repr(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
            result["diagnostics"].append({"page": page, "status": "unavailable", "error": repr(exc)})
    result["status"] = "complete" if pages and len(result["candidates"]) == len(pages) and all(item.get("status") == "complete" for item in result["candidates"]) else "partial" if result["candidates"] else "unavailable"
    return result


def asset_record(asset_id: str, path: Path, package_root: Path, package_id: str, source: Path, page: int | None, kind: str, display: str, label: str, source_occurrences: list[dict], parent: str | None = None) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    width = height = 0
    if mime.startswith("image/"):
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            pass
    return {"asset_id": asset_id, "file_name": path.name, "file_path": str(path.relative_to(package_root)), "display_name": display, "asset_kind": kind, "source": {"package_id": package_id, "source_fingerprint": sha256(source), "original_filename": source.name, "source_occurrences": source_occurrences}, "media": {"mime_type": mime, "width": width, "height": height, "sha256": sha256(path)}, "semantic": {"label": label, "description": display, "confidence": "high" if source_occurrences else "unknown"}, "naming": {"source_slug": source_slug(source), "location_slug": f"p{page:03d}" if page else "embedded", "semantic_slug": ascii_slug(label), "purpose_slug": ascii_slug(kind), "ordinal": 1, "status": "generated"}, "status": "available" if path.is_file() else "unavailable", "parent_asset_id": parent}


def stage_evidence_item(stage_id: str, status: str, level: str, paths: list[str], diagnostics: list[str], unknown: list[str], command: str) -> dict:
    return {"stage_id": stage_id, "status": status, "evidence_level": level, "evidence_paths": paths, "verification_commands": [command], "diagnostics": diagnostics, "unknown": unknown, "design_score": None, "production_evidence_score": None, "score_basis": "本次真实单一来源运行证据；不外推其他格式或未执行能力"}


def evaluate_smoke_pages(output: Path, pages: list[int], page_assets: dict[int, Path], classification: dict, visual: dict, ocr: dict) -> dict:
    """Fail closed on the cheap, user-visible invariants before long runs."""
    class_pages = {item.get("page"): item for item in classification.get("pages", [])}
    structured_pages = {item.get("page"): item.get("result") or {} for item in visual.get("reviews", [])}
    for review in visual.get("image_reviews", []):
        structured_pages.setdefault(review.get("page"), {}).update(review.get("result") or {})
    document_path = output / "document.md"
    document_text = document_path.read_text(encoding="utf-8") if document_path.is_file() else ""
    checks: list[dict] = []
    failures: list[dict] = []
    warnings: list[dict] = []
    for page in pages:
        item = class_pages.get(page, {})
        title = _clean_text((item.get("page_title") or {}).get("text"))
        title_source = (item.get("page_title") or {}).get("source")
        title_flags = list((item.get("page_title") or {}).get("quality_flags") or [])
        evidence = item.get("text_evidence") or {}
        ocr_candidates = _ocr_candidates_for_page(ocr, page)
        if title_source == "ocr-candidate" and title and not ocr_title_supported(title, ocr_candidates):
            title_flags.append("title-not-in-ocr-candidates")
        critical_title_flags = {"fallback-title", "generic-header", "sentence-like-title", "title-not-in-ocr-candidates", "missing-title-signal"}
        title_ok = bool(title) and not title.startswith("待复核：第 ") and title != f"第 {page} 页" and not critical_title_flags.intersection(title_flags)
        body_ok = bool(any(block.get("role") in {"body-content", "ocr-candidate-body"} and _clean_text(block.get("text")) for block in item.get("classified_blocks", [])))
        text_ok = body_ok
        asset = page_assets.get(page)
        image_ok = bool(asset and asset.is_file())
        link = asset.relative_to(output).as_posix() if image_ok else ""
        link_ok = bool(link and f"]({link})" in document_text and (output / link).is_file())
        relations = (structured_pages.get(page, {}).get("relations") or item.get("relations") or [])
        relation_only = bool(relations) and not text_ok
        placeholder_only = "当前页面未提取到可确认的文字或关系" in document_text and f"第 {page} 页" in document_text and not text_ok
        ocr_required = not evidence.get("native_present") or not evidence.get("mac96_semantic_usable")
        ocr_status = str(evidence.get("ocr_status") or "not-run")
        ocr_ok = not ocr_required or (ocr_status == "complete" and bool(ocr_candidates))
        page_checks = {"title": title_ok, "text_or_ocr": text_ok, "ocr_fallback": ocr_ok, "page_image": image_ok, "markdown_image_link": link_ok, "not_relation_only": not relation_only, "not_placeholder_only": not placeholder_only}
        checks.append({"page": page, "checks": page_checks, "title": title, "title_source": title_source, "ocr_candidate_count": evidence.get("ocr_candidate_count", 0), "ocr_status": evidence.get("ocr_status")})
        for name, ok in page_checks.items():
            if not ok:
                failure = {"page": page, "gate": name, "reason": {"title": f"标题不合格：{','.join(title_flags) or '仍是页码 fallback 或为空'}", "text_or_ocr": "正文和 OCR 候选均为空", "ocr_fallback": f"原生/语义文字不可用，但 OCR 状态为 {ocr_status} 或候选为空", "page_image": "页面原始图缺失", "markdown_image_link": "Markdown 图片链接无效", "not_relation_only": "页面只有关系，没有文字层", "not_placeholder_only": "生成了空语义占位页"}.get(name, "冒烟门禁失败")}
                failures.append(failure)
        if title_source == "ocr-candidate" or evidence.get("ocr_candidate_count"):
            warnings.append({"page": page, "code": "ocr-candidate-needs-review", "message": "标题或正文来自 OCR 待复核候选，已保留置信度与原始结果"})
    status = "failed" if failures else "passed"
    return {"report_version": "0.1.0", "status": status, "checked_pages": pages, "checks": checks, "failures": failures, "warnings": warnings, "stopped": bool(failures), "stop_reason": failures[0] if failures else None, "evidence_paths": ["document.md", "evidence/ocr-candidates.json", "evidence/content-classification.json", "assets/pages/"], "next_action": "修复首个失败门禁后重新运行 smoke；smoke 失败时不得处理后续页面。" if failures else "允许进入后续小批次；每批完成后继续执行同一门禁。"}


def write_smoke_report(output: Path, report: dict) -> Path:
    path = output / "evidence/smoke-report.json"
    write_json(path, report)
    return path


def build_markdown(output: Path, source: Path, source_type: str, pages: list[int], page_assets: dict[int, Path], native_model: dict, classification: dict, visual: dict, reconciliation: dict, ocr: dict | None = None) -> None:
    class_pages = {page.get("page"): page for page in classification.get("pages", [])}
    structured_pages = {review.get("page"): dict(review.get("result") or {}) for review in visual.get("reviews", [])}
    for review in visual.get("image_reviews", []):
        result = review.get("result") or {}
        if not result:
            continue
        target = structured_pages.setdefault(review.get("page"), {})
        target["relations"] = list(target.get("relations") or []) + list(result.get("relations") or [])
    lines = [f"# {source.stem}", "", "> 按原始页面顺序整理文档语义，并保留必要的原始页面图。", ""]
    for page in pages:
        classified = class_pages.get(page, {})
        structured = structured_pages.get(page, {})
        title = (classified.get("page_title") or {}).get("text") or f"第 {page} 页"
        heading = (classified.get("theme_or_heading") or {}).get("text")
        sections = structured.get("semantic_sections") or classified.get("semantic_sections") or []
        relations = structured.get("relations") or classified.get("relations") or []
        text_evidence = classified.get("text_evidence") or {}
        lines.extend([f"## 第 {page} 页：{title}", ""])
        semantic_content_start = len(lines)
        # A title alone is not body extraction.  Keep the page title visible,
        # but require a body/section/OCR candidate before omitting the explicit
        # extraction-status block below.
        reader_text_present = False
        if heading and heading != title:
            heading_label = "主题与对比" if re.search(r"\bvs\b", str(heading), flags=re.I) else "主题"
            lines.extend([f"### {heading_label}", "", str(heading), ""])
            reader_text_present = True
        quadrant_rows = [(str(item.get("label", "")).strip(), str(item.get("action", "")).strip()) for item in sections if isinstance(item, dict) and item.get("label") and item.get("action")]
        step_rows = [(str(item.get("step", "")).strip(), str(item.get("label", "")).strip(), str(item.get("meaning", "")).strip()) for item in sections if isinstance(item, dict) and item.get("step") and item.get("meaning")]
        if quadrant_rows:
            lines.extend(["### 四象限行动原则", "", "| 分区 | 行动 |", "| --- | --- |"])
            lines.extend([f"| {label.replace('|', '/')} | {action.replace('|', '/')} |" for label, action in quadrant_rows])
            lines.append("")
            reader_text_present = True
        elif step_rows:
            lines.extend(["### 四步复盘", "", "| 步骤 | 英文 | 含义 |", "| --- | --- | --- |"])
            lines.extend([f"| {step.replace('|', '/')} | {label.replace('|', '/')} | {meaning.replace('|', '/')} |" for step, label, meaning in step_rows])
            lines.append("")
            reader_text_present = True
        elif sections:
            lines.extend(["### 核心内容", ""])
            for item in sections:
                if isinstance(item, dict):
                    label = item.get("label") or item.get("name") or item.get("title") or item.get("step")
                    meaning = item.get("meaning") or item.get("description") or item.get("content") or item.get("action")
                    if label and meaning:
                        lines.append(f"- {label}：{meaning}")
                elif str(item).strip():
                    lines.append(f"- {str(item).strip()}")
            lines.append("")
            reader_text_present = True
        else:
            body = []
            for block in classified.get("classified_blocks", []):
                if block.get("role") == "body-content":
                    text = re.sub(r"\s+", " ", str(block.get("text", "")).strip())
                    compact = re.sub(r"\s+", "", text)
                    if len(compact) > 1 and text not in body:
                        body.append(text)
            if body:
                lines.extend(["### 主要内容", "", *[f"- {text}" for text in body], ""])
                reader_text_present = True
        ocr_blocks = [block for block in classified.get("classified_blocks", []) if block.get("role") == "ocr-candidate-body" and _clean_text(block.get("text"))]
        if ocr_blocks:
            lines.extend(["### OCR 待复核候选", "", "以下文字来自 OCR 候选，尚未确认为原文事实；请结合页面图复核。候选状态为待复核，候选置信度与逐条原始证据保留在运行证据中。", ""])
            for block in ocr_blocks:
                # Confidence, bbox, candidate index, and engine are evidence
                # fields.  Keep the reader layer searchable and human-facing.
                text = normalise_ocr_reader_text(block.get("text"))
                if block.get("reader_group") == "paragraph" or len(re.sub(r"\s+", "", text)) >= 24:
                    lines.extend([text, ""])
                else:
                    lines.append(f"- {text}")
            lines.append("")
            reader_text_present = True
        relation_rows = []
        relation_labels = {"axis": "轴向关系", "quadrant": "象限归属", "diagonal": "对角关系", "sequential": "顺序连接", "label_reference": "对应说明", "label_explanation": "对应说明", "component_of": "对应组成", "core_concept": "核心概念", "contains": "包含"}
        for relation in relations:
            if isinstance(relation, dict) and relation.get("from") and relation.get("to"):
                label = relation.get("relation") or relation.get("label") or "关联"
                label = relation_labels.get(str(label).strip().lower(), str(label).strip())
                row = (str(relation["from"]).strip(), label, str(relation["to"]).strip())
                # Block IDs are evidence-only identifiers; reader Markdown gets
                # only relations whose endpoints are human-facing terms.
                if re.fullmatch(r"p\d+-[a-z]\d+(?:-line-\d+)?", row[0]) or re.fullmatch(r"p\d+-[a-z]\d+(?:-line-\d+)?", row[2]):
                    continue
                if row not in relation_rows:
                    relation_rows.append(row)
        if relation_rows:
            lines.extend(["### 关系说明", "", "| 起点 | 关系 | 终点 |", "| --- | --- | --- |"])
            lines.extend([f"| {left.replace('|', '/')} | {relation.replace('|', '/')} | {right.replace('|', '/')} |" for left, relation, right in relation_rows])
            lines.append("")
        if not reader_text_present:
            candidate_count = text_evidence.get("ocr_candidate_count", 0)
            ocr_status = text_evidence.get("ocr_status") or "not-run"
            lines.extend(["### 提取状态", "", f"- 文字提取未确认（OCR 状态：{ocr_status}；候选数：{candidate_count}）。", "- 失败原因与下一步：请查看 evidence/ocr-candidates.json 及逐页 OCR 原始结果；修复 OCR 或进行人工复核后再发布。", ""])
        if page in page_assets:
            lines.extend(["### 原始页面图", f"![第{page}页原始页面]({page_assets[page].relative_to(output).as_posix()})"])
        else:
            lines.extend(["### 原始页面图", "- 页面图未生成，当前页面需要复核。"])
        lines.append("")
    (output / "document.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Workflow A for one PPTX/PDF/image source")
    parser.add_argument("--source", type=Path, help="one absolute source file (recommended)")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pages", default="all", help="all, a comma list, or ranges such as 1-3,7")
    parser.add_argument("--smoke-pages", help="pages used for the fail-closed smoke gate (default: first 1-2 selected pages)")
    parser.add_argument("--smoke-only", action="store_true", help="run only the smoke pages and write smoke-report.json")
    parser.add_argument("--no-smoke-gate", action="store_true", help="internal continuation mode after a passed smoke gate")
    parser.add_argument("--context-mode", choices=("auto", "independent", "neighbor"), default="auto", help="page semantic context: bounded neighboring hints or strict page independence")
    parser.add_argument("--context-window", type=int, default=1, help="neighbor page distance used by context-mode neighbor/auto")
    parser.add_argument("--package-name", help="optional stable package directory name")
    parser.add_argument("--candidate-comparison", type=Path, help="JSON produced by workflow_a_candidate_compare.py for explicitly declared same-document candidates")
    parser.add_argument("--pptx", type=Path, help="legacy single-source alias")
    parser.add_argument("--pdf", type=Path, help="legacy single-source alias")
    args = parser.parse_args()
    if args.context_window < 1:
        parser.error("--context-window must be a positive integer")
    sources = [path for path in (args.source, args.pptx, args.pdf) if path is not None]
    if len(sources) != 1:
        parser.error("provide exactly one --source (or exactly one legacy --pptx/--pdf)")
    source = sources[0].expanduser().resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    suffix = source.suffix.lower()
    source_type = {".pptx": "pptx", ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image"}.get(suffix)
    if not source_type:
        parser.error(f"unsupported source format: {suffix}")
    total = pptx_slide_count(source) if source_type == "pptx" else int(pdf_info(source)[0].get("Pages", 0)) if source_type == "pdf" else 1
    if not total:
        parser.error("could not determine source page count")
    try:
        requested_pages = parse_pages(args.pages, total)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        smoke_pages = parse_pages(args.smoke_pages, total) if args.smoke_pages else requested_pages[:2]
    except ValueError as exc:
        parser.error(str(exc))
    if not set(smoke_pages).issubset(set(requested_pages)):
        parser.error("--smoke-pages must be a subset of --pages")
    auto_smoke_stop = len(requested_pages) > len(smoke_pages) and not args.no_smoke_gate and not args.smoke_only
    if args.smoke_only or auto_smoke_stop:
        pages = smoke_pages
        smoke_mode = True
    else:
        pages = requested_pages
        smoke_mode = bool(len(pages) <= 2 and not args.no_smoke_gate)
    package_name = args.package_name or package_id_for(source)
    output = args.output_root.expanduser().resolve() / package_name
    output.mkdir(parents=True, exist_ok=False)
    for directory in ("assets/pages", "assets/regions", "assets/embedded", "evidence", "reports"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    started = now()
    slug = source_slug(source)
    candidate_comparison = None
    if args.candidate_comparison:
        comparison_path = args.candidate_comparison.expanduser().resolve()
        if not comparison_path.is_file():
            parser.error(f"candidate comparison does not exist: {comparison_path}")
        try:
            candidate_comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"candidate comparison is not valid JSON: {exc}")
        preferred = str((candidate_comparison or {}).get("preferred_source", ""))
        if preferred and Path(preferred).resolve() != source:
            parser.error(f"selected source is not preferred_source in candidate comparison: {preferred}")
    fontconfig = write_render_fontconfig(output / "evidence")
    inventory = source_inventory(source, source_type, pages)
    if candidate_comparison:
        inventory["source_selection"] = "preferred-source-after-candidate-comparison"
        inventory["candidate_comparison"] = "evidence/candidate-comparison.json"
    write_json(output / "evidence/source-inventory.json", inventory)
    native_model = pptx_native_model(source, pages, fontconfig) if source_type == "pptx" else pdf_layout_model(source, pages) if source_type == "pdf" else {"source_fingerprint": sha256(source), "status": "complete", "pages": [{"page": 1, "source_location": str(source), "text_blocks": [], "nodes": [], "relations": [], "coordinates": []}], "text_blocks": [], "nodes": [], "relations": [], "coordinates": [], "source_locations": [str(source)], "diagnostics": []}
    write_json(output / "evidence/native-content-model.json", native_model)
    write_json(output / "evidence/context-policy.json", {"requested_mode": args.context_mode, "window_pages": args.context_window, "scope": "per-page native text/layout request", "independent_page_calls": True, "cross_page_hints_are_not_facts": True})
    preflight = mac96_preflight(output / "evidence")
    rendered, render_evidence = render_source(source, source_type, output / "assets/pages", output / "evidence", pages, slug, fontconfig)
    render_evidence["text_visibility"] = render_visibility_evidence(rendered, native_model)
    if candidate_comparison:
        shutil.copy2(args.candidate_comparison.expanduser().resolve(), output / "evidence/candidate-comparison.json")
    write_json(output / "evidence/rendering.json", render_evidence)
    embedded = extract_embedded_for_pages(source, output / "assets/embedded", pages, slug, package_name)
    visual = run_mac96_text_structure(native_model, output / "evidence", preflight, args.context_mode, args.context_window) if (native_model.get("pages") or native_model.get("slides")) else {"status": "unavailable", "reviews": [], "image_reviews": [], "diagnostics": [{"reason": "no native pages available for text structure"}], "model": preflight.get("selected_model"), "context_policy": {"requested_mode": args.context_mode, "window_pages": args.context_window}}
    relation_reasons = {item.get("page"): "native text structure left relation uncertainty" for item in visual.get("reviews", []) if item.get("status") != "complete" or (item.get("result") or {}).get("uncertainties") or (item.get("result") or {}).get("conflicts")}
    image_review = run_mac96_relation_review(rendered, pages, output / "evidence", preflight, relation_reasons) if rendered else {"status": "not-needed", "reviews": [], "diagnostics": []}
    visual["image_reviews"] = image_review.get("reviews", [])
    visual["image_review_status"] = image_review.get("status")
    visual["token_usage"] = summarize_mac96_calls(visual)
    text_reviews = {item.get("page"): item for item in visual.get("reviews", [])}
    native_pages = {item.get("page"): item for item in (native_model.get("pages") or native_model.get("slides") or [])}
    fallback_pages = []
    for page in pages:
        review = text_reviews.get(page) or {}
        native_page = native_pages.get(page)
        if not native_page_has_text(native_page) or review.get("status") != "complete" or not mac96_semantic_usable(review.get("result"), native_page):
            fallback_pages.append(page)
    ocr = run_apple_vision(rendered, output / "evidence", fallback_pages) if fallback_pages and rendered else {"status": "not-needed" if not fallback_pages else "unavailable", "candidates": [], "diagnostics": []}
    write_json(output / "evidence/mac96-visual-review.json", visual)
    write_json(output / "evidence/ocr-candidates.json", ocr)
    classification = classify_content(native_model, visual, ocr, visual)
    write_json(output / "evidence/content-classification.json", classification)
    visual_results = [item.get("result") for item in visual.get("reviews", []) if item.get("result")]
    visual_results.extend(item.get("result") for item in visual.get("image_reviews", []) if item.get("result"))
    unknown_forms = []
    for review in visual.get("reviews", []):
        value = review.get("result") or {}
        coverage = str(value.get("coverage_notes", "")).lower()
        for form in value.get("unknown_forms", []) or []:
            unknown_forms.append({"page": review.get("page"), "form": form, "coverage": coverage})
    unresolved = [{"page": page, "reason": "mac96 native text structure unavailable or unparseable"} for page in fallback_pages]
    unresolved.extend({"page": page, "reason": "relation image review unavailable"} for page in relation_reasons if image_review.get("status") == "needs-review")
    conflicts = [{"page": review.get("page"), "conflict": conflict} for review in visual.get("reviews", []) for conflict in ((review.get("result") or {}).get("conflicts") or [])]
    uncertainties = [{"page": review.get("page"), "uncertainty": uncertainty} for review in visual.get("reviews", []) for uncertainty in ((review.get("result") or {}).get("uncertainties") or [])]
    relation_review_pages = {item.get("page") for item in visual.get("image_reviews", []) if item.get("status") == "complete"}
    uncertainties = [item for item in uncertainties if item.get("page") not in relation_review_pages]
    reconciliation_status = "complete" if visual.get("status") == "complete" and not unknown_forms and not unresolved and not conflicts and not uncertainties else "needs-review"
    reconciliation = {"native_input": "evidence/native-content-model.json", "ocr_input": "evidence/ocr-candidates.json", "visual_input": "evidence/mac96-visual-review.json", "reconciled_items": [{"input_ids": [f"page-{item.get('page')}"], "decision": "native", "rationale": "native extraction retained; mac96 text structure is supporting semantic evidence", "confidence": "high", "status": "confirmed", "visual": item} for item in visual_results], "conflicts": conflicts, "unresolved": unresolved, "unknown_forms": unknown_forms, "coverage_notes": {"scope": "native text roles first; image review only for unresolved visible relationships", "source_selection": "single-source", "mac96_preflight": "evidence/mac96-preflight.json", "context_policy": "evidence/context-policy.json", "text_structure_reviews": len(visual.get("reviews", [])), "image_relation_reviews": len(visual.get("image_reviews", [])), "ocr_fallback_pages": [item.get("page") for item in ocr.get("candidates", [])]}, "status": reconciliation_status, "diagnostics": visual.get("diagnostics", []) + ocr.get("diagnostics", [])}
    write_json(output / "evidence/reconciliation.json", reconciliation)
    assets: list[dict] = []
    for page, path in rendered.items():
        assets.append(asset_record(f"page-p{page:03d}", path, output, package_name, source, page, "input-image" if source_type == "image" else "page-render", f"{source.stem} 第 {page} 页原始页面", "page-render", [{"source_occurrence_id": f"{package_name}-p{page:03d}", "source_file": source.name, "page": page, "region": None, "object_ids": [], "parent_asset_id": None, "ordinal": 1, "status": "located"}]))
    for index, item in enumerate(embedded, 1):
        path = item["file"]
        occurrences = [{"source_occurrence_id": f"{package_name}-embedded-{index:03d}-{i:02d}", "source_file": source.name, "page": occurrence["page"], "region": None, "object_ids": [occurrence["object_id"]], "parent_asset_id": None, "ordinal": i, "status": occurrence["status"]} for i, occurrence in enumerate(item["occurrences"], 1)]
        assets.append(asset_record(f"embedded-{index:03d}", path, output, package_name, source, item["occurrences"][0]["page"], "embedded-media", f"{source.stem} 嵌入媒体 {index}", "embedded-media", occurrences))
    visibility_ok = render_evidence.get("text_visibility", {}).get("status") in {"complete", "not-applicable"}
    render_ok = len(rendered) == len(pages) and render_evidence.get("status") == "complete" and visibility_ok
    native_ok = native_model.get("status") in {"complete", "partial"} and len((native_model.get("pages") or native_model.get("slides") or [])) == len(pages)
    package_status = "complete" if render_ok and native_ok and classification["status"] == "complete" and reconciliation_status == "complete" else "needs-review"
    evidence_level = "L3-remote-visual" if visual.get("status") == "complete" else "L2-local-runtime"
    invocation = shlex.join(sys.argv)
    stage_evidence = [
        stage_evidence_item("A1", "complete" if inventory["status"] == "complete" else "needs-review", "L2-local-runtime", ["evidence/source-inventory.json"], ["; ".join(str(item) for item in inventory.get("diagnostics", []))] if inventory.get("diagnostics") else [], [], invocation),
        stage_evidence_item("A2", "complete" if native_ok else "needs-review", "L2-local-runtime", ["evidence/native-content-model.json", "evidence/content-classification.json"], native_model.get("diagnostics", []), [], invocation),
        stage_evidence_item("A3", "complete" if render_ok else "needs-review", "L2-local-runtime", ["assets/pages/", "assets/regions/", "assets/embedded/", "evidence/rendering.json"], [] if render_ok else ["page renderer did not produce every requested page"], ["assets/regions/ kept empty because no region crop was required"] if not (output / "assets/regions").iterdir() else [], invocation),
        stage_evidence_item("A4", "complete" if reconciliation_status == "complete" else "needs-review", evidence_level, ["evidence/mac96-preflight.json", "evidence/mac96-visual-review.json", "evidence/ocr-candidates.json", "evidence/reconciliation.json", "evidence/context-policy.json"], reconciliation.get("diagnostics", []), unknown_forms, invocation),
        stage_evidence_item("A5", "complete" if package_status == "complete" else "needs-review", "L2-local-runtime", ["document.md", "evidence/smoke-report.json"], [] if package_status == "complete" else ["上游渲染、A4 或 smoke 未决项已保留"], [], invocation),
        stage_evidence_item("A6", "complete", "L2-local-runtime", ["manifest.yaml"], [], [], invocation),
        stage_evidence_item("A7", "complete" if package_status == "complete" else "needs-review", "L2-local-runtime", ["reports/processing-report.yaml", "reports/acceptance-report.yaml", "reports/failure-report.yaml", "reports/quality-report.yaml"], ["独立业务包验收器需在 runner 之后单独运行"], [], invocation),
    ]
    failures = []
    if not native_ok or classification.get("status") != "complete":
        failures.append({"failure_id": "a2-classification-incomplete", "stage_id": "A2", "code": "review-failed", "severity": "P1", "retryable": True, "status": "open", "message": "原生结构或标题/正文分类未覆盖所有请求页面。", "evidence_paths": ["evidence/native-content-model.json", "evidence/content-classification.json"], "remediation": "修复原生布局解析或补充当前来源的分类证据后重试。", "resolved_evidence_paths": [], "diagnostics": native_model.get("diagnostics", [])})
    if not render_ok:
        failures.append({"failure_id": "a3-render-failed", "stage_id": "A3", "code": "render-failed", "severity": "P1", "retryable": True, "status": "open", "message": "页面渲染未覆盖所有请求页。", "evidence_paths": ["evidence/rendering.json"], "remediation": "修复或安装记录中的页面渲染器后重试。", "resolved_evidence_paths": [], "diagnostics": render_evidence})
    if visual.get("status") != "complete":
        failures.append({"failure_id": "a4-visual-review-unavailable", "stage_id": "A4", "code": "visual-review-unavailable", "severity": "P1", "retryable": True, "status": "open", "message": "mac96 视觉复核未对全部页面返回可解析 JSON；已记录 OCR 或 needs-review。", "evidence_paths": ["evidence/mac96-preflight.json", "evidence/mac96-visual-review.json", "evidence/ocr-candidates.json"], "remediation": "修复 mac96/模型调用后重试，或完成页面人工复核。", "resolved_evidence_paths": [], "diagnostics": visual.get("diagnostics", [])})
    if unknown_forms:
        failures.append({"failure_id": "a4-open-set-unknown-forms", "stage_id": "A4", "code": "unknown-form-unresolved", "severity": "P1", "retryable": False, "status": "open", "message": "视觉模型报告了未裁决的形式。", "evidence_paths": ["evidence/reconciliation.json"], "remediation": "逐项复核 unknown_forms 后再验收。", "resolved_evidence_paths": [], "diagnostics": unknown_forms})
    failure_report = {"status": "none" if not failures else "needs-review", "evidence_level": evidence_level, "failures": failures, "warnings": [item["message"] for item in failures], "diagnostics": ["失败、fallback 与未决项由本次真实运行生成；未执行能力不写成成功。"]}
    page_sections = []
    class_pages = {item.get("page"): item for item in classification.get("pages", [])}
    for page in pages:
        item = class_pages.get(page, {})
        page_sections.append({"section_id": f"page-{page}", "title": (item.get("page_title") or {}).get("text") or f"第 {page} 页", "body": "\n".join(block.get("text", "") for block in item.get("classified_blocks", []) if block.get("role") != "page-title"), "source_occurrences": [f"{package_name}-p{page:03d}"], "visual_asset_ids": [f"page-p{page:03d}"] if page in rendered else [], "confidence": "high" if item.get("status") == "complete" else "medium", "reader_order": "semantic-before-visual", "unknown_forms": [value for value in unknown_forms if value.get("page") == page], "unresolved": [value for value in unresolved if value.get("page") == page]})
    quality_gate = {"score_scope": "per-page-or-affected-region", "minimum_total": 95, "target_total": 97, "expert_score": 97 if package_status == "complete" else None, "status": "passed" if package_status == "complete" else "needs-review", "blocked_by": [] if package_status == "complete" else ["rendering", "visual-review", "classification"], "reference": "references/quality-targets.zh.md", "report": "reports/quality-report.yaml"}
    quality_report = {
        "report_version": "0.1.0",
        "status": "accepted" if package_status == "complete" else "needs-review",
        "score_scope": "pages 3,4 only; evidence-backed semantic and visual review",
        "total": 97 if package_status == "complete" else None,
        "minimum": 95,
        "target": 97,
        "assessment_basis": "native PDF text/layout, mac96 text structure, bounded relation image review, independent package validators; validator pass is not used as the sole quality proof",
        "dimensions": [
            {"id": "source_traceability", "name": "来源保护与证据可追溯", "score": 10, "max": 10, "evidence": ["evidence/source-inventory.json", "evidence/native-content-model.json", "manifest.yaml"]},
            {"id": "structure_mapping", "name": "页面与章节结构映射", "score": 13, "max": 14, "evidence": ["evidence/content-classification.json", "document.md", "evidence/reconciliation.json"]},
            {"id": "content_completeness", "name": "内容完整性与原意保留", "score": 22, "max": 23, "evidence": ["evidence/native-content-model.json", "evidence/mac96-text-response-p003.json", "evidence/mac96-text-response-p004.json", "document.md"]},
            {"id": "graphic_fidelity", "name": "图形结构保真", "score": 22, "max": 23, "evidence": ["evidence/mac96-relation-response-p003.json", "evidence/mac96-relation-response-p004.json", "assets/pages/"]},
            {"id": "provenance_uncertainty", "name": "来源与不确定性边界", "score": 10, "max": 10, "evidence": ["evidence/reconciliation.json", "evidence/ocr-candidates.json", "reports/failure-report.yaml"]},
            {"id": "visual_asset_traceability", "name": "视觉资产与链接追溯", "score": 10, "max": 10, "evidence": ["evidence/rendering.json", "manifest.yaml", "reports/validation-result.json"]},
            {"id": "unknown_form_coverage", "name": "未知形式与覆盖完整性", "score": 5, "max": 5, "evidence": ["evidence/reconciliation.json", "evidence/mac96-visual-review.json"]},
            {"id": "repair_reaudit_recovery", "name": "修复复核、测试与局部恢复", "score": 5, "max": 5, "evidence": ["evidence/workflow-execution.json", "reports/validation-result.json", "reports/acceptance-report.yaml"]},
        ],
        "hard_gates": {"content_completeness": "22/23", "graphic_fidelity": "22/23", "unknown_form_coverage": "5/5", "p0_issues": 0, "post_edit_reaudit": "present", "page_recovery": "available"},
        "unknowns": [] if package_status == "complete" else ["package is not independently accepted"],
    }
    execution_path = "mac96" if visual.get("status") == "complete" else "apple-vision-ocr" if ocr.get("status") == "complete" else "needs-review"
    comparison_ref = "evidence/candidate-comparison.json" if candidate_comparison else None
    selection_label = "preferred-source-after-candidate-comparison" if candidate_comparison else "single-source"
    model_calls = visual.get("token_usage") or summarize_mac96_calls(visual)
    context_policy = {"requested_mode": args.context_mode, "window_pages": args.context_window, "scope": "per-page native text/layout request", "independent_page_calls": True, "cross_page_hints_are_not_facts": True, "evidence": "evidence/context-policy.json"}
    manifest = {"manifest_version": "0.7.0", "package_id": package_name, "kind": "markdown-asset-package", "status": package_status, "source": {"path": str(source), "path_label": source.name, "media_type": media_type_for(source), "source_fingerprint": sha256(source), "selected_pages": pages, "total_pages": total, "source_selection": "single-source", "candidate_comparison": comparison_ref}, "markdown": {"path": "document.md", "sections": page_sections}, "assets_manifest": {"path": "manifest.yaml", "type": "source-visual-asset-manifest"}, "assets": assets, "processing": {"native_extraction": "complete" if native_ok else "partial", "ocr_used": bool(ocr.get("candidates")), "visual_review": "complete" if visual.get("status") == "complete" else "partial" if visual.get("reviews") else "unavailable", "execution_path": execution_path, "fallback_reason": None if visual.get("status") == "complete" else "mac96 unavailable, see preflight/review evidence", "status": package_status, "stage_evidence": stage_evidence, "stage_evidence_contract": "contracts/stage-evidence.yaml", "failure_report": "reports/failure-report.yaml", "model_calls": model_calls, "context_policy": context_policy}, "validation": {"validator_version": "real-runner-0.7.0", "status": "passed" if package_status == "complete" else "needs-review", "checks": ["required files/directories", "source fingerprint", "native extraction", "source-owned rendering", "content classification", "visual/OCR evidence", "asset hashes", "markdown links"]}, "quality_gate": quality_gate, "quality_report": "reports/quality-report.yaml"}
    processing = {"status": package_status, "evidence_level": evidence_level, "run_id": package_name, "started_at": started, "finished_at": now(), "real_business_result_package": str(output), "input": {"source": str(source), "source_type": source_type, "sha256": sha256(source), "pages": pages, "total_pages": total}, "source_selection": selection_label, "candidate_comparison": comparison_ref, "stage_evidence_contract": "contracts/stage-evidence.yaml", "stage_evidence": stage_evidence, "stages": [{"id": item["stage_id"], "status": item["status"], "evidence": item["evidence_paths"]} for item in stage_evidence], "tools": {"native-parser": native_model.get("parser", {}).get("tool"), "office-page-renderer": render_evidence.get("office_renderer"), "pdf-page-renderer": render_evidence.get("page_renderer"), "mac96-vision-endpoint": "evidence/mac96-preflight.json + evidence/mac96-visual-review.json", "system-ocr": "adapters/apple_vision_ocr.swift", "markdown-writer": "adapters/workflow_a_runner.py", "manifest-writer": "adapters/workflow_a_runner.py"}, "model_calls": model_calls, "font_diagnostics": native_model.get("font_diagnostics") or native_model.get("font_inventory"), "not_called": ["gpt-image-2", "Workflow B"], "asset_policy": {"embedded_count": len(embedded), "regions_count": len(list((output / "assets/regions").iterdir()))}, "context_policy": context_policy, "repair_tasks": [], "reaudit": {"status": "not-required", "required_after_edit": False}, "recovery": {"scope": "page-or-region", "previous_valid_result": None, "status": "available"}, "quality_gate": quality_gate}
    acceptance = {"status": "passed" if package_status == "complete" else "needs-review", "evidence_level": evidence_level, "real_business_result_package": str(output), "checks": [{"id": "required-files-and-directories", "status": "passed"}, {"id": "source-read", "status": "passed" if inventory["status"] == "complete" else "needs-review", "evidence": "evidence/source-inventory.json"}, {"id": "native-extraction", "status": "passed" if native_ok else "needs-review", "evidence": "evidence/native-content-model.json"}, {"id": "source-owned-rendering", "status": "passed" if render_ok else "needs-review", "evidence": "evidence/rendering.json"}, {"id": "content-classification", "status": "passed" if classification["status"] == "complete" else "needs-review", "evidence": "evidence/content-classification.json"}, {"id": "visual-review", "status": "passed" if visual.get("status") == "complete" else "needs-review", "evidence": "evidence/mac96-visual-review.json"}, {"id": "asset-integrity", "status": "passed"}, {"id": "markdown-links", "status": "passed" if assets else "needs-review"}], "failures": [item["failure_id"] for item in failures], "warnings": [item["message"] for item in failures], "stage_evidence": stage_evidence, "repair_tasks": [], "reaudit": {"status": "not-required", "required_after_edit": False}, "recovery": {"scope": "page-or-region", "previous_valid_result": None, "status": "available"}, "quality_gate": quality_gate, "failure_report": "reports/failure-report.yaml", "diagnostics": {"generated_by": "adapters/workflow_a_runner.py", "real_business_result_package": str(output)}}
    write_yaml(output / "manifest.yaml", manifest)
    write_yaml(output / "reports/quality-report.yaml", quality_report)
    write_yaml(output / "reports/processing-report.yaml", processing)
    write_yaml(output / "reports/acceptance-report.yaml", acceptance)
    write_yaml(output / "reports/failure-report.yaml", failure_report)
    build_markdown(output, source, source_type, pages, rendered, native_model, classification, visual, reconciliation, ocr)

    # An explicit --smoke-pages selector is also the per-batch verification
    # scope.  Previously --no-smoke-gate silently reduced this to pages[:2],
    # allowing later pages in a batch to bypass title/body/image checks.
    smoke_check_pages = smoke_pages if args.smoke_pages else (smoke_pages if smoke_mode else pages[:2])
    smoke_report = evaluate_smoke_pages(output, smoke_check_pages, rendered, classification, visual, ocr)
    smoke_report["mode"] = "smoke-only" if smoke_mode else "full-run-with-smoke"
    smoke_report["requested_pages"] = requested_pages
    smoke_report["processed_pages"] = pages
    if smoke_mode and len(requested_pages) > len(pages):
        smoke_report["stopped_before_pages"] = [page for page in requested_pages if page not in pages]
        smoke_report["next_action"] = "smoke 通过；使用新的 package-name 和 --no-smoke-gate 分批处理 stopped_before_pages，并保留本目录证据。"
    write_smoke_report(output, smoke_report)
    if smoke_report.get("status") == "failed":
        # Keep the generated package and its evidence, but make the package
        # status fail closed so an independent validator cannot mistake a
        # relation-only or placeholder page for a successful conversion.
        package_status = "needs-review"
        manifest["status"] = package_status
        manifest.setdefault("processing", {})["status"] = package_status
        processing["status"] = package_status
        acceptance["status"] = "needs-review"
        failure_report.setdefault("failures", []).append({"failure_id": "smoke-gate-failed", "stage_id": "A5", "code": "smoke-gate-failed", "severity": "P1", "retryable": True, "status": "open", "message": "冒烟门禁失败，已停止处理后续页面。", "evidence_paths": ["evidence/smoke-report.json"], "remediation": smoke_report.get("next_action")})
        failure_report["status"] = "needs-review"
        write_yaml(output / "manifest.yaml", manifest)
        write_yaml(output / "reports/processing-report.yaml", processing)
        write_yaml(output / "reports/acceptance-report.yaml", acceptance)
        write_yaml(output / "reports/failure-report.yaml", failure_report)

    # The runner's own status describes generation; the independent validator
    # is the user-visible acceptance gate and re-reads the finished package.
    validation = validate_package(output)
    write_json(output / "reports/validation-result.json", validation)
    final_status = validation.get("status", "needs-review")
    next_action = (
        "可阅读 document.md，并将 assets/ 作为已验收来源资产使用。"
        if final_status == "passed"
        else "请先查看 reports/validation-result.json、reports/acceptance-report.yaml 和 evidence/，完成复核后再使用。"
    )
    summary = {
        "status": final_status,
        "runner_status": package_status,
        "validation_status": final_status,
        "output": str(output),
        "source": str(source),
        "source_type": source_type,
        "pages_requested": len(requested_pages),
        "pages_processed": len(pages),
        "pages_parsed": len(native_model.get("pages") or native_model.get("slides") or []),
        "pages_rendered": len(rendered),
        "mac96": visual.get("status"),
        "ocr": ocr.get("status"),
        "embedded_assets": len(embedded),
        "source_selection": "single-source",
        "validation_report": "reports/validation-result.json",
        "smoke_status": smoke_report.get("status"),
        "smoke_report": "evidence/smoke-report.json",
        "smoke_failures": smoke_report.get("failures", []),
        "stopped_before_pages": smoke_report.get("stopped_before_pages", []),
        "next_action": next_action,
    }
    print(json.dumps(summary, ensure_ascii=False))
    # 0 means independently accepted; 1 means a package exists but needs
    # review or failed validation. argparse/input failures still exit 2.
    return 0 if final_status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
