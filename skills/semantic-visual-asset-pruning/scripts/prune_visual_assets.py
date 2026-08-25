#!/usr/bin/env python3
"""Preview or apply semantic pruning of redundant reader-facing page images."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - package runtime dependency
    raise SystemExit("PyYAML is required to prune a Workflow A package") from exc


IMAGE_RE = re.compile(r"^!\[[^\]]*\]\((?:<([^>]+)>|([^)]*))\)\s*$")
LANGGRAPH_IMAGE_RE = re.compile(
    r"^\s*!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)\s*$"
)
REMOVABLE_REASONS = {"cover", "table-of-contents", "text-covered"}
COMPLEX_REASONS = {
    "diagram",
    "table",
    "flow",
    "color-coded-structure",
    "relation-dense-layout",
    "mixed-text-visual",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(part.strip()) for part in token.split("-", 1))
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise ValueError(f"invalid page number: {token}")
            pages.add(page)
    if not pages:
        raise ValueError("--exclude-pages must contain at least one page")
    return sorted(pages)


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"expected mapping: {path}")
    return value


def write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def page_from_asset(asset: dict) -> int | None:
    occurrences = ((asset.get("source") or {}).get("source_occurrences") or [])
    for occurrence in occurrences:
        if isinstance(occurrence.get("page"), int):
            return occurrence["page"]
    return None


def asset_records(
    root: Path,
    manifest: dict,
    pages: list[int],
    reason_map: dict[int, str] | None = None,
) -> tuple[list[dict], list[str]]:
    wanted = set(pages)
    records: list[dict] = []
    errors: list[str] = []
    for asset in manifest.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("asset_kind") != "page-render":
            continue
        page = page_from_asset(asset)
        if page not in wanted:
            continue
        path = root / str(asset.get("file_path", ""))
        if not path.is_file():
            errors.append(f"missing asset file: {asset.get('file_path')}")
            continue
        records.append(
            {
                "asset_id": asset.get("asset_id"),
                "page": page,
                "file_path": asset.get("file_path"),
                "sha256": sha256(path),
                "declared_sha256": ((asset.get("media") or {}).get("sha256")),
                "source_fingerprint": ((manifest.get("source") or {}).get("source_fingerprint")),
                "display_name": asset.get("display_name"),
                "source_asset_action": "retained-for-provenance",
                "source_occurrence_count": len(((asset.get("source") or {}).get("source_occurrences") or [])),
                "reason_code": (reason_map or {}).get(page, "user-approved-page-selection"),
                "confidence": "high",
                "evidence_paths": [
                    "index.md",
                    "manifest.yaml",
                    "evidence/content-classification.json",
                    "evidence/reconciliation.json",
                ],
            }
        )
    found = {record["page"] for record in records}
    errors.extend(f"page {page} has no registered page-render asset" for page in sorted(wanted - found))
    return records, errors


def build_report(
    root: Path,
    pages: list[int],
    mode: str,
    manifest: dict,
    document: str,
    reason_map: dict[int, str] | None = None,
) -> tuple[dict, list[dict], list[str]]:
    records, errors = asset_records(root, manifest, pages, reason_map)
    all_pages = sorted(
        {
            page_from_asset(asset)
            for asset in manifest.get("assets") or []
            if page_from_asset(asset) is not None
        }
    )
    report = {
        "schema": "visual-asset-pruning",
        "version": "0.1.0",
        "status": "complete" if not errors else "needs-review",
        "mode": mode,
        "source_fingerprint": ((manifest.get("source") or {}).get("source_fingerprint")),
        "selected_pages": all_pages,
        "excluded_pages": pages,
        "kept_pages": [page for page in all_pages if page not in pages],
        "rule": "remove-only-when-semantic-text-coverage-is-confirmed",
        "assets": records,
        "synchronization": {
            "document": "pending" if mode == "apply" and not errors else "not-run",
            "manifest_sections": "pending" if mode == "apply" and not errors else "not-run",
            "manifest_assets": "retained",
            "source_occurrences": "retained",
            "source_files": "retained",
        },
        "diagnostics": errors,
    }
    return report, records, errors


def apply(root: Path, manifest: dict, document: str, report: dict, records: list[dict]) -> None:
    excluded_paths = {record["file_path"] for record in records}
    original_assets = copy.deepcopy(manifest.get("assets") or [])
    original_asset_hashes = {record["file_path"]: record["sha256"] for record in records}
    missing_source_files = [path for path in excluded_paths if not (root / path).is_file()]
    if missing_source_files:
        raise ValueError(f"source assets disappeared before apply: {missing_source_files}")
    output: list[str] = []
    skip_blank = False
    for line in document.splitlines():
        image = IMAGE_RE.match(line.strip())
        target = unquote((image.group(1) or image.group(2) or "").strip()) if image else None
        if target in excluded_paths:
            skip_blank = True
            continue
        if skip_blank and not line.strip():
            skip_blank = False
            continue
        skip_blank = False
        output.append(line)
    (root / "index.md").write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")

    for section in ((manifest.get("markdown") or {}).get("sections") or []):
        ids = section.get("visual_asset_ids") or []
        section["visual_asset_ids"] = [asset_id for asset_id in ids if asset_id not in {record["asset_id"] for record in records}]
    manifest.setdefault("evidence", {})["visual_asset_pruning"] = "evidence/visual-asset-pruning.json"
    manifest.setdefault("processing", {})["visual_asset_pruning"] = "evidence/visual-asset-pruning.json"
    for stage in manifest.get("processing", {}).get("stage_evidence", []) or []:
        if stage.get("stage_id") in {"A5", "A6"}:
            paths = stage.setdefault("evidence_paths", [])
            if "evidence/visual-asset-pruning.json" not in paths:
                paths.append("evidence/visual-asset-pruning.json")
            diagnostics = stage.setdefault("diagnostics", [])
            if "visual asset semantic pruning applied" not in diagnostics:
                diagnostics.append("visual asset semantic pruning applied")
    if manifest.get("assets") != original_assets:
        raise ValueError("semantic pruning attempted to change manifest.assets")
    for path, expected_hash in original_asset_hashes.items():
        if sha256(root / path) != expected_hash:
            raise ValueError(f"source asset hash changed during apply: {path}")
    write_yaml(root / "manifest.yaml", manifest)

    processing_path = root / "reports/processing-report.yaml"
    if processing_path.is_file():
        processing = load_yaml(processing_path)
        for stage in processing.get("stage_evidence") or []:
            if stage.get("stage_id") in {"A5", "A6"}:
                paths = stage.setdefault("evidence_paths", [])
                if "evidence/visual-asset-pruning.json" not in paths:
                    paths.append("evidence/visual-asset-pruning.json")
                diagnostics = stage.setdefault("diagnostics", [])
                if "visual asset semantic pruning applied" not in diagnostics:
                    diagnostics.append("visual asset semantic pruning applied")
        write_yaml(processing_path, processing)
    acceptance_path = root / "reports/acceptance-report.yaml"
    if acceptance_path.is_file():
        acceptance = load_yaml(acceptance_path)
        checks = acceptance.setdefault("checks", [])
        if not any(item.get("id") == "visual-asset-pruning" for item in checks if isinstance(item, dict)):
            checks.append({"id": "visual-asset-pruning", "status": "passed", "evidence": ["evidence/visual-asset-pruning.json"]})
        for stage in acceptance.get("stage_evidence") or []:
            if stage.get("stage_id") in {"A5", "A6"}:
                paths = stage.setdefault("evidence_paths", [])
                if "evidence/visual-asset-pruning.json" not in paths:
                    paths.append("evidence/visual-asset-pruning.json")
                diagnostics = stage.setdefault("diagnostics", [])
                if "visual asset semantic pruning applied" not in diagnostics:
                    diagnostics.append("visual asset semantic pruning applied")
        write_yaml(acceptance_path, acceptance)

    report["synchronization"] = {
        "document": "updated",
        "manifest_sections": "updated",
        "manifest_assets": "retained",
        "source_occurrences": "retained",
        "source_files": "retained",
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"path must stay inside package_root: {path}") from exc


def resolve_report_path(root: Path, value: Path) -> Path:
    path = value.expanduser()
    if not path.is_absolute():
        path = root / path
    relative_to_root(root, path)
    return path.resolve()


def langgraph_markdown_paths(root: Path) -> list[Path]:
    paths = list((root / "page-units").glob("p[0-9][0-9][0-9].md"))
    paths.extend(root.glob("*.md"))
    return sorted({path.resolve() for path in paths if path.is_file()})


def langgraph_asset_records(root: Path) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    for path in sorted((root / "assets").glob("page-[0-9][0-9][0-9].png")):
        match = re.fullmatch(r"page-(\d{3})\.png", path.name)
        if not match:
            continue
        page = int(match.group(1))
        records[page] = {
            "page": page,
            "image_path": path.relative_to(root).as_posix(),
            "sha256": sha256(path),
        }
    return records


def image_target(line: str) -> str | None:
    match = LANGGRAPH_IMAGE_RE.fullmatch(line.rstrip("\r\n"))
    if not match:
        return None
    return unquote((match.group(1) or match.group(2) or "").strip())


def target_asset_path(document_path: Path, target: str) -> Path | None:
    if not target or "://" in target or target.startswith("data:"):
        return None
    return (document_path.parent / target).resolve()


def references_by_asset(
    document_path: Path, text: str, assets: dict[int, dict[str, Any]], root: Path
) -> dict[int, int]:
    absolute_assets = {
        page: (root / record["image_path"]).resolve() for page, record in assets.items()
    }
    counts: dict[int, int] = {page: 0 for page in assets}
    for line in text.splitlines(keepends=True):
        target = image_target(line)
        resolved = target_asset_path(document_path, target) if target else None
        for page, asset_path in absolute_assets.items():
            if resolved == asset_path:
                counts[page] += 1
    return counts


def langgraph_reference_inventory(
    root: Path, assets: dict[int, dict[str, Any]]
) -> tuple[dict[int, int], dict[str, dict[int, int]]]:
    totals: dict[int, int] = {page: 0 for page in assets}
    per_document: dict[str, dict[int, int]] = {}
    for path in langgraph_markdown_paths(root):
        counts = references_by_asset(path, path.read_text(encoding="utf-8"), assets, root)
        per_document[path.relative_to(root).as_posix()] = counts
        for page, count in counts.items():
            totals[page] += count
    return totals, per_document


def remove_asset_image_lines(
    document_path: Path,
    text: str,
    assets: dict[int, dict[str, Any]],
    root: Path,
) -> tuple[str, dict[int, int]]:
    absolute_assets = {
        page: (root / record["image_path"]).resolve() for page, record in assets.items()
    }
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    removed: dict[int, int] = {page: 0 for page in assets}
    skip_following_blank = False
    for line in lines:
        if skip_following_blank and not line.strip():
            skip_following_blank = False
            continue
        skip_following_blank = False
        target = image_target(line)
        resolved = target_asset_path(document_path, target) if target else None
        matched_page = next(
            (page for page, asset_path in absolute_assets.items() if resolved == asset_path),
            None,
        )
        if matched_page is not None:
            removed[matched_page] += 1
            skip_following_blank = True
            continue
        output.append(line)
    return "".join(output), removed


def classification_is_removable(decision: dict[str, Any]) -> bool:
    reasons = decision.get("reason_codes")
    return (
        decision.get("decision") == "candidate-remove"
        and isinstance(reasons, list)
        and bool(reasons)
        and set(reasons).issubset(REMOVABLE_REASONS)
        and decision.get("semantic_text_coverage") == "complete"
        and decision.get("visual_increment") == "none"
    )


def build_langgraph_plan(
    root: Path,
    classification_path: Path,
    *,
    delete_source_files: bool,
    selected_pages: list[int] | None = None,
) -> dict[str, Any]:
    classification = json.loads(classification_path.read_text(encoding="utf-8"))
    if not isinstance(classification, dict):
        raise ValueError("classification report must be a JSON object")
    decisions = classification.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("classification report is missing decisions")
    assets = langgraph_asset_records(root)
    if not assets:
        raise ValueError("LangGraph run has no page PNG assets")
    selection = set(selected_pages or assets)
    errors: list[str] = []
    decision_map: dict[int, dict[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, dict) or not isinstance(item.get("page"), int):
            continue
        page = item["page"]
        if page in selection:
            decision_map[page] = item
    missing_decisions = sorted(selection - set(decision_map))
    missing_assets = sorted(selection - set(assets))
    if missing_decisions:
        errors.append(f"classification missing pages: {missing_decisions}")
    if missing_assets:
        errors.append(f"assets missing pages: {missing_assets}")
    for page in sorted(selection & set(assets) & set(decision_map)):
        expected = decision_map[page].get("sha256")
        if not isinstance(expected, str) or expected != assets[page]["sha256"]:
            errors.append(f"classification asset hash mismatch: page {page}")

    removable_pages = sorted(
        page
        for page in selection & set(assets) & set(decision_map)
        if classification_is_removable(decision_map[page])
    )
    overall_reference_counts, _ = langgraph_reference_inventory(root, assets)
    total_reference_count = sum(overall_reference_counts.values())
    if delete_source_files and total_reference_count == 0:
        errors.append(
            "no Markdown page-image references found; cannot safely verify reader-layer synchronization before deleting LangGraph PNG assets"
        )
    missing_required_refs = sorted(
        page
        for page in selection & set(assets) & set(decision_map)
        if decision_map[page].get("decision") in {"retain", "crop-required", "needs-review"}
        and overall_reference_counts.get(page, 0) == 0
    )
    if missing_required_refs:
        errors.append(
            "retain-or-needs-review pages missing Markdown image references: "
            + ",".join(str(page) for page in missing_required_refs)
        )
    removable_assets = {page: assets[page] for page in removable_pages}
    markdown_locks: list[dict[str, Any]] = []
    references: dict[int, list[str]] = {page: [] for page in removable_pages}
    for path in langgraph_markdown_paths(root):
        before = path.read_text(encoding="utf-8")
        after, removed = remove_asset_image_lines(path, before, removable_assets, root)
        relative = path.relative_to(root).as_posix()
        for page, count in removed.items():
            if count:
                references[page].append(relative)
        markdown_locks.append(
            {
                "path": relative,
                "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                "expected_after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
                "removed_image_line_count": sum(removed.values()),
            }
        )

    decision_records: list[dict[str, Any]] = []
    for page in sorted(selection & set(assets)):
        decision = decision_map.get(page) or {
            "decision": "needs-review",
            "reason_codes": ["needs-review"],
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
        }
        eligible = classification_is_removable(decision)
        decision_records.append(
            {
                "page": page,
                "image_path": assets[page]["image_path"],
                "pre_delete_sha256": assets[page]["sha256"],
                "decision": decision.get("decision"),
                "reason_codes": decision.get("reason_codes") or ["needs-review"],
                "semantic_text_coverage": decision.get("semantic_text_coverage", "unknown"),
                "visual_increment": decision.get("visual_increment", "unknown"),
                "classification_source": decision.get("classification_source", "unknown"),
                "eligible_for_delete": eligible,
                "planned_action": (
                    "delete"
                    if eligible
                    else "crop-before-delete"
                    if decision.get("decision") == "crop-required"
                    else "retain"
                ),
                "markdown_reference_paths": references.get(page, []),
            }
        )
    retained_assets = [
        assets[page]
        for page in sorted(set(assets) - set(removable_pages))
    ]
    plan = {
        "schema": "langgraph-visual-asset-pruning-plan",
        "version": "0.1.0",
        "status": "ready" if not errors else "needs-review",
        "mode": "preview",
        "package_kind": "langgraph-simple",
        "package_root": str(root),
        "source_fingerprint": classification.get("source_fingerprint"),
        "classification_report": relative_to_root(root, classification_path),
        "classification_report_sha256": sha256(classification_path),
        "delete_source_files": delete_source_files,
        "selected_pages": sorted(selection),
        "counts": {
            "assets_before": len(assets),
            "delete": len(removable_pages),
            "retain": sum(record["decision"] == "retain" for record in decision_records),
            "crop_required": sum(record["decision"] == "crop-required" for record in decision_records),
            "needs_review": sum(record["decision"] == "needs-review" for record in decision_records),
        },
        "decisions": decision_records,
        "input_lock": {
            "candidate_assets": [assets[page] for page in removable_pages],
            "retained_assets": retained_assets,
            "markdown": markdown_locks,
        },
        "diagnostics": errors,
    }
    return plan


def apply_langgraph_plan(
    root: Path, plan_path: Path, *, delete_source_files: bool
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("schema") != "langgraph-visual-asset-pruning-plan":
        raise ValueError("not a LangGraph visual-asset pruning plan")
    if plan.get("status") != "ready":
        raise ValueError("pruning plan is not ready")
    if Path(str(plan.get("package_root"))).resolve() != root:
        raise ValueError("pruning plan package_root mismatch")
    if bool(plan.get("delete_source_files")) != delete_source_files:
        raise ValueError("--delete-source-files must match the hash-locked plan")
    classification_path = root / str(plan.get("classification_report"))
    if not classification_path.is_file() or sha256(classification_path) != plan.get("classification_report_sha256"):
        raise ValueError("classification report hash drifted after preview")

    lock = plan.get("input_lock") or {}
    candidate_assets = lock.get("candidate_assets") or []
    retained_assets = lock.get("retained_assets") or []
    markdown_locks = lock.get("markdown") or []
    candidate_map = {int(item["page"]): item for item in candidate_assets}
    for item in candidate_assets + retained_assets:
        path = root / str(item.get("image_path"))
        if not path.is_file() or sha256(path) != item.get("sha256"):
            raise ValueError(f"asset hash drifted after preview: {item.get('image_path')}")

    originals: dict[Path, bytes] = {}
    expected_markdown: dict[Path, bytes] = {}
    removed_by_document: dict[str, dict[int, int]] = {}
    for item in markdown_locks:
        path = root / str(item.get("path"))
        if not path.is_file() or sha256(path) != item.get("before_sha256"):
            raise ValueError(f"Markdown hash drifted after preview: {item.get('path')}")
        before = path.read_text(encoding="utf-8")
        after, removed = remove_asset_image_lines(path, before, candidate_map, root)
        after_bytes = after.encode("utf-8")
        if hashlib.sha256(after_bytes).hexdigest() != item.get("expected_after_sha256"):
            raise ValueError(f"Markdown projection drifted after preview: {item.get('path')}")
        originals[path] = path.read_bytes()
        expected_markdown[path] = after_bytes
        removed_by_document[str(item.get("path"))] = removed

    image_originals = {
        root / str(item["image_path"]): (root / str(item["image_path"])).read_bytes()
        for item in candidate_assets
    }
    before_count = len(langgraph_asset_records(root))
    report: dict[str, Any]
    try:
        modified_paths: list[str] = []
        for path, expected in expected_markdown.items():
            if originals[path] != expected:
                atomic_write_bytes(path, expected)
                modified_paths.append(path.relative_to(root).as_posix())
        if delete_source_files:
            for path in image_originals:
                path.unlink()

        deleted_records: list[dict[str, Any]] = []
        for decision in plan.get("decisions") or []:
            if not decision.get("eligible_for_delete"):
                continue
            page = int(decision["page"])
            image_path = root / str(decision["image_path"])
            changed = sorted(
                path
                for path, counts in removed_by_document.items()
                if counts.get(page, 0)
            )
            deleted_records.append(
                {
                    "page": page,
                    "image_path": decision["image_path"],
                    "pre_delete_sha256": decision["pre_delete_sha256"],
                    "reason_codes": decision["reason_codes"],
                    "markdown_modified_paths": changed,
                    "deletion_status": "deleted" if delete_source_files and not image_path.exists() else "not-deleted",
                }
            )

        deleted_abs = {(root / item["image_path"]).resolve() for item in deleted_records}
        remaining_references: list[str] = []
        for path in langgraph_markdown_paths(root):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                target = image_target(line)
                resolved = target_asset_path(path, target) if target else None
                if resolved in deleted_abs:
                    remaining_references.append(f"{path.relative_to(root)}:{line_number}")
        retained_missing = [
            str(item["image_path"])
            for item in retained_assets
            if not (root / str(item["image_path"])).is_file()
            or sha256(root / str(item["image_path"])) != item.get("sha256")
        ]
        markdown_exact = all(path.read_bytes() == expected for path, expected in expected_markdown.items())
        deleted_absent = all(not path.exists() for path in image_originals) if delete_source_files else True
        complex_retained_missing = [
            str(decision.get("image_path"))
            for decision in plan.get("decisions") or []
            if decision.get("planned_action") in {"retain", "crop-before-delete"}
            and set(decision.get("reason_codes") or []).intersection(COMPLEX_REASONS)
            and not (root / str(decision.get("image_path"))).is_file()
        ]
        after_count = len(langgraph_asset_records(root))
        checks = {
            "deleted_assets_absent": deleted_absent,
            "deleted_asset_references_absent": not remaining_references,
            "retained_assets_exist_and_match": not retained_missing,
            "markdown_non_image_content_unchanged": markdown_exact,
            "complex_visual_assets_retained": not complex_retained_missing,
            "image_count_delta_matches": before_count - after_count == (len(candidate_assets) if delete_source_files else 0),
        }
        status = "passed" if all(checks.values()) else "failed"
        report = {
            "schema": "langgraph-visual-asset-pruning-result",
            "version": "0.1.0",
            "status": status,
            "mode": "applied",
            "package_root": str(root),
            "source_fingerprint": plan.get("source_fingerprint"),
            "plan_path": relative_to_root(root, plan_path),
            "plan_sha256": sha256(plan_path),
            "classification_report": plan.get("classification_report"),
            "classification_report_sha256": plan.get("classification_report_sha256"),
            "counts": {
                "images_before": before_count,
                "images_after": after_count,
                "deleted": len(deleted_records) if delete_source_files else 0,
                "retained": sum(item.get("decision") == "retain" for item in plan.get("decisions") or []),
                "crop_required": sum(item.get("decision") == "crop-required" for item in plan.get("decisions") or []),
                "needs_review": sum(item.get("decision") == "needs-review" for item in plan.get("decisions") or []),
            },
            "deleted_assets": deleted_records,
            "retained_pages": [
                item["page"] for item in plan.get("decisions") or [] if item.get("decision") == "retain"
            ],
            "crop_required_pages": [
                item["page"] for item in plan.get("decisions") or [] if item.get("decision") == "crop-required"
            ],
            "needs_review_pages": [
                item["page"] for item in plan.get("decisions") or [] if item.get("decision") == "needs-review"
            ],
            "markdown_modified_paths": modified_paths,
            "verification": {
                "checks": checks,
                "remaining_deleted_asset_references": remaining_references,
                "retained_missing_or_changed": retained_missing,
                "complex_retained_missing": complex_retained_missing,
            },
            "diagnostics": [] if status == "passed" else ["one or more post-delete checks failed"],
        }
        if status != "passed":
            raise ValueError("post-delete verification failed")
    except Exception as exc:
        for path, value in originals.items():
            atomic_write_bytes(path, value)
        for path, value in image_originals.items():
            if not path.exists():
                atomic_write_bytes(path, value)
        report = {
            "schema": "langgraph-visual-asset-pruning-result",
            "version": "0.1.0",
            "status": "failed",
            "mode": "rolled-back",
            "package_root": str(root),
            "plan_path": relative_to_root(root, plan_path),
            "plan_sha256": sha256(plan_path),
            "counts": {"images_before": before_count, "images_after": len(langgraph_asset_records(root)), "deleted": 0},
            "deleted_assets": [],
            "diagnostics": [str(exc)],
        }
    return report


def parse_reason_map(values: list[str]) -> dict[int, str]:
    allowed = {"cover", "table-of-contents", "text-covered", "user-approved-page-selection", "needs-review"}
    result: dict[int, str] = {}
    for value in values:
        page_text, separator, reason = value.partition("=")
        if not separator or not page_text.isdigit() or int(page_text) < 1 or reason not in allowed:
            raise ValueError(f"invalid --reason mapping {value!r}; use PAGE=REASON")
        result[int(page_text)] = reason
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--exclude-pages", help="page numbers/ranges, e.g. 1,2,23-27")
    parser.add_argument("--reason", action="append", default=[], metavar="PAGE=REASON", help="optional reason override")
    parser.add_argument("--apply", action="store_true", help="apply the approved pruning")
    parser.add_argument(
        "--classification-report",
        type=Path,
        help="mac96 candidate report for a simple LangGraph run",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("evidence/visual-asset-pruning-plan.json"),
        help="hash-locked preview plan path for a simple LangGraph run",
    )
    parser.add_argument(
        "--apply-plan",
        type=Path,
        help="apply an existing hash-locked LangGraph pruning plan",
    )
    parser.add_argument(
        "--delete-source-files",
        action="store_true",
        help="physically delete selected LangGraph derived page PNGs; must be set in preview and apply",
    )
    args = parser.parse_args()
    root = args.package_root.expanduser().resolve()
    if args.apply_plan:
        if args.apply or args.classification_report or args.exclude_pages:
            parser.error("--apply-plan cannot be combined with --apply, --classification-report, or --exclude-pages")
        plan_path = resolve_report_path(root, args.apply_plan)
        try:
            report = apply_langgraph_plan(
                root, plan_path, delete_source_files=args.delete_source_files
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            report = {
                "schema": "langgraph-visual-asset-pruning-result",
                "version": "0.1.0",
                "status": "failed",
                "mode": "not-applied",
                "package_root": str(root),
                "diagnostics": [str(exc)],
            }
        report_path = root / "evidence/visual-asset-pruning.json"
        write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("status") == "passed" else 1

    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        if args.apply:
            parser.error("simple LangGraph runs require preview first, then --apply-plan")
        if not args.classification_report:
            parser.error("simple LangGraph runs require --classification-report")
        classification_path = resolve_report_path(root, args.classification_report)
        plan_path = resolve_report_path(root, args.plan)
        selected_pages = parse_pages(args.exclude_pages) if args.exclude_pages else None
        try:
            plan = build_langgraph_plan(
                root,
                classification_path,
                delete_source_files=args.delete_source_files,
                selected_pages=selected_pages,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            plan = {
                "schema": "langgraph-visual-asset-pruning-plan",
                "version": "0.1.0",
                "status": "needs-review",
                "mode": "preview",
                "package_root": str(root),
                "diagnostics": [str(exc)],
            }
        write_json(plan_path, plan)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan.get("status") == "ready" else 1

    if not args.exclude_pages:
        parser.error("Workflow A manifest packages require --exclude-pages")
    if args.classification_report or args.delete_source_files:
        parser.error("LangGraph-only flags cannot be used with a Workflow A manifest package")
    pages = parse_pages(args.exclude_pages)
    reason_map = parse_reason_map(args.reason)
    document_path = root / "index.md"
    if not manifest_path.is_file() or not document_path.is_file():
        raise SystemExit("package_root must contain manifest.yaml and index.md")
    manifest = load_yaml(manifest_path)
    document = document_path.read_text(encoding="utf-8")
    report, records, errors = build_report(root, pages, "apply" if args.apply else "preview", manifest, document, reason_map)
    if not errors and args.apply:
        try:
            apply(root, manifest, document, report, records)
        except (OSError, ValueError) as exc:
            report["status"] = "failed"
            report.setdefault("diagnostics", []).append(str(exc))
    report_path = root / "evidence/visual-asset-pruning.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
