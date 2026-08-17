from __future__ import annotations

import json
from pathlib import Path

from adapters.mac96_pdf_vision_resume import existing_output_pages, page_outputs_complete, page_reader_record
from adapters.mac96_pdf_vision_runner import page_unit_is_present, write_page_outputs


def complete_result(page: int) -> dict:
    return {
        "contract": "vision-page-analysis",
        "status": "complete",
        "pass_strategy_used": "dual",
        "model_call_count": 2,
        "text": {
            "status": "complete",
            "markdown": f"## {page} 页面标题\n\n页面正文",
        },
        "visual": {
            "status": "complete",
            "visual_analysis": {
                "unknown_forms": [],
                "uncertainties": [],
            },
        },
        "diagnostics": [],
    }


def test_page_completion_requires_both_json_layers_and_page_markdown(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-001.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")

    result = complete_result(1)
    write_page_outputs(
        target,
        page=1,
        image=image,
        source_fingerprint="a" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+dual",
    )

    assert (target / "evidence/vision-page-analysis-p001.json").is_file()
    assert (target / "evidence/vision-text-transcription-p001.json").is_file()
    assert page_unit_is_present(target, 1)
    assert page_outputs_complete(target, 1)
    assert existing_output_pages(target, 100) == {1}

    (target / "page-units/p001.md").unlink()
    assert not page_outputs_complete(target, 1)


def test_resume_repairs_missing_page_markdown_without_replacing_existing_json(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-007.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")
    evidence = target / "evidence"
    evidence.mkdir()
    result = complete_result(7)
    analysis_path = evidence / "vision-page-analysis-p007.json"
    analysis_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    text_path = evidence / "vision-text-transcription-p007.json"
    text_path.write_text(
        json.dumps(
            {
                "status": "complete",
                "candidate_markdown": result["text"]["markdown"],
                "source_evidence": "evidence/vision-page-analysis-p007.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert not page_outputs_complete(target, 7)
    write_page_outputs(
        target,
        page=7,
        image=image,
        source_fingerprint="b" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+dual",
    )

    assert page_outputs_complete(target, 7)
    assert json.loads(analysis_path.read_text(encoding="utf-8")) == result
    assert "页面正文" in (target / "page-units/p007.md").read_text(encoding="utf-8")


def test_page_projection_preserves_a_valid_existing_markdown_unit(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-002.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")
    custom = target / "page-units/p002.md"
    custom.parent.mkdir()
    custom.write_text(
        "人工确认后的页面 Markdown\n\n![原始页面图](<../assets/page-002.png>)\n",
        encoding="utf-8",
    )

    result = complete_result(2)
    write_page_outputs(
        target,
        page=2,
        image=image,
        source_fingerprint="c" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+dual",
    )

    assert custom.read_text(encoding="utf-8").startswith("人工确认后的页面 Markdown")


def test_resume_manifest_section_body_uses_repaired_page_unit(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-003.png"
    evidence = target / "evidence"
    image.parent.mkdir(parents=True)
    evidence.mkdir()
    image.write_bytes(b"page-image")
    result = complete_result(3)
    (evidence / "vision-page-analysis-p003.json").write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )
    repaired = "## 修复后的页面标题\n\n修复后的页面正文\n\n![原始页面图](<../assets/page-003.png>)\n"
    page_unit = target / "page-units/p003.md"
    page_unit.parent.mkdir()
    page_unit.write_text(repaired, encoding="utf-8")

    section, _trace, _status, _reader_markdown = page_reader_record(target, 3, "package")

    assert section["body"] == repaired.strip()
