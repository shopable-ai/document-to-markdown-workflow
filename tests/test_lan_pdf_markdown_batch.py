from __future__ import annotations

import csv
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import adapters.lan_pdf_markdown_batch as batch


def _write_pdf(path: Path, text: bytes = b"%PDF-1.7\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    return path


BATCH_CONFIG = {"pages": "all", "evidence_level": "compact", "batch_size": 1, "timeout_seconds": 30}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def test_flat_package_names_keep_deep_same_title_sources_separate(tmp_path: Path):
    source_root = tmp_path / "library"
    left = _write_pdf(source_root / "left" / "deep" / "Book.pdf")
    right = _write_pdf(source_root / "right" / "deep" / "Book.pdf")
    entries = batch.entries_from_paths([left, right], source_root=source_root, route="hybrid")
    batch_root = tmp_path / "conversions" / "hybrid"
    roots = [batch.run_root_for_entry(batch_root, entry) for entry in entries]

    assert len(set(roots)) == 2
    assert all(root.parent == batch_root and root.name.startswith("Book-") for root in roots)
    assert roots == [batch.run_root_for_entry(batch_root, entry) for entry in entries]


def test_existing_batch_layout_is_pinned_and_aliases_are_not_legacy_packages(tmp_path: Path):
    source = _write_pdf(tmp_path / "library" / "Book.pdf")
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    runs = tmp_path / "runs"
    runs.mkdir()
    (tmp_path / "book").mkdir()
    (runs / "book").symlink_to("../book", target_is_directory=True)
    assert batch._package_layout(tmp_path, None, entries) == batch.FLAT_LAYOUT

    (runs / "old-package").mkdir()
    with pytest.raises(ValueError, match="ambiguous legacy directory"):
        batch._package_layout(tmp_path, None, entries)
    expected = batch.run_root_for_entry(tmp_path, entries[0], layout=batch.LEGACY_LAYOUT)
    expected.mkdir(parents=True)
    (expected / "progress.json").write_text("{}", encoding="utf-8")
    assert batch._package_layout(tmp_path, None, entries) == batch.LEGACY_LAYOUT
    assert batch._package_layout(tmp_path, {"format_version": "0.1.0"}, entries) == batch.LEGACY_LAYOUT
    assert batch._package_layout(tmp_path, {"package_layout": "flat-v1"}, entries) == batch.FLAT_LAYOUT
    with pytest.raises(ValueError, match="unsupported batch package layout"):
        batch._package_layout(tmp_path, {"package_layout": "future"}, entries)


def test_legacy_run_root_keeps_original_source_parents_and_fingerprint(tmp_path: Path):
    source = _write_pdf(tmp_path / "A" / "B" / "Book.pdf")
    entry, = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    root = tmp_path / "batch"

    assert batch.run_root_for_entry(root, entry, layout=batch.LEGACY_LAYOUT) == (
        root / "runs" / "A" / "B" / f"Book-{batch.sha256(source)[:8]}"
    )


@pytest.mark.parametrize("layout", [batch.FLAT_LAYOUT, batch.LEGACY_LAYOUT])
def test_child_logs_stay_at_the_batch_root_in_both_layouts(tmp_path: Path, layout: str):
    source = _write_pdf(tmp_path / "input" / "deep" / "Book.pdf")
    entry, = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    root = tmp_path / "runs" / "batch"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"package_layout": layout}), encoding="utf-8")

    package = batch.run_root_for_entry(root, entry, layout=layout)
    assert batch._child_log_root(package).parent == root / "logs"


def test_resume_preserves_relocated_package_even_after_results_snapshot_is_lost(tmp_path: Path):
    source = _write_pdf(tmp_path / "Book.pdf")
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    root = tmp_path / "batch"
    relocated = root / "Book-existing"
    relocated.mkdir(parents=True)
    (relocated / "progress.json").write_text("{}", encoding="utf-8")
    result = {"source_path": str(source), "source_sha256": batch.sha256(source),
              "run_root": str(relocated), "status": "failed"}
    results_path = root / "results.json"
    results_path.write_text(json.dumps({"items": [result]}), encoding="utf-8")

    calls = []

    def executor(entry, *, run_root, resume, **kwargs):
        calls.append((run_root, resume))
        return result

    config = {"pages": "all", "evidence_level": "compact", "batch_size": 1, "timeout_seconds": 30}
    batch.run_batch(entries, batch_root=root, origin={}, config=config, executor=executor)
    results_path.unlink()
    batch.run_batch(entries, batch_root=root, origin={}, config=config, executor=executor)

    assert calls == [(relocated, True), (relocated, True)]
    assert not batch.run_root_for_entry(root, entries[0]).exists()


def test_registered_package_cannot_point_outside_batch(tmp_path: Path):
    source = _write_pdf(tmp_path / "Book.pdf")
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    root = tmp_path / "batch"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    alias = root / "book"
    alias.symlink_to(outside, target_is_directory=True)
    result = {"source_sha256": batch.sha256(source), "run_root": str(alias)}
    with pytest.raises(ValueError, match="package path outside batch"):
        batch._registered_run_roots(root, entries, batch.FLAT_LAYOUT, None, {str(source): result})


def test_manifest_only_resume_rejects_changed_pdf_at_same_path(tmp_path: Path):
    source = _write_pdf(tmp_path / "Book.pdf", b"%PDF-old")
    entry = batch.SourceEntry(str(source), "Book.pdf")
    root = tmp_path / "batch"
    run_roots = batch._registered_run_roots(root, [entry], batch.FLAT_LAYOUT, None, {})
    source.write_bytes(b"%PDF-new")

    with pytest.raises(ValueError, match="manifest package source fingerprint differs"):
        batch._registered_run_roots(root, [entry], batch.FLAT_LAYOUT, {"run_roots": run_roots}, {})


@pytest.mark.parametrize("parent_spelling", ["Café", "CAFÉ", "Café"])
def test_registered_packages_may_not_nest_inside_one_another(tmp_path: Path, parent_spelling: str):
    left = _write_pdf(tmp_path / "A.pdf")
    right = _write_pdf(tmp_path / "B.pdf")
    entries = batch.entries_from_paths([left, right], source_root=tmp_path, route="hybrid")
    root = tmp_path / "batch"
    run_roots = {
        str(left): {"path": str(root / "Café"), "source_sha256": batch.sha256(left)},
        str(right): {"path": str(root / parent_spelling / "assets" / "B"), "source_sha256": batch.sha256(right)},
    }

    with pytest.raises(ValueError, match="registered package paths overlap"):
        batch._registered_run_roots(root, entries, batch.FLAT_LAYOUT, {"run_roots": run_roots}, {})


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
    units = run_root / "page-units"
    units.mkdir(exist_ok=True)
    backups = run_root / "backups" / "page-units-pre-cleanup"
    backups.mkdir(parents=True, exist_ok=True)
    page_texts = []
    for page in selected_pages:
        raw_text = f"第 {page} 页正文内容。"
        text = f"<!-- page: {page} -->\n\n## 第 {page} 页\n\n{raw_text}"
        for directory in (units, backups):
            (directory / f"p{page:03d}.md").write_text(text + "\n", encoding="utf-8")
        name = f"page-{page:03d}"
        extraction = run_root / "pages" / name / "evidence" / f"{name}.json"
        extraction.parent.mkdir(parents=True, exist_ok=True)
        extraction.write_text(json.dumps({
            "page": page, "status": "passed", "source_sha256": source_hash, "text": raw_text + "\n",
            "line_count": 1, "nonempty_line_ids": [f"p{page}-native-l0001"], "model_calls": [],
        }), encoding="utf-8")
        page_texts.append(text)
    merged = run_root / "document-temp.md"
    index = run_root / "index.md"
    for path in (merged, index):
        path.write_text("\n\n".join(page_texts) + "\n", encoding="utf-8")
    common = {
        "status": "passed",
        "source": str(source.resolve()),
        "source_sha256": source_hash,
        "selected_pages": selected_pages,
        "total_pages": total_pages,
    }
    (run_root / "summary.json").write_text(json.dumps({
        **common, "run_root": str(run_root), "batch_size": 1, "old_summary_reused": False,
        "model_calls": [], "visual_model": "not-run", "rendering": "not-run",
    }), encoding="utf-8")
    reports = run_root / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "heading-repair.json").write_text(json.dumps({
        "format_version": "0.6.0-pure-native-langgraph", "status": "passed",
        "source_sha256": batch.sha256(merged), "index_sha256": batch.sha256(index),
        "validation": {"heading_prefix_only": True, "page_markers_preserved": True, "visible_body_preserved": True},
    }), encoding="utf-8")
    coverage = {
        "format_version": "0.6.0-pure-native-langgraph", "status": "passed",
        "expected_page_sequence": selected_pages, "actual_page_sequence": selected_pages,
        "checks": {"page_unit_set_matches": True, "page_unit_markers_match": True, "merged_page_sequence_matches": True},
    }
    for name in ("merge-coverage.json", "final-coverage.json"):
        (reports / name).write_text(json.dumps(coverage), encoding="utf-8")
    (reports / "page-completion.json").write_text(json.dumps({
        "status": "passed", "expected_pages": selected_pages, "completed_pages": selected_pages,
        **{key: [] for key in batch.hybrid_validator.COVERAGE_EXCEPTIONS},
    }), encoding="utf-8")
    (reports / "page-cleanup.json").write_text(json.dumps({
        "status": "passed", "selected_pages": selected_pages,
        "checks": {"pre_cleanup_backup_exists": True, "all_page_units_nonempty": True,
                   "only_complete_lines_deleted": True, "body_rewrite": False},
    }), encoding="utf-8")
    (reports / "native-table-font-validation.json").write_text(json.dumps({
        "status": "passed", "detected_table_count": 0, "rendered_table_count": 0,
        "table_failures": [], "font_usage": {}, "font_size_usage": {},
    }), encoding="utf-8")
    (reports / "validation-result.json").write_text(
        json.dumps(
            {
                **common,
                "actual_full_pdf_coverage": selected_pages == list(range(1, total_pages + 1)),
                "checks": {"full_pdf_coverage_required_and_met": True},
                "native_text_physical_lines": len(selected_pages), "native_text_nonempty_line_ids": len(selected_pages),
                "table_count_pdfplumber_find_tables": 0, "rendered_table_count": 0,
                "font_usage": {}, "font_size_usage": {},
            }
        ),
        encoding="utf-8",
    )
    evidence = run_root / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "batch-command.json").write_text(json.dumps({
        "parsed_result": {"summary": {"run_root": str(run_root)}},
    }), encoding="utf-8")
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


def _write_native_replay_run(run_root: Path, **kwargs) -> None:
    # Export fixtures also use _write_complete_run and own their assets layout.
    _write_complete_run(run_root, **kwargs)
    (run_root / "assets").mkdir(exist_ok=True)


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
    reports = run_root / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "validation-result.json").write_text(json.dumps({
        **common,
        "validator": "pdf-hybrid-output-validator",
        "actual_full_pdf_coverage": selected_pages == list(range(1, total_pages + 1)),
        "index_sha256": batch.sha256(run_root / "index.md"),
        "checks": {"page_completion_passed": True, "index_full_page_sequence": True},
    }), encoding="utf-8")
    (run_root / "progress.json").write_text(
        json.dumps({**common, "stages": [{"stage": "independent_acceptance", "status": "passed"}]}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("reader_required", [False, True])
def test_final_completion_accepts_hybrid_contract(tmp_path: Path, reader_required):
    source = _write_pdf(tmp_path / "hybrid.pdf", b"hybrid")
    run_root = tmp_path / "hybrid-run"
    _write_complete_hybrid_run(run_root, source=source, selected_pages=[1, 2], total_pages=2)
    if reader_required:
        acceptance = run_root / "acceptance.json"
        acceptance.write_text(json.dumps({
            **_read_json(acceptance), "reader_visual_projection_required": True,
        }), encoding="utf-8")
        (run_root / "reports" / "reader-visual-validation.json").write_text(
            json.dumps({"status": "passed", "bytes_verified": True}), encoding="utf-8",
        )

    result = batch._inspect_final_completion(
        run_root,
        source_path=str(source.resolve()),
        source_sha256=batch.sha256(source),
        requested_pages="all",
    )

    assert result["accepted"] is True
    assert result["completion_kind"] == "validated-final"
    assert result["validation_report"] == str(run_root / "reports" / "validation-result.json")
    assert result["progress_path"] == str(run_root / "progress.json")


def test_native_resume_uses_native_validator_and_progress_over_compatibility_files(tmp_path: Path, monkeypatch):
    source = _write_pdf(tmp_path / "native.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")
    package = batch.run_root_for_entry(root, entries[0])
    _write_native_replay_run(package, source=source, selected_pages=[1], total_pages=1)
    native_progress = package / "evidence" / "progress-current.json"
    validator = package / "reports" / "validation-result.json"
    for path in (validator, native_progress, package / "summary.json"):
        path.write_text(json.dumps({
            **_read_json(path), "format_version": "0.6.0-pure-native-langgraph",
        }), encoding="utf-8")
    compat = {
        "format_version": "0.6.0-pure-native-run-root-acceptance",
        "status": "passed", "source": str(source), "source_sha256": batch.sha256(source),
        "selected_pages": [1], "total_pages": 1,
    }
    (package / "acceptance.json").write_text(json.dumps(compat), encoding="utf-8")
    (package / "progress.json").write_text(json.dumps({
        **compat, "stages": [{"stage": "independent_acceptance", "status": "passed"}],
    }), encoding="utf-8")
    (root / "results.json").write_text(json.dumps({"items": [{
        "source_path": str(source), "run_root": str(package), "source_sha256": batch.sha256(source),
        "status": "failed", "error": "incomplete-final-evidence: smoke-only at independent_acceptance",
        "returncode": 0,
    }]}), encoding="utf-8")

    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: pytest.fail("the package should be reused"))
    code, payload = batch.run_batch(
        entries, batch_root=root, origin={}, config={**BATCH_CONFIG, "min_free_bytes": 1},
        executor=lambda *_args, **_kwargs: pytest.fail("the package should be reused"),
    )
    assert code == 0
    assert payload["summary"]["skipped_existing"] == payload["strict_accepted_count"] == 1
    row, = _read_json(root / "results.json")["items"]
    assert row["progress_path"] == str(native_progress)
    assert row["validation_report"] == str(validator)
    assert row["status"] == "passed"


@pytest.mark.parametrize("damage", [
    "deleted-unit", "empty-unit", "unexpected-unit", "wrong-unit-marker", "changed-unit-body", "changed-index-body",
    "missing-heading-report", "missing-coverage", "failed-coverage",
    "missing-raw-merge", "changed-raw-merge", "missing-backups", "missing-backup-page", "missing-extraction",
    "wrong-extraction-hash", "missing-merge-coverage", "missing-cleanup-report", "missing-table-report",
    "missing-page-completion", "missing-assets-dir",
])
def test_native_resume_rejects_changed_current_evidence_despite_saved_pass(tmp_path: Path, monkeypatch, damage):
    source = _write_pdf(tmp_path / "native.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")
    package = batch.run_root_for_entry(root, entries[0])
    _write_native_replay_run(package, source=source, selected_pages=[1, 2], total_pages=2)
    unit = package / "page-units" / "p001.md"
    index = package / "index.md"
    reports = package / "reports"
    if damage == "deleted-unit":
        unit.unlink()
    elif damage == "empty-unit":
        unit.write_text(" \n", encoding="utf-8")
    elif damage == "unexpected-unit":
        unit.with_name("p003.md").write_text("<!-- page: 3 -->\nExtra text.\n", encoding="utf-8")
    elif damage == "wrong-unit-marker":
        unit.write_text(unit.read_text(encoding="utf-8").replace("<!-- page: 1 -->", "<!-- page: 2 -->"), encoding="utf-8")
    elif damage in {"changed-unit-body", "changed-index-body"}:
        path = unit if damage == "changed-unit-body" else index
        path.write_text(path.read_text(encoding="utf-8").replace("第 1 页正文内容。", "Changed body text."), encoding="utf-8")
    elif damage == "missing-heading-report":
        (reports / "heading-repair.json").unlink()
    elif damage == "missing-coverage":
        (reports / "final-coverage.json").unlink()
    elif damage == "failed-coverage":
        coverage = reports / "final-coverage.json"
        coverage.write_text(json.dumps({**_read_json(coverage), "status": "failed"}), encoding="utf-8")
    elif damage == "changed-raw-merge":
        merge = package / "document-temp.md"
        merge.write_text(merge.read_text(encoding="utf-8") + "Edited.\n", encoding="utf-8")
    elif damage == "wrong-extraction-hash":
        evidence = package / "pages/page-001/evidence/page-001.json"
        evidence.write_text(json.dumps({**_read_json(evidence), "source_sha256": "0" * 64}), encoding="utf-8")
    else:
        removed = package / {
            "missing-raw-merge": "document-temp.md",
            "missing-backups": "backups/page-units-pre-cleanup",
            "missing-backup-page": "backups/page-units-pre-cleanup/p001.md",
            "missing-extraction": "pages/page-001/evidence/page-001.json",
            "missing-merge-coverage": "reports/merge-coverage.json",
            "missing-cleanup-report": "reports/page-cleanup.json",
            "missing-table-report": "reports/native-table-font-validation.json",
            "missing-page-completion": "reports/page-completion.json",
            "missing-assets-dir": "assets",
        }[damage]
        shutil.rmtree(removed) if removed.is_dir() else removed.unlink()
    row = {
        "source_path": str(source), "run_root": str(package), "source_sha256": batch.sha256(source), "status": "passed",
    }
    (root / "results.json").write_text(json.dumps({"items": [row]}), encoding="utf-8")
    canonical = reports / "validation-result.json"
    saved_pass = canonical.read_bytes()
    assert _read_json(canonical)["status"] == "passed"
    assert "<!-- page: 1 -->" in index.read_text(encoding="utf-8")
    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))

    code, payload = batch.run_batch(
        entries, batch_root=root, origin={}, config={**BATCH_CONFIG, "min_free_bytes": 1},
        executor=lambda *_args, **_kwargs: pytest.fail("disk guard must block retry after evidence rejection"),
    )
    assert code == 75
    assert payload["summary"]["skipped_existing"] == payload["strict_accepted_count"] == 0
    assert _read_json(root / "results.json")["items"][0]["status"] == "pending"
    assert canonical.read_bytes() == saved_pass


@pytest.mark.parametrize("damage", [
    "page-units/p001.md", "document-temp.md", "backups/page-units-pre-cleanup/p001.md",
    "pages/page-001/evidence/page-001.json", "changed-index-body",
])
def test_native_batch_rechecks_evidence_after_graph_resume_returns_zero(tmp_path: Path, monkeypatch, damage):
    source = _write_pdf(tmp_path / "native.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")
    package = batch.run_root_for_entry(root, entries[0])
    _write_native_replay_run(package, source=source, selected_pages=[1], total_pages=1)
    if damage == "changed-index-body":
        index = package / "index.md"
        index.write_text(index.read_text(encoding="utf-8") + "Changed body text.\n", encoding="utf-8")
    else:
        (package / damage).unlink()
    commands = []

    def stale_graph(command, **kwargs):
        assert "--resume" in command and "--no-model" in command
        commands.append(command)
        kwargs["stdout"].write('{"final_status":"passed"}\n')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(batch.subprocess, "run", stale_graph)
    monkeypatch.setattr(batch, "_active_conversion_processes", lambda *_: [])
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=BATCH_CONFIG)
    assert len(commands) == 1
    assert code == 1 and payload["status"] == "failed"
    assert payload["strict_accepted_count"] == payload["summary"]["skipped_existing"] == 0
    row, = _read_json(root / "results.json")["items"]
    assert row["status"] == "failed" and row["returncode"] == 0 and row["resume_used"] is True


@pytest.mark.parametrize("legacy_root,recorded", [(".stages/native-pages/inner", True), ("batch-run", False)])
def test_native_replay_supports_legacy_extraction_and_nested_page_package(tmp_path: Path, legacy_root, recorded):
    source = _write_pdf(tmp_path / "native.pdf")
    package = tmp_path / "package"
    _write_native_replay_run(package, source=source, selected_pages=[1], total_pages=1)
    extraction = package / legacy_root
    (extraction / "reports").mkdir(parents=True)
    (package / "pages").rename(extraction / "pages")
    (package / "reports/page-completion.json").rename(extraction / "reports/page-completion.json")
    shutil.copyfile(package / "summary.json", extraction / "summary.json")
    page_package = extraction / "pages/page-001"
    (page_package / page_package.name).mkdir()
    (page_package / "evidence").rename(page_package / page_package.name / "evidence")
    command = package / "evidence/batch-command.json"
    if recorded:
        command.write_text(json.dumps({
            "parsed_result": {"summary": {"run_root": str(extraction)}},
        }), encoding="utf-8")
    else:
        command.unlink()
    relocated = package.with_name("relocated")
    package.rename(relocated)
    assert batch._reusable_completion({
        "run_root": str(relocated), "source_path": str(source), "source_sha256": batch.sha256(source),
    }, requested_pages="all") is not None


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


def test_cli_inherits_collection_lock_without_releasing_parent_ownership(tmp_path: Path):
    source = _write_pdf(tmp_path / "book.pdf")
    root = tmp_path / "batch"
    lock_path = tmp_path / ".pdf-collection-queue.lock"
    with lock_path.open("a+") as parent, lock_path.open("a+") as competitor:
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = subprocess.run([
            sys.executable, batch.__file__, "--source", str(source), "--batch-root", str(root),
            "--min-free-bytes", str(1 << 80), "--collection-lock-fd", str(parent.fileno()),
        ], pass_fds=(parent.fileno(),), capture_output=True, text=True, timeout=15)
        assert completed.returncode == 75, completed.stderr
        assert json.loads(completed.stdout)["status"] == "paused"
        assert _read_json(lock_path)["batch_root"] == str(root)
        with pytest.raises(BlockingIOError):
            fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with lock_path.open("a+") as successor:
        fcntl.flock(successor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_inherited_lock_rejects_invalid_wrong_file_and_separately_opened_fds(tmp_path: Path):
    lock_path = tmp_path / ".pdf-collection-queue.lock"
    other_path = tmp_path / "other"
    options = {"lock_root": tmp_path, "batch_root": tmp_path / "batch", "entries": []}
    with lock_path.open("a+") as parent, lock_path.open("a+") as competitor, other_path.open("a+") as other:
        parent.write("parent owner\n")
        parent.flush()
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for fd in (-1, other.fileno()):
            with pytest.raises(batch.BatchLockError, match="invalid inherited collection lock fd"):
                with batch.acquire_collection_lock(**options, inherited_fd=fd):
                    pytest.fail("invalid fd acquired the lock")
        with pytest.raises(batch.BatchLockError, match="active PDF collection queue"):
            with batch.acquire_collection_lock(**options, inherited_fd=competitor.fileno()):
                pytest.fail("a competing open file description acquired the lock")

        (tmp_path / ".pdf-collection-paused.json").write_text("{}", encoding="utf-8")
        with pytest.raises(batch.BatchLockError, match="PDF collection is paused"):
            with batch.acquire_collection_lock(**options, inherited_fd=parent.fileno()):
                pytest.fail("inherited fd bypassed the manual pause")
        assert lock_path.read_text(encoding="utf-8") == "parent owner\n"
        with pytest.raises(BlockingIOError):
            fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert "--collection-lock-fd" not in batch.build_parser().format_help()


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


@pytest.mark.parametrize("blocked_index,prior_status,probe_error", [
    (0, "failed", False), (2, "passed", False), (2, "running", False), (2, "failed", True),
])
def test_resume_checks_prior_residuals_before_any_book(tmp_path: Path, monkeypatch, blocked_index, prior_status, probe_error):
    sources = [_write_pdf(tmp_path / f"book-{i}.pdf") for i in range(3)]
    entries = batch.entries_from_paths(sources, source_root=tmp_path, route="native-only")
    root = tmp_path / "batch"
    blocked_source = sources[blocked_index]
    # A registered non-default run root must be probed even if already passed
    # or scheduled after the book we would otherwise start next.
    package = root / "registered"
    _write_native_replay_run(package, source=blocked_source, selected_pages=[1], total_pages=1)
    worker = f"999 lan_pdf_markdown_graph.py --run-root {package}"
    previous = {
        "source_path": str(blocked_source), "run_root": str(package), "source_sha256": batch.sha256(blocked_source),
        "status": prior_status, "stdout_log": "prior.log",
    }
    if prior_status == "failed":
        previous.update(queue_halted=True, halt_reason="residual-conversion-processes", residual_processes=[worker])
    elif prior_status == "passed":
        previous["residual_processes"] = [worker]
    (root / "results.json").write_text(json.dumps({"items": [previous]}), encoding="utf-8")
    cleared = False
    events = []

    def probe(entry, run_root):
        events.append(("probe", entry.source, run_root))
        if cleared:
            return []
        if probe_error:
            raise subprocess.TimeoutExpired("ps", 10)
        return [worker]

    def executor(entry, *, run_root, **kwargs):
        events.append(("invoke", entry.source, run_root))
        _write_native_replay_run(run_root, source=entry.source, selected_pages=[1], total_pages=1)
        return {"status": "passed", "execution": "completed"}

    monkeypatch.setattr(batch, "_active_conversion_processes", probe)
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=BATCH_CONFIG, executor=executor)
    assert code == 1
    assert events == [("probe", blocked_source, package)]
    assert payload["halt_reason"] == "residual-conversion-processes"
    assert payload["strict_accepted_count"] == 0
    snapshot = _read_json(root / "progress-current.json")
    assert snapshot["active_source"] is None and snapshot["summary"]["halted"] is True
    row, = _read_json(root / "results.json")["items"]
    assert row["queue_halted"] is True and row["residual_processes"] == [worker]
    assert row["stdout_log"] == "prior.log"
    if probe_error:
        assert "TimeoutExpired" in row["residual_check_error"]

    cleared = True
    events.clear()
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=BATCH_CONFIG, executor=executor)
    assert code == 0
    assert events[0] == ("probe", blocked_source, package)
    assert [source for action, source, _ in events if action == "invoke"] == [s for s in sources if s != blocked_source]
    assert payload["summary"]["skipped_existing"] == 1
    assert payload["strict_accepted_count"] == 3
    row = _read_json(root / "results.json")["items"][blocked_index]
    assert row["execution"] == "skipped-existing" and not row.get("queue_halted")
    assert row["residual_cleared_at"] and row["residual_processes"] == [worker]


@pytest.mark.parametrize("layout", [batch.FLAT_LAYOUT, batch.LEGACY_LAYOUT])
def test_run_batch_resumes_and_skips_existing_success(tmp_path: Path, layout: str):
    source_root = tmp_path / "books"
    first = _write_pdf(source_root / "A" / "first.pdf", b"first")
    second = _write_pdf(source_root / "B" / "second.pdf", b"second")
    entries = batch.entries_from_paths([first, second], source_root=source_root, route="native-only")
    batch_root = tmp_path / "batch-root"
    if layout == batch.LEGACY_LAYOUT:
        batch_root.mkdir()
        # A manifest without package_layout predates the new layout.
        (batch_root / "manifest.json").write_text(
            json.dumps({"batch_id": "legacy", "inputs": [batch._serialise_entry(e) for e in entries]}),
            encoding="utf-8",
        )
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
        _write_native_replay_run(
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
    assert (batch_root / "runs").exists() == (layout == batch.LEGACY_LAYOUT)
    manifest = json.loads((batch_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_layout"] == layout

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
    assert [Path(row["run_root"]) for row in rows] == [
        batch.run_root_for_entry(batch_root, entry, layout=layout) for entry in entries
    ]


def test_disk_pause_uses_headroom_only_before_new_books(tmp_path: Path, monkeypatch):
    sources = [_write_pdf(tmp_path / f"book-{i}.pdf") for i in range(3)]
    entries = batch.entries_from_paths(sources, source_root=tmp_path, route="native-only")
    root = tmp_path / "batch"
    progress_path = root / "progress-current.json"
    free = 100
    disk_checks = []
    calls = []

    def disk_usage(destination):
        disk_checks.append((destination, free))
        return SimpleNamespace(free=free)

    def executor(entry, *, run_root, **kwargs):
        nonlocal free
        progress = _read_json(progress_path)
        assert progress["status"] == "running"
        assert progress["active_source"] == str(entry.source)
        assert progress["summary"]["running"] == 1
        assert progress["strict_accepted_count"] == len(calls)
        calls.append(entry.source)
        if entry.source == sources[0]:
            free = 99  # The active book still gets to finish.
        _write_native_replay_run(run_root, source=entry.source, selected_pages=[1], total_pages=1)
        return {"status": "passed", "execution": "completed"}

    monkeypatch.setattr(batch.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(batch, "_active_conversion_processes", lambda *_: [])
    config = {**BATCH_CONFIG, "min_free_bytes": 100}
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=config, executor=executor)

    assert code == 75
    assert calls == sources[:1]
    assert disk_checks == [(root, 100), (root, 99)]
    assert payload["status"] == "paused"
    assert payload["reason"] == payload["halt_reason"] == "insufficient-disk-headroom"
    progress = _read_json(progress_path)
    assert progress == {
        "status": "paused",
        "active_source": None,
        "summary": payload["summary"],
        "updated_at": _read_json(root / "manifest.json")["updated_at"],
        "pages": "all",
        "strict_accepted_count": 1,
        "halt_reason": "insufficient-disk-headroom",
    }
    assert progress["summary"]["completed_total"] == 1
    assert progress["summary"]["pending_total"] == 2
    assert progress["summary"]["planned_total"] == 3
    assert len(_read_json(root / "manifest.json")["run_roots"]) == 3
    assert [item["status"] for item in _read_json(root / "results.json")["items"]] == ["passed"]
    assert not batch.run_root_for_entry(root, entries[1]).exists()

    free = 100
    disk_checks.clear()
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=config, executor=executor)
    assert code == 0
    assert calls == sources
    assert disk_checks == [(root, 100), (root, 100)]
    assert payload["summary"]["skipped_existing"] == 1
    assert _read_json(progress_path)["strict_accepted_count"] == 3
    assert _read_json(progress_path)["active_source"] is None
    assert _read_json(progress_path)["halt_reason"] is None

    free = 0
    disk_checks.clear()
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=config, executor=executor)
    assert code == 0
    assert calls == sources
    assert disk_checks == []
    assert payload["summary"]["skipped_existing"] == 3


def test_interrupted_resume_keeps_later_rows_and_recovers_registered_packages(tmp_path: Path, monkeypatch):
    sources = [_write_pdf(tmp_path / f"book-{i}.pdf", str(i).encode()) for i in range(6)]
    entries = batch.entries_from_paths(sources, source_root=tmp_path, route="hybrid")
    root = tmp_path / "batch"
    root.mkdir()
    roots = {
        str(source): {"path": str(root / f"registered-{i}"), "source_sha256": batch.sha256(source)}
        for i, source in enumerate(sources)
    }
    packages = [Path(roots[str(source)]["path"]) for source in sources]
    (root / "manifest.json").write_text(json.dumps({
        "package_layout": batch.FLAT_LAYOUT,
        "inputs": [batch._serialise_entry(entry) for entry in entries],
        "run_roots": roots,
    }), encoding="utf-8")
    (root / "results.json").write_text(json.dumps({"items": [
        {"source_path": str(sources[i]), "run_root": str(packages[i]),
         "source_sha256": roots[str(sources[i])]["source_sha256"],
         "status": status, "stdout_log": f"prior-log-{i}"}
        for i, status in enumerate(["running", "passed", "running", "needs-review"])
    ]}), encoding="utf-8")
    for i in (1, 2, 4):  # Item 2 never recorded passed; item 4 has no row at all.
        _write_complete_hybrid_run(packages[i], source=sources[i], selected_pages=[1], total_pages=1)
    packages[3].mkdir()
    (packages[3] / "index.md").write_text("partial", encoding="utf-8")
    monkeypatch.setattr(batch, "_active_conversion_processes", lambda *_: [])
    # This queue test supplies the separate validator's verdict; the page-unit
    # replay itself is exercised with engineering evidence in another fixture.
    monkeypatch.setattr(batch.hybrid_validator, "validate_package", lambda package, _: _read_json(
        package / "reports" / "validation-result.json"
    ))

    def interrupt(entry, *, run_root, **kwargs):
        assert entry.source == sources[0]
        run_root.mkdir()
        (run_root / "index.md").write_text("interrupted", encoding="utf-8")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(entries, batch_root=root, origin={}, config=BATCH_CONFIG, executor=interrupt)

    snapshot = _read_json(root / "progress-current.json")
    assert snapshot["active_source"] == str(sources[0])
    assert snapshot["summary"]["running"] == 1
    assert snapshot["strict_accepted_count"] == 3
    items = _read_json(root / "results.json")["items"]
    assert [item["status"] for item in items] == ["running", "passed", "passed", "needs-review", "passed"]
    assert [item["stdout_log"] for item in items[1:4]] == ["prior-log-1", "prior-log-2", "prior-log-3"]
    assert _read_json(root / "manifest.json")["run_roots"] == roots

    calls = []

    def executor(entry, *, run_root, resume, **kwargs):
        calls.append((entry.source, run_root, resume))
        _write_complete_hybrid_run(run_root, source=entry.source, selected_pages=[1], total_pages=1)
        return {"status": "passed", "execution": "completed"}

    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=BATCH_CONFIG, executor=executor)
    assert code == 0
    assert calls == [(sources[i], packages[i], i != 5) for i in (0, 3, 5)]
    assert payload["summary"]["skipped_existing"] == 3
    assert payload["strict_accepted_count"] == 6
    assert [item["run_root"] for item in _read_json(root / "results.json")["items"]] == list(map(str, packages))


def test_cli_disk_pause_before_first_book_and_rejects_negative_threshold(tmp_path: Path, monkeypatch, capsys):
    source = _write_pdf(tmp_path / "book.pdf")
    root = tmp_path / "batch"
    argv = ["--source", str(source), "--batch-root", str(root)]
    assert batch.build_parser().parse_args(argv).min_free_bytes == 0
    with pytest.raises(SystemExit) as invalid:
        batch.main([*argv, "--min-free-bytes", "-1"])
    assert invalid.value.code == 2
    assert not root.exists()
    capsys.readouterr()

    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    assert batch.main([*argv, "--min-free-bytes", "1"]) == 75
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "paused"
    assert payload["strict_accepted_count"] == 0
    assert payload["summary"]["pending_total"] == 1
    assert _read_json(root / "results.json")["items"] == []
    assert _read_json(root / "progress-current.json")["active_source"] is None


def test_manifest_only_resume_pins_selector_and_does_not_count_partial_package(tmp_path: Path, monkeypatch, capsys):
    source = _write_pdf(tmp_path / "book.pdf")
    root = tmp_path / "batch"
    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    resume_argv = ["--batch-root", str(root), "--min-free-bytes", "1"]
    assert batch.main(["--source", str(source), *resume_argv, "--pages", "2"]) == 75
    capsys.readouterr()
    package = Path(_read_json(root / "manifest.json")["run_roots"][str(source)]["path"])
    _write_native_replay_run(package, source=source, selected_pages=[2], total_pages=3)
    assert batch.main(resume_argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["skipped_existing"] == 1
    assert payload["strict_accepted_count"] == 0
    assert _read_json(root / "progress-current.json")["pages"] == "2"

    paths = [root / name for name in ("manifest.json", "results.json", "progress-current.json")]
    original = [path.read_bytes() for path in paths]
    assert batch.main([*resume_argv, "--pages", "1"]) == 2
    assert "existing batch page selector is '2', not '1'" in capsys.readouterr().err
    assert [path.read_bytes() for path in paths] == original


@pytest.mark.parametrize("mismatch", ["sha256", "validator-status", "selected-pages"])
def test_resume_does_not_reuse_stale_passed_row_with_mismatched_package(tmp_path: Path, monkeypatch, mismatch):
    source = _write_pdf(tmp_path / "book.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")
    package = batch.run_root_for_entry(root, entries[0])
    selected = [1] if mismatch == "selected-pages" else [2]
    _write_native_replay_run(package, source=source, selected_pages=selected, total_pages=3)
    report_path = package / "reports" / "validation-result.json"
    report = _read_json(report_path)
    if mismatch == "sha256":
        report["source_sha256"] = "0" * 64
    elif mismatch == "validator-status":
        report["status"] = "needs-review"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    (root / "results.json").write_text(json.dumps({"items": [{
        "source_path": str(source), "run_root": str(package), "source_sha256": batch.sha256(source),
        "status": "passed", "accepted": True, "full_pdf_coverage": True,
    }]}), encoding="utf-8")

    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    code, payload = batch.run_batch(
        entries, batch_root=root, origin={}, config={**BATCH_CONFIG, "pages": "2", "min_free_bytes": 1},
        executor=lambda *_args, **_kwargs: pytest.fail("low space must prevent launching a retry"),
    )
    assert code == 75
    assert payload["strict_accepted_count"] == 0
    assert payload["summary"]["skipped_existing"] == 0
    assert payload["summary"]["pending_total"] == 1
    assert _read_json(root / "results.json")["items"][0]["status"] == "pending"


def test_passing_executor_result_without_package_evidence_is_not_counted(tmp_path: Path, monkeypatch):
    source = _write_pdf(tmp_path / "book.pdf")
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="native-only")
    root = tmp_path / "batch"
    monkeypatch.setattr(batch, "_active_conversion_processes", lambda *_: [])
    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: pytest.fail("default must disable disk checks"))
    code, payload = batch.run_batch(
        entries, batch_root=root, origin={}, config=BATCH_CONFIG,
        executor=lambda *_args, **_kwargs: {"status": "passed", "accepted": True, "full_pdf_coverage": True},
    )
    assert code == 1
    assert payload["strict_accepted_count"] == 0
    assert payload["summary"]["failed"] == 1


@pytest.mark.parametrize("problem", ["missing", "corrupt", "failed", "needs-review", "index-sha256", "reader"])
def test_hybrid_resume_never_recovers_from_internal_acceptance_alone(tmp_path: Path, monkeypatch, problem):
    source = _write_pdf(tmp_path / "hybrid.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    package = root / "registered-hybrid"
    _write_complete_hybrid_run(package, source=source, selected_pages=[1], total_pages=1)
    report_path = package / "reports" / "validation-result.json"
    if problem == "missing":
        report_path.unlink()
    elif problem == "corrupt":
        report_path.write_bytes(b"\xff not json")
    elif problem == "reader":
        acceptance_path = package / "acceptance.json"
        acceptance_path.write_text(json.dumps({
            **_read_json(acceptance_path), "reader_visual_projection_required": True,
        }), encoding="utf-8")
    elif problem == "index-sha256":
        index_path = package / "index.md"
        index_path.write_text(index_path.read_text(encoding="utf-8") + "Changed text\n", encoding="utf-8")
    else:
        report_path.write_text(json.dumps({**_read_json(report_path), "status": problem}), encoding="utf-8")
    (root / "results.json").write_text(json.dumps({"items": [{
        "source_path": str(source), "run_root": str(package), "source_sha256": batch.sha256(source),
        "status": "running",
    }]}), encoding="utf-8")

    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    code, payload = batch.run_batch(
        entries, batch_root=root, origin={}, config={**BATCH_CONFIG, "min_free_bytes": 1},
        executor=lambda *_args, **_kwargs: pytest.fail("low space must prevent launching a retry"),
    )
    assert code == 75
    assert payload["strict_accepted_count"] == 0
    assert payload["summary"]["running"] == payload["summary"]["skipped_existing"] == 0
    assert _read_json(root / "results.json")["items"][0]["status"] == "interrupted"
    assert _read_json(root / "progress-current.json")["active_source"] is None


@pytest.mark.parametrize("missing_unit", [False, True])
def test_hybrid_reuse_replays_page_evidence_even_with_saved_pass(tmp_path: Path, monkeypatch, missing_unit):
    source = _write_pdf(tmp_path / "book.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route="hybrid")
    package = batch.run_root_for_entry(root, entries[0])
    _write_complete_hybrid_run(package, source=source, selected_pages=[1], total_pages=1)
    reports = package / "reports"
    unit = package / "page-units" / "p001.md"
    unit.parent.mkdir()
    unit.write_text("Page 1 text.\n", encoding="utf-8")
    route = package / "evidence" / "page-routes" / "p001.json"
    route.parent.mkdir(parents=True)
    route.write_text(json.dumps({
        "page": 1, "status": "passed", "source_sha256": batch.sha256(source),
        "page_unit_sha256": batch.sha256(unit),
    }), encoding="utf-8")
    (reports / "page-completion.json").write_text(json.dumps({
        "status": "passed", "expected_pages": [1], "completed_pages": [1],
        **{key: [] for key in batch.hybrid_validator.COVERAGE_EXCEPTIONS},
    }), encoding="utf-8")
    (reports / "heading-repair.json").write_text(json.dumps({
        "status": "passed", "index_sha256": batch.sha256(package / "index.md"),
        "validation": {"heading_prefix_only": True}, "formatting_validation": {"passed": True},
    }), encoding="utf-8")
    probes = []

    def pdfinfo(command, **kwargs):
        assert command == ["pdfinfo", str(source)]
        probes.append(command)
        return SimpleNamespace(stdout="Pages: 1\n")

    monkeypatch.setattr(batch.hybrid_validator.subprocess, "run", pdfinfo)
    canonical = reports / "validation-result.json"
    verified = batch.hybrid_validator.validate_package(package, source)
    assert verified["status"] == "passed"
    canonical.write_text(json.dumps(verified), encoding="utf-8")
    saved_pass = canonical.read_bytes()
    probes.clear()
    if missing_unit:
        unit.unlink()
    (root / "results.json").write_text(json.dumps({"items": [{
        "source_path": str(source), "run_root": str(package), "source_sha256": batch.sha256(source),
        "status": "passed",
    }]}), encoding="utf-8")
    monkeypatch.setattr(batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))

    code, payload = batch.run_batch(
        entries, batch_root=root, origin={}, config={**BATCH_CONFIG, "min_free_bytes": 1},
        executor=lambda *_args, **_kwargs: pytest.fail("only replay or pause was expected"),
    )
    assert len(probes) == 1
    assert canonical.read_bytes() == saved_pass
    assert code == (75 if missing_unit else 0)
    assert payload["summary"]["skipped_existing"] == (0 if missing_unit else 1)
    assert payload["strict_accepted_count"] == (0 if missing_unit else 1)


@pytest.mark.parametrize("route", ["native-only", "hybrid"])
def test_default_executor_inspects_new_package_once(tmp_path: Path, monkeypatch, route):
    source = _write_pdf(tmp_path / "book.pdf")
    root = tmp_path / "batch"
    entries = batch.entries_from_paths([source], source_root=tmp_path, route=route)
    run_root = batch.run_root_for_entry(root, entries[0])
    inspections = []
    inspect = batch._inspect_final_completion

    def inspect_once(path, **kwargs):
        inspections.append((path, kwargs["source_sha256"]))
        return inspect(path, **kwargs)

    def fake_run(_command, **_kwargs):
        fixture_writer = _write_complete_hybrid_run if route == "hybrid" else _write_native_replay_run
        fixture_writer(run_root, source=source, selected_pages=[1], total_pages=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    monkeypatch.setattr(batch, "_active_conversion_processes", lambda *_: [])
    monkeypatch.setattr(batch, "_inspect_final_completion", inspect_once)
    monkeypatch.setattr(batch.hybrid_validator, "validate_package", lambda *_: pytest.fail("fresh outputs must not replay"))
    code, payload = batch.run_batch(entries, batch_root=root, origin={}, config=BATCH_CONFIG)
    assert code == 0
    assert payload["strict_accepted_count"] == 1
    assert inspections == [(run_root, batch.sha256(source))]


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

    assert batch._reusable_completion(item, requested_pages="all") is None


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

    assert batch._reusable_completion(item, requested_pages="all") is None


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

    assert batch._reusable_completion(item, requested_pages="all") is None


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
    markdown_files = [run_root / "index.md", run_root / "document-temp.md"] + [
        run_root / directory / f"p{page:03d}.md"
        for directory in ("page-units", "backups/page-units-pre-cleanup") for page in (1, 2)
    ]
    assert result["markdown_file_count"] == len(markdown_files)
    assert result["markdown_total_bytes"] == sum(path.stat().st_size for path in markdown_files)


def test_book_working_copy_neither_changes_nor_substitutes_for_acceptance(tmp_path: Path):
    source = _write_pdf(tmp_path / "《书名》.pdf")
    run_root = tmp_path / "run"
    _write_complete_run(run_root, source=source, selected_pages=[1], total_pages=1)
    index = run_root / "index.md"
    original = index.read_bytes()
    copy = run_root / "书名.md"
    copy.write_text("手工排版的工作稿", encoding="utf-8")

    def accepted():
        return batch._inspect_final_completion(
            run_root, source_path=str(source), source_sha256=batch.sha256(source), requested_pages="all",
        )["accepted"]

    assert accepted() is True
    copy.write_bytes(original)
    index.write_text("失去页标记的正式稿", encoding="utf-8")
    assert accepted() is False


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
    assets.mkdir(exist_ok=True)
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
        _write_native_replay_run(
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
        _write_native_replay_run(
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
