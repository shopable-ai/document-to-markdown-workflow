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
