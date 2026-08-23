from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
import ocr_markdown_structure as structure  # noqa: E402


def candidate(text: str, *, x: float, y: float, height: float = 0.03, confidence: float = 1.0):
    return {
        "text": text,
        "confidence": confidence,
        "bbox": {"x": x, "y": y, "width": 0.5, "height": height},
    }


def payload(*items):
    return {"status": "complete", "candidates": list(items)}


def test_reconstructs_title_paragraph_and_list_without_losing_visible_text():
    result = structure.reconstruct(
        payload(
            candidate("第一章 可靠标题", x=0.1, y=0.88, height=0.07),
            candidate("这是同一个自然段的第一行，", x=0.1, y=0.74),
            candidate("这是第二行。", x=0.1, y=0.70),
            candidate("1. 第一项", x=0.1, y=0.61),
            candidate("2. 第二项", x=0.1, y=0.56),
            candidate("微信：abc123", x=0.1, y=0.08),
        ),
        page=1,
    )

    assert result["status"] == "passed"
    assert "# 第一章 可靠标题" in result["markdown"] or "## 第一章 可靠标题" in result["markdown"]
    assert "这是同一个自然段的第一行，这是第二行。" in result["markdown"]
    assert "1. 第一项" in result["markdown"]
    assert "2. 第二项" in result["markdown"]
    assert "微信：abc123" in result["markdown"]
    assert result["candidate_accounting"] == {"unmapped_indices": [], "duplicate_indices": []}
    assert result["input_candidate_count"] == result["mapped_candidate_count"] == 6
    assert result["advertising_policy"] == {
        "mode": "preserve-only",
        "classification_stage": "not-run",
        "applied_action": "preserve",
    }


def test_directory_rows_keep_chapters_cases_and_right_column_page_numbers():
    result = structure.reconstruct(
        payload(
            candidate("目录", x=0.45, y=0.90, height=0.06),
            candidate("第三章：初次见面话术", x=0.1, y=0.80, height=0.04),
            candidate("案例 1：如何建立话题？", x=0.1, y=0.73),
            candidate("44", x=0.82, y=0.733, height=0.02),
            candidate("案例 2：如何礼貌询问？", x=0.1, y=0.68),
            candidate("45", x=0.82, y=0.683, height=0.02),
            candidate("案例 3：如何继续沟通？", x=0.1, y=0.63),
            candidate("46", x=0.82, y=0.633, height=0.02),
        ),
        page=2,
    )

    assert result["structure_inventory"]["page_kind"] == "table-of-contents"
    assert result["structure_inventory"]["toc_entry_count"] == 3
    assert result["structure_inventory"]["heading_count"] == 2
    assert "# 目录" in result["markdown"]
    assert "## 第三章：初次见面话术" in result["markdown"]
    assert "- 案例 1：如何建立话题？ 44" in result["markdown"]
    assert result["candidate_accounting"]["unmapped_indices"] == []


def test_directory_wrapped_case_is_one_traceable_entry():
    result = structure.reconstruct(
        payload(
            candidate("第四章：职场话术", x=0.1, y=0.88),
            candidate("案例24：参加饭局如何恰当称呼不熟悉的领导", x=0.1, y=0.80),
            candidate("呢？", x=0.1, y=0.75),
            candidate("69", x=0.82, y=0.752, height=0.02),
            candidate("案例 25：下一条？", x=0.1, y=0.68),
            candidate("70", x=0.82, y=0.682, height=0.02),
        ),
        page=3,
    )

    assert result["status"] == "passed"
    assert "- 案例24：参加饭局如何恰当称呼不熟悉的领导呢？ 69" in result["markdown"]
    entry = next(block for block in result["blocks"] if "案例24" in block["markdown"])
    assert entry["wrapped_row_count"] == 2
    assert entry["source_candidate_indices"] == [1, 2, 3]
    assert result["candidate_accounting"] == {"unmapped_indices": [], "duplicate_indices": []}


def test_directory_cross_page_boundaries_are_preserved_and_force_review():
    previous = structure.reconstruct(
        payload(
            candidate("第六章：销售话术", x=0.1, y=0.88),
            candidate("案例 46：怎么委婉地告知客户一对一咨询是收费", x=0.1, y=0.80),
        ),
        page=4,
    )
    current = structure.reconstruct(
        payload(
            candidate("的？", x=0.1, y=0.88),
            candidate("90", x=0.82, y=0.882, height=0.02),
            candidate("案例47：怎么跟同行沟通获取有效信息？91", x=0.1, y=0.80),
        ),
        page=5,
    )

    assert previous["status"] == "needs-review"
    assert previous["structure_inventory"]["ends_with_continuation"] is True
    assert current["status"] == "needs-review"
    assert current["structure_inventory"]["starts_with_continuation"] is True
    assert "的？ 90" in current["markdown"]


def test_low_confidence_content_is_preserved_and_forces_review():
    result = structure.reconstruct(
        payload(candidate("疑似推广文字", x=0.1, y=0.5, confidence=0.3)),
        page=3,
    )

    assert result["status"] == "needs-review"
    assert "疑似推广文字" in result["markdown"]
    assert result["structure_inventory"]["low_confidence_candidate_indices"] == [0]


def test_cli_writes_separate_markdown_and_evidence(tmp_path: Path):
    source = tmp_path / "ocr.json"
    markdown = tmp_path / "page.md"
    evidence = tmp_path / "structure.json"
    source.write_text(
        json.dumps(payload(candidate("正文。", x=0.1, y=0.5)), ensure_ascii=False),
        encoding="utf-8",
    )

    assert structure.main([
        "--ocr-json", str(source),
        "--page", "7",
        "--output-md", str(markdown),
        "--evidence", str(evidence),
    ]) == 0
    assert markdown.read_text(encoding="utf-8") == "正文。\n"
    record = json.loads(evidence.read_text(encoding="utf-8"))
    assert record["page"] == 7
    assert record["status"] == "passed"
