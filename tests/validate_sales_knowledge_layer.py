#!/usr/bin/env python3
"""Independent validator for the deterministic sales-script knowledge layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
from pathlib import Path
from typing import Any


REQUIRED = [
    "README.md", "00-总目录.md", "taxonomy.yaml", "source-index.jsonl", "topic-index.json",
    "duplicate-clusters.json", "索引/source-to-topic.json", "索引/phrase-to-source.json",
    "reports/inventory.json", "reports/processing.json", "reports/token-usage.json",
    ".state/progress-current.json", ".state/progress.jsonl",
]
UNIT_FIELDS = {"id", "content_type", "sales_stage", "channel", "industry", "scenario", "customer_signal", "objective", "wording", "next_step", "usage_boundary", "risk_tags", "source_refs", "confidence"}


def tree_fingerprint(root: Path) -> dict[str, Any]:
    paths = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    digest = hashlib.sha256()
    total = 0
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(struct.pack(">Q", len(relative)))
        digest.update(relative)
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
        total += len(data)
    return {"files": len(paths), "bytes": total, "sha256": digest.hexdigest()}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def markdown_links(text: str) -> list[str]:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"<pre>.*?</pre>", "", text, flags=re.S)
    return [target for target in re.findall(r"!?(?:\[[^\]]*\])\(([^)]+)\)", text) if not re.match(r"^[a-z]+://", target)]


def active_builder_processes(output_root: Path) -> list[dict[str, Any]]:
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=True)
    rows: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if match:
            rows[int(match.group(1))] = (int(match.group(2)), match.group(3))
    ancestors = {os.getpid()}
    current = os.getpid()
    while current in rows:
        current = rows[current][0]
        if not current or current in ancestors:
            break
        ancestors.add(current)
    needle = str(output_root)
    return [
        {"pid": pid, "command": command}
        for pid, (_, command) in rows.items()
        if pid not in ancestors and "sales_script_knowledge_layer.py" in command and needle in command
    ]


def validate(input_root: Path, output_root: Path, expected: dict[str, Any], pilot: bool) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    def check(check_id: str, condition: bool, detail: str) -> None:
        checks.append({"id": check_id, "status": "passed" if condition else "failed", "detail": detail})
        if not condition:
            errors.append({"check": check_id, "message": detail})

    missing = [name for name in REQUIRED if not (output_root / name).is_file()]
    check("required-outputs", not missing and (output_root / "主题库").is_dir() and (output_root / "话术卡片").is_dir(), f"missing={missing}")
    if missing:
        return {"status": "failed", "checks": checks, "errors": errors}

    tree = tree_fingerprint(input_root)
    check("input-tree-immutable", tree == expected, f"actual={tree}; expected={expected}")

    try:
        sources = [json.loads(line) for line in (output_root / "source-index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception as exc:
        sources = []
        errors.append({"check": "source-inventory-complete", "message": str(exc)})
    paths = [record.get("path") for record in sources]
    valid_statuses = {"processed", "unclassified", "needs-review"}
    source_ok = len(sources) == 389 and len(set(paths)) == 389 and all(record.get("status") in valid_statuses for record in sources) and all((input_root / str(path)).is_file() for path in paths)
    check("source-inventory-complete", source_ok, f"records={len(sources)} unique_paths={len(set(paths))}")

    try:
        taxonomy = load_json(output_root / "taxonomy.yaml")
        topic_count = len(taxonomy.get("primary_topics", []))
        taxonomy_ok = 8 <= topic_count <= 20
    except Exception as exc:
        topic_count = 0
        taxonomy_ok = False
        errors.append({"check": "taxonomy-valid", "message": str(exc)})
    classified = all(record.get("topics") or record.get("unclassified_reason") or record.get("needs_review_reason") for record in sources)
    check("taxonomy-valid", taxonomy_ok and classified, f"primary_topics={topic_count}; classified_sources={sum(bool(r.get('topics')) for r in sources)}")

    try:
        duplicates = load_json(output_root / "duplicate-clusters.json")
        duplicate_ok = all(set(item) >= {"canonical_id", "member_sources", "algorithm"} for key in ("exact_document_groups", "normalized_document_groups") for item in duplicates.get(key, []))
    except Exception as exc:
        duplicate_ok = False
        errors.append({"check": "duplicate-analysis-traceable", "message": str(exc)})
    check("duplicate-analysis-traceable", duplicate_ok, "duplicate groups include canonical ID, sources, and algorithm")

    phrase = load_json(output_root / "索引" / "phrase-to-source.json")
    units = phrase.get("units", [])
    unit_ids = [unit.get("id") for unit in units]
    source_cache: dict[str, str] = {}
    dangling = 0
    bad_hash = 0
    incomplete = 0
    synthesis_without_evidence = 0
    for unit in units:
        if not UNIT_FIELDS.issubset(unit) or not unit.get("source_refs") or unit.get("content_type") not in {"verbatim", "normalized", "canonical-synthesis"}:
            incomplete += 1
        if unit.get("content_type") == "canonical-synthesis" and len(unit.get("source_refs", [])) < 2:
            synthesis_without_evidence += 1
        for ref in unit.get("source_refs", []):
            relative = ref.get("source_path")
            path = input_root / str(relative)
            if not path.is_file():
                dangling += 1
                continue
            if relative not in source_cache:
                source_cache[relative] = path.read_text(encoding="utf-8")
            text = source_cache[relative]
            start, end = ref.get("start_char"), ref.get("end_char")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end > len(text) or start >= end:
                dangling += 1
                continue
            excerpt = text[start:end]
            if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != ref.get("excerpt_sha256"):
                bad_hash += 1
    grounded_ok = len(unit_ids) == len(set(unit_ids)) and incomplete == 0 and dangling == 0 and bad_hash == 0
    check("knowledge-units-grounded", grounded_ok, f"units={len(units)} duplicate_ids={len(unit_ids)-len(set(unit_ids))} incomplete={incomplete} dangling={dangling} bad_hash={bad_hash}")
    check("synthesis-separated", synthesis_without_evidence == 0, f"synthesis_without_evidence={synthesis_without_evidence}")

    source_map = load_json(output_root / "索引" / "source-to-topic.json").get("sources", {})
    source_map_ids = {item for value in source_map.values() for item in value.get("unit_ids", [])}
    phrase_ids = set(unit_ids)
    bidirectional_ok = set(source_map) == set(paths) and source_map_ids <= phrase_ids
    if not pilot:
        ref_paths = {ref["source_path"] for unit in units for ref in unit.get("source_refs", [])}
        bidirectional_ok = bidirectional_ok and ref_paths == {path for path in paths if source_map[path].get("unit_ids")}
    check("bidirectional-index-complete", bidirectional_ok, f"source_index={len(paths)} source_map={len(source_map)} phrase_ids={len(phrase_ids)}")

    markdown_files = [output_root / "README.md", output_root / "00-总目录.md", *sorted((output_root / "主题库").glob("*.md")), *sorted((output_root / "话术卡片").glob("*.md"))]
    bad_markdown = []
    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            bad_markdown.append(str(path))
            continue
        structural_text = re.sub(r"<pre>.*?</pre>", "", text, flags=re.S)
        if not text.strip() or len(re.findall(r"(?m)^#\s+", structural_text)) != 1:
            bad_markdown.append(str(path))
            continue
        for target in markdown_links(text):
            clean = target.split("#", 1)[0]
            if clean and not (path.parent / clean).resolve().exists():
                bad_markdown.append(f"{path}:{target}")
                break
    check("markdown-deliverables-readable", not bad_markdown, f"bad_markdown_count={len(bad_markdown)}")

    tokens = load_json(output_root / "reports" / "token-usage.json")
    cloud_ok = tokens.get("corpus_text_external_cloud_calls") == 0 and tokens.get("corpus_text_external_cloud_characters") == 0
    check("cloud-corpus-boundary", cloud_ok, f"calls={tokens.get('corpus_text_external_cloud_calls')} characters={tokens.get('corpus_text_external_cloud_characters')}")

    processing = load_json(output_root / "reports" / "processing.json")
    recorded_immutable = processing.get("input_tree_before") == expected and processing.get("input_tree_after") == expected
    check("input-tree-before-after-recorded", recorded_immutable, f"before={processing.get('input_tree_before')} after={processing.get('input_tree_after')}")
    active = active_builder_processes(output_root)
    check("no-active-work", not active, f"active_processing_process_count={len(active)}")
    final_ok = processing.get("total") == 389 and processing.get("completed") == 389 and processing.get("failed") == 0 and processing.get("pending") == 0
    if pilot:
        final_ok = final_ok and processing.get("mode") == "pilot" and len(units) > 0
    else:
        final_ok = final_ok and processing.get("mode") == "full" and len(units) > 0
    check("final-reports-pass", final_ok, f"mode={processing.get('mode')} total={processing.get('total')} completed={processing.get('completed')} failed={processing.get('failed')} pending={processing.get('pending')}")
    return {
        "format_version": "1.0", "status": "passed" if not errors else "failed", "mode": "pilot" if pilot else "full",
        "input_root": str(input_root), "output_root": str(output_root), "summary": {
            "source_count": len(sources), "knowledge_unit_count": len(units), "source_reference_count": sum(len(u.get("source_refs", [])) for u in units),
            "dangling_source_ref_count": dangling, "bad_excerpt_hash_count": bad_hash, "unsupported_unit_count": incomplete,
            "synthesis_without_evidence_count": synthesis_without_evidence, "bad_markdown_count": len(bad_markdown), "errors": len(errors),
            "input_tree": tree, "active_processing_process_count": len(active),
        }, "checks": checks, "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--expected-tree-hash", default="11c2e9ac04bd8f1dba5dad1c6d939dd89b71ae015ea50d7704724a24d643925a")
    parser.add_argument("--expected-tree-files", type=int, default=3739)
    parser.add_argument("--expected-tree-bytes", type=int, default=251108525)
    args = parser.parse_args()
    output_root = Path(args.output_root).resolve()
    report = validate(Path(args.input_root).resolve(), output_root, {"files": args.expected_tree_files, "bytes": args.expected_tree_bytes, "sha256": args.expected_tree_hash}, args.pilot)
    target = output_root / "reports" / ("pilot-validation.json" if args.pilot else "validation-result.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    progress_path = output_root / ".state" / "progress-current.json"
    progress = load_json(progress_path)
    progress.update({"status": report["status"], "phase": "pilot-validated" if args.pilot else "full-validated", "validation_report": str(target)})
    progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_root / ".state" / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(progress, ensure_ascii=False) + "\n")
    print(json.dumps({"status": report["status"], **report["summary"], "report": str(target)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
