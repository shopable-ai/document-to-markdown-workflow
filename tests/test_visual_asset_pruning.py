from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

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
