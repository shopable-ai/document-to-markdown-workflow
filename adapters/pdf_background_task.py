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
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from output_naming import resolve_package_target
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import resolve_package_target


ROOT = Path(__file__).resolve().parents[1]
GRAPH_ADAPTER = ROOT / "adapters" / "lan_pdf_markdown_graph.py"
BATCH_ADAPTER = ROOT / "adapters" / "run_pdf_batches.py"
WORKFLOW_A_ADAPTER = ROOT / "adapters" / "workflow_a_source_runner.py"
DEFAULT_STATE_ROOT = Path.home() / ".codex" / "background-tasks"
ALLOWED_STATUSES = {
    "queued",
    "running",
    "paused",
    "cancelled",
    "failed",
    "completed",
    "budget-exhausted",
}
TERMINAL_STATUSES = {"cancelled", "failed", "completed", "budget-exhausted"}
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
    except (OSError, json.JSONDecodeError):
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
            check=False,
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


def _refresh_page_counts(state: dict[str, Any]) -> dict[str, Any]:
    """Read one latest snapshot; never read or replay progress.jsonl."""
    progress_path_raw = state.get("work_progress_path")
    if not progress_path_raw:
        return state
    progress = _read_json(Path(str(progress_path_raw)))
    if not progress:
        return state
    completed = _count_pages(
        progress.get(
            "completed_pages",
            progress.get("processed_pages", progress.get("passed_count", 0)),
        )
    )
    if completed == 0 and progress.get("status") in {"passed", "completed"}:
        completed = _count_pages(progress.get("selected_pages", 0))
    failed = _count_pages(progress.get("failed_pages", 0))
    return {
        **state,
        "completed_pages": completed,
        "failed_pages": failed,
        "work_status": progress.get("status", progress.get("final_status")),
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
    selected_pages = scope.get("selected_pages")
    return {
        **state,
        "completed_pages": _count_pages(selected_pages),
        "failed_pages": 0,
        "work_status": result.get("status"),
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
    }
    for key in ("completed_at", "cancelled_at", "returncode", "error", "next_action"):
        if state.get(key) is not None:
            snapshot[key] = state[key]
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


def _spawn_worker(task_root: Path, state_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    token = uuid.uuid4().hex
    log_path = task_root / "run.log"
    command = _worker_command(state_root, state["run_id"], token)
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
    }
    with _task_lock(task_root):
        _write_json_atomic(task_root / "progress-current.json", state)
        _append_event(task_root / "progress.jsonl", state, "queued")
        state = _spawn_worker(task_root, state_root, state)
    cli = Path(__file__).resolve()
    state_args = ["--state-root", str(state_root)]
    return {
        "status": "started",
        "run_id": run_id,
        "pid": state["pid"],
        "run_root": str(run_root),
        "task_root": str(task_root),
        "log_path": str(task_root / "run.log"),
        "task_type": task_type,
        "status_command": shlex.join(
            [sys.executable, str(cli), "status", run_id, *state_args]
        ),
        "cancel_command": shlex.join(
            [sys.executable, str(cli), "cancel", run_id, *state_args]
        ),
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


def worker_main(state_root: Path, run_id: str, token: str) -> int:
    try:
        task_root, state = _wait_for_worker_activation(state_root, run_id, token)
        command = state.get("active_command") or state.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValueError("stored background command is invalid")
        completed = subprocess.run(command, cwd=ROOT, check=False)
        returncode = completed.returncode
        with _task_lock(task_root):
            _, latest = _load_state(state_root, run_id)
            if latest.get("status") == "cancelled":
                return 0
            latest = _refresh_page_counts(latest)
            latest = _refresh_terminal_runner_result(latest, task_root)
            if returncode == 0:
                status, event = "completed", "completed"
                error, next_action = None, None
            elif returncode == 75:
                status, event = "budget-exhausted", "budget-exhausted"
                error = "background command exhausted its configured budget"
                next_action = f"resume {run_id} after increasing the task budget"
            else:
                status, event = "failed", "failed"
                error = f"background command exited with return code {returncode}"
                next_action = f"inspect {task_root / 'run.log'} and run resume {run_id}"
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
        state = _refresh_page_counts(state)
        active = _task_is_active(state) if state["status"] in {"queued", "running"} else False
        if state["status"] in {"queued", "running"} and not active:
            state = _transition(
                task_root,
                state,
                "failed",
                event="failed",
                error="recorded worker is not active or its PID identity no longer matches",
                next_action=f"inspect {task_root / 'run.log'} and run resume {run_id}",
                completed_at=now_iso(),
            )
            active = False
        elif state != stored_state:
            _write_json_atomic(task_root / "progress-current.json", state)
        return _public_snapshot(state, active=active)


def _wait_for_group_exit(pgid: int, timeout_seconds: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    members = _process_group_members(pgid)
    while members and time.monotonic() < deadline:
        time.sleep(0.05)
        members = _process_group_members(pgid)
    return members


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
                    completed_at=now_iso(),
                )
            snapshot = _public_snapshot(state)
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


def resume_task(state_root: Path, run_id: str) -> dict[str, Any]:
    task_root, state = _load_state(state_root, run_id)
    with _task_lock(task_root):
        _, state = _load_state(state_root, run_id)
        if state["status"] not in {"paused", "cancelled", "failed", "budget-exhausted"}:
            raise ValueError(f"run {run_id} cannot resume from status {state['status']}")
        resume_command = state.get("resume_command")
        if not isinstance(resume_command, list) or not resume_command:
            raise ValueError(f"run {run_id} does not support resume")
        state = _transition(
            task_root,
            state,
            "queued",
            event="resume-queued",
            active_command=resume_command,
            resume_count=int(state.get("resume_count") or 0) + 1,
            resumed_at=now_iso(),
            completed_at=None,
            cancelled_at=None,
        )
        state = _spawn_worker(task_root, state_root, state)
    return {
        "status": "started",
        "run_id": run_id,
        "pid": state["pid"],
        "run_root": state["run_root"],
        "task_root": state["task_root"],
        "log_path": state["log_path"],
        "task_type": state["task_type"],
        "resumed": True,
    }


def _default_run_root(output_root: Path, source: Path) -> Path:
    return resolve_package_target(output_root, source)[1]


def _build_pdf_commands(args: argparse.Namespace, run_id: str) -> tuple[Path, list[str], list[str] | None, Path | None]:
    source = args.source.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if args.task_type == "workflow-a":
        run_root = (args.run_root.expanduser().resolve() if args.run_root else _default_run_root(output_root, source))
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

    start = subparsers.add_parser("start", help="start one detached PDF task and return immediately")
    start.add_argument("--source", type=Path, required=True)
    start.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "runs",
        help="parent directory for conversion packages (default: project runs/)",
    )
    start.add_argument("--run-root", type=Path)
    start.add_argument("--pages", default="all")
    start.add_argument(
        "--task-type",
        choices=("native-pdf", "native-pdf-batch", "hybrid-pdf", "visual-pdf", "workflow-a"),
        required=True,
    )
    start.add_argument("--evidence-level", default="compact")
    start.add_argument("--batch-size", type=int, default=1)
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

    worker = subparsers.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("run_id")
    worker.add_argument("--state-root", type=Path, required=True)
    worker.add_argument("--worker-token", required=True)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if action.dest != "_worker"
    ]
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "_worker":
        return worker_main(args.state_root, args.run_id, args.worker_token)
    try:
        if args.action == "start":
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
            payload = resume_task(args.state_root, args.run_id)
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
    return 0 if payload.get("status") not in {"failed", "budget-exhausted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
