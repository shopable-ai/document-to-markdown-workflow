#!/usr/bin/env python3
"""Classify page images on mac96 without changing the Markdown asset package."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


DECISIONS = {"retain", "candidate-remove", "needs-review"}
REASONS = {
    "cover",
    "table-of-contents",
    "text-covered",
    "diagram",
    "table",
    "flow",
    "color-coded-structure",
    "relation-dense-layout",
    "needs-review",
}
CONFIDENCE = {"high", "medium", "low", "unknown"}
PROMPT_VERSION = "semantic-visual-asset-classification-mac96-v1"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROMPT = ROOT / "references/prompts/semantic-visual-asset-classification-mac96.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(part.strip()) for part in token.split("-", 1))
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise ValueError(f"invalid page number: {token}")
            pages.add(page)
    if not pages:
        raise ValueError("--pages must contain at least one page")
    return sorted(pages)


def load_package(root: Path) -> tuple[dict, dict[int, dict]]:
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8")) or {}
    sections = {
        int(section["section_id"].split("-")[-1]): section
        for section in (manifest.get("markdown") or {}).get("sections", [])
        if isinstance(section, dict) and re.fullmatch(r"page-\d+", str(section.get("section_id", "")))
    }
    assets: dict[int, dict] = {}
    for asset in manifest.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("asset_kind") != "page-render":
            continue
        occurrences = ((asset.get("source") or {}).get("source_occurrences") or [])
        pages = [item.get("page") for item in occurrences if isinstance(item, dict) and isinstance(item.get("page"), int)]
        if pages:
            assets[pages[0]] = asset
    return manifest, {page: {"asset": asset, "section": sections.get(page)} for page, asset in assets.items()}


def prompt_for(page: int, section: dict | None) -> str:
    title = (section or {}).get("title", "")
    body = re.sub(r"\s+", " ", str((section or {}).get("body", ""))).strip()
    body = body[:1200]
    return (
        f"{PROMPT_VERSION}\n"
        f"当前页码: {page}\n"
        f"Markdown 标题: {title}\n"
        f"Markdown 文字层摘录: {body}\n"
        "请按系统提示检查图片，并严格返回 JSON。"
    )


def parse_model_payload(payload: Any) -> dict:
    """Validate a model object and fail closed to needs-review."""
    if isinstance(payload, dict) and isinstance(payload.get("response"), str):
        raw = payload["response"].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fenced = re.search(r"\{.*\}", raw, re.DOTALL)
            if fenced:
                try:
                    payload = json.loads(fenced.group(0))
                except json.JSONDecodeError:
                    payload = {}
            else:
                payload = {}
    if not isinstance(payload, dict):
        payload = {}
    decision = payload.get("decision")
    reasons = payload.get("reason_codes")
    confidence = payload.get("confidence")
    coverage = payload.get("semantic_text_coverage")
    increment = payload.get("visual_increment")
    if decision not in DECISIONS or not isinstance(reasons, list) or not reasons or any(item not in REASONS for item in reasons):
        return {
            "decision": "needs-review",
            "reason_codes": ["needs-review"],
            "confidence": "unknown",
            "semantic_text_coverage": "unknown",
            "visual_increment": "unknown",
            "rationale": "模型响应缺少可验证的分类字段",
        }
    if confidence not in CONFIDENCE:
        confidence = "unknown"
    if coverage not in {"complete", "partial", "unknown"}:
        coverage = "unknown"
    if increment not in {"none", "present", "unknown"}:
        increment = "unknown"
    if decision == "candidate-remove" and (
        not set(reasons).intersection({"cover", "table-of-contents", "text-covered"})
        or coverage != "complete"
        or increment != "none"
    ):
        decision = "needs-review"
        reasons = ["needs-review", *[item for item in reasons if item in REASONS and item != "needs-review"]]
    return {
        "decision": decision,
        "reason_codes": reasons,
        "confidence": confidence,
        "semantic_text_coverage": coverage,
        "visual_increment": increment,
        "rationale": str(payload.get("rationale") or "").strip()[:1000],
    }


def classify_remote(
    image: Path,
    system_prompt: str,
    prompt: str,
    host: str,
    model: str,
    timeout: int,
) -> dict:
    request = {
        "model": model,
        "system": system_prompt,
        "prompt": prompt,
        "images": [base64.b64encode(image.read_bytes()).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    command = [
        "ssh",
        host,
        "/usr/bin/curl",
        "-sS",
        "http://127.0.0.1:11434/api/generate",
        "-H",
        "Content-Type:application/json",
        "--data-binary",
        "@-",
    ]
    try:
        result = subprocess.run(command, input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "needs-review", "decision": "needs-review", "reason_codes": ["needs-review"], "confidence": "unknown", "rationale": f"mac96 transport failed: {exc}"}
    if result.returncode != 0:
        return {"status": "needs-review", "decision": "needs-review", "reason_codes": ["needs-review"], "confidence": "unknown", "rationale": f"mac96 curl failed with exit {result.returncode}: {result.stderr.decode('utf-8', 'replace')[-500:]}"}
    try:
        raw = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "needs-review", "decision": "needs-review", "reason_codes": ["needs-review"], "confidence": "unknown", "rationale": f"mac96 response is not JSON: {exc}"}
    parsed = parse_model_payload(raw)
    parsed["status"] = "complete" if parsed["decision"] != "needs-review" else "needs-review"
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--pages", required=True, help="page numbers/ranges, e.g. 23-27")
    parser.add_argument("--output", default="evidence/visual-asset-classification-mac96.json")
    parser.add_argument("--host", default="mac96")
    parser.add_argument("--model", default="qwen3-vl:30b-a3b-instruct")
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    root = args.package_root.expanduser().resolve()
    pages = parse_pages(args.pages)
    manifest, page_map = load_package(root)
    prompt_path = args.prompt_file.expanduser().resolve()
    system_prompt = prompt_path.read_text(encoding="utf-8")
    records: list[dict] = []
    diagnostics: list[str] = []
    for page in pages:
        entry = page_map.get(page)
        if not entry:
            records.append({"page": page, "decision": "needs-review", "reason_codes": ["needs-review"], "rationale": "manifest 没有 page-render 来源资产"})
            diagnostics.append(f"page {page} has no registered page-render asset")
            continue
        asset = entry["asset"]
        image = root / str(asset.get("file_path", ""))
        base = {
            "page": page,
            "asset_id": asset.get("asset_id"),
            "file_path": asset.get("file_path"),
            "sha256": sha256(image) if image.is_file() else None,
            "source_fingerprint": (manifest.get("source") or {}).get("source_fingerprint"),
            "status": "needs-review",
        }
        if not image.is_file():
            base.update({"decision": "needs-review", "reason_codes": ["needs-review"], "rationale": "来源页面图不存在"})
            diagnostics.append(f"missing asset file: {asset.get('file_path')}")
        else:
            base.update(classify_remote(image, system_prompt, prompt_for(page, entry.get("section")), args.host, args.model, args.timeout))
        records.append(base)
    status = "complete" if not diagnostics and all(item.get("decision") in {"retain", "candidate-remove"} for item in records) else "needs-review"
    report = {
        "schema": "visual-asset-classification-mac96",
        "version": "0.1.0",
        "status": status,
        "mode": "candidate-only",
        "source_fingerprint": (manifest.get("source") or {}).get("source_fingerprint"),
        "prompt_version": PROMPT_VERSION,
        "prompt_path": str(prompt_path),
        "prompt_sha256": sha256(prompt_path),
        "target": {"transport": "ssh", "host": args.host, "model": args.model},
        "selected_pages": pages,
        "decisions": records,
        "apply": {"allowed": False, "reason": "候选报告不授权删除；须另行获得用户批准并显式调用 pruning --apply"},
        "diagnostics": diagnostics,
    }
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
