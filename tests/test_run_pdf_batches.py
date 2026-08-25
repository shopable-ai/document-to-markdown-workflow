from __future__ import annotations

from pathlib import Path

from adapters import run_pdf_batches as batch


class _ProcResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_native_page_command_targets_the_shared_pages_root():
    pages_root = Path("/tmp/run/pages")

    command = batch._native_page_command(
        page_python="/tmp/python",
        source=Path("/tmp/book.pdf"),
        pages_root=pages_root,
        page=114,
    )

    assert command[command.index("--output-root") + 1] == str(pages_root)
    assert command[command.index("--pages") + 1] == "114"
    assert "--no-source-markdown-alias" in command
    assert "attempt-page-114" not in " ".join(command)


def test_archive_incomplete_page_package_preserves_attempts(tmp_path: Path):
    pages_root = tmp_path / "pages"
    package = pages_root / "page-121"
    package.mkdir(parents=True)
    (package / "partial.txt").write_text("first", encoding="utf-8")

    first = batch._archive_incomplete_page_package(pages_root, 121)
    package.mkdir()
    (package / "partial.txt").write_text("second", encoding="utf-8")
    second = batch._archive_incomplete_page_package(pages_root, 121)

    assert first == pages_root / "_incomplete-page-packages/page-121/attempt-001"
    assert second == pages_root / "_incomplete-page-packages/page-121/attempt-002"
    assert (first / "partial.txt").read_text(encoding="utf-8") == "first"
    assert (second / "partial.txt").read_text(encoding="utf-8") == "second"
    assert not package.exists()


def test_visual_only_text_page_uses_local_ocr_instead_of_visual_model(tmp_path: Path):
    command, route = batch._visual_page_command(
        helper_python="/tmp/python",
        source=Path("/tmp/book.pdf"),
        visual_root=tmp_path / "visual",
        page=50,
        visual_dpi=220,
        timeout=7200,
        can_resume=False,
        decision={
            "route": "visual-only",
            "text_signals": {"present": False},
            "table_detection": {"present": False},
            "rendered_structure": {"visual_structure_present": False},
        },
    )

    assert command[1] == str(batch.APPLE_OCR_ADAPTER)
    assert "convert" not in command
    assert route == "apple-vision-ocr"


def test_visual_structure_page_keeps_visual_model_worker(tmp_path: Path):
    command, route = batch._visual_page_command(
        helper_python="/tmp/python",
        source=Path("/tmp/book.pdf"),
        visual_root=tmp_path / "visual",
        page=57,
        visual_dpi=220,
        timeout=7200,
        can_resume=True,
        decision={
            "route": "native-plus-visual",
            "text_signals": {"present": True},
            "table_detection": {"present": False},
            "rendered_structure": {"visual_structure_present": True},
        },
    )

    assert command[1] == str(batch.VISUAL_ADAPTER)
    assert "convert" in command
    assert "--resume" in command
    assert route == "lan-visual-model"


def test_full_page_background_text_supplement_uses_local_ocr(tmp_path: Path):
    command, route = batch._visual_page_command(
        helper_python="/tmp/python",
        source=Path("/tmp/book.pdf"),
        visual_root=tmp_path / "visual",
        page=57,
        visual_dpi=220,
        timeout=7200,
        can_resume=False,
        decision={
            "route": "native-plus-visual",
            "native_body_sufficient": True,
            "text_signals": {"present": False},
            "image_stats": {"max_area_ratio": 0.9355},
            "table_detection": {"present": True},
            "rendered_structure": {
                "status": "passed",
                "visual_structure_present": False,
            },
        },
    )

    assert command[1] == str(batch.APPLE_OCR_ADAPTER)
    assert "convert" not in command
    assert route == "apple-vision-ocr"


def test_confirmed_table_count_rejects_title_bar_false_positive():
    count = batch._confirmed_table_count(
        [
            {
                "table_detection": {"pdfplumber_count": 2},
                "rendered_structure": {
                    "status": "passed",
                    "visual_structure_present": False,
                    "structure_types": [],
                },
            }
        ]
    )

    assert count == 0


def test_confirmed_table_count_keeps_corroborated_and_unprobed_signals():
    count = batch._confirmed_table_count(
        [
            {
                "table_detection": {"pdfplumber_count": 3},
                "rendered_structure": {
                    "status": "passed",
                    "visual_structure_present": True,
                    "structure_types": ["table-or-matrix"],
                },
            },
            {
                "table_detection": {"pdfplumber_count": 1},
                "rendered_structure": {
                    "status": "unavailable",
                    "visual_structure_present": False,
                    "structure_types": [],
                },
            },
        ]
    )

    assert count == 4


def test_remove_redundant_page_aliases_only_deletes_exact_generated_copy(tmp_path: Path):
    pages_root = tmp_path / "pages"
    duplicate_package = pages_root / "page-003"
    distinct_package = pages_root / "page-004"
    duplicate_package.mkdir(parents=True)
    distinct_package.mkdir(parents=True)
    for package, alias_text in (
        (duplicate_package, "第 003 页\n"),
        (distinct_package, "人工修改内容\n"),
    ):
        (package / "index.md").write_text(
            f"第 {package.name[-3:]} 页\n", encoding="utf-8"
        )
        (package / "醒与悟2.0.md").write_text(alias_text, encoding="utf-8")

    removed = batch._remove_redundant_page_aliases(
        pages_root,
        Path("醒与悟2.0.pdf"),
    )

    assert removed == [str(duplicate_package / "醒与悟2.0.md")]
    assert not (duplicate_package / "醒与悟2.0.md").exists()
    assert (distinct_package / "醒与悟2.0.md").is_file()


def test_remove_stale_source_aliases_preserves_non_generated_markdown(tmp_path: Path):
    final = tmp_path / "index.md"
    keep = tmp_path / "醒与悟2.0.md"
    stage = tmp_path / "document-temp.md"
    stale = tmp_path / "醒与悟2.0-02.md"
    unrelated = tmp_path / "醒与悟2.0-03.md"
    old_valid = tmp_path / "醒与悟2.0-04.md"
    final.write_text("<!-- page:1 -->\n\n最终正文\n", encoding="utf-8")
    keep.write_text("<!-- page:1 -->\n\n最终正文\n", encoding="utf-8")
    stage.write_text("阶段正文\n", encoding="utf-8")
    stale.write_text("阶段正文\n", encoding="utf-8")
    unrelated.write_text("人工保留内容\n", encoding="utf-8")
    old_valid.write_text("<!-- page:1 -->\n\n旧的完整正文\n", encoding="utf-8")

    removed = batch._remove_stale_source_aliases(
        tmp_path,
        Path("醒与悟2.0.pdf"),
        keep=keep,
    )

    assert removed == [str(stale), str(old_valid)]
    assert keep.is_file()
    assert not stale.exists()
    assert unrelated.is_file()
    assert not old_valid.exists()


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


def test_completed_visual_page_uses_nested_source_hash_from_current_summary_schema(
    tmp_path: Path,
):
    package = tmp_path / "page-002"
    visual = package / "visual"
    markdown = visual / "page-units" / "p002.md"
    result = visual / "evidence" / "page-result-p002.json"
    markdown.parent.mkdir(parents=True)
    result.parent.mkdir(parents=True)
    markdown.write_text("<!-- page:2 -->\n\n## 封面\n", encoding="utf-8")
    batch._write_json_atomic(
        visual / "summary.json",
        {"status": "failed", "source": {"sha256": "abc"}},
    )
    batch._write_json_atomic(
        result,
        {
            "page": 2,
            "status": "passed",
            "source_sha256": "abc",
            "markdown": {"current_sha256": batch.sha256(markdown)},
        },
    )

    assert batch._visual_page_reusable(
        visual_root=visual,
        source_sha256="abc",
        page=2,
    ) is True
    assert batch._select_visual_attempt(
        package=package,
        source_sha256="abc",
        page=2,
    ) == (visual, True)


def test_copy_visual_asset_creates_the_book_level_asset(tmp_path: Path):
    visual = tmp_path / "visual"
    source = visual / "assets" / "page-001.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"page-image")
    assets = tmp_path / "book-assets"
    assets.mkdir()

    copied = batch._copy_visual_asset(visual, assets, 1)

    assert copied == assets / "page-001.png"
    assert copied.read_bytes() == b"page-image"


def test_merge_page_units_projects_visual_asset_paths_to_book_root(tmp_path: Path):
    units = tmp_path / "page-units"
    units.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "page-001.png").write_bytes(b"image")
    (units / "p001.md").write_text(
        "<!-- page:1 -->\n\n![封面](../assets/page-001.png)\n",
        encoding="utf-8",
    )

    merged = batch._merge_page_units(units, [1])

    assert "![第1页原始页面图](assets/page-001.png)" in merged
    assert "../assets/" not in merged


def test_batch_merge_matches_heading_repair_deterministic_merge(tmp_path: Path):
    from adapters import lan_markdown_heading_repair as heading

    units = tmp_path / "page-units"
    units.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "page-001.png").write_bytes(b"image")
    page_unit = units / "p001.md"
    page_unit.write_text(
        "<!-- page:1 -->\n\n正文\n\n![第1页原始页面图](../assets/page-001.png)\n",
        encoding="utf-8",
    )

    assert batch._merge_page_units(units, [1]) == heading._deterministic_merge(
        tmp_path, [(1, page_unit)]
    )


def test_batch_merge_matches_heading_repair_without_reader_asset(tmp_path: Path):
    from adapters import lan_markdown_heading_repair as heading

    units = tmp_path / "page-units"
    units.mkdir()
    page_unit = units / "p001.md"
    page_unit.write_text(
        "<!-- page:1 -->\n\n正文第一段\n\n\n\n正文第二段\n",
        encoding="utf-8",
    )

    assert batch._merge_page_units(units, [1]) == heading._deterministic_merge(
        tmp_path, [(1, page_unit)]
    )


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


def test_visual_only_keeps_ocr_text_that_matches_partial_native_layer():
    visual = "<!-- page:24 -->\n\n光、为利所困。\n\n人品之不高。\n"

    merged = batch._merge_page_route_unit(
        page=24,
        route="visual-only",
        native_text="光、为利所困。\n\n人品之不高。",
        visual_markdown=visual,
    )

    assert merged == visual


def test_duplicate_visual_supplement_keeps_native_text_and_page_asset():
    native = "做精、做实、做周全。"
    visual = "<!-- page:66 -->\n\n做精、做实、做周全。\n"

    merged = batch._merge_page_route_unit(
        page=66,
        route="native-plus-visual",
        native_text=native,
        visual_markdown=visual,
    )

    assert native in merged
    assert "![第66页原始页面图](../assets/page-066.png)" in merged
    assert "视觉补充" not in merged


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


def test_summary_from_stdout_recovers_indented_json_after_preamble():
    stdout = 'RUN_ROOT path=/tmp/run\n{\n  "status": "passed",\n  "summary_path": "/tmp/run/summary.json"\n}\n'

    assert batch._summary_from_stdout(stdout) == {
        "status": "passed",
        "summary_path": "/tmp/run/summary.json",
    }


def test_ollama_healthcheck_fails_within_bounded_probe(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise TimeoutError("model host did not respond")

    monkeypatch.setattr(batch, "urlopen", unavailable)

    result = batch._ollama_healthcheck(timeout_seconds=1)

    assert result["status"] == "failed"
    assert "TimeoutError" in result["error"]
