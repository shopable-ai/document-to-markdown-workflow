#!/usr/bin/env python3
"""Queue multiple Workflow A sources after generic batch execution is promoted.

The generic source runner has not promoted batch execution yet.  This module
is deliberately fail-closed and contains no compatibility route to the
retired document-profile runner.  Run one source at a time through
``workflow_a_source_runner.py`` until a new batch contract is implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CONFIG_KEYS = {"api_key", "authorization", "cookie", "secret", "subscription_url", "token"}
SUPPORTED_SUFFIXES = {".pptx", ".pdf", ".png", ".jpg", ".jpeg"}
# These validator findings describe an explicit review boundary (usually OCR,
# visual uncertainty, or the package's honest needs-review status).  They do
# not make a page batch safe by themselves; smoke must still pass.  Every
# other validator failure is a hard stop because it indicates missing,
# malformed, or semantically empty package evidence.
REVIEW_ONLY_VALIDATOR_CODES = {"unknown-form-coverage", "failure-report", "status-consistency"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_yaml_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)


def _reject_secrets(value: Any, path: str = "config") -> None:
    """Reject secret-bearing config keys before a config snapshot is persisted."""
    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if key_lower in FORBIDDEN_CONFIG_KEYS or any(part in key_lower for part in FORBIDDEN_CONFIG_KEYS):
                raise ValueError(f"forbidden secret-bearing config key: {path}.{key}")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.is_file():
        raise ValueError(f"batch config does not exist: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"batch config is invalid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("batch config root must be a mapping")
    _reject_secrets(value)
    return value


def read_source_list(path: Path) -> list[Path]:
    if not path.is_file():
        raise ValueError(f"source list does not exist: {path}")
    result: list[Path] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A source list is intentionally one literal path per line.  Globs are
        # resolved by the caller so that expansion is explicit and auditable.
        candidate = Path(line).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise ValueError(f"source list line {line_number} is not a readable file: {line}")
        result.append(candidate)
    return result


def normalise_sources(sources: list[Path], source_list: Path | None = None) -> tuple[list[Path], list[dict[str, Any]]]:
    combined = [path.expanduser().resolve() for path in sources]
    if source_list is not None:
        combined.extend(read_source_list(source_list))
    if not combined:
        raise ValueError("provide at least one --source or --source-list")
    unique: list[Path] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    for path in combined:
        if not path.is_file():
            raise ValueError(f"source does not exist: {path}")
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"unsupported source format: {path.suffix or '<none>'} ({path})")
        key = (str(path), sha256(path))
        if key in seen:
            duplicates.append({"path": str(path), "sha256": key[1], "first_index": seen[key], "status": "deduplicated"})
            continue
        seen[key] = len(unique)
        unique.append(path)
    return unique, duplicates


def _positive_int(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise ValueError(f"{name} must be a positive integer")
    return result


def parse_page_selector(value: str, total: int) -> list[int]:
    """Parse a page selector locally so page-batch orchestration is testable."""
    if value.strip().lower() == "all":
        return list(range(1, total + 1))
    result: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if match:
            start, end = (int(part) for part in match.groups())
            if end < start:
                raise ValueError(f"invalid page range: {token}")
            result.update(range(start, end + 1))
        elif token.isdigit():
            result.add(int(token))
        else:
            raise ValueError(f"invalid page selector: {token}")
    pages = sorted(result)
    if not pages or pages[0] < 1 or pages[-1] > total:
        raise ValueError(f"pages must be within 1..{total}: {value}")
    return pages


def split_page_batches(pages: list[int], batch_size: int) -> list[list[int]]:
    if batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    return [pages[index:index + batch_size] for index in range(0, len(pages), batch_size)]


def page_selector(pages: list[int]) -> str:
    """Use a stable compact selector in child command evidence."""
    return ",".join(str(page) for page in pages)


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _package_hash(package: Path) -> str | None:
    document = package / "index.md"
    if not document.is_file():
        return None
    return sha256(document)


def _page_states(package: Path, pages: list[int]) -> list[dict[str, Any]]:
    classification = _read_json(package / "evidence/content-classification.json") or {}
    ocr = _read_json(package / "evidence/ocr-candidates.json") or {}
    visual = _read_json(package / "evidence/mac96-visual-review.json") or {}
    classified = {item.get("page"): item for item in classification.get("pages", []) if isinstance(item, dict)}
    ocr_by_page = {item.get("page"): item for item in ocr.get("candidates", []) if isinstance(item, dict)}
    visual_by_page = {item.get("page"): item for item in visual.get("reviews", []) if isinstance(item, dict)}
    relation_by_page = {item.get("page"): item for item in visual.get("image_reviews", []) if isinstance(item, dict)}
    result = []
    for page in pages:
        item = classified.get(page, {})
        evidence = item.get("text_evidence") or {}
        ocr_item = ocr_by_page.get(page) or {}
        visual_item = visual_by_page.get(page) or {}
        visual_status = visual_item.get("status") or "not-run"
        if relation_by_page.get(page):
            visual_status = f"text:{visual_status};relation:{relation_by_page[page].get('status', 'unknown')}"
        result.append({"page": page, "status": item.get("status", "needs-review"), "native": "present" if evidence.get("native_present") else "empty", "ocr": ocr_item.get("status") or evidence.get("ocr_status") or "not-needed", "visual": visual_status, "title_source": (item.get("page_title") or {}).get("source"), "candidate_count": ocr_item.get("candidate_count", evidence.get("ocr_candidate_count", 0))})
    return result


def validator_gate(validation: dict[str, Any] | None) -> tuple[bool, list[dict[str, Any]]]:
    """Return whether validator evidence is safe to continue past.

    ``needs-review`` is allowed only when its findings are the known OCR/
    visual review boundary.  Missing output, ``failed`` status, or any other
    finding stops the queue and leaves the failed package for recovery.
    """
    if not isinstance(validation, dict):
        return False, [{"code": "validator-missing", "message": "reports/validation-result.json is missing or unreadable"}]
    status = validation.get("status")
    failures = [item for item in validation.get("failures", []) if isinstance(item, dict)]
    if status == "passed":
        return (not failures), failures
    if status != "needs-review":
        return False, failures or [{"code": f"validator-{status or 'missing'}", "message": "validator did not return passed or needs-review"}]
    hard = [item for item in failures if item.get("code") not in REVIEW_ONLY_VALIDATOR_CODES]
    return (not hard), hard


def run_page_batches(source: Path, output_root: Path, pages: str = "all", smoke_pages: str | None = None, batch_size: int = 8, resume_record: Path | None = None, timeout: int = 1800, context_mode: str = "auto", context_window: int = 1) -> tuple[int, dict[str, Any]]:
    """Run one long document through smoke + bounded page batches.

    Each page batch gets a new package directory.  Checkpoints are append-only
    records of the package hash and page states; a resume never overwrites an
    earlier package or silently advances past a failed batch.
    """
    raise ValueError("batch execution is not promoted; run workflow_a_source_runner.py once per source")


def resolve_concurrency(cli_value: str | int | None, config: dict[str, Any], source_count: int) -> dict[str, Any]:
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    configured = execution.get("concurrency")
    requested = cli_value if cli_value is not None else configured if configured is not None else "auto"
    if isinstance(requested, str) and requested.strip().lower() in {"serial", "auto"}:
        policy = requested.strip().lower()
    else:
        policy = "fixed"
    if policy == "serial":
        value, source = 1, "explicit-serial"
    elif policy == "fixed":
        value, source = _positive_int(requested, "concurrency", 1), "explicit-or-configured"
    else:
        # Ollama's effective parallel capacity is not safely inferable from
        # hostname/model listing.  Honour only an explicit, positive capacity
        # hint; otherwise stay serial and record why.
        env_hint = os.environ.get("WORKFLOW_A_MAC96_CONCURRENCY")
        hint = env_hint or execution.get("mac96_capacity_hint")
        if hint is not None:
            value, source = _positive_int(hint, "mac96_capacity_hint", 1), "explicit-capacity-hint"
        else:
            value, source = 1, "conservative-default-no-verified-capacity"
    return {"requested": requested, "policy": policy, "workers": min(value, max(source_count, 1)), "capacity_source": source}


def _compact_source_id(path: Path) -> str:
    stem = "-".join(part for part in path.stem.lower().replace("_", "-").split() if part)
    stem = "".join(char if ("a" <= char <= "z" or "0" <= char <= "9" or char == "-") else "-" for char in stem).strip("-") or "source"
    return f"{stem}-{sha256(path)[:8]}"


@dataclass(frozen=True)
class Job:
    index: int
    source: Path
    source_sha256: str
    package_base: str


def build_jobs(sources: list[Path]) -> list[Job]:
    return [Job(index=index, source=source, source_sha256=sha256(source), package_base=f"{_compact_source_id(source)}-{source.suffix.lower().lstrip('.') or 'file'}") for index, source in enumerate(sources)]


def _status_from_summary(summary: dict[str, Any] | None, returncode: int | None) -> str:
    if summary and summary.get("status") in {"passed", "needs-review", "failed"}:
        return str(summary["status"])
    return "failed" if returncode not in (0, None) else "needs-review"


def _run_command(command: list[str], timeout: int) -> dict[str, Any]:
    record = {"command": shlex.join(command), "started_at": now(), "status": "running", "returncode": None, "stdout": "", "stderr": ""}
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        record.update({"returncode": proc.returncode, "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-12000:], "status": "complete" if proc.returncode == 0 else "failed", "finished_at": now()})
    except subprocess.TimeoutExpired as exc:
        record.update({"status": "failed", "error": "timeout", "stdout": str(exc.stdout or "")[-12000:], "stderr": str(exc.stderr or "")[-12000:], "finished_at": now()})
    except Exception as exc:  # pragma: no cover - defensive runtime boundary
        record.update({"status": "failed", "error": repr(exc), "finished_at": now()})
    return record


def _summary_from_stdout(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "status" in value:
            return value
    return None


def _package_for_attempt(packages_root: Path, job: Job, attempt: int) -> tuple[str, Path]:
    package_name = job.package_base if attempt == 1 else f"{job.package_base}--attempt-{attempt:02d}"
    return package_name, packages_root / package_name


def execute_job(job: Job, packages_root: Path, pages: str, context_mode: str, context_window: int, timeout: int, max_attempts: int, retry_backoff: float, previous: dict[str, Any] | None) -> dict[str, Any]:
    raise ValueError("batch execution is not promoted; run workflow_a_source_runner.py once per source")
    attempts = int((previous or {}).get("attempts", 0))
    while attempts < max_attempts:
        attempts += 1
        package_name, package_root = _package_for_attempt(packages_root, job, attempts)
        command = [sys.executable, str(ROOT / "adapters/workflow_a_source_runner.py"), "--source", str(job.source), "--output-root", str(packages_root), "--pages", pages, "--execute"]
        command_record = _run_command(command, timeout)
        summary = _summary_from_stdout(command_record.get("stdout", ""))
        status = _status_from_summary(summary, command_record.get("returncode"))
        acceptance = {"status": "passed", "package": str(package_root)}
        if status == "passed" and not _successful_existing(acceptance, packages_root):
            status = "failed"
            command_record["error"] = "runner reported passed but the independent package validator evidence is missing"
        item = {
            "index": job.index,
            "source": str(job.source),
            "sha256": job.source_sha256,
            "package": str(package_root) if package_root.is_dir() else None,
            "package_name": package_name,
            "attempts": attempts,
            "status": status,
            "runner_summary": summary,
            "command": command_record,
            "started_at": command_record.get("started_at"),
            "finished_at": command_record.get("finished_at"),
        }
        if status in {"passed", "needs-review"} or attempts >= max_attempts:
            return item
        if retry_backoff > 0:
            time.sleep(retry_backoff * (2 ** (attempts - 1)))
    return {"index": job.index, "source": str(job.source), "sha256": job.source_sha256, "status": "failed", "attempts": attempts}


def _load_existing_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    return value if isinstance(value, dict) else None


def _successful_existing(item: dict[str, Any], packages_root: Path) -> bool:
    if item.get("status") != "passed" or not item.get("package"):
        return False
    package = Path(str(item["package"]))
    if not package.is_absolute():
        package = packages_root / package
    report = package / "reports/validation-result.json"
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return value.get("status") == "passed"


def run_batch(sources: list[Path], output_root: Path, pages: str = "all", config: dict[str, Any] | None = None, concurrency: str | int | None = None, resume_record: Path | None = None, retry_needs_review: bool = False) -> tuple[int, dict[str, Any]]:
    raise ValueError("batch execution is not promoted; run workflow_a_source_runner.py once per source")
    config = config or {}
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    context = config.get("context") if isinstance(config.get("context"), dict) else {}
    retry = config.get("retry") if isinstance(config.get("retry"), dict) else {}
    timeout = _positive_int(execution.get("per_source_timeout_seconds"), "per_source_timeout_seconds", 1800)
    max_attempts = _positive_int(retry.get("max_attempts"), "max_attempts", 1)
    retry_backoff = float(retry.get("backoff_seconds", 0) or 0)
    if retry_backoff < 0:
        raise ValueError("backoff_seconds must be >= 0")
    context_mode = str(context.get("mode", "auto")).lower()
    if context_mode not in {"auto", "independent", "neighbor"}:
        raise ValueError("context.mode must be auto, independent, or neighbor")
    context_window = _positive_int(context.get("window_pages"), "window_pages", 1)
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    jobs = build_jobs(sources)
    capacity = resolve_concurrency(concurrency, config, len(jobs))
    prior = _load_existing_record(resume_record) if resume_record else None
    if resume_record and not prior:
        raise ValueError(f"resume record is missing or invalid: {resume_record}")
    if prior:
        batch_root = resume_record.parent.resolve()
        packages_root = Path(str(prior.get("packages_root", batch_root / "packages"))).expanduser().resolve()
        batch_id = str(prior.get("run_id") or batch_root.name)
        if not packages_root.is_dir():
            raise ValueError(f"resume packages root does not exist: {packages_root}")
        prior_digests = [str(item.get("sha256")) for item in prior.get("input_digests", []) if item.get("sha256")]
        if prior_digests and prior_digests != [job.source_sha256 for job in jobs]:
            raise ValueError("resume sources do not match the original batch input digests")
        state = prior
        state["status"] = "running"
        state["resumed_at"] = now()
        state["config_snapshot"] = {"pages": pages, "concurrency": capacity, "context": {"mode": context_mode, "window_pages": context_window}, "retry": {"max_attempts": max_attempts, "backoff_seconds": retry_backoff}}
        state["queue"] = [{"index": job.index, "source": str(job.source), "sha256": job.source_sha256, "status": "planned", "attempts": 0, "package": None} for job in jobs]
    else:
        batch_id = f"batch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{hashlib.sha256((''.join(job.source_sha256 for job in jobs) + pages).encode()).hexdigest()[:8]}"
        batch_root = output_root / batch_id
        packages_root = batch_root / "packages"
        batch_root.mkdir(parents=False, exist_ok=False)
        packages_root.mkdir()
        state = {
            "run_id": batch_id,
            "workflow_id": "document-to-markdown-assets",
            "workflow_version": "0.3.0",
            "status": "running",
            "started_at": now(),
            "input_digests": [{"index": job.index, "path": str(job.source), "sha256": job.source_sha256} for job in jobs],
            "config_snapshot": {"pages": pages, "concurrency": capacity, "context": {"mode": context_mode, "window_pages": context_window}, "retry": {"max_attempts": max_attempts, "backoff_seconds": retry_backoff}},
            "evidence_level": "L2-local-runtime",
            "packages_root": str(packages_root),
            "queue": [{"index": job.index, "source": str(job.source), "sha256": job.source_sha256, "status": "planned", "attempts": 0, "package": None} for job in jobs],
            "duplicates": [],
            "failure": None,
            "stage_evidence": [],
        }
    record_path = batch_root / "run-record.yaml"
    write_yaml_atomic(record_path, state)
    lock = threading.Lock()

    def persist() -> None:
        with lock:
            write_yaml_atomic(record_path, state)

    previous_by_sha = {str(item.get("sha256")): item for item in (prior or {}).get("queue", []) if item.get("sha256")}

    runnable: list[Job] = []
    for job in jobs:
        previous = previous_by_sha.get(job.source_sha256)
        if previous and _successful_existing(previous, packages_root):
            state["queue"][job.index].update(previous)
            state["queue"][job.index]["status"] = "passed"
        elif previous and previous.get("status") == "needs-review" and not retry_needs_review:
            state["queue"][job.index].update(previous)
            state["queue"][job.index]["status"] = "needs-review"
        else:
            runnable.append(job)
    persist()

    def submit(job: Job) -> dict[str, Any]:
        with lock:
            state["queue"][job.index]["status"] = "running"
            write_yaml_atomic(record_path, state)
        result = execute_job(job, packages_root, pages, context_mode, context_window, timeout, max_attempts, retry_backoff, previous_by_sha.get(job.source_sha256))
        with lock:
            state["queue"][job.index].update(result)
            write_yaml_atomic(record_path, state)
        return result

    if capacity["workers"] == 1:
        for job in runnable:
            submit(job)
    else:
        with ThreadPoolExecutor(max_workers=capacity["workers"], thread_name_prefix="workflow-a") as pool:
            futures: list[Future[dict[str, Any]]] = [pool.submit(submit, job) for job in runnable]
            for future in as_completed(futures):
                future.result()

    statuses = [item.get("status") for item in state["queue"]]
    state["status"] = "succeeded" if statuses and all(status == "passed" for status in statuses) else "needs-review" if any(status == "needs-review" for status in statuses) else "failed"
    state["finished_at"] = now()
    state["summary"] = {"total": len(statuses), "passed": statuses.count("passed"), "needs_review": statuses.count("needs-review"), "failed": statuses.count("failed"), "skipped": statuses.count("skipped"), "queue_order": "source-list-order", "single_source_packages": True}
    state["outputs"] = [item.get("package") for item in state["queue"] if item.get("package")]
    queue_evidence_status = "complete" if state["status"] == "succeeded" else "needs-review" if state["status"] == "needs-review" else "failed"
    verification_commands = [item["command"]["command"] for item in state["queue"] if isinstance(item.get("command"), dict) and item["command"].get("command")]
    if not verification_commands:
        queue_evidence_status = "needs-review"
    state["stage_evidence"] = [{"stage_id": "BATCH-QUEUE", "status": queue_evidence_status, "evidence_level": "L2-local-runtime", "evidence_paths": ["run-record.yaml"], "verification_commands": verification_commands or ["batch queue has no child command evidence"], "diagnostics": ["每个 source 仍由单文件 Workflow A runner 独立验收"], "unknown": [], "design_score": None, "production_evidence_score": None, "score_basis": "批次编排证据；单个 source 的 A1-A7 仍在其 package 内记录"}]
    write_yaml_atomic(record_path, state)
    return (0 if state["status"] == "succeeded" else 1), state


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue multiple Workflow A sources; one independently validated package per source")
    parser.add_argument("--source", action="append", type=Path, default=[], help="repeatable source file")
    parser.add_argument("--source-list", type=Path, help="UTF-8 text file, one literal source path per line")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pages", default="all", help="all, a comma list, or ranges such as 1-3,7; applied to each source")
    parser.add_argument("--batch-size", type=int, help="page-batch mode for one long PDF (default 8)")
    parser.add_argument("--smoke-pages", help="explicit smoke page selector for page-batch mode")
    parser.add_argument("--resume-pages", type=Path, help="resume a page-batch checkpoint.json without creating a new run")
    parser.add_argument("--config", type=Path, help="optional YAML batch config")
    parser.add_argument("--concurrency", help="serial, auto, or a positive integer; default auto is conservatively serial")
    parser.add_argument("--context-mode", choices=("auto", "independent", "neighbor"), help="page context policy for each child runner")
    parser.add_argument("--context-window", type=int, help="neighbor page distance for child runners")
    parser.add_argument("--resume", action="store_true", help="resume the newest batch run under output-root")
    parser.add_argument("--resume-run", type=Path, help="resume this exact batch run-record.yaml")
    parser.add_argument("--retry-needs-review", action="store_true", help="retry prior needs-review items when resuming")
    args = parser.parse_args()
    try:
        sources, duplicates = normalise_sources(args.source, args.source_list)
        parser.error("batch execution is not promoted; run workflow_a_source_runner.py once per source")
        config = load_config(args.config.expanduser().resolve() if args.config else None)
        if args.context_mode is not None or args.context_window is not None:
            context_config = config.setdefault("context", {})
            if args.context_mode is not None:
                context_config["mode"] = args.context_mode
            if args.context_window is not None:
                context_config["window_pages"] = args.context_window
        resume_record = args.resume_run.expanduser().resolve() if args.resume_run else None
        if args.resume and resume_record:
            parser.error("use either --resume or --resume-run, not both")
        if args.resume:
            candidates = sorted(args.output_root.expanduser().resolve().glob("batch-*/run-record.yaml"), key=lambda item: item.stat().st_mtime, reverse=True)
            resume_record = candidates[0] if candidates else None
            if resume_record is None:
                parser.error("--resume found no batch run-record.yaml under output-root")
        if args.batch_size is not None or args.resume_pages:
            if len(sources) != 1:
                parser.error("page-batch mode accepts exactly one PDF source")
            if args.resume and args.resume_pages:
                parser.error("use either --resume or --resume-pages")
            page_record = args.resume_pages.expanduser().resolve() if args.resume_pages else None
            batch_size = args.batch_size or _positive_int((config.get("execution") or {}).get("batch_size"), "batch_size", 8)
            code, state = run_page_batches(sources[0], args.output_root, args.pages, args.smoke_pages, batch_size, page_record, _positive_int((config.get("execution") or {}).get("per_source_timeout_seconds"), "per_source_timeout_seconds", 1800), str((config.get("context") or {}).get("mode", "auto")), _positive_int((config.get("context") or {}).get("window_pages"), "window_pages", 1))
            print(json.dumps({"status": state.get("status"), "run_id": state.get("run_id"), "output": str(Path(str(state.get("checkpoint", args.output_root)))), "summary": state.get("summary", {}), "checkpoint": str((Path(str(state.get("checkpoint"))) if state.get("checkpoint") else args.output_root))}, ensure_ascii=False))
            return code
        code, state = run_batch(sources, args.output_root, args.pages, config, args.concurrency, resume_record, args.retry_needs_review)
        state["duplicates"] = duplicates
        record_path = Path(str(state["packages_root"])).parent / "run-record.yaml"
        write_yaml_atomic(record_path, state)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": state["status"], "run_id": state["run_id"], "output": str(Path(str(state["packages_root"])).parent), "packages": state.get("outputs", []), "summary": state.get("summary", {}), "concurrency": state["config_snapshot"]["concurrency"]}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
