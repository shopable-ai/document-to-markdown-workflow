#!/usr/bin/env python3
"""Compare two explicitly declared format candidates without creating a package."""
from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

try:
    from workflow_a_runner import (
        media_type_for,
        parse_pages,
        pdf_info,
        pdf_layout_model,
        pptx_native_model,
        pptx_slide_count,
        render_source,
        render_visibility_evidence,
        sha256,
        source_slug,
        now,
        write_render_fontconfig,
    )
except ImportError:  # pragma: no cover
    from .workflow_a_runner import (
        media_type_for,
        parse_pages,
        pdf_info,
        pdf_layout_model,
        pptx_native_model,
        pptx_slide_count,
        render_source,
        render_visibility_evidence,
        sha256,
        source_slug,
        now,
        write_render_fontconfig,
    )


def source_type(path: Path) -> str:
    return {".pptx": "pptx", ".pdf": "pdf"}[path.suffix.lower()]


def total_pages(path: Path, kind: str) -> int:
    if kind == "pptx":
        return pptx_slide_count(path)
    return int(pdf_info(path)[0].get("Pages", 0) or 0)


def normalized_text(model: dict) -> str:
    blocks = model.get("text_blocks") or []
    return re.sub(r"\s+", "", "\n".join(str(item.get("text", "")) for item in blocks))


def candidate_record(path: Path, pages: list[int], root: Path) -> dict:
    kind = source_type(path)
    evidence = root / source_slug(path)
    assets = root / f"{source_slug(path)}-assets"
    evidence.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    fontconfig = write_render_fontconfig(evidence)
    native = pptx_native_model(path, pages, fontconfig) if kind == "pptx" else pdf_layout_model(path, pages)
    rendered, rendering = render_source(path, kind, assets, evidence, pages, source_slug(path), fontconfig)
    visibility = render_visibility_evidence(rendered, native)
    text = normalized_text(native)
    char_count = len(text)
    render_coverage = len(rendered) / max(1, len(pages))
    visible_coverage = sum(item.get("visible_text_regions", 0) >= max(1, int(item.get("native_text_blocks", 0) * 0.5)) for item in visibility.get("pages", [])) / max(1, len(pages))
    native_complete = native.get("status") in {"complete", "partial"} and len(native.get("pages") or native.get("slides") or []) == len(pages)
    score = round((0.35 if native_complete else 0.0) + 0.4 * render_coverage + 0.2 * visible_coverage + (0.05 if kind == "pptx" and native.get("nodes") else 0.03 if kind == "pdf" and native.get("text_blocks") else 0.0), 4)
    return {
        "path": str(path),
        "media_type": media_type_for(path),
        "sha256": sha256(path),
        "media_type_short": kind,
        "total_pages": total_pages(path, kind),
        "selected_pages": pages,
        "compared_at": now(),
        "visual_model": "not-called; representative pages use local native/layout and pixel evidence",
        "representative_assets": {str(page): str(rendered[page]) for page in sorted(rendered)},
        "native": {"status": native.get("status"), "text_char_count": char_count, "text_block_count": len(native.get("text_blocks", [])), "node_count": len(native.get("nodes", [])), "relation_count": len(native.get("relations", [])), "diagnostics": native.get("diagnostics", [])},
        "rendering": rendering,
        "text_visibility": visibility,
        "score": score,
        "temporary_evidence_root": str(evidence),
    }


def compare(source_a: Path, source_b: Path, pages: list[int], root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    left = candidate_record(source_a, pages, root)
    right = candidate_record(source_b, pages, root)
    similarity = SequenceMatcher(None, normalized_text(_native_for_compare(source_a, pages)), normalized_text(_native_for_compare(source_b, pages))).ratio()
    same_stem = source_a.stem == source_b.stem
    pdf_candidate = next((item for item in (left, right) if item["media_type_short"] == "pdf"), None)
    pptx_candidate = next((item for item in (left, right) if item["media_type_short"] == "pptx"), None)
    same_page_count = pdf_candidate and pptx_candidate and pdf_candidate["total_pages"] == pptx_candidate["total_pages"]
    if same_stem and same_page_count and pdf_candidate and pdf_candidate["text_visibility"].get("status") == "complete":
        preferred = pdf_candidate
        selection_reason = "同名同页数候选被声明为同一逻辑文档；按当前 Workflow A 选择规则，优先采用 PDF 原始页面截图内容。PPTX 的 Office 渲染、原生结构和差异仅作为候选证据保留，不进入主来源 package。"
    else:
        preferred = left if left["score"] >= right["score"] else right
        selection_reason = "优先选择代表页原生结构完整、来源自有页面渲染覆盖完整且原生文字区域具有可见像素的候选；分数仅用于候选比较，不替代最终逐页视觉复核。"
    return {
        "comparison_version": "1.0.0",
        "comparison_scope": "same-logical-document-representative-pages",
        "selected_pages": pages,
        "candidates": [left, right],
        "text_similarity": round(similarity, 4),
        "preferred_source": preferred["path"],
        "selection_reason": selection_reason,
        "uncertainties": ["字体替代可能改变换行和字宽；主来源全量运行仍需逐页复核。"],
    }


def _native_for_compare(path: Path, pages: list[int]) -> dict:
    kind = source_type(path)
    if kind == "pptx":
        return pptx_native_model(path, pages)
    return pdf_layout_model(path, pages)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two explicitly declared Workflow A candidates")
    parser.add_argument("--source-a", type=Path, required=True)
    parser.add_argument("--source-b", type=Path, required=True)
    parser.add_argument("--pages", default="3,4")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_a, source_b = args.source_a.expanduser().resolve(), args.source_b.expanduser().resolve()
    if not source_a.is_file() or not source_b.is_file() or source_a == source_b:
        parser.error("source-a and source-b must be two readable, different files")
    kinds = {source_type(source_a), source_type(source_b)}
    if kinds != {"pptx", "pdf"}:
        parser.error("candidate comparison requires one PPTX and one PDF")
    total = min(total_pages(source_a, source_type(source_a)), total_pages(source_b, source_type(source_b)))
    try:
        pages = parse_pages(args.pages, total)
    except ValueError as exc:
        parser.error(str(exc))
    output_path = args.output.expanduser().resolve()
    result = compare(source_a, source_b, pages, output_path.parent / f"{output_path.stem}-assets")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "complete", "preferred_source": result["preferred_source"], "output": str(output_path), "pages": pages}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
