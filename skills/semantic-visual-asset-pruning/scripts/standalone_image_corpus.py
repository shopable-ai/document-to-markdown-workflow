#!/usr/bin/env python3
"""Stream-safe standalone image corpus auditing and deletion."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import gc
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Callable

import fcntl
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[3]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MANIFEST_NAME = "input-manifest.jsonl"
CLASSIFICATION_NAME = "classification.jsonl"
LEDGER_NAME = "deletion-ledger.jsonl"
SUMMARY_NAME = "summary.json"
PROGRESS_NAME = "progress.json"
INDEX_NAME = "runtime-index.sqlite3"
LOG_NAME = "execution.log"
REPORT_NAME = "最终验收报告.md"
TXT_LISTS = {
    "retained": "retained-paths.txt",
    "crop-required": "crop-required-paths.txt",
    "needs-review": "needs-review-paths.txt",
    "deleted": "deleted-paths.txt",
    "skipped": "skipped-paths.txt",
}
FINAL_STATES = {
    "retained",
    "crop-required",
    "needs-review",
    "ocr-failed",
    "sha-drift",
    "deleted",
    "deleted-recovered",
    "delete-failed",
}
DELETE_READY_STATE = "candidate-remove-pending-delete"


Classifier = Callable[[dict[str, Any], "CorpusConfig"], dict[str, Any]]


@dataclasses.dataclass(slots=True)
class CorpusConfig:
    corpus_root: Path
    evidence_dir: Path
    input_manifest: Path | None = None
    prior_classification: Path | None = None
    resume: bool = False
    max_workers: int = 1
    memory_limit_mb: int = 0
    classifier: Classifier | None = None
    ocr_engine: str = "auto"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def write_lines_atomic(path: Path, lines: Iterator[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{utc_now()} {message}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def ensure_durable_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def iter_jsonl(path: Path) -> Any:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            yield value


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_AUDIT_MODULE: Any | None = None


def audit_module() -> Any:
    global _AUDIT_MODULE
    if _AUDIT_MODULE is None:
        _AUDIT_MODULE = load_module(
            ROOT / "skills/region-cropping/scripts/audit_text_heavy_reader_assets.py",
            "standalone_text_asset_audit",
        )
    return _AUDIT_MODULE


def normalize_path(path: Path) -> str:
    return path.resolve().as_posix()


def relative_to(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def validate_workers(value: int) -> int:
    if value < 1 or value > 2:
        raise ValueError("max_workers must be 1 or 2")
    return value


def open_and_lock_manifest(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"manifest is already locked: {path}") from exc
    handle.seek(0)
    return handle


def discover_images(corpus_root: Path, evidence_dir: Path) -> Iterator[Path]:
    """Yield images deterministically without materializing the corpus in memory."""
    evidence_root = evidence_dir.resolve()
    for directory, dirnames, filenames in os.walk(corpus_root):
        dirnames.sort()
        filenames.sort()
        directory_path = Path(directory).resolve()
        if directory_path == evidence_root or evidence_root in directory_path.parents:
            dirnames[:] = []
            continue
        for filename in filenames:
            path = directory_path / filename
            if path.suffix.lower() in IMAGE_SUFFIXES:
                yield path


def build_prior_index(path: Path | None, index_path: Path) -> sqlite3.Connection | None:
    if path is None or not path.is_file():
        return None
    connection = sqlite3.connect(index_path)
    connection.execute("DROP TABLE IF EXISTS prior_records")
    connection.execute(
        "CREATE TABLE prior_records (lookup_key TEXT PRIMARY KEY, decision TEXT NOT NULL, reasons_json TEXT NOT NULL)"
    )
    with connection:
        for record in iter_jsonl(path):
            decision = record.get("decision")
            if not isinstance(decision, str):
                continue
            reason_codes = record.get("reason_codes")
            reasons = (
                [item for item in reason_codes if isinstance(item, str)]
                if isinstance(reason_codes, list)
                else [str(record["reason"])] if record.get("reason") else []
            )
            for key in (record.get("path"), record.get("absolute_path"), record.get("file_path"), record.get("relative_path")):
                if isinstance(key, str) and key:
                    connection.execute(
                        "INSERT OR REPLACE INTO prior_records VALUES (?, ?, ?)",
                        (key, decision, json.dumps(reasons, ensure_ascii=False)),
                    )
    return connection


def ensure_manifest(config: CorpusConfig, manifest_handle: Any) -> Path:
    manifest_path = (config.input_manifest or (config.evidence_dir / MANIFEST_NAME)).resolve()
    manifest_handle.seek(0)
    existing = manifest_handle.read(1)
    if existing:
        if not config.resume and config.input_manifest is None:
            raise RuntimeError("existing default input manifest requires --resume")
        return manifest_path
    if config.resume:
        raise RuntimeError(f"--resume requires an existing manifest: {manifest_path}")
    prior_index = build_prior_index(
        config.prior_classification,
        config.evidence_dir / "prior-classification-index.sqlite3",
    )
    try:
        manifest_handle.seek(0)
        for sequence, image_path in enumerate(discover_images(config.corpus_root, config.evidence_dir), 1):
            stat = image_path.stat()
            snapshot_error = ""
            try:
                with Image.open(image_path) as image:
                    width, height = image.size
            except OSError as exc:
                width, height = 0, 0
                snapshot_error = str(exc)
            absolute = normalize_path(image_path)
            relative = relative_to(config.corpus_root, image_path)
            entry = {
                "sequence": sequence,
                "path": absolute,
                "relative_path": relative,
                "sha256": sha256(image_path),
                "bytes": stat.st_size,
                "width": width,
                "height": height,
                "snapshot_mtime_ns": stat.st_mtime_ns,
            }
            if snapshot_error:
                entry["snapshot_error"] = snapshot_error
            if prior_index is not None:
                prior = prior_index.execute(
                    "SELECT decision, reasons_json FROM prior_records WHERE lookup_key IN (?, ?) LIMIT 1",
                    (absolute, relative),
                ).fetchone()
                if prior is not None:
                    entry["prior_decision"] = prior[0]
                    entry["prior_reason_codes"] = json.loads(prior[1])
                    entry["prior_source"] = str(config.prior_classification)
            manifest_handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            manifest_handle.flush()
            os.fsync(manifest_handle.fileno())
    finally:
        if prior_index is not None:
            prior_index.close()
    manifest_handle.seek(0)
    return manifest_path


def validate_manifest_scope(manifest_path: Path, corpus_root: Path) -> None:
    previous_sequence = 0
    for record in iter_jsonl(manifest_path):
        path = Path(str(record.get("path", ""))).resolve()
        try:
            expected_relative = path.relative_to(corpus_root).as_posix()
        except ValueError as exc:
            raise RuntimeError(f"input manifest path escapes corpus_root: {path}") from exc
        if expected_relative != str(record.get("relative_path", "")):
            raise RuntimeError(f"input manifest relative_path mismatch: {path}")
        sequence = int(record.get("sequence", 0))
        if sequence <= previous_sequence:
            raise RuntimeError("input manifest sequence must be unique and strictly increasing")
        previous_sequence = sequence


def verify_or_lock_manifest(manifest_path: Path, evidence_dir: Path, corpus_root: Path) -> None:
    lock_path = evidence_dir / "input-manifest.lock.json"
    digest = sha256(manifest_path)
    count = sum(1 for _record in iter_jsonl(manifest_path))
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if (
            lock.get("sha256") != digest
            or int(lock.get("count", -1)) != count
            or Path(str(lock.get("corpus_root", ""))).resolve() != corpus_root
        ):
            raise RuntimeError("locked input manifest changed after snapshot")
        return
    write_json_atomic(
        lock_path,
        {
            "schema": "standalone-image-corpus-input-lock",
            "version": "1.0.0",
            "manifest": str(manifest_path),
            "corpus_root": str(corpus_root),
            "sha256": digest,
            "count": count,
            "locked_at": utc_now(),
        },
    )


def init_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS manifest_entries (
            relative_path TEXT PRIMARY KEY,
            input_sequence INTEGER NOT NULL UNIQUE,
            absolute_path TEXT NOT NULL UNIQUE,
            manifest_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            prior_decision TEXT,
            prior_reason_codes_json TEXT
        );
        CREATE TABLE IF NOT EXISTS item_state (
            relative_path TEXT PRIMARY KEY,
            absolute_path TEXT NOT NULL UNIQUE,
            manifest_sha256 TEXT NOT NULL,
            classification_status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 0,
            decision TEXT,
            reason_codes_json TEXT,
            semantic_text_coverage TEXT,
            visual_increment TEXT,
            deletion_eligible INTEGER NOT NULL DEFAULT 0,
            deterministic_gates_json TEXT NOT NULL DEFAULT '{}',
            skill_gates_json TEXT NOT NULL DEFAULT '{}',
            rationale TEXT,
            classifier_meta_json TEXT,
            deletion_state TEXT NOT NULL DEFAULT 'none',
            final_state TEXT NOT NULL DEFAULT 'pending',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS new_after_snapshot (
            relative_path TEXT PRIMARY KEY
        );
        """
    )
    connection.commit()
    return connection


def sync_manifest_into_db(connection: sqlite3.Connection, manifest_path: Path) -> None:
    with connection:
        for record in iter_jsonl(manifest_path):
            path = Path(str(record["path"])).resolve()
            row = (
                str(record["relative_path"]),
                int(record["sequence"]),
                normalize_path(path),
                str(record["sha256"]),
                int(record.get("bytes", record.get("size_bytes", path.stat().st_size if path.exists() else 0))),
                record.get("prior_decision"),
                json.dumps(record.get("prior_reason_codes") or [], ensure_ascii=False),
            )
            connection.execute(
                """
                INSERT INTO manifest_entries (
                    relative_path,
                    input_sequence,
                    absolute_path,
                    manifest_sha256,
                    size_bytes,
                    prior_decision,
                    prior_reason_codes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    absolute_path = excluded.absolute_path,
                    manifest_sha256 = excluded.manifest_sha256,
                    size_bytes = excluded.size_bytes,
                    prior_decision = excluded.prior_decision,
                    prior_reason_codes_json = excluded.prior_reason_codes_json
                """,
                row,
            )
            connection.execute(
                """
                INSERT INTO item_state (
                    relative_path,
                    absolute_path,
                    manifest_sha256,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    absolute_path = excluded.absolute_path,
                    manifest_sha256 = excluded.manifest_sha256
                """,
                (row[0], row[2], row[3], utc_now()),
            )


def apply_classification_row(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    relative_path = str(row["relative_path"])
    decision = str(row.get("decision", "needs-review"))
    status = str(row.get("status", "needs-review"))
    reason_codes = row.get("reason_codes") or []
    manifest = connection.execute(
        "SELECT manifest_sha256 FROM item_state WHERE relative_path = ?",
        (relative_path,),
    ).fetchone()
    input_sha = str(row.get("input_sha256") or row.get("sha256") or "")
    sha_matches = manifest is not None and input_sha == str(manifest[0])
    deterministic_gates = dict(row.get("deterministic_deletion_gates") or {})
    skill_gates = dict(row.get("skill_deletion_gates") or {})
    deletion_eligible = bool(
        row.get("deletion_eligible") is True
        and sha_matches
        and deterministic_gates
        and skill_gates
        and all(deterministic_gates.values())
        and all(skill_gates.values())
        and float(row.get("decision_confidence_score", 0)) >= 95
        and row.get("semantic_text_coverage") == "complete"
        and row.get("visual_increment") == "none"
    )
    if decision == "candidate-remove" and deletion_eligible:
        final_state = DELETE_READY_STATE
    elif "classifier-exception" in reason_codes:
        final_state = "pending"
    elif status == "skipped-changed":
        final_state = "sha-drift"
    elif status == "ocr-failed":
        final_state = "ocr-failed"
    else:
        final_state = decision if decision in {"retained", "crop-required"} else decision
        if final_state == "retain":
            final_state = "retained"
        if final_state not in FINAL_STATES:
            final_state = "needs-review"
    with connection:
        connection.execute(
            """
            UPDATE item_state
            SET classification_status = ?,
                attempt = ?,
                decision = ?,
                reason_codes_json = ?,
                semantic_text_coverage = ?,
                visual_increment = ?,
                deletion_eligible = ?,
                deterministic_gates_json = ?,
                skill_gates_json = ?,
                rationale = ?,
                classifier_meta_json = ?,
                final_state = CASE
                    WHEN deletion_state IN ('deleted', 'deleted-recovered') THEN final_state
                    ELSE ?
                END,
                updated_at = ?
            WHERE relative_path = ?
            """,
            (
                status,
                int(row.get("attempt", 1)),
                decision,
                json.dumps(reason_codes, ensure_ascii=False),
                row.get("semantic_text_coverage"),
                row.get("visual_increment"),
                int(deletion_eligible),
                json.dumps(deterministic_gates, ensure_ascii=False),
                json.dumps(skill_gates, ensure_ascii=False),
                row.get("rationale"),
                json.dumps(row.get("resource_usage") or {}, ensure_ascii=False),
                final_state,
                utc_now(),
                relative_path,
            ),
        )


def apply_ledger_row(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    relative_path = str(row["relative_path"])
    state = str(row.get("state", "delete-failed"))
    final_state = (
        state
        if state in {"deleted", "deleted-recovered"}
        else "sha-drift"
        if state == "skipped-changed"
        else "needs-review"
        if state == "missing"
        else "delete-failed"
    )
    with connection:
        connection.execute(
            """
            UPDATE item_state
            SET deletion_state = ?,
                final_state = ?,
                updated_at = ?
            WHERE relative_path = ?
            """,
            (state, final_state, utc_now(), relative_path),
        )


def rebuild_db_from_logs(
    connection: sqlite3.Connection,
    classification_path: Path,
    ledger_path: Path,
) -> None:
    for row in iter_jsonl(classification_path) or []:
        apply_classification_row(connection, row)
    for row in iter_jsonl(ledger_path) or []:
        if row.get("event") == "delete-intent":
            with connection:
                connection.execute(
                    """
                    UPDATE item_state
                    SET deletion_state = 'intent',
                        updated_at = ?
                    WHERE relative_path = ?
                    """,
                    (utc_now(), str(row["relative_path"])),
                )
            continue
        apply_ledger_row(connection, row)


def current_children(root_pid: int) -> tuple[int, int, int, int]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,command="],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return 0, 0, 0, 0
    if result.returncode != 0:
        return 0, 0, 0, 0
    nodes: dict[int, tuple[int, int, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid, ppid, rss = (int(part) for part in parts[:3])
        except ValueError:
            continue
        nodes[pid] = (ppid, rss, parts[3] if len(parts) == 4 else "")
    stack = [root_pid]
    seen: set[int] = set()
    total_rss = 0
    child_rss = 0
    child_count = 0
    child_peak = 0
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        payload = nodes.get(pid)
        if payload is None:
            continue
        total_rss += payload[1]
        command = payload[2].lower()
        if pid != root_pid and any(name in command for name in ("vision_ocr", "tesseract", "apple-vision-ocr")):
            child_rss += payload[1]
            child_count += 1
            child_peak = max(child_peak, payload[1])
        for candidate, (ppid, _rss, _command) in nodes.items():
            if ppid == pid and candidate not in seen:
                stack.append(candidate)
    return total_rss, child_rss, child_count, child_peak


class ProcessTreeMonitor:
    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self.tree_rss_peak_kib = 0
        self.child_rss_peak_kib = 0
        self.child_count_peak = 0
        self.single_child_rss_peak_kib = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            tree_rss, child_rss, child_count, child_peak = current_children(os.getpid())
            self.tree_rss_peak_kib = max(self.tree_rss_peak_kib, tree_rss)
            self.child_rss_peak_kib = max(self.child_rss_peak_kib, child_rss)
            self.child_count_peak = max(self.child_count_peak, child_count)
            self.single_child_rss_peak_kib = max(self.single_child_rss_peak_kib, child_peak)
            self._stop.wait(self.interval_seconds)

    def __enter__(self) -> "ProcessTreeMonitor":
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        self.thread.join(timeout=1.0)
        tree_rss, child_rss, child_count, child_peak = current_children(os.getpid())
        self.tree_rss_peak_kib = max(self.tree_rss_peak_kib, tree_rss)
        self.child_rss_peak_kib = max(self.child_rss_peak_kib, child_rss)
        self.child_count_peak = max(self.child_count_peak, child_count)
        self.single_child_rss_peak_kib = max(self.single_child_rss_peak_kib, child_peak)


def with_monitor(classifier: Classifier, entry: dict[str, Any], config: CorpusConfig) -> dict[str, Any]:
    with ProcessTreeMonitor() as monitor:
        result = classifier(entry, config)
    if not isinstance(result, dict):
        raise ValueError("classifier must return an object")
    resource_usage = dict(result.get("resource_usage") or {})
    resource_usage.setdefault("tree_rss_peak_kib", monitor.tree_rss_peak_kib)
    resource_usage.setdefault("ocr_child_rss_peak_kib", monitor.child_rss_peak_kib)
    resource_usage.setdefault("ocr_child_count_peak", monitor.child_count_peak)
    resource_usage.setdefault("single_ocr_child_rss_peak_kib", monitor.single_child_rss_peak_kib)
    result["resource_usage"] = resource_usage
    return result


def normalize_groups(words: list[Any]) -> list[dict[str, int]]:
    groups: list[dict[str, int]] = []
    tolerance = 48
    for word in sorted(words, key=lambda item: (item.top, item.left)):
        center_y = word.top + word.height // 2
        target = None
        for group in groups:
            if abs(group["center_y"] - center_y) <= tolerance:
                target = group
                break
        if target is None:
            target = {
                "top": word.top,
                "bottom": word.top + word.height,
                "left": word.left,
                "right": word.left + word.width,
                "center_y": center_y,
                "word_count": 0,
            }
            groups.append(target)
        target["top"] = min(target["top"], word.top)
        target["bottom"] = max(target["bottom"], word.top + word.height)
        target["left"] = min(target["left"], word.left)
        target["right"] = max(target["right"], word.left + word.width)
        target["center_y"] = round((target["top"] + target["bottom"]) / 2)
        target["word_count"] += 1
    return groups


def detect_internal_structure(non_text_mask: np.ndarray, image_width: int, image_height: int) -> dict[str, float]:
    if non_text_mask.size == 0:
        return {"internal_dark_ratio": 0.0, "horizontal_hits": 0.0, "vertical_hits": 0.0}
    top = round(non_text_mask.shape[0] * 0.08)
    bottom = round(non_text_mask.shape[0] * 0.92)
    left = round(non_text_mask.shape[1] * 0.08)
    right = round(non_text_mask.shape[1] * 0.92)
    if bottom <= top or right <= left:
        core = non_text_mask
    else:
        core = non_text_mask[top:bottom, left:right]
    row_ratio = core.sum(axis=1) / max(1, core.shape[1])
    col_ratio = core.sum(axis=0) / max(1, core.shape[0])
    return {
        "internal_dark_ratio": float(core.sum()) / max(1, core.size),
        "horizontal_hits": float((row_ratio >= 0.18).sum()),
        "vertical_hits": float((col_ratio >= 0.18).sum()),
    }


def count_spatial_text_groups(groups: list[dict[str, int]], image_width: int) -> int:
    if not groups:
        return 0
    columns: list[int] = []
    for group in groups:
        center_x = round((group["left"] + group["right"]) / 2)
        bucket = round(center_x / max(1, image_width / 3))
        if bucket not in columns:
            columns.append(bucket)
    return len(columns)


def standalone_decision_from_features(features: dict[str, Any]) -> tuple[str, list[str], str]:
    if not features["ocr_succeeded"]:
        return "needs-review", ["ocr-failed"], "OCR 未返回稳定结果，保守进入 needs-review。"
    if features["average_confidence"] < 45:
        return "needs-review", ["ocr-failed"], "OCR 置信度不足，不能安全删除。"
    if features["word_count"] == 0:
        return "retain", ["no-text-detected"], "未检出稳定文字，按图片保留。"
    if features.get("photo_texture_ratio", 0.0) >= 0.08 or features.get("photo_score", 0.0) >= 0.8:
        return "retain", ["photo"], "非文字纹理达到照片门，必须保留。"
    if features.get("screenshot_ui_score", 0.0) >= 0.8:
        return "retain", ["screenshot"], "检测到截图界面结构，必须保留。"
    if features.get("line_intersections", 0) >= 2 and features["internal_horizontal_hits"] >= 2 and features["internal_vertical_hits"] >= 2:
        return "retain", ["table"], "内部网格线命中，按表格或截图保留。"
    if features.get("connected_line_count", 0) >= 2 or features.get("directional_edge_count", 0) >= 1:
        return "retain", ["diagram"], "内部连接或交叉结构命中，按关系图保留。"
    if features.get("semantic_container_count", 0) >= 2 or (
        features["spatial_columns"] >= 3 and features.get("color_coding_ratio", 0.0) >= 0.01
    ):
        return "retain", ["mixed-text-visual"], "文字组在二维空间中分布明显，按空间化文字图保留。"
    if features["internal_dark_ratio"] >= 0.05 and features["group_count"] <= 3:
        return "crop-required", ["mixed-text-visual"], "图片主要是正文，但仍有局部视觉增量。"
    sparse_typography = (
        features.get("group_count", 0) <= 3
        and features.get("explained_foreground_ratio", 1.0) >= 0.80
        and features.get("residual_edge_ratio", features["internal_dark_ratio"]) <= 0.003
        and features.get("residual_texture_ratio", 0.0) <= 0.002
        and features.get("residual_color_ratio", 0.0) <= 0.002
        and features.get("max_residual_component_ratio", 0.0) <= 0.003
        and features.get("line_intersections", 0) == 0
        and features.get("connected_line_count", 0) == 0
    )
    if sparse_typography or (
        features["group_count"] <= 3 and features["internal_dark_ratio"] <= 0.012
    ):
        return "candidate-remove", ["text-covered", "ordinary-typography"], "OCR 解释普通正文或标题，且无内部结构增量。"
    if features["internal_dark_ratio"] >= 0.02:
        return "retain", ["mixed-text-visual"], "存在不可忽略的内部非文字结构。"
    return "needs-review", ["needs-review"], "信号介于纯文字与结构图之间，保守进入 needs-review。"


def deletion_gate(result: dict[str, Any]) -> tuple[dict[str, bool], bool]:
    deterministic = dict(result.get("deterministic_deletion_gates") or {})
    required_deterministic = {
        "ocr_reliable": result.get("ocr_summary", {}).get("succeeded") is True,
        "semantic_text_coverage_complete": result.get("semantic_text_coverage") == "complete",
        "visual_increment_none": result.get("visual_increment") == "none",
        "no_semantic_structure": result.get("visual_structure_metrics", {}).get("semantic_structure_detected") is False,
    }
    for key, default in required_deterministic.items():
        deterministic.setdefault(key, default)
    skill = dict(result.get("skill_deletion_gates") or {})
    skill.setdefault("candidate_remove", result.get("decision") == "candidate-remove")
    skill.setdefault("confidence_at_least_95", float(result.get("decision_confidence_score", 0)) >= 95)
    skill.setdefault("sha_locked", True)
    result["deterministic_deletion_gates"] = deterministic
    result["skill_deletion_gates"] = skill
    eligible = (
        result.get("decision") == "candidate-remove"
        and all(deterministic.values())
        and all(skill.values())
    )
    return {**deterministic, **skill}, eligible


def default_classifier(entry: dict[str, Any], config: CorpusConfig) -> dict[str, Any]:
    audit = audit_module()
    image_path = Path(entry["path"])
    apple_binary = None
    compile_diagnostics: list[str] = []
    if config.ocr_engine in {"auto", "apple-vision"}:
        apple_binary = config.evidence_dir / "apple-vision-ocr"
        if not apple_binary.is_file():
            compile_diagnostics = audit.compile_apple_vision(apple_binary)
            if compile_diagnostics:
                apple_binary = None
    languages = audit.detect_tesseract_languages()
    words, used_ocr_engine, diagnostics = audit.select_ocr_words(
        image_path,
        ocr_engine=config.ocr_engine,
        apple_vision_binary=apple_binary,
        tesseract_languages=languages,
    )
    diagnostics = [*compile_diagnostics, *diagnostics]
    average_confidence = (
        sum(word.confidence for word in words) / len(words)
        if words
        else 0.0
    )
    analysis_meta, dark_mask, non_text_mask = audit.scaled_masks(image_path, words)
    structure = detect_internal_structure(
        non_text_mask,
        int(analysis_meta["original_width"]),
        int(analysis_meta["original_height"]),
    )
    groups = normalize_groups(words)
    foreground_pixels = int(dark_mask.sum())
    explained_foreground_ratio = (
        (foreground_pixels - int(non_text_mask.sum())) / foreground_pixels
        if foreground_pixels
        else 1.0
    )
    semantic_structure_detected = bool(
        structure["horizontal_hits"] >= 2 and structure["vertical_hits"] >= 2
    )
    features = {
        "ocr_succeeded": not diagnostics or bool(words),
        "word_count": len(words),
        "average_confidence": average_confidence,
        "group_count": len(groups),
        "spatial_columns": count_spatial_text_groups(groups, int(analysis_meta["original_width"])),
        "internal_dark_ratio": structure["internal_dark_ratio"],
        "internal_horizontal_hits": int(structure["horizontal_hits"]),
        "internal_vertical_hits": int(structure["vertical_hits"]),
        "explained_foreground_ratio": explained_foreground_ratio,
        "residual_edge_ratio": structure["internal_dark_ratio"],
        "residual_texture_ratio": 0.0,
        "residual_color_ratio": 0.0,
        "max_residual_component_ratio": 0.0,
        "line_intersections": 4 if semantic_structure_detected else 0,
        "connected_line_count": 0,
    }
    decision, reason_codes, rationale = standalone_decision_from_features(features)
    status = "complete"
    semantic_text_coverage = "complete" if decision == "candidate-remove" else "partial"
    visual_increment = "none" if decision == "candidate-remove" else "present"
    if decision == "needs-review" and "ocr-failed" in reason_codes:
        status = "ocr-failed"
        semantic_text_coverage = "unknown"
        visual_increment = "unknown"
    if decision == "retain" and reason_codes == ["no-text-detected"]:
        visual_increment = "present"
    result = {
        "status": status,
        "decision": decision,
        "reason_codes": reason_codes,
        "semantic_text_coverage": semantic_text_coverage,
        "visual_increment": visual_increment,
        "rationale": rationale,
        "decision_confidence_score": 98 if decision in {"candidate-remove", "retain"} else 90,
        "ocr_summary": {
            "succeeded": bool(features["ocr_succeeded"]),
            "engine": used_ocr_engine,
            "word_count": len(words),
            "group_count": len(groups),
            "average_confidence": round(average_confidence, 2),
        },
        "visual_structure_metrics": {
            **features,
            "semantic_structure_detected": semantic_structure_detected,
        },
        "ocr_engine_used": used_ocr_engine,
        "diagnostics": diagnostics,
        "features": features,
    }
    deletion_gate(result)
    return result


def query_row(connection: sqlite3.Connection, relative_path: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT m.input_sequence, m.prior_decision, m.prior_reason_codes_json, i.*
        FROM item_state AS i JOIN manifest_entries AS m USING(relative_path)
        WHERE i.relative_path = ?
        """,
        (relative_path,),
    ).fetchone()
    if row is None:
        raise KeyError(relative_path)
    return row


def query_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ["retained", "crop-required", "needs-review", "deleted", "deleted-recovered", "sha-drift", "ocr-failed", DELETE_READY_STATE, "pending", "delete-failed"]:
        counts[key] = int(
            connection.execute(
                "SELECT COUNT(*) FROM item_state WHERE final_state = ?",
                (key,),
            ).fetchone()[0]
        )
    counts["total"] = int(connection.execute("SELECT COUNT(*) FROM item_state").fetchone()[0])
    counts["processed"] = counts["total"] - counts["pending"] - counts[DELETE_READY_STATE]
    return counts


def refresh_txt_lists(
    connection: sqlite3.Connection,
    evidence_dir: Path,
) -> None:
    mapping = {
        "retained": {"retained"},
        "crop-required": {"crop-required"},
        "needs-review": {"needs-review", "ocr-failed", "sha-drift", "delete-failed"},
        "deleted": {"deleted", "deleted-recovered"},
    }
    for category, states in mapping.items():
        rows = connection.execute(
            "SELECT relative_path FROM item_state WHERE final_state IN (%s) ORDER BY relative_path"
            % ",".join("?" * len(states)),
            tuple(states),
        )
        write_lines_atomic(
            evidence_dir / TXT_LISTS[category],
            (f"{row['relative_path']}\n" for row in rows),
        )
    skipped = connection.execute(
        "SELECT relative_path FROM new_after_snapshot ORDER BY relative_path"
    )
    write_lines_atomic(
        evidence_dir / TXT_LISTS["skipped"],
        (f"{row['relative_path']}\n" for row in skipped),
    )


def write_progress(
    connection: sqlite3.Connection,
    config: CorpusConfig,
    *,
    status: str,
    stop_reason: str | None,
) -> dict[str, Any]:
    counts = query_counts(connection)
    new_after_count = int(connection.execute("SELECT COUNT(*) FROM new_after_snapshot").fetchone()[0])
    last = connection.execute(
        "SELECT m.input_sequence, i.relative_path FROM item_state i JOIN manifest_entries m USING(relative_path) WHERE i.final_state != 'pending' ORDER BY m.input_sequence DESC LIMIT 1"
    ).fetchone()
    current = connection.execute(
        "SELECT input_sequence FROM manifest_entries m JOIN item_state i USING(relative_path) WHERE i.final_state = 'pending' ORDER BY input_sequence LIMIT 1"
    ).fetchone()
    peak = {row["key"]: int(row["value"]) for row in connection.execute("SELECT key, value FROM meta")}
    progress = {
        "status": status,
        "updated_at": utc_now(),
        "corpus_root": str(config.corpus_root),
        "evidence_dir": str(config.evidence_dir),
        "manifest_path": str((config.input_manifest or (config.evidence_dir / MANIFEST_NAME)).resolve()),
        "classification_path": str((config.evidence_dir / CLASSIFICATION_NAME).resolve()),
        "deletion_ledger_path": str((config.evidence_dir / LEDGER_NAME).resolve()),
        "index_path": str((config.evidence_dir / INDEX_NAME).resolve()),
        "max_workers": config.max_workers,
        "effective_workers": 1,
        "input_total": counts["total"],
        "classified_count": counts["processed"],
        "deleted_count": counts["deleted"] + counts["deleted-recovered"],
        "retained_count": counts["retained"],
        "needs_review_count": counts["needs-review"],
        "crop_required_count": counts["crop-required"],
        "skipped_count": counts["sha-drift"] + new_after_count,
        "error_count": counts["ocr-failed"] + counts["delete-failed"],
        "current_input_sequence": int(current[0]) if current else None,
        "last_successful_commit_path": str(last["relative_path"]) if last else None,
        "process_pid": os.getpid(),
        "resumed": config.resume,
        "recovery": {
            "interruption_or_resume_occurred": config.resume,
            "reused_records": peak.get("reused_records", 0),
            "new_processed_records": peak.get("new_processed_records", 0),
            "deleted_recovered_records": counts["deleted-recovered"],
        },
        "markdown_access": {"files_read": 0, "files_modified": 0, "mode_contract": "forbidden"},
        "counts": counts,
        "stop_reason": stop_reason,
        "new_after_snapshot_count": new_after_count,
        "resource_usage": {
            "tree_rss_kib": current_children(os.getpid())[0],
            "remaining_child_rss_kib": current_children(os.getpid())[1],
            "remaining_ocr_child_count": current_children(os.getpid())[2],
            "peak_process_tree_rss_kib": peak.get("tree_rss_peak_kib", 0),
            "peak_ocr_child_count": peak.get("ocr_child_count_peak", 0),
            "peak_single_ocr_child_rss_kib": peak.get("single_ocr_child_rss_peak_kib", 0),
        },
    }
    write_json_atomic(config.evidence_dir / PROGRESS_NAME, progress)
    write_json_atomic(config.evidence_dir / SUMMARY_NAME, progress)
    refresh_txt_lists(connection, config.evidence_dir)
    return progress


def write_report(progress: dict[str, Any], config: CorpusConfig) -> None:
    counts = progress["counts"]
    lines = [
        "# 最终验收报告",
        "",
        f"- 生成时间: {progress['updated_at']}",
        f"- 状态: {progress['status']}",
        f"- 输入总数: {counts['total']}",
        f"- 已删除: {counts['deleted'] + counts['deleted-recovered']}",
        f"- 保留: {counts['retained']}",
        f"- 需裁图: {counts['crop-required']}",
        f"- 需复核/失败关闭: {counts['needs-review'] + counts['sha-drift'] + counts['ocr-failed'] + counts['delete-failed']}",
        f"- 新快照后新增: {progress['new_after_snapshot_count']}",
        "",
        "## 验收检查",
        "",
    ]
    checks = [
        ("输入全覆盖", counts["pending"] == 0 and counts[DELETE_READY_STATE] == 0),
        ("删除哈希链闭合", counts["delete-failed"] == 0),
        ("未读写 Markdown", progress["markdown_access"]["files_read"] == 0 and progress["markdown_access"]["files_modified"] == 0),
        ("无残留 OCR 子进程", progress["resource_usage"]["remaining_child_rss_kib"] == 0),
    ]
    for title, passed in checks:
        lines.append(f"- {title}: {'passed' if passed else 'failed'}")
    if progress.get("stop_reason"):
        lines.extend(["", "## 停止原因", "", f"- {progress['stop_reason']}"])
    write_text_atomic(config.evidence_dir / REPORT_NAME, "\n".join(lines).rstrip() + "\n")


def discover_new_after_snapshot(config: CorpusConfig, connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute("DELETE FROM new_after_snapshot")
    for path in discover_images(config.corpus_root, config.evidence_dir):
        relative = relative_to(config.corpus_root, path)
        exists = connection.execute(
            "SELECT 1 FROM manifest_entries WHERE relative_path = ?",
            (relative,),
        ).fetchone()
        if exists is None:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO new_after_snapshot(relative_path) VALUES (?)",
                    (relative,),
                )


def maybe_memory_stop(config: CorpusConfig) -> str | None:
    if config.memory_limit_mb <= 0:
        return None
    limit_kib = config.memory_limit_mb * 1024
    tree_rss, _child_rss, _child_count, _single_child = current_children(os.getpid())
    if tree_rss <= limit_kib:
        return None
    gc.collect()
    time.sleep(0.05)
    tree_rss, _child_rss, _child_count, _single_child = current_children(os.getpid())
    if tree_rss <= limit_kib:
        return None
    return f"memory-limit-exceeded:{tree_rss}KiB>{limit_kib}KiB"


def next_pending_row(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT m.input_sequence, m.relative_path, m.absolute_path, m.manifest_sha256, m.prior_decision, m.prior_reason_codes_json,
               i.classification_status, i.attempt, i.decision, i.deletion_state, i.final_state,
               i.deletion_eligible, i.deterministic_gates_json, i.skill_gates_json
        FROM manifest_entries AS m
        JOIN item_state AS i USING (relative_path)
        WHERE i.final_state NOT IN ('retained', 'crop-required', 'needs-review', 'ocr-failed', 'sha-drift', 'deleted', 'deleted-recovered', 'delete-failed')
        ORDER BY m.input_sequence
        LIMIT 1
        """
    ).fetchone()


def build_input_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sequence": row["input_sequence"],
        "relative_path": row["relative_path"],
        "path": row["absolute_path"],
        "sha256": row["manifest_sha256"],
        "prior_decision": row["prior_decision"] if "prior_decision" in row.keys() else None,
        "prior_reason_codes": json.loads(row["prior_reason_codes_json"] or "[]")
        if "prior_reason_codes_json" in row.keys()
        else [],
    }


def append_classification(
    config: CorpusConfig,
    connection: sqlite3.Connection,
    record: dict[str, Any],
) -> None:
    append_jsonl(config.evidence_dir / CLASSIFICATION_NAME, record)
    apply_classification_row(connection, record)
    with connection:
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES ('new_processed_records', '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(meta.value AS INTEGER) + 1 AS TEXT)
            """
        )
    usage = record.get("resource_usage") or {}
    for key in ("tree_rss_peak_kib", "ocr_child_count_peak", "single_ocr_child_rss_peak_kib"):
        value = int(usage.get(key, 0) or 0)
        with connection:
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = CAST(MAX(CAST(meta.value AS INTEGER), CAST(excluded.value AS INTEGER)) AS TEXT)
                """,
                (key, str(value)),
            )


def append_ledger(
    config: CorpusConfig,
    connection: sqlite3.Connection,
    record: dict[str, Any],
) -> None:
    append_jsonl(config.evidence_dir / LEDGER_NAME, record)
    if record.get("event") != "delete-intent":
        apply_ledger_row(connection, record)
    else:
        with connection:
            connection.execute(
                """
                UPDATE item_state
                SET deletion_state = 'intent',
                    updated_at = ?
                WHERE relative_path = ?
                """,
                (utc_now(), record["relative_path"]),
            )


def classify_one(
    config: CorpusConfig,
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> str | None:
    entry = build_input_entry(row)
    attempt = int(row["attempt"] or 0) + 1
    image_path = Path(entry["path"])
    if not image_path.exists():
        record = {
            "completed_at": utc_now(),
            "sequence": entry["sequence"],
            "attempt": attempt,
            "relative_path": entry["relative_path"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "input_sha256": entry["sha256"],
            "status": "missing",
            "decision": "needs-review",
            "reason_codes": ["missing"],
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
            "decision_confidence_score": 0,
            "ocr_summary": {},
            "visual_structure_metrics": {},
            "deterministic_deletion_gates": {},
            "skill_deletion_gates": {},
            "deletion_eligible": False,
            "rationale": "锁定清单中的图片已不存在。",
            "error": "input-missing-before-classification",
        }
        append_classification(config, connection, record)
        return None
    current_sha = sha256(image_path)
    if current_sha != entry["sha256"]:
        record = {
            "completed_at": utc_now(),
            "sequence": entry["sequence"],
            "attempt": attempt,
            "relative_path": entry["relative_path"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "input_sha256": entry["sha256"],
            "current_sha256": current_sha,
            "status": "skipped-changed",
            "decision": "needs-review",
            "reason_codes": ["sha-drift"],
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
            "decision_confidence_score": 0,
            "ocr_summary": {},
            "visual_structure_metrics": {},
            "deterministic_deletion_gates": {},
            "skill_deletion_gates": {},
            "deletion_eligible": False,
            "rationale": "图片内容已偏离锁定快照，失败关闭。",
            "error": "input-sha256-drift",
        }
        append_classification(config, connection, record)
        return None
    classifier = config.classifier or default_classifier
    try:
        result = with_monitor(classifier, entry, config)
    except Exception as exc:
        record = {
            "completed_at": utc_now(),
            "sequence": entry["sequence"],
            "attempt": attempt,
            "relative_path": entry["relative_path"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "input_sha256": entry["sha256"],
            "status": "needs-review",
            "decision": "needs-review",
            "reason_codes": ["classifier-exception"],
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
            "decision_confidence_score": 0,
            "ocr_summary": {},
            "visual_structure_metrics": {},
            "deterministic_deletion_gates": {},
            "skill_deletion_gates": {},
            "deletion_eligible": False,
            "rationale": str(exc),
            "error": str(exc),
        }
        append_classification(config, connection, record)
        return f"stop:classifier-exception:{exc}"
    _gates, deletion_eligible = deletion_gate(result)
    record = {
        "completed_at": utc_now(),
        "sequence": entry["sequence"],
        "attempt": attempt,
        "relative_path": entry["relative_path"],
        "path": entry["path"],
        "sha256": entry["sha256"],
        "input_sha256": entry["sha256"],
        "status": result.get("status", "needs-review"),
        "decision": result.get("decision", "needs-review"),
        "reason_codes": result.get("reason_codes") or ["needs-review"],
        "semantic_text_coverage": result.get("semantic_text_coverage", "unknown"),
        "visual_increment": result.get("visual_increment", "unknown"),
        "rationale": str(result.get("rationale") or "")[:2000],
        "decision_confidence_score": float(result.get("decision_confidence_score", 0)),
        "ocr_summary": result.get("ocr_summary") or {},
        "visual_structure_metrics": result.get("visual_structure_metrics") or result.get("features") or {},
        "deterministic_deletion_gates": result.get("deterministic_deletion_gates") or {},
        "skill_deletion_gates": result.get("skill_deletion_gates") or {},
        "deletion_eligible": deletion_eligible,
        "error": str(result.get("error") or ""),
        "resource_usage": result.get("resource_usage") or {},
        "diagnostics": result.get("diagnostics") or [],
        "features": result.get("features") or {},
    }
    append_classification(config, connection, record)
    if record["deletion_eligible"]:
        return "delete"
    return None


def delete_one(
    config: CorpusConfig,
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> None:
    entry = build_input_entry(row)
    image_path = Path(entry["path"])
    had_durable_intent = row["deletion_state"] == "intent"
    deterministic_gates = json.loads(row["deterministic_gates_json"] or "{}")
    skill_gates = json.loads(row["skill_gates_json"] or "{}")
    classification_authorizes_delete = bool(
        row["decision"] == "candidate-remove"
        and int(row["deletion_eligible"] or 0) == 1
        and row["final_state"] == DELETE_READY_STATE
        and deterministic_gates
        and skill_gates
        and all(deterministic_gates.values())
        and all(skill_gates.values())
    )
    if not classification_authorizes_delete:
        append_ledger(
            config,
            connection,
            {
                "completed_at": utc_now(),
                "sequence": entry["sequence"],
                "event": "delete-result",
                "state": "delete-failed",
                "relative_path": entry["relative_path"],
                "path": entry["path"],
                "sha256": entry["sha256"],
                "input_sha256": entry["sha256"],
                "rationale": "delete-intent lacks a matching classification with both deletion gates.",
            },
        )
        return
    intent = {
        "completed_at": utc_now(),
        "sequence": entry["sequence"],
        "event": "delete-intent",
        "relative_path": entry["relative_path"],
        "path": entry["path"],
        "sha256": entry["sha256"],
        "input_sha256": entry["sha256"],
    }
    if not had_durable_intent:
        append_ledger(config, connection, intent)
    if not image_path.exists():
        state = "deleted-recovered" if had_durable_intent else "missing"
        record = {
            "completed_at": utc_now(),
            "sequence": entry["sequence"],
            "event": "delete-result",
            "state": state,
            "relative_path": entry["relative_path"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "input_sha256": entry["sha256"],
            "rationale": (
                "发现运行前已有 delete-intent 且文件已不存在，恢复为 deleted-recovered。"
                if had_durable_intent
                else "本次写入 delete-intent 后发现文件已不存在，失败关闭为 missing。"
            ),
        }
        append_ledger(config, connection, record)
        return
    current_sha = sha256(image_path)
    if current_sha != entry["sha256"]:
        record = {
            "completed_at": utc_now(),
            "sequence": entry["sequence"],
            "event": "delete-result",
            "state": "skipped-changed",
            "relative_path": entry["relative_path"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "input_sha256": entry["sha256"],
            "current_sha256": current_sha,
            "rationale": "delete-intent 之后 SHA 漂移，停止删除。",
        }
        append_ledger(config, connection, record)
        return
    try:
        image_path.unlink()
        _fsync_dir(image_path.parent)
    except OSError as exc:
        record = {
            "completed_at": utc_now(),
            "sequence": entry["sequence"],
            "event": "delete-result",
            "state": "delete-failed",
            "relative_path": entry["relative_path"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "input_sha256": entry["sha256"],
            "rationale": str(exc),
        }
        append_ledger(config, connection, record)
        return
    record = {
        "completed_at": utc_now(),
        "sequence": entry["sequence"],
        "event": "delete-result",
        "state": "deleted",
        "relative_path": entry["relative_path"],
        "path": entry["path"],
        "sha256": entry["sha256"],
        "input_sha256": entry["sha256"],
    }
    append_ledger(config, connection, record)


def run_corpus(config: CorpusConfig) -> dict[str, Any]:
    config = dataclasses.replace(
        config,
        corpus_root=config.corpus_root.expanduser().resolve(),
        evidence_dir=config.evidence_dir.expanduser().resolve(),
        input_manifest=config.input_manifest.expanduser().resolve() if config.input_manifest else None,
        prior_classification=config.prior_classification.expanduser().resolve() if config.prior_classification else None,
        max_workers=validate_workers(config.max_workers),
    )
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    ensure_durable_file(config.evidence_dir / CLASSIFICATION_NAME)
    ensure_durable_file(config.evidence_dir / LEDGER_NAME)
    append_log(config.evidence_dir / LOG_NAME, "run-start")
    manifest_path = (config.input_manifest or (config.evidence_dir / MANIFEST_NAME)).resolve()
    with contextlib.ExitStack() as stack:
        manifest_handle = stack.enter_context(open_and_lock_manifest(manifest_path))
        ensure_manifest(config, manifest_handle)
        manifest_lock_path = config.evidence_dir / "input-manifest.lock.json"
        if manifest_lock_path.is_file():
            verify_or_lock_manifest(manifest_path, config.evidence_dir, config.corpus_root)
            validate_manifest_scope(manifest_path, config.corpus_root)
        else:
            validate_manifest_scope(manifest_path, config.corpus_root)
            verify_or_lock_manifest(manifest_path, config.evidence_dir, config.corpus_root)
        connection = init_db(config.evidence_dir / INDEX_NAME)
        stack.callback(connection.close)
        sync_manifest_into_db(connection, manifest_path)
        rebuild_db_from_logs(connection, config.evidence_dir / CLASSIFICATION_NAME, config.evidence_dir / LEDGER_NAME)
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('new_processed_records', '0')"
            )
        if config.resume:
            reused = int(
                connection.execute(
                    "SELECT COUNT(*) FROM item_state WHERE classification_status != 'pending'"
                ).fetchone()[0]
            )
            with connection:
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('reused_records', ?)",
                    (str(reused),),
                )
        discover_new_after_snapshot(config, connection)
        stop_reason: str | None = None
        progress = write_progress(connection, config, status="running", stop_reason=None)
        append_log(config.evidence_dir / LOG_NAME, f"pending={progress['counts']['pending']} delete_ready={progress['counts'][DELETE_READY_STATE]}")
        while True:
            stop_reason = maybe_memory_stop(config)
            if stop_reason is not None:
                gc.collect()
                append_log(config.evidence_dir / LOG_NAME, stop_reason)
                break
            row = next_pending_row(connection)
            if row is None:
                break
            if row["decision"] == "candidate-remove" or row["final_state"] == DELETE_READY_STATE or row["deletion_state"] == "intent":
                delete_one(config, connection, row)
            else:
                action = classify_one(config, connection, row)
                if isinstance(action, str) and action.startswith("stop:"):
                    stop_reason = action.removeprefix("stop:")
                    progress = write_progress(connection, config, status="running", stop_reason=stop_reason)
                    append_log(config.evidence_dir / LOG_NAME, stop_reason)
                    break
                if action == "delete":
                    refreshed = query_row(connection, row["relative_path"])
                    delete_one(config, connection, refreshed)
            stop_reason = maybe_memory_stop(config)
            progress = write_progress(connection, config, status="running", stop_reason=stop_reason)
            gc.collect()
            if stop_reason is not None:
                append_log(config.evidence_dir / LOG_NAME, stop_reason)
                break
        counts = query_counts(connection)
        status = "complete"
        if stop_reason is not None:
            status = "needs-review"
        elif counts["pending"] or counts[DELETE_READY_STATE] or counts["needs-review"] or counts["sha-drift"] or counts["ocr-failed"] or counts["delete-failed"]:
            status = "needs-review"
        progress = write_progress(connection, config, status=status, stop_reason=stop_reason)
        write_report(progress, config)
        append_log(config.evidence_dir / LOG_NAME, f"run-finish status={status}")
        return progress


def parse_args(argv: list[str] | None = None) -> CorpusConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_root", type=Path)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--prior-classification", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--memory-limit-mb", type=int, default=0)
    parser.add_argument("--ocr-engine", default="auto")
    args = parser.parse_args(argv)
    return CorpusConfig(
        corpus_root=args.corpus_root,
        evidence_dir=args.evidence_dir,
        input_manifest=args.input_manifest,
        prior_classification=args.prior_classification,
        resume=args.resume,
        max_workers=args.max_workers,
        memory_limit_mb=args.memory_limit_mb,
        ocr_engine=args.ocr_engine,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        progress = run_corpus(parse_args(argv))
    except Exception as exc:  # pragma: no cover - CLI surface
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(progress, ensure_ascii=False, indent=2))
    return 0 if progress["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
