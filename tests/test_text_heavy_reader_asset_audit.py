from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/region-cropping/scripts/audit_text_heavy_reader_assets.py"
SPEC = importlib.util.spec_from_file_location("text_heavy_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def make_package(tmp_path: Path, name: str, image_builder) -> Path:
    package = tmp_path / name
    (package / "assets").mkdir(parents=True)
    (package / "evidence").mkdir()
    image_path = package / "assets" / f"{name}.png"
    image_builder(image_path)
    (package / "index.md").write_text(
        f"# 文档\n\n前文说明。\n\n![{name}](assets/{name}.png)\n\n后文补充。\n",
        encoding="utf-8",
    )
    return package


def run(package: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(package), "--ocr-engine", "tesseract", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def build_text_only(path: Path) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((120, 200), "Decision process decision process", fill="black")
    draw.text((120, 280), "Decision process decision process", fill="black")
    image.save(path)


def build_text_with_margin(path: Path) -> None:
    image = Image.new("RGB", (1200, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.text((420, 120), "Header text should be trimmed", fill="black")
    draw.text((300, 430), "Central semantic label", fill="black")
    draw.text((300, 520), "Central semantic label", fill="black")
    draw.text((380, 860), "Footer text should be trimmed", fill="black")
    image.save(path)


def build_diagram(path: Path) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 240, 420, 520), outline="black", width=6)
    draw.rectangle((760, 240, 1080, 520), outline="black", width=6)
    draw.line((420, 380, 760, 380), fill="black", width=10)
    draw.polygon([(700, 360), (760, 380), (700, 400)], fill="black")
    draw.text((180, 340), "Input", fill="black")
    draw.text((840, 340), "Output", fill="black")
    image.save(path)


def build_tall_ebook_crop(path: Path) -> None:
    image = Image.new("RGB", (600, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 80), "Header paragraph", fill="black")
    draw.rectangle((70, 390, 530, 1010), outline="black", width=8)
    draw.line((120, 700, 480, 700), fill="black", width=8)
    draw.text((220, 680), "Core label", fill="black")
    draw.text((80, 1280), "Footer paragraph", fill="black")
    image.save(path)


def test_marks_text_only_asset_as_candidate_remove(tmp_path: Path) -> None:
    package = make_package(tmp_path, "text-only", build_text_only)
    document = package / "index.md"
    document.write_text(
        "# 文档\n\nDecision process decision process\n\n"
        "![text-only](assets/text-only.png)\n\n"
        "Decision process decision process\n",
        encoding="utf-8",
    )

    result = run(package)

    assert result.returncode == 0, result.stderr
    report = json.loads((package / "evidence/text-heavy-reader-asset-audit.json").read_text(encoding="utf-8"))
    assert report["counts"]["candidate_remove"] == 1
    assert report["decisions"][0]["decision"] == "candidate-remove"
    assert report["decisions"][0]["deletion_eligible"] is True
    assert report["decisions"][0]["decision_confidence_score"] >= 95
    assert report["quality_gate"]["passed"] is True
    plan = json.loads((package / "evidence/text-heavy-reader-asset-removal-plan.json").read_text(encoding="utf-8"))
    assert plan["status"] == "ready"
    assert plan["candidates"][0]["image_sha256"] == report["decisions"][0]["sha256"]


def test_marks_text_margin_asset_as_crop_required(tmp_path: Path) -> None:
    package = make_package(tmp_path, "text-margin", build_text_with_margin)

    result = run(package)

    assert result.returncode == 0, result.stderr
    report = json.loads((package / "evidence/text-heavy-reader-asset-audit.json").read_text(encoding="utf-8"))
    assert report["decisions"][0]["decision"] == "crop-required"
    assert report["decisions"][0]["suggested_crop"]["area_ratio"] < 0.88


def test_marks_structured_diagram_as_retain(tmp_path: Path) -> None:
    package = make_package(tmp_path, "diagram", build_diagram)

    result = run(package)

    assert result.returncode == 0, result.stderr
    report = json.loads((package / "evidence/text-heavy-reader-asset-audit.json").read_text(encoding="utf-8"))
    assert report["decisions"][0]["decision"] == "retain"
    assert report["decisions"][0]["deletion_eligible"] is False


def test_converts_apple_vision_bottom_left_bbox_to_top_left_pixels() -> None:
    payload = {
        "candidates": [
            {
                "text": "示例",
                "confidence": 0.96,
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
            }
        ]
    }

    words = AUDIT.apple_vision_candidates_to_words(payload, 1000, 800)

    assert len(words) == 1
    assert words[0].confidence == 96
    assert (words[0].left, words[0].top, words[0].width, words[0].height) == (
        100,
        560,
        300,
        80,
    )


def test_ordered_coverage_rejects_same_characters_in_wrong_order() -> None:
    left = "甲乙丙丁戊己庚辛"
    right = "辛庚己戊丁丙乙甲"

    assert AUDIT.multiset_overlap_ratio(left, right) == 1.0
    assert AUDIT.ordered_overlap_ratio(left, right) < 0.8


def test_explicit_asset_scope_selects_only_named_markdown_images(tmp_path: Path) -> None:
    package = tmp_path / "package"
    (package / "assets").mkdir(parents=True)
    for name in ("p045__1.3.png", "p059__2.2.png", "unrelated.png"):
        Image.new("RGB", (40, 80), "white").save(package / "assets" / name)
    document = package / "index.md"
    document.write_text(
        "\n".join(
            [
                "![first](assets/p045__1.3.png)",
                "![second](assets/p059__2.2.png)",
                "![other](assets/unrelated.png)",
            ]
        ),
        encoding="utf-8",
    )

    selected, diagnostics = AUDIT.select_image_records(
        AUDIT.markdown_images(document),
        package_root=package,
        requested_assets=["p045__1.3.png", "p059__2.2.png"],
    )

    assert diagnostics == []
    assert [Path(item["resolved_path"]).name for item in selected] == [
        "p045__1.3.png",
        "p059__2.2.png",
    ]


def test_explicit_asset_scope_fails_closed_on_ambiguous_basename(tmp_path: Path) -> None:
    package = tmp_path / "package"
    (package / "assets/a").mkdir(parents=True)
    (package / "assets/b").mkdir(parents=True)
    for folder in ("a", "b"):
        Image.new("RGB", (40, 80), "white").save(package / "assets" / folder / "same.png")
    document = package / "index.md"
    document.write_text(
        "![a](assets/a/same.png)\n![b](assets/b/same.png)\n",
        encoding="utf-8",
    )

    selected, diagnostics = AUDIT.select_image_records(
        AUDIT.markdown_images(document),
        package_root=package,
        requested_assets=["same.png"],
    )

    assert selected == []
    assert diagnostics == ["ambiguous requested asset basename: same.png"]


def test_ebook_edge_text_profile_trims_only_top_and_bottom_text_bands(tmp_path: Path) -> None:
    image_path = tmp_path / "tall.png"
    build_tall_ebook_crop(image_path)
    words = [
        AUDIT.WordBox("Header paragraph", 98, 80, 80, 180, 18),
        AUDIT.WordBox("Core label", 98, 220, 680, 120, 18),
        AUDIT.WordBox("Footer paragraph", 98, 80, 1280, 180, 18),
    ]
    analysis_meta, dark_mask, non_text_mask = AUDIT.scaled_masks(image_path, words)

    result = AUDIT.ebook_edge_text_crop(
        words,
        analysis_meta=analysis_meta,
        dark_mask=dark_mask,
        non_text_mask=non_text_mask,
    )

    assert result["eligible"] is True
    assert result["top"]["text_word_count"] == 1
    assert result["bottom"]["text_word_count"] == 1
    assert result["crop"]["x"] == 0
    assert result["crop"]["width"] == 600
    assert 100 < result["crop"]["y"] < 390
    assert result["crop"]["y"] + result["crop"]["height"] < 1280
    assert result["kept_center_word_count"] == 1


def test_successful_ocr_with_no_text_is_a_no_op_retain_decision() -> None:
    decision, reason_codes, _rationale = AUDIT.decide_asset(
        word_count=0,
        average_confidence=0,
        context_overlap=0,
        ordered_overlap=0,
        non_text_dark_ratio=0.02,
        horizontal_rule_rows=8,
        vertical_rule_columns=8,
        non_text_bounds_area_ratio=0.5,
        simple_order_markers_present=False,
        crop_area_ratio=1.0,
        edge_trim_total=0,
        edge_text_trim_eligible=False,
        ocr_succeeded=True,
    )

    assert decision == "retain"
    assert reason_codes == ["no-text-detected"]


def test_applies_hash_locked_removal_plan_after_markdown_relink(tmp_path: Path) -> None:
    package = make_package(tmp_path, "text-only", build_text_only)
    document = package / "index.md"
    document.write_text(
        "# 文档\n\nDecision process decision process\n\n"
        "![text-only](assets/text-only.png)\n\n"
        "Decision process decision process\n",
        encoding="utf-8",
    )
    assert run(package).returncode == 0

    result = run(package, "--apply-plan", "evidence/text-heavy-reader-asset-removal-plan.json")

    assert result.returncode == 0, result.stderr
    assert not (package / "assets/text-only.png").exists()
    assert "assets/text-only.png" not in document.read_text(encoding="utf-8")
    removal = json.loads(
        (package / "evidence/text-heavy-reader-asset-removal-result.json").read_text(encoding="utf-8")
    )
    assert removal["status"] == "passed"
    assert removal["checks"]["asset_files_removed_after_relink"] is True


def test_removal_plan_fails_closed_on_markdown_hash_drift(tmp_path: Path) -> None:
    package = make_package(tmp_path, "text-only", build_text_only)
    document = package / "index.md"
    document.write_text(
        "# 文档\n\nDecision process decision process\n\n"
        "![text-only](assets/text-only.png)\n\n"
        "Decision process decision process\n",
        encoding="utf-8",
    )
    assert run(package).returncode == 0
    document.write_text(document.read_text(encoding="utf-8") + "\n漂移\n", encoding="utf-8")

    result = run(package, "--apply-plan", "evidence/text-heavy-reader-asset-removal-plan.json")

    assert result.returncode == 1
    assert (package / "assets/text-only.png").is_file()
    assert "assets/text-only.png" in document.read_text(encoding="utf-8")


def test_reuses_completed_audit_without_running_ocr_again(tmp_path: Path) -> None:
    package = make_package(tmp_path, "text-only", build_text_only)
    document = package / "index.md"
    document.write_text(
        "# 文档\n\nDecision process decision process\n\n"
        "![text-only](assets/text-only.png)\n\n"
        "Decision process decision process\n",
        encoding="utf-8",
    )
    assert run(package).returncode == 0
    plan_path = package / "evidence/text-heavy-reader-asset-removal-plan.json"
    plan_path.unlink()

    result = run(package, "--reuse-audit")

    assert result.returncode == 0, result.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["status"] == "ready"
    assert plan["candidates"][0]["image_path"] == "assets/text-only.png"
    assert (package / "assets/text-only.png").is_file()


def test_formal_auditor_dispatches_standalone_mode_without_markdown(tmp_path: Path) -> None:
    corpus = tmp_path / "image-corpus"
    corpus.mkdir()
    evidence = tmp_path / "standalone-evidence"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(corpus),
            "--mode",
            "standalone-image-corpus",
            "--evidence-dir",
            str(evidence),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads((evidence / "summary.json").read_text(encoding="utf-8"))
    assert summary["input_total"] == 0
    assert summary["markdown_access"] == {
        "files_read": 0,
        "files_modified": 0,
        "mode_contract": "forbidden",
    }
    assert not (corpus / "index.md").exists()
