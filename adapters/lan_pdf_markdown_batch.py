#!/usr/bin/env python3
"""Batch LAN PDF-to-Markdown conversions with route-aware resumable records."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

try:
    from output_naming import package_timestamp, readable_package_stem
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import package_timestamp, readable_package_stem
    from . import pdf_resource_slots as resource_slots


ROOT = Path(__file__).resolve().parents[1]
GRAPH_ADAPTER = ROOT / "adapters" / "lan_pdf_markdown_graph.py"
DEFAULT_BATCHES_PARENT = Path("/Users/a0000/pdf_runs/lan-pdf-batches")
FORMAT_VERSION = "0.1.0"
STATUS_VALUES = {"passed", "failed", "needs-review"}
ROUTE_VALUES = {"native-only", "hybrid", "vision-only"}
FINAL_STAGE = "heading-audit-repair-validation"
CONVERSION_PROCESS_NAMES = (
    "lan_pdf_markdown_batch.py",
    "lan_pdf_markdown_graph.py",
    "run_pdf_batches.py",
    "lan_pdf_vision_to_markdown.py",
    "apple_vision_pdf_to_markdown.py",
    "mac96_pdf_vision_runner.py",
)


class BatchLockError(RuntimeError):
    """Raised when another queue already owns one PDF collection."""


def _graph_route_arguments(route: str | None) -> list[str]:
    selected = route or "native-only"
    if selected == "native-only":
        return ["--no-model"]
    if selected == "hybrid":
        return []
    if selected == "vision-only":
        # Document-level detection says native text is insufficient, but the
        # production contract still requires page-hybrid routing.  That router
        # uses Apple Vision OCR for plain scanned text and reserves the LAN
        # visual model for pages with corroborated visual structure.  The
        # explicit full-document --visual-only mode is not implied here.
        return []
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


@contextmanager
def acquire_collection_lock(
    *,
    lock_root: Path,
    batch_root: Path,
    entries: list[SourceEntry],
) -> Iterator[Path]:
    """Allow only one top-level queue beneath the same collection root."""

    lock_root = lock_root.expanduser().resolve()
    lock_root.mkdir(parents=True, exist_ok=True)
    pause_path = lock_root / ".pdf-collection-paused.json"
    if pause_path.is_file():
        pause = _read_json(pause_path) or {}
        reason = str(pause.get("reason") or "manual-safety-pause")
        raise BatchLockError(
            f"PDF collection is paused at {lock_root}: {reason}; "
            f"resume only after clearing the recorded safety condition"
        )
    lock_path = lock_root / ".pdf-collection-queue.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip()
            detail = f" owner={owner}" if owner else ""
            raise BatchLockError(
                f"active PDF collection queue already owns {lock_root};{detail}"
            ) from exc
        owner = {
            "pid": os.getpid(),
            "batch_root": str(batch_root.expanduser().resolve()),
            "source_count": len(entries),
            "started_at": now_iso(),
        }
        handle.seek(0)
        handle.truncate()
        json.dump(owner, handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def _pdf_page_count(source: Path) -> int:
    completed = subprocess.run(
        ["pdfinfo", str(source)], capture_output=True, text=True, check=False, timeout=60
    )
    if completed.returncode != 0:
        raise ValueError(f"pdfinfo failed while ordering source: {source}")
    match = re.search(r"^Pages:\s+(\d+)\s*$", completed.stdout, re.MULTILINE)
    if not match:
        raise ValueError(f"pdfinfo returned no page count: {source}")
    return int(match.group(1))


def order_entries(entries: list[SourceEntry], order: str) -> list[SourceEntry]:
    if order == "input":
        return entries
    if order != "shortest-first":
        raise ValueError(f"unsupported source order: {order}")
    page_counts = {str(entry.source): _pdf_page_count(entry.source) for entry in entries}
    return sorted(
        entries,
        key=lambda entry: (page_counts[str(entry.source)], entry.relative_path.casefold()),
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


def entries_from_nonpassed_results(
    result_paths: list[Path], *, source_root: Path | None, route: str
) -> list[SourceEntry]:
    """Promote failed/needs-review sources into a stronger route queue."""
    entries: list[SourceEntry] = []
    seen: set[str] = set()
    for result_path in result_paths:
        payload = _read_json(result_path.expanduser().resolve())
        if not payload or not isinstance(payload.get("items"), list):
            raise ValueError(f"reroute results is missing an items list: {result_path}")
        for item in payload["items"]:
            if not isinstance(item, dict) or item.get("status") not in {"failed", "needs-review"}:
                continue
            if not item.get("source_path"):
                raise ValueError(f"non-passed reroute item has no source_path: {result_path}")
            source = Path(str(item["source_path"])).expanduser().resolve()
            key = str(source)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                _entry_from_path(
                    source,
                    source_root=source_root,
                    route=route,
                    source_sha256=str(item.get("source_sha256")) if item.get("source_sha256") else None,
                    file_size=int(item["file_size"]) if isinstance(item.get("file_size"), int) else None,
                    modified_at=str(item.get("modified_at")) if item.get("modified_at") else None,
                )
            )
    return entries


def _rebase_entries(entries: list[SourceEntry], source_root: Path) -> list[SourceEntry]:
    return [
        _entry_from_path(
            entry.source,
            source_root=source_root,
            route=entry.route,
            route_confidence=entry.route_confidence,
            source_sha256=entry.source_sha256,
            file_size=entry.file_size,
            modified_at=entry.modified_at,
            reason_codes=entry.reason_codes,
        )
        for entry in entries
    ]


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
    source_directory: Path | None,
    source_list: Path | None,
    route_list: Path | None,
    route_report: Path | None,
    route: str | None,
    source_root: Path | None,
    existing_manifest: dict[str, Any] | None = None,
) -> tuple[list[SourceEntry], dict[str, Any]]:
    if source_directory is not None:
        if sources or source_list is not None or route_list is not None or route_report is not None:
            raise ValueError(
                "--source-dir cannot be combined with --source, --source-list, "
                "--route-list, or --route-report"
            )
        directory = source_directory.expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"source directory does not exist: {directory}")
        direct_paths = sorted(
            (
                path.resolve()
                for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() == ".pdf"
            ),
            key=lambda path: str(path).casefold(),
        )
        if not direct_paths:
            raise ValueError(f"source directory contains no PDF files: {directory}")
        selected_route = route or "hybrid"
        entries = entries_from_paths(
            direct_paths,
            source_root=directory,
            route=selected_route,
        )
        return entries, {
            "kind": "source-directory",
            "route": selected_route,
            "source_directory": str(directory),
            "source_root": str(directory),
            "pdf_count": len(entries),
        }
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
    raise ValueError(
        "provide --source-dir, --source, --source-list, --route-list, or --route-report"
    )


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
    hybrid_acceptance_path = run_root / "acceptance.json"
    hybrid_progress_path = run_root / "progress.json"
    hybrid_contract = hybrid_acceptance_path.is_file() and hybrid_progress_path.is_file()
    validation_path = hybrid_acceptance_path if hybrid_contract else run_root / "reports" / "validation-result.json"
    progress_path = hybrid_progress_path if hybrid_contract else run_root / "evidence" / "progress-current.json"
    summary = _read_json(summary_path)
    validation = _read_json(validation_path)
    progress = _read_json(progress_path)
    index_text = ""
    if index_path.is_file():
        try:
            index_text = index_path.read_text(encoding="utf-8")
        except OSError:
            index_text = ""
    page_marker_sequence = [
        int(value)
        for value in re.findall(r"<!--\s*page:\s*(\d+)\s*-->", index_text)
    ]
    page_marker_count = len(page_marker_sequence)
    image_references = re.findall(
        r"!\[[^\]]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)]*)?\)", index_text
    )
    missing_image_references: list[str] = []
    valid_image_reference_count = 0
    for raw_reference in image_references:
        parsed = urlparse(raw_reference)
        if parsed.scheme or raw_reference.startswith(("#", "data:")):
            valid_image_reference_count += 1
            continue
        target = (run_root / unquote(parsed.path)).resolve()
        try:
            target.relative_to(run_root)
        except ValueError:
            missing_image_references.append(raw_reference)
            continue
        if target.is_file() and target.stat().st_size > 0:
            valid_image_reference_count += 1
        else:
            missing_image_references.append(raw_reference)
    meaningful_lines = [
        line
        for line in index_text.splitlines()
        if line.strip()
        and not re.match(r"^\s*<!--.*-->\s*$", line)
        and not re.match(r"^\s*#{1,6}\s+", line)
        and not re.match(r"^\s*!\[[^\]]*\]\(", line)
    ]
    meaningful_text_chars = len(re.sub(r"\s+", "", "\n".join(meaningful_lines)))
    incomplete_placeholder_present = "本页转换尚未完成" in index_text
    package_files = [path for path in run_root.rglob("*") if path.is_file()]
    markdown_files = [path for path in package_files if path.suffix.lower() == ".md"]
    package_total_bytes = sum(path.stat().st_size for path in package_files)
    markdown_total_bytes = sum(path.stat().st_size for path in markdown_files)
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
    progress_stages = progress.get("stages") if progress else None
    if hybrid_contract and isinstance(progress_stages, list) and progress_stages:
        current_stage = str(progress_stages[-1].get("stage") or current_stage)
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
    full_page_sequence = bool(
        total_pages and selected == list(range(1, total_pages + 1))
    )
    validation_checks = validation.get("checks") if validation else None
    full_coverage = bool(
        full_page_sequence
        and validation
        and (
            validation.get("actual_full_pdf_coverage") is True
            or (
                hybrid_contract
                and isinstance(validation_checks, dict)
                and validation_checks.get("selected_pages_cover_full_pdf") is True
                and validation_checks.get("page_completion_passed") is True
            )
        )
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
    checks = validation_checks
    ignored_policy_checks = {"no_model_calls_required"} if hybrid_contract else set()
    validation_checks_pass = isinstance(checks, dict) and bool(checks) and all(
        value is True
        for name, value in checks.items()
        if name not in ignored_policy_checks
    )
    completed_stages = progress.get("completed_stages") if progress else None
    if hybrid_contract:
        final_stage_recorded = bool(
            isinstance(progress_stages, list)
            and any(
                isinstance(item, dict)
                and item.get("stage") == "independent_acceptance"
                and item.get("status") == "passed"
                for item in progress_stages
            )
        )
    else:
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
            meaningful_text_chars > 0 or valid_image_reference_count > 0,
            not incomplete_placeholder_present,
            bool(selected) and page_marker_sequence == selected,
            not missing_image_references,
            summary is not None and summary.get("status") == "passed",
            validation is not None and validation.get("status") == "passed",
            progress is not None and progress.get("status") == "passed",
            current_stage == ("independent_acceptance" if hybrid_contract else FINAL_STAGE),
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
        "index_bytes": index_path.stat().st_size if index_path.is_file() else 0,
        "index_characters": len(index_text),
        "meaningful_text_characters": meaningful_text_chars,
        "page_marker_count": page_marker_count,
        "page_marker_sequence": page_marker_sequence,
        "image_reference_count": len(image_references),
        "valid_image_reference_count": valid_image_reference_count,
        "missing_image_references": missing_image_references,
        "incomplete_placeholder_present": incomplete_placeholder_present,
        "package_file_count": len(package_files),
        "package_total_bytes": package_total_bytes,
        "markdown_file_count": len(markdown_files),
        "markdown_total_bytes": markdown_total_bytes,
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
    halted_items = [item for item in items if item.get("queue_halted") is True]
    status = "passed"
    if halted_items:
        status = "failed"
    elif running or pending:
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
        "halted": bool(halted_items),
        "halt_reason": halted_items[-1].get("halt_reason") if halted_items else None,
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
                "index_bytes": item.get("index_bytes"),
                "index_characters": item.get("index_characters"),
                "meaningful_text_characters": item.get("meaningful_text_characters"),
                "page_marker_count": item.get("page_marker_count"),
                "page_marker_sequence": item.get("page_marker_sequence"),
                "image_reference_count": item.get("image_reference_count"),
                "valid_image_reference_count": item.get("valid_image_reference_count"),
                "missing_image_references": item.get("missing_image_references"),
                "incomplete_placeholder_present": item.get("incomplete_placeholder_present"),
                "package_file_count": item.get("package_file_count"),
                "package_total_bytes": item.get("package_total_bytes"),
                "markdown_file_count": item.get("markdown_file_count"),
                "markdown_total_bytes": item.get("markdown_total_bytes"),
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
            "index_bytes",
            "index_characters",
            "meaningful_text_characters",
            "page_marker_count",
            "page_marker_sequence",
            "image_reference_count",
            "valid_image_reference_count",
            "missing_image_references",
            "incomplete_placeholder_present",
            "package_file_count",
            "package_total_bytes",
            "markdown_file_count",
            "markdown_total_bytes",
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


def _read_log_tail(path: Path, *, max_bytes: int = 12000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            payload = handle.read(max_bytes)
    except OSError:
        return ""
    return payload.decode("utf-8", errors="replace").strip()


def _child_log_root(run_root: Path) -> Path:
    resolved = run_root.expanduser().resolve()
    for ancestor in resolved.parents:
        if ancestor.name == "runs":
            key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
            return ancestor.parent / "logs" / f"{resolved.name}-{key}"
    key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return resolved.parent / ".batch-child-logs" / f"{resolved.name}-{key}"


def _active_conversion_processes(entry: SourceEntry, run_root: Path) -> list[str]:
    """Return live conversion workers still anchored to the completed book."""

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    anchors = (str(entry.source), str(run_root.expanduser().resolve()))
    matches: list[str] = []
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if not any(name in command for name in CONVERSION_PROCESS_NAMES):
            continue
        if any(anchor in command for anchor in anchors):
            matches.append(line)
    return matches


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
    # Keep supervisor logs outside the package.  A new graph run must see an
    # empty/nonexistent run_root, and accepted package contents remain owned by
    # the conversion workflow rather than its batch supervisor.
    log_root = _child_log_root(run_root)
    log_root.mkdir(parents=True, exist_ok=True)
    invocation_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}"
    stdout_log = log_root / f"{invocation_id}.stdout.log"
    stderr_log = log_root / f"{invocation_id}.stderr.log"
    try:
        with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            proc = subprocess.run(
                command,
                cwd=ROOT,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
        stdout = _read_log_tail(stdout_log)
        stderr = _read_log_tail(stderr_log)
        returncode = proc.returncode
        error = None
    except OSError as exc:
        stdout = _read_log_tail(stdout_log)
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
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "started_at": started_at,
        "finished_at": finished_at,
        "error": error,
    }


def _validate_batch_config(config: dict[str, Any]) -> dict[str, Any]:
    batch_size = int(config.get("batch_size", 1))
    if batch_size != 1:
        raise ValueError(
            "route-aware PDF batch_size must be 1; no books started"
        )
    timeout_seconds = int(config.get("timeout_seconds", 0))
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    return {
        **config,
        "batch_size": 1,
        "book_concurrency": 1,
        "machine_resource_policy": resource_slots.resource_policy_summary(),
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
    effective_config = _validate_batch_config(config)
    with acquire_collection_lock(
        lock_root=batch_root.parent,
        batch_root=batch_root,
        entries=entries,
    ):
        return _run_batch_unlocked(
            entries,
            batch_root=batch_root,
            origin=origin,
            config=effective_config,
            executor=executor,
        )


def _run_batch_unlocked(
    entries: list[SourceEntry],
    *,
    batch_root: Path,
    origin: dict[str, Any],
    config: dict[str, Any],
    executor: Any,
) -> tuple[int, dict[str, Any]]:
    batch_root = batch_root.expanduser().resolve()
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
        residual_processes = _active_conversion_processes(entry, run_root)
        if residual_processes:
            previous_error = str(item.get("error") or "").strip()
            residual_error = (
                "book process exited while conversion workers were still active; "
                "queue stopped before starting the next book"
            )
            item = {
                **item,
                "status": "failed",
                "queue_halted": True,
                "halt_reason": "residual-conversion-processes",
                "residual_processes": residual_processes,
                "error": f"{previous_error}; {residual_error}" if previous_error else residual_error,
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
        if residual_processes:
            break
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
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="recursively convert every PDF under this directory; defaults to hybrid page routing",
    )
    parser.add_argument("--source", type=Path, action="append", default=[], help="repeatable literal PDF path")
    parser.add_argument("--source-list", type=Path, help="UTF-8 text file, one PDF path per line")
    parser.add_argument("--route-list", type=Path, help="route-detection TXT list such as 原生文本路线.txt")
    parser.add_argument("--route-report", type=Path, help="route-detection report.json to select a route from")
    parser.add_argument(
        "--reroute-results",
        type=Path,
        action="append",
        default=[],
        help="repeatable prior results.json; append only failed/needs-review sources using --route",
    )
    parser.add_argument(
        "--route",
        choices=sorted(ROUTE_VALUES),
        help="route to load from --route-report or annotate sources; source directories default to hybrid",
    )
    parser.add_argument("--source-root", type=Path, help="preserve source-relative paths under this root")
    parser.add_argument("--pages", default="all")
    parser.add_argument("--evidence-level", default="compact")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-files", type=int, help="cap how many selected sources to process")
    parser.add_argument(
        "--order",
        choices=("input", "shortest-first"),
        default="input",
        help="source execution order; shortest-first prevents one large book blocking the queue",
    )
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
            source_directory=args.source_dir,
            source_list=args.source_list,
            route_list=args.route_list,
            route_report=args.route_report,
            route=args.route,
            source_root=args.source_root,
            existing_manifest=manifest,
        )
        explicit_source_root = args.source_root.expanduser().resolve() if args.source_root else None
        if explicit_source_root is not None:
            entries = _rebase_entries(entries, explicit_source_root)
        if args.reroute_results:
            rerouted = entries_from_nonpassed_results(
                args.reroute_results,
                source_root=explicit_source_root,
                route=args.route,
            )
            known = {str(entry.source) for entry in entries}
            entries.extend(entry for entry in rerouted if str(entry.source) not in known)
            origin = {
                **origin,
                "reroute_results": [str(path.expanduser().resolve()) for path in args.reroute_results],
                "rerouted_count": len([entry for entry in rerouted if str(entry.source) not in known]),
            }
        entries = order_entries(entries, args.order)
        if args.max_files is not None:
            if args.max_files < 1:
                parser.error("--max-files must be a positive integer")
            entries = entries[: args.max_files]
        if not entries:
            parser.error("no sources selected")
        batch_root = (
            args.batch_root.expanduser().resolve()
            if args.batch_root
            else _timestamp_batch_root(
                args.output_root,
                f"lan-pdf-route-batch-{origin.get('route') or 'native-only'}",
            )
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
                "order": args.order,
            },
        )
    except Exception as exc:
        print(f"ERROR handled=true {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
