#!/usr/bin/env python3
"""Optional bounded dispatcher for the active source-independent runner.

The ordinary single-source entrypoint is ``workflow_a_source_runner.py``.
This dispatcher is reserved for an already supplied same-document candidate
comparison and an explicit A1-A7 envelope; it invokes the active deterministic
runner in execute mode and never routes through the retired document-profile
runner.
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
CANONICAL_VERDICTS = {"passed", "needs-review", "failed", "planned"}
STAGE_STATUSES = {"complete", "needs-review", "unavailable", "failed", "not-run"}


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


def parse_runner_summary(stdout: str) -> dict:
    """Read either the runner's pretty JSON or a final compact JSON line."""
    payload = stdout.strip()
    if not payload:
        return {}
    try:
        value = json.loads(payload)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    for line in reversed(payload.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _reported_validator_status(summary: dict) -> str | None:
    direct = summary.get("validator_status")
    if isinstance(direct, str):
        return direct
    validation = summary.get("validation")
    if isinstance(validation, dict) and isinstance(validation.get("status"), str):
        return validation["status"]
    return None


def _runner_verdict(summary: dict, returncode: int | None) -> str:
    """Normalize the child result without upgrading process success to proof."""
    reported = summary.get("status")
    if reported not in CANONICAL_VERDICTS:
        return "failed" if returncode not in {0, None} else "needs-review"
    if reported == "passed":
        if returncode == 0 and _reported_validator_status(summary) == "passed":
            return "passed"
        return "needs-review"
    if reported == "failed":
        return "failed"
    return "needs-review"


def apply_runner_result(record: dict, runner_record: dict) -> tuple[dict, str]:
    """Propagate only runner facts that were actually reported."""
    summary = parse_runner_summary(str(runner_record.get("stdout", "")))
    verdict = _runner_verdict(summary, runner_record.get("returncode"))

    reported_stages = summary.get("stage_evidence")
    if not isinstance(reported_stages, list):
        processing = summary.get("processing")
        reported_stages = (processing.get("stage_evidence") if isinstance(processing, dict) else []) or []
    stage_statuses = {
        item.get("stage_id"): item.get("status")
        for item in reported_stages
        if isinstance(item, dict)
        and item.get("stage_id")
        and item.get("status") in STAGE_STATUSES
    }
    for stage in record.get("stages", []):
        if stage.get("stage_id") in stage_statuses:
            stage["status"] = stage_statuses[stage["stage_id"]]

    record["status"] = verdict
    record["exit_code"] = runner_record.get("returncode")
    record["runner_summary"] = summary
    record["package_path"] = summary.get("package_path")
    record["validator_status"] = _reported_validator_status(summary) or "not-run"
    validation_path = summary.get("validation_result_path")
    if validation_path is None and isinstance(summary.get("evidence"), dict):
        validation_path = summary["evidence"].get("validation")
    record["validation_result_path"] = validation_path
    return summary, verdict


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

    # Bounded dispatch consumes the already recorded comparison as input.
    # Re-running the comparator here would add an unnecessary second route;
    # ordinary single-source runs should bypass this dispatcher entirely.
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
        "--execute",
    ]
    runner_record = run_command(runner_command, cwd=ROOT)
    record["commands"].append({"stage": "single-source-runner", **runner_record})
    runner_summary, verdict = apply_runner_result(record, runner_record)
    if runner_summary.get("status") == "planned":
        record["next_action"] = "unexpected plan-only response; use the direct source runner execute entrypoint."
    record["finished_at"] = now()
    # The runner summary names the selected package.  Put the final execution
    # record inside that package while retaining a root-level copy for failed
    # comparator/runner starts that never create a package.
    try:
        package_root = Path(str(runner_summary.get("package_path", "")))
        if package_root.is_dir() and (package_root / "evidence").is_dir():
            package_execution = package_root / "evidence" / "workflow-execution.json"
            package_execution.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            execution_path = package_execution
    except OSError:
        pass
    execution_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": verdict,
                "execution": str(execution_path),
                "package_path": record.get("package_path"),
                "validator_status": record.get("validator_status"),
                "validation_result_path": record.get("validation_result_path"),
            },
            ensure_ascii=False,
        )
    )
    if verdict == "passed":
        return 0
    return 2 if runner_record.get("returncode") == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
