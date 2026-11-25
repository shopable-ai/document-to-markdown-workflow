from __future__ import annotations

import copy
from dataclasses import asdict, replace
import errno
import json
import multiprocessing
import os
from pathlib import Path
import signal
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont
import pytest

from adapters import pdf_hybrid_visual_audit as audit
from adapters import pdf_hybrid_reader_export as reader
from adapters import pdf_scan_ocr
from adapters import run_pdf_batches as batch


LINES = ("A complete first line of body text", "The second line must stay in order", "Independent local text evidence")
BODY = "\n\n".join(LINES)
NO_VECTORS = {"version": "pdfplumber-vector-v1", "status": "complete", "counts": {"line": 0, "rect": 0, "curve": 0}}


def draw_page(path: Path, kind: str):
    w, h = 640, 820
    im = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=18)
    boxes = []
    for i, value in enumerate(LINES):
        pos = (100, 150 + 52*i)
        draw.text(pos, value, fill="black", font=font)
        x0, y0, x1, y1 = draw.textbbox(pos, value, font=font)
        boxes.append(audit.Box(value, .98, x0/w, y0/h, x1/w, y1/h))
    if kind in {"scan", "missing-ocr"}:
        draw.line((2, 100, 2, 650), fill="black", width=1)
        draw.line((100, 725, 540, 725), fill="#444", width=1)
        draw.text((314, 780), "21", fill="black", font=font)
        x0, y0, x1, y1 = draw.textbbox((314, 780), "21", font=font)
        boxes.extend([audit.Box("-21-", .3, x0/w, y0/h, x1/w, y1/h)])
    if kind == "mixed":
        draw.ellipse((320, 440, 490, 580), fill="#2244aa")
    if kind in {"table", "integrated"}:
        top = 470 if kind == "table" else 130
        for y in (top, top + 40, 650):
            draw.line((85, y, 540, y), fill="#c9c9c9", width=1)
        for x in (85, 310, 540):
            draw.line((x, top, x, 650), fill="#c9c9c9", width=1)
    if kind == "missing-ocr":
        draw.text((105, 315), "An entire line missed by OCR", fill="black", font=font)
    if kind == "arrow":
        draw.line((300, 400, 357, 425, 335, 426), fill="#999", width=1)
        draw.line((357, 425, 350, 410), fill="#999", width=1)
    if kind == "pale-overlay":
        draw.text((105, 315), "Visible pale letters", fill="#e5e5e5", font=font)
    im.save(path)
    return boxes


@pytest.mark.parametrize(("kind", "body", "expected"), [
    ("scan", BODY, "candidate-remove"),
    ("plain", BODY, "candidate-remove"),
    ("mixed", BODY, "crop-required"),
    ("table", BODY, "crop-required"),
    ("integrated", BODY, "retain"),
    ("plain", BODY.replace("must", ""), "needs-review"),
    ("plain", "\n".join(reversed(LINES)), "needs-review"),
    ("missing-ocr", BODY, "needs-review"),
])
def test_page_detector(tmp_path, kind, body, expected):
    image = tmp_path / "page.png"
    evidence = audit.TextEvidence("fixture", [[b] for b in draw_page(image, kind)])
    result = audit.classify(evidence, body, image)
    assert result["decision"] == expected
    if kind in {"table", "integrated"}:
        assert result["visual"]["intersecting_rules"] >= 4
    if kind == "missing-ocr":
        assert result["gates"]["reading_order_coverage"] is True
        assert result["gates"]["visual_clear"] is False


def test_alt_text_comments_and_other_pages_do_not_count_as_transcription(tmp_path):
    image = tmp_path / "page.png"
    evidence = audit.TextEvidence("fixture", [[b] for b in draw_page(image, "plain")])
    result = audit.classify(evidence, f"<!-- {BODY} -->\n![{LINES[0]}](assets/page-001.png)\n\n" + LINES[-1], image)
    assert result["decision"] == "needs-review"
    assert result["gates"]["character_coverage"] is False


def put(root: Path, name: str, value: dict | str) -> Path:
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")
    return file


@pytest.fixture
def scan_resume_package(tmp_path, monkeypatch):
    root = tmp_path / "engineering"
    source = put(tmp_path, "source.pdf", "synthetic PDF identity; no real OCR tools")
    source_sha = audit.digest(source)
    pages = [1, 2]
    sections = []
    put(root, "acceptance.json", {"status": "passed", "source_sha256": source_sha, "selected_pages": pages,
         batch.hybrid_validator.READER_REQUIRED: True, batch.hybrid_validator.READER_AUDIT_VERSION: audit.VERSION})
    put(root, "summary.json", {"status": "running"})
    put(root, "progress.json", {"status": "running", "stages": [{"stage": "independent_acceptance", "status": "passed"}]})
    put(root, "evidence.json", {"status": "running"})
    (root / "assets").mkdir()
    for page in pages:
        image_ref = f"assets/page-{page:03d}.png"
        image = root / image_ref
        boxes = draw_page(image, "scan")
        image_hash = audit.digest(image)
        section = f"<!-- page:{page} -->\n\n![Page]({image_ref})\n\n{BODY}\n"
        sections.append(section)
        unit = put(root, f"page-units/p{page:03d}.md", section)
        visual = f"pages/page-{page:03d}/visual"
        primary = put(root, f"{visual}/evidence/apple-vision-p{page:03d}.json", {
            "status": "complete", "candidate_count": len(boxes), "engine": "Apple Vision VNRecognizeTextRequest",
            "candidates": [{"text": b.text, "confidence": b.confidence,
                            "bbox": {"x": b.x0, "y": 1-b.y1, "width": b.x1-b.x0, "height": b.y1-b.y0}}
                           for b in boxes],
        })
        put(root, f"{visual}/evidence/page-result-p{page:03d}.json", {
            "status": "passed", "page": page, "source_sha256": source_sha,
            "asset": {"sha256": image_hash}, "ocr_evidence_sha256": audit.digest(primary),
        })
        put(root, f"{visual}/evidence/request-evidence-p{page:03d}.json", {"image_sha256": image_hash})
        put(root, f"evidence/page-routes/p{page:03d}.json", {
            "page": page, "status": "passed", "source_sha256": source_sha, "page_unit_sha256": audit.digest(unit),
            "visual_asset_sha256": image_hash, "visual_run_root": visual, "route": "visual-only",
            "recognition_route": "apple-vision-ocr", "image_stats": {"image_count": 1, "max_area_ratio": 1},
            "signals": {"pdf_vector_objects": NO_VECTORS, "page_image_required": True,
                        "native_text_visibility": {"status": "hidden"}},
        })
    index = put(root, "index.md", "".join(sections))
    put(root, "reports/heading-repair.json", {
        "status": "passed", "index_sha256": audit.digest(index), "validation": {"heading_prefix_only": True},
        "formatting_validation": {"passed": True},
    })
    put(root, "reports/page-completion.json", batch._page_completion_report(
        selected=pages, page_results=[{"page": page, "status": "passed"} for page in pages],
    ))
    log = tmp_path / "ocr-calls"
    parent_pid = os.getpid()

    def recognize(_data, record):
        page = record["page"]
        with log.open("a") as handle:
            handle.write(f"{page}\n")
        if page == 2 and os.getpid() != parent_pid:
            # Real collect() holds the page lock; no result has been published.
            os.kill(os.getpid(), signal.SIGKILL)
        record.update(status="ok", engine_version="5.0", engine_sha256="a"*64,
                      model_sha256={lang: "b"*64 for lang in pdf_scan_ocr.LANGUAGES},
                      image_size=[640, 820], rows=[[asdict(b)] for b in boxes])

    monkeypatch.setattr(pdf_scan_ocr, "_recognize", recognize)
    monkeypatch.setattr(audit, "SCAN_AUDIT_MAX_WORKERS", 1)

    def pdfinfo(command, **_kwargs):
        assert command == ["pdfinfo", str(source)]
        return SimpleNamespace(stdout="Pages: 2\n", returncode=0, stderr="")

    monkeypatch.setattr(batch.subprocess, "run", pdfinfo)
    monkeypatch.setattr(batch, "_run_command", lambda *_: pytest.fail("engineering work must be reused"))

    def stage():
        return batch.run_page_batches.__wrapped__(source, tmp_path, resume_package=root, hybrid=True,
                                                  write_book_markdown=False)

    return root, source, pages, log, stage


def test_sigkill_in_secondary_ocr_reuses_completed_cache_and_verifies_reader(scan_resume_package):
    root, source, pages, log, stage = scan_resume_package
    validation_path = root / batch.hybrid_validator.READER_VALIDATION
    audit_path = root / batch.hybrid_validator.READER_AUDIT
    engineering = {path: audit.digest(path) for path in [root / "index.md", root / "acceptance.json",
                   *(root / f"evidence/page-routes/p{page:03d}.json" for page in pages)]}

    child = multiprocessing.get_context("fork").Process(target=stage)
    child.start()
    try:
        child.join(10)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill()
            child.join(5)
        child.close()

    cache = root / audit.SCAN_CACHE
    completed, interrupted = (cache / f"p{page:03d}.json" for page in pages)
    completed_bytes = completed.read_bytes()
    assert not interrupted.exists() and interrupted.with_suffix(".json.lock").exists()
    assert not audit_path.exists() and not validation_path.exists()
    assert not list(root.parent.glob(".reader-visual-*"))
    assert log.read_text().splitlines() == ["1", "2"]

    code, result = stage()

    assert (code, result["status"], result["run_id"], result["output"]) == (0, "passed", root.name, str(root))
    check = batch.hybrid_validator.validate_package(root, source)
    assert (check["status"], check["engineering_status"], check["reader_visual"]["bytes_verified"]) == ("passed", "passed", True)
    assert {path: audit.digest(path) for path in engineering} == engineering
    assert completed.read_bytes() == completed_bytes
    assert log.read_text().splitlines() == ["1", "2", "2"]
    report = audit.read_json(audit_path)
    assert [row["page"] for row in report["pages"]] == pages
    for row in report["pages"]:
        page = row["page"]
        cache_ref = (audit.SCAN_CACHE / f"p{page:03d}.json").as_posix()
        assert row["evidence_sha256"][cache_ref] == audit.digest(root / cache_ref)
        assert audit.digest(root / row["asset"]) == row["asset_sha256"]
    validation_bytes = validation_path.read_bytes()
    assert stage() == (code, result) and validation_path.read_bytes() == validation_bytes
    assert log.read_text().splitlines() == ["1", "2", "2"]
    source.write_bytes(b"different source fingerprint")
    assert batch.hybrid_validator.validate_reader_stage(root, source, pages)["status"] == "failed"
    assert stage()[1]["status"] == "failed"
    assert log.read_text().splitlines() == ["1", "2", "2"]


def test_secondary_ocr_disk_write_failure_does_not_commit_reader(scan_resume_package, monkeypatch):
    root, _, _, log, stage = scan_resume_package
    real_publish = pdf_scan_ocr._publish

    def disk_full(path, record):
        if record["page"] == 2:
            raise OSError(errno.ENOSPC, "fixture write failure", str(path))
        real_publish(path, record)

    with monkeypatch.context() as failed:
        failed.setattr(pdf_scan_ocr, "_publish", disk_full)
        code, result = stage()
    assert code == 1 and result["status"] == "failed"
    assert "fixture write failure" in result["summary"]["reader_visual"]["error"]
    assert not (root / batch.hybrid_validator.READER_AUDIT).exists()
    assert not (root / batch.hybrid_validator.READER_VALIDATION).exists()

    code, result = stage()
    assert code == 0 and result["status"] == "passed"
    assert result["summary"]["reader_visual"]["bytes_verified"] is True
    assert log.read_text().splitlines() == ["1", "2", "2"]


def test_empty_apple_ocr_retains_render_without_aborting_collection_or_replay(tmp_path, monkeypatch):
    root = tmp_path / "engineering"
    source = put(tmp_path, "fixture.pdf", "fixture PDF")
    source_sha = audit.digest(source)
    cover = "<!-- page:1 -->\n\n![Page](assets/page-001.png)\n"
    following = "<!-- page:2 -->\n\nBody on the following page.\n"
    index = put(root, "index.md", cover + "\n" + following)
    unit1 = put(root, "page-units/p001.md", cover)
    unit2 = put(root, "page-units/p002.md", following)
    image = root / "assets/page-001.png"
    image.parent.mkdir()
    Image.new("RGB", (120, 160), "white").save(image)
    image_sha = audit.digest(image)
    visual = "pages/page-001/visual"
    evidence = root / visual / "evidence"
    ocr = put(evidence, "apple-vision-p001.json", {
        "status": "complete", "engine": "Apple Vision VNRecognizeTextRequest", "candidate_count": 0, "candidates": [],
    })
    put(evidence, "page-result-p001.json", {"status": "passed", "page": 1, "source_sha256": source_sha,
        "asset": {"sha256": image_sha}, "ocr_evidence_sha256": audit.digest(ocr)})
    put(evidence, "request-evidence-p001.json", {"image_sha256": image_sha})
    put(root, "evidence/page-routes/p001.json", {"page": 1, "route": "visual-only", "status": "passed",
        "source_sha256": source_sha, "page_unit_sha256": audit.digest(unit1), "visual_run_root": visual,
        "visual_asset_sha256": image_sha, "recognition_route": "apple-vision-ocr-fallback",
        "image_stats": {"image_count": 1, "max_area_ratio": 1}, "signals": {"pdf_vector_objects": NO_VECTORS}})
    put(root, "evidence/page-routes/p002.json", {"page": 2, "route": "native-only", "status": "passed",
        "source_sha256": source_sha, "page_unit_sha256": audit.digest(unit2)})
    monkeypatch.setattr(pdf_scan_ocr, "collect", lambda *_a, **_k: pytest.fail("no trusted primary OCR text"))

    report = audit.audit_package(root, source, [1, 2], collect_scan=True)
    first, second = report["pages"]
    assert first["reader_action"] == "retain" and first["decision"] == "needs-review"
    assert first["asset_sha256"] == image_sha and not first["independent_text_evidence"]
    assert first["evidence_diagnostics"] == ["cached_apple:ValueError"]
    assert second["page"] == 2
    assert audit.validate_audit(root, source, report, [1, 2])["status"] == "passed"
    target = tmp_path / "reader"
    assert reader.export_reader(root, report, target)["status"] == "passed"
    assert (target / "index.md").read_bytes() == index.read_bytes()
    assert audit.digest(target / "assets/page-001.png") == image_sha


def test_collection_is_bounded_and_ordered_but_replay_is_sequential(tmp_path, monkeypatch):
    sequence = list(range(1, 10))
    source = put(tmp_path, "source.pdf", "fixture PDF")
    root = tmp_path / "engineering"
    put(root, "index.md", "".join(f"<!-- page:{i} -->\nBody {i}\n" for i in sequence))
    source_sha = audit.digest(source)
    caller = get_ident()
    lock = Lock()
    first_wave = Barrier(4, timeout=5)
    second_finished = Event()
    active = peak = 0
    finished = []
    sequential = []

    def audit_page(package, page, body, sha, *, version, collect_scan):
        nonlocal active, peak
        assert (package, body.strip(), sha) == (root, f"Body {page}", source_sha)
        if version == audit.VERSION and collect_scan:
            with lock:
                active += 1
                peak = max(peak, active)
            if page <= 4:
                first_wave.wait()
            if page == 1:
                assert second_finished.wait(timeout=5)
            with lock:
                finished.append(page)
                active -= 1
            if page == 2:
                second_finished.set()
        else:
            assert get_ident() == caller
            sequential.append((page, version, collect_scan))
        return {"page": page, "asset": f"assets/page-{page:03d}.png",
                "decision": "retain", "reader_action": "retain"}

    monkeypatch.setattr(audit, "audit_page", audit_page)
    report = audit.audit_package(root, source, sequence, collect_scan=True)
    assert peak == 4
    assert sorted(finished) == sequence and finished.index(2) < finished.index(1)
    assert [row["page"] for row in report["pages"]] == report["page_numbers_by_decision"]["retain"] == sequence
    assert sequential == []

    assert audit.validate_audit(root, source, report, sequence)["status"] == "passed"
    assert sequential == [(i, audit.VERSION, False) for i in sequence]
    for version in (audit.PREVIOUS_VERSION, audit.LEGACY_VERSION):
        sequential.clear()
        audit.audit_package(root, source, sequence, version=version, collect_scan=True)
        assert sequential == [(i, version, True) for i in sequence]


@pytest.mark.parametrize("scan", [False, True])
def test_package_gate_reuses_locked_coordinate_evidence_and_revalidates(tmp_path, monkeypatch, scan):
    root = tmp_path / "engineering"
    source = put(tmp_path, "source.pdf", "fixture source; OCR and layout evidence are fixture data")
    source_sha = audit.digest(source)
    index = put(root, "index.md", "<!-- page:1 -->\n\n![Page](assets/page-001.png)\n\n" + BODY + "\n")
    unit = put(root, "page-units/p001.md", index.read_text())
    image = root / "assets/page-001.png"
    image.parent.mkdir()
    boxes = draw_page(image, "scan" if scan else "plain")
    image_sha = audit.digest(image)
    route = {"page": 1, "status": "passed", "source_sha256": source_sha, "page_unit_sha256": audit.digest(unit),
             "visual_asset_sha256": image_sha, "native_body_sufficient": True,
             "image_stats": {"image_count": 1 if scan else 0, "max_area_ratio": 1 if scan else 0},
             "signals": {"pdf_vector_objects": NO_VECTORS}}
    if scan:
        route.update(route="visual-only", recognition_route="apple-vision-ocr")
        route["signals"].update(page_image_required=True, native_text_visibility={"status": "hidden"})
        route["visual_run_root"] = "pages/page-001/visual"
        base = root / route["visual_run_root"] / "evidence"
        evidence = put(base, "apple-vision-p001.json", {"status": "complete", "candidate_count": len(boxes),
                        "engine": "Apple Vision VNRecognizeTextRequest",
                        "candidates": [{"text": b.text, "confidence": b.confidence,
                                        "bbox": {"x": b.x0, "y": 1-b.y1, "width": b.x1-b.x0, "height": b.y1-b.y0}}
                                       for b in boxes]})
        put(base, "page-result-p001.json", {"status": "passed", "page": 1, "source_sha256": source_sha,
            "asset": {"sha256": image_sha}, "ocr_evidence_sha256": audit.digest(evidence)})
        put(base, "request-evidence-p001.json", {"image_sha256": image_sha})
    else:
        coords = "".join(f'<line><word xMin="{b.x0*640}" yMin="{b.y0*820}" '
                         f'xMax="{b.x1*640}" yMax="{b.y1*820}">{b.text}</word></line>' for b in boxes)
        evidence = put(root, "pages/page-001/evidence/page-001.json", {"status": "passed", "page": 1,
            "source_sha256": source_sha, "text": BODY, "pdf_vector_objects": NO_VECTORS,
            "layout": {"status": "complete", "command": {"stdout":
            f'<html><body><page width="640" height="820">{coords}</page></body></html>'}}})
        route.update(native_evidence=str(evidence.relative_to(root)), native_evidence_sha256=audit.digest(evidence))
    put(root, "evidence/page-routes/p001.json", route)
    report = audit.audit_package(root, source, [1])
    if scan:
        assert report["page_numbers_by_decision"]["needs-review"] == [1]
        assert report["pages"][0]["reason_code"] == "scan-independent-ocr-unavailable"
        assert report["pages"][0]["reader_action"] == "retain"
        previous = audit.audit_package(root, source, [1], version=audit.PREVIOUS_VERSION)
        assert previous["pages"][0]["reason_code"] == "independent-text-evidence-missing"
        assert audit.validate_audit(root, source, previous)["status"] == "passed"
        legacy = audit.audit_package(root, source, [1], version=audit.LEGACY_VERSION)
        assert legacy["page_numbers_by_decision"]["candidate-remove"] == [1]
        assert audit.validate_audit(root, source, legacy)["status"] == "passed"
    else:
        assert report["page_numbers_by_decision"]["candidate-remove"] == [1]
        assert report["pages"][0]["reader_action"] == "omit"
    assert audit.validate_audit(root, source, report)["status"] == "passed"
    forged = copy.deepcopy(report)
    forged["pages"][0]["decision"] = "retain"
    assert audit.validate_audit(root, source, forged)["status"] == "failed"
    if scan:
        calls = []
        def collect(input_image, output, **key):
            calls.append(input_image)
            result = dict(pdf_scan_ocr._binding(**key), schema=pdf_scan_ocr.SCHEMA, version=pdf_scan_ocr.VERSION,
                          engine="tesseract", elapsed_ms=1, engine_version="5.0", engine_sha256="a"*64,
                          model_sha256={lang: "b"*64 for lang in pdf_scan_ocr.LANGUAGES}, psm=pdf_scan_ocr.PSM,
                          oem=pdf_scan_ocr.OEM, languages=list(pdf_scan_ocr.LANGUAGES), status="ok",
                          image_size=[640, 820], reason_code=None, diagnostic=None, rows=[[asdict(b)] for b in boxes])
            result["payload_sha256"] = pdf_scan_ocr._payload_sha(result)
            put(root, output.relative_to(root).as_posix(), result)
        monkeypatch.setattr(pdf_scan_ocr, "collect", collect)
        with_second = audit.audit_package(root, source, collect_scan=True)
        assert with_second["page_numbers_by_decision"]["candidate-remove"] == [1]
        assert audit.validate_audit(root, source, with_second)["status"] == "passed"
        assert audit.audit_package(root, source, collect_scan=True) == with_second
        copy_root = tmp_path / "reader"
        assert reader.export_reader(root, with_second, copy_root)["status"] == "passed"
        assert reader.validate_reader(root, with_second, copy_root)["status"] == "passed"
        assert not (copy_root / "assets").exists()
        assert (copy_root / "index.md").read_bytes() == index.read_bytes().replace(b"![Page](assets/page-001.png)\n", b"")
        cache = root / audit.SCAN_CACHE / "p001.json"
        cache.write_text(cache.read_text().replace(audit.digest(evidence), "0"*64), encoding="utf-8")
        assert audit.validate_audit(root, source, with_second)["status"] == "failed"
        assert audit.audit_package(root, source, collect_scan=True)["counts"]["candidate-remove"] == 0
        assert calls == [image]
    evidence.write_text("{}", encoding="utf-8")
    assert audit.validate_audit(root, source, report)["status"] == "failed"
    assert audit.audit_package(root, source)["page_numbers_by_decision"]["needs-review"] == [1]


@pytest.mark.parametrize(("kind", "text_edit", "omit"), [
    ("scan", lambda s: s, True),
    ("plain", lambda s: s.replace("2.5", "2"), False),
    ("plain", lambda s: s.replace("-", ""), False),
    ("plain", lambda s: s.replace("!", ""), False),
    ("plain", lambda s: s.removesuffix(LINES[1]), False),
    ("plain", lambda s: "\n".join(reversed(s.splitlines())), False),
    ("table", lambda s: s, False),
    ("arrow", lambda s: s, False),
    ("mixed", lambda s: s, False),
    ("pale-overlay", lambda s: s, False),
])
def test_scan_requires_verbatim_text_order_and_visual_clearance(tmp_path, kind, text_edit, omit):
    image = tmp_path / "page.png"
    boxes = draw_page(image, kind)
    boxes[:3] = [replace(boxes[0], text=LINES[0] + " -2.5!"), boxes[1], replace(boxes[2], text=LINES[1])]
    rows = [[b] for b in boxes]
    body = "\n".join(row[0].text for row in rows if not audit.footer_number(row))
    primary, second = audit.TextEvidence("apple-vision-cached", rows), audit.TextEvidence("tesseract", rows)
    assert (audit.assess_scan(primary, second, text_edit(body), image)["reader_action"] == "omit") is omit
    with pytest.raises(ValueError, match="independent"):
        audit.assess_scan(primary, primary, body, image)


@pytest.mark.parametrize(("edit", "action"), [
    (lambda lines: "## " + "\n".join(lines), "omit"),
    (lambda lines: "\n".join(lines).replace("-2.5", "2.5"), "no-reference-unverified"),
    (lambda lines: "\n".join(reversed(lines)), "no-reference-unverified"),
    (lambda lines: "\n".join(lines[:-1]), "no-reference-unverified"),
])
def test_final_native_text_gate_checks_already_unreferenced_pages(tmp_path, edit, action):
    root = tmp_path / "engineering"
    source = put(tmp_path, "input.pdf", "fixture PDF")
    source_sha = audit.digest(source)
    lines = ["Temperature -2.5 °C.", "Second line; still visible.", "Second line; still visible."]
    unit = put(root, "page-units/p001.md", "<!-- page:1 -->\n\n" + "\n".join(lines) + "\n")
    put(root, "index.md", "<!-- page:1 -->\n\n" + edit(lines) + "\n")
    xml = "".join(f'<line><word xMin="10" yMin="{20*i+5}" xMax="90" yMax="{20*i+10}">{line}</word></line>'
                  for i, line in enumerate(lines))
    evidence = put(root, "native/p001.json", {"status": "passed", "page": 1, "source_sha256": source_sha,
                   "text": "\n".join(lines), "pdf_vector_objects": NO_VECTORS,
                   "layout": {"status": "complete", "command": {
                       "stdout": f'<html><body><page width="100" height="100">{xml}</page></body></html>'}}})
    put(root, "evidence/page-routes/p001.json", {
        "page": 1, "status": "passed", "source_sha256": source_sha, "route": "native-only",
        "page_unit_sha256": audit.digest(unit), "native_evidence": str(evidence.relative_to(root)),
        "native_evidence_sha256": audit.digest(evidence), "native_body_sufficient": True,
        "image_stats": {"image_count": 0}, "rendered_structure": {"status": "not-run"},
        "signals": {"pdf_vector_objects": NO_VECTORS},
    })
    report = audit.audit_package(root, source)
    assert report["pages"][0]["reader_action"] == action
    assert report["no_page_image_reference"] == [1]
    assert report["verified_no_page_image"] == ([1] if action == "omit" else [])
    assert report["unverified_no_page_image"] == ([] if action == "omit" else [1])
    assert audit.validate_audit(root, source, report)["status"] == "passed"
    assert not (root / "assets").exists()
    if action == "omit":
        route_path = root / "evidence/page-routes/p001.json"
        route = audit.read_json(route_path)
        for unsafe_vector_signal in ({"version": "pdfplumber-vector-v1", "status": "unavailable"},
                                     {**NO_VECTORS, "counts": {**NO_VECTORS["counts"], "curve": 1}}):
            route["signals"]["pdf_vector_objects"] = unsafe_vector_signal
            put(root, "evidence/page-routes/p001.json", route)
            assert audit.audit_package(root, source)["unverified_no_page_image"] == [1]


def test_new_run_does_not_accept_legacy_audit_version(tmp_path):
    put(tmp_path, "acceptance.json", {"reader_visual_audit_version": audit.VERSION})
    result = audit.validate_audit(tmp_path, None, {"version": audit.LEGACY_VERSION})
    assert result["status"] == "failed"
    assert "acceptance policy" in result["diagnostics"][0]
