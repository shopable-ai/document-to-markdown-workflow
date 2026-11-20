from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from adapters import pdf_background_task as background


def _wait_for_state(
    state_root: Path,
    run_id: str,
    statuses: set[str],
    *,
    timeout: float = 8.0,
) -> dict:
    state_path = state_root / run_id / "progress-current.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.02)
            continue
        if state.get("status") in statuses:
            return state
        time.sleep(0.02)
    raise AssertionError(f"task {run_id} did not reach {statuses}")


def _start_test_task(
    tmp_path: Path,
    *,
    command: list[str],
    resume_command: list[str] | None = None,
    run_id: str,
    work_progress_path: Path | None = None,
    task_type: str = "test",
) -> tuple[Path, Path, dict]:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    state_root = tmp_path / "state"
    run_root = tmp_path / "output" / run_id
    result = background.start_task(
        source=source,
        output_root=tmp_path / "output",
        run_root=run_root,
        state_root=state_root,
        command=command,
        resume_command=resume_command,
        pages="all",
        task_type=task_type,
        work_progress_path=work_progress_path,
        run_id=run_id,
    )
    return state_root, run_root, result


def test_detached_start_returns_once_and_keeps_page_output_on_disk(tmp_path: Path):
    marker = tmp_path / "worker-finished"
    script = (
        "import pathlib,time;"
        "[print(f'PAGE {i}', flush=True) for i in range(100)];"
        "time.sleep(1);"
        f"pathlib.Path({str(marker)!r}).write_text('done')"
    )

    started = time.monotonic()
    state_root, _, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", script],
        run_id="silent-100-pages",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert result["status"] == "started"
    assert result["run_id"] == "silent-100-pages"
    assert result["pid"] > 0
    assert result["task_type"] == "test"
    assert "PAGE" not in json.dumps(result)
    assert str(state_root.resolve()) in result["status_command"]
    assert not marker.exists()

    state = _wait_for_state(state_root, result["run_id"], {"completed"})
    assert marker.read_text(encoding="utf-8") == "done"
    assert state["status"] == "completed"
    log = (state_root / result["run_id"] / "run.log").read_text(encoding="utf-8")
    assert "PAGE 0" in log and "PAGE 99" in log
    events = [
        json.loads(line)
        for line in (state_root / result["run_id"] / "progress.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["event"] for event in events].count("completed") == 1
    assert not any("page" in event for event in events)


def test_status_reads_only_latest_snapshot_and_does_not_append_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_progress = tmp_path / "work-progress-current.json"
    work_progress.write_text(
        json.dumps({"status": "running", "completed_pages": list(range(1, 8)), "failed_pages": [4]}),
        encoding="utf-8",
    )
    state_root, _, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", "pass"],
        run_id="latest-only",
        work_progress_path=work_progress,
    )
    _wait_for_state(state_root, result["run_id"], {"completed"})
    ledger = state_root / result["run_id"] / "progress.jsonl"
    before = ledger.read_bytes()
    reads: list[Path] = []
    original = background._read_json

    def recording_read(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(background, "_read_json", recording_read)
    snapshot = background.status_task(state_root, result["run_id"])

    assert snapshot["completed_pages"] == 7
    assert snapshot["failed_pages"] == 1
    assert all(path.name != "progress.jsonl" for path in reads)
    assert reads.count(state_root / result["run_id"] / "progress-current.json") == 1
    assert reads.count(work_progress) == 1
    assert ledger.read_bytes() == before


def test_hybrid_failed_postprocess_does_not_hide_completed_pages(tmp_path: Path):
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps({"status": "needs-review", "page_completion": {
        "completed_pages": [1, 2, 3], "failed_pages": [4],
    }}), encoding="utf-8")

    status = background._refresh_page_counts({"work_progress_path": str(progress)})

    assert status["completed_pages"] == 3
    assert status["failed_pages"] == 1
    assert status["work_status"] == "needs-review"


def test_status_supports_runner_passed_count_and_cli_emits_one_json_line(tmp_path: Path):
    work_progress = tmp_path / "work-progress-current.json"
    work_progress.write_text(
        json.dumps({"status": "running", "passed_count": 12, "failed_pages": []}),
        encoding="utf-8",
    )
    state_root, _, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", "pass"],
        run_id="one-status-line",
        work_progress_path=work_progress,
    )
    _wait_for_state(state_root, result["run_id"], {"completed"})

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(background.__file__).resolve()),
            "status",
            result["run_id"],
            "--state-root",
            str(state_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    lines = completed.stdout.splitlines()
    assert completed.returncode == 0
    assert len(lines) == 1
    assert json.loads(lines[0])["completed_pages"] == 12
    assert completed.stderr == ""


def test_status_rejects_reused_or_stale_pid_identity(tmp_path: Path):
    state_root = tmp_path / "state"
    task_root = state_root / "stale-pid"
    task_root.mkdir(parents=True)
    timestamp = background.now_iso()
    background._write_json_atomic(
        task_root / "progress-current.json",
        {
            "run_id": "stale-pid",
            "source": str(tmp_path / "source.pdf"),
            "output_root": str(tmp_path / "output"),
            "run_root": str(tmp_path / "run"),
            "pid": os.getpid(),
            "process_group_id": os.getpgid(0),
            "worker_token": "token-that-is-not-in-this-pytest-command",
            "status": "running",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_pages": 0,
            "failed_pages": 0,
        },
    )

    snapshot = background.status_task(state_root, "stale-pid")

    assert snapshot["status"] == "failed"
    assert snapshot["active"] is False
    assert "PID identity" in snapshot["error"]


def test_cancel_terminates_the_entire_process_group(tmp_path: Path):
    child_pid_path = tmp_path / "grandchild.pid"
    script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid));"
        "time.sleep(30)"
    )
    state_root, _, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", script],
        run_id="cancel-process-group",
    )
    deadline = time.monotonic() + 5
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))

    cancelled = background.cancel_task(
        state_root,
        result["run_id"],
        timeout_seconds=5,
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["residual_processes"] == []
    assert background._process_record(child_pid) is None
    state = json.loads(
        (state_root / result["run_id"] / "progress-current.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "cancelled"


def test_cancel_never_signals_a_stale_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_root = tmp_path / "state"
    task_root = state_root / "cancel-stale"
    task_root.mkdir(parents=True)
    timestamp = background.now_iso()
    background._write_json_atomic(
        task_root / "progress-current.json",
        {
            "run_id": "cancel-stale",
            "source": str(tmp_path / "source.pdf"),
            "output_root": str(tmp_path / "output"),
            "run_root": str(tmp_path / "run"),
            "pid": os.getpid(),
            "process_group_id": os.getpgid(0),
            "worker_token": "wrong-token",
            "status": "running",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_pages": 0,
            "failed_pages": 0,
        },
    )

    def forbidden_killpg(*_args):
        raise AssertionError("stale PID must not be signalled")

    monkeypatch.setattr(background.os, "killpg", forbidden_killpg)
    snapshot = background.cancel_task(state_root, "cancel-stale")

    assert snapshot["status"] == "failed"
    assert snapshot["residual_processes"] == []
    assert "stale or mismatched PID" in snapshot["error"]


def test_cancel_handles_process_exit_race_as_one_cancelled_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_root = tmp_path / "state"
    task_root = state_root / "cancel-exit-race"
    task_root.mkdir(parents=True)
    timestamp = background.now_iso()
    background._write_json_atomic(
        task_root / "progress-current.json",
        {
            "run_id": "cancel-exit-race",
            "source": str(tmp_path / "source.pdf"),
            "output_root": str(tmp_path / "output"),
            "run_root": str(tmp_path / "run"),
            "pid": 987654,
            "process_group_id": 987654,
            "worker_token": "race-token",
            "task_type": "test",
            "status": "running",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_pages": 0,
            "failed_pages": 0,
        },
    )
    monkeypatch.setattr(background, "_task_is_active", lambda _state: True)
    monkeypatch.setattr(
        background.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(background, "_process_group_members", lambda _pgid: [])

    snapshot = background.cancel_task(state_root, "cancel-exit-race")

    assert snapshot["status"] == "cancelled"
    assert snapshot["residual_processes"] == []


def test_cancel_handles_process_exit_before_sigkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_root = tmp_path / "state"
    task_root = state_root / "cancel-before-sigkill"
    task_root.mkdir(parents=True)
    timestamp = background.now_iso()
    background._write_json_atomic(
        task_root / "progress-current.json",
        {
            "run_id": "cancel-before-sigkill",
            "source": str(tmp_path / "source.pdf"),
            "output_root": str(tmp_path / "output"),
            "run_root": str(tmp_path / "run"),
            "pid": 987655,
            "process_group_id": 987655,
            "worker_token": "race-token",
            "task_type": "test",
            "status": "running",
            "started_at": timestamp,
            "updated_at": timestamp,
            "completed_pages": 0,
            "failed_pages": 0,
        },
    )
    signals: list[int] = []

    def racing_killpg(_pgid: int, sig: int):
        signals.append(sig)
        if len(signals) == 2:
            raise ProcessLookupError()

    monkeypatch.setattr(background, "_task_is_active", lambda _state: True)
    monkeypatch.setattr(background.os, "killpg", racing_killpg)
    monkeypatch.setattr(
        background,
        "_wait_for_group_exit",
        lambda _pgid, _timeout: [{"pid": 987656, "command": "child"}],
    )
    monkeypatch.setattr(background, "_process_group_members", lambda _pgid: [])

    snapshot = background.cancel_task(state_root, "cancel-before-sigkill")

    assert signals == [background.signal.SIGTERM, background.signal.SIGKILL]
    assert snapshot["status"] == "cancelled"
    assert snapshot["residual_processes"] == []


def test_resume_reuses_the_existing_run_root(tmp_path: Path):
    marker = tmp_path / "resumed"
    resume_script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('yes')"
    state_root, run_root, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", "raise SystemExit(3)"],
        resume_command=[sys.executable, "-c", resume_script],
        run_id="resume-same-root",
    )
    failed = _wait_for_state(state_root, result["run_id"], {"failed"})
    assert failed["returncode"] == 3

    resumed = background.resume_task(state_root, result["run_id"])
    completed = _wait_for_state(state_root, result["run_id"], {"completed"})

    assert resumed["status"] == "started"
    assert resumed["task_type"] == "test"
    assert resumed["run_root"] == str(run_root.resolve())
    assert completed["run_root"] == str(run_root.resolve())
    assert completed["resume_count"] == 1
    assert marker.read_text(encoding="utf-8") == "yes"


def test_visual_resume_can_skip_optional_asset_pruning(tmp_path: Path):
    marker = tmp_path / "resume-args.json"
    resume_script = (
        "import json,pathlib,sys;"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))"
    )
    state_root, _, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", "raise SystemExit(3)"],
        resume_command=[sys.executable, "-c", resume_script],
        run_id="visual-resume-without-pruning",
        task_type="visual-pdf",
    )
    _wait_for_state(state_root, result["run_id"], {"failed"})

    background.resume_task(
        state_root,
        result["run_id"],
        no_visual_asset_pruning=True,
    )
    _wait_for_state(state_root, result["run_id"], {"completed"})

    assert json.loads(marker.read_text(encoding="utf-8")) == [
        "--no-visual-asset-pruning"
    ]


def test_failed_task_writes_one_actionable_terminal_event(tmp_path: Path):
    state_root, _, result = _start_test_task(
        tmp_path,
        command=[sys.executable, "-c", "print('private failure detail'); raise SystemExit(9)"],
        run_id="one-failure-summary",
    )
    state = _wait_for_state(state_root, result["run_id"], {"failed"})
    events = [
        json.loads(line)
        for line in (state_root / result["run_id"] / "progress.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert [event["event"] for event in events].count("failed") == 1
    assert state["returncode"] == 9
    assert "resume one-failure-summary" in state["next_action"]
    assert "private failure detail" not in json.dumps(result)
    assert "private failure detail" in (
        state_root / result["run_id"] / "run.log"
    ).read_text(encoding="utf-8")


def test_workflow_a_terminal_result_supplies_real_processed_page_count(tmp_path: Path):
    task_root = tmp_path / "task"
    task_root.mkdir()
    payload = {
        "status": "needs-review",
        "scope": {"pages": "all", "selected_pages": [1, 2, 3, 7]},
        "nested": {"selected_pages": [999]},
    }
    (task_root / "run.log").write_text(
        "runner diagnostic before result\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    state = background._refresh_terminal_runner_result(
        {
            "task_type": "workflow-a",
            "completed_pages": 0,
            "failed_pages": 0,
        },
        task_root,
    )

    assert state["completed_pages"] == 4
    assert state["failed_pages"] == 0
    assert state["work_status"] == "needs-review"


def test_cli_requires_explicit_task_type_and_workflow_route_is_not_silent():
    parser = background.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["start", "--source", "/tmp/book.pdf", "--output-root", "/tmp/out"]
        )

    args = parser.parse_args(
        [
            "start",
            "--task-type",
            "workflow-a",
            "--source",
            "/tmp/book.pdf",
            "--output-root",
            "/tmp/out",
        ]
    )
    _, command, resume_command, progress_path = background._build_pdf_commands(
        args, "run-1"
    )

    assert command[command.index("--output-root") + 1] == str(Path("/tmp/out").resolve())
    assert str(background.WORKFLOW_A_ADAPTER) in command
    assert str(background.GRAPH_ADAPTER) not in command
    assert resume_command is None
    assert progress_path is None


def test_cli_defaults_output_root_to_project_runs_directory():
    parser = background.build_parser()

    args = parser.parse_args(
        [
            "start",
            "--task-type",
            "native-pdf-batch",
            "--source",
            "/tmp/book.pdf",
        ]
    )

    assert args.output_root == background.ROOT / "runs"


def test_background_commands_keep_hybrid_and_visual_only_routes_explicit():
    parser = background.build_parser()
    hybrid = parser.parse_args(
        [
            "start",
            "--task-type",
            "hybrid-pdf",
            "--source",
            "/tmp/book.pdf",
            "--output-root",
            "/tmp/out",
            "--run-root",
            "/tmp/existing-run",
        ]
    )
    run_root, command, resume_command, progress_path = background._build_pdf_commands(
        hybrid, "run-1"
    )

    assert "--no-model" not in command
    assert "--visual-only" not in command
    assert command[-2:] == ["--dpi", "220"]
    assert resume_command == [*command, "--resume"]
    assert progress_path == run_root / "progress.json"

    visual = parser.parse_args(
        [
            "start",
            "--task-type",
            "visual-pdf",
            "--source",
            "/tmp/book.pdf",
            "--output-root",
            "/tmp/out",
        ]
    )
    visual_root, visual_command, _, _ = background._build_pdf_commands(visual, "run-2")
    assert visual_root.parent == Path("/tmp/out").resolve()
    assert re.fullmatch(r"book-\d{8}-\d{6}(?:-\d{2})?", visual_root.name)
    assert "--visual-only" in visual_command


def test_background_command_supports_local_apple_ocr_without_model_flags():
    parser = background.build_parser()
    args = parser.parse_args(
        [
            "start",
            "--task-type",
            "local-ocr-pdf",
            "--source",
            "/tmp/book.pdf",
            "--output-root",
            "/tmp/out",
            "--run-root",
            "/tmp/apple-ocr-run",
            "--timeout-seconds",
            "120",
            "--jobs",
            "3",
        ]
    )

    run_root, command, resume_command, progress_path = background._build_pdf_commands(
        args, "run-local-ocr"
    )

    assert str(background.LOCAL_OCR_ADAPTER) in command
    assert "--visual-only" not in command
    assert "--no-model" not in command
    assert "--dpi" in command
    assert command[command.index("--dpi") + 1] == "220"
    assert "--jobs" in command
    assert command[command.index("--jobs") + 1] == "3"
    assert resume_command == [*command, "--resume"]
    assert progress_path == run_root / "evidence" / "progress-current.json"


def test_background_commands_default_to_timestamped_source_named_run_root(monkeypatch):
    monkeypatch.setattr(
        background,
        "resolve_package_target",
        lambda output_root, source, **kwargs: (
            "Z001-人性即骗局-20260824-120000",
            output_root / "Z001-人性即骗局-20260824-120000",
        ),
    )
    parser = background.build_parser()
    native = parser.parse_args(
        [
            "start",
            "--task-type",
            "native-pdf",
            "--source",
            "/tmp/Z001 人性即骗局.pdf",
            "--output-root",
            "/tmp/out",
        ]
    )

    run_root, command, resume_command, progress_path = background._build_pdf_commands(
        native, "ignored-run-id"
    )

    assert run_root == Path("/tmp/out/Z001-人性即骗局-20260824-120000").resolve()
    assert "--run-root" in command
    assert command[command.index("--run-root") + 1] == str(run_root)
    assert resume_command == [*command, "--resume"]
    assert progress_path == run_root / "evidence" / "progress-current.json"
