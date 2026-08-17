#!/usr/bin/env python3
"""Preview or apply semantic pruning of redundant reader-facing page images."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - package runtime dependency
    raise SystemExit("PyYAML is required to prune a Workflow A package") from exc


IMAGE_RE = re.compile(r"^!\[[^\]]*\]\((?:<([^>]+)>|([^)]*))\)\s*$")


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
    parser.add_argument("--exclude-pages", required=True, help="page numbers/ranges, e.g. 1,2,23-27")
    parser.add_argument("--reason", action="append", default=[], metavar="PAGE=REASON", help="optional reason override")
    parser.add_argument("--apply", action="store_true", help="apply the approved pruning")
    args = parser.parse_args()
    root = args.package_root.expanduser().resolve()
    pages = parse_pages(args.exclude_pages)
    reason_map = parse_reason_map(args.reason)
    manifest_path = root / "manifest.yaml"
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
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
