#!/usr/bin/env python3
"""Compatibility wrapper for the retired text-only vision adapter.

New code should call ``mac96_vision_page_analysis.analyze(mode="text")``.
This wrapper keeps old callers and historical tests readable without
maintaining a second model-request implementation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .mac96_vision_page_analysis import DEFAULT_MODEL, DEFAULT_PROMPT, analyze
except ImportError:
    from mac96_vision_page_analysis import DEFAULT_MODEL, DEFAULT_PROMPT, analyze


def transcribe(
    image: Path,
    *,
    asset_id: str,
    source_fingerprint: str,
    page_or_region: str,
    host: str = "mac96",
    model: str = DEFAULT_MODEL,
    prompt_file: Path = DEFAULT_PROMPT,
    timeout: int = 480,
) -> dict[str, Any]:
    result = analyze(
        image,
        asset_id=asset_id,
        source_fingerprint=source_fingerprint,
        page_or_region=page_or_region,
        mode="text",
        pass_strategy="single",
        host=host,
        model=model,
        prompt_file=prompt_file,
        timeout=timeout,
    )
    text = result["text"]
    return {
        "contract": "vision-text-transcription",
        "version": "0.1.0-compat",
        "input_asset_id": result["input_asset_id"],
        "source_fingerprint": result["source_fingerprint"],
        "image_sha256": result["image_sha256"],
        "page_or_region": result["page_or_region"],
        "prompt_version": result["prompt_version"],
        "transport": result["transport"],
        "target": result["target"],
        "model": result["model"],
        "requested_at": (result.get("requests") or [{}])[0].get("requested_at"),
        "candidate_markdown": text.get("markdown", ""),
        "status": text.get("status"),
        "diagnostics": text.get("diagnostics", []) + result.get("diagnostics", []),
        "canonical_evidence": "vision-page-analysis",
        "canonical_adapter": "adapters/mac96_vision_page_analysis.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--page-or-region", required=True)
    parser.add_argument("--host", default="mac96")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = transcribe(
        args.image,
        asset_id=args.asset_id,
        source_fingerprint=args.source_fingerprint,
        page_or_region=args.page_or_region,
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
