#!/usr/bin/env python3
"""Deterministically audit reader assets for text-heavy crops."""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import numpy as np
from PIL import Image


IMAGE_RE = re.compile(
    r"^\s*!\[([^\]]*)\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)\s*$"
)
NORM_CHAR_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")
PREFERRED_TESSERACT_LANGS = ("chi_sim", "eng")
ROOT = Path(__file__).resolve().parents[3]
APPLE_VISION_SOURCE = ROOT / "adapters/apple_vision_ocr.swift"
MINIMUM_DELETION_SCORE = 95
AUDIT_VERSION = "0.3.0"


@dataclass(frozen=True)
class WordBox:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def normalize_visible_text(text: str) -> str:
    return "".join(NORM_CHAR_RE.findall(text)).lower()


def multiset_overlap_ratio(left: str, right: str) -> float:
    if not left:
        return 0.0
    left_counts: dict[str, int] = {}
    for char in left:
        left_counts[char] = left_counts.get(char, 0) + 1
    right_counts: dict[str, int] = {}
    for char in right:
        right_counts[char] = right_counts.get(char, 0) + 1
    shared = 0
    for char, count in left_counts.items():
        shared += min(count, right_counts.get(char, 0))
    return shared / len(left)


def ordered_overlap_ratio(left: str, right: str) -> float:
    if not left:
        return 0.0
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(left)


def markdown_images(document_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    lines = document_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = IMAGE_RE.fullmatch(line)
        if not match:
            continue
        target = unquote((match.group(2) or match.group(3) or "").strip())
        if "://" in target or target.startswith("data:"):
            continue
        context_lines = lines[max(0, index - 16) : index] + lines[index + 1 : index + 9]
        records.append(
            {
                "line_number": index + 1,
                "alt": match.group(1).strip(),
                "target": target,
                "resolved_path": (document_path.parent / target).resolve(),
                "context": "\n".join(context_lines),
            }
        )
    return records


def select_image_records(
    records: list[dict[str, Any]],
    *,
    package_root: Path,
    requested_assets: list[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve a user-supplied image allowlist without scanning the asset directory."""
    if not requested_assets:
        return records, []
    normalized_records: list[tuple[dict[str, Any], Path, str, str]] = []
    for record in records:
        resolved = Path(record["resolved_path"]).resolve()
        try:
            relative = resolved.relative_to(package_root).as_posix()
        except ValueError:
            relative = ""
        target = str(record.get("target", "")).removeprefix("./")
        normalized_records.append((record, resolved, relative, target))

    selected: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    selected_ids: set[int] = set()
    for raw_request in requested_assets:
        request = unquote(raw_request.strip()).removeprefix("./")
        request_path = Path(request)
        if not request:
            diagnostics.append("empty requested asset")
            continue
        if request_path.is_absolute():
            matches = [item for item in normalized_records if item[1] == request_path.resolve()]
        elif "/" in request:
            matches = [
                item for item in normalized_records if request in {item[2], item[3]}
            ]
        else:
            matches = [item for item in normalized_records if item[1].name == request]
            distinct_paths = {item[1] for item in matches}
            if len(distinct_paths) > 1:
                diagnostics.append(f"ambiguous requested asset basename: {request}")
                continue
        if not matches:
            diagnostics.append(f"requested asset is not referenced by Markdown: {raw_request}")
            continue
        for record, _resolved, _relative, _target in matches:
            identity = id(record)
            if identity not in selected_ids:
                selected.append(record)
                selected_ids.add(identity)
    return selected, diagnostics


def detect_tesseract_languages() -> list[str]:
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    available = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return [lang for lang in PREFERRED_TESSERACT_LANGS if lang in available]


def run_tesseract_tsv(image_path: Path, languages: list[str]) -> tuple[list[WordBox], list[str]]:
    with tempfile.TemporaryDirectory(prefix="reader-asset-ocr-") as temp_dir:
        output_base = Path(temp_dir) / "ocr"
        command = ["tesseract", str(image_path), str(output_base), "--psm", "11", "tsv"]
        if languages:
            command.extend(["-l", "+".join(languages)])
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        diagnostics: list[str] = []
        if result.returncode != 0:
            diagnostics.append(result.stderr.strip() or "tesseract failed")
            return [], diagnostics
        tsv_path = output_base.with_suffix(".tsv")
        if not tsv_path.is_file():
            diagnostics.append("tesseract did not emit TSV output")
            return [], diagnostics
        words: list[WordBox] = []
        with tsv_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                raw_text = (row.get("text") or "").strip()
                raw_conf = (row.get("conf") or "-1").strip()
                if not raw_text:
                    continue
                try:
                    confidence = float(raw_conf)
                except ValueError:
                    confidence = -1.0
                if confidence < 0:
                    continue
                try:
                    left = int(row.get("left") or "0")
                    top = int(row.get("top") or "0")
                    width = int(row.get("width") or "0")
                    height = int(row.get("height") or "0")
                except ValueError:
                    continue
                if width <= 0 or height <= 0:
                    continue
                words.append(
                    WordBox(
                        text=raw_text,
                        confidence=confidence,
                        left=left,
                        top=top,
                        width=width,
                        height=height,
                    )
                )
        return words, diagnostics


def compile_apple_vision(binary_path: Path) -> list[str]:
    if shutil.which("swiftc") is None:
        return ["swiftc is unavailable"]
    if not APPLE_VISION_SOURCE.is_file():
        return [f"missing Apple Vision source: {APPLE_VISION_SOURCE}"]
    result = subprocess.run(
        ["swiftc", str(APPLE_VISION_SOURCE), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return [result.stderr.strip() or "Apple Vision OCR compilation failed"]
    return []


def apple_vision_candidates_to_words(
    payload: dict[str, Any], image_width: int, image_height: int
) -> list[WordBox]:
    words: list[WordBox] = []
    for candidate in payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        text = str(candidate.get("text") or "").strip()
        bbox = candidate.get("bbox")
        if not text or not isinstance(bbox, dict):
            continue
        try:
            x = float(bbox["x"])
            y = float(bbox["y"])
            width = float(bbox["width"])
            height = float(bbox["height"])
            confidence = float(candidate.get("confidence", 0.0)) * 100
        except (KeyError, TypeError, ValueError):
            continue
        left = max(0, round(x * image_width))
        top = max(0, round((1.0 - y - height) * image_height))
        pixel_width = min(image_width - left, max(1, round(width * image_width)))
        pixel_height = min(image_height - top, max(1, round(height * image_height)))
        words.append(WordBox(text, confidence, left, top, pixel_width, pixel_height))
    return words


def run_apple_vision(image_path: Path, binary_path: Path) -> tuple[list[WordBox], list[str]]:
    result = subprocess.run(
        [str(binary_path), "--no-language-correction", str(image_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return [], [result.stderr.strip() or "Apple Vision OCR failed"]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], ["Apple Vision OCR returned invalid JSON"]
    if payload.get("status") != "complete":
        return [], [f"Apple Vision OCR status: {payload.get('status', 'unknown')}"]
    with Image.open(image_path) as image:
        width, height = image.size
    return apple_vision_candidates_to_words(payload, width, height), []


def scaled_masks(image_path: Path, words: list[WordBox]) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    with Image.open(image_path) as image:
        image = image.convert("L")
        original_width, original_height = image.size
        scale = min(1.0, 1200 / max(original_width, original_height))
        if scale < 1.0:
            resized = image.resize(
                (max(1, round(original_width * scale)), max(1, round(original_height * scale))),
                Image.Resampling.LANCZOS,
            )
        else:
            resized = image.copy()
        grayscale = np.asarray(resized)
    height, width = grayscale.shape
    dark_mask = grayscale < 235
    text_mask = np.zeros((height, width), dtype=bool)
    padding = max(2, round(4 * scale))
    for word in words:
        left = max(0, round(word.left * scale) - padding)
        top = max(0, round(word.top * scale) - padding)
        right = min(width, round((word.left + word.width) * scale) + padding)
        bottom = min(height, round((word.top + word.height) * scale) + padding)
        text_mask[top:bottom, left:right] = True
    non_text_mask = dark_mask & ~text_mask
    return (
        {
            "original_width": original_width,
            "original_height": original_height,
            "analysis_width": width,
            "analysis_height": height,
            "scale": scale,
        },
        dark_mask,
        non_text_mask,
    )


def non_text_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    points = np.argwhere(mask)
    if points.size == 0:
        return None
    top = int(points[:, 0].min())
    bottom = int(points[:, 0].max())
    left = int(points[:, 1].min())
    right = int(points[:, 1].max())
    return left, top, right + 1, bottom + 1


def scaled_word_bounds(
    words: list[WordBox],
    *,
    scale: float,
    width: int,
    height: int,
    padding: int,
) -> tuple[int, int, int, int] | None:
    if not words:
        return None
    left = min(max(0, round(word.left * scale) - padding) for word in words)
    top = min(max(0, round(word.top * scale) - padding) for word in words)
    right = max(min(width, round((word.left + word.width) * scale) + padding) for word in words)
    bottom = max(min(height, round((word.top + word.height) * scale) + padding) for word in words)
    return left, top, right, bottom


def merge_bounds(
    first: tuple[int, int, int, int] | None,
    second: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    if first is None:
        return second
    if second is None:
        return first
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def expand_bounds(
    bounds: tuple[int, int, int, int] | None,
    width: int,
    height: int,
    *,
    padding: int,
) -> tuple[int, int, int, int]:
    if bounds is None:
        return 0, 0, width, height
    left, top, right, bottom = bounds
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + padding),
        min(height, bottom + padding),
    )


def project_bounds(
    bounds: tuple[int, int, int, int],
    scale: float,
    original_width: int,
    original_height: int,
) -> dict[str, Any]:
    left, top, right, bottom = bounds
    if scale <= 0:
        scale = 1.0
    x = max(0, round(left / scale))
    y = max(0, round(top / scale))
    width = min(original_width - x, max(1, round((right - left) / scale)))
    height = min(original_height - y, max(1, round((bottom - top) / scale)))
    return {
        "geometry": f"{width}x{height}+{x}+{y}",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _blank_run_after(
    row_ratios: np.ndarray,
    *,
    start: int,
    stop: int,
    minimum_run: int,
    blank_ratio: float = 0.002,
) -> tuple[int, int] | None:
    start = max(0, start)
    stop = min(len(row_ratios), stop)
    cursor = start
    while cursor < stop:
        if row_ratios[cursor] > blank_ratio:
            cursor += 1
            continue
        run_start = cursor
        while cursor < stop and row_ratios[cursor] <= blank_ratio:
            cursor += 1
        if cursor - run_start >= minimum_run:
            return run_start, cursor
    return None


def _blank_run_before(
    row_ratios: np.ndarray,
    *,
    start: int,
    stop: int,
    minimum_run: int,
    blank_ratio: float = 0.002,
) -> tuple[int, int] | None:
    """Return the blank run closest to ``stop`` inside [start, stop)."""
    start = max(0, start)
    stop = min(len(row_ratios), stop)
    cursor = stop - 1
    while cursor >= start:
        if row_ratios[cursor] > blank_ratio:
            cursor -= 1
            continue
        run_end = cursor + 1
        while cursor >= start and row_ratios[cursor] <= blank_ratio:
            cursor -= 1
        run_start = cursor + 1
        if run_end - run_start >= minimum_run:
            return run_start, run_end
    return None


def _band_non_text_ratio(mask: np.ndarray, start: int, stop: int) -> float:
    start = max(0, start)
    stop = min(mask.shape[0], stop)
    if start >= stop or mask.shape[1] == 0:
        return 0.0
    band = mask[start:stop, :]
    return float(band.sum()) / band.size


def ebook_edge_text_crop(
    words: list[WordBox],
    *,
    analysis_meta: dict[str, Any],
    dark_mask: np.ndarray,
    non_text_mask: np.ndarray,
) -> dict[str, Any]:
    """Propose a y-only crop when text forms separable ebook edge bands.

    OCR presence alone is insufficient: an edge band must be spatially near the
    top or bottom, separated from the retained center by a blank gutter, and
    contain little non-text structure. Interior OCR remains part of the figure.
    """
    height, width = dark_mask.shape
    scale = float(analysis_meta["scale"])
    row_ratios = dark_mask.sum(axis=1) / max(1, width)
    minimum_gutter = max(8, round(height * 0.008))
    safety_margin = max(6, round(height * 0.008))
    top_edge_limit = round(height * 0.30)
    bottom_edge_limit = round(height * 0.70)
    top_search_stop = round(height * 0.48)
    bottom_search_start = round(height * 0.52)

    scaled_words = [
        {
            "word": word,
            "top": max(0, round(word.top * scale)),
            "bottom": min(height, round((word.top + word.height) * scale)),
            "center": round((word.top + word.height / 2) * scale),
        }
        for word in words
    ]
    top_words = [item for item in scaled_words if item["center"] <= top_edge_limit]
    bottom_words = [item for item in scaled_words if item["center"] >= bottom_edge_limit]

    top_boundary = 0
    top_evidence: dict[str, Any] = {
        "eligible": False,
        "text_word_count": len(top_words),
        "boundary": 0,
        "blank_gutter_rows": 0,
        "non_text_dark_ratio": 0.0,
    }
    if top_words:
        last_text_row = max(int(item["bottom"]) for item in top_words)
        blank_run = _blank_run_after(
            row_ratios,
            start=last_text_row + 1,
            stop=top_search_stop,
            minimum_run=minimum_gutter,
        )
        if blank_run is not None:
            run_start, run_end = blank_run
            candidate = max(run_start, run_end - safety_margin)
            band_ratio = _band_non_text_ratio(non_text_mask, 0, candidate)
            edge_only = all(
                item["center"] <= top_edge_limit
                for item in scaled_words
                if item["bottom"] <= candidate
            )
            eligible = candidate > safety_margin and band_ratio <= 0.01 and edge_only
            if eligible:
                top_boundary = candidate
            top_evidence.update(
                {
                    "eligible": eligible,
                    "boundary": round(candidate / scale) if eligible else 0,
                    "blank_gutter_rows": run_end - run_start,
                    "non_text_dark_ratio": round(band_ratio, 6),
                }
            )

    bottom_boundary = height
    bottom_evidence: dict[str, Any] = {
        "eligible": False,
        "text_word_count": len(bottom_words),
        "boundary": int(analysis_meta["original_height"]),
        "blank_gutter_rows": 0,
        "non_text_dark_ratio": 0.0,
    }
    if bottom_words:
        first_text_row = min(int(item["top"]) for item in bottom_words)
        blank_run = _blank_run_before(
            row_ratios,
            start=bottom_search_start,
            stop=first_text_row,
            minimum_run=minimum_gutter,
        )
        if blank_run is not None:
            run_start, run_end = blank_run
            candidate = min(run_end, run_start + safety_margin)
            band_ratio = _band_non_text_ratio(non_text_mask, candidate, height)
            edge_only = all(
                item["center"] >= bottom_edge_limit
                for item in scaled_words
                if item["top"] >= candidate
            )
            eligible = candidate < height - safety_margin and band_ratio <= 0.01 and edge_only
            if eligible:
                bottom_boundary = candidate
            bottom_evidence.update(
                {
                    "eligible": eligible,
                    "boundary": round(candidate / scale) if eligible else int(analysis_meta["original_height"]),
                    "blank_gutter_rows": run_end - run_start,
                    "non_text_dark_ratio": round(band_ratio, 6),
                }
            )

    eligible = top_evidence["eligible"] or bottom_evidence["eligible"]
    if bottom_boundary <= top_boundary:
        eligible = False
        top_boundary = 0
        bottom_boundary = height
        top_evidence["eligible"] = False
        bottom_evidence["eligible"] = False
    crop = project_bounds(
        (0, top_boundary, width, bottom_boundary),
        scale,
        int(analysis_meta["original_width"]),
        int(analysis_meta["original_height"]),
    )
    kept_center_word_count = sum(
        top_boundary < item["center"] < bottom_boundary for item in scaled_words
    )
    return {
        "eligible": bool(eligible),
        "profile": "ebook-edge-text",
        "top": top_evidence,
        "bottom": bottom_evidence,
        "kept_center_word_count": kept_center_word_count,
        "crop": crop,
    }


def summarize_non_text(mask: np.ndarray) -> dict[str, Any]:
    if mask.size == 0:
        return {
            "dark_ratio": 0.0,
            "horizontal_rule_rows": 0,
            "vertical_rule_columns": 0,
            "bounds_area_ratio": 0.0,
        }
    height, width = mask.shape
    non_text_pixels = int(mask.sum())
    row_ratio = mask.sum(axis=1) / max(1, width)
    col_ratio = mask.sum(axis=0) / max(1, height)
    bounds = non_text_bounds(mask)
    bounds_area_ratio = 0.0
    if bounds is not None:
        left, top, right, bottom = bounds
        bounds_area_ratio = ((right - left) * (bottom - top)) / (width * height)
    return {
        "dark_ratio": round(non_text_pixels / (width * height), 6),
        "horizontal_rule_rows": int((row_ratio >= 0.18).sum()),
        "vertical_rule_columns": int((col_ratio >= 0.18).sum()),
        "bounds_area_ratio": round(bounds_area_ratio, 6),
    }


def decide_asset(
    *,
    word_count: int,
    average_confidence: float,
    context_overlap: float,
    ordered_overlap: float,
    non_text_dark_ratio: float,
    horizontal_rule_rows: int,
    vertical_rule_columns: int,
    non_text_bounds_area_ratio: float,
    simple_order_markers_present: bool,
    crop_area_ratio: float,
    edge_trim_total: int,
    edge_text_trim_eligible: bool,
    ocr_succeeded: bool,
) -> tuple[str, list[str], str]:
    structure_detected = (
        non_text_dark_ratio >= 0.016
        or horizontal_rule_rows >= 5
        or vertical_rule_columns >= 5
        or non_text_bounds_area_ratio >= 0.12
    )
    simple_structure_covered = (
        structure_detected
        and non_text_dark_ratio <= 0.012
        and horizontal_rule_rows <= 4
        and vertical_rule_columns <= 4
        and simple_order_markers_present
        and context_overlap >= 0.9
        and ordered_overlap >= 0.8
    )
    if word_count == 0:
        if ocr_succeeded:
            return "retain", ["no-text-detected"], "OCR 未检出文字；本轮没有可执行的去文字裁切。"
        return "needs-review", ["needs-review"], "OCR 未得到稳定文字，无法确认是否为纯文字图。"
    if average_confidence < 45:
        return "needs-review", ["needs-review"], "OCR 置信度偏低，不能安全删除或继续收裁。"
    if (
        context_overlap >= 0.9
        and ordered_overlap >= 0.8
        and non_text_dark_ratio <= 0.012
        and horizontal_rule_rows <= 4
        and vertical_rule_columns <= 4
    ):
        reason_codes = ["text-covered"]
        if simple_structure_covered:
            reason_codes.append("simple-order-covered")
        return "candidate-remove", reason_codes, "图片文字及简单顺序结构已被 Markdown 完整覆盖。"
    if edge_text_trim_eligible:
        return (
            "crop-required",
            ["ebook-edge-text-band"],
            "顶部或底部文字形成了与中央视觉内容分离的边带，可只做纵向二次裁切。",
        )
    if structure_detected and not simple_structure_covered:
        return "retain", ["mixed-text-visual"], "图中仍有明显结构化视觉增量，不应按纯文字图删除。"
    if (
        crop_area_ratio <= 0.88
        and edge_trim_total >= 80
        and context_overlap < 0.9
        and non_text_dark_ratio <= 0.012
        and horizontal_rule_rows <= 4
        and vertical_rule_columns <= 4
    ):
        return "crop-required", ["text-covered"], "当前图仍含可裁掉的文字边带，建议先二次裁切。"
    return "needs-review", ["needs-review"], "介于纯文字与结构图之间，保守进入 needs-review。"


def deletion_evidence(
    *,
    word_count: int,
    average_confidence: float,
    context_overlap: float,
    ordered_overlap: float,
    non_text_dark_ratio: float,
    horizontal_rule_rows: int,
    vertical_rule_columns: int,
    non_text_bounds_area_ratio: float,
    simple_order_markers_present: bool,
) -> tuple[dict[str, bool], int]:
    sparse_structure = non_text_bounds_area_ratio >= 0.12
    simple_structure_covered = (
        sparse_structure
        and non_text_dark_ratio <= 0.012
        and horizontal_rule_rows <= 4
        and vertical_rule_columns <= 4
        and simple_order_markers_present
        and context_overlap >= 0.9
        and ordered_overlap >= 0.8
    )
    gates = {
        "ocr_text_present": word_count > 0,
        "ocr_confidence_sufficient": average_confidence >= 45,
        "markdown_multiset_coverage": context_overlap >= 0.9,
        "markdown_ordered_coverage": ordered_overlap >= 0.8,
        "visual_increment_covered": (
            (
                non_text_dark_ratio <= 0.012
                and horizontal_rule_rows <= 4
                and vertical_rule_columns <= 4
                and not sparse_structure
            )
            or simple_structure_covered
        ),
    }
    score = 10
    score += 20 if average_confidence >= 70 else 15 if average_confidence >= 45 else 0
    score += 20 if context_overlap >= 0.9 else round(20 * min(1.0, context_overlap / 0.9))
    score += 15 if ordered_overlap >= 0.8 else round(15 * min(1.0, ordered_overlap / 0.8))
    score += 35 if gates["visual_increment_covered"] else 0
    return gates, min(100, score)


def select_ocr_words(
    image_path: Path,
    *,
    ocr_engine: str,
    apple_vision_binary: Path | None,
    tesseract_languages: list[str],
) -> tuple[list[WordBox], str, list[str]]:
    diagnostics: list[str] = []
    if ocr_engine in {"auto", "apple-vision"} and apple_vision_binary is not None:
        words, apple_diagnostics = run_apple_vision(image_path, apple_vision_binary)
        diagnostics.extend(apple_diagnostics)
        average = sum(word.confidence for word in words) / len(words) if words else 0.0
        if words and (average >= 45 or ocr_engine == "apple-vision"):
            return words, "apple-vision", diagnostics
        if ocr_engine == "apple-vision":
            return words, "apple-vision", diagnostics
    if ocr_engine in {"auto", "tesseract"}:
        words, tesseract_diagnostics = run_tesseract_tsv(image_path, tesseract_languages)
        diagnostics.extend(tesseract_diagnostics)
        return words, "tesseract", diagnostics
    return [], ocr_engine, diagnostics or [f"OCR engine unavailable: {ocr_engine}"]


def asset_page_map(package_root: Path) -> dict[str, int]:
    report_path = package_root / "evidence/reader-asset-crop-result.json"
    if not report_path.is_file():
        return {}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    mapping: dict[str, int] = {}
    for page in report.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_number = page.get("page")
        if not isinstance(page_number, int):
            continue
        for region in page.get("regions", []):
            path = region.get("file_path")
            if isinstance(path, str):
                mapping[path] = page_number
    return mapping


def audit_asset(
    image_path: Path,
    *,
    package_root: Path,
    markdown_context: str,
    ocr_engine: str,
    apple_vision_binary: Path | None,
    tesseract_languages: list[str],
    page_number: int | None,
    layout_profile: str,
) -> dict[str, Any]:
    words, selected_engine, diagnostics = select_ocr_words(
        image_path,
        ocr_engine=ocr_engine,
        apple_vision_binary=apple_vision_binary,
        tesseract_languages=tesseract_languages,
    )
    analysis_meta, dark_mask, non_text_mask = scaled_masks(image_path, words)
    non_text_summary = summarize_non_text(non_text_mask)
    normalized_ocr = normalize_visible_text("".join(word.text for word in words))
    normalized_context = normalize_visible_text(markdown_context)
    simple_order_markers_present = any(marker in markdown_context for marker in ("→", "->", "=>"))
    overlap = multiset_overlap_ratio(normalized_ocr, normalized_context)
    ordered_overlap = ordered_overlap_ratio(normalized_ocr, normalized_context)
    average_confidence = round(
        sum(word.confidence for word in words) / len(words), 2
    ) if words else 0.0
    analysis_padding = round(16 * analysis_meta["scale"]) or 1
    bounds = expand_bounds(
        merge_bounds(
            non_text_bounds(non_text_mask),
            scaled_word_bounds(
                words,
                scale=analysis_meta["scale"],
                width=analysis_meta["analysis_width"],
                height=analysis_meta["analysis_height"],
                padding=max(2, round(10 * analysis_meta["scale"])),
            ),
        ),
        analysis_meta["analysis_width"],
        analysis_meta["analysis_height"],
        padding=analysis_padding,
    )
    general_crop = project_bounds(
        bounds,
        analysis_meta["scale"],
        analysis_meta["original_width"],
        analysis_meta["original_height"],
    )
    is_tall = analysis_meta["original_height"] / max(1, analysis_meta["original_width"]) >= 1.15
    use_ebook_profile = layout_profile == "ebook-edge-text" or (
        layout_profile == "auto" and is_tall
    )
    edge_text_layout = ebook_edge_text_crop(
        words,
        analysis_meta=analysis_meta,
        dark_mask=dark_mask,
        non_text_mask=non_text_mask,
    )
    if use_ebook_profile:
        crop = edge_text_layout["crop"]
        crop_basis = (
            "ebook-edge-text-bands" if edge_text_layout["eligible"] else "ebook-no-edge-text-no-op"
        )
    else:
        crop = general_crop
        crop_basis = "general-content-bounds"
    crop_area_ratio = round(
        (crop["width"] * crop["height"])
        / (analysis_meta["original_width"] * analysis_meta["original_height"]),
        6,
    )
    trim = {
        "left": crop["x"],
        "top": crop["y"],
        "right": analysis_meta["original_width"] - (crop["x"] + crop["width"]),
        "bottom": analysis_meta["original_height"] - (crop["y"] + crop["height"]),
    }
    edge_trim_total = sum(trim.values())
    decision, reason_codes, rationale = decide_asset(
        word_count=len(words),
        average_confidence=average_confidence,
        context_overlap=overlap,
        ordered_overlap=ordered_overlap,
        non_text_dark_ratio=non_text_summary["dark_ratio"],
        horizontal_rule_rows=non_text_summary["horizontal_rule_rows"],
        vertical_rule_columns=non_text_summary["vertical_rule_columns"],
        non_text_bounds_area_ratio=non_text_summary["bounds_area_ratio"],
        simple_order_markers_present=simple_order_markers_present,
        crop_area_ratio=crop_area_ratio,
        edge_trim_total=edge_trim_total,
        edge_text_trim_eligible=bool(use_ebook_profile and edge_text_layout["eligible"]),
        ocr_succeeded=selected_engine in {"apple-vision", "tesseract"} and not diagnostics,
    )
    deletion_gates, decision_score = deletion_evidence(
        word_count=len(words),
        average_confidence=average_confidence,
        context_overlap=overlap,
        ordered_overlap=ordered_overlap,
        non_text_dark_ratio=non_text_summary["dark_ratio"],
        horizontal_rule_rows=non_text_summary["horizontal_rule_rows"],
        vertical_rule_columns=non_text_summary["vertical_rule_columns"],
        non_text_bounds_area_ratio=non_text_summary["bounds_area_ratio"],
        simple_order_markers_present=simple_order_markers_present,
    )
    deletion_eligible = (
        decision == "candidate-remove"
        and decision_score >= MINIMUM_DELETION_SCORE
        and all(deletion_gates.values())
    )
    classification_source = "deterministic-ocr-layout"
    return {
        "page": page_number,
        "image_path": image_path.relative_to(package_root).as_posix(),
        "sha256": sha256(image_path),
        "decision": decision,
        "decision_confidence_score": decision_score,
        "minimum_deletion_score": MINIMUM_DELETION_SCORE,
        "deletion_gates": deletion_gates,
        "deletion_eligible": deletion_eligible,
        "reason_codes": reason_codes,
        "semantic_text_coverage": (
            "complete" if overlap >= 0.9 and ordered_overlap >= 0.8 and words else "partial"
        ),
        "visual_increment": (
            "covered-simple-order"
            if decision == "candidate-remove" and simple_order_markers_present
            else "none"
            if decision == "candidate-remove"
            else "present"
        ),
        "classification_source": classification_source,
        "layout_profile": {
            "requested": layout_profile,
            "applied": "ebook-edge-text" if use_ebook_profile else "general",
            "is_tall": is_tall,
            "crop_basis": crop_basis,
            "edge_text": edge_text_layout,
        },
        "ocr_summary": {
            "engine": selected_engine,
            "language_order": tesseract_languages,
            "word_count": len(words),
            "average_confidence": average_confidence,
            "normalized_char_count": len(normalized_ocr),
        },
        "markdown_overlap": {
            "normalized_context_char_count": len(normalized_context),
            "ocr_context_overlap_ratio": round(overlap, 6),
            "ordered_coverage_ratio": round(ordered_overlap, 6),
        },
        "non_text_summary": non_text_summary,
        "suggested_crop": {
            **crop,
            "area_ratio": crop_area_ratio,
            "trim_pixels": trim,
        },
        "rationale": rationale,
        "audit_evidence_quality": {
            "score": 100 if selected_engine in {"apple-vision", "tesseract"} else 80,
            "minimum": 95,
            "dimensions": {
                "reference_and_hash": 20,
                "ocr_route_and_boxes": 20 if selected_engine in {"apple-vision", "tesseract"} else 0,
                "markdown_coverage": 20,
                "visual_increment_metrics": 20,
                "fail_closed_deletion_gate": 20,
            },
        },
        "diagnostics": diagnostics,
    }


def build_report(
    package_root: Path,
    document: Path,
    *,
    ocr_engine: str,
    requested_assets: list[str] | None = None,
    layout_profile: str = "auto",
) -> dict[str, Any]:
    referenced_image_records = markdown_images(document)
    image_records, scope_diagnostics = select_image_records(
        referenced_image_records,
        package_root=package_root,
        requested_assets=requested_assets,
    )
    page_map = asset_page_map(package_root)
    tesseract_languages = detect_tesseract_languages()
    decisions: list[dict[str, Any]] = []
    diagnostics: list[str] = list(scope_diagnostics)
    route_diagnostics: list[str] = []
    with tempfile.TemporaryDirectory(prefix="reader-asset-apple-vision-") as temp_dir:
        apple_vision_binary: Path | None = None
        if ocr_engine in {"auto", "apple-vision"}:
            candidate_binary = Path(temp_dir) / "apple-vision-ocr"
            route_diagnostics = compile_apple_vision(candidate_binary)
            if not route_diagnostics:
                apple_vision_binary = candidate_binary
            elif ocr_engine == "apple-vision":
                diagnostics.extend(route_diagnostics)
        for record in image_records:
            image_path = Path(record["resolved_path"])
            if not image_path.is_file():
                diagnostics.append(f"missing image referenced by Markdown: {record['target']}")
                continue
            relative = image_path.relative_to(package_root).as_posix()
            decision = audit_asset(
                image_path,
                package_root=package_root,
                markdown_context=record["context"],
                ocr_engine=ocr_engine,
                apple_vision_binary=apple_vision_binary,
                tesseract_languages=tesseract_languages,
                page_number=page_map.get(relative),
                layout_profile=layout_profile,
            )
            decision["markdown_reference"] = {
                "document": document.relative_to(package_root).as_posix(),
                "line_number": record["line_number"],
                "alt": record["alt"],
                "target": record["target"],
            }
            decisions.append(decision)
    counts = {
        "referenced_images": len(referenced_image_records),
        "selected_images": len(image_records),
        "audited_images": len(decisions),
        "candidate_remove": sum(item["decision"] == "candidate-remove" for item in decisions),
        "crop_required": sum(item["decision"] == "crop-required" for item in decisions),
        "retain": sum(item["decision"] == "retain" for item in decisions),
        "needs_review": sum(item["decision"] == "needs-review" for item in decisions),
        "deletion_eligible": sum(item["deletion_eligible"] for item in decisions),
    }
    minimum_evidence_score = min(
        (item["audit_evidence_quality"]["score"] for item in decisions),
        default=0,
    )
    status = "complete"
    if diagnostics or counts["needs_review"] or minimum_evidence_score < 95:
        status = "needs-review"
    return {
        "schema": "text-heavy-reader-asset-audit",
        "version": AUDIT_VERSION,
        "status": status,
        "mode": "deterministic-audit",
        "package_root": str(package_root),
        "document": document.relative_to(package_root).as_posix(),
        "ocr_engine_requested": ocr_engine,
        "scope": {
            "mode": "explicit-allowlist" if requested_assets else "all-markdown-images",
            "requested_assets": requested_assets or [],
            "selected_targets": [str(record["target"]) for record in image_records],
            "directory_scan_performed": False,
        },
        "layout_profile_requested": layout_profile,
        "tesseract_languages": tesseract_languages,
        "route_diagnostics": route_diagnostics,
        "quality_gate": {
            "minimum": 95,
            "score": minimum_evidence_score,
            "passed": minimum_evidence_score >= 95,
            "scope": "audit-evidence-completeness-not-crop-visual-quality",
        },
        "counts": counts,
        "decisions": decisions,
        "diagnostics": diagnostics,
    }


def build_removal_plan(report: dict[str, Any], document: Path) -> dict[str, Any]:
    candidates = []
    lines = document.read_text(encoding="utf-8").splitlines()
    for item in report["decisions"]:
        if not item["deletion_eligible"]:
            continue
        reference = item["markdown_reference"]
        line_number = int(reference["line_number"])
        candidates.append(
            {
                "image_path": item["image_path"],
                "image_sha256": item["sha256"],
                "decision_confidence_score": item["decision_confidence_score"],
                "deletion_gates": item["deletion_gates"],
                "markdown_line_number": line_number,
                "markdown_line": lines[line_number - 1],
                "markdown_target": reference["target"],
            }
        )
    return {
        "schema": "text-heavy-reader-asset-removal-plan",
        "version": "0.1.0",
        "status": "ready" if candidates else "no-op",
        "package_root": report["package_root"],
        "document": report["document"],
        "document_sha256": sha256(document),
        "audit_schema": report["schema"],
        "audit_version": report["version"],
        "audit_quality_gate": report["quality_gate"],
        "minimum_deletion_score": MINIMUM_DELETION_SCORE,
        "candidates": candidates,
    }


def apply_removal_plan(package_root: Path, plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    diagnostics: list[str] = []
    if plan.get("schema") != "text-heavy-reader-asset-removal-plan":
        diagnostics.append("invalid removal plan schema")
    if plan.get("status") not in {"ready", "no-op"}:
        diagnostics.append("removal plan is not ready")
    audit_quality_gate = plan.get("audit_quality_gate")
    audit_quality_score = audit_quality_gate.get("score") if isinstance(audit_quality_gate, dict) else None
    if (
        not isinstance(audit_quality_gate, dict)
        or audit_quality_gate.get("passed") is not True
        or not isinstance(audit_quality_score, (int, float))
        or audit_quality_score < MINIMUM_DELETION_SCORE
    ):
        diagnostics.append("audit evidence quality gate failed")
    document = (package_root / str(plan.get("document", "index.md"))).resolve()
    try:
        document.relative_to(package_root)
    except ValueError:
        diagnostics.append("Markdown path escapes package root")
    if not document.is_file() or sha256(document) != plan.get("document_sha256"):
        diagnostics.append("Markdown hash drifted after plan creation")
    candidates = plan.get("candidates")
    if not isinstance(candidates, list):
        diagnostics.append("removal plan candidates must be a list")
        candidates = []
    original_lines = document.read_text(encoding="utf-8").splitlines(keepends=True) if document.is_file() else []
    removal_indexes: set[int] = set()
    asset_paths: list[Path] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            diagnostics.append("invalid removal candidate")
            continue
        score = candidate.get("decision_confidence_score")
        gates = candidate.get("deletion_gates")
        if not isinstance(score, int) or score < MINIMUM_DELETION_SCORE:
            diagnostics.append(f"deletion score below {MINIMUM_DELETION_SCORE}")
        if not isinstance(gates, dict) or not gates or not all(gates.values()):
            diagnostics.append("deletion hard gate failed")
        line_index = int(candidate.get("markdown_line_number", 0)) - 1
        expected_line = str(candidate.get("markdown_line", ""))
        if line_index < 0 or line_index >= len(original_lines):
            diagnostics.append("Markdown reference line is out of range")
        elif original_lines[line_index].rstrip("\r\n") != expected_line:
            diagnostics.append("Markdown reference line drifted after plan creation")
        else:
            removal_indexes.add(line_index)
        asset_path = (package_root / str(candidate.get("image_path", ""))).resolve()
        try:
            asset_path.relative_to(package_root)
        except ValueError:
            diagnostics.append("asset path escapes package root")
            continue
        if not asset_path.is_file() or sha256(asset_path) != candidate.get("image_sha256"):
            diagnostics.append(f"asset hash drifted: {candidate.get('image_path')}")
        else:
            if asset_path not in asset_paths:
                asset_paths.append(asset_path)
    if diagnostics:
        return {
            "schema": "text-heavy-reader-asset-removal-result",
            "version": "0.1.0",
            "status": "needs-review",
            "removed_assets": [],
            "diagnostics": diagnostics,
        }
    if not candidates:
        return {
            "schema": "text-heavy-reader-asset-removal-result",
            "version": "0.1.0",
            "status": "passed",
            "removed_assets": [],
            "diagnostics": [],
        }
    original_document = document.read_bytes()
    asset_backups = {path: path.read_bytes() for path in asset_paths}
    try:
        rewritten = "".join(line for index, line in enumerate(original_lines) if index not in removal_indexes)
        temporary = document.with_name(f".{document.name}.{os.getpid()}.tmp")
        temporary.write_text(rewritten, encoding="utf-8")
        os.replace(temporary, document)
        for asset_path in asset_paths:
            asset_path.unlink()
        remaining_targets = {record["target"] for record in markdown_images(document)}
        for candidate in candidates:
            if candidate["markdown_target"] in remaining_targets:
                raise RuntimeError(f"Markdown reference remains: {candidate['markdown_target']}")
        if any(path.exists() for path in asset_paths):
            raise RuntimeError("one or more assets remain after deletion")
    except Exception as exc:
        document.write_bytes(original_document)
        for path, payload in asset_backups.items():
            path.write_bytes(payload)
        return {
            "schema": "text-heavy-reader-asset-removal-result",
            "version": "0.1.0",
            "status": "failed",
            "removed_assets": [],
            "diagnostics": [str(exc)],
        }
    return {
        "schema": "text-heavy-reader-asset-removal-result",
        "version": "0.1.0",
        "status": "passed",
        "document_before_sha256": plan["document_sha256"],
        "document_after_sha256": sha256(document),
        "removed_assets": [
            {"image_path": candidate["image_path"], "sha256": candidate["image_sha256"]}
            for candidate in candidates
        ],
        "checks": {
            "plan_hashes_matched": True,
            "markdown_references_removed": True,
            "asset_files_removed_after_relink": True,
        },
        "diagnostics": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", type=Path)
    parser.add_argument(
        "--mode",
        choices=("markdown-reader-assets", "standalone-image-corpus", "image-only"),
        default="markdown-reader-assets",
        help="Use standalone-image-corpus/image-only for an explicit image corpus with no Markdown access.",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Required evidence directory for standalone-image-corpus mode.",
    )
    parser.add_argument(
        "--input-manifest",
        type=Path,
        help="Reuse a locked standalone input-manifest.jsonl instead of creating a new snapshot.",
    )
    parser.add_argument(
        "--prior-classification",
        type=Path,
        help="Optional prior JSONL whose matching decision/reason is copied into a new standalone manifest.",
    )
    parser.add_argument("--resume", action="store_true", help="Resume standalone mode from its durable checkpoint.")
    parser.add_argument("--max-workers", type=int, default=1, help="Standalone worker count; maximum 2.")
    parser.add_argument(
        "--memory-limit-mib",
        type=int,
        default=4096,
        help="Standalone process-tree RSS safety limit.",
    )
    parser.add_argument("--document", default="index.md")
    parser.add_argument(
        "--ocr-engine",
        choices=("auto", "apple-vision", "tesseract"),
        default="auto",
        help="OCR route. auto prefers Apple Vision and falls back to Tesseract.",
    )
    parser.add_argument(
        "--asset",
        action="append",
        dest="requested_assets",
        help=(
            "Audit only this Markdown-referenced image. Repeat for an explicit allowlist; "
            "a basename must be unique."
        ),
    )
    parser.add_argument(
        "--layout-profile",
        choices=("auto", "general", "ebook-edge-text"),
        default="auto",
        help=(
            "ebook-edge-text only proposes y-axis trims for separable top/bottom text bands; "
            "auto uses it for tall images."
        ),
    )
    parser.add_argument(
        "--output",
        default="evidence/text-heavy-reader-asset-audit.json",
        help="Path relative to package_root.",
    )
    parser.add_argument(
        "--plan-output",
        default="evidence/text-heavy-reader-asset-removal-plan.json",
        help="Hash-locked removal plan path relative to package_root.",
    )
    parser.add_argument(
        "--apply-plan",
        help="Apply a previously generated removal plan relative to package_root.",
    )
    parser.add_argument(
        "--reuse-audit",
        action="store_true",
        help="Reuse the current audit output to regenerate only the hash-locked plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    package_root = args.package_root.resolve()
    if args.mode in {"standalone-image-corpus", "image-only"}:
        if args.evidence_dir is None:
            raise SystemExit("--evidence-dir is required for standalone-image-corpus mode")
        module_path = (
            ROOT
            / "skills"
            / "semantic-visual-asset-pruning"
            / "scripts"
            / "standalone_image_corpus.py"
        )
        if not module_path.is_file():
            raise SystemExit(f"missing standalone image corpus implementation: {module_path}")
        spec = importlib.util.spec_from_file_location("standalone_image_corpus", module_path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"cannot load standalone image corpus implementation: {module_path}")
        standalone = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = standalone
        spec.loader.exec_module(standalone)
        forwarded = [
            str(package_root),
            "--evidence-dir",
            str(args.evidence_dir.resolve()),
            "--max-workers",
            str(args.max_workers),
            "--memory-limit-mb",
            str(args.memory_limit_mib),
            "--ocr-engine",
            args.ocr_engine,
        ]
        if args.input_manifest is not None:
            forwarded.extend(["--input-manifest", str(args.input_manifest.resolve())])
        if args.prior_classification is not None:
            forwarded.extend(["--prior-classification", str(args.prior_classification.resolve())])
        if args.resume:
            forwarded.append("--resume")
        return int(standalone.main(forwarded))
    if args.apply_plan:
        plan_path = (package_root / args.apply_plan).resolve()
        if not plan_path.is_file():
            raise SystemExit(f"missing removal plan: {plan_path}")
        result = apply_removal_plan(package_root, plan_path)
        result_path = package_root / "evidence/text-heavy-reader-asset-removal-result.json"
        write_json(result_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "passed" else 1
    document = (package_root / args.document).resolve()
    if not document.is_file():
        raise SystemExit(f"missing Markdown document: {document}")
    output_path = (package_root / args.output).resolve()
    if args.reuse_audit:
        if not output_path.is_file():
            raise SystemExit(f"missing audit report: {output_path}")
        report = json.loads(output_path.read_text(encoding="utf-8"))
        if report.get("schema") != "text-heavy-reader-asset-audit" or report.get("version") != AUDIT_VERSION:
            raise SystemExit("audit report schema/version is not reusable")
        if Path(str(report.get("package_root", ""))).resolve() != package_root:
            raise SystemExit("audit report package_root does not match")
        plan = build_removal_plan(report, document)
        plan_path = (package_root / args.plan_output).resolve()
        write_json(plan_path, plan)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    report = build_report(
        package_root,
        document,
        ocr_engine=args.ocr_engine,
        requested_assets=args.requested_assets,
        layout_profile=args.layout_profile,
    )
    write_json(output_path, report)
    plan = build_removal_plan(report, document)
    plan_path = (package_root / args.plan_output).resolve()
    write_json(plan_path, plan)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
