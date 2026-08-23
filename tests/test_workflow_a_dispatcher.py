from __future__ import annotations

import json
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


def test_registry_recommends_direct_execute_entrypoint():
    import yaml

    registry = yaml.safe_load((ROOT / "workflow-registry.yaml").read_text(encoding="utf-8"))
    entry = next(item for item in registry["workflows"] if item["id"] == "document-to-markdown-assets")

    assert entry["execution_entrypoint"].endswith("--execute")
    assert entry["plan_entrypoint"].endswith("--plan-only")


def test_dispatcher_routes_only_to_active_source_runner():
    source = DISPATCHER.read_text(encoding="utf-8")
    assert "workflow_a_source_runner.py" in source
    assert "workflow_a_candidate_compare.py" not in source

    sys.path.insert(0, str(DISPATCHER.parent))
    from workflow_a_dispatcher import ACTIVE_RUNNER

    assert ACTIVE_RUNNER == ROOT / "adapters/workflow_a_source_runner.py"
    retired_runner = ROOT / "archive" / "legacy-adapters" / ("workflow_a_" + "runner.py")
    assert not retired_runner.exists()


def test_workflow_excludes_retired_legacy_route():
    import yaml

    registry = yaml.safe_load((ROOT / "workflow-registry.yaml").read_text(encoding="utf-8"))
    entry = next(item for item in registry["workflows"] if item["id"] == "document-to-markdown-assets")
    workflow = yaml.safe_load((ROOT / "workflows/document-to-markdown-assets/workflow.yaml").read_text(encoding="utf-8"))

    assert entry["excluded_legacy_routes"] == ["workflow_a_runner", "workflow_a_legacy_loader", "legacy-document-profile"]
    assert workflow["execution_policy"]["runner_boundary"]["legacy_entrypoint"] is None
    assert workflow["execution_policy"]["legacy_code_policy"]["compatibility_entrypoints"] == []
    assert workflow["entrypoint"]["compatibility"] == []
    retired_loader = ROOT / "adapters" / ("workflow_a_" + "legacy_loader.py")
    assert not retired_loader.exists()


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


def test_dispatcher_propagates_needs_review_without_promoting_unreported_stages(tmp_path):
    sys.path.insert(0, str(DISPATCHER.parent))
    from workflow_a_dispatcher import apply_runner_result

    record = {
        "stages": [
            {"stage_id": "A1", "status": "complete"},
            *[{"stage_id": f"A{index}", "status": "not-run"} for index in range(2, 8)],
        ]
    }
    package = tmp_path / "reader-package"
    summary = {
        "status": "needs-review",
        "package_path": str(package),
        "validation": {"status": "not-run", "result_path": None},
    }

    parsed, verdict = apply_runner_result(
        record,
        {"returncode": 1, "stdout": json.dumps(summary, ensure_ascii=False, indent=2)},
    )

    assert parsed == summary
    assert verdict == record["status"] == "needs-review"
    assert record["package_path"] == str(package)
    assert record["validator_status"] == "not-run"
    assert record["stages"][0]["status"] == "complete"
    assert all(stage["status"] == "not-run" for stage in record["stages"][1:])


def test_dispatcher_rejects_self_reported_pass_without_validator_proof():
    sys.path.insert(0, str(DISPATCHER.parent))
    from workflow_a_dispatcher import apply_runner_result

    record = {"stages": [{"stage_id": f"A{index}", "status": "not-run"} for index in range(1, 8)]}
    summary = {"status": "passed", "package_path": "/tmp/package", "validation": {"status": "not-run"}}

    _, verdict = apply_runner_result(record, {"returncode": 0, "stdout": json.dumps(summary)})

    assert verdict == "needs-review"
    assert record["status"] == "needs-review"
    assert all(stage["status"] == "not-run" for stage in record["stages"])
