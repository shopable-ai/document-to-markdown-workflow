from __future__ import annotations

import argparse
import json
import math
import sys
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
import pdf_route_detection as detector  # noqa: E402


def _sample_record(page: int, *, text_profile: str, layout_profile: str) -> dict:
    return {
        "page": page,
        "text": {"non_ws": 0},
        "images": {"image_count": 0, "total_area_ratio": 0.0, "max_area_ratio": 0.0},
        "page_signal": {
            "text_profile": text_profile,
            "layout_profile": layout_profile,
            "full_page_image": False,
            "image_heavy": False,
        },
    }


def _write_current_report(output_root: Path, report: dict, *, source_root: Path | None = None) -> Path:
    run_root = output_root / "20260821-120000-current"
    report_path = run_root / detector.REPORT_NAME
    detector.write_json(report_path, report)
    entry = {
        "run_id": report["run_id"],
        "report": str(report_path),
        "source_root": str(source_root) if source_root else None,
    }
    detector.write_json(
        output_root / detector.CURRENT_INDEX_NAME,
        {
            "contract": detector.CONTRACT,
            "version": detector.VERSION,
            "current": entry,
            "sources": {str(source_root): entry} if source_root else {},
        },
    )
    return report_path


def test_select_sample_pages_keeps_head_middle_and_tail():
    assert detector.select_sample_pages(1) == [1]
    assert detector.select_sample_pages(6) == [1, 2, 3, 4, 5, 6]
    assert detector.select_sample_pages(20) == [1, 2, 3, 4, 5, 10, 15, 19, 20]


def test_parse_pdfimages_output_estimates_full_page_image_ratio():
    stdout = "\n".join(
        [
            "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio",
            "--------------------------------------------------------------------------------------------",
            "1 0 image 699 966 rgb 3 8 jpeg no 4 0 82 88 45.1K 2.3%",
            "2 0 image 18 18 rgb 3 8 jpeg no 14 0 105 105 808B 83%",
        ]
    )

    stats = detector.parse_pdfimages_output(stdout, page_size=(612.0, 792.0))

    assert math.isclose(stats[1].max_area_ratio, 1.0, rel_tol=0.03)
    assert stats[2].max_area_ratio < 0.01


def test_page_route_keeps_plain_text_native_only_without_visual_request():
    decision = detector.decide_page_route(
        page=12,
        native_text="这是第一段完整正文。\n这是第二段完整正文。",
        images=detector.PageImageStats(),
        bbox_metrics={"large_blank_region": False},
        table_count=0,
        rendered_structure={"visual_structure_present": False, "status": "passed"},
    )

    assert decision["route"] == "native-only"
    assert decision["needs_review"] is False
    assert decision["visual_model_required"] is False


def test_page_route_uses_visual_supplement_for_figure_reference_plus_image_object():
    decision = detector.decide_page_route(
        page=57,
        native_text=(
            "正文完整说明了业务增长率、相对市场占有率和四个象限的用途。"
            "管理者应结合资源投入、市场地位和产品阶段进行判断。"
            "如图 1 所示：\n图 1：波士顿矩阵图"
        ),
        images=detector.PageImageStats(
            image_count=1,
            total_area_ratio=0.32,
            max_area_ratio=0.32,
        ),
        bbox_metrics={
            "block_count": 4,
            "text_bbox_area_ratio": 0.11,
            "large_blank_region": True,
            "max_vertical_gap_ratio": 0.31,
        },
        table_count=0,
        rendered_structure={"visual_structure_present": True, "structure_types": ["matrix"]},
    )

    assert decision["route"] == "native-plus-visual"
    assert decision["needs_review"] is False
    assert "visual-cue-with-image-object" in decision["reason_codes"]
    assert {
        "native_text_chars",
        "text_signals",
        "image_stats",
        "bbox_metrics",
        "table_detection",
        "rendered_structure",
    } <= decision.keys()


def test_page_route_supplements_meaningful_image_even_without_text_cue():
    decision = detector.decide_page_route(
        page=58,
        native_text=(
            "这一页包含完整的分析正文，用于解释市场变化、业务位置、资源配置和行动优先级。"
            "原生文字足以阅读，但页面中的语义图片仍需要保留并补充其结构关系。"
        ),
        images=detector.PageImageStats(
            image_count=1,
            total_area_ratio=0.92,
            max_area_ratio=0.92,
        ),
        bbox_metrics={"block_count": 4, "text_bbox_area_ratio": 0.1, "large_blank_region": False},
        table_count=0,
        rendered_structure={"visual_structure_present": False, "status": "passed"},
    )

    assert decision["route"] == "native-plus-visual"
    assert "meaningful-page-image-present" in decision["reason_codes"]


def test_page_route_treats_short_image_labels_as_visual_only_not_complete_body():
    decision = detector.decide_page_route(
        page=1337,
        native_text="战略目标 关键任务 执行结果 复盘改进",
        images=detector.PageImageStats(
            image_count=1,
            total_area_ratio=0.67,
            max_area_ratio=0.67,
        ),
        bbox_metrics={
            "block_count": 1,
            "text_bbox_area_ratio": 0.012,
            "large_blank_region": True,
        },
        table_count=0,
        rendered_structure={"visual_structure_present": True, "structure_types": ["chart-axis-or-flow-lines"]},
    )

    assert decision["route"] == "visual-only"
    assert decision["native_body_sufficient"] is False


def test_pdfplumber_table_is_enough_to_supplement_even_without_detected_grid_lines():
    decision = detector.decide_page_route(
        page=62,
        native_text=(
            "以下内容比较四种方案的适用条件、主要优势、潜在限制和建议动作。"
            "正文信息完整，但表格的行列对应关系仍需要在同页视觉补充中保存。"
        ),
        images=detector.PageImageStats(),
        bbox_metrics={"block_count": 4, "text_bbox_area_ratio": 0.1, "large_blank_region": False},
        table_count=1,
        rendered_structure={"visual_structure_present": False, "status": "passed"},
    )

    assert decision["route"] == "native-plus-visual"
    assert decision["table_detection"]["present"] is True
    assert decision["rendered_structure"]["visual_structure_present"] is False


def test_page_route_sends_image_table_with_empty_native_text_to_visual_only():
    decision = detector.decide_page_route(
        page=179,
        native_text="\f",
        images=detector.PageImageStats(
            image_count=1,
            total_area_ratio=0.78,
            max_area_ratio=0.78,
        ),
        bbox_metrics={"large_blank_region": True},
        table_count=0,
        rendered_structure={
            "visual_structure_present": True,
            "structure_types": ["table-or-matrix"],
        },
    )

    assert decision["route"] == "visual-only"
    assert decision["visual_model_required"] is True
    assert "native-text-empty-or-insufficient" in decision["reason_codes"]


def test_page_route_marks_uncorroborated_figure_reference_needs_review_without_visual_call():
    decision = detector.decide_page_route(
        page=88,
        native_text="正文内容完整，具体关系如下图所示。",
        images=detector.PageImageStats(),
        bbox_metrics={"large_blank_region": True},
        table_count=0,
        rendered_structure={"visual_structure_present": False, "status": "passed"},
    )

    assert decision["route"] == "native-only"
    assert decision["needs_review"] is True
    assert decision["visual_model_required"] is False
    assert "uncorroborated-visual-text-cue" in decision["reason_codes"]


def test_decide_route_prefers_native_when_dense_text_dominates():
    sample_records = [
        _sample_record(page, text_profile="dense", layout_profile="narrative")
        for page in range(1, 9)
    ]
    sample_records.append(_sample_record(9, text_profile="none", layout_profile="diagram-like"))
    image_stats = {1: detector.PageImageStats(image_count=1, total_area_ratio=1.0, max_area_ratio=1.0)}

    summary = detector.summarise_document(
        total_pages=120,
        sample_records=sample_records,
        image_stats=image_stats,
    )
    decision = detector.decide_route(summary)

    assert decision["route"] == "native-only"
    assert "dense-native-text-dominates" in decision["reason_codes"]
    assert "isolated-cover-or-insert-pages-detected" in decision["reason_codes"]


def test_decide_route_allows_cover_only_outliers_to_stay_native():
    sample_records = [
        _sample_record(page, text_profile="dense", layout_profile="narrative")
        for page in range(1, 6)
    ]
    sample_records.extend(
        [
            _sample_record(6, text_profile="present", layout_profile="narrative"),
            _sample_record(7, text_profile="present", layout_profile="narrative"),
            _sample_record(8, text_profile="none", layout_profile="diagram-like"),
            _sample_record(9, text_profile="none", layout_profile="diagram-like"),
        ]
    )
    image_stats = {1: detector.PageImageStats(image_count=1, total_area_ratio=1.0, max_area_ratio=1.0)}

    summary = detector.summarise_document(
        total_pages=135,
        sample_records=sample_records,
        image_stats=image_stats,
    )
    decision = detector.decide_route(summary)

    assert decision["route"] == "native-only"
    assert "isolated-cover-or-insert-pages-detected" in decision["reason_codes"]


def test_decide_route_prefers_hybrid_when_text_exists_but_layout_is_sparse():
    sample_records = [
        _sample_record(page, text_profile="present", layout_profile="sparse-layout")
        for page in range(1, 10)
    ]
    image_stats = {3: detector.PageImageStats(image_count=2, total_area_ratio=0.42, max_area_ratio=0.24)}

    summary = detector.summarise_document(
        total_pages=20,
        sample_records=sample_records,
        image_stats=image_stats,
    )
    decision = detector.decide_route(summary)

    assert decision["route"] == "hybrid"
    assert "visual-structure-needs-staged-review" in decision["reason_codes"]


def test_decide_route_prefers_vision_when_diagram_pages_dominate():
    sample_records = [
        _sample_record(page, text_profile="sparse", layout_profile="diagram-like")
        for page in range(1, 10)
    ]

    summary = detector.summarise_document(
        total_pages=9,
        sample_records=sample_records,
        image_stats={},
    )
    decision = detector.decide_route(summary)

    assert decision["route"] == "vision-only"
    assert "diagram-like-pages-dominate" in decision["reason_codes"]


def test_decide_route_keeps_native_primary_when_images_dominate_but_sampled_text_is_usable():
    sample_records = [
        _sample_record(page, text_profile="dense", layout_profile="narrative")
        for page in range(1, 10)
    ]
    image_stats = {
        page: detector.PageImageStats(image_count=1, total_area_ratio=1.0, max_area_ratio=1.0)
        for page in range(1, 96)
    }

    summary = detector.summarise_document(
        total_pages=100,
        sample_records=sample_records,
        image_stats=image_stats,
    )
    decision = detector.decide_route(summary)

    assert decision["route"] == "hybrid"
    assert "native-text-exists" in decision["reason_codes"]
    assert "full-page-images-dominate" not in decision["reason_codes"]


def test_run_reuses_latest_record_by_path_without_rehashing(tmp_path, monkeypatch):
    output_root = tmp_path / "runs"
    source = tmp_path / "book.pdf"
    source.write_bytes(b"same-path-cache")
    snapshot = detector.snapshot_source(source, None)
    cached_record = {
        "status": "complete",
        "source_path": str(source),
        "relative_path": source.name,
        "source_sha256": "cached-sha",
        "file_size": snapshot.file_size,
        "modified_at": snapshot.modified_at,
        "decision": {
            "route": "native-only",
            "confidence": "high",
            "reason_codes": ["dense-native-text-dominates"],
            "recommended_entrypoint": "python3 adapters/run_pdf_batches.py --source <pdf> --output-root <dir> --pages all",
        },
    }
    _write_current_report(
        output_root,
        {
            "contract": detector.CONTRACT,
            "version": detector.VERSION,
            "run_id": "latest-run",
            "records": [cached_record],
        },
    )

    monkeypatch.setattr(detector, "sha256", lambda path: (_ for _ in ()).throw(AssertionError("sha256 should not run")))
    monkeypatch.setattr(
        detector,
        "analyse_source",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("analyse_source should not run")),
    )

    report_path = detector.run(
        argparse.Namespace(
            source=[source],
            source_dir=[],
            source_list=None,
            output_root=output_root,
            backfill_report=None,
        )
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["reused_count"] == 1
    assert report["summary"]["routes"]["native-only"] == 1
    reused = report["records"][0]
    assert reused["source_path"] == str(source)
    assert reused["reuse"]["matched_on"] == "path-size-mtime"
    assert report_path.name == detector.REPORT_NAME
    assert report_path.parent.parent == output_root
    assert report_path.parent.name.count("-") == 2
    assert (output_root / detector.CURRENT_INDEX_NAME).is_file()
    assert (report_path.parent / detector.ROUTE_LISTS_DIRNAME / "native-only.txt").read_text(
        encoding="utf-8"
    ) == f"{source}\n"


def test_run_reuses_latest_record_by_sha256_after_move(tmp_path, monkeypatch):
    output_root = tmp_path / "runs"
    source = tmp_path / "moved.pdf"
    source.write_bytes(b"same-content-different-path")
    source_sha = detector.sha256(source)
    snapshot = detector.snapshot_source(source, None)
    cached_record = {
        "status": "complete",
        "source_path": str(tmp_path / "old-name.pdf"),
        "relative_path": "old-name.pdf",
        "source_sha256": source_sha,
        "file_size": snapshot.file_size,
        "modified_at": "2026-08-20T00:00:00+00:00",
        "decision": {
            "route": "vision-only",
            "confidence": "high",
            "reason_codes": ["full-page-images-dominate"],
            "recommended_entrypoint": "python3 adapters/mac96_pdf_vision_runner.py --source <pdf> --output-root <dir> --pages all",
        },
    }
    _write_current_report(
        output_root,
        {
            "contract": detector.CONTRACT,
            "version": detector.VERSION,
            "run_id": "latest-run",
            "records": [cached_record],
        },
    )

    monkeypatch.setattr(
        detector,
        "analyse_source",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("analyse_source should not run")),
    )

    report_path = detector.run(
        argparse.Namespace(
            source=[source],
            source_dir=[],
            source_list=None,
            output_root=output_root,
            backfill_report=None,
        )
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["reused_count"] == 1
    assert report["summary"]["routes"]["vision-only"] == 1
    reused = report["records"][0]
    assert reused["source_path"] == str(source)
    assert reused["relative_path"] == source.name
    assert reused["source_sha256"] == source_sha
    assert reused["reuse"]["matched_on"] == "sha256"


def test_reader_outputs_for_single_source_dir_keep_technical_files_out_of_root(tmp_path):
    source_root = tmp_path / "source-root"
    source_root.mkdir()

    outputs = detector.publication_outputs(tmp_path / "runs", source_root)

    assert outputs["summary"] == str(source_root / "PDF转换路线总览.md")
    assert outputs["reader_root"] == str(source_root / "PDF转换路线")
    assert outputs["review"] == str(source_root / "PDF转换路线" / "待人工复核.csv")
    assert outputs["route_lists"]["native-only"] == str(source_root / "PDF转换路线" / "原生文本路线.txt")
    assert outputs["current_index"] == str(tmp_path / "runs" / "current.json")
    assert "latest_report" not in outputs
    assert "latest_records_ndjson" not in outputs


def test_backfill_report_writes_reader_outputs_without_pdf_analysis(tmp_path, monkeypatch):
    source_root = tmp_path / "电子书"
    source_root.mkdir()
    complete_source = source_root / "原生.pdf"
    failed_source = source_root / "失败.pdf"
    old_report_path = tmp_path / "old-report.json"
    old_report = {
        "contract": detector.CONTRACT,
        "version": "0.1.0",
        "run_id": "legacy-run",
        "detected_at": "2026-08-21T00:00:00+00:00",
        "status": "partial",
        "source_root": str(source_root),
        "tooling": {"pdfinfo": "pdfinfo"},
        "records": [
            {
                "status": "complete",
                "source_path": str(complete_source),
                "relative_path": complete_source.name,
                "source_sha256": "sha",
                "decision": {
                    "route": "native-only",
                    "confidence": "low",
                    "reason_codes": ["dense-native-text-dominates"],
                    "recommended_entrypoint": "native",
                },
            },
            {
                "status": "failed",
                "source_path": str(failed_source),
                "relative_path": failed_source.name,
                "error": "broken xref",
            },
        ],
    }
    detector.write_json(old_report_path, old_report)
    monkeypatch.setattr(
        detector,
        "analyse_source",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("backfill must not analyse PDFs")),
    )
    monkeypatch.setattr(
        detector,
        "discover_sources",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("backfill must not discover PDFs")),
    )

    report_path = detector.run(
        argparse.Namespace(
            source=[],
            source_dir=[source_root],
            source_list=None,
            output_root=tmp_path / "runs" / "pdf-route-detection",
            backfill_report=old_report_path,
        )
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["version"] == detector.VERSION
    assert report["summary"]["complete_count"] == 1
    assert report["summary"]["failed_count"] == 1
    assert report["migration"]["source_run_id"] == "legacy-run"
    assert report_path.parent.name.count("-") == 2
    assert (report_path.parent / detector.RECORDS_NAME).is_file()
    assert (report_path.parent / detector.ROUTE_LISTS_DIRNAME / "native-only.txt").read_text(
        encoding="utf-8"
    ) == f"{complete_source}\n"
    assert (source_root / "PDF转换路线总览.md").is_file()
    assert (source_root / "PDF转换路线" / "原生文本路线.txt").is_file()
    review = (source_root / "PDF转换路线" / "待人工复核.csv").read_text(encoding="utf-8-sig")
    assert "原生.pdf" in review
    assert "失败.pdf" in review
    assert "原生文本路线,低" in review
    assert "检测失败" in review
    assert "原生文字层内容密集" in review
    assert not list(source_root.glob("*.json"))
    assert not list(source_root.glob("*.ndjson"))


def test_analyse_source_samples_past_leading_image_pages_before_vision_decision(
    tmp_path, monkeypatch
):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan")
    snapshot = detector.snapshot_source(source, None)
    full_image_probe = {"called": False}

    def fake_run_command(command, *, timeout=120):
        if command[0] == "pdfinfo":
            return SimpleNamespace(
                returncode=0,
                stdout="Pages: 10\nPage size: 612 x 792 pts\n",
                stderr="",
            )
        if command[0] == "pdfimages" and "-f" in command and "-l" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio",
                        "--------------------------------------------------------------------------------------------",
                        "1 0 image 699 966 rgb 3 8 jpeg no 4 0 82 88 45.1K 2.3%",
                        "2 0 image 699 966 rgb 3 8 jpeg no 5 0 82 88 45.1K 2.3%",
                        "3 0 image 699 966 rgb 3 8 jpeg no 6 0 82 88 45.1K 2.3%",
                    ]
                ),
                stderr="",
            )
        if command[0] == "pdfimages" and "-list" in command:
            full_image_probe["called"] = True
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio",
                        "--------------------------------------------------------------------------------------------",
                        "1 0 image 699 966 rgb 3 8 jpeg no 4 0 82 88 45.1K 2.3%",
                        "2 0 image 699 966 rgb 3 8 jpeg no 5 0 82 88 45.1K 2.3%",
                        "3 0 image 699 966 rgb 3 8 jpeg no 6 0 82 88 45.1K 2.3%",
                        "5 0 image 200 200 rgb 3 8 jpeg no 7 0 72 72 12.0K 0.4%",
                    ]
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(detector, "run_command", fake_run_command)

    def fake_extract_text_metrics(pdftotext, source_path, page):
        if page in {1, 2, 3}:
            return {
                "chars": 0,
                "non_ws": 0,
                "line_count": 0,
                "non_empty_line_count": 0,
                "long_line_count": 0,
                "token_count": 0,
                "avg_non_ws_per_line": 0.0,
                "preview": "",
            }
        if page == 5:
            return {
                "chars": 180,
                "non_ws": 120,
                "line_count": 12,
                "non_empty_line_count": 8,
                "long_line_count": 3,
                "token_count": 22,
                "avg_non_ws_per_line": 15.0,
                "preview": "目录 第一章 第二章 第三章",
            }
        return {
            "chars": 620,
            "non_ws": 420,
            "line_count": 20,
            "non_empty_line_count": 14,
            "long_line_count": 8,
            "token_count": 120,
            "avg_non_ws_per_line": 24.0,
            "preview": "这是可读的正文内容，用于证明原生文字层存在并可恢复段落结构。",
        }

    monkeypatch.setattr(detector, "extract_text_metrics", fake_extract_text_metrics)

    record = detector.analyse_source(
        source_snapshot=snapshot,
        source_sha256="sha",
        pdfinfo="pdfinfo",
        pdftotext="pdftotext",
        pdfimages="pdfimages",
    )

    assert full_image_probe["called"] is True
    assert record["decision"]["route"] in {"native-only", "hybrid"}
    assert "leading-pages-full-image-no-text" not in record["decision"]["reason_codes"]
    assert record["sample_pages"][-1] == 10
