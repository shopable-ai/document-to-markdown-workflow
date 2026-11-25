"""Cooperative disk floor for explicitly guarded PDF collection workers."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


ENV = "WORKFLOW_PDF_COLLECTION_DISK_FLOOR_BYTES"
MINIMUM_FLOOR_BYTES = 500_000_000
PAUSE_REASON = "insufficient-disk-headroom"
PAUSE_EXIT = 75


class DiskFloorReached(Exception):
    """Stop at a safe write boundary without marking a page or book failed."""

    def __init__(self, *, free_bytes: int, floor_bytes: int, phase: str,
                 page: int | None = None, write_bytes: int = 0):
        self.details = {
            "free_bytes": free_bytes, "floor_bytes": floor_bytes, "phase": phase,
            "page": page, "next_write_bytes": write_bytes,
        }
        super().__init__(f"disk floor at {phase}: {free_bytes} bytes free, "
                         f"floor {floor_bytes}, next write {write_bytes}")


def free_bytes_at(path: Path) -> int:
    target = path.resolve()
    while not target.exists():
        target = target.parent
    return shutil.disk_usage(target).free


def configured_floor() -> int | None:
    value = os.environ.get(ENV)
    if value is None:
        return None
    floor = int(value)
    if floor < MINIMUM_FLOOR_BYTES:
        raise ValueError(f"{ENV} must be at least {MINIMUM_FLOOR_BYTES}")
    return floor


def check(path: Path, *, phase: str, page: int | None = None, write_bytes: int = 0) -> None:
    floor = configured_floor()
    if floor is None:
        return
    free = free_bytes_at(path)
    if free <= floor or write_bytes > free - floor:
        raise DiskFloorReached(free_bytes=free, floor_bytes=floor, phase=phase,
                               page=page, write_bytes=write_bytes)
