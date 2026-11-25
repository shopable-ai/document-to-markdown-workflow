from __future__ import annotations

import io
import shutil
import sys

from PIL import Image, ImageChops, ImageDraw
import pytest

from adapters import apple_vision_pdf_to_markdown as runner


def test_blank_ocr_page_keeps_a_passed_traceable_structure_record():
    record = runner._blank_structure(7)

    assert record["status"] == "passed"
    assert record["page"] == 7
    assert record["candidate_accounting"] == {
        "unmapped_indices": [],
        "duplicate_indices": [],
    }
    assert record["structure_inventory"]["page_kind"] == "image-only-no-recognized-text"
    assert record["advertising_policy"]["applied_action"] == "preserve"


def test_default_jobs_is_bounded_and_positive(monkeypatch):
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 10)
    assert runner.default_jobs() == 4

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 1)
    assert runner.default_jobs() == 1


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="requires Poppler")
def test_fast_png_is_lossless_and_does_not_replace_an_existing_asset(tmp_path):
    source = tmp_path / "test.pdf"
    blank = Image.new("RGB", (144, 128), "white")
    complex_page = blank.copy()
    draw = ImageDraw.Draw(complex_page)
    draw.rectangle((5, 7, 100, 38), fill="yellow", outline="darkblue", width=3)
    draw.line((5, 60, 99, 75, 20, 115), fill="purple", width=4)
    draw.text((17, 18), "Source text", fill="black")
    complex_page.save(source, save_all=True, append_images=[blank])

    for page in (1, 2):
        original = tmp_path / f"original-{page}.png"
        updated = tmp_path / f"updated-{page}.png"
        slow = runner.package.render_or_reuse_page(source, page, 220, original)
        fast = runner.package.render_or_reuse_page(source, page, 220, updated, fast_png=True)
        assert fast["renderer"] == "poppler-rgb-pillow-png"
        assert fast["raster_wall_seconds"] > 0
        assert fast["png_encode_wall_seconds"] > 0
        assert fast["sha256"] == runner.package.sha256_file(updated)
        assert (slow["pixel_width"], slow["pixel_height"]) == (fast["pixel_width"], fast["pixel_height"])
        with Image.open(original) as old_png, Image.open(updated) as new_png:
            assert old_png.mode == new_png.mode == "RGB"
            assert old_png.info == new_png.info
            assert ImageChops.difference(old_png, new_png).getbbox() is None
        resumed = runner.package.render_or_reuse_page(source, page, 220, original, fast_png=True)
        assert resumed["reused"] is True
        assert resumed["sha256"] == slow["sha256"]


def test_fast_png_refuses_incomplete_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.package, "_bounded_poppler_ppm", lambda _: io.BytesIO(b"P6\n2 2\n255\nab"))
    target = tmp_path / "page.png"
    assert runner.package._render_fast_png(["unused"], 220, target) is None
    assert not target.exists()


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="requires Poppler")
def test_fast_png_falls_back_to_legacy_when_memory_limit_is_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.package, "_FAST_PPM_LIMIT", 64)
    source = tmp_path / "test.pdf"
    Image.new("RGB", (40, 40), "white").save(source)
    original = tmp_path / "legacy.png"
    target = tmp_path / "fast.png"
    slow = runner.package.render_or_reuse_page(source, 1, 220, original)
    fallback = runner.package.render_or_reuse_page(source, 1, 220, target, fast_png=True)
    assert fallback["renderer"] == "poppler-png-fallback"
    assert fallback["sha256"] == slow["sha256"]
    assert "raster_wall_seconds" not in fallback


def test_raw_frame_capture_is_bounded(monkeypatch):
    monkeypatch.setattr(runner.package, "_FAST_PPM_LIMIT", 128)
    assert runner.package._bounded_poppler_ppm([sys.executable, "-c", "print('x' * 129)"]) is None
    assert runner.package._bounded_poppler_ppm([sys.executable, "-c", "print('ok')"]).read() == b"ok\n"


def test_raw_frame_capture_times_out(monkeypatch):
    monkeypatch.setattr(runner.package, "_FAST_PPM_TIMEOUT", 0.1)
    assert runner.package._bounded_poppler_ppm([sys.executable, "-c", "import time; time.sleep(30)"]) is None
