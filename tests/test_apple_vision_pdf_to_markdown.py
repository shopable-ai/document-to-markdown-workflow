from __future__ import annotations

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
