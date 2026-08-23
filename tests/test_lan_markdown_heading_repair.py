from __future__ import annotations

import json
from pathlib import Path

from adapters import lan_markdown_heading_repair as repair
from adapters import lan_pdf_vision_to_markdown as vision


def write_page_result(run_root: Path, page: int, action: str) -> None:
    page_unit_reference = None if action == "omit" else vision.page_unit_image_reference(page)
    document_reference = None if action == "omit" else vision.merged_page_image_reference(page)
    payload = {
        "page": page,
        "status": "passed",
        "page_state": "converted",
        "reader_image": {
            "action": action,
            "decision_source": "test-fixture",
            "reason_codes": ["fixture"],
            "asset_present": True,
            "semantic_text_coverage": "complete" if action == "omit" else "unknown",
            "layout_dependency": "none" if action == "omit" else "unknown",
            "page_unit_reference": page_unit_reference,
            "document_reference": document_reference,
            "metrics": {},
        },
    }
    evidence = run_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"page-result-p{page:03d}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_no_model_heading_repair_preserves_page_line_count_with_trailing_blank_lines(tmp_path: Path):
    run_root = tmp_path / "run"
    page_units = run_root / "page-units"
    reports = run_root / "reports"
    page_units.mkdir(parents=True)
    reports.mkdir(parents=True)

    page_text = (
        "<!-- page:1 -->\n"
        "\n"
        "第一部分\n"
        "\n"
        "正文段落\n"
        "\n"
    )
    page_unit = page_units / "p001.md"
    page_unit.write_text(page_text, encoding="utf-8")
    write_page_result(run_root, 1, "omit")
    (run_root / "document-temp.md").write_text(
        repair._deterministic_merge(run_root, [(1, page_unit)]),
        encoding="utf-8",
    )

    result = repair.run_heading_repair(run_root=run_root, use_model=False)

    assert result["status"] == "passed"
    assert (result["validation"] or {})["heading_prefix_only"] is True
    index_text = (run_root / "index.md").read_text(encoding="utf-8")
    assert "<!-- page:1 -->" in index_text
    assert "# 第一部分" in index_text


def test_heading_repair_accepts_four_digit_page_units_in_numeric_order(tmp_path: Path):
    run_root = tmp_path / "run"
    page_units = run_root / "page-units"
    reports = run_root / "reports"
    page_units.mkdir(parents=True)
    reports.mkdir(parents=True)

    pages = {
        930: "<!-- page:930 -->\n\n第九百三十页\n\n正文甲\n",
        1860: "<!-- page:1860 -->\n\n第一千八百六十页\n\n正文乙\n",
    }
    units: list[tuple[int, Path]] = []
    for page, text in pages.items():
        path = page_units / f"p{page:03d}.md"
        path.write_text(text, encoding="utf-8")
        write_page_result(run_root, page, "omit")
        units.append((page, path))

    (run_root / "document-temp.md").write_text(
        repair._deterministic_merge(run_root, units),
        encoding="utf-8",
    )

    result = repair.run_heading_repair(run_root=run_root, use_model=False)

    assert result["status"] == "passed"
    index_text = (run_root / "index.md").read_text(encoding="utf-8")
    assert "<!-- page:930 -->" in index_text
    assert "<!-- page:1860 -->" in index_text


def test_heading_repair_matches_vision_projection_after_heading_promotion(tmp_path: Path):
    run_root = tmp_path / "run"
    page_units = run_root / "page-units"
    reports = run_root / "reports"
    page_units.mkdir(parents=True)
    reports.mkdir(parents=True)

    page = 16
    page_text = (
        "<!-- page:16 -->\n\n"
        "![第16页原始页面图](../assets/page-016.png)\n\n\n"
        "### 第三部分\n\n"
        "正文段落\n"
    )
    page_unit = page_units / "p016.md"
    page_unit.write_text(page_text, encoding="utf-8")
    write_page_result(run_root, page, "retain")
    expected_merge = vision.project_page_image_reference(
        page_text,
        page,
        vision.merged_page_image_reference(page),
    )
    (run_root / "document-temp.md").write_text(expected_merge, encoding="utf-8")

    result = repair.run_heading_repair(run_root=run_root, use_model=False)

    assert result["status"] == "passed"
    assert (run_root / "index.md").read_text(encoding="utf-8") == expected_merge
