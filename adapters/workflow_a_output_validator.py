#!/usr/bin/env python3
"""Independent checks for a real Workflow A package.

The validator re-reads files and recomputes links, media metadata, hashes,
stage evidence, and A4 reconciliation status. It does not trust package status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml
from PIL import Image


REQUIRED_FILES = [
    "index.md",
    "manifest.yaml",
    "reports/processing-report.yaml",
    "reports/acceptance-report.yaml",
    "reports/failure-report.yaml",
]
REQUIRED_DIRS = ["assets"]
LEGACY_DIRS = ("assets/pages", "assets/regions", "assets/embedded")
STAGES = {f"A{i}" for i in range(1, 8)}
FLAT_ASCII_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:-\d{2})?\.[a-z0-9]+$")
FLAT_NAME_FORBIDDEN_RE = re.compile(
    r"page-render|original-page|source-media|"
    r"embedded-media|complex-region|__|o\d{2}(?:\.|$)"
)
LEGACY_ASCII_NAME_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*__p\d{3}__"
    r"(?:page-render|embedded-media|complex-region|crop|input-image)__"
    r"[a-z0-9]+(?:-[a-z0-9]+)*__[a-z0-9]+(?:-[a-z0-9]+)*__o\d{2}\.[a-z0-9]+$"
)
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


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def validate_package(package_root: Path) -> dict:
    root = package_root.resolve()
    failures: list[dict] = []
    checks: list[dict] = []

    def passed(check_id: str, evidence: list[str] | None = None):
        checks.append({"id": check_id, "status": "passed", "evidence": evidence or []})

    def failed(check_id: str, message: str, stage: str, evidence: list[str] | None = None):
        checks.append({"id": check_id, "status": "failed", "evidence": evidence or [], "message": message})
        failures.append({"code": check_id, "stage_id": stage, "severity": "P1", "message": message, "evidence_paths": evidence or []})

    missing_files = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    missing_dirs = [name for name in REQUIRED_DIRS if not (root / name).is_dir()]
    if missing_files:
        failed("required-files", f"missing files: {missing_files}", "A7", missing_files)
    else:
        passed("required-files", REQUIRED_FILES)
    if missing_dirs:
        failed("required-directories", f"missing directories: {missing_dirs}", "A7", missing_dirs)
    else:
        passed("required-directories", REQUIRED_DIRS)
    if missing_files:
        return {"validator": "adapters/workflow_a_output_validator.py", "status": "failed", "checks": checks, "failures": failures}
    if missing_dirs:
        return {"validator": "adapters/workflow_a_output_validator.py", "status": "failed", "checks": checks, "failures": failures}

    manifest = load(root / "manifest.yaml") or {}
    flat_layout = manifest.get("output_layout") == "flat-assets-v1"
    if flat_layout:
        legacy_dirs = [name for name in LEGACY_DIRS if (root / name).exists()]
        nested_dirs = [path.relative_to(root).as_posix() for path in (root / "assets").rglob("*") if path.is_dir()]
        if legacy_dirs or nested_dirs:
            failed("asset-layout", f"flat package contains legacy/nested asset directories: {legacy_dirs + nested_dirs}", "A3", ["manifest.yaml", "assets"])
        else:
            passed("asset-layout", ["manifest.yaml", "assets"])
    processing = load(root / "reports/processing-report.yaml") or {}
    acceptance = load(root / "reports/acceptance-report.yaml") or {}
    failure_report = load(root / "reports/failure-report.yaml") or {}
    assets = manifest.get("assets", [])
    asset_paths = {item.get("file_path") for item in assets if isinstance(item, dict)}
    processing_input = (processing.get("input") or {}) if isinstance(processing, dict) else {}
    source_path = processing_input.get("source") or (manifest.get("source") or {}).get("path")
    source_hash = processing_input.get("sha256") or (manifest.get("source") or {}).get("source_fingerprint")
    if not source_path or not source_hash:
        failed("source-provenance", "package does not record source absolute path and SHA-256", "A1", ["manifest.yaml", "reports/processing-report.yaml"])
    else:
        source_file = Path(source_path)
        if not source_file.is_file():
            failed("source-provenance", f"source path is not readable: {source_path}", "A1", [str(source_path)])
        elif digest(source_file) != source_hash:
            failed("source-provenance", "recorded source SHA-256 does not match the current input", "A1", [str(source_file)])
        else:
            passed("source-provenance", [str(source_file)])
    if (manifest.get("source") or {}).get("source_selection") != "single-source":
        failed("source-selection", "real package must explicitly record source_selection=single-source", "A1", ["manifest.yaml"])
    else:
        passed("source-selection", ["manifest.yaml"])
    source_pages = (manifest.get("source") or {}).get("selected_pages") or (manifest.get("source") or {}).get("selected_original_pages") or []
    if not source_pages:
        failed("page-selection", "manifest does not record selected source pages", "A1", ["manifest.yaml"])
    else:
        page_asset_pages = {
            page
            for item in assets
            if isinstance(item, dict) and item.get("asset_kind") in {"page-render", "input-image"}
            for occurrence in ((item.get("source") or {}).get("source_occurrences") or [])
            for page in [occurrence.get("page")]
            if isinstance(page, int)
        }
        if len(source_pages) > 1 and set(source_pages) != page_asset_pages:
            failed("page-coverage", f"page assets {sorted(page_asset_pages)} do not cover selected pages {source_pages}", "A3", ["manifest.yaml", "evidence/rendering.json"])
        else:
            passed("page-coverage", ["manifest.yaml", "evidence/rendering.json"])
    if not assets:
        failed("manifest-assets", "manifest.assets is empty", "A6", ["manifest.yaml"])
    else:
        passed("manifest-assets", ["manifest.yaml"])
    asset_ids = [item.get("asset_id") for item in assets if isinstance(item, dict)]
    asset_file_names = [item.get("file_name") for item in assets if isinstance(item, dict)]
    if len(asset_ids) != len(set(asset_ids)):
        failed("asset-id-uniqueness", "manifest contains duplicate asset_id values", "A6", ["manifest.yaml"])
    if len(asset_file_names) != len(set(asset_file_names)):
        failed("asset-name-uniqueness", "manifest contains duplicate file_name values", "A6", ["manifest.yaml"])
    for asset in assets:
        asset_id = asset.get("asset_id", "<missing>")
        relative = asset.get("file_path")
        path = (root / relative).resolve() if isinstance(relative, str) else None
        if path is None or not path.is_file() or root not in path.parents:
            failed("asset-file", f"asset {asset_id} path is missing or escapes package", "A3", [str(relative)])
            continue
        name_pattern = FLAT_ASCII_NAME_RE if flat_layout else LEGACY_ASCII_NAME_RE
        file_name = asset.get("file_name", "")
        if not name_pattern.fullmatch(file_name):
            failed("asset-naming", f"asset {asset_id} has unstable file_name", "A6", [str(relative)])
        if flat_layout and FLAT_NAME_FORBIDDEN_RE.search(file_name):
            failed("asset-naming", f"asset {asset_id} leaks page or engineering fields in file_name", "A6", [str(relative)])
        if flat_layout and path.parent != root / "assets":
            failed("asset-layout", f"asset {asset_id} is not directly under assets/", "A3", [str(relative)])
        occurrences = (asset.get("source") or {}).get("source_occurrences") or []
        if not occurrences or any(item.get("status") == "unknown" for item in occurrences):
            failed("asset-provenance", f"asset {asset_id} has incomplete source occurrences", "A6", [str(relative)])
        try:
            media = asset.get("media") or {}
            if str(media.get("mime_type", "")).startswith("image/"):
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    width, height = image.size
                    format_name = image.format
                actual_mime = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(format_name, mimetypes.guess_type(path.name)[0])
                if media.get("mime_type") != actual_mime or media.get("width") != width or media.get("height") != height:
                    failed("asset-media-metadata", f"asset {asset_id} media metadata mismatch", "A3", [str(relative)])
            if media.get("sha256") != digest(path):
                failed("asset-sha256", f"asset {asset_id} sha256 mismatch", "A6", [str(relative)])
        except Exception as exc:
            failed("asset-readable", f"asset {asset_id} cannot be read: {exc}", "A3", [str(relative)])
    document_text = (root / "index.md").read_text(encoding="utf-8")
    forbidden_reader_fields = ("标题下正文文字块", "视觉关系", "来源与不确定性", "A4 状态", "native_bbox", "object_id", "模型调用")
    leaked_fields = [field for field in forbidden_reader_fields if field in document_text]
    if leaked_fields:
        failed("reader-layer-separation", f"index.md exposes engineering fields: {leaked_fields}", "A5", ["index.md"])
    else:
        passed("reader-layer-separation", ["index.md"])
    leaked_repair_markers = [marker for marker in READER_FORBIDDEN_MARKERS if marker in document_text]
    if leaked_repair_markers:
        failed("reader-layer-separation", f"index.md exposes unresolved repair markers: {leaked_repair_markers}", "A5", ["index.md"])
    else:
        passed("reader-repair-layer-separation", ["index.md"])
    # A page with a valid relation table but no title/body/OCR candidate is
    # not a usable Markdown conversion.  This check intentionally reads the
    # reader layer, so a relation-only mac96 response cannot satisfy A5.
    page_matches = list(re.finditer(r"(?m)^## 第 (\d+) 页：([^\n]*)\n", document_text))
    semantic_failures = []
    for index, match in enumerate(page_matches):
        page_number = int(match.group(1))
        title = match.group(2).strip()
        section_end = page_matches[index + 1].start() if index + 1 < len(page_matches) else len(document_text)
        section = document_text[match.end():section_end]
        section_without_image = re.split(r"(?m)^### 原始页面图", section, maxsplit=1)[0]
        has_body = bool(re.search(r"(?m)^### (?:主要内容|核心内容|主题|分区内容|步骤内容|四象限行动原则|四步复盘)\s*$", section_without_image))
        has_non_placeholder_title = bool(title) and not re.search(r"^待复核：第|^第\s*\d+\s*页$", title)
        has_relation = bool(re.search(r"(?m)^### 关系说明\s*$", section_without_image))
        if not has_non_placeholder_title or not has_body:
            semantic_failures.append({"page": page_number, "reason": "标题 fallback、正文/OCR 候选为空或页面只有关系说明" if has_relation else "标题 fallback 或正文/OCR 候选为空"})
    if semantic_failures:
        failed("reader-semantic-content", f"reader Markdown has semantically empty pages: {semantic_failures}", "A5", ["index.md", "evidence/content-classification.json", "evidence/ocr-candidates.json"])
    else:
        passed("reader-semantic-content", ["index.md"])
    rendering_path = root / "evidence/rendering.json"
    rendering = json.loads(rendering_path.read_text(encoding="utf-8")) if rendering_path.is_file() else {}
    if not rendering:
        failed("rendering-evidence", "missing source-owned rendering evidence", "A3", ["evidence/rendering.json"])
    elif rendering.get("status") != "complete" or rendering.get("source_owned") is False:
        failed("rendering-evidence", "source-owned rendering is incomplete", "A3", ["evidence/rendering.json"])
    else:
        passed("rendering-evidence", ["evidence/rendering.json"])
    visibility = rendering.get("text_visibility") or {}
    if visibility and visibility.get("status") not in {"complete", "not-applicable"}:
        failed("rendered-text-visibility", "native text regions are not visibly rendered", "A3", ["evidence/rendering.json"])
    else:
        passed("rendered-text-visibility", ["evidence/rendering.json"])
    links = [unquote(match.group(1) or match.group(2) or "").strip() for match in re.finditer(r"!\[[^]]*\]\((?:<([^>]+)>|([^)]+))\)", document_text)]
    broken = [link for link in links if link not in asset_paths or not (root / link).is_file()]
    if broken:
        failed("markdown-links", f"broken or unregistered links: {broken}", "A5", broken)
    else:
        passed("markdown-links", links)
    evidence = processing.get("stage_evidence") or (manifest.get("processing") or {}).get("stage_evidence") or []
    stage_ids = [item.get("stage_id") for item in evidence if isinstance(item, dict)]
    if set(stage_ids) != STAGES or len(stage_ids) != len(set(stage_ids)):
        failed("stage-evidence", "stage_evidence must cover A1-A7 exactly", "A7", ["reports/processing-report.yaml"])
    else:
        missing_evidence = [path for item in evidence for path in (item.get("evidence_paths") or []) if not (root / path).exists()]
        if missing_evidence:
            failed("stage-evidence-paths", f"missing evidence paths: {missing_evidence}", "A7", missing_evidence)
        else:
            passed("stage-evidence", ["reports/processing-report.yaml"])
    reconciliation_path = root / "evidence/reconciliation.json"
    try:
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8")) if reconciliation_path.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        reconciliation = {}
        failed("reconciliation-readable", f"cannot read reconciliation evidence: {exc}", "A4", [str(reconciliation_path)])
    if reconciliation.get("status") != "complete" or reconciliation.get("unresolved") or reconciliation.get("unknown_forms"):
        failed("unknown-form-coverage", "A4 reconciliation has unresolved or unknown forms", "A4", ["evidence/reconciliation.json"])
    else:
        passed("unknown-form-coverage", ["evidence/reconciliation.json"])
    if failure_report.get("status") == "none" and failure_report.get("failures") == []:
        passed("failure-report", ["reports/failure-report.yaml"])
    else:
        failed("failure-report", "failure report is not empty status=none", "A7", ["reports/failure-report.yaml"])
    if not failures and manifest.get("status") == "complete" and acceptance.get("status") == "passed":
        passed("status-consistency", ["manifest.yaml", "reports/acceptance-report.yaml"])
    else:
        failed("status-consistency", "package self-reported status is inconsistent", "A7", ["manifest.yaml", "reports/acceptance-report.yaml"])
    return {"validator": "adapters/workflow_a_output_validator.py", "version": "0.3.1", "package_root": str(root), "status": "passed" if not failures else "needs-review", "evidence_level": processing.get("evidence_level"), "checks": checks, "failures": failures, "warnings": [], "diagnostics": ["independent re-read of package files, source fingerprint, renderer evidence and media"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_root", type=Path)
    args = parser.parse_args()
    result = validate_package(args.package_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
