#!/usr/bin/env python3
"""Detect which PDF-to-Markdown route fits each source before conversion.

The detector is deliberately fail-closed and inexpensive. It uses only local
PDF metadata, sampled native text extraction, and image-object accounting to
classify each source into one of three buckets:

- ``native-only``: native text is strong enough for the pure native route.
- ``hybrid``: native text exists, but visual structure or image-heavy pages
  likely need the staged Workflow A path.
- ``vision-only``: native text is absent or too weak to trust as the primary
  conversion path.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies")
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "pdf-route-detection"
CONTRACT = "pdf-route-detection"
VERSION = "0.5.0"
READABLE_REPORT_VERSIONS = {"0.1.0", "0.2.0", "0.3.0", "0.4.0", VERSION}
REPORT_NAME = "report.json"
RECORDS_NAME = "records.ndjson"
RECORDS_DIRNAME = "records"
ROUTE_LISTS_DIRNAME = "route-lists"
CURRENT_INDEX_NAME = "current.json"
READER_SUMMARY_NAME = "PDF转换路线总览.md"
READER_ROOT_NAME = "PDF转换路线"
REVIEW_NAME = "待人工复核.csv"
READER_ROUTE_NAMES = {
    "native-only": "原生文本路线.txt",
    "hybrid": "图文混合路线.txt",
    "vision-only": "视觉识别路线.txt",
}
ROUTE_LABELS = {
    "native-only": "原生文本路线",
    "hybrid": "图文混合路线",
    "vision-only": "视觉识别路线",
}
STATUS_LABELS = {"complete": "完成", "partial": "部分完成", "failed": "检测失败"}
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
REASON_LABELS = {
    "dense-native-text-dominates": "原生文字层内容密集",
    "diagram-like-pages-dominate": "图示型页面占主导",
    "diagram-or-scan-dominant": "图示或扫描页面占主导",
    "fallback-to-visual-route": "保守回退到视觉路线",
    "full-page-images-dominate": "整页图片占主导",
    "image-heavy-pages-present": "存在图片密集页面",
    "isolated-cover-or-insert-pages-detected": "存在少量封面或插页",
    "leading-pages-full-image-no-text": "开头抽样页为整页图片且无文字层",
    "native-text-exists": "存在原生文字层",
    "native-text-lacks-document-structure": "原生文字缺少可用文档结构",
    "native-text-not-trusted-primary": "原生文字不宜作为主要来源",
    "native-text-signal-is-weak": "原生文字信号较弱",
    "sampled-native-page-incomplete": "抽样页存在原生文字不完整页面",
    "sampled-pages-have-no-usable-native-text": "抽样页没有可用原生文字",
    "sampled-pages-look-layout-sparse": "抽样页版面较稀疏",
    "sparse-native-page-no-visual-content": "稀疏原生页面未发现额外视觉内容",
    "full-page-background-with-complete-text-layer": "完整文字层覆盖了无额外结构的整页背景图",
    "visual-overhead-is-low": "视觉处理收益较低",
    "visual-structure-needs-staged-review": "视觉结构需要分阶段处理",
}
SUPPORTED_SUFFIXES = {".pdf"}
PAGE_SIZE_RE = re.compile(r"^Page size:\s*([0-9.]+)\s+x\s+([0-9.]+)\s+pts", re.MULTILINE)
PAGES_RE = re.compile(r"^Pages:\s*(\d+)\s*$", re.MULTILINE)
TOKEN_RE = re.compile(r"\S+")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
GARBLED_RE = re.compile(r"[�□�]|cid:\d+")
BODY_KEYWORDS = (
    "目录",
    "第",
    "章",
    "节",
    "篇",
    "一",
    "二",
    "三",
    "四",
    "五",
)
PAGE_ROUTES = ("native-only", "native-plus-visual", "visual-only")
VISUAL_CUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "figure-reference",
        re.compile(
            r"(?:如图|见图|如下图|见下图|图\s*[0-9一二三四五六七八九十]+(?:\s*所示)?)"
        ),
    ),
    # Do not start a table reference inside ordinary words such as "代表一个"
    # or "发表一下".
    # Real references like "如表一所示" and "见表 2" remain matched.
    (
        "table-reference",
        re.compile(r"(?<![代发])表\s*[0-9一二三四五六七八九十]+"),
    ),
    (
        "matrix-reference",
        re.compile(
            r"(?:矩阵图|四象限|(?:如图|见图|如下图|见下图).{0,12}矩阵|矩阵.{0,6}(?:如下图|见下图|所示))"
        ),
    ),
    ("flow-reference", re.compile(r"(?:流程图|模型图|关系图|结构图|坐标轴)")),
)
MIN_NATIVE_TEXT_CHARS = 16
MIN_NATIVE_BODY_CHARS = 80
MEANINGFUL_IMAGE_AREA_RATIO = 0.08
# Exported ebook backgrounds commonly stop at the printable page margin.  A
# 95% cutoff misclassifies those 93-94% watermark/background objects as
# semantic figures even when the searchable text layer is complete.
FULL_PAGE_BACKGROUND_MIN_AREA_RATIO = 0.93


@dataclass
class PageImageStats:
    image_count: int = 0
    total_area_ratio: float = 0.0
    max_area_ratio: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_count": self.image_count,
            "total_area_ratio": round(self.total_area_ratio, 4),
            "max_area_ratio": round(self.max_area_ratio, 4),
        }


class DetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    source_path: Path
    relative_path: str
    file_size: int
    modified_at: str


def visual_text_signals(text: str) -> dict[str, Any]:
    """Return candidate-only visual references found in native page text.

    These matches never select a visual route by themselves.  They only make
    the page eligible for corroboration by image objects, layout, tables, or a
    deterministic raster-structure probe.
    """

    normalized = str(text or "").replace("\f", "")
    hits: list[dict[str, str]] = []
    for code, pattern in VISUAL_CUE_PATTERNS:
        for match in pattern.finditer(normalized):
            hits.append({"code": code, "text": match.group(0)[:80]})
    return {
        "present": bool(hits),
        "codes": sorted({item["code"] for item in hits}),
        "hits": hits,
    }


def bbox_layout_metrics(
    blocks: list[dict[str, Any]], page_size: dict[str, Any] | None
) -> dict[str, Any]:
    """Summarize text occupancy and large vertical whitespace from bbox data."""

    width = float((page_size or {}).get("width") or 0.0)
    height = float((page_size or {}).get("height") or 0.0)
    page_area = width * height
    boxes: list[tuple[float, float, float, float]] = []
    for block in blocks:
        box = block.get("bbox") if isinstance(block, dict) else None
        if not isinstance(box, dict):
            continue
        try:
            x0 = max(0.0, float(box.get("x0") or 0.0))
            top = max(0.0, float(box.get("top") or 0.0))
            x1 = min(width, float(box.get("x1") or 0.0)) if width else float(box.get("x1") or 0.0)
            bottom = min(height, float(box.get("bottom") or 0.0)) if height else float(box.get("bottom") or 0.0)
        except (TypeError, ValueError):
            continue
        if x1 > x0 and bottom > top:
            boxes.append((x0, top, x1, bottom))

    intervals = sorted((top, bottom) for _, top, _, bottom in boxes)
    merged: list[list[float]] = []
    for top, bottom in intervals:
        if merged and top <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], bottom)
        else:
            merged.append([top, bottom])
    gaps: list[float] = []
    cursor = 0.0
    for top, bottom in merged:
        gaps.append(max(0.0, top - cursor))
        cursor = max(cursor, bottom)
    if height:
        gaps.append(max(0.0, height - cursor))
    max_gap_ratio = max(gaps, default=height if not boxes else 0.0) / height if height else 0.0
    text_area_ratio = (
        min(1.0, sum((x1 - x0) * (bottom - top) for x0, top, x1, bottom in boxes) / page_area)
        if page_area
        else 0.0
    )
    return {
        "status": "complete" if width and height else "unavailable",
        "block_count": len(boxes),
        "text_bbox_area_ratio": round(text_area_ratio, 4),
        "max_vertical_gap_ratio": round(max_gap_ratio, 4),
        "large_blank_region": bool(height and max_gap_ratio >= 0.18),
    }


def bbox_metrics_from_native_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    direct = evidence.get("bbox_metrics")
    if isinstance(direct, dict):
        return direct
    layout = evidence.get("layout") if isinstance(evidence.get("layout"), dict) else {}
    blocks = evidence.get("text_blocks") if isinstance(evidence.get("text_blocks"), list) else []
    if blocks:
        return bbox_layout_metrics(blocks, layout.get("page_size"))

    # Older page evidence retained bbox-layout XHTML inside the command record.
    # Derive compact metrics without requiring the page to be extracted again.
    command = layout.get("command") if isinstance(layout.get("command"), dict) else {}
    xml = command.get("stdout")
    if not isinstance(xml, str) or not xml.strip():
        return {
            "status": "unavailable",
            "block_count": int(layout.get("block_count") or 0),
            "text_bbox_area_ratio": 0.0,
            "max_vertical_gap_ratio": 0.0,
            "large_blank_region": False,
        }
    try:
        root = ET.fromstring(xml)
        page_node = next(
            (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "page"),
            None,
        )
        if page_node is None:
            raise ValueError("bbox page missing")
        parsed_blocks = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "block":
                continue
            parsed_blocks.append(
                {
                    "bbox": {
                        "x0": node.attrib.get("xMin", 0),
                        "top": node.attrib.get("yMin", 0),
                        "x1": node.attrib.get("xMax", 0),
                        "bottom": node.attrib.get("yMax", 0),
                    }
                }
            )
        return bbox_layout_metrics(
            parsed_blocks,
            {
                "width": page_node.attrib.get("width", 0),
                "height": page_node.attrib.get("height", 0),
            },
        )
    except (ET.ParseError, TypeError, ValueError):
        return {
            "status": "failed",
            "block_count": int(layout.get("block_count") or 0),
            "text_bbox_area_ratio": 0.0,
            "max_vertical_gap_ratio": 0.0,
            "large_blank_region": False,
        }


def page_needs_render_probe(
    *, native_text: str, images: PageImageStats, bbox_metrics: dict[str, Any], table_count: Any
) -> bool:
    non_ws = sum(1 for char in str(native_text or "").replace("\f", "") if not char.isspace())
    table_present = isinstance(table_count, int) and not isinstance(table_count, bool) and table_count > 0
    return bool(
        visual_text_signals(native_text)["present"]
        or images.image_count > 0
        or bbox_metrics.get("large_blank_region")
        or table_present
        or non_ws < MIN_NATIVE_TEXT_CHARS
    )


def _read_pgm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    position = 0
    tokens: list[bytes] = []
    while len(tokens) < 4:
        while position < len(data) and data[position] in b" \t\r\n":
            position += 1
        if position < len(data) and data[position] == ord("#"):
            position = data.find(b"\n", position)
            if position < 0:
                raise DetectionError("invalid PGM comment")
            continue
        start = position
        while position < len(data) and data[position] not in b" \t\r\n":
            position += 1
        if start == position:
            raise DetectionError("invalid PGM header")
        tokens.append(data[start:position])
    if tokens[0] != b"P5":
        raise DetectionError("pdftoppm did not emit binary PGM")
    width, height, maximum = (int(value) for value in tokens[1:])
    if maximum > 255:
        raise DetectionError("16-bit PGM is unsupported")
    if position < len(data) and data[position:position + 2] == b"\r\n":
        position += 2
    elif position < len(data) and data[position] in b" \t\r\n":
        position += 1
    pixels = data[position: position + width * height]
    if len(pixels) != width * height:
        raise DetectionError("PGM raster is truncated")
    return width, height, pixels


def _grouped_long_lines(
    width: int, height: int, pixels: bytes, *, horizontal: bool, dark: int = 80
) -> int:
    outer = height if horizontal else width
    inner = width if horizontal else height
    threshold = max(8, int(inner * (0.22 if horizontal else 0.16)))
    qualifying: list[int] = []
    for outer_index in range(outer):
        longest = current = 0
        for inner_index in range(inner):
            offset = (
                outer_index * width + inner_index
                if horizontal
                else inner_index * width + outer_index
            )
            if pixels[offset] <= dark:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        if longest >= threshold:
            qualifying.append(outer_index)
    groups = 0
    previous = -2
    for value in qualifying:
        if value != previous + 1:
            groups += 1
        previous = value
    return groups


def inspect_rendered_structure(
    source: Path, page: int, *, pdftoppm: str | None = None
) -> dict[str, Any]:
    """Detect line/grid structure locally; never invoke OCR or a model."""

    executable = pdftoppm or runtime_tool("pdftoppm")
    if not executable:
        return {"status": "unavailable", "visual_structure_present": False, "structure_types": []}
    with tempfile.TemporaryDirectory(prefix="pdf-page-structure-") as temporary:
        prefix = Path(temporary) / f"p{page:03d}"
        result = run_command(
            [
                executable,
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                "-gray",
                "-scale-to",
                "600",
                str(source),
                str(prefix),
            ],
            timeout=120,
        )
        pgm = prefix.with_suffix(".pgm")
        if result.returncode != 0 or not pgm.is_file():
            return {
                "status": "failed",
                "visual_structure_present": False,
                "structure_types": [],
                "error": (result.stderr or "pdftoppm did not emit PGM")[-400:],
            }
        try:
            width, height, pixels = _read_pgm(pgm)
        except (OSError, ValueError, DetectionError) as exc:
            return {
                "status": "failed",
                "visual_structure_present": False,
                "structure_types": [],
                "error": str(exc),
            }
    horizontal_lines = _grouped_long_lines(width, height, pixels, horizontal=True)
    vertical_lines = _grouped_long_lines(width, height, pixels, horizontal=False)
    structure_types: list[str] = []
    if horizontal_lines >= 3 and vertical_lines >= 3:
        structure_types.append("table-or-matrix")
    elif horizontal_lines >= 1 and vertical_lines >= 1:
        structure_types.append("chart-axis-or-flow-lines")
    return {
        "status": "passed",
        "width": width,
        "height": height,
        "long_horizontal_line_groups": horizontal_lines,
        "long_vertical_line_groups": vertical_lines,
        "visual_structure_present": bool(structure_types),
        "structure_types": structure_types,
    }


def decide_page_route(
    *,
    page: int,
    native_text: str,
    images: PageImageStats,
    bbox_metrics: dict[str, Any] | None = None,
    table_count: Any = 0,
    rendered_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose one page route from corroborated deterministic evidence."""

    bbox = bbox_metrics or {}
    rendered = rendered_structure or {
        "status": "not-run",
        "visual_structure_present": False,
        "structure_types": [],
    }
    cue = visual_text_signals(native_text)
    normalized_text = str(native_text or "").replace("\f", "")
    non_ws = sum(1 for char in normalized_text if not char.isspace())
    native_lines = len([line for line in normalized_text.splitlines() if line.strip()])
    bbox_area = float(bbox.get("text_bbox_area_ratio") or 0.0)
    bbox_blocks = int(bbox.get("block_count") or 0)
    native_usable = non_ws >= MIN_NATIVE_TEXT_CHARS
    native_body_sufficient = bool(
        native_usable
        and (
            non_ws >= MIN_NATIVE_BODY_CHARS
            or native_lines >= 4
            or bbox_area >= 0.04
            or bbox_blocks >= 4
        )
    )
    image_object_present = images.image_count > 0
    meaningful_image = bool(
        image_object_present
        and (
            images.max_area_ratio >= MEANINGFUL_IMAGE_AREA_RATIO
            or images.total_area_ratio >= MEANINGFUL_IMAGE_AREA_RATIO
        )
    )
    table_present = isinstance(table_count, int) and not isinstance(table_count, bool) and table_count > 0
    raster_structure = bool(rendered.get("visual_structure_present"))
    large_blank = bool(bbox.get("large_blank_region"))
    unconfirmed_single_table = bool(
        table_count == 1
        and rendered.get("status") == "passed"
        and not raster_structure
    )
    reason_codes: list[str] = []

    corroborated_visual = image_object_present or table_present or raster_structure
    if not native_usable or (not native_body_sufficient and corroborated_visual):
        if corroborated_visual:
            route = "visual-only"
            needs_review = False
            reason_codes.append("native-text-empty-or-insufficient")
            if image_object_present:
                reason_codes.append("page-image-object-present")
            if table_present or "table-or-matrix" in (rendered.get("structure_types") or []):
                reason_codes.append("table-or-matrix-structure-present")
            if raster_structure:
                reason_codes.append("rendered-visual-structure-present")
        elif rendered.get("status") == "passed" and not cue["present"]:
            route = "native-only"
            needs_review = False
            reason_codes.append("sparse-native-page-no-visual-content")
        else:
            route = "native-only"
            needs_review = True
            reason_codes.extend(
                ["native-text-insufficient", "visual-evidence-not-corroborated"]
            )
    elif (
        native_body_sufficient
        and image_object_present
        and images.max_area_ratio >= FULL_PAGE_BACKGROUND_MIN_AREA_RATIO
        and not cue["present"]
        and (not table_present or unconfirmed_single_table)
        and not raster_structure
        and rendered.get("status") == "passed"
    ):
        # Many exported ebooks carry a page-sized background raster underneath
        # a complete searchable text layer.  Area alone is not semantic visual
        # evidence: when both structure probes are clean, a model call merely
        # transcribes the same page again and can multiply runtime by hours.
        route = "native-only"
        needs_review = False
        reason_codes.extend(
            ["native-text-usable", "full-page-background-with-complete-text-layer"]
        )
        if unconfirmed_single_table:
            reason_codes.append("single-table-signal-unconfirmed-by-render")
    elif (
        cue["present"]
        and not image_object_present
        and not table_present
        and not raster_structure
    ):
        # A textual reference is not proof that the referenced figure survived
        # as an embedded PDF image.  The rendered page is still authoritative,
        # so route it through the local visual/OCR path instead of leaving a
        # permanently incomplete native-only page.
        route = "native-plus-visual"
        needs_review = False
        reason_codes.extend(
            [
                "native-text-usable",
                "uncorroborated-visual-text-cue",
                "visual-cue-requires-page-corroboration",
            ]
        )
    elif cue["present"] and image_object_present:
        route = "native-plus-visual"
        needs_review = False
        reason_codes.extend(["native-text-usable", "visual-cue-with-image-object"])
    elif meaningful_image:
        route = "native-plus-visual"
        needs_review = False
        reason_codes.extend(["native-text-usable", "meaningful-page-image-present"])
        if large_blank:
            reason_codes.append("bbox-large-blank-region")
        if raster_structure:
            reason_codes.append("rendered-visual-structure-present")
    elif table_present:
        route = "native-plus-visual"
        needs_review = False
        reason_codes.extend(
            ["native-text-usable", "table-or-matrix-structure-present"]
        )
    elif image_object_present and raster_structure:
        route = "native-plus-visual"
        needs_review = False
        reason_codes.extend(
            ["native-text-usable", "page-image-object-present", "rendered-visual-structure-present"]
        )
    elif raster_structure and large_blank:
        route = "native-plus-visual"
        needs_review = False
        reason_codes.extend(
            ["native-text-usable", "rendered-visual-structure-present", "bbox-large-blank-region"]
        )
    else:
        route = "native-only"
        needs_review = False
        reason_codes.append("native-text-usable-no-corroborated-visual-content")

    table_detection = {
        "status": "complete" if isinstance(table_count, int) and not isinstance(table_count, bool) else "unavailable",
        "pdfplumber_count": table_count,
        "present": table_present,
    }
    image_stats = images.as_dict()
    return {
        "page": int(page),
        "route": route,
        "status": "needs-review" if needs_review else "passed",
        "needs_review": needs_review,
        "visual_model_required": route in {"native-plus-visual", "visual-only"},
        "reason_codes": reason_codes,
        "native_text_chars": non_ws,
        "native_text_lines": native_lines,
        "native_body_sufficient": native_body_sufficient,
        "text_signals": cue,
        "image_stats": image_stats,
        "bbox_metrics": bbox,
        "table_detection": table_detection,
        "rendered_structure": rendered,
        "signals": {
            "native_non_whitespace_chars": non_ws,
            "visual_text_cues": cue,
            "images": image_stats,
            "bbox": bbox,
            "pdfplumber_table_count": table_count,
            "rendered_structure": rendered,
        },
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reader_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        return "Unknown"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_ndjson(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def runtime_tool(name: str) -> str | None:
    candidates = [
        RUNTIME_ROOT / "bin" / "override" / name,
        RUNTIME_ROOT / "native" / "poppler" / "poppler" / "bin" / name,
    ]
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def require_tool(name: str) -> str:
    tool = runtime_tool(name)
    if not tool:
        raise DetectionError(f"required local tool is unavailable: {name}")
    return tool


def run_command(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DetectionError(f"command failed: {' '.join(command)} ({exc})") from exc


def safe_slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return cleaned[:48] or "source"


def parse_page_size(stdout: str) -> tuple[float, float] | None:
    match = PAGE_SIZE_RE.search(stdout)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def parse_page_count(stdout: str) -> int:
    match = PAGES_RE.search(stdout)
    if not match:
        raise DetectionError("pdfinfo did not report total pages")
    return int(match.group(1))


def read_source_list(path: Path) -> list[Path]:
    if not path.is_file():
        raise DetectionError(f"source list does not exist: {path}")
    result: list[Path] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line).expanduser()
        if not candidate.is_absolute():
            candidate = (path.parent / candidate).resolve()
        if not candidate.is_file():
            raise DetectionError(f"source list line {line_number} is not a readable file: {line}")
        result.append(candidate.resolve())
    return result


def discover_sources(
    *,
    sources: list[Path],
    source_dirs: list[Path],
    source_list: Path | None,
) -> list[Path]:
    discovered: list[Path] = []
    for source in sources:
        resolved = source.expanduser().resolve()
        if not resolved.is_file():
            raise DetectionError(f"source does not exist: {source}")
        discovered.append(resolved)
    for source_dir in source_dirs:
        root = source_dir.expanduser().resolve()
        if not root.is_dir():
            raise DetectionError(f"source directory does not exist: {source_dir}")
        for path in sorted(root.rglob("*.pdf"), key=lambda item: str(item)):
            if path.is_file():
                discovered.append(path.resolve())
        for path in sorted(root.rglob("*.PDF"), key=lambda item: str(item)):
            if path.is_file():
                discovered.append(path.resolve())
    if source_list is not None:
        discovered.extend(read_source_list(source_list.expanduser().resolve()))
    if not discovered:
        raise DetectionError("provide at least one --source, --source-dir, or --source-list")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in discovered:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if path not in seen:
            seen.add(path)
            unique.append(path)
    if not unique:
        raise DetectionError("no PDF sources were discovered")
    return unique


def relative_path_for_source(source: Path, source_root: Path | None) -> str:
    if source_root and source.is_relative_to(source_root):
        return str(source.relative_to(source_root))
    return source.name


def modified_at_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def snapshot_source(source: Path, source_root: Path | None) -> SourceSnapshot:
    return SourceSnapshot(
        source_path=source,
        relative_path=relative_path_for_source(source, source_root),
        file_size=source.stat().st_size,
        modified_at=modified_at_iso(source),
    )


def select_sample_pages(total_pages: int) -> list[int]:
    if total_pages < 1:
        raise DetectionError("total_pages must be positive")
    if total_pages <= 10:
        return list(range(1, total_pages + 1))
    candidates = [
        1,
        2,
        3,
        4,
        5,
        max(1, total_pages // 4),
        max(1, total_pages // 2),
        max(1, (total_pages * 3) // 4),
        max(1, total_pages - 1),
        total_pages,
    ]
    return sorted({page for page in candidates if 1 <= page <= total_pages})


def parse_pdfimages_output(stdout: str, *, page_size: tuple[float, float] | None) -> dict[int, PageImageStats]:
    stats: dict[int, PageImageStats] = {}
    page_area = None
    if page_size is not None:
        page_area = page_size[0] * page_size[1]
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("page") or set(line) == {"-"}:
            continue
        parts = line.split()
        if len(parts) < 16:
            continue
        try:
            page = int(parts[0])
            image_type = parts[2]
            width = int(parts[3])
            height = int(parts[4])
            xppi = float(parts[12])
            yppi = float(parts[13])
        except (TypeError, ValueError):
            continue
        if image_type == "smask":
            continue
        page_stats = stats.setdefault(page, PageImageStats())
        page_stats.image_count += 1
        if not page_area or xppi <= 0 or yppi <= 0:
            continue
        display_width_pt = width / xppi * 72.0
        display_height_pt = height / yppi * 72.0
        area_ratio = max(0.0, min(2.0, (display_width_pt * display_height_pt) / page_area))
        page_stats.total_area_ratio += area_ratio
        page_stats.max_area_ratio = max(page_stats.max_area_ratio, area_ratio)
    return stats


def extract_text_metrics(pdftotext: str, source: Path, page: int) -> dict[str, Any]:
    command = [pdftotext, "-f", str(page), "-l", str(page), "-layout", str(source), "-"]
    result = run_command(command, timeout=120)
    if result.returncode != 0:
        raise DetectionError(
            f"pdftotext failed for {source.name} page {page}: {(result.stderr or '').strip()[-400:]}"
        )
    raw_text = result.stdout.replace("\f", "")
    lines = raw_text.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]
    non_ws = sum(1 for char in raw_text if not char.isspace())
    long_line_count = sum(1 for line in non_empty_lines if sum(1 for char in line if not char.isspace()) >= 20)
    token_count = len(TOKEN_RE.findall(raw_text))
    avg_non_ws_per_line = (
        round(non_ws / len(non_empty_lines), 3) if non_empty_lines else 0.0
    )
    cjk_chars = sum(1 for char in raw_text if CJK_RE.match(char))
    cjk_ratio = round(cjk_chars / max(non_ws, 1), 4) if non_ws else 0.0
    garbled_chars = len(GARBLED_RE.findall(raw_text))
    garbled_ratio = round(garbled_chars / max(non_ws, 1), 4) if non_ws else 0.0
    keyword_hits = sorted({keyword for keyword in BODY_KEYWORDS if keyword in raw_text})
    native_text_coverage = (
        round(min(1.0, non_ws / max(120.0, len(non_empty_lines) * 24.0)), 4)
        if non_empty_lines
        else 0.0
    )
    preview = raw_text.strip().replace("\n", " ")[:160]
    return {
        "chars": len(raw_text),
        "non_ws": non_ws,
        "line_count": len(lines),
        "non_empty_line_count": len(non_empty_lines),
        "long_line_count": long_line_count,
        "token_count": token_count,
        "avg_non_ws_per_line": avg_non_ws_per_line,
        "native_chars": len(raw_text),
        "non_whitespace_chars": non_ws,
        "non_empty_lines": len(non_empty_lines),
        "native_text_coverage": native_text_coverage,
        "contains_body_keywords": bool(keyword_hits),
        "keyword_hits": keyword_hits,
        "contains_cjk": cjk_chars > 0,
        "cjk_ratio": cjk_ratio,
        "garbled_char_ratio": garbled_ratio,
        "recoverable_structure": (
            long_line_count >= 2
            or any(
                line.lstrip().startswith(("-", "*", "1.", "2.", "3.", "#"))
                for line in non_empty_lines
            )
        ),
        "table_or_multicolumn_risk": (
            len(non_empty_lines) >= 6
            and avg_non_ws_per_line < 12
            and token_count >= 20
        ),
        "preview": preview,
    }


def page_ranges(pages: list[int]) -> list[tuple[int, int]]:
    if not pages:
        return []
    ordered = sorted(set(pages))
    start = ordered[0]
    end = ordered[0]
    ranges: list[tuple[int, int]] = []
    for page in ordered[1:]:
        if page == end + 1:
            end = page
            continue
        ranges.append((start, end))
        start = page
        end = page
    ranges.append((start, end))
    return ranges


def extract_image_stats_for_pages(
    pdfimages: str,
    source: Path,
    pages: list[int],
    *,
    page_size: tuple[float, float] | None,
) -> dict[int, PageImageStats]:
    collected: dict[int, PageImageStats] = {}
    for start, end in page_ranges(pages):
        result = run_command(
            [pdfimages, "-f", str(start), "-l", str(end), "-list", str(source)],
            timeout=240,
        )
        if result.returncode != 0:
            raise DetectionError(
                f"pdfimages failed for {source} pages {start}-{end}: {(result.stderr or '').strip()[-400:]}"
            )
        collected.update(parse_pdfimages_output(result.stdout, page_size=page_size))
    return collected


def classify_page_signal(text: dict[str, Any], images: PageImageStats) -> dict[str, Any]:
    non_ws = int(text.get("non_ws", 0))
    long_line_count = int(text.get("long_line_count", 0))
    avg_non_ws_per_line = float(text.get("avg_non_ws_per_line", 0.0))
    preview = str(text.get("preview") or "")
    native_text_coverage = float(
        text.get(
            "native_text_coverage",
            round(min(1.0, non_ws / max(120.0, max(int(text.get("non_empty_line_count", 0)), 1) * 24.0)), 4)
            if non_ws
            else 0.0,
        )
    )
    contains_body_keywords = bool(text.get("contains_body_keywords")) or any(
        keyword in preview for keyword in BODY_KEYWORDS
    )
    cjk_ratio = float(
        text.get(
            "cjk_ratio",
            1.0 if preview and CJK_RE.search(preview) else (1.0 if non_ws >= 120 else 0.0),
        )
    )
    garbled_ratio = float(text.get("garbled_char_ratio", 0.0))
    complex_layout = bool(text.get("table_or_multicolumn_risk"))
    full_page_image = images.max_area_ratio >= 0.85 or images.total_area_ratio >= 0.95
    image_heavy = images.max_area_ratio >= 0.25 or images.total_area_ratio >= 0.35

    if non_ws < 20 or garbled_ratio >= 0.2:
        text_profile = "none"
    elif (
        non_ws >= 220
        and long_line_count >= 2
        and avg_non_ws_per_line >= 12
        and cjk_ratio >= 0.15
        and native_text_coverage >= 0.35
    ):
        text_profile = "dense"
    elif non_ws >= 80 and cjk_ratio >= 0.1 and (contains_body_keywords or native_text_coverage >= 0.25):
        text_profile = "present"
    else:
        text_profile = "sparse"

    if complex_layout:
        layout_profile = "sparse-layout"
    elif non_ws < 80 or (non_ws < 160 and long_line_count <= 1 and not contains_body_keywords):
        layout_profile = "diagram-like"
    elif long_line_count <= 2 or avg_non_ws_per_line < 10:
        layout_profile = "sparse-layout"
    else:
        layout_profile = "narrative"

    return {
        "text_profile": text_profile,
        "layout_profile": layout_profile,
        "full_page_image": full_page_image,
        "image_heavy": image_heavy,
    }


def classify_native_quality(text: dict[str, Any], images: PageImageStats) -> dict[str, Any]:
    signal = classify_page_signal(text, images)
    non_ws = int(text.get("non_ws", 0))
    if non_ws == 0:
        return {
            "status": "failed",
            "route": "vision",
            "fallback_reason": "page-native-text-empty",
        }
    if float(text.get("garbled_char_ratio", 0.0)) >= 0.2:
        return {
            "status": "failed",
            "route": "vision",
            "fallback_reason": "page-native-text-garbled",
        }
    if signal["text_profile"] in {"dense", "present"} and signal["layout_profile"] == "narrative":
        return {"status": "usable", "route": "native", "fallback_reason": None}
    if (
        signal["text_profile"] in {"dense", "present"}
        and not bool(text.get("table_or_multicolumn_risk"))
    ):
        return {"status": "usable", "route": "native", "fallback_reason": None}
    if bool(text.get("table_or_multicolumn_risk")):
        return {
            "status": "weak",
            "route": "vision",
            "fallback_reason": "page-native-structure-complex",
        }
    return {
        "status": "weak",
        "route": "vision",
        "fallback_reason": "page-native-text-coverage-low",
    }


def build_page_record(page: int, text_metrics: dict[str, Any], images: PageImageStats) -> dict[str, Any]:
    return {
        "page": page,
        "text": text_metrics,
        "images": images.as_dict(),
        "page_signal": classify_page_signal(text_metrics, images),
        "native_quality": classify_native_quality(text_metrics, images),
    }


def _record_native_quality(record: dict[str, Any]) -> dict[str, Any]:
    quality = record.get("native_quality")
    if isinstance(quality, dict) and quality.get("route"):
        return quality
    signal = record.get("page_signal") or {}
    text_profile = signal.get("text_profile")
    layout_profile = signal.get("layout_profile")
    if text_profile == "none":
        return {
            "status": "failed",
            "route": "vision",
            "fallback_reason": "page-native-text-empty",
        }
    if text_profile in {"dense", "present"} and layout_profile in {"narrative", "sparse-layout"}:
        return {"status": "usable", "route": "native", "fallback_reason": None}
    return {
        "status": "weak",
        "route": "vision",
        "fallback_reason": "page-native-text-coverage-low",
    }


def _record_recoverable_structure(record: dict[str, Any]) -> bool:
    text = record.get("text") if isinstance(record.get("text"), dict) else {}
    if "recoverable_structure" in text:
        return bool(text.get("recoverable_structure"))
    return (record.get("page_signal") or {}).get("layout_profile") == "narrative"


def _record_complex_layout(record: dict[str, Any]) -> bool:
    text = record.get("text") if isinstance(record.get("text"), dict) else {}
    return bool(text.get("table_or_multicolumn_risk"))


def should_short_circuit_vision_only(sample_records: list[dict[str, Any]]) -> bool:
    return False


def representative_pages(sample_records: list[dict[str, Any]], total_pages: int) -> dict[str, int]:
    result: dict[str, int] = {}
    if not sample_records:
        return result
    result["first_page"] = int(sample_records[0]["page"])
    toc = next(
        (
            int(item["page"])
            for item in sample_records
            if item["text"].get("contains_body_keywords") and "目录" in str(item["text"].get("preview") or "")
        ),
        None,
    )
    if toc is not None:
        result["toc_page"] = toc
    body = next(
        (
            int(item["page"])
            for item in sample_records
            if _record_native_quality(item)["route"] == "native"
            and item["page_signal"]["layout_profile"] == "narrative"
        ),
        None,
    )
    if body is not None:
        result["body_page"] = body
    longest = max(
        sample_records,
        key=lambda item: int(item["text"].get("non_ws", 0)),
    )
    if int(longest["text"].get("non_ws", 0)) > 0:
        result["long_body_page"] = int(longest["page"])
    image_heavy_text = next(
        (
            int(item["page"])
            for item in sample_records
            if item["page_signal"]["image_heavy"]
            and _record_native_quality(item)["route"] == "native"
        ),
        None,
    )
    if image_heavy_text is not None:
        result["image_heavy_text_page"] = image_heavy_text
    result["last_page"] = total_pages
    return result


def native_smoke_pages(sample_records: list[dict[str, Any]], total_pages: int) -> list[int]:
    pages: list[int] = []
    for page in representative_pages(sample_records, total_pages).values():
        if page not in pages:
            pages.append(page)
    return pages


def page_selector(pages: list[int]) -> str:
    return ",".join(str(page) for page in pages)


def summarise_document(
    *,
    total_pages: int,
    sample_records: list[dict[str, Any]],
    image_stats: dict[int, PageImageStats],
) -> dict[str, Any]:
    sampled_pages = len(sample_records)
    dense_pages = sum(
        1
        for item in sample_records
        if item["page_signal"]["text_profile"] == "dense"
    )
    text_present_pages = sum(
        1
        for item in sample_records
        if item["page_signal"]["text_profile"] in {"dense", "present"}
    )
    sparse_or_diagram_pages = sum(
        1
        for item in sample_records
        if item["page_signal"]["layout_profile"] in {"sparse-layout", "diagram-like"}
    )
    diagram_like_pages = sum(
        1 for item in sample_records if item["page_signal"]["layout_profile"] == "diagram-like"
    )
    zero_text_pages = sum(
        1 for item in sample_records if item["page_signal"]["text_profile"] == "none"
    )
    usable_native_pages = sum(
        1 for item in sample_records if _record_native_quality(item)["route"] == "native"
    )
    failed_native_pages = sum(
        1 for item in sample_records if _record_native_quality(item)["status"] == "failed"
    )
    recoverable_pages = sum(
        1 for item in sample_records if _record_recoverable_structure(item)
    )
    complex_layout_pages = sum(
        1 for item in sample_records if _record_complex_layout(item)
    )
    full_page_image_pages = sum(1 for item in image_stats.values() if item.max_area_ratio >= 0.85 or item.total_area_ratio >= 0.95)
    image_heavy_pages = sum(1 for item in image_stats.values() if item.max_area_ratio >= 0.25 or item.total_area_ratio >= 0.35)

    def ratio(count: int, whole: int) -> float:
        return round(count / whole, 4) if whole else 0.0

    return {
        "sampled_pages": sampled_pages,
        "dense_text_ratio": ratio(dense_pages, sampled_pages),
        "text_present_ratio": ratio(text_present_pages, sampled_pages),
        "sparse_or_diagram_ratio": ratio(sparse_or_diagram_pages, sampled_pages),
        "diagram_like_ratio": ratio(diagram_like_pages, sampled_pages),
        "zero_text_ratio": ratio(zero_text_pages, sampled_pages),
        "usable_native_ratio": ratio(usable_native_pages, sampled_pages),
        "failed_native_ratio": ratio(failed_native_pages, sampled_pages),
        "recoverable_structure_ratio": ratio(recoverable_pages, sampled_pages),
        "complex_layout_ratio": ratio(complex_layout_pages, sampled_pages),
        "full_page_image_ratio": ratio(full_page_image_pages, total_pages),
        "image_heavy_ratio": ratio(image_heavy_pages, total_pages),
        "full_page_image_pages": full_page_image_pages,
        "image_heavy_pages": image_heavy_pages,
        "representative_pages": representative_pages(sample_records, total_pages),
        "native_smoke_pages": native_smoke_pages(sample_records, total_pages),
    }


def decide_route(summary: dict[str, Any]) -> dict[str, Any]:
    dense_text_ratio = float(summary["dense_text_ratio"])
    text_present_ratio = float(summary["text_present_ratio"])
    sparse_or_diagram_ratio = float(summary["sparse_or_diagram_ratio"])
    diagram_like_ratio = float(summary["diagram_like_ratio"])
    zero_text_ratio = float(summary["zero_text_ratio"])
    usable_native_ratio = float(summary.get("usable_native_ratio", 0.0))
    failed_native_ratio = float(summary.get("failed_native_ratio", 0.0))
    recoverable_structure_ratio = float(summary.get("recoverable_structure_ratio", 0.0))
    complex_layout_ratio = float(summary.get("complex_layout_ratio", 0.0))
    image_heavy_ratio = float(summary["image_heavy_ratio"])
    reason_codes: list[str] = []

    if usable_native_ratio >= 0.6 and recoverable_structure_ratio >= 0.5:
        if (
            failed_native_ratio > 0
            or complex_layout_ratio >= 0.25
            or image_heavy_ratio >= 0.25
        ):
            decision = "hybrid"
            confidence = "high" if usable_native_ratio >= 0.75 else "medium"
            reason_codes.extend(["native-text-exists", "visual-structure-needs-staged-review"])
            if failed_native_ratio > 0:
                reason_codes.append("sampled-native-page-incomplete")
            if complex_layout_ratio >= 0.25:
                reason_codes.append("sampled-pages-look-layout-sparse")
            if image_heavy_ratio >= 0.25:
                reason_codes.append("image-heavy-pages-present")
        else:
            decision = "native-only"
            confidence = "high" if dense_text_ratio >= 0.7 else "medium"
            reason_codes.extend(["dense-native-text-dominates", "visual-overhead-is-low"])
    elif usable_native_ratio >= 0.3 or text_present_ratio >= 0.5:
        decision = "hybrid"
        confidence = "medium"
        reason_codes.extend(["native-text-exists", "visual-structure-needs-staged-review"])
        if sparse_or_diagram_ratio >= 0.25 or complex_layout_ratio >= 0.25:
            reason_codes.append("sampled-pages-look-layout-sparse")
        if image_heavy_ratio > 0:
            reason_codes.append("image-heavy-pages-present")
    elif text_present_ratio < 0.3 and (zero_text_ratio >= 0.5 or failed_native_ratio >= 0.5):
        decision = "vision-only"
        confidence = "high" if zero_text_ratio >= 0.7 and failed_native_ratio >= 0.7 else "medium"
        reason_codes.extend(["sampled-pages-have-no-usable-native-text", "diagram-or-scan-dominant"])
        if diagram_like_ratio >= 0.5:
            reason_codes.append("diagram-like-pages-dominate")
    elif diagram_like_ratio >= 0.8 and dense_text_ratio == 0 and usable_native_ratio == 0:
        decision = "vision-only"
        confidence = "medium"
        reason_codes.extend(["diagram-like-pages-dominate", "native-text-lacks-document-structure"])
    else:
        decision = "vision-only"
        confidence = "medium"
        reason_codes.extend(["native-text-signal-is-weak", "fallback-to-visual-route"])

    return {
        "route": decision,
        "confidence": confidence,
        "reason_codes": reason_codes,
    }


def recommended_entrypoint(route: str, smoke_pages: list[int] | None = None) -> str:
    smoke_arg = f" --smoke-pages {page_selector(smoke_pages)}" if smoke_pages else ""
    if route == "native-only":
        return (
            "python3 adapters/lan_pdf_markdown_graph.py --source <pdf> --pages all "
            f"--auto-run-root --evidence-level compact --no-model --batch-size 1{smoke_arg}"
        )
    if route == "hybrid":
        return (
            "python3 adapters/lan_pdf_markdown_graph.py --source <pdf> --pages all "
            f"--auto-run-root --evidence-level compact --batch-size 1 --dpi 220{smoke_arg}"
        )
    return (
        "python3 adapters/lan_pdf_markdown_graph.py --source <pdf> --pages all "
        "--auto-run-root --evidence-level compact --visual-only --dpi 220"
    )


def analyse_source(
    *,
    source_snapshot: SourceSnapshot,
    source_sha256: str,
    pdfinfo: str,
    pdftotext: str,
    pdfimages: str,
) -> dict[str, Any]:
    source = source_snapshot.source_path
    info_result = run_command([pdfinfo, str(source)], timeout=120)
    if info_result.returncode != 0:
        raise DetectionError(f"pdfinfo failed for {source}: {(info_result.stderr or '').strip()[-400:]}")
    total_pages = parse_page_count(info_result.stdout)
    page_size = parse_page_size(info_result.stdout)
    sample_pages = select_sample_pages(total_pages)
    image_result = run_command([pdfimages, "-list", str(source)], timeout=240)
    if image_result.returncode != 0:
        raise DetectionError(f"pdfimages failed for {source}: {(image_result.stderr or '').strip()[-400:]}")
    image_stats = parse_pdfimages_output(image_result.stdout, page_size=page_size)
    sample_records: list[dict[str, Any]] = []
    for page in sample_pages:
        text_metrics = extract_text_metrics(pdftotext, source, page)
        page_images = image_stats.get(page, PageImageStats())
        sample_records.append(build_page_record(page, text_metrics, page_images))
    summary = summarise_document(
        total_pages=total_pages,
        sample_records=sample_records,
        image_stats=image_stats,
    )
    decision = decide_route(summary)
    smoke_pages = summary.get("native_smoke_pages") or native_smoke_pages(
        sample_records, total_pages
    )
    return {
        "status": "complete",
        "source_path": str(source),
        "relative_path": source_snapshot.relative_path,
        "source_sha256": source_sha256,
        "file_size": source_snapshot.file_size,
        "modified_at": source_snapshot.modified_at,
        "total_pages": total_pages,
        "page_size_points": {
            "width": round(page_size[0], 3),
            "height": round(page_size[1], 3),
        } if page_size else None,
        "sample_pages": sample_pages,
        "sample_records": sample_records,
        "document_summary": summary,
        "decision": {
            **decision,
            "native_smoke_pages": smoke_pages,
            "native_smoke_selector": page_selector(smoke_pages),
            "recommended_entrypoint": recommended_entrypoint(decision["route"], smoke_pages),
        },
    }


def build_run_root(output_root: Path, sources: list[Path]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(
        "\n".join(str(path) for path in sources).encode("utf-8")
    ).hexdigest()[:10]
    return output_root / f"{timestamp}-{digest}"


def write_route_lists(run_root: Path, records: list[dict[str, Any]]) -> dict[str, str]:
    route_root = run_root / ROUTE_LISTS_DIRNAME
    outputs: dict[str, str] = {}
    for route in ("native-only", "hybrid", "vision-only"):
        selected = [
            item["source_path"]
            for item in records
            if item.get("status") == "complete" and item.get("decision", {}).get("route") == route
        ]
        path = route_root / f"{route}.txt"
        write_text(path, "\n".join(selected) + ("\n" if selected else ""))
        outputs[route] = str(path)
    return outputs


def read_route_report(report_path: Path) -> dict[str, Any] | None:
    if not report_path.is_file():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("contract") != CONTRACT or payload.get("version") not in READABLE_REPORT_VERSIONS:
        return None
    if not isinstance(payload.get("records"), list):
        return None
    return payload


def read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_latest_record_maps(report: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    # Reports from older policy versions remain readable for backfill and audit,
    # but their route decisions must not be reused after routing rules change.
    if not report or report.get("version") != VERSION:
        return {}, {}
    by_path: dict[str, dict[str, Any]] = {}
    by_sha: dict[str, dict[str, Any]] = {}
    for record in report.get("records", []):
        if record.get("status") != "complete":
            continue
        source_path = record.get("source_path")
        source_sha = record.get("source_sha256")
        if isinstance(source_path, str):
            by_path[source_path] = record
        if isinstance(source_sha, str):
            by_sha[source_sha] = record
    return by_path, by_sha


def can_reuse_by_path(record: dict[str, Any] | None, snapshot: SourceSnapshot) -> bool:
    if not record or record.get("status") != "complete":
        return False
    return (
        record.get("source_path") == str(snapshot.source_path)
        and record.get("file_size") == snapshot.file_size
        and record.get("modified_at") == snapshot.modified_at
    )


def reuse_record(
    record: dict[str, Any],
    *,
    snapshot: SourceSnapshot,
    source_sha256: str | None,
    latest_run_id: str | None,
    matched_on: str,
) -> dict[str, Any]:
    reused = copy.deepcopy(record)
    reused["source_path"] = str(snapshot.source_path)
    reused["relative_path"] = snapshot.relative_path
    reused["file_size"] = snapshot.file_size
    reused["modified_at"] = snapshot.modified_at
    if source_sha256 is not None:
        reused["source_sha256"] = source_sha256
    reused["reuse"] = {
        "mode": "current-index",
        "matched_on": matched_on,
        "latest_run_id": latest_run_id,
    }
    return reused


def publication_outputs(output_root: Path, source_root: Path | None) -> dict[str, Any]:
    outputs: dict[str, Any] = {
        "current_index": str(output_root / CURRENT_INDEX_NAME),
    }
    if source_root is None:
        return outputs
    reader_root = source_root / READER_ROOT_NAME
    outputs.update(
        {
            "summary": str(source_root / READER_SUMMARY_NAME),
            "reader_root": str(reader_root),
            "review": str(reader_root / REVIEW_NAME),
            "route_lists": {
                route: str(reader_root / filename)
                for route, filename in READER_ROUTE_NAMES.items()
            },
        }
    )
    return outputs


def current_report_path(output_root: Path, source_root: Path | None) -> Path | None:
    payload = read_json_object(output_root / CURRENT_INDEX_NAME)
    if not payload or payload.get("contract") != CONTRACT:
        return None
    entry: Any = None
    if source_root is not None:
        sources = payload.get("sources")
        if isinstance(sources, dict):
            entry = sources.get(str(source_root))
        if entry is None:
            candidate = payload.get("current")
            if isinstance(candidate, dict) and candidate.get("source_root") == str(source_root):
                entry = candidate
    else:
        entry = payload.get("current")
    if not isinstance(entry, dict) or not isinstance(entry.get("report"), str):
        return None
    return Path(entry["report"])


def load_latest_index_report(output_root: Path, source_root: Path | None) -> dict[str, Any] | None:
    candidates: list[Path] = []
    current_path = current_report_path(output_root, source_root)
    if current_path is not None:
        candidates.append(current_path)
    # Read-only compatibility lets the first v0.3 run reuse an older completed scan.
    if source_root is not None:
        candidates.append(source_root / "_pdf-route-detection.latest-report.json")
    candidates.extend(
        [
            output_root / "route-detection-index" / "latest-report.json",
            ROOT / "runs" / "real" / "route-detection-index" / "latest-report.json",
        ]
    )
    for candidate in candidates:
        payload = read_route_report(candidate)
        if payload is not None:
            return payload
    return None


def review_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record.get("status") != "complete"
        or record.get("decision", {}).get("confidence") == "low"
    ]


def write_reader_outputs(
    source_root: Path,
    report: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    outputs = publication_outputs(report_path.parent.parent, source_root)
    records = report.get("records", [])
    for route, path_str in outputs["route_lists"].items():
        selected = [
            record["source_path"]
            for record in records
            if record.get("status") == "complete"
            and record.get("decision", {}).get("route") == route
        ]
        write_text(Path(path_str), "\n".join(selected) + ("\n" if selected else ""))

    review_path = Path(outputs["review"])
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["状态", "转换路线", "置信度", "文件路径", "判定理由", "错误信息"])
        for record in review_records(records):
            decision = record.get("decision", {})
            reasons = [
                REASON_LABELS.get(code, code)
                for code in decision.get("reason_codes", [])
            ]
            writer.writerow(
                [
                    STATUS_LABELS.get(
                        record.get("status"),
                        record.get("status", ""),
                    ),
                    ROUTE_LABELS.get(decision.get("route"), decision.get("route", "")),
                    CONFIDENCE_LABELS.get(decision.get("confidence"), decision.get("confidence", "")),
                    record.get("source_path", ""),
                    "；".join(reasons),
                    " ".join(str(record.get("error", "")).split()),
                ]
            )

    summary = report["summary"]
    source_count = int(report.get("input", {}).get("source_count", len(records)))
    lines = [
        "# PDF 转换路线总览",
        "",
        f"- 检测状态：{STATUS_LABELS.get(report.get('status'), report.get('status', 'Unknown'))}",
        f"- PDF 总数：{source_count}",
        f"- 检测完成：{summary.get('complete_count', 0)}",
        f"- 检测失败：{summary.get('failed_count', 0)}",
        f"- 待人工复核：{len(review_records(records))}",
        f"- 生成时间：{reader_timestamp(report.get('detected_at'))}",
        f"- 运行 ID：`{report.get('run_id', 'Unknown')}`",
        f"- 完整审计报告：`{report_path}`",
        "",
        "## 路线结论",
        "",
        "| 转换路线 | 数量 | 占全部 PDF | 清单 |",
        "|---|---:|---:|---|",
    ]
    for route in ("native-only", "hybrid", "vision-only"):
        count = int(summary.get("routes", {}).get(route, 0))
        ratio = f"{count / source_count:.1%}" if source_count else "0.0%"
        filename = READER_ROUTE_NAMES[route]
        lines.append(
            f"| {ROUTE_LABELS[route]} | {count} | {ratio} "
            f"| [{filename}]({READER_ROOT_NAME}/{filename}) |"
        )
    lines.extend(
        [
            "",
            "## 人工复核",
            "",
            "复核表只收录检测失败和低置信度记录，避免把全部结果再次堆给人工阅读。",
            "",
            f"打开 [{REVIEW_NAME}]({READER_ROOT_NAME}/{REVIEW_NAME}) 查看。",
            "",
            "## 使用边界",
            "",
            "路线清单可直接交给后续批处理；完整 JSON、NDJSON 和逐文件记录只保存在工程审计目录。",
        ]
    )
    write_text(Path(outputs["summary"]), "\n".join(lines) + "\n")
    return outputs


def write_current_index(
    output_root: Path,
    source_root: Path | None,
    report: dict[str, Any],
    report_path: Path,
) -> Path:
    index_path = output_root / CURRENT_INDEX_NAME
    existing = read_json_object(index_path) or {}
    sources = existing.get("sources") if isinstance(existing.get("sources"), dict) else {}
    entry = {
        "run_id": report["run_id"],
        "detected_at": report["detected_at"],
        "status": report["status"],
        "source_root": str(source_root) if source_root else None,
        "report": str(report_path),
    }
    if source_root is not None:
        sources[str(source_root)] = entry
    write_json(
        index_path,
        {
            "contract": CONTRACT,
            "version": VERSION,
            "current": entry,
            "sources": sources,
        },
    )
    return index_path


def write_record_outputs(run_root: Path, records: list[dict[str, Any]]) -> tuple[Path, Path]:
    records_root = run_root / RECORDS_DIRNAME
    ndjson_path = run_root / RECORDS_NAME
    write_text(ndjson_path, "")
    for index, record in enumerate(records, 1):
        source_name = Path(str(record.get("source_path", "source"))).stem
        write_json(records_root / f"{index:04d}-{safe_slug(source_name)}.json", record)
        append_ndjson(ndjson_path, record)
    return records_root, ndjson_path


def summarise_records(
    records: list[dict[str, Any]],
    *,
    reused_count: int,
) -> tuple[str, dict[str, Any]]:
    failures = sum(1 for record in records if record.get("status") != "complete")
    by_route = {
        route: sum(
            1
            for record in records
            if record.get("status") == "complete"
            and record.get("decision", {}).get("route") == route
        )
        for route in ("native-only", "hybrid", "vision-only")
    }
    status = (
        "failed"
        if records and failures == len(records)
        else "partial"
        if failures
        else "complete"
    )
    return status, {
        "complete_count": len(records) - failures,
        "failed_count": failures,
        "reused_count": reused_count,
        "routes": by_route,
    }


def publish_report(
    *,
    report: dict[str, Any],
    run_root: Path,
    output_root: Path,
    source_root: Path | None,
) -> Path:
    report_path = run_root / REPORT_NAME
    report["outputs"]["report"] = str(report_path)
    report["outputs"]["publication"] = publication_outputs(output_root, source_root)
    write_json(report_path, report)
    if source_root is not None:
        write_reader_outputs(source_root, report, report_path)
    write_current_index(output_root, source_root, report, report_path)
    return report_path


def run_backfill(args: argparse.Namespace, backfill_report: Path) -> Path:
    if args.source or args.source_list is not None or len(args.source_dir) > 1:
        raise DetectionError("--backfill-report may only be combined with one optional --source-dir")
    source_report_path = backfill_report.expanduser().resolve()
    source_report = read_route_report(source_report_path)
    if source_report is None:
        raise DetectionError(f"backfill report is not a readable {CONTRACT} report: {source_report_path}")

    if len(args.source_dir) == 1:
        source_root = args.source_dir[0].expanduser().resolve()
    else:
        raw_source_root = source_report.get("source_root")
        source_root = Path(raw_source_root).expanduser().resolve() if isinstance(raw_source_root, str) else None
    if source_root is None or not source_root.is_dir():
        raise DetectionError("backfill requires an existing source root from the report or one --source-dir")

    output_root = args.output_root.expanduser().resolve()
    records = copy.deepcopy(source_report["records"])
    source_paths = [Path(str(record.get("source_path", "source.pdf"))) for record in records]
    run_root = build_run_root(output_root, source_paths)
    run_root.mkdir(parents=True, exist_ok=True)
    records_root, ndjson_path = write_record_outputs(run_root, records)
    route_lists = write_route_lists(run_root, records)
    status, summary = summarise_records(
        records,
        reused_count=sum(1 for record in records if record.get("status") == "complete"),
    )
    report = {
        "contract": CONTRACT,
        "version": VERSION,
        "run_id": run_root.name,
        "detected_at": now(),
        "status": status,
        "source_root": str(source_root),
        "input": {
            "sources": [str(path) for path in source_paths],
            "source_count": len(records),
        },
        "tooling": source_report.get("tooling", {}),
        "outputs": {
            "run_root": str(run_root),
            "records_ndjson": str(ndjson_path),
            "records_root": str(records_root),
            "route_lists": route_lists,
        },
        "summary": summary,
        "migration": {
            "mode": "backfill-existing-report",
            "source_report": str(source_report_path),
            "source_report_sha256": sha256(source_report_path),
            "source_run_id": source_report.get("run_id"),
            "source_version": source_report.get("version"),
        },
        "records": records,
    }
    return publish_report(
        report=report,
        run_root=run_root,
        output_root=output_root,
        source_root=source_root,
    )


def run_detection(args: argparse.Namespace) -> Path:
    sources = discover_sources(
        sources=args.source,
        source_dirs=args.source_dir,
        source_list=args.source_list,
    )
    source_root = None
    if len(args.source_dir) == 1:
        source_root = args.source_dir[0].expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    latest_index_report = load_latest_index_report(output_root, source_root)
    latest_by_path, latest_by_sha = build_latest_record_maps(latest_index_report)
    latest_run_id = latest_index_report.get("run_id") if latest_index_report else None
    run_root = build_run_root(output_root, sources)
    run_root.mkdir(parents=True, exist_ok=True)

    pdfinfo: str | None = None
    pdftotext: str | None = None
    pdfimages: str | None = None

    records: list[dict[str, Any]] = []
    records_root = run_root / RECORDS_DIRNAME
    ndjson_path = run_root / RECORDS_NAME
    write_text(ndjson_path, "")
    reused_count = 0
    for index, source in enumerate(sources, 1):
        snapshot = snapshot_source(source, source_root)
        try:
            cached = latest_by_path.get(str(source))
            if can_reuse_by_path(cached, snapshot):
                reused_count += 1
                record = reuse_record(
                    cached,
                    snapshot=snapshot,
                    source_sha256=None,
                    latest_run_id=latest_run_id,
                    matched_on="path-size-mtime",
                )
            else:
                source_sha = sha256(source)
                cached = latest_by_sha.get(source_sha)
                if cached:
                    reused_count += 1
                    record = reuse_record(
                        cached,
                        snapshot=snapshot,
                        source_sha256=source_sha,
                        latest_run_id=latest_run_id,
                        matched_on="sha256",
                    )
                else:
                    if pdfinfo is None:
                        pdfinfo = require_tool("pdfinfo")
                        pdftotext = require_tool("pdftotext")
                        pdfimages = require_tool("pdfimages")
                    record = analyse_source(
                        source_snapshot=snapshot,
                        source_sha256=source_sha,
                        pdfinfo=pdfinfo,
                        pdftotext=pdftotext,
                        pdfimages=pdfimages,
                    )
        except DetectionError as exc:
            record = {
                "status": "failed",
                "source_path": str(source),
                "relative_path": snapshot.relative_path,
                "error": str(exc),
            }
        records.append(record)
        write_json(records_root / f"{index:04d}-{safe_slug(source.stem)}.json", record)
        append_ndjson(ndjson_path, record)

    route_lists = write_route_lists(run_root, records)
    status, summary = summarise_records(records, reused_count=reused_count)
    report = {
        "contract": CONTRACT,
        "version": VERSION,
        "run_id": run_root.name,
        "detected_at": now(),
        "status": status,
        "source_root": str(source_root) if source_root else None,
        "input": {
            "sources": [str(path) for path in sources],
            "source_count": len(sources),
        },
        "tooling": {
            "pdfinfo": pdfinfo,
            "pdftotext": pdftotext,
            "pdfimages": pdfimages,
        },
        "outputs": {
            "run_root": str(run_root),
            "records_ndjson": str(ndjson_path),
            "records_root": str(records_root),
            "route_lists": route_lists,
        },
        "summary": summary,
        "records": records,
    }
    return publish_report(
        report=report,
        run_root=run_root,
        output_root=output_root,
        source_root=source_root,
    )


def run(args: argparse.Namespace) -> Path:
    backfill_report = getattr(args, "backfill_report", None)
    if backfill_report is not None:
        return run_backfill(args, backfill_report)
    return run_detection(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        default=[],
        help="Explicit PDF source. May be repeated.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        action="append",
        default=[],
        help="Directory scanned recursively for PDFs. May be repeated.",
    )
    parser.add_argument(
        "--source-list",
        type=Path,
        help="UTF-8 text file containing one source path per line.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Parent directory that will receive the route-detection run root.",
    )
    parser.add_argument(
        "--backfill-report",
        type=Path,
        help="Republish an existing route report into the current layout without rescanning PDFs.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        report_path = run(args)
    except DetectionError as exc:
        parser.exit(1, f"{exc}\n")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
