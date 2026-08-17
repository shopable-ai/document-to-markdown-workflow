#!/usr/bin/env python3
"""Repair one page-unit Markdown file through the LAN language model.

The adapter is intentionally page-scoped. It sends only the selected
Markdown file to Ollama over SSH, validates the returned reader layer, and
publishes an atomic in-place update only after the structural guard passes.
Online conversation fallback is owned by the workflow caller because this
adapter cannot invoke the host conversation model.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "references/prompts/markdown-content-repair-lan.zh.txt"
DEFAULT_MODEL = "qwen3.6:latest"
PROMPT_VERSION = "markdown-content-repair-lan-v1"
PAGE_NUMBER = re.compile(r"^<!--\s*page-number:\s*(\d+)\s*-->$", re.M)
IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
FORBIDDEN_READER_FIELDS = (
    "api_key",
    "authorization",
    "subscription_url",
    "bbox",
    "confidence",
    "evidence/",
    "manifest.yaml",
    "模型调用",
    "过程文件路径",
)
RELATION_BOUNDARY_MARKERS = ("Unknown/needs-review", "〔无法确认〕", "待核对项")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strip_fence(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _reader_atoms(value: str) -> Counter[str]:
    """Collect source characters/words so repair cannot invent reader content."""

    atoms: Counter[str] = Counter()
    atoms.update(re.findall(r"[\u3400-\u9fff]", value))
    atoms.update(token.casefold() for token in re.findall(r"[A-Za-z0-9_]+", value))
    return atoms


def _request(
    page_text: str,
    *,
    host: str,
    model: str,
    prompt_file: Path,
    timeout: int,
) -> tuple[str, str]:
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    payload = {
        "model": model,
        "system": "只处理当前一页 Markdown，不读取其他文件；只返回修复后的 Markdown。",
        "prompt": f"{prompt}\n\n当前页面 Markdown：\n---\n{page_text}\n---",
        "stream": False,
        "options": {"temperature": 0},
    }
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        host,
        "/usr/bin/curl",
        "-sS",
        "--max-time",
        str(timeout),
        "http://127.0.0.1:11434/api/generate",
        "-H",
        "Content-Type:application/json",
        "--data-binary",
        "@-",
    ]
    try:
        result = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=timeout + 20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return "unavailable", f"LAN repair request timed out: {exc}"
    except OSError as exc:
        return "unavailable", f"LAN repair request could not start: {exc}"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[-500:]
        return "unavailable", f"LAN repair SSH/curl failed with exit {result.returncode}: {detail}"
    try:
        response = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return "failed", f"LAN repair response was not JSON: {exc}"
    raw = response.get("response") if isinstance(response, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return "failed", "LAN repair response did not contain non-empty Markdown"
    return "complete", _strip_fence(raw)


def validate_repaired_markdown(original: str, repaired: str, page: int | None = None) -> list[str]:
    errors: list[str] = []
    if not repaired.strip():
        errors.append("repaired Markdown is empty")
        return errors
    original_page = PAGE_NUMBER.search(original)
    repaired_page = PAGE_NUMBER.search(repaired)
    if original_page and repaired_page and original_page.group(1) != repaired_page.group(1):
        errors.append("page-number metadata changed")
    if original_page and not repaired_page:
        errors.append("page-number metadata was removed")
    if page is not None and repaired_page and int(repaired_page.group(1)) != page:
        errors.append(f"repaired page-number is not {page}")
    original_images = IMAGE_LINK.findall(original)
    repaired_images = IMAGE_LINK.findall(repaired)
    for link in original_images:
        if link not in repaired_images:
            errors.append(f"original page image link was removed: {link}")
    for marker in RELATION_BOUNDARY_MARKERS:
        if marker in original and marker not in repaired:
            errors.append(f"uncertain reader boundary was removed: {marker}")
    for marker in FORBIDDEN_READER_FIELDS:
        if marker in repaired:
            errors.append(f"engineering field leaked into reader Markdown: {marker}")
    if re.search(r"^\s*```(?:json|yaml)\b", repaired, re.M | re.I):
        errors.append("structured diagnostic fence leaked into reader Markdown")
    original_atoms = _reader_atoms(original)
    repaired_atoms = _reader_atoms(repaired)
    invented = sorted(
        atom for atom, count in repaired_atoms.items()
        if count > original_atoms.get(atom, 0)
    )
    if invented:
        errors.append(f"repaired Markdown introduced reader content: {', '.join(invented[:12])}")
    return errors


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=f".{os.getpid()}.tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text.rstrip() + "\n")
    temporary.replace(path)


def repair_page(
    page_unit: Path,
    *,
    page: int | None = None,
    host: str = "mac96",
    model: str = DEFAULT_MODEL,
    prompt_file: Path = DEFAULT_PROMPT,
    timeout: int = 180,
    write_back: bool = True,
) -> dict[str, Any]:
    page_unit = page_unit.expanduser().resolve()
    original = page_unit.read_text(encoding="utf-8")
    result: dict[str, Any] = {
        "contract": "markdown-content-repair",
        "version": "0.4.0",
        "page_unit": str(page_unit),
        "page": page,
        "prompt_version": PROMPT_VERSION,
        "transport": "ssh",
        "target": host,
        "model": model,
        "input_sha256": sha256_text(original),
        "requested_at": now(),
        "status": "deferred",
        "write_back": False,
        "diagnostics": [],
        "changes": [],
    }
    status, value = _request(original, host=host, model=model, prompt_file=prompt_file, timeout=timeout)
    result["model_status"] = status
    if status != "complete":
        result["diagnostics"].append(value)
        result["next_action"] = "保留原文件；仅在明确授权时交给当前在线模型回退一次"
        return result
    repaired = str(value)
    errors = validate_repaired_markdown(original, repaired, page)
    result["output_sha256"] = sha256_text(repaired)
    result["validation_errors"] = errors
    if errors:
        result["diagnostics"].extend(errors)
        result["next_action"] = "保留原文件并进入 needs-review"
        return result
    result["status"] = "applied"
    result["write_back"] = bool(write_back)
    result["changes"] = [{"before": original, "after": repaired, "operation": "lan-model-page-unit-repair"}]
    if write_back and repaired != original:
        _atomic_write(page_unit, repaired)
    result["final_sha256"] = sha256_text(repaired if write_back else original)
    result["next_action"] = "局域网修复成功；不再调用在线模型"
    return result


def write_review(path: Path, package_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the package-level diff ledger for page-scoped repair results."""

    items: list[dict[str, Any]] = []
    model_calls: list[dict[str, Any]] = []
    page_results: list[dict[str, Any]] = []
    for result in results:
        page = result.get("page")
        page_label = f"p{int(page):03d}" if isinstance(page, int) else "pNNN"
        change = (result.get("changes") or [{}])[0]
        before = change.get("before")
        after = change.get("after")
        status = "applied" if result.get("status") == "applied" else "deferred"
        items.append({
            "id": f"page-unit-{page_label}",
            "kind": "paragraph",
            "source_occurrence": None,
            "section_id": f"page-{int(page):03d}" if isinstance(page, int) else "page-unknown",
            "visual_asset_id": None,
            "reader_text": after if status == "applied" else before,
            "source_text": before,
            "candidate_text": after,
            "proposed_text": after,
            "repair": {
                "operation": "page-unit-structure-repair" if after is not None else "none",
                "basis": "markdown-context",
                "before": before,
                "after": after,
                "evidence": [f"evidence/markdown-content-repair-{page_label}.json"],
            },
            "rationale": "局域网语言模型只整理当前 page-unit，不读取图片或其他页面。",
            "status": status,
            "decision_note": result.get("next_action"),
        })
        model_calls.append({
            "page": page,
            "route": "lan-language-model",
            "transport": result.get("transport"),
            "target": result.get("target"),
            "model": result.get("model"),
            "status": result.get("model_status"),
        })
        page_results.append({
            "page": page,
            "status": result.get("status"),
            "input_sha256": result.get("input_sha256"),
            "output_sha256": result.get("output_sha256"),
            "final_sha256": result.get("final_sha256"),
            "write_back": result.get("write_back", False),
            "diagnostics": result.get("diagnostics", []),
        })
    payload = {
        "contract": "markdown-content-repair",
        "version": "0.4.0",
        "package_id": package_id,
        "document": "index.md",
        "generated_from": [f"page-units/p{int(item.get('page')):03d}.md" for item in results if isinstance(item.get("page"), int)],
        "status": "applied" if results and all(item.get("status") == "applied" for item in results) else "deferred",
        "route": {
            "primary": "lan-language-model",
            "online_fallback": "explicit-current-task-only-after-lan-failure",
            "lan_success_stops_fallback": True,
        },
        "model_calls": model_calls,
        "page_results": page_results,
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-unit", type=Path, required=True)
    parser.add_argument("--page", type=int)
    parser.add_argument("--host", default="mac96")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--no-write-back", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        result = repair_page(
            args.page_unit,
            page=args.page,
            host=args.host,
            model=args.model,
            prompt_file=args.prompt_file,
            timeout=args.timeout,
            write_back=not args.no_write_back,
        )
    except (OSError, ValueError) as exc:
        print(f"markdown content repair failed: {exc}", file=__import__("sys").stderr)
        return 2
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "applied" else 1


if __name__ == "__main__":
    raise SystemExit(main())
