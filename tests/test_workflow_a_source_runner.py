"""Focused contract tests for the source-independent Workflow A runner.

These tests intentionally keep fixture conversion outputs under pytest's
temporary directory.  They do not use fixture expected Markdown or historical
``tests/runs`` output as conversion evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "adapters" / "workflow_a_source_runner.py"
PROFILE_PATH = ROOT / "references" / "profiles" / "source-runner-generic.yaml"
FIXTURE_SELECTED = ROOT / "tests" / "fixtures" / "18个工作思维模型-last-2-slides" / "selected"


def _runner_module():
    sys.path.insert(0, str(ROOT / "adapters"))
    import workflow_a_source_runner

    return workflow_a_source_runner


def _run(source: Path, output_root: Path, pages: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--source",
            str(source),
            "--output-root",
            str(output_root),
            "--pages",
            pages,
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout.strip(), result.stderr
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # Keep this helper tolerant of a short human-readable preamble while
        # still requiring the final runner result to be machine-readable.
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1])


def _fixture(name: str) -> Path:
    path = FIXTURE_SELECTED / name
    assert path.is_file(), path
    return path


def test_active_runner_is_a_single_deterministic_entrypoint():
    source = RUNNER.read_text(encoding="utf-8")

    assert "workflow_a_optimized_runner" not in source
    assert "archive/legacy-adapters" not in source
    assert "from workflow_a_optimized_runner" not in source
    assert "from .workflow_a_optimized_runner" not in source


def test_execute_does_not_create_secondary_optimized_repair_outputs(tmp_path: Path):
    source = _fixture("18个工作思维模型-第24-25页.pdf")
    result = _run(source, tmp_path / "execute-output", "1", "--execute")
    payload = _payload(result)

    assert result.returncode == 1, result.stderr
    assert payload["status"] == "needs-review"
    assert payload["validation"]["status"] == "not-run"
    assert payload["validation"]["passed"] is False
    package = Path(payload["package_path"])
    assert (package / "index.md").is_file()
    assert (package / "assets").is_dir()
    assert not any(path.suffix.lower() in {".json", ".yaml", ".yml"} for path in package.rglob("*"))
    assert not (package / "evidence").exists()
    assert not (package / "reports").exists()


@pytest.mark.parametrize(
    ("suffix", "source_type"),
    [
        (".ppt", "ppt"),
        (".pptx", "pptx"),
        (".pdf", "pdf"),
        (".png", "image"),
        (".jpg", "image"),
        (".jpeg", "image"),
    ],
)
def test_generic_profile_routes_supported_source_suffixes(suffix: str, source_type: str):
    runner = _runner_module()
    profile = runner.load_yaml(PROFILE_PATH)

    selected = runner.source_type_for(profile, suffix)

    assert selected["id"] == source_type
    assert selected["native_extractors"]
    assert selected["page_asset_strategy"]


def test_page_selector_is_sorted_deduplicated_and_does_not_infer_source_pages():
    runner = _runner_module()

    assert runner.parse_pages("1-2,2,4") == [1, 2, 4]
    assert runner.parse_pages("all") is None
    with pytest.raises(ValueError, match="invalid page"):
        runner.parse_pages("3-1")
    with pytest.raises(ValueError, match="invalid page selector"):
        runner.parse_pages("0")


def test_output_package_name_uses_readable_source_title(tmp_path: Path):
    runner = _runner_module()
    source = tmp_path / "50个相处话术案例.pdf"
    when = datetime(2026, 8, 21, 15, 42, 33, tzinfo=timezone(timedelta(hours=8)))

    package_id, target = runner.resolve_package_target(tmp_path, source, when=when)

    assert package_id == "50个相处话术案例"
    assert target == tmp_path / package_id
    assert not re.fullmatch(r"source-[0-9a-f]{12}-.+", package_id)


def test_output_package_name_adds_short_collision_suffix(tmp_path: Path):
    runner = _runner_module()
    source = tmp_path / "50个相处话术案例.pdf"
    when = datetime(2026, 8, 21, 15, 42, 33, tzinfo=timezone(timedelta(hours=8)))
    (tmp_path / "50个相处话术案例").mkdir()

    package_id, target = runner.resolve_package_target(tmp_path, source, when=when)

    assert package_id == "50个相处话术案例-02"
    assert target == tmp_path / package_id


def test_plan_only_does_not_read_or_hash_source(tmp_path: Path):
    source = tmp_path / "source.pdf"
    output_root = tmp_path / "output"

    result = _run(source, output_root, "1-2", "--plan-only")
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "planned"
    assert payload["source"]["source_type"] == "pdf"
    assert payload["source"]["sha256"] is None
    assert payload["source"]["source_fingerprint_status"] == "not-run"
    assert payload["scope"]["selected_pages"] == [1, 2]
    assert not output_root.exists()


def test_active_markdown_keeps_semantic_title_out_of_page_prefix(tmp_path: Path):
    runner = _runner_module()
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "current-source.pptx"
    native = {
        "pages": [{
            "page": 1,
            "text_blocks": [{
                "block_id": "p1-o1",
                "text": "当前来源标题\n正文内容",
                "lines": [],
                "bbox": {"x": 0, "y": 0, "cx": 1, "cy": 1, "unit": "EMU"},
                "order": 1,
            }],
            "nodes": [],
            "image_refs": [],
        }],
    }
    sections = runner.assemble_markdown(package, source, [1], native, {}, {})
    text = (package / "index.md").read_text(encoding="utf-8")

    assert "## 当前来源标题" in text
    assert "## 第 1 页：当前来源标题" not in text
    assert "current-source.pptx" not in text
    assert sections[0]["title"] == "当前来源标题"


def test_progress_and_audit_evidence_helpers_are_machine_readable(tmp_path: Path):
    runner = _runner_module()
    progress = tmp_path / "evidence" / "progress.jsonl"
    runner.append_progress(progress, run_id="run-1", source_fingerprint="a" * 64, stage="source_inventory", status="complete", pages=[1], next_action="continue")
    event = json.loads(progress.read_text(encoding="utf-8").strip())

    assert event["run_id"] == "run-1"
    assert event["source_fingerprint"] == "a" * 64
    assert event["page"] == 1
    assert event["stage"] == "source_inventory"


def test_page_assets_use_short_semantic_names_and_keep_provenance_in_manifest_fields(tmp_path: Path):
    runner = _runner_module()
    source = tmp_path / "source-1130abf8.pdf"
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    rendered = tmp_path / "render.png"
    from PIL import Image

    Image.new("RGB", (2, 2), "white").save(rendered)
    native = {
        "pages": [{
            "page": 3,
            "text_blocks": [{
                "block_id": "p3-title",
                "text": "Leadership 3357",
                "bbox": {"top": 1},
                "order": 1,
            }],
        }],
    }

    assets, paths, slugs = runner._copy_page_assets(
        source,
        ".pdf",
        {3: rendered},
        package,
        "a" * 64,
        native,
    )

    assert paths[3].name == "leadership-3357.png"
    assert slugs[3] == "leadership-3357"
    assert assets[0]["file_name"] == "leadership-3357.png"
    assert assets[0]["naming"]["source_slug"] == "source-1130abf8"
    assert "page-render" not in assets[0]["file_name"]
    assert "original-page" not in assets[0]["file_name"]


def test_page_assets_accept_verified_alias_for_non_ascii_title(tmp_path: Path):
    runner = _runner_module()
    source = tmp_path / "21个成长思维模型.pdf"
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    rendered = tmp_path / "render.png"
    from PIL import Image

    Image.new("RGB", (2, 2), "white").save(rendered)
    native = {"pages": [{"page": 16, "text_blocks": [{"block_id": "p16-title", "text": "智者思维模型道法术器势志", "bbox": {"top": 1}, "order": 1}]}]}
    assets, paths, _ = runner._copy_page_assets(
        source,
        ".pdf",
        {16: rendered},
        package,
        "b" * 64,
        native,
        {"智者思维模型道法术器势志": "dao-methods"},
    )

    assert paths[16].name == "dao-methods.png"
    assert assets[0]["naming"]["status"] == "generated"


def test_page_assets_add_only_minimal_suffix_for_real_semantic_collision(tmp_path: Path):
    runner = _runner_module()
    source = tmp_path / "growth-models.pdf"
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    from PIL import Image

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (2, 2), "white").save(first)
    Image.new("RGB", (3, 3), "black").save(second)
    native = {
        "pages": [
            {"page": 9, "text_blocks": [{"block_id": "p9-title", "text": "Life Value", "bbox": {"top": 1}, "order": 1}]},
            {"page": 10, "text_blocks": [{"block_id": "p10-title", "text": "Life Value", "bbox": {"top": 1}, "order": 1}]},
        ],
    }

    assets, paths, _ = runner._copy_page_assets(
        source,
        ".pdf",
        {9: first, 10: second},
        package,
        "c" * 64,
        native,
    )

    assert [paths[page].name for page in (9, 10)] == ["life-value.png", "life-value-02.png"]
    assert [item["naming"]["ordinal"] for item in assets] == [1, 2]


def test_plan_only_honors_custom_profile_without_reading_source(tmp_path: Path):
    runner = _runner_module()
    profile = runner.load_yaml(PROFILE_PATH)
    profile["id"] = "test-profile-route"
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")

    result = _run(
        tmp_path / "input.png",
        tmp_path / "output",
        "1",
        "--profile",
        str(profile_path),
        "--plan-only",
    )
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    assert payload["runner"]["profile_id"] == "test-profile-route"
    assert payload["source"]["source_type"] == "image"
    assert payload["scope"]["one_source_per_run"] is True


def test_each_plan_is_isolated_to_its_single_source(tmp_path: Path):
    source_a = tmp_path / "first.pdf"
    source_b = tmp_path / "second.pdf"

    first = _payload(_run(source_a, tmp_path / "out-a", "1", "--plan-only"))
    second = _payload(_run(source_b, tmp_path / "out-b", "1", "--plan-only"))

    assert first["source"]["path"] == str(source_a.resolve())
    assert second["source"]["path"] == str(source_b.resolve())
    assert first["source"]["path"] != second["source"]["path"]
    assert first["scope"]["one_source_per_run"] is True
    assert second["scope"]["one_source_per_run"] is True
    assert "sources" not in first
    assert "sources" not in second


@pytest.mark.parametrize(
    ("filename", "pages", "source_type", "native", "asset_source"),
    [
        (
            "18个工作思维模型-第24-25页.pptx",
            "24-25",
            "pptx",
            "pptx-xml-parser",
            "office-render",
        ),
        (
            "18个工作思维模型-第24-25页.pdf",
            "1-2",
            "pdf",
            "pdf-layout-parser",
            "pdf-render",
        ),
        ("pdf-page-24.png", "1", "image", "image-metadata", "original-preserved"),
    ],
)
def test_fixture_plan_routes_pptx_pdf_and_image_without_fixture_page_defaults(
    filename: str,
    pages: str,
    source_type: str,
    native: str,
    asset_source: str,
    tmp_path: Path,
):
    result = _run(_fixture(filename), tmp_path / "plan-output", pages, "--plan-only")
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "planned"
    assert payload["source"]["source_type"] == source_type
    assert payload["source"]["sha256"] is None
    assert payload["scope"]["selected_pages"] == [int(value) for value in pages.replace("-", ",").split(",")]
    assert native in payload["route"]["native_extraction"]
    assert asset_source in payload["route"]["asset_sources"]


def test_execute_missing_source_returns_explicit_failed_status(tmp_path: Path):
    result = _run(tmp_path / "missing.pdf", tmp_path / "output", "1", "--execute")
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["status"] == "failed"
    assert payload.get("error") or payload.get("diagnostics") or payload.get("failure")


def test_execute_creates_a_fresh_output_root_for_pdf_preflight(tmp_path: Path):
    source = _fixture("18个工作思维模型-第24-25页.pdf")
    output_root = tmp_path / "new-run" / "pdf"
    result = _run(source, output_root, "all", "--execute")
    payload = _payload(result)

    assert result.returncode in {0, 1}, result.stderr
    package = Path(payload["package_path"])
    assert package.is_dir()
    assert package.parent == output_root.resolve()
    assert package.name == source.stem
    assert (package / "index.md").is_file()
    assert not (package / "evidence").exists()


def test_pdf_native_layer_preserves_block_line_word_bbox_and_reader_text(tmp_path: Path):
    source = _fixture("18个工作思维模型-第24-25页.pdf")
    result = _run(source, tmp_path / "pdf-output", "all", "--execute", "--engineering-output")
    payload = _payload(result)
    package = Path(payload["package_path"])
    native = json.loads((package / "evidence/native-content-model.json").read_text(encoding="utf-8"))
    document = (package / "index.md").read_text(encoding="utf-8")

    assert native["status"] == "complete"
    assert native["coverage"]["page_count"] == 2
    assert native["coverage"]["block_count"] > 0
    assert native["coverage"]["word_count"] > 0
    for page in native["pages"]:
        assert page["layout"]["status"] == "complete"
        assert page["layout"]["flows"]
        for block in page["text_blocks"]:
            assert block["bbox"]["unit"] == "pt"
            assert block["lines"]
            assert all(line["word_ids"] for line in block["lines"])
            for line in block["lines"]:
                assert line["text"].replace(" ", "") in document.replace(" ", "")
    assert "提升领导力的3357" in document
    assert "搞定自己vs搞定别人" in document
    assert "assets/" in document
    assert "assets/pages/" not in document


def test_image_candidate_layers_keep_source_fingerprint_when_ocr_is_unavailable(tmp_path: Path):
    source = _fixture("pdf-page-24.png")
    result = _run(source, tmp_path / "image-output", "all", "--execute", "--engineering-output")
    payload = _payload(result)
    package = Path(payload["package_path"])
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    document = (package / "index.md").read_text(encoding="utf-8")
    ocr = json.loads((package / "evidence/ocr-candidates.json").read_text(encoding="utf-8"))
    visual = json.loads((package / "evidence/visual-candidates.json").read_text(encoding="utf-8"))

    assert ocr["source_fingerprint"] == fingerprint
    assert ocr["candidate_layer_state"] == "raw-layer-not-generated"
    assert ocr["status"] == "needs-review"
    assert ocr["candidates"] == []
    assert visual["source_fingerprint"] == fingerprint
    assert "OCR 待复核候选" not in document
    assert "没有原生文字层" not in document
    assert "![原始页面]" in document


def test_unsupported_source_suffix_returns_explicit_failed_status(tmp_path: Path):
    result = _run(tmp_path / "input.rtf", tmp_path / "output", "1", "--plan-only")
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["status"] == "failed"
    assert "support source suffix" in payload["error"]


def test_docx_plan_is_whole_document_and_does_not_read_source(tmp_path: Path):
    source, output = tmp_path / "missing.DOCX", tmp_path / "output"
    result = _run(source, output, "all", "--plan-only")
    payload = _payload(result)
    assert result.returncode == 0
    assert payload["source"]["source_type"] == "docx"
    assert payload["source"]["sha256"] is None
    assert payload["scope"]["selected_pages"] is None
    assert not output.exists()
    for flags, expected in (
        (("1", "--execute"), "requires --pages all"),
        (("all", "--execute", "--engineering-output"), "does not support engineering output"),
    ):
        rejected = _run(source, output, *flags)
        assert rejected.returncode == 2
        assert expected in _payload(rejected)["error"]
        assert not output.exists()


@pytest.mark.parametrize(
    ("filename", "pages", "expected_pages"),
    [
        ("18个工作思维模型-第24-25页.pptx", "24-25", [24, 25]),
        ("18个工作思维模型-第24-25页.pdf", "all", [1, 2]),
        ("pdf-page-24.png", "all", [1]),
    ],
)
def test_execute_fixture_creates_minimal_reader_package(
    filename: str,
    pages: str,
    expected_pages: list[int],
    tmp_path: Path,
):
    source = _fixture(filename)
    output_root = tmp_path / "execute-output"
    result = _run(source, output_root, pages, "--execute")
    payload = _payload(result)

    assert result.returncode == 1, result.stderr
    assert payload["status"] == "needs-review"
    assert payload["validation"]["status"] == "not-run"
    assert payload["validation"]["authority"] == "independent-validator"
    assert payload["source"]["path"] == str(source.resolve())
    assert payload["source"]["source_fingerprint_status"] == "computed"
    assert len(payload["source"]["sha256"]) == 64
    assert payload["scope"]["selected_pages"] == expected_pages
    assert payload["scope"]["one_source_per_run"] is True

    package_value = (
        payload.get("output")
        or payload.get("package_root")
        or payload.get("package")
        or payload.get("package_path")
    )
    assert package_value, payload
    package = Path(package_value)
    assert package.is_dir()
    assert package.name == source.stem
    for relative in ("index.md", "assets"):
        assert (package / relative).exists(), relative
    assert not (package / "manifest.yaml").exists()
    assert not (package / "evidence").exists()
    assert not (package / "reports").exists()
    assert not (package / "page-units").exists()
    assert not list(package.glob("*.json"))
    markdown = (package / "index.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"\((?:<)?(assets/[^)> ]+)", markdown))
    actual = {path.relative_to(package).as_posix() for path in (package / "assets").iterdir() if path.is_file()}
    assert referenced == actual
