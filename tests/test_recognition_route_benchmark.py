from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
import recognition_route_benchmark as benchmark  # noqa: E402


def test_parse_page_image_and_existing_route(tmp_path: Path):
    image = tmp_path / "page.png"
    evidence = tmp_path / "vision.json"
    image.write_bytes(b"image")
    evidence.write_text("{}", encoding="utf-8")

    assert benchmark.parse_page_image(f"7={image}") == (7, image.resolve())
    assert benchmark.parse_existing_route(f"mac96-30b,7,{evidence}") == (
        "mac96-30b", 7, evidence.resolve()
    )


def test_run_records_raw_ocr_structure_and_existing_route(tmp_path: Path, monkeypatch):
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    script = tmp_path / "ocr.swift"
    script.write_text("// test", encoding="utf-8")
    source_fingerprint = "a" * 64
    existing = tmp_path / "vision.json"
    existing.write_text(json.dumps({
        "status": "complete",
        "source_fingerprint": source_fingerprint,
        "image_sha256": benchmark.digest(image),
        "model": "qwen3-vl:8b",
        "prompt_version": "v-test",
        "pass_strategy_used": "single",
        "metrics": {"wall_seconds": 3.2},
        "text": {"markdown": "# 标题\n\n正文"},
        "diagnostics": [],
    }, ensure_ascii=False), encoding="utf-8")
    ocr = {
        "status": "complete",
        "engine": "Apple Vision VNRecognizeTextRequest",
        "candidates": [{
            "text": "微信：abc123",
            "confidence": 1.0,
            "bbox": {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04},
        }],
    }
    monkeypatch.setattr(
        benchmark,
        "run_apple_vision",
        lambda *_args, **_kwargs: (ocr, {"wall_seconds": 0.1}, json.dumps(ocr, ensure_ascii=False)),
    )
    output = tmp_path / "run"
    args = benchmark.argparse.Namespace(
        output_root=output,
        page_image=[(1, image)],
        apple_vision_script=script,
        timeout=10,
        source_fingerprint=source_fingerprint,
        existing_route=[("mac96-small", 1, existing)],
        run_id="test-run",
    )

    report_path = benchmark.run(args)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["status"] == "needs-review"
    assert report["quality_assessment"] == "not-run"
    assert report["advertising_policy"] == {"mode": "preserve-only", "treatment": "not-run"}
    assert {item["route"] for item in report["records"]} == {
        "apple-vision-ocr", "mac96-small"
    }
    apple = next(item for item in report["records"] if item["route"] == "apple-vision-ocr")
    assert apple["candidate_accounting"] == {"unmapped_indices": [], "duplicate_indices": []}
    assert apple["advertising_policy"]["applied_action"] == "preserve"
    assert "微信：abc123" in Path(apple["structured_markdown"]).read_text(encoding="utf-8")
    small = next(item for item in report["records"] if item["route"] == "mac96-small")
    assert small["image_sha256_match"] is True
    assert small["source_fingerprint_match"] is True
    assert small["route_model_match"] is True


def test_existing_30b_route_rejects_evidence_from_another_model(tmp_path: Path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps({
            "status": "complete",
            "source_fingerprint": "f" * 64,
            "image_sha256": "a" * 64,
            "model": "gpt-5.4",
            "text": {"markdown": "# 目录"},
        }),
        encoding="utf-8",
    )

    record = benchmark.existing_route_record(
        "mac96-30b",
        2,
        evidence,
        image=None,
        source_fingerprint="f" * 64,
    )

    assert record["status"] == "failed"
    assert record["route_model_match"] is False
    assert any("does not match" in item for item in record["diagnostics"])


def test_existing_route_indexes_legacy_candidate_markdown(tmp_path: Path):
    evidence = tmp_path / "legacy.json"
    evidence.write_text(
        json.dumps({
            "status": "complete",
            "source_fingerprint": "f" * 64,
            "model": "qwen3-vl:30b-a3b-instruct",
            "candidate_markdown": "# 目录\n\n- 案例 1",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    record = benchmark.existing_route_record(
        "mac96-30b-v5",
        1,
        evidence,
        image=None,
        source_fingerprint="f" * 64,
    )

    assert record["structured_markdown"] == "# 目录\n\n- 案例 1"
