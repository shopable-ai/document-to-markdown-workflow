#!/usr/bin/env python3
"""Analyze one current image through the LAN mac96 vision model.

The adapter unifies image text extraction and visual-structure review.  The
default ``full``/``single`` path uses one model request and returns both
layers.  ``dual`` is an explicit high-risk path with specialized text and
layout requests.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "references/prompts/vision-page-analysis-mac96.zh.txt"
DEFAULT_MODEL = "qwen3-vl:30b-a3b-instruct"
PROMPT_VERSION = "vision-page-analysis-v8"
MODES = {"text", "layout", "full"}
PASS_STRATEGIES = {"single", "dual"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_fence(value: str, *, language: str = "") -> str:
    text = value.strip()
    suffix = re.escape(language) if language else r"(?:json|markdown|md)?"
    match = re.fullmatch(rf"```{suffix}\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _base_evidence(
    image: Path,
    *,
    asset_id: str,
    source_fingerprint: str,
    page_or_region: str,
    prompt_version: str,
    model: str,
    mode: str,
    pass_strategy: str,
) -> dict[str, Any]:
    image_hash = sha256(image) if image.is_file() else None
    return {
        "contract": "vision-page-analysis",
        "version": "0.3.0",
        "input_asset_id": asset_id,
        "source_fingerprint": source_fingerprint,
        "image_sha256": image_hash,
        "page_or_region": page_or_region,
        "prompt_version": prompt_version,
        "transport": "ssh",
        "target": "mac96",
        "model": model,
        "mode_requested": mode,
        "pass_strategy_requested": pass_strategy,
        "pass_strategy_used": None,
        "model_call_count": 0,
        "text": {
            "status": "not-requested" if mode == "layout" else "unavailable",
            "source": "not-requested" if mode == "layout" else "mac96",
            "markdown": "",
            "structure_inventory": {},
            "diagnostics": [],
        },
        "visual": {
            "status": "not-requested" if mode == "text" else "unavailable",
            "source": "not-requested" if mode == "text" else "mac96",
            "visual_analysis": {},
            "diagnostics": [],
        },
        "requests": [],
        "status": "failed",
        "diagnostics": [],
    }


def _request(
    image: Path,
    *,
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int,
) -> tuple[str, str, dict[str, Any]]:
    request = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "images": [base64.b64encode(image.read_bytes()).decode("ascii")],
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
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=timeout + 20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return "unavailable", f"mac96 SSH request timed out: {exc}", {
            "wall_seconds": round(time.monotonic() - started, 3),
        }
    except OSError as exc:
        return "unavailable", f"mac96 SSH could not start: {exc}", {
            "wall_seconds": round(time.monotonic() - started, 3),
        }
    wall_seconds = round(time.monotonic() - started, 3)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[-500:]
        return "unavailable", f"mac96 SSH/curl failed with exit {result.returncode}: {detail}", {
            "wall_seconds": wall_seconds,
        }
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return "failed", f"mac96 response was not JSON: {exc}", {"wall_seconds": wall_seconds}
    if not isinstance(payload, dict):
        return "failed", "mac96 response was not an object", {"wall_seconds": wall_seconds}
    metrics = {"wall_seconds": wall_seconds}
    for key in (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    ):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = value
    if isinstance(payload.get("done_reason"), str):
        metrics["done_reason"] = payload["done_reason"]
    response = payload.get("response")
    if not isinstance(response, str) or not response.strip():
        return "failed", "mac96 response did not contain a non-empty response", metrics
    return "complete", response.strip(), metrics


def _system_prompt(mode: str) -> str:
    base = (
        "你是严格的页面视觉理解器。只阅读本次请求附带的这一张图片。"
        "不得使用 OCR，不得读取或参考其他文件、前后图片或历史回答。"
        f"当前输出模式是 {mode}。"
        "只输出当前模式规定的结果，不输出分析过程，不把模型常识写成图片事实。"
    )
    if mode == "full":
        base += (
            "markdown 字段必须是具有真实 Markdown 语法的字符串，不是平铺纯文本。"
            "图片中确有标题时必须在标题原文前写 # 加空格；列表项必须写 - 加空格。"
            "先生成 markdown，再从该字符串反向统计 structure_inventory。"
            "title_levels 只允许使用 #、##、###、####、#####、######，不得写 1、2、3。"
            "清单和 markdown 不一致会被程序直接判失败。"
        )
    return base


def _user_prompt(template: str, mode: str, page_or_region: str) -> str:
    mode_rules = {
        "text": "只输出 Markdown 文字，不要输出 JSON。",
        "layout": "只输出一个 JSON 对象，不要输出 Markdown 围栏或解释。",
        "full": "只输出一个 JSON 对象，包含 markdown 和 visual_analysis，不要输出 Markdown 围栏或解释。",
    }
    return (
        f"{template.strip()}\n\n"
        f"当前页/区域标识：{page_or_region}\n"
        f"当前模式：{mode}\n"
        f"模式输出约束：{mode_rules[mode]}"
    )


def _normalise_visual(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("visual_analysis")
    if not isinstance(value, dict):
        value = payload.get("visual")
    if not isinstance(value, dict):
        value = payload.get("layout")
    if not isinstance(value, dict):
        value = payload
    relationships = value.get("relationships")
    if relationships is None:
        relationships = value.get("relations", [])
    page_content_type = value.get("page_content_type")
    if page_content_type not in {"text-only", "text-and-visual", "visual-only", "unknown"}:
        page_content_type = "unknown"
    layout_dependency = value.get("layout_dependency")
    if layout_dependency not in {"none", "markdown-representable", "image-required", "unknown"}:
        layout_dependency = "unknown"
    semantic_visuals = value.get("semantic_visuals")
    if not isinstance(semantic_visuals, list):
        semantic_visuals = []
    ignored_elements = value.get("ignored_elements")
    if not isinstance(ignored_elements, list):
        ignored_elements = []
    reader_image_policy = value.get("reader_image_policy")
    if not isinstance(reader_image_policy, dict):
        reader_image_policy = {}
    action = reader_image_policy.get("action")
    reason = reader_image_policy.get("reason")
    confidence = reader_image_policy.get("confidence")
    if action not in {"omit", "retain", "needs-review"}:
        action = "retain"
    if not isinstance(reason, str) or not reason.strip():
        reason = "classification-missing"
    if confidence not in {"high", "medium", "low", "unknown"}:
        confidence = "unknown"
    structure_inventory = value.get("structure_inventory")
    if not isinstance(structure_inventory, dict):
        structure_inventory = {}
    title_levels = structure_inventory.get("title_levels")
    header_footer_candidates = structure_inventory.get("header_footer_candidates")
    cross_page_continuity = structure_inventory.get("cross_page_continuity")
    if not isinstance(title_levels, list):
        title_levels = []
    if not isinstance(header_footer_candidates, list):
        header_footer_candidates = []
    if cross_page_continuity not in {"complete", "needs-review", "unknown"}:
        cross_page_continuity = "unknown"

    def non_negative_count(key: str) -> int:
        count = structure_inventory.get(key)
        return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0

    return {
        "page_content_type": page_content_type,
        "layout_dependency": layout_dependency,
        "semantic_visuals": semantic_visuals,
        "ignored_elements": ignored_elements,
        "reader_image_policy": {
            "action": action,
            "reason": reason,
            "confidence": confidence,
        },
        "structure_inventory": {
            "title_levels": title_levels,
            "paragraph_count": non_negative_count("paragraph_count"),
            "list_count": non_negative_count("list_count"),
            "toc_entry_count": non_negative_count("toc_entry_count"),
            "header_footer_candidates": header_footer_candidates,
            "cross_page_continuity": cross_page_continuity,
        },
        "visual_forms": value.get("visual_forms", []),
        "regions": value.get("regions", []),
        "relationships": relationships if isinstance(relationships, list) else [],
        "tables_or_charts": value.get("tables_or_charts", []),
        "unknown_forms": value.get("unknown_forms", []),
        "coverage_notes": value.get("coverage_notes", ""),
        "uncertainties": value.get("uncertainties", []),
    }


def _derive_markdown_structure_inventory(markdown: str) -> dict[str, Any]:
    heading_levels = re.findall(r"(?m)^(#{1,6})\s+\S", markdown)
    list_item_count = len(re.findall(r"(?m)^\s*(?:[-+*]\s+|\d+[.)]\s+)\S", markdown))
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]
    paragraph_count = sum(
        1
        for block in blocks
        if not re.match(r"^(?:#{1,6}\s+|[-+*]\s+|\d+[.)]\s+)", block)
    )
    heading_text = re.findall(r"(?m)^#{1,6}\s+(.+)$", markdown)
    toc_entry_count = 0
    if any(text.strip() == "目录" for text in heading_text):
        toc_entry_count = max(0, len(heading_text) - 1) + list_item_count
    return {
        "derived_from": "actual-markdown-syntax",
        "title_levels": heading_levels,
        "paragraph_count": paragraph_count,
        "list_count": list_item_count,
        "toc_entry_count": toc_entry_count,
        "header_footer_candidates": [],
        "cross_page_continuity": "unknown",
    }


def _markdown_structure_mismatches(markdown: str, visual: dict[str, Any]) -> list[str]:
    """Return contradictions between declared structure and emitted Markdown syntax."""
    inventory = visual.get("structure_inventory")
    if not isinstance(inventory, dict):
        return ["structure_inventory is missing"]
    actual_headings = re.findall(r"(?m)^(#{1,6})\s+\S", markdown)
    declared_headings = inventory.get("title_levels")
    if not isinstance(declared_headings, list):
        return ["structure_inventory.title_levels must be an array"]
    declared_headings = [item for item in declared_headings if isinstance(item, str)]
    mismatches: list[str] = []
    if actual_headings != declared_headings:
        mismatches.append(
            f"title_levels declared {declared_headings!r} but Markdown contains {actual_headings!r}"
        )
    actual_list_items = len(re.findall(r"(?m)^\s*(?:[-+*]\s+|\d+[.)]\s+)\S", markdown))
    declared_list_items = inventory.get("list_count")
    if not isinstance(declared_list_items, int) or isinstance(declared_list_items, bool) or declared_list_items < 0:
        mismatches.append("structure_inventory.list_count must be a non-negative integer")
    elif actual_list_items != declared_list_items:
        mismatches.append(
            f"list_count declared {declared_list_items} but Markdown contains {actual_list_items} list items"
        )
    return mismatches


def _parse_pass(mode: str, raw: str) -> tuple[str, Any]:
    if mode == "text":
        markdown = _strip_fence(raw)
        if not markdown:
            return "failed", "text response was empty"
        return "complete", markdown
    try:
        payload = json.loads(_strip_fence(raw, language="json"))
    except json.JSONDecodeError as exc:
        return "failed", f"{mode} response was not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return "failed", f"{mode} response was not a JSON object"
    if mode == "layout":
        visual = _normalise_visual(payload)
        if not any(visual[key] for key in ("semantic_visuals", "ignored_elements", "visual_forms", "regions", "relationships", "tables_or_charts", "unknown_forms", "coverage_notes", "uncertainties")) and visual["page_content_type"] == "unknown" and visual["reader_image_policy"]["reason"] == "classification-missing":
            return "failed", "layout response did not contain visual analysis fields"
        return "complete", visual
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        markdown = payload.get("candidate_markdown")
    visual_payload = payload.get("visual_analysis")
    if not isinstance(markdown, str) or not markdown.strip() or not isinstance(visual_payload, dict):
        return "failed", "full response must contain non-empty markdown and visual_analysis"
    if not isinstance(visual_payload.get("structure_inventory"), dict):
        return "failed", "full response must contain visual_analysis.structure_inventory"
    result = {"markdown": _strip_fence(markdown), "visual": _normalise_visual(payload)}
    mismatches = _markdown_structure_mismatches(result["markdown"], result["visual"])
    if mismatches:
        return "failed", "structure-inventory-markdown-mismatch: " + "; ".join(mismatches)
    return "complete", result


def _record_pass(
    image: Path,
    *,
    label: str,
    host: str,
    model: str,
    template: str,
    page_or_region: str,
    timeout: int,
) -> dict[str, Any]:
    requested_at = now()
    status, raw_or_error, metrics = _request(
        image,
        host=host,
        model=model,
        system_prompt=_system_prompt(label),
        user_prompt=_user_prompt(template, label, page_or_region),
        timeout=timeout,
    )
    record: dict[str, Any] = {
        "pass": label,
        "requested_at": requested_at,
        "prompt_version": PROMPT_VERSION,
        "status": status,
        "finished_at": now(),
        "metrics": metrics,
    }
    if status != "complete":
        record["diagnostics"] = [raw_or_error]
        return record
    record["raw_response"] = raw_or_error
    parsed_status, parsed_or_error = _parse_pass(label, raw_or_error)
    record["status"] = parsed_status
    if parsed_status == "complete":
        record["result"] = parsed_or_error
    else:
        record["diagnostics"] = [parsed_or_error]
    return record


def _apply_pass(evidence: dict[str, Any], record: dict[str, Any]) -> None:
    label = record["pass"]
    evidence["requests"].append(record)
    if label == "text":
        evidence["text"]["status"] = record["status"]
        evidence["text"]["source"] = "mac96"
        if record["status"] == "complete":
            evidence["text"]["markdown"] = record["result"]
            evidence["text"]["structure_inventory"] = _derive_markdown_structure_inventory(record["result"])
        else:
            evidence["text"]["diagnostics"].extend(record.get("diagnostics", []))
    elif label == "layout":
        evidence["visual"]["status"] = record["status"]
        evidence["visual"]["source"] = "mac96"
        if record["status"] == "complete":
            evidence["visual"]["visual_analysis"] = record["result"]
        else:
            evidence["visual"]["diagnostics"].extend(record.get("diagnostics", []))
    else:
        evidence["text"]["status"] = record["status"]
        evidence["text"]["source"] = "mac96"
        evidence["visual"]["status"] = record["status"]
        evidence["visual"]["source"] = "mac96"
        if record["status"] == "complete":
            evidence["text"]["markdown"] = record["result"]["markdown"]
            evidence["text"]["structure_inventory"] = _derive_markdown_structure_inventory(record["result"]["markdown"])
            evidence["visual"]["visual_analysis"] = record["result"]["visual"]
        else:
            diagnostics = record.get("diagnostics", [])
            evidence["text"]["diagnostics"].extend(diagnostics)
            evidence["visual"]["diagnostics"].extend(diagnostics)


def _overall_status(mode: str, evidence: dict[str, Any]) -> str:
    requested = []
    if mode in {"text", "full"}:
        requested.append(evidence["text"]["status"])
    if mode in {"layout", "full"}:
        requested.append(evidence["visual"]["status"])
    if requested and all(status == "complete" for status in requested):
        return "complete"
    if any(status == "complete" for status in requested):
        return "partial"
    if any(status == "unavailable" for status in requested):
        return "unavailable"
    return "failed"


def analyze(
    image: Path,
    *,
    asset_id: str,
    source_fingerprint: str,
    page_or_region: str,
    mode: str = "full",
    pass_strategy: str = "single",
    host: str = "mac96",
    model: str = DEFAULT_MODEL,
    prompt_file: Path = DEFAULT_PROMPT,
    timeout: int = 480,
) -> dict[str, Any]:
    image = image.expanduser().resolve()
    prompt_file = prompt_file.expanduser().resolve()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    if pass_strategy not in PASS_STRATEGIES:
        raise ValueError(f"pass_strategy must be one of {sorted(PASS_STRATEGIES)}")
    evidence = _base_evidence(
        image,
        asset_id=asset_id,
        source_fingerprint=source_fingerprint,
        page_or_region=page_or_region,
        prompt_version=PROMPT_VERSION,
        model=model,
        mode=mode,
        pass_strategy=pass_strategy,
    )
    if not image.is_file():
        evidence["status"] = "failed"
        evidence["diagnostics"].append(f"image does not exist: {image}")
        return evidence
    try:
        template = prompt_file.read_text(encoding="utf-8")
    except OSError as exc:
        evidence["status"] = "failed"
        evidence["diagnostics"].append(f"prompt could not be read: {exc}")
        return evidence

    if pass_strategy == "dual":
        labels = ["text", "layout"] if mode == "full" else [mode]
        evidence["pass_strategy_used"] = "dual" if len(labels) == 2 else "single"
    else:
        labels = [mode]
        evidence["pass_strategy_used"] = "single"
    for label in labels:
        record = _record_pass(
            image,
            label=label,
            host=host,
            model=model,
            template=template,
            page_or_region=page_or_region,
            timeout=timeout,
        )
        evidence["model_call_count"] += 1
        _apply_pass(evidence, record)
    request_metrics = [item.get("metrics") or {} for item in evidence["requests"]]
    evidence["metrics"] = {
        "wall_seconds": round(sum(float(item.get("wall_seconds", 0.0)) for item in request_metrics), 3),
        "prompt_eval_count": sum(
            int(item["prompt_eval_count"])
            for item in request_metrics
            if isinstance(item.get("prompt_eval_count"), int)
        ) if any(isinstance(item.get("prompt_eval_count"), int) for item in request_metrics) else None,
        "eval_count": sum(
            int(item["eval_count"])
            for item in request_metrics
            if isinstance(item.get("eval_count"), int)
        ) if any(isinstance(item.get("eval_count"), int) for item in request_metrics) else None,
        "unknown_fields": [
            key
            for key in ("prompt_eval_count", "eval_count")
            if not all(isinstance(item.get(key), int) for item in request_metrics)
        ],
    }
    evidence["status"] = _overall_status(mode, evidence)
    if mode in {"text", "full"} and evidence["text"]["status"] != "complete":
        evidence["text"]["fallback"] = "conversation-online-vision-page-analysis"
        evidence["diagnostics"].append("text layer requires current-conversation vision fallback")
    if mode in {"layout", "full"} and evidence["visual"]["status"] != "complete":
        evidence["diagnostics"].append("visual structure layer remains unavailable or failed; OCR cannot provide it")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--page-or-region", required=True)
    parser.add_argument("--mode", choices=sorted(MODES), default="full")
    parser.add_argument("--pass-strategy", choices=sorted(PASS_STRATEGIES), default="single")
    parser.add_argument("--host", default="mac96")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = analyze(
        args.image,
        asset_id=args.asset_id,
        source_fingerprint=args.source_fingerprint,
        page_or_region=args.page_or_region,
        mode=args.mode,
        pass_strategy=args.pass_strategy,
        host=args.host,
        model=args.model,
        prompt_file=args.prompt_file,
        timeout=args.timeout,
    )
    if args.output:
        args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.expanduser().resolve().write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
