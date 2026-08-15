from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

from validate_real_output_root import validate_package


ROOT = Path(__file__).parent
FIXTURE_RUN = ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package"


def _load(path: Path):
    if yaml is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value):
    if yaml is not None:
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def make_package(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    shutil.copytree(FIXTURE_RUN, package)
    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    source_fingerprint = manifest["assets"][0]["source"]["source_fingerprint"]
    manifest.update(
        {
            "package_id": "test-package",
            "status": "needs-review",
            "source": {
                "path_label": "source.pdf",
                "media_type": "application/pdf",
                "source_fingerprint": source_fingerprint,
                "selected_original_pages": [24, 25],
            },
            "markdown": {
                "path": "document.md",
                "sections": [
                    {
                        "section_id": "page-24",
                        "title": "第 24 页",
                        "body": "语义内容",
                        "source_occurrences": ["occ-pdf-p024-page-render-01"],
                        "visual_asset_ids": ["run-page-24"],
                        "confidence": "high",
                        "reader_order": "semantic-before-visual",
                        "unknown_forms": [],
                        "unresolved": [],
                    },
                    {
                        "section_id": "page-25",
                        "title": "第 25 页",
                        "body": "语义内容",
                        "source_occurrences": ["occ-pdf-p025-page-render-01"],
                        "visual_asset_ids": ["run-page-25"],
                        "confidence": "high",
                        "reader_order": "semantic-before-visual",
                        "unknown_forms": [],
                        "unresolved": [],
                    },
                ],
            },
            "validation": {
                "validator_version": "test",
                "status": "needs-review",
                "checks": ["required files", "media integrity", "stage evidence"],
            },
        }
    )
    manifest["processing"].update(
        {
            "native_extraction": "complete",
            "visual_review": "not-requested",
            "execution_path": "needs-review",
            "status": "needs-review",
            "stage_evidence_contract": "contracts/stage-evidence.yaml",
            "failure_report": "reports/failure-report.yaml",
        }
    )
    for stage in manifest["processing"]["stage_evidence"]:
        stage["evidence_paths"] = ["manifest.yaml"]
    for asset in manifest["assets"]:
        asset["source"]["package_id"] = "test-package"
        asset["source"]["source_fingerprint"] = source_fingerprint
    _write(manifest_path, manifest)
    for report_name in ("processing-report.yaml", "acceptance-report.yaml"):
        report_path = package / "reports" / report_name
        report = _load(report_path)
        report["stage_evidence"] = manifest["processing"]["stage_evidence"]
        if report_name == "acceptance-report.yaml":
            report["failures"] = []
        _write(report_path, report)
    return package


def test_real_package_validator_accepts_structurally_valid_needs_review_package(tmp_path):
    result = validate_package(make_package(tmp_path))
    assert result.valid, result.errors
    assert result.declared_status == "needs-review"


def test_real_package_validator_detects_media_hash_drift(tmp_path):
    package = make_package(tmp_path)
    asset = package / "assets/pages/work-models__p024__page-render__leadership-3357__original-page__o01.png"
    asset.write_bytes(asset.read_bytes() + b"corruption")
    result = validate_package(package)
    assert any(error.startswith("integrity-check-failed:") for error in result.errors)


def test_real_package_validator_detects_broken_markdown_link(tmp_path):
    package = make_package(tmp_path)
    document = package / "document.md"
    document.write_text(document.read_text(encoding="utf-8").replace("assets/pages/", "assets/missing/"), encoding="utf-8")
    result = validate_package(package)
    assert any(error.startswith("path.missing:") or error.startswith("broken-link:") for error in result.errors)


def test_real_package_validator_detects_stage_and_report_status_drift(tmp_path):
    package = make_package(tmp_path)
    acceptance_path = package / "reports/acceptance-report.yaml"
    acceptance = _load(acceptance_path)
    acceptance["stage_evidence"][0]["status"] = "complete"
    _write(acceptance_path, acceptance)
    result = validate_package(package)
    assert any(error.startswith("status-consistency:") for error in result.errors)


def test_real_package_validator_detects_unknown_occurrence_on_available_asset(tmp_path):
    package = make_package(tmp_path)
    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    manifest["assets"][0]["source"]["source_occurrences"][0]["status"] = "unknown"
    _write(manifest_path, manifest)
    result = validate_package(package)
    assert any(error.startswith("provenance-missing:") for error in result.errors)


def test_real_package_validator_detects_unknown_manifest_asset_id(tmp_path):
    package = make_package(tmp_path)
    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    manifest["markdown"]["sections"][0]["visual_asset_ids"] = ["missing-asset"]
    _write(manifest_path, manifest)
    result = validate_package(package)
    assert any(error.startswith("manifest-asset-id:") for error in result.errors)


def test_real_package_validator_requires_failure_report_for_real_output(tmp_path):
    package = make_package(tmp_path)
    (package / "reports/failure-report.yaml").unlink()
    result = validate_package(package)
    assert any(error.startswith("required-file:") for error in result.errors)
