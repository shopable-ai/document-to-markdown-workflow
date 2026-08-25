#!/usr/bin/env python3
"""Conservative post-merge Markdown formatting for pure-text reader output."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from adapters.markdown_model_router import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_DOTENV_PATH,
        ModelRouteError,
        load_dotenv,
        load_routing_config,
        protect_markdown_structures,
        restore_markdown_structures,
        run_model_routes,
        validate_model_markdown,
    )
except ModuleNotFoundError:  # Direct script execution from adapters/.
    from markdown_model_router import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_DOTENV_PATH,
        ModelRouteError,
        load_dotenv,
        load_routing_config,
        protect_markdown_structures,
        restore_markdown_structures,
        run_model_routes,
        validate_model_markdown,
    )


ROOT = Path(__file__).resolve().parents[1]
SKILL_POLICY_PATH = ROOT / "skills" / "markdown-format-repair" / "SKILL.md"
WORKFLOW_PROMPT_PATH = ROOT / "references" / "prompts" / "markdown-post-merge-formatting.zh.txt"
RUNTIME_PROMPT_PATH = (
    ROOT / "references" / "prompts" / "markdown-post-merge-formatting-runtime.zh.txt"
)

PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s*:\s*\d+\s*-->$")
ATX_RE = re.compile(r"^#{1,6}\s+")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)")
TABLE_RE = re.compile(r"^\s*\|")
BLOCKQUOTE_RE = re.compile(r"^\s*>")
IMAGE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")
FENCE_RE = re.compile(r"^\s*```")
HORIZONTAL_RULE_RE = re.compile(r"^\s*---+\s*$")
ENDS_WITH_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9,.;:!?)]*$")
STARTS_WITH_ASCII_WORD = re.compile(r"^[A-Za-z0-9(]")
MODEL_MODES = {"deterministic", "model-optional", "model-required"}


class PostMergeModelError(ModelRouteError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _is_protected_line(line: str, *, fenced: bool) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if fenced:
        return True
    return bool(
        PAGE_MARKER_RE.fullmatch(stripped)
        or ATX_RE.match(line)
        or LIST_RE.match(line)
        or TABLE_RE.match(line)
        or BLOCKQUOTE_RE.match(line)
        or IMAGE_RE.match(line)
        or FENCE_RE.match(line)
        or HORIZONTAL_RULE_RE.match(line)
    )


def _join_plain_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    merged = lines[0].strip()
    for raw in lines[1:]:
        current = raw.strip()
        if not current:
            continue
        if ENDS_WITH_ASCII_WORD.search(merged) and STARTS_WITH_ASCII_WORD.search(current):
            merged = f"{merged} {current}"
        else:
            merged = f"{merged}{current}"
    return merged


def format_markdown_text(text: str) -> tuple[str, dict[str, Any]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    output: list[str] = []
    paragraph_groups = 0
    merged_plain_lines = 0
    duplicate_plain_lines_removed = 0
    blank_line_collapses = 0
    fenced = False
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if FENCE_RE.match(line):
            fenced = not fenced
            if stripped or (output and output[-1] != ""):
                output.append(line)
            index += 1
            continue
        if not stripped:
            if output and output[-1] == "":
                blank_line_collapses += 1
            else:
                output.append("")
            index += 1
            continue
        if _is_protected_line(line, fenced=fenced):
            output.append(line)
            index += 1
            continue

        group: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip():
                break
            if FENCE_RE.match(candidate) or _is_protected_line(candidate, fenced=fenced):
                break
            cleaned = candidate.strip()
            if group and cleaned == group[-1]:
                duplicate_plain_lines_removed += 1
            else:
                group.append(cleaned)
            index += 1
        if group:
            paragraph_groups += 1
            merged_plain_lines += max(0, len(group) - 1)
            output.append(_join_plain_lines(group))
        else:
            output.append(line)
            index += 1

    while output and output[0] == "":
        output.pop(0)
        blank_line_collapses += 1
    while output and output[-1] == "":
        output.pop()
        blank_line_collapses += 1

    formatted = "\n".join(output) + "\n"
    stats = {
        "status": "passed",
        "paragraph_groups": paragraph_groups,
        "merged_plain_lines": merged_plain_lines,
        "duplicate_plain_lines_removed": duplicate_plain_lines_removed,
        "blank_line_collapses": blank_line_collapses,
        "changed": formatted != normalized if normalized.endswith("\n") else formatted != normalized + "\n",
        "policy_sources": [
            str(path)
            for path in (SKILL_POLICY_PATH, WORKFLOW_PROMPT_PATH)
            if path.is_file()
        ],
        "notes": [
            "保守格式化：只做段落合并、重复纯文本去重、空行归一化。",
            "不改图片路径、不改页面顺序、不改标题文字。",
        ],
    }
    return formatted, stats


def _split_model_chunks(markdown: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("MARKDOWN_FORMATTER_MAX_CHARS must be positive")
    markers = list(re.finditer(r"(?m)^<!--\s*page\s*:\s*\d+\s*-->\s*$", markdown))
    if not markers:
        return [markdown]
    blocks: list[str] = []
    for index, marker in enumerate(markers):
        start = 0 if index == 0 else marker.start()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(markdown)
        blocks.append(markdown[start:end])
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) > max_chars:
            chunks.append(current)
            current = block
        else:
            current += block
    if current:
        chunks.append(current)
    return chunks


def _model_prompt(policy: str, markdown: str, *, chunk: int, chunk_count: int) -> str:
    return (
        f"{policy.rstrip()}\n\n"
        "# 当前 Workflow 的强制覆盖约束\n\n"
        "以下约束优先于上方通用提示词：\n"
        "- 必须原样保留所有 `<!-- page:N -->` 页面标记及其顺序。\n"
        "- 必须原样保留所有 Markdown 图片路径及其顺序。\n"
        "- 所有 `[[WF_PAGE_MARKER_NNNN]]` 和 `[[WF_IMAGE_REFERENCE_NNNN]]` 是受保护占位符，必须原样保留在原位置。\n"
        "- 不得总结、截断或补写正文，不得输出解释或代码围栏。\n"
        "- 只输出整理后的 Markdown。\n\n"
        f"当前分块：{chunk}/{chunk_count}\n\n"
        "# 待整理 Markdown\n\n"
        f"{markdown}"
    )


def _format_with_models(
    *, markdown: str, policy: str, config_path: Path, mode: str
) -> tuple[str, dict[str, Any]]:
    max_chars = int(os.environ.get("MARKDOWN_FORMATTER_MAX_CHARS", "12000"))
    chunks = _split_model_chunks(markdown, max_chars)
    config = load_routing_config(config_path)
    outputs: list[str] = []
    attempts: list[dict[str, Any]] = []
    selected_routes: list[str] = []
    deterministic_fallback_chunks: list[int] = []
    for index, chunk_text in enumerate(chunks, 1):
        protected_text, protected_mapping = protect_markdown_structures(chunk_text)
        result = run_model_routes(
            markdown=protected_text,
            prompt=_model_prompt(
                policy,
                protected_text,
                chunk=index,
                chunk_count=len(chunks),
            ),
            config=config,
        )
        attempts.extend(
            {**attempt, "chunk": index, "chunk_count": len(chunks)}
            for attempt in result.attempts
        )
        if result.status == "passed" and result.markdown is not None:
            restored = restore_markdown_structures(
                result.markdown,
                protected_mapping,
            )
            restored_errors = validate_model_markdown(chunk_text, restored)
            if restored_errors:
                attempts.append(
                    {
                        "route": result.route_id,
                        "transport": result.transport,
                        "model": result.model,
                        "profile": result.profile,
                        "status": "rejected-after-restore",
                        "chunk": index,
                        "chunk_count": len(chunks),
                        "error": "；".join(restored_errors),
                    }
                )
                if mode == "model-required":
                    raise PostMergeModelError(
                        f"restored model output failed for chunk {index}/{len(chunks)}",
                        {
                            "model_applied": bool(selected_routes),
                            "selected_routes": list(dict.fromkeys(selected_routes)),
                            "attempts": attempts,
                            "chunk_count": len(chunks),
                            "deterministic_fallback_chunks": [],
                            "method": "failed",
                        },
                    )
                deterministic, _stats = format_markdown_text(chunk_text)
                outputs.append(deterministic)
                deterministic_fallback_chunks.append(index)
                continue
            normalized_restored, _normalization_stats = format_markdown_text(restored)
            normalized_errors = validate_model_markdown(chunk_text, normalized_restored)
            if normalized_errors:
                raise PostMergeModelError(
                    f"post-model normalization failed for chunk {index}/{len(chunks)}: "
                    + "；".join(normalized_errors),
                    {
                        "model_applied": bool(selected_routes),
                        "selected_routes": list(dict.fromkeys(selected_routes)),
                        "attempts": attempts,
                        "chunk_count": len(chunks),
                        "deterministic_fallback_chunks": deterministic_fallback_chunks,
                        "method": "failed",
                    },
                )
            outputs.append(normalized_restored)
            if result.route_id:
                selected_routes.append(result.route_id)
            continue
        if mode == "model-required":
            raise PostMergeModelError(
                f"all model routes failed for chunk {index}/{len(chunks)}",
                {
                    "model_applied": bool(selected_routes),
                    "selected_routes": list(dict.fromkeys(selected_routes)),
                    "attempts": attempts,
                    "chunk_count": len(chunks),
                    "deterministic_fallback_chunks": [],
                    "method": "failed",
                },
            )
        deterministic, _stats = format_markdown_text(chunk_text)
        outputs.append(deterministic)
        deterministic_fallback_chunks.append(index)
    formatted = "".join(part.rstrip() + "\n" for part in outputs)
    guard_errors = validate_model_markdown(markdown, formatted)
    if guard_errors:
        raise PostMergeModelError(
            "combined model output failed guard: " + "；".join(guard_errors),
            {
                "model_applied": bool(selected_routes),
                "selected_routes": list(dict.fromkeys(selected_routes)),
                "attempts": attempts,
                "chunk_count": len(chunks),
                "deterministic_fallback_chunks": deterministic_fallback_chunks,
                "method": "failed",
            },
        )
    unique_routes = list(dict.fromkeys(selected_routes))
    return formatted, {
        "model_applied": bool(selected_routes),
        "selected_routes": unique_routes,
        "attempts": attempts,
        "chunk_count": len(chunks),
        "deterministic_fallback_chunks": deterministic_fallback_chunks,
        "method": (
            "model-plus-deterministic-normalization"
            if selected_routes and not deterministic_fallback_chunks
            else "model-and-deterministic-fallback"
            if selected_routes
            else "deterministic-fallback"
        ),
    }


def format_run_root(
    *,
    run_root: Path,
    source: Path,
    destination: Path | None = None,
    mode: str | None = None,
    config_path: Path | None = None,
    dotenv_path: Path | None = None,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    source_path = source.expanduser().resolve()
    target_path = (destination or (run_root / "index.md")).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少待格式化 Markdown：{source_path}")
    original = source_path.read_text(encoding="utf-8")
    source_size_bytes = source_path.stat().st_size
    loaded_env = load_dotenv((dotenv_path or DEFAULT_DOTENV_PATH).expanduser().resolve())
    selected_mode = mode or os.environ.get("MARKDOWN_FORMATTER_MODE", "deterministic")
    if selected_mode not in MODEL_MODES:
        raise ValueError(f"unsupported Markdown formatter mode: {selected_mode}")
    model_report: dict[str, Any] = {
        "model_applied": False,
        "selected_routes": [],
        "attempts": [],
        "chunk_count": 0,
        "deterministic_fallback_chunks": [],
        "method": "deterministic",
    }
    if selected_mode == "deterministic":
        formatted, stats = format_markdown_text(original)
    else:
        policy = RUNTIME_PROMPT_PATH.read_text(encoding="utf-8")
        try:
            formatted, model_report = _format_with_models(
                markdown=original,
                policy=policy,
                config_path=(config_path or DEFAULT_CONFIG_PATH).expanduser().resolve(),
                mode=selected_mode,
            )
            stats = {
                "status": "passed",
                "changed": formatted != original,
                "paragraph_groups": None,
                "merged_plain_lines": None,
                "duplicate_plain_lines_removed": None,
                "blank_line_collapses": None,
                "policy_sources": [str(WORKFLOW_PROMPT_PATH), str(RUNTIME_PROMPT_PATH)],
                "notes": ["模型按正式提示词整理；输出通过页面标记和图片引用守卫。"],
            }
        except ModelRouteError as exc:
            if isinstance(exc, PostMergeModelError):
                model_report = exc.report
            if selected_mode == "model-required":
                failure_report = {
                    "status": "failed",
                    "source_path": str(source_path),
                    "output_path": str(target_path),
                    "changed": False,
                    "mode": selected_mode,
                    "method": "failed",
                    "model_applied": model_report["model_applied"],
                    "selected_routes": model_report["selected_routes"],
                    "model_attempts": model_report["attempts"],
                    "chunk_count": model_report["chunk_count"],
                    "deterministic_fallback_chunks": model_report[
                        "deterministic_fallback_chunks"
                    ],
                    "source_size_bytes": source_size_bytes,
                    "output_size_bytes": target_path.stat().st_size
                    if target_path.is_file()
                    else None,
                    "policy_sources": [str(WORKFLOW_PROMPT_PATH), str(RUNTIME_PROMPT_PATH)],
                    "error": str(exc),
                    "loaded_env_keys": loaded_env,
                }
                _write_json(
                    run_root / "reports" / "markdown-post-merge-formatting.json",
                    failure_report,
                )
                raise
            formatted, stats = format_markdown_text(original)
            model_report = {
                **model_report,
                "method": "deterministic-fallback",
                "configuration_error": str(exc),
            }
    _atomic_write_text(target_path, formatted)
    report = {
        "status": stats["status"],
        "source_path": str(source_path),
        "output_path": str(target_path),
        "changed": stats["changed"],
        "mode": selected_mode,
        "method": model_report["method"],
        "model_applied": model_report["model_applied"],
        "selected_routes": model_report["selected_routes"],
        "model_attempts": model_report["attempts"],
        "chunk_count": model_report["chunk_count"],
        "deterministic_fallback_chunks": model_report["deterministic_fallback_chunks"],
        "source_size_bytes": source_size_bytes,
        "output_size_bytes": target_path.stat().st_size,
        "policy_sources": stats["policy_sources"],
        "paragraph_groups": stats["paragraph_groups"],
        "merged_plain_lines": stats["merged_plain_lines"],
        "duplicate_plain_lines_removed": stats["duplicate_plain_lines_removed"],
        "blank_line_collapses": stats["blank_line_collapses"],
        "notes": stats["notes"],
        "loaded_env_keys": loaded_env,
    }
    if model_report.get("configuration_error"):
        report["configuration_error"] = model_report["configuration_error"]
    _write_json(run_root / "reports" / "markdown-post-merge-formatting.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--mode", choices=sorted(MODEL_MODES))
    parser.add_argument("--route", help="模型 route id；设置后禁用自动回退")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_DOTENV_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.route:
        os.environ["MARKDOWN_FORMATTER_ROUTE"] = args.route
    source = args.source or (args.run_root / "document-temp.md")
    try:
        report = format_run_root(
            run_root=args.run_root,
            source=source,
            destination=args.destination,
            mode=args.mode,
            config_path=args.config,
            dotenv_path=args.env_file,
        )
    except (OSError, ValueError, TypeError, ModelRouteError) as exc:
        print(f"ERROR handled=true {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
