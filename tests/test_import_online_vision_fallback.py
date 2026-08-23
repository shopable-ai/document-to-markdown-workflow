from __future__ import annotations

import argparse
import json
from pathlib import Path

import adapters.import_online_vision_fallback as importer
from adapters.mac96_pdf_vision_runner import digest


def raw_fallback(page: int) -> dict:
    return {
        "page": page,
        "provider": "current-conversation",
        "model": "gpt-test",
        "model_call_count": 1,
        "prompt_eval_count": "Unknown",
        "eval_count": "Unknown",
        "markdown": "错误文字",
        "page_content_type": "text-only",
        "layout_dependency": "markdown-representable",
        "semantic_visuals": [],
        "ignored_elements": [],
        "reader_image_policy": {
            "action": "omit",
            "reason": "text-fully-represented-in-markdown",
            "confidence": "high",
        },
        "relationships": [],
        "tables_or_charts": [],
        "unknown_forms": [],
        "uncertainties": ["待人工确认"],
    }


def test_canonical_result_applies_review_without_mutating_raw(tmp_path: Path):
    image = tmp_path / "page-002.png"
    image.write_bytes(b"image")
    raw = raw_fallback(2)
    snapshot = json.loads(json.dumps(raw, ensure_ascii=False))

    result = importer.canonical_result(
        raw=raw,
        raw_path=Path("evidence/fallback-online-vision-p002.json"),
        page=2,
        image=image,
        source_fingerprint="a" * 64,
        review_item={
            "page": 2,
            "corrected_markdown": "# 正确标题\n\n正确正文",
            "remaining_uncertainties": [],
            "remaining_unknown_forms": [],
        },
    )

    assert raw == snapshot
    assert result["text"]["markdown"] == "# 正确标题\n\n正确正文"
    assert result["visual"]["visual_analysis"]["uncertainties"] == []
    assert result["transport"] == "current-conversation"
    assert result["fallback_reason"] == "mac96-unreachable"
    assert result["image_sha256"] == digest(image)


def test_import_pages_preserves_raw_and_archives_stale_projection(tmp_path: Path, monkeypatch):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    (package / "evidence").mkdir()
    (package / "page-units").mkdir()
    image = package / "assets/page-002.png"
    image.write_bytes(b"image")
    (package / "evidence/source-fingerprint.json").write_text(
        json.dumps({"sha256": digest(source)}), encoding="utf-8"
    )
    (package / "evidence/source-inventory.json").write_text(json.dumps({
        "source_fingerprint": digest(source),
        "page_count_or_dimensions": {"page_count": 2},
    }), encoding="utf-8")
    raw_path = package / "evidence/fallback-online-vision-p002.json"
    raw = raw_fallback(2)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    (package / "evidence/vision-page-analysis-p002.json").write_text(
        json.dumps({"status": "partial"}), encoding="utf-8"
    )
    (package / "page-units/p002.md").write_text("旧投影\n", encoding="utf-8")
    review = package / "reviews/online.json"
    review.parent.mkdir()
    review.write_text(json.dumps({
        "status": "passed",
        "manual_visual_review": "passed",
        "pages": [2],
        "items": [{
            "page": 2,
            "corrected_markdown": "# 标题\n\n正文",
            "remaining_uncertainties": [],
            "remaining_unknown_forms": [],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(importer, "pdf_page_count", lambda _source: 2)
    monkeypatch.setattr(importer, "validate_resume_stage_gate", lambda *_args: {"scope": "single-page"})

    importer.import_pages(argparse.Namespace(
        source=source,
        package=package,
        pages="2",
        prior_gate=package / "reviews/gate.json",
        review=review,
    ))

    assert json.loads(raw_path.read_text(encoding="utf-8")) == raw
    canonical = json.loads((package / "evidence/vision-page-analysis-p002.json").read_text(encoding="utf-8"))
    transcription = json.loads((package / "evidence/vision-text-transcription-p002.json").read_text(encoding="utf-8"))
    assert canonical["text"]["markdown"] == "# 标题\n\n正文"
    assert transcription["transport"] == "current-conversation"
    assert transcription["fallback_used"] == "mac96-unreachable"
    assert "![" not in (package / "page-units/p002.md").read_text(encoding="utf-8")
    assert list((package / "evidence/attempts").glob("vision-page-analysis-p002-*.json"))
    assert list((package / "evidence/attempts").glob("p002-*.md"))


def test_import_pages_can_use_recorded_fingerprint_when_source_disappears(tmp_path: Path, monkeypatch):
    missing_source = tmp_path / "missing.pdf"
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    (package / "evidence").mkdir()
    (package / "page-units").mkdir()
    (package / "assets/page-002.png").write_bytes(b"image")
    recorded = "b" * 64
    (package / "evidence/source-fingerprint.json").write_text(
        json.dumps({"sha256": recorded}), encoding="utf-8"
    )
    (package / "evidence/source-inventory.json").write_text(json.dumps({
        "source_fingerprint": recorded,
        "page_count_or_dimensions": {"page_count": 135},
    }), encoding="utf-8")
    raw_path = package / "evidence/fallback-online-vision-p002.json"
    raw = raw_fallback(2)
    raw["uncertainties"] = []
    raw["markdown"] = "# 标题\n\n正文"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(importer, "validate_resume_stage_gate", lambda *_args: {"scope": "single-page"})

    importer.import_pages(argparse.Namespace(
        source=missing_source,
        package=package,
        pages="2",
        prior_gate=package / "reviews/gate.json",
        review=None,
    ))

    assert (package / "page-units/p002.md").is_file()
    event = json.loads((package / "evidence/runtime-ledger.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert event["source_live_check"] == "unavailable-used-recorded-fingerprint-and-existing-render"
