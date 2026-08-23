from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))

import recognition_route_quality_validator as validator


SHA = "a" * 64


def benchmark(*, include_small: bool = True):
    records = [
        {
            "route": "apple-vision-ocr",
            "page": 1,
            "image_sha256": SHA,
            "status": "complete",
            "structure_evidence": "/evidence/apple.json",
        },
        {
            "route": "mac96-30b",
            "page": 1,
            "image_sha256": SHA,
            "status": "complete",
            "structured_markdown": "# 标题",
        },
    ]
    if include_small:
        records.append(
            {
                "route": "mac96-small-vision-model",
                "page": 1,
                "image_sha256": SHA,
                "status": "complete",
                "structured_markdown": "# 标题",
            }
        )
    return {
        "contract": "recognition-route-benchmark",
        "source_fingerprint": "f" * 64,
        "pages": [1],
        "records": records,
    }


def review(route: str, *, verdict: str = "pass"):
    dimensions = {
        name: {
            "verdict": verdict if name == "paragraph_segmentation" else "pass",
            "evidence": [f"page image vs {route} markdown"],
            "note": "已逐项人工核对",
        }
        for name in validator.DIMENSIONS
    }
    status = "passed" if verdict == "pass" else "needs-review"
    return {
        "route": route,
        "page": 1,
        "image_sha256": SHA,
        "status": status,
        "dimensions": dimensions,
        "silent_omission_count": 0,
        "hallucinated_structure_count": 0,
        "advertising_policy": {
            "mode": "preserve-only",
            "changed_or_deleted_visible_text": False,
        },
    }


def assessment(routes):
    return {
        "contract": validator.CONTRACT,
        "version": validator.VERSION,
        "source_fingerprint": "f" * 64,
        "benchmark_report": "/benchmark.json",
        "assessments": [review(route) for route in routes],
        "route_selection": {
            "selected_default": "calibration-pending",
            "rule": "按页质量守卫选择",
            "fallback": ["升级到下一条已校准路线", "保留全部失败证据"],
        },
    }


def test_all_routes_and_dimensions_can_pass():
    routes = ["apple-vision-ocr", "mac96-30b", "mac96-small-vision-model"]
    result = validator.validate(benchmark(), assessment(routes))

    assert result["status"] == "passed"
    assert result["errors"] == []
    assert result["warnings"] == []


def test_nonempty_markdown_cannot_pass_with_a_structure_failure():
    value = assessment(["apple-vision-ocr", "mac96-30b", "mac96-small-vision-model"])
    value["assessments"][0] = review("apple-vision-ocr", verdict="fail")

    result = validator.validate(benchmark(), value)

    assert result["status"] == "needs-review"
    assert result["passed_assessment_count"] == 2


def test_missing_small_model_blocks_default_selection():
    value = assessment(["apple-vision-ocr", "mac96-30b"])
    value["route_selection"]["selected_default"] = "apple-vision-ocr"

    result = validator.validate(benchmark(include_small=False), value)

    assert result["status"] == "failed"
    assert "small vision model has no real benchmark record" in result["warnings"]
    assert any("cannot select a default" in error for error in result["errors"])


def test_advertising_text_change_is_rejected():
    value = assessment(["apple-vision-ocr", "mac96-30b", "mac96-small-vision-model"])
    value["assessments"][0]["advertising_policy"]["changed_or_deleted_visible_text"] = True

    result = validator.validate(benchmark(), value)

    assert result["status"] == "failed"
    assert any("visible text was not proven unchanged" in error for error in result["errors"])
