"""Regression checks for the production runner boundary.

These tests exercise argument and contract behavior only. They do not convert
fixture or real source files and never create a business output package.
"""
from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "archive/legacy-adapters/workflow_a_runner.py"


def test_runner_page_selector_is_dynamic():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import parse_pages

    assert parse_pages("all", 4) == [1, 2, 3, 4]
    assert parse_pages("1-2,4", 4) == [1, 2, 4]


def test_runner_rejects_multiple_source_flags_without_running_conversion(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--source",
            str(RUNNER),
            "--pptx",
            str(RUNNER),
            "--output-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "exactly one" in result.stderr
    assert not list(tmp_path.iterdir())


def test_daily_trigger_and_result_contract_are_registered():
    registry = yaml.safe_load((ROOT / "workflow-registry.yaml").read_text(encoding="utf-8"))
    entry = next(item for item in registry["workflows"] if item["id"] == "document-to-markdown-assets")
    assert entry["trigger_keywords"] == ["文档转 Markdown"]
    assert entry["daily_trigger"] == "文档转 Markdown"
    assert entry["execution_observability"]["final_report"] == "reports/validation-result.json"
    assert entry["execution_observability"]["statuses"] == ["passed", "needs-review", "failed"]

    workflow = yaml.safe_load(
        (ROOT / "workflows/document-to-markdown-assets/workflow.yaml").read_text(encoding="utf-8")
    )
    prompt_inputs = workflow["entrypoint"]["prompt_inputs"]
    assert prompt_inputs[:3] == ["source", "output_root", "pages"]
    assert set(prompt_inputs[3:]).issubset({"profile", "config"})
    assert len(prompt_inputs) == len(set(prompt_inputs))
    assert workflow["entrypoint"]["trigger_keyword"] == "文档转 Markdown"
    assert workflow["execution_observability"]["final_report"] == "reports/validation-result.json"


def test_fixture_page_numbers_are_not_production_runner_defaults():
    runner_text = RUNNER.read_text(encoding="utf-8")
    assert "pages = [24, 25]" not in runner_text
    assert "page == 24" not in runner_text
    assert "page == 25" not in runner_text
    assert "18个工作思维模型" not in runner_text


def test_independent_validator_distinguishes_missing_package_from_review(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_output_validator import validate_package

    result = validate_package(tmp_path / "missing-package")
    assert result["status"] == "failed"


def test_reader_markdown_separates_inline_theme_from_page_title(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import build_markdown, classify_content

    native = {
        "slides": [{
            "page": 33,
            "width": 7200000,
            "height": 9600000,
            "text_blocks": [{
                "block_id": "p33-o4",
                "object_id": "4",
                "text": "人活着其实就两件事\n搞定自己vs搞定别人",
                "paragraphs": ["人活着其实就两件事\n搞定自己vs搞定别人"],
                "bbox": {"x": 1, "y": 1, "cx": 3, "cy": 2, "unit": "EMU"},
                "order": 1,
            }],
        }],
        "page_dimensions": {"cx": 7200000, "cy": 9600000},
    }
    classification = classify_content(native)
    assert classification["pages"][0]["page_title"]["text"] == "人活着其实就两件事"
    assert classification["pages"][0]["theme_or_heading"]["text"] == "搞定自己 vs 搞定别人"
    output = tmp_path / "package"
    (output / "assets/pages").mkdir(parents=True)
    build_markdown(output, Path("21个成长思维模型.pptx"), "pptx", [33], {}, native, classification, {"reviews": []}, {"status": "complete"})
    text = (output / "index.md").read_text()
    assert "## 人活着其实就两件事" in text
    assert "### 主题与对比" in text
    assert "搞定自己 vs 搞定别人" in text
    for forbidden in ("标题下正文文字块", "视觉关系", "来源与不确定性", "A4 状态", "native_bbox", "object_id"):
        assert forbidden not in text


def test_render_fontconfig_records_cjk_aliases(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import write_render_fontconfig

    path = write_render_fontconfig(tmp_path)
    text = path.read_text()
    assert "微软雅黑" in text
    assert "PingFang SC" in text


def test_progress_event_contains_page_and_elapsed_fields(tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import append_progress_event

    path = tmp_path / "evidence" / "progress.jsonl"
    append_progress_event(
        path,
        run_id="run-1",
        source_fingerprint="a" * 64,
        stage="source_rendering",
        status="complete",
        pages=[3],
        next_action="继续提取",
        elapsed_seconds=1.25,
    )
    event = json.loads(path.read_text(encoding="utf-8").strip())
    assert event["page"] == 3
    assert event["attempt"] == 1
    assert event["elapsed_seconds"] == 1.25
    assert "source_rendering" in capsys.readouterr().err


def test_execution_preflight_blocks_unsupported_source_before_conversion(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_preflight import run_preflight

    source = tmp_path / "input.docx"
    source.write_bytes(b"not-a-supported-production-input")
    report = run_preflight(source, tmp_path / "out", "1", total_pages=1)
    assert report["can_start"] is False
    assert report["status"] == "failed"
    assert any(item["check_id"] == "source-format-supported" for item in report["failures"])


def test_execution_preflight_accepts_image_and_keeps_page_scope(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_preflight import run_preflight

    source = tmp_path / "input.png"
    from PIL import Image

    Image.new("RGB", (2, 2), "white").save(source)
    report = run_preflight(source, tmp_path / "out", "1", total_pages=1)
    assert report["can_start"] is True
    assert report["selected_pages"] == [1]
    assert report["source"]["sha256"]
    assert {item["check_id"] for item in report["checks"]} >= {"source-readable", "page-scope-valid", "output-collision", "external-route-policy"}


def test_execution_preflight_emits_risk_mapping_metadata(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_preflight import run_preflight
    from PIL import Image

    source = tmp_path / "input.png"
    Image.new("RGB", (2, 2), "white").save(source)
    report = run_preflight(source, tmp_path / "out", "1", total_pages=1)

    assert report["audit_metadata"]["evidence_boundary"] == "preflight-only"
    assert report["audit_metadata"]["execution_boundary"] == "before-A1"
    assert all(item["risk_ids"] is not None for item in report["checks"])
    assert all(item["detectability"] for item in report["checks"])
    assert all(item["evidence_class"] for item in report["checks"])
    assert all(item["next_action"] for item in report["checks"])


def test_execution_preflight_uses_actual_source_page_ids_when_available():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_preflight import _parse_selector

    selected, errors = _parse_selector("24-25", total_pages=2, available_pages=[24, 25])
    assert selected == [24, 25]
    assert errors == []

    _, errors = _parse_selector("1", total_pages=2, available_pages=[24, 25])
    assert errors == ["selected pages are absent from source page set: [1]"]


def test_execution_preflight_blocks_existing_package_without_overwrite(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_preflight import run_preflight

    source = tmp_path / "input.png"
    from PIL import Image

    Image.new("RGB", (2, 2), "white").save(source)
    output_root = tmp_path / "out"
    output_root.mkdir()
    package_name = "input-package"
    (output_root / package_name).mkdir()
    report = run_preflight(source, output_root, "1", total_pages=1, package_name=package_name)
    assert report["can_start"] is False
    assert any(item["check_id"] == "output-collision" for item in report["failures"])


def test_execution_preflight_rejects_stale_candidate_fingerprint(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_preflight import run_preflight
    from PIL import Image

    source = tmp_path / "preferred.png"
    other = tmp_path / "candidate.png"
    Image.new("RGB", (2, 2), "white").save(source)
    Image.new("RGB", (2, 2), "black").save(other)
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "preferred_source": str(source),
                "selected_pages": [1],
                "candidates": [
                    {"path": str(source), "sha256": "0" * 64},
                    {"path": str(other), "sha256": "0" * 64},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_preflight(source, tmp_path / "out", "1", total_pages=1, candidate_comparison=comparison)
    assert report["can_start"] is False
    assert any(item["check_id"] == "candidate-fingerprint" for item in report["failures"])


def test_schema_valid_but_semantically_empty_mac96_is_unusable():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import mac96_semantic_usable

    assert not mac96_semantic_usable({"title": {"text": ""}, "subtitle": {"text": ""}, "semantic_sections": [], "relations": []}, {"text_blocks": []})
    assert mac96_semantic_usable({"title": {"text": "机会成本模型"}, "subtitle": {"text": ""}, "semantic_sections": []}, {"text_blocks": []})


def test_text_structure_normalization_never_injects_known_fixture_content():
    """A different source must not inherit page-specific fixture semantics."""
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import _normalise_text_structure

    native_input = {
        "page": 3,
        "ordered_text_blocks": [
            {"block_id": "p3-b1", "text": "完全不同的来源标题", "bbox": {"x": 0, "y": 0, "cx": 1, "cy": 1}}
        ],
    }
    result = _normalise_text_structure(3, {}, native_input)
    assert result["title"]["text"] == ""
    assert result["subtitle"]["text"] == ""
    assert result["semantic_sections"] == []
    assert result["relations"] == []
    runner_text = RUNNER.read_text(encoding="utf-8")
    for leaked_fixture_text in ("四象限时间管理法则", "一看就会的GRAI复盘法", "秒杀同龄人的蕞高效的 4步复盘方法"):
        assert leaked_fixture_text not in runner_text


def test_ocr_candidates_become_reviewable_markdown_text(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import build_markdown, classify_content

    native = {"pages": [{"page": 1, "width": 100, "height": 100, "text_blocks": [], "relations": []}], "page_dimensions": {}}
    ocr = {"engine": "Apple Vision", "candidates": [{"page": 1, "status": "complete", "candidate_count": 2, "candidates": [{"text": "01 机会成本模型", "confidence": 0.5, "bbox": {"y": 0.8}}, {"text": "机会成本是放弃选项的最大价值。", "confidence": 0.9, "bbox": {"y": 0.7}}]}]}
    classification = classify_content(native, {}, ocr, {})
    classification_text = json.dumps(classification, ensure_ascii=False)
    assert '"confidence": 0.5' not in classification_text
    assert '"candidate_index"' not in classification_text
    assert '"engine": "Apple Vision"' not in classification_text
    output = tmp_path / "package"
    (output / "assets/pages").mkdir(parents=True)
    image = output / "assets/pages/opportunity-cost-model-p001.png"
    from PIL import Image
    Image.new("RGB", (2, 2), "white").save(image)
    build_markdown(output, Path("source.pdf"), "pdf", [1], {1: image}, native, classification, {"reviews": []}, {"status": "needs-review"}, ocr)
    text = (output / "index.md").read_text(encoding="utf-8")
    assert "## 01 机会成本模型" in text
    assert "机会成本是放弃选项的最大价值。" in text
    assert "### 文字候选" in text
    assert "尚未完成逐字核验；请结合页面图复核" in text
    assert "OCR 待复核候选" not in text
    assert "置信度：" not in text
    assert "Apple Vision" not in text
    assert "候选置信度与逐条原始证据保留在运行证据中" in text


def test_ocr_reader_merges_nearby_lines_and_normalises_safe_glyphs():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import merge_ocr_body_candidates

    merged = merge_ocr_body_candidates([
        {"text": "机会成本是为了某种东西而所要放弃另一些东西的蕞大价值", "bbox": {"x": 0.07, "y": 0.76}},
        {"text": "也可以理解为在面临多方案择一决策时", "bbox": {"x": 0.07, "y": 0.74}},
        {"text": "A", "bbox": {"x": 0.30, "y": 0.59}},
    ])
    assert len(merged) == 2
    assert merged[0]["text"].startswith("机会成本是为了某种东西而所要放弃另一些东西的最大价值")
    assert merged[0]["_reader_group"] == "paragraph"
    assert merged[1]["text"] == "A"


def test_ocr_title_selector_rejects_layout_headers_and_body_sentences():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import select_ocr_title, title_quality_flags

    candidates = [
        {"text": "每天学一个思维模型", "confidence": 1, "bbox": {"y": 0.888}},
        {"text": "模型结构", "confidence": 1, "bbox": {"y": 0.705}},
        {"text": "博弈论是研究具有斗争或竞争性质现象的数学理论和方法。", "confidence": 1, "bbox": {"y": 0.712}},
        {"text": "90.博弈论思维模型", "confidence": 1, "bbox": {"y": 0.824}},
    ]
    selected = select_ocr_title(candidates)
    assert selected["text"] == "90.博弈论思维模型"
    assert title_quality_flags(candidates[0]) == ["generic-header", "outside-title-band"]
    assert "sentence-like-title" in title_quality_flags(candidates[2])


def test_ocr_title_selector_supports_analysis_method_titles():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import select_ocr_title

    selected = select_ocr_title([
        {"text": "每天学一个思维模型", "confidence": 1, "bbox": {"y": 0.888}},
        {"text": "5W1H分析法", "confidence": 1, "bbox": {"y": 0.823}},
    ])
    assert selected["text"] == "5W1H分析法"


def test_ocr_title_selector_uses_supported_phrase_when_title_glyph_drops_character():
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import select_ocr_title

    selected = select_ocr_title([
        {"text": "福格行 模型", "confidence": 0.5, "bbox": {"y": 0.822}},
        {"text": "福格行为模型以BJFogg命名，表明一个行为得以发生", "confidence": 1, "bbox": {"y": 0.789}},
    ])
    assert selected["text"] == "福格行为模型"


def test_smoke_gate_rejects_relation_only_page(tmp_path):
    sys.path.insert(0, str(ROOT / "adapters"))
    from workflow_a_legacy_loader import evaluate_smoke_pages

    output = tmp_path / "package"
    (output / "assets/pages").mkdir(parents=True)
    image = output / "assets/pages/opportunity-cost-model-p001.png"
    image.write_bytes(b"image")
    (output / "index.md").write_text("## 第 1 页：待复核：第 1 页\n\n### 关系说明\n", encoding="utf-8")
    classification = {"pages": [{"page": 1, "page_title": {"text": "待复核：第 1 页", "source": "missing"}, "classified_blocks": [], "text_evidence": {"ocr_status": "unavailable", "ocr_candidate_count": 0}}]}
    visual = {"reviews": [{"page": 1, "result": {"relations": [{"from": "A", "to": "B"}]}}]}
    report = evaluate_smoke_pages(output, [1], {1: image}, classification, visual, {"candidates": []})
    assert report["status"] == "failed"
    assert {failure["gate"] for failure in report["failures"]} >= {"title", "text_or_ocr", "not_relation_only"}
