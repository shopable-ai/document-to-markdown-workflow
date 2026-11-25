#!/usr/bin/env python3
"""Batch LAN PDF-to-Markdown conversions with route-aware resumable records."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Any, Iterator
import unicodedata
from urllib.parse import unquote, urlparse

try:
    from output_naming import package_timestamp, readable_package_stem
    import pdf_disk_guard as disk_guard
    import pdf_hybrid_output_validator as hybrid_validator
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import package_timestamp, readable_package_stem
    from . import pdf_disk_guard as disk_guard
    from . import pdf_hybrid_output_validator as hybrid_validator
    from . import pdf_resource_slots as resource_slots


ROOT = Path(__file__).resolve().parents[1]
GRAPH_ADAPTER = ROOT / "adapters" / "lan_pdf_markdown_graph.py"
DEFAULT_BATCHES_PARENT = Path("/Users/a0000/pdf_runs/lan-pdf-batches")
FORMAT_VERSION = "0.1.0"
FLAT_LAYOUT = "flat-v1"
LEGACY_LAYOUT = "nested-runs-v1"
STATUS_VALUES = {"passed", "failed", "needs-review"}
ROUTE_VALUES = {"native-only", "hybrid", "vision-only"}
FINAL_STAGE = "heading-audit-repair-validation"
FINAL_VALIDATION_REPORT = Path("reports/validation-result.json")
DISK_HEADROOM_REASON = disk_guard.PAUSE_REASON
RESIDUAL_PROCESSES_REASON = "residual-conversion-processes"
PAUSED_EXIT_CODE = disk_guard.PAUSE_EXIT
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
    inherited_fd: int | None = None,
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
    if inherited_fd is None:
        lock_handle = lock_path.open("a+", encoding="utf-8")
    else:
        try:
            fd_record = os.fstat(inherited_fd)
            if not stat.S_ISREG(fd_record.st_mode) or not os.path.samestat(fd_record, lock_path.stat()):
                raise ValueError("descriptor does not refer to the collection lock's regular file")
            if fcntl.fcntl(inherited_fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR:
                raise ValueError("descriptor must be readable and writable")
            lock_handle = os.fdopen(os.dup(inherited_fd), "r+", encoding="utf-8")
        except (OSError, ValueError, OverflowError) as exc:
            raise BatchLockError(f"invalid inherited collection lock fd {inherited_fd}: {exc}") from exc
    with lock_handle as handle:
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
            if inherited_fd is None:
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
    except (OSError, UnicodeError, json.JSONDecodeError):
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


def _manifest_pages(manifest: dict[str, Any] | None) -> str | None:
    config = (manifest or {}).get("config")
    pages = config.get("pages") if isinstance(config, dict) else None
    return pages if isinstance(pages, str) else None


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


def _package_layout(
    batch_root: Path, manifest: dict[str, Any] | None, entries: list[SourceEntry]
) -> str:
    if manifest is not None:
        # Manifests written before this field always used nested runs.
        layout = manifest.get("package_layout", LEGACY_LAYOUT)
        if layout not in (FLAT_LAYOUT, LEGACY_LAYOUT):
            raise ValueError(f"unsupported batch package layout: {layout!r}")
        return layout
    runs = batch_root / "runs"
    # An alias-only runs directory is not a second set of packages. A real
    # legacy tree without a manifest needs an exact expected package slot and
    # evidence of prior conversion before we adopt it.
    if runs.is_dir() and any(path.is_dir() and not path.is_symlink() for path in runs.iterdir()):
        for entry in entries:
            candidate = run_root_for_entry(batch_root, entry, layout=LEGACY_LAYOUT)
            if (candidate.is_dir() and candidate.resolve() == candidate
                    and any((candidate / marker).is_file() for marker in (
                        "plan.json", "summary.json", "progress.json", "index.md",
                        "evidence/progress-current.json", ".stages/native-smoke/evidence/progress-current.json",
                    ))):
                return LEGACY_LAYOUT
        raise ValueError(f"ambiguous legacy directory: {runs}; restore the batch manifest or select a new batch root")
    return FLAT_LAYOUT


def run_root_for_entry(batch_root: Path, entry: SourceEntry, *, layout: str = FLAT_LAYOUT) -> Path:
    if layout == LEGACY_LAYOUT:
        parents, leaf = _source_slot(entry)
        return batch_root / "runs" / Path(*parents) / leaf
    if layout != FLAT_LAYOUT:
        raise ValueError(f"unsupported batch package layout: {layout!r}")
    # Source identity includes the path: the same PDF bytes in two different
    # input locations remain separate sources, even with identical titles.
    fingerprint = entry.source_sha256 or sha256(entry.source)
    identity = hashlib.sha256(os.fsencode(entry.source) + bytes(1) + fingerprint.encode("ascii"))
    title = readable_package_stem(entry.source.stem, fallback="pdf", max_chars=48)
    return batch_root / f"{title}-{identity.hexdigest()[:16]}"


def _owned_package_root(batch_root: Path, value: object) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"invalid registered package path: {value!r}")
    path = Path(value).resolve()
    if not path.is_relative_to(batch_root):
        raise ValueError(f"package path outside batch: {path}")
    parts = path.relative_to(batch_root).parts
    if (not parts or parts[0].casefold() == "logs" or parts[0].startswith(".")
            or (parts[0].casefold() == "runs" and len(parts) < 2) or (path.exists() and not path.is_dir())):
        raise ValueError(f"invalid registered package path: {path}")
    return path


def _registered_run_roots(
    batch_root: Path, entries: list[SourceEntry], layout: str,
    manifest: dict[str, Any] | None, results: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    registered = (manifest or {}).get("run_roots", {})
    if not isinstance(registered, dict):
        raise ValueError("batch manifest run_roots must be an object")
    roots: dict[str, dict[str, str]] = {}
    used: dict[tuple[str, ...], Path] = {}
    for entry in entries:
        source = str(entry.source)
        fingerprint = sha256(entry.source)
        if entry.source_sha256 and entry.source_sha256 != fingerprint:
            raise ValueError(f"selected source fingerprint differs: {source}")
        previous = results.get(source)
        manifest_root = None
        if source in registered:
            registration = registered[source]
            if not isinstance(registration, dict) or registration.get("source_sha256") != fingerprint:
                raise ValueError(f"manifest package source fingerprint differs: {source}")
            manifest_root = _owned_package_root(batch_root, registration.get("path"))
        result_root = None
        if previous and previous.get("run_root"):
            if previous.get("source_sha256") != fingerprint:
                raise ValueError(f"registered package source fingerprint differs: {source}")
            result_root = _owned_package_root(batch_root, previous["run_root"])
        if manifest_root and result_root and manifest_root != result_root:
            raise ValueError(f"conflicting registered package paths: {source}")
        path = manifest_root or result_root or _owned_package_root(
            batch_root, str(run_root_for_entry(batch_root, replace(entry, source_sha256=fingerprint), layout=layout))
        )
        # macOS may resolve different case/Unicode spellings to the same
        # directory while Path.resolve() preserves the spelling supplied.
        key = tuple(unicodedata.normalize("NFC", part).casefold() for part in path.relative_to(batch_root).parts)
        if key in used:
            raise ValueError(f"multiple sources share a registered package path: {path}")
        used[key] = path
        roots[source] = {"path": str(path), "source_sha256": fingerprint}
    for key, path in used.items():
        if any(key[:i] in used for i in range(1, len(key))):
            raise ValueError(f"registered package paths overlap: {path}")
    return roots


def _matches_requested_pages(selector: str, selected: list[int], total: int) -> bool:
    if selector.strip().lower() == "all":
        return len(selected) == total and all(page == i for i, page in enumerate(selected, 1))
    requested: set[int] = set()
    try:
        for token in selector.split(","):
            if not token.strip():
                continue
            first, separator, last = token.strip().partition("-")
            start = int(first)
            end = int(last) if separator else start
            if not 1 <= start <= end <= total:
                return False
            requested.update(range(start, end + 1))
    except ValueError:
        return False
    return bool(requested) and selected == sorted(requested)


def _native_extraction_root(run_root: Path, summary: dict[str, Any]) -> Path | None:
    legacy_roots = (Path(".stages/native-pages"), Path("batch-run"))
    command = _read_json(run_root / "evidence" / "batch-command.json") or {}
    result = command.get("parsed_result")
    extracted = result.get("summary") if isinstance(result, dict) else None
    recorded = extracted.get("run_root") if isinstance(extracted, dict) else None
    if isinstance(recorded, str) and Path(recorded).is_absolute():
        origin = summary.get("run_root")
        for anchor in (run_root, Path(origin) if isinstance(origin, str) else run_root):
            if not Path(recorded).is_relative_to(anchor):
                continue
            relative = Path(recorded).relative_to(anchor)
            if relative == Path(".") or any(
                relative.is_relative_to(legacy) and len(relative.parts) <= len(legacy.parts) + 1
                for legacy in legacy_roots
            ):
                # Use the recorded layout even if its evidence was deleted.
                # A relocated package still uses the same package-local slot.
                candidate = run_root / relative
                return candidate if candidate.resolve() == candidate else None
    if (run_root / "pages").exists() or (run_root / "reports/page-completion.json").exists():
        return run_root
    legacy_candidates = []
    for relative in legacy_roots:
        candidate = run_root / relative
        if (candidate / "summary.json").is_file():
            legacy_candidates.append(candidate)
        else:
            legacy_candidates.extend(path.parent for path in candidate.glob("*/summary.json"))
    if len(legacy_candidates) == 1 and legacy_candidates[0].resolve() == legacy_candidates[0]:
        return legacy_candidates[0]
    return None


def _native_coverage_passed(report: dict[str, Any], selected: list[int]) -> bool:
    checks = report.get("checks")
    return (
        report.get("status") == "passed"
        and report.get("expected_page_sequence") == report.get("actual_page_sequence") == selected
        and isinstance(checks, dict)
        and all(checks.get(key) is True for key in (
            "page_unit_set_matches", "page_unit_markers_match", "merged_page_sequence_matches",
        ))
        and all(value is True for value in checks.values())
    )


def _native_original_page(
    extraction_root: Path, backup: Path, page: int, source_sha256: str
) -> tuple[int, int] | None:
    name = f"page-{page:03d}"
    package = extraction_root / "pages" / name
    # Match Graph._native_page_evidence: prefer the direct JSON; the nested
    # legacy page package is an alternative layout, not a second attempt.
    evidence_path = package / "evidence" / f"{name}.json"
    if not evidence_path.is_file():
        evidence_path = package / name / "evidence" / f"{name}.json"
    if evidence_path.resolve() != evidence_path or backup.resolve() != backup or not backup.is_file():
        return None
    evidence = _read_json(evidence_path) or {}
    text = evidence.get("text")
    if not (
        evidence.get("page") == page and evidence.get("status") == "passed"
        and evidence.get("source_sha256") == source_sha256 and evidence.get("model_calls") == []
        and isinstance(text, str) and text.strip()
    ):
        return None
    lines = text.splitlines()
    line_ids = [f"p{page}-native-l{index:04d}" for index, line in enumerate(lines, 1) if line.strip()]
    if evidence.get("line_count") != len(lines) or evidence.get("nonempty_line_ids") != line_ids:
        return None
    expected_backup = f"<!-- page: {page} -->\n\n## 第 {page} 页\n\n" + text.rstrip("\n") + "\n"
    if backup.read_bytes() != expected_backup.encode("utf-8"):
        return None
    return len(lines), len(line_ids)


def _replay_native_completion(
    run_root: Path, selected: list[int], index_bytes: bytes,
    *, summary: dict[str, Any], validation: dict[str, Any], source_sha256: str,
) -> bool:
    reports = run_root / "reports"
    heading = _read_json(reports / "heading-repair.json") or {}
    heading_checks = heading.get("validation")
    if not (
        heading.get("status") == "passed"
        and isinstance(heading_checks, dict)
        and heading_checks.get("heading_prefix_only") is True
        and heading_checks.get("page_markers_preserved") is True
        and heading.get("index_sha256") == hashlib.sha256(index_bytes).hexdigest()
    ):
        return False

    if not all(_native_coverage_passed(_read_json(reports / name) or {}, selected)
               for name in ("merge-coverage.json", "final-coverage.json")):
        return False
    cleanup = _read_json(reports / "page-cleanup.json") or {}
    cleanup_checks = cleanup.get("checks")
    if not (cleanup.get("status") == "passed" and cleanup.get("selected_pages") == selected
            and isinstance(cleanup_checks, dict) and cleanup_checks.get("body_rewrite") is False
            and all(cleanup_checks.get(key) is True for key in (
                "pre_cleanup_backup_exists", "all_page_units_nonempty", "only_complete_lines_deleted",
            ))):
        return False
    table = _read_json(reports / "native-table-font-validation.json") or {}
    detected = table.get("detected_table_count")
    if not (table.get("status") == "passed" and table.get("table_failures") == []
            and type(detected) is int and detected >= 0
            and detected == table.get("rendered_table_count")
            == validation.get("table_count_pdfplumber_find_tables") == validation.get("rendered_table_count")
            and all(isinstance(table.get(key), dict) and table[key] == validation.get(key)
                    for key in ("font_usage", "font_size_usage"))):
        return False

    units = run_root / "page-units"
    backups = run_root / "backups" / "page-units-pre-cleanup"
    raw_merge = run_root / "document-temp.md"
    expected_names = [f"p{page:03d}.md" for page in selected]
    merged_digest = hashlib.sha256()
    physical_lines = nonempty_lines = 0
    try:
        extraction_root = _native_extraction_root(run_root, summary)
        if extraction_root is None:
            return False
        extraction = _read_json(extraction_root / "summary.json") or {}
        completion = _read_json(extraction_root / "reports/page-completion.json") or {}
        assets = run_root / "assets"
        if not (
            extraction.get("status") == "passed" and extraction.get("source_sha256") == source_sha256
            and extraction.get("selected_pages") == selected and extraction.get("batch_size") == 1
            and extraction.get("model_calls") == [] and extraction.get("visual_model") == "not-run"
            and extraction.get("rendering") == "not-run" and extraction.get("old_summary_reused") is False
            and completion.get("status") == "passed"
            and completion.get("expected_pages") == completion.get("completed_pages") == selected
            and all(completion.get(key) == [] for key in hybrid_validator.COVERAGE_EXCEPTIONS)
            and assets.resolve() == assets and not any(assets.iterdir())
            and raw_merge.resolve() == raw_merge and raw_merge.is_file()
            and sha256(raw_merge) == heading.get("source_sha256")
        ):
            return False
        for directory in (units, backups):
            if directory.resolve() != directory or {
                path.name for path in directory.iterdir() if re.fullmatch(r"p\d+\.md", path.name)
            } != set(expected_names):
                return False
        for index, (page, name) in enumerate(zip(selected, expected_names)):
            original = _native_original_page(extraction_root, backups / name, page, source_sha256)
            if original is None:
                return False
            physical_lines += original[0]
            nonempty_lines += original[1]
            unit = units / name
            if unit.is_symlink() or not unit.is_file():
                return False
            text = unit.read_text(encoding="utf-8").rstrip()
            markers = re.findall(r"<!--\s*page\s*:\s*(\d+)\s*-->", text)
            if not text or [int(value) for value in markers] != [page]:
                return False
            if index:
                merged_digest.update(b"\n\n")
            merged_digest.update(text.encode("utf-8"))
    except (OSError, ValueError, RuntimeError):
        return False
    merged_digest.update(b"\n")
    return (
        # The heading source is document-temp.md, not the PDF.
        merged_digest.hexdigest() == heading.get("source_sha256")
        and physical_lines == validation.get("native_text_physical_lines")
        and nonempty_lines == validation.get("native_text_nonempty_line_ids")
    )


def _inspect_final_completion(
    run_root: Path,
    *,
    source_path: str | None,
    source_sha256: str | None,
    requested_pages: str,
    replay_native: bool = False,
    replay_hybrid: bool = False,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    index_path = run_root / "index.md"
    summary_path = run_root / "summary.json"
    hybrid_acceptance_path = run_root / "acceptance.json"
    hybrid_progress_path = run_root / "progress.json"
    native_progress_path = run_root / "evidence" / "progress-current.json"
    validation_path = run_root / FINAL_VALIDATION_REPORT
    validation = _read_json(validation_path)
    native_progress = _read_json(native_progress_path)
    # Native graph also publishes root-level acceptance/progress compatibility
    # files. The canonical validator and native stage checkpoint take priority.
    native_validator = str((validation or {}).get("format_version", "")).endswith("-pure-native-langgraph")
    native_final_stage = (native_progress or {}).get("current_stage") == FINAL_STAGE
    hybrid_contract = (
        (validation or {}).get("validator") == hybrid_validator.VALIDATOR
        or (not native_validator and not native_final_stage
            and hybrid_acceptance_path.is_file() and hybrid_progress_path.is_file())
    )
    progress_path = hybrid_progress_path if hybrid_contract else native_progress_path
    summary = _read_json(summary_path)
    progress = _read_json(hybrid_progress_path) if hybrid_contract else native_progress
    acceptance = _read_json(hybrid_acceptance_path) if hybrid_contract else None
    records = (summary, validation, progress, acceptance) if hybrid_contract else (summary, validation, progress)
    index_bytes = b""
    index_text = ""
    if index_path.is_file():
        try:
            index_bytes = index_path.read_bytes()
            index_text = index_bytes.decode("utf-8")
        except (OSError, UnicodeError):
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
    if (hybrid_contract and isinstance(progress_stages, list) and progress_stages
            and isinstance(progress_stages[-1], dict)):
        current_stage = str(progress_stages[-1].get("stage") or current_stage)
    selected_values = [
        payload.get("selected_pages")
        for payload in records
        if payload and isinstance(payload.get("selected_pages"), list)
    ]
    total_values = [
        payload.get("total_pages")
        for payload in records
        if payload and type(payload.get("total_pages")) is int and payload["total_pages"] > 0
    ]
    selected = selected_values[0] if selected_values else []
    total_pages = total_values[0] if total_values else None
    page_facts_agree = (
        len(selected_values) == len(records)
        and all(type(page) is int for value in selected_values for page in value)
        and all(value == selected for value in selected_values)
        and len(total_values) == len(records)
        and all(value == total_pages for value in total_values)
    )
    full_page_sequence = bool(
        total_pages and _matches_requested_pages("all", selected, total_pages)
    )
    acceptance_checks = (acceptance or {}).get("checks")
    hybrid_full_coverage = (
        isinstance(acceptance_checks, dict)
        and acceptance_checks.get("selected_pages_cover_full_pdf") is True
        and acceptance_checks.get("page_completion_passed") is True
    )
    full_coverage = bool(
        full_page_sequence
        and validation
        and validation.get("actual_full_pdf_coverage") is True
        and (not hybrid_contract or hybrid_full_coverage)
    )
    hashes_match = bool(source_sha256) and all(
        payload and payload.get("source_sha256") == source_sha256
        for payload in records
    )
    paths_match = all(
        not payload.get("source") or str(Path(str(payload["source"])).expanduser().resolve()) == source_path
        for payload in records
        if payload
    ) if source_path else True
    checks = (validation or {}).get("checks")
    validation_checks_pass = (
        isinstance(checks, dict) and bool(checks) and all(value is True for value in checks.values())
    )
    hybrid_checks_pass = True
    if hybrid_contract:
        hybrid_checks_pass = (
            bool(validation)
            and validation.get("validator") == hybrid_validator.VALIDATOR
            and validation.get("index_sha256") == hashlib.sha256(index_bytes).hexdigest()
            and isinstance(acceptance_checks, dict)
            and hybrid_full_coverage
            and all(
                value is True for name, value in acceptance_checks.items() if name != "no_model_calls_required"
            )
        )
        reader_expected = (
            hybrid_validator.READER_REQUIRED in (acceptance or {})
            or hybrid_validator.reader_stage_committed(summary or {}, progress or {})
            or any(hybrid_validator.path_present(run_root / path) for path in (
                hybrid_validator.READER_AUDIT, hybrid_validator.READER_VALIDATION, hybrid_validator.READER_PROJECTION,
            ))
        )
        if reader_expected:
            reader = _read_json(run_root / hybrid_validator.READER_VALIDATION) or {}
            hybrid_checks_pass = (
                hybrid_checks_pass and reader.get("status") == "passed" and reader.get("bytes_verified") is True
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
            all(payload is not None and payload.get("status") == "passed" for payload in records),
            current_stage == ("independent_acceptance" if hybrid_contract else FINAL_STAGE),
            final_stage_recorded,
            page_facts_agree,
            total_pages is not None and _matches_requested_pages(requested_pages, selected, total_pages),
            hashes_match,
            paths_match,
            validation_checks_pass,
            hybrid_checks_pass,
            full_coverage if hybrid_contract or requested_pages.strip().lower() == "all" else True,
        ]
    )
    if accepted and hybrid_contract and replay_hybrid:
        # The saved verdict can outlive page units, route evidence, or a
        # reader projection. Only reusing a package needs this live replay.
        try:
            replay = hybrid_validator.validate_package(run_root, Path(source_path)) if source_path else {}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            replay = {}
        accepted = (
            replay.get("status") == "passed"
            and replay.get("actual_full_pdf_coverage") is True
            and replay.get("source_sha256") == source_sha256
            and replay.get("selected_pages") == selected
            and replay.get("total_pages") == total_pages
            and replay.get("index_sha256") == (validation or {}).get("index_sha256")
        )
    elif accepted and not hybrid_contract and replay_native:
        accepted = _replay_native_completion(
            run_root, selected, index_bytes, summary=summary or {}, validation=validation or {},
            source_sha256=source_sha256 or "",
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


def _reusable_completion(
    item: dict[str, Any], *, requested_pages: str, replay_hybrid: bool = True
) -> dict[str, Any] | None:
    run_root = Path(item["run_root"])
    if not ((run_root / "index.md").is_file() and (run_root / FINAL_VALIDATION_REPORT).is_file()):
        return None
    completion = _inspect_final_completion(
        run_root,
        source_path=item["source_path"],
        source_sha256=item["source_sha256"],
        requested_pages=requested_pages,
        replay_native=True,
        replay_hybrid=replay_hybrid,
    )
    return completion if completion["accepted"] else None


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


def _summarise_results(
    items: list[dict[str, Any]], *, planned_total: int, halt_reason: str | None = None
) -> dict[str, Any]:
    statuses = [str(item.get("status")) for item in items]
    executions = [str(item.get("execution")) for item in items]
    failed = statuses.count("failed")
    needs_review = statuses.count("needs-review")
    passed = statuses.count("passed")
    skipped = executions.count("skipped-existing")
    running = statuses.count("running")
    terminal = passed + failed + needs_review
    pending = max(0, planned_total - terminal)
    status = "passed"
    if halt_reason == DISK_HEADROOM_REASON:
        status = "paused"
    elif halt_reason:
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
        "planned_total": planned_total,
        "completed_total": terminal,
        "pending_total": pending,
        "halted": halt_reason is not None,
        "halt_reason": halt_reason,
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
    package_layout: str,
    run_roots: dict[str, dict[str, str]],
    entries: list[SourceEntry],
    origin: dict[str, Any],
    config: dict[str, Any],
    results: list[dict[str, Any]],
    progress: dict[str, Any],
) -> tuple[Path, Path, Path]:
    manifest_path = batch_root / "manifest.json"
    results_path = batch_root / "results.json"
    mapping_path = batch_root / "source-output-map.csv"
    source_list_path = batch_root / "selected-sources.txt"
    progress_path = batch_root / "progress-current.json"
    manifest = {
        "format_version": FORMAT_VERSION,
        "batch_id": batch_id,
        "mode": "lan-pdf-route-aware-batch",
        "package_layout": package_layout,
        "run_roots": run_roots,
        "created_at": created_at,
        "updated_at": progress["updated_at"],
        "origin": origin,
        "config": config,
        "paths": {
            "batch_root": str(batch_root),
            "results": str(results_path),
            "mapping_csv": str(mapping_path),
            "selected_sources": str(source_list_path),
            "progress_current": str(progress_path),
        },
        "inputs": [_serialise_entry(entry) for entry in entries],
        "summary": progress["summary"],
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
    _write_json_atomic(progress_path, progress)
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
    key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    name = f"{resolved.name}-{key}"
    if (_load_manifest(resolved.parent) or {}).get("package_layout") == FLAT_LAYOUT:
        return resolved.parent / "logs" / name
    for ancestor in resolved.parents:
        if ancestor.name == "runs" and (ancestor.parent / "manifest.json").is_file():
            return ancestor.parent / "logs" / name
    return resolved.parent / ".batch-child-logs" / name


def _active_conversion_processes(entry: SourceEntry, run_root: Path) -> list[str]:
    """Return live conversion workers still anchored to the completed book."""

    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
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


def _residual_halt_evidence(entry: SourceEntry, run_root: Path, item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        processes = _active_conversion_processes(entry, run_root)
    except (OSError, subprocess.SubprocessError) as exc:
        # A failed probe is not confirmation that a previous worker is gone.
        detail = f"could not verify conversion workers cleared: {type(exc).__name__}: {exc}"
        evidence = {"residual_check_error": detail}
    else:
        if not processes:
            return None
        detail = "conversion workers are still active; queue stopped before starting the next book"
        evidence = {"residual_processes": processes, "residual_check_error": None}
    error = str(item.get("error") or "").strip()
    if detail not in error:
        error = f"{error}; {detail}" if error else detail
    return {
        **evidence,
        "status": "failed",
        "accepted": False,
        "queue_halted": True,
        "halt_reason": RESIDUAL_PROCESSES_REASON,
        "error": error,
    }


def invoke_single_conversion(
    entry: SourceEntry,
    *,
    run_root: Path,
    pages: str,
    evidence_level: str,
    batch_size: int,
    timeout_seconds: int,
    resume: bool,
    disk_floor_bytes: int | None = None,
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
                **({"env": {**os.environ, disk_guard.ENV: str(disk_floor_bytes)}}
                   if disk_floor_bytes is not None else {}),
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
    if (disk_floor_bytes is not None and returncode == PAUSED_EXIT_CODE and child
            and child.get("status") == "paused" and child.get("reason") == DISK_HEADROOM_REASON
            and isinstance(child.get("disk_pause"), dict)
            and child["disk_pause"].get("floor_bytes") == disk_floor_bytes):
        return {
            "status": "interrupted", "execution": "paused-for-disk", "accepted": False,
            "disk_pause": child["disk_pause"], "resume_used": resume, "run_root": str(run_root),
            "final_stage": child["disk_pause"].get("phase", "invocation"),
            "completion_kind": "incomplete", "returncode": returncode,
            "stdout_log": str(stdout_log), "stderr_log": str(stderr_log),
            "started_at": started_at, "finished_at": finished_at,
        }
    summary_path = run_root / "summary.json"
    validation_report = run_root / FINAL_VALIDATION_REPORT
    summary_status = _read_status(summary_path)
    validation_status = _read_status(validation_report)
    completion = _inspect_final_completion(
        run_root,
        source_path=str(entry.source),
        source_sha256=entry.source_sha256 or sha256(entry.source),
        requested_pages=pages,
        replay_native=True,
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
    min_free_bytes = config.get("min_free_bytes", 0)
    if type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError("min_free_bytes must be a nonnegative integer")
    disk_floor_bytes = config.get("disk_floor_bytes")
    if disk_floor_bytes is not None and (type(disk_floor_bytes) is not int
                                       or disk_floor_bytes < disk_guard.MINIMUM_FLOOR_BYTES):
        raise ValueError(f"disk_floor_bytes must be at least {disk_guard.MINIMUM_FLOOR_BYTES}")
    return {
        **config,
        "batch_size": 1,
        "min_free_bytes": min_free_bytes if disk_floor_bytes is None else disk_floor_bytes,
        "book_concurrency": 1,
        "machine_resource_policy": resource_slots.resource_policy_summary(),
    }


def _free_bytes_for_run_root(run_root: Path) -> int:
    destination = run_root
    while not destination.exists():
        destination = destination.parent
    return shutil.disk_usage(destination).free


def run_batch(
    entries: list[SourceEntry],
    *,
    batch_root: Path,
    origin: dict[str, Any],
    config: dict[str, Any],
    executor: Any = None,
    inherited_fd: int | None = None,
) -> tuple[int, dict[str, Any]]:
    batch_root = batch_root.expanduser().resolve()
    _preflight_entries(entries)
    effective_config = _validate_batch_config(config)
    with acquire_collection_lock(
        lock_root=batch_root.parent,
        batch_root=batch_root,
        entries=entries,
        inherited_fd=inherited_fd,
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
    layout = _package_layout(batch_root, manifest, entries)
    pages = str(config["pages"])
    previous_pages = _manifest_pages(manifest)
    if previous_pages is not None and pages != previous_pages:
        raise ValueError(
            f"existing batch page selector is {previous_pages!r}, not {pages!r}; "
            "use the original --pages or choose a new --batch-root"
        )
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
    run_roots = _registered_run_roots(batch_root, entries, layout, manifest, result_by_source)
    min_free_bytes = config["min_free_bytes"]
    disk_floor_bytes = config.get("disk_floor_bytes")
    if disk_floor_bytes is not None and any(entry.route not in {"hybrid", "vision-only"} for entry in entries):
        raise ValueError("--disk-floor-bytes requires the page-hybrid graph for all sources")
    bases: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    halt_reason = None
    disk_pause = None
    # Reconcile the entire queue before replacing any checkpoint. A killed
    # resume must not erase later rows, and final package evidence can have
    # reached disk before its result row did.
    for index, entry in enumerate(entries, 1):
        source = str(entry.source)
        previous = result_by_source.get(source)
        registration = run_roots[source]
        base = {
            "index": index,
            "source_path": source,
            "relative_path": entry.relative_path,
            "route": entry.route or "native-only",
            "route_confidence": entry.route_confidence,
            "reason_codes": list(entry.reason_codes),
            "source_sha256": registration["source_sha256"],
            "file_size": entry.file_size,
            "modified_at": entry.modified_at,
            "run_root": registration["path"],
        }
        bases[source] = base
        if previous and (
            previous.get("status") == "running"
            or previous.get("queue_halted") is True
            or previous.get("halt_reason")
            or (not previous.get("residual_cleared_at")
                and (previous.get("residual_processes") or previous.get("residual_check_error")))
        ):
            residual = _residual_halt_evidence(entry, Path(base["run_root"]), previous)
            if residual is not None:
                halt_reason = RESIDUAL_PROCESSES_REASON
                results[source] = {**previous, **base, **residual, "execution": "blocked-residual"}
                continue
            previous = dict(previous)
            previous.pop("queue_halted", None)
            previous.pop("halt_reason", None)
            previous["residual_cleared_at"] = now_iso()
        completion = _reusable_completion(base, requested_pages=pages)
        if completion is not None:
            item = {
                **(previous or {}), **base, **completion,
                "status": "passed", "execution": "skipped-existing",
                "resume_used": bool((previous or {}).get("resume_used")),
                "error": None,
            }
            results[source] = item
        elif previous is not None:
            item = {**previous, **base}
            if item.get("status") in {"running", "passed"}:
                status = "interrupted" if item["status"] == "running" else "pending"
                item.update(status=status, execution=status, accepted=False)
            results[source] = item

    def checkpoint(
        *, active_source: str | None = None, halt_reason: str | None = None
    ) -> tuple[tuple[Path, Path, Path], dict[str, Any]]:
        rows = [results[source] for source in bases if source in results]
        summary = _summarise_results(rows, planned_total=len(entries), halt_reason=halt_reason)
        progress = {
            "status": summary["status"],
            "active_source": active_source,
            "summary": summary,
            "updated_at": now_iso(),
            "pages": pages,
            "strict_accepted_count": sum(
                item.get("status") == "passed"
                and item.get("accepted") is True
                and item.get("full_pdf_coverage") is True
                for item in rows
            ),
            "halt_reason": halt_reason,
            **({"disk_floor_bytes": disk_floor_bytes} if disk_floor_bytes is not None else {}),
            **({"disk_pause": disk_pause} if halt_reason == DISK_HEADROOM_REASON and disk_pause else {}),
        }
        paths = persist_batch_state(
            batch_root=batch_root,
            batch_id=batch_id,
            created_at=created_at,
            package_layout=layout,
            run_roots=run_roots,
            entries=entries,
            origin=origin,
            config=config,
            results=rows,
            progress=progress,
        )
        return paths, progress

    invoke = invoke_single_conversion if executor is None else executor
    for index, entry in enumerate(entries, 1):
        if halt_reason is not None:
            break
        source = str(entry.source)
        base = bases[source]
        if results.get(source, {}).get("status") == "passed":
            print(
                f"BATCH_SKIP index={index}/{len(entries)} source={entry.source}",
                file=sys.stderr,
                flush=True,
            )
            continue
        run_root = Path(base["run_root"])
        if min_free_bytes:
            free = _free_bytes_for_run_root(run_root)
            if free < min_free_bytes or (disk_floor_bytes is not None and free == min_free_bytes):
                halt_reason = DISK_HEADROOM_REASON
                if disk_floor_bytes is not None:
                    disk_pause = {"free_bytes": free, "floor_bytes": disk_floor_bytes,
                                  "phase": "before-book", "page": None, "next_write_bytes": 0}
                break
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
            "final_stage": "invocation",
            "completion_kind": "incomplete",
            "started_at": now_iso(),
        }
        results[source] = running_item
        checkpoint(active_source=source)
        try:
            item = invoke(
                replace(entry, source_sha256=base["source_sha256"]) if executor is None else entry,
                run_root=run_root,
                pages=pages,
                evidence_level=str(config["evidence_level"]),
                batch_size=int(config["batch_size"]),
                timeout_seconds=int(config["timeout_seconds"]),
                resume=resume,
                **({"disk_floor_bytes": disk_floor_bytes}
                   if executor is None and disk_floor_bytes is not None else {}),
            )
            # The production invocation already inspected this exact package.
            # An injected executor's status alone is not independent evidence.
            if executor is not None and item.get("status") == "passed":
                completion = _reusable_completion(base, requested_pages=pages, replay_hybrid=False)
                if completion is None:
                    item = {**item, "status": "failed", "accepted": False, "error": "incomplete-final-evidence"}
                else:
                    item = {**item, **completion}
        except Exception as exc:
            item = {
                "status": "failed",
                "execution": "failed-invocation",
                "resume_used": resume,
                "final_stage": "invocation",
                "completion_kind": "incomplete",
                "returncode": None,
                "error": f"executor-error: {type(exc).__name__}: {exc}",
                "started_at": running_item["started_at"],
                "finished_at": now_iso(),
            }
        residual = _residual_halt_evidence(entry, run_root, item)
        results[source] = {**item, **base, **(residual or {})}
        if residual is not None:
            halt_reason = RESIDUAL_PROCESSES_REASON
            break
        if (disk_floor_bytes is not None and item.get("execution") == "paused-for-disk"
                and item.get("returncode") == PAUSED_EXIT_CODE):
            disk_pause = item["disk_pause"]
            halt_reason = DISK_HEADROOM_REASON
            break
        checkpoint()
    (manifest_path, results_path, mapping_path), progress = checkpoint(halt_reason=halt_reason)
    payload = {
        "status": progress["status"],
        "batch_id": batch_id,
        "batch_root": str(batch_root),
        "manifest": str(manifest_path),
        "results": str(results_path),
        "mapping_csv": str(mapping_path),
        "progress_current": str(batch_root / "progress-current.json"),
        "summary": progress["summary"],
        "strict_accepted_count": progress["strict_accepted_count"],
        "halt_reason": halt_reason,
    }
    if halt_reason == DISK_HEADROOM_REASON:
        payload["reason"] = halt_reason
        if disk_pause is not None:
            payload["disk_pause"] = disk_pause
        return PAUSED_EXIT_CODE, payload
    return (0 if progress["status"] == "passed" else 1), payload


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
    parser.add_argument("--source-root", type=Path, help="record source paths relative to this root in batch metadata")
    parser.add_argument("--pages", help="page selector; defaults to the resumed selection or all")
    parser.add_argument("--evidence-level", default="compact")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--min-free-bytes", type=int, default=0,
        help="pause before a new book if the destination has fewer free bytes (default: 0)",
    )
    parser.add_argument("--disk-floor-bytes", type=int,
                        help="explicit page-hybrid disk floor checked before books and at in-book write boundaries")
    parser.add_argument("--max-files", type=int, help="cap how many selected sources to process")
    parser.add_argument(
        "--order",
        choices=("input", "shortest-first"),
        default="input",
        help="source execution order; shortest-first prevents one large book blocking the queue",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_BATCHES_PARENT)
    parser.add_argument("--batch-root", type=Path, help="exact batch root to create or resume")
    parser.add_argument("--collection-lock-fd", type=int, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.min_free_bytes < 0:
        parser.error("--min-free-bytes must be a nonnegative integer")
    try:
        manifest = _load_manifest(args.batch_root.expanduser().resolve()) if args.batch_root else None
        previous_pages = _manifest_pages(manifest)
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
            inherited_fd=args.collection_lock_fd,
            config={
                "pages": args.pages if args.pages is not None else previous_pages or "all",
                "evidence_level": args.evidence_level,
                "batch_size": args.batch_size,
                "timeout_seconds": args.timeout_seconds,
                "min_free_bytes": args.min_free_bytes,
                **({"disk_floor_bytes": args.disk_floor_bytes} if args.disk_floor_bytes is not None else {}),
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
