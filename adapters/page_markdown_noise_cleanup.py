#!/usr/bin/env python3
"""Plan, apply, and verify conservative page-unit Markdown noise cleanup.

Only complete physical lines may be removed.  The adapter learns repeated
boundary banners, accepts source-specific exact/regex/fuzzy rules, deletes
footer page numbers only immediately before the original-page image, and
removes ``---`` only when it is a first/last non-empty boundary after the
other planned deletions.  It never reads page images or OCR/Vision evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable


VERSION = "1.0.0"
PAGE_FILE = re.compile(r"^p(\d{3})\.md$")
ORIGINAL_IMAGE = re.compile(r"\.\./assets/page-\d{3}\.png")
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(?:<[^>]+>|[^)]+)\s*\)")
PAGE_NUMBER_LINE = re.compile(r"^(?:[-*+]\s+)?[-–—−\s]+\d{1,4}[-–—−\s]+$")
PAGE_NUMBER_VALUE = re.compile(r"\d+")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
LIST_MARKER = re.compile(r"^\s{0,3}(?:[-*+]\s+|>\s+)")

DEFAULT_PROFILE: dict[str, Any] = {
    "version": VERSION,
    "zones": {
        "header_nonempty_lines": 6,
        "footer_nonempty_lines": 6,
        "adjacent_line_window": 4,
    },
    "discovery": {
        "enabled": True,
        "minimum_occurrences": 3,
        "minimum_page_ratio": 0.02,
        "minimum_cooccurrences": 2,
        "fuzzy_similarity": 0.84,
    },
    "header_rules": [],
    "protected_exact": [
        "正文",
        "视觉补充",
        "列表/图示文字",
        "表格与公式",
        "解读",
        "举例与思考",
        "模型结构",
        "已确认的关系说明",
        "原始页面图",
        "待核对项",
        "暂无待核对项。",
        "暂无已确认的关系说明。",
    ],
}


class NoiseCleanupError(ValueError):
    """Raised when inputs or cleanup integrity checks are invalid."""


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    value: str
    minimum_similarity: float = 1.0
    ignore_case: bool = False
    source: str = "profile"
    zones: tuple[str, ...] = ("header", "footer", "template-block")

    def matches(self, raw: str) -> bool:
        candidate = normalize_markup(raw)
        if self.kind == "exact":
            expected = normalize_markup(self.value)
            if self.ignore_case:
                return candidate.casefold() == expected.casefold()
            return candidate == expected
        if self.kind == "regex":
            flags = re.IGNORECASE if self.ignore_case else 0
            return re.fullmatch(self.value, candidate, flags=flags) is not None
        if self.kind == "fuzzy":
            actual = similarity_text(candidate)
            expected = similarity_text(self.value)
            return bool(actual and expected) and SequenceMatcher(None, actual, expected).ratio() >= self.minimum_similarity
        raise NoiseCleanupError(f"unsupported header rule kind: {self.kind}")


@dataclass
class PageRecord:
    path: Path
    name: str
    page: int
    data: bytes
    lines: list[str]
    input_sha256: str


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_markup(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).replace("\u200b", "").strip()
    previous = None
    while text != previous:
        previous = text
        text = HEADING.sub("", text, count=1)
        text = LIST_MARKER.sub("", text, count=1)
    if len(text) >= 4 and text.startswith("**") and text.endswith("**"):
        text = text[2:-2].strip()
    return re.sub(r"\s+", " ", text)


def similarity_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", " ", normalize_markup(value).casefold()).strip()


def _deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_profile(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _deep_merge(DEFAULT_PROFILE, None)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(raw)
    else:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - repository test runtime includes PyYAML
            raise NoiseCleanupError("YAML profile requires PyYAML; use JSON instead") from exc
        value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise NoiseCleanupError("noise cleanup profile must be an object")
    return _deep_merge(DEFAULT_PROFILE, value)


def _validate_profile(profile: dict[str, Any]) -> None:
    if str(profile.get("version")) != VERSION:
        raise NoiseCleanupError(f"profile version must be {VERSION}")
    zones = profile.get("zones")
    discovery = profile.get("discovery")
    rules = profile.get("header_rules")
    if not isinstance(zones, dict) or not isinstance(discovery, dict) or not isinstance(rules, list):
        raise NoiseCleanupError("profile zones, discovery, and header_rules have invalid types")
    for key in ("header_nonempty_lines", "footer_nonempty_lines", "adjacent_line_window"):
        if not isinstance(zones.get(key), int) or zones[key] < 1:
            raise NoiseCleanupError(f"profile zones.{key} must be a positive integer")
    for item in rules:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise NoiseCleanupError("each header rule requires a non-empty id")
        kind = item.get("kind")
        if kind not in {"exact", "regex", "fuzzy"}:
            raise NoiseCleanupError(f"unsupported header rule kind: {kind}")
        field = "pattern" if kind == "regex" else "value"
        if not isinstance(item.get(field), str) or not item[field]:
            raise NoiseCleanupError(f"header rule {item['id']} requires {field}")
        if kind == "regex":
            re.compile(item[field], re.IGNORECASE if item.get("ignore_case") else 0)


def _compile_profile_rules(profile: dict[str, Any]) -> list[Rule]:
    compiled: list[Rule] = []
    seen: set[str] = set()
    for item in profile["header_rules"]:
        rule_id = item["id"]
        if rule_id in seen:
            raise NoiseCleanupError(f"duplicate header rule id: {rule_id}")
        seen.add(rule_id)
        kind = item["kind"]
        compiled.append(
            Rule(
                id=rule_id,
                kind=kind,
                value=item["pattern"] if kind == "regex" else item["value"],
                minimum_similarity=float(item.get("minimum_similarity", 0.84)),
                ignore_case=bool(item.get("ignore_case", False)),
                source="profile",
                zones=tuple(item.get("zones") or ("header", "footer", "template-block")),
            )
        )
    return compiled


def _parse_pages(value: str) -> set[int] | None:
    if value == "all":
        return None
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start, end = int(start_raw), int(end_raw)
            if start < 1 or end < start:
                raise NoiseCleanupError(f"invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise NoiseCleanupError(f"invalid page number: {token}")
            pages.add(page)
    if not pages:
        raise NoiseCleanupError("page selector is empty")
    return pages


def _read_pages(root: Path, selected: set[int] | None) -> list[PageRecord]:
    if not root.is_dir():
        raise NoiseCleanupError(f"page-units directory does not exist: {root}")
    records: list[PageRecord] = []
    seen_pages: set[int] = set()
    for path in sorted(root.glob("p[0-9][0-9][0-9].md")):
        match = PAGE_FILE.fullmatch(path.name)
        if not match:
            continue
        page = int(match.group(1))
        if selected is not None and page not in selected:
            continue
        if page in seen_pages:
            raise NoiseCleanupError(f"duplicate page number: {page}")
        seen_pages.add(page)
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NoiseCleanupError(f"page unit is not UTF-8: {path}") from exc
        records.append(
            PageRecord(
                path=path,
                name=path.name,
                page=page,
                data=data,
                lines=text.splitlines(keepends=True),
                input_sha256=sha256_bytes(data),
            )
        )
    if selected is not None:
        missing = sorted(selected - seen_pages)
        if missing:
            raise NoiseCleanupError(f"selected page units are missing: {missing}")
    if not records:
        raise NoiseCleanupError("no pNNN.md page units found")
    return records


def _line_without_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _is_comment(raw: str) -> bool:
    stripped = raw.strip()
    return stripped.startswith("<!--") and stripped.endswith("-->")


def _is_protected(raw: str, protected_exact: set[str]) -> bool:
    stripped = raw.strip()
    normalized = normalize_markup(raw)
    if not stripped or _is_comment(raw) or MARKDOWN_IMAGE.fullmatch(stripped):
        return True
    if stripped.startswith("```") or "Unknown/needs-review" in raw or "needs-review" in raw:
        return True
    if "|" in stripped and stripped.startswith("|"):
        return True
    return normalized in protected_exact


def _is_banner_like(raw: str, protected_exact: set[str]) -> bool:
    normalized = normalize_markup(raw)
    if _is_protected(raw, protected_exact) or PAGE_NUMBER_LINE.fullmatch(raw.strip()):
        return False
    if not (3 <= len(normalized) <= 100):
        return False
    if normalized.endswith(("。", "！", "？", "；", ".", "!", "?", ";")):
        return False
    return bool(re.search(r"[A-Za-z0-9\u3400-\u9fff]", normalized))


def _is_semantic_markup(raw: str) -> bool:
    stripped = raw.strip()
    return bool(HEADING.match(stripped) or LIST_MARKER.match(stripped) or re.match(r"\d+[.)]\s+", stripped))


def _nonempty_indices(record: PageRecord) -> list[int]:
    return [i for i, line in enumerate(record.lines) if _line_without_newline(line).strip()]


def _original_image_indices(record: PageRecord) -> list[int]:
    return [i for i, line in enumerate(record.lines) if ORIGINAL_IMAGE.search(line) and MARKDOWN_IMAGE.search(line)]


def _boundary_pool(record: PageRecord, profile: dict[str, Any], protected_exact: set[str]) -> set[int]:
    nonempty = [i for i in _nonempty_indices(record) if not _is_protected(_line_without_newline(record.lines[i]), protected_exact)]
    head_limit = profile["zones"]["header_nonempty_lines"]
    footer_limit = profile["zones"]["footer_nonempty_lines"]
    pool = set(nonempty[:head_limit])
    images = _original_image_indices(record)
    before_image = [i for i in nonempty if not images or i < images[0]]
    pool.update(before_image[-footer_limit:])
    return {
        i for i in pool
        if not _is_semantic_markup(record.lines[i])
        and _is_banner_like(_line_without_newline(record.lines[i]), protected_exact)
    }


def _anchor_compatible(left: str, right: str) -> bool:
    left_text, right_text = similarity_text(left), similarity_text(right)
    left_words = re.findall(r"[a-z0-9]+", left_text)
    right_words = re.findall(r"[a-z0-9]+", right_text)
    if len(left_words) >= 2 and len(right_words) >= 2 and left_words[:2] == right_words[:2]:
        return True
    left_compact = re.sub(r"\s+", "", left_text)
    right_compact = re.sub(r"\s+", "", right_text)
    return len(left_compact) >= 4 and len(right_compact) >= 4 and left_compact[:4] == right_compact[:4]


def _discover_rules(
    records: list[PageRecord],
    profile: dict[str, Any],
    protected_exact: set[str],
) -> tuple[list[Rule], list[dict[str, Any]]]:
    discovery = profile["discovery"]
    if not discovery.get("enabled", True):
        return [], []
    occurrences: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    per_file_values: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for record in records:
        for index in sorted(_boundary_pool(record, profile, protected_exact)):
            raw = _line_without_newline(record.lines[index])
            normalized = normalize_markup(raw)
            occurrences[normalized].append((record.name, index, raw))
            per_file_values[record.name].append((index, normalized))
    minimum = max(
        int(discovery.get("minimum_occurrences", 3)),
        math.ceil(len(records) * float(discovery.get("minimum_page_ratio", 0.02))),
    )
    frequent = {
        value for value, items in occurrences.items()
        if len({file for file, _, _ in items}) >= minimum
    }
    cooccurrences: Counter[tuple[str, str]] = Counter()
    window = profile["zones"]["adjacent_line_window"]
    for items in per_file_values.values():
        for pos, (left_index, left) in enumerate(items):
            if left not in frequent:
                continue
            for right_index, right in items[pos + 1:]:
                if right not in frequent or left == right or abs(left_index - right_index) > window:
                    continue
                cooccurrences[tuple(sorted((left, right)))] += 1
    min_cooccurrences = int(discovery.get("minimum_cooccurrences", 2))
    standalone_minimum = max(5, minimum * 2)
    confirmed: set[str] = set()
    for value in frequent:
        page_count = len({file for file, _, _ in occurrences[value]})
        cooccurs = any(value in pair and count >= min_cooccurrences for pair, count in cooccurrences.items())
        if cooccurs or page_count >= standalone_minimum:
            confirmed.add(value)
    similarity = float(discovery.get("fuzzy_similarity", 0.84))
    members = set(confirmed)
    attachment: dict[str, str] = {}
    for value in occurrences:
        if value in members:
            continue
        candidates = []
        for canonical in confirmed:
            if not _anchor_compatible(value, canonical):
                continue
            score = SequenceMatcher(None, similarity_text(value), similarity_text(canonical)).ratio()
            if score >= similarity:
                candidates.append((score, canonical))
        if candidates:
            _, canonical = max(candidates)
            members.add(value)
            attachment[value] = canonical
    rules: list[Rule] = []
    report: list[dict[str, Any]] = []
    for value in sorted(members):
        rule_id = f"auto-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
        rules.append(Rule(id=rule_id, kind="exact", value=value, source="discovery"))
        report.append(
            {
                "id": rule_id,
                "kind": "exact",
                "value": value,
                "page_count": len({file for file, _, _ in occurrences[value]}),
                "attached_to": attachment.get(value),
            }
        )
    return rules, report


def _match_rules(record: PageRecord, rules: list[Rule], protected_exact: set[str]) -> dict[int, list[Rule]]:
    matches: dict[int, list[Rule]] = defaultdict(list)
    for index, line in enumerate(record.lines):
        raw = _line_without_newline(line)
        if _is_protected(raw, protected_exact):
            continue
        for rule in rules:
            if rule.source != "profile" and _is_semantic_markup(raw):
                continue
            if rule.matches(raw):
                matches[index].append(rule)
    return matches


def _is_ignorable_scaffold(raw: str, protected_exact: set[str]) -> bool:
    stripped = raw.strip()
    return (
        not stripped
        or stripped == "---"
        or _is_comment(raw)
        or HEADING.match(stripped) is not None
        or normalize_markup(raw) in protected_exact
    )


def _safe_header(index: int, record: PageRecord, matched: set[int], protected_exact: set[str]) -> bool:
    for earlier in range(index):
        if earlier in matched:
            continue
        raw = _line_without_newline(record.lines[earlier])
        if _is_ignorable_scaffold(raw, protected_exact):
            continue
        return False
    return True


def _footer_target(record: PageRecord, index: int) -> int:
    later_images = [value for value in _original_image_indices(record) if value > index]
    return min(later_images) if later_images else len(record.lines)


def _safe_footer(
    index: int,
    record: PageRecord,
    matched: set[int],
    protected_exact: set[str],
    footer_nonempty_lines: int,
) -> bool:
    target = _footer_target(record, index)
    if target <= index:
        return False
    nonempty_seen = 0
    for later in range(index + 1, target):
        if later in matched:
            continue
        raw = _line_without_newline(record.lines[later])
        if not raw.strip():
            continue
        nonempty_seen += 1
        if nonempty_seen > footer_nonempty_lines or not _is_ignorable_scaffold(raw, protected_exact):
            return False
    return True


def _template_block_indices(matches: dict[int, list[Rule]], window: int) -> set[int]:
    indices = sorted(matches)
    safe: set[int] = set()
    for start_pos, start in enumerate(indices):
        block = [value for value in indices[start_pos:] if value - start <= window]
        rule_ids = {
            rule.id for index in block for rule in matches[index]
            if "template-block" in rule.zones
        }
        if len(rule_ids) >= 2:
            safe.update(index for index in block if any("template-block" in rule.zones for rule in matches[index]))
    return safe


def _footer_page_number(index: int, record: PageRecord, deletions: set[int], protected_exact: set[str], limit: int) -> bool:
    later_images = [value for value in _original_image_indices(record) if value > index]
    target = min(later_images) if later_images else len(record.lines)
    nonempty_seen = 0
    for later in range(index + 1, target):
        if later in deletions:
            continue
        raw = _line_without_newline(record.lines[later])
        if not raw.strip():
            continue
        nonempty_seen += 1
        if nonempty_seen > limit or not _is_ignorable_scaffold(raw, protected_exact):
            return False
    return True


def _footer_number_indices(record: PageRecord, deletions: set[int], protected_exact: set[str], limit: int) -> set[int]:
    """Plan duplicate copies of the same footer, preserving different numbers for review."""
    numbers: dict[int, str] = {}
    for index, line in enumerate(record.lines):
        raw = _line_without_newline(line).strip()
        if PAGE_NUMBER_LINE.fullmatch(raw):
            numbers[index] = PAGE_NUMBER_VALUE.search(raw).group()
    planned: dict[int, str] = {}
    for index, value in reversed(list(numbers.items())):
        same_number = {other for other, other_value in planned.items() if other_value == value}
        if _footer_page_number(index, record, deletions | same_number, protected_exact, limit):
            planned[index] = value
    return set(planned)


def _boundary_delimiters(record: PageRecord, deletions: set[int]) -> set[int]:
    planned = set(deletions)
    while True:
        remaining = [
            index for index, line in enumerate(record.lines)
            if index not in planned and _line_without_newline(line).strip()
        ]
        if not remaining:
            return planned - deletions
        added = False
        for index in {remaining[0], remaining[-1]}:
            if _line_without_newline(record.lines[index]).strip() == "---":
                planned.add(index)
                added = True
        if not added:
            return planned - deletions


def _deletion_entry(record: PageRecord, index: int, kind: str, rule_id: str) -> dict[str, Any]:
    line = record.lines[index]
    return {
        "file": record.name,
        "line": index + 1,
        "kind": kind,
        "rule_id": rule_id,
        "raw": _line_without_newline(line),
        "line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
    }


def _scan_residual_high_confidence(
    record: PageRecord,
    expected: bytes,
    rules: list[Rule],
    profile: dict[str, Any],
    protected_exact: set[str],
) -> list[dict[str, Any]]:
    projected = PageRecord(
        path=record.path,
        name=record.name,
        page=record.page,
        data=expected,
        lines=expected.decode("utf-8").splitlines(keepends=True),
        input_sha256=sha256_bytes(expected),
    )
    matches = _match_rules(projected, rules, protected_exact)
    matched_indices = set(matches)
    safe = _template_block_indices(matches, profile["zones"]["adjacent_line_window"])
    for index, line_rules in matches.items():
        if any("header" in rule.zones for rule in line_rules) and _safe_header(
            index, projected, matched_indices, protected_exact
        ):
            safe.add(index)
        if any("footer" in rule.zones for rule in line_rules) and _safe_footer(
            index,
            projected,
            matched_indices,
            protected_exact,
            profile["zones"]["footer_nonempty_lines"],
        ):
            safe.add(index)
    residuals = [
        {
            "file": record.name,
            "line": index + 1,
            "kind": "header-template",
            "rule_id": matches[index][0].id,
            "raw": _line_without_newline(projected.lines[index]),
        }
        for index in sorted(safe)
    ]
    for index, line in enumerate(projected.lines):
        raw = _line_without_newline(line)
        if PAGE_NUMBER_LINE.fullmatch(raw.strip()) and _footer_page_number(
            index,
            projected,
            set(),
            protected_exact,
            profile["zones"]["footer_nonempty_lines"],
        ):
            residuals.append(
                {
                    "file": record.name,
                    "line": index + 1,
                    "kind": "footer-page-number",
                    "rule_id": "footer-page-number",
                    "raw": raw,
                }
            )
    for index in sorted(_boundary_delimiters(projected, set())):
        residuals.append(
            {
                "file": record.name,
                "line": index + 1,
                "kind": "boundary-delimiter",
                "rule_id": "boundary-delimiter",
                "raw": _line_without_newline(projected.lines[index]),
            }
        )
    return residuals


def _atomic_write(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    os.chmod(temporary, mode)
    temporary.replace(path)


def cleanup_directory(
    page_units: Path,
    *,
    profile: dict[str, Any] | None = None,
    pages: str = "all",
    apply: bool = False,
) -> dict[str, Any]:
    root = page_units.expanduser().resolve()
    resolved_profile = _deep_merge(DEFAULT_PROFILE, profile)
    _validate_profile(resolved_profile)
    records = _read_pages(root, _parse_pages(pages))
    protected_exact = {normalize_markup(value) for value in resolved_profile["protected_exact"]}
    profile_rules = _compile_profile_rules(resolved_profile)
    discovered_rules, discovered_report = _discover_rules(records, resolved_profile, protected_exact)
    rules = profile_rules + discovered_rules
    file_plans: dict[str, list[dict[str, Any]]] = {}
    expected_bytes: dict[str, bytes] = {}
    suspects: list[dict[str, Any]] = []
    retained_internal_delimiters: list[dict[str, Any]] = []
    counts_by_kind: Counter[str] = Counter()
    window = resolved_profile["zones"]["adjacent_line_window"]
    footer_limit = resolved_profile["zones"]["footer_nonempty_lines"]

    for record in records:
        matches = _match_rules(record, rules, protected_exact)
        matched_indices = set(matches)
        safe = _template_block_indices(matches, window)
        for index, line_rules in matches.items():
            if any("header" in rule.zones for rule in line_rules) and _safe_header(index, record, matched_indices, protected_exact):
                safe.add(index)
            if any("footer" in rule.zones for rule in line_rules) and _safe_footer(
                index, record, matched_indices, protected_exact, footer_limit
            ):
                safe.add(index)
        deletions: dict[int, tuple[str, str]] = {}
        for index in sorted(safe):
            rule = matches[index][0]
            deletions[index] = ("header-template", rule.id)
        for index, line_rules in sorted(matches.items()):
            if index in safe:
                continue
            profile_match = next((rule for rule in line_rules if rule.source == "profile"), None)
            if profile_match:
                suspects.append(
                    {
                        "file": record.name,
                        "line": index + 1,
                        "kind": "header-like-outside-safe-zone",
                        "rule_id": profile_match.id,
                        "raw": _line_without_newline(record.lines[index]),
                    }
                )
        for index in _footer_number_indices(record, set(deletions), protected_exact, footer_limit):
            deletions[index] = ("footer-page-number", "footer-page-number")
        for index in _boundary_delimiters(record, set(deletions)):
            deletions[index] = ("boundary-delimiter", "boundary-delimiter")
        for index, line in enumerate(record.lines):
            if _line_without_newline(line).strip() == "---" and index not in deletions:
                retained_internal_delimiters.append({"file": record.name, "line": index + 1, "raw": "---"})
        entries = [
            _deletion_entry(record, index, kind, rule_id)
            for index, (kind, rule_id) in sorted(deletions.items())
        ]
        file_plans[record.name] = entries
        counts_by_kind.update(entry["kind"] for entry in entries)
        deleted_indices = set(deletions)
        expected_bytes[record.name] = "".join(
            line for index, line in enumerate(record.lines) if index not in deleted_indices
        ).encode("utf-8")

    image_links_unchanged = True
    delete_only_reconstruction = True
    all_files_nonempty = True
    residual_high_confidence: list[dict[str, Any]] = []
    for record in records:
        expected = expected_bytes[record.name]
        expected_text = expected.decode("utf-8")
        before_images = [line for line in record.lines if ORIGINAL_IMAGE.search(line)]
        after_images = [line for line in expected_text.splitlines(keepends=True) if ORIGINAL_IMAGE.search(line)]
        if before_images != after_images:
            image_links_unchanged = False
        if not expected_text.strip():
            all_files_nonempty = False
        deleted = {entry["line"] - 1 for entry in file_plans[record.name]}
        reconstructed = "".join(line for index, line in enumerate(record.lines) if index not in deleted).encode("utf-8")
        if reconstructed != expected:
            delete_only_reconstruction = False
        residual_high_confidence.extend(
            _scan_residual_high_confidence(record, expected, rules, resolved_profile, protected_exact)
        )

    if apply:
        for record in records:
            if sha256_bytes(record.path.read_bytes()) != record.input_sha256:
                raise NoiseCleanupError(f"page unit changed after scan; refusing partial cleanup: {record.path}")
        for record in records:
            expected = expected_bytes[record.name]
            if expected == record.data:
                continue
            if sha256_bytes(record.path.read_bytes()) != record.input_sha256:
                raise NoiseCleanupError(f"page unit changed before write: {record.path}")
            _atomic_write(record.path, expected)
        for record in records:
            actual = record.path.read_bytes()
            if actual != expected_bytes[record.name]:
                raise NoiseCleanupError(f"post-write verification failed: {record.path}")

    file_results = []
    changed_files = 0
    for record in records:
        output = expected_bytes[record.name]
        changed = output != record.data
        changed_files += int(changed)
        file_results.append(
            {
                "file": record.name,
                "page": record.page,
                "input_sha256": record.input_sha256,
                "output_sha256": sha256_bytes(output),
                "changed": changed,
                "deletions": file_plans[record.name],
            }
        )
    status = "needs-review" if suspects or residual_high_confidence else "passed"
    result = {
        "contract": "page-markdown-noise-cleanup",
        "version": VERSION,
        "mode": "apply" if apply else "scan",
        "status": status,
        "page_units": str(root),
        "selected_pages": [record.page for record in records],
        "profile": resolved_profile,
        "counts": {
            "files": len(records),
            "changed_files": changed_files,
            "planned_deletions": sum(len(items) for items in file_plans.values()),
            "deleted_lines": sum(len(items) for items in file_plans.values()) if apply else 0,
            "suspects": len(suspects),
        },
        "counts_by_kind": dict(sorted(counts_by_kind.items())),
        "discovered_rules": discovered_report,
        "files": file_results,
        "suspects": suspects,
        "retained_internal_delimiters": retained_internal_delimiters,
        "residual_high_confidence": residual_high_confidence,
        "checks": {
            "all_files_nonempty": all_files_nonempty,
            "image_links_unchanged": image_links_unchanged,
            "delete_only_reconstruction": delete_only_reconstruction,
            "input_hashes_rechecked_before_apply": apply,
            "only_changed_files_rewritten": True,
        },
        "next_action": (
            "查看 suspects，补充来源专用 profile 后重新 scan；不要自动删除正文候选。"
            if status == "needs-review"
            else "scan 已通过；可使用 --apply 原子写回。" if not apply
            else "清洗与写后验证通过；再进入 markdown-content-repair。"
        ),
    }
    return result


def _write_evidence(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-units", type=Path, required=True)
    parser.add_argument("--pages", default="all")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        result = cleanup_directory(args.page_units, profile=profile, pages=args.pages, apply=args.apply)
    except (OSError, NoiseCleanupError, json.JSONDecodeError) as exc:
        print(f"page Markdown noise cleanup failed: {exc}", file=__import__("sys").stderr)
        return 2
    if args.evidence:
        _write_evidence(args.evidence, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
