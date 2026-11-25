from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
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


def _collection_args(tmp_path: Path, *extras: str):
    inputs = [tmp_path / name for name in ("第一 本.pdf", "第二(版).PDF")]
    for path in inputs:
        path.write_bytes(b"%PDF-1.7\n")
    source_list = tmp_path / "books.txt"
    source_list.write_text("".join(f"{path}\n" for path in inputs), encoding="utf-8")
    args = background.build_parser().parse_args([
        "start", "--task-type", "pdf-collection", "--source-list", str(source_list),
        "--route", "hybrid", "--state-root", str(tmp_path / "state"),
        "--output-root", str(tmp_path / "output"), *extras,
    ])
    return args, inputs


def test_collection_wraps_one_serial_queue_and_resumes_with_a_frozen_input_list(tmp_path, monkeypatch):
    args, inputs = _collection_args(tmp_path)
    adapter = tmp_path / "fixture_collection.py"
    adapter.write_text(
        """import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
def flag(arg): return sys.argv[sys.argv.index(arg) + 1]
root = Path(flag('--batch-root'))
sources = Path(flag('--source-list')).read_text().splitlines()
(root / 'manifest.json').write_text(json.dumps({
    'mode': 'lan-pdf-route-aware-batch', 'config': {'pages': flag('--pages')},
    'inputs': [{'source_path': p, 'route': flag('--route')} for p in sources],
}))
marker = root / 'first-attempt'
first = not marker.exists()
marker.write_text('started')
updated_at = datetime.now(timezone.utc).isoformat()
if first:
    package = root / 'fixture-book'
    route = package / 'evidence/page-routes/p001.json'
    route.parent.mkdir(parents=True)
    route.write_text(json.dumps({'page': 1, 'status': 'passed', 'source_sha256': 'fixture'}))
    (root / 'results.json').write_text(json.dumps({'updated_at': updated_at, 'items': [{
        'source_path': sources[1], 'run_root': str(package), 'status': 'running', 'source_sha256': 'fixture',
    }]}))
(root / 'progress-current.json').write_text(json.dumps({
    'status': 'running' if first else 'passed',
    'updated_at': updated_at,
    'active_source': sources[1] if first else None,
    'strict_accepted_count': 1 if first else 2,
    'summary': {'planned_total': 2, 'passed': 1 if first else 2},
}))
if first: time.sleep(40)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(background, "COLLECTION_ADAPTER", adapter)
    started = background._start_collection(args)
    run_id = started["run_id"]
    root = Path(started["run_root"])
    assert root == tmp_path / "output/conversions/hybrid"
    assert started["task_type"] == "pdf-collection"
    assert started["expected_sources"] == 2
    try:
        already = background._start_collection(args)
        assert already["status"] == "already-running"
        assert already["pid"] == started["pid"]
        args.route = "native-only"
        other_route = background._start_collection(args)
        assert other_route["status"] == "collection-busy"
        assert other_route["owner"]["batch_root"] == str(root)
        assert not (root.parent / "native-only").exists()
        args.route = "hybrid"
        state = _wait_for_state(args.state_root, run_id, {"running"})
        assert state["command"][1] == str(adapter)
        assert state["command"].count("--source-list") == 1
        assert state["command"][state["command"].index("--min-free-bytes") + 1] == str(2**31)
        path = Path(state["source_list_snapshot"])
        assert path.read_text(encoding="utf-8").splitlines() == [str(p) for p in inputs]
        deadline = time.monotonic() + 5
        while not (root / "progress-current.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        # A newly launched status CLI consumes existing worker artifacts;
        # the already loaded worker needs no new producer code or restart.
        cli = subprocess.run(shlex.split(started["status_command"]), capture_output=True, text=True, check=True)
        assert len(cli.stdout.splitlines()) == 1 and cli.stderr == ""
        snapshot = json.loads(cli.stdout)
        assert snapshot["pid"] == started["pid"]
        assert snapshot["active_source"] == str(inputs[1])
        assert snapshot["strict_accepted_count"] == 1
        assert snapshot["batch_summary"]["planned_total"] == 2
        assert snapshot["current_phase"] == "page-processing"
        assert snapshot["progress_checkpoint"]["page"] == 1
    finally:
        cancelled = background.cancel_task(args.state_root, run_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["residual_processes"] == []
    assert cancelled["active_source"] is None

    args.source_list.unlink()  # The task no longer depends on the submitter's list.
    result = background.resume_task(args.state_root, run_id)
    assert result["run_root"] == str(root)
    assert result["run_id"] == run_id
    _wait_for_state(args.state_root, run_id, {"completed"})
    snapshot = background.status_task(args.state_root, run_id)
    assert snapshot["strict_accepted_count"] == 2
    assert snapshot["active_source"] is None
    (root / "manifest.json").unlink()
    inputs[0].write_bytes(b"different PDF bytes")
    with pytest.raises(ValueError, match="registered batch manifest is missing"):
        background.resume_task(args.state_root, run_id)
    assert background.status_task(args.state_root, run_id)["pid"] == result["pid"]


def test_collection_status_does_not_replay_stale_summary_while_resuming(tmp_path):
    progress = tmp_path / "progress-current.json"
    progress.write_text(json.dumps({
        "updated_at": "2026-11-24T09:00:00+00:00",
        "status": "paused", "strict_accepted_count": 2,
        "summary": {"planned_total": 3, "passed": 2},
        "active_source": "/old.pdf", "halt_reason": "insufficient-disk-headroom",
    }), encoding="utf-8")
    result = background._refresh_page_counts({
        "task_type": "pdf-collection", "run_root": str(tmp_path),
        "work_progress_path": str(progress), "resumed_at": "2026-11-24T10:00:00+00:00",
    })
    assert result["strict_accepted_count"] == 0
    assert all(result[key] is None for key in ("batch_summary", "active_source", "halt_reason", "work_status"))


def _collection_status_fixture(tmp_path, monkeypatch, *, route="hybrid"):
    now = 1_800_000_000
    monkeypatch.setattr(background.time, "time", lambda: now)
    monkeypatch.setattr(background, "_task_is_active", lambda _: True)
    monkeypatch.setattr(background, "_process_group_members", lambda _: [])
    state_root = tmp_path / "state"
    batch = tmp_path / "output" / route
    source = tmp_path / "当前 书.pdf"
    package = batch / "nest" / "当前 书"

    def write(path, payload, *, at=now - 1):
        background._write_json_atomic(path, payload)
        os.utime(path, (at, at))

    def stamp(seconds):
        return datetime.fromtimestamp(now - seconds, timezone.utc).isoformat()

    state = {
        "run_id": "fixture", "task_type": "pdf-collection", "status": "running",
        "started_at": stamp(720), "resumed_at": stamp(650), "updated_at": stamp(650),
        "source": str(tmp_path / "source-list.txt"), "output_root": str(batch.parent),
        "run_root": str(batch), "route": route, "expected_sources": 2,
        "work_progress_path": str(batch / "progress-current.json"), "process_group_id": 42,
    }
    progress = {
        "updated_at": stamp(600), "status": "running", "active_source": str(source),
        "summary": {"planned_total": 2, "passed": 1}, "strict_accepted_count": 1,
    }
    results = {"updated_at": progress["updated_at"], "items": [{
        "source_path": str(source), "run_root": str(package), "status": "running",
        "source_sha256": "fixture-hash",
    }]}
    task = state_root / "fixture"
    write(task / "progress-current.json", state)
    (task / "progress.jsonl").write_text("fixture ledger\n", encoding="utf-8")
    write(batch / "progress-current.json", progress)
    write(batch / "results.json", results)
    return task, state, progress, results, package, source, write, stamp


def test_collection_status_observes_live_page_and_latest_durable_page_without_writing(tmp_path, monkeypatch):
    task, _, _, _, package, source, write, stamp = _collection_status_fixture(tmp_path, monkeypatch)
    routes = package / "evidence" / "page-routes"
    records = {"status": "passed", "source_sha256": "fixture-hash"}
    write(routes / "p099.json", {**records, "page": 99}, at=1_800_000_000 - 700)  # Previous attempt.
    write(routes / "p006.json", {**records, "page": 6}, at=1_800_000_000 - 30)
    write(routes / "p007.json", {**records, "page": 7}, at=1_800_000_000 - 12)
    ocr = background.ROOT / "adapters" / "apple_vision_pdf_to_markdown.py"
    visual = package / "pages/page-008/visual"
    monkeypatch.setattr(background, "_process_group_members", lambda pgid: [
        {"command": f"python {ocr} --source {source} --run-root {package} other/visual --pages 91"},
        {"command": f"python {ocr} --source {source}.old --run-root {visual} --pages 92"},
        {"command": shlex.join(["python", str(ocr), "--source", str(source), "--run-root", str(visual), "--pages", "8"])},
    ] if pgid == 42 else [])
    tracked = [task / "progress-current.json", task / "progress.jsonl", routes / "p007.json"]
    before = [path.read_bytes() for path in tracked]
    reads = []
    reader = background._read_json

    def read(path):
        reads.append(path)
        return reader(path)

    monkeypatch.setattr(background, "_read_json", read)
    snapshot = background.status_task(task.parent, task.name)

    assert snapshot["active"] is True
    assert snapshot["strict_accepted_count"] == 1
    assert snapshot["current_phase"] == "apple-vision-ocr"
    assert snapshot["phase_source"] == "process"
    assert snapshot["current_page"] == 8
    assert snapshot["progress_checkpoint"] == {
        "kind": "page", "page": 7, "status": "passed",
        "updated_at": stamp(12), "age_seconds": 12,
        "freshness": "recent", "stale_after_seconds": 300,
    }
    assert [path for path in reads if path.parent == routes] == [routes / "p007.json"]
    assert all(path.name not in {"progress.jsonl", "run.log"} for path in reads)
    assert [path.read_bytes() for path in tracked] == before


def test_collection_no_recent_checkpoint_is_not_worker_failure(tmp_path, monkeypatch):
    task, _, _, _, package, source, write, _ = _collection_status_fixture(tmp_path, monkeypatch)
    write(package / "progress.json", {"source": str(source), "stage": "old-stage"}, at=1_800_000_000 - 625)
    write(package / "evidence/page-routes/p010.json", {
        "page": 10, "status": "passed", "source_sha256": "fixture-hash",
    }, at=1_800_000_000 - 605)
    ocr = background.ROOT / "adapters" / "apple_vision_pdf_to_markdown.py"
    monkeypatch.setattr(background, "_process_group_members", lambda _: [
        {"command": f"python {ocr} --source {source} --run-root {package}/pages/page-001/visual --pages 1"},
    ])

    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["status"] == "running"
    assert snapshot["active"] is True
    assert snapshot["current_page"] == 1
    assert snapshot["current_phase"] == "apple-vision-ocr"
    assert snapshot["progress_checkpoint"]["kind"] == "batch"
    assert snapshot["progress_checkpoint"]["page"] is None
    assert snapshot["progress_checkpoint"]["freshness"] == "no-recent-checkpoint"
    assert snapshot["progress_checkpoint"]["age_seconds"] == 600
    assert "error" not in snapshot and "next_action" not in snapshot


def test_collection_status_separates_unverified_worker_from_checkpoint_age(tmp_path, monkeypatch):
    task, _, _, _, _, _, _, _ = _collection_status_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(background, "_task_is_active", lambda _: False)
    monkeypatch.setattr(background, "_process_group_members", lambda _: pytest.fail("worker identity unverified"))
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["status"] == "failed"
    assert snapshot["active"] is False
    assert snapshot["current_phase"] == "worker-unverified"
    assert snapshot["progress_checkpoint"]["freshness"] == "not-running"
    assert "PID identity" in snapshot["error"]


def test_collection_status_ignores_other_results_generation_and_previous_activation(tmp_path, monkeypatch):
    task, state, _, results, package, source, write, stamp = _collection_status_fixture(tmp_path, monkeypatch)
    write(package / "progress.json", {"source": str(source), "stage": "wrong-generation"})
    write(Path(state["run_root"]) / "results.json", {**results, "updated_at": stamp(500)})
    monkeypatch.setattr(background, "_process_group_members", lambda _: pytest.fail("not the active generation"))
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["current_phase"] == "source-starting"
    assert snapshot["current_page"] is None
    assert snapshot["progress_checkpoint"]["kind"] == "batch"

    write(task / "progress-current.json", {**state, "resumed_at": stamp(10)})
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["current_phase"] == "queue-preparing"
    assert snapshot["active_source"] is None
    assert snapshot["progress_checkpoint"]["freshness"] == "awaiting-checkpoint"
    assert snapshot["progress_checkpoint"]["updated_at"] is None


def test_collection_status_uses_reader_phase_when_process_inspection_unavailable(tmp_path, monkeypatch):
    task, state, progress, _, package, source, write, _ = _collection_status_fixture(tmp_path, monkeypatch)
    batch = Path(state["run_root"])
    write(batch / "manifest.json", {"updated_at": progress["updated_at"], "run_roots": {
        str(source): {"path": str(package), "source_sha256": "fixture-hash"},
    }})
    (batch / "results.json").unlink()  # The smaller registration is sufficient.
    write(package / "progress.json", {
        "source": str(source), "source_sha256": "fixture-hash", "status": "running",
        "reader_visual": {"stage": "reader_visual_projection", "status": "running"},
    })

    def unavailable(_):
        raise RuntimeError("ps unavailable")

    monkeypatch.setattr(background, "_process_group_members", unavailable)
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["current_phase"] == "reader_visual_projection"
    assert snapshot["phase_source"] == "checkpoint"
    assert snapshot["current_page"] is None
    assert snapshot["progress_checkpoint"]["kind"] == "book"
    assert snapshot["progress_checkpoint"]["freshness"] == "recent"


def test_collection_status_tracks_secondary_ocr_checkpoint_until_audit_is_published(tmp_path, monkeypatch):
    task, _, _, _, package, source, write, _ = _collection_status_fixture(tmp_path, monkeypatch)
    write(package / "progress.json", {
        "source": str(source), "source_sha256": "fixture-hash", "status": "running",
        "reader_visual": {"stage": "reader_visual_projection", "status": "running"},
    }, at=1_800_000_000 - 30)
    write(package / "evidence/reader-scan-ocr/p007.json", {
        "schema": "pdf-scan-ocr", "source_sha256": "fixture-hash", "page": 7, "status": "ok",
    }, at=1_800_000_000 - 5)

    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["current_phase"] == "reader-scan-ocr"
    assert snapshot["current_page"] is None  # The completed scan is not a live worker page.
    assert snapshot["progress_checkpoint"]["kind"] == "scan-ocr"
    assert snapshot["progress_checkpoint"]["page"] == 7
    assert snapshot["progress_checkpoint"]["age_seconds"] == 5

    write(package / "reports/reader-visual-audit.json", {})
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["current_phase"] == "reader-projection-verification"
    assert snapshot["progress_checkpoint"]["page"] is None


def test_collection_status_does_not_read_oversized_book_snapshot(tmp_path, monkeypatch):
    task, _, _, _, package, source, write, _ = _collection_status_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(background, "COLLECTION_OBSERVATION_JSON_MAX_BYTES", 1024)
    write(package / "progress.json", {"source": str(source), "details": "x" * 1024})
    original = background._read_json

    def read(path):
        assert path != package / "progress.json"
        return original(path)

    monkeypatch.setattr(background, "_read_json", read)
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["status"] == "running"
    assert snapshot["progress_checkpoint"]["kind"] == "batch"


def test_collection_status_observes_native_legacy_page_checkpoint(tmp_path, monkeypatch):
    task, _, _, _, package, source, write, _ = _collection_status_fixture(tmp_path, monkeypatch, route="native-only")
    write(package / "pages/page-012/page-012/summary.json", {
        "source": str(source), "source_sha256": "fixture-hash", "status": "passed", "selected_pages": [12],
    })
    snapshot = background.status_task(task.parent, task.name)
    assert snapshot["current_phase"] == "page-processing"
    assert snapshot["current_page"] is None
    assert snapshot["progress_checkpoint"]["page"] == 12


def test_collection_uses_real_batch_entrypoint_and_reports_disk_pause(tmp_path):
    args, _ = _collection_args(tmp_path, "--min-free-bytes", str(2**62))
    started = background._start_collection(args)
    run_id = started["run_id"]
    try:
        state = _wait_for_state(args.state_root, run_id, {"paused", "failed"})
        assert state["status"] == "paused", Path(started["log_path"]).read_text()
        assert Path(state["command"][1]) == background.COLLECTION_ADAPTER
        assert state["returncode"] == 75
        manifest = json.loads((Path(started["run_root"]) / "manifest.json").read_text())
        assert manifest["mode"] == "lan-pdf-route-aware-batch"
        assert len(manifest["inputs"]) == 2
        snapshot = background.status_task(args.state_root, run_id)
        assert snapshot["halt_reason"] == "insufficient-disk-headroom"
        assert snapshot["strict_accepted_count"] == 0
        assert snapshot["active"] is False
        assert snapshot["active_source"] is None
        assert snapshot["current_phase"] == "paused"
        assert snapshot["current_page"] is None
        assert snapshot["progress_checkpoint"]["freshness"] == "not-running"
        cancelled = background.cancel_task(args.state_root, run_id)
        assert cancelled["status"] == "cancelled"
        assert cancelled["residual_processes"] == []

        # An official, stopped batch can be adopted at its registered root;
        # its manifest, rather than reconstructed list metadata, drives it.
        args.run_root = Path(started["run_root"])
        args.state_root = tmp_path / "adopted-state"
        adopted = background._start_collection(args)
        state = _wait_for_state(args.state_root, adopted["run_id"], {"paused", "failed"})
        assert state["status"] == "paused"
        assert "--source-list" not in state["command"]
        assert adopted["run_root"] == started["run_root"]
        args.pages = "1"
        args.state_root = tmp_path / "mismatch-state"
        with pytest.raises(ValueError, match="does not match"):
            background._start_collection(args)
    finally:
        for state_root in (tmp_path / "state", tmp_path / "adopted-state"):
            if (state_root / run_id / "progress-current.json").exists():
                if background.status_task(state_root, run_id)["active"]:
                    background.cancel_task(state_root, run_id)


def test_hybrid_resume_persists_disk_floor_and_passes_one_override_to_batch(tmp_path, monkeypatch):
    args, _ = _collection_args(tmp_path)
    adapter = tmp_path / "fixture_disk_floor.py"
    adapter.write_text(
        """import json, sys
from datetime import datetime, timezone
from pathlib import Path
def flag(arg): return sys.argv[sys.argv.index(arg) + 1]
root = Path(flag('--batch-root'))
manifest = root / 'manifest.json'
if not manifest.exists():
    sources = Path(flag('--source-list')).read_text().splitlines()
    manifest.write_text(json.dumps({
        'mode': 'lan-pdf-route-aware-batch', 'config': {'pages': flag('--pages')},
        'inputs': [{'source_path': path, 'route': flag('--route')} for path in sources],
    }))
(root / 'batch-argv.json').write_text(json.dumps(sys.argv[1:]))
(root / 'progress-current.json').write_text(json.dumps({
    'status': 'paused', 'updated_at': datetime.now(timezone.utc).isoformat(),
    'halt_reason': 'insufficient-disk-headroom',
}))
raise SystemExit(75)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(background, "COLLECTION_ADAPTER", adapter)
    started = background._start_collection(args)
    run_id = started["run_id"]
    root = Path(started["run_root"])
    original = _wait_for_state(args.state_root, run_id, {"paused"})
    original_command = original["command"]
    assert original["resume_command"] == original_command
    assert "--disk-floor-bytes" not in original_command
    assert original_command[original_command.index("--min-free-bytes") + 1] == str(2**31)
    assert "disk_floor_bytes" not in background.status_task(args.state_root, run_id)
    manifest = (root / "manifest.json").read_bytes()

    # The same public resume command works with an explicit override and later
    # without one. Reapplying the same value or changing it never adds a copy.
    resume_cli = shlex.split(started["resume_command"])
    for count, (options, floor) in enumerate([
        (["--disk-floor-bytes", "500000000"], 500_000_000),
        (["--disk-floor-bytes=500000000"], 500_000_000),
        (["--disk-floor-bytes", "650000000"], 650_000_000),
        ([], 650_000_000),
    ], 1):
        cli = subprocess.run([*resume_cli, *options], capture_output=True, text=True, check=True)
        reply = json.loads(cli.stdout)
        assert (reply["run_id"], reply["run_root"], reply["disk_floor_bytes"]) == (run_id, str(root), floor)
        state = _wait_for_state(args.state_root, run_id, {"paused", "failed"})
        assert state["status"] == "paused", Path(started["log_path"]).read_text()
        assert state["resume_count"] == count
        expected = [*original_command, "--disk-floor-bytes", str(floor)]
        assert state["command"] == original_command
        assert state["resume_command"] == state["active_command"] == expected
        batch_argv = json.loads((root / "batch-argv.json").read_text())
        assert batch_argv[:-2] == expected[2:]
        assert batch_argv[-2] == "--collection-lock-fd"
        assert background.status_task(args.state_root, run_id)["disk_floor_bytes"] == floor
        assert (root / "manifest.json").read_bytes() == manifest


@pytest.mark.parametrize("value", ["-1", "0", "499999999", "500000000.5", "500MB"])
def test_resume_cli_rejects_invalid_disk_floors(tmp_path, capsys, value):
    argv = ["resume", "unknown", "--state-root", str(tmp_path), "--disk-floor-bytes", value]
    if value in {"500000000.5", "500MB"}:
        with pytest.raises(SystemExit) as exc:
            background.main(argv)
        assert exc.value.code == 2
        assert "invalid int value" in capsys.readouterr().err
    else:
        assert background.main(argv) == 2
        reply = json.loads(capsys.readouterr().out)
        assert reply["status"] == "failed"
        assert "integer >= 500000000" in reply["error"]
    assert not (tmp_path / "unknown").exists()


@pytest.mark.parametrize(("task_type", "route"), [("visual-pdf", None), ("pdf-collection", "native-only")])
def test_resume_disk_floor_rejects_other_tasks_without_state_changes(tmp_path, task_type, route):
    state_path = tmp_path / "fixture" / "progress-current.json"
    background._write_json_atomic(state_path, {"status": "paused", "task_type": task_type, "route": route})
    original = state_path.read_bytes()
    with pytest.raises(ValueError, match="only valid for registered hybrid pdf-collection"):
        background.resume_task(tmp_path, "fixture", disk_floor_bytes=500_000_000)
    assert state_path.read_bytes() == original


@pytest.mark.parametrize("blocker", ["missing-manifest", "wrong-manifest", "collection-lock", "running"])
def test_resume_disk_floor_preserves_existing_admission_checks(tmp_path, blocker):
    args, sources = _collection_args(tmp_path)
    root = args.output_root / "conversions/hybrid"
    root.mkdir(parents=True)
    state_path = args.state_root / "fixture" / "progress-current.json"
    background._write_json_atomic(state_path, {
        "status": "running" if blocker == "running" else "failed",
        "task_type": "pdf-collection", "route": "hybrid", "run_root": str(root), "pages": "all",
        "expected_sources": len(sources), "source_list_snapshot": str(args.source_list),
        "resume_command": [sys.executable, str(background.COLLECTION_ADAPTER)],
    })
    original = state_path.read_bytes()
    if blocker != "missing-manifest":
        background._write_json_atomic(root / "manifest.json", {
            "mode": "lan-pdf-route-aware-batch",
            "config": {"pages": "1" if blocker == "wrong-manifest" else "all"},
            "inputs": [{"source_path": str(path), "route": "hybrid"} for path in sources],
        })
    with (root.parent / ".pdf-collection-queue.lock").open("w+") as lock:
        if blocker == "collection-lock":
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        error = {
            "missing-manifest": "registered batch manifest is missing",
            "wrong-manifest": "existing batch manifest does not match",
            "collection-lock": "queue still owns",
            "running": "cannot resume from status running",
        }[blocker]
        with pytest.raises(ValueError, match=error):
            background.resume_task(args.state_root, "fixture", disk_floor_bytes=500_000_000)
    assert state_path.read_bytes() == original


def test_collection_does_not_spawn_over_official_foreground_queue(tmp_path):
    args, _ = _collection_args(tmp_path)
    batch_root = args.output_root / "conversions/hybrid"
    path = batch_root.parent / ".pdf-collection-queue.lock"
    path.parent.mkdir(parents=True)
    with path.open("w+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        json.dump({"pid": os.getpid(), "batch_root": str(batch_root)}, lock)
        lock.flush()
        snapshot = background._start_collection(args)
        assert snapshot["status"] == "already-running"
        assert snapshot["managed"] is False
        assert not batch_root.exists()


def test_collection_checks_batch_acceptance_instead_of_trusting_zero_exit(tmp_path, monkeypatch):
    args, _ = _collection_args(tmp_path)
    adapter = tmp_path / "no_batch_snapshot.py"
    adapter.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(background, "COLLECTION_ADAPTER", adapter)
    started = background._start_collection(args)
    state = _wait_for_state(args.state_root, started["run_id"], {"failed", "completed"})
    assert state["status"] == "failed"
    assert state["returncode"] == 0
    assert "without a fresh, complete batch acceptance snapshot" in state["error"]


def test_collection_cancel_reports_possible_orphan_without_signalling_it(tmp_path, monkeypatch):
    args, _ = _collection_args(tmp_path)
    adapter = tmp_path / "fixture_orphan.py"
    adapter.write_text(
        "import os, sys, time\nfrom pathlib import Path\n"
        "root = Path(sys.argv[sys.argv.index('--batch-root') + 1])\n"
        "(root / 'child-pid').write_text(str(os.getpid()))\n"
        "time.sleep(40)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(background, "COLLECTION_ADAPTER", adapter)
    started = background._start_collection(args)
    run_id, pgid = started["run_id"], started["pid"]
    path = Path(started["run_root"]) / "child-pid"
    try:
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        orphan_pid = int(path.read_text())
        os.kill(pgid, signal.SIGKILL)  # Test-only: the inherited-lock child survives.
        state = _wait_for_state(args.state_root, run_id, {"running"})
        while background._task_is_active(state) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not background._task_is_active(state)

        def forbid_signal(*_):
            pytest.fail("cancellation must not signal a group with unverified identity")

        with monkeypatch.context() as safety:
            safety.setattr(background.os, "killpg", forbid_signal)
            result = background.cancel_task(args.state_root, run_id)
            assert result["status"] == "failed"
            assert result["residual_status"] == "unverified-group-members"
            assert orphan_pid in {member["pid"] for member in result["residual_processes"]}
            with pytest.raises(ValueError, match="queue still owns"):
                background.resume_task(args.state_root, run_id)
            safety.setattr(background, "_process_group_members", lambda _: [])
            result = background.cancel_task(args.state_root, run_id)
            assert result["residual_status"] == "unknown"
            assert result["residual_processes"] is None
    finally:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("invalid", ["relative.pdf", "/missing.pdf", "non-pdf"])
def test_collection_rejects_invalid_inputs_before_start(tmp_path, invalid):
    args, inputs = _collection_args(tmp_path)
    if invalid == "non-pdf":
        path = tmp_path / "wrong.docx"
        path.touch()
        invalid = str(path)
    args.source_list.write_text(f"{inputs[0]}\n{invalid}\n", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        background._start_collection(args)
    assert not args.output_root.exists()
    assert not args.state_root.exists()


def test_collection_production_contract_registry_and_cli_agree():
    import yaml

    def read(relative):
        return yaml.safe_load((background.ROOT / relative).read_text(encoding="utf-8"))

    runtime = read("contracts/workflow-a-production-runtime-context.yaml")["execution"]["pdf_collection"]
    lifecycle = read("contracts/long-task-interaction.yaml")["pdf_collection"]
    workflow = read("workflows/document-to-markdown-assets/workflow.yaml")
    registry = read("workflow-registry.yaml")
    entry = next(item for item in registry["workflows"] if item["id"] == workflow["id"])
    assert entry["batch_entrypoint_status"].startswith("blocked")
    assert entry["pdf_collection_entrypoint"] == runtime["entrypoint"] == workflow["entrypoint"]["pdf_collection"]
    assert runtime["entrypoint"].endswith(lifecycle["start"])
    assert lifecycle["disk_precheck"]["default_bytes"] == background.COLLECTION_MIN_FREE_BYTES
    for route in runtime["routes"]:
        command = runtime["entrypoint"].replace("<hybrid|native-only>", route)
        args = background.build_parser().parse_args(command.split()[2:])
        assert args.task_type == background.COLLECTION_TASK
        assert args.route == route


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
