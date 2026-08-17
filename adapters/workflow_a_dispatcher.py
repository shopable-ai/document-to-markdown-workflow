#!/usr/bin/env python3
"""Small, observable dispatcher for the active source-independent Workflow A runner.

The dispatcher reads the registered Workflow and its phase-gate specification,
records the A1-A7 envelope, runs the explicit candidate comparison, and then
invokes the active plan-only source runner.  The legacy document-profile
runner is deliberately not part of this path.
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

try:
    from workflow_a_preflight import run_preflight
except ImportError:  # pragma: no cover - package-style imports in test tools
    from .workflow_a_preflight import run_preflight


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "workflow-registry.yaml"
WORKFLOW = ROOT / "workflows/document-to-markdown-assets/workflow.yaml"
ACTIVE_RUNNER = ROOT / "adapters/workflow_a_source_runner.py"


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


def candidate_comparison_gate(path: Path, source: Path) -> tuple[bool, list[str]]:
    """Validate the completed preflight before starting the conversion runner."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"candidate comparison is unreadable: {exc}"]
    failures: list[str] = []
    if not value.get("preferred_source"):
        failures.append("preferred_source is missing")
    elif Path(str(value["preferred_source"])).expanduser().resolve() != source:
        failures.append("preferred_source does not match selected source")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        failures.append("candidate comparison must retain exactly two candidates")
    if not value.get("selected_pages"):
        failures.append("selected_pages is missing")
    return not failures, failures


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
    preflight = run_preflight(source, output_root, args.pages, candidate_comparison=comparison)
    preflight_path = output_root / ".preflight" / f"{source.stem}-{preflight['source'].get('sha256', 'unknown')[:8]}-{now().replace(':', '').replace('+00:00', 'z')}.json"
    preflight_path.parent.mkdir(parents=True, exist_ok=True)
    preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8")
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
        "preflight": {"status": preflight["status"], "can_start": preflight["can_start"], "path": str(preflight_path), "failures": preflight.get("failures", []), "warnings": preflight.get("warnings", [])},
        "status": "running",
    }

    if not preflight["can_start"]:
        record["status"] = "failed"
        record["stop_reason"] = {"stage": "execution_preflight", "diagnostics": [item["message"] for item in preflight.get("failures", [])], "next_action": preflight["next_action"]}
        record["finished_at"] = now()
        execution_path.parent.mkdir(parents=True, exist_ok=True)
        execution_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "failed", "execution": str(execution_path), "preflight": str(preflight_path)}, ensure_ascii=False))
        return 1

    # Plan-only dispatch consumes the already recorded comparison as input.
    # Re-running the comparator here would re-enter the archived compatibility
    # adapter through its historical helper imports.
    comparison_record = {
        "status": "skipped",
        "command": "not-run; candidate comparison was supplied and preflight-checked",
        "started_at": now(),
        "finished_at": now(),
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }
    record["commands"].append({"stage": "candidate-comparison", **comparison_record})
    comparison_ok, comparison_failures = candidate_comparison_gate(comparison, source)
    record["stages"][0].update({"status": "complete" if comparison_ok else "failed", "diagnostics": comparison_failures})
    if not comparison_ok:
        record["status"] = "failed"
        record["stop_reason"] = {"stage": "candidate-comparison", "diagnostics": comparison_failures, "next_action": "修复来源候选预检后重新运行；不得启动后续 runner。"}
        record["finished_at"] = now()
        execution_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "failed", "execution": str(execution_path)}, ensure_ascii=False))
        return 1

    runner_command = [
        sys.executable,
        str(ACTIVE_RUNNER),
        "--source",
        str(source),
        "--candidate-comparison",
        str(comparison),
        "--output-root",
        str(output_root),
        "--pages",
        args.pages,
        "--plan-only",
    ]
    runner_record = run_command(runner_command, cwd=ROOT)
    record["commands"].append({"stage": "single-source-runner", **runner_record})
    runner_summary = {}
    try:
        runner_summary = json.loads(runner_record.get("stdout", ""))
    except ValueError:
        runner_summary = {}
    plan_only = runner_summary.get("status") == "planned"
    for stage in record["stages"]:
        stage["status"] = "needs-review" if plan_only else "complete" if runner_record["status"] == "complete" else "needs-review"
    record["status"] = "needs-review" if plan_only else "complete" if runner_record["status"] == "complete" else "needs-review"
    if plan_only:
        record["next_action"] = "通用 execute 尚未提升；当前仅保存来源无关执行计划，不得声称转换完成。"
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
    return 0 if runner_record.get("returncode") == 0 and not plan_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
