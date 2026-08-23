from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/runs/fixture-contract-20260814-0118/outputs/document-asset-package"
PRUNE = ROOT / "skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py"
CLASSIFY = ROOT / "skills/semantic-visual-asset-pruning/scripts/classify_visual_assets_mac96.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def copy_fixture(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    shutil.copytree(FIXTURE, package)
    return package


def run_prune(package: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRUNE), str(package), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def make_langgraph_package(tmp_path: Path, *, with_image_links: bool) -> Path:
    package = tmp_path / "langgraph-package"
    (package / "assets").mkdir(parents=True)
    (package / "page-units").mkdir()
    (package / "evidence").mkdir()

    decisions = []
    for page in (1, 2):
        image_path = package / "assets" / f"page-{page:03d}.png"
        payload = f"png-{page}".encode("utf-8")
        image_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        (package / "page-units" / f"p{page:03d}.md").write_text(
            f"<!-- page:{page} -->\n\n# 第{page}页\n\n这是第{page}页的纯文字内容。\n",
            encoding="utf-8",
        )
        (package / "evidence" / f"page-result-p{page:03d}.json").write_text(
            json.dumps(
                {
                    "page": page,
                    "status": "passed",
                    "asset": {
                        "path": str(image_path),
                        "sha256": digest,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        decisions.append(
            {
                "page": page,
                "sha256": digest,
                "decision": "candidate-remove" if page == 1 else "retain",
                "reason_codes": ["text-covered"] if page == 1 else ["diagram"],
                "semantic_text_coverage": "complete",
                "visual_increment": "none" if page == 1 else "present",
                "classification_source": "test-fixture",
            }
        )

    image_line = "![第2页](assets/page-002.png)\n\n" if with_image_links else ""
    body = (
        "<!-- page:1 -->\n\n# 第1页\n\n纯文字。\n\n"
        "<!-- page:2 -->\n\n# 第2页\n\n"
        f"{image_line}"
        "这里保留一张有用图片。\n"
    )
    (package / "index.md").write_text(body, encoding="utf-8")
    (package / "document-temp.md").write_text(body, encoding="utf-8")
    (package / "evidence" / "visual-asset-classification-mac96.json").write_text(
        json.dumps(
            {
                "schema": "visual-asset-classification-mac96",
                "version": "0.2.0",
                "status": "complete",
                "mode": "candidate-only",
                "source_fingerprint": "fixture-source",
                "decisions": decisions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return package


def make_custom_langgraph_package(
    tmp_path: Path,
    *,
    page_decisions: dict[int, dict[str, Any]],
    root_linked_pages: set[int],
) -> Path:
    package = tmp_path / "langgraph-package-custom"
    (package / "assets").mkdir(parents=True)
    (package / "page-units").mkdir()
    (package / "evidence").mkdir()

    root_sections: list[str] = []
    decisions: list[dict[str, Any]] = []
    for page in sorted(page_decisions):
        image_path = package / "assets" / f"page-{page:03d}.png"
        payload = f"png-{page}".encode("utf-8")
        image_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        title = f"第{page}页"
        unit_image = f"![第{page}页](../assets/page-{page:03d}.png)\n\n"
        root_image = f"![第{page}页](assets/page-{page:03d}.png)\n\n" if page in root_linked_pages else ""
        (package / "page-units" / f"p{page:03d}.md").write_text(
            f"<!-- page:{page} -->\n\n# {title}\n\n{unit_image}这是第{page}页的正文。\n",
            encoding="utf-8",
        )
        (package / "evidence" / f"page-result-p{page:03d}.json").write_text(
            json.dumps(
                {
                    "page": page,
                    "status": "passed",
                    "asset": {
                        "path": str(image_path),
                        "sha256": digest,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        decision = dict(page_decisions[page])
        decision.update(
            {
                "page": page,
                "sha256": digest,
                "classification_source": "test-fixture",
            }
        )
        decisions.append(decision)
        root_sections.append(
            f"<!-- page:{page} -->\n\n# {title}\n\n{root_image}这是第{page}页的正文。\n"
        )

    merged = "\n\n".join(root_sections).rstrip() + "\n"
    (package / "index.md").write_text(merged, encoding="utf-8")
    (package / "document-temp.md").write_text(merged, encoding="utf-8")
    (package / "evidence" / "visual-asset-classification-mac96.json").write_text(
        json.dumps(
            {
                "schema": "visual-asset-classification-mac96",
                "version": "0.2.0",
                "status": "complete",
                "mode": "candidate-only",
                "source_fingerprint": "fixture-source",
                "decisions": decisions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return package


def test_preview_does_not_change_reader_or_source_assets(tmp_path):
    package = copy_fixture(tmp_path)
    before_document = (package / "index.md").read_text(encoding="utf-8")
    before_manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))

    result = run_prune(package, "--exclude-pages", "24", "--reason", "24=text-covered")

    assert result.returncode == 0, result.stderr
    assert (package / "index.md").read_text(encoding="utf-8") == before_document
    after_manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))
    assert after_manifest["assets"] == before_manifest["assets"]
    report = json.loads((package / "evidence/visual-asset-pruning.json").read_text(encoding="utf-8"))
    assert report["mode"] == "preview"
    assert report["assets"][0]["reason_code"] == "text-covered"
    assert report["synchronization"]["manifest_assets"] == "retained"


def test_apply_removes_only_reader_link_and_retains_manifest_asset(tmp_path):
    package = copy_fixture(tmp_path)
    manifest_path = package / "manifest.yaml"
    before_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    page24 = next(asset for asset in before_manifest["assets"] if asset["source"]["source_occurrences"][0]["page"] == 24)
    page24_path = package / page24["file_path"]
    before_bytes = page24_path.read_bytes()
    before_document = (package / "index.md").read_text(encoding="utf-8")

    result = run_prune(package, "--exclude-pages", "24", "--reason", "24=text-covered", "--apply")

    assert result.returncode == 0, result.stderr
    after_document = (package / "index.md").read_text(encoding="utf-8")
    assert "leadership-3357.png" not in after_document
    assert "self-vs-others.png" in after_document
    after_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert after_manifest["assets"] == before_manifest["assets"]
    assert page24_path.read_bytes() == before_bytes
    report = json.loads((package / "evidence/visual-asset-pruning.json").read_text(encoding="utf-8"))
    assert report["synchronization"] == {
        "document": "updated",
        "manifest_sections": "updated",
        "manifest_assets": "retained",
        "source_occurrences": "retained",
        "source_files": "retained",
    }
    assert before_document != after_document


def test_apply_fails_closed_when_selected_source_asset_is_missing(tmp_path):
    package = copy_fixture(tmp_path)
    manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))
    page24 = next(asset for asset in manifest["assets"] if asset["source"]["source_occurrences"][0]["page"] == 24)
    (package / page24["file_path"]).unlink()
    before_document = (package / "index.md").read_text(encoding="utf-8")

    result = run_prune(package, "--exclude-pages", "24", "--apply")

    assert result.returncode == 1
    assert (package / "index.md").read_text(encoding="utf-8") == before_document
    report = json.loads((package / "evidence/visual-asset-pruning.json").read_text(encoding="utf-8"))
    assert report["status"] == "needs-review"
    assert report["diagnostics"]


def test_mac96_classifier_fails_closed_for_invalid_candidate():
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier")
    result = module.parse_model_payload(
        {
            "decision": "candidate-remove",
            "reason_codes": ["text-covered"],
            "confidence": "high",
            "semantic_text_coverage": "partial",
            "visual_increment": "none",
            "rationale": "不完整",
        }
    )
    assert result["decision"] == "needs-review"
    assert "needs-review" in result["reason_codes"]


def test_mac96_classifier_accepts_structured_candidate_only():
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier_valid")
    result = module.parse_model_payload(
        {
            "response": json.dumps(
                {
                    "decision": "candidate-remove",
                    "reason_codes": ["table-of-contents"],
                    "confidence": "high",
                    "semantic_text_coverage": "complete",
                    "visual_increment": "none",
                    "rationale": "目录文字已可靠提取，版面没有额外关系编码",
                },
                ensure_ascii=False,
            )
        }
    )
    assert result["decision"] == "candidate-remove"
    assert result["reason_codes"] == ["table-of-contents"]


def test_mac96_classifier_accepts_legacy_text_covered_decision_alias():
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier_legacy_alias")
    result = module.parse_model_payload(
        {
            "response": json.dumps(
                {
                    "decision": "text-covered",
                    "reason_codes": ["text-covered"],
                    "confidence": "high",
                    "semantic_text_coverage": "complete",
                    "visual_increment": "none",
                    "rationale": "正文纯文字已覆盖，页眉页脚装饰不计入。",
                },
                ensure_ascii=False,
            )
        }
    )

    assert result["decision"] == "candidate-remove"
    assert result["reason_codes"] == ["text-covered"]


def test_mac96_classifier_remote_header_has_no_shell_splitting_space(monkeypatch, tmp_path):
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier_command")
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"response": json.dumps({
            "decision": "retain",
            "reason_codes": ["diagram"],
            "confidence": "high",
            "semantic_text_coverage": "complete",
            "visual_increment": "present",
            "rationale": "存在图示",
        })}).encode(), stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.classify_remote(image, "system", "prompt", "mac96", "model", 30)

    assert "Content-Type:application/json" in observed["command"]
    assert result["decision"] == "retain"


def test_page_result_markdown_heuristic_does_not_decide_candidate_remove_for_plain_text_page():
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier_plain_text")
    result = module.classify_page_result_markdown_heuristic(
        {
            "status": "passed",
            "cleanup": {
                "changes": [
                    {"reason": "repeated-header"},
                    {"reason": "repeated-footer"},
                ]
            },
        },
        {
            "body": "<!-- page:9 -->\n\n# 人性\n\n1、你会说话，就能搞定50%的人。\n2、你会分利，就能搞定70%的人。\n"
        },
    )

    assert result is not None
    assert result["decision"] == "needs-review"
    assert result["reason_codes"] == ["needs-review"]
    assert result["semantic_text_coverage"] == "unknown"
    assert result["visual_increment"] == "unknown"


def test_page_result_markdown_heuristic_fails_closed_on_uncertainty_marker():
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier_uncertain")
    result = module.classify_page_result_markdown_heuristic(
        {"status": "passed"},
        {
            "body": "<!-- page:23 -->\n\n# 守住原则\n\n- 图示关系：Unknown/needs-review。\n"
        },
    )

    assert result is None


def test_page_result_markdown_heuristic_fails_closed_on_special_layout_signals():
    module = load_module(CLASSIFY, "semantic_visual_asset_classifier_layout")
    result = module.classify_page_result_markdown_heuristic(
        {"status": "passed"},
        {
            "body": "<!-- page:24 -->\n# 做事顺序对了\n# 人生就顺了\n\n思维决定行为 | 行为决定结果 | 结果决定命运\n"
        },
    )

    assert result is None


def test_langgraph_preview_fails_closed_when_delete_requested_but_no_markdown_image_links(tmp_path):
    package = make_langgraph_package(tmp_path, with_image_links=False)

    result = run_prune(
        package,
        "--classification-report",
        "evidence/visual-asset-classification-mac96.json",
        "--delete-source-files",
    )

    assert result.returncode == 1
    plan = json.loads((package / "evidence/visual-asset-pruning-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "needs-review"
    assert any("no Markdown page-image references found" in item for item in plan["diagnostics"])


def test_langgraph_preview_fails_closed_when_retained_page_has_no_markdown_image_link(tmp_path):
    package = make_langgraph_package(tmp_path, with_image_links=False)
    body = (
        "<!-- page:1 -->\n\n# 第1页\n\n![第1页](assets/page-001.png)\n\n纯文字。\n\n"
        "<!-- page:2 -->\n\n# 第2页\n\n这里保留一张有用图片，但当前没有图片引用。\n"
    )
    (package / "index.md").write_text(body, encoding="utf-8")
    (package / "document-temp.md").write_text(body, encoding="utf-8")

    result = run_prune(
        package,
        "--classification-report",
        "evidence/visual-asset-classification-mac96.json",
        "--delete-source-files",
    )

    assert result.returncode == 1
    plan = json.loads((package / "evidence/visual-asset-pruning-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "needs-review"
    assert any(
        "retain-or-needs-review pages missing Markdown image references: 2" in item
        for item in plan["diagnostics"]
    )


def test_langgraph_preview_fails_closed_when_needs_review_page_has_no_markdown_image_link(tmp_path):
    package = make_custom_langgraph_package(
        tmp_path,
        page_decisions={
            9: {
                "decision": "candidate-remove",
                "reason_codes": ["text-covered"],
                "confidence": "high",
                "semantic_text_coverage": "complete",
                "visual_increment": "none",
            },
            24: {
                "decision": "needs-review",
                "reason_codes": ["needs-review"],
                "confidence": "unknown",
                "semantic_text_coverage": "unknown",
                "visual_increment": "unknown",
            },
        },
        root_linked_pages={9},
    )
    (package / "page-units/p024.md").write_text(
        "<!-- page:24 -->\n\n# 第24页\n\n这是第24页的正文。\n",
        encoding="utf-8",
    )

    result = run_prune(
        package,
        "--classification-report",
        "evidence/visual-asset-classification-mac96.json",
        "--delete-source-files",
    )

    assert result.returncode == 1
    plan = json.loads((package / "evidence/visual-asset-pruning-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "needs-review"
    assert any(
        "retain-or-needs-review pages missing Markdown image references: 24" in item
        for item in plan["diagnostics"]
    )


def test_langgraph_apply_projects_page9_delete_and_keeps_page24_retain(tmp_path):
    package = make_custom_langgraph_package(
        tmp_path,
        page_decisions={
            9: {
                "decision": "candidate-remove",
                "reason_codes": ["text-covered"],
                "confidence": "high",
                "semantic_text_coverage": "complete",
                "visual_increment": "none",
            },
            24: {
                "decision": "retain",
                "reason_codes": ["diagram"],
                "confidence": "high",
                "semantic_text_coverage": "complete",
                "visual_increment": "present",
            },
        },
        root_linked_pages={9, 24},
    )

    preview = run_prune(
        package,
        "--classification-report",
        "evidence/visual-asset-classification-mac96.json",
        "--delete-source-files",
    )
    assert preview.returncode == 0, preview.stderr
    plan = json.loads((package / "evidence/visual-asset-pruning-plan.json").read_text(encoding="utf-8"))
    page9 = next(item for item in plan["decisions"] if item["page"] == 9)
    page24 = next(item for item in plan["decisions"] if item["page"] == 24)
    assert page9["planned_action"] == "delete"
    assert page24["planned_action"] == "retain"

    applied = run_prune(
        package,
        "--apply-plan",
        "evidence/visual-asset-pruning-plan.json",
        "--delete-source-files",
    )
    assert applied.returncode == 0, applied.stderr

    index_text = (package / "index.md").read_text(encoding="utf-8")
    document_temp = (package / "document-temp.md").read_text(encoding="utf-8")
    page9_unit = (package / "page-units/p009.md").read_text(encoding="utf-8")
    page24_unit = (package / "page-units/p024.md").read_text(encoding="utf-8")

    assert "page-009.png" not in index_text
    assert "page-009.png" not in document_temp
    assert "page-009.png" not in page9_unit
    assert "page-024.png" in index_text
    assert "page-024.png" in document_temp
    assert "page-024.png" in page24_unit
    assert not (package / "assets/page-009.png").exists()
    assert (package / "assets/page-024.png").is_file()
