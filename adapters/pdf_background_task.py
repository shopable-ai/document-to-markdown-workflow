#!/usr/bin/env python3
"""Run long PDF conversions outside the calling Codex conversation.

The manager owns process lifecycle and compact status only.  Conversion
stdout/stderr and detailed progress stay on disk; no worker output is relayed
to the caller.  The public CLI is intentionally limited to one JSON snapshot
per explicit start/status/cancel/resume command.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from output_naming import resolve_package_target
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import resolve_package_target
    from . import pdf_resource_slots as resource_slots


ROOT = Path(__file__).resolve().parents[1]
GRAPH_ADAPTER = ROOT / "adapters" / "lan_pdf_markdown_graph.py"
BATCH_ADAPTER = ROOT / "adapters" / "run_pdf_batches.py"
COLLECTION_ADAPTER = ROOT / "adapters" / "lan_pdf_markdown_batch.py"
COLLECTION_TASK = "pdf-collection"
COLLECTION_MIN_FREE_BYTES = 2 * 1024**3
COLLECTION_MIN_DISK_FLOOR_BYTES = 500_000_000
COLLECTION_CHECKPOINT_STALE_SECONDS = 300
COLLECTION_OBSERVATION_JSON_MAX_BYTES = 2 * 1024**2
WORKFLOW_A_ADAPTER = ROOT / "adapters" / "workflow_a_source_runner.py"
LOCAL_OCR_ADAPTER = ROOT / "adapters" / "apple_vision_pdf_to_markdown.py"
DEFAULT_STATE_ROOT = Path.home() / ".codex" / "background-tasks"
ALLOWED_STATUSES = {
    "queued",
    "running",
    "paused",
    "cancelled",
    "failed",
    "needs-review",
    "completed",
    "budget-exhausted",
}
TERMINAL_STATUSES = {"cancelled", "failed", "needs-review", "completed", "budget-exhausted"}
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _append_event(path: Path, state: dict[str, Any], event: str) -> None:
    payload = {
        "run_id": state["run_id"],
        "event": event,
        "status": state["status"],
        "updated_at": state["updated_at"],
        "completed_pages": state.get("completed_pages", 0),
        "failed_pages": state.get("failed_pages", 0),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"invalid run_id: {run_id!r}")
    return run_id


def _task_root(state_root: Path, run_id: str) -> Path:
    return state_root.expanduser().resolve() / _validate_run_id(run_id)


@contextmanager
def _task_lock(task_root: Path) -> Iterator[None]:
    task_root.mkdir(parents=True, exist_ok=True)
    lock_path = task_root / ".state.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_state(state_root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    task_root = _task_root(state_root, run_id)
    state = _read_json(task_root / "progress-current.json")
    if not state:
        raise ValueError(f"unknown run_id: {run_id}")
    if state.get("status") not in ALLOWED_STATUSES:
        raise ValueError(f"invalid task status for {run_id}: {state.get('status')!r}")
    state.setdefault("task_root", str(task_root))
    state.setdefault("log_path", str(task_root / "run.log"))
    state.setdefault("task_type", "unknown")
    return task_root, state


def _transition(
    task_root: Path,
    state: dict[str, Any],
    status: str,
    *,
    event: str | None = None,
    **updates: Any,
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported task status: {status}")
    updated = {**state, **updates, "status": status, "updated_at": now_iso()}
    _write_json_atomic(task_root / "progress-current.json", updated)
    if event:
        _append_event(task_root / "progress.jsonl", updated, event)
    return updated


def _process_record(pid: int) -> tuple[int, str] | None:
    if pid <= 0:
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pgid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return None
    try:
        pgid_raw, command = line.split(None, 1)
        return int(pgid_raw), command
    except ValueError:
        return None


def _task_is_active(state: dict[str, Any]) -> bool:
    pid = int(state.get("pid") or 0)
    expected_pgid = int(state.get("process_group_id") or 0)
    token = str(state.get("worker_token") or "")
    record = _process_record(pid)
    if record is None or not token:
        return False
    actual_pgid, command = record
    return actual_pgid == expected_pgid and token in command and "_worker" in command


def _process_group_members(pgid: int) -> list[dict[str, Any]]:
    if pgid <= 0:
        return []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot verify process group {pgid}: {exc}") from exc
    members: list[dict[str, Any]] = []
    for raw in result.stdout.splitlines():
        parts = raw.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, candidate_pgid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if candidate_pgid == pgid:
            members.append({"pid": pid, "command": parts[2]})
    return members


def _count_pages(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, list):
        return len(value)
    return 0


def _timestamp(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _collection_progress(state: dict[str, Any]) -> dict[str, Any]:
    path = state.get("work_progress_path")
    if not path:
        return {}
    progress = _read_json(Path(path)) or {}
    activation = _timestamp(state.get("resumed_at") or state.get("started_at"))
    updated = _timestamp(progress.get("updated_at"))
    return progress if activation is not None and updated is not None and updated >= activation else {}


def _refresh_collection_counts(state: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "work_status": progress.get("status"),
        "batch_summary": progress.get("summary"),
        "active_source": progress.get("active_source"),
        "strict_accepted_count": progress.get("strict_accepted_count", 0),
        "halt_reason": progress.get("halt_reason"),
        "batch_manifest_seen": state.get("batch_manifest_seen", False)
        or (Path(state["run_root"]) / "manifest.json").is_file(),
    }


def _refresh_page_counts(state: dict[str, Any]) -> dict[str, Any]:
    """Read one latest snapshot; never read or replay progress.jsonl."""
    progress_path_raw = state.get("work_progress_path")
    if not progress_path_raw:
        return state
    if state.get("task_type") == COLLECTION_TASK:
        return _refresh_collection_counts(state, _collection_progress(state))
    progress = _read_json(Path(str(progress_path_raw)))
    if not progress:
        return state
    coverage = progress.get("page_completion")
    page_counts = coverage if isinstance(coverage, dict) else progress
    completed = _count_pages(
        page_counts.get(
            "completed_pages",
            page_counts.get("processed_pages", page_counts.get("passed_count", 0)),
        )
    )
    if completed == 0 and progress.get("status") in {"passed", "completed"}:
        completed = _count_pages(progress.get("selected_pages", 0))
    failed = _count_pages(page_counts.get("failed_pages", 0))
    return {
        **state,
        "completed_pages": completed,
        "failed_pages": failed,
        "work_status": progress.get("status", progress.get("final_status")),
    }


def _collection_observation_json(path: Path) -> dict[str, Any]:
    # Secondary evidence is optional; do not load large failure snapshots
    # that may contain per-page subprocess output.
    try:
        if path.stat().st_size > COLLECTION_OBSERVATION_JSON_MAX_BYTES:
            return {}
    except OSError:
        return {}
    return _read_json(path) or {}


def _collection_active_package(
    batch_root: Path, progress: dict[str, Any], source: str,
) -> tuple[Path, str | None] | None:
    # The batch writes both registries before it publishes progress-current.
    # Prefer the smaller manifest, and don't join different generations.
    generation = progress["updated_at"]
    manifest = _collection_observation_json(batch_root / "manifest.json")
    run_roots = manifest.get("run_roots")
    entry = run_roots.get(source) if manifest.get("updated_at") == generation and isinstance(run_roots, dict) else None
    if isinstance(entry, dict):
        raw_path, fingerprint = entry.get("path"), entry.get("source_sha256")
    else:
        results = _collection_observation_json(batch_root / "results.json")
        items = results.get("items")
        if results.get("updated_at") != generation or not isinstance(items, list):
            return None
        row = next((item for item in items if isinstance(item, dict)
                    and item.get("source_path") == source and item.get("status") == "running"), None)
        if row is None:
            return None
        raw_path, fingerprint = row.get("run_root"), row.get("source_sha256")
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        return None
    try:
        package = Path(raw_path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if package != batch_root and package.is_relative_to(batch_root):
        return package, fingerprint if isinstance(fingerprint, str) else None
    return None


def _process_option(command: str, option: str) -> str | None:
    # ps may display a path containing spaces without quotes. Read through
    # the next option rather than splitting the reconstructed command line.
    match = re.search(rf"(?:^|\s){option}\s+(.+?)(?=\s+--[a-z][a-z-]*(?:\s|$)|$)", command)
    if not match:
        return None
    value = match[1]
    if value.startswith(("'", '"')):
        try:
            parts = shlex.split(value)
        except ValueError:
            return None
        return parts[0] if len(parts) == 1 else None
    return value


def _collection_process_phase(
    pgid: int, source: str, package: Path,
) -> tuple[str, int | None] | None:
    try:
        members = _process_group_members(pgid)
    except RuntimeError:
        return None
    for script, option, subcommand, phase, source_required in (
        ("lan_pdf_vision_to_markdown.py", "--run-root", "validate", "independent-acceptance", True),
        ("apple_vision_pdf_to_markdown.py", "--run-root", None, "apple-vision-ocr", True),
        ("lan_pdf_vision_to_markdown.py", "--run-root", "convert", "page-visual-conversion", True),
        ("lan_pdf_to_markdown.py", "--output-root", None, "page-native-extraction", True),
        ("page_markdown_noise_cleanup.py", "--page-units", None, "page-markdown-cleanup", False),
        ("lan_markdown_heading_repair.py", "--run-root", None, "heading-audit-repair", False),
    ):
        adapter_path = str(ROOT / "adapters" / script)
        adapter = "(?:" + "|".join(re.escape(value) for value in (adapter_path, shlex.quote(adapter_path))) + ")"
        if subcommand:
            adapter += rf"\s+{subcommand}"
        for member in members:
            command = member["command"]
            if not re.search(rf"(?:^|\s){adapter}(?=\s|$)", command):
                continue
            target = _process_option(command, option)
            if target is None or not Path(target).is_relative_to(package):
                continue
            if source_required and _process_option(command, "--source") != source:
                continue
            page = _process_option(command, "--pages") if source_required else None
            return phase, int(page) if page and re.fullmatch(r"[1-9][0-9]{0,8}", page) else None
    return None


def _checkpoint_mtime(path: Path, since: float) -> float | None:
    try:
        updated = path.stat().st_mtime
    except OSError:
        return None
    return updated if updated >= since else None


def _collection_page_checkpoint(
    package: Path, fingerprint: str | None, since: float, *, hybrid: bool, scan_ocr: bool = False,
) -> tuple[float, int, str] | None:
    """Stat only this book's flat checkpoint directory; read just the newest page."""
    directory = package / ("evidence/reader-scan-ocr" if scan_ocr else "evidence/page-routes" if hybrid else "pages")
    json_page = hybrid or scan_ocr
    pattern = r"p([0-9]{3,9})\.json" if json_page else r"page-([0-9]{3,9})"
    newest = None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                match = re.fullmatch(pattern, entry.name)
                if not match:
                    continue
                path = Path(entry.path)
                paths = (path,) if json_page else (path / "summary.json", path / entry.name / "summary.json")
                for candidate in paths:
                    updated = _checkpoint_mtime(candidate, since)
                    if updated is not None and (newest is None or updated > newest[0]):
                        newest = (updated, int(match[1]), candidate)
    except OSError:
        return None
    if newest is None:
        return None
    updated, page, path = newest
    record = _collection_observation_json(path)
    status = record.get("status")
    statuses = {"ok", "layout_uncertain", "unavailable", "failed"} if scan_ocr else {"passed", "failed", "needs-review"}
    if (not isinstance(status, str) or status not in statuses
            or (json_page and record.get("page") != page)
            or (scan_ocr and (not fingerprint or record.get("schema") != "pdf-scan-ocr"))
            or (fingerprint and record.get("source_sha256") != fingerprint)):
        return None
    return updated, page, status


def _collection_observation(
    state: dict[str, Any], progress: dict[str, Any], *, active: bool,
) -> dict[str, Any]:
    updated = _timestamp(progress.get("updated_at"))
    activation = _timestamp(state.get("resumed_at") or state.get("started_at"))
    phase = ("between-sources" if progress else "queue-preparing") if active else state["status"]
    phase_source = "queue" if active else "lifecycle"
    current_page = checkpoint_page = None
    kind = "batch" if updated is not None else None
    checkpoint_status = progress.get("status")
    source = progress.get("active_source")
    if active and isinstance(source, str) and updated is not None:
        phase = "source-starting"
        since = updated  # Batch publishes dispatch before invoking the book.
        registered = _collection_active_package(Path(state["run_root"]), progress, source)
        if registered:
            package, fingerprint = registered
            hybrid = state["route"] == "hybrid"
            page_checkpoint = _collection_page_checkpoint(package, fingerprint, since, hybrid=hybrid)
            if page_checkpoint:
                updated, checkpoint_page, checkpoint_status = page_checkpoint
                kind, phase, phase_source = "page", "page-processing", "checkpoint"
            for relative, fallback in (
                ("plan.json", "page-processing"),
                ("reports/page-completion.json", "book-finalization"),
                ("progress.json" if hybrid else "evidence/progress-current.json", "book-finalization"),
            ):
                path = package / relative
                at = _checkpoint_mtime(path, since)
                if at is None:
                    continue
                if relative == "reports/page-completion.json":
                    # Later page-route rewrites during cleanup need not mean
                    # page processing is still the most recent phase.
                    phase, phase_source = fallback, "checkpoint"
                    if at > updated:
                        updated, checkpoint_page, checkpoint_status, kind = at, None, None, "book"
                    continue
                record = _collection_observation_json(path)
                recorded_source = record.get("source")
                if isinstance(recorded_source, dict):
                    recorded_source = recorded_source.get("path")
                if recorded_source != source or (record.get("source_sha256") and record["source_sha256"] != fingerprint):
                    continue
                if at >= updated:
                    reader = record.get("reader_visual")
                    stage = (reader.get("stage") if isinstance(reader, dict) else None) or record.get("current_stage") or record.get("stage")
                    phase, phase_source = stage or fallback, "checkpoint"
                    updated, checkpoint_page, checkpoint_status, kind = at, None, record.get("status"), "book"
            if hybrid and phase == "reader_visual_projection" and checkpoint_status == "running":
                audit_mtime = _checkpoint_mtime(package / "reports/reader-visual-audit.json", updated)
                if audit_mtime is not None:
                    updated, phase = audit_mtime, "reader-projection-verification"
                else:
                    scan = _collection_page_checkpoint(package, fingerprint, updated, hybrid=True, scan_ocr=True)
                    if scan:
                        updated, checkpoint_page, checkpoint_status = scan
                        kind, phase = "scan-ocr", "reader-scan-ocr"
            live = _collection_process_phase(int(state.get("process_group_id") or 0), source, package)
            if live:
                phase, current_page = live
                phase_source = "process"
    elif not active and state.get("worker_identity_unverified"):
        phase = "worker-unverified"

    now = time.time()
    age = max(0, now - updated) if updated is not None else None
    waiting_age = age if age is not None else (now - activation if activation is not None else None)
    if not active:
        freshness = "not-running"
    elif waiting_age is not None and waiting_age >= COLLECTION_CHECKPOINT_STALE_SECONDS:
        freshness = "no-recent-checkpoint"
    else:
        freshness = "recent" if age is not None else "awaiting-checkpoint"
    return {
        "current_phase": phase,
        "phase_source": phase_source,
        "current_page": current_page,
        "progress_checkpoint": {
            "kind": kind,
            "page": checkpoint_page,
            "status": checkpoint_status,
            "updated_at": datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated is not None else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "freshness": freshness,
            "stale_after_seconds": COLLECTION_CHECKPOINT_STALE_SECONDS,
        },
    }


def _last_json_object(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any] | None:
    """Read only a bounded log tail and recover the final JSON object."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    starts = [index for index, char in enumerate(text) if char == "{"]
    decoder = json.JSONDecoder()
    for index in reversed(starts):
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not text[index + end :].strip():
            return value
    return None


def _refresh_terminal_runner_result(
    state: dict[str, Any], task_root: Path
) -> dict[str, Any]:
    """Fill terminal counts for runners without an incremental page snapshot."""
    if state.get("task_type") != "workflow-a":
        return state
    result = _last_json_object(task_root / "run.log")
    if not result:
        return state
    scope = result.get("scope") if isinstance(result.get("scope"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    selected_pages = scope.get("selected_pages")
    return {
        **state,
        "completed_pages": _count_pages(selected_pages),
        "failed_pages": 0,
        "work_status": result.get("status"),
        "runner_error": result.get("error"),
        "package_path": result.get("package_path"),
        "validation_result_path": validation.get("result_path"),
        "validator_status": validation.get("status"),
        "converted_documents": scope.get("converted_documents"),
        "word_metadata_pages": scope.get("word_metadata_pages"),
    }


def _public_snapshot(
    state: dict[str, Any], *, active: bool | None = None
) -> dict[str, Any]:
    if active is None:
        active = _task_is_active(state) if state["status"] in {"queued", "running"} else False
    snapshot = {
        "status": state["status"],
        "run_id": state["run_id"],
        "source": state["source"],
        "output_root": state["output_root"],
        "run_root": state["run_root"],
        "task_root": state["task_root"],
        "log_path": state["log_path"],
        "task_type": state["task_type"],
        "pid": state.get("pid"),
        "process_group_id": state.get("process_group_id"),
        "active": active,
        "started_at": state["started_at"],
        "updated_at": state["updated_at"],
        "completed_pages": state.get("completed_pages", 0),
        "failed_pages": state.get("failed_pages", 0),
        "machine_resource_policy": state.get("machine_resource_policy"),
    }
    for key in ("completed_at", "cancelled_at", "returncode", "error", "next_action",
                "package_path", "validation_result_path", "validator_status", "converted_documents",
                "word_metadata_pages"):
        if state.get(key) is not None:
            snapshot[key] = state[key]
    if state["task_type"] == COLLECTION_TASK:
        snapshot.update(
            route=state["route"],
            expected_sources=state["expected_sources"],
            strict_accepted_count=state.get("strict_accepted_count", 0),
            active_source=state.get("active_source") if active else None,
            batch_summary=state.get("batch_summary"),
            halt_reason=state.get("halt_reason"),
        )
        if "disk_floor_bytes" in state:
            snapshot["disk_floor_bytes"] = state["disk_floor_bytes"]
    return snapshot


def _new_run_id(source: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    source_part = re.sub(r"[^A-Za-z0-9]+", "-", source.stem).strip("-")[:24] or "pdf"
    return f"pdf-{timestamp}-{source_part}-{uuid.uuid4().hex[:8]}"


def _worker_command(state_root: Path, run_id: str, token: str) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "_worker",
        run_id,
        "--state-root",
        str(state_root.expanduser().resolve()),
        "--worker-token",
        token,
    ]


def _spawn_worker(
    task_root: Path, state_root: Path, state: dict[str, Any], *, collection_lock_fd: int | None = None,
) -> dict[str, Any]:
    token = uuid.uuid4().hex
    log_path = task_root / "run.log"
    command = _worker_command(state_root, state["run_id"], token)
    pass_fds = ()
    if collection_lock_fd is not None:
        command.extend(["--collection-lock-fd", str(collection_lock_fd)])
        pass_fds = (collection_lock_fd,)
    try:
        with log_path.open("ab", buffering=0) as log_handle:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                pass_fds=pass_fds,
            )
    except Exception as exc:
        _transition(
            task_root,
            state,
            "failed",
            event="failed",
            error=f"failed to start detached worker: {exc}",
            next_action=f"inspect {log_path}",
            completed_at=now_iso(),
        )
        raise
    return _transition(
        task_root,
        state,
        "running",
        event="running",
        pid=process.pid,
        process_group_id=process.pid,
        worker_token=token,
        worker_identity_unverified=False,
        error=None,
        returncode=None,
        next_action=None,
    )


def start_task(
    *,
    source: Path,
    output_root: Path,
    run_root: Path,
    state_root: Path,
    command: list[str],
    resume_command: list[str] | None,
    pages: str,
    task_type: str,
    work_progress_path: Path | None = None,
    run_id: str | None = None,
    collection_sources: list[Path] | None = None,
    collection_route: str | None = None,
    collection_lock_fd: int | None = None,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    run_root = run_root.expanduser().resolve()
    state_root = state_root.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source does not exist: {source}")
    if not command:
        raise ValueError("background task command cannot be empty")
    run_id = _validate_run_id(run_id or _new_run_id(source))
    task_root = _task_root(state_root, run_id)
    if (task_root / "progress-current.json").exists():
        raise ValueError(f"run_id already exists: {run_id}")
    output_root.mkdir(parents=True, exist_ok=True)
    if task_type != "workflow-a" or source.suffix.lower() != ".docx":
        run_root.mkdir(parents=True, exist_ok=True)
    task_root.mkdir(parents=True, exist_ok=False)
    started_at = now_iso()
    state: dict[str, Any] = {
        "run_id": run_id,
        "source": str(source),
        "output_root": str(output_root),
        "run_root": str(run_root),
        "task_root": str(task_root),
        "log_path": str(task_root / "run.log"),
        "task_type": task_type,
        "pid": None,
        "process_group_id": None,
        "status": "queued",
        "started_at": started_at,
        "updated_at": started_at,
        "completed_pages": 0,
        "failed_pages": 0,
        "pages": pages,
        "command": command,
        "resume_command": resume_command,
        "work_progress_path": str(work_progress_path) if work_progress_path else None,
        "resume_count": 0,
        "machine_resource_policy": resource_slots.resource_policy_summary(),
    }
    with _task_lock(task_root):
        if collection_sources is not None:
            frozen_list = task_root / "source-list.txt"
            frozen_list.write_text("".join(f"{path}\n" for path in collection_sources), encoding="utf-8")
            state.update(
                route=collection_route,
                expected_sources=len(collection_sources),
                source_list_snapshot=str(frozen_list),
                batch_manifest_seen=(run_root / "manifest.json").is_file(),
            )
        _write_json_atomic(task_root / "progress-current.json", state)
        _append_event(task_root / "progress.jsonl", state, "queued")
        state = _spawn_worker(task_root, state_root, state, collection_lock_fd=collection_lock_fd)
    return {
        "status": "started",
        "run_id": run_id,
        "pid": state["pid"],
        "run_root": str(run_root),
        "task_root": str(task_root),
        "log_path": str(task_root / "run.log"),
        "task_type": task_type,
        "machine_resource_policy": state["machine_resource_policy"],
        **_control_commands(state_root, run_id, include_resume=collection_sources is not None),
        **({"route": collection_route, "expected_sources": len(collection_sources)}
           if collection_sources is not None else {}),
    }


def _control_commands(state_root: Path, run_id: str, *, include_resume: bool = True) -> dict[str, str]:
    return {
        f"{action}_command": shlex.join(
            [sys.executable, str(Path(__file__).resolve()), action, run_id,
             "--state-root", str(state_root.expanduser().resolve())]
        )
        for action in (("status", "cancel", "resume") if include_resume else ("status", "cancel"))
    }


def _wait_for_worker_activation(
    state_root: Path, run_id: str, token: str, timeout_seconds: float = 5.0
) -> tuple[Path, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        task_root, state = _load_state(state_root, run_id)
        if state.get("worker_token") == token and int(state.get("pid") or 0) == os.getpid():
            return task_root, state
        time.sleep(0.01)
    raise RuntimeError("background worker activation handshake timed out")


def worker_main(
    state_root: Path, run_id: str, token: str, *, collection_lock_fd: int | None = None,
) -> int:
    try:
        task_root, state = _wait_for_worker_activation(state_root, run_id, token)
        command = state.get("active_command") or state.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValueError("stored background command is invalid")
        if collection_lock_fd is not None:
            command = [*command, "--collection-lock-fd", str(collection_lock_fd)]
        try:
            completed = subprocess.run(
                command, cwd=ROOT, check=False,
                pass_fds=(collection_lock_fd,) if collection_lock_fd is not None else (),
            )
        finally:
            if collection_lock_fd is not None:
                os.close(collection_lock_fd)
        returncode = completed.returncode
        with _task_lock(task_root):
            _, latest = _load_state(state_root, run_id)
            if latest.get("status") == "cancelled":
                return 0
            latest = _refresh_page_counts(latest)
            latest = _refresh_terminal_runner_result(latest, task_root)
            batch_summary = latest.get("batch_summary")
            docx_task = latest["task_type"] == "workflow-a" and Path(latest["source"]).suffix.lower() == ".docx"
            if returncode == 0 and latest["task_type"] == COLLECTION_TASK and not (
                latest.get("work_status") == "passed"
                and isinstance(batch_summary, dict)
                and batch_summary.get("planned_total") == latest["expected_sources"]
                and batch_summary.get("passed") == latest["expected_sources"]
            ):
                status, event = "failed", "failed"
                error = "PDF collection exited without a fresh, complete batch acceptance snapshot"
                next_action = f"inspect {task_root / 'run.log'} and run resume {run_id}"
            elif returncode == 0:
                status, event = "completed", "completed"
                error, next_action = None, None
            elif (returncode == 1 and docx_task
                  and latest.get("work_status") == "needs-review"
                  and latest.get("validation_result_path")):
                status, event = "needs-review", "needs-review"
                error = None
                next_action = f"review {latest['validation_result_path']}"
            elif (
                returncode == 75
                and latest["task_type"] == COLLECTION_TASK
                and latest.get("work_status") == "paused"
            ):
                status, event = "paused", "paused"
                error = None
                next_action = f"free disk space before running resume {run_id}"
            elif returncode == 75:
                status, event = "budget-exhausted", "budget-exhausted"
                error = "background command exhausted its configured budget"
                next_action = f"resume {run_id} after increasing the task budget"
            else:
                status, event = "failed", "failed"
                error = f"background command exited with return code {returncode}"
                next_action = f"inspect {task_root / 'run.log'} and run resume {run_id}"
                if docx_task:
                    error = latest.get("runner_error") or error
                    next_action = f"inspect {task_root / 'run.log'} and start a new task after correcting the error"
            _transition(
                task_root,
                latest,
                status,
                event=event,
                returncode=returncode,
                error=error,
                next_action=next_action,
                completed_at=now_iso(),
            )
        return 0
    except Exception as exc:
        try:
            task_root, state = _load_state(state_root, run_id)
            with _task_lock(task_root):
                _, state = _load_state(state_root, run_id)
                _transition(
                    task_root,
                    state,
                    "failed",
                    event="failed",
                    error=str(exc),
                    next_action=f"inspect {task_root / 'run.log'}",
                    completed_at=now_iso(),
                )
        except Exception as state_exc:
            print(
                f"background worker failed and could not persist state: {state_exc}",
                file=sys.stderr,
                flush=True,
            )
        return 1


def status_task(state_root: Path, run_id: str) -> dict[str, Any]:
    task_root = _task_root(state_root, run_id)
    with _task_lock(task_root):
        _, state = _load_state(state_root, run_id)
        stored_state = state
        collection = state.get("task_type") == COLLECTION_TASK
        progress = _collection_progress(state) if collection else {}
        state = _refresh_collection_counts(state, progress) if collection else _refresh_page_counts(state)
        active = _task_is_active(state) if state["status"] in {"queued", "running"} else False
        if state["status"] in {"queued", "running"} and not active:
            state = _transition(
                task_root,
                state,
                "failed",
                event="failed",
                error="recorded worker is not active or its PID identity no longer matches",
                next_action=f"inspect {task_root / 'run.log'} and run resume {run_id}",
                worker_identity_unverified=True,
                completed_at=now_iso(),
            )
            active = False
        elif not collection and state != stored_state:
            _write_json_atomic(task_root / "progress-current.json", state)
        snapshot = _public_snapshot(state, active=active)
    if collection:
        # Keep normal collection queries read-only. Only the existing
        # unverified-worker lifecycle transition above writes task state.
        snapshot.update(_collection_observation(state, progress, active=active))
    return snapshot


def _wait_for_group_exit(pgid: int, timeout_seconds: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    members = _process_group_members(pgid)
    while members and time.monotonic() < deadline:
        time.sleep(0.05)
        members = _process_group_members(pgid)
    return members


def _unverified_collection_residuals(state: dict[str, Any]) -> dict[str, Any]:
    """Report observations, not ownership, when signalling has been refused."""
    try:
        members = _process_group_members(int(state.get("process_group_id") or 0))
    except RuntimeError:
        members = []
    # An empty read of a group whose leader is gone does not prove that
    # other workers are gone (or that any current members belong to us).
    return {
        "residual_processes": members or None,
        "residual_status": "unverified-group-members" if members else "unknown",
        "next_action": "inspect the recorded process group and collection lock; resume only after workers clear",
    }


def cancel_task(
    state_root: Path, run_id: str, *, timeout_seconds: float = 5.0
) -> dict[str, Any]:
    task_root, state = _load_state(state_root, run_id)
    with _task_lock(task_root):
        _, state = _load_state(state_root, run_id)
        if state["status"] == "cancelled":
            snapshot = _public_snapshot(state)
            snapshot["residual_processes"] = []
            return snapshot
        if state["task_type"] == COLLECTION_TASK and state["status"] == "paused":
            state = _transition(
                task_root,
                _refresh_page_counts(state),
                "cancelled",
                event="cancelled",
                cancelled_at=now_iso(),
                next_action=f"resume {run_id} when continuation is wanted",
            )
            return {**_public_snapshot(state), "residual_processes": []}
        if state["status"] in TERMINAL_STATUSES or not _task_is_active(state):
            state = _refresh_page_counts(state)
            if state["status"] not in TERMINAL_STATUSES:
                state = _transition(
                    task_root,
                    state,
                    "failed",
                    event="failed",
                    error="cancel refused to signal a stale or mismatched PID",
                    next_action=f"inspect {task_root / 'run.log'}",
                    worker_identity_unverified=True,
                    completed_at=now_iso(),
                )
            snapshot = _public_snapshot(state)
            if state["task_type"] == COLLECTION_TASK and state.get("worker_identity_unverified"):
                return {**snapshot, **_unverified_collection_residuals(state)}
            snapshot["residual_processes"] = []
            return snapshot
        pgid = int(state["process_group_id"])
        # Keep the state lock through termination.  A finishing worker must
        # wait here instead of racing a terminal write against cancellation.
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            residual = _process_group_members(pgid)
        else:
            residual = _wait_for_group_exit(pgid, timeout_seconds)
        if residual:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                residual = _process_group_members(pgid)
            else:
                residual = _wait_for_group_exit(pgid, min(timeout_seconds, 2.0))

        latest = _refresh_page_counts(state)
        if residual:
            latest = _transition(
                task_root,
                latest,
                "failed",
                event="failed",
                error="cancel could not terminate every process in the task group",
                next_action=f"inspect process group {pgid}",
                worker_identity_unverified=True,
                completed_at=now_iso(),
            )
        else:
            latest = _transition(
                task_root,
                latest,
                "cancelled",
                event="cancelled",
                error=None,
                next_action=f"resume {run_id} when continuation is wanted",
                cancelled_at=now_iso(),
            )
    snapshot = _public_snapshot(latest)
    snapshot["residual_processes"] = residual
    return snapshot


def _with_collection_disk_floor(command: list[str], disk_floor_bytes: int) -> list[str]:
    flag = "--disk-floor-bytes"
    updated: list[str] = []
    has_pending_value = False
    for arg in command:
        if has_pending_value:
            if arg.startswith("--"):
                raise ValueError(f"stored {flag} is missing its value")
            has_pending_value = False
        elif arg == flag:
            has_pending_value = True
        elif not arg.startswith(f"{flag}="):
            updated.append(arg)
    if has_pending_value:
        raise ValueError(f"stored {flag} is missing its value")
    return [*updated, flag, str(disk_floor_bytes)]


def resume_task(
    state_root: Path,
    run_id: str,
    *,
    no_visual_asset_pruning: bool = False,
    disk_floor_bytes: int | None = None,
) -> dict[str, Any]:
    if disk_floor_bytes is not None and (
        type(disk_floor_bytes) is not int or disk_floor_bytes < COLLECTION_MIN_DISK_FLOOR_BYTES
    ):
        raise ValueError(f"--disk-floor-bytes must be an integer >= {COLLECTION_MIN_DISK_FLOOR_BYTES}")
    task_root, state = _load_state(state_root, run_id)
    with _task_lock(task_root), ExitStack() as admission:
        _, state = _load_state(state_root, run_id)
        collection = state["task_type"] == COLLECTION_TASK
        if disk_floor_bytes is not None and (not collection or state.get("route") != "hybrid"):
            raise ValueError("--disk-floor-bytes is only valid for registered hybrid pdf-collection tasks")
        resumable = {"paused", "cancelled", "failed", "budget-exhausted"}
        if collection:
            resumable.add("completed")  # Revalidate package evidence before skipping.
        if state["status"] not in resumable:
            raise ValueError(f"run {run_id} cannot resume from status {state['status']}")
        resume_command = state.get("resume_command")
        if not isinstance(resume_command, list) or not resume_command:
            raise ValueError(f"run {run_id} does not support resume")
        active_command = list(resume_command)
        if no_visual_asset_pruning:
            if state.get("task_type") != "visual-pdf":
                raise ValueError(
                    "--no-visual-asset-pruning is only valid for visual-pdf tasks"
                )
            if "--no-visual-asset-pruning" not in active_command:
                active_command.append("--no-visual-asset-pruning")
        collection_lock_fd = None
        resume_updates: dict[str, Any] = {}
        if collection:
            run_root = Path(state["run_root"])
            collection_lock_fd, _ = admission.enter_context(
                _reserve_collection_lock(run_root, state["expected_sources"])
            )
            if collection_lock_fd is None:
                raise ValueError(f"a PDF queue still owns {run_root.parent}; do not start another worker")
            sources = _collection_sources(Path(state["source_list_snapshot"]))
            registered = _matching_collection_manifest(run_root, sources, state["route"], state["pages"])
            if not registered and (
                disk_floor_bytes is not None
                or state.get("batch_manifest_seen")
                or state["status"] in {"completed", "paused"}
                or (run_root.is_dir() and any(path.name != ".DS_Store" for path in run_root.iterdir()))
            ):
                raise ValueError(f"registered batch manifest is missing: {run_root / 'manifest.json'}; restore it before resume")
            if disk_floor_bytes is not None:
                active_command = _with_collection_disk_floor(active_command, disk_floor_bytes)
                resume_updates = {"resume_command": active_command, "disk_floor_bytes": disk_floor_bytes}
        state = _transition(
            task_root,
            state,
            "queued",
            event="resume-queued",
            active_command=active_command,
            resume_count=int(state.get("resume_count") or 0) + 1,
            resumed_at=now_iso(),
            completed_at=None,
            cancelled_at=None,
            **resume_updates,
        )
        state = _spawn_worker(task_root, state_root, state, collection_lock_fd=collection_lock_fd)
    return {
        "status": "started",
        "run_id": run_id,
        "pid": state["pid"],
        "run_root": state["run_root"],
        "task_root": state["task_root"],
        "log_path": state["log_path"],
        "task_type": state["task_type"],
        "resumed": True,
        **(_control_commands(state_root, run_id) if collection else {}),
        **({"disk_floor_bytes": state["disk_floor_bytes"]} if "disk_floor_bytes" in state else {}),
    }


def _default_run_root(output_root: Path, source: Path) -> Path:
    return resolve_package_target(output_root, source, include_timestamp=True)[1]


def _collection_sources(source_list: Path) -> list[Path]:
    sources: dict[Path, None] = {}
    for number, raw in enumerate(source_list.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path = Path(line)
        if not path.is_absolute():
            raise ValueError(f"source list line {number} must be an absolute PDF path")
        path = path.resolve(strict=True)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ValueError(f"source list line {number} is not a PDF file: {path}")
        sources[path] = None
    if len(sources) < 2:
        raise ValueError("pdf-collection requires at least two distinct PDF paths")
    return list(sources)


def _matching_collection_manifest(
    run_root: Path, sources: list[Path], route: str, pages: str,
) -> bool:
    path = run_root / "manifest.json"
    if not path.exists():
        return False
    manifest = _read_json(path) or {}
    inputs = manifest.get("inputs")
    config = manifest.get("config") or {}
    if (
        manifest.get("mode") != "lan-pdf-route-aware-batch"
        or not isinstance(inputs, list)
        or len(inputs) != len(sources)
        or not all(isinstance(item, dict) and item.get("route") == route for item in inputs)
        or {item.get("source_path") for item in inputs} != {str(source) for source in sources}
        or not isinstance(config, dict)
        or config.get("pages") != pages
    ):
        raise ValueError(f"existing batch manifest does not match the requested sources, route and pages: {path}")
    return True


@contextmanager
def _reserve_collection_lock(
    run_root: Path, source_count: int,
) -> Iterator[tuple[int | None, dict[str, Any]]]:
    """Reserve the official queue lock before acknowledging a managed start."""
    path = run_root.parent / ".pdf-collection-queue.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            try:
                owner = json.load(handle)
            except (ValueError, OSError):
                owner = {}
            yield None, owner if isinstance(owner, dict) else {}
            return
        owner = {"batch_root": str(run_root), "source_count": source_count,
                 "started_at": now_iso(), "phase": "launching"}
        handle.seek(0)
        handle.truncate()
        json.dump(owner, handle, ensure_ascii=False)
        handle.flush()
        # Close our copy without LOCK_UN. A detached worker inherits the same
        # open file description and owns it until the batch stops.
        yield handle.fileno(), owner


def _start_collection(args: argparse.Namespace) -> dict[str, Any]:
    if args.source_list is None or args.route is None:
        raise ValueError("pdf-collection requires --source-list and an explicit --route")
    if args.run_id is not None:
        raise ValueError("pdf-collection assigns a stable run_id for its exact batch root")
    if args.batch_size != 1:
        raise ValueError("pdf-collection runs one book at a time; --batch-size must be 1")
    if args.timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be positive")
    min_free_bytes = args.min_free_bytes if args.min_free_bytes is not None else COLLECTION_MIN_FREE_BYTES
    if min_free_bytes < 0:
        raise ValueError("--min-free-bytes must be non-negative")
    source_list = args.source_list.expanduser().resolve()
    sources = _collection_sources(source_list)
    output_root = args.output_root.expanduser().resolve()
    run_root = (args.run_root or (output_root / "conversions" / args.route)).expanduser().resolve()
    if args.run_root is not None and not run_root.is_relative_to(output_root):
        output_root = run_root.parent
    state_root = args.state_root.expanduser().resolve()
    run_id = "collection-" + hashlib.sha256(os.fsencode(run_root)).hexdigest()[:24]
    task_root = _task_root(state_root, run_id)
    with _task_lock(state_root / ".launch-locks" / run_id), ExitStack() as admission:
        if (task_root / "progress-current.json").exists():
            _, existing = _load_state(state_root, run_id)
            if (
                existing.get("task_type") != COLLECTION_TASK
                or existing.get("run_root") != str(run_root)
                or existing.get("route") != args.route
                or existing.get("pages") != args.pages
                or set(_collection_sources(Path(existing["source_list_snapshot"]))) != set(sources)
            ):
                raise ValueError(f"batch root is already registered for another selection: {run_root}")
            snapshot = status_task(state_root, run_id)
            commands = _control_commands(state_root, run_id)
            if snapshot["active"]:
                return {**snapshot, **commands, "status": "already-running"}
            return {
                **snapshot,
                **commands,
                "status": "already-registered",
                "existing_status": snapshot["status"],
                "next_action": commands["resume_command"],
            }
        lock_fd, owner = admission.enter_context(_reserve_collection_lock(run_root, len(sources)))
        if lock_fd is None:
            return {
                "status": "already-running" if owner.get("batch_root") == str(run_root) else "collection-busy",
                "run_root": str(run_root),
                "owner": owner,
                "managed": False,
                "next_action": "use the existing queue; do not start another process",
            }
        existing_manifest = _matching_collection_manifest(run_root, sources, args.route, args.pages)
        if not existing_manifest and any((run_root / name).exists() for name in (
            "results.json", "progress-current.json", "selected-sources.txt",
        )):
            raise ValueError(f"registered batch manifest is missing: {run_root / 'manifest.json'}; restore it before start")
        command = [
            sys.executable, str(COLLECTION_ADAPTER),
            "--batch-root", str(run_root),
            "--route", args.route,
            "--pages", args.pages,
            "--batch-size", "1",
            "--evidence-level", args.evidence_level,
            "--timeout-seconds", str(args.timeout_seconds),
            "--min-free-bytes", str(min_free_bytes),
        ]
        # Existing batches must use their registered inputs/relative paths,
        # not reconstruct metadata from a user's loose list of paths.
        if not existing_manifest:
            command.extend(["--source-list", str(task_root / "source-list.txt")])
        return start_task(
            source=source_list,
            output_root=output_root,
            run_root=run_root,
            state_root=state_root,
            command=command,
            resume_command=command,
            pages=args.pages,
            task_type=COLLECTION_TASK,
            work_progress_path=run_root / "progress-current.json",
            run_id=run_id,
            collection_sources=sources,
            collection_route=args.route,
            collection_lock_fd=lock_fd,
        )


def _build_pdf_commands(args: argparse.Namespace, run_id: str) -> tuple[Path, list[str], list[str] | None, Path | None]:
    source = args.source.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if args.task_type == "workflow-a":
        docx = source.suffix.lower() == ".docx"
        if docx and args.pages.strip().lower() != "all":
            raise ValueError("DOCX requires --pages all; Word pagination is not a stable native selector")
        if args.run_root:
            run_root = args.run_root.expanduser().absolute() if docx else args.run_root.expanduser().resolve()
        else:
            run_root = resolve_package_target(output_root, source)[1] if docx else _default_run_root(output_root, source)
        if docx and (run_root.parent != output_root or run_root.exists() or run_root.is_symlink()):
            raise ValueError("DOCX run root must be a new direct child of the output root")
        command = [
            sys.executable,
            str(WORKFLOW_A_ADAPTER),
            "--source",
            str(source),
            "--output-root",
            str(output_root),
            "--pages",
            args.pages,
            "--execute",
        ]
        if docx:
            command.extend(["--run-root", str(run_root)])
        return run_root, command, None, None

    if args.task_type == "native-pdf-batch":
        run_root = (args.run_root.expanduser().resolve() if args.run_root else _default_run_root(output_root, source))
        command = [
            sys.executable,
            str(BATCH_ADAPTER),
            "--source",
            str(source),
            "--output-root",
            str(output_root),
            "--pages",
            args.pages,
            "--batch-size",
            str(args.batch_size),
            "--context-mode",
            "independent",
            "--resume-package",
            str(run_root),
        ]
        return run_root, command, command, run_root / "progress.json"

    if args.task_type == "local-ocr-pdf":
        run_root = (
            args.run_root.expanduser().resolve()
            if args.run_root
            else _default_run_root(output_root, source)
        )
        command = [
            sys.executable,
            str(LOCAL_OCR_ADAPTER),
            "--source",
            str(source),
            "--run-root",
            str(run_root),
            "--pages",
            args.pages,
            "--dpi",
            "220",
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--jobs",
            str(args.jobs),
        ]
        return (
            run_root,
            command,
            [*command, "--resume"],
            run_root / "evidence" / "progress-current.json",
        )

    run_root = (args.run_root.expanduser().resolve() if args.run_root else _default_run_root(output_root, source))
    command = [
        sys.executable,
        str(GRAPH_ADAPTER),
        "--source",
        str(source),
        "--run-root",
        str(run_root),
        "--pages",
        args.pages,
        "--evidence-level",
        args.evidence_level,
        "--batch-size",
        str(args.batch_size),
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.task_type == "visual-pdf":
        command.extend(["--visual-only", "--dpi", "220"])
    if args.task_type == "hybrid-pdf":
        command.extend(["--dpi", "220"])
    if args.task_type == "native-pdf":
        command.append("--no-model")
    resume_command = [*command, "--resume"]
    progress_path = (
        run_root / "progress.json"
        if args.task_type == "hybrid-pdf"
        else run_root / "evidence" / "progress-current.json"
    )
    return run_root, command, resume_command, progress_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(
        dest="action",
        required=True,
        metavar="{start,status,cancel,resume}",
    )

    start = subparsers.add_parser("start", help="start one detached conversion task and return immediately")
    inputs = start.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", type=Path)
    inputs.add_argument("--source-list", type=Path, help="pdf-collection: UTF-8 list of absolute PDF paths")
    start.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "runs",
        help="parent directory for conversion packages (default: project runs/)",
    )
    start.add_argument("--run-root", type=Path)
    start.add_argument("--pages", default="all")
    start.add_argument("--route", choices=("hybrid", "native-only"), help="required for pdf-collection")
    start.add_argument("--min-free-bytes", type=int, help="pdf-collection: headroom checked before each book (default 2 GiB)")
    start.add_argument(
        "--task-type",
        choices=(
            "native-pdf",
            "native-pdf-batch",
            "local-ocr-pdf",
            "hybrid-pdf",
            "visual-pdf",
            "workflow-a",
            COLLECTION_TASK,
        ),
        required=True,
    )
    start.add_argument("--evidence-level", default="compact")
    start.add_argument("--batch-size", type=int, default=1)
    start.add_argument("--jobs", type=int, default=2)
    start.add_argument("--timeout-seconds", type=int, default=7200)
    start.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    start.add_argument("--run-id")

    action_help = {
        "status": "read one latest snapshot without replaying progress history",
        "cancel": "terminate the verified task process group",
        "resume": "resume a supported task in its existing run root",
    }
    for action in ("status", "cancel", "resume"):
        child = subparsers.add_parser(action, help=action_help[action])
        child.add_argument("run_id")
        child.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    cancel = subparsers.choices["cancel"]
    cancel.add_argument("--timeout-seconds", type=float, default=5.0)
    resume = subparsers.choices["resume"]
    resume.add_argument("--no-visual-asset-pruning", action="store_true")
    resume.add_argument(
        "--disk-floor-bytes", type=int,
        help=("registered hybrid pdf-collection only: persist the disk pause floor for this run "
              f"(before and within each book; minimum {COLLECTION_MIN_DISK_FLOOR_BYTES} bytes)"),
    )

    worker = subparsers.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("run_id")
    worker.add_argument("--state-root", type=Path, required=True)
    worker.add_argument("--worker-token", required=True)
    worker.add_argument("--collection-lock-fd", type=int)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if action.dest != "_worker"
    ]
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "_worker":
        return worker_main(
            args.state_root, args.run_id, args.worker_token, collection_lock_fd=args.collection_lock_fd,
        )
    try:
        if args.action == "start" and args.task_type == COLLECTION_TASK:
            payload = _start_collection(args)
        elif args.action == "start":
            if args.source is None or args.route is not None or args.min_free_bytes is not None:
                raise ValueError("single-source tasks require --source; list, route and headroom flags are collection-only")
            source = args.source.expanduser().resolve()
            run_id = _validate_run_id(args.run_id or _new_run_id(source))
            run_root, command, resume_command, progress_path = _build_pdf_commands(args, run_id)
            payload = start_task(
                source=source,
                output_root=args.output_root,
                run_root=run_root,
                state_root=args.state_root,
                command=command,
                resume_command=resume_command,
                pages=args.pages,
                task_type=args.task_type,
                work_progress_path=progress_path,
                run_id=run_id,
            )
        elif args.action == "status":
            payload = status_task(args.state_root, args.run_id)
        elif args.action == "cancel":
            payload = cancel_task(
                args.state_root,
                args.run_id,
                timeout_seconds=args.timeout_seconds,
            )
        else:
            payload = resume_task(
                args.state_root,
                args.run_id,
                no_visual_asset_pruning=args.no_visual_asset_pruning,
                disk_floor_bytes=args.disk_floor_bytes,
            )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        payload = {"status": "failed", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("status") not in {"failed", "needs-review", "budget-exhausted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
