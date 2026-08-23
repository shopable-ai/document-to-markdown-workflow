from __future__ import annotations

from pathlib import Path

from adapters import run_pdf_batches as batch


class _ProcResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_background_process_matches_only_same_source(monkeypatch):
    current_source = Path("/tmp/current.pdf")
    proc_output = "\n".join(
        [
            "100 python /repo/adapters/lan_pdf_to_markdown.py --source /tmp/current.pdf",
            "101 python /repo/adapters/run_pdf_batches.py --source /tmp/other.pdf",
            "102 python /repo/adapters/page_markdown_noise_cleanup.py --source /tmp/current.pdf",
            "777 python /repo/adapters/run_pdf_batches.py --source /tmp/current.pdf",
        ]
    )

    monkeypatch.setattr(batch.os, "getpid", lambda: 777)
    monkeypatch.setattr(
        batch.subprocess,
        "run",
        lambda *args, **kwargs: _ProcResult(proc_output),
    )

    matches = batch._background_process_matches(current_source)

    assert matches == [
        "100 python /repo/adapters/lan_pdf_to_markdown.py --source /tmp/current.pdf",
        "102 python /repo/adapters/page_markdown_noise_cleanup.py --source /tmp/current.pdf",
    ]


def test_markdown_page_coverage_rejects_truncated_final_index():
    complete = "\n\n".join(
        f"<!-- page:{page} -->\n\n第 {page} 页" for page in (1, 2, 3)
    ) + "\n"
    truncated = "\n\n".join(
        f"<!-- page:{page} -->\n\n第 {page} 页" for page in (1, 3)
    ) + "\n"

    report = batch._markdown_page_coverage(
        selected=[1, 2, 3],
        raw_markdown=complete,
        merged_markdown=complete,
        final_markdown=truncated,
    )

    assert report["status"] == "failed"
    assert report["expected_page_count"] == 3
    assert report["final_page_count"] == 2
    assert report["checks"]["final_index_page_sequence_matches"] is False
    assert report["missing_pages"] == [2]


def test_page_job_failures_do_not_stop_later_pages():
    visited = []

    def worker(page: int):
        visited.append(page)
        if page == 2:
            raise RuntimeError("page failed")
        return {"page": page, "status": "passed"}

    results = batch._run_page_jobs_continue([1, 2, 3], worker)

    assert visited == [1, 2, 3]
    assert [item["status"] for item in results] == ["passed", "failed", "passed"]
    assert results[1]["page"] == 2


def test_completed_page_route_is_reused_without_conversion(tmp_path: Path):
    unit = tmp_path / "page-units" / "p057.md"
    route = tmp_path / "evidence" / "page-routes" / "p057.json"
    native_evidence = tmp_path / "pages" / "page-057" / "evidence.json"
    unit.parent.mkdir(parents=True)
    route.parent.mkdir(parents=True)
    native_evidence.parent.mkdir(parents=True)
    unit.write_text("<!-- page:57 -->\n\n原生正文\n\n### 视觉补充\n\n图示关系\n", encoding="utf-8")
    native_evidence.write_text('{"source_sha256":"abc","status":"passed"}\n', encoding="utf-8")
    visual_unit = tmp_path / "pages" / "page-057" / "visual" / "page-units" / "p057.md"
    visual_unit.parent.mkdir(parents=True)
    visual_unit.write_text("<!-- page:57 -->\n\n图示关系\n", encoding="utf-8")
    route.write_text(
        __import__("json").dumps(
            {
                "page": 57,
                "source_sha256": "abc",
                "route": "native-plus-visual",
                "status": "passed",
                "page_unit_sha256": batch.sha256(unit),
                "native_evidence": str(native_evidence),
                "native_evidence_sha256": batch.sha256(native_evidence),
                "visual_page_unit": str(visual_unit),
                "visual_page_unit_sha256": batch.sha256(visual_unit),
            }
        ),
        encoding="utf-8",
    )

    reusable = batch._reusable_routed_page(
        page=57,
        source_sha256="abc",
        page_unit_path=unit,
        route_record_path=route,
    )

    assert reusable["route"] == "native-plus-visual"
    assert reusable["resume_reused"] is True


def test_modified_completed_page_is_not_reused_when_hash_drifted(tmp_path: Path):
    unit = tmp_path / "p001.md"
    native = tmp_path / "native-evidence.json"
    route = tmp_path / "p001.json"
    unit.write_text("<!-- page:1 -->\n\n原文\n", encoding="utf-8")
    native.write_text('{"status":"passed"}\n', encoding="utf-8")
    route.write_text(
        __import__("json").dumps(
            {
                "page": 1,
                "source_sha256": "abc",
                "route": "native-only",
                "status": "passed",
                "page_unit_sha256": batch.sha256(unit),
                "native_evidence": str(native),
                "native_evidence_sha256": batch.sha256(native),
            }
        ),
        encoding="utf-8",
    )
    unit.write_text("<!-- page:1 -->\n\n被修改的正文\n", encoding="utf-8")

    assert batch._reusable_routed_page(
        page=1,
        source_sha256="abc",
        page_unit_path=unit,
        route_record_path=route,
    ) is None


def test_partial_visual_directory_without_summary_uses_new_attempt(tmp_path: Path):
    package = tmp_path / "page-002"
    partial = package / "visual"
    partial.mkdir(parents=True)
    (partial / "run.log").write_text("partial", encoding="utf-8")

    selected, resume = batch._select_visual_attempt(
        package=package,
        source_sha256="abc",
        page=2,
    )

    assert selected == package / "visual-attempt-002"
    assert resume is False
    assert (partial / "run.log").read_text(encoding="utf-8") == "partial"


def test_native_and_visual_supplement_merge_into_one_page_unit_without_overwrite():
    native = "原生第一段\n原生第二段"
    visual = "<!-- page:57 -->\n\n## 第 57 页\n\n原生第一段\n\n| 象限 | 含义 |\n| --- | --- |\n| 明星 | 高增长 |\n"

    merged = batch._merge_page_route_unit(
        page=57,
        route="native-plus-visual",
        native_text=native,
        visual_markdown=visual,
    )

    assert merged.count("<!-- page:57 -->") == 1
    assert native in merged
    assert "### 视觉补充" in merged
    assert "| 象限 | 含义 |" in merged
    assert merged.index(native) < merged.index("### 视觉补充")


def test_incomplete_page_report_lists_unfinished_selected_pages():
    report = batch._page_completion_report(
        selected=[1, 2, 3],
        page_results=[
            {"page": 1, "status": "passed"},
            {"page": 2, "status": "failed"},
        ],
    )

    assert report["status"] == "needs-review"
    assert report["completed_pages"] == [1]
    assert report["incomplete_pages"] == [2, 3]
    assert report["duplicate_pages"] == []
    assert report["unexpected_pages"] == []


def test_page_completion_reports_duplicate_and_unexpected_route_records():
    report = batch._page_completion_report(
        selected=[1, 2],
        page_results=[
            {"page": 1, "status": "passed"},
            {"page": 1, "status": "passed"},
            {"page": 2, "status": "passed"},
            {"page": 9, "status": "passed"},
        ],
    )

    assert report["status"] == "needs-review"
    assert report["duplicate_pages"] == [1]
    assert report["unexpected_pages"] == [9]


def test_markdown_table_count_is_not_a_self_comparison():
    markdown = (
        "正文\n\n"
        "| 象限 | 含义 |\n"
        "| --- | --- |\n"
        "| 明星 | 高增长 |\n\n"
        "普通竖线 | 不是表格\n"
    )

    assert batch._markdown_table_count(markdown) == 1
    assert batch._markdown_table_count("只有正文，没有表格\n") == 0
