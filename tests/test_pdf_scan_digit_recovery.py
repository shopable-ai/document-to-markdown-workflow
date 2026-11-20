from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest

from adapters import apple_vision_pdf_to_markdown as vision
from adapters import pdf_scan_digit_recovery as recovery
from adapters import pdf_hybrid_visual_audit as audit


@dataclass(frozen=True)
class Box:
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Rows:
    engine: str
    rows: list[list[Box]]
    layout_uncertain: bool = False


TITLES = ["案例甲、完整标题……", "案例乙：另一个标题", "案例丙，第三个标题"]
NUMBERS = ["17", "34", "46"]
IMAGE_LINE = "![原图](<assets/page 001.png>)"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def anchor(i, *, confidence=.96):
    y = .20 + .14 * i
    return Box(TITLES[i], confidence, .12, y - .003, .39, y + .027)


def digit(i, **changes):
    y = .20 + .14 * i
    return replace(Box(NUMBERS[i], .98, .79, y, .815, y + .024), **changes)


def sample(n=1, *, separate_digit=False):
    lines = ["<!-- page: 1 -->", "", "## 第 1 页", "", *(f"- {t}  " for t in TITLES[:n]), "", IMAGE_LINE]
    markdown = "\r\n".join(lines).encode()  # deliberately no final newline
    primary = Rows("apple-vision-cached", [[anchor(i)] for i in range(n)])
    independent_rows = []
    im = Image.new("RGB", (1000, 1200), "white")
    draw = ImageDraw.Draw(im)
    for i in range(n):
        # Independent tokenizer and punctuation differ; neither is written to Markdown.
        a, b = replace(anchor(i), text=TITLES[i][:3], x1=.19), replace(anchor(i), text=TITLES[i][4:].rstrip("…"), x0=.21)
        d = digit(i)
        if separate_digit:
            independent_rows.extend([[a, b], [d]])
        else:
            independent_rows.append([a, b, d])
        draw.text((int(d.x0 * 1000), int(d.y0 * 1200)), d.text, fill="black", font=ImageFont.load_default(size=22))
    encoded = io.BytesIO()
    im.save(encoded, format="PNG")
    return markdown, primary, Rows("tesseract", independent_rows), encoded.getvalue()


def lock(markdown, primary, independent, render):
    return recovery.PageLocks(sha(b"verified-pdf-id"), 1, sha(render), sha(markdown),
                              recovery.rows_sha256(primary), recovery.rows_sha256(independent))


def diagnose(markdown, primary, independent, render):
    return recovery.diagnose(markdown, primary, independent, locks=lock(markdown, primary, independent, render))


def stub_vision(monkeypatch, text, *, engine="Apple Vision VNRecognizeTextRequest", confidence=.99,
                bbox=None, extra_candidate=False):
    calls = []

    def read(path, timeout):
        assert path.name == "crop.png"  # no expected digits in the OCR request
        with Image.open(path) as im:
            assert im.width < 50 and im.height < 50
            assert im.convert("L").getextrema()[0] == 0
        calls.append(Path(path))
        candidate = {"text": text, "confidence": confidence, "bbox": bbox or
                     {"x": .12, "y": .12, "width": .76, "height": .76}}
        candidates = [candidate] * (2 if extra_candidate else 1)
        return {"engine": engine, "status": "complete", "os": "synthetic macOS", "languages": ["zh-Hans", "en-US"],
                "uses_language_correction": True, "candidate_count": len(candidates), "candidates": candidates}, .07

    monkeypatch.setattr(vision, "_run_apple_vision", read)
    return calls


@pytest.mark.parametrize("separate_digit", [False, True])
def test_reports_exact_anchor_and_normalized_geometry_without_editing(separate_digit):
    markdown, primary, independent, render = sample(separate_digit=separate_digit)
    diagnostic = diagnose(markdown, primary, independent, render)
    assert diagnostic.status == "proposals"
    proposal, = diagnostic.proposals
    assert proposal.exact_line == f"- {TITLES[0]}  "
    assert proposal.line_number == 5 and proposal.anchor == "案例甲完整标题"
    assert proposal.digits == "17" and proposal.independent_digit_row == int(separate_digit)
    assert proposal.digit_bbox == (.79, .2, .815, .224)
    assert proposal.crop_bbox[0] < .79 < .815 < proposal.crop_bbox[2]


def test_two_distinct_crop_proofs_append_only_digits_and_preserve_all_other_bytes(monkeypatch):
    markdown, primary, independent, render = sample(2, separate_digit=True)
    locks = lock(markdown, primary, independent, render)
    result = recovery.diagnose(markdown, primary, independent, locks=locks)
    proofs = []
    for proposal in result.proposals:
        calls = stub_vision(monkeypatch, proposal.digits)
        proof = recovery.read_apple_vision_crop(render, locks=locks, proposal=proposal)
        assert proof.schema == recovery.PROOF_SCHEMA and proof.wall_seconds == .07
        assert proof.adapter_sha256 == sha(vision.APPLE_VISION_SCRIPT.read_bytes())
        assert len(calls) == 1 and not calls[0].exists()
        proofs.append(proof)
    apply = lambda items: recovery.apply_recovery(markdown, primary, independent, locks=locks, render_bytes=render, proofs=items)
    with pytest.raises(recovery.RecoveryRefused, match="missing-duplicate-or-unrelated"):
        apply(proofs[:1])
    with pytest.raises(recovery.RecoveryRefused, match="missing-duplicate-or-unrelated"):
        apply(proofs[:1] * 2)
    fixed = apply(proofs[::-1])
    assert fixed == markdown.replace(TITLES[0].encode(), f"{TITLES[0]} 17".encode()).replace(
        TITLES[1].encode(), f"{TITLES[1]} 34".encode())


@pytest.mark.parametrize("mods,reason", [
    ({"text": "7"}, "disagree"),
    ({"text": "17 "}, "disagree"),
    ({"text": "-17"}, "disagree"),
    ({"engine": "tesseract"}, "not-an-independent"),
    ({"confidence": .89}, "low-confidence"),
    ({"extra_candidate": True}, "not-an-independent"),
    ({"bbox": {"x": 0., "y": .1, "width": .09, "height": .8}}, "does-not-localize"),
])
def test_ocr_proposal_cannot_self_confirm_or_accept_crop_disagreement(monkeypatch, mods, reason):
    markdown, primary, independent, render = sample()
    locks = lock(markdown, primary, independent, render)
    proposal, = recovery.diagnose(markdown, primary, independent, locks=locks).proposals
    stub_vision(monkeypatch, **({"text": "17"} | mods))
    proof = recovery.read_apple_vision_crop(render, locks=locks, proposal=proposal)
    with pytest.raises(recovery.RecoveryRefused, match=reason):
        recovery.apply_recovery(markdown, primary, independent, locks=locks, render_bytes=render, proofs=[proof])


@pytest.mark.parametrize("substitute", [
    "- 案例甲\r\n- 完整标题……",  # No physical line has the full anchor.
    "- 案例甲、完整标题……\r\n\r\n17",  # The digit already appears on a split line.
    "- 案例甲、完整标题……17",  # Already present.
    "**案例甲、完整标题……**",  # Inline Markdown formatting, not a plain anchor.
    "- 案例甲、完全标题……",  # No fuzzy fix for different content.
    "- 案例甲、完整标题……\r\n- 案例甲、完整标题……",  # Repeated anchor.
    "- 案例甲、完整标题……\r\n- 案例甲、完整标题……17",  # Ambiguous including pre-numbered anchor.
    " \t案例甲、完整标题……",  # Indented code.
    "~~~\r\n- 案例甲、完整标题……\r\n~~~",  # Not body text.
    "<!--\r\n- 案例甲、完整标题……\r\n-->",
])
def test_refuses_split_repeated_or_nonbody_anchors(substitute):
    markdown, primary, independent, render = sample()
    markdown = markdown.replace(f"- {TITLES[0]}".encode(), substitute.encode())
    assert diagnose(markdown, primary, independent, render).status == "needs-review"


@pytest.mark.parametrize("case,reason", [
    ("low-confidence", "digit-confidence-geometry-or-margin"),
    ("primary-duplicate", "nonunique-ocr-anchor"),
    ("primary-multiple-y", "nonunique-row-overlap"),
    ("independent-multiple-y", "nonunique-row-overlap"),
    ("digits-not-right", "not-isolated-right"),
    ("nearby-ink-box", "crop-touches-neighbor"),
    ("different-left-box", "left-anchors-disagree"),
    ("uncertain-layout", "uncertain-ocr-layout"),
])
def test_geometry_and_evidence_guards(case, reason):
    markdown, primary, independent, render = sample()
    match case:
        case "low-confidence":
            independent.rows[0][-1] = digit(0, confidence=.89)
        case "primary-duplicate":
            primary.rows.append([replace(anchor(1), text=TITLES[0])])
        case "primary-multiple-y":
            primary.rows.append([replace(anchor(0), text="extra row")])
        case "independent-multiple-y":
            independent.rows.append([replace(anchor(0), text="extra row")])
        case "digits-not-right":
            independent.rows[0][-1] = digit(0, x0=.35, x1=.38)
        case "nearby-ink-box":
            independent.rows.append([Box("噪声", .98, .798, .225, .806, .230)])
        case "different-left-box":
            primary.rows[0] = [replace(anchor(0), x0=.40, x1=.64)]
        case "uncertain-layout":
            independent = replace(independent, layout_uncertain=True)
    result = diagnose(markdown, primary, independent, render)
    assert result.status == "needs-review"
    assert reason in result.rejected[0]["reason"]


def test_too_many_or_out_of_order_proposals_refuse_the_page():
    markdown, primary, independent, render = sample(3)
    assert diagnose(markdown, primary, independent, render).status == "needs-review"
    markdown, primary, independent, render = sample(2)
    swapped = markdown.replace(TITLES[0].encode(), b"placeholder").replace(TITLES[1].encode(), TITLES[0].encode())
    assert diagnose(swapped.replace(b"placeholder", TITLES[1].encode()), primary, independent, render).status == "needs-review"


def test_hash_drift_and_engine_failure_close_the_guard(monkeypatch):
    markdown, primary, independent, render = sample()
    locks = lock(markdown, primary, independent, render)
    proposal, = recovery.diagnose(markdown, primary, independent, locks=locks).proposals
    with pytest.raises(recovery.RecoveryRefused, match="markdown-hash"):
        recovery.diagnose(markdown + b"\n", primary, independent, locks=locks)
    with pytest.raises(recovery.RecoveryRefused, match="rows-hash"):
        recovery.diagnose(markdown, replace(primary, layout_uncertain=True), independent, locks=locks)
    with pytest.raises(recovery.RecoveryRefused, match="nonindependent-row-engines"):
        diagnose(markdown, primary, replace(independent, engine="apple-vision"), render)
    with pytest.raises(recovery.RecoveryRefused, match="render-hash"):
        recovery.read_apple_vision_crop(render + b"\n", locks=locks, proposal=proposal)
    with pytest.raises(recovery.RecoveryRefused, match="invalid-crop-request"):
        recovery.read_apple_vision_crop(render, locks=locks, proposal=replace(proposal, digits="99"))

    stub_vision(monkeypatch, "17")
    proof = recovery.read_apple_vision_crop(render, locks=locks, proposal=proposal)
    with pytest.raises(recovery.RecoveryRefused, match="proof-lock-mismatch"):
        recovery.apply_recovery(markdown, primary, independent, locks=locks, render_bytes=render,
                                proofs=[replace(proof, payload_sha256=sha(b"changed"))])
    with pytest.raises(recovery.RecoveryRefused, match="unrelated-crop-proof"):
        recovery.apply_recovery(markdown, primary, independent, locks=replace(locks, source_sha256=sha(b"another")),
                                render_bytes=render, proofs=[proof])

    def missing(path, timeout):
        raise vision.OcrRunFailure("missing Swift")

    monkeypatch.setattr(vision, "_run_apple_vision", missing)
    with pytest.raises(recovery.RecoveryRefused, match="crop-unavailable"):
        recovery.read_apple_vision_crop(render, locks=locks, proposal=proposal)


@pytest.mark.parametrize("edge,reason", [(False, "no-clear-ink"), (True, "edge-ink")])
def test_requires_real_unclipped_crop_ink_before_ocr(monkeypatch, edge, reason):
    markdown, primary, independent, _ = sample()
    im = Image.new("RGB", (1000, 1200), "white")
    if edge:
        ImageDraw.Draw(im).line((795, 225, 795, 270), fill="black", width=2)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    render = buf.getvalue()
    locks = lock(markdown, primary, independent, render)
    proposal, = recovery.diagnose(markdown, primary, independent, locks=locks).proposals
    calls = stub_vision(monkeypatch, "17")
    with pytest.raises(recovery.RecoveryRefused, match=reason):
        recovery.read_apple_vision_crop(render, locks=locks, proposal=proposal)
    assert not calls


def test_refuses_multiple_markers_and_empty_ocr_evidence():
    markdown, primary, independent, render = sample()
    with pytest.raises(recovery.RecoveryRefused, match="ambiguous-page-marker"):
        diagnose(markdown + b"\n<!-- page: 2 -->\n", primary, independent, render)
    result = diagnose(markdown, replace(primary, rows=[]), independent, render)
    assert result.status == "needs-review" and result.rejected == ({"reason": "empty-ocr-evidence"},)


def test_independently_recovered_number_repasses_whole_page_gate(tmp_path, monkeypatch):
    image = tmp_path / "scan.png"
    render = Image.new("RGB", (1000, 1200), "white")
    draw = ImageDraw.Draw(render)
    text = "A standalone chapter title"
    font = ImageFont.load_default(size=22)
    draw.text((120, 240), text, fill="black", font=font)
    draw.text((790, 240), "17", fill="black", font=font)
    render.save(image)

    def box(x, value):
        x0, y0, x1, y1 = draw.textbbox((x, 240), value, font=font)
        return audit.Box(value, .99, x0/1000, y0/1200, x1/1000, y1/1200)

    title, number = box(120, text), box(790, "17")
    primary = audit.TextEvidence("apple-vision-cached", [[title]])
    independent = audit.TextEvidence("tesseract", [[title, number]])
    markdown = f"<!-- page:1 -->\n\n{text}\n\n![Page](assets/page-001.png)\n".encode()
    render_bytes = image.read_bytes()
    locks = lock(markdown, primary, independent, render_bytes)
    assert audit.assess_scan(primary, independent, markdown.decode(), image)["reader_action"] == "retain"
    proposal, = recovery.diagnose(markdown, primary, independent, locks=locks).proposals
    stub_vision(monkeypatch, "17", bbox={"x": .16, "y": .18, "width": .68, "height": .64})
    proof = recovery.read_apple_vision_crop(render_bytes, locks=locks, proposal=proposal)
    fixed, verdict = audit.verify_scan_recovery(markdown, primary, independent, image, locks=locks, proofs=[proof])
    assert fixed == markdown.replace(text.encode(), (text + " 17").encode())
    assert verdict["reader_action"] == "omit"

    draw.line((300, 400, 520, 400), fill="black", width=1)
    render.save(image)
    locks = lock(markdown, primary, independent, image.read_bytes())
    proposal, = recovery.diagnose(markdown, primary, independent, locks=locks).proposals
    proof = recovery.read_apple_vision_crop(image.read_bytes(), locks=locks, proposal=proposal)
    with pytest.raises(recovery.RecoveryRefused, match="whole-page-gates"):
        audit.verify_scan_recovery(markdown, primary, independent, image, locks=locks, proofs=[proof])
