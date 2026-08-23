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


def _flatten_fixture_assets(package: Path) -> None:
    """Materialize the ignored fixture as the current flat package layout."""

    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    replacements: dict[str, str] = {}
    assets_root = package / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)
    for asset in manifest.get("assets", []) or []:
        if not isinstance(asset, dict):
            continue
        old_relative = asset.get("file_path")
        if not isinstance(old_relative, str):
            continue
        occurrence = ((asset.get("source") or {}).get("source_occurrences") or [{}])[0]
        page = occurrence.get("page") if isinstance(occurrence, dict) else None
        semantic_slug = ((asset.get("naming") or {}).get("semantic_slug") or "asset").strip()
        old_path = package / old_relative
        if not old_path.exists() and isinstance(page, int):
            extension = old_path.suffix.lower() or ".bin"
            candidates = sorted(assets_root.glob(f"{semantic_slug}*{extension}"))
            candidates += sorted((assets_root / "pages").glob(f"{semantic_slug}*{extension}"))
            if candidates:
                old_path = candidates[0]
                old_relative = old_path.relative_to(package).as_posix()
        suffix = old_path.suffix.lower() or ".bin"
        new_name = f"{semantic_slug}{suffix}"
        ordinal = 1
        while (assets_root / new_name).exists() and (assets_root / new_name).resolve() != old_path.resolve():
            ordinal += 1
            new_name = f"{semantic_slug}-{ordinal:02d}{suffix}"
        new_path = assets_root / new_name
        if old_path == new_path:
            continue
        shutil.move(str(old_path), str(new_path))
        new_relative = new_path.relative_to(package).as_posix()
        replacements[old_relative] = new_relative
        asset["file_name"] = new_path.name
        asset["file_path"] = new_relative

    for path in package.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for old_relative, new_relative in replacements.items():
            text = text.replace(old_relative, new_relative)
        path.write_text(text, encoding="utf-8")

    for directory in sorted((package / "assets").glob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def make_package(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    shutil.copytree(FIXTURE_RUN, package)
    _flatten_fixture_assets(package)
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
                "path": "index.md",
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


def test_reader_only_package_requires_only_markdown_and_referenced_images(tmp_path: Path):
    package = tmp_path / "reader"
    (package / "assets").mkdir(parents=True)
    (package / "assets" / "page-one.png").write_bytes(b"png")
    (package / "index.md").write_text("# 关键点\n\n![页面](assets/page-one.png)\n", encoding="utf-8")

    result = validate_package(package)

    assert result.valid
    assert result.as_dict()["status"] == "passed"


def test_reader_only_text_package_does_not_require_assets_directory(tmp_path: Path):
    package = tmp_path / "reader-text-only"
    package.mkdir()
    (package / "index.md").write_text("# 关键点\n\n只有文字内容。\n", encoding="utf-8")

    result = validate_package(package)

    assert result.valid, result.errors
    assert result.as_dict()["status"] == "passed"


def test_reader_only_package_allows_source_named_markdown_alias(tmp_path: Path):
    package = tmp_path / "reader-source-alias"
    package.mkdir()
    content = "# 关键点\n\n只有文字内容。\n"
    (package / "index.md").write_text(content, encoding="utf-8")
    (package / "第009部《绝秘人性天书》.md").write_text(content, encoding="utf-8")

    result = validate_package(package)

    assert result.valid, result.errors
    assert result.as_dict()["status"] == "passed"


def test_reader_only_package_rejects_unreferenced_or_engineering_files(tmp_path: Path):
    package = tmp_path / "reader"
    (package / "assets").mkdir(parents=True)
    (package / "assets" / "page-one.png").write_bytes(b"png")
    (package / "assets" / "unused.png").write_bytes(b"png")
    (package / "index.md").write_text("# 关键点\n\n![页面](assets/page-one.png)\n", encoding="utf-8")
    (package / "evidence").mkdir()
    (package / "extra.json").write_text("{}", encoding="utf-8")

    result = validate_package(package)

    assert not result.valid
    assert any("unreferenced-asset" in error for error in result.errors)
    assert any("reader-layout" in error for error in result.errors)


def test_real_package_validator_accepts_structurally_valid_needs_review_package(tmp_path):
    result = validate_package(make_package(tmp_path))
    assert result.valid, result.errors
    assert result.declared_status == "needs-review"


def test_real_package_validator_does_not_warn_when_all_reader_images_are_explicitly_omitted(tmp_path):
    package = make_package(tmp_path)
    index_path = package / "index.md"
    index_path.write_text(
        "\n".join(
            line for line in index_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("![")
        ) + "\n",
        encoding="utf-8",
    )
    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    for section in manifest["markdown"]["sections"]:
        section["visual_asset_ids"] = []
        section["reader_image_policy"] = {
            "action": "omit",
            "reason": "text-fully-represented-in-markdown",
            "confidence": "high",
        }
    _write(manifest_path, manifest)

    result = validate_package(package)

    assert result.valid, result.errors
    assert not any(warning.startswith("markdown.links:") for warning in result.warnings)


def test_real_package_validator_detects_media_hash_drift(tmp_path):
    package = make_package(tmp_path)
    asset = package / "assets/leadership-3357.png"
    asset.write_bytes(asset.read_bytes() + b"corruption")
    result = validate_package(package)
    assert any(error.startswith("integrity-check-failed:") for error in result.errors)


def test_real_package_validator_detects_broken_markdown_link(tmp_path):
    package = make_package(tmp_path)
    document = package / "index.md"
    document.write_text(document.read_text(encoding="utf-8").replace("assets/leadership-3357.png", "assets/missing/leadership-3357.png"), encoding="utf-8")
    result = validate_package(package)
    assert any(error.startswith("path.missing:") or error.startswith("broken-link:") for error in result.errors)


def test_real_package_validator_rejects_ocr_review_marker_in_reader_markdown(tmp_path):
    package = make_package(tmp_path)
    document = package / "index.md"
    document.write_text(document.read_text(encoding="utf-8") + "\nOCR 待复核：请检查该文字。\n", encoding="utf-8")

    result = validate_package(package)

    assert any(error.startswith("reader-layer-separation:") for error in result.errors)


def test_real_package_validator_rejects_page_number_heading(tmp_path):
    package = make_package(tmp_path)
    document = package / "index.md"
    document.write_text(document.read_text(encoding="utf-8").replace("## 提升领导力的3357", "## 第 24 页", 1), encoding="utf-8")

    result = validate_package(package)

    assert any(error.startswith("reader-semantic-content:") for error in result.errors)


def test_real_package_validator_rejects_untraceable_markdown_content_repair(tmp_path):
    package = make_package(tmp_path)
    review = package / "reviews" / "markdown-content-repair.yaml"
    review.parent.mkdir()
    _write(
        review,
        {
            "contract": "markdown-content-repair",
            "status": "deferred",
            "items": [{
                "id": "repair-1",
                "kind": "paragraph",
                "source_occurrence": "missing-occurrence",
                "section_id": "page-24",
                "source_text": "文 字",
                "candidate_text": "文字",
                "proposed_text": "文字",
                "status": "deferred",
                "repair": {
                    "operation": "join-split-text",
                    "basis": "extracted-text",
                    "before": "文 字",
                    "after": "文字",
                },
            }],
        },
    )

    result = validate_package(package)

    assert any(error.startswith("markdown-content-repair:") for error in result.errors)


def test_real_package_validator_requires_page_projection_triplets_for_mac96(tmp_path):
    package = make_package(tmp_path)
    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    manifest["processing"]["execution_path"] = "mac96-direct-vision"
    _write(manifest_path, manifest)

    result = validate_package(package)

    assert any(error.startswith("page-output-contract:") for error in result.errors)


def _prepare_mac96_divider_page(package: Path, page: int = 24) -> Path:
    manifest_path = package / "manifest.yaml"
    manifest = _load(manifest_path)
    source_fingerprint = manifest["source"]["source_fingerprint"]
    asset = next(
        item
        for item in manifest["assets"]
        if ((item.get("source") or {}).get("source_occurrences") or [{}])[0].get("page") == page
    )
    old_asset_id = asset["asset_id"]
    asset_path = package / asset["file_path"]
    original_sha256 = (asset.get("media") or {}).get("sha256")
    asset["asset_id"] = f"page-p{page:03d}"
    asset_path.unlink()

    for section in manifest["markdown"]["sections"]:
        if old_asset_id in section.get("visual_asset_ids", []):
            section["visual_asset_ids"] = [asset["asset_id"]]

    manifest["source"]["selected_pages"] = [page]
    manifest["processing"]["execution_path"] = "mac96-direct-vision"
    _write(manifest_path, manifest)

    index_path = package / "index.md"
    index_path.write_text(
        "\n".join(
            line
            for line in index_path.read_text(encoding="utf-8").splitlines()
            if asset["file_path"] not in line
        ) + "\n",
        encoding="utf-8",
    )
    evidence = package / "evidence"
    _write(
        evidence / f"vision-page-analysis-p{page:03d}.json",
        {
            "input_asset_id": asset["asset_id"],
            "source_fingerprint": source_fingerprint,
            "image_sha256": original_sha256,
            "status": "complete",
        },
    )
    _write(
        evidence / f"vision-text-transcription-p{page:03d}.json",
        {
            "input_asset_id": asset["asset_id"],
            "source_fingerprint": source_fingerprint,
            "status": "complete",
            "candidate_markdown": "# 结构分隔标题",
        },
    )
    unit_path = package / "page-units" / f"p{page:03d}.md"
    unit_path.parent.mkdir(exist_ok=True)
    unit_path.write_text(
        "---\ntype: divider\n---\n\n# 结构分隔标题\n",
        encoding="utf-8",
    )
    _write(
        evidence / "visual-asset-pruning.json",
        {
            "schema": "visual-asset-pruning",
            "version": "0.1.0",
            "status": "complete",
            "mode": "apply",
            "source_fingerprint": source_fingerprint,
            "excluded_pages": [page],
            "assets": [
                {
                    "asset_id": asset["asset_id"],
                    "page": page,
                    "file_path": asset["file_path"],
                    "sha256": original_sha256,
                    "reason_code": "user-approved-page-selection",
                    "source_asset_action": "deleted-by-explicit-user-request",
                }
            ],
            "synchronization": {
                "document": "updated",
                "manifest_assets": "retained",
                "source_occurrences": "retained",
                "source_files": "deleted-by-explicit-user-request",
            },
            "diagnostics": [],
        },
    )
    return unit_path


def test_real_package_validator_allows_an_explicit_divider_to_delete_its_page_image(tmp_path: Path):
    package = make_package(tmp_path)
    unit_path = _prepare_mac96_divider_page(package)

    result = validate_package(package)

    assert result.valid, result.errors
    unit_path.write_text("# 普通页面\n", encoding="utf-8")
    non_divider_result = validate_package(package)
    assert any(
        error.startswith("page-output-contract:") and "non-divider" in error
        for error in non_divider_result.errors
    )


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
