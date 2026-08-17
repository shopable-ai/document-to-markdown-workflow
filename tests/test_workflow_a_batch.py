from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "adapters" / "workflow_a_batch.py"


def _module():
    sys.path.insert(0, str(BATCH.parent))
    import workflow_a_batch

    return workflow_a_batch


def test_batch_requires_at_least_one_source(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BATCH), "--output-root", str(tmp_path / "out")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "at least one" in result.stderr
    assert not (tmp_path / "out").exists()


def test_source_list_is_literal_and_deduplicated(tmp_path):
    module = _module()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    source_list = tmp_path / "sources.txt"
    source_list.write_text(f"# comment\n{first}\n{first}\n{second}\n", encoding="utf-8")
    sources, duplicates = module.normalise_sources([], source_list)
    assert sources == [first.resolve(), second.resolve()]
    assert duplicates[0]["status"] == "deduplicated"


def test_auto_concurrency_is_conservative_without_capacity_hint(monkeypatch):
    module = _module()
    monkeypatch.delenv("WORKFLOW_A_MAC96_CONCURRENCY", raising=False)
    resolved = module.resolve_concurrency("auto", {}, 4)
    assert resolved["workers"] == 1
    assert resolved["capacity_source"] == "conservative-default-no-verified-capacity"


def test_explicit_capacity_is_bounded_by_source_count(monkeypatch):
    module = _module()
    monkeypatch.setenv("WORKFLOW_A_MAC96_CONCURRENCY", "3")
    resolved = module.resolve_concurrency("auto", {}, 2)
    assert resolved["workers"] == 2
    assert resolved["capacity_source"] == "explicit-capacity-hint"


def test_batch_config_rejects_secret_keys(tmp_path):
    module = _module()
    config = tmp_path / "config.yaml"
    config.write_text("execution:\n  api_key: should-not-be-here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="secret-bearing"):
        module.load_config(config)


def test_context_mode_is_registered_in_runner_help():
    result = subprocess.run([sys.executable, str(ROOT / "archive/legacy-adapters/workflow_a_runner.py"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--context-mode" in result.stdout
    assert "--context-window" in result.stdout


def test_run_batch_requires_independent_package_acceptance(monkeypatch, tmp_path):
    module = _module()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf-fixture")

    def fake_runner(command, timeout):
        output_root = Path(command[command.index("--output-root") + 1])
        package_name = command[command.index("--package-name") + 1]
        package = output_root / package_name
        (package / "reports").mkdir(parents=True)
        (package / "reports/validation-result.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        return {"command": "fake", "started_at": "now", "finished_at": "now", "status": "complete", "returncode": 0, "stdout": json.dumps({"status": "passed", "output": str(package)}), "stderr": ""}

    monkeypatch.setattr(module, "_run_command", fake_runner)
    code, state = module.run_batch([source], tmp_path / "out", legacy_compat=True)
    assert code == 0
    assert state["status"] == "succeeded"
    assert state["summary"]["passed"] == 1
    assert state["outputs"]


def test_page_selector_and_batches_are_deterministic():
    module = _module()
    assert module.parse_page_selector("1-3,7", 10) == [1, 2, 3, 7]
    assert module.split_page_batches([3, 4, 5, 6, 7], 2) == [[3, 4], [5, 6], [7]]
    assert module.page_selector([3, 4, 7]) == "3,4,7"


def test_page_batch_checkpoint_rejects_changed_source(tmp_path):
    module = _module()
    source = tmp_path / "source.pdf"
    source.write_bytes((ROOT / "tests/fixtures/18个工作思维模型-last-2-slides/source/18个工作思维模型.pdf").read_bytes())
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"source_sha256": "different"}), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        module.run_page_batches(source, tmp_path / "out", "1", "1", 1, checkpoint, legacy_compat=True)


def test_batch_help_exposes_smoke_and_checkpoint_controls():
    result = subprocess.run([sys.executable, str(BATCH), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--batch-size" in result.stdout
    assert "--smoke-pages" in result.stdout
    assert "--resume-pages" in result.stdout


def test_page_batch_stops_on_failed_independent_validator(monkeypatch, tmp_path):
    module = _module()
    source = tmp_path / "source.pdf"
    source.write_bytes((ROOT / "tests/fixtures/18个工作思维模型-last-2-slides/source/18个工作思维模型.pdf").read_bytes())

    def fake_runner(command, timeout):
        output_root = Path(command[command.index("--output-root") + 1])
        package_name = command[command.index("--package-name") + 1]
        package = output_root / package_name
        (package / "evidence").mkdir(parents=True)
        (package / "reports").mkdir()
        pages = [int(value) for value in command[command.index("--pages") + 1].split(",")]
        failed = pages == [25]
        (package / "evidence/smoke-report.json").write_text(json.dumps({"status": "passed", "checked_pages": pages}), encoding="utf-8")
        (package / "reports/validation-result.json").write_text(json.dumps({"status": "failed" if failed else "needs-review", "failures": [{"code": "fixture-failure"}] if failed else []}), encoding="utf-8")
        return {"command": "fake", "started_at": "now", "finished_at": "now", "status": "complete", "returncode": 1 if failed else 0, "stdout": json.dumps({"status": "needs-review", "output": str(package)}), "stderr": ""}

    monkeypatch.setattr(module, "_run_command", fake_runner)
    code, state = module.run_page_batches(source, tmp_path / "out", "24-25", "24", 1, legacy_compat=True)
    assert code == 1
    assert state["status"] == "needs-review"
    assert state["stop_reason"]["batch_index"] == 2
    assert state["stop_reason"]["stage"] == "batch-validation"
    assert state["next_page"] == 25


def test_batch_cli_is_fail_closed_without_legacy_compat(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf-fixture")
    result = subprocess.run(
        [sys.executable, str(BATCH), "--source", str(source), "--output-root", str(tmp_path / "out")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "legacy-compat" in result.stderr
