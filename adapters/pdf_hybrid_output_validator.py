#!/usr/bin/env python3
"""Independently re-read and check one full page-hybrid PDF Markdown package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from importlib import import_module
from pathlib import Path
from urllib.parse import unquote, urlsplit


VALIDATOR = "pdf-hybrid-output-validator"
READER_AUDIT = Path("reports/reader-visual-audit.json")
READER_VALIDATION = Path("reports/reader-visual-validation.json")
READER_PROJECTION = Path("reader")
READER_REQUIRED = "reader_visual_projection_required"
READER_AUDIT_VERSION = "reader_visual_audit_version"
PAGE_MARKER = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", re.MULTILINE)
IMAGE_REFERENCE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))[^\n)]*\)")
COVERAGE_EXCEPTIONS = ("missing_pages", "failed_pages", "needs_review_pages", "incomplete_pages",
                       "duplicate_pages", "unexpected_pages")


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def validate_engineering_package(root: Path, source: Path) -> dict:
    """Check the existing engineering gate, independent of reader-stage state."""
    root = root.resolve()
    source = source.resolve()
    checks: dict[str, bool] = {}
    report = {"validator": VALIDATOR, "source": str(source), "checks": checks}
    try:
        source_hash = _digest(source)
        info = subprocess.run(["pdfinfo", str(source)], text=True, capture_output=True,
                              check=True, timeout=30, env={**os.environ, "LC_ALL": "C"})
        match = re.search(r"^Pages:\s+([1-9]\d*)\s*$", info.stdout, re.MULTILINE)
        if match is None:
            raise ValueError("pdfinfo did not return a valid page count")
        total = int(match.group(1))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {**report, "status": "failed", "error": str(exc)}

    expected = list(range(1, total + 1))
    coverage = _read_report(root / "reports/page-completion.json")
    checks["page_completion_passed"] = (coverage.get("status") == "passed"
        and coverage.get("expected_pages") == expected and coverage.get("completed_pages") == expected
        and all(coverage.get(key) == [] for key in COVERAGE_EXCEPTIONS))
    bad_units = []
    for page in expected:
        unit_path = root / "page-units" / f"p{page:03d}.md"
        route = _read_report(root / "evidence/page-routes" / f"p{page:03d}.json")
        try:
            unit = unit_path.read_bytes()
        except OSError:
            unit = b""
        if (not unit.strip() or route.get("page") != page or route.get("status") != "passed"
                or route.get("source_sha256") != source_hash
                or route.get("page_unit_sha256") != hashlib.sha256(unit).hexdigest()):
            bad_units.append(page)
    checks["page_units_match_source_and_route_evidence"] = not bad_units

    try:
        index = (root / "index.md").read_bytes()
        markdown = index.decode("utf-8")
    except (OSError, UnicodeError):
        index, markdown = b"", ""
    sequence = [int(value) for value in PAGE_MARKER.findall(markdown)]
    checks["index_full_page_sequence"] = sequence == expected
    checks["index_has_no_incomplete_page_placeholders"] = bool(markdown.strip()) and "本页转换尚未完成" not in markdown
    invalid_images = set()
    for angle, bare in IMAGE_REFERENCE.findall(markdown):
        ref = angle or bare
        try:
            parsed = urlsplit(ref)
            target = (root / unquote(parsed.path)).resolve()
            if (parsed.scheme or parsed.netloc or not target.is_relative_to(root)
                    or not target.is_file() or target.stat().st_size == 0):
                invalid_images.add(ref)
        except (OSError, ValueError, RuntimeError):
            invalid_images.add(ref)
    checks["image_references_are_local_and_resolve"] = not invalid_images
    heading = _read_report(root / "reports/heading-repair.json")
    heading_checks = heading.get("validation")
    formatting = heading.get("formatting_validation")
    checks["index_matches_heading_and_formatting_evidence"] = (bool(index)
        and heading.get("index_sha256") == hashlib.sha256(index).hexdigest()
        and heading.get("status") == "passed"
        and isinstance(heading_checks, dict) and heading_checks.get("heading_prefix_only") is True
        and isinstance(formatting, dict) and formatting.get("passed") is True)

    counts = Counter(sequence)
    return {**report, "status": "passed" if all(checks.values()) else "failed",
            "source_sha256": source_hash, "total_pages": total, "selected_pages": expected,
            "actual_full_pdf_coverage": sequence == expected and checks["page_completion_passed"],
            "index_sha256": hashlib.sha256(index).hexdigest() if index else None,
            "missing_pages": [page for page in expected if not counts[page]],
            "duplicate_pages": sorted(page for page, count in counts.items() if count > 1),
            "unexpected_pages": sorted(counts.keys() - set(expected)),
            "unverified_page_units": bad_units, "invalid_image_references": sorted(invalid_images)}


def path_present(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def reader_stage_expected(root: Path) -> bool:
    return (
        READER_REQUIRED in _read_report(root / "acceptance.json")
        or any(path_present(root / item) for item in (READER_AUDIT, READER_VALIDATION, READER_PROJECTION))
        or reader_stage_committed(_read_report(root / "summary.json"), _read_report(root / "progress.json"))
    )


def reader_stage_committed(*records: dict) -> bool:
    return any(
        isinstance(stage := record.get("reader_visual"), dict) and stage.get("committed") is True
        for record in records
    )


def reader_components():
    # Legacy packages and the engineering gate do not depend on either worker.
    prefix = f"{__package__}." if __package__ else ""
    return (
        import_module(prefix + "pdf_hybrid_visual_audit"),
        import_module(prefix + "pdf_hybrid_reader_export"),
    )


def check_reader_paths(root: Path) -> None:
    reports = root / READER_AUDIT.parent
    if reports.is_symlink() or not reports.is_dir():
        raise ValueError("reader reports directory is missing or is a symlink")
    for relative, is_directory in ((READER_AUDIT, False), (READER_VALIDATION, False), (READER_PROJECTION, True)):
        path = root / relative
        if path_present(path) and (path.is_symlink() or not (path.is_dir() if is_directory else path.is_file())):
            raise ValueError(f"invalid reader artifact path: {relative}")


def read_reader_audit(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    report = json.loads(raw)
    if not isinstance(report, dict) or not report:
        raise ValueError("empty or unreadable reader visual audit")
    return report, hashlib.sha256(raw).hexdigest()


def reader_acceptance(audit_sha: str, audit: dict, reader_result: dict) -> dict:
    """Bind verified projection bytes to the audit; semantic review is separate."""
    unresolved = [
        {"page": row["page"], "decision": row["decision"]}
        for row in audit["pages"] if row["decision"] in {"crop-required", "needs-review"}
    ]
    unprotected = audit.get("unverified_no_page_image") or []
    return {
        "schema": "pdf-hybrid-reader-visual-validation",
        "status": "needs-review" if unprotected else "passed",
        "bytes_verified": True,
        "semantic_status": "needs-review" if unresolved else "resolved",
        "unresolved_pages": unresolved,
        "audit_report": READER_AUDIT.as_posix(),
        "audit_sha256": audit_sha,
        "projection": READER_PROJECTION.as_posix(),
        "projection_index_sha256": reader_result["index_sha256"],
        "public_delivery": False,
    }


def validate_reader_stage(root: Path, source: Path, selected_pages: list[int]) -> dict:
    """Re-verify persisted audit and projection without trusting their acceptance record."""
    try:
        check_reader_paths(root)
        audit_path, validation_path, destination = (root / item for item in (READER_AUDIT, READER_VALIDATION, READER_PROJECTION))
        if not (audit_path.is_file() and validation_path.is_file() and destination.is_dir()):
            raise ValueError("incomplete reader visual stage: expected both reports and the reader projection")
        audit, audit_sha = read_reader_audit(audit_path)
        recorded = _read_report(validation_path)
        if recorded.get("audit_sha256") != audit_sha:
            raise ValueError("reader visual audit does not match its acceptance record")
        audit_worker, export_worker = reader_components()
        audit_check = audit_worker.validate_audit(root, source, audit, selected_pages=selected_pages)
        if audit_check.get("status") != "passed":
            return {"status": "failed", "bytes_verified": False, "audit_validation": audit_check}
        reader_check = export_worker.validate_reader(root, audit, destination)
        if reader_check.get("status") != "passed":
            return {"status": "failed", "bytes_verified": False, "reader_validation": reader_check}
        verified = reader_acceptance(audit_sha, audit, reader_check)
        if json.dumps(recorded, sort_keys=True) != json.dumps(verified, sort_keys=True):
            raise ValueError("reader visual acceptance record does not match independent verification")
        return verified
    except (OSError, ValueError, ImportError, RuntimeError, KeyError) as exc:
        return {"status": "failed", "bytes_verified": False, "error": str(exc)}


def validate_package(root: Path, source: Path) -> dict:
    root, source = root.resolve(), source.resolve()
    engineering = validate_engineering_package(root, source)
    if not reader_stage_expected(root):
        return engineering
    if engineering["status"] != "passed":
        return {**engineering, "engineering_status": "failed", "reader_visual": {"status": "not-run"}}
    reader = validate_reader_stage(root, source, engineering["selected_pages"])
    return {
        **engineering,
        "status": reader["status"],
        "engineering_status": "passed",
        "checks": {**engineering["checks"], "reader_visual_projection_verified": reader.get("bytes_verified") is True},
        "reader_visual": reader,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    result = validate_package(args.run_root, args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
