#!/usr/bin/env python3
"""Batch LAN PDF-to-Markdown conversions with route-aware resumable records."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

try:
    from output_naming import package_timestamp, readable_package_stem
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import package_timestamp, readable_package_stem


ROOT = Path(__file__).resolve().parents[1]
GRAPH_ADAPTER = ROOT / "adapters" / "lan_pdf_markdown_graph.py"
DEFAULT_BATCHES_PARENT = Path("/Users/a0000/pdf_runs/lan-pdf-batches")
FORMAT_VERSION = "0.1.0"
STATUS_VALUES = {"passed", "failed", "needs-review"}
ROUTE_VALUES = {"native-only", "hybrid", "vision-only"}
FINAL_STAGE = "heading-audit-repair-validation"


def _graph_route_arguments(route: str | None) -> list[str]:
    selected = route or "native-only"
    if selected == "native-only":
        return ["--no-model"]
    if selected == "hybrid":
        return []
    if selected == "vision-only":
        return ["--visual-only"]
    raise ValueError(f"unsupported document route: {selected}")


@dataclass(frozen=True)
class SourceEntry:
    source_path: str
    relative_path: str
    route: str | None = None
    route_confidence: str | None = None
    source_sha256: str | None = None
    file_size: int | None = None
    modified_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @property
    def source(self) -> Path:
        return Path(self.source_path).expanduser().resolve()


def _source_preflight_error(entry: SourceEntry) -> str | None:
    """Return a compact input error after attempting a real source read."""
    source = entry.source
    if source.suffix.lower() != ".pdf":
        return f"unsupported suffix: {source.suffix or '<none>'}"
    if not source.is_file():
        return "not a file"
    try:
        with source.open("rb") as stream:
            if not stream.read(1):
                return "empty file"
    except OSError as exc:
        return f"open failed: {type(exc).__name__}: {exc}"
    return None


def _preflight_entries(entries: list[SourceEntry]) -> None:
    failures = [
        (str(entry.source), error)
        for entry in entries
        if (error := _source_preflight_error(entry)) is not None
    ]
    if not failures:
        return
    shown = failures[:10]
    details = "\n".join(f"- {path}: {error}" for path, error in shown)
    omitted = len(failures) - len(shown)
    suffix = f"\n- ... {omitted} more" if omitted else ""
    raise ValueError(
        f"PDF readability preflight failed for {len(failures)}/{len(entries)} sources; "
        f"no conversions started:\n{details}{suffix}"
    )


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _stat_iso(path: Path) -> tuple[int, str]:
    stat = path.stat()
    return stat.st_size, datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()


def _path_list_with_missing(path: Path, *, strict: bool) -> tuple[list[Path], list[str]]:
    if not path.is_file():
        raise ValueError(f"source list does not exist: {path}")
    result: list[Path] = []
    missing: list[str] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line).expanduser()
        if not candidate.is_absolute():
            candidate = (path.parent / candidate).resolve()
        if not candidate.is_file():
            if strict:
                raise ValueError(f"source list line {line_number} is not a readable file: {line}")
            missing.append(str(candidate))
            continue
        result.append(candidate.resolve())
    return result, missing


def _path_list(path: Path) -> list[Path]:
    result, _ = _path_list_with_missing(path, strict=True)
    return result


def _normalise_relative_path(source: Path, relative_path: str | None, source_root: Path | None) -> str:
    if relative_path:
        return str(Path(relative_path))
    if source_root is not None and source.is_relative_to(source_root):
        return str(source.relative_to(source_root))
    return source.name


def _input_source_root(entries: list[SourceEntry], explicit_root: Path | None) -> Path | None:
    if explicit_root is not None:
        return explicit_root.expanduser().resolve()
    if not entries:
        return None
    parents = [str(Path(entry.source_path).expanduser().resolve().parent) for entry in entries]
    try:
        return Path(os.path.commonpath(parents))
    except ValueError:
        return None


def _entry_from_path(
    source: Path,
    *,
    source_root: Path | None,
    route: str | None,
    route_confidence: str | None = None,
    source_sha256: str | None = None,
    file_size: int | None = None,
    modified_at: str | None = None,
    relative_path: str | None = None,
    reason_codes: tuple[str, ...] = (),
) -> SourceEntry:
    size = file_size
    modified = modified_at
    if size is None or modified is None:
        size, modified = _stat_iso(source)
    return SourceEntry(
        source_path=str(source.resolve()),
        relative_path=_normalise_relative_path(source.resolve(), relative_path, source_root),
        route=route,
        route_confidence=route_confidence,
        source_sha256=source_sha256,
        file_size=size,
        modified_at=modified,
        reason_codes=reason_codes,
    )


def entries_from_paths(
    paths: list[Path],
    *,
    source_root: Path | None,
    route: str | None,
) -> list[SourceEntry]:
    unique: list[SourceEntry] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"source does not exist: {resolved}")
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(_entry_from_path(resolved, source_root=source_root, route=route))
    return unique


def entries_from_route_report(report_path: Path, route: str) -> tuple[list[SourceEntry], dict[str, Any]]:
    report_path = report_path.expanduser().resolve()
    report = _read_json(report_path)
    if not report:
        raise ValueError(f"route report is missing or invalid JSON: {report_path}")
    if route not in ROUTE_VALUES:
        raise ValueError(f"unsupported route: {route}")
    source_root_raw = report.get("source_root")
    source_root = Path(str(source_root_raw)).expanduser().resolve() if source_root_raw else None
    outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
    route_lists = outputs.get("route_lists") if isinstance(outputs.get("route_lists"), dict) else {}
    publication = outputs.get("publication") if isinstance(outputs.get("publication"), dict) else {}
    publication_lists = (
        publication.get("route_lists") if isinstance(publication.get("route_lists"), dict) else {}
    )
    record_map = {
        str(item.get("source_path")): item
        for item in report.get("records", [])
        if isinstance(item, dict) and item.get("source_path")
    }
    selected_paths: list[Path] | None = None
    missing_sources: list[str] = []
    selected_list_path = next(
        (
            Path(str(candidate)).expanduser().resolve()
            for candidate in (route_lists.get(route), publication_lists.get(route))
            if candidate
        ),
        None,
    )
    if selected_list_path is not None and selected_list_path.is_file():
        selected_paths, missing_sources = _path_list_with_missing(selected_list_path, strict=False)
    entries: list[SourceEntry] = []
    seen: set[str] = set()
    if selected_paths is not None:
        for path in selected_paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            record = record_map.get(key) or {}
            decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
            entries.append(
                _entry_from_path(
                    path,
                    source_root=source_root,
                    route=route,
                    route_confidence=decision.get("confidence"),
                    source_sha256=record.get("source_sha256"),
                    file_size=record.get("file_size"),
                    modified_at=record.get("modified_at"),
                    relative_path=record.get("relative_path"),
                    reason_codes=tuple(decision.get("reason_codes") or ()),
                )
            )
    else:
        for record in report.get("records", []):
            if not isinstance(record, dict) or record.get("status") != "complete":
                continue
            decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
            if decision.get("route") != route:
                continue
            path = Path(str(record["source_path"])).expanduser().resolve()
            if not path.is_file():
                missing_sources.append(str(path))
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                _entry_from_path(
                    path,
                    source_root=source_root,
                    route=route,
                    route_confidence=decision.get("confidence"),
                    source_sha256=record.get("source_sha256"),
                    file_size=record.get("file_size"),
                    modified_at=record.get("modified_at"),
                    relative_path=record.get("relative_path"),
                    reason_codes=tuple(decision.get("reason_codes") or ()),
                )
            )
    if not entries:
        raise ValueError(f"route report contains no sources for route {route}: {report_path}")
    return entries, {
        "kind": "route-report",
        "route_report": str(report_path),
        "route": route,
        "source_root": str(source_root) if source_root else None,
        "selected_list": str(selected_list_path) if selected_list_path else None,
        "missing_sources": sorted(set(missing_sources)),
        "run_id": report.get("run_id"),
        "detected_at": report.get("detected_at"),
    }


def _entry_from_manifest(item: dict[str, Any]) -> SourceEntry:
    return SourceEntry(
        source_path=str(item["source_path"]),
        relative_path=str(item["relative_path"]),
        route=item.get("route"),
        route_confidence=item.get("route_confidence"),
        source_sha256=item.get("source_sha256"),
        file_size=item.get("file_size"),
        modified_at=item.get("modified_at"),
        reason_codes=tuple(item.get("reason_codes") or ()),
    )


def _serialise_entry(entry: SourceEntry) -> dict[str, Any]:
    return {
        "source_path": entry.source_path,
        "relative_path": entry.relative_path,
        "route": entry.route,
        "route_confidence": entry.route_confidence,
        "source_sha256": entry.source_sha256,
        "file_size": entry.file_size,
        "modified_at": entry.modified_at,
        "reason_codes": list(entry.reason_codes),
    }


def discover_source_entries(
    *,
    sources: list[Path],
    source_list: Path | None,
    route_list: Path | None,
    route_report: Path | None,
    route: str | None,
    source_root: Path | None,
    existing_manifest: dict[str, Any] | None = None,
) -> tuple[list[SourceEntry], dict[str, Any]]:
    if route_report is not None:
        return entries_from_route_report(route_report, route or "native-only")
    direct_paths = [path.expanduser().resolve() for path in sources]
    if source_list is not None:
        direct_paths.extend(_path_list(source_list.expanduser().resolve()))
    if route_list is not None:
        direct_paths.extend(_path_list(route_list.expanduser().resolve()))
    if direct_paths:
        entries = entries_from_paths(
            direct_paths,
            source_root=source_root.expanduser().resolve() if source_root else None,
            route=route or "native-only",
        )
        root = _input_source_root(entries, source_root.expanduser().resolve() if source_root else None)
        if root is not None:
            entries = [
                _entry_from_path(
                    entry.source,
                    source_root=root,
                    route=entry.route,
                    route_confidence=entry.route_confidence,
                    source_sha256=entry.source_sha256,
                    file_size=entry.file_size,
                    modified_at=entry.modified_at,
                    relative_path=None,
                    reason_codes=entry.reason_codes,
                )
                for entry in entries
            ]
        origin = {
            "kind": "literal-sources",
            "route": route or "native-only",
            "source_list": str(source_list.expanduser().resolve()) if source_list else None,
            "route_list": str(route_list.expanduser().resolve()) if route_list else None,
            "source_root": str(root) if root else None,
        }
        return entries, origin
    if existing_manifest and existing_manifest.get("inputs"):
        entries = [_entry_from_manifest(item) for item in existing_manifest["inputs"] if isinstance(item, dict)]
        return entries, existing_manifest.get("origin", {"kind": "resume-only"})
    raise ValueError("provide --source, --source-list, --route-list, or --route-report")


def _timestamp_batch_root(parent: Path, label: str) -> Path:
    parent = parent.expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    stem = f"{readable_package_stem(label, fallback='batch', max_chars=48)}-{package_timestamp()}"
    candidate = parent / stem
    ordinal = 1
    while candidate.exists():
        ordinal += 1
        candidate = parent / f"{stem}-{ordinal:02d}"
    return candidate


def _load_manifest(batch_root: Path) -> dict[str, Any] | None:
    return _read_json(batch_root / "manifest.json")


def _load_results(batch_root: Path) -> list[dict[str, Any]]:
    payload = _read_json(batch_root / "results.json") or {}
    items = payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _ensure_input_match(entries: list[SourceEntry], manifest: dict[str, Any]) -> None:
    existing = manifest.get("inputs")
    if not isinstance(existing, list):
        return
    current = [_serialise_entry(entry) for entry in entries]
    if existing != current:
        raise ValueError("existing batch manifest inputs do not match the current selection")


def _source_slot(entry: SourceEntry) -> tuple[list[str], str]:
    relative = Path(entry.relative_path)
    parents = [
        readable_package_stem(part, fallback="dir", max_chars=80)
        for part in relative.parent.parts
        if part not in {"", "."}
    ]
    sha_part = (entry.source_sha256 or sha256(entry.source))[:8]
    leaf = f"{readable_package_stem(entry.source.stem, fallback='pdf', max_chars=96)}-{sha_part}"
    return parents, leaf


def run_root_for_entry(batch_root: Path, entry: SourceEntry) -> Path:
    parents, leaf = _source_slot(entry)
    return batch_root / "runs" / Path(*parents) / leaf if parents else batch_root / "runs" / leaf


def _inspect_final_completion(
    run_root: Path,
    *,
    source_path: str | None,
    source_sha256: str | None,
    requested_pages: str,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    index_path = run_root / "index.md"
    summary_path = run_root / "summary.json"
    validation_path = run_root / "reports" / "validation-result.json"
    progress_path = run_root / "evidence" / "progress-current.json"
    summary = _read_json(summary_path)
    validation = _read_json(validation_path)
    progress = _read_json(progress_path)
    has_smoke = any(
        candidate.is_dir()
        for candidate in (
            run_root / ".stages" / "native-smoke",
            run_root / "smoke-run",
        )
    )
    has_batch = any(
        candidate.is_dir()
        for candidate in (
            run_root / ".stages" / "native-pages",
            run_root / "batch-run",
        )
    )
    completion_kind = "smoke-only" if has_smoke and not has_batch else "partial-full" if has_batch else "incomplete"
    current_stage = None
    for payload in (progress, validation):
        if payload:
            current_stage = payload.get("current_stage") or payload.get("stage") or current_stage
    selected_values = [
        payload.get("selected_pages")
        for payload in (summary, validation, progress)
        if payload and isinstance(payload.get("selected_pages"), list)
    ]
    total_values = [
        payload.get("total_pages")
        for payload in (summary, validation, progress)
        if payload and isinstance(payload.get("total_pages"), int)
    ]
    selected = selected_values[0] if selected_values else []
    total_pages = total_values[0] if total_values else None
    page_facts_agree = (
        len(selected_values) == 3
        and all(value == selected for value in selected_values)
        and len(total_values) == 3
        and all(value == total_pages for value in total_values)
    )
    full_coverage = bool(
        total_pages
        and selected == list(range(1, total_pages + 1))
        and validation
        and validation.get("actual_full_pdf_coverage") is True
    )
    hashes_match = bool(source_sha256) and all(
        payload and payload.get("source_sha256") == source_sha256
        for payload in (summary, validation, progress)
    )
    paths_match = all(
        not payload.get("source") or str(Path(str(payload["source"])).expanduser().resolve()) == source_path
        for payload in (summary, validation, progress)
        if payload
    ) if source_path else True
    checks = validation.get("checks") if validation else None
    validation_checks_pass = isinstance(checks, dict) and bool(checks) and all(
        value is True for value in checks.values()
    )
    completed_stages = progress.get("completed_stages") if progress else None
    final_stage_recorded = bool(
        isinstance(completed_stages, list)
        and any(
            isinstance(item, dict)
            and item.get("stage") == FINAL_STAGE
            and item.get("status") == "passed"
            for item in completed_stages
        )
    )
    accepted = all(
        [
            index_path.is_file() and index_path.stat().st_size > 0,
            summary is not None and summary.get("status") == "passed",
            validation is not None and validation.get("status") == "passed",
            progress is not None and progress.get("status") == "passed",
            current_stage == FINAL_STAGE,
            final_stage_recorded,
            page_facts_agree,
            hashes_match,
            paths_match,
            validation_checks_pass,
            full_coverage if requested_pages.strip().lower() == "all" else True,
        ]
    )
    if accepted:
        completion_kind = "validated-final"
        current_stage = "full-accepted"
    return {
        "accepted": accepted,
        "completion_kind": completion_kind,
        "final_stage": current_stage or completion_kind,
        "selected_page_count": len(selected),
        "total_pages": total_pages,
        "full_pdf_coverage": full_coverage,
        "index_markdown": str(index_path) if index_path.is_file() else None,
        "summary_path": str(summary_path) if summary_path.is_file() else None,
        "validation_report": str(validation_path) if validation_path.is_file() else None,
        "progress_path": str(progress_path) if progress_path.is_file() else None,
    }


def _existing_success(item: dict[str, Any], *, requested_pages: str) -> bool:
    if item.get("status") != "passed":
        return False
    run_root_raw = item.get("run_root")
    if not run_root_raw:
        return False
    completion = _inspect_final_completion(
        Path(str(run_root_raw)),
        source_path=str(item.get("source_path")) if item.get("source_path") else None,
        source_sha256=str(item.get("source_sha256")) if item.get("source_sha256") else None,
        requested_pages=requested_pages,
    )
    return bool(completion["accepted"])


def _parse_child_result(stdout: str) -> dict[str, Any] | None:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _read_status(path: Path) -> str | None:
    value = _read_json(path)
    status = value.get("status") if value else None
    return str(status) if isinstance(status, str) else None


def _summarise_results(items: list[dict[str, Any]], *, planned_total: int | None = None) -> dict[str, Any]:
    statuses = [str(item.get("status")) for item in items]
    executions = [str(item.get("execution")) for item in items]
    failed = statuses.count("failed")
    needs_review = statuses.count("needs-review")
    passed = statuses.count("passed")
    skipped = executions.count("skipped-existing")
    running = statuses.count("running")
    planned = len(items) if planned_total is None else planned_total
    terminal = passed + failed + needs_review
    pending = max(0, planned - terminal)
    status = "passed"
    if running or pending:
        status = "running"
    elif failed:
        status = "partial" if passed or needs_review else "failed"
    elif needs_review:
        status = "needs-review"
    return {
        "status": status,
        "total": len(items),
        "passed": passed,
        "needs_review": needs_review,
        "failed": failed,
        "skipped_existing": skipped,
        "running": running,
        "planned_total": planned,
        "completed_total": terminal,
        "pending_total": pending,
    }


def _map_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        rows.append(
            {
                "index": item.get("index"),
                "status": item.get("status"),
                "execution": item.get("execution"),
                "resume_used": item.get("resume_used"),
                "route": item.get("route"),
                "source_path": item.get("source_path"),
                "relative_path": item.get("relative_path"),
                "source_sha256": item.get("source_sha256"),
                "run_root": item.get("run_root"),
                "index_markdown": item.get("index_markdown"),
                "summary_json": item.get("summary_path"),
                "validation_report": item.get("validation_report"),
                "progress_json": item.get("progress_path"),
                "final_stage": item.get("final_stage"),
                "completion_kind": item.get("completion_kind"),
                "selected_page_count": item.get("selected_page_count"),
                "total_pages": item.get("total_pages"),
                "full_pdf_coverage": item.get("full_pdf_coverage"),
                "returncode": item.get("returncode"),
                "error": item.get("error"),
                "started_at": item.get("started_at"),
                "finished_at": item.get("finished_at"),
            }
        )
    return rows


def persist_batch_state(
    *,
    batch_root: Path,
    batch_id: str,
    created_at: str,
    entries: list[SourceEntry],
    origin: dict[str, Any],
    config: dict[str, Any],
    results: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    manifest_path = batch_root / "manifest.json"
    results_path = batch_root / "results.json"
    mapping_path = batch_root / "source-output-map.csv"
    source_list_path = batch_root / "selected-sources.txt"
    summary = _summarise_results(results, planned_total=len(entries))
    manifest = {
        "format_version": FORMAT_VERSION,
        "batch_id": batch_id,
        "mode": "lan-pdf-route-aware-batch",
        "created_at": created_at,
        "updated_at": now_iso(),
        "origin": origin,
        "config": config,
        "paths": {
            "batch_root": str(batch_root),
            "results": str(results_path),
            "mapping_csv": str(mapping_path),
            "selected_sources": str(source_list_path),
        },
        "inputs": [_serialise_entry(entry) for entry in entries],
        "summary": summary,
    }
    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(results_path, {"batch_id": batch_id, "updated_at": manifest["updated_at"], "items": results})
    _write_text_atomic(source_list_path, "".join(f"{entry.source}\n" for entry in entries))
    _write_csv_atomic(
        mapping_path,
        [
            "index",
            "status",
            "execution",
            "resume_used",
            "route",
            "source_path",
            "relative_path",
            "source_sha256",
            "run_root",
            "index_markdown",
            "summary_json",
            "validation_report",
            "progress_json",
            "final_stage",
            "completion_kind",
            "selected_page_count",
            "total_pages",
            "full_pdf_coverage",
            "returncode",
            "error",
            "started_at",
            "finished_at",
        ],
        _map_rows(results),
    )
    return manifest_path, results_path, mapping_path


def invoke_single_conversion(
    entry: SourceEntry,
    *,
    run_root: Path,
    pages: str,
    evidence_level: str,
    batch_size: int,
    timeout_seconds: int,
    resume: bool,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(GRAPH_ADAPTER),
        "--source",
        str(entry.source),
        "--pages",
        pages,
        "--run-root",
        str(run_root),
        "--evidence-level",
        evidence_level,
        "--batch-size",
        str(batch_size),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    command.extend(_graph_route_arguments(entry.route))
    if resume:
        command.append("--resume")
    started_at = now_iso()
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        stdout = proc.stdout[-12000:]
        stderr = proc.stderr[-12000:]
        returncode = proc.returncode
        error = None
    except OSError as exc:
        stdout = ""
        stderr = str(exc)[-12000:]
        returncode = None
        error = f"subprocess-error: {type(exc).__name__}: {exc}"
    finished_at = now_iso()
    child = _parse_child_result(stdout)
    summary_path = run_root / "summary.json"
    validation_report = run_root / "reports" / "validation-result.json"
    summary_status = _read_status(summary_path)
    validation_status = _read_status(validation_report)
    completion = _inspect_final_completion(
        run_root,
        source_path=str(entry.source),
        source_sha256=entry.source_sha256 or sha256(entry.source),
        requested_pages=pages,
    )
    status = "passed" if returncode == 0 and completion["accepted"] else "failed"
    child_status = None
    if child and child.get("final_status") in STATUS_VALUES:
        child_status = str(child["final_status"])
    elif child and child.get("status") in STATUS_VALUES:
        child_status = str(child["status"])
    if status == "failed" and returncode in {0, 1} and (
        child_status == "needs-review"
        or validation_status == "needs-review"
        or summary_status == "needs-review"
    ):
        status = "needs-review"
    if status == "failed" and error is None and not completion["accepted"]:
        error = f"incomplete-final-evidence: {completion['completion_kind']} at {completion['final_stage']}"
    return {
        **completion,
        "status": status,
        "execution": "completed",
        "resume_used": resume,
        "run_root": str(run_root),
        "child_result": child,
        "summary_status": summary_status,
        "validation_status": validation_status,
        "command": shlex.join(command),
        "returncode": returncode,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "started_at": started_at,
        "finished_at": finished_at,
        "error": error,
    }


def run_batch(
    entries: list[SourceEntry],
    *,
    batch_root: Path,
    origin: dict[str, Any],
    config: dict[str, Any],
    executor: Any = invoke_single_conversion,
) -> tuple[int, dict[str, Any]]:
    batch_root = batch_root.expanduser().resolve()
    _preflight_entries(entries)
    batch_root.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(batch_root)
    if manifest:
        _ensure_input_match(entries, manifest)
        batch_id = str(manifest.get("batch_id") or batch_root.name)
        created_at = str(manifest.get("created_at") or now_iso())
    else:
        batch_id = batch_root.name
        created_at = now_iso()
    existing_results = _load_results(batch_root)
    result_by_source = {
        str(item.get("source_path")): item
        for item in existing_results
        if item.get("source_path")
    }
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, 1):
        previous = result_by_source.get(str(entry.source))
        base = {
            "index": index,
            "source_path": str(entry.source),
            "relative_path": entry.relative_path,
            "route": entry.route or "native-only",
            "route_confidence": entry.route_confidence,
            "reason_codes": list(entry.reason_codes),
            "source_sha256": entry.source_sha256 or sha256(entry.source),
            "file_size": entry.file_size,
            "modified_at": entry.modified_at,
        }
        run_root = run_root_for_entry(batch_root, entry)
        if previous and _existing_success(previous, requested_pages=str(config["pages"])):
            item = {**previous, **base, "execution": "skipped-existing", "resume_used": bool(previous.get("resume_used"))}
            results.append(item)
            persist_batch_state(
                batch_root=batch_root,
                batch_id=batch_id,
                created_at=created_at,
                entries=entries,
                origin=origin,
                config=config,
                results=results,
            )
            print(
                f"BATCH_SKIP index={index}/{len(entries)} source={entry.source}",
                file=sys.stderr,
                flush=True,
            )
            continue
        resume = run_root.exists() and any(run_root.iterdir())
        print(
            f"BATCH_RUN index={index}/{len(entries)} resume={str(resume).lower()} source={entry.source}",
            file=sys.stderr,
            flush=True,
        )
        running_item = {
            **base,
            "status": "running",
            "execution": "running",
            "resume_used": resume,
            "run_root": str(run_root),
            "final_stage": "invocation",
            "completion_kind": "incomplete",
            "started_at": now_iso(),
        }
        results.append(running_item)
        persist_batch_state(
            batch_root=batch_root,
            batch_id=batch_id,
            created_at=created_at,
            entries=entries,
            origin=origin,
            config=config,
            results=results,
        )
        try:
            item = executor(
                entry,
                run_root=run_root,
                pages=str(config["pages"]),
                evidence_level=str(config["evidence_level"]),
                batch_size=int(config["batch_size"]),
                timeout_seconds=int(config["timeout_seconds"]),
                resume=resume,
            )
        except Exception as exc:
            item = {
                "status": "failed",
                "execution": "failed-invocation",
                "resume_used": resume,
                "run_root": str(run_root),
                "final_stage": "invocation",
                "completion_kind": "incomplete",
                "returncode": None,
                "error": f"executor-error: {type(exc).__name__}: {exc}",
                "started_at": running_item["started_at"],
                "finished_at": now_iso(),
            }
        results[-1] = {**base, **item}
        persist_batch_state(
            batch_root=batch_root,
            batch_id=batch_id,
            created_at=created_at,
            entries=entries,
            origin=origin,
            config=config,
            results=results,
        )
    summary = _summarise_results(results, planned_total=len(entries))
    manifest_path, results_path, mapping_path = persist_batch_state(
        batch_root=batch_root,
        batch_id=batch_id,
        created_at=created_at,
        entries=entries,
        origin=origin,
        config=config,
        results=results,
    )
    payload = {
        "status": summary["status"],
        "batch_id": batch_id,
        "batch_root": str(batch_root),
        "manifest": str(manifest_path),
        "results": str(results_path),
        "mapping_csv": str(mapping_path),
        "summary": summary,
    }
    return (0 if summary["status"] == "passed" else 1), payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", default=[], help="repeatable literal PDF path")
    parser.add_argument("--source-list", type=Path, help="UTF-8 text file, one PDF path per line")
    parser.add_argument("--route-list", type=Path, help="route-detection TXT list such as 原生文本路线.txt")
    parser.add_argument("--route-report", type=Path, help="route-detection report.json to select a route from")
    parser.add_argument(
        "--route",
        choices=sorted(ROUTE_VALUES),
        default="native-only",
        help="route to load from --route-report or to annotate literal sources",
    )
    parser.add_argument("--source-root", type=Path, help="preserve source-relative paths under this root")
    parser.add_argument("--pages", default="all")
    parser.add_argument("--evidence-level", default="compact")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-files", type=int, help="cap how many selected sources to process")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_BATCHES_PARENT)
    parser.add_argument("--batch-root", type=Path, help="exact batch root to create or resume")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        manifest = _load_manifest(args.batch_root.expanduser().resolve()) if args.batch_root else None
        entries, origin = discover_source_entries(
            sources=args.source,
            source_list=args.source_list,
            route_list=args.route_list,
            route_report=args.route_report,
            route=args.route,
            source_root=args.source_root,
            existing_manifest=manifest,
        )
        if args.max_files is not None:
            if args.max_files < 1:
                parser.error("--max-files must be a positive integer")
            entries = entries[: args.max_files]
        if not entries:
            parser.error("no sources selected")
        batch_root = (
            args.batch_root.expanduser().resolve()
            if args.batch_root
            else _timestamp_batch_root(args.output_root, f"lan-pdf-route-batch-{args.route}")
        )
        code, payload = run_batch(
            entries,
            batch_root=batch_root,
            origin=origin,
            config={
                "pages": args.pages,
                "evidence_level": args.evidence_level,
                "batch_size": args.batch_size,
                "timeout_seconds": args.timeout_seconds,
            },
        )
    except Exception as exc:
        print(f"ERROR handled=true {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
