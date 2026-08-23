from __future__ import annotations

import csv
import json
from pathlib import Path

import adapters.lan_pdf_markdown_batch as batch


def _write_pdf(path: Path, text: bytes = b"%PDF-1.7\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    return path


def test_document_routes_map_to_explicit_graph_modes():
    assert batch._graph_route_arguments("native-only") == ["--no-model"]
    assert batch._graph_route_arguments("hybrid") == []
    assert batch._graph_route_arguments("vision-only") == ["--visual-only"]


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
    (run_root / "index.md").write_text("# complete\n", encoding="utf-8")
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


def test_invoke_single_conversion_does_not_apply_stage_timeout_to_whole_book(
    tmp_path: Path, monkeypatch
):
    source = _write_pdf(tmp_path / "book.pdf", b"whole-book")
    entry = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")[0]
    run_root = tmp_path / "run"
    observed: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = '{"final_status":"passed"}'
        stderr = ""

    def fake_run(command, **kwargs):
        observed.update(kwargs)
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
    assert result["status"] == "passed"
    assert result["final_stage"] == "full-accepted"
