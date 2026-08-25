from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters import markdown_post_merge_formatter as formatter
from adapters.markdown_model_router import ModelRouteError, ModelRunResult


def test_format_markdown_text_preserves_images_and_page_markers():
    original = (
        "<!-- page:1 -->\n\n"
        "第一行\n"
        "第二行\n\n"
        "![图片](assets/page-001.png)\n\n"
        "<!-- page:2 -->\n\n"
        "第三行\n"
        "第四行\n"
    )

    formatted, stats = formatter.format_markdown_text(original)

    assert stats["changed"] is True
    assert "<!-- page:1 -->" in formatted
    assert "<!-- page:2 -->" in formatted
    assert "![图片](assets/page-001.png)" in formatted
    assert "第一行第二行" in formatted
    assert "第三行第四行" in formatted


def test_format_markdown_text_keeps_lists_and_headings_separate():
    original = (
        "# 标题\n\n"
        "- 条目一\n"
        "- 条目二\n\n"
        "正文第一行\n"
        "正文第二行\n"
    )

    formatted, _stats = formatter.format_markdown_text(original)

    assert "# 标题" in formatted
    assert "- 条目一\n- 条目二" in formatted
    assert "正文第一行正文第二行" in formatted


def test_split_model_chunks_keeps_page_units_whole():
    markdown = (
        "文档标题\n\n<!-- page:1 -->\n\n第一页正文\n\n"
        "<!-- page:2 -->\n\n第二页正文\n\n"
        "<!-- page:3 -->\n\n第三页正文\n"
    )

    chunks = formatter._split_model_chunks(markdown, max_chars=55)

    assert "文档标题" in chunks[0]
    assert "<!-- page:1 -->" in chunks[0]
    assert "<!-- page:2 -->" in "".join(chunks)
    assert "<!-- page:3 -->" in "".join(chunks)
    assert len(chunks) >= 2


def test_runtime_prompt_preserves_full_prompt_as_governance_source():
    runtime_prompt = formatter.RUNTIME_PROMPT_PATH.read_text(encoding="utf-8")
    governance_prompt = formatter.WORKFLOW_PROMPT_PATH.read_text(encoding="utf-8")

    assert len(runtime_prompt) < len(governance_prompt)
    assert "<!-- page:N -->" in runtime_prompt
    assert "图片路径和顺序不得改变" in runtime_prompt


def test_general_repair_policy_is_a_registered_skill_not_a_temporary_prompt():
    skill_policy = formatter.SKILL_POLICY_PATH.read_text(encoding="utf-8")

    assert formatter.SKILL_POLICY_PATH == (
        formatter.ROOT / "skills" / "markdown-format-repair" / "SKILL.md"
    )
    assert "name: markdown-format-repair" in skill_policy
    assert not (
        formatter.ROOT / ".prompt" / "Markdown文档整理与修复通用提示词.md"
    ).exists()


def test_format_run_root_applies_model_result_and_reports_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run_root = tmp_path / "run"
    reports = run_root / "reports"
    reports.mkdir(parents=True)
    source = run_root / "document-temp.md"
    source.write_text("<!-- page:1 -->\n\n原始\n文字\n", encoding="utf-8")
    config = tmp_path / "routes.toml"
    config.write_text(
        'version=1\ndefault_route_order=["test"]\n[routes.test]\ntransport="codex-cli"\n',
        encoding="utf-8",
    )

    def fake_routes(**kwargs: object) -> ModelRunResult:
        return ModelRunResult(
            status="passed",
            markdown="<!-- page:1 -->\n\n原始文字\n",
            route_id="test",
            transport="codex-cli",
            model="model-a",
            profile="official",
            attempts=({"route": "test", "status": "passed"},),
        )

    monkeypatch.setattr(formatter, "run_model_routes", fake_routes)

    report = formatter.format_run_root(
        run_root=run_root,
        source=source,
        mode="model-required",
        config_path=config,
        dotenv_path=tmp_path / "missing.env",
    )

    assert (run_root / "index.md").read_text(encoding="utf-8") == "<!-- page:1 -->\n\n原始文字\n"
    assert report["model_applied"] is True
    assert report["selected_routes"] == ["test"]
    assert report["method"] == "model-plus-deterministic-normalization"


def test_model_output_still_receives_deterministic_baseline_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run_root = tmp_path / "run"
    (run_root / "reports").mkdir(parents=True)
    source = run_root / "document-temp.md"
    source.write_text(
        "<!-- page:1 -->\n\n第一行\n第二行\n\n![图](assets/page-001.png)\n",
        encoding="utf-8",
    )
    config = tmp_path / "routes.toml"
    config.write_text(
        'version=1\ndefault_route_order=["test"]\n[routes.test]\ntransport="codex-cli"\n',
        encoding="utf-8",
    )

    def unchanged_model(**kwargs: object) -> ModelRunResult:
        return ModelRunResult(
            status="passed",
            markdown=str(kwargs["markdown"]),
            route_id="test",
            transport="codex-cli",
            model="model-a",
            profile=None,
            attempts=({"route": "test", "status": "passed"},),
        )

    monkeypatch.setattr(formatter, "run_model_routes", unchanged_model)

    report = formatter.format_run_root(
        run_root=run_root,
        source=source,
        mode="model-required",
        config_path=config,
        dotenv_path=tmp_path / "missing.env",
    )

    index_text = (run_root / "index.md").read_text(encoding="utf-8")
    assert "第一行第二行" in index_text
    assert "<!-- page:1 -->" in index_text
    assert "![图](assets/page-001.png)" in index_text
    assert report["method"] == "model-plus-deterministic-normalization"


def test_model_optional_falls_back_to_deterministic_on_configuration_error(
    tmp_path: Path,
):
    run_root = tmp_path / "run"
    (run_root / "reports").mkdir(parents=True)
    source = run_root / "document-temp.md"
    source.write_text("第一行\n第二行\n", encoding="utf-8")

    report = formatter.format_run_root(
        run_root=run_root,
        source=source,
        mode="model-optional",
        config_path=tmp_path / "missing.toml",
        dotenv_path=tmp_path / "missing.env",
    )

    assert (run_root / "index.md").read_text(encoding="utf-8") == "第一行第二行\n"
    assert report["method"] == "deterministic-fallback"
    assert report["model_applied"] is False
    assert "configuration_error" in report


def test_model_required_failure_does_not_replace_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run_root = tmp_path / "run"
    (run_root / "reports").mkdir(parents=True)
    source = run_root / "document-temp.md"
    destination = run_root / "index.md"
    source.write_text("<!-- page:1 -->\n\n正文\n", encoding="utf-8")
    destination.write_text("已有终稿\n", encoding="utf-8")
    config = tmp_path / "routes.toml"
    config.write_text(
        'version=1\ndefault_route_order=["test"]\n[routes.test]\ntransport="codex-cli"\n',
        encoding="utf-8",
    )

    def failed_routes(**kwargs: object) -> ModelRunResult:
        return ModelRunResult(
            status="failed",
            markdown=None,
            route_id=None,
            transport=None,
            model=None,
            profile=None,
            attempts=({"route": "test", "status": "failed"},),
        )

    monkeypatch.setattr(formatter, "run_model_routes", failed_routes)

    with pytest.raises(ModelRouteError):
        formatter.format_run_root(
            run_root=run_root,
            source=source,
            destination=destination,
            mode="model-required",
            config_path=config,
            dotenv_path=tmp_path / "missing.env",
        )

    assert destination.read_text(encoding="utf-8") == "已有终稿\n"
    failure_report = json.loads(
        (run_root / "reports" / "markdown-post-merge-formatting.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure_report["status"] == "failed"
    assert failure_report["method"] == "failed"
    assert failure_report["model_attempts"] == [
        {"route": "test", "status": "failed", "chunk": 1, "chunk_count": 1}
    ]
