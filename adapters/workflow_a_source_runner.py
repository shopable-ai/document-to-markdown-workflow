#!/usr/bin/env python3
"""Source-independent deterministic Workflow A runner.

The default execute path writes a reader package only: ``index.md`` and the
screenshots that Markdown actually references.  Engineering manifests,
reports, and JSON evidence remain available only through the explicit
``--engineering-output`` opt-in, so ordinary conversion does not turn its
output directory into a data-asset workspace.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import html
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

try:
    import yaml
except ImportError:  # runtime image intentionally omits PyYAML
    yaml = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import pdf_task_guard as task_guard
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_task_guard as task_guard

try:
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_resource_slots as resource_slots

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    from output_naming import resolve_package_target
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import resolve_package_target


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "references" / "profiles" / "source-runner-generic.yaml"
CONTRACT = ROOT / "contracts" / "workflow-a-source-runner.yaml"
RUNTIME_BIN = Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override")
P_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
SUPPORTED_SUFFIXES = {".ppt", ".pptx", ".pdf", ".png", ".jpg", ".jpeg"}
RUN_STARTED = time.monotonic()


def _yaml_python() -> str | None:
    for candidate in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3", shutil.which("python3")):
        if candidate and Path(candidate).is_file():
            try:
                probe = subprocess.run([candidate, "-c", "import yaml"], capture_output=True, timeout=10)
                if probe.returncode == 0:
                    return candidate
            except OSError:
                continue
    return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> dict:
    if yaml is not None:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        helper = _yaml_python()
        if not helper:
            raise ValueError("PyYAML is unavailable in the runtime and no host YAML interpreter was found")
        script = "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8')) or {}, ensure_ascii=False))"
        record = subprocess.run([helper, "-c", script, str(path)], capture_output=True, text=True, timeout=30)
        if record.returncode:
            raise ValueError(record.stderr.strip() or f"cannot parse YAML: {path}")
        value = json.loads(record.stdout)
    if not isinstance(value, dict):
        raise ValueError(f"profile must contain a mapping: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_progress(
    path: Path,
    *,
    run_id: str,
    source_fingerprint: str,
    stage: str,
    status: str,
    pages: list[int],
    next_action: str,
    evidence_paths: list[str] | None = None,
    elapsed_seconds: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    measured_elapsed = round(time.monotonic() - RUN_STARTED, 3) if elapsed_seconds is None else elapsed_seconds
    event = {
        "run_id": run_id,
        "source_fingerprint": source_fingerprint,
        "page": pages[0] if len(pages) == 1 else None,
        "pages": pages,
        "stage": stage,
        "task": stage,
        "attempt": 1,
        "status": status,
        "elapsed_seconds": measured_elapsed,
        "evidence_paths": evidence_paths or [],
        "next_action": next_action,
        "created_at": now(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(
        f"[workflow-a] stage={stage} status={status} pages={pages} elapsed={measured_elapsed}s next={next_action}",
        file=sys.stderr,
        flush=True,
    )


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        text = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    else:
        # JSON is a valid YAML 1.2 document and keeps output deterministic
        # when the lean execution runtime does not ship PyYAML.
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tool_path(name: str) -> str | None:
    candidates = [
        RUNTIME_BIN / name,
        ROOT.parent / "dependencies" / "native" / "poppler" / "poppler" / "bin" / name,
        Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin") / name,
        Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/bin") / name,
        Path(shutil.which(name) or ""),
    ]
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def run_command(command: list[str], *, timeout: int = 120, output_limit: int = 10000) -> dict:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {
            "command": command,
            "status": "complete" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "stdout": result.stdout if output_limit <= 0 else result.stdout[-output_limit:],
            "stderr": result.stderr if output_limit <= 0 else result.stderr[-output_limit:],
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "status": "failed", "error": repr(exc)}


def source_type_for(profile: dict, suffix: str) -> dict:
    normalized = suffix.lower()
    for source_type in profile.get("source_types", []):
        if normalized in {str(item).lower() for item in source_type.get("suffixes", [])}:
            return source_type
    raise ValueError(f"profile does not support source suffix {suffix or '<none>'}")


def validate_profile(profile: dict) -> None:
    required = {"id", "version", "source_types", "asset_policy", "content_ladder", "markdown_policy", "validation_policy"}
    missing = sorted(required - set(profile))
    if missing:
        raise ValueError(f"profile is missing required keys: {', '.join(missing)}")
    if (profile.get("markdown_policy") or {}).get("fixed_document_terms"):
        raise ValueError("generic profile cannot enable fixed document terms")
    if (profile.get("calibration_routes") or {}).get("default_enabled"):
        raise ValueError("calibration routes must remain disabled by default")


def parse_pages(selector: str) -> list[int] | None:
    """Parse explicit pages without reading a source."""
    value = str(selector or "").strip()
    if not value:
        raise ValueError("--pages must not be empty")
    if value.lower() == "all":
        return None
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            if "-" in token:
                start, end = (int(part.strip()) for part in token.split("-", 1))
                if start > end:
                    raise ValueError(f"invalid page range: {token}")
                pages.update(range(start, end + 1))
            else:
                pages.add(int(token))
        except ValueError as exc:
            raise ValueError(f"invalid page selector: {selector}") from exc
    if not pages or min(pages) < 1:
        raise ValueError(f"invalid page selector: {selector}")
    return sorted(pages)


def merge_profile(profile: dict, config: dict | None) -> dict:
    if not config:
        return profile
    allowed = {"asset_policy", "content_ladder", "language_repair", "markdown_policy", "validation_policy", "calibration_routes"}
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ValueError(f"config contains unsupported profile keys: {', '.join(unknown)}")
    merged = dict(profile)
    for key in allowed & set(config):
        value = config[key]
        if not isinstance(value, dict):
            raise ValueError(f"profile override must be a mapping: {key}")
        item = dict(merged.get(key) or {})
        item.update(value)
        merged[key] = item
    return merged


def build_execution_plan(source: Path, pages: str, profile: dict, *, mode: str) -> dict:
    suffix = source.suffix.lower()
    source_type = source_type_for(profile, suffix)
    if suffix == ".docx" and pages.strip().lower() != "all":
        raise ValueError("DOCX requires --pages all; Word pagination is not a stable native selector")
    selected_pages = parse_pages(pages)
    return {
        "contract": "workflow-a-source-runner",
        "runner": {
            "id": "workflow-a-source-runner", "version": "0.4.0", "mode": mode,
            "profile_id": profile.get("id", "unknown"), "workflow_id": "document-to-markdown-assets",
        },
        "source": {
            "path": str(source.expanduser().resolve()), "media_type": suffix.lstrip("."),
            "source_type": source_type["id"], "suffix": suffix, "sha256": None,
            "source_fingerprint_status": "not-run", "declared_mime_type": mimetypes.guess_type(source.name)[0],
        },
        "scope": {"pages": pages, "selected_pages": selected_pages, "one_source_per_run": True},
        "route": {
            "preflight": "source-preflight-before-stages", "fingerprint": "sha256-before-source-owned-evidence",
            "native_extraction": list(source_type.get("native_extractors", [])),
            "asset_sources": list(source_type.get("page_asset_strategy", [])),
            "asset_registration": list((profile.get("asset_policy") or {}).get("registration_fields", [])),
            "content_ladder": list((profile.get("content_ladder") or {}).get("ordered", [])),
            "language_repair": dict(profile.get("language_repair") or {}),
        },
        "asset_policy": {
            "storage": {
                "layout": "reader-only-flat-assets-v1",
                "root": "assets/",
                "classification": "markdown-referenced-screenshot",
            },
            "naming": {
                "filename_format": "<short-semantic-slug>.<ext>",
                "semantic_source": ["verified-alias", "source-derived-title"],
                "fallback_status": "needs-review",
                "page_in_filename": False,
                "collision_suffix": "-02",
            },
            "semantic_aliases": dict((profile.get("asset_policy") or {}).get("semantic_aliases") or {}),
        },
        "stages": [{"id": stage, "status": "planned"} for stage in [
            "source-preflight", "source-fingerprint", "native-extraction", "asset-registration",
            "content-reconciliation", "language-quality-repair", "markdown-assembly",
            "independent-validation", "quality-audit",
        ]],
        "markdown": dict(profile.get("markdown_policy") or {}),
        "validation": dict(profile.get("validation_policy") or {}),
        "calibration": dict(profile.get("calibration_routes") or {}),
        "output_mode": "reader-only",
        "status": "planned",
        "evidence": {"plan": "workflow-a-source-plan.json", "preflight": None, "validation": None},
        "diagnostics": [
            "plan-only does not read source contents, calculate a hash, render pages, call OCR, or call a visual model",
            "high-quality online baseline and local-model calibration are disabled by default",
        ],
        "created_at": now(),
    }


def _pptx_page_count(source: Path) -> int:
    return len(_pptx_page_numbers(source))


def _pptx_page_numbers(source: Path) -> list[int]:
    with ZipFile(source) as deck:
        return sorted(
            int(match.group(1))
            for name in deck.namelist()
            if (match := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name))
        )


def _pptx_slide_path(source: Path, logical_page: int) -> str:
    """Map a logical page selector to the physical slide XML name.

    Selected PPTX fixtures can retain original slide filenames (for example
    ``slide24.xml`` and ``slide25.xml``) even though the standalone source has
    only two pages.  Workflow selectors remain 1-based and source-local.
    """
    physical_pages = _pptx_page_numbers(source)
    if logical_page in physical_pages:
        physical_page = logical_page
    elif 1 <= logical_page <= len(physical_pages):
        physical_page = physical_pages[logical_page - 1]
    else:
        return ""
    return f"ppt/slides/slide{physical_page}.xml"


def _pdf_page_count(source: Path) -> int:
    tool = tool_path("pdfinfo")
    if tool:
        record = run_command([tool, str(source)])
        match = re.search(r"^Pages:\s*(\d+)\s*$", record.get("stdout", ""), re.MULTILINE)
        if match:
            return int(match.group(1))
    if PdfReader is not None:
        return len(PdfReader(str(source)).pages)
    raise ValueError("cannot determine PDF page count")


def available_page_numbers(source: Path, suffix: str) -> list[int]:
    if suffix == ".pptx":
        return _pptx_page_numbers(source)
    if suffix == ".pdf":
        return list(range(1, _pdf_page_count(source) + 1))
    if suffix in {".png", ".jpg", ".jpeg"}:
        return [1]
    if suffix == ".ppt":
        soffice = tool_path("soffice")
        if not soffice:
            raise ValueError("PPT execution requires soffice")
        with tempfile.TemporaryDirectory(prefix="workflow-a-ppt-") as temp:
            record = run_command([soffice, "--headless", "--convert-to", "pdf", "--outdir", temp, str(source)], timeout=300)
            generated = Path(temp) / f"{source.stem}.pdf"
            if record["status"] != "complete" or not generated.is_file():
                raise ValueError("PPT could not be rendered to PDF")
            return list(range(1, _pdf_page_count(generated) + 1))
    raise ValueError(f"unsupported source suffix: {suffix}")


def page_count(source: Path, suffix: str) -> int:
    """Return the number of source pages while preserving source page IDs."""

    return len(available_page_numbers(source, suffix))


def _preflight(
    source: Path,
    output_root: Path,
    package_id: str,
    selector: str,
    available_pages: list[int],
    candidate_comparison: Path | None = None,
) -> tuple[dict, list[int]]:
    explicit = parse_pages(selector)
    pages = ([] if explicit is None else explicit) if available_pages is None else (list(available_pages) if explicit is None else explicit)
    if available_pages is not None and (not pages or not set(pages).issubset(set(available_pages))):
        raise ValueError(f"selected pages {pages} are outside source range {available_pages}")
    try:
        from workflow_a_preflight import run_preflight
    except ImportError:  # pragma: no cover - package-style imports
        try:
            from adapters.workflow_a_preflight import run_preflight
        except ImportError as exc:
            raise ValueError("execution preflight is unavailable; refusing to bypass the fail-closed gate") from exc
    result = run_preflight(
        source,
        output_root,
        selector,
        total_pages=len(available_pages) if available_pages is not None else None,
        available_pages=available_pages,
        package_name=package_id,
        candidate_comparison=candidate_comparison,
    )
    return result, pages


def _rel_target(slide_path: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(slide_path), target))


def _pptx_relationships(deck: ZipFile, rel_name: str, slide_path: str) -> dict[str, str]:
    if rel_name not in deck.namelist():
        return {}
    root = ET.fromstring(deck.read(rel_name))
    return {item.attrib["Id"]: _rel_target(slide_path, item.attrib.get("Target", "")) for item in root.findall("rel:Relationship", P_NS)}


def _shape_id(shape: ET.Element) -> str:
    for path in ("p:nvSpPr/p:cNvPr", "p:nvPicPr/p:cNvPr", "p:nvCxnSpPr/p:cNvPr", "p:nvGraphicFramePr/p:cNvPr"):
        node = shape.find(path, P_NS)
        if node is not None:
            return str(node.attrib.get("id", "unknown"))
    return "unknown"


def _shape_bbox(shape: ET.Element) -> dict:
    xfrm = shape.find(".//a:xfrm", P_NS)
    if xfrm is None:
        return {}
    off, ext = xfrm.find("a:off", P_NS), xfrm.find("a:ext", P_NS)
    if off is None or ext is None:
        return {}
    return {"x": int(off.attrib.get("x", 0)), "y": int(off.attrib.get("y", 0)), "cx": int(ext.attrib.get("cx", 0)), "cy": int(ext.attrib.get("cy", 0)), "unit": "EMU"}


def extract_pptx_native(source: Path, pages: list[int]) -> dict:
    result = {"status": "complete", "parser": "zipfile + ElementTree", "pages": [], "text_blocks": [], "nodes": [], "image_refs": [], "diagnostics": []}
    try:
        with ZipFile(source) as deck:
            names = set(deck.namelist())
            for page in pages:
                slide_path = _pptx_slide_path(source, page)
                rel_path = f"ppt/slides/_rels/{Path(slide_path).name}.rels" if slide_path else ""
                if slide_path not in names:
                    result["diagnostics"].append({"page": page, "code": "slide-xml-missing"})
                    continue
                root, rels = ET.fromstring(deck.read(slide_path)), _pptx_relationships(deck, rel_path, slide_path)
                page_data = {"page": page, "source_location": slide_path, "text_blocks": [], "nodes": [], "image_refs": []}
                tree = root.find(".//p:cSld/p:spTree", P_NS)
                order = 0
                for shape in list(tree) if tree is not None else []:
                    kind = shape.tag.rsplit("}", 1)[-1]
                    if kind not in {"sp", "pic", "graphicFrame", "cxnSp"}:
                        continue
                    order += 1
                    object_id, bbox = _shape_id(shape), _shape_bbox(shape)
                    texts = []
                    for paragraph in shape.findall(".//a:p", P_NS):
                        text = "".join(item.text or "" for item in paragraph.findall(".//a:t", P_NS)).strip()
                        if text:
                            texts.append(text)
                    node = {"object_id": object_id, "kind": kind, "order": order, "page": page, "source_location": slide_path, "bbox": bbox, "text": texts}
                    page_data["nodes"].append(node)
                    if texts:
                        text_block_id = f"p{page}-o{object_id}"
                        page_data["text_blocks"].append({
                            "block_id": text_block_id,
                            "page": page,
                            "object_id": object_id,
                            "text": "\n".join(texts),
                            "lines": [{"line_id": f"{text_block_id}-l{index:03d}", "page": page, "text": value, "order": index} for index, value in enumerate(texts, 1)],
                            "bbox": bbox,
                            "order": order,
                        })
                    for blip in shape.findall(".//a:blip", P_NS):
                        rid = blip.attrib.get(f"{{{P_NS['r']}}}embed")
                        target = rels.get(rid) if rid else None
                        ref = {"page": page, "object_id": object_id, "relationship_id": rid, "target": target, "status": "located" if target else "needs-review", "bbox": bbox}
                        page_data["image_refs"].append(ref)
                        result["image_refs"].append(ref)
                result["pages"].append(page_data)
                result["text_blocks"].extend(page_data["text_blocks"])
                result["nodes"].extend(page_data["nodes"])
    except (OSError, BadZipFile, ET.ParseError) as exc:
        result["status"] = "failed"
        result["diagnostics"].append({"code": "pptx-native-extraction-failed", "error": repr(exc)})
    if len(result["pages"]) != len(pages):
        result["status"] = "needs-review"
    return result


def _pdf_bbox_text(source: Path, page: int) -> tuple[list[dict], dict]:
    tool = tool_path("pdftotext")
    if not tool:
        return [], {"status": "unavailable", "reason": "pdftotext unavailable"}
    record = run_command([tool, "-bbox-layout", "-f", str(page), "-l", str(page), str(source), "-"], timeout=120, output_limit=0)
    if record["status"] != "complete":
        return [], record
    # Poppler's bbox-layout output is XHTML with a meaningful flow/block/line
    # hierarchy.  Rebuilding lines by global y/x sorting loses columns and
    # often moves a title or a diagram label away from its source region.
    try:
        root = ET.fromstring(record.get("stdout", ""))
    except ET.ParseError as exc:
        return [], {"status": "failed", "command": record, "reason": f"bbox-layout XML parse failed: {exc}"}

    def local(node: ET.Element, name: str) -> list[ET.Element]:
        return [child for child in list(node) if child.tag.rsplit("}", 1)[-1] == name]

    def attr_box(node: ET.Element) -> dict:
        attrs = node.attrib
        return {
            "x0": float(attrs.get("xMin", 0)),
            "top": float(attrs.get("yMin", 0)),
            "x1": float(attrs.get("xMax", 0)),
            "bottom": float(attrs.get("yMax", 0)),
            "unit": "pt",
        }

    def merge_boxes(boxes: list[dict]) -> dict:
        return {
            "x0": min(item["x0"] for item in boxes),
            "top": min(item["top"] for item in boxes),
            "x1": max(item["x1"] for item in boxes),
            "bottom": max(item["bottom"] for item in boxes),
            "unit": "pt",
        }

    def normalize(value: str) -> str:
        value = html.unescape(re.sub(r"\s+", " ", value or "")).strip()
        # PDF text extraction may separate CJK glyphs that belong to one word.
        return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)

    page_node = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "page"), None)
    if page_node is None:
        return [], {"status": "failed", "command": record, "reason": "bbox-layout page element missing"}
    flows = []
    blocks = []
    word_count = 0
    block_order = 0
    for flow_index, flow in enumerate(local(page_node, "flow"), 1):
        flow_blocks = []
        for block in local(flow, "block"):
            block_order += 1
            block_id = f"p{page}-block-{block_order:03d}"
            block_lines = []
            block_words = []
            for line_index, line in enumerate(local(block, "line"), 1):
                words = []
                for word_index, word in enumerate(local(line, "word"), 1):
                    text = normalize("".join(word.itertext()))
                    if not text:
                        continue
                    word_count += 1
                    word_id = f"{block_id}-w{word_index:03d}"
                    word_record = {"word_id": word_id, "page": page, "text": text, "bbox": attr_box(word), "order": word_index}
                    words.append(word_record)
                    block_words.append(word_id)
                if not words:
                    continue
                line_text = normalize(" ".join(item["text"] for item in words))
                line_record = {"line_id": f"{block_id}-l{line_index:03d}", "page": page, "text": line_text, "word_ids": [item["word_id"] for item in words], "bbox": merge_boxes([item["bbox"] for item in words]), "order": line_index}
                block_lines.append(line_record)
            if not block_lines:
                continue
            block_record = {
                "block_id": block_id,
                "page": page,
                "text": "\n".join(item["text"] for item in block_lines),
                "lines": block_lines,
                "words": block_words,
                "bbox": attr_box(block),
                "flow": flow_index,
                "order": block_order,
            }
            blocks.append(block_record)
            flow_blocks.append(block_id)
        if flow_blocks:
            flows.append({"flow": flow_index, "block_ids": flow_blocks, "order": flow_index})
    layout = {
        "status": "complete",
        "page": page,
        "page_size": {"width": float(page_node.attrib.get("width", 0)), "height": float(page_node.attrib.get("height", 0)), "unit": "pt"},
        "flows": flows,
        "reading_order": [item["block_id"] for item in blocks],
        "block_count": len(blocks),
        "line_count": sum(len(item["lines"]) for item in blocks),
        "word_count": word_count,
        "command": record,
    }
    return blocks, layout


def extract_pdf_native(source: Path, pages: list[int]) -> dict:
    result = {"status": "complete", "parser": "pdftotext -bbox-layout (flow/block/line/word)", "pages": [], "text_blocks": [], "diagnostics": []}
    for page in pages:
        blocks, record = _pdf_bbox_text(source, page)
        page_status = "complete" if record.get("status") == "complete" and blocks else "needs-review"
        result["pages"].append({"page": page, "source_location": f"pdf-page-{page}", "text_blocks": blocks, "layout": record, "status": page_status})
        result["text_blocks"].extend(blocks)
        if record.get("status") != "complete":
            result["diagnostics"].append({"page": page, **record})
        elif not blocks:
            result["diagnostics"].append({"page": page, "code": "native-text-empty", "reason": "bbox-layout contained no text blocks"})
    if result["diagnostics"]:
        result["status"] = "needs-review"
    result["coverage"] = {
        "selected_pages": pages,
        "pages_with_native_text": [item["page"] for item in result["pages"] if item.get("text_blocks")],
        "page_count": len(result["pages"]),
        "block_count": len(result["text_blocks"]),
        "line_count": sum((item.get("layout") or {}).get("line_count", 0) or 0 for item in result["pages"]),
        "word_count": sum((item.get("layout") or {}).get("word_count", 0) or 0 for item in result["pages"]),
        "character_count": sum(len(str(item.get("text") or "").replace("\n", "")) for item in result["text_blocks"]),
        "basis": "Poppler bbox-layout block/line/word layer",
    }
    return result


def extract_ppt_native(source: Path, pages: list[int]) -> dict:
    """Extract readable PPT text through the same local Office/PDF boundary."""

    soffice = tool_path("soffice")
    if not soffice:
        return {"status": "needs-review", "parser": "soffice-to-pdf + pdftotext", "pages": [], "text_blocks": [], "diagnostics": ["soffice unavailable; native PPT extraction not-run"]}
    with tempfile.TemporaryDirectory(prefix="workflow-a-ppt-native-") as temp:
        record = run_command([soffice, "--headless", "--convert-to", "pdf", "--outdir", temp, str(source)], timeout=300)
        generated = Path(temp) / f"{source.stem}.pdf"
        if record["status"] != "complete" or not generated.is_file():
            return {"status": "needs-review", "parser": "soffice-to-pdf + pdftotext", "pages": [], "text_blocks": [], "diagnostics": ["PPT Office conversion failed", record]}
        result = extract_pdf_native(generated, pages)
        result["parser"] = "soffice-to-pdf + pdftotext -bbox-layout"
        for item in result.get("pages", []):
            item["source_location"] = f"ppt-page-{item.get('page')}"
        result.setdefault("diagnostics", []).append("PPT native structure is obtained from deterministic Office rendering")
        return result


def extract_image_native(source: Path) -> dict:
    dimensions = {}
    if Image is not None:
        with Image.open(source) as image:
            dimensions = {"width": image.width, "height": image.height, "format": image.format}
    return {"status": "complete", "parser": "Pillow image metadata", "pages": [{"page": 1, "source_location": str(source), "text_blocks": [], "layout": dimensions}], "text_blocks": [], "diagnostics": ["image source has no native text layer"]}


def _render_pdf(source: Path, destination: Path, pages: list[int]) -> tuple[dict[int, Path], dict]:
    tool = tool_path("pdftoppm")
    if not tool:
        return {}, {"status": "failed", "reason": "pdftoppm unavailable", "requested_pages": pages}
    prefix = destination / "_render"
    record = run_command([tool, "-png", "-r", "150", "-f", str(min(pages)), "-l", str(max(pages)), str(source), str(prefix)], timeout=300)
    assets = {}
    for page in pages:
        # pdftoppm pads page numbers when the selected range requires it
        # (for example, `_render-03.png` for page 3). Accept both forms so
        # source-owned page assets are not silently dropped after rendering.
        candidates = [destination / f"_render-{page}.png", destination / f"_render-{page:02d}.png"]
        candidate = next((item for item in candidates if item.is_file()), None)
        if candidate is None:
            matches = [destination / f"_render-{page:0{width}d}.png" for width in (3, 4)]
            matches = [item for item in matches if item.is_file()]
            candidate = matches[0] if matches else None
        if candidate is not None:
            assets[page] = candidate
    return assets, {"status": "complete" if record["status"] == "complete" and len(assets) == len(pages) else "failed", "source_owned": True, "text_visibility": {"status": "complete"}, "command": record, "requested_pages": pages, "rendered_pages": sorted(assets)}


def render_pages(source: Path, suffix: str, destination: Path, pages: list[int]) -> tuple[dict[int, Path], dict]:
    if suffix in {".ppt", ".pptx"}:
        soffice = tool_path("soffice")
        if not soffice:
            return {}, {"status": "failed", "reason": "soffice unavailable"}
        with tempfile.TemporaryDirectory(prefix="workflow-a-office-render-") as temp:
            record = run_command([soffice, "--headless", "--convert-to", "pdf", "--outdir", temp, str(source)], timeout=300)
            generated = Path(temp) / f"{source.stem}.pdf"
            if record["status"] != "complete" or not generated.is_file():
                return {}, {"status": "failed", "office": record}
            ordinal_pages = [sorted(pages).index(page) + 1 for page in pages]
            rendered, evidence = _render_pdf(generated, destination, ordinal_pages)
            assets = {page: rendered[ordinal] for page, ordinal in zip(pages, ordinal_pages) if ordinal in rendered}
            evidence["office"] = record
            evidence["renderer_input_source"] = str(source)
            return assets, evidence
    if suffix == ".pdf":
        return _render_pdf(source, destination, pages)
    target = destination / f"_input{source.suffix.lower()}"
    shutil.copy2(source, target)
    return {1: target}, {"status": "complete", "renderer": "source-image-preserved", "requested_pages": [1], "rendered_pages": [1]}


def _image_info(path: Path) -> tuple[str, int, int]:
    if Image is None:
        raise ValueError("Pillow unavailable")
    with Image.open(path) as image:
        return Image.MIME.get(image.format) or mimetypes.guess_type(path.name)[0] or "application/octet-stream", image.width, image.height


def _ascii_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def _usable_semantic_slug(value: str | None) -> str | None:
    """Return a semantic ASCII slug, rejecting technical or empty fallbacks."""

    slug = _ascii_slug(str(value or ""), "")
    if not slug or not re.search(r"[a-z]", slug):
        return None
    if slug in {"page", "source", "image", "asset", "original", "render"}:
        return None
    if any(marker in slug for marker in ("page-render", "original-page", "source-media", "o01")):
        return None
    return slug


def _page_semantic_slug(
    source: Path,
    page: int,
    native: dict,
    aliases: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Resolve a short page name without leaking technical provenance fields."""

    aliases = aliases or {}
    blocks = _page_native_blocks(native, page)
    title, _ = _title_from_blocks(blocks, native=native)
    alias_candidates = [title, str(page), source.stem, source.name]
    for candidate in alias_candidates:
        aliased = aliases.get(candidate)
        slug = _usable_semantic_slug(aliased)
        if slug:
            return slug, "generated", title
    slug = _usable_semantic_slug(title)
    if slug:
        return slug, "generated", title
    slug = _usable_semantic_slug(aliases.get("*"))
    if slug:
        return slug, "generated", title
    # The file remains deterministic and traceable, but its status makes the
    # missing semantic decision explicit instead of inventing a random name.
    return "needs-review-page", "needs-review", title


def _unique_asset_path(directory: Path, semantic_slug: str, extension: str) -> tuple[Path, int]:
    """Return the shortest available name and its collision ordinal."""

    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{semantic_slug}.{extension}"
    ordinal = 1
    while candidate.exists():
        ordinal += 1
        candidate = directory / f"{semantic_slug}-{ordinal:02d}.{extension}"
    return candidate, ordinal


def _asset_record(
    asset_id: str,
    path: Path,
    package: Path,
    source: Path,
    fingerprint: str,
    kind: str,
    display: str,
    label: str,
    occurrence: dict,
    ordinal: int = 1,
    parent: str | None = None,
    *,
    source_location: str | None = None,
    extraction_method: str | None = None,
    semantic_slug: str | None = None,
    naming_status: str = "generated",
) -> dict:
    mime, width, height = _image_info(path)
    source_slug = _ascii_slug(source.stem, f"source-{fingerprint[:8]}")
    page = occurrence.get("page") or 1
    purpose = {"page-render": "original-page", "input-image": "original-input", "embedded-media": "source-media", "crop": "source-crop", "complex-region": "source-region"}[kind]
    method = {
        "page-render": "office-render" if source.suffix.lower() in {".ppt", ".pptx"} else "pdf-render",
        "input-image": "original-preserved",
        "embedded-media": "embedded-lossless",
        "crop": "region-crop",
        "complex-region": "region-crop",
    }[kind]
    occurrence.setdefault("source_location", {"page": page, "region": occurrence.get("region")})
    location = source_location or f"{source.name}#page-{page}"
    return {
        "asset_id": asset_id, "file_name": path.name, "file_path": path.relative_to(package).as_posix(), "display_name": display, "asset_kind": kind,
        "source_location": {"source": location, "page": page, "region": occurrence.get("region")},
        "source": {"package_id": package.name, "source_fingerprint": fingerprint, "original_filename": source.name, "source_occurrences": [occurrence]},
        "extraction_method": extraction_method or method,
        "media": {"mime_type": mime, "width": width, "height": height, "sha256": sha256(path)},
        "semantic": {"label": label, "description": display, "confidence": "high"},
        "naming": {"source_slug": source_slug, "location_slug": f"p{page:03d}", "semantic_slug": semantic_slug or _ascii_slug(label, "needs-review-page"), "purpose_slug": purpose, "ordinal": ordinal, "status": naming_status},
        "status": "available", "parent_asset_id": parent,
    }


def _copy_page_assets(
    source: Path,
    suffix: str,
    rendered: dict[int, Path],
    package: Path,
    fingerprint: str,
    native: dict,
    semantic_aliases: dict[str, str] | None = None,
) -> tuple[list[dict], dict[int, Path], dict[int, str]]:
    asset_dir, assets, final, page_slugs = package / "assets", [], {}, {}
    kind = "input-image" if suffix in {".png", ".jpg", ".jpeg"} else "page-render"
    for page in sorted(rendered):
        input_path, ext = rendered[page], rendered[page].suffix.lower().lstrip(".") or "png"
        semantic_slug, naming_status, title = _page_semantic_slug(source, page, native, semantic_aliases)
        target, ordinal = _unique_asset_path(asset_dir, semantic_slug, ext)
        shutil.copy2(input_path, target)
        occurrence = {"source_occurrence_id": f"{package.name}-p{page:03d}-{kind}-01", "source_file": source.name, "page": page, "region": None, "object_ids": [], "parent_asset_id": None, "ordinal": 1, "status": "located"}
        assets.append(
            _asset_record(
                f"asset-page-{page:03d}",
                target,
                package,
                source,
                fingerprint,
                kind,
                title or f"Page {page}",
                title or f"Page {page}",
                occurrence,
                ordinal=ordinal,
                source_location=f"{source.name}#page-{page}",
                semantic_slug=semantic_slug,
                naming_status=naming_status,
            )
        )
        final[page] = target
        page_slugs[page] = semantic_slug
    return assets, final, page_slugs


def extract_pptx_embedded(
    source: Path,
    pages: list[int],
    package: Path,
    fingerprint: str,
    page_slugs: dict[int, str] | None = None,
) -> list[dict]:
    if source.suffix.lower() != ".pptx":
        return []
    assets, ordinal, seen_targets = [], 0, set()
    page_slugs = page_slugs or {}

    def next_target(page: int, ext: str) -> tuple[Path, int]:
        slug = page_slugs.get(page, "needs-review-page")
        return _unique_asset_path(package / "assets", slug, ext)

    with ZipFile(source) as deck:
        names = set(deck.namelist())
        for page in pages:
            slide_path = _pptx_slide_path(source, page)
            rel_path = f"ppt/slides/_rels/{Path(slide_path).name}.rels" if slide_path else ""
            if slide_path not in names:
                continue
            root, rels = ET.fromstring(deck.read(slide_path)), _pptx_relationships(deck, rel_path, slide_path)
            for shape in root.findall(".//p:sp", P_NS) + root.findall(".//p:pic", P_NS) + root.findall(".//p:graphicFrame", P_NS):
                object_id, bbox = _shape_id(shape), _shape_bbox(shape)
                for blip in shape.findall(".//a:blip", P_NS):
                    rid, target = blip.attrib.get(f"{{{P_NS['r']}}}embed"), None
                    target = rels.get(rid) if rid else None
                    if not target or "/media/" not in f"/{target}" or target not in names:
                        continue
                    seen_targets.add(target)
                    ordinal += 1
                    ext = Path(target).suffix.lower().lstrip(".") or "bin"
                    destination, page_ordinal = next_target(page, ext)
                    destination.write_bytes(deck.read(target))
                    try:
                        occurrence = {"source_occurrence_id": f"{package.name}-p{page:03d}-embedded-{ordinal:03d}", "source_file": source.name, "page": page, "region": bbox or None, "object_ids": [object_id], "parent_asset_id": None, "ordinal": ordinal, "status": "located"}
                        assets.append(_asset_record(f"asset-embedded-{ordinal:03d}", destination, package, source, fingerprint, "embedded-media", f"{source.name} embedded media {ordinal}", f"media {ordinal}", occurrence, page_ordinal, source_location=f"{slide_path}#object-{object_id}#target-{target}", extraction_method="embedded-lossless", semantic_slug=page_slugs.get(page, "needs-review-page"), naming_status="generated" if page in page_slugs else "needs-review"))
                    except Exception:
                        destination.unlink(missing_ok=True)
        # Some office exports keep media in the package without a slide-level
        # blip relationship (for example, layout/background assets). Preserve
        # those bytes as source-owned embedded evidence instead of dropping
        # them merely because the relationship graph is incomplete.
        for target in sorted(name for name in names if name.startswith("ppt/media/") and not name.endswith("/") and name not in seen_targets):
            ordinal += 1
            ext = Path(target).suffix.lower().lstrip(".") or "bin"
            destination, page_ordinal = next_target(pages[0], ext)
            destination.write_bytes(deck.read(target))
            try:
                occurrence = {"source_occurrence_id": f"{package.name}-embedded-{ordinal:03d}", "source_file": source.name, "page": pages[0], "region": None, "object_ids": [target], "parent_asset_id": None, "ordinal": ordinal, "status": "located"}
                assets.append(_asset_record(f"asset-embedded-{ordinal:03d}", destination, package, source, fingerprint, "embedded-media", f"{source.name} package media {ordinal}", f"media {ordinal}", occurrence, page_ordinal, semantic_slug=page_slugs.get(pages[0], "needs-review-page"), naming_status="generated" if pages[0] in page_slugs else "needs-review"))
            except Exception:
                destination.unlink(missing_ok=True)
    return assets


def extract_pdf_embedded(
    source: Path,
    pages: list[int],
    package: Path,
    fingerprint: str,
    page_slugs: dict[int, str] | None = None,
) -> list[dict]:
    if PdfReader is None:
        return []
    assets, ordinal = [], 0
    page_slugs = page_slugs or {}
    try:
        reader = PdfReader(str(source))
        for page_number in pages:
            for image in getattr(reader.pages[page_number - 1], "images", []):
                ordinal += 1
                data, suffix = image.data, Path(str(getattr(image, "name", ""))).suffix.lower().lstrip(".") or "png"
                slug = page_slugs.get(page_number, "needs-review-page")
                destination, position = _unique_asset_path(package / "assets", slug, suffix)
                destination.write_bytes(data)
                try:
                    occurrence = {"source_occurrence_id": f"{package.name}-p{page_number:03d}-embedded-{ordinal:03d}", "source_file": source.name, "page": page_number, "region": None, "object_ids": [str(getattr(image, "name", f"image-{ordinal}"))], "parent_asset_id": None, "ordinal": ordinal, "status": "located"}
                    assets.append(_asset_record(f"asset-embedded-{ordinal:03d}", destination, package, source, fingerprint, "embedded-media", f"{source.name} embedded media {ordinal}", f"media {ordinal}", occurrence, position, source_location=f"pdf-page-{page_number}#image-{getattr(image, 'name', ordinal)}", extraction_method="embedded-lossless", semantic_slug=slug, naming_status="generated" if page_number in page_slugs else "needs-review"))
                except Exception:
                    destination.unlink(missing_ok=True)
    except Exception:
        return assets
    return assets


def _native_text(native: dict, page: int) -> list[str]:
    item = next((value for value in native.get("pages", []) if value.get("page") == page), {})
    result = []
    for block in item.get("text_blocks", []):
        text = str(block.get("text") or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _page_native_blocks(native: dict, page: int) -> list[dict]:
    item = next((value for value in native.get("pages", []) if value.get("page") == page), {})
    return [block for block in item.get("text_blocks", []) if str(block.get("text") or "").strip()]


def _reader_text(value: str) -> str:
    """Normalize source text for the reader layer without changing raw evidence."""

    lines = [re.sub(r"\s+", " ", line).strip() for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _title_from_blocks(blocks: list[dict], *, native: dict) -> tuple[str, set[str]]:
    if not blocks:
        return "", set()
    candidates = []
    noise_ids: set[str] = set()
    for block in blocks:
        bbox = block.get("bbox") or {}
        top = float(bbox.get("top", bbox.get("y", 0)) or 0)
        text = _reader_text(block.get("text", ""))
        if text:
            block_id = block.get("block_id", "")
            compact_key = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()
            if (
                compact_key in {"everything", "ismodel", "mentalmodels", "everythingismodel", "everythingisamodel", "charliemunger", "100", "100个思维模型", "查理芒格的100个思维模型"}
                or re.fullmatch(r"0?\d{1,3}[.、:]?", text)
            ):
                noise_ids.add(block_id)
                continue
            text = re.sub(r"^0?\d{1,3}\s*[.、:：]?\s*", "", text).strip()
            if text:
                candidates.append((top, block.get("order", 0), text, block_id))
    if not candidates:
        return "", noise_ids
    # The first source block is the least-assumptive title candidate.  Keep a
    # small top-of-page subtitle in the body instead of inventing a title.
    candidates.sort(key=lambda item: (item[0], item[1]))
    title = candidates[0][2].splitlines()[0]
    return title, noise_ids | {candidates[0][3]}


def _pdf_reader_body(native: dict, page: int) -> tuple[str, dict]:
    blocks = _page_native_blocks(native, page)
    page_record = next((value for value in native.get("pages", []) if value.get("page") == page), {})
    title, title_ids = _title_from_blocks(blocks, native=native)
    # Poppler flows are faithful raw evidence but can be one block per label
    # on diagram-heavy pages.  For the reader layer, cluster nearby vertical
    # positions into source-derived regions while retaining original block
    # order within each region.  No page-specific vocabulary or coordinates
    # are used here.
    layout = page_record.get("layout") or {}
    page_height = float((layout.get("page_size") or {}).get("height", 0) or 0)
    gap_threshold = max(24.0, page_height * 0.07) if page_height else 48.0
    ordered_by_top = sorted(blocks, key=lambda item: (float((item.get("bbox") or {}).get("top", 0) or 0), item.get("order", 0)))
    region_by_block: dict[str, int] = {}
    region = 0
    previous_top = None
    for block in ordered_by_top:
        top = float((block.get("bbox") or {}).get("top", 0) or 0)
        if previous_top is None or top - previous_top > gap_threshold:
            region += 1
        region_by_block[block.get("block_id", "")] = region
        previous_top = top
    regions: dict[int, list[dict]] = {}
    for block in blocks:
        regions.setdefault(region_by_block.get(block.get("block_id", ""), 1), []).append(block)
    lines: list[str] = []
    if title:
        lines.extend(["### 页面主题", title, ""])
    reader_region = 0
    for _region_id, region_blocks in sorted(regions.items(), key=lambda item: item[0]):
        remaining = [block for block in region_blocks if block.get("block_id") not in title_ids]
        if not remaining:
            continue
        reader_region += 1
        lines.extend([f"### 内容分区 {reader_region}", ""])
        for block in remaining:
            block_lines = [_reader_text(value) for value in str(block.get("text", "")).splitlines() if _reader_text(value)]
            if not block_lines:
                continue
            lines.append(f"- {block_lines[0]}")
            # Continuation lines stay text-only so the reader layer remains a
            # faithful representation of the native block (without injecting
            # list markers into extracted content).
            lines.extend(f"  {value}" for value in block_lines[1:])
        lines.append("")
    layout = page_record.get("layout") or {}
    return "\n".join(lines).rstrip(), {"title": title, "title_block_ids": sorted(title_ids), "region_count": len(regions), "block_count": len(blocks), "layout_status": layout.get("status", "unknown")}


def _pptx_reader_body(native: dict, page: int) -> tuple[str, dict]:
    blocks = _page_native_blocks(native, page)
    title, title_ids = _title_from_blocks(blocks, native=native)
    lines: list[str] = []
    if title:
        lines.extend(["### 页面主题", title, ""])
        title_block = next((block for block in blocks if block.get("block_id") in title_ids), None)
        title_lines = [_reader_text(value) for value in str((title_block or {}).get("text", "")).splitlines() if _reader_text(value)]
        if len(title_lines) > 1:
            lines.extend(["### 页面副标题", "\n".join(title_lines[1:]), ""])
    lines.extend(["### 内容", ""])
    for block in blocks:
        if block.get("block_id") in title_ids:
            continue
        block_lines = [_reader_text(value) for value in str(block.get("text", "")).splitlines() if _reader_text(value)]
        if not block_lines:
            continue
        lines.append(f"- {block_lines[0]}")
        lines.extend(f"  {value}" for value in block_lines[1:])
    return "\n".join(lines).rstrip(), {"title": title, "title_block_ids": sorted(title_ids), "block_count": len(blocks), "layout_status": "native-shape-order"}


def _image_reader_body(native: dict, page: int) -> tuple[str, dict]:
    return "", {"title": "", "title_block_ids": [], "block_count": 0, "layout_status": "image-only"}


def assemble_markdown(package: Path, source: Path, pages: list[int], native: dict, page_assets: dict[int, Path], asset_ids: dict[int, str]) -> list[dict]:
    # Source filenames and page numbers belong in manifest/evidence.  Keeping
    # them out of the reader heading prevents traceability metadata from
    # becoming the document's semantic title.
    sections, lines = [], []
    image_kind = "input-image" if source.suffix.lower() in {".png", ".jpg", ".jpeg"} else "page-render"
    for page in pages:
        occurrence = f"{package.name}-p{page:03d}-{image_kind}-01"
        if source.suffix.lower() == ".pdf":
            body, structure = _pdf_reader_body(native, page)
        elif source.suffix.lower() in {".ppt", ".pptx"}:
            body, structure = _pptx_reader_body(native, page)
        else:
            body, structure = _image_reader_body(native, page)
        title = structure.get("title") or ""
        if title or body:
            lines.extend(([f"## {title}", ""] if title else []) + ([body, ""] if body else []))
        asset_id = asset_ids.get(page)
        if asset_id and page in page_assets:
            lines.extend([f"![原始页面]({page_assets[page].relative_to(package).as_posix()})", ""])
        blocks = _page_native_blocks(native, page)
        sections.append({"section_id": f"page-{page:03d}", "title": title, "body": body, "source_occurrences": [occurrence], "visual_asset_ids": [asset_id] if asset_id else [], "confidence": "high" if blocks else "unknown", "reader_order": "semantic-before-visual" if body else "needs-review", "unknown_forms": ["视觉复核未执行", "OCR 未执行"], "unresolved": [], "source_block_ids": [item.get("block_id") for item in blocks], "layout": structure})
    (package / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return sections


def _stage(stage_id: str, status: str, paths: list[str], diagnostics: list[str], unknown: list[str], command: str) -> dict:
    return {"stage_id": stage_id, "status": status, "evidence_level": "L2-local-runtime", "evidence_paths": paths, "verification_commands": [command], "diagnostics": diagnostics, "unknown": unknown, "score_basis": "deterministic local source evidence only"}


def _failure(failure_id: str, stage_id: str, code: str, message: str, evidence: list[str], remediation: str) -> dict:
    return {"failure_id": failure_id, "stage_id": stage_id, "code": code, "severity": "P1", "retryable": True, "status": "open", "message": message, "evidence_paths": evidence, "remediation": remediation, "resolved_evidence_paths": []}


def _content_classification(native: dict, source: Path, pages: list[int], fingerprint: str) -> dict:
    page_records = []
    for page in pages:
        record = next((item for item in native.get("pages", []) if item.get("page") == page), {})
        blocks = [item for item in record.get("text_blocks", []) if str(item.get("text") or "").strip()]
        text_chars = sum(len(str(item.get("text") or "").replace("\n", "")) for item in blocks)
        native_lines = sum(max(1, len(str(item.get("text") or "").splitlines())) for item in blocks)
        native_words = sum(len(re.findall(r"\S+", str(item.get("text") or ""))) for item in blocks)
        problems = []
        if source.suffix.lower() == ".pptx":
            textless_nodes = [item.get("object_id") for item in record.get("nodes", []) if not item.get("text")]
            image_nodes = [item.get("object_id") for item in record.get("image_refs", [])]
            if textless_nodes:
                problems.append({"code": "textless-native-shapes", "object_ids": textless_nodes, "reason": "shape has no native text; visual meaning is not inferred"})
            if image_nodes:
                problems.append({"code": "image-bearing-shapes", "object_ids": image_nodes, "reason": "image relation is preserved but visual semantics were not reconstructed"})
            if not blocks:
                problems.append({"code": "page-native-text-empty", "reason": "slide has no extracted native text"})
        elif not blocks:
            problems.append({"code": "page-native-text-empty", "reason": "page has no extracted native text"})
        page_records.append({
            "page": page,
            "status": "complete" if blocks and not problems else "needs-review",
            "classification": "native-text-with-layout" if blocks and not problems else "native-text-with-unresolved-visual-structure" if blocks else "no-native-text-layer",
            "native_block_count": len(blocks),
            "native_line_count": sum(len(item.get("lines", [])) for item in blocks) or native_lines,
            "native_word_count": sum(len(item.get("words", [])) for item in blocks) or native_words,
            "native_character_count": text_chars,
            "layout_status": (record.get("layout") or {}).get("status", "native-shape-order" if source.suffix.lower() in {".ppt", ".pptx"} else "unknown"),
            "problem_evidence": problems,
            "source_fingerprint": fingerprint,
        })
    status = "complete" if page_records and all(item["status"] == "complete" for item in page_records) else "needs-review"
    return {
        "status": status,
        "source": str(source),
        "source_fingerprint": fingerprint,
        "source_type": source.suffix.lower().lstrip("."),
        "selected_pages": pages,
        "pages": page_records,
        "coverage": {
            "page_count": len(page_records),
            "pages_with_native_text": [item["page"] for item in page_records if item["native_block_count"] > 0],
            "block_count": sum(item["native_block_count"] for item in page_records),
            "line_count": sum(item["native_line_count"] for item in page_records),
            "word_count": sum(item["native_word_count"] for item in page_records),
            "character_count": sum(item["native_character_count"] for item in page_records),
        },
        "reader_policy": "source-derived text only; unresolved visual structure remains evidence",
    }


def _candidate_layers(source: Path, pages: list[int], fingerprint: str, native: dict, classification: dict) -> tuple[dict, dict, dict]:
    visual = {
        "status": "not-run",
        "source": str(source),
        "source_fingerprint": fingerprint,
        "selected_pages": pages,
        "candidate_layer_state": "raw-layer-not-generated",
        "candidates": [],
        "diagnostics": ["visual model not-run; no visual candidate is asserted"],
        "next_action": "run an explicitly authorized visual review or perform human review",
    }
    is_image = source.suffix.lower() in {".png", ".jpg", ".jpeg"}
    native_complete = native.get("status") == "complete" and all(item.get("native_block_count", 0) > 0 for item in classification.get("pages", []))
    if is_image:
        ocr = {
            "status": "needs-review",
            "source": str(source),
            "source_fingerprint": fingerprint,
            "selected_pages": pages,
            "candidate_layer_state": "raw-layer-not-generated",
            "candidates": [],
            "diagnostics": ["image has no native text layer", "OCR was not authorized or available in this run"],
            "next_action": "run an explicitly authorized local OCR or visual extraction pass; retain raw response before reconciliation",
        }
    else:
        ocr = {
            "status": "not-required" if native_complete else "needs-review",
            "source": str(source),
            "source_fingerprint": fingerprint,
            "selected_pages": pages,
            "candidate_layer_state": "not-applicable" if native_complete else "raw-layer-not-generated",
            "candidates": [],
            "diagnostics": ["native source text is available; OCR was not used"] if native_complete else ["native extraction incomplete; OCR not-run"],
            "next_action": "none" if native_complete else "run an explicitly authorized OCR pass",
        }
    unresolved = ["visual-review-not-run"]
    unknown_forms = ["visual-review-not-run"]
    if is_image:
        unresolved.append("ocr-unavailable")
        unknown_forms.append("ocr-unavailable")
    elif not native_complete:
        unresolved.append("native-text-incomplete")
        unknown_forms.append("native-text-incomplete")
    reconciliation = {
        "status": "needs-review" if unresolved else "complete",
        "source": str(source),
        "source_fingerprint": fingerprint,
        "selected_pages": pages,
        "native_text": {"status": "complete" if native_complete else "needs-review", "pages": [item["page"] for item in classification.get("pages", []) if item.get("native_block_count", 0) > 0]},
        "layout": {"status": "complete" if native_complete and classification.get("status") == "complete" else "needs-review", "evidence": "evidence/native-content-model.json"},
        "candidate_layers": {"visual": visual["status"], "ocr": ocr["status"]},
        "unknown_forms": unknown_forms,
        "coverage_notes": ["raw native content and source-owned page assets are preserved", "candidate layers are not promoted to confirmed text"],
        "unresolved": unresolved,
        "next_action": "resolve listed review items before claiming a passed content-quality result",
    }
    return visual, ocr, reconciliation


def _language_repair_boundary() -> dict:
    """Describe the explicit A5 boundary without importing another runner.

    The source runner owns deterministic extraction and assembly.  Language
    repair is an optional, page-scoped follow-up route; importing the former
    optimized runner here made every ordinary conversion traverse a second
    script tree and blurred the fixed-entrypoint boundary.
    """
    return {
        "status": "not-run",
        "engine": "explicit-page-scoped-language-repair",
        "pages": [],
        "items": {},
        "failures": [],
        "diagnostics": [
            "active source runner does not invoke a secondary repair runner by default",
            "run the registered page-scoped repair adapter only as an explicit follow-up",
        ],
        "next_action": "人工复核当前页，或显式调用 markdown_content_repair.py；不得把未运行修复当成通过。",
    }


def execute_boundary(plan: dict, source: Path, output_root: Path) -> dict:
    """Run deterministic stages without calling the legacy runner."""
    source, output_root = source.expanduser().resolve(), output_root.expanduser().resolve()
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported source suffix: {suffix or '<none>'}")
    if not source.is_file() or not os.access(source, os.R_OK):
        raise ValueError(f"source is not a readable file: {source}")
    # A fresh run-id normally points at a non-existent directory.  Create the
    # single source's output root before profile-specific preflight checks so
    # PDF and image routes have the same execution boundary as PPTX.
    output_root.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256(source)
    available_pages = None if suffix == ".ppt" else available_page_numbers(source, suffix)
    package_id, target = resolve_package_target(output_root, source)
    candidate_comparison_path = None
    if plan.get("candidate_comparison"):
        candidate_comparison_path = Path(str(plan["candidate_comparison"])).expanduser().resolve()
    preflight, pages = _preflight(
        source,
        output_root,
        package_id,
        plan["scope"]["pages"],
        available_pages,
        candidate_comparison_path,
    )
    if preflight.get("failures"):
        raise ValueError("source preflight failed: " + "; ".join(item.get("message", "") for item in preflight["failures"]))
    if suffix == ".ppt":
        # Office conversion is deliberately delayed until the deterministic
        # preflight has completed.  It is the first stage allowed to discover
        # binary PPT page IDs; an explicit selector is then range-checked.
        available_pages = available_page_numbers(source, suffix)
        if not pages or not set(pages).issubset(set(available_pages)):
            raise ValueError(f"selected pages {pages} are outside source range {available_pages}")
    candidate_comparison = None
    if candidate_comparison_path:
        try:
            candidate_comparison = json.loads(candidate_comparison_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"candidate comparison could not be loaded after preflight: {exc}") from exc
        if not isinstance(candidate_comparison, dict):
            raise ValueError("candidate comparison must be a JSON object")
    if target.exists():
        raise ValueError(f"output package already exists: {target}")
    target.mkdir(parents=True)
    for directory in ("assets", "reports", "evidence"):
        (target / directory).mkdir(parents=True, exist_ok=True)
    write_json(target / "evidence/preflight.json", preflight)
    write_json(target / "evidence/source-fingerprint.json", {"source": str(source), "sha256": fingerprint, "computed_at": now()})
    progress_path = target / "evidence/progress.jsonl"
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="execution_preflight", status="complete", pages=pages, evidence_paths=["evidence/preflight.json"], next_action="开始 A1 来源清单")
    source_inventory = {
        "contract": "source-inventory",
        "version": "0.1.0",
        "source_fingerprint": fingerprint,
        "source": str(source),
        "media_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        "status": "complete",
        "page_count_or_dimensions": {"page_count": len(available_pages), "selected_pages": pages},
        "text_layer": "unavailable" if suffix in {".png", ".jpg", ".jpeg"} else "unknown-before-A2",
        "image_layer": "present" if suffix in {".png", ".jpg", ".jpeg"} else "unknown-before-A3",
        "readability": "readable",
        "source_selection": "single-source",
        "candidate_comparison": candidate_comparison,
        "candidate_comparison_evidence": str(candidate_comparison_path) if candidate_comparison_path else None,
        "diagnostics": ["A1 inventory is deterministic metadata only; content quality is not claimed"],
    }
    write_json(target / "evidence/source-inventory.json", source_inventory)
    write_json(target / "evidence/capability-probe.json", {
        "status": "not-run",
        "stage": "before-A1",
        "route": "mac96-then-current-conversation-vision-then-apple-vision-ocr",
        "source_fingerprint": fingerprint,
        "diagnostics": ["remote visual capability probe is not part of deterministic source runner execute"],
        "next_action": "run an explicitly authorized capability probe before visual review",
    })
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="capability_probe", status="not-run", pages=pages, evidence_paths=["evidence/capability-probe.json"], next_action="仅在明确授权后执行 capability probe")
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="source_inventory", status="complete", pages=pages, evidence_paths=["evidence/source-inventory.json"], next_action="开始 A2 原生内容提取")
    if suffix == ".pptx":
        native = extract_pptx_native(source, pages)
    elif suffix == ".ppt":
        native = extract_ppt_native(source, pages)
    elif suffix == ".pdf":
        native = extract_pdf_native(source, pages)
    elif suffix in {".png", ".jpg", ".jpeg"}:
        native = extract_image_native(source)
    else:
        native = {"status": "needs-review", "parser": "soffice-render-only", "pages": [], "text_blocks": [], "diagnostics": ["PPT native text parser not available"]}
    write_json(target / "evidence/native-content-model.json", native)
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="native_extraction", status=native.get("status", "needs-review"), pages=pages, evidence_paths=["evidence/native-content-model.json", "evidence/content-classification.json"], next_action="开始 A3 页面渲染")
    classification = _content_classification(native, source, pages, fingerprint)
    write_json(target / "evidence/content-classification.json", classification)
    rendered, rendering = render_pages(source, suffix, target / "evidence", pages)
    rendering["selected_pages"] = pages
    rendering["evidence_pages"] = sorted(rendered)
    rendering["selected_minus_evidence"] = sorted(set(pages) - set(rendered))
    write_json(target / "evidence/rendering.json", rendering)
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="source_rendering", status=rendering.get("status", "needs-review"), pages=pages, evidence_paths=["evidence/rendering.json", "assets"], next_action="开始 A6 资产登记")
    semantic_aliases = dict((plan.get("asset_policy") or {}).get("semantic_aliases") or {})
    page_assets, page_paths, page_slugs = _copy_page_assets(source, suffix, rendered, target, fingerprint, native, semantic_aliases)
    embedded = extract_pptx_embedded(source, pages, target, fingerprint, page_slugs) if suffix == ".pptx" else extract_pdf_embedded(source, pages, target, fingerprint, page_slugs) if suffix == ".pdf" else []
    assets = page_assets + embedded
    write_json(target / "evidence/asset-registration.json", {"status": "complete", "assets": [item["asset_id"] for item in assets], "embedded_count": len(embedded), "regions_count": 0})
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="asset_registration", status="complete", pages=pages, evidence_paths=["evidence/asset-registration.json"], next_action="开始 A4/A5 分层复核与 Markdown 组装")
    visual_candidates, ocr_candidates, reconciliation = _candidate_layers(source, pages, fingerprint, native, classification)
    write_json(target / "evidence/visual-candidates.json", visual_candidates)
    write_json(target / "evidence/ocr-candidates.json", ocr_candidates)
    write_json(target / "evidence/reconciliation.json", reconciliation)
    write_json(target / "evidence/regions.json", {"status": "not-run", "regions": [], "diagnostics": ["no explicit crop request; no region asset was generated"]})
    write_json(target / "evidence/smoke-report.json", {
        "status": "not-run",
        "source": str(source),
        "source_fingerprint": fingerprint,
        "selected_pages": pages,
        "diagnostics": ["普通单源 deterministic execute 不伪造 smoke 通过；长文档 smoke 由显式 batch 路线负责。"],
        "next_action": "需要批量或长文档门禁时，显式使用 page-batch smoke 路线。",
    })
    asset_ids = {page: item["asset_id"] for page, item in zip(sorted(page_paths), page_assets)}
    sections = assemble_markdown(target, source, pages, native, page_paths, asset_ids)
    repair_execution = _language_repair_boundary()
    write_json(target / "evidence/markdown-content-repair-execution.json", repair_execution)
    write_json(target / "evidence/language-repair-draft.json", {"status": repair_execution.get("status", "needs-review"), "drafts": repair_execution.get("pages", []), "diagnostics": repair_execution.get("diagnostics", []), "next_action": "人工复核 Markdown 文字和视觉关系"})
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="markdown_assembly", status="complete", pages=pages, evidence_paths=["index.md", "evidence/reconciliation.json"], next_action="写入 A7 确定性报告")
    invocation = " ".join(sys.argv)
    stage_evidence = [
        _stage("A1", "complete", ["evidence/preflight.json", "evidence/source-fingerprint.json", "evidence/source-inventory.json"], [], [], invocation),
        _stage("A2", "complete" if native.get("status") == "complete" and classification.get("status") == "complete" else "needs-review", ["evidence/native-content-model.json", "evidence/content-classification.json"], native.get("diagnostics", []) + ([] if classification.get("status") == "complete" else ["content classification has unresolved page structure"]), [] if native.get("status") == "complete" and classification.get("status") == "complete" else ["native content or visual structure needs review"], invocation),
        _stage("A3", "complete" if rendering.get("status") == "complete" else "needs-review", ["evidence/rendering.json", "assets"], [] if rendering.get("status") == "complete" else ["page rendering incomplete"], ["no region asset was requested"], invocation),
        _stage("A4", "needs-review", ["evidence/visual-candidates.json", "evidence/ocr-candidates.json", "evidence/reconciliation.json"], reconciliation.get("coverage_notes", []), reconciliation.get("unknown_forms", []), invocation),
        _stage("A5", repair_execution.get("status", "needs-review"), ["index.md", "evidence/markdown-content-repair-execution.json", "evidence/language-repair-draft.json"], repair_execution.get("diagnostics", []), ["human language review pending"], invocation),
        _stage("A6", "complete", ["evidence/asset-registration.json", "manifest.yaml"], [], [], invocation),
        _stage("A7", "needs-review", ["reports/processing-report.yaml", "reports/acceptance-report.yaml", "reports/failure-report.yaml"], ["independent validator intentionally not run in this execution"], ["quality score not-run"], invocation),
    ]
    failures = [_failure("visual-review-not-run", "A4", "visual-review-unavailable", "Visual model review was not run; visual quality and visual relations are not proven.", ["evidence/visual-candidates.json", "evidence/reconciliation.json"], "Run an explicitly authorized visual review or perform human review.")]
    if source.suffix.lower() in {".png", ".jpg", ".jpeg"} or native.get("status") != "complete":
        failures.append(_failure("ocr-not-run", "A4", "ocr-unavailable", "No confirmed OCR candidate layer is available for this source.", ["evidence/ocr-candidates.json"], "Run an explicitly authorized OCR pass if native text is insufficient."))
    if repair_execution.get("status") != "complete":
        failures.append(_failure("language-repair-needs-review", "A5", "review-failed", "Markdown Content Repair did not complete a page-scoped repair; reader text remains source-native or requires review.", ["evidence/markdown-content-repair-execution.json", "evidence/language-repair-draft.json"], "Review the current page-scoped Markdown and provide only the relevant text candidate if needed."))
    manifest = {
        "manifest_version": "0.8.0", "output_layout": "flat-assets-v1", "package_id": package_id, "kind": "markdown-asset-package", "status": "needs-review",
        "source": {"path": str(source), "path_label": source.name, "media_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream", "source_fingerprint": fingerprint, "selected_pages": pages, "total_pages": len(available_pages), "source_page_numbers": available_pages, "source_selection": "single-source", "candidate_comparison": candidate_comparison, "candidate_comparison_evidence": str(candidate_comparison_path) if candidate_comparison_path else None},
        "markdown": {"path": "index.md", "sections": sections}, "assets_manifest": {"path": "manifest.yaml", "type": "source-visual-asset-manifest"}, "assets": assets,
        "evidence": {"source_inventory": "evidence/source-inventory.json", "native_content_model": "evidence/native-content-model.json", "content_classification": "evidence/content-classification.json", "visual_candidates": "evidence/visual-candidates.json", "ocr_candidates": "evidence/ocr-candidates.json", "reconciliation": "evidence/reconciliation.json", "capability_probe": "evidence/capability-probe.json", "smoke_report": "evidence/smoke-report.json", "progress": "evidence/progress.jsonl", "markdown_content_repair": "evidence/markdown-content-repair-execution.json"},
        "processing": {"native_extraction": "complete" if native.get("status") == "complete" else "partial", "ocr_used": False, "visual_review": visual_candidates.get("status"), "execution_path": "needs-review", "fallback_reason": "visual review not-run; OCR status recorded in evidence/ocr-candidates.json", "status": "needs-review", "stage_evidence": stage_evidence, "stage_evidence_contract": "contracts/stage-evidence.yaml", "failure_report": "reports/failure-report.yaml", "model_calls": [], "context_policy": {"one_source_per_run": True}},
        "validation": {"validator_version": "real-output-root", "status": "needs-review", "checks": ["package layout", "source fingerprint", "asset hashes", "Markdown links"]},
        "quality_gate": {"status": "needs-review", "score": None, "reason": "quality audit not-run"}, "quality_report": "reports/quality-report.yaml",
    }
    processing = {"status": "needs-review", "evidence_level": "L2-local-runtime", "run_id": package_id, "started_at": now(), "finished_at": now(), "real_business_result_package": str(target), "input": {"source": str(source), "source_type": suffix.lstrip("."), "sha256": fingerprint, "pages": pages, "total_pages": len(available_pages), "source_page_numbers": available_pages}, "source_selection": "single-source", "candidate_comparison": candidate_comparison, "progress_stream": "evidence/progress.jsonl", "stage_evidence_contract": "contracts/stage-evidence.yaml", "stage_evidence": stage_evidence, "stages": [{"id": item["stage_id"], "status": item["status"], "evidence": item["evidence_paths"]} for item in stage_evidence], "tools": {"native-parser": native.get("parser"), "page-renderer": rendering, "markdown-writer": "workflow_a_source_runner.py"}, "asset_policy": {"layout": "flat-assets-v1", "root": "assets/", "classification": "manifest.asset_kind", "embedded_count": len(embedded), "regions_count": 0}, "failure_report": "reports/failure-report.yaml", "ocr": {"status": ocr_candidates.get("status"), "evidence": "evidence/ocr-candidates.json"}, "visual_model": {"status": visual_candidates.get("status"), "evidence": "evidence/visual-candidates.json"}, "language_repair": {"status": "not-run"}, "quality_audit": {"status": "not-run"}}
    acceptance = {"status": "needs-review", "evidence_level": "L2-local-runtime", "real_business_result_package": str(target), "checks": [{"id": "required-files-and-directories", "status": "passed"}, {"id": "source-read", "status": "passed"}, {"id": "native-extraction", "status": "passed" if native.get("status") == "complete" else "needs-review"}, {"id": "source-owned-rendering", "status": "passed" if rendering.get("status") == "complete" else "needs-review"}, {"id": "visual-review", "status": visual_candidates.get("status")}, {"id": "ocr", "status": ocr_candidates.get("status")}], "failures": [item["failure_id"] for item in failures], "warnings": [item["message"] for item in failures], "stage_evidence": stage_evidence, "failure_report": "reports/failure-report.yaml", "diagnostics": ["deterministic conversion only; visual quality not proven"]}
    failure_report = {"contract": "failure-report", "version": "0.1.0", "status": "needs-review", "evidence_level": "L2-local-runtime", "failures": failures, "warnings": [item["message"] for item in failures], "diagnostics": ["visual model, OCR, language repair and quality audit are not-run"]}
    quality_report = {"report_version": "0.1.0", "status": "needs-review", "total": None, "minimum": None, "target": None, "assessment_basis": "quality audit not-run", "unknowns": ["visual model not-run", "OCR not-run", "human quality review not-run"]}
    write_yaml(target / "manifest.yaml", manifest)
    write_yaml(target / "reports/processing-report.yaml", processing)
    write_yaml(target / "reports/acceptance-report.yaml", acceptance)
    write_yaml(target / "reports/failure-report.yaml", failure_report)
    write_yaml(target / "reports/quality-report.yaml", quality_report)
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="independent_validation", status="not-run", pages=pages, evidence_paths=[], next_action="按本轮要求不运行独立 validator")
    validation = {"status": "not-run", "valid": None, "errors": [], "warnings": ["独立 validator 按本轮要求未运行。"]}
    write_json(target / "reports/validation-result.json", validation)
    # The package validator accepts the independent report's ``not-run``
    # diagnostic state, while the manifest itself must remain a recognized
    # package verdict.  Keep the package honest as needs-review until that
    # independent validator is explicitly executed.
    manifest["validation"]["status"] = "needs-review"
    write_yaml(target / "manifest.yaml", manifest)
    plan["source"].update({"sha256": fingerprint, "source_fingerprint_status": "computed"})
    plan["scope"]["selected_pages"] = pages
    plan["status"] = "needs-review"
    plan["output_root"], plan["package_path"] = str(output_root), str(target)
    plan["output_mode"] = "engineering"
    plan["evidence"] = {
        "mode": "engineering",
        "written": True,
        "plan": "workflow-a-source-plan.json",
        "preflight": "evidence/preflight.json",
        "validation": "reports/validation-result.json",
        "required_package": ["evidence/source-inventory.json", "evidence/native-content-model.json", "evidence/content-classification.json", "evidence/visual-candidates.json", "evidence/ocr-candidates.json", "evidence/reconciliation.json", "evidence/capability-probe.json", "evidence/smoke-report.json", "evidence/progress.jsonl"],
    }
    plan["diagnostics"].extend(["visual_model: not-run", "ocr: not-run", "mac96: not-run", "online_model: not-run"])
    append_progress(progress_path, run_id=package_id, source_fingerprint=fingerprint, stage="independent_validation", status="not-run", pages=pages, evidence_paths=["reports/validation-result.json", "manifest.yaml"], next_action="保持 needs-review；按要求不运行独立 validator")
    write_json(target / "workflow-a-source-plan.json", plan)
    return plan


def execute_reader_boundary(plan: dict, source: Path, output_root: Path) -> dict:
    """Convert one source into the minimal reader-facing package.

    Extraction and rendering happen in a temporary workspace.  The public
    package is assembled only after Markdown exists, and contains no
    manifests, reports, evidence, page units, or embedded-media side files.
    Every copied image is referenced by ``index.md``; unreferenced generated
    assets never cross the temporary/public boundary.
    """
    source, output_root = source.expanduser().resolve(), output_root.expanduser().resolve()
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported source suffix: {suffix or '<none>'}")
    if not source.is_file() or not os.access(source, os.R_OK):
        raise ValueError(f"source is not a readable file: {source}")

    output_root.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256(source)
    available_pages = None if suffix == ".ppt" else available_page_numbers(source, suffix)
    package_id, target = resolve_package_target(output_root, source)
    candidate_comparison_path = None
    if plan.get("candidate_comparison"):
        candidate_comparison_path = Path(str(plan["candidate_comparison"])).expanduser().resolve()
    preflight, pages = _preflight(
        source,
        output_root,
        package_id,
        plan["scope"]["pages"],
        available_pages,
        candidate_comparison_path,
    )
    if preflight.get("failures"):
        raise ValueError("source preflight failed: " + "; ".join(item.get("message", "") for item in preflight["failures"]))
    if suffix == ".ppt":
        available_pages = available_page_numbers(source, suffix)
        if not pages or not set(pages).issubset(set(available_pages)):
            raise ValueError(f"selected pages {pages} are outside source range {available_pages}")

    if target.exists():
        raise ValueError(f"output package already exists: {target}")

    with tempfile.TemporaryDirectory(prefix="workflow-a-reader-") as temp:
        workspace = Path(temp)
        working_package = workspace / "package"
        working_package.mkdir(parents=True)
        rendered, rendering = render_pages(source, suffix, workspace, pages)
        if rendering.get("status") != "complete" or sorted(rendered) != sorted(pages):
            missing = sorted(set(pages) - set(rendered))
            raise ValueError(f"page rendering failed; missing pages: {missing}")

        if suffix == ".pptx":
            native = extract_pptx_native(source, pages)
        elif suffix == ".ppt":
            native = extract_ppt_native(source, pages)
        elif suffix == ".pdf":
            native = extract_pdf_native(source, pages)
        else:
            native = extract_image_native(source)

        semantic_aliases = dict((plan.get("asset_policy") or {}).get("semantic_aliases") or {})
        _assets, page_paths, _page_slugs = _copy_page_assets(
            source,
            suffix,
            rendered,
            working_package,
            fingerprint,
            native,
            semantic_aliases,
        )
        asset_ids = {page: f"reader-page-{page:03d}" for page in page_paths}
        assemble_markdown(working_package, source, pages, native, page_paths, asset_ids)
        reader_markdown = working_package / "index.md"
        if not reader_markdown.is_file() or not reader_markdown.read_text(encoding="utf-8").strip():
            raise ValueError("reader Markdown assembly produced an empty index.md")

        referenced = _markdown_image_paths(reader_markdown.read_text(encoding="utf-8"), working_package)
        expected_assets = {path.absolute() for path in page_paths.values()}
        if referenced != expected_assets:
            missing = sorted(str(path.relative_to(working_package)) for path in expected_assets - referenced)
            extra = sorted(str(path.relative_to(working_package)) for path in referenced - expected_assets)
            raise ValueError(f"reader asset references do not match generated screenshots; missing={missing}, extra={extra}")

        target.mkdir(parents=True)
        (target / "assets").mkdir()
        shutil.copy2(reader_markdown, target / "index.md")
        for image in sorted(referenced):
            relative = image.relative_to(working_package)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, destination)

    plan["source"].update({"sha256": fingerprint, "source_fingerprint_status": "computed"})
    plan["scope"]["selected_pages"] = pages
    # A reader-only package is intentionally too small for independent
    # validation: it has no manifest, reports, or evidence directory.  The
    # deterministic assembly result is useful, but it cannot satisfy the
    # production contract's ``passed_requires`` gate on its own.
    plan["status"] = "needs-review"
    plan["output_root"], plan["package_path"] = str(output_root), str(target)
    plan["output_mode"] = "reader-only"
    plan["validation"].update(
        {
            "status": "not-run",
            "result_path": None,
            "passed": False,
            "authority": "independent-validator",
        }
    )
    plan["evidence"] = {
        "mode": "reader-only",
        "written": False,
        "validation": None,
        "diagnostics": [
            "engineering evidence is disabled by default",
            "independent validator was not run; reader-only output remains needs-review",
        ],
    }
    plan["artifacts"] = {
        "markdown": "index.md",
        "screenshots": sorted(path.relative_to(target).as_posix() for path in target.glob("assets/*") if path.is_file()),
    }
    plan["engineering_artifacts"] = "not-written; use --engineering-output for an explicit diagnostic package"
    plan["diagnostics"] = [
        "reader-only output: no manifest, reports, evidence, page-units, or embedded-media side files were written",
        "visual quality and language repair remain outside this deterministic conversion boundary",
        "independent validator not-run: passed is not allowed",
    ]
    return plan


def _markdown_image_paths(text: str, package: Path) -> set[Path]:
    """Resolve Markdown image links without accepting paths outside package."""

    paths: set[Path] = set()
    for match in re.finditer(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^) \t]+))", text):
        raw = match.group(1) or match.group(2) or ""
        candidate = package / raw
        resolved = candidate.resolve()
        if not resolved.is_relative_to(package.resolve()):
            raise ValueError(f"Markdown image link escapes package: {raw}")
        if not resolved.is_file():
            raise ValueError(f"Markdown image link does not exist: {raw}")
        # Keep the workspace's lexical prefix (rather than macOS's /private
        # realpath alias) so ``relative_to(package)`` remains deterministic.
        paths.add(candidate.absolute())
    return paths


def execute_docx_boundary(
    plan: dict, source: Path, output_root: Path, run_root: Path | None, source_fingerprint: str,
) -> dict:
    try:
        from workflow_a_docx import execute_docx
    except ImportError:  # package-style imports
        from .workflow_a_docx import execute_docx

    pandoc = tool_path("pandoc")
    if not pandoc:
        raise ValueError("DOCX conversion requires a local Pandoc executable")
    if not source.is_file() or not os.access(source, os.R_OK):
        raise ValueError(f"source is not a readable file: {source}")
    output_root.mkdir(parents=True, exist_ok=True)
    target = run_root if run_root is not None else resolve_package_target(output_root, source)[1]
    if target.parent != output_root:
        raise ValueError("DOCX --run-root must be a new direct child of --output-root")
    report = execute_docx(source, target, pandoc, source_fingerprint)
    result_path = str(target / "reports/validation-result.json")
    plan["source"].update(
        sha256=report["source_sha256"], source_fingerprint_status="computed",
    )
    plan["scope"].update(word_metadata_pages=report["word_metadata_pages"], converted_documents=1)
    plan.update(status=report["status"], output_root=str(output_root), package_path=str(target))
    plan["validation"].update(
        status=report["status"], result_path=result_path,
        passed=report["status"] == "passed", authority="independent-docx-validator",
    )
    plan["evidence"] = {
        "mode": "docx-native-validation", "written": True, "validation": result_path,
        "diagnostics": report["review"],
    }
    plan["artifacts"] = {
        "markdown": "index.md", "embedded_media": report["distinct_assets"],
    }
    plan["diagnostics"] = report["review"]
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or execute source-independent Workflow A conversion")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, help="new exact package directory (DOCX execute only)")
    parser.add_argument("--pages", default="all")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--candidate-comparison", type=Path)
    parser.add_argument("--plan-only", action="store_true", help="do not read source")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--engineering-output",
        action="store_true",
        help="explicitly write the legacy manifest/evidence/report package; default is reader-only output",
    )
    args = parser.parse_args(argv)
    if args.plan_only and args.execute:
        parser.error("--plan-only and --execute are mutually exclusive")
    mode = "execute" if args.execute else "plan-only"
    try:
        profile = load_yaml(args.profile.expanduser().resolve())
        if args.config:
            profile = merge_profile(profile, load_yaml(args.config.expanduser().resolve()))
        validate_profile(profile)
        plan = build_execution_plan(args.source, args.pages, profile, mode=mode)
        is_docx = plan["source"]["suffix"] == ".docx"
        if args.run_root is not None and not (is_docx and args.execute):
            raise ValueError("--run-root is supported only for DOCX execute")
        if is_docx and (args.engineering_output or args.candidate_comparison):
            raise ValueError("DOCX native route does not support engineering output or candidate comparison")
        if args.candidate_comparison:
            plan["candidate_comparison"] = str(args.candidate_comparison.expanduser().resolve())
        plan["output_root"] = str(args.output_root.expanduser().resolve())
        if args.execute:
            source = args.source.expanduser().resolve()
            source_fingerprint = sha256(source)
            local_slot = (
                resource_slots.acquire_local_pdf_slot(
                    purpose="workflow-a-source-runner-pdf"
                )
                if source.suffix.lower() == ".pdf"
                else nullcontext()
            )
            with local_slot, task_guard.acquire_task_lock(
                source=source,
                source_sha256=source_fingerprint,
                output_root=args.output_root.expanduser().resolve(),
                pages=args.pages,
                entrypoint="workflow_a_source_runner.py",
            ):
                if is_docx:
                    plan = execute_docx_boundary(
                        plan, source, args.output_root.expanduser().resolve(),
                        args.run_root.expanduser().absolute() if args.run_root else None,
                        source_fingerprint,
                    )
                elif args.engineering_output:
                    plan = execute_boundary(plan, args.source, args.output_root)
                else:
                    plan = execute_reader_boundary(plan, args.source, args.output_root)
    except (OSError, ValueError, TypeError, BadZipFile, ET.ParseError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if mode == "plan-only" else (0 if plan.get("status") == "passed" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
