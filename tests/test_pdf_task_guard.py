from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters import pdf_task_guard as guard


def test_task_lock_blocks_the_same_source_output_and_pages(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    output_root = tmp_path / "output"
    output_root.mkdir()

    with guard.acquire_task_lock(
        source=source,
        source_sha256="sha-1",
        output_root=output_root,
        pages="1-3",
        entrypoint="tests",
    ):
        with pytest.raises(guard.TaskLockError):
            with guard.acquire_task_lock(
                source=source,
                source_sha256="sha-1",
                output_root=output_root,
                pages="1-3",
                entrypoint="tests",
            ):
                pass

    with guard.acquire_task_lock(
        source=source,
        source_sha256="sha-1",
        output_root=output_root,
        pages="1-3",
        entrypoint="tests",
    ):
        pass

    assert not (output_root / ".pdf-task-locks").exists()


def test_write_cancelled_run_state_marks_resume_required(tmp_path: Path):
    summary_path = tmp_path / "summary.json"
    progress_path = tmp_path / "progress-current.json"

    guard.write_cancelled_run_state(
        summary_paths=[summary_path],
        progress_paths=[progress_path],
        reason="signal-received",
        source=str(tmp_path / "book.pdf"),
        selected_pages=[1, 2, 3],
        total_pages=10,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    progress = json.loads(progress_path.read_text(encoding="utf-8"))

    assert summary["status"] == "cancelled"
    assert progress["status"] == "cancelled"
    assert summary["resume_required"] is True
    assert progress["resume_required"] is True
    assert summary["next_step"] == "rerun with --resume after inspecting the cancelled run"
    assert progress["cancel_reason"] == "signal-received"


def test_task_lock_does_not_treat_a_reused_pid_as_the_old_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    output_root = tmp_path / "output"
    lock_path = guard.task_lock_path("sha-1", output_root, "all")
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "status": "running",
                "pid": 4321,
                "process_identity": "identity-from-old-process",
                "entrypoint": "old.py",
                "source": str(source),
                "source_sha256": "sha-1",
                "output_root": str(output_root),
                "pages": "all",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "_pid_is_running", lambda _pid: True)
    monkeypatch.setattr(
        guard,
        "_process_identity",
        lambda pid: "identity-from-reused-process" if pid == 4321 else "current-owner",
    )

    with guard.acquire_task_lock(
        source=source,
        source_sha256="sha-1",
        output_root=output_root,
        pages="all",
        entrypoint="new.py",
    ):
        current = json.loads(lock_path.read_text(encoding="utf-8"))
        assert current["process_identity"] == "current-owner"

    assert not lock_path.exists()
