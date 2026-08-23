from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "adapters" / "lan_pdf_to_markdown.py"
FIXTURE = ROOT / "tests" / "fixtures" / "18个工作思维模型-last-2-slides" / "selected" / "18个工作思维模型-第24-25页.pdf"


def _payload(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout.strip(), result.stderr
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1])


def test_no_model_execute_writes_source_named_markdown_alias(tmp_path: Path):
    source = tmp_path / "第009部《绝秘人性天书》.pdf"
    shutil.copy2(FIXTURE, source)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "output"),
            "--pages",
            "1",
            "--execute",
            "--no-model",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    package = Path(payload["markdown"]).parent
    alias = package / "第009部《绝秘人性天书》.md"
    assert alias.is_file()
    assert payload["source_named_markdown"] == str(alias)
    assert alias.read_text(encoding="utf-8") == (package / "index.md").read_text(encoding="utf-8")
    page_evidence = json.loads(
        (Path(payload["evidence"]) / "page-001.json").read_text(encoding="utf-8")
    )
    assert page_evidence["bbox_metrics"]["status"] == "complete"
    assert "max_vertical_gap_ratio" in page_evidence["bbox_metrics"]
