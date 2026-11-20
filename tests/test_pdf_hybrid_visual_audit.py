from __future__ import annotations

import copy
from dataclasses import asdict, replace
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest

from adapters import pdf_hybrid_visual_audit as audit
from adapters import pdf_hybrid_reader_export as reader
from adapters import pdf_scan_ocr


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
