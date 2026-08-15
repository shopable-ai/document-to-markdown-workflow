#!/usr/bin/env python3
"""Rebuild reader Markdown from an existing Workflow A evidence package.

This is deliberately a local, no-model recovery path.  It keeps the original
run directory untouched, reuses native/OCR/visual JSON already on disk, and
only reruns classification, Markdown generation, smoke checks, and validation.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))

from workflow_a_output_validator import validate_package  # noqa: E402
from workflow_a_runner import (  # noqa: E402
    build_markdown,
    classify_content,
    evaluate_smoke_pages,
    write_json,
    write_smoke_report,
)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def page_assets(package: Path, pages: list[int]) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for path in (package / "assets/pages").glob("*.png"):
        match = re.search(r"__p(\d{3})__", path.name)
        if match and int(match.group(1)) in pages:
            result[int(match.group(1))] = path
    return result


def package_pages(package: Path) -> list[int]:
    manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8")) or {}
    pages = (manifest.get("source") or {}).get("selected_pages") or []
    return [int(page) for page in pages]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reclassify_package(source_package: Path, target_package: Path, original_run: Path) -> dict:
    if target_package.exists():
        raise FileExistsError(f"refusing to overwrite existing target: {target_package}")
    shutil.copytree(source_package, target_package)
    evidence = target_package / "evidence"
    native = read_json(evidence / "native-content-model.json")
    visual = read_json(evidence / "mac96-visual-review.json")
    ocr = read_json(evidence / "ocr-candidates.json")
    reconciliation = read_json(evidence / "reconciliation.json")
    pages = package_pages(target_package)
    manifest = yaml.safe_load((target_package / "manifest.yaml").read_text(encoding="utf-8")) or {}
    processing_input = ((yaml.safe_load((target_package / "reports/processing-report.yaml").read_text(encoding="utf-8")) or {}).get("input") or {})
    source = Path(str(processing_input.get("source") or (manifest.get("source") or {}).get("path"))).resolve()
    source_type = str(processing_input.get("source_type") or "pdf")
    assets = page_assets(target_package, pages)
    classification = classify_content(native, visual, ocr, visual)
    write_json(evidence / "content-classification.json", classification)
    build_markdown(target_package, source, source_type, pages, assets, native, classification, visual, reconciliation, ocr)
    smoke = evaluate_smoke_pages(target_package, pages, assets, classification, visual, ocr)
    smoke["mode"] = "reclassified-from-existing-evidence"
    smoke["requested_pages"] = pages
    smoke["processed_pages"] = pages
    smoke["source_package"] = str(source_package)
    write_smoke_report(target_package, smoke)
    manifest.setdefault("recovery", {})["reclassified_from"] = str(original_run)
    manifest.setdefault("recovery", {})["model_calls_reused"] = True
    manifest.setdefault("markdown", {})["path"] = "document.md"
    classified_pages = {item.get("page"): item for item in classification.get("pages", [])}
    manifest["markdown"]["sections"] = []
    package_id = str(manifest.get("package_id") or target_package.name)
    for page in pages:
        item = classified_pages.get(page, {})
        title = (item.get("page_title") or {}).get("text") or f"第 {page} 页"
        body = "\n".join(
            str(block.get("text", ""))
            for block in item.get("classified_blocks", [])
            if block.get("role") != "page-title" and str(block.get("text", "")).strip()
        )
        manifest["markdown"]["sections"].append({
            "section_id": f"page-{page}",
            "title": title,
            "body": body,
            "source_occurrences": [f"{package_id}-p{page:03d}"],
            "visual_asset_ids": [f"page-p{page:03d}"] if any((target_package / "assets/pages").glob(f"*__p{page:03d}__*")) else [],
            "confidence": "high" if item.get("status") == "complete" else "medium",
            "reader_order": "semantic-before-visual",
            "unknown_forms": [],
            "unresolved": [] if smoke.get("status") == "passed" else [{"page": page, "reason": "smoke gate failed"}],
        })
    (target_package / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    validation = validate_package(target_package)
    write_json(target_package / "reports/validation-result.json", validation)
    write_json(evidence / "reclassification.json", {
        "status": "complete",
        "source_package": str(source_package),
        "target_package": str(target_package),
        "model_calls_reused": True,
        "changed_layers": ["evidence/content-classification.json", "document.md", "evidence/smoke-report.json", "manifest.yaml", "reports/validation-result.json"],
        "old_document_sha256": None,
        "new_smoke_status": smoke.get("status"),
        "new_validation_status": validation.get("status"),
    })
    return {"package": str(target_package), "pages": pages, "smoke_status": smoke.get("status"), "validation_status": validation.get("status")}


def audit_reader_pages(target_run: Path, results: list[dict]) -> dict:
    """Audit every rebuilt page, not only smoke endpoints or sample pages."""
    rows: list[dict] = []
    for result in results:
        package = Path(result["package"])
        classification = read_json(package / "evidence/content-classification.json")
        document = (package / "document.md").read_text(encoding="utf-8")
        pages = classification.get("pages", [])
        for item in pages:
            page = int(item.get("page"))
            title_info = item.get("page_title") or {}
            title = str(title_info.get("text") or "").strip()
            start = document.find(f"## 第 {page} 页：")
            end = document.find("## 第 ", start + 1) if start >= 0 else -1
            section = document[start:end if end >= 0 else None] if start >= 0 else ""
            reader_section = section.split("### 原始页面图", 1)[0]
            body_present = bool(re.search(r"^### (?:主要内容|核心内容|OCR 待复核候选|主题|四象限行动原则|四步复盘)\s*$", reader_section, re.MULTILINE))
            engineering_leak = bool(re.search(r"置信度\s*[:：]\s*0\.\d|bbox|candidate[_ ]?index|模型请求|Apple Vision", section))
            rows.append({
                "page": page,
                "package": str(package),
                "title": title,
                "title_source": title_info.get("source"),
                "quality_flags": title_info.get("quality_flags") or [],
                "body_present": body_present,
                "placeholder_title": not title or title.startswith("待复核：第 ") or title == f"第 {page} 页",
                "engineering_fields_in_reader": engineering_leak,
                "ocr_status": (item.get("text_evidence") or {}).get("ocr_status"),
                "ocr_candidate_count": (item.get("text_evidence") or {}).get("ocr_candidate_count", 0),
            })
    rows.sort(key=lambda item: item["page"])
    pages = [item["page"] for item in rows]
    bad = [item for item in rows if item["quality_flags"] or not item["body_present"] or item["placeholder_title"] or item["engineering_fields_in_reader"]]
    report = {
        "status": "passed" if len(rows) == len(set(pages)) and pages == list(range(1, len(rows) + 1)) and not bad else "needs-review",
        "audited_pages": len(rows),
        "expected_pages": list(range(1, len(rows) + 1)) if rows else [],
        "missing_pages": sorted(set(range(1, max(pages, default=0) + 1)) - set(pages)),
        "duplicate_pages": sorted({page for page in pages if pages.count(page) > 1}),
        "bad_pages": bad,
        "rows": rows,
        "reader_layer_policy": "title/body/OCR candidate/relations/page image; OCR metadata remains only in evidence/ocr-candidates.json",
    }
    write_json(target_run / "full-page-title-audit.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Reclassify existing Workflow A packages without model/OCR calls")
    parser.add_argument("--source-run", type=Path, required=True, help="existing page-batch run directory")
    parser.add_argument("--target-run", type=Path, required=True, help="new run directory; must not already exist")
    args = parser.parse_args()
    source_run = args.source_run.expanduser().resolve()
    target_run = args.target_run.expanduser().resolve()
    packages = sorted((source_run / "packages").glob("*/"))
    if not packages:
        parser.error(f"no packages found under {source_run / 'packages'}")
    target_run.mkdir(parents=True, exist_ok=False)
    (target_run / "packages").mkdir()
    results = []
    for package in packages:
        results.append(reclassify_package(package, target_run / "packages" / package.name, source_run))
    all_pages = [page for result in results for page in result["pages"]]
    failed = [result for result in results if result["smoke_status"] != "passed"]
    smoke_passed_pages = sum(len(result["pages"]) for result in results if result["smoke_status"] == "passed")
    page_audit = audit_reader_pages(target_run, results)
    checkpoint = {
        "status": "completed" if not failed else "needs-review",
        "workflow_id": "document-to-markdown-assets",
        "recovery_mode": "local-reclassification-from-existing-evidence",
        "source_run": str(source_run),
        "target_run": str(target_run),
        "model_calls_reused": True,
        "processed_pages": len(all_pages),
        "smoke_passed_pages": smoke_passed_pages,
        "confirmed_pages": 0,
        "needs_review_pages": len(all_pages),
        "packages": results,
        "page_title_audit": str(target_run / "full-page-title-audit.json"),
        "updated_at": now(),
    }
    write_json(target_run / "checkpoint.json", checkpoint)
    final = {
        "status": "needs-review" if failed or any(result["validation_status"] != "passed" for result in results) else "passed",
        "processed_pages": len(all_pages),
        "smoke_passed_pages": smoke_passed_pages,
        "confirmed_pages": 0,
        "needs_review_pages": len(all_pages),
        "stopped": bool(failed),
        "stop_reason": failed[0] if failed else None,
        "ocr_candidate_boundary": "OCR candidates remain searchable review evidence and are not confirmed facts",
        "checkpoint": str(target_run / "checkpoint.json"),
        "page_title_audit": str(target_run / "full-page-title-audit.json"),
        "source_run": str(source_run),
        "packages": results,
    }
    write_json(target_run / "final-report.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
