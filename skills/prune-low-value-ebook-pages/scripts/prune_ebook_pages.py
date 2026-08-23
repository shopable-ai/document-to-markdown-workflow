#!/usr/bin/env python3
"""Preview or apply hash-locked deletion of low-value ebook page units."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA = "ebook-page-pruning-plan"
VERSION = "1.0.0"
PAGE_FILE_RE = re.compile(r"p(\d{3,})\.md")
ALLOWED_REASONS = {
    "promotion-or-cta",
    "table-of-contents",
    "copyright-or-publication",
    "publisher-catalog-or-recommendation",
    "duplicate-cover-or-title",
    "blank-or-decoration",
    "user-approved-low-relevance",
}


class PruningError(ValueError):
    """Raised when a pruning plan or page-unit directory is unsafe."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise PruningError(f"invalid page range: {token}") from exc
            if start < 1 or end < start:
                raise PruningError(f"invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            try:
                page = int(token)
            except ValueError as exc:
                raise PruningError(f"invalid page number: {token}") from exc
            if page < 1:
                raise PruningError(f"invalid page number: {token}")
            pages.add(page)
    if not pages:
        raise PruningError("page selector must contain at least one page")
    return sorted(pages)


def parse_reason_map(values: Iterable[str], selected_pages: list[int]) -> dict[int, str]:
    result: dict[int, str] = {}
    for value in values:
        selector, separator, reason = value.partition("=")
        if not separator or reason not in ALLOWED_REASONS:
            allowed = ", ".join(sorted(ALLOWED_REASONS))
            raise PruningError(f"invalid --reason {value!r}; allowed reasons: {allowed}")
        for page in parse_pages(selector):
            previous = result.get(page)
            if previous is not None and previous != reason:
                raise PruningError(f"page {page} has conflicting reasons: {previous}, {reason}")
            result[page] = reason
    selected = set(selected_pages)
    missing = sorted(selected - set(result))
    extra = sorted(set(result) - selected)
    if missing:
        raise PruningError(f"selected pages are missing reasons: {missing}")
    if extra:
        raise PruningError(f"reason map includes unselected pages: {extra}")
    return result


def page_number(path: Path) -> int:
    match = PAGE_FILE_RE.fullmatch(path.name)
    if not match:
        raise PruningError(f"invalid page-unit filename: {path.name}")
    return int(match.group(1))


def inventory(page_units: Path) -> list[dict[str, object]]:
    if not page_units.is_dir():
        raise PruningError(f"page-units directory not found: {page_units}")
    records: list[dict[str, object]] = []
    seen: set[int] = set()
    for path in sorted(page_units.glob("p*.md")):
        if path.is_symlink() or not path.is_file():
            raise PruningError(f"page unit must be a regular non-symlink file: {path}")
        page = page_number(path)
        if page in seen:
            raise PruningError(f"duplicate page number: {page}")
        seen.add(page)
        payload = path.read_bytes()
        if not payload:
            raise PruningError(f"page unit is empty: {path.name}")
        records.append(
            {
                "file": path.name,
                "page": page,
                "sha256": sha256_bytes(payload),
                "size_bytes": len(payload),
                "line_count": len(payload.splitlines()),
            }
        )
    if not records:
        raise PruningError(f"no pNNN.md page units found: {page_units}")
    records.sort(key=lambda item: int(item["page"]))
    return records


def inventory_sha256(records: list[dict[str, object]]) -> str:
    compact = [
        {"file": item["file"], "page": item["page"], "sha256": item["sha256"]}
        for item in records
    ]
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def ensure_plan_outside_page_units(plan_path: Path, page_units: Path) -> None:
    try:
        plan_path.resolve().relative_to(page_units.resolve())
    except ValueError:
        return
    raise PruningError("plan must be stored outside the page-units directory")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists() and path.read_bytes() == encoded:
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def build_preview(
    *,
    page_units: Path,
    selected_pages: list[int],
    expected_page_count: int,
    reasons: dict[int, str],
) -> dict[str, object]:
    records = inventory(page_units)
    if len(records) != expected_page_count:
        raise PruningError(
            f"page count mismatch: expected {expected_page_count}, found {len(records)}; "
            "wait for all page groups to finish and merge"
        )
    by_page = {int(item["page"]): item for item in records}
    missing = [page for page in selected_pages if page not in by_page]
    if missing:
        raise PruningError(f"selected page units are missing: {missing}")
    if len(selected_pages) >= len(records):
        raise PruningError("refusing to delete every page unit")
    selected_page_set = set(selected_pages)
    selected = [
        {
            **by_page[page],
            "input_sha256": by_page[page]["sha256"],
            "reason_code": reasons[page],
        }
        for page in selected_pages
    ]
    for item in selected:
        item.pop("sha256", None)
    retained = [item for item in records if int(item["page"]) not in selected_page_set]
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "ready",
        "mode": "preview",
        "created_at": utc_now(),
        "page_units": str(page_units),
        "expected_page_count": expected_page_count,
        "inventory": {
            "page_count": len(records),
            "first_page": records[0]["page"],
            "last_page": records[-1]["page"],
            "sha256": inventory_sha256(records),
        },
        "selected_pages": selected_pages,
        "retained_page_count": len(retained),
        "files": selected,
        "expected_post_inventory_sha256": inventory_sha256(retained),
        "checks": {
            "expected_page_count_matches": True,
            "all_selected_files_exist": True,
            "all_selected_pages_have_reasons": True,
            "all_files_nonempty": True,
            "at_least_one_page_retained": True,
            "plan_contains_no_page_body": True,
        },
        "protected_layers": [
            "source-document",
            "page-images",
            "ocr-and-bbox",
            "vision-json",
            "manifest-and-reports",
        ],
        "next_action": "review this hash-locked plan, then run apply; rebuild downstream index/total from retained page-units",
        "claim_boundary": "whole-page pruning of the selected knowledge-base page-unit directory only",
    }


def validate_plan(plan: dict[str, object]) -> None:
    if plan.get("schema") != SCHEMA or plan.get("version") != VERSION:
        raise PruningError("unsupported pruning plan schema or version")
    if plan.get("status") not in {"ready", "applied"}:
        raise PruningError(f"plan is not applicable: status={plan.get('status')!r}")
    if not isinstance(plan.get("page_units"), str) or not plan["page_units"]:
        raise PruningError("plan is missing page_units")
    if not isinstance(plan.get("files"), list) or not plan["files"]:
        raise PruningError("plan contains no files")


def current_post_inventory_matches(plan: dict[str, object], page_units: Path) -> bool:
    records = inventory(page_units)
    files = plan.get("files")
    assert isinstance(files, list)
    return (
        len(records) == plan.get("retained_page_count")
        and inventory_sha256(records) == plan.get("expected_post_inventory_sha256")
        and all(isinstance(item, dict) and not (page_units / str(item["file"])).exists() for item in files)
    )


def apply_plan(plan_path: Path) -> dict[str, object]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PruningError(f"cannot read plan: {plan_path}: {exc}") from exc
    if not isinstance(plan, dict):
        raise PruningError("plan must be a JSON object")
    validate_plan(plan)
    page_units = Path(str(plan["page_units"])).expanduser().resolve()
    ensure_plan_outside_page_units(plan_path, page_units)
    if plan.get("status") == "applied":
        if not current_post_inventory_matches(plan, page_units):
            raise PruningError("applied plan no longer matches the current post-pruning directory")
        return plan

    records = inventory(page_units)
    expected_inventory = plan.get("inventory")
    if not isinstance(expected_inventory, dict):
        raise PruningError("plan is missing inventory")
    if len(records) != expected_inventory.get("page_count") or inventory_sha256(records) != expected_inventory.get("sha256"):
        raise PruningError("page-unit directory changed after preview; deleted 0 files, generate a new plan")
    by_name = {str(item["file"]): item for item in records}
    planned_files = plan.get("files")
    assert isinstance(planned_files, list)
    targets: list[Path] = []
    for planned in planned_files:
        if not isinstance(planned, dict):
            raise PruningError("invalid file entry in plan")
        name = str(planned.get("file", ""))
        if Path(name).name != name or not PAGE_FILE_RE.fullmatch(name):
            raise PruningError(f"unsafe planned filename: {name!r}")
        current = by_name.get(name)
        if current is None or current.get("sha256") != planned.get("input_sha256"):
            raise PruningError(f"planned file changed or disappeared: {name}; deleted 0 files")
        targets.append(page_units / name)

    for target in targets:
        target.unlink()

    post_records = inventory(page_units)
    if len(post_records) != plan.get("retained_page_count"):
        raise PruningError("post-pruning page count does not match the plan")
    if inventory_sha256(post_records) != plan.get("expected_post_inventory_sha256"):
        raise PruningError("post-pruning inventory does not match the plan")
    if any(target.exists() for target in targets):
        raise PruningError("one or more planned page units still exist after apply")

    plan["status"] = "applied"
    plan["mode"] = "apply"
    plan["applied_at"] = utc_now()
    plan["deleted_file_count"] = len(targets)
    plan["post_inventory"] = {
        "page_count": len(post_records),
        "sha256": inventory_sha256(post_records),
    }
    plan["checks"] = {
        **dict(plan.get("checks") or {}),
        "pre_apply_inventory_unchanged": True,
        "planned_files_absent": True,
        "retained_files_unchanged": True,
        "post_inventory_matches_plan": True,
    }
    plan["next_action"] = "rebuild downstream index/total or reader collection from retained page-units; do not reuse a pre-pruning merge"
    write_json(plan_path, plan)
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview = subparsers.add_parser("preview", help="write a hash-locked deletion plan without deleting files")
    preview.add_argument("--page-units", type=Path, required=True)
    preview.add_argument("--pages", required=True, help="explicit pages/ranges, for example 1,2-10")
    preview.add_argument("--expected-page-count", type=int, required=True)
    preview.add_argument("--reason", action="append", required=True, metavar="PAGES=REASON")
    preview.add_argument("--plan", type=Path, required=True)
    apply = subparsers.add_parser("apply", help="apply an unchanged preview plan")
    apply.add_argument("--plan", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "preview":
            page_units = args.page_units.expanduser().resolve()
            plan_path = args.plan.expanduser().resolve()
            if args.expected_page_count < 2:
                raise PruningError("--expected-page-count must be at least 2")
            ensure_plan_outside_page_units(plan_path, page_units)
            selected_pages = parse_pages(args.pages)
            reasons = parse_reason_map(args.reason, selected_pages)
            result = build_preview(
                page_units=page_units,
                selected_pages=selected_pages,
                expected_page_count=args.expected_page_count,
                reasons=reasons,
            )
            write_json(plan_path, result)
        else:
            result = apply_plan(args.plan.expanduser().resolve())
    except (OSError, PruningError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
