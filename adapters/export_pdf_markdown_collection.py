#!/usr/bin/env python3
"""Publish validated PDF Markdown packages as a source-shaped reader collection."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from lan_pdf_markdown_batch import _inspect_final_completion
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .lan_pdf_markdown_batch import _inspect_final_completion


FORMAT_VERSION = "0.2.0"
IMAGE_REFERENCE_PATTERN = re.compile(
    r"(!\[[^\]]*\]\(\s*<?)([^\s)>]+)(>?(?:\s+[^)]*)?\))"
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0]) if rows else []
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _link_or_copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        os.link(source, temporary)
    except OSError:
        shutil.copy2(source, temporary)
    os.replace(temporary, destination)
    if _sha256(source) != _sha256(destination):
        raise RuntimeError(f"published asset hash mismatch: {destination}")


def _publish_markdown_assets(
    *, source_markdown: Path, run_root: Path, destination: Path, delivery_root: Path
) -> tuple[str, list[str]]:
    source_text = source_markdown.read_text(encoding="utf-8")
    book_scope = destination.relative_to(delivery_root).with_suffix("")
    published: dict[str, Path] = {}

    def replace(match: re.Match[str]) -> str:
        raw_reference = match.group(2)
        parsed = urlparse(raw_reference)
        if parsed.scheme or raw_reference.startswith(("#", "data:")):
            return match.group(0)
        source_asset = (run_root / unquote(parsed.path)).resolve()
        try:
            source_asset.relative_to(run_root)
        except ValueError as exc:
            raise ValueError(f"image reference escapes run root: {raw_reference}") from exc
        if not source_asset.is_file() or source_asset.stat().st_size <= 0:
            raise ValueError(f"missing source image reference: {source_asset}")
        asset_relative = Path("assets") / book_scope / source_asset.name
        asset_key = os.path.normcase(asset_relative.as_posix())
        previous = published.get(asset_key)
        if previous is not None and previous != source_asset:
            raise ValueError(
                f"multiple source assets map to one delivery path: {asset_relative}"
            )
        published[asset_key] = source_asset
        _link_or_copy_atomic(source_asset, delivery_root / asset_relative)
        rewritten = Path(
            os.path.relpath(delivery_root / asset_relative, start=destination.parent)
        ).as_posix()
        if parsed.query:
            rewritten += f"?{parsed.query}"
        if parsed.fragment:
            rewritten += f"#{parsed.fragment}"
        return f"{match.group(1)}{rewritten}{match.group(3)}"

    rewritten_text = IMAGE_REFERENCE_PATTERN.sub(replace, source_text)
    return rewritten_text, sorted(
        str((delivery_root / Path(key)).resolve()) for key in published
    )


def _relative_pdf_path(source: Path, source_root: Path) -> Path:
    try:
        relative = source.resolve().relative_to(source_root.resolve())
    except ValueError as exc:
        raise ValueError(f"source is outside --source-root: {source}") from exc
    if source.suffix.lower() != ".pdf":
        raise ValueError(f"expected PDF source: {source}")
    return relative


def expected_sources(route_report: Path, source_root: Path) -> list[dict[str, str]]:
    report = _read_json(route_report.resolve())
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError(f"route report has no records list: {route_report}")
    expected: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "complete":
            continue
        raw_source = record.get("source_path")
        raw_hash = record.get("source_sha256")
        if not raw_source or not raw_hash:
            raise ValueError("every complete route record must contain source_path and source_sha256")
        source = Path(str(raw_source)).expanduser().resolve()
        relative = _relative_pdf_path(source, source_root)
        key = str(source)
        if key in seen:
            raise ValueError(f"duplicate source in route report: {source}")
        seen.add(key)
        expected.append(
            {
                "source_path": key,
                "source_sha256": str(raw_hash),
                "relative_path": str(relative),
                "delivery_relative_path": str(relative.with_suffix(".md")),
            }
        )
    if not expected:
        raise ValueError(f"route report contains no complete PDF records: {route_report}")
    return expected


def _result_candidates(result_paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for result_path in result_paths:
        payload = _read_json(result_path.resolve())
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError(f"batch results has no items list: {result_path}")
        for item in items:
            if not isinstance(item, dict) or not item.get("source_path"):
                continue
            source = str(Path(str(item["source_path"])).expanduser().resolve())
            candidates.setdefault(source, []).append({**item, "batch_results": str(result_path.resolve())})
    return candidates


def _accepted_candidate(
    candidates: list[dict[str, Any]], *, source_path: str, source_sha256: str
) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    accepted: list[dict[str, Any]] = []
    for item in candidates:
        run_root_raw = item.get("run_root")
        if item.get("status") != "passed" or not run_root_raw:
            failures.append(str(item.get("status") or "missing-status"))
            continue
        completion = _inspect_final_completion(
            Path(str(run_root_raw)),
            source_path=source_path,
            source_sha256=source_sha256,
            requested_pages="all",
        )
        if completion["accepted"]:
            accepted.append({**item, **completion})
        else:
            failures.append(
                f"rejected:{completion['completion_kind']}:{completion['final_stage']}"
            )
    if not accepted:
        return None, failures
    accepted.sort(key=lambda item: str(item.get("finished_at") or item.get("started_at") or ""))
    return accepted[-1], failures


def build_collection(
    *,
    route_report: Path,
    result_paths: list[Path],
    source_root: Path,
    delivery_root: Path,
) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    delivery_root = delivery_root.expanduser().resolve()
    expected = expected_sources(route_report, source_root)
    candidates = _result_candidates(result_paths)
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    destinations: set[str] = set()
    selected: list[tuple[dict[str, str], dict[str, Any], Path]] = []
    for entry in expected:
        destination = delivery_root / entry["delivery_relative_path"]
        destination_key = os.path.normcase(str(destination))
        if destination_key in destinations:
            raise ValueError(f"multiple PDFs map to the same Markdown destination: {destination}")
        destinations.add(destination_key)
        candidate, failures = _accepted_candidate(
            candidates.get(entry["source_path"], []),
            source_path=entry["source_path"],
            source_sha256=entry["source_sha256"],
        )
        if candidate is None:
            unresolved.append(
                {
                    "source_path": entry["source_path"],
                    "relative_path": entry["relative_path"],
                    "observed": failures or ["missing-result"],
                }
            )
            continue
        selected.append((entry, candidate, destination))

    if unresolved:
        raise ValueError(
            f"collection not published: {len(unresolved)}/{len(expected)} sources lack a validated passed result; "
            f"first={json.dumps(unresolved[0], ensure_ascii=False)}"
        )

    existing_markdown = {
        str(path.resolve()) for path in delivery_root.rglob("*.md")
    } if delivery_root.is_dir() else set()
    expected_markdown = {str(destination.resolve()) for _, _, destination in selected}
    unexpected = sorted(existing_markdown - expected_markdown)
    if unexpected:
        raise ValueError(f"delivery root contains unexpected Markdown files; first={unexpected[0]}")

    delivery_root.mkdir(parents=True, exist_ok=True)
    for index, (entry, candidate, destination) in enumerate(selected, 1):
        source_markdown = Path(str(candidate["index_markdown"])).resolve()
        run_root = Path(str(candidate["run_root"])).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        delivery_text, delivery_assets = _publish_markdown_assets(
            source_markdown=source_markdown,
            run_root=run_root,
            destination=destination,
            delivery_root=delivery_root,
        )
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(delivery_text, encoding="utf-8")
        os.replace(temporary, destination)
        delivery_md_sha256 = _sha256(destination)
        missing_delivery_assets = [
            path for path in delivery_assets if not Path(path).is_file()
        ]
        if missing_delivery_assets:
            raise RuntimeError(
                f"published Markdown has missing assets: {missing_delivery_assets[0]}"
            )
        rows.append(
            {
                "index": index,
                "source_path": entry["source_path"],
                "relative_path": entry["relative_path"],
                "source_sha256": entry["source_sha256"],
                "run_root": candidate["run_root"],
                "source_markdown": str(source_markdown),
                "delivery_markdown": str(destination),
                "delivery_relative_path": entry["delivery_relative_path"],
                "markdown_sha256": delivery_md_sha256,
                "source_markdown_sha256": _sha256(source_markdown),
                "source_index_bytes": candidate["index_bytes"],
                "index_bytes": destination.stat().st_size,
                "index_characters": len(delivery_text),
                "meaningful_text_characters": candidate["meaningful_text_characters"],
                "page_marker_count": candidate["page_marker_count"],
                "page_marker_sequence": candidate["page_marker_sequence"],
                "image_reference_count": candidate["image_reference_count"],
                "valid_image_reference_count": candidate["valid_image_reference_count"],
                "missing_image_references": candidate["missing_image_references"],
                "delivery_asset_count": len(delivery_assets),
                "delivery_assets": delivery_assets,
                "incomplete_placeholder_present": candidate["incomplete_placeholder_present"],
                "total_pages": candidate["total_pages"],
                "package_file_count": candidate["package_file_count"],
                "package_total_bytes": candidate["package_total_bytes"],
                "validation_report": candidate["validation_report"],
                "batch_results": candidate["batch_results"],
                "status": "passed",
            }
        )

    mapping_path = delivery_root / "source-output-map.csv"
    manifest_path = delivery_root / "collection-manifest.json"
    _write_csv_atomic(mapping_path, rows)
    total_bytes = sum(int(row["index_bytes"]) for row in rows)
    total_assets = sum(int(row["delivery_asset_count"]) for row in rows)
    manifest = {
        "format_version": FORMAT_VERSION,
        "status": "passed",
        "created_at": datetime.now().astimezone().isoformat(),
        "source_root": str(source_root),
        "route_report": str(route_report.resolve()),
        "batch_results": [str(path.resolve()) for path in result_paths],
        "delivery_root": str(delivery_root),
        "expected_count": len(expected),
        "published_count": len(rows),
        "markdown_total_bytes": total_bytes,
        "asset_count": total_assets,
        "mapping_csv": str(mapping_path),
        "items": rows,
    }
    _write_json_atomic(manifest_path, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-report", type=Path, required=True)
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = build_collection(
            route_report=args.route_report,
            result_paths=args.results,
            source_root=args.source_root,
            delivery_root=args.delivery_root,
        )
    except Exception as exc:
        print(f"ERROR handled=true {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
