from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "adapters" / "workflow_a_dispatcher.py"


def test_dispatcher_reads_registered_workflow_and_rejects_unbounded_pages(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(DISPATCHER),
            "--workflow",
            "document-to-markdown-assets",
            "--source",
            str(tmp_path / "missing.pdf"),
            "--candidate-comparison",
            str(tmp_path / "missing.json"),
            "--output-root",
            str(tmp_path / "out"),
            "--pages",
            "all",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "explicit pages" in result.stderr
    assert not (tmp_path / "out").exists()


def test_dispatcher_spec_order_is_a1_to_a7():
    sys.path.insert(0, str(DISPATCHER.parent))
    from workflow_a_dispatcher import load_specs

    registry, entry, workflow = load_specs()
    assert registry["version"] == "0.1.0"
    assert entry["id"] == workflow["id"] == "document-to-markdown-assets"
    assert [stage["id"] for stage in workflow["stages"]] == [f"A{i}" for i in range(1, 8)]


def test_dispatcher_routes_only_to_active_source_runner():
    source = DISPATCHER.read_text(encoding="utf-8")
    assert "workflow_a_source_runner.py" in source
    assert "workflow_a_runner.py" not in source
    assert "workflow_a_candidate_compare.py" not in source

    sys.path.insert(0, str(DISPATCHER.parent))
    from workflow_a_dispatcher import ACTIVE_RUNNER

    assert ACTIVE_RUNNER == ROOT / "adapters/workflow_a_source_runner.py"
    assert ACTIVE_RUNNER != ROOT / "archive/legacy-adapters/workflow_a_runner.py"


def test_dispatcher_candidate_gate_blocks_runner_prerequisite_failure(tmp_path):
    sys.path.insert(0, str(DISPATCHER.parent))
    from workflow_a_dispatcher import candidate_comparison_gate

    source = (tmp_path / "source.pdf").resolve()
    source.write_bytes(b"source")
    comparison = tmp_path / "comparison.json"
    comparison.write_text('{"preferred_source": "wrong.pdf", "candidates": [], "selected_pages": []}', encoding="utf-8")
    passed, failures = candidate_comparison_gate(comparison, source)
    assert not passed
    assert len(failures) == 3
