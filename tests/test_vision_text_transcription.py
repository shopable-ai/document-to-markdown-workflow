from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def analysis_module():
    sys.path.insert(0, str(ROOT / "adapters"))
    import mac96_vision_page_analysis

    return mac96_vision_page_analysis


def test_unified_contract_defaults_to_one_full_pass_and_keeps_ocr_text_only():
    contract = load_yaml(ROOT / "contracts/vision-page-analysis.yaml")

    assert contract["input"]["default_mode"] == "full"
    assert contract["input"]["default_pass_strategy"] == "single"
    assert contract["policy"]["single_pass_default"] == "full-mode-one-request"
    assert contract["policy"]["dual_pass_order"] == ["text", "layout"]
    assert contract["transport"]["conversation_fallback"]["target"] == "current-conversation"
    assert contract["transport"]["conversation_fallback"]["adapter"] == "none-direct-current-vision-model"
    assert contract["transport"]["text_fallback"]["target"] == "apple-vision-ocr"
    assert contract["transport"]["text_fallback"]["does_not_provide"] == "visual-analysis"
    assert contract["projections"]["markdown"]["workflow_output"] == "index.md"
    assert contract["projections"]["visual"]["workflow_output"] == "evidence/vision-page-analysis.json"
    assert contract["policy"]["fallback_order"] == [
        "mac96-vision-page-analysis",
        "conversation-online-vision-page-analysis",
        "apple-vision-ocr",
    ]
    assert contract["prompt"]["version"] == "vision-page-analysis-v4"


def test_text_prompt_preserves_single_image_markdown_transcription_boundaries():
    skill = (ROOT / "skills/vision-page-analysis/SKILL.md").read_text(encoding="utf-8")
    prompt = (ROOT / "references/prompts/vision-page-analysis-mac96.zh.txt").read_text(encoding="utf-8")

    for required in (
        "请只根据当前上传的这一张图片提取文字内容。",
        "直接使用当前视觉模型阅读图片，不使用 OCR。",
        "不读取其他文件，不参考上一张或下一张图片。",
        "只提取图片中实际出现的文字，不补写、不解释、不总结、不改写原意。",
        "不要新增“定义”“选择关系”“解读”“总结”等图片中没有的标题。",
        "文字无法确认时标记为“〔无法确认〕”，不要自行猜测。",
    ):
        assert required in skill
        assert required in prompt
    assert "`text` 模式只输出整理后的 Markdown" in prompt
    assert "`full` 模式只把整理后的 Markdown 放入 `markdown` 字段" in prompt


def test_workflow_registers_one_active_page_analysis_skill_and_compatibility_aliases():
    registry = load_yaml(ROOT / "skill-registry.yaml")
    workflow = load_yaml(ROOT / "workflows/document-to-markdown-assets/workflow.yaml")
    a4 = next(stage for stage in workflow["stages"] if stage["id"] == "A4")
    aliases = {
        item["id"]: item
        for item in registry["skills"]
        if item.get("kind") == "compatibility-alias"
    }

    assert a4["skills"] == [
        "vision-page-analysis",
        "local-ocr",
        "text-source-reconciliation",
        "layout-relation-reconciliation",
    ]
    assert aliases["vision-text-transcription"]["alias_of"] == "vision-page-analysis"
    assert aliases["mac96-visual-review"]["alias_of"] == "vision-page-analysis"
    assert workflow["execution_policy"]["vision_page_analysis"]["default_mode"] == "full"
    assert workflow["execution_policy"]["vision_page_analysis"]["default_pass_strategy"] == "single"
    assert workflow["execution_policy"]["vision_page_analysis"]["visual_fallback"] == "failed"
    assert workflow["execution_policy"]["online_visual_model"]["current_conversation"]["policy"] == "fallback-after-mac96-only"


def test_compatibility_skill_documents_current_conversation_fallback():
    skill = (ROOT / "skills/vision-text-transcription/SKILL.md").read_text(encoding="utf-8")

    assert "SSH mac96 → 当前对话在线视觉模型 → Apple Vision OCR" in skill
    assert "不创建" in skill
    assert "不读取其他图片、文件、OCR JSON、bbox 或前后页面" in skill


def test_full_single_pass_returns_markdown_and_visual_analysis_in_one_request(monkeypatch, tmp_path: Path):
    adapter = analysis_module()
    image = tmp_path / "page.png"
    image.write_bytes(b"single-image")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("只读当前图片，不使用 OCR。", encoding="utf-8")
    captured = {}

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "response": json.dumps(
                    {
                        "page": "page-1",
                        "markdown": "## 标题\n\n正文",
                        "visual_analysis": {
                            "relationships": [{"from": "A", "to": "B", "label": "连接"}],
                            "coverage_notes": "已检查整页并完成开放集合扫描",
                            "unknown_forms": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                "done": True,
            },
            ensure_ascii=False,
        ).encode()
        stderr = b""

    def fake_run(command, *, input, capture_output, timeout, check):
        captured["command"] = command
        captured["request"] = json.loads(input.decode("utf-8"))
        captured["timeout"] = timeout
        return Result()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.analyze(
        image,
        asset_id="asset-1",
        source_fingerprint="f" * 64,
        page_or_region="page-1",
        prompt_file=prompt,
    )

    assert result["status"] == "complete"
    assert result["mode_requested"] == "full"
    assert result["pass_strategy_used"] == "single"
    assert result["model_call_count"] == 1
    assert result["text"]["markdown"] == "## 标题\n\n正文"
    assert result["visual"]["visual_analysis"]["relationships"][0]["label"] == "连接"
    assert captured["command"][:7] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "mac96", "/usr/bin/curl"]
    assert captured["request"]["images"] == [base64.b64encode(b"single-image").decode("ascii")]
    assert len(captured["request"]["images"]) == 1
    assert "不使用 OCR" in captured["request"]["prompt"]


def test_full_dual_pass_uses_two_specialized_requests_only_when_requested(monkeypatch, tmp_path: Path):
    adapter = analysis_module()
    image = tmp_path / "page.png"
    image.write_bytes(b"dual-image")
    responses = [
        {"response": "## 标题\n\n正文", "done": True},
        {
            "response": json.dumps(
                {
                    "relationships": [{"from": "左", "to": "右", "label": "比较"}],
                    "coverage_notes": "已完成结构扫描",
                },
                ensure_ascii=False,
            ),
            "done": True,
        },
    ]
    calls = []

    class Result:
        returncode = 0
        stderr = b""

        def __init__(self, payload):
            self.stdout = json.dumps(payload, ensure_ascii=False).encode()

    def fake_run(command, *, input, capture_output, timeout, check):
        calls.append(json.loads(input.decode("utf-8")))
        return Result(responses.pop(0))

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.analyze(
        image,
        asset_id="asset-2",
        source_fingerprint="e" * 64,
        page_or_region="page-2",
        mode="full",
        pass_strategy="dual",
    )

    assert result["status"] == "complete"
    assert result["pass_strategy_used"] == "dual"
    assert result["model_call_count"] == 2
    assert [item["pass"] for item in result["requests"]] == ["text", "layout"]
    assert result["text"]["markdown"] == "## 标题\n\n正文"
    assert result["visual"]["visual_analysis"]["relationships"][0]["label"] == "比较"
    assert len(calls) == 2
    assert len(calls[0]["images"]) == len(calls[1]["images"]) == 1


def test_unavailable_visual_request_does_not_claim_visual_completion(monkeypatch, tmp_path: Path):
    adapter = analysis_module()
    image = tmp_path / "page.png"
    image.write_bytes(b"unavailable-image")

    class Result:
        returncode = 255
        stdout = b""
        stderr = b"ssh: connect failed"

    monkeypatch.setattr(adapter.subprocess, "run", lambda *args, **kwargs: Result())
    result = adapter.analyze(
        image,
        asset_id="asset-3",
        source_fingerprint="d" * 64,
        page_or_region="page-3",
        mode="full",
    )

    assert result["status"] == "unavailable"
    assert result["text"]["fallback"] == "conversation-online-vision-page-analysis"
    assert result["visual"]["status"] == "unavailable"
    assert result["visual"]["source"] == "mac96"
    assert "visual" in json.dumps(result, ensure_ascii=False)


def test_legacy_transcription_wrapper_reuses_unified_adapter(monkeypatch, tmp_path: Path):
    sys.path.insert(0, str(ROOT / "adapters"))
    import mac96_vision_text_transcription
    import mac96_vision_page_analysis

    image = tmp_path / "page.png"
    image.write_bytes(b"legacy-image")

    class Result:
        returncode = 0
        stdout = json.dumps({"response": "## 兼容标题\n\n正文", "done": True}).encode()
        stderr = b""

    monkeypatch.setattr(mac96_vision_page_analysis.subprocess, "run", lambda *args, **kwargs: Result())
    result = mac96_vision_text_transcription.transcribe(
        image,
        asset_id="asset-legacy",
        source_fingerprint="a" * 64,
        page_or_region="page-legacy",
    )

    assert result["status"] == "complete"
    assert result["candidate_markdown"] == "## 兼容标题\n\n正文"
    assert result["canonical_evidence"] == "vision-page-analysis"
