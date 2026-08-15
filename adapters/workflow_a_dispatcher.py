#!/usr/bin/env python3
"""Small, observable dispatcher for Workflow A.

The project intentionally does not grow a general workflow engine here.  This
adapter reads the registered Workflow and its phase-gate specification, records
the A1-A7 execution envelope, then invokes the existing candidate comparator
and single-source runner as child commands.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "workflow-registry.yaml"
WORKFLOW = ROOT / "workflows/document-to-markdown-assets/workflow.yaml"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_specs() -> tuple[dict, dict, dict]:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
    entry = next((item for item in registry.get("workflows", []) if item.get("id") == workflow.get("id")), None)
    if entry is None:
        raise ValueError(f"workflow {workflow.get('id')!r} is not registered")
    if entry.get("path") != str(WORKFLOW.relative_to(ROOT)):
        raise ValueError("registry path does not match document-to-markdown-assets workflow")
    if workflow.get("id") != "document-to-markdown-assets" or len(workflow.get("stages", [])) != 7:
        raise ValueError("Workflow A must declare exactly A1-A7")
    if [stage.get("id") for stage in workflow["stages"]] != [f"A{i}" for i in range(1, 8)]:
        raise ValueError("Workflow A stages must be ordered A1-A7")
    return registry, entry, workflow


def run_command(command: list[str], *, cwd: Path, timeout: int = 1800) -> dict:
    record = {
        "command": shlex.join(command),
        "started_at": now(),
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "status": "not-run",
    }
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        record.update(
            {
                "returncode": proc.returncode,
                "stdout": proc.stdout[-20000:],
                "stderr": proc.stderr[-20000:],
                "status": "complete" if proc.returncode == 0 else "failed",
                "finished_at": now(),
            }
        )
    except Exception as exc:  # pragma: no cover - defensive runtime evidence
        record.update({"status": "failed", "error": repr(exc), "finished_at": now()})
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch registered Workflow A stages")
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate-comparison", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pages", required=True, help="explicit pages only; all is intentionally rejected")
    args = parser.parse_args()
    if args.workflow != "document-to-markdown-assets":
        parser.error("only document-to-markdown-assets is supported")
    if args.pages.strip().lower() == "all":
        parser.error("dispatcher requires explicit pages; --pages all is blocked for this bounded verification")
    source = args.source.expanduser().resolve()
    comparison = args.candidate_comparison.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    if not comparison.is_file():
        parser.error(f"candidate comparison does not exist: {comparison}")
    try:
        registry, entry, workflow = load_specs()
        comparison_data = json.loads(comparison.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        parser.error(f"workflow specification or candidate comparison is invalid: {exc}")
    preferred = Path(str(comparison_data.get("preferred_source", ""))).expanduser().resolve()
    if preferred != source:
        parser.error(f"source is not candidate comparison preferred_source: {preferred}")
    candidates = comparison_data.get("candidates") or []
    candidate_paths = [Path(str(item.get("path"))).expanduser().resolve() for item in candidates if item.get("path")]
    if len(candidate_paths) != 2 or any(not item.is_file() for item in candidate_paths):
        parser.error("candidate comparison must retain two readable source candidates")
    output_root.mkdir(parents=True, exist_ok=True)
    execution_path = output_root / "evidence" / "workflow-execution.json"
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    record = {
        "execution_version": "0.1.0",
        "workflow_id": workflow["id"],
        "workflow_version": workflow.get("version"),
        "registry_version": registry.get("version"),
        "started_at": started,
        "source": {"path": str(source), "sha256": sha256(source), "media_type": source.suffix.lower(), "pages": args.pages},
        "selected_pages": args.pages,
        "stages": [{"stage_id": stage["id"], "name": stage.get("name"), "status": "not-run"} for stage in workflow["stages"]],
        "commands": [],
        "fallback": [],
        "status": "running",
    }

    # Re-run the explicit candidate comparator using the candidates already
    # recorded in the user's comparison file.  This keeps the execution chain
    # observable without inventing a second selection policy.
    comparison_command = [
        sys.executable,
        str(ROOT / "adapters/workflow_a_candidate_compare.py"),
        "--source-a",
        str(candidate_paths[0]),
        "--source-b",
        str(candidate_paths[1]),
        "--pages",
        args.pages,
        "--output",
        str(comparison),
    ]
    comparison_record = run_command(comparison_command, cwd=ROOT)
    record["commands"].append({"stage": "candidate-comparison", **comparison_record})
    if comparison_record["status"] != "complete":
        record["status"] = "failed"
        record["finished_at"] = now()
        execution_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "failed", "execution": str(execution_path)}, ensure_ascii=False))
        return 1

    runner_command = [
        sys.executable,
        str(ROOT / "adapters/workflow_a_runner.py"),
        "--source",
        str(source),
        "--candidate-comparison",
        str(comparison),
        "--output-root",
        str(output_root),
        "--pages",
        args.pages,
    ]
    runner_record = run_command(runner_command, cwd=ROOT)
    record["commands"].append({"stage": "single-source-runner", **runner_record})
    for stage in record["stages"]:
        stage["status"] = "complete" if runner_record["status"] == "complete" else "needs-review"
    record["status"] = "complete" if runner_record["status"] == "complete" else "needs-review"
    record["finished_at"] = now()
    record["exit_code"] = runner_record.get("returncode")
    record["runner_summary"] = runner_record.get("stdout", "").splitlines()[-1:] or []
    # The runner summary names the selected package.  Put the final execution
    # record inside that package while retaining a root-level copy for failed
    # comparator/runner starts that never create a package.
    try:
        summary = json.loads(runner_record.get("stdout", "").splitlines()[-1])
        package_root = Path(str(summary.get("output", "")))
        if package_root.is_dir():
            package_execution = package_root / "evidence" / "workflow-execution.json"
            package_execution.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            execution_path = package_execution
    except (ValueError, OSError, IndexError):
        pass
    execution_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": record["status"], "execution": str(execution_path), "runner": runner_record.get("stdout", "").splitlines()[-1:]}, ensure_ascii=False))
    return 0 if runner_record.get("returncode") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
