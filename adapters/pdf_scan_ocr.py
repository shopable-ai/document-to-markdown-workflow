"""Locked Tesseract evidence from one caller-selected page raster.

Only status='ok' is usable for text comparison, and only if the compared
text was not derived from this engine. No result authorizes image removal.
Failure records are immutable too: retry at a new output path. Invalid or
drifted cache/input bindings raise ValueError rather than being overwritten.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from statistics import median
import subprocess
import tempfile
import time


SCHEMA = "pdf-scan-ocr"
VERSION = "1.0.0"
ENGINE = "tesseract"
LANGUAGES = ("chi_sim", "eng")
PSM = 6
OEM = 1
_SHA = re.compile(r"[0-9a-f]{64}")
_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
_WORD_KEYS = {"text", "confidence", "x0", "y0", "x1", "y1"}
_PROVENANCE_KEYS = ("engine_version", "engine_sha256", "model_sha256")


class _OCRFailure(Exception):
    def __init__(self, code: str, diagnostic: str):
        super().__init__(diagnostic)
        self.code = code


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _binding(source_sha: str, page: int, asset_sha: str, primary_sha: str, final_sha: str) -> dict:
    if type(page) is not int or page < 1:
        raise ValueError("scan OCR page must be a positive integer")
    values = dict(source_sha256=source_sha, asset_sha256=asset_sha,
                  primary_sha256=primary_sha, final_sha256=final_sha)
    for key, value in values.items():
        if not _is_sha(value):
            raise ValueError(f"scan OCR {key} must be lowercase SHA-256")
    return dict(values, page=page)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_sha(record: dict) -> str:
    payload = {key: value for key, value in record.items() if key != "payload_sha256"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unique_object(items: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _valid_text(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and not any(ord(c) < 32 or c in "\x7f\u0085\u2028\u2029\ufffd" for c in value))


def _geometry_issues(rows: list[list[dict]], width: int, height: int) -> set[str]:
    # PSM 6 can flatten columns. Keep original order and flag ambiguous geometry.
    typical = median(word["y1"] - word["y0"] for row in rows for word in row)
    previous_center = -1.
    previous_gaps: list[tuple[float, float]] = []
    issues: set[str] = set()
    for row in rows:
        mids = [(word["y0"] + word["y1"]) / 2 for word in row]
        center = median(mids)
        if center - previous_center < typical * .45:
            issues.add("rows-overlap-or-out-of-order")
        previous_center = center
        if max(mids) - min(mids) > typical * 1.25:
            issues.add("tokens-disagree-on-row")
        alpha = [i for i, word in enumerate(row) if any(c.isalpha() for c in word["text"])]
        gaps = []
        for i, (left, right) in enumerate(zip(row, row[1:])):
            if right["x0"] + right["x1"] <= left["x0"] + left["x1"]:
                issues.add("tokens-out-of-order")
            x0, x1 = left["x1"], right["x0"]
            if x1 - x0 >= max(.12, 4 * typical * height / width) and alpha and alpha[0] <= i < alpha[-1]:
                # A numeric-only TOC suffix does not by itself imply columns.
                gaps.append((x0, x1))
        if any(min(b, d) - max(a, c) >= .08 for a, b in previous_gaps for c, d in gaps):
            issues.add("repeated-text-gutter")
        previous_gaps = gaps
    return issues


def _validate(record: dict, binding: dict) -> None:
    if not isinstance(record, dict) or (record.get("schema"), record.get("version")) != (SCHEMA, VERSION):
        raise ValueError("unsupported scan OCR evidence schema/version")
    for key, value in binding.items():
        if type(record.get(key)) is not type(value) or record[key] != value:
            raise ValueError(f"scan OCR binding mismatch: {key}")
    if record.get("payload_sha256") != _payload_sha(record):
        raise ValueError("scan OCR payload hash mismatch")
    if (record.get("engine"), record.get("languages"), record.get("psm"), record.get("oem")) != (
        ENGINE, list(LANGUAGES), PSM, OEM,
    ):
        raise ValueError("invalid scan OCR recognition profile")
    cost = record.get("elapsed_ms")
    if type(cost) is not int or cost < 0:
        raise ValueError("invalid scan OCR cost")
    required = {*_PROVENANCE_KEYS, "status", "rows", "image_size", "reason_code", "diagnostic"}
    if not required <= record.keys():
        raise ValueError("missing scan OCR record fields")
    version, executable, models = (record[key] for key in _PROVENANCE_KEYS)
    if (not isinstance(models, dict) or set(models) - set(LANGUAGES)
            or any(not _is_sha(value) for value in models.values())
            or (version is not None and not (isinstance(version, str) and re.fullmatch(r"\S+", version)))
            or (executable is not None and not _is_sha(executable))):
        raise ValueError("invalid scan OCR engine provenance")
    status, rows, size = (record[key] for key in ("status", "rows", "image_size"))
    reason, diagnostic = (record[key] for key in ("reason_code", "diagnostic"))
    if status != "ok" and not (isinstance(reason, str) and reason and isinstance(diagnostic, str) and diagnostic):
        raise ValueError("missing scan OCR diagnostic")
    if status == "failed" and rows == [] and size is None:
        return
    if (status not in ("ok", "layout_uncertain") or (status == "ok" and (reason is not None or diagnostic is not None))
            or not (version and executable and set(models) == set(LANGUAGES))
            or not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or n < 1 for n in size)
            or not isinstance(rows, list) or not rows):
        raise ValueError("invalid scan OCR status, provenance, or rows")
    for row in rows:
        if not isinstance(row, list) or not row:
            raise ValueError("empty or invalid scan OCR row")
        for word in row:
            if not isinstance(word, dict) or word.keys() != _WORD_KEYS or not _valid_text(word["text"]):
                raise ValueError("invalid scan OCR word")
            conf, x0, y0, x1, y1 = (word[key] for key in ("confidence", "x0", "y0", "x1", "y1"))
            if (any(type(n) not in (int, float) for n in (conf, x0, y0, x1, y1))
                    or not (0 <= conf <= 1 and 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)):
                raise ValueError("invalid scan OCR coordinates or confidence")
    if status == "ok" and _geometry_issues(rows, *size):
        raise ValueError("scan OCR success contradicts its row geometry")


def _read(path: Path, binding: dict) -> dict:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("expected a regular, non-symlink cache file")
        record = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        _validate(record, binding)
    except (OSError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError(f"unusable scan OCR cache {path}: {exc}") from exc
    return record


def load(path: Path, *, source_sha: str, page: int, asset_sha: str, primary_sha: str, final_sha: str) -> dict:
    """Replay evidence offline. Separately bind the SHA-256 of the actual JSON bytes in the audit."""
    return _read(path, _binding(source_sha, page, asset_sha, primary_sha, final_sha))


def _run(executable: Path, args: list[str], *, data: bytes | None = None) -> bytes:
    try:
        process = subprocess.run(
            [str(executable), *args], input=data, capture_output=True, check=False,
            timeout=30 if data is not None else 5,
            env={**os.environ, "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise _OCRFailure("tesseract-timeout", f"Tesseract timed out running {args[0]}") from exc
    except OSError as exc:
        raise _OCRFailure("tesseract-unavailable", str(exc)) from exc
    stderr = process.stderr.decode("utf-8", errors="replace").strip()
    # Tesseract can exit 0 after loading only some requested models.
    if re.search(r"failed loading language|error opening data file|couldn.t load any languages", stderr, re.I):
        raise _OCRFailure("tesseract-language-unavailable", stderr[:1000])
    if process.returncode != 0 or re.search(r"(?im)^(?:error\b|failed\b|read_params_file\b)", stderr):
        raise _OCRFailure("tesseract-failed", f"Tesseract exit {process.returncode}: {stderr[:1000]}")
    return process.stdout


def _model_hashes(data_dir: Path) -> dict[str, str]:
    models = {}
    for lang in LANGUAGES:
        path = data_dir / f"{lang}.traineddata"
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("missing or empty traineddata file")
            models[lang] = _digest(path)
        except OSError as exc:
            raise _OCRFailure("tesseract-language-unavailable", f"{lang}: {exc}") from exc
    return models


def _discover(record: dict) -> tuple[Path, Path]:
    found = shutil.which(ENGINE)
    if not found:
        raise _OCRFailure("tesseract-unavailable", "Tesseract was not found on PATH")
    try:
        executable = Path(found).resolve(strict=True)
        record["engine_sha256"] = _digest(executable)
    except OSError as exc:
        raise _OCRFailure("tesseract-unavailable", str(exc)) from exc
    version_output = _run(executable, ["--version"]).decode("utf-8", errors="replace")
    version = re.match(r"tesseract (\S+)", version_output)
    if not version:
        raise _OCRFailure("tesseract-version-unknown", "Tesseract --version did not report a version")
    record["engine_version"] = version.group(1)
    lang_output = _run(executable, ["--list-langs"]).decode("utf-8", errors="replace").splitlines()
    header = re.fullmatch(r'List of available languages in "(.+)" \(\d+\):', lang_output[0]) if lang_output else None
    if not header:
        raise _OCRFailure("tesseract-language-unavailable", "Tesseract did not identify a local tessdata directory")
    missing = set(LANGUAGES) - {line.strip() for line in lang_output[1:]}
    if missing:
        raise _OCRFailure("tesseract-language-unavailable", f"Missing local Tesseract models: {', '.join(sorted(missing))}")
    data_dir = Path(header.group(1)).resolve()
    record["model_sha256"] = _model_hashes(data_dir)
    return executable, data_dir


def _parse_tsv(data: bytes) -> tuple[list[list[dict]], list[int], set[str]]:
    lines = data.decode("utf-8").split("\n")
    if lines[0].rstrip("\r") != _TSV_HEADER:
        raise ValueError("bad TSV header")
    width = height = 0
    rows: list[list[dict]] = []
    line_ids: set[tuple[int, int, int]] = set()
    previous_id = None
    previous_word = 0
    issues: set[str] = set()
    for raw in lines[1:]:
        if not raw.rstrip("\r"):
            continue
        level, page, block, par, line, word, left, top, w, h, confidence, token = raw.rstrip("\r").split("\t", 11)
        level, page, block, par, line, word, left, top, w, h = map(
            int, (level, page, block, par, line, word, left, top, w, h),
        )
        if level == 1:
            if width or page != 1 or any((block, par, line, word, left, top)) or min(w, h) <= 0:
                raise ValueError("expected a single image in TSV")
            width, height = w, h
        elif not width or page != 1 or not 2 <= level <= 5:
            raise ValueError("invalid TSV hierarchy")
        elif level == 5:
            key = (block, par, line)
            if min(*key, word) < 1:
                raise ValueError("invalid TSV word lineage")
            if not token.strip():
                issues.add("empty-word-token")
                continue
            conf = float(confidence)
            if (not _valid_text(token) or not 0 <= conf <= 100
                    or not (0 <= left < left + w <= width and 0 <= top < top + h <= height)):
                raise ValueError("invalid TSV word text, confidence, or bounds")
            if key != previous_id:
                if key in line_ids:
                    issues.add("interleaved-tsv-lines")
                line_ids.add(key)
                rows.append([])
                previous_id, previous_word = key, 0
            if word != previous_word + 1:
                issues.add("non-contiguous-tsv-word-index")
            previous_word = word
            rows[-1].append(dict(text=token, confidence=conf / 100, x0=left / width, y0=top / height,
                                 x1=(left + w) / width, y1=(top + h) / height))
    if not width:
        raise ValueError("missing TSV page geometry")
    if rows:
        issues |= _geometry_issues(rows, width, height)
    if len({key[0] for key in line_ids}) > 1:
        issues.add("multiple-tsv-blocks")
    return rows, [width, height], issues


def _recognize(data: bytes, record: dict) -> None:
    executable, data_dir = _discover(record)
    output = _run(executable, ["stdin", "stdout", "--tessdata-dir", str(data_dir),
                               "-l", "+".join(LANGUAGES), "--oem", str(OEM), "--psm", str(PSM),
                               "-c", "tessedit_load_sublangs=", "-c", "tessedit_create_tsv=1"], data=data)
    if _digest(executable) != record["engine_sha256"] or _model_hashes(data_dir) != record["model_sha256"]:
        raise _OCRFailure("tesseract-provenance-changed", "Tesseract executable or traineddata changed during OCR")
    try:
        rows, size, issues = _parse_tsv(output)
    except (ValueError, OverflowError) as exc:
        raise _OCRFailure("tesseract-invalid-tsv", str(exc)) from exc
    if not rows:
        raise _OCRFailure("tesseract-no-text", "Tesseract returned no usable words")
    record.update(rows=rows, image_size=size, status="layout_uncertain" if issues else "ok")
    if issues:
        record.update(reason_code="layout-uncertain", diagnostic=", ".join(sorted(issues)))


def _publish(output: Path, record: dict) -> None:
    # Atomically publish completed JSON without replacing even a non-cooperating writer.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write((json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            os.link(temporary, output)
        finally:
            temporary.unlink()


def collect(image: Path, output: Path, *, source_sha: str, page: int, asset_sha: str,
            primary_sha: str, final_sha: str) -> dict:
    """Run local OCR once per path, with exact image bytes and an immutable page binding."""
    binding = _binding(source_sha, page, asset_sha, primary_sha, final_sha)
    lock_path = output.with_name(output.name + ".lock")
    if image.resolve() in (output.resolve(), lock_path.resolve()):
        raise ValueError("scan OCR cache/lock must not overlap the source image")
    output.parent.mkdir(parents=True, exist_ok=True)
    # The lock inode is persistent; readers need no lock after atomic publish.
    with os.fdopen(os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = image.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset_sha:
            raise ValueError("scan OCR raster SHA-256 does not match asset_sha256")
        if output.exists() or output.is_symlink():
            cached = _read(output, binding)
            if cached["status"] != "failed":
                current: dict = {}
                try:
                    _discover(current)
                except _OCRFailure as exc:
                    raise ValueError(f"cannot re-use scan OCR cache: {exc.code}: {exc}") from exc
                if any(cached[key] != current[key] for key in _PROVENANCE_KEYS):
                    raise ValueError("scan OCR cache engine/model provenance mismatch")
            return cached

        started = time.monotonic()
        record = dict(binding, schema=SCHEMA, version=VERSION, engine=ENGINE, engine_version=None,
                      engine_sha256=None, model_sha256={}, languages=list(LANGUAGES), psm=PSM, oem=OEM,
                      rows=[], image_size=None, status="failed", reason_code=None, diagnostic=None)
        try:
            _recognize(data, record)
        except _OCRFailure as exc:
            record.update(reason_code=exc.code, diagnostic=str(exc))
        except OSError as exc:
            record.update(reason_code="tesseract-provenance-unreadable", diagnostic=str(exc))
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        record["payload_sha256"] = _payload_sha(record)
        _validate(record, binding)
        _publish(output, record)
        return record
