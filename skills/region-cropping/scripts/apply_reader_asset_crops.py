#!/usr/bin/env python3
"""Hash-locked page-render cropping, Markdown relinking, and safe source deletion."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from PIL import Image


MINIMUM_QUALITY_SCORE = 95
REQUIRED_QUALITY_CHECKS = {
    "visual_content_complete",
    "required_labels_complete",
    "caption_or_context_complete",
    "boundary_safe",
    "body_text_pollution_acceptable",
}
QUALITY_SCORE_MAXIMUMS = {
    "visual_content_complete": 40,
    "required_labels_complete": 20,
    "caption_or_context_complete": 15,
    "boundary_safe": 15,
    "body_text_pollution_acceptable": 10,
}
GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$")
IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)\s*$")
SEMANTIC_FILE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.png$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(path, payload)


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def inside(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes package root: {value}") from exc
    return path


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def parse_geometry(value: str) -> tuple[int, int, int, int]:
    match = GEOMETRY_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid crop geometry: {value!r}")
    width, height, x, y = (int(item) for item in match.groups())
    if width < 1 or height < 1:
        raise ValueError(f"empty crop geometry: {value!r}")
    return width, height, x, y


def image_target(line: str) -> str | None:
    match = IMAGE_RE.fullmatch(line.rstrip("\r\n"))
    if not match:
        return None
    return unquote((match.group(2) or match.group(3) or "").strip())


def markdown_replacement(
    root: Path,
    document_path: Path,
    document: str,
    page_specs: list[dict[str, Any]],
) -> tuple[str, dict[str, int]]:
    replacements: dict[Path, list[dict[str, Any]]] = {}
    for page in page_specs:
        source = inside(root, str(page["source_path"]))
        replacements[source] = list(page["regions"])
    counts = {relative(root, path): 0 for path in replacements}
    output: list[str] = []
    for line in document.splitlines(keepends=True):
        target = image_target(line)
        resolved = (document_path.parent / target).resolve() if target and "://" not in target else None
        regions = replacements.get(resolved) if resolved else None
        if not regions:
            output.append(line)
            continue
        source_key = relative(root, resolved)
        counts[source_key] += 1
        newline = "\n" if line.endswith(("\n", "\r")) else ""
        rendered = []
        for region in regions:
            output_path = inside(root, str(region["file_path"]))
            link = os.path.relpath(output_path, document_path.parent).replace(os.sep, "/")
            rendered.append(f"![{region['display_name']}]({link})")
        output.append(("\n\n".join(rendered)) + newline)
    return "".join(output), counts


def validate_quality(region: dict[str, Any], diagnostics: list[str], label: str) -> int:
    review = region.get("quality_review")
    if not isinstance(review, dict):
        diagnostics.append(f"{label}: missing quality_review")
        return 0
    score = review.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        diagnostics.append(f"{label}: quality score must be an integer from 0 to 100")
        return 0
    if score < MINIMUM_QUALITY_SCORE:
        diagnostics.append(f"{label}: quality score {score} is below {MINIMUM_QUALITY_SCORE}")
    if not str(review.get("method") or "").strip():
        diagnostics.append(f"{label}: quality review method is required")
    breakdown = review.get("score_breakdown")
    if not isinstance(breakdown, dict):
        diagnostics.append(f"{label}: missing quality score_breakdown")
    else:
        invalid_dimensions = []
        for name, maximum in QUALITY_SCORE_MAXIMUMS.items():
            value = breakdown.get(name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > maximum
            ):
                invalid_dimensions.append(name)
        if invalid_dimensions:
            diagnostics.append(
                f"{label}: invalid quality score dimensions: {','.join(sorted(invalid_dimensions))}"
            )
        elif sum(int(breakdown[name]) for name in QUALITY_SCORE_MAXIMUMS) != score:
            diagnostics.append(f"{label}: quality score_breakdown does not equal score {score}")
    checks = review.get("checks")
    if not isinstance(checks, dict):
        diagnostics.append(f"{label}: missing quality checks")
        return score
    missing = sorted(REQUIRED_QUALITY_CHECKS - set(checks))
    failed = sorted(name for name in REQUIRED_QUALITY_CHECKS if checks.get(name) is not True)
    if missing:
        diagnostics.append(f"{label}: missing quality checks: {','.join(missing)}")
    if failed:
        diagnostics.append(f"{label}: failed quality checks: {','.join(failed)}")
    return score


def build_plan(root: Path, spec_path: Path) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    diagnostics: list[str] = []
    if not isinstance(spec, dict) or spec.get("schema") != "reader-asset-crop-spec":
        raise ValueError("not a reader-asset-crop-spec")
    if Path(str(spec.get("package_root"))).resolve() != root:
        diagnostics.append("spec package_root mismatch")
    if spec.get("delete_engineering_page_images") is not True:
        diagnostics.append("spec must explicitly enable deletion of regenerable engineering page images")
    document_path = inside(root, str(spec.get("document") or "index.md"))
    if not document_path.is_file():
        diagnostics.append(f"missing Markdown document: {relative(root, document_path)}")
        document = ""
    else:
        document = document_path.read_text(encoding="utf-8")

    page_specs = spec.get("pages")
    if not isinstance(page_specs, list) or not page_specs:
        diagnostics.append("spec pages must be a non-empty list")
        page_specs = []
    pages: list[dict[str, Any]] = []
    output_paths: set[str] = set()
    source_paths: set[str] = set()
    for raw_page in page_specs:
        if not isinstance(raw_page, dict):
            diagnostics.append("page entry must be an object")
            continue
        page = raw_page.get("page")
        source_text = str(raw_page.get("source_path") or "")
        label = f"page {page}"
        if not isinstance(page, int) or page < 1:
            diagnostics.append(f"{label}: invalid page number")
        try:
            source = inside(root, source_text)
        except ValueError as exc:
            diagnostics.append(str(exc))
            continue
        if source_text in source_paths:
            diagnostics.append(f"{label}: duplicate source_path {source_text}")
        source_paths.add(source_text)
        if raw_page.get("source_kind") != "regenerable-engineering-page-render":
            diagnostics.append(f"{label}: source_kind does not authorize post-crop deletion")
        if not source.is_file():
            diagnostics.append(f"{label}: missing source {source_text}")
            continue
        actual_hash = sha256(source)
        if raw_page.get("source_sha256") != actual_hash:
            diagnostics.append(f"{label}: source SHA-256 mismatch")
        try:
            with Image.open(source) as image:
                source_width, source_height = image.size
        except OSError as exc:
            diagnostics.append(f"{label}: unreadable source image: {exc}")
            continue
        raw_regions = raw_page.get("regions")
        if not isinstance(raw_regions, list) or not raw_regions:
            diagnostics.append(f"{label}: regions must be a non-empty list")
            continue
        regions: list[dict[str, Any]] = []
        for index, raw_region in enumerate(raw_regions, 1):
            region_label = f"{label} region {index}"
            if not isinstance(raw_region, dict):
                diagnostics.append(f"{region_label}: region must be an object")
                continue
            file_text = str(raw_region.get("file_path") or "")
            try:
                output = inside(root, file_text)
            except ValueError as exc:
                diagnostics.append(str(exc))
                continue
            if output.parent != (root / "assets").resolve():
                diagnostics.append(f"{region_label}: reader crop must be directly under assets/")
            if not SEMANTIC_FILE_RE.fullmatch(output.name) or re.fullmatch(r"page-\d+\.png", output.name):
                diagnostics.append(f"{region_label}: output filename must be a semantic ASCII slug")
            if file_text in output_paths:
                diagnostics.append(f"{region_label}: duplicate output path {file_text}")
            output_paths.add(file_text)
            if output.exists():
                diagnostics.append(f"{region_label}: output already exists: {file_text}")
            display_name = str(raw_region.get("display_name") or "").strip()
            if not display_name:
                diagnostics.append(f"{region_label}: missing display_name")
            try:
                width, height, x, y = parse_geometry(str(raw_region.get("geometry") or ""))
            except ValueError as exc:
                diagnostics.append(f"{region_label}: {exc}")
                continue
            rotate_degrees = raw_region.get("rotate_degrees", 0)
            if rotate_degrees not in {0, 90, 180, 270}:
                diagnostics.append(f"{region_label}: rotate_degrees must be 0, 90, 180, or 270")
                rotate_degrees = 0
            if x + width > source_width or y + height > source_height:
                diagnostics.append(f"{region_label}: crop exceeds {source_width}x{source_height}")
            area_ratio = (width * height) / (source_width * source_height)
            if area_ratio >= 0.9:
                diagnostics.append(f"{region_label}: crop retains {area_ratio:.1%} of the full page")
            score = validate_quality(raw_region, diagnostics, region_label)
            regions.append(
                {
                    "file_path": file_text,
                    "display_name": display_name,
                    "geometry": str(raw_region.get("geometry")),
                    "width": width,
                    "height": height,
                    "x": x,
                    "y": y,
                    "rotate_degrees": rotate_degrees,
                    "output_width": height if rotate_degrees in {90, 270} else width,
                    "output_height": width if rotate_degrees in {90, 270} else height,
                    "area_ratio": round(area_ratio, 6),
                    "quality_score": score,
                    "quality_review": raw_region.get("quality_review"),
                }
            )
        pages.append(
            {
                "page": page,
                "source_path": source_text,
                "source_sha256": actual_hash,
                "source_kind": raw_page.get("source_kind"),
                "source_width": source_width,
                "source_height": source_height,
                "regions": regions,
            }
        )

    projected, reference_counts = markdown_replacement(root, document_path, document, pages)
    for source_text, count in reference_counts.items():
        if count != 1:
            diagnostics.append(f"{source_text}: expected exactly one Markdown image reference, found {count}")
    before_bytes = document.encode("utf-8")
    after_bytes = projected.encode("utf-8")
    return {
        "schema": "reader-asset-crop-plan",
        "version": "0.1.0",
        "status": "ready" if not diagnostics else "needs-review",
        "mode": "preview",
        "package_root": str(root),
        "minimum_quality_score": MINIMUM_QUALITY_SCORE,
        "spec_path": relative(root, spec_path),
        "spec_sha256": sha256(spec_path),
        "delete_engineering_page_images": True,
        "document": relative(root, document_path),
        "document_before_sha256": sha256_bytes(before_bytes),
        "document_expected_after_sha256": sha256_bytes(after_bytes),
        "reference_counts": reference_counts,
        "pages": pages,
        "counts": {
            "source_page_images": len(pages),
            "semantic_crops": sum(len(page["regions"]) for page in pages),
        },
        "diagnostics": diagnostics,
    }


def render_crop(source: Path, region: dict[str, Any]) -> bytes:
    with Image.open(source) as image:
        image.load()
        x, y = int(region["x"]), int(region["y"])
        width, height = int(region["width"]), int(region["height"])
        crop = image.crop((x, y, x + width, y + height))
        rotate_degrees = int(region.get("rotate_degrees", 0))
        if rotate_degrees:
            crop = crop.rotate(rotate_degrees, expand=True)
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def markdown_image_references(root: Path) -> set[Path]:
    references: set[Path] = set()
    for document in root.rglob("*.md"):
        for line in document.read_text(encoding="utf-8").splitlines():
            target = image_target(line)
            if target and "://" not in target and not target.startswith("data:"):
                references.add((document.parent / target).resolve())
    return references


def apply_plan(root: Path, plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("schema") != "reader-asset-crop-plan":
        raise ValueError("not a reader-asset-crop-plan")
    if plan.get("status") != "ready":
        raise ValueError("crop plan is not ready")
    if Path(str(plan.get("package_root"))).resolve() != root:
        raise ValueError("crop plan package_root mismatch")
    spec_path = inside(root, str(plan["spec_path"]))
    if not spec_path.is_file() or sha256(spec_path) != plan.get("spec_sha256"):
        raise ValueError("crop spec drifted after preview")
    document_path = inside(root, str(plan["document"]))
    if not document_path.is_file() or sha256(document_path) != plan.get("document_before_sha256"):
        raise ValueError("Markdown drifted after preview")
    original_markdown = document_path.read_bytes()
    pages = plan.get("pages") or []
    source_originals: dict[Path, bytes] = {}
    output_paths: list[Path] = []
    rendered: dict[Path, bytes] = {}
    for page in pages:
        source = inside(root, str(page["source_path"]))
        if not source.is_file() or sha256(source) != page.get("source_sha256"):
            raise ValueError(f"source image drifted after preview: {page.get('source_path')}")
        source_originals[source] = source.read_bytes()
        for region in page.get("regions") or []:
            output = inside(root, str(region["file_path"]))
            if output.exists():
                raise ValueError(f"crop output appeared after preview: {region.get('file_path')}")
            payload = render_crop(source, region)
            with Image.open(io.BytesIO(payload)) as crop:
                if crop.size != (int(region["output_width"]), int(region["output_height"])):
                    raise ValueError(f"rendered crop size mismatch: {region.get('file_path')}")
            output_paths.append(output)
            rendered[output] = payload
    document = original_markdown.decode("utf-8")
    projected, reference_counts = markdown_replacement(root, document_path, document, pages)
    projected_bytes = projected.encode("utf-8")
    if sha256_bytes(projected_bytes) != plan.get("document_expected_after_sha256"):
        raise ValueError("Markdown projection drifted after preview")

    try:
        for output, payload in rendered.items():
            atomic_write(output, payload)
        atomic_write(document_path, projected_bytes)
        references = markdown_image_references(root)
        missing_output_refs = [relative(root, output) for output in output_paths if output.resolve() not in references]
        if missing_output_refs:
            raise ValueError(f"generated crops are not referenced: {missing_output_refs}")
        for source in source_originals:
            source.unlink()
        source_deleted = all(not source.exists() for source in source_originals)
        output_valid = all(output.is_file() and output.stat().st_size > 0 for output in output_paths)
        old_refs_absent = all(source.resolve() not in markdown_image_references(root) for source in source_originals)
        checks = {
            "source_hashes_matched_preview": True,
            "crop_dimensions_match_plan": output_valid,
            "markdown_projection_matches_preview": document_path.read_bytes() == projected_bytes,
            "old_page_image_references_absent": old_refs_absent,
            "all_output_assets_referenced": not missing_output_refs,
            "source_page_images_deleted_after_relink": source_deleted,
            "quality_scores_at_least_95": all(
                int(region.get("quality_score", 0)) >= MINIMUM_QUALITY_SCORE
                for page in pages
                for region in page.get("regions") or []
            ),
        }
        if not all(checks.values()):
            raise ValueError("one or more post-apply checks failed")
    except Exception:
        atomic_write(document_path, original_markdown)
        for source, payload in source_originals.items():
            if not source.exists():
                atomic_write(source, payload)
        for output in output_paths:
            if output.exists():
                output.unlink()
        raise

    result_pages: list[dict[str, Any]] = []
    for page in pages:
        regions = []
        for region in page.get("regions") or []:
            output = inside(root, str(region["file_path"]))
            regions.append(
                {
                    "file_path": region["file_path"],
                    "display_name": region["display_name"],
                    "geometry": region["geometry"],
                    "rotate_degrees": region.get("rotate_degrees", 0),
                    "quality_score": region["quality_score"],
                    "output_sha256": sha256(output),
                    "output_width": region["output_width"],
                    "output_height": region["output_height"],
                }
            )
        result_pages.append(
            {
                "page": page["page"],
                "source_path": page["source_path"],
                "source_pre_delete_sha256": page["source_sha256"],
                "source_deleted": True,
                "regions": regions,
            }
        )
    return {
        "schema": "reader-asset-crop-result",
        "version": "0.1.0",
        "status": "passed",
        "mode": "applied",
        "package_root": str(root),
        "plan_path": relative(root, plan_path),
        "plan_sha256": sha256(plan_path),
        "minimum_quality_score": MINIMUM_QUALITY_SCORE,
        "counts": {
            "source_page_images_deleted": len(source_originals),
            "semantic_crops_created": len(output_paths),
        },
        "reference_counts_before": reference_counts,
        "pages": result_pages,
        "checks": checks,
        "diagnostics": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--spec", type=Path, help="preview a crop specification")
    group.add_argument("--apply-plan", type=Path, help="apply a hash-locked preview plan")
    parser.add_argument("--plan", type=Path, default=Path("evidence/reader-asset-crop-plan.json"))
    args = parser.parse_args()
    root = args.package_root.expanduser().resolve()
    if args.spec:
        plan_path = inside(root, str(args.plan))
        try:
            spec_path = inside(root, str(args.spec))
            plan = build_plan(root, spec_path)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            plan = {
                "schema": "reader-asset-crop-plan",
                "version": "0.1.0",
                "status": "needs-review",
                "mode": "preview",
                "package_root": str(root),
                "minimum_quality_score": MINIMUM_QUALITY_SCORE,
                "diagnostics": [str(exc)],
            }
        write_json(plan_path, plan)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan.get("status") == "ready" else 1

    result_path = root / "evidence/reader-asset-crop-result.json"
    try:
        apply_path = inside(root, str(args.apply_plan))
        result = apply_plan(root, apply_path)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        result = {
            "schema": "reader-asset-crop-result",
            "version": "0.1.0",
            "status": "failed",
            "mode": "not-applied",
            "package_root": str(root),
            "diagnostics": [str(exc)],
        }
    write_json(result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
