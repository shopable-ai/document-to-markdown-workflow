from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/region-cropping/scripts/apply_reader_asset_crops.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_package(tmp_path: Path, *, score: int = 97) -> tuple[Path, Path]:
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    (package / "evidence").mkdir()
    source = package / "assets/page-053.png"
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((100, 100), "body paragraph", fill="black")
    draw.rectangle((180, 430, 820, 900), outline="black", width=8)
    draw.line((250, 700, 750, 700), fill="black", width=8)
    draw.text((350, 940), "figure caption", fill="black")
    draw.text((100, 1200), "next paragraph", fill="black")
    image.save(source)
    (package / "index.md").write_text(
        "# 文档\n\n正文。\n\n![第53页原始页面图](assets/page-053.png)\n\n后文。\n",
        encoding="utf-8",
    )
    spec = {
        "schema": "reader-asset-crop-spec",
        "version": "0.1.0",
        "package_root": str(package),
        "document": "index.md",
        "delete_engineering_page_images": True,
        "pages": [
            {
                "page": 53,
                "source_path": "assets/page-053.png",
                "source_sha256": sha256(source),
                "source_kind": "regenerable-engineering-page-render",
                "replacement_alt": "厘清首要任务",
                "regions": [
                    {
                        "file_path": "assets/clarify-primary-task.png",
                        "display_name": "厘清首要任务",
                        "geometry": "700x600+150+390",
                        "quality_review": {
                            "method": "hybrid-ocr-layout-plus-visual-inspection",
                            "score": score,
                            "score_breakdown": {
                                "visual_content_complete": score - 57,
                                "required_labels_complete": 20,
                                "caption_or_context_complete": 15,
                                "boundary_safe": 12,
                                "body_text_pollution_acceptable": 10,
                            },
                            "checks": {
                                "visual_content_complete": True,
                                "required_labels_complete": True,
                                "caption_or_context_complete": True,
                                "boundary_safe": True,
                                "body_text_pollution_acceptable": True,
                            },
                        },
                    }
                ],
            }
        ],
    }
    spec_path = package / "evidence/reader-asset-crop-spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return package, spec_path


def run(package: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(package), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preview_hash_locks_projection_without_mutation(tmp_path: Path):
    package, _ = make_package(tmp_path)
    before_source = (package / "assets/page-053.png").read_bytes()
    before_markdown = (package / "index.md").read_text(encoding="utf-8")

    result = run(package, "--spec", "evidence/reader-asset-crop-spec.json")

    assert result.returncode == 0, result.stderr
    plan = json.loads((package / "evidence/reader-asset-crop-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "ready"
    assert plan["minimum_quality_score"] == 95
    assert plan["pages"][0]["regions"][0]["quality_score"] == 97
    assert (package / "assets/page-053.png").read_bytes() == before_source
    assert (package / "index.md").read_text(encoding="utf-8") == before_markdown
    assert not (package / "assets/clarify-primary-task.png").exists()


def test_apply_crops_relinks_then_deletes_engineering_page_image(tmp_path: Path):
    package, _ = make_package(tmp_path)
    preview = run(package, "--spec", "evidence/reader-asset-crop-spec.json")
    assert preview.returncode == 0, preview.stderr

    applied = run(package, "--apply-plan", "evidence/reader-asset-crop-plan.json")

    assert applied.returncode == 0, applied.stderr
    assert not (package / "assets/page-053.png").exists()
    crop = package / "assets/clarify-primary-task.png"
    assert crop.is_file()
    assert Image.open(crop).size == (700, 600)
    markdown = (package / "index.md").read_text(encoding="utf-8")
    assert "page-053.png" not in markdown
    assert "![厘清首要任务](assets/clarify-primary-task.png)" in markdown
    report = json.loads((package / "evidence/reader-asset-crop-result.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["checks"]["source_page_images_deleted_after_relink"] is True
    assert report["checks"]["all_output_assets_referenced"] is True
    assert report["pages"][0]["source_pre_delete_sha256"]
    assert report["pages"][0]["regions"][0]["output_sha256"] == sha256(crop)


def test_preview_rejects_quality_score_below_95(tmp_path: Path):
    package, _ = make_package(tmp_path, score=94)

    result = run(package, "--spec", "evidence/reader-asset-crop-spec.json")

    assert result.returncode == 1
    plan = json.loads((package / "evidence/reader-asset-crop-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "needs-review"
    assert any("below 95" in item for item in plan["diagnostics"])
    assert (package / "assets/page-053.png").is_file()


def test_preview_rejects_score_not_derived_from_breakdown(tmp_path: Path):
    package, spec_path = make_package(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["pages"][0]["regions"][0]["quality_review"]["score_breakdown"][
        "boundary_safe"
    ] = 10
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = run(package, "--spec", "evidence/reader-asset-crop-spec.json")

    assert result.returncode == 1
    plan = json.loads((package / "evidence/reader-asset-crop-plan.json").read_text(encoding="utf-8"))
    assert any("does not equal score" in item for item in plan["diagnostics"])


def test_apply_fails_closed_when_source_hash_drifts(tmp_path: Path):
    package, _ = make_package(tmp_path)
    preview = run(package, "--spec", "evidence/reader-asset-crop-spec.json")
    assert preview.returncode == 0, preview.stderr
    source = package / "assets/page-053.png"
    source.write_bytes(source.read_bytes() + b"drift")
    before_markdown = (package / "index.md").read_bytes()

    applied = run(package, "--apply-plan", "evidence/reader-asset-crop-plan.json")

    assert applied.returncode == 1
    assert source.is_file()
    assert (package / "index.md").read_bytes() == before_markdown
    assert not (package / "assets/clarify-primary-task.png").exists()


def test_apply_supports_multiple_semantic_crops_for_one_page(tmp_path: Path):
    package, spec_path = make_package(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    second = dict(spec["pages"][0]["regions"][0])
    second.update(
        {
            "file_path": "assets/primary-task-balance.png",
            "display_name": "首要任务的平衡区间",
            "geometry": "700x300+150+690",
        }
    )
    spec["pages"][0]["regions"].append(second)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert run(package, "--spec", "evidence/reader-asset-crop-spec.json").returncode == 0
    applied = run(package, "--apply-plan", "evidence/reader-asset-crop-plan.json")

    assert applied.returncode == 0, applied.stderr
    markdown = (package / "index.md").read_text(encoding="utf-8")
    assert "assets/clarify-primary-task.png" in markdown
    assert "assets/primary-task-balance.png" in markdown
    assert not (package / "assets/page-053.png").exists()


def test_apply_rotates_landscape_figure_for_reader_orientation(tmp_path: Path):
    package, spec_path = make_package(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    region = spec["pages"][0]["regions"][0]
    region["geometry"] = "700x300+150+390"
    region["rotate_degrees"] = 90
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert run(package, "--spec", "evidence/reader-asset-crop-spec.json").returncode == 0
    applied = run(package, "--apply-plan", "evidence/reader-asset-crop-plan.json")

    assert applied.returncode == 0, applied.stderr
    crop = package / "assets/clarify-primary-task.png"
    assert Image.open(crop).size == (300, 700)
    report = json.loads((package / "evidence/reader-asset-crop-result.json").read_text(encoding="utf-8"))
    output = report["pages"][0]["regions"][0]
    assert output["rotate_degrees"] == 90
    assert (output["output_width"], output["output_height"]) == (300, 700)
