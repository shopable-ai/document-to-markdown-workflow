#!/usr/bin/env python3
"""Run the source-scoped Markdown/layout optimization pass for Workflow A.

The adapter starts from an already assembled package and repairs only its
existing ``index.md`` reader layer.  Raw OCR/page evidence is copied into a
new versioned directory and kept immutable; it is provenance/evidence, not a
cleanup input.  No online model is called.  mac96 availability remains an
explicit uncertainty.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - the repository runtime provides PyYAML
    yaml = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_VERSION = "0.6.0"
RAW_FILES = ("evidence/ocr-candidates.json",)
RAW_GLOB = "evidence/apple-vision-p*.json"
GENERIC_HEADERS = {
    "每天学一个思维模型",
    "查理芒格思维模型与100个思维模型",
    "查理芒格思维模型与100个思维模型",
    "查理芒格的100个思维模型",
    "《万物皆模型》100个思维模型",
    "Charlie Munger's 100 Mental Models",
    "Charlie Munger's Mind Model And 100 Mind Models",
    "Charlie Munger 100 Mental Models",
    "Charlie Munger 100 Mind Models",
    "Everything is a mental models And 100 Mental Models",
    "Everything is a mental model And 100 Mental Models",
    "Everything is a mental models",
    "Everything is a mental model",
    "Charlie Munger",
    "模型结构",
    "模型结构推导",
}
TITLE_SIGNAL = re.compile(r"模型|法则|方法|分析法|理论|原则|定律|效应|视角|原理|系统|成本|心流|SWOT|PDCA|5W|HOOK", re.I)
SECTION_SIGNAL = re.compile(r"^(总结|思考|举例|实用公式|公式|定义|例如|补充|核心|关键|应用|方法|步骤|原则|解读|结论|反思|模型结构|A\s|B\s)")
READER_NOISE_SIGNAL = re.compile(
    r"^(?:everything|is\s+(?:a\s+)?model|mental\s+models?|everything\s+is\s+(?:a\s+)?model|"
    r"everything\s+is\s+(?:a\s+)?mental\s+model|is\s+model|mental\s+model)$",
    re.I,
)
SAFE_REPLACEMENTS = {
    "蕞": "最", "決": "决", "綁": "绑", "雜": "杂", "製": "制",
    "轉": "转", "視": "视", "覺": "觉", "獨": "独", "處": "处",
    "導": "导", "學": "学", "個": "个", "與": "与", "從": "从",
    "為": "为", "這": "这", "現": "现", "會": "会", "過": "过",
    "裡": "里", "對": "对", "應": "应", "發": "发", "讓": "让",
    "無": "无", "體": "体", "國": "国", "點": "点", "說": "说",
    "當": "当", "並": "并", "產": "产", "遠": "远", "簡": "简",
    "複": "复", "維": "维", "線": "线", "節": "节", "開": "开",
    "關": "关", "連": "连", "過": "过", "錯": "错", "類": "类",
    "問": "问", "題": "题", "變": "变", "態": "态", "實": "实",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required for the optimized package")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, value: Any) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML is required for the optimized package")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def parse_pages(selector: str, total: int) -> list[int]:
    selector = selector.strip().lower()
    if selector == "all":
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start, end = int(left), int(right)
            pages.update(range(min(start, end), max(start, end) + 1))
        else:
            pages.add(int(part))
    result = sorted(pages)
    if not result or min(result) < 1 or max(result) > total:
        raise ValueError(f"pages must be within 1..{total}: {selector}")
    return result


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_text(value: Any) -> tuple[str, list[dict[str, str]]]:
    text = compact(value)
    changes: list[dict[str, str]] = []
    for source, target in SAFE_REPLACEMENTS.items():
        if source in text:
            before = text
            text = text.replace(source, target)
            changes.append({"from": source, "to": target, "before": before, "after": text})
    text = text.replace("（Triune Brain Theory），也称”“", "（Triune Brain Theory），也称“")
    text = re.sub(r"[ 　]+", " ", text)
    return text.strip(), changes


def compact_key(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", compact(value), flags=re.UNICODE).lower()


def is_generic_header(text: str) -> bool:
    key = compact_key(text)
    if not key:
        return not bool(re.search(r"[=＝→➜|]", compact(text)))
    if READER_NOISE_SIGNAL.fullmatch(compact(text)):
        return True
    normalized_headers = {compact_key(item) for item in GENERIC_HEADERS}
    if key in normalized_headers:
        return True
    if key in {"100", "100100", "100", "100个", "100个思维模型"} or re.fullmatch(r"/?100", key):
        return True
    if key.startswith("charliemunger") or key.startswith("everythingismodel") or key.startswith("everythingisamental") or key.startswith("everythingisamodel"):
        return True
    return False


def title_score(candidate: dict[str, Any], page: int) -> tuple[float, ...]:
    text = compact(candidate.get("text"))
    bbox = candidate.get("bbox") or {}
    y = float(bbox.get("y", 0) or 0)
    confidence = float(candidate.get("confidence", 0) or 0)
    numbered = bool(re.match(r"^\s*\d{1,3}\s*[.、]?\s*\S", text))
    signal = bool(TITLE_SIGNAL.search(text))
    in_band = 0.70 <= y <= 0.86
    sentence = bool(re.search(r"[。！？；;]", text))
    length = len(re.sub(r"\s+", "", text))
    page_prefix = bool(re.match(rf"^\s*0?{page}(?:\s*[.、]?|$)", text))
    return (
        float(in_band), float(signal), float(numbered), float(page_prefix),
        float(not sentence), float(confidence), -float(max(length, 1)),
        -abs(y - 0.81),
    )


def strip_page_prefix(text: str, page: int) -> str:
    value = compact(text)
    value = re.sub(rf"^\s*0?{page}\s*[.、]?\s*", "", value)
    value = re.sub(r"^\s*[：:：]\s*", "", value)
    value = re.sub(r"思维思维", "思维", value)
    return value.strip() or "待核对：页面语义标题"


def candidate_id(page: int, index: int) -> str:
    return f"ocr-p{page:03d}-c{index:03d}"


def group_candidates(candidates: list[dict[str, Any]], title_index: int | None, page: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]], int]:
    """Return prose, labels and compact uncertain items from one OCR page."""
    prose: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    changes: list[dict[str, str]] = []
    seen: set[str] = set()
    usable: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        if title_index is not None and index == title_index:
            continue
        text, edits = normalize_text(raw.get("text"))
        changes.extend(edits)
        if not text or is_generic_header(text):
            continue
        key = compact_key(text)
        if key in seen:
            continue
        seen.add(key)
        bbox = raw.get("bbox") or {}
        item = {
            "candidate_index": index,
            "candidate_id": candidate_id(page, index),
            "text": text,
            "source_text": compact(raw.get("text")),
            "confidence": float(raw.get("confidence", 0) or 0),
            "bbox": bbox,
            "y": float(bbox.get("y", 0) or 0),
            "x": float(bbox.get("x", 0) or 0),
            "width": float(bbox.get("width", 0) or 0),
        }
        usable.append(item)
    usable.sort(key=lambda item: (-item["y"], item["x"], item["candidate_index"]))
    # Same-column adjacent prose is safe to join; cross-column diagram labels
    # stay separate and are never converted into asserted relationships.
    merged: list[dict[str, Any]] = []
    for item in usable:
        compact_len = len(re.sub(r"\s+", "", item["text"]))
        prose_like = compact_len >= 18 or bool(re.search(r"[。！？；;：:]", item["text"])) or bool(SECTION_SIGNAL.search(item["text"]))
        if prose_like and merged:
            previous = merged[-1]
            gap = previous["y"] - item["y"]
            same_column = abs(previous["x"] - item["x"]) <= 0.12
            previous_len = len(re.sub(r"\s+", "", previous["text"]))
            if previous.get("prose_like") and same_column and 0 <= gap <= 0.035 and previous_len >= 12:
                cjk_join = bool(previous["text"] and item["text"] and re.match(r"[\u4e00-\u9fff]", previous["text"][-1]) and re.match(r"[\u4e00-\u9fff]", item["text"][0]))
                joiner = "" if cjk_join or previous["text"].endswith(("、", "，", "；", "：", "（", "“", "-")) or item["text"].startswith(("，", "。", "；", "：", "）")) else " "
                previous["text"] = (previous["text"] + joiner + item["text"]).strip()
                previous["source_candidate_ids"].append(item["candidate_id"])
                previous["candidate_indices"].append(item["candidate_index"])
                previous["confidence"] = min(previous["confidence"], item["confidence"])
                continue
        item["prose_like"] = prose_like
        item["source_candidate_ids"] = [item["candidate_id"]]
        item["candidate_indices"] = [item["candidate_index"]]
        merged.append(item)
    for item in merged:
        if item["prose_like"]:
            prose.append(item)
        else:
            labels.append(item)
        suspicious = item["confidence"] < 0.5 or any(ch in item["text"] for ch in ("｝", "《", "］", "•", "蕞", "雜", "製"))
        if suspicious:
            uncertain.append(item)
    # Merge short same-column labels before applying the reader-layer cap.
    compact_labels: list[dict[str, Any]] = []
    for item in labels:
        if compact_labels:
            previous = compact_labels[-1]
            gap = previous["y"] - item["y"]
            same_column = abs(previous["x"] - item["x"]) <= 0.08
            previous_len = len(re.sub(r"\s+", "", previous["text"]))
            item_len = len(re.sub(r"\s+", "", item["text"]))
            if same_column and 0 <= gap <= 0.035 and previous_len >= 3 and item_len >= 3:
                cjk_join = bool(previous["text"] and item["text"] and re.match(r"[\u4e00-\u9fff]", previous["text"][-1]) and re.match(r"[\u4e00-\u9fff]", item["text"][0]))
                joiner = "" if cjk_join else " "
                previous["text"] = (previous["text"] + joiner + item["text"]).strip()
                previous["source_candidate_ids"].extend(item.get("source_candidate_ids", []))
                previous["candidate_indices"].extend(item.get("candidate_indices", []))
                previous["confidence"] = min(previous["confidence"], item["confidence"])
                continue
        compact_labels.append(item)
    labels = compact_labels
    # One-character noise and repeated English banner fragments are not useful
    # reader text; the source page and immutable OCR JSON remain complete.
    labels = [item for item in labels if len(re.sub(r"\s+", "", item["text"])) > 1]
    omitted_label_count = max(0, len(labels) - 20)
    labels = labels[:20]
    # Keep the review layer compact; full candidates remain in immutable JSON.
    uncertain.sort(key=lambda item: (item["confidence"], -item["y"]))
    return prose, labels, uncertain[:6], changes, omitted_label_count


def find_page_asset(package: Path, page: int) -> Path | None:
    page_root = package / "assets"
    manifest_path = package / "manifest.yaml"
    if manifest_path.is_file() and yaml is not None:
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            for asset in manifest.get("assets", []) or []:
                if not isinstance(asset, dict) or asset.get("asset_kind") not in {"page-render", "input-image"}:
                    continue
                occurrences = ((asset.get("source") or {}).get("source_occurrences") or [])
                if any(item.get("page") == page for item in occurrences if isinstance(item, dict)):
                    relative = asset.get("file_path")
                    candidate = package / relative if isinstance(relative, str) else None
                    if candidate and candidate.is_file():
                        return candidate
        except (OSError, ValueError):
            pass
    matches = sorted(page_root.glob(f"*-p{page:03d}.png"))
    matches += sorted(page_root.glob(f"*-p{page:03d}-*.png"))
    if not matches:
        # Keep old optimized packages readable during migration; new output
        # must use the short semantic filename policy.
        matches = sorted(page_root.glob(f"*__p{page:03d}__*.png"))
    if not matches:
        legacy_root = package / "assets/pages"
        matches = sorted(legacy_root.glob(f"*-p{page:03d}.png"))
        matches += sorted(legacy_root.glob(f"*-p{page:03d}-*.png"))
        if not matches:
            matches = sorted(legacy_root.glob(f"*__p{page:03d}__*.png"))
    if not matches:
        # Reader-layer names no longer repeat page numbers. Resolve the
        # fallback by the caller's selected-page order.
        matches = sorted(page_root.glob("*.png"))
        if not matches:
            matches = sorted((package / "assets/pages").glob("*.png"))
        if 1 <= page <= len(matches):
            return matches[page - 1]
    return matches[0] if matches else None


def read_page_record(ocr: dict[str, Any], page: int) -> dict[str, Any] | None:
    return next((item for item in ocr.get("candidates", []) if isinstance(item, dict) and item.get("page") == page), None)


def process_page(ocr: dict[str, Any], package: Path, page: int) -> dict[str, Any]:
    record = read_page_record(ocr, page) or {}
    candidates = [item for item in record.get("candidates", []) if isinstance(item, dict)]
    normalized_candidates = []
    for index, item in enumerate(candidates):
        text, edits = normalize_text(item.get("text"))
        normalized_candidates.append({"index": index, "text": text, "confidence": float(item.get("confidence", 0) or 0), "bbox": item.get("bbox") or {}, "edits": edits})
    eligible = [item for item in candidates if compact(item.get("text")) and not is_generic_header(item.get("text", ""))]
    title_item = max(eligible, key=lambda item: title_score(item, page)) if eligible else None
    title_index = candidates.index(title_item) if title_item in candidates else None
    title_text = strip_page_prefix(normalize_text(title_item.get("text"))[0], page) if title_item else "待核对：页面语义标题"
    prose, labels, uncertain, changes, omitted_label_count = group_candidates(candidates, title_index, page)
    page_asset = find_page_asset(package, page)
    if not page_asset:
        raise FileNotFoundError(f"missing page asset for page {page}")
    relation = {"status": "Unknown/needs-review", "reason": "mac96 SSH unavailable; no visual relation conclusion was generated", "page_image_required": True}
    return {
        "page": page,
        "status": "complete",
        "title": title_text,
        "title_source": "apple-vision-candidate" if title_item else "needs-review-marker",
        "title_candidate_id": candidate_id(page, title_index) if title_index is not None else None,
        "prose": [{k: v for k, v in item.items() if k not in {"prose_like", "y", "x", "width"}} for item in prose],
        "labels": [{k: v for k, v in item.items() if k not in {"prose_like", "y", "x", "width"}} for item in labels],
        "omitted_label_count": omitted_label_count,
        "uncertain": [{k: v for k, v in item.items() if k not in {"prose_like", "y", "x", "width"}} for item in uncertain],
        "relation": relation,
        "source_candidate_count": int(record.get("candidate_count", len(candidates)) or 0),
        "source_engine": record.get("engine", "Apple Vision"),
        "page_asset": page_asset.relative_to(package).as_posix(),
        "normalization_changes": changes,
        "raw_page_evidence": f"evidence/apple-vision-p{page:03d}.json",
    }


def parse_document_pages(document: str, expected_pages: list[int] | None = None) -> dict[int, dict[str, Any]]:
    """Read the existing reader Markdown into isolated page sections.

    This repair pass intentionally uses only the assembled Markdown.  OCR JSON,
    bbox/confidence records, Apple Vision JSON, and page pixels remain package
    evidence and are not inputs to the cleanup decisions.
    """
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", document, flags=re.M))
    pages: dict[int, dict[str, Any]] = {}
    for ordinal, match in enumerate(matches, 1):
        title = compact(match.group(1))
        end = matches[ordinal].start() if ordinal < len(matches) else len(document)
        body = document[match.end():end]
        image_match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", body)
        page_match = re.search(r"(?:__p|-p)(\d{3})(?:__|-|\.)", image_match.group(1)) if image_match else None
        page = int(page_match.group(1)) if page_match else (
            expected_pages[ordinal - 1] if expected_pages and ordinal <= len(expected_pages) else ordinal
        )
        pages[page] = {"page": page, "title": title, "body": body, "page_asset": image_match.group(1) if image_match else ""}
    return pages


def _markdown_noise_line(text: str) -> bool:
    value = compact(re.sub(r"^[-*+]\s+", "", text))
    if not value or is_generic_header(value):
        return True
    compact_value = re.sub(r"\s+", "", value)
    if (
        len(compact_value) <= 1
        and not re.search(r"[\u3400-\u9fff]", compact_value)
        and not re.search(r"[=＝→➜|]", compact_value)
    ):
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}", value):
        return True
    return False


def _semantic_markdown_title(title: str, page: int) -> tuple[str, bool]:
    value = compact(re.sub(r"^0?\d{1,3}\s*[.、:：]?\s*", "", title))
    value = re.sub(r"思维思维", "思维", value)
    if not value or is_generic_header(value) or re.search(r"\d{4,}", value):
        return "待核对：页面语义标题", False
    return value, True


def process_markdown_page(record: dict[str, Any], package: Path, page: int) -> dict[str, Any]:
    """Repair one already assembled Markdown page without re-running OCR."""
    title, title_ok = _semantic_markdown_title(record.get("title", ""), page)
    body_text = record.get("body", "")
    body_text = re.sub(r"^\s*###\s+OCR\s+待复核候选\s*$", "", body_text, flags=re.M | re.I)
    body_text = re.sub(r"^\s*以下文字来自 OCR 候选.*$", "", body_text, flags=re.M)
    body_text = re.sub(r"^\s*候选状态为待复核.*$", "", body_text, flags=re.M)
    lines = body_text.splitlines()
    prose: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending_seen: set[str] = set()
    normalized_changes: list[dict[str, str]] = []
    paragraph_index = 0
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("### 原始页面图") or stripped.startswith("!["):
            continue
        if stripped.startswith("###"):
            continue
        text, edits = normalize_text(re.sub(r"^[-*+]\s+", "", stripped))
        normalized_changes.extend(edits)
        if _markdown_noise_line(text):
            continue
        key = compact_key(text)
        if key in seen:
            continue
        seen.add(key)
        block_id = f"markdown-p{page:03d}-b{paragraph_index:03d}"
        paragraph_index += 1
        block = {"text": text, "source_text": stripped, "source_block_ids": [block_id], "source": "index.md"}
        if any(marker in text for marker in ("｝", "《", "］", "蕞", "雜", "製", "概率") if marker not in {"概率"}):
            pending_key = compact_key(text)
            if pending_key not in pending_seen:
                uncertain.append(block)
                pending_seen.add(pending_key)
            continue
        if stripped.startswith(("-", "*", "+")) or re.match(r"^\d+[.、)]\s*", stripped):
            labels.append(block)
        elif re.search(r"[=＝→➜|]|(?:公式|计算)", text) or re.search(r"^(?:表格|节点|根节点|叶节点)", text):
            labels.append({**block, "role": "table-or-formula"})
        elif re.search(r"^(?:例如|案例|举例|思考|反思|总结|解读)", text):
            prose.append({**block, "role": "example-or-thought"})
        elif re.search(r"^(?:模型结构|结构|核心要素|组成|步骤|流程)", text):
            prose.append({**block, "role": "model-structure"})
        else:
            prose.append(block)
    page_asset = record.get("page_asset") or ""
    if page_asset:
        page_path = package / page_asset
        if not page_path.is_file():
            page_asset = ""
    if not page_asset:
        asset = find_page_asset(package, page)
        page_asset = asset.relative_to(package).as_posix() if asset else ""
    return {
        "page": page,
        "status": "complete" if page_asset else "needs-review",
        "title": title,
        "title_source": "index.md" if title_ok else "needs-review-marker",
        "title_candidate_id": f"markdown-p{page:03d}-title",
        "prose": prose,
        "labels": labels,
        "omitted_label_count": 0,
        "uncertain": uncertain[:8],
        "relation": {"status": "Unknown/needs-review", "reason": "Markdown repair does not infer visual relationships", "page_image_required": True},
        "source_candidate_count": len(prose) + len(labels) + len(uncertain),
        "source_engine": "assembled-document-markdown",
        "page_asset": page_asset,
        "normalization_changes": normalized_changes,
        "raw_page_evidence": None,
        "source_document": "index.md",
    }


def markdown_for_page(item: dict[str, Any]) -> str:
    lines = [f"## {item['title']}", ""]
    prose = item.get("prose") or []
    labels = item.get("labels") or []
    buckets: dict[str, list[str]] = {"正文": [], "列表与分区文字": [], "表格与公式": [], "举例与思考": [], "模型结构": []}
    for block in prose:
        text = compact(block.get("text"))
        if not text:
            continue
        if re.search(r"[=＝→➜]|(?:公式|计算|成本为)", text):
            bucket = "表格与公式"
        elif re.search(r"^(?:例如|案例|举例|思考|反思|问题)", text):
            bucket = "举例与思考"
        elif re.search(r"^(?:模型结构|结构|核心要素|组成|步骤|流程)", text):
            bucket = "模型结构"
        else:
            bucket = "正文"
        buckets[bucket].append(text)
    for label in labels:
        text = compact(label.get("text"))
        if not text:
            continue
        if re.search(r"[=＝→➜]|(?:公式|计算)", text):
            bucket = "表格与公式"
        elif re.search(r"^(?:例如|案例|举例|思考|反思|问题)", text):
            bucket = "举例与思考"
        elif re.search(r"^(?:模型结构|结构|核心要素|组成|步骤|流程)", text):
            bucket = "模型结构"
        else:
            bucket = "列表与分区文字"
        buckets[bucket].append(text)
    emitted = False
    for bucket, values in buckets.items():
        if not values:
            continue
        emitted = True
        lines.extend([f"### {bucket}", ""])
        for value in values:
            lines.extend([f"- {value}" if bucket != "正文" else value, ""])
    if not emitted:
        lines.extend(["### 正文", "", "页面正文未能从现有 Markdown 稳定恢复。", ""])
    if item.get("omitted_label_count"):
        lines.extend([f"- 另有 {item['omitted_label_count']} 个低结构短标签仅保留在页面图和证据层。", ""])
    lines.extend([
        "### 已确认的关系说明",
        "",
        "关系结论：Unknown/needs-review。当前环境未完成 mac96 页面关系复核，因此不对箭头、层级、流程或图示连接作推断。",
        "",
        f"![原始页面图]({item['page_asset']})",
        "",
        "### 待核对项",
        "",
    ])
    uncertain = item.get("uncertain") or []
    if uncertain:
        for candidate in uncertain:
            text = compact(candidate.get("text"))
            reason = "字符异常或需结合页面图核对" if candidate.get("source") == "index.md" else "字符或布局仍需结合页面图核对"
            lines.append(f"- 文本：{text}；原因：{reason}。")
    else:
        lines.append("- 暂无单独列出的文字项；复杂图示关系仍保留为 Unknown/needs-review。")
    lines.append("")
    return "\n".join(lines)


def build_document(package: Path, pages: list[int], page_items: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    chunks = ["# 查理芒格的 100 个思维模型", "", "本版本按原始扫描页面恢复标题、正文、分区文字和页面图；未确认的图示连接保留 Unknown/needs-review。", ""]
    for page in pages:
        item = page_items.get(page) or {
            "page": page,
            "status": "failed",
            "title": "待核对：页面语义标题",
            "prose": [],
            "labels": [],
            "uncertain": [],
            "relation": {"status": "Unknown/needs-review", "reason": "页面清洗批次失败"},
            "page_asset": find_page_asset(package, page).relative_to(package).as_posix() if find_page_asset(package, page) else "",
        }
        if not item.get("page_asset"):
            continue
        chunks.append(markdown_for_page(item))
        chunks.append("")
        occurrence = f"{package.name}-p{page:03d}"
        sections.append({
            "section_id": f"page-{page}",
            "title": item.get("title") or "待核对：页面语义标题",
            "body": "\n".join(compact(block.get("text")) for block in (item.get("prose") or []) if compact(block.get("text"))) or "页面正文未能从现有 Markdown 稳定恢复。",
            "source_occurrences": [occurrence],
            "visual_asset_ids": [f"page-p{page:03d}"],
            "confidence": "medium" if item.get("title_source") != "needs-review-marker" else "low",
            "reader_order": "semantic-before-visual",
            "unknown_forms": ["visual-relations-unknown"],
            "unresolved": [{"page": page, "reason": "mac96 visual relation review unavailable"}],
        })
    (package / "index.md").write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")
    return sections


def asset_records(package: Path, source: Path, package_id: str, pages: list[int]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in pages:
        path = find_page_asset(package, page)
        if path is None:
            continue
        width = height = 0
        mime = "image/png"
        if Image is not None:
            with Image.open(path) as image:
                width, height = image.size
                mime = Image.MIME.get(image.format, mime)
        records.append({
            "asset_id": f"page-p{page:03d}",
            "file_name": path.name,
            "file_path": path.relative_to(package).as_posix(),
            "display_name": f"原始页面 {page}",
            "asset_kind": "page-render",
            "source": {
                "package_id": package_id,
                "source_fingerprint": sha256(source),
                "original_filename": source.name,
                "source_occurrences": [{
                    "source_occurrence_id": f"{package_id}-p{page:03d}",
                    "source_file": str(source),
                    "page": page,
                    "region": "full-page",
                    "object_ids": [],
                    "parent_asset_id": None,
                    "ordinal": 1,
                    "status": "located",
                }],
            },
            "media": {"mime_type": mime, "width": width, "height": height, "sha256": sha256(path)},
            "semantic": {"label": "原始页面图", "description": "保留用于视觉复核的来源页图", "confidence": "high"},
            "naming": {"source_slug": "100-models", "location_slug": f"p{page:03d}", "semantic_slug": re.sub(r"-\d{2}$", "", path.stem), "purpose_slug": "page-render", "ordinal": 1, "status": "generated"},
            "status": "available",
        })
    return records


def append_progress(path: Path, package_id: str, fingerprint: str, stage: str, status: str, pages: list[int], evidence: list[str], next_action: str, diagnostics: list[str] | None = None) -> None:
    event = {
        "at": now(), "finished_at": now(), "run_id": package_id, "source_fingerprint": fingerprint,
        "stage": stage, "task": stage, "page": None, "pages": pages, "attempt": 1,
        "status": status, "elapsed_seconds": 0.0, "evidence_paths": evidence,
        "next_action": next_action, "diagnostics": diagnostics or [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def stage(stage_id: str, status: str, paths: list[str], diagnostics: list[str], unknown: list[str], command: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id, "status": status, "evidence_level": "L2-local-runtime",
        "evidence_paths": paths, "diagnostics": diagnostics, "unknown": unknown,
        "verification_commands": [command], "score_basis": "真实来源的本地执行证据；不把未完成视觉关系复核升级为通过",
    }


def failure(fid: str, stage_id: str, code: str, message: str, paths: list[str], remediation: str, retryable: bool = True) -> dict[str, Any]:
    return {"failure_id": fid, "stage_id": stage_id, "code": code, "severity": "P1", "retryable": retryable, "status": "open", "message": message, "evidence_paths": paths, "remediation": remediation, "resolved_evidence_paths": []}


def write_markdown_content_review(package: Path, page_items: dict[int, dict[str, Any]]) -> None:
    items: list[dict[str, Any]] = []
    for page, item in sorted(page_items.items()):
        occurrence = f"{package.name}-p{page:03d}"
        section_id = f"page-{page}"
        items.append({
            "id": f"title-p{page:03d}", "kind": "heading", "source_occurrence": occurrence,
            "section_id": section_id, "reader_text": item.get("title"),
            "source_text": item.get("title"), "candidate_text": item.get("title"), "proposed_text": item.get("title"),
            "repair": {"operation": "none", "basis": "markdown-context", "before": item.get("title"), "after": item.get("title"), "evidence": ["index.md"]},
            "rationale": "只处理现有 Markdown 标题中的页面编号和噪声；不读取或改写外部提取证据。",
            "status": "applied", "decision_note": "按确定性 Markdown 内容修复规则直接写回",
        })
        for ordinal, block in enumerate(item.get("prose") or [], 1):
            source_text = block.get("source_text", block.get("text"))
            reader_text = block.get("text")
            items.append({
                "id": f"text-p{page:03d}-{ordinal:03d}", "kind": "paragraph", "source_occurrence": occurrence,
                "section_id": section_id, "reader_text": reader_text,
                "source_text": source_text, "candidate_text": reader_text, "proposed_text": reader_text,
                "repair": {"operation": "none", "basis": "markdown-context", "before": source_text, "after": reader_text, "evidence": ["index.md"]},
                "rationale": "仅按当前 Markdown 页内文字去重、移除 banner 和无意义碎片；不跨页、不推断图示关系。", "status": "applied", "decision_note": "按确定性 Markdown 内容修复规则直接写回",
            })
    write_yaml(package / "reviews/markdown-content-repair.yaml", {"contract": "markdown-content-repair", "version": "0.3.0", "package_id": package.name, "document": "index.md", "generated_from": ["index.md"], "status": "applied", "items": items})


def audit_package(package: Path, pages: list[int], page_items: dict[int, dict[str, Any]], sections: list[dict[str, Any]], raw_hashes: dict[str, str]) -> dict[str, Any]:
    document = (package / "index.md").read_text(encoding="utf-8")
    headings = re.findall(r"^##\s+(.+)$", document, flags=re.M)
    image_links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", document)
    generic_counts = Counter()
    for item in page_items.values():
        for block in (item.get("prose") or []) + (item.get("labels") or []):
            text = compact(block.get("text"))
            if is_generic_header(text):
                generic_counts[text] += 1
    fallback_titles = [page for page, item in page_items.items() if str(item.get("title", "")).startswith("待核对：")]
    suspicious_markers = [marker for marker in ("蕞", "雜", "製", "｝", "《", "］") if marker in document]
    checks = {
        "page_count_covered": len(sections) == len(pages) == 100,
        "semantic_heading_per_page": len(headings) == len(sections) == len(pages),
        "page_image_per_page": len(image_links) == len(sections) == len(pages) and all((package / link).is_file() for link in image_links),
        "generic_header_not_in_reader_layer": not generic_counts and "Charlie Munger's" not in document,
        "raw_candidate_dump_absent": document.count("候选") < 20 and "bbox" not in document and "confidence" not in document,
        "fallback_titles_explicit": all(str(page_items[page].get("title", "")).startswith("待核对：") or page_items[page].get("title") for page in pages),
        "raw_evidence_immutable_hashes_recorded": bool(raw_hashes),
    }
    score = {
        "source_traceability": 10 if checks["raw_evidence_immutable_hashes_recorded"] else 0,
        "structure_mapping": 14 if checks["semantic_heading_per_page"] else 0,
        "content_completeness": 18 if checks["page_count_covered"] else 0,
        "graphic_fidelity": 14 if checks["page_image_per_page"] else 0,
        "provenance_uncertainty": 10,
        "visual_asset_traceability": 10 if checks["page_image_per_page"] else 0,
        "unknown_form_coverage": 3,
        "repair_reaudit_recovery": 4,
    }
    return {
        "report_version": "0.2.0", "status": "needs-review", "executed_at": now(),
        "scope": "optimized source package; deterministic reader-layer audit",
        "selected_pages": pages, "checks": checks, "fallback_titles": fallback_titles,
        "generic_header_counts": dict(generic_counts), "suspicious_reader_markers": suspicious_markers,
        "score": score, "computed_total": sum(score.values()), "minimum": 95, "target": 97,
        "blocked_by": ["mac96 visual relation review unavailable", "human language/visual review still required"],
        "raw_evidence_sha256": raw_hashes,
        "post_edit_reaudit": {"status": "complete", "document_sha256": sha256(package / "index.md")},
        "diagnostics": ["审计脚本已实际读取 Markdown、页面链接、派生页记录和原始证据哈希；结果仍需视觉复核，不能写成 passed。"],
        "next_action": "完成第1、2、24、25页及其余页面的人工视觉关系复核后再重新审计。",
    }


def build_reconciliation(package: Path, pages: list[int], page_items: dict[int, dict[str, Any]], raw_hashes: dict[str, str]) -> dict[str, Any]:
    unresolved = [{"page": page, "reason": "mac96 visual relation review unavailable", "status": "Unknown/needs-review", "page_image": page_items[page].get("page_asset")} for page in pages]
    return {
        "version": "0.4.0", "native_input": "index.md", "ocr_input": None, "visual_input": "evidence/mac96-visual-review.json",
        "reconciled_items": [{"page": page, "title": page_items[page].get("title"), "title_source": page_items[page].get("title_source"), "source_block_count": page_items[page].get("source_candidate_count", 0), "decision": "reader-layer-markdown-repair", "status": "candidate", "source_evidence": "index.md"} for page in pages],
        "conflicts": [], "unresolved": unresolved, "unknown_forms": [{"page": page, "form": "visual-relation", "status": "Unknown/needs-review"} for page in pages],
        "coverage_notes": {"pages": len(pages), "markdown_pages": len(page_items), "raw_evidence_sha256": raw_hashes, "mac96_status": "unavailable", "relation_policy": "no relation claims without visual confirmation", "reader_layer_policy": "repair reads index.md; OCR evidence remains immutable and outside reader layer"},
        "status": "needs-review", "diagnostics": ["A5 consumes the existing index.md; no OCR JSON or bbox data is used for cleanup."],
    }


def write_classification(package: Path, pages: list[int], page_items: dict[int, dict[str, Any]]) -> None:
    payload = {"version": "0.4.0", "status": "complete", "source": "index.md", "pages": [{"page": page, "status": page_items[page].get("status"), "page_title": {"text": page_items[page].get("title"), "source": page_items[page].get("title_source"), "quality_flags": ["markdown-title"] if page_items[page].get("title_source") != "needs-review-marker" else ["fallback-title"]}, "classified_blocks": [{"role": block.get("role", "body-content"), "text": block.get("text"), "source_block_ids": block.get("source_block_ids", [])} for block in page_items[page].get("prose", [])] + [{"role": block.get("role", "layout-label"), "text": block.get("text"), "source_block_ids": block.get("source_block_ids", [])} for block in page_items[page].get("labels", [])], "text_evidence": {"source_layer": "assembled-document-markdown", "raw_ocr_promoted": False, "source_block_count": page_items[page].get("source_candidate_count", 0)}} for page in pages], "diagnostics": ["classification executed from existing index.md page order; it does not assert visual relationships"]}
    write_json(package / "evidence/content-classification.json", payload)


def build_manifest(package: Path, source: Path, pages: list[int], sections: list[dict[str, Any]], assets: list[dict[str, Any]], stage_evidence: list[dict[str, Any]], package_status: str, quality_audit: dict[str, Any]) -> dict[str, Any]:
    fingerprint = sha256(source)
    return {
        "manifest_version": "0.9.0", "workflow_id": "document-to-markdown-assets", "workflow_version": WORKFLOW_VERSION,
        "package_id": package.name, "kind": "markdown-asset-package", "status": package_status,
        "source": {"path": str(source), "path_label": source.name, "media_type": "application/pdf", "source_fingerprint": fingerprint, "selected_pages": pages, "total_pages": 100, "source_selection": "single-source", "candidate_comparison": None},
        "markdown": {"path": "index.md", "sections": sections}, "assets_manifest": {"path": "manifest.yaml", "type": "source-visual-asset-manifest"}, "assets": assets,
        "evidence": {"source_inventory": "evidence/source-inventory.json", "capability_probe": "evidence/capability-probe.json", "progress": "evidence/progress.jsonl", "ocr_candidates": "evidence/ocr-candidates.json", "reconciliation": "evidence/reconciliation.json", "language_repair": "evidence/language-repair-draft.json", "quality_audit": "evidence/quality-audit.json", "raw_immutable_hashes": "evidence/raw-immutable-hashes.json"},
        "processing": {"native_extraction": "preserved-from-v2", "ocr_used": False, "visual_review": "unavailable", "execution_path": "document-markdown+deterministic-content-repair", "fallback_reason": "mac96 unavailable; page images and relation Unknown markers retained", "status": package_status, "stage_evidence": stage_evidence, "stage_evidence_contract": "contracts/stage-evidence.yaml", "failure_report": "reports/failure-report.yaml", "model_calls": [], "context_policy": {"one_source_per_run": True, "raw_ocr_immutable": True, "repair_input": "index.md"}, "language_quality_repair": {"status": "complete", "evidence": "reviews/markdown-content-repair.yaml"}, "quality_audit": {"status": quality_audit.get("status"), "evidence": "reports/quality-report.yaml"}},
        "validation": {"validator_version": "not-run-by-request", "status": "not-run", "checks": ["source fingerprint", "raw evidence immutability", "100-page semantic coverage", "page image links", "stage evidence A1-A7"]},
        "quality_gate": {"score_scope": "per-page-or-affected-region", "minimum_total": 95, "target_total": 97, "status": "needs-review", "score": quality_audit.get("computed_total"), "blocked_by": quality_audit.get("blocked_by", []), "report": "reports/quality-report.yaml"},
    }


def prepare_package(base: Path, target: Path, source: Path, resume: bool) -> dict[str, str]:
    if not resume or not target.exists():
        if target.exists():
            raise FileExistsError(f"refusing to overwrite existing optimized package: {target}")
        shutil.copytree(base, target)
    target.mkdir(parents=True, exist_ok=True)
    raw_hashes: dict[str, str] = {}
    for relative in RAW_FILES:
        path = base / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        raw_hashes[relative] = sha256(path)
        destination = target / relative
        if not destination.is_file():
            shutil.copy2(path, destination)
        if sha256(destination) != raw_hashes[relative]:
            raise RuntimeError(f"immutable raw evidence drifted: {relative}")
    for path in sorted(base.glob(RAW_GLOB)):
        relative = path.relative_to(base).as_posix()
        raw_hashes[relative] = sha256(path)
        destination = target / relative
        if not destination.is_file():
            shutil.copy2(path, destination)
        if sha256(destination) != raw_hashes[relative]:
            raise RuntimeError(f"immutable raw evidence drifted: {relative}")
    write_json(target / "evidence/raw-immutable-hashes.json", {"generated_at": now(), "source_package": str(base), "hashes": raw_hashes, "policy": "raw OCR JSON and page-level Apple Vision JSON are copied byte-for-byte and never overwritten"})
    return raw_hashes


def execute(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    base = args.base_package.resolve()
    output_root = args.output_root.resolve()
    if not source.is_file() or not base.is_dir():
        raise FileNotFoundError("source and base package must exist")
    if sha256(source) != args.expected_sha256:
        raise RuntimeError("source SHA-256 does not match the requested immutable input")
    total = 100
    requested = parse_pages(args.pages, total)
    package = output_root / args.package_name
    raw_hashes = prepare_package(base, package, source, args.resume)
    package_id = package.name
    progress_path = package / "evidence/progress.jsonl"
    if not args.resume:
        progress_path.write_text("", encoding="utf-8")
    append_progress(progress_path, package_id, sha256(source), "execution_preflight", "complete", requested, ["evidence/preflight.json"], "复用 v2 A1-A3/A6-A7 证据，进入 A5 Markdown 修复")
    document_source = base / "index.md"
    if not document_source.is_file():
        raise FileNotFoundError(document_source)
    document_pages = parse_document_pages(document_source.read_text(encoding="utf-8"))
    missing_document_pages = sorted(set(requested) - set(document_pages))
    if missing_document_pages:
        raise RuntimeError(f"existing index.md is missing pages: {missing_document_pages}")
    append_progress(progress_path, package_id, sha256(source), "source_inventory", "preserved", requested, ["index.md", "manifest.yaml"], "A1-A3/A6/A7 复用 v2，不重跑")
    page_items: dict[int, dict[str, Any]] = {}
    failed_pages: list[int] = []
    processed: set[int] = set()
    for path in sorted((package / "evidence").glob("optimized-page-p*.json")):
        try:
            item = load_json(path)
            if item.get("page"):
                page_items[int(item["page"])] = item
                processed.add(int(item["page"]))
        except Exception:
            continue
    batches: list[dict[str, Any]] = []
    batch_pages = [page for page in requested if page not in processed]
    if batch_pages:
        chunks = [batch_pages[index:index + max(1, args.batch_size)] for index in range(0, len(batch_pages), max(1, args.batch_size))]
        for batch_index, chunk in enumerate(chunks, 1):
            batch_record = {"batch_id": f"b{batch_index:03d}", "pages": chunk, "started_at": now(), "status": "running", "failed_pages": []}
            for page in chunk:
                try:
                    item = process_markdown_page(document_pages[page], package, page)
                    page_items[page] = item
                    write_json(package / f"evidence/optimized-page-p{page:03d}.json", item)
                    processed.add(page)
                except Exception as exc:
                    failed_pages.append(page)
                    batch_record["failed_pages"].append({"page": page, "error": repr(exc)})
                    page_items[page] = {"page": page, "status": "failed", "title": "待核对：页面语义标题", "prose": [], "labels": [], "uncertain": [], "relation": {"status": "Unknown/needs-review", "reason": repr(exc)}, "page_asset": find_page_asset(package, page).relative_to(package).as_posix() if find_page_asset(package, page) else ""}
            batch_record["finished_at"] = now()
            batch_record["status"] = "complete" if not batch_record["failed_pages"] else "needs-review"
            batches.append(batch_record)
            checkpoint = {"version": "0.3.0", "package_id": package_id, "source_fingerprint": sha256(source), "batch_size": args.batch_size, "completed_pages": sorted(processed), "failed_pages": failed_pages, "batches": batches, "next_action": "继续下一批" if len(processed) < len(requested) else "执行 A5/A7 派生证据收尾"}
            write_json(package / "checkpoint.json", checkpoint)
            write_json(package / "evidence/checkpoint.json", checkpoint)
            append_progress(progress_path, package_id, sha256(source), "batch_checkpoint", batch_record["status"], chunk, ["checkpoint.json", "evidence/checkpoint.json"], checkpoint["next_action"], [repr(item) for item in batch_record["failed_pages"]])
    effective_pages = sorted(set(processed) | set(page_items))
    # Full/resumed assembly.  Re-read all page records and rebuild derived
    # evidence, which keeps adjacent pages from being overwritten by a batch.
    if set(requested) - set(page_items):
        missing = sorted(set(requested) - set(page_items))
        raise RuntimeError(f"full optimization is incomplete; missing pages: {missing}")
    sections = build_document(package, requested, page_items)
    write_classification(package, requested, page_items)
    write_markdown_content_review(package, page_items)
    reconciliation = build_reconciliation(package, requested, page_items, raw_hashes)
    write_json(package / "evidence/reconciliation.json", reconciliation)
    language = {"status": "complete", "method": "deterministic-document-markdown-content-repair", "raw_inputs": ["index.md"], "evidence_only": ["evidence/ocr-candidates.json", "evidence/apple-vision-p*.json", "assets/"], "output": "index.md", "pages": requested, "changed_counts": {"pages": len(requested), "glyph_replacement_operations": sum(len(item.get("normalization_changes", [])) for item in page_items.values()), "deduplicated_header_lines": len(GENERIC_HEADERS)}, "unresolved": ["visual relation review", "human review of compact pending text"], "diagnostics": ["A5 只读取现有 index.md；原始 OCR、bbox、置信度、逐页 Apple Vision JSON 和页面图未作为清洗输入。"], "next_action": "对第1、2、24、25页和其余页面执行人工语言/视觉复核。"}
    write_json(package / "evidence/language-repair-draft.json", language)
    audit = {"status": "preserved", "source": "v2 reports/quality-report.yaml", "reason": "A7 已在 v2 真实完成，本轮不重复运行；仅记录 A5 派生包。", "selected_pages": requested, "blocked_by": ["mac96 visual relation review unavailable", "human review of compact pending text"], "document_sha256": sha256(package / "index.md")}
    write_json(package / "evidence/quality-audit.json", audit)
    write_yaml(package / "reports/quality-report.yaml", audit)
    stage_evidence = [stage("A1", "complete", ["evidence/preflight.json", "evidence/source-inventory.json"], ["preserved from v2; not re-executed"], [], "source package v2"), stage("A2", "complete", ["evidence/native-content-model.json", "evidence/content-classification.json"], ["preserved from v2; not re-executed"], [], "source package v2"), stage("A3", "complete", ["evidence/rendering.json", "assets/"], ["preserved from v2; page pixels copied unchanged"], [], "source package v2"), stage("A4", "needs-review", ["evidence/capability-probe.json", "evidence/reconciliation.json"], ["mac96 unavailable; relation review not completed"], ["visual-relations-unknown"], "human visual review"), stage("A5", "complete", ["index.md", "reviews/markdown-content-repair.yaml", "evidence/language-repair-draft.json"], ["existing index.md was actually repaired; human review remains"], ["human language review pending"], "python3 adapters/workflow_a_optimized_runner.py --pages all"), stage("A6", "complete", ["manifest.yaml", "evidence/raw-immutable-hashes.json"], ["preserved from v2; raw evidence copied byte-for-byte"], [], "source package v2"), stage("A7", "complete", ["reports/quality-report.yaml"], ["preserved from v2; no independent validator or new A7 audit run"], ["visual review pending"], "source package v2")]
    assets = asset_records(package, source, package_id, requested)
    manifest = build_manifest(package, source, requested, sections, assets, stage_evidence, "needs-review", audit)
    write_yaml(package / "manifest.yaml", manifest)
    processing = {"status": "needs-review", "evidence_level": "L2-local-runtime", "run_id": package_id, "started_at": now(), "finished_at": now(), "real_business_result_package": str(package), "input": {"source": str(source), "source_type": "pdf", "sha256": sha256(source), "pages": requested, "total_pages": total}, "progress_stream": "evidence/progress.jsonl", "stage_evidence": stage_evidence, "stages": [{"id": item["stage_id"], "status": item["status"], "evidence": item["evidence_paths"]} for item in stage_evidence], "language_quality_repair": language, "quality_audit": audit, "failure_report": "reports/failure-report.yaml", "tools": {"repair_input": "index.md", "markdown_writer": "adapters/workflow_a_optimized_runner.py"}}
    failures = [failure("a4-visual-review-unavailable", "A4", "visual-review-unavailable", "mac96 SSH target is unavailable; visual relations remain Unknown/needs-review.", ["evidence/capability-probe.json", "evidence/reconciliation.json"], "Run mac96 or complete human review for relation-dense pages."), failure("language-human-review-pending", "A5", "review-failed", "Markdown content repair ran, but compact pending text still needs human review.", ["evidence/language-repair-draft.json", "reviews/markdown-content-repair.yaml"], "Review compact pending items against the current Markdown.")]
    write_yaml(package / "reports/processing-report.yaml", processing)
    write_yaml(package / "reports/acceptance-report.yaml", {"status": "needs-review", "evidence_level": "L2-local-runtime", "stage_evidence": stage_evidence, "failures": [item["failure_id"] for item in failures], "warnings": [item["message"] for item in failures], "failure_report": "reports/failure-report.yaml"})
    write_yaml(package / "reports/failure-report.yaml", {"status": "needs-review", "evidence_level": "L2-local-runtime", "failures": failures, "warnings": [item["message"] for item in failures], "diagnostics": ["全量 Markdown 修复已执行；A1-A3/A6/A7 复用 v2，视觉关系与部分语言项仍需人工复核。"]})
    validator = {"status": "not-run", "valid": None, "package_root": str(package), "errors": [], "warnings": ["独立 validator 按本轮要求未运行；仅保留确定性审计和原始证据哈希。"]}
    manifest["validation"]["status"] = "not-run"
    write_yaml(package / "manifest.yaml", manifest)
    write_json(package / "reports/validation-result.json", validator)
    append_progress(progress_path, package_id, sha256(source), "text_structure_or_relation_review_or_fallback", "needs-review", requested, ["evidence/capability-probe.json", "evidence/reconciliation.json"], "A5 Markdown 修复已完成；关系保留 Unknown")
    append_progress(progress_path, package_id, sha256(source), "language_quality_repair", "complete", requested, ["evidence/language-repair-draft.json", "reviews/markdown-content-repair.yaml"], "A6 资产登记")
    append_progress(progress_path, package_id, sha256(source), "markdown_assembly", "complete", requested, ["index.md", "manifest.yaml"], "A7 确定性审计")
    append_progress(progress_path, package_id, sha256(source), "semantic_visual_quality_audit", "complete", requested, ["evidence/quality-audit.json", "reports/quality-report.yaml"], "独立 validator 按要求未运行；保持 needs-review")
    append_progress(progress_path, package_id, sha256(source), "independent_validation", "not-run", requested, ["reports/validation-result.json"], "按本轮要求不运行独立 validator")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base-package", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--pages", default="all")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--expected-sha256", default="be988bf98709f1637c8c6be972d25aefb1e9230dbee0c88479cafc0c3d8b20a9")
    args = parser.parse_args(argv)
    try:
        return execute(args)
    except Exception as exc:
        print(f"optimized workflow failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
