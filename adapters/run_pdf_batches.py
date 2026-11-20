#!/usr/bin/env python3
"""Queue a PDF through the existing native adapter in bounded page batches.

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
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    from output_naming import (
        resolve_package_target,
        source_markdown_filename,
    )
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from .output_naming import (
        resolve_package_target,
        source_markdown_filename,
    )

try:
    import pdf_task_guard as task_guard
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_task_guard as task_guard

try:
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_resource_slots as resource_slots

try:
    import pdf_route_detection as page_router
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_route_detection as page_router

try:
    import pdf_hybrid_output_validator as hybrid_validator
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_hybrid_output_validator as hybrid_validator


ROOT = Path(__file__).resolve().parents[1]
LAN_ADAPTER = ROOT / "adapters" / "lan_pdf_to_markdown.py"
VISUAL_ADAPTER = ROOT / "adapters" / "lan_pdf_vision_to_markdown.py"
APPLE_OCR_ADAPTER = ROOT / "adapters" / "apple_vision_pdf_to_markdown.py"
NOISE_CLEANUP_ADAPTER = ROOT / "adapters" / "page_markdown_noise_cleanup.py"
HEADING_REPAIR_ADAPTER = ROOT / "adapters" / "lan_markdown_heading_repair.py"
DEFAULT_NOISE_PROFILE = ROOT / "references" / "reader-noise-profile.yaml"
BUNDLED_PDF_PYTHON = Path(
    "/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
)
FORMAT_VERSION = "0.6.0-pure-native-run-root-acceptance"
HYBRID_FORMAT_VERSION = "0.7.0-page-hybrid-routing"
NATIVE_TEXT_VISIBILITY_VERSION = 1
LEGACY_FULL_PAGE_TEXT_REASON = "full-page-background-with-complete-text-layer"
UNVERIFIED_FULL_PAGE_TEXT_REASON = "full-page-background-text-visibility-unverified"
LOCAL_PAGE_OCR_ROUTE = "apple-vision-ocr"
OLLAMA_HEALTH_URL = "http://shopme.local:11434/api/ps"
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


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _page_unit_text(page: int, text: str) -> str:
    body = text.rstrip("\n")
    if body:
        return f"<!-- page:{page} -->\n\n{body}\n"
    return f"<!-- page:{page} -->\n"


def _page_package_paths(pages_root: Path, page: int) -> tuple[Path, Path, Path]:
    """Accept both current and legacy nested page-package layouts."""
    flat = pages_root / f"page-{page:03d}"
    nested = flat / flat.name
    if (nested / "summary.json").is_file() or (nested / "evidence" / f"page-{page:03d}.json").is_file():
        package = nested
    else:
        package = flat
    return (
        package,
        package / "summary.json",
        package / "evidence" / f"page-{page:03d}.json",
    )


def _archive_incomplete_page_package(pages_root: Path, page: int) -> Path | None:
    """Preserve an invalid page package so the native adapter can retry cleanly."""

    package_root = pages_root / f"page-{page:03d}"
    if not package_root.exists():
        return None
    archive_parent = (
        pages_root / "_incomplete-page-packages" / f"page-{page:03d}"
    )
    ordinal = 1
    while True:
        target = archive_parent / f"attempt-{ordinal:03d}"
        if not target.exists():
            break
        ordinal += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(package_root), str(target))
    return target


def _native_page_command(
    *, page_python: str, source: Path, pages_root: Path, page: int
) -> list[str]:
    """Build one native page command with a flat ``pages/page-NNN`` package."""

    return [
        page_python,
        str(LAN_ADAPTER),
        "--source",
        str(source),
        "--output-root",
        str(pages_root),
        "--pages",
        str(page),
        "--execute",
        "--no-model",
        "--engineering-output",
        "--no-source-markdown-alias",
    ]


def _visual_page_command(
    *,
    helper_python: str,
    source: Path,
    visual_root: Path,
    page: int,
    visual_dpi: int,
    timeout: int,
    can_resume: bool,
    decision: dict[str, Any],
) -> tuple[list[str], str]:
    """Select OCR for plain scanned text and a visual model for real structure."""

    text_signals = decision.get("text_signals") or {}
    table_detection = decision.get("table_detection") or {}
    rendered = decision.get("rendered_structure") or {}
    local_ocr_only = bool(
        (decision.get("signals") or {}).get("page_image_required") is True
        or (
            decision.get("route") == "visual-only"
            and not text_signals.get("present")
            and not table_detection.get("present")
            and not rendered.get("visual_structure_present")
        )
    )
    if local_ocr_only:
        command = _apple_ocr_page_command(
            helper_python=helper_python,
            source=source,
            visual_root=visual_root,
            page=page,
            visual_dpi=visual_dpi,
            timeout=timeout,
        )
        route = LOCAL_PAGE_OCR_ROUTE
    else:
        command = [
            helper_python,
            str(VISUAL_ADAPTER),
            "convert",
            "--source",
            str(source),
            "--run-root",
            str(visual_root),
            "--pages",
            str(page),
            "--evidence-level",
            "compact",
            "--dpi",
            str(visual_dpi),
            "--timeout-seconds",
            str(timeout),
            "--progress-every",
            "1",
        ]
        route = "lan-visual-model"
    if can_resume:
        command.append("--resume")
    return command, route


def _apple_ocr_page_command(
    *,
    helper_python: str,
    source: Path,
    visual_root: Path,
    page: int,
    visual_dpi: int,
    timeout: int,
) -> list[str]:
    return [
        helper_python,
        str(APPLE_OCR_ADAPTER),
        "--source",
        str(source),
        "--run-root",
        str(visual_root),
        "--pages",
        str(page),
        "--dpi",
        str(visual_dpi),
        "--timeout-seconds",
        str(timeout),
        "--progress-every",
        "1",
    ]


def _remove_redundant_page_aliases(pages_root: Path, source: Path) -> list[str]:
    """Remove only generated page aliases that are byte-identical to index.md."""

    alias_name = source_markdown_filename(source)
    removed: list[str] = []
    for outer in sorted(pages_root.glob("page-[0-9][0-9][0-9]")):
        roots = [outer]
        legacy_nested = outer / outer.name
        if legacy_nested.is_dir():
            roots.append(legacy_nested)
        for package in roots:
            canonical = package / "index.md"
            alias = package / alias_name
            if (
                canonical.is_file()
                and alias.is_file()
                and sha256(canonical) == sha256(alias)
            ):
                alias.unlink()
                removed.append(str(alias))
    return removed


def _remove_stale_source_aliases(
    batch_root: Path,
    source: Path,
    *,
    keep: Path | None = None,
) -> list[str]:
    """Remove numbered resume aliases only when they duplicate a known stage."""

    source_stem = Path(source_markdown_filename(source)).stem
    numbered = re.compile(rf"^{re.escape(source_stem)}-[0-9]{{2}}\.md$")
    generated = [
        batch_root / "index.md",
        batch_root / "document-temp.md",
        batch_root / "document-temp.raw.md",
        batch_root / "document-temp.pre-heading.md",
    ]
    if keep is not None:
        generated.append(keep)
    generated_hashes = {sha256(path) for path in generated if path.is_file()}
    canonical_pages = (
        re.findall(r"<!--\s*page:(\d+)\s*-->", (batch_root / "index.md").read_text(encoding="utf-8"))
        if (batch_root / "index.md").is_file()
        else []
    )
    removed: list[str] = []
    for candidate in sorted(batch_root.glob("*.md")):
        if (keep is not None and candidate == keep) or not numbered.fullmatch(candidate.name):
            continue
        candidate_pages = (
            re.findall(r"<!--\s*page:(\d+)\s*-->", candidate.read_text(encoding="utf-8"))
            if candidate.is_file() and canonical_pages
            else []
        )
        if candidate.is_file() and (
            sha256(candidate) in generated_hashes
            or (bool(canonical_pages) and candidate_pages == canonical_pages)
        ):
            candidate.unlink()
            removed.append(str(candidate))
    return removed


def _collect_page_image_stats(
    source: Path,
    selected: list[int],
    *,
    page_size: tuple[float, float] | None,
) -> tuple[dict[int, page_router.PageImageStats], dict[str, Any]]:
    try:
        pdfimages = page_router.require_tool("pdfimages")
        stats = page_router.extract_image_stats_for_pages(
            pdfimages,
            source,
            selected,
            page_size=page_size,
        )
        return stats, {"status": "passed", "tool": pdfimages}
    except Exception as exc:
        # The page decision fails closed to needs-review when image-object
        # evidence is unavailable; the batch still evaluates later pages.
        return {}, {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _page_route_decision(
    *,
    source: Path,
    page: int,
    native_evidence: dict[str, Any],
    images: page_router.PageImageStats,
    image_probe: dict[str, Any],
) -> dict[str, Any]:
    native_text = str(native_evidence.get("text") or "")
    bbox = page_router.bbox_metrics_from_native_evidence(native_evidence)
    table_count = native_evidence.get("table_count_pdfplumber_find_tables")
    vectors = native_evidence.get("pdf_vector_objects")
    vector_counts = vectors.get("counts") if isinstance(vectors, dict) else None
    vector_objects_present = (
        isinstance(vector_counts, dict)
        and vectors.get("version") == "pdfplumber-vector-v1"
        and vectors.get("status") == "complete"
        and any(type(vector_counts.get(kind)) is int and vector_counts[kind] > 0 for kind in ("line", "rect", "curve"))
    )
    if vector_objects_present or page_router.page_needs_render_probe(
        native_text=native_text,
        images=images,
        bbox_metrics=bbox,
        table_count=table_count,
    ):
        try:
            rendered = page_router.inspect_rendered_structure(source, page)
        except (OSError, page_router.DetectionError) as exc:
            rendered = {"status": "failed", "visual_structure_present": False, "structure_types": [], "error": str(exc)}
    else:
        rendered = {
            "status": "not-run",
            "visual_structure_present": False,
            "structure_types": [],
        }
    decision = page_router.decide_page_route(
        page=page,
        native_text=native_text,
        images=images,
        bbox_metrics=bbox,
        table_count=table_count,
        rendered_structure=rendered,
    )
    full_page_background_with_text = (
        image_probe.get("status") == "passed"
        and images.image_count > 0
        and images.max_area_ratio >= page_router.FULL_PAGE_BACKGROUND_MIN_AREA_RATIO
        and bool(native_text.strip())
    )
    decision["signals"]["native_text_visibility"] = {
        "version": NATIVE_TEXT_VISIBILITY_VERSION,
        "status": "unknown",
        "reason": (UNVERIFIED_FULL_PAGE_TEXT_REASON if full_page_background_with_text
                   else "no-paint-visibility-detector"),
    }
    if "pdf_vector_objects" in native_evidence:
        decision["signals"]["pdf_vector_objects"] = vectors
    if vector_objects_present:
        decision["reason_codes"].append("pdf-vector-objects-present")
    if full_page_background_with_text and not (
        rendered.get("status") == "passed" and rendered.get("visual_structure_present") is True
    ):
        # A negative or unavailable line/grid probe cannot distinguish visible
        # native text from an invisible OCR overlay. Keep the extraction as raw
        # evidence; use the retained page image and fresh OCR for the body.
        reasons = [reason for reason in decision["reason_codes"]
                   if reason not in {LEGACY_FULL_PAGE_TEXT_REASON, "native-text-usable"}]
        decision.update(
            route="visual-only",
            status="passed",
            needs_review=False,
            visual_model_required=False,
            reason_codes=[*reasons, UNVERIFIED_FULL_PAGE_TEXT_REASON, "page-image-and-local-ocr-required"],
        )
        decision["signals"]["page_image_required"] = True
    elif vector_objects_present and decision["route"] == "native-only":
        # Layout objects can be meaningful without resembling a line grid.
        # Keep native text here when usable, but retain a render for review.
        decision.update(
            route="native-plus-visual" if decision["native_body_sufficient"] else "visual-only",
            status="passed",
            needs_review=False,
            visual_model_required=False,
            reason_codes=["pdf-vector-objects-present", "page-image-and-local-ocr-required"],
        )
        decision["signals"]["page_image_required"] = True
    if image_probe.get("status") != "passed":
        decision["signals"]["pdfimages_probe"] = image_probe
        if decision["route"] == "native-only" and not decision["needs_review"]:
            cues = (decision["signals"].get("visual_text_cues") or {}).get("present")
            if cues or not native_text.replace("\f", "").strip():
                decision["status"] = "needs-review"
                decision["needs_review"] = True
                decision["reason_codes"].append("pdfimages-evidence-unavailable")
    return decision


def _confirmed_table_count(page_route_results: list[dict[str, Any]]) -> int:
    """Count table signals that survive the independent render probe.

    ``pdfplumber.find_tables`` is deliberately retained as raw evidence, but
    ebook title bars and printable-area backgrounds can look like tables to
    its line heuristic. A successful rendered probe that explicitly finds no
    table-or-matrix structure is stronger negative evidence for final Markdown
    acceptance. If the probe is unavailable or finds another visual structure,
    fail closed and retain the raw count.
    """
    confirmed = 0
    for item in page_route_results:
        detection = item.get("table_detection") or {}
        count = detection.get("pdfplumber_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            continue
        rendered = item.get("rendered_structure") or {}
        structure_types = rendered.get("structure_types") or []
        if "table-or-matrix" in structure_types:
            confirmed += count
        elif rendered.get("status") != "passed" or rendered.get("visual_structure_present"):
            confirmed += count
    return confirmed


def _checked_file(path: Path, expected_sha256: Any) -> bool:
    try:
        return (isinstance(expected_sha256, str) and path.is_file()
                and path.stat().st_size > 0 and sha256(path) == expected_sha256)
    except OSError:
        return False


def _has_page_image_link(text: str, page: int) -> bool:
    return bool(re.search(rf"(?m)^!\[[^\]\n]*\]\(\.\./assets/page-{page:03d}\.png\)[ \t]*$", text))


def _visual_page_reusable(
    *, visual_root: Path, source_sha256: str, page: int, require_local_ocr: bool = False,
) -> bool:
    summary = _read_json(visual_root / "summary.json")
    markdown = visual_root / "page-units" / f"p{page:03d}.md"
    result = _read_json(visual_root / "evidence" / f"page-result-p{page:03d}.json")
    if not summary or not result or not markdown.is_file():
        return False
    if require_local_ocr and result.get("route") != LOCAL_PAGE_OCR_ROUTE:
        return False
    completed = summary.get("completed_pages")
    if not isinstance(completed, list):
        completed = [page] if result.get("status") == "passed" else []
    recorded_markdown_sha = (result.get("markdown") or {}).get("current_sha256")
    summary_source = summary.get("source") if isinstance(summary.get("source"), dict) else {}
    summary_source_sha256 = summary.get("source_sha256") or summary_source.get("sha256")
    try:
        markdown_text = markdown.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if require_local_ocr and not (
        _checked_file(visual_root / "assets" / f"page-{page:03d}.png", (result.get("asset") or {}).get("sha256"))
        and _checked_file(visual_root / "evidence" / f"apple-vision-p{page:03d}.json", result.get("ocr_evidence_sha256"))
    ):
        return False
    return bool(
        summary_source_sha256 == source_sha256
        and result.get("source_sha256") == source_sha256
        and result.get("page") == page
        and result.get("status") == "passed"
        and page in completed
        and _page_marker_sequence(markdown_text) == [page]
        and isinstance(recorded_markdown_sha, str)
        and sha256(markdown) == recorded_markdown_sha
    )


def _visual_root_can_resume(visual_root: Path, source_sha256: str) -> bool:
    summary = _read_json(visual_root / "summary.json")
    if not summary:
        return False
    source = summary.get("source") if isinstance(summary.get("source"), dict) else {}
    return bool((summary.get("source_sha256") or source.get("sha256")) == source_sha256)


def _select_visual_attempt(
    *, package: Path, source_sha256: str, page: int, require_local_ocr: bool = False,
) -> tuple[Path, bool]:
    """Choose a reusable/resumable attempt without deleting partial evidence."""

    candidates = [package / "visual", *sorted(package.glob("visual-attempt-*"))]
    for candidate in candidates:
        if _visual_page_reusable(
            visual_root=candidate,
            source_sha256=source_sha256,
            page=page,
            require_local_ocr=require_local_ocr,
        ):
            return candidate, True
    if not require_local_ocr:
        for candidate in reversed(candidates):
            if _visual_root_can_resume(candidate, source_sha256):
                return candidate, True
    preferred = package / "visual"
    if not preferred.exists() or not any(preferred.iterdir()):
        return preferred, False
    ordinal = 2
    while True:
        attempt = package / f"visual-attempt-{ordinal:03d}"
        if not attempt.exists() or not any(attempt.iterdir()):
            return attempt, False
        ordinal += 1


def _copy_visual_asset(visual_root: Path, assets_root: Path, page: int) -> Path | None:
    source = visual_root / "assets" / f"page-{page:03d}.png"
    if not source.is_file():
        return None
    target = assets_root / source.name
    if target.exists():
        if sha256(target) != sha256(source):
            raise ValueError(f"page {page} visual asset conflicts with existing asset")
        return target
    shutil.copy2(source, target)
    return target


def _merge_page_units(page_units_root: Path, selected: list[int]) -> str:
    try:
        import lan_pdf_vision_to_markdown as visual
    except ImportError:  # pragma: no cover - package-style imports in tests/tools
        from . import lan_pdf_vision_to_markdown as visual

    run_root = page_units_root.parent
    parts = []
    for page in selected:
        path = page_units_root / f"p{page:03d}.md"
        text = path.read_text(encoding="utf-8")
        policy = visual.page_reader_image_policy(run_root, page)
        if policy.get("document_reference") is not None:
            text = visual.project_page_image_reference(
                text, page, policy["document_reference"]
            )
        parts.append(text.rstrip())
    return "\n\n".join(parts).rstrip() + "\n"


def _page_marker_sequence(markdown: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(r"<!--\s*page\s*:\s*(\d+)\s*-->", markdown)
    ]


def _markdown_table_count(markdown: str) -> int:
    """Count GFM table delimiter rows as independently rendered tables."""

    count = 0
    lines = markdown.replace("\r\n", "\n").splitlines()
    delimiter = re.compile(
        r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
    )
    for index, line in enumerate(lines):
        if not delimiter.match(line):
            continue
        previous = lines[index - 1] if index else ""
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if "|" in previous and "|" in following:
            count += 1
    return count


def _markdown_page_coverage(
    *, selected: list[int], raw_markdown: str, merged_markdown: str, final_markdown: str
) -> dict[str, Any]:
    raw_sequence = _page_marker_sequence(raw_markdown)
    merged_sequence = _page_marker_sequence(merged_markdown)
    final_sequence = _page_marker_sequence(final_markdown)
    final_counts = {page: final_sequence.count(page) for page in set(final_sequence)}
    selected_set = set(selected)
    checks = {
        "raw_document_page_sequence_matches": raw_sequence == selected,
        "merged_document_page_sequence_matches": merged_sequence == selected,
        "final_index_page_sequence_matches": final_sequence == selected,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "expected_page_count": len(selected),
        "raw_page_count": len(raw_sequence),
        "merged_page_count": len(merged_sequence),
        "final_page_count": len(final_sequence),
        "expected_page_sequence": selected,
        "raw_page_sequence": raw_sequence,
        "merged_page_sequence": merged_sequence,
        "final_page_sequence": final_sequence,
        "missing_pages": [page for page in selected if final_counts.get(page, 0) == 0],
        "duplicate_pages": [page for page in selected if final_counts.get(page, 0) > 1],
        "unexpected_pages": sorted(page for page in final_counts if page not in selected_set),
        "checks": checks,
    }


def _run_page_jobs_continue(
    pages: list[int], worker: Any
) -> list[dict[str, Any]]:
    """Run every page even when an earlier page raises.

    The caller owns the durable failure record and placeholder page-unit.  This
    helper guarantees the control-flow property independently of model/runtime
    details and is intentionally serial so page evidence remains deterministic.
    """

    results: list[dict[str, Any]] = []
    for page in pages:
        try:
            value = worker(page)
            if not isinstance(value, dict):
                raise TypeError("page worker must return a mapping")
            results.append({"page": page, **value})
        except Exception as exc:  # page-level continuation boundary
            results.append(
                {
                    "page": page,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    return results


def _ollama_healthcheck(timeout_seconds: int = 5) -> dict[str, Any]:
    """Bound model availability before a page can block the serial queue."""
    started = time.monotonic()
    try:
        with urlopen(OLLAMA_HEALTH_URL, timeout=timeout_seconds) as response:
            body = response.read()
        payload = json.loads(body)
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ValueError("Ollama health response has no models list")
        return {
            "status": "passed",
            "url": OLLAMA_HEALTH_URL,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "models": [str(item.get("name")) for item in models if isinstance(item, dict)],
        }
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "url": OLLAMA_HEALTH_URL,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _reusable_routed_page(
    *,
    page: int,
    source_sha256: str,
    page_unit_path: Path,
    route_record_path: Path,
) -> dict[str, Any] | None:
    record = _read_json(route_record_path)
    if not record:
        return None
    route = record.get("route")
    signals = record.get("signals") or {}
    if LEGACY_FULL_PAGE_TEXT_REASON in (record.get("reason_codes") or []):
        return None  # Route the page again; the raw native extraction can still be reused.
    images = record.get("image_stats") or {}
    rendered = record.get("rendered_structure") or {}
    if not signals.get("native_text_visibility") and (
        (record.get("native_text_chars") or 0) > 0
        and (images.get("image_count") or 0) > 0
        and (images.get("max_area_ratio") or 0) >= page_router.FULL_PAGE_BACKGROUND_MIN_AREA_RATIO
        and not (rendered.get("status") == "passed" and rendered.get("visual_structure_present") is True)
    ):
        return None
    if (
        record.get("page") != page
        or record.get("source_sha256") != source_sha256
        or route not in page_router.PAGE_ROUTES
        or record.get("status") != "passed"
        or not page_unit_path.is_file()
    ):
        return None
    recorded_page_unit_sha = record.get("page_unit_sha256")
    if not isinstance(recorded_page_unit_sha, str) or sha256(page_unit_path) != recorded_page_unit_sha:
        return None
    page_unit_text = page_unit_path.read_text(encoding="utf-8")
    markers = _page_marker_sequence(page_unit_text)
    if markers != [page]:
        return None
    native_evidence = Path(str(record.get("native_evidence") or ""))
    native_evidence_sha = record.get("native_evidence_sha256")
    if (
        not native_evidence.is_file()
        or not isinstance(native_evidence_sha, str)
        or sha256(native_evidence) != native_evidence_sha
    ):
        return None
    if route in {"native-plus-visual", "visual-only"}:
        visual_page_unit = Path(str(record.get("visual_page_unit") or ""))
        visual_page_unit_sha = record.get("visual_page_unit_sha256")
        if (
            not visual_page_unit.is_file()
            or not isinstance(visual_page_unit_sha, str)
            or sha256(visual_page_unit) != visual_page_unit_sha
        ):
            return None
    if signals.get("page_image_required") is True and not (
        route in {"visual-only", "native-plus-visual"}
        and record.get("recognition_route") == LOCAL_PAGE_OCR_ROUTE
        and _has_page_image_link(page_unit_text, page)
        and _checked_file(page_unit_path.parent.parent / "assets" / f"page-{page:03d}.png", record.get("visual_asset_sha256"))
        and _visual_page_reusable(
            visual_root=Path(str(record.get("visual_run_root") or "")),
            source_sha256=source_sha256, page=page, require_local_ocr=True,
        )
    ):
        return None
    return {
        **record,
        "model_previously_called": bool(record.get("model_called")),
        "model_called": False,
        "resume_reused": True,
    }


def _visual_supplement_body(page: int, native_text: str, visual_markdown: str) -> str:
    """Keep visual structure while removing wrapper and exact native repeats."""

    native_compact = re.sub(r"\s+", "", native_text)
    kept: list[str] = []
    for line in visual_markdown.replace("\r\n", "\n").splitlines():
        stripped = line.strip()
        if re.fullmatch(r"<!--\s*page\s*:\s*\d+\s*-->", stripped):
            continue
        if re.fullmatch(rf"##\s+第\s*{page}\s*页", stripped):
            continue
        compact = re.sub(r"[\s`*_>#-]+", "", stripped)
        structural = stripped.startswith(("|", "![]", "![", "```", "- ", "* ", ">"))
        if compact and not structural and len(compact) >= 3 and compact in native_compact:
            continue
        kept.append(line.rstrip())
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept).strip()


def _merge_page_route_unit(
    *, page: int, route: str, native_text: str, visual_markdown: str | None
) -> str:
    if route not in page_router.PAGE_ROUTES:
        raise ValueError(f"unsupported page route: {route}")
    native_unit = _page_unit_text(page, native_text)
    if route == "native-only":
        return native_unit
    if not visual_markdown or not visual_markdown.strip():
        raise ValueError(f"page {page} route {route} has no visual Markdown")
    # A visual-only route owns the whole page. Do not subtract a short or
    # partial native layer from its OCR output, because identical OCR text is
    # still the authoritative visual transcription for this route.
    supplement = _visual_supplement_body(
        page,
        "" if route == "visual-only" else native_text,
        visual_markdown,
    )
    if route == "native-plus-visual":
        if not supplement:
            # The visual worker may confirm exactly the same text as the native
            # layer. Preserve the copied page asset instead of treating "no new
            # text" as conversion failure.
            image = f"![第{page}页原始页面图](../assets/page-{page:03d}.png)"
            return native_unit.rstrip() + "\n\n" + image + "\n"
        return native_unit.rstrip() + "\n\n### 视觉补充\n\n" + supplement + "\n"

    # visual-only still owns exactly one page marker.  The visual worker may
    # include its own page heading, which is preserved after wrapper removal.
    if not supplement:
        raise ValueError(f"page {page} visual-only Markdown is empty")
    return f"<!-- page:{page} -->\n\n{supplement}\n"


def _incomplete_page_unit(page: int, native_text: str = "") -> str:
    body = native_text.rstrip("\n")
    if body:
        body += "\n\n"
    return (
        f"<!-- page:{page} -->\n\n{body}"
        "*本页转换尚未完成，需依据页面路线与失败证据继续处理。*\n"
    )


def _page_completion_report(
    *, selected: list[int], page_results: list[dict[str, Any]]
) -> dict[str, Any]:
    observed_pages = [
        int(item["page"])
        for item in page_results
        if isinstance(item, dict) and isinstance(item.get("page"), int)
    ]
    observed_counts = {page: observed_pages.count(page) for page in set(observed_pages)}
    duplicate = sorted(page for page, count in observed_counts.items() if count > 1)
    unexpected = sorted(page for page in observed_counts if page not in set(selected))
    by_page = {
        int(item["page"]): item
        for item in page_results
        if isinstance(item, dict) and isinstance(item.get("page"), int)
    }
    completed = [
        page for page in selected if (by_page.get(page) or {}).get("status") == "passed"
    ]
    reused = [
        page for page in completed if bool((by_page.get(page) or {}).get("resume_reused"))
    ]
    failed = [
        page for page in selected if (by_page.get(page) or {}).get("status") == "failed"
    ]
    needs_review = [
        page
        for page in selected
        if (by_page.get(page) or {}).get("status") == "needs-review"
    ]
    incomplete = [page for page in selected if page not in completed]
    missing = [page for page in selected if page not in by_page]
    return {
        "status": "passed" if not incomplete and not duplicate and not unexpected else "needs-review",
        "expected_pages": selected,
        "completed_pages": completed,
        "reused_pages": reused,
        "failed_pages": failed,
        "needs_review_pages": needs_review,
        "missing_pages": missing,
        "duplicate_pages": duplicate,
        "unexpected_pages": unexpected,
        "incomplete_pages": incomplete,
        "counts": {
            "expected": len(selected),
            "completed": len(completed),
            "reused": len(reused),
            "failed": len(failed),
            "needs_review": len(needs_review),
            "missing": len(missing),
            "duplicate": len(duplicate),
            "unexpected": len(unexpected),
        },
    }


def _background_process_matches(source: Path | None = None, run_root: Path | None = None) -> list[str]:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=30,
    )
    patterns = (
        "lan_pdf_to_markdown.py",
        "run_pdf_batches.py",
        "page_markdown_noise_cleanup.py",
        "lan_markdown_heading_repair.py",
        "qwen3-vl",
        "lan_pdf_vision_to_markdown.py",
    )
    anchors = {str(path) for path in (source, run_root) if path is not None}
    if source is not None:
        anchors.add(source.name)
    current_pid = os.getpid()
    matches = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            pid_raw, command = line.split(None, 1)
            pid = int(pid_raw)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if anchors and not any(anchor in command for anchor in anchors):
            continue
        if any(pattern in command for pattern in patterns):
            matches.append(line)
    return matches


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


READER_VISUAL_STAGE = "reader_visual_projection"


def _finalize_hybrid_reader(
    root: Path, source: Path, selected: list[int], *, previously_verified: bool = False,
) -> dict[str, Any]:
    """Publish the internal copy once; never regenerate committed reader evidence."""
    validation_path = root / hybrid_validator.READER_VALIDATION
    if previously_verified or hybrid_validator.path_present(validation_path):
        return hybrid_validator.validate_reader_stage(root, source, selected)

    try:
        hybrid_validator.check_reader_paths(root)
        audit_worker, export_worker = hybrid_validator.reader_components()
        audit_path = root / hybrid_validator.READER_AUDIT
        destination = root / hybrid_validator.READER_PROJECTION
        if destination.exists() and not audit_path.is_file():
            raise ValueError("reader projection exists without its audit; refusing to replace it")

        # Export into a disjoint directory on the same filesystem. Only the
        # fixed reader/ path is published inside the engineering package.
        with tempfile.TemporaryDirectory(prefix=".reader-visual-", dir=root.parent) as temporary:
            staging = Path(temporary)
            pending_audit = None if audit_path.is_file() else staging / audit_path.name
            if pending_audit is not None:
                lock = (_read_json(root / "acceptance.json") or {}).get(hybrid_validator.READER_AUDIT_VERSION)
                scan_options = {"collect_scan": True} if lock == "3.0.0" else {}
                _write_json_atomic(pending_audit, audit_worker.audit_package(
                    root, source, selected_pages=selected, **scan_options))
            audit, audit_sha = hybrid_validator.read_reader_audit(pending_audit or audit_path)
            audit_check = audit_worker.validate_audit(root, source, audit, selected_pages=selected)
            if audit_check.get("status") != "passed":
                return {"status": "failed", "bytes_verified": False, "audit_validation": audit_check}
            if pending_audit is not None:
                pending_audit.replace(audit_path)

            if not destination.is_dir():
                staged_destination = staging / destination.name
                exported = export_worker.export_reader(root, audit, staged_destination)
                if isinstance(exported, dict) and exported.get("status") != "passed":
                    return {"status": "failed", "bytes_verified": False, "reader_export": exported}
                staged_destination.rename(destination)
            reader_check = export_worker.validate_reader(root, audit, destination)
            if reader_check.get("status") != "passed":
                return {"status": "failed", "bytes_verified": False, "reader_validation": reader_check}
            acceptance = hybrid_validator.reader_acceptance(audit_sha, audit, reader_check)
            pending_validation = staging / validation_path.name
            _write_json_atomic(pending_validation, acceptance)
            pending_validation.replace(validation_path)
            return acceptance
    except (OSError, ValueError, ImportError, RuntimeError, KeyError) as exc:
        return {"status": "failed", "bytes_verified": False, "error": str(exc)}


def _record_hybrid_reader_state(
    root: Path, summary: dict[str, Any], progress: dict[str, Any], evidence: dict[str, Any], reader: dict[str, Any],
) -> None:
    stage = {
        "stage": READER_VISUAL_STAGE,
        "committed": reader.get("bytes_verified") is True or hybrid_validator.reader_stage_committed(summary, progress, evidence),
        "projection": hybrid_validator.READER_PROJECTION.as_posix(),
        "public_delivery": False,
        "audit_report": hybrid_validator.READER_AUDIT.as_posix(),
        "validation_report": hybrid_validator.READER_VALIDATION.as_posix(),
        **reader,
    }
    # Batch consolidation uses the last engineering stage as its completion
    # key. The post-gate internal projection has its own checkpoint.
    for filename, record in (("progress.json", progress), ("summary.json", summary), ("evidence.json", evidence)):
        record.update(status=reader["status"], engineering_status="passed", reader_visual=stage)
        path = root / filename
        if _read_json(path) != record:
            _write_json_atomic(path, record)


def _page_batch_result(root: Path, run_id: str, summary: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return (0 if summary["status"] == "passed" else 1), {
        "status": summary["status"], "run_id": run_id, "output": str(root),
        "summary": summary, "checkpoint": str(root / "progress.json"),
    }


def _resume_accepted_hybrid(
    root: Path, run_id: str, source: Path, source_hash: str, selected: list[int],
) -> tuple[int, dict[str, Any]] | None:
    acceptance = _read_json(root / "acceptance.json") or {}
    expected = hybrid_validator.reader_stage_expected(root)
    if not expected and acceptance.get("status") != "passed":
        return None  # Conversion has not reached the engineering gate.

    summary = _read_json(root / "summary.json") or {}
    progress = _read_json(root / "progress.json") or {}
    evidence = _read_json(root / "evidence.json") or {}
    if (acceptance.get("status") != "passed" or acceptance.get("source_sha256") != source_hash
            or acceptance.get("selected_pages") != selected or not summary
            or not isinstance(progress.get("stages"), list) or not evidence):
        return _page_batch_result(root, run_id, {**summary, "status": "failed",
            "error": "accepted hybrid checkpoint is incomplete or does not match the source and pages"})
    engineering = hybrid_validator.validate_engineering_package(root, source)
    if engineering["status"] != "passed":
        return _page_batch_result(root, run_id, {**summary, "status": "failed",
            "engineering_status": "failed", "engineering_validation": engineering})
    if expected:
        previously_verified = hybrid_validator.reader_stage_committed(summary, progress, evidence)
        reader = _finalize_hybrid_reader(root, source, selected, previously_verified=previously_verified)
        _record_hybrid_reader_state(root, summary, progress, evidence, reader)
    return _page_batch_result(root, run_id, summary)


@resource_slots.uses_local_pdf_slot("bounded-pdf-page-batches")
def run_page_batches(
    source: Path,
    output_root: Path,
    pages: str = "all",
    smoke_pages: str | None = None,
    batch_size: int = 8,
    resume_record: Path | None = None,
    timeout: int = 1800,
    context_mode: str = "auto",
    context_window: int = 1,
    resume_package: Path | None = None,
    hybrid: bool = False,
    visual_dpi: int = 220,
) -> tuple[int, dict[str, Any]]:
    """Run one long document through smoke + bounded page batches.

    In hybrid mode native evidence is always collected or reused first, then a
    corroborated page decision selects native-only, native-plus-visual, or
    visual-only.  Page failures are recorded and later pages still run.
    """
    source = source.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if hybrid and not 200 <= visual_dpi <= 250:
        raise ValueError("visual_dpi must be within 200..250 for hybrid pages")
    output_root.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(["pdfinfo", str(source)], capture_output=True, text=True, timeout=60)
    pages_total = next((int(line.split(":", 1)[1].strip()) for line in probe.stdout.splitlines() if line.startswith("Pages:")), 0)
    if probe.returncode or not pages_total:
        raise ValueError(f"cannot determine PDF page count: {probe.stderr.strip()}")
    selected = parse_page_selector(pages, pages_total)
    smoke = parse_page_selector(smoke_pages, pages_total) if smoke_pages else []
    if smoke and not set(smoke).issubset(set(selected)):
        raise ValueError("smoke pages must be within selected pages")
    started = time.monotonic()
    source_hash = sha256(source)
    if resume_package is not None:
        batch_root = resume_package.expanduser().resolve()
        if not batch_root.is_dir():
            raise ValueError(f"resume package does not exist: {batch_root}")
        run_id = batch_root.name
        if hybrid:
            resumed = _resume_accepted_hybrid(batch_root, run_id, source, source_hash, selected)
            if resumed is not None:
                return resumed
    else:
        run_id, batch_root = resolve_package_target(output_root, source)
    pages_root = batch_root / "pages"
    page_units_root = batch_root / "page-units"
    assets_root = batch_root / "assets"
    reports_root = batch_root / "reports"
    pages_root.mkdir(parents=True, exist_ok=True)
    page_units_root.mkdir(exist_ok=True)
    assets_root.mkdir(exist_ok=True)
    reports_root.mkdir(exist_ok=True)
    page_routes_root = batch_root / "evidence" / "page-routes"
    page_routes_root.mkdir(parents=True, exist_ok=True)
    page_python = (
        str(BUNDLED_PDF_PYTHON)
        if BUNDLED_PDF_PYTHON.is_file()
        else sys.executable
    )
    # Per-page native extraction depends on the bundled runtime's PDF stack,
    # while the deterministic cleanup helpers need the host PyYAML install.
    helper_python = sys.executable
    page_size_match = re.search(
        r"^Page size:\s*([0-9.]+)\s+x\s+([0-9.]+)\s+pts",
        probe.stdout,
        re.MULTILINE,
    )
    page_size = (
        (float(page_size_match.group(1)), float(page_size_match.group(2)))
        if page_size_match
        else None
    )
    image_stats, image_probe = (
        _collect_page_image_stats(source, selected, page_size=page_size)
        if hybrid
        else ({}, {"status": "not-run", "reason": "native-only-policy"})
    )
    plan = {
        "format_version": HYBRID_FORMAT_VERSION if hybrid else FORMAT_VERSION,
        "run_id": run_id,
        "source": str(source),
        "selected_pages": selected,
        "total_pages": pages_total,
        "batch_size": batch_size,
        "context_mode": "independent",
        "route_policy": "page-hybrid" if hybrid else "native-only",
        "page_routes": list(page_router.PAGE_ROUTES) if hybrid else ["native-only"],
        "model_policy": "visual-model-only-for-routed-pages" if hybrid else "no-model",
        "visual_policy": "conditional-page-supplement" if hybrid else "not-run",
        "visual_dpi": visual_dpi if hybrid else None,
        "image_object_probe": image_probe,
        "output_layout": {
            "run_root": str(batch_root),
            "assets": "assets/",
            "page_units": "page-units/",
            "raw_document": "document-temp.raw.md",
            "merged_document": "document-temp.md",
            "final_markdown": "index.md",
            "source_markdown_alias": None,
            "final_summary": "summary.json",
            "final_progress": "progress.json",
            "final_evidence": "evidence.json",
            "final_acceptance": "acceptance.json",
        },
    }
    _write_json_atomic(batch_root / "plan.json", plan)

    def _failed(stage: str, details: dict[str, Any], *, status: str = "failed") -> tuple[int, dict[str, Any]]:
        details = dict(details)
        if hybrid:
            durable_routes = [
                record
                for page in selected
                if (record := _read_json(page_routes_root / f"p{page:03d}.json"))
            ]
            page_completion = _page_completion_report(
                selected=selected,
                page_results=durable_routes,
            )
            _write_json_atomic(reports_root / "page-completion.json", page_completion)
            details.setdefault("page_completion", page_completion)
            details.setdefault(
                "page_completion_report",
                str(reports_root / "page-completion.json"),
            )
        state = {
            "format_version": HYBRID_FORMAT_VERSION if hybrid else FORMAT_VERSION,
            "status": status,
            "stage": stage,
            "run_id": run_id,
            "source": str(source),
            "source_sha256": source_hash,
            "selected_pages": selected,
            "total_pages": pages_total,
            **details,
        }
        _write_json_atomic(batch_root / "progress.json", state)
        _write_json_atomic(batch_root / "summary.json", state)
        _write_json_atomic(batch_root / "evidence.json", details)
        return 1, {
            "status": status,
            "run_id": run_id,
            "output": str(batch_root),
            "summary": state,
            "checkpoint": str(batch_root / "progress.json"),
        }

    records: list[dict[str, Any]] = []
    page_route_results: list[dict[str, Any]] = []
    native_page_results: list[dict[str, Any]] = []
    existing_pages: set[int] = set()
    if hybrid:
        def process_hybrid_page(page: int) -> dict[str, Any]:
            page_unit_path = page_units_root / f"p{page:03d}.md"
            route_path = page_routes_root / f"p{page:03d}.json"
            reusable = _reusable_routed_page(
                page=page,
                source_sha256=source_hash,
                page_unit_path=page_unit_path,
                route_record_path=route_path,
            )
            if reusable:
                _write_json_atomic(route_path, reusable)
                return reusable

            package, summary_path, evidence_path = _page_package_paths(pages_root, page)
            native_summary = _read_json(summary_path)
            native_evidence = _read_json(evidence_path)
            native_reused = bool(
                native_summary
                and native_evidence
                and native_summary.get("source_sha256") == source_hash
                and native_evidence.get("source_sha256") == source_hash
                and native_summary.get("status") in {"passed", "needs-review"}
                and native_evidence.get("status") in {"passed", "needs-review"}
            )
            if native_reused:
                records.append(
                    {
                        "command": ["reused-existing-native-page", str(page)],
                        "status": "complete",
                        "returncode": 0,
                        "page": page,
                        "summary": native_summary,
                        "resume_reused": True,
                    }
                )
            else:
                archived_native_package = _archive_incomplete_page_package(
                    pages_root, page
                )
                command = _native_page_command(
                    page_python=page_python,
                    source=source,
                    pages_root=pages_root,
                    page=page,
                )
                native_record = _run_command(command, timeout)
                native_summary = _summary_from_stdout(native_record.get("stdout", ""))
                native_record.update(
                    {
                        "page": page,
                        "summary": native_summary,
                        "archived_incomplete_page_package": (
                            str(archived_native_package)
                            if archived_native_package
                            else None
                        ),
                    }
                )
                records.append(native_record)
                package, summary_path, evidence_path = _page_package_paths(pages_root, page)
                native_evidence = _read_json(evidence_path)
                if (
                    not native_summary
                    or native_summary.get("status") not in {"passed", "needs-review"}
                    or not native_evidence
                    or native_evidence.get("status") not in {"passed", "needs-review"}
                ):
                    _write_text_atomic(page_unit_path, _incomplete_page_unit(page))
                    failed = {
                        "format_version": HYBRID_FORMAT_VERSION,
                        "page": page,
                        "source_sha256": source_hash,
                        "route": "unresolved",
                        "status": "failed",
                        "reason_codes": ["native-extraction-failed"],
                    }
                    _write_json_atomic(route_path, failed)
                    return failed

            assert native_evidence is not None
            native_text = str(native_evidence.get("text") or "")
            decision = _page_route_decision(
                source=source,
                page=page,
                native_evidence=native_evidence,
                images=image_stats.get(page, page_router.PageImageStats()),
                image_probe=image_probe,
            )
            route_record = {
                "format_version": HYBRID_FORMAT_VERSION,
                "source": str(source),
                "source_sha256": source_hash,
                "native_evidence": str(evidence_path),
                "native_evidence_sha256": sha256(evidence_path),
                "native_resume_reused": native_reused,
                **decision,
            }
            if decision["route"] == "native-only":
                _write_text_atomic(
                    page_unit_path,
                    _merge_page_route_unit(
                        page=page,
                        route="native-only",
                        native_text=native_text,
                        visual_markdown=None,
                    ),
                )
                route_record.update(
                    {
                        "status": decision["status"],
                        "model_called": False,
                        "page_unit": str(page_unit_path),
                        "page_unit_sha256": sha256(page_unit_path),
                    }
                )
                _write_json_atomic(route_path, route_record)
                return route_record

            require_local_ocr = decision["signals"].get("page_image_required") is True
            visual_root, visual_can_resume = _select_visual_attempt(
                package=package,
                source_sha256=source_hash,
                page=page,
                require_local_ocr=require_local_ocr,
            )
            visual_reused = _visual_page_reusable(
                visual_root=visual_root,
                source_sha256=source_hash,
                page=page,
                require_local_ocr=require_local_ocr,
            )
            visual_command, recognition_route = _visual_page_command(
                helper_python=helper_python,
                source=source,
                visual_root=visual_root,
                page=page,
                visual_dpi=visual_dpi,
                timeout=timeout,
                can_resume=visual_can_resume,
                decision=decision,
            )
            if visual_reused:
                saved_visual = _read_json(
                    visual_root / "evidence" / f"page-result-p{page:03d}.json"
                ) or {}
                recognition_route = (
                    LOCAL_PAGE_OCR_ROUTE
                    if saved_visual.get("route") == LOCAL_PAGE_OCR_ROUTE
                    else "lan-visual-model"
                )

            def visual_failure(reason: str) -> dict[str, Any]:
                fallback_text = "" if require_local_ocr and decision["route"] == "visual-only" else native_text
                _write_text_atomic(page_unit_path, _incomplete_page_unit(page, fallback_text))
                route_record.update(
                    status="failed",
                    model_called=not visual_reused and recognition_route == "lan-visual-model",
                    recognition_route=recognition_route,
                    reason_codes=[*decision["reason_codes"], reason],
                    page_unit=str(page_unit_path),
                    page_unit_sha256=sha256(page_unit_path),
                )
                _write_json_atomic(route_path, route_record)
                return route_record

            if not visual_reused:
                model_health = (
                    _ollama_healthcheck()
                    if recognition_route == "lan-visual-model"
                    else {"status": "not-required"}
                )
                if model_health.get("status") == "failed":
                    visual_command = _apple_ocr_page_command(
                        helper_python=helper_python,
                        source=source,
                        visual_root=visual_root,
                        page=page,
                        visual_dpi=visual_dpi,
                        timeout=timeout,
                    )
                    recognition_route = "apple-vision-ocr-fallback"
                visual_record = _run_command(visual_command, timeout)
                visual_record["model_health"] = model_health
                visual_result = _summary_from_stdout(visual_record.get("stdout", ""))
                visual_record.update(
                    {"page": page, "route": decision["route"], "summary": visual_result}
                )
                records.append(visual_record)
                if (
                    visual_record.get("returncode") != 0
                    or not visual_result
                    or visual_result.get("status") != "passed"
                ):
                    return visual_failure("visual-page-conversion-failed")
                if require_local_ocr and not _visual_page_reusable(
                    visual_root=visual_root, source_sha256=source_hash, page=page, require_local_ocr=True,
                ):
                    return visual_failure("required-page-image-or-local-ocr-evidence-unverified")

            visual_unit_path = visual_root / "page-units" / f"p{page:03d}.md"
            visual_markdown = visual_unit_path.read_text(encoding="utf-8")
            try:
                asset = _copy_visual_asset(visual_root, assets_root, page)
            except (OSError, ValueError):
                if require_local_ocr:
                    return visual_failure("required-page-image-copy-failed")
                raise
            merged_unit = _merge_page_route_unit(
                page=page, route=decision["route"], native_text=native_text, visual_markdown=visual_markdown,
            )
            if require_local_ocr:
                if asset is None:
                    return visual_failure("required-page-image-not-materialized")
                if not _has_page_image_link(merged_unit, page):
                    # The standalone OCR worker may omit its reader link for a
                    # text-heavy page. Keep its evidence and publish our copy.
                    marker, _, body = merged_unit.partition("\n\n")
                    merged_unit = f"{marker}\n\n![第{page}页原始页面图](../assets/{asset.name})\n\n{body}"
            _write_text_atomic(page_unit_path, merged_unit)
            route_record.update(
                {
                    "status": "passed",
                    "reason_codes": [
                        *route_record["reason_codes"],
                        *(
                            ["lan-visual-model-unavailable-apple-ocr-fallback"]
                            if recognition_route == "apple-vision-ocr-fallback"
                            else []
                        ),
                    ],
                    "model_called": (
                        not visual_reused and recognition_route == "lan-visual-model"
                    ),
                    "recognition_route": recognition_route,
                    "visual_resume_reused": visual_reused,
                    "visual_run_root": str(visual_root),
                    "visual_page_unit": str(visual_unit_path),
                    "visual_page_unit_sha256": sha256(visual_unit_path),
                    "visual_asset": str(asset) if asset else None,
                    "visual_asset_sha256": sha256(asset) if asset else None,
                    "page_unit": str(page_unit_path),
                    "page_unit_sha256": sha256(page_unit_path),
                }
            )
            _write_json_atomic(route_path, route_record)
            return route_record

        page_route_results = _run_page_jobs_continue(selected, process_hybrid_page)
        for result in page_route_results:
            page = int(result["page"])
            unit = page_units_root / f"p{page:03d}.md"
            route_path = page_routes_root / f"p{page:03d}.json"
            if not unit.is_file():
                _write_text_atomic(unit, _incomplete_page_unit(page))
            if result.get("status") == "failed" and not route_path.is_file():
                _write_json_atomic(
                    route_path,
                    {
                        "format_version": HYBRID_FORMAT_VERSION,
                        "page": page,
                        "source_sha256": source_hash,
                        "route": "unresolved",
                        **result,
                    },
                )
    else:
        for page in selected:
            package, summary_path, evidence_path = _page_package_paths(pages_root, page)
            if not summary_path.is_file() or not evidence_path.is_file():
                continue
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if summary.get("status") != "passed" or evidence.get("status") != "passed":
                continue
            existing_pages.add(page)
            records.append(
                {
                    "command": ["reused-existing-page", str(page)],
                    "started_at": None,
                    "finished_at": None,
                    "status": "complete",
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "elapsed_seconds": 0.0,
                    "page": page,
                    "summary": summary,
                    "resume_reused": True,
                }
            )

        def process_native_page(page: int) -> dict[str, Any]:
            if page in existing_pages:
                return {
                    "page": page,
                    "route": "native-only",
                    "status": "passed",
                    "resume_reused": True,
                }
            archived_native_package = _archive_incomplete_page_package(
                pages_root, page
            )
            command = _native_page_command(
                page_python=page_python,
                source=source,
                pages_root=pages_root,
                page=page,
            )
            record = _run_command(command, timeout)
            summary = _summary_from_stdout(record.get("stdout", ""))
            record["page"] = page
            record["summary"] = summary
            record["archived_incomplete_page_package"] = (
                str(archived_native_package) if archived_native_package else None
            )
            records.append(record)
            if (
                record.get("returncode") != 0
                or not summary
                or summary.get("status") != "passed"
            ):
                _write_text_atomic(
                    page_units_root / f"p{page:03d}.md",
                    _incomplete_page_unit(page),
                )
                return {
                    "page": page,
                    "route": "native-only",
                    "status": "failed",
                    "reason_codes": ["native-extraction-failed"],
                }
            return {"page": page, "route": "native-only", "status": "passed"}

        native_page_results = _run_page_jobs_continue(selected, process_native_page)
        for result in native_page_results:
            page = int(result["page"])
            unit = page_units_root / f"p{page:03d}.md"
            if result.get("status") != "passed" and not unit.is_file():
                _write_text_atomic(unit, _incomplete_page_unit(page))

    line_ids = []
    table_counts = []
    page_source_hashes: set[str] = set()
    native_elapsed_seconds = 0.0
    record_by_page = {int(item["page"]): item for item in records}
    for page in selected:
        package, _, evidence_path = _page_package_paths(pages_root, page)
        evidence = _read_json(evidence_path) or {}
        native_page_result = next(
            (item for item in native_page_results if item.get("page") == page),
            {},
        )
        if not hybrid and native_page_result.get("status") == "passed":
            _write_text_atomic(
                page_units_root / f"p{page:03d}.md",
                _page_unit_text(page, str(evidence.get("text") or "")),
            )
        line_ids.extend(evidence.get("nonempty_line_ids", []))
        table_counts.append(evidence.get("table_count_pdfplumber_find_tables"))
        if isinstance(evidence.get("source_sha256"), str):
            page_source_hashes.add(str(evidence["source_sha256"]))
        page_summary = (record_by_page.get(page) or {}).get("summary") or {}
        try:
            native_elapsed_seconds += float(page_summary.get("elapsed_seconds") or 0)
        except (TypeError, ValueError):
            pass

    raw_document_path = batch_root / "document-temp.raw.md"
    raw_document_text = _merge_page_units(page_units_root, selected)
    _write_text_atomic(raw_document_path, raw_document_text)

    noise_scan_path = reports_root / "page-markdown-noise-cleanup.scan.json"
    noise_scan_record = _run_command(
        [
            helper_python,
            str(NOISE_CLEANUP_ADAPTER),
            "--page-units",
            str(page_units_root),
            "--pages",
            "all",
            "--profile",
            str(DEFAULT_NOISE_PROFILE),
            "--evidence",
            str(noise_scan_path),
        ],
        timeout,
    )
    noise_scan = _summary_from_stdout(noise_scan_record.get("stdout", "")) or _read_json(noise_scan_path)
    if noise_scan_record.get("returncode") != 0 or not noise_scan or noise_scan.get("status") != "passed":
        return _failed(
            "page_markdown_noise_scan",
            {
                "records": records,
                "noise_scan_command": noise_scan_record,
                "noise_scan": noise_scan,
            },
            status="needs-review" if noise_scan_record.get("returncode") == 1
            and (noise_scan or {}).get("status") == "needs-review" else "failed",
        )

    noise_apply_path = reports_root / "page-markdown-noise-cleanup.apply.json"
    noise_apply_record = _run_command(
        [
            helper_python,
            str(NOISE_CLEANUP_ADAPTER),
            "--page-units",
            str(page_units_root),
            "--pages",
            "all",
            "--profile",
            str(DEFAULT_NOISE_PROFILE),
            "--apply",
            "--evidence",
            str(noise_apply_path),
        ],
        timeout,
    )
    noise_apply = _summary_from_stdout(noise_apply_record.get("stdout", "")) or _read_json(noise_apply_path)
    if noise_apply_record.get("returncode") != 0 or not noise_apply or noise_apply.get("status") != "passed":
        return _failed(
            "page_markdown_noise_apply",
            {
                "records": records,
                "noise_scan_command": noise_scan_record,
                "noise_scan": noise_scan,
                "noise_apply_command": noise_apply_record,
                "noise_apply": noise_apply,
            },
        )
    if hybrid:
        for page in selected:
            route_path = page_routes_root / f"p{page:03d}.json"
            route_record = _read_json(route_path)
            page_unit_path = page_units_root / f"p{page:03d}.md"
            if not route_record or not page_unit_path.is_file():
                continue
            route_record["page_unit_sha256"] = sha256(page_unit_path)
            route_record["postprocess"] = {
                "page_markdown_noise_cleanup": "passed",
            }
            _write_json_atomic(route_path, route_record)

    merged_document_path = batch_root / "document-temp.md"
    merged_document_text = _merge_page_units(page_units_root, selected)
    _write_text_atomic(batch_root / "document-temp.pre-heading.md", merged_document_text)
    _write_text_atomic(merged_document_path, merged_document_text)

    heading_record = _run_command(
        [helper_python, str(HEADING_REPAIR_ADAPTER), "--run-root", str(batch_root), "--no-model"],
        timeout,
    )
    heading_report = _summary_from_stdout(heading_record.get("stdout", "")) or _read_json(reports_root / "heading-repair.json")
    if heading_record.get("returncode") != 0 or not heading_report or heading_report.get("status") != "passed":
        return _failed(
            "heading_repair",
            {
                "records": records,
                "noise_scan_command": noise_scan_record,
                "noise_scan": noise_scan,
                "noise_apply_command": noise_apply_record,
                "noise_apply": noise_apply,
                "heading_command": heading_record,
                "heading_report": heading_report,
            },
            status="needs-review" if heading_record.get("returncode") == 1
            and (heading_report or {}).get("status") == "needs-review" else "failed",
        )

    duplicate_line_ids = sorted({item for item in line_ids if line_ids.count(item) > 1})
    table_count = sum(item for item in table_counts if isinstance(item, int))
    if hybrid:
        page_route_results = [
            _read_json(page_routes_root / f"p{page:03d}.json")
            or {"page": page, "status": "failed", "route": "unresolved"}
            for page in selected
        ]
    completion = _page_completion_report(
        selected=selected,
        page_results=[item for item in page_route_results if isinstance(item, dict)],
    ) if hybrid else _page_completion_report(
        selected=selected,
        page_results=native_page_results,
    )
    _write_json_atomic(reports_root / "page-completion.json", completion)
    final_index = batch_root / "index.md"
    if not final_index.is_file() or final_index.stat().st_size == 0:
        return _failed(
            "final_markdown_missing",
            {
                "records": records,
                "heading_command": heading_record,
                "heading_report": heading_report,
            },
        )

    final_markdown_text = final_index.read_text(encoding="utf-8")
    page_coverage = _markdown_page_coverage(
        selected=selected,
        raw_markdown=raw_document_text,
        merged_markdown=merged_document_path.read_text(encoding="utf-8"),
        final_markdown=final_markdown_text,
    )
    page_sequence_checks = page_coverage["checks"]
    if page_coverage["status"] != "passed":
        return _failed(
            "final_markdown_page_coverage",
            page_coverage,
        )

    removed_page_aliases = _remove_redundant_page_aliases(pages_root, source)
    removed_source_aliases = _remove_stale_source_aliases(
        batch_root,
        source,
    )

    expected_pages = list(range(1, pages_total + 1))
    background_processes = _background_process_matches(source, batch_root)
    page_units_exist = all((page_units_root / f"p{page:03d}.md").is_file() for page in selected)
    model_calls_empty = all(not (((item.get("summary") or {}).get("model_calls")) or []) for item in records)
    visual_model_not_run = all(((item.get("summary") or {}).get("visual_model") == "not-run") for item in records)
    source_hash_consistent = page_source_hashes == {source_hash}
    coverage_complete = selected == expected_pages
    rendered_table_count = _markdown_table_count(final_markdown_text)
    confirmed_table_count = (
        _confirmed_table_count(page_route_results) if hybrid else table_count
    )
    table_pages_preserved_as_images = bool(hybrid) and all(
        not (
            isinstance((item.get("table_detection") or {}).get("pdfplumber_count"), int)
            and (item.get("table_detection") or {}).get("pdfplumber_count") > 0
            and (
                (item.get("rendered_structure") or {}).get("status") != "passed"
                or (item.get("rendered_structure") or {}).get("visual_structure_present")
            )
        )
        or (
            item.get("status") == "passed"
            and str(item.get("recognition_route") or "").startswith("apple-vision-ocr")
            and Path(str(item.get("visual_asset") or "")).is_file()
            and Path(str(item.get("visual_asset"))).stat().st_size > 0
        )
        for item in page_route_results
    )
    table_count_match = (
        rendered_table_count >= confirmed_table_count or table_pages_preserved_as_images
    )
    independent_validation = None
    if hybrid:
        independent_validation = hybrid_validator.validate_engineering_package(batch_root, source)
        _write_json_atomic(reports_root / "validation-result.json", independent_validation)
    route_counts = {
        route: sum(1 for item in page_route_results if item.get("route") == route)
        for route in page_router.PAGE_ROUTES
    } if hybrid else {"native-only": len(selected), "native-plus-visual": 0, "visual-only": 0}
    model_call_pages = sorted(
        int(item["page"])
        for item in page_route_results
        if item.get("model_called") and isinstance(item.get("page"), int)
    ) if hybrid else []
    native_only_model_free = all(
        not item.get("model_called")
        for item in page_route_results
        if item.get("route") == "native-only"
    ) if hybrid else model_calls_empty
    route_records_complete = all(
        (page_routes_root / f"p{page:03d}.json").is_file()
        and (record.get("page") == page)
        and (record.get("source_sha256") == source_hash)
        and record.get("status") in {"passed", "needs-review", "failed"}
        and (
            record.get("route") in page_router.PAGE_ROUTES
            or (record.get("status") == "failed" and record.get("route") == "unresolved")
        )
        for page, record in zip(selected, page_route_results)
    ) if hybrid else True
    hard_checks = [
        coverage_complete,
        not duplicate_line_ids,
        page_units_exist,
        source_hash_consistent,
        native_only_model_free,
        route_records_complete,
        not completion.get("duplicate_pages"),
        not completion.get("unexpected_pages"),
        not background_processes,
        bool(noise_scan and noise_scan.get("status") == "passed"),
        bool(noise_apply and noise_apply.get("status") == "passed"),
        bool(heading_report and heading_report.get("status") == "passed"),
        all(page_sequence_checks.values()),
        table_count_match,
    ]
    if not hybrid:
        hard_checks.extend([model_calls_empty, visual_model_not_run])
    elif completion["status"] == "passed":
        hard_checks.append(independent_validation["status"] == "passed")
    if all(hard_checks) and completion["status"] == "passed":
        summary_status = "passed"
    elif all(hard_checks):
        summary_status = "needs-review"
    else:
        summary_status = "failed"

    summary = {
        "format_version": HYBRID_FORMAT_VERSION if hybrid else FORMAT_VERSION,
        "status": summary_status,
        "run_id": run_id,
        "run_root": str(batch_root),
        "source": str(source),
        "source_sha256": source_hash,
        "selected_pages": selected,
        "total_pages": pages_total,
        "batch_size": batch_size,
        "page_batches": len(split_page_batches(selected, batch_size)),
        "native_text_lines": len(line_ids),
        "missing_line_ids": [],
        "duplicate_line_ids": duplicate_line_ids,
        "table_count_pdfplumber_find_tables": table_count,
        "confirmed_table_count": confirmed_table_count,
        "rendered_table_count": rendered_table_count,
        "route_policy": "page-hybrid" if hybrid else "native-only",
        "route_counts": route_counts,
        "page_routes": page_route_results if hybrid else [],
        "page_completion": completion,
        "page_completion_report": str(reports_root / "page-completion.json"),
        "model_calls": [{"page": page, "purpose": "page-visual-supplement"} for page in model_call_pages],
        "visual_model": "page-routed" if hybrid else "not-run",
        "rendering": "candidate-pages-only" if hybrid else "not-run",
        "markdown": str(final_index),
        "index_markdown": str(final_index),
        "source_named_markdown": None,
        "document_temp": str(merged_document_path),
        "document_temp_raw": str(raw_document_path),
        "page_units": str(page_units_root),
        "merged_page_count": page_coverage["merged_page_count"],
        "final_page_count": page_coverage["final_page_count"],
        "assets": str(assets_root),
        "page_route_evidence": str(page_routes_root),
        "plan": str(batch_root / "plan.json"),
        "acceptance": str(batch_root / "acceptance.json"),
        "native_extraction_elapsed_seconds": round(native_elapsed_seconds, 3),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "old_summary_reused": False,
        "background_processes": background_processes,
    }
    evidence = {
        "status": summary["status"],
        "run_root": str(batch_root),
        "source_sha256": source_hash,
        "selected_pages": selected,
        "page_results": records,
        "line_id_count": len(line_ids),
        "missing_line_ids": [],
        "duplicate_line_ids": duplicate_line_ids,
        "table_count_pdfplumber_find_tables": table_count,
        "confirmed_table_count": confirmed_table_count,
        "rendered_table_count": rendered_table_count,
        "route_policy": summary["route_policy"],
        "route_counts": route_counts,
        "page_routes": page_route_results if hybrid else [],
        "page_completion": completion,
        "model_calls": summary["model_calls"],
        "visual_model": summary["visual_model"],
        "rendering": summary["rendering"],
        "noise_cleanup_scan_command": noise_scan_record,
        "noise_cleanup_scan": noise_scan,
        "noise_cleanup_apply_command": noise_apply_record,
        "noise_cleanup_apply": noise_apply,
        "heading_repair_command": heading_record,
        "heading_repair": heading_report,
        "page_sequence_checks": page_sequence_checks,
        "redundant_page_aliases_removed": removed_page_aliases,
        "stale_source_aliases_removed": removed_source_aliases,
    }
    progress = {
        "format_version": FORMAT_VERSION,
        "status": summary["status"],
        "run_id": run_id,
        "source": str(source),
        "source_sha256": source_hash,
        "selected_pages": selected,
        "total_pages": pages_total,
        "stages": [
            {"stage": "smoke", "status": "passed" if not smoke or set(smoke).issubset(set(selected)) else "failed"},
            {"stage": "page_native_extraction", "status": "passed" if source_hash_consistent else "failed"},
            {"stage": "page_route_detection", "status": completion["status"] if hybrid else "not-run"},
            {"stage": "conditional_visual_supplement", "status": completion["status"] if hybrid else "not-run"},
            {"stage": "page_markdown_noise_cleanup", "status": noise_apply["status"]},
            {"stage": "heading_audit_and_repair", "status": heading_report["status"]},
            {"stage": "final_markdown_page_coverage", "status": "passed" if all(page_sequence_checks.values()) else "failed"},
            {"stage": "independent_acceptance", "status": summary["status"]},
        ],
    }

    acceptance = {
        "format_version": HYBRID_FORMAT_VERSION if hybrid else FORMAT_VERSION,
        "status": summary["status"],
        "source": str(source),
        "source_sha256": source_hash,
        "selected_pages": selected,
        "total_pages": pages_total,
        "paths": {
            "run_root": str(batch_root),
            "markdown": str(final_index),
            "index_markdown": str(final_index),
            "source_named_markdown": None,
            "document_temp": str(merged_document_path),
            "document_temp_raw": str(raw_document_path),
            "page_units": str(page_units_root),
            "assets": str(assets_root),
            "progress": str(batch_root / "progress.json"),
            "summary": str(batch_root / "summary.json"),
            "evidence": str(batch_root / "evidence.json"),
            "plan": str(batch_root / "plan.json"),
            "reports": str(reports_root),
            "page_routes": str(page_routes_root),
            "page_completion": str(reports_root / "page-completion.json"),
        },
        "checks": {
            "progress_status_passed": summary["status"] == "passed",
            "summary_status_passed": summary["status"] == "passed",
            "selected_pages_cover_full_pdf": coverage_complete,
            "page_units_present": page_units_exist,
            **page_sequence_checks,
            "missing_line_ids_empty": True,
            "duplicate_line_ids_empty": not duplicate_line_ids,
            "table_counts_match": table_count_match,
            "source_sha256_consistent": source_hash_consistent,
            "old_summary_reused_false": summary["old_summary_reused"] is False,
            "route_records_complete": route_records_complete,
            "page_completion_passed": completion["status"] == "passed",
            "incomplete_pages_empty": not completion["incomplete_pages"],
            "duplicate_pages_empty": not completion.get("duplicate_pages"),
            "unexpected_pages_empty": not completion.get("unexpected_pages"),
            "native_only_pages_have_no_model_calls": native_only_model_free,
            "no_model_calls_required": not hybrid,
            "no_model_calls": (
                model_calls_empty and heading_report.get("model_called") is False
                if not hybrid
                else True
            ),
            "no_visual_model": visual_model_not_run if not hybrid else True,
            "no_background_processes": not background_processes,
        },
        "background_processes": background_processes,
        "noise_cleanup": {
            "scan_status": noise_scan.get("status"),
            "apply_status": noise_apply.get("status"),
        },
        "heading_repair": {
            "status": heading_report.get("status"),
            "model_called": heading_report.get("model_called"),
            "candidate_count": heading_report.get("candidate_count"),
            "changed_count": len(heading_report.get("changed") or []),
            "unresolved_count": len(heading_report.get("unresolved") or []),
        },
    }

    if hybrid and summary_status == "passed":
        # Commit the engineering verdict first. Until reader verification ends,
        # summary/progress must not advertise terminal batch success.
        acceptance[hybrid_validator.READER_REQUIRED] = True
        acceptance[hybrid_validator.READER_AUDIT_VERSION] = "3.0.0"
        _record_hybrid_reader_state(batch_root, summary, progress, evidence, {"status": "running"})
        _write_json_atomic(batch_root / "acceptance.json", acceptance)
        reader = _finalize_hybrid_reader(batch_root, source, selected)
        _record_hybrid_reader_state(batch_root, summary, progress, evidence, reader)
    else:
        _write_json_atomic(batch_root / "progress.json", progress)
        _write_json_atomic(batch_root / "summary.json", summary)
        _write_json_atomic(batch_root / "evidence.json", evidence)
        _write_json_atomic(batch_root / "acceptance.json", acceptance)
    return _page_batch_result(batch_root, run_id, summary)


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
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict) and "status" in value:
        return value
    # Visual conversion may print a short preamble followed by indented JSON.
    # Recover only a final object that consumes the remainder of stdout.
    decoder = json.JSONDecoder()
    for offset, character in enumerate(stdout):
        if character != "{":
            continue
        try:
            candidate, consumed = decoder.raw_decode(stdout[offset:])
        except json.JSONDecodeError:
            continue
        if stdout[offset + consumed :].strip():
            continue
        if isinstance(candidate, dict) and "status" in candidate:
            return candidate
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
    parser.add_argument("--resume-package", type=Path, help="reuse this existing page-batch directory and skip passed page outputs")
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="enable page-level native-only/native-plus-visual/visual-only routing",
    )
    parser.add_argument(
        "--visual-dpi",
        type=int,
        default=220,
        help="DPI for pages routed to the visual worker (200-250)",
    )
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
        if args.batch_size is None and not args.resume_pages:
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
            candidates = sorted(args.output_root.expanduser().resolve().glob("*/run-record.yaml"), key=lambda item: item.stat().st_mtime, reverse=True)
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
            with task_guard.acquire_task_lock(
                source=sources[0],
                source_sha256=sha256(sources[0]),
                output_root=args.output_root.expanduser().resolve(),
                pages=args.pages,
                entrypoint="run_pdf_batches.py",
            ):
                code, state = run_page_batches(
                    sources[0],
                    args.output_root,
                    args.pages,
                    args.smoke_pages,
                    batch_size,
                    page_record,
                    _positive_int((config.get("execution") or {}).get("per_source_timeout_seconds"), "per_source_timeout_seconds", 1800),
                    str((config.get("context") or {}).get("mode", "auto")),
                    _positive_int((config.get("context") or {}).get("window_pages"), "window_pages", 1),
                    args.resume_package,
                    args.hybrid,
                    args.visual_dpi,
                )
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
