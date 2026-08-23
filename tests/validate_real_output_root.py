#!/usr/bin/env python3
"""Validate one real Workflow A output package.

This is intentionally separate from ``validate_workflow_package.py``.  The
latter checks the specification package; this module reads a concrete
``<output_root>/<package-name>`` and checks its files, links, manifest media,
provenance, stage evidence, and report status consistency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - JSON manifests still work
    yaml = None

try:
    from PIL import Image
except ModuleNotFoundError:  # pragma: no cover - useful error is emitted below
    Image = None


REQUIRED_FILES = (
    "index.md",
    "manifest.yaml",
    "reports/processing-report.yaml",
    "reports/acceptance-report.yaml",
    "reports/failure-report.yaml",
)
REQUIRED_DIRECTORIES = ("assets",)
READER_FORBIDDEN_NAMES = {
    "manifest.yaml",
    "evidence",
    "reports",
    "page-units",
    "document-temp.md",
    "workflow-a-source-plan.json",
}
READER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
LEGACY_ASSET_DIRECTORIES = ("assets/pages", "assets/regions", "assets/embedded")
STAGE_IDS = tuple(f"A{i}" for i in range(1, 8))
STAGE_STATUSES = {"complete", "needs-review", "unavailable", "failed", "not-run"}
EVIDENCE_LEVELS = {
    "L1-spec-static",
    "L2-fixture-contract",
    "L2-local-runtime",
    "L3-remote-visual",
    "L4-image-generation",
}
ASSET_KINDS = {"page-render", "embedded-media", "complex-region", "crop", "input-image"}
ASSET_STATUSES = {"available", "needs-review", "unavailable"}
OCCURRENCE_STATUSES = {"located", "needs-review", "unknown"}
FAILURE_STATUSES = {"none", "needs-review", "failed"}
FAILURE_CODES = {
    "source-invalid",
    "unsupported-format",
    "extract-timeout",
    "render-failed",
    "ocr-unavailable",
    "visual-review-unavailable",
    "contract-invalid",
    "provenance-missing",
    "integrity-check-failed",
    "broken-link",
    "unknown-form-unresolved",
    "provider-timeout",
    "provider-rate-limit",
    "image-download-failed",
    "review-failed",
    "full-document-coverage-incomplete",
    "other",
}
FAILURE_SEVERITIES = {"P0", "P1", "P2", "info"}
FORBIDDEN_REPORT_FIELDS = {"api_key", "authorization", "cookie", "secret", "subscription_url"}
IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^) \t]+))")
FLAT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:-\d{2})?\.[a-z0-9]+$")
FLAT_NAME_FORBIDDEN_RE = re.compile(
    r"page-render|original-page|source-media|"
    r"embedded-media|complex-region|__|o\d{2}(?:\.|$)"
)
HEADING_RE = re.compile(r"^#{1,2}\s+\S.*$", re.MULTILINE)
SECTION_HEADING_RE = re.compile(r"^##\s+([^\n]+)$", re.MULTILINE)
PAGE_ONLY_TITLE_RE = re.compile(r"^(?:待复核[：:]\s*)?第\s*\d+\s*页(?:\s*[：:].*)?$")
READER_FORBIDDEN_MARKERS = (
    "待复核",
    "原文疑似",
    "来源页",
    "A4 状态",
    "来源与原图",
    "OCR 待复核",
    "OCR 解释",
    "OCR 语义",
    "建议结合原图复核",
    "审核状态",
    "source_occurrence",
    "visual_asset_id",
)


def _load(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(raw)
    return json.loads(raw)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _reader_image_action(analysis: Any) -> str:
    """Mirror the runner's fail-closed page-image decision for validation."""

    if not isinstance(analysis, dict):
        return "retain"
    policy = analysis.get("reader_image_policy")
    if not isinstance(policy, dict) or policy.get("action") != "omit":
        return "retain"
    if analysis.get("page_content_type") != "text-only":
        return "retain"
    if analysis.get("layout_dependency") not in {"none", "markdown-representable"}:
        return "retain"
    for field in ("semantic_visuals", "relationships", "tables_or_charts", "unknown_forms", "uncertainties"):
        if analysis.get(field):
            return "retain"
    return "omit"


def _is_divider_page_unit(text: str) -> bool:
    """Recognize the narrow route-free divider contract without parsing body YAML."""

    match = re.match(r"\A---\r?\n(?P<frontmatter>.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if not match:
        return False
    frontmatter = match.group("frontmatter")
    if not re.search(r"(?m)^type:\s*['\"]?divider['\"]?\s*$", frontmatter):
        return False
    return bool(re.search(r"(?m)^#{1,6}\s+\S", text[match.end():]))


@dataclass
class ValidationResult:
    package_root: Path
    errors: list[str]
    warnings: list[str]
    declared_status: str | None

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        computed_status = "passed" if self.valid and self.declared_status == "complete" else "needs-review" if self.valid else "failed"
        return {
            "status": computed_status,
            "valid": self.valid,
            "package_root": str(self.package_root),
            "errors": self.errors,
            "warnings": self.warnings,
        }


class _Validator:
    def __init__(self, package_root: Path):
        self.root = package_root.resolve()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.documents: dict[str, Any] = {}
        self.occurrence_ids: set[str] = set()
        self.asset_ids: set[str] = set()
        self.section_ids: set[str] = set()
        self.asset_by_path: dict[str, dict[str, Any]] = {}
        self.asset_by_id: dict[str, dict[str, Any]] = {}
        self.visual_asset_pruning: dict[str, Any] | None = None
        self.pruned_asset_paths: set[str] = set()
        self.pruned_asset_ids: set[str] = set()
        self.pruned_pages: set[int] = set()
        self.flat_layout = False

    def error(self, code: str, message: str) -> None:
        self.errors.append(f"{code}: {message}")

    def warning(self, code: str, message: str) -> None:
        self.warnings.append(f"{code}: {message}")

    def package_path(self, value: Any, label: str, *, must_exist: bool = True) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            self.error("path.invalid", f"{label} must be a non-empty relative path")
            return None
        candidate = Path(value)
        if candidate.is_absolute():
            self.error("path.escape", f"{label} is absolute: {value}")
            return None
        resolved = (self.root / candidate).resolve()
        if not _is_under(resolved, self.root):
            self.error("path.escape", f"{label} escapes package root: {value}")
            return None
        if must_exist and not resolved.exists():
            self.error("path.missing", f"{label} does not exist: {value}")
            return None
        return resolved

    def evidence_path(self, value: Any, label: str) -> Path | None:
        """Resolve evidence paths, which may legitimately point to source files outside the package."""

        if not isinstance(value, str) or not value.strip():
            self.error("path.invalid", f"{label} must be a non-empty path")
            return None
        candidate = Path(value)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        if not resolved.exists():
            self.error("path.missing", f"{label} does not exist: {value}")
            return None
        return resolved

    def load_document(self, relative: str) -> Any:
        path = self.root / relative
        if not path.is_file():
            return None
        try:
            value = _load(path)
        except Exception as exc:
            self.error("parse-error", f"{relative} is not valid YAML/JSON: {exc}")
            return None
        self.documents[relative] = value
        return value

    def load_visual_asset_pruning(self) -> None:
        path = self.root / "evidence" / "visual-asset-pruning.json"
        if not path.is_file():
            return
        try:
            report = _load(path)
        except Exception as exc:
            self.error("visual-asset-pruning", f"report is not valid JSON/YAML: {exc}")
            return
        if not isinstance(report, dict):
            self.error("visual-asset-pruning", "report must be a mapping")
            return
        self.visual_asset_pruning = report
        if report.get("schema") != "visual-asset-pruning" or report.get("status") != "complete" or report.get("mode") != "apply":
            self.error("visual-asset-pruning", "physical deletion requires a complete apply report")
            return
        synchronization = _mapping(report.get("synchronization")) or {}
        if synchronization.get("document") != "updated" or synchronization.get("source_files") != "deleted-by-explicit-user-request":
            self.error("visual-asset-pruning", "physical deletion report has incomplete synchronization evidence")
            return
        for record in report.get("assets") or []:
            if not isinstance(record, dict):
                continue
            if record.get("reason_code") != "user-approved-page-selection":
                continue
            if record.get("source_asset_action") != "deleted-by-explicit-user-request":
                continue
            asset_id = record.get("asset_id")
            file_path = record.get("file_path")
            page = record.get("page")
            digest = record.get("sha256")
            if not isinstance(asset_id, str) or not isinstance(file_path, str) or not isinstance(page, int):
                self.error("visual-asset-pruning", "approved deletion record is missing asset identity")
                continue
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                self.error("visual-asset-pruning", f"{asset_id} deletion record is missing its original SHA-256")
                continue
            self.pruned_asset_ids.add(asset_id)
            self.pruned_asset_paths.add(file_path)
            self.pruned_pages.add(page)

    def validate_layout(self) -> None:
        if not self.root.is_dir():
            self.error("output-root", f"package root is not a directory: {self.root}")
            return
        for relative in REQUIRED_FILES:
            if not (self.root / relative).is_file():
                self.error("required-file", f"missing {relative}")
        for relative in REQUIRED_DIRECTORIES:
            if not (self.root / relative).is_dir():
                self.error("required-directory", f"missing {relative}/")

    def validate_reader_output(self) -> None:
        """Validate the default reader-only package without engineering files."""

        if not self.root.is_dir():
            self.error("output-root", f"package root is not a directory: {self.root}")
            return
        index = self.root / "index.md"
        assets = self.root / "assets"
        if not index.is_file():
            self.error("required-file", "missing index.md")
            return
        if assets.exists() and not assets.is_dir():
            self.error("reader-layout", "assets exists but is not a directory")
            return
        if not index.read_text(encoding="utf-8").strip():
            self.error("reader-markdown", "index.md is empty")

        referenced: set[Path] = set()
        text = index.read_text(encoding="utf-8")
        for match in IMAGE_LINK_RE.finditer(text):
            raw = match.group(1) or match.group(2) or ""
            candidate = (self.root / unquote(raw)).resolve()
            if not _is_under(candidate, self.root):
                self.error("reader-link", f"image link escapes package: {raw}")
                continue
            if not _is_under(candidate, assets.resolve()):
                self.error("reader-link", f"image link must target assets/: {raw}")
                continue
            if not candidate.is_file():
                self.error("broken-link", f"reader image does not exist: {raw}")
                continue
            if candidate.suffix.lower() not in READER_IMAGE_SUFFIXES:
                self.error("reader-link", f"reader image is not a screenshot file: {raw}")
                continue
            referenced.add(candidate)

        if referenced and not assets.is_dir():
            self.error("required-directory", "index.md references images but assets/ is missing")
            return

        actual = {path.resolve() for path in assets.rglob("*") if path.is_file()} if assets.is_dir() else set()
        for path in sorted(actual - referenced):
            self.error("unreferenced-asset", f"assets file is not referenced by index.md: {path.relative_to(self.root)}")
        for path in sorted(referenced - actual):
            self.error("broken-link", f"reader image is missing: {path.relative_to(self.root)}")
        for path in assets.rglob("*") if assets.is_dir() else ():
            if path.is_dir() and path != assets:
                self.error("reader-layout", f"assets must be flat; found directory: {path.relative_to(self.root)}")
            elif path.is_file() and path.suffix.lower() not in READER_IMAGE_SUFFIXES:
                self.error("reader-layout", f"non-image asset is not allowed: {path.relative_to(self.root)}")

        for path in self.root.iterdir():
            if path.name in READER_FORBIDDEN_NAMES or path.name.startswith("evidence") or path.name.startswith("reports"):
                self.error("reader-layout", f"engineering output is forbidden in reader-only package: {path.name}")
            elif path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml"}:
                self.error("reader-layout", f"data-asset file is forbidden in reader-only package: {path.name}")

    def validate_manifest(self, manifest: Any) -> None:
        data = _mapping(manifest)
        if data is None:
            self.error("manifest.invalid", "manifest.yaml must contain a mapping")
            return
        self.validate_asset_layout(data)
        for key in ("package_id", "source", "markdown", "assets", "processing", "validation"):
            if key not in data:
                self.error("manifest.required", f"manifest missing {key}")
        package_id = data.get("package_id")
        if not isinstance(package_id, str) or not package_id.strip():
            self.error("manifest.package-id", "package_id must be a non-empty string")
        source = _mapping(data.get("source"))
        if source is None:
            self.error("manifest.source", "source must be a mapping")
        else:
            for key in ("path_label", "media_type", "source_fingerprint"):
                if not isinstance(source.get(key), str) or not source[key].strip():
                    self.error("manifest.source", f"source.{key} must be a non-empty string")
            fingerprint = source.get("source_fingerprint")
            if isinstance(fingerprint, str) and not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
                self.error("manifest.source", "source_fingerprint must be a SHA-256 hex digest")
            if self.visual_asset_pruning and self.visual_asset_pruning.get("source_fingerprint") != fingerprint:
                self.error("visual-asset-pruning", "report source fingerprint differs from manifest")
        status = data.get("status")
        if status is not None and not isinstance(status, str):
            self.error("manifest.status", "manifest status must be a string when present")
        self.validate_assets(data.get("assets"), package_id, source)
        self.validate_reconstruction_profiles(data.get("visual_reconstruction_profiles"))
        self.validate_markdown(data.get("markdown"), package_id)
        self.validate_processing(data.get("processing"), status)
        self.validate_page_output_pairs(data, source, _mapping(data.get("processing")))
        self.validate_execution_evidence(data, source)
        validation = _mapping(data.get("validation"))
        if validation is None:
            self.error("manifest.validation", "validation must be a mapping")
        else:
            for key in ("validator_version", "status", "checks"):
                if key not in validation:
                    self.error("manifest.validation", f"validation missing {key}")
            if validation.get("status") not in {"passed", "needs-review", "failed", "fixture-contract-only"}:
                self.error("manifest.validation", "validation.status is not a recognized state")

    def validate_asset_layout(self, manifest: dict[str, Any]) -> None:
        """Require flat assets for new packages while keeping old packages readable."""
        self.flat_layout = manifest.get("output_layout") == "flat-assets-v1"
        if not self.flat_layout:
            self.warning("asset-layout", "legacy asset layout; new runs must use output_layout=flat-assets-v1")
            return
        for relative in LEGACY_ASSET_DIRECTORIES:
            if (self.root / relative).exists():
                self.error("asset-layout", f"flat package must not contain legacy directory: {relative}/")
        assets_root = self.root / "assets"
        if assets_root.is_dir():
            nested = [path.relative_to(self.root).as_posix() for path in assets_root.rglob("*") if path.is_dir()]
            if nested:
                self.error("asset-layout", f"flat package contains nested asset directories: {nested}")

    def validate_execution_evidence(self, manifest: dict[str, Any], source: dict[str, Any] | None) -> None:
        """Check the active runner's cross-stage evidence without upgrading its quality state."""
        evidence = _mapping(manifest.get("evidence"))
        if evidence is None:
            # Older fixture/legacy packages predate the active source-runner
            # evidence map.  Keep them structurally reviewable, but make the
            # boundary visible instead of silently treating them as current.
            self.warning("execution-evidence", "manifest has no active-runner evidence map; fixture/legacy boundary is unverified")
            return
        active_keys = {"source_inventory", "capability_probe", "smoke_report", "progress"}
        if not active_keys.intersection(evidence):
            self.warning("execution-evidence", "manifest evidence contains only optional/legacy records; active-runner boundary is unverified")
            return
        required = {
            "source_inventory": "evidence/source-inventory.json",
            "capability_probe": "evidence/capability-probe.json",
            "smoke_report": "evidence/smoke-report.json",
            "progress": "evidence/progress.jsonl",
        }
        resolved: dict[str, Path] = {}
        for key, fallback in required.items():
            value = evidence.get(key, fallback)
            path = self.package_path(value, f"manifest.evidence.{key}")
            if path is not None:
                resolved[key] = path

        source_fingerprint = source.get("source_fingerprint") if source else None
        inventory_path = resolved.get("source_inventory")
        if inventory_path is not None:
            try:
                inventory = _load(inventory_path)
            except Exception as exc:
                self.error("execution-evidence", f"source inventory is not valid JSON/YAML: {exc}")
                inventory = None
            if not isinstance(inventory, dict):
                self.error("execution-evidence", "source inventory must be a mapping")
            else:
                for key in ("source_fingerprint", "media_type", "status", "page_count_or_dimensions", "text_layer", "image_layer", "readability", "diagnostics"):
                    if key not in inventory:
                        self.error("execution-evidence", f"source inventory missing {key}")
                if source_fingerprint and inventory.get("source_fingerprint") != source_fingerprint:
                    self.error("status-consistency", "source inventory fingerprint differs from manifest source fingerprint")
                self.validate_forbidden_fields(inventory, "source-inventory")

        for key in ("capability_probe", "smoke_report"):
            path = resolved.get(key)
            if path is None or path.suffix == ".jsonl":
                continue
            try:
                payload = _load(path)
            except Exception as exc:
                self.error("execution-evidence", f"{key} is not valid JSON/YAML: {exc}")
                continue
            if not isinstance(payload, dict):
                self.error("execution-evidence", f"{key} must be a mapping")
                continue
            if source_fingerprint and payload.get("source_fingerprint") not in {None, source_fingerprint}:
                self.error("status-consistency", f"{key} fingerprint differs from manifest source fingerprint")
            if payload.get("status") not in {"not-run", "passed", "needs-review", "failed", "complete"}:
                self.error("execution-evidence", f"{key} has an invalid status")
            self.validate_forbidden_fields(payload, key)

        progress_path = resolved.get("progress")
        if progress_path is not None:
            stages: set[str] = set()
            try:
                progress_handle = progress_path.open("r", encoding="utf-8")
            except OSError as exc:
                self.error("execution-evidence", f"progress stream cannot be read: {exc}")
                progress_handle = None
            if progress_handle is not None:
                with progress_handle:
                    for index, line in enumerate(progress_handle, 1):
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as exc:
                            self.error("execution-evidence", f"progress line {index} is not valid JSON: {exc}")
                            continue
                        if not isinstance(event, dict):
                            self.error("execution-evidence", f"progress line {index} must be a mapping")
                            continue
                        stage = event.get("stage")
                        if isinstance(stage, str):
                            stages.add(stage)
                        for key in ("run_id", "source_fingerprint", "stage", "task", "attempt", "status", "elapsed_seconds", "evidence_paths", "next_action"):
                            if key not in event:
                                self.error("execution-evidence", f"progress line {index} missing {key}")
                        if source_fingerprint and event.get("source_fingerprint") != source_fingerprint:
                            self.error("status-consistency", f"progress line {index} fingerprint differs from manifest source fingerprint")
                        if not isinstance(event.get("evidence_paths"), list):
                            self.error("execution-evidence", f"progress line {index} evidence_paths must be a list")
                        else:
                            for evidence_path in event["evidence_paths"]:
                                if isinstance(evidence_path, str) and evidence_path.strip():
                                    self.package_path(evidence_path, f"progress line {index} evidence")
                        self.validate_forbidden_fields(event, f"progress[{index}]")
            required_stages = {"execution_preflight", "source_inventory", "native_extraction", "source_rendering", "markdown_assembly", "independent_validation"}
            missing = sorted(required_stages - stages)
            if missing:
                self.error("execution-evidence", f"progress stream missing stages: {', '.join(missing)}")

        current_value = evidence.get("progress_current")
        if current_value:
            current_path = self.package_path(current_value, "manifest.evidence.progress_current")
            if current_path is not None:
                try:
                    current = _load(current_path)
                except Exception as exc:
                    self.error("execution-evidence", f"progress current snapshot is not valid JSON/YAML: {exc}")
                    current = None
                if not isinstance(current, dict):
                    self.error("execution-evidence", "progress current snapshot must be a mapping")
                else:
                    if source_fingerprint and current.get("source_fingerprint") not in {None, source_fingerprint}:
                        self.error("status-consistency", "progress current snapshot fingerprint differs from manifest source fingerprint")
                    if current.get("status") not in {"in-progress", "not-run", "passed", "needs-review", "failed", "complete"}:
                        self.error("execution-evidence", "progress current snapshot has an invalid status")
                    self.validate_forbidden_fields(current, "progress-current")

    def validate_reconstruction_profiles(self, profiles: Any) -> None:
        if profiles is None:
            return
        if not isinstance(profiles, list):
            self.error("reconstruction-profile", "visual_reconstruction_profiles must be a list")
            return
        required = ("profile_id", "source", "reconstruction", "canvas", "composition", "visual_elements", "text_fidelity", "style", "evidence", "uncertainty", "status")
        for index, profile in enumerate(profiles):
            item = _mapping(profile)
            if item is None:
                self.error("reconstruction-profile", f"profiles[{index}] must be a mapping")
                continue
            for key in required:
                if key not in item:
                    self.error("reconstruction-profile", f"profiles[{index}] missing {key}")
            source = _mapping(item.get("source"))
            if source is None:
                self.error("reconstruction-profile", f"profiles[{index}].source must be a mapping")
            else:
                asset_id = source.get("asset_id")
                if asset_id not in self.asset_ids:
                    self.error("reconstruction-profile", f"profiles[{index}] references unknown asset_id {asset_id}")
                asset_path = self.package_path(source.get("asset_path"), f"profiles[{index}].source.asset_path")
                declared_sha = source.get("asset_sha256")
                if asset_path is not None and isinstance(declared_sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", declared_sha):
                    if _sha256(asset_path).lower() != declared_sha.lower():
                        self.error("reconstruction-profile", f"profiles[{index}] asset SHA-256 does not match source asset")
                elif not isinstance(declared_sha, str):
                    self.error("reconstruction-profile", f"profiles[{index}] source.asset_sha256 is required")
            if item.get("status") not in {"available", "needs-review", "not-applicable", "unavailable"}:
                self.error("reconstruction-profile", f"profiles[{index}] has invalid status")
            if item.get("status") == "needs-review" and not item.get("uncertainty"):
                self.error("reconstruction-profile", f"profiles[{index}] needs uncertainty entries")
            self.validate_forbidden_fields(item, f"visual-reconstruction-profiles[{index}]")

    def validate_assets(self, assets: Any, package_id: Any, source: dict[str, Any] | None) -> None:
        if not isinstance(assets, list) or not assets:
            self.error("manifest.assets", "assets must be a non-empty list")
            return
        source_fingerprints = {
            value
            for value in (
                source.get("source_fingerprint") if source else None,
                source.get("pdf_fingerprint") if source else None,
            )
            if isinstance(value, str) and value
        }
        for index, asset in enumerate(assets):
            item = _mapping(asset)
            if item is None:
                self.error("asset.invalid", f"assets[{index}] must be a mapping")
                continue
            asset_id = item.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id.strip():
                self.error("asset.id", f"assets[{index}] has no asset_id")
                continue
            if asset_id in self.asset_ids:
                self.error("asset.id-duplicate", f"duplicate asset_id: {asset_id}")
            self.asset_ids.add(asset_id)
            self.asset_by_id[asset_id] = item
            for key in ("file_name", "file_path", "display_name", "asset_kind", "source", "media", "semantic", "naming", "status"):
                if key not in item:
                    self.error("asset.required", f"{asset_id} missing {key}")
            file_path_value = item.get("file_path")
            file_path = self.package_path(
                file_path_value,
                f"asset {asset_id}.file_path",
                must_exist=item.get("status") != "unavailable" and file_path_value not in self.pruned_asset_paths,
            )
            if file_path is not None:
                if file_path.name != item.get("file_name"):
                    self.error("asset.path", f"{asset_id}.file_name does not match file_path basename")
                normalized = file_path.relative_to(self.root).as_posix()
                if normalized in self.asset_by_path:
                    self.error("asset.path-duplicate", f"multiple assets point to {normalized}")
                self.asset_by_path[normalized] = item
                if self.flat_layout:
                    if file_path.parent != self.root / "assets":
                        self.error("asset.path", f"{asset_id} must be stored directly under assets/: {normalized}")
            filename = item.get("file_name")
            if isinstance(filename, str):
                if "/" in filename or "\\" in filename:
                    self.error("asset.filename", f"{asset_id}.file_name must not contain path separators")
                try:
                    filename.encode("ascii")
                except UnicodeEncodeError:
                    self.error("asset.filename", f"{asset_id}.file_name must be ASCII")
                if self.flat_layout:
                    if not FLAT_NAME_RE.fullmatch(filename):
                        self.error("asset.filename", f"{asset_id}.file_name must be a short semantic slug")
                    if FLAT_NAME_FORBIDDEN_RE.search(filename):
                        self.error("asset.filename", f"{asset_id}.file_name leaks page or engineering fields")
            if item.get("asset_kind") not in ASSET_KINDS:
                self.error("asset.kind", f"{asset_id}.asset_kind is invalid")
            status = item.get("status")
            if status not in ASSET_STATUSES:
                self.error("asset.status", f"{asset_id}.status is invalid")
            asset_source = _mapping(item.get("source"))
            if asset_source is None:
                self.error("asset.source", f"{asset_id}.source must be a mapping")
            else:
                if asset_source.get("package_id") != package_id:
                    self.error("asset.source", f"{asset_id}.source.package_id does not match manifest")
                if source_fingerprints and asset_source.get("source_fingerprint") not in source_fingerprints:
                    self.error("asset.source", f"{asset_id}.source_fingerprint does not match manifest source")
                self.validate_occurrences(asset_id, item, asset_source)
            self.validate_media(asset_id, item, file_path, status)
        for asset_id, item in self.asset_by_id.items():
            source_data = _mapping(item.get("source")) or {}
            for occurrence in source_data.get("source_occurrences", []) or []:
                parent = occurrence.get("parent_asset_id")
                if parent is not None and parent not in self.asset_ids:
                    self.error("provenance", f"{asset_id} occurrence parent_asset_id is unknown: {parent}")

    def validate_occurrences(self, asset_id: str, item: dict[str, Any], source: dict[str, Any]) -> None:
        occurrences = source.get("source_occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            self.error("provenance", f"{asset_id} must have at least one source_occurrence")
            return
        for index, occurrence in enumerate(occurrences):
            record = _mapping(occurrence)
            if record is None:
                self.error("provenance", f"{asset_id}.source_occurrences[{index}] must be a mapping")
                continue
            required = ("source_occurrence_id", "source_file", "page", "region", "object_ids", "parent_asset_id", "ordinal", "status")
            for key in required:
                if key not in record:
                    self.error("provenance", f"{asset_id} occurrence missing {key}")
            occurrence_id = record.get("source_occurrence_id")
            if not isinstance(occurrence_id, str) or not occurrence_id.strip():
                self.error("provenance", f"{asset_id} occurrence has invalid source_occurrence_id")
            elif occurrence_id in self.occurrence_ids:
                self.error("provenance", f"duplicate source_occurrence_id: {occurrence_id}")
            else:
                self.occurrence_ids.add(occurrence_id)
            if not isinstance(record.get("source_file"), str) or not record["source_file"].strip():
                self.error("provenance", f"{asset_id} occurrence source_file is required")
            if not isinstance(record.get("object_ids"), list):
                self.error("provenance", f"{asset_id} occurrence object_ids must be a list")
            if not isinstance(record.get("ordinal"), int) or record["ordinal"] < 1:
                self.error("provenance", f"{asset_id} occurrence ordinal must be a positive integer")
            if record.get("status") not in OCCURRENCE_STATUSES:
                self.error("provenance", f"{asset_id} occurrence status is invalid")
            if item.get("status") == "available" and record.get("status") == "unknown":
                self.error("provenance-missing", f"available asset {asset_id} has an unknown occurrence")
            if item.get("asset_kind") in {"page-render", "input-image"} and not isinstance(record.get("page"), int):
                self.error("provenance-missing", f"{asset_id} requires a located page number")

    def validate_media(self, asset_id: str, item: dict[str, Any], file_path: Path | None, status: Any) -> None:
        media = _mapping(item.get("media"))
        if media is None:
            self.error("media.required", f"{asset_id}.media must be a mapping")
            return
        for key in ("mime_type", "width", "height", "sha256"):
            if key not in media:
                self.error("media.required", f"{asset_id}.media missing {key}")
        if not isinstance(media.get("mime_type"), str) or not media.get("mime_type"):
            self.error("media.mime", f"{asset_id}.media.mime_type is required")
        if not isinstance(media.get("width"), int) or media.get("width", 0) <= 0:
            self.error("media.dimensions", f"{asset_id}.media.width must be a positive integer")
        if not isinstance(media.get("height"), int) or media.get("height", 0) <= 0:
            self.error("media.dimensions", f"{asset_id}.media.height must be a positive integer")
        digest = media.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            self.error("media.sha", f"{asset_id}.media.sha256 must be a SHA-256 hex digest")
        if file_path is None or not file_path.is_file():
            return
        if status != "available":
            return
        actual_digest = _sha256(file_path)
        if digest != actual_digest:
            self.error("integrity-check-failed", f"{asset_id} sha256 does not match file content")
        declared_mime = media.get("mime_type")
        if Image is None:
            self.error("media.unavailable", "Pillow is required for available image MIME/dimension checks")
            return
        try:
            with Image.open(file_path) as image:
                actual_mime = Image.MIME.get(image.format)
                if actual_mime and declared_mime != actual_mime:
                    self.error("media.mime", f"{asset_id} declares {declared_mime}, actual image MIME is {actual_mime}")
                if media.get("width") != image.width or media.get("height") != image.height:
                    self.error("media.dimensions", f"{asset_id} dimensions do not match image content")
        except Exception as exc:
            self.error("media.unreadable", f"{asset_id} cannot be opened as an image: {exc}")
        guessed = mimetypes.guess_type(file_path.name)[0]
        if guessed and declared_mime != guessed:
            self.error("media.mime", f"{asset_id} MIME does not match filename: {declared_mime} vs {guessed}")

    def validate_markdown(self, markdown: Any, package_id: Any) -> None:
        data = _mapping(markdown)
        if data is None:
            self.error("markdown.required", "markdown must be a mapping")
            return
        path = self.package_path(data.get("path"), "markdown.path")
        if path is None:
            return
        if path.name != "index.md":
            self.error("markdown.path", "markdown.path must point to index.md")
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            self.error("markdown.read", f"cannot read index.md: {exc}")
            return
        sections = data.get("sections")
        if not isinstance(sections, list):
            self.error("markdown.sections", "markdown.sections must be a list")
        else:
            for index, section in enumerate(sections):
                self.validate_section(index, section)
        links = list(IMAGE_LINK_RE.finditer(text))
        for match in links:
            target = unquote((match.group(1) or match.group(2) or "").strip())
            if target.startswith(("http://", "https://", "data:")):
                self.error("broken-link", f"external image link is not package-traceable: {target}")
                continue
            target = target.split("#", 1)[0]
            linked = self.package_path(target, "Markdown image link")
            normalized = target.replace("\\", "/").lstrip("./")
            if linked is None:
                continue
            asset = self.asset_by_path.get(normalized)
            if asset is None:
                self.error("broken-link", f"Markdown image is not registered in manifest: {target}")
            elif asset.get("status") == "unavailable":
                self.error("broken-link", f"Markdown links unavailable asset: {target}")
            self.validate_reader_order(text, match.start())
        expects_reader_image = any(
            isinstance(section, dict)
            and (
                bool(section.get("visual_asset_ids"))
                or (section.get("reader_image_policy") or {}).get("action") != "omit"
            )
            for section in (sections or [])
        )
        if not links and self.asset_by_id and expects_reader_image:
            self.warning("markdown.links", "index.md contains no image links")
        self.validate_reader_semantics(text)

    def validate_reader_semantics(self, text: str) -> None:
        """Reject page-only headings and relation/image-only sections."""
        leaked = [marker for marker in READER_FORBIDDEN_MARKERS if marker in text]
        if leaked:
            self.error("reader-layer-separation", f"index.md exposes engineering or unresolved repair markers: {leaked}")
        page_headings = [line.strip() for line in text.splitlines() if re.match(r"^#{1,6}\s+", line) and PAGE_ONLY_TITLE_RE.search(line.lstrip("# "))]
        if page_headings:
            self.error("reader-semantic-content", f"section heading uses source page placeholder: {page_headings}")
        sections = list(SECTION_HEADING_RE.finditer(text))
        for index, match in enumerate(sections):
            title = match.group(1).strip()
            end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
            section = text[match.end():end]
            before_image = re.split(r"(?m)^!\[", section, maxsplit=1)[0]
            semantic_lines = [
                line.strip()
                for line in before_image.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "```"))
            ]
            if PAGE_ONLY_TITLE_RE.fullmatch(title):
                self.error("reader-semantic-content", f"section heading is page-only: {title}")
            if not semantic_lines:
                self.error("reader-semantic-content", f"section has no reader semantic body: {title}")

    def validate_section(self, index: int, section: Any) -> None:
        data = _mapping(section)
        if data is None:
            self.error("markdown.section", f"sections[{index}] must be a mapping")
            return
        for key in ("section_id", "title", "body", "source_occurrences", "visual_asset_ids", "confidence", "reader_order", "unknown_forms"):
            if key not in data:
                self.error("markdown.section", f"sections[{index}] missing {key}")
        section_id = data.get("section_id")
        if not isinstance(section_id, str) or not section_id.strip():
            self.error("markdown.section", f"sections[{index}] has invalid section_id")
        else:
            self.section_ids.add(section_id)
        if data.get("reader_order") not in {"semantic-before-visual", "needs-review"}:
            self.error("markdown.section", f"sections[{index}] has invalid reader_order")
        if not isinstance(data.get("source_occurrences"), list):
            self.error("markdown.section", f"sections[{index}].source_occurrences must be a list")
        else:
            for occurrence_id in data["source_occurrences"]:
                if occurrence_id not in self.occurrence_ids:
                    self.error("provenance", f"section {data.get('section_id')} references unknown occurrence {occurrence_id}")
        if not isinstance(data.get("visual_asset_ids"), list):
            self.error("markdown.section", f"sections[{index}].visual_asset_ids must be a list")
        else:
            for asset_id in data["visual_asset_ids"]:
                if asset_id not in self.asset_ids:
                    self.error("manifest-asset-id", f"section {data.get('section_id')} references unknown asset {asset_id}")

    def validate_reader_order(self, text: str, image_start: int) -> None:
        headings = list(HEADING_RE.finditer(text[:image_start]))
        section_start = headings[-1].end() if headings else 0
        before = text[section_start:image_start]
        semantic_lines = [
            line.strip()
            for line in before.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "!", "```"))
        ]
        if not semantic_lines:
            # Image-only pages are an explicit needs-review output when no
            # text layer exists; the source image remains the evidence.
            non_image_lines = [line for line in before.splitlines() if line.strip() and not line.lstrip().startswith("!")]
            if not headings and not non_image_lines:
                return
            self.error("reader-order", "Markdown image appears before semantic section content")

    def validate_markdown_content_repair(self) -> None:
        path = self.root / "reviews" / "markdown-content-repair.yaml"
        if not path.exists():
            return
        try:
            data = _load(path)
        except Exception as exc:
            self.error("markdown-content-repair", f"cannot parse reviews/markdown-content-repair.yaml: {exc}")
            return
        if not isinstance(data, dict):
            self.error("markdown-content-repair", "reviews/markdown-content-repair.yaml must be a mapping")
            return
        if data.get("contract") != "markdown-content-repair":
            self.error("markdown-content-repair", "review contract must be markdown-content-repair")
        if data.get("status") not in {"applied", "skipped", "rejected", "deferred"}:
            self.error("markdown-content-repair", "review status is invalid")
        items = data.get("items")
        if not isinstance(items, list):
            self.error("markdown-content-repair", "review items must be a list")
            return
        operations = {
            "none",
            "join-split-text",
            "restore-sentence-break",
            "restore-paragraph-break",
            "restore-list-structure",
            "restore-heading-structure",
            "restore-text-order",
            "local-punctuation",
            "remove-duplicate-fragment",
            "page-unit-structure-repair",
        }
        bases = {"markdown-context", "extracted-text", "cross-source-agreement", "human-confirmation", "none"}
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                self.error("markdown-content-repair", f"items[{index}] must be a mapping")
                continue
            for key in ("id", "kind", "section_id", "source_text", "candidate_text", "proposed_text", "status"):
                if key not in item:
                    self.error("markdown-content-repair", f"items[{index}] missing {key}")
            if item.get("kind") not in {"heading", "paragraph", "list", "table", "punctuation", "duplicate-fragment"}:
                self.error("markdown-content-repair", f"items[{index}] has invalid kind")
            if item.get("status") not in {"applied", "skipped", "rejected", "deferred"}:
                self.error("markdown-content-repair", f"items[{index}] has invalid status")
            source_occurrence = item.get("source_occurrence")
            if source_occurrence is not None and source_occurrence not in self.occurrence_ids:
                self.error("markdown-content-repair", f"items[{index}] references unknown source_occurrence")
            if item.get("section_id") not in self.section_ids:
                self.error("markdown-content-repair", f"items[{index}] references unknown section_id")
            repair = item.get("repair")
            if repair is None:
                continue
            if not isinstance(repair, dict):
                self.error("markdown-content-repair", f"items[{index}].repair must be a mapping")
                continue
            operation = repair.get("operation")
            if operation not in operations:
                self.error("markdown-content-repair", f"items[{index}].repair has invalid operation")
            if repair.get("basis") not in bases:
                self.error("markdown-content-repair", f"items[{index}].repair has invalid basis")
            if operation != "none":
                if not isinstance(item.get("proposed_text"), str) or not item["proposed_text"].strip():
                    self.error("markdown-content-repair", f"items[{index}].repair needs proposed_text")
                if not isinstance(repair.get("before"), str) or not isinstance(repair.get("after"), str):
                    self.error("markdown-content-repair", f"items[{index}].repair needs before and after")

    def validate_processing(self, processing: Any, manifest_status: Any) -> None:
        data = _mapping(processing)
        if data is None:
            self.error("processing.required", "processing must be a mapping")
            return
        for key in ("native_extraction", "ocr_used", "visual_review", "execution_path", "fallback_reason", "status", "stage_evidence", "stage_evidence_contract", "failure_report"):
            if key not in data:
                self.error("processing.required", f"processing missing {key}")
        if manifest_status and data.get("status") and data["status"] != manifest_status:
            self.error("status-consistency", f"manifest status {manifest_status} differs from processing status {data['status']}")
        report_path = data.get("failure_report")
        if report_path != "reports/failure-report.yaml":
            self.error("report-link", "manifest.processing.failure_report must be reports/failure-report.yaml")
        else:
            self.package_path(report_path, "processing.failure_report")
        self.validate_stage_evidence(data.get("stage_evidence"), "manifest.processing.stage_evidence")

    def validate_page_output_pairs(
        self,
        manifest: dict[str, Any],
        source: dict[str, Any] | None,
        processing: dict[str, Any] | None,
    ) -> None:
        """Require the active page-vision runner's JSON/Markdown triplets."""

        page_vision_paths = {
            "mac96-direct-vision",
            "mac96-with-conversation-online-fallback",
            "conversation-online-vision",
        }
        if not processing or processing.get("execution_path") not in page_vision_paths:
            return
        selected_pages = (source or {}).get("selected_pages") or (source or {}).get("selected_original_pages")
        if not isinstance(selected_pages, list) or not selected_pages:
            self.error("page-output-contract", "page-scoped vision requires source.selected_pages")
            return
        declared_status = manifest.get("status")
        for raw_page in selected_pages:
            if not isinstance(raw_page, int) or raw_page < 1:
                self.error("page-output-contract", f"invalid selected page: {raw_page}")
                continue
            analysis_path = self.root / "evidence" / f"vision-page-analysis-p{raw_page:03d}.json"
            transcription_path = self.root / "evidence" / f"vision-text-transcription-p{raw_page:03d}.json"
            unit_path = self.root / "page-units" / f"p{raw_page:03d}.md"
            for path in (analysis_path, transcription_path, unit_path):
                if not path.is_file():
                    self.error("page-output-contract", f"page {raw_page} missing required projection: {path.relative_to(self.root)}")
            if not analysis_path.is_file() or not transcription_path.is_file() or not unit_path.is_file():
                continue
            try:
                analysis = _load(analysis_path)
                transcription = _load(transcription_path)
                unit_text = unit_path.read_text(encoding="utf-8")
            except Exception as exc:
                self.error("page-output-contract", f"page {raw_page} projection cannot be read: {exc}")
                continue
            if not isinstance(analysis, dict) or not isinstance(transcription, dict):
                self.error("page-output-contract", f"page {raw_page} JSON projections must be mappings")
                continue
            is_divider = _is_divider_page_unit(unit_text)
            expected_asset_id = f"page-p{raw_page:03d}"
            asset = self.asset_by_id.get(expected_asset_id)
            if analysis.get("input_asset_id") != expected_asset_id:
                self.error("page-output-contract", f"page {raw_page} analysis input_asset_id is inconsistent")
            if transcription.get("input_asset_id") != expected_asset_id:
                self.error("page-output-contract", f"page {raw_page} transcription input_asset_id is inconsistent")
            if source and analysis.get("source_fingerprint") != source.get("source_fingerprint"):
                self.error("page-output-contract", f"page {raw_page} analysis source fingerprint is inconsistent")
            if source and transcription.get("source_fingerprint") != source.get("source_fingerprint"):
                self.error("page-output-contract", f"page {raw_page} transcription source fingerprint is inconsistent")
            if asset:
                asset_path = self.package_path(
                    asset.get("file_path"),
                    f"page {raw_page} source image",
                    must_exist=asset.get("status") != "unavailable" and asset.get("file_path") not in self.pruned_asset_paths,
                )
                image_hash = analysis.get("image_sha256")
                if asset_path is not None and asset_path.is_file() and isinstance(image_hash, str) and image_hash != _sha256(asset_path):
                    self.error("page-output-contract", f"page {raw_page} analysis image hash differs from source asset")
            if declared_status == "complete":
                strategy = analysis.get("pass_strategy_used")
                expected_calls = 1 if strategy == "single" else 2 if strategy == "dual" else None
                if analysis.get("status") != "complete" or expected_calls is None or analysis.get("model_call_count") != expected_calls:
                    self.error("page-output-contract", f"complete package page {raw_page} lacks a complete single/dual analysis record")
                if transcription.get("status") != "complete" or not str(transcription.get("candidate_markdown") or "").strip():
                    self.error("page-output-contract", f"complete package page {raw_page} lacks complete text transcription")
            visual = analysis.get("visual") if isinstance(analysis, dict) else None
            visual_analysis = visual.get("visual_analysis") if isinstance(visual, dict) else None
            image_action = _reader_image_action(visual_analysis)
            has_image_link = f"../assets/page-{raw_page:03d}.png" in unit_text
            if not unit_text.strip():
                self.error("page-output-contract", f"page {raw_page} Markdown unit is empty")
            elif is_divider:
                approved_deletion = bool(
                    asset
                    and asset.get("asset_id") in self.pruned_asset_ids
                    and asset.get("file_path") in self.pruned_asset_paths
                    and raw_page in self.pruned_pages
                )
                if not approved_deletion:
                    self.error("page-output-contract", f"page {raw_page} divider image deletion lacks an approved pruning record")
                elif (self.root / str(asset.get("file_path"))).exists():
                    self.error("visual-asset-pruning", f"page {raw_page} deletion report conflicts with an existing source asset")
                if has_image_link:
                    self.error("page-output-contract", f"page {raw_page} divider Markdown must not retain its page image link")
            elif raw_page in self.pruned_pages or (asset is not None and asset.get("file_path") in self.pruned_asset_paths):
                self.error("page-output-contract", f"page {raw_page} non-divider cannot use a divider image deletion approval")
            elif asset is not None and asset.get("status") == "unavailable":
                self.error("page-output-contract", f"page {raw_page} non-divider source image cannot be unavailable")
            elif image_action == "omit" and has_image_link:
                self.error("page-output-contract", f"page {raw_page} pure-text Markdown unexpectedly retains its page image link")
            elif image_action != "omit" and not has_image_link:
                self.error("page-output-contract", f"page {raw_page} Markdown unit is missing its required page image link")

    def validate_stage_evidence(self, stages: Any, label: str) -> dict[str, dict[str, Any]]:
        if not isinstance(stages, list):
            self.error("stage-evidence", f"{label} must be a list")
            return {}
        result: dict[str, dict[str, Any]] = {}
        for index, stage in enumerate(stages):
            item = _mapping(stage)
            if item is None:
                self.error("stage-evidence", f"{label}[{index}] must be a mapping")
                continue
            for key in ("stage_id", "status", "evidence_level", "evidence_paths", "diagnostics", "verification_commands"):
                if key not in item:
                    self.error("stage-evidence", f"{label}[{index}] missing {key}")
            stage_id = item.get("stage_id")
            if stage_id in result:
                self.error("stage-evidence", f"{label} repeats {stage_id}")
            if stage_id not in STAGE_IDS:
                self.error("stage-evidence", f"{label} has invalid stage_id {stage_id}")
            elif stage_id in result:
                pass
            else:
                result[stage_id] = item
            if item.get("status") not in STAGE_STATUSES:
                self.error("stage-evidence", f"{label} {stage_id} has invalid status")
            if item.get("evidence_level") not in EVIDENCE_LEVELS:
                self.error("stage-evidence", f"{label} {stage_id} has invalid evidence_level")
            paths = item.get("evidence_paths")
            if not isinstance(paths, list) or not paths:
                self.error("stage-evidence", f"{label} {stage_id} needs evidence_paths")
            else:
                for evidence_path in paths:
                    if not isinstance(evidence_path, str):
                        self.error("stage-evidence", f"{label} {stage_id} has non-string evidence path")
                    else:
                        self.evidence_path(evidence_path.strip(), f"{label} {stage_id} evidence")
            commands = item.get("verification_commands")
            if not isinstance(commands, list) or not commands or not all(isinstance(command, str) and command.strip() for command in commands):
                self.error("stage-evidence", f"{label} {stage_id} needs a verification command")
            for key in ("diagnostics", "unknown", "score_basis"):
                if item.get("status") == "complete" and key not in item:
                    self.error("stage-evidence", f"complete {label} {stage_id} missing {key}")
        if set(result) != set(STAGE_IDS):
            self.error("stage-evidence", f"{label} must cover exactly A1-A7")
        return result

    def validate_reports(self, manifest: Any, processing_report: Any, acceptance: Any, failure: Any) -> None:
        manifest_data = _mapping(manifest) or {}
        processing_data = _mapping(processing_report)
        acceptance_data = _mapping(acceptance)
        failure_data = _mapping(failure)
        if processing_data is None:
            self.error("processing-report", "processing-report.yaml must contain a mapping")
        if acceptance_data is None:
            self.error("acceptance-report", "acceptance-report.yaml must contain a mapping")
        if failure_data is None:
            self.error("failure-report", "failure-report.yaml must contain a mapping")
        if processing_data is None or acceptance_data is None or failure_data is None:
            return
        for key in ("status", "evidence_level", "stage_evidence"):
            if key not in processing_data:
                self.error("processing-report", f"processing report missing {key}")
        for key in ("status", "evidence_level", "stage_evidence", "failures"):
            if key not in acceptance_data:
                self.error("acceptance-report", f"acceptance report missing {key}")
        for key in ("status", "evidence_level", "failures", "warnings", "diagnostics"):
            if key not in failure_data:
                self.error("failure-report", f"failure report missing {key}")
        processing_stages = self.validate_stage_evidence(processing_data.get("stage_evidence"), "processing-report.stage_evidence")
        acceptance_stages = self.validate_stage_evidence(acceptance_data.get("stage_evidence"), "acceptance-report.stage_evidence")
        manifest_stages = self.validate_stage_evidence((_mapping(manifest_data.get("processing")) or {}).get("stage_evidence"), "manifest.processing.stage_evidence")
        self.compare_stage_maps(manifest_stages, processing_stages, "manifest vs processing report")
        self.compare_stage_maps(manifest_stages, acceptance_stages, "manifest vs acceptance report")
        failure_status = failure_data.get("status")
        if failure_status not in FAILURE_STATUSES:
            self.error("failure-report", "failure report status is invalid")
        failures = failure_data.get("failures")
        if not isinstance(failures, list):
            self.error("failure-report", "failure report failures must be a list")
            failures = []
        failure_ids: list[str] = []
        for index, item in enumerate(failures):
            self.validate_failure(index, item)
            if isinstance(item, dict) and isinstance(item.get("failure_id"), str):
                failure_ids.append(item["failure_id"])
        if failure_status == "none" and failures:
            self.error("failure-report", "status=none requires failures=[]")
        if failure_status != "none" and not failures:
            self.error("failure-report", "non-none failure status requires at least one failure")
        acceptance_failures = acceptance_data.get("failures")
        if not isinstance(acceptance_failures, list):
            self.error("acceptance-report", "acceptance failures must be a list")
            acceptance_failures = []
        if sorted(acceptance_failures) != sorted(failure_ids):
            self.error("status-consistency", "acceptance failures do not match failure-report failure IDs")
        acceptance_status = acceptance_data.get("status")
        if acceptance_status not in {"passed", "needs-review", "failed"}:
            self.error("acceptance-report", "acceptance status is invalid")
        manifest_status = manifest_data.get("status")
        if acceptance_status == "passed" and manifest_status != "complete":
            self.error("status-consistency", "acceptance passed requires manifest status complete")
        if manifest_status == "complete" and acceptance_status != "passed":
            self.error("status-consistency", "manifest complete requires acceptance passed")
        if acceptance_status == "passed" and failure_status != "none":
            self.error("status-consistency", "acceptance passed is incompatible with failure-report failures")
        if acceptance_status == "passed" and (_mapping(manifest_data.get("validation")) or {}).get("status") != "passed":
            self.error("status-consistency", "acceptance passed requires manifest.validation.status passed")
        if acceptance_status == "passed":
            if any(stage.get("status") != "complete" for stage in manifest_stages.values()):
                self.error("status-consistency", "acceptance passed requires every A1-A7 stage to be complete")
            reconciliation_path = self.root / "evidence/reconciliation.json"
            if not reconciliation_path.is_file():
                self.error("unknown-form-coverage", "acceptance passed requires evidence/reconciliation.json")
            else:
                try:
                    reconciliation = _load(reconciliation_path)
                except Exception as exc:
                    self.error("unknown-form-coverage", f"cannot parse evidence/reconciliation.json: {exc}")
                    reconciliation = None
                if not isinstance(reconciliation, dict):
                    self.error("unknown-form-coverage", "reconciliation evidence must be a mapping")
                else:
                    if "unknown_forms" not in reconciliation or "coverage_notes" not in reconciliation:
                        self.error("unknown-form-coverage", "reconciliation must include unknown_forms and coverage_notes")
                    if reconciliation.get("status") != "complete" or reconciliation.get("unresolved"):
                        self.error("unknown-form-coverage", "acceptance passed requires complete reconciliation with no unresolved items")
        report_failure_path = processing_data.get("failure_report")
        if report_failure_path is not None and report_failure_path != "reports/failure-report.yaml":
            self.error("report-link", "processing-report.failure_report must be reports/failure-report.yaml")
        acceptance_failure_path = acceptance_data.get("failure_report")
        if acceptance_failure_path is not None and acceptance_failure_path != "reports/failure-report.yaml":
            self.error("report-link", "acceptance-report.failure_report must be reports/failure-report.yaml")
        self.validate_forbidden_fields(failure_data)

    def compare_stage_maps(self, left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]], label: str) -> None:
        if set(left) != set(right):
            self.error("status-consistency", f"{label} stage IDs differ")
            return
        for stage_id in STAGE_IDS:
            left_item = left.get(stage_id, {})
            right_item = right.get(stage_id, {})
            for key in ("status", "evidence_level", "evidence_paths", "verification_commands"):
                if left_item.get(key) != right_item.get(key):
                    self.error("status-consistency", f"{label} {stage_id} {key} differs")

    def validate_failure(self, index: int, failure: Any) -> None:
        item = _mapping(failure)
        if item is None:
            self.error("failure-report", f"failures[{index}] must be a mapping")
            return
        for key in ("failure_id", "stage_id", "code", "severity", "retryable", "status", "message", "evidence_paths", "remediation"):
            if key not in item:
                self.error("failure-report", f"failures[{index}] missing {key}")
        if not isinstance(item.get("failure_id"), str) or not item.get("failure_id"):
            self.error("failure-report", f"failures[{index}] has invalid failure_id")
        if item.get("stage_id") not in STAGE_IDS:
            self.error("failure-report", f"failures[{index}] has invalid stage_id")
        if item.get("code") not in FAILURE_CODES:
            self.error("failure-report", f"failures[{index}] has invalid code")
        if item.get("severity") not in FAILURE_SEVERITIES:
            self.error("failure-report", f"failures[{index}] has invalid severity")
        if item.get("status") not in {"open", "acknowledged", "resolved", "blocked"}:
            self.error("failure-report", f"failures[{index}] has invalid status")
        paths = item.get("evidence_paths")
        if not isinstance(paths, list) or not paths:
            self.error("failure-report", f"failures[{index}] needs evidence_paths")
        else:
            for path in paths:
                if isinstance(path, str):
                    self.evidence_path(path, f"failure {item.get('failure_id')} evidence")
                else:
                    self.error("failure-report", f"failures[{index}] has non-string evidence path")
        if not isinstance(item.get("remediation"), str) or not item["remediation"].strip():
            self.error("failure-report", f"failures[{index}] needs remediation")
        if item.get("status") == "resolved" and not item.get("resolved_evidence_paths"):
            self.error("failure-report", f"resolved failure {item.get('failure_id')} needs resolved_evidence_paths")

    def validate_forbidden_fields(self, value: Any, path: str = "failure-report") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in FORBIDDEN_REPORT_FIELDS:
                    self.error("secret-field", f"{path} contains forbidden field {key}")
                self.validate_forbidden_fields(nested, f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                self.validate_forbidden_fields(nested, f"{path}[{index}]")

    def run(self) -> ValidationResult:
        if (self.root / "manifest.yaml").is_file() is False and (self.root / "index.md").is_file():
            self.validate_reader_output()
            return ValidationResult(self.root, self.errors, self.warnings, "complete")
        self.validate_layout()
        self.load_visual_asset_pruning()
        manifest = self.load_document("manifest.yaml")
        processing = self.load_document("reports/processing-report.yaml")
        acceptance = self.load_document("reports/acceptance-report.yaml")
        failure = self.load_document("reports/failure-report.yaml")
        declared_status = manifest.get("status") if isinstance(manifest, dict) else None
        if manifest is not None:
            self.validate_manifest(manifest)
            self.validate_markdown_content_repair()
        if manifest is not None or processing is not None or acceptance is not None or failure is not None:
            self.validate_reports(manifest, processing, acceptance, failure)
        return ValidationResult(self.root, self.errors, self.warnings, declared_status)


def validate_package(package_root: str | Path) -> ValidationResult:
    """Return deterministic validation evidence for one package directory."""

    return _Validator(Path(package_root)).run()


def validate_output_root(output_root: str | Path, package_name: str | None = None) -> ValidationResult:
    """Validate ``output_root/package_name`` (or a direct package path)."""

    return validate_package(resolve_package_root(output_root, package_name))


validate_real_output_root = validate_output_root


def resolve_package_root(output_root: str | Path, package_name: str | None = None) -> Path:
    root = Path(output_root)
    if package_name:
        return root / package_name
    if (root / "manifest.yaml").is_file():
        return root
    if (root / "index.md").is_file():
        return root
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and ((path / "manifest.yaml").is_file() or (path / "index.md").is_file())
    ) if root.is_dir() else []
    return candidates[0] if len(candidates) == 1 else root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", nargs="?", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--package-name")
    args = parser.parse_args(argv)
    package_root = args.package_root or args.output_root
    if package_root is None:
        parser.error("provide PACKAGE_ROOT or --output-root")
    result = validate_package(resolve_package_root(package_root, args.package_name))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
