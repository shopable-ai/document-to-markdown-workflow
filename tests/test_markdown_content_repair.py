from __future__ import annotations

import json
from pathlib import Path

from adapters import markdown_content_repair as repair


def page_markdown(page: int = 1) -> str:
    return (
        f"<!-- page-number: {page} -->\n"
        "<!-- unit-status: needs-review -->\n"
        "<!-- relation-status: needs-review -->\n\n"
        "## 页面标题\n\n"
        "一段  文字。\n\n"
        "## 待核对项\n\n"
        "- 图示关系：Unknown/needs-review。\n\n"
        f"![原始页面图](<../assets/page-{page:03d}.png>)\n"
    )


def test_repair_contract_declares_lan_first_route():
    import yaml

    contract = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "contracts/markdown-content-repair.yaml").read_text(encoding="utf-8")
    )
    assert contract["version"] == "0.4.0"
    assert contract["route"]["order"][0]["id"] == "lan-language-model"
    assert contract["route"]["order"][1]["status"] == "fallback-only"
    assert contract["route"]["success_policy"]["lan_success_stops_fallback"] is True


def test_guard_preserves_page_metadata_image_and_uncertain_relation():
    original = page_markdown()
    repaired = original.replace("一段  文字。", "一段文字。")
    assert repair.validate_repaired_markdown(original, repaired, page=1) == []
    assert repair.validate_repaired_markdown(original, repaired.replace("Unknown/needs-review", "已确认关系"), page=1)


def test_guard_accepts_text_only_page_without_image_link():
    original = page_markdown().replace("\n![原始页面图](<../assets/page-001.png>)\n", "\n")
    repaired = original.replace("一段  文字。", "一段文字。")

    assert repair.validate_repaired_markdown(original, repaired, page=1) == []


def test_guard_rejects_reader_content_invented_by_model():
    original = page_markdown()
    repaired = original.replace("一段  文字。", "这是新增说明。一段文字。")

    errors = repair.validate_repaired_markdown(original, repaired, page=1)

    assert any("introduced reader content" in error for error in errors)


def test_strip_fence_removes_full_document_yaml_envelope():
    original = page_markdown()

    assert repair._strip_fence(f"---\n{original}\n---") == original.strip()


def test_guard_rejects_unstripped_full_document_yaml_envelope():
    original = page_markdown()

    errors = repair.validate_repaired_markdown(original, f"---\n{original}\n---", page=1)

    assert "entire page was wrapped in YAML delimiters" in errors


def test_lan_success_writes_page_unit_and_records_hashes(monkeypatch, tmp_path: Path):
    page_unit = tmp_path / "page-units/p001.md"
    page_unit.parent.mkdir()
    original = page_markdown()
    page_unit.write_text(original, encoding="utf-8")
    repaired = original.replace("一段  文字。", "一段文字。")
    calls = []

    def fake_request(page_text, **kwargs):
        calls.append((page_text, kwargs))
        return "complete", repaired

    monkeypatch.setattr(repair, "_request", fake_request)
    result = repair.repair_page(page_unit, page=1)

    assert result["status"] == "applied"
    assert result["model_status"] == "complete"
    assert result["next_action"] == "局域网修复成功；不再调用在线模型"
    assert result["input_sha256"] != result["output_sha256"]
    assert result["final_sha256"] == result["output_sha256"]
    assert len(calls) == 1
    assert "页面标题" in calls[0][0]
    assert "page-002" not in calls[0][0]
    assert page_unit.read_text(encoding="utf-8") == repaired.rstrip() + "\n"


def test_lan_failure_keeps_original_page_unit(monkeypatch, tmp_path: Path):
    page_unit = tmp_path / "page-units/p002.md"
    page_unit.parent.mkdir()
    original = page_markdown(2)
    page_unit.write_text(original, encoding="utf-8")
    monkeypatch.setattr(repair, "_request", lambda *args, **kwargs: ("unavailable", "ssh unavailable"))

    result = repair.repair_page(page_unit, page=2)

    assert result["status"] == "deferred"
    assert result["write_back"] is False
    assert "ssh unavailable" in result["diagnostics"]
    assert page_unit.read_text(encoding="utf-8") == original


def test_adapter_does_not_expose_credentials_in_evidence(tmp_path: Path):
    page_unit = tmp_path / "p001.md"
    page_unit.write_text(page_markdown(), encoding="utf-8")
    evidence = {
        "contract": "markdown-content-repair",
        "model": repair.DEFAULT_MODEL,
        "transport": "ssh",
        "target": "mac96",
    }
    serialized = json.dumps(evidence, ensure_ascii=False)
    assert all(secret not in serialized for secret in ("api_key", "authorization", "cookie", "subscription_url"))
