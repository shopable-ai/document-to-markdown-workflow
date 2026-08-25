from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import adapters.lan_pdf_markdown_batch as batch


def _write_pdf(path: Path, text: bytes = b"%PDF-1.7\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    return path


def test_document_routes_map_to_explicit_graph_modes():
    assert batch._graph_route_arguments("native-only") == ["--no-model"]
    assert batch._graph_route_arguments("hybrid") == []
    assert batch._graph_route_arguments("vision-only") == []


def test_shortest_first_orders_by_real_page_count(tmp_path: Path, monkeypatch):
    long = _write_pdf(tmp_path / "long.pdf")
    short_b = _write_pdf(tmp_path / "B-short.pdf")
    short_a = _write_pdf(tmp_path / "A-short.pdf")
    entries = batch.entries_from_paths(
        [long, short_b, short_a], source_root=tmp_path, route="hybrid"
    )
    counts = {long: 100, short_b: 2, short_a: 2}
    monkeypatch.setattr(batch, "_pdf_page_count", lambda source: counts[source])

    ordered = batch.order_entries(entries, "shortest-first")

    assert [entry.source for entry in ordered] == [short_a, short_b, long]


def test_source_directory_discovers_pdfs_recursively_and_defaults_to_hybrid(
    tmp_path: Path,
):
    source_root = tmp_path / "books"
    first = _write_pdf(source_root / "A" / "first.pdf", b"first")
    second = _write_pdf(source_root / "B" / "SECOND.PDF", b"second")
    (source_root / "ignore.txt").write_text("not a PDF", encoding="utf-8")

    entries, origin = batch.discover_source_entries(
        sources=[],
        source_directory=source_root,
        source_list=None,
        route_list=None,
        route_report=None,
        route=None,
        source_root=None,
    )

    assert [entry.source for entry in entries] == [first, second]
    assert [entry.relative_path for entry in entries] == ["A/first.pdf", "B/SECOND.PDF"]
    assert {entry.route for entry in entries} == {"hybrid"}
    assert origin == {
        "kind": "source-directory",
        "route": "hybrid",
        "source_directory": str(source_root.resolve()),
        "source_root": str(source_root.resolve()),
        "pdf_count": 2,
    }


def _report_record(path: Path, *, route: str, sha: str, relative_path: str) -> dict:
    stat = path.stat()
    return {
        "status": "complete",
        "source_path": str(path),
        "relative_path": relative_path,
        "source_sha256": sha,
        "file_size": stat.st_size,
        "modified_at": "2026-08-21T09:00:00+08:00",
        "decision": {
            "route": route,
            "confidence": "high",
            "reason_codes": ["dense-native-text-dominates"],
        },
    }


def _write_complete_run(
    run_root: Path,
    *,
    source: Path,
    selected_pages: list[int],
    total_pages: int,
    progress_status: str = "passed",
) -> None:
    source_hash = batch.sha256(source)
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "index.md").write_text(
        "".join(
            f"<!-- page: {page} -->\n\n## 第 {page} 页\n\n第 {page} 页正文内容。\n\n"
            for page in selected_pages
        ),
        encoding="utf-8",
    )
    common = {
        "status": "passed",
        "source": str(source.resolve()),
        "source_sha256": source_hash,
        "selected_pages": selected_pages,
        "total_pages": total_pages,
    }
    (run_root / "summary.json").write_text(json.dumps(common), encoding="utf-8")
    reports = run_root / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "validation-result.json").write_text(
        json.dumps(
            {
                **common,
                "actual_full_pdf_coverage": selected_pages == list(range(1, total_pages + 1)),
                "checks": {"full_pdf_coverage_required_and_met": True},
            }
        ),
        encoding="utf-8",
    )
    evidence = run_root / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "progress-current.json").write_text(
        json.dumps(
            {
                **common,
                "status": progress_status,
                "current_stage": "heading-audit-repair-validation",
                "completed_stages": [
                    {"stage": "heading-audit-repair-validation", "status": "passed"}
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_complete_hybrid_run(
    run_root: Path, *, source: Path, selected_pages: list[int], total_pages: int
) -> None:
    source_hash = batch.sha256(source)
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "index.md").write_text(
        "".join(
            f"<!-- page:{page} -->\n\n## 第 {page} 页\n\n混合路线第 {page} 页正文。\n\n"
            for page in selected_pages
        ),
        encoding="utf-8",
    )
    common = {
        "status": "passed",
        "source": str(source.resolve()),
        "source_sha256": source_hash,
        "selected_pages": selected_pages,
        "total_pages": total_pages,
    }
    (run_root / "summary.json").write_text(json.dumps(common), encoding="utf-8")
    (run_root / "acceptance.json").write_text(
        json.dumps(
            {
                **common,
                "checks": {
                    "selected_pages_cover_full_pdf": True,
                    "page_completion_passed": True,
                    "no_model_calls_required": False,
                    "no_model_calls": True,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_root / "progress.json").write_text(
        json.dumps({**common, "stages": [{"stage": "independent_acceptance", "status": "passed"}]}),
        encoding="utf-8",
    )


def test_final_completion_accepts_hybrid_contract(tmp_path: Path):
    source = _write_pdf(tmp_path / "hybrid.pdf", b"hybrid")
    run_root = tmp_path / "hybrid-run"
    _write_complete_hybrid_run(run_root, source=source, selected_pages=[1, 2], total_pages=2)

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["accepted"] is True
    assert result["completion_kind"] == "validated-final"
    assert result["validation_report"] == str(run_root / "acceptance.json")
    assert result["progress_path"] == str(run_root / "progress.json")


def test_entries_from_route_report_prefers_route_list_order(tmp_path: Path):
    source_root = tmp_path / "books"
    first = _write_pdf(source_root / "A" / "first.pdf", b"first")
    second = _write_pdf(source_root / "B" / "second.pdf", b"second")
    route_list = tmp_path / "native-only.txt"
    route_list.write_text(f"{second}\n{first}\n", encoding="utf-8")
    report = {
        "run_id": "20260821-170459-d19ae62475",
        "source_root": str(source_root),
        "outputs": {"route_lists": {"native-only": str(route_list)}},
        "records": [
            _report_record(first, route="native-only", sha="sha-first", relative_path="A/first.pdf"),
            _report_record(second, route="native-only", sha="sha-second", relative_path="B/second.pdf"),
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    entries, origin = batch.entries_from_route_report(report_path, "native-only")

    assert [entry.source for entry in entries] == [second, first]
    assert entries[0].relative_path == "B/second.pdf"
    assert entries[0].source_sha256 == "sha-second"
    assert origin["selected_list"] == str(route_list)


def test_entries_from_route_report_fall_back_to_records_when_list_missing(tmp_path: Path):
    source_root = tmp_path / "books"
    native = _write_pdf(source_root / "native.pdf", b"native")
    hybrid = _write_pdf(source_root / "hybrid.pdf", b"hybrid")
    report = {
        "run_id": "20260821-170459-d19ae62475",
        "source_root": str(source_root),
        "records": [
            _report_record(native, route="native-only", sha="sha-native", relative_path="native.pdf"),
            _report_record(hybrid, route="hybrid", sha="sha-hybrid", relative_path="hybrid.pdf"),
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    entries, origin = batch.entries_from_route_report(report_path, "native-only")

    assert [entry.source for entry in entries] == [native]
    assert origin["selected_list"] is None


def test_entries_from_route_report_skips_missing_sources_from_reader_list(tmp_path: Path):
    source_root = tmp_path / "books"
    existing = _write_pdf(source_root / "keep.pdf", b"keep")
    missing = source_root / "missing.pdf"
    route_list = tmp_path / "native-only.txt"
    route_list.write_text(f"{existing}\n{missing}\n", encoding="utf-8")
    report = {
        "run_id": "20260821-170459-d19ae62475",
        "source_root": str(source_root),
        "outputs": {"route_lists": {"native-only": str(route_list)}},
        "records": [
            _report_record(existing, route="native-only", sha="sha-keep", relative_path="keep.pdf"),
            {
                "status": "complete",
                "source_path": str(missing),
                "relative_path": "missing.pdf",
                "source_sha256": "sha-missing",
                "file_size": 1,
                "modified_at": "2026-08-21T09:00:00+08:00",
                "decision": {"route": "native-only", "confidence": "low", "reason_codes": []},
            },
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    entries, origin = batch.entries_from_route_report(report_path, "native-only")

    assert [entry.source for entry in entries] == [existing]
    assert origin["missing_sources"] == [str(missing)]


def test_nonpassed_results_are_rebased_and_promoted_to_stronger_route(tmp_path: Path):
    source_root = tmp_path / "books"
    needs_review = _write_pdf(source_root / "nested" / "review.pdf", b"review")
    passed = _write_pdf(source_root / "passed.pdf", b"passed")
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "status": "needs-review",
                        "source_path": str(needs_review),
                        "source_sha256": batch.sha256(needs_review),
                    },
                    {"status": "passed", "source_path": str(passed)},
                ]
            }
        ),
        encoding="utf-8",
    )

    entries = batch.entries_from_nonpassed_results(
        [results_path], source_root=source_root, route="hybrid"
    )

    assert len(entries) == 1
    assert entries[0].source == needs_review
    assert entries[0].relative_path == "nested/review.pdf"
    assert entries[0].route == "hybrid"


def test_run_batch_preflights_all_sources_before_invoking_any_conversion(
    tmp_path: Path, monkeypatch
):
    first = _write_pdf(tmp_path / "first.pdf")
    second = _write_pdf(tmp_path / "second.pdf")
    entries = batch.entries_from_paths(
        [first, second], source_root=tmp_path, route="native-only"
    )
    calls: list[str] = []

    def fake_probe(entry: batch.SourceEntry) -> str | None:
        return None if entry.source == first else "permission denied"

    def fake_executor(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("executor must not start after preflight failure")

    monkeypatch.setattr(batch, "_source_preflight_error", fake_probe)

    try:
        batch.run_batch(
            entries,
            batch_root=tmp_path / "batch-root",
            origin={"kind": "literal-sources"},
            config={
                "pages": "all",
                "evidence_level": "compact",
                "batch_size": 1,
                "timeout_seconds": 30,
            },
            executor=fake_executor,
        )
    except ValueError as exc:
        assert "PDF readability preflight failed for 1/2 sources" in str(exc)
        assert str(second) in str(exc)
        assert "permission denied" in str(exc)
    else:  # pragma: no cover - protects the fail-closed contract
        raise AssertionError("run_batch should reject unreadable inputs")

    assert calls == []
    assert not (tmp_path / "batch-root").exists()


def test_run_batch_rejects_invalid_page_batch_size_before_invoking_any_book(
    tmp_path: Path,
):
    first = _write_pdf(tmp_path / "first.pdf")
    second = _write_pdf(tmp_path / "second.pdf")
    entries = batch.entries_from_paths(
        [first, second], source_root=tmp_path, route="hybrid"
    )
    calls: list[str] = []

    def fake_executor(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("executor must not start after config preflight failure")

    with pytest.raises(ValueError, match="batch_size must be 1"):
        batch.run_batch(
            entries,
            batch_root=tmp_path / "batch-root",
            origin={"kind": "literal-sources"},
            config={
                "pages": "all",
                "evidence_level": "compact",
                "batch_size": 8,
                "timeout_seconds": 30,
            },
            executor=fake_executor,
        )

    assert calls == []
    assert not (tmp_path / "batch-root").exists()


def test_collection_lock_blocks_a_second_queue_with_a_different_batch_root(
    tmp_path: Path,
):
    source = _write_pdf(tmp_path / "book.pdf")
    entries = batch.entries_from_paths(
        [source], source_root=tmp_path, route="hybrid"
    )
    lock_root = tmp_path / "collection"

    with batch.acquire_collection_lock(
        lock_root=lock_root,
        batch_root=lock_root / "first-batch",
        entries=entries,
    ):
        with pytest.raises(batch.BatchLockError, match="active PDF collection queue"):
            batch.run_batch(
                entries,
                batch_root=lock_root / "second-batch",
                origin={"kind": "literal-sources"},
                config={
                    "pages": "all",
                    "evidence_level": "compact",
                    "batch_size": 1,
                    "timeout_seconds": 30,
                },
                executor=lambda *_args, **_kwargs: pytest.fail(
                    "the second queue must not invoke a book"
                ),
            )


def test_paused_collection_blocks_before_invoking_any_book(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf")
    entries = batch.entries_from_paths(
        [source], source_root=tmp_path, route="hybrid"
    )
    collection_root = tmp_path / "collection"
    collection_root.mkdir()
    (collection_root / ".pdf-collection-paused.json").write_text(
        json.dumps({"reason": "disk-space-below-safety-threshold"}),
        encoding="utf-8",
    )

    with pytest.raises(batch.BatchLockError, match="PDF collection is paused"):
        batch.run_batch(
            entries,
            batch_root=collection_root / "batch-root",
            origin={"kind": "literal-sources"},
            config={
                "pages": "all",
                "evidence_level": "compact",
                "batch_size": 1,
                "timeout_seconds": 30,
            },
            executor=lambda *_args, **_kwargs: pytest.fail(
                "a paused collection must not invoke a book"
            ),
        )

    assert not (collection_root / "batch-root").exists()


def test_run_batch_stops_before_next_book_when_previous_left_a_live_worker(
    tmp_path: Path, monkeypatch
):
    first = _write_pdf(tmp_path / "first.pdf")
    second = _write_pdf(tmp_path / "second.pdf")
    entries = batch.entries_from_paths(
        [first, second], source_root=tmp_path, route="hybrid"
    )
    calls: list[str] = []

    def fake_executor(entry: batch.SourceEntry, *, run_root: Path, **_kwargs) -> dict:
        calls.append(str(entry.source))
        return {
            "status": "failed",
            "execution": "completed",
            "resume_used": False,
            "run_root": str(run_root),
            "returncode": 1,
            "error": "child failed",
        }

    monkeypatch.setattr(
        batch,
        "_active_conversion_processes",
        lambda entry, run_root: [
            f"999 lan_pdf_markdown_graph.py --source {entry.source} --run-root {run_root}"
        ],
    )

    code, payload = batch.run_batch(
        entries,
        batch_root=tmp_path / "batch-root",
        origin={"kind": "literal-sources"},
        config={
            "pages": "all",
            "evidence_level": "compact",
            "batch_size": 1,
            "timeout_seconds": 30,
        },
        executor=fake_executor,
    )

    assert code == 1
    assert calls == [str(first)]
    assert payload["summary"]["pending_total"] == 1
    assert payload["summary"]["status"] == "failed"
    assert payload["summary"]["halted"] is True


def test_run_batch_resumes_and_skips_existing_success(tmp_path: Path):
    source_root = tmp_path / "books"
    first = _write_pdf(source_root / "A" / "first.pdf", b"first")
    second = _write_pdf(source_root / "B" / "second.pdf", b"second")
    entries = batch.entries_from_paths([first, second], source_root=source_root, route="native-only")
    batch_root = tmp_path / "batch-root"
    calls: list[str] = []

    def fake_executor(
        entry: batch.SourceEntry,
        *,
        run_root: Path,
        pages: str,
        evidence_level: str,
        batch_size: int,
        timeout_seconds: int,
        resume: bool,
    ) -> dict:
        calls.append(str(entry.source))
        _write_complete_run(
            run_root,
            source=entry.source,
            selected_pages=[1, 2],
            total_pages=2,
        )
        report_dir = run_root / "reports"
        return {
            "status": "passed",
            "execution": "completed",
            "resume_used": resume,
            "run_root": str(run_root),
            "index_markdown": str(run_root / "index.md"),
            "summary_path": str(run_root / "summary.json"),
            "validation_report": str(report_dir / "validation-result.json"),
            "child_result": {"final_status": "passed"},
            "summary_status": "passed",
            "validation_status": "passed",
            "command": "fake",
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "started_at": "2026-08-21T09:00:00+08:00",
            "finished_at": "2026-08-21T09:00:01+08:00",
            "error": None,
        }

    code, first_payload = batch.run_batch(
        entries,
        batch_root=batch_root,
        origin={"kind": "literal-sources"},
        config={"pages": "all", "evidence_level": "compact", "batch_size": 1, "timeout_seconds": 30},
        executor=fake_executor,
    )

    assert code == 0
    assert len(calls) == 2
    assert first_payload["summary"]["passed"] == 2

    calls.clear()
    code, second_payload = batch.run_batch(
        entries,
        batch_root=batch_root,
        origin={"kind": "literal-sources"},
        config={"pages": "all", "evidence_level": "compact", "batch_size": 1, "timeout_seconds": 30},
        executor=fake_executor,
    )

    assert code == 0
    assert calls == []
    assert second_payload["summary"]["skipped_existing"] == 2

    rows = list(csv.DictReader((batch_root / "source-output-map.csv").read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 2
    assert rows[0]["status"] == "passed"
    assert rows[0]["execution"] == "skipped-existing"


def test_smoke_only_run_is_not_existing_success(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf", b"smoke-only")
    run_root = tmp_path / "run"
    smoke_root = run_root / ".stages" / "native-smoke"
    _write_complete_run(
        smoke_root,
        source=source,
        selected_pages=[1, 2, 3],
        total_pages=10,
    )
    item = {
        "status": "passed",
        "source_path": str(source.resolve()),
        "source_sha256": batch.sha256(source),
        "run_root": str(run_root),
        "validation_report": str(smoke_root / "reports" / "validation-result.json"),
    }

    assert batch._existing_success(item, requested_pages="all") is False


def test_full_coverage_mismatch_is_not_existing_success(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf", b"partial")
    run_root = tmp_path / "run"
    _write_complete_run(
        run_root,
        source=source,
        selected_pages=[1, 2, 3],
        total_pages=10,
    )
    item = {
        "status": "passed",
        "source_path": str(source.resolve()),
        "source_sha256": batch.sha256(source),
        "run_root": str(run_root),
        "validation_report": str(run_root / "reports" / "validation-result.json"),
    }

    assert batch._existing_success(item, requested_pages="all") is False


def test_final_progress_must_be_passed_before_existing_run_is_skipped(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf", b"interrupted-final-write")
    run_root = tmp_path / "run"
    _write_complete_run(
        run_root,
        source=source,
        selected_pages=[1, 2],
        total_pages=2,
        progress_status="running",
    )
    item = {
        "status": "passed",
        "source_path": str(source.resolve()),
        "source_sha256": batch.sha256(source),
        "run_root": str(run_root),
        "validation_report": str(run_root / "reports" / "validation-result.json"),
    }

    assert batch._existing_success(item, requested_pages="all") is False


def test_final_completion_records_markdown_and_package_content_sizes(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf", b"content-metrics")
    run_root = tmp_path / "run"
    _write_complete_run(
        run_root,
        source=source,
        selected_pages=[1, 2],
        total_pages=2,
    )

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["accepted"] is True
    assert result["index_bytes"] > 0
    assert result["index_characters"] > 0
    assert result["meaningful_text_characters"] > 0
    assert result["page_marker_count"] == 2
    assert result["package_file_count"] >= 4
    assert result["package_total_bytes"] >= result["markdown_total_bytes"] > 0
    assert result["markdown_file_count"] == 1


def test_heading_only_markdown_is_not_accepted_as_completed_content(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf", b"empty-template")
    run_root = tmp_path / "run"
    _write_complete_run(
        run_root,
        source=source,
        selected_pages=[1],
        total_pages=1,
    )
    (run_root / "index.md").write_text(
        "<!-- page: 1 -->\n\n## 第 1 页\n",
        encoding="utf-8",
    )

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["meaningful_text_characters"] == 0
    assert result["accepted"] is False


def test_wrong_page_marker_sequence_is_rejected_even_when_count_matches(tmp_path: Path):
    source = _write_pdf(tmp_path / "wrong-sequence.pdf", b"source")
    run_root = tmp_path / "run"
    _write_complete_run(run_root, source=source, selected_pages=[1, 2], total_pages=2)
    (run_root / "index.md").write_text(
        "<!-- page:1 -->\n\n正文一\n\n<!-- page:1 -->\n\n正文二\n",
        encoding="utf-8",
    )

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["page_marker_count"] == 2
    assert result["page_marker_sequence"] == [1, 1]
    assert result["accepted"] is False


def test_missing_relative_image_and_incomplete_placeholder_are_rejected(tmp_path: Path):
    source = _write_pdf(tmp_path / "bad-assets.pdf", b"source")
    run_root = tmp_path / "run"
    _write_complete_run(run_root, source=source, selected_pages=[1], total_pages=1)
    (run_root / "index.md").write_text(
        "<!-- page:1 -->\n\n![页面](assets/missing.png)\n\n*本页转换尚未完成，需继续处理。*\n",
        encoding="utf-8",
    )

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["missing_image_references"] == ["assets/missing.png"]
    assert result["incomplete_placeholder_present"] is True
    assert result["accepted"] is False


def test_image_only_page_is_accepted_when_relative_asset_exists(tmp_path: Path):
    source = _write_pdf(tmp_path / "image-page.pdf", b"source")
    run_root = tmp_path / "run"
    _write_complete_run(run_root, source=source, selected_pages=[1], total_pages=1)
    assets = run_root / "assets"
    assets.mkdir()
    (assets / "page-001.png").write_bytes(b"png")
    (run_root / "index.md").write_text(
        "<!-- page:1 -->\n\n## 第 1 页\n\n![页面](assets/page-001.png)\n",
        encoding="utf-8",
    )

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["meaningful_text_characters"] == 0
    assert result["valid_image_reference_count"] == 1
    assert result["accepted"] is True


def test_invoke_single_conversion_does_not_apply_stage_timeout_to_whole_book(
    tmp_path: Path, monkeypatch
):
    source = _write_pdf(tmp_path / "book.pdf", b"whole-book")
    entry = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")[0]
    run_root = tmp_path / "run"
    observed: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        assert not run_root.exists()
        kwargs["stdout"].write('{"final_status":"passed"}\n')
        kwargs["stdout"].flush()
        _write_complete_run(
            run_root,
            source=source,
            selected_pages=[1],
            total_pages=1,
        )
        return Completed()

    monkeypatch.setattr(batch.subprocess, "run", fake_run)

    result = batch.invoke_single_conversion(
        entry,
        run_root=run_root,
        pages="all",
        evidence_level="compact",
        batch_size=1,
        timeout_seconds=30,
        resume=False,
    )

    assert "timeout" not in observed
    assert "capture_output" not in observed
    assert Path(result["stdout_log"]).is_file()
    assert result["stdout_tail"] == '{"final_status":"passed"}'
    assert result["status"] == "passed"
    assert result["final_stage"] == "full-accepted"


def test_invoke_single_conversion_returns_only_bounded_log_tails(
    tmp_path: Path, monkeypatch
):
    source = _write_pdf(tmp_path / "book.pdf", b"large-logs")
    entry = batch.entries_from_paths(
        [source], source_root=tmp_path, route="native-only"
    )[0]
    run_root = tmp_path / "run"

    class Completed:
        returncode = 0

    def fake_run(_command, **kwargs):
        kwargs["stdout"].write(
            "STDOUT-BEGIN-MUST-NOT-BE-RETURNED\n"
            + ("x" * 20000)
            + '\n{"final_status":"passed"}\n'
        )
        kwargs["stderr"].write(
            "STDERR-BEGIN-MUST-NOT-BE-RETURNED\n" + ("y" * 20000) + "\n"
        )
        kwargs["stdout"].flush()
        kwargs["stderr"].flush()
        _write_complete_run(
            run_root,
            source=source,
            selected_pages=[1],
            total_pages=1,
        )
        return Completed()

    monkeypatch.setattr(batch.subprocess, "run", fake_run)

    result = batch.invoke_single_conversion(
        entry,
        run_root=run_root,
        pages="all",
        evidence_level="compact",
        batch_size=1,
        timeout_seconds=30,
        resume=False,
    )

    assert len(result["stdout_tail"].encode("utf-8")) <= 12000
    assert len(result["stderr_tail"].encode("utf-8")) <= 12000
    assert "STDOUT-BEGIN-MUST-NOT-BE-RETURNED" not in result["stdout_tail"]
    assert "STDERR-BEGIN-MUST-NOT-BE-RETURNED" not in result["stderr_tail"]
    assert result["child_result"] == {"final_status": "passed"}
    assert result["status"] == "passed"
