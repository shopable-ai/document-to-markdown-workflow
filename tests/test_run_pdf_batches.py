from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

from adapters import run_pdf_batches as batch


class _ProcResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


@pytest.fixture
def interrupted_hybrid(tmp_path, monkeypatch):
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"synthetic PDF identity; no PDF tool reads it")
    source_hash = batch.sha256(source)
    log = tmp_path / "worker-calls"
    root = tmp_path / "book-package"
    root.mkdir()

    def pdfinfo(command, **_kwargs):
        assert command == ["pdfinfo", str(source)]
        return SimpleNamespace(stdout="Pages: 3\n", returncode=0, stderr="")

    def route(*, page, **_kwargs):
        return {"page": page, "route": "visual-only" if page == 2 else "native-only", "status": "passed",
                "reason_codes": [], "signals": {"page_image_required": page == 2}}

    def worker(command, _timeout):
        adapter = command[1]
        if adapter == str(batch.LAN_ADAPTER):
            page = int(command[command.index("--pages") + 1])
            with log.open("a") as handle:
                handle.write(f"native:{page}\n")
            _, summary_path, evidence_path = batch._page_package_paths(root / "pages", page)
            summary = {"status": "passed", "source_sha256": source_hash, "selected_pages": [page]}
            batch._write_json_atomic(summary_path, summary)
            batch._write_json_atomic(evidence_path, {
                "status": "passed", "page": page, "source_sha256": source_hash,
                "text": f"Native fixture page {page}.", "nonempty_line_ids": [f"native-{page}"],
                "table_count_pdfplumber_find_tables": 0,
            })
        elif adapter == str(batch.APPLE_OCR_ADAPTER):
            assert command[command.index("--pages") + 1] == "2"
            with log.open("a") as handle:
                handle.write("primary:2\n")
            visual = Path(command[command.index("--run-root") + 1])
            unit = visual / "page-units/p002.md"
            asset = visual / "assets/page-002.png"
            raw = visual / "evidence/apple-vision-p002.json"
            batch._write_json_atomic(visual / "summary.json", {"status": "running", "source": {"sha256": source_hash}})
            batch._write_text_atomic(unit, "<!-- page:2 -->\n\nOCR fixture page 2.\n")
            asset.parent.mkdir()
            asset.write_bytes(b"fixture render bytes")
            batch._write_json_atomic(raw, {"status": "complete", "candidates": [{"text": "OCR fixture page 2."}]})
            batch._write_json_atomic(visual / "evidence/page-result-p002.json", {
                "status": "passed", "page": 2, "source_sha256": source_hash, "route": batch.LOCAL_PAGE_OCR_ROUTE,
                "asset": {"sha256": batch.sha256(asset)}, "ocr_evidence_sha256": batch.sha256(raw),
                "markdown": {"current_sha256": batch.sha256(unit)},
            })
            # The worker committed its page; the book route and next page have not run.
            assert os.getpid() != parent_pid, "the resumed process must not invoke primary OCR again"
            os.kill(os.getpid(), signal.SIGKILL)
        elif adapter == str(batch.HEADING_REPAIR_ADAPTER):
            index = root / "index.md"
            index.write_bytes((root / "document-temp.md").read_bytes())
            summary = {"status": "passed", "index_sha256": batch.sha256(index),
                       "validation": {"heading_prefix_only": True}, "formatting_validation": {"passed": True}}
            batch._write_json_atomic(root / "reports/heading-repair.json", summary)
        else:
            assert adapter == str(batch.NOISE_CLEANUP_ADAPTER)
            summary = {"status": "passed"}
        return {"returncode": 0, "stdout": json.dumps(summary)}

    monkeypatch.setattr(batch.subprocess, "run", pdfinfo)
    monkeypatch.setattr(batch, "_collect_page_image_stats", lambda *_, **__: ({}, {}))
    monkeypatch.setattr(batch, "_page_route_decision", route)
    monkeypatch.setattr(batch, "_background_process_matches", lambda *_: [])
    monkeypatch.setattr(batch, "_run_command", worker)

    def run():
        return batch.run_page_batches.__wrapped__(source, tmp_path, resume_package=root, hybrid=True,
                                                  write_book_markdown=False)

    parent_pid = os.getpid()
    child = multiprocessing.get_context("fork").Process(target=run)
    child.start()
    try:
        child.join(10)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill()
            child.join(5)
        child.close()

    assert (root / "evidence/page-routes/p001.json").is_file()
    assert not (root / "evidence/page-routes/p002.json").exists()
    assert not (root / "pages/page-003").exists()
    return source, root, log, run


def test_sigkill_resumes_same_package_using_routed_page_and_primary_ocr(interrupted_hybrid, monkeypatch):
    source, root, log, run = interrupted_hybrid
    native_unit = root / "page-units/p001.md"
    visual = root / "pages/page-002/visual"
    raw = visual / "evidence/apple-vision-p002.json"
    raster = visual / "assets/page-002.png"
    stable_hashes = {path: batch.sha256(path) for path in (native_unit, raw, raster)}
    monkeypatch.setattr(batch, "_finalize_hybrid_reader", lambda *_a, **_k: {"status": "needs-review"})

    code, result = run()

    assert (code, result["status"]) == (1, "needs-review")  # Fixture does not adjudicate reader semantics.
    assert (result["run_id"], result["output"]) == (root.name, str(root))
    assert log.read_text().splitlines() == ["native:1", "native:2", "primary:2", "native:3"]
    assert {path: batch.sha256(path) for path in stable_hashes} == stable_hashes
    assert batch._read_json(root / "evidence/page-routes/p001.json")["resume_reused"] is True
    scan_route = batch._read_json(root / "evidence/page-routes/p002.json")
    assert scan_route["native_resume_reused"] is scan_route["visual_resume_reused"] is True
    assert scan_route["source_sha256"] == batch.sha256(source)
    assert batch.sha256(root / "assets/page-002.png") == stable_hashes[raster] == scan_route["visual_asset_sha256"]
    final = (root / "index.md").read_text()
    assert batch._page_marker_sequence(final) == [1, 2, 3]
    assert "Native fixture page 1." in final and "Native fixture page 3." in final
    assert "OCR fixture page 2." in final and "![第2页原始页面图](assets/page-002.png)" in final
    assert "Native fixture page 2." not in final
    assert batch._read_json(root / "reports/validation-result.json")["status"] == "passed"


def test_failed_retry_cannot_rehash_stale_passed_route(interrupted_hybrid, monkeypatch):
    _, root, _, run = interrupted_hybrid
    route_path = root / "evidence/page-routes/p001.json"
    old_route = route_path.read_bytes()
    (root / "page-units/p001.md").write_text("<!-- page:1 -->\n\nUnverified drift.\n")
    real_route = batch._page_route_decision

    def fail_first(*, page, **kwargs):
        if page == 1:
            raise RuntimeError("simulated page retry failure")
        return real_route(page=page, **kwargs)

    monkeypatch.setattr(batch, "_page_route_decision", fail_first)
    monkeypatch.setattr(batch, "_finalize_hybrid_reader", lambda *_a, **_k: pytest.fail("engineering must not pass"))

    code, result = run()

    assert code == 1 and result["status"] != "passed"
    assert batch._read_json(root / "reports/page-completion.json")["failed_pages"] == [1]
    assert batch._read_json(root / "reports/validation-result.json")["status"] == "failed"
    assert batch._read_json(route_path)["status"] == "failed"
    assert any(path.read_bytes() == old_route for path in route_path.parent.glob("_superseded/p001-*.json"))


@pytest.mark.parametrize(("engineering_passed", "reader_passed"), [(True, True), (True, False), (False, False)])
def test_hybrid_reader_starts_only_after_final_index_and_engineering_gate(tmp_path, monkeypatch, engineering_passed, reader_passed):
    source = tmp_path / "《书名》.pdf"
    source.write_bytes(b"fixture PDF")
    root = tmp_path / "run"
    root.mkdir()
    _, native_summary, native_evidence = batch._page_package_paths(root / "pages", 1)
    fingerprint = batch.sha256(source)
    batch._write_json_atomic(native_summary, {"status": "passed", "source_sha256": fingerprint})
    batch._write_json_atomic(native_evidence, {
        "status": "passed", "source_sha256": fingerprint, "text": "Fixture body", "nonempty_line_ids": ["1"],
    })

    monkeypatch.setattr(batch.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="Pages: 1\n", returncode=0, stderr="",
    ))
    monkeypatch.setattr(batch, "_collect_page_image_stats", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(batch, "_page_route_decision", lambda **kwargs: {"page": 1, "route": "native-only", "status": "passed"})
    monkeypatch.setattr(batch, "_background_process_matches", lambda *args: [])

    def helper(command, _timeout):
        if command[1] == str(batch.HEADING_REPAIR_ADAPTER):
            index = root / "index.md"
            index.write_bytes((root / "document-temp.md").read_bytes())
            batch._write_json_atomic(root / "reports/heading-repair.json", {
                "status": "passed", "index_sha256": batch.sha256(index) if engineering_passed else "mismatch",
                "validation": {"heading_prefix_only": True}, "formatting_validation": {"passed": True},
            })
        else:
            assert command[1] == str(batch.NOISE_CLEANUP_ADAPTER)
        return {"returncode": 0, "stdout": json.dumps({"status": "passed"})}

    entered = []

    def finalize(package, pdf, selected, *, previously_verified=False):
        entered.append(True)
        assert (package, pdf, selected) == (root, source, [1])
        assert batch._read_json(root / "reports/validation-result.json")["status"] == "passed"
        assert batch._read_json(root / "acceptance.json")["status"] == "passed"
        assert (root / "index.md").is_file()
        if not previously_verified:
            assert batch._read_json(root / "progress.json")["status"] == "running"
            assert not (root / "书名.md").exists()
        return {"status": "passed" if reader_passed else "failed", "bytes_verified": reader_passed}

    monkeypatch.setattr(batch, "_run_command", helper)
    monkeypatch.setattr(batch, "_finalize_hybrid_reader", finalize)
    code, result = batch.run_page_batches.__wrapped__(source, tmp_path, resume_package=root, hybrid=True)
    assert (code, result["status"]) == ((0, "passed") if reader_passed else (1, "failed"))
    assert entered == ([True] if engineering_passed else [])
    acceptance = batch._read_json(root / "acceptance.json")
    assert acceptance["status"] == ("passed" if engineering_passed else "failed")
    if engineering_passed:
        assert acceptance[batch.hybrid_validator.READER_AUDIT_VERSION] == "3.0.0"
        assert result["summary"]["engineering_status"] == "passed"
        assert result["summary"]["reader_visual"]["status"] == ("passed" if reader_passed else "failed")
    else:
        assert "reader_visual" not in result["summary"]
    copy = root / "书名.md"
    assert copy.is_file() is reader_passed
    if reader_passed:
        index = root / "index.md"
        original = index.read_bytes()
        assert copy.read_bytes() == original
        assert result["summary"]["source_named_markdown"] == str(copy)
        copy.write_text("手工排版", encoding="utf-8")

        code, resumed = batch.run_page_batches.__wrapped__(source, tmp_path, resume_package=root, hybrid=True)

        assert code == 0 and resumed["summary"]["source_named_markdown"] == str(copy)
        assert copy.read_text(encoding="utf-8") == "手工排版"
        assert index.read_bytes() == original


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


@pytest.mark.parametrize("probe_status", ["passed", "unavailable", "timeout"])
def test_full_page_background_with_unverified_text_uses_local_ocr(tmp_path, monkeypatch, probe_status):
    def render(_source, _page):
        if probe_status == "timeout":
            raise batch.page_router.DetectionError("fixture timeout")
        return {"status": probe_status, "visual_structure_present": False, "structure_types": []}

    monkeypatch.setattr(batch.page_router, "inspect_rendered_structure", render)
    decision = batch._page_route_decision(
        source=tmp_path / "book.pdf",
        page=57,
        native_evidence={
            "text": "Dense extractable text is not proof of visible text. " * 8,
            "table_count_pdfplumber_find_tables": 1,
        },
        images=batch.page_router.PageImageStats(image_count=1, max_area_ratio=0.9355),
        image_probe={"status": "passed"},
    )
    assert decision["native_body_sufficient"] is True
    assert decision["route"] == "visual-only"
    assert decision["visual_model_required"] is False
    assert decision["signals"]["page_image_required"] is True
    assert decision["signals"]["native_text_visibility"] == {
        "version": 1, "status": "unknown", "reason": "full-page-background-text-visibility-unverified",
    }
    assert "full-page-background-with-complete-text-layer" not in decision["reason_codes"]
    assert "page-image-and-local-ocr-required" in decision["reason_codes"]

    command, route = batch._visual_page_command(
        helper_python="/tmp/python",
        source=Path("/tmp/book.pdf"),
        visual_root=tmp_path / "visual",
        page=57,
        visual_dpi=220,
        timeout=7200,
        can_resume=False,
        decision=decision,
    )

    assert command[1] == str(batch.APPLE_OCR_ADAPTER)
    assert "convert" not in command
    assert route == "apple-vision-ocr"


def test_full_page_background_with_rendered_structure_keeps_visual_model(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.page_router, "inspect_rendered_structure", lambda *_: {
        "status": "passed", "visual_structure_present": True, "structure_types": ["table-or-matrix"],
    })
    decision = batch._page_route_decision(
        source=tmp_path / "book.pdf", page=1,
        native_evidence={"text": "Dense extractable text. " * 8, "table_count_pdfplumber_find_tables": 1},
        images=batch.page_router.PageImageStats(image_count=1, max_area_ratio=1),
        image_probe={"status": "passed"},
    )
    assert decision["route"] == "native-plus-visual"
    assert decision["visual_model_required"] is True
    assert "page_image_required" not in decision["signals"]
    assert decision["signals"]["native_text_visibility"]["status"] == "unknown"


@pytest.mark.parametrize("curves", [0, 1])
def test_vector_signal_corroborates_visual_even_without_raster_grid(tmp_path, monkeypatch, curves):
    renders = []

    def render(_source, page):
        renders.append(page)
        return {"status": "passed", "visual_structure_present": False, "structure_types": []}

    monkeypatch.setattr(batch.page_router, "inspect_rendered_structure", render)
    vectors = {"version": "pdfplumber-vector-v1", "status": "complete", "counts": {"line": 0, "rect": 0, "curve": curves}}
    decision = batch._page_route_decision(
        source=tmp_path / "book.pdf", page=1,
        native_evidence={"text": "Plain text body with normal reading order. " * 8,
                         "table_count_pdfplumber_find_tables": 0, "pdf_vector_objects": vectors},
        images=batch.page_router.PageImageStats(), image_probe={"status": "passed"},
    )

    assert decision["signals"]["pdf_vector_objects"] == vectors
    assert decision["signals"]["native_text_visibility"] == {
        "version": 1, "status": "unknown", "reason": "no-paint-visibility-detector",
    }
    assert "native_fast_path" not in decision["signals"]
    assert decision["visual_model_required"] is False
    if curves:
        assert renders == [1]
        assert decision["route"] == "native-plus-visual"
        assert "pdf-vector-objects-present" in decision["reason_codes"]
        assert decision["signals"]["page_image_required"] is True
    else:
        assert renders == []
        assert decision["route"] == "native-only"
        assert "page_image_required" not in decision["signals"]


@pytest.mark.parametrize("worker_fault", [None, "missing-link", "missing-image", "missing-ocr"])
def test_background_guard_reroutes_legacy_page_and_requires_worker_evidence(tmp_path, monkeypatch, worker_fault):
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"synthetic source; all tools stubbed")
    fingerprint = batch.sha256(source)
    root = tmp_path / "run"
    package, native_summary, native_evidence = batch._page_package_paths(root / "pages", 1)
    native_text = "Unverified extracted characters not in the page image. " * 8
    batch._write_json_atomic(native_summary, {"status": "passed", "source_sha256": fingerprint})
    batch._write_json_atomic(native_evidence, {
        "status": "passed", "page": 1, "source_sha256": fingerprint, "text": native_text,
        "table_count_pdfplumber_find_tables": 0,
    })
    native_sha = batch.sha256(native_evidence)
    unit = root / "page-units/p001.md"
    batch._write_text_atomic(unit, f"<!-- page:1 -->\n\n{native_text}\n")
    route_path = root / "evidence/page-routes/p001.json"
    batch._write_json_atomic(route_path, {
        "page": 1, "source_sha256": fingerprint, "route": "native-only", "status": "passed",
        "page_unit_sha256": batch.sha256(unit), "native_evidence": str(native_evidence),
        "native_evidence_sha256": native_sha,
        "reason_codes": ["native-text-usable", "full-page-background-with-complete-text-layer"],
    })
    old_attempt = package / "visual"
    old_unit = old_attempt / "page-units/p001.md"
    batch._write_text_atomic(old_unit, "<!-- page:1 -->\n\nOld model attempt.\n")
    batch._write_json_atomic(old_attempt / "summary.json", {"status": "passed", "source_sha256": fingerprint})
    batch._write_json_atomic(old_attempt / "evidence/page-result-p001.json", {
        "status": "passed", "page": 1, "source_sha256": fingerprint, "route": "lan-visual-model",
        "markdown": {"current_sha256": batch.sha256(old_unit)},
    })

    def pdf_info(command, **_kwargs):
        assert command == ["pdfinfo", str(source)]
        return SimpleNamespace(stdout="Pages: 1\n", returncode=0, stderr="")

    monkeypatch.setattr(batch.subprocess, "run", pdf_info)
    monkeypatch.setattr(batch, "_collect_page_image_stats", lambda *_, **__: (
        {1: batch.page_router.PageImageStats(image_count=1, max_area_ratio=1)}, {"status": "passed"},
    ))
    monkeypatch.setattr(batch.page_router, "inspect_rendered_structure", lambda *_: {
        "status": "passed", "visual_structure_present": False, "structure_types": [],
    })
    commands = []

    def worker(command, _timeout):
        commands.append(command[1])
        if command[1] == str(batch.NOISE_CLEANUP_ADAPTER):
            # Stop after exercising per-page routing and artifact handling.
            return {"returncode": 1, "stdout": '{"status":"needs-review"}'}
        assert command[1] == str(batch.APPLE_OCR_ADAPTER)
        assert "--resume" not in command
        visual = Path(command[command.index("--run-root") + 1])
        assert visual == package / "visual-attempt-002"
        worker_unit = visual / "page-units/p001.md"
        image_link = "![Page 1](../assets/page-001.png)\n"
        if worker_fault == "missing-link":
            image_link = "[Only a hyperlink](../assets/page-001.png)\n"
        batch._write_text_atomic(worker_unit, f"<!-- page:1 -->\n\nOCR body.\n\n{image_link}")
        worker_asset = visual / "assets/page-001.png"
        if worker_fault != "missing-image":
            worker_asset.parent.mkdir()
            worker_asset.write_bytes(b"synthetic image artifact")
        ocr_path = visual / "evidence/apple-vision-p001.json"
        if worker_fault != "missing-ocr":
            batch._write_json_atomic(ocr_path, {"candidates": [{"text": "OCR body.", "bbox": [0, 0, 1, 1]}]})
        batch._write_json_atomic(visual / "summary.json", {
            "status": "passed", "source": {"sha256": fingerprint}, "completed_pages": [1],
        })
        batch._write_json_atomic(visual / "evidence/page-result-p001.json", {
            "status": "passed", "page": 1, "source_sha256": fingerprint,
            "route": "apple-vision-ocr", "markdown": {"current_sha256": batch.sha256(worker_unit)},
            "asset": {"sha256": batch.sha256(worker_asset) if worker_asset.exists() else "missing"},
            "ocr_evidence_sha256": batch.sha256(ocr_path) if ocr_path.exists() else "missing",
        })
        return {"returncode": 0, "stdout": '{"status":"passed"}'}

    monkeypatch.setattr(batch, "_run_command", worker)
    batch.run_page_batches.__wrapped__(source, tmp_path, resume_package=root, hybrid=True)
    assert commands == [str(batch.APPLE_OCR_ADAPTER), str(batch.NOISE_CLEANUP_ADAPTER)]
    route = batch._read_json(route_path)
    assert route["signals"]["page_image_required"] is True
    assert route["native_resume_reused"] is True
    assert route["native_evidence_sha256"] == batch.sha256(native_evidence) == native_sha
    assert old_unit.read_text(encoding="utf-8") == "<!-- page:1 -->\n\nOld model attempt.\n"
    assert route["recognition_route"] == "apple-vision-ocr"
    assert route["model_called"] is False
    asset = root / "assets/page-001.png"
    if worker_fault in {"missing-image", "missing-ocr"}:
        assert route["status"] == "failed"
        assert "required-page-image-or-local-ocr-evidence-unverified" in route["reason_codes"]
        assert not asset.exists()
    else:
        assert route["status"] == "passed"
        body = unit.read_text(encoding="utf-8")
        assert "OCR body." in body and native_text not in body
        expected_alt = "第1页原始页面图" if worker_fault == "missing-link" else "Page 1"
        assert f"![{expected_alt}](../assets/page-001.png)" in body
        assert batch.sha256(asset) == route["visual_asset_sha256"]
        reuse = dict(page=1, source_sha256=fingerprint, page_unit_path=unit, route_record_path=route_path)
        assert batch._reusable_routed_page(**reuse)["resume_reused"] is True
        asset.unlink()
        assert batch._reusable_routed_page(**reuse) is None


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


@pytest.mark.parametrize("installed", [False, True])
def test_ollama_healthcheck_requires_the_pinned_model(monkeypatch, installed):
    from adapters.lan_pdf_vision_to_markdown import EXPECTED_MODEL

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            models = [{"name": "text-only:latest"}]
            if installed:
                models.append({"model": EXPECTED_MODEL})
            return json.dumps({"models": models}).encode()

    def open_tags(url, timeout):
        assert url.endswith("/api/tags") and timeout == 5
        return Response()

    monkeypatch.setattr(batch, "urlopen", open_tags)
    result = batch._ollama_healthcheck()
    assert result["model"] == EXPECTED_MODEL
    assert result["status"] == ("passed" if installed else "failed")
    assert result.get("reason") == (None if installed else "pinned-model-not-installed")


@pytest.mark.parametrize("model_available", [False, True])
@pytest.mark.parametrize("ocr_verified", [False, True])
def test_failed_lan_page_uses_verified_ocr_without_overwriting_attempts(
    tmp_path, monkeypatch, model_available, ocr_verified,
):
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"synthetic source; workers stubbed")
    fingerprint = batch.sha256(source)
    root = tmp_path / "run"
    package, native_summary, native_evidence = batch._page_package_paths(root / "pages", 1)
    batch._write_json_atomic(native_summary, {"status": "passed", "source_sha256": fingerprint})
    batch._write_json_atomic(native_evidence, {
        "status": "passed", "page": 1, "source_sha256": fingerprint, "text": "",
        "table_count_pdfplumber_find_tables": 0,
    })
    old_visual = package / "visual"
    old_result = old_visual / "evidence/page-result-p001.json"
    batch._write_json_atomic(old_visual / "summary.json", {"status": "failed", "source_sha256": fingerprint})
    batch._write_json_atomic(old_result, {"status": "failed", "error": "Ollama HTTP 404"})
    old_bytes = old_result.read_bytes()
    monkeypatch.setattr(batch.subprocess, "run", lambda *_, **__: SimpleNamespace(
        stdout="Pages: 1\n", returncode=0, stderr="",
    ))
    monkeypatch.setattr(batch, "_collect_page_image_stats", lambda *_, **__: ({}, {"status": "passed"}))
    monkeypatch.setattr(batch, "_page_route_decision", lambda **_: {
        "page": 1, "route": "visual-only", "status": "passed", "reason_codes": ["rendered-visual-structure-present"],
        "signals": {}, "rendered_structure": {"visual_structure_present": True},
    })
    monkeypatch.setattr(batch, "_ollama_healthcheck", lambda: {"status": "passed"} if model_available else {
        "status": "failed", "reason": "pinned-model-not-installed",
    })
    invoked = []

    def worker(command, _timeout):
        adapter = command[1]
        invoked.append(adapter)
        if adapter == str(batch.NOISE_CLEANUP_ADAPTER):
            return {"returncode": 1, "stdout": '{"status":"needs-review"}'}
        visual = Path(command[command.index("--run-root") + 1])
        if adapter == str(batch.VISUAL_ADAPTER):
            assert visual == package / "visual-attempt-002" and "--resume" not in command
            batch._write_json_atomic(visual / "evidence/page-result-p001.json", {
                "status": "failed", "error": "Ollama HTTP 404",
            })
            return {"returncode": 1, "stdout": '{"status":"failed"}'}
        assert adapter == str(batch.APPLE_OCR_ADAPTER)
        assert visual == package / ("visual-attempt-003" if model_available else "visual-attempt-002")
        assert "--resume" not in command
        markdown = visual / "page-units/p001.md"
        asset = visual / "assets/page-001.png"
        ocr = visual / "evidence/apple-vision-p001.json"
        batch._write_text_atomic(markdown, "<!-- page:1 -->\n\nOCR caption.\n")
        asset.parent.mkdir()
        asset.write_bytes(b"synthetic page image")
        if ocr_verified:
            batch._write_json_atomic(ocr, {"candidates": [{"text": "OCR caption."}]})
        batch._write_json_atomic(visual / "summary.json", {"status": "passed", "source_sha256": fingerprint})
        batch._write_json_atomic(visual / "evidence/page-result-p001.json", {
            "status": "passed", "page": 1, "source_sha256": fingerprint, "route": batch.LOCAL_PAGE_OCR_ROUTE,
            "markdown": {"current_sha256": batch.sha256(markdown)}, "asset": {"sha256": batch.sha256(asset)},
            "ocr_evidence_sha256": batch.sha256(ocr) if ocr_verified else "missing",
        })
        return {"returncode": 0, "stdout": '{"status":"passed"}'}

    monkeypatch.setattr(batch, "_run_command", worker)
    batch.run_page_batches.__wrapped__(source, tmp_path, resume_package=root, hybrid=True)
    assert old_result.read_bytes() == old_bytes
    assert invoked == [*([str(batch.VISUAL_ADAPTER)] if model_available else []),
                       str(batch.APPLE_OCR_ADAPTER), str(batch.NOISE_CLEANUP_ADAPTER)]
    route_path = root / "evidence/page-routes/p001.json"
    route = batch._read_json(route_path)
    assert route["model_called"] is model_available
    assert route["recognition_route"] == batch.LOCAL_PAGE_OCR_FALLBACK
    assert route["visual_fallback_reason"] == (
        "lan-visual-page-conversion-failed" if model_available else "pinned-model-not-installed"
    )
    assert route["status"] == ("passed" if ocr_verified else "failed")
    if model_available:
        failed = Path(route["failed_visual_run_root"]) / "evidence/page-result-p001.json"
        assert batch._read_json(failed)["error"] == "Ollama HTTP 404"
    if ocr_verified:
        unit = root / "page-units/p001.md"
        assert "OCR caption." in unit.read_text() and "../assets/page-001.png" in unit.read_text()
        assert batch._reusable_routed_page(page=1, source_sha256=fingerprint, page_unit_path=unit,
                                          route_record_path=route_path)["resume_reused"] is True
