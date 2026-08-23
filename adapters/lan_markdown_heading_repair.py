#!/usr/bin/env python3
"""Audit visible headings and create ``index.md`` with ATX-only repairs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from adapters.lan_pdf_vision_to_markdown import (
        page_reader_image_policy,
        project_page_image_reference,
    )
except ModuleNotFoundError:  # Direct script execution from adapters/.
    from lan_pdf_vision_to_markdown import (
        page_reader_image_policy,
        project_page_image_reference,
    )


EXPECTED_MODEL = "qwen3-vl:30b-a3b-instruct"
DEFAULT_OLLAMA_URL = "http://shopme.local:11434"
PAGE_FILE_RE = re.compile(r"p(\d+)\.md$")
PAGE_MARKER_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")
ATX_RE = re.compile(r"^(#{1,6})([ \t]+)(.*)$")
PLAIN_CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百千0-9]+\s*[章节篇部卷]|"
    r"(?:章节|篇章|部分)\s*[一二三四五六七八九十百千0-9]+)\s*[^。！？!?]{0,48}$"
)
PLAIN_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,2})\s+[^。！？!?]{1,80}$")
SHORT_HEADING_RE = re.compile(r"^\s*(摘要|目录|前言|序言|引言|结语|后记|附录)\s*$")
PROTECTED_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)、]\s+|>\s*|\||!\[|\[.+\]\(|<!--|```|---\s*$)"
)
DECISION_RE = re.compile(
    r"^LINE\s+(\d+)\tLEVEL\s+([0-6])\tCONFIDENCE\s+"
    r"(high|medium|low)\tREASON\s+(.+)$",
    re.IGNORECASE,
)
class HeadingRepairError(RuntimeError):
    """A handled heading-audit or integrity failure."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _page_units(run_root: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted((run_root / "page-units").glob("p*.md")):
        match = PAGE_FILE_RE.fullmatch(path.name)
        if match:
            found.append((int(match.group(1)), path))
    if not found:
        raise HeadingRepairError("没有可审计的 page-units/pNNN.md")
    return sorted(found, key=lambda item: item[0])


def _project_page_unit_image_path(run_root: Path, text: str, page: int) -> str:
    policy = page_reader_image_policy(run_root, page)
    return project_page_image_reference(text, page, policy["document_reference"])


def _deterministic_merge(run_root: Path, units: list[tuple[int, Path]]) -> str:
    parts = []
    for page, path in units:
        text = path.read_text(encoding="utf-8")
        policy = page_reader_image_policy(run_root, page)
        if policy.get("document_reference") is None:
            parts.append(text.rstrip())
        else:
            parts.append(_project_page_unit_image_path(run_root, text, page).rstrip())
    return "\n\n".join(parts) + "\n"


def _candidate(
    *, page: int, line_number: int, line: str, current_level: int, rule: str
) -> dict[str, Any]:
    match = ATX_RE.match(line)
    visible_text = match.group(3) if match else line
    return {
        "page": page,
        "line": line_number,
        "current_level": current_level,
        "rule": rule,
        "visible_text_sha256": sha256_bytes(visible_text.encode("utf-8")),
    }


def find_heading_candidates(page: int, text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    last_level: int | None = None
    fenced = False
    for line_number, line in enumerate(text.replace("\r\n", "\n").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not stripped or PAGE_MARKER_RE.fullmatch(stripped):
            continue
        heading = ATX_RE.match(line)
        if heading:
            level = len(heading.group(1))
            if level > 3:
                candidates.append(
                    _candidate(
                        page=page,
                        line_number=line_number,
                        line=line,
                        current_level=level,
                        rule="heading-level-above-three",
                    )
                )
            elif last_level is not None and level > last_level + 1:
                candidates.append(
                    _candidate(
                        page=page,
                        line_number=line_number,
                        line=line,
                        current_level=level,
                        rule="heading-level-jump",
                    )
                )
            last_level = level
            continue
        if (
            len(stripped) <= 64
            and not PROTECTED_RE.match(line)
            and PLAIN_CHAPTER_RE.fullmatch(line)
        ):
            candidates.append(
                _candidate(
                    page=page,
                    line_number=line_number,
                    line=line,
                    current_level=0,
                    rule="visible-chapter-line-without-atx",
                )
            )
    unique: dict[int, dict[str, Any]] = {}
    for item in candidates:
        unique.setdefault(int(item["line"]), item)
    return list(unique.values())


def _deterministic_level(line: str, current_level: int) -> tuple[int, str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    if SHORT_HEADING_RE.fullmatch(stripped):
        return 1, "high", "deterministic-short-heading"
    if PLAIN_CHAPTER_RE.fullmatch(line):
        return 1, "high", "deterministic-chapter-heading"
    section = PLAIN_SECTION_RE.fullmatch(line)
    if section:
        segments = section.group(1).split(".")
        return min(len(segments), 3), "high", "deterministic-numbered-section"
    if 1 <= current_level <= 3:
        return current_level, "medium", "deterministic-keep-existing-atx"
    if current_level > 3:
        return 3, "medium", "deterministic-cap-heading-level"
    return None


def _deterministic_decisions(
    *, page_markdown: str, candidates: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    lines = page_markdown.replace("\r\n", "\n").splitlines()
    decisions: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        line_number = int(candidate["line"])
        if line_number < 1 or line_number > len(lines):
            continue
        resolved = _deterministic_level(lines[line_number - 1], int(candidate["current_level"]))
        if resolved is None:
            continue
        level, confidence, reason = resolved
        decisions[line_number] = {
            "level": level,
            "confidence": confidence,
            "reason": reason,
        }
    return decisions


def _heading_prompt(page: int, page_markdown: str, candidates: list[dict[str, Any]]) -> str:
    candidate_lines = "\n".join(
        f"- line={item['line']} current_level={item['current_level']} rule={item['rule']}"
        for item in candidates
    )
    return f"""你看到的是 PDF 第 {page} 页图片，以及这一页现有的 Markdown。
只审计下列候选行是否为图片中可见标题，以及正确的 ATX 层级。不得审计或改写其他行。

候选行：
{candidate_lines}

当前页 Markdown：
{page_markdown}

每个候选行必须恰好输出一行，除此之外不要输出任何内容：
LINE <行号>\tLEVEL <0-6>\tCONFIDENCE <high|medium|low>\tREASON <简短原因>

LEVEL 0 表示该行不应是标题；1-6 表示 # 数量。只能判断 # 前缀，不得提供改写后的文字。
页面没有可见标题时不得新增标题。"""


def _model_decisions(
    *,
    page: int,
    page_markdown: str,
    image_path: Path,
    candidates: list[dict[str, Any]],
    timeout_seconds: int,
) -> tuple[dict[int, dict[str, Any]], str | None]:
    if not image_path.is_file() or image_path.stat().st_size == 0:
        return {}, f"缺少异常页图片：{image_path}"
    try:
        from lan_pdf_vision_to_markdown import canonical_json_bytes, send_streaming_request
    except ImportError as exc:
        return {}, f"无法加载 LAN 视觉请求器：{exc}"
    prompt = _heading_prompt(page, page_markdown, candidates)
    payload = {
        "model": EXPECTED_MODEL,
        "prompt": prompt,
        "images": [base64.b64encode(image_path.read_bytes()).decode("ascii")],
        "stream": True,
        "think": False,
        "options": {
            "temperature": 0,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }
    if "format" in payload:
        return {}, "标题请求意外包含 format"
    try:
        aggregate = send_streaming_request(
            canonical_json_bytes(payload), timeout_seconds=timeout_seconds
        )
    except Exception as exc:
        return {}, f"标题模型调用失败：{exc}"
    allowed_lines = {int(item["line"]) for item in candidates}
    decisions: dict[int, dict[str, Any]] = {}
    invalid_lines: list[str] = []
    for raw in str(aggregate.get("response") or "").splitlines():
        if not raw.strip():
            continue
        match = DECISION_RE.fullmatch(raw.strip())
        if not match:
            invalid_lines.append(raw)
            continue
        line_number = int(match.group(1))
        if line_number not in allowed_lines or line_number in decisions:
            invalid_lines.append(raw)
            continue
        decisions[line_number] = {
            "level": int(match.group(2)),
            "confidence": match.group(3).lower(),
            "reason": match.group(4).strip(),
        }
    if invalid_lines:
        return {}, "标题模型输出包含协议外内容"
    if set(decisions) != allowed_lines:
        return decisions, "标题模型没有逐项返回全部候选行"
    return decisions, None


def _visible_body(line: str) -> str:
    match = ATX_RE.match(line)
    return match.group(3) if match else line


def _same_visible_body(old_line: str, new_line: str) -> bool:
    old_body = _visible_body(old_line)
    new_body = _visible_body(new_line)
    return old_body == new_body or old_body.lstrip() == new_body.lstrip()


def _with_heading_level(line: str, level: int) -> str:
    if not 0 <= level <= 6:
        raise HeadingRepairError(f"非法标题层级：{level}")
    match = ATX_RE.match(line)
    if match:
        body = match.group(3)
        if level == 0:
            return body
        return "#" * level + match.group(2) + body
    if level == 0:
        return line
    return "#" * level + " " + line


def _apply_page_decisions(
    *,
    page: int,
    original: str,
    candidates: list[dict[str, Any]],
    decisions: dict[int, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = original.replace("\r\n", "\n")
    lines = normalized.splitlines()
    changed: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    by_line = {int(item["line"]): item for item in candidates}
    for line_number, candidate in sorted(by_line.items()):
        decision = decisions.get(line_number)
        if decision is None or decision.get("confidence") == "low":
            unresolved.append({**candidate, "reason": "missing-or-low-confidence-decision"})
            continue
        old_line = lines[line_number - 1]
        new_line = _with_heading_level(old_line, int(decision["level"]))
        if not _same_visible_body(old_line, new_line):
            raise HeadingRepairError(f"第 {page} 页第 {line_number} 行正文发生变化")
        old_level_match = ATX_RE.match(old_line)
        old_level = len(old_level_match.group(1)) if old_level_match else 0
        new_level = int(decision["level"])
        if new_line != old_line:
            lines[line_number - 1] = new_line
            changed.append(
                {
                    "page": page,
                    "line": line_number,
                    "from_level": old_level,
                    "to_level": new_level,
                    "changed": True,
                    "reason": decision["reason"],
                    "confidence": decision["confidence"],
                }
            )
        elif candidate["rule"] in {"heading-level-above-three", "heading-level-jump"}:
            unresolved.append({**candidate, "reason": "candidate-remained-unchanged"})
    final_text = "\n".join(lines)
    if normalized.endswith("\n"):
        final_text += "\n"
    return final_text, changed, unresolved


def _validate_heading_only(
    original_pages: dict[int, str], final_pages: dict[int, str]
) -> dict[str, Any]:
    errors: list[str] = []
    for page in sorted(original_pages):
        before = original_pages[page].replace("\r\n", "\n").splitlines()
        after = final_pages[page].replace("\r\n", "\n").splitlines()
        if len(before) != len(after):
            errors.append(f"第 {page} 页行数改变")
            continue
        for line_number, (old_line, new_line) in enumerate(zip(before, after), 1):
            if old_line == new_line:
                continue
            if not _same_visible_body(old_line, new_line):
                errors.append(f"第 {page} 页第 {line_number} 行非标题内容改变")
            if PROTECTED_RE.match(old_line):
                errors.append(f"第 {page} 页第 {line_number} 行受保护结构被修改")
    before_merged = "\n\n".join(original_pages[page].rstrip() for page in sorted(original_pages)) + "\n"
    after_merged = "\n\n".join(final_pages[page].rstrip() for page in sorted(final_pages)) + "\n"
    before_markers = [int(value) for value in PAGE_MARKER_RE.findall(before_merged)]
    after_markers = [int(value) for value in PAGE_MARKER_RE.findall(after_merged)]
    if before_markers != after_markers:
        errors.append("页面标记顺序改变")
    return {
        "passed": not errors,
        "heading_prefix_only": not errors,
        "page_markers_preserved": before_markers == after_markers,
        "errors": errors,
    }


def run_heading_repair(
    *, run_root: Path, timeout_seconds: int = 900, use_model: bool = True
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    document_temp = run_root / "document-temp.md"
    if not document_temp.is_file() or document_temp.stat().st_size == 0:
        raise HeadingRepairError(f"缺少 document-temp.md：{document_temp}")
    units = _page_units(run_root)
    expected_merge = _deterministic_merge(run_root, units)
    if document_temp.read_text(encoding="utf-8") != expected_merge:
        raise HeadingRepairError("document-temp.md 不是当前 page-units 的确定性合并")
    existing_report_path = run_root / "reports" / "heading-repair.json"
    existing_index_path = run_root / "index.md"
    if existing_report_path.is_file() and existing_index_path.is_file():
        existing = json.loads(existing_report_path.read_text(encoding="utf-8"))
        if (
            existing.get("status") == "passed"
            and existing.get("source_sha256") == sha256_file(document_temp)
            and existing.get("index_sha256") == sha256_file(existing_index_path)
            and (existing.get("validation") or {}).get("heading_prefix_only") is True
            and (existing.get("validation") or {}).get("page_markers_preserved") is True
        ):
            return existing
    original_pages = {
        page: path.read_text(encoding="utf-8") for page, path in units
    }
    candidates_by_page = {
        page: candidates
        for page, text in original_pages.items()
        if (candidates := find_heading_candidates(page, text))
    }
    final_pages = dict(original_pages)
    changes: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    model_pages: list[int] = []
    model_errors: list[dict[str, Any]] = []
    if candidates_by_page and use_model:
        for page, candidates in candidates_by_page.items():
            page_path = run_root / "page-units" / f"p{page:03d}.md"
            image_path = run_root / "assets" / f"page-{page:03d}.png"
            page_markdown = page_path.read_text(encoding="utf-8")
            decisions, error = _model_decisions(
                page=page,
                page_markdown=page_markdown,
                image_path=image_path,
                candidates=candidates,
                timeout_seconds=timeout_seconds,
            )
            model_pages.append(page)
            if error:
                model_errors.append({"page": page, "error": error})
            repaired, page_changes, page_unresolved = _apply_page_decisions(
                page=page,
                original=page_markdown,
                candidates=candidates,
                decisions=decisions,
            )
            final_pages[page] = repaired
            changes.extend(page_changes)
            unresolved.extend(page_unresolved)
    elif candidates_by_page:
        for page, candidates in candidates_by_page.items():
            page_markdown = final_pages[page]
            decisions = _deterministic_decisions(
                page_markdown=page_markdown,
                candidates=candidates,
            )
            repaired, page_changes, page_unresolved = _apply_page_decisions(
                page=page,
                original=page_markdown,
                candidates=candidates,
                decisions=decisions,
            )
            final_pages[page] = repaired
            changes.extend(page_changes)
            unresolved.extend(page_unresolved)

    validation = _validate_heading_only(original_pages, final_pages)
    if not validation["passed"]:
        raise HeadingRepairError(f"标题限定校验失败：{validation['errors']}")
    final_text = (
        "\n\n".join(
            _project_page_unit_image_path(run_root, final_pages[page], page).rstrip()
            for page, _path in units
        )
        + "\n"
    )
    index_path = run_root / "index.md"
    status = "needs-review" if unresolved or model_errors else "passed"
    if status == "passed":
        atomic_write_text(index_path, final_text)
    report = {
        "status": status,
        "source_path": str(document_temp),
        "source_sha256": sha256_file(document_temp),
        "index_path": str(index_path) if status == "passed" else None,
        "index_sha256": sha256_file(index_path) if status == "passed" else None,
        "model_called": bool(model_pages),
        "model": EXPECTED_MODEL if model_pages else None,
        "model_pages": model_pages,
        "candidate_count": sum(len(items) for items in candidates_by_page.values()),
        "changed": changes,
        "unresolved": unresolved,
        "model_errors": model_errors,
        "validation": validation,
    }
    reports = run_root / "reports"
    write_json(reports / "heading-repair.json", report)
    write_json(
        reports / "validation-result.json",
        {
            "status": status,
            "document_temp_sha256": report["source_sha256"],
            "index_sha256": report["index_sha256"],
            "heading_prefix_only": validation["heading_prefix_only"],
            "page_markers_preserved": validation["page_markers_preserved"],
            "errors": validation["errors"] + [item["error"] for item in model_errors],
        },
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="只做规则审计；存在异常时输出 needs-review，不调用模型",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_heading_repair(
            run_root=args.run_root,
            timeout_seconds=args.timeout_seconds,
            use_model=not args.no_model,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, HeadingRepairError) as exc:
        print(f"ERROR handled=true {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
