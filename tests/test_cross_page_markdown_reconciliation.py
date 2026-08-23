from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
import cross_page_markdown_reconciliation as reconciliation  # noqa: E402


def record(page: int, *, starts: bool, ends: bool, block: dict):
    return {
        "page": page,
        "structure_inventory": {
            "starts_with_continuation": starts,
            "ends_with_continuation": ends,
        },
        "blocks": [block],
    }


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_reconciles_only_explicit_adjacent_toc_continuation(tmp_path: Path):
    previous_path = tmp_path / "p004.json"
    current_path = tmp_path / "p005.json"
    previous = record(
        4,
        starts=False,
        ends=True,
        block={
            "kind": "toc-entry",
            "markdown": "- 案例 46：怎么委婉地告知客户一对一咨询是收费",
            "page_reference_detected": False,
            "source_candidate_indices": [7],
        },
    )
    current = record(
        5,
        starts=True,
        ends=False,
        block={
            "kind": "toc-entry",
            "markdown": "- 的？ ：90",
            "page_reference_detected": True,
            "source_candidate_indices": [0, 1],
        },
    )
    write(previous_path, previous)
    write(current_path, current)

    result = reconciliation.reconcile(
        previous,
        current,
        previous_path=previous_path,
        current_path=current_path,
    )

    assert result["status"] == "passed"
    assert result["source_records_mutated"] is False
    assert result["merged_block"]["markdown"] == (
        "- 案例 46：怎么委婉地告知客户一对一咨询是收费的？ ：90"
    )
    assert result["merged_block"]["source_blocks"] == [
        {"page": 4, "source_candidate_indices": [7]},
        {"page": 5, "source_candidate_indices": [0, 1]},
    ]


def test_ambiguous_new_case_is_not_merged(tmp_path: Path):
    previous_path = tmp_path / "p004.json"
    current_path = tmp_path / "p005.json"
    previous = record(
        4,
        starts=False,
        ends=True,
        block={
            "kind": "toc-entry",
            "markdown": "- 案例 46：未完",
            "page_reference_detected": False,
        },
    )
    current = record(
        5,
        starts=True,
        ends=False,
        block={
            "kind": "toc-entry",
            "markdown": "- 案例 47：新条目 91",
            "page_reference_detected": True,
        },
    )
    write(previous_path, previous)
    write(current_path, current)

    result = reconciliation.reconcile(
        previous,
        current,
        previous_path=previous_path,
        current_path=current_path,
    )

    assert result["status"] == "needs-review"
    assert result["merged_block"] is None


def test_cli_writes_separate_derived_evidence(tmp_path: Path):
    previous_path = tmp_path / "p004.json"
    current_path = tmp_path / "p005.json"
    output_path = tmp_path / "reconciliation.json"
    write(
        previous_path,
        record(
            4,
            starts=False,
            ends=True,
            block={
                "kind": "toc-entry",
                "markdown": "- 案例 46：收费",
                "page_reference_detected": False,
            },
        ),
    )
    write(
        current_path,
        record(
            5,
            starts=True,
            ends=False,
            block={
                "kind": "toc-entry",
                "markdown": "- 的？ 90",
                "page_reference_detected": True,
            },
        ),
    )

    assert reconciliation.main([
        "--previous", str(previous_path),
        "--current", str(current_path),
        "--output", str(output_path),
    ]) == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "passed"
