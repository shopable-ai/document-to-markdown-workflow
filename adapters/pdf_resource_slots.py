#!/usr/bin/env python3
"""Two independent machine-wide singleton slots for PDF conversion.

One local PDF conversion and one visual-model request may overlap. Nested
work inherits its parent's lease and does not consume another slot.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
from functools import wraps
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterator, TypeVar
import uuid


LOCAL_PDF_SLOT = "local-pdf"
VISUAL_MODEL_SLOT = "visual-model"
SLOTS = {LOCAL_PDF_SLOT, VISUAL_MODEL_SLOT}
SLOT_ROOT_ENV = "WORKFLOW_PDF_RESOURCE_SLOT_ROOT"
SLOT_ENV_VARS = {
    LOCAL_PDF_SLOT: "WORKFLOW_PDF_LOCAL_SLOT_OWNER",
    VISUAL_MODEL_SLOT: "WORKFLOW_PDF_VISUAL_SLOT_OWNER",
}

_thread_state = threading.local()
_ReturnT = TypeVar("_ReturnT")


class ResourceSlotBusy(RuntimeError):
    """Raised when a requested machine-wide slot is already occupied."""


@dataclass(frozen=True)
class ResourceSlotLease:
    slot: str
    token: str
    inherited: bool
    lock_path: Path
    owner_pid: int
    purpose: str
    slot_number: int
    capacity: int


def slot_root() -> Path:
    configured = os.environ.get(SLOT_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".codex" / "resource-slots" / "pdf-markdown"


def resource_slot_capacity(slot: str) -> int:
    if slot in SLOTS:
        return 1
    raise ValueError(f"unknown PDF resource slot: {slot}")


def resource_policy_summary() -> dict[str, Any]:
    return {
        "local_pdf_capacity": 1,
        "visual_model_capacity": 1,
        "policy": "fixed-singleton-slots",
    }


def slot_lock_path(slot: str, slot_number: int = 1) -> Path:
    if slot not in SLOTS:
        raise ValueError(f"unknown PDF resource slot: {slot}")
    capacity = resource_slot_capacity(slot)
    if not 1 <= slot_number <= capacity:
        raise ValueError(
            f"slot number must be from 1 to {capacity} for {slot}: {slot_number}"
        )
    # Slot 1 deliberately keeps the previous path so a running legacy holder
    # and the new pool cannot both claim the first unit of capacity.
    suffix = "" if slot_number == 1 else f"-{slot_number:02d}"
    return slot_root() / f"{slot}{suffix}.lock"


def slot_lock_paths(slot: str) -> list[Path]:
    return [
        slot_lock_path(slot, slot_number)
        for slot_number in range(1, resource_slot_capacity(slot) + 1)
    ]


def _thread_tokens() -> dict[str, str]:
    tokens = getattr(_thread_state, "tokens", None)
    if tokens is None:
        tokens = {}
        _thread_state.tokens = tokens
    return tokens


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _owner_from_environment(slot: str) -> dict[str, Any]:
    raw = os.environ.get(SLOT_ENV_VARS[slot])
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _pid_is_running(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _lock_is_held(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _inherited_owner(slot: str) -> tuple[dict[str, Any], Path] | None:
    owner = _owner_from_environment(slot)
    token = owner.get("token")
    owner_pid = owner.get("owner_pid")
    if not isinstance(token, str) or not token or not isinstance(owner_pid, int):
        return None
    slot_number = owner.get("slot_number", 1)
    if not isinstance(slot_number, int) or slot_number < 1:
        return None
    try:
        path = slot_lock_path(slot, slot_number)
    except ValueError:
        return None
    if owner_pid == os.getpid():
        return (owner, path) if _thread_tokens().get(slot) == token else None
    metadata = _read_metadata(path)
    if (
        metadata.get("status") == "held"
        and metadata.get("token") == token
        and metadata.get("owner_pid") == owner_pid
        and _pid_is_running(owner_pid)
        and _lock_is_held(path)
    ):
        return owner, path
    return None


def _write_metadata(fd: int, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, payload)
    os.fsync(fd)


def _busy_message(slot: str, paths: list[Path]) -> str:
    owners = []
    for path in paths:
        owner = _read_metadata(path)
        if owner:
            owners.append(
                f"{owner.get('slot_number', 1)}/{len(paths)}"
                f" pid={owner.get('owner_pid')}"
                f" purpose={owner.get('purpose')}"
                f" acquired_at={owner.get('acquired_at')}"
            )
    detail = f" owners=[{'; '.join(owners)}]" if owners else ""
    return f"PDF resource slot pool is busy: {slot} capacity={len(paths)}.{detail}"


def _try_acquire_path(paths: list[Path]) -> tuple[int, Path, int] | None:
    for slot_number, path in enumerate(paths, 1):
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue
        return fd, path, slot_number
    return None


@contextmanager
def acquire_resource_slot(
    slot: str,
    *,
    purpose: str,
    blocking: bool = True,
    timeout_seconds: float | None = None,
) -> Iterator[ResourceSlotLease]:
    """Acquire one machine-wide slot, or inherit the active parent lease."""

    paths = slot_lock_paths(slot)
    paths[0].parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    inherited = _inherited_owner(slot)
    if inherited:
        inherited_owner, inherited_path = inherited
        yield ResourceSlotLease(
            slot=slot,
            token=str(inherited_owner["token"]),
            inherited=True,
            lock_path=inherited_path,
            owner_pid=int(inherited_owner["owner_pid"]),
            purpose=purpose,
            slot_number=int(inherited_owner.get("slot_number", 1)),
            capacity=int(inherited_owner.get("capacity", len(paths))),
        )
        return

    fd: int | None = None
    path: Path | None = None
    slot_number: int | None = None
    acquired = False
    started = time.monotonic()
    try:
        while True:
            attempt = _try_acquire_path(paths)
            if attempt is not None:
                fd, path, slot_number = attempt
                acquired = True
                break
            if not blocking:
                raise ResourceSlotBusy(_busy_message(slot, paths))
            if (
                timeout_seconds is not None
                and time.monotonic() - started >= timeout_seconds
            ):
                raise ResourceSlotBusy(_busy_message(slot, paths))
            time.sleep(0.1)

        assert fd is not None and path is not None and slot_number is not None
        token = uuid.uuid4().hex
        owner = {
            "status": "held",
            "slot": slot,
            "token": token,
            "owner_pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "purpose": purpose,
            "slot_number": slot_number,
            "capacity": len(paths),
            "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _write_metadata(fd, owner)
        env_name = SLOT_ENV_VARS[slot]
        previous_env = os.environ.get(env_name)
        previous_thread_token = _thread_tokens().get(slot)
        os.environ[env_name] = json.dumps(
            owner, ensure_ascii=False, separators=(",", ":")
        )
        _thread_tokens()[slot] = token
        try:
            yield ResourceSlotLease(
                slot=slot,
                token=token,
                inherited=False,
                lock_path=path,
                owner_pid=os.getpid(),
                purpose=purpose,
                slot_number=slot_number,
                capacity=len(paths),
            )
        finally:
            if previous_thread_token is None:
                _thread_tokens().pop(slot, None)
            else:
                _thread_tokens()[slot] = previous_thread_token
            if previous_env is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous_env
            released = dict(owner)
            released.update(
                status="released",
                released_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            _write_metadata(fd, released)
    finally:
        if acquired and fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        if fd is not None:
            os.close(fd)


def acquire_local_pdf_slot(
    *, purpose: str, blocking: bool = True, timeout_seconds: float | None = None
):
    return acquire_resource_slot(
        LOCAL_PDF_SLOT,
        purpose=purpose,
        blocking=blocking,
        timeout_seconds=timeout_seconds,
    )


def acquire_visual_model_slot(
    *, purpose: str, blocking: bool = True, timeout_seconds: float | None = None
):
    return acquire_resource_slot(
        VISUAL_MODEL_SLOT,
        purpose=purpose,
        blocking=blocking,
        timeout_seconds=timeout_seconds,
    )


def uses_local_pdf_slot(purpose: str):
    """Decorate one complete local PDF-to-Markdown execution boundary."""

    def decorate(function: Callable[..., _ReturnT]) -> Callable[..., _ReturnT]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> _ReturnT:
            with acquire_local_pdf_slot(purpose=purpose):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def uses_visual_model_slot(purpose: str):
    """Decorate one real visual-model request boundary."""

    def decorate(function: Callable[..., _ReturnT]) -> Callable[..., _ReturnT]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> _ReturnT:
            with acquire_visual_model_slot(purpose=purpose):
                return function(*args, **kwargs)

        return wrapped

    return decorate
