from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import adapters.lan_pdf_markdown_graph as graph
from adapters import run_pdf_batches as batch


def test_safe_run_label_keeps_readable_source_name():
    label = graph._safe_run_label(Path("/tmp/50个相处话术案例.pdf"))

    assert label == "50个相处话术案例"


def test_timestamp_run_root_uses_name_then_time_and_collision_suffix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(graph, "package_timestamp", lambda: "20260821-154233")

    first = graph._timestamp_run_root(tmp_path, "50个相处话术案例")
    first.mkdir()
    second = graph._timestamp_run_root(tmp_path, "50个相处话术案例")

    assert first.name == "50个相处话术案例-20260821-154233"
    assert second.name == "50个相处话术案例-20260821-154233-02"


def test_native_pdf_facts_uses_actual_readers_when_access_probe_is_false(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.7\n")

    class Completed:
        returncode = 0
        stdout = "Pages: 1\nPage size: 612 x 792 pts\nPDF version: 1.7\nEncrypted: no\n"
        stderr = ""

    monkeypatch.setattr(graph.os, "access", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(graph.shutil, "which", lambda _name: "/usr/bin/pdfinfo")
    monkeypatch.setattr(graph.subprocess, "run", lambda *_args, **_kwargs: Completed())

    facts = graph._native_pdf_facts(source)

    assert facts["path"] == str(source)
    assert facts["total_pages"] == 1
    assert facts["sha256"] == graph._native_sha256(source)


def test_native_merge_coverage_accepts_every_selected_page_once(tmp_path: Path):
    units = tmp_path / "page-units"
    units.mkdir()
    for page in (1, 2, 3):
        (units / f"p{page:03d}.md").write_text(
            f"<!-- page: {page} -->\n\n第 {page} 页\n",
            encoding="utf-8",
        )

    graph._native_merge(tmp_path, [1, 2, 3])
    report = graph._native_check_merge_coverage(
        run_root=tmp_path,
        selected_pages=[1, 2, 3],
        markdown_path=tmp_path / "document-temp.md",
        require_exact_content=True,
    )

    assert report["status"] == "passed"
    assert report["expected_page_count"] == 3
    assert report["actual_page_count"] == 3
    assert report["actual_page_sequence"] == [1, 2, 3]
    assert all(report["checks"].values())


def test_native_merge_coverage_rejects_a_truncated_final_markdown(tmp_path: Path):
    units = tmp_path / "page-units"
    units.mkdir()
    for page in (1, 2, 3):
        (units / f"p{page:03d}.md").write_text(
            f"<!-- page: {page} -->\n\n第 {page} 页\n",
            encoding="utf-8",
        )
    final = tmp_path / "index.md"
    final.write_text(
        "<!-- page: 1 -->\n\n第 1 页\n\n<!-- page: 3 -->\n\n第 3 页\n",
        encoding="utf-8",
    )

    report = graph._native_check_merge_coverage(
        run_root=tmp_path,
        selected_pages=[1, 2, 3],
        markdown_path=final,
        require_exact_content=False,
    )

    assert report["status"] == "failed"
    assert report["actual_page_count"] == 2
    assert report["missing_pages"] == [2]
    assert report["checks"]["merged_page_sequence_matches"] is False


def test_visual_merge_coverage_allows_deterministic_cross_page_reconciliation(
    tmp_path: Path,
):
    units = tmp_path / "page-units"
    units.mkdir()
    (units / "p001.md").write_text(
        "<!-- page:1 -->\n\n上一页断句\n",
        encoding="utf-8",
    )
    (units / "p002.md").write_text(
        "<!-- page:2 -->\n\n下一页续句\n",
        encoding="utf-8",
    )
    merged = tmp_path / "document-temp.md"
    merged.write_text(
        "<!-- page:1 -->\n\n上一页断句下一页续句\n\n<!-- page:2 -->\n",
        encoding="utf-8",
    )

    report = graph._visual_check_merge_coverage(
        run_root=tmp_path,
        selected_pages=[1, 2],
        markdown_path=merged,
    )

    assert report["status"] == "passed"
    assert report["checks"]["merged_page_sequence_matches"] is True
    assert report["checks"]["merged_content_matches_page_units"] is True


def test_visual_asset_classification_timeout_scales_for_sequential_pages():
    assert graph._visual_asset_classification_stage_timeout(
        base_timeout_seconds=1800,
        remaining_page_count=1,
    ) == 1800
    assert graph._visual_asset_classification_stage_timeout(
        base_timeout_seconds=1800,
        remaining_page_count=15,
    ) == 4560


def test_main_allows_no_model_with_auto_run_root(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    auto_root = tmp_path / "runs" / "book"
    captured: dict[str, object] = {}

    monkeypatch.setattr(graph, "_default_run_root", lambda parent, source: auto_root)

    def fake_run_native(settings: graph.NativeGraphSettings):
        captured["settings"] = settings
        return {"final_status": "passed", "run_root": str(settings.run_root)}

    monkeypatch.setattr(graph, "run_native_graph", fake_run_native)

    exit_code = graph.main(
        [
            "--source",
            str(source),
            "--pages",
            "all",
            "--auto-run-root",
            "--no-model",
        ]
    )

    assert exit_code == 0
    settings = captured["settings"]
    assert isinstance(settings, graph.NativeGraphSettings)
    assert settings.source == source
    assert settings.run_root == auto_root
    assert settings.pages == "all"
    captured_io = capsys.readouterr()
    output = json.loads(captured_io.out)
    assert output["run_root"] == str(auto_root)
    assert f"RUN_ROOT mode=native-no-model path={auto_root}" in captured_io.err


def test_default_native_smoke_pages_cover_first_middle_and_last():
    pages = list(range(1, 21))

    assert graph._default_native_smoke_pages(pages) == [1, 2, 3, 10, 20]


def test_classify_native_page_route_keeps_plain_text_page_native_only():
    decision = graph._classify_native_page_route(
        {
            "text": "第一段完整正文\n第二段完整正文\n第三段完整正文",
            "line_count": 3,
            "nonempty_line_ids": [
                "p1-native-l0001",
                "p1-native-l0002",
                "p1-native-l0003",
            ],
            "model_calls": [],
            "images": {"image_count": 0},
        }
    )

    assert decision["route"] == "native-only"
    assert decision["fallback_reason"] is None
    assert decision["model_called"] is False


def test_classify_native_page_route_marks_empty_page_for_vision_fallback():
    decision = graph._classify_native_page_route(
        {
            "text": "",
            "line_count": 0,
            "nonempty_line_ids": [],
            "model_calls": [],
            "images": {
                "image_count": 1,
                "total_area_ratio": 0.8,
                "max_area_ratio": 0.8,
            },
        }
    )

    assert decision["route"] == "visual-only"
    assert decision["native_quality"] == "weak"


def test_classify_native_page_route_uses_visual_supplement_for_page_57_signal():
    decision = graph._classify_native_page_route(
        {
            "page": 57,
            "text": (
                "正文完整说明了业务增长率、相对市场占有率、四个象限的含义和资源配置方法。"
                "管理者需要结合市场地位、产品阶段与现金流进行判断。如图 1 所示：\n"
                "图 1：波士顿矩阵图"
            ),
            "bbox_metrics": {
                "block_count": 4,
                "text_bbox_area_ratio": 0.1,
                "large_blank_region": True,
            },
            "images": {
                "image_count": 1,
                "total_area_ratio": 0.3,
                "max_area_ratio": 0.3,
            },
            "rendered_structure": {
                "visual_structure_present": True,
                "structure_types": ["table-or-matrix"],
            },
        }
    )

    assert decision["route"] == "native-plus-visual"
    assert decision["model_called"] is False


def test_native_smoke_report_counts_native_and_vision_candidates():
    report = graph._build_native_smoke_report(
        smoke_pages=[1, 2, 10],
        total_pages=120,
        page_routes=[
            {"page": 1, "route": "native-only", "fallback_reason": None, "native_quality": "usable", "model_called": False},
            {"page": 2, "route": "native-only", "fallback_reason": None, "native_quality": "usable", "model_called": False},
            {"page": 10, "route": "visual-only", "fallback_reason": "native-text-empty-or-insufficient", "native_quality": "weak", "model_called": False},
        ],
    )

    assert report["status"] == "needs-review"
    assert report["route_counts"] == {
        "native-only": 2,
        "native-plus-visual": 0,
        "visual-only": 1,
    }
    assert report["model_calls"] == 0


def test_bounded_native_smoke_can_pass_its_stage_without_full_book_acceptance(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    source_hash = graph._native_sha256(source)
    batch_root = tmp_path / graph.INTERNAL_STAGE_ROOT / graph.NATIVE_SMOKE_STAGE
    selected = [1, 2, 3, 10, 20]
    for page in selected:
        evidence = graph._native_page_evidence(batch_root, page)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "page": page,
                    "source_sha256": source_hash,
                    "text": "第一段正文\n第二段正文",
                    "model_calls": [],
                }
            ),
            encoding="utf-8",
        )
    result = {
        "status": "failed",
        "checkpoint": str(batch_root / "progress.json"),
        "summary": {
            "status": "failed",
            "source": str(source.resolve()),
            "source_sha256": source_hash,
            "selected_pages": selected,
            "total_pages": 20,
            "model_calls": [],
            "visual_model": "not-run",
            "rendering": "not-run",
        },
    }

    assert graph._native_smoke_batch_root_if_usable(
        result,
        source_facts={
            "path": str(source.resolve()),
            "sha256": source_hash,
            "total_pages": 20,
        },
        smoke_pages=selected,
    ) == batch_root.resolve()


def test_bounded_native_smoke_accepts_nested_page_package_layout(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    source_hash = graph._native_sha256(source)
    batch_root = tmp_path / "native-smoke"
    selected = [1, 2]
    for page in selected:
        evidence = (
            batch_root
            / "pages"
            / f"page-{page:03d}"
            / f"page-{page:03d}"
            / "evidence"
            / f"page-{page:03d}.json"
        )
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps({"page": page, "source_sha256": source_hash}),
            encoding="utf-8",
        )
    result = {
        "status": "failed",
        "checkpoint": str(batch_root / "progress.json"),
        "summary": {
            "status": "failed",
            "source": str(source.resolve()),
            "source_sha256": source_hash,
            "selected_pages": selected,
            "total_pages": 2,
            "model_calls": [],
            "visual_model": "not-run",
            "rendering": "not-run",
        },
    }

    assert graph._native_smoke_batch_root_if_usable(
        result,
        source_facts={
            "path": str(source.resolve()),
            "sha256": source_hash,
            "total_pages": 2,
        },
        smoke_pages=selected,
    ) == batch_root.resolve()


def test_native_page_evidence_resolves_existing_nested_page_package(tmp_path: Path):
    batch_root = tmp_path / "native-smoke"
    nested = (
        batch_root
        / "pages"
        / "page-001"
        / "page-001"
        / "evidence"
        / "page-001.json"
    )
    nested.parent.mkdir(parents=True)
    nested.write_text("{}", encoding="utf-8")

    assert graph._native_page_evidence(batch_root, 1) == nested


def test_native_resume_accepts_completed_top_level_page_packages(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    facts = {
        "path": str(source.resolve()),
        "sha256": graph._native_sha256(source),
        "total_pages": 1,
    }
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "source_sha256": facts["sha256"],
                "selected_pages": [1],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pages" / "page-001").mkdir(parents=True)
    (tmp_path / "page-units").mkdir()
    (tmp_path / "backups" / "page-units-pre-cleanup").mkdir(parents=True)
    for root in (
        tmp_path / "page-units",
        tmp_path / "backups" / "page-units-pre-cleanup",
    ):
        (root / "p001.md").write_text("<!-- page: 1 -->\n", encoding="utf-8")

    decision = graph._native_resume_decision(
        run_root=tmp_path,
        source_facts=facts,
        selected_pages=[1],
    )

    assert decision["conversion"] == "reuse-full"
    assert decision["batch_root"] == tmp_path


def test_native_batch_root_prefers_summary_run_root_over_progress_output(tmp_path: Path):
    batch_root = tmp_path / "native-smoke"

    assert graph._native_batch_root(
        {
            "output": str(batch_root / "progress.json"),
            "summary": {"run_root": str(batch_root)},
        }
    ) == batch_root.resolve()


def test_smoke_only_resume_restarts_full_conversion_instead_of_stalling(tmp_path: Path):
    run_root = tmp_path / "run"
    (run_root / graph.INTERNAL_STAGE_ROOT / graph.NATIVE_SMOKE_STAGE).mkdir(parents=True)
    reports = run_root / "reports"
    reports.mkdir()
    (reports / "native-smoke-report.json").write_text(
        json.dumps({"status": "passed", "smoke_pages": [1, 2, 3], "total_pages": 10}),
        encoding="utf-8",
    )

    decision = graph._native_resume_decision(
        run_root=run_root,
        source_facts={"path": "/tmp/book.pdf", "sha256": "abc", "total_pages": 10},
        selected_pages=list(range(1, 11)),
    )

    assert decision["smoke"] == "reuse"
    assert decision["conversion"] == "restart-full"
    assert decision["batch_root"] is None


def test_default_page_graph_delegates_to_hybrid_batch_in_exact_run_root(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    run_root = tmp_path / "existing-run"
    captured = {}

    monkeypatch.setattr(
        graph,
        "_native_pdf_facts",
        lambda _source: {"sha256": "abc", "total_pages": 3},
    )
    monkeypatch.setattr(
        graph.task_guard,
        "acquire_task_lock",
        lambda **_kwargs: nullcontext(),
    )

    def fake_run_page_batches(**kwargs):
        captured.update(kwargs)
        return 1, {
            "status": "needs-review",
            "summary": {
                "page_completion": {
                    "completed_pages": [1, 3],
                    "incomplete_pages": [2],
                }
            },
        }

    monkeypatch.setattr(batch, "run_page_batches", fake_run_page_batches)

    result = graph.run_page_hybrid_graph(
        source=source,
        run_root=run_root,
        pages="all",
        batch_size=1,
        timeout_seconds=60,
        dpi=220,
        resume=False,
    )

    assert captured["hybrid"] is True
    assert captured["resume_package"] == run_root.resolve()
    assert captured["visual_dpi"] == 220
    assert result["completed_pages"] == [1, 3]
    assert result["failed_pages"] == [2]
    assert result["final_status"] == "needs-review"
