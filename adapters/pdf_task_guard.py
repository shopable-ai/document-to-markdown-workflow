from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class TaskLockError(RuntimeError):
    """Raised when another active task already owns the same logical PDF run."""


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


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_identity(pid: int) -> str | None:
    """Return a stable identity for one live process, not merely its PID."""
    if pid <= 0:
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _process_command(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    command = result.stdout.strip()
    return command if result.returncode == 0 and command else None


def normalize_pages(pages: str) -> str:
    selector = pages.strip().lower()
    if selector == "all":
        return "all"
    selected: set[int] = set()
    unknown_tokens: list[str] = []
    for token in selector.split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            selected.add(int(token))
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if not match:
            unknown_tokens.append(token)
            continue
        start, end = (int(value) for value in match.groups())
        if end < start:
            start, end = end, start
        selected.update(range(start, end + 1))
    if unknown_tokens:
        return ",".join(sorted(unknown_tokens))
    ordered = sorted(selected)
    if not ordered:
        return selector or "all"
    ranges: list[str] = []
    start = end = ordered[0]
    for page in ordered[1:]:
        if page == end + 1:
            end = page
            continue
        ranges.append(str(start) if start == end else f"{start}-{end}")
        start = end = page
    ranges.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(ranges)


def task_lock_key(source_sha256: str, output_root: Path, pages: str) -> str:
    payload = {
        "source_sha256": source_sha256,
        "output_root": str(output_root.expanduser().resolve()),
        "pages": normalize_pages(pages),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def task_lock_path(source_sha256: str, output_root: Path, pages: str) -> Path:
    root = output_root.expanduser().resolve() / ".pdf-task-locks"
    return root / f"{task_lock_key(source_sha256, output_root, pages)}.json"


@contextmanager
def acquire_task_lock(
    *,
    source: Path,
    source_sha256: str,
    output_root: Path,
    pages: str,
    entrypoint: str,
) -> Iterator[Path]:
    lock_path = task_lock_path(source_sha256, output_root, pages)
    payload = {
        "status": "running",
        "pid": os.getpid(),
        "process_identity": _process_identity(os.getpid()),
        "entrypoint": entrypoint,
        "source": str(source.expanduser().resolve()),
        "source_sha256": source_sha256,
        "output_root": str(output_root.expanduser().resolve()),
        "pages": normalize_pages(pages),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            existing = _read_json(lock_path) or {}
            owner_pid = int(existing.get("pid") or 0)
            recorded_identity = existing.get("process_identity")
            current_identity = _process_identity(owner_pid)
            pid_running = _pid_is_running(owner_pid)
            legacy_command = _process_command(owner_pid) if pid_running and not recorded_identity else None
            legacy_owner_matches = bool(
                legacy_command
                and str(existing.get("entrypoint") or "") in legacy_command
                and str(existing.get("source") or "") in legacy_command
                and str(existing.get("output_root") or "") in legacy_command
            )
            if pid_running and (
                (recorded_identity and current_identity == recorded_identity)
                or legacy_owner_matches
            ):
                raise TaskLockError(
                    "active PDF task lock already exists: "
                    f"entrypoint={existing.get('entrypoint')} pid={owner_pid} "
                    f"source={existing.get('source')} pages={existing.get('pages')}"
                )
            if pid_running and current_identity is None:
                raise TaskLockError(
                    "PDF task lock owner identity could not be verified; "
                    f"refusing to replace lock for pid={owner_pid}"
                )
            lock_path.unlink(missing_ok=True)
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            break
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)
        try:
            lock_path.parent.rmdir()
        except OSError:
            # Another active task may still own a lock in the shared folder.
            pass


def write_cancelled_run_state(
    *,
    summary_paths: list[Path],
    progress_paths: list[Path],
    reason: str,
    source: str,
    selected_pages: list[int],
    total_pages: int,
) -> None:
    payload = {
        "status": "cancelled",
        "cancel_reason": reason,
        "source": source,
        "selected_pages": selected_pages,
        "total_pages": total_pages,
        "resume_required": True,
        "next_step": "rerun with --resume after inspecting the cancelled run",
    }
    for path in [*summary_paths, *progress_paths]:
        current = _read_json(path) or {}
        merged = {**current, **payload}
        _write_json_atomic(path, merged)
