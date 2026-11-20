#!/usr/bin/env python3
"""Read-only, page-local visual omission gate for page-hybrid reader projections."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import unquote
import xml.etree.ElementTree as ET

SCHEMA = "pdf-hybrid-reader-visual-audit"
VERSION = "3.0.0"
PREVIOUS_VERSION = "2.0.0"
LEGACY_VERSION = "1.0.0"
SCAN_CACHE = Path("evidence/reader-scan-ocr")
MARKER = re.compile(r"^[ \t]*<!--\s*page:(\d+)\s*-->[ \t]*\r?$", re.M)
IMAGE = re.compile(r"!\[[^\]\n]*\]\([ \t]*(?:<([^>\n]+)>|([^\s)]+))(?:[ \t]+[^)\n]*)?\)")
PAGE_IMAGE = re.compile(r"(?:^|/)page-(\d+)\.png$", re.I)
SHA = re.compile(r"[0-9a-f]{64}")
DECISIONS = ("candidate-remove", "crop-required", "retain", "needs-review")
GATES = ("text_confident", "character_coverage", "reading_order_coverage", "visual_clear")


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def package_path(root: Path, ref: str) -> Path:
    path = (root / ref).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"evidence path outside package: {ref}")
    return path


@dataclass(frozen=True)
class Box:
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if not (self.text and 0 <= self.confidence <= 1 and
                0 <= self.x0 < self.x1 <= 1 and 0 <= self.y0 < self.y1 <= 1):
            raise ValueError("invalid text geometry or confidence")


@dataclass(frozen=True)
class TextEvidence:
    engine: str
    rows: list[list[Box]]
    layout_uncertain: bool = False


def norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c)[0] in "LNMS")


def strict_norm(text: str) -> str:
    # Do not discard decimal points, signs, punctuation or case in the new gate.
    return "".join(c for c in unicodedata.normalize("NFC", text) if not c.isspace())


def markdown_text(body: str, *, strict: bool = False) -> str:
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = IMAGE.sub("", body)  # Alt text cannot prove transcription.
    body = re.sub(r"\[([^\]\n]+)\]\([^)\n]+\)", r"\1", body)
    body = re.sub(r"</?[A-Za-z][^>\n]*>", "", body)
    body = re.sub(r"^[ \t]{0,3}\d+[.)][ \t]+", "", body, flags=re.M)
    body = re.sub(r"^[ \t]*(?:#{1,6}[ \t]+)?第\s*\d+\s*页(?:视觉补充)?[ \t]*$", "", body, flags=re.M)
    if strict:
        body = re.sub(r"^[ \t]{0,3}(?:#{1,6}|[-+*])[ \t]+", "", body, flags=re.M)
    return (strict_norm if strict else norm)(html.unescape(body))


def footer_number(row: list[Box]) -> bool:
    # Poppler may make the dash, number and closing dash separate word boxes.
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", "".join(b.text for b in row)))
    return (min(b.y0 for b in row) >= .92 and max(b.x1 for b in row) - min(b.x0 for b in row) <= .065
            and re.fullmatch(r"(?:[0-9]{1,4}|[-–—·•][0-9]{1,4}[-–—·•]|\([0-9]{1,4}\))", value) is not None)


def coverage(evidence: TextEvidence, markdown: str, *, strict: bool = False) -> tuple[dict, dict]:
    rows = [row for row in evidence.rows if not footer_number(row)]
    body = markdown_text(markdown, strict=strict)
    normalization = strict_norm if strict else norm
    lines = [normalization("".join(box.text for box in row)) for row in rows]
    lines = [line for line in lines if line]
    reference = "".join(lines)
    shared = sum((Counter(reference) & Counter(body)).values())
    matched = cursor = 0
    for line in lines:
        start = body.find(line, cursor)
        if start >= 0:
            matched += 1
            cursor = start + len(line)
    weights = [(len(norm(b.text)), b.confidence) for row in rows for b in row]
    count = sum(n for n, _ in weights)
    confidence = sum(n * c for n, c in weights) / max(1, count)
    low = sum(n for n, c in weights if c < .55) / max(1, count)
    very_low_row = any(sum(len(norm(b.text)) for b in row if b.confidence < .35) >= 3 for row in rows)
    return (
        {"text_confident": count >= 6 and confidence >= .80 and low <= .08 and not very_low_row,
         "character_coverage": bool(reference) and shared == len(reference) and (not strict or len(body) == len(reference)),
         "reading_order_coverage": bool(lines) and matched == len(lines) and not evidence.layout_uncertain
         and (not strict or body == reference)},
        {"engine": evidence.engine, "normalized_characters": len(reference), "shared_characters": shared,
         "matched_rows": matched, "total_rows": len(lines), "weighted_confidence": round(confidence, 6),
         "low_confidence_fraction": round(low, 6), "very_low_confidence_row": very_low_row,
         "layout_uncertain": evidence.layout_uncertain, "ignored_footer_numbers": len(evidence.rows) - len(rows)},
    )


def apple_rows(payload: dict) -> TextEvidence:
    try:
        from .ocr_markdown_structure import group_rows, parse_candidates
    except ImportError:  # direct script
        from ocr_markdown_structure import group_rows, parse_candidates
    candidates = parse_candidates(payload)
    if payload.get("candidate_count") != len(candidates):
        raise ValueError("OCR candidate count mismatch")
    rows = [[Box(c.text, float(c.confidence or 0), c.x, max(0, 1 - c.y - c.height),
                 min(1, c.x + c.width), 1 - c.y) for c in row]
            for row in group_rows(candidates)]
    split = sum(any(b.x0 - a.x1 > .12 for a, b in zip(row, row[1:])) for row in rows)
    vertical = any(b.y1 - b.y0 > .055 and b.y1 - b.y0 > 2 * (b.x1 - b.x0) and len(norm(b.text)) > 2
                   for row in rows for b in row)
    return TextEvidence("apple-vision-cached", rows, split >= 3 or vertical)


def native_rows(payload: dict, *, strict: bool = False) -> TextEvidence:
    layout = payload["layout"]
    if payload["status"] != "passed" or layout["status"] != "complete":
        raise ValueError("native text layer is not reliable")
    xml = ET.fromstring(layout["command"]["stdout"])
    page, = xml.findall(".//{*}page")
    w, h = float(page.attrib["width"]), float(page.attrib["height"])
    rows = []
    for line in page.findall(".//{*}line"):
        row = [Box("".join(word.itertext()), 1., float(word.attrib["xMin"]) / w,
                   float(word.attrib["yMin"]) / h, float(word.attrib["xMax"]) / w,
                   float(word.attrib["yMax"]) / h) for word in line.findall("{*}word")]
        if row:
            rows.append(row)
    layout_text = norm("".join(b.text for row in rows for b in row))
    visible = norm(payload["text"])
    if not rows or not visible or '\ufffd' in payload["text"] or Counter(layout_text) != Counter(visible):
        raise ValueError("native coordinate and plain text layers disagree")
    if strict and strict_norm("".join(b.text for row in rows for b in row)) != strict_norm(payload["text"]):
        raise ValueError("native coordinate and plain text layers differ in content or order")
    return TextEvidence("poppler-bbox-cached", rows)


def _rules(mask, minimum: int) -> list[tuple[int, int, int, int]]:
    """Find genuine continuous thin rules, including one-pixel lines."""
    import numpy as np

    prefix = np.pad(np.cumsum(mask, axis=1, dtype=np.uint16), ((0, 0), (1, 0)))
    hits = prefix[:, minimum:] - prefix[:, :-minimum] == minimum
    out = []
    for y in np.flatnonzero(hits.any(axis=1)):
        span = np.flatnonzero(hits[y])
        for group in np.split(span, 1 + np.flatnonzero(np.diff(span) > 1)):
            x0, x1 = int(group[0]), int(group[-1]) + minimum
            if out and out[-1][3] == y and abs(out[-1][0] - x0) < 5 and abs(out[-1][2] - x1) < 5:
                a, b, c, _ = out.pop()
                out.append((min(a, x0), b, max(c, x1), int(y) + 1))
            else:
                out.append((x0, int(y), x1, int(y) + 1))
    return out


def visual_metrics(image_path: Path, evidence: TextEvidence, *, scan_guard: bool = False) -> dict:
    import numpy as np
    from PIL import Image, ImageFilter

    with Image.open(image_path) as source:
        source.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        rgba = source.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
    gray = rgb.convert("L")
    lum = np.asarray(gray, dtype=np.int16)
    h, w = lum.shape
    if min(w, h) < 160:
        raise ValueError("analysis render too small")
    background = np.asarray(gray.filter(ImageFilter.GaussianBlur(max(5, min(w, h) / 100))), dtype=np.int16)
    contrast = background - lum
    r, g, b = (np.asarray(channel) for channel in rgb.split())
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    tint = int(np.percentile(chroma[::8, ::8], 60))
    colored = chroma >= max(24, tint + 16)
    ink = (contrast >= 16) | (lum <= min(170, int(np.percentile(lum[::8, ::8], 88)) - 38)) | colored
    rule_ink = (contrast >= 8) | ink
    covered = np.zeros((h, w), dtype=bool)
    pad = max(2, round(min(w, h) * .004))
    body = [b for row in evidence.rows if not footer_number(row) for b in row]
    for row in evidence.rows:
        for b in row:
            x0, x1 = max(0, int(b.x0*w)-pad), min(w, math.ceil(b.x1*w)+pad)
            y0, y1 = max(0, int(b.y0*h)-pad), min(h, math.ceil(b.y1*h)+pad)
            covered[y0:y1, x0:x1] = True

    horizontal = _rules(rule_ink, max(24, round(w * .075)))
    vertical = [(y0, x0, y1, x1) for x0, y0, x1, y1 in _rules(rule_ink.T, max(24, round(h * .055)))]
    def crosses(a, b):
        return a[0] <= b[2]+2 and b[0] <= a[2]+2 and a[1] <= b[3]+2 and b[1] <= a[3]+2

    intersections = [(a, b) for a in horizontal for b in vertical if crosses(a, b)
                     and .025*w < b[0] < .975*w and .025*h < a[1] < .975*h]
    top = min((b.y0 for b in body), default=0) * h
    bottom = max((b.y1 for b in body), default=1) * h
    ignored = []
    for rules, opposite, axis, length in ((horizontal, vertical, 1, h), (vertical, horizontal, 0, w)):
        side_rules = [a for a in rules if a[axis + 2]-a[axis] <= .012 * length
                      and not any(crosses(a, b) for b in opposite)
                      and (a[axis + 2] <= .024*length or a[axis] >= .976*length)]
        if axis == 1:
            for edge in ([a for a in rules if a[3] <= min(top, .13*h)],
                         [a for a in rules if a[1] >= max(bottom, .87*h)]):
                if (len(edge) == 1 and edge[0][3]-edge[0][1] <= .007*h and
                        edge[0][2]-edge[0][0] >= .25*w and not any(crosses(edge[0], b) for b in opposite)):
                    side_rules.extend(edge)
        ignored.extend(side_rules)
    for x0, y0, x1, y1 in ignored:
        ink[max(0,y0-pad):y1+pad, max(0,x0-pad):x1+pad] = False
        if scan_guard:
            rule_ink[max(0,y0-pad):y1+pad, max(0,x0-pad):x1+pad] = False
    non_text = ink & ~covered
    remaining_rules = [a for a in horizontal + vertical if a not in ignored]
    # A text bbox is not a certificate about overlapped rules. Keep long
    # interior geometry even when a word box happens to cover its pixels.
    lines = remaining_rules if scan_guard else [a for a in remaining_rules if (
        rule_ink[a[1]:a[3], a[0]:a[2]] & ~covered[a[1]:a[3], a[0]:a[2]]).sum() >= 24]
    residual = int(non_text.sum())
    tile = max(12, round(min(w, h) * .018))
    padded = np.pad(non_text, ((0, -h % tile), (0, -w % tile)))
    counts = padded.reshape(padded.shape[0]//tile, tile, padded.shape[1]//tile, tile).sum(axis=(1, 3))
    dense = counts >= max(8, math.ceil(tile*tile*.065))
    dense_count = int(dense.sum())
    yy, xx = np.where(dense)
    boxes = list(lines)
    if dense_count:
        boxes.append((int(xx.min())*tile, int(yy.min())*tile, min(w, (int(xx.max())+1)*tile), min(h, (int(yy.max())+1)*tile)))
    for a, b in intersections:
        boxes.extend((a, b))
    bounds = ([min(a[0] for a in boxes)/w, min(a[1] for a in boxes)/h,
               max(a[2] for a in boxes)/w, max(a[3] for a in boxes)/h] if boxes else None)
    peak = int(counts.max())
    clear = not (lines or intersections or dense_count or peak >= 12 or residual > max(20, .00012*w*h))
    # A dense thin horizontal strip can be a whole omitted OCR row. A compact
    # photographic/illustrated patch needs vertical depth, or actual rules.
    dense_depth = int(yy.max() - yy.min() + 1) if dense_count else 0
    structured = bool(intersections or (dense_depth >= 3 and dense_count >= 12 and peak >= .28*tile*tile))
    result = {"clear": clear, "structured": structured, "non_text_ink_pixels": residual,
            "dense_tiles": dense_count, "peak_tile_ink": peak, "non_text_rule_count": len(lines),
            "intersecting_rules": len(intersections), "ignored_edge_rules": len(set(ignored)),
            "visual_bounds": [round(a, 6) for a in bounds] if bounds else None}
    if scan_guard:
        # Use lower contrast only as a veto, and require a localized coherent
        # cluster, not a white background or a handful of scanner specks.
        faint = np.pad(rule_ink & ~covered, ((0, -h % tile), (0, -w % tile)))
        faint_peak = int(faint.reshape(faint.shape[0]//tile, tile, faint.shape[1]//tile, tile)
                         .sum(axis=(1, 3)).max())
        result.update(faint_residual_peak=faint_peak, clear=clear and faint_peak < max(12, .04*tile*tile))
    return result


def classify(evidence: TextEvidence, markdown: str, image_path: Path, *, route_hint: bool = False,
             strict: bool = False) -> dict:
    gates, metrics = coverage(evidence, markdown, strict=strict)
    visual = visual_metrics(image_path, evidence)
    gates["visual_clear"] = visual["clear"] and not route_hint
    if all(gates.values()):
        decision, reason = "candidate-remove", "text-covered-no-visual-increment"
    elif not all(gates[name] for name in GATES[:3]):
        decision, reason = "needs-review", "unproven-text-or-reading-order-coverage"
    elif visual["structured"] and visual["visual_bounds"]:
        x0, y0, x1, y1 = visual["visual_bounds"]
        outside_rows = sum(all(b.x1 < x0 or b.x0 > x1 or b.y1 < y0 or b.y0 > y1 for b in row)
                           for row in evidence.rows if not footer_number(row))
        if outside_rows >= 2 and (x1-x0)*(y1-y0) <= .60:
            decision, reason = "crop-required", "localized-visual-structure"
        else:
            decision, reason = "retain", "integrated-visual-structure"
    else:
        decision, reason = "needs-review", "ambiguous-visual-increment"
    return {"decision": decision, "reason_code": reason, "gates": gates, "text": metrics, "visual": visual}


def source_fingerprint(root: Path, source: Path | None, index_sha: str, selected: list[int]) -> tuple[Path, str, str]:
    if source is not None and source.is_file():
        return source.resolve(), digest(source), "live-pdf"
    # Historical packages can move independently of the PDFs. An explicit source
    # mismatch is still an error; never search parent directories for replacements.
    accepted = read_json(root / "reports/validation-result.json")
    recorded = Path(accepted["source"])
    fingerprint = accepted.get("source_sha256")
    if (accepted.get("validator") != "pdf-hybrid-output-validator" or accepted.get("status") != "passed"
            or accepted.get("actual_full_pdf_coverage") is not True or accepted.get("index_sha256") != index_sha
            or not isinstance(fingerprint, str) or not SHA.fullmatch(fingerprint)
            or selected != accepted.get("selected_pages")
            or selected != list(range(1, (accepted.get("total_pages") or 0) + 1))
            or not accepted.get("checks") or not all(flag is True for flag in accepted["checks"].values())
            or (source is not None and source.resolve() != recorded.resolve())):
        raise ValueError("missing live PDF and no matching independently accepted source snapshot")
    if recorded.is_file():
        if digest(recorded) != fingerprint:
            raise ValueError("live PDF disagrees with accepted source fingerprint")
        return recorded.resolve(), fingerprint, "live-pdf"
    return recorded, fingerprint, "accepted-package-snapshot"


def cached_apple(root: Path, route: dict, page: int, source_sha: str, asset_sha: str) -> tuple[TextEvidence, dict]:
    p = f"p{page:03d}"
    evidence_root = package_path(root, route["visual_run_root"]) / "evidence"
    result_path = package_path(root, str(evidence_root / f"page-result-{p}.json"))
    request_path = package_path(root, str(evidence_root / f"request-evidence-{p}.json"))
    ocr_path = package_path(root, str(evidence_root / f"apple-vision-{p}.json"))
    result, request = read_json(result_path), read_json(request_path)
    raw = ocr_path.read_bytes()
    if (result.get("status") != "passed" or result.get("page") != page or result.get("source_sha256") != source_sha
            or (result.get("asset") or {}).get("sha256") != asset_sha or request.get("image_sha256") != asset_sha
            or result.get("ocr_evidence_sha256") != sha(raw)):
        raise ValueError("cached OCR is not locked to this render and source")
    evidence = apple_rows(json.loads(raw))
    if result.get("review_resolution"):
        evidence = TextEvidence(evidence.engine, evidence.rows, True)
    return evidence, {str(path.relative_to(root)): value for path, value in
                      ((result_path, digest(result_path)), (request_path, digest(request_path)), (ocr_path, sha(raw)))}


def cached_native(root: Path, route: dict, page: int, source_sha: str, *, strict: bool = False) -> tuple[TextEvidence, dict]:
    path = package_path(root, route["native_evidence"])
    raw = path.read_bytes()
    payload = json.loads(raw)
    if (route.get("native_evidence_sha256") != sha(raw) or payload.get("page") != page
            or payload.get("source_sha256") != source_sha):
        raise ValueError("native evidence fingerprint mismatch")
    if strict and (route.get("signals") or {}).get("pdf_vector_objects") != payload.get("pdf_vector_objects"):
        raise ValueError("route vector signals do not match their source evidence")
    return native_rows(payload, strict=strict), {str(path.relative_to(root)): sha(raw)}


def _no_vector_candidates(signals: dict) -> bool:
    vector = signals.get("pdf_vector_objects") or {}
    counts = vector.get("counts") or {}
    return (vector.get("version") == "pdfplumber-vector-v1" and vector.get("status") == "complete"
            and all(type(counts.get(kind)) is int and counts[kind] == 0 for kind in ("line", "rect", "curve")))


def _is_scan_candidate(route: dict) -> bool:
    images = route.get("image_stats") or {}
    signals = route.get("signals") or {}
    # Never use Poppler's hidden/unverified text layer as a second OCR.
    return (route.get("route") == "visual-only"
            and route.get("recognition_route") in {"apple-vision-ocr", "apple-vision-ocr-fallback"}
            and images.get("image_count", 0) >= 1
            and (signals.get("page_image_required") is True or images.get("max_area_ratio", 0) >= .85))


def _disagreement(evidence: TextEvidence, body: str) -> dict:
    """Find the first conflict, including a terminal or repeated missing span."""
    ref = "".join(strict_norm("".join(b.text for b in row)) for row in evidence.rows if not footer_number(row))
    actual = markdown_text(body, strict=True)
    head = next((i for i, (a, b) in enumerate(zip(ref, actual)) if a != b), min(len(ref), len(actual)))
    room = min(len(ref), len(actual)) - head
    tail = next((i for i in range(room) if ref[-i-1] != actual[-i-1]), room)
    return {"equal": ref == actual, "equal_prefix": head, "equal_suffix": tail,
            "evidence_range": [head, len(ref)-tail], "final_range": [head, len(actual)-tail],
            "evidence_preview": ref[head:min(len(ref)-tail, head+24)],
            "final_preview": actual[head:min(len(actual)-tail, head+24)]}


def assess_scan(primary: TextEvidence, second: TextEvidence, body: str, image: Path) -> dict:
    if primary.engine != "apple-vision-cached" or second.engine != "tesseract":
        raise ValueError("scan comparison requires independent recognition families")
    primary_gates, primary_text = coverage(primary, body, strict=True)
    second_gates, second_text = coverage(second, body, strict=True)
    gates = {name: primary_gates[name] and second_gates[name] for name in GATES[:3]}
    # A union of the masks can erase missed ink, and whole-line boxes can
    # straddle unrelated marks. Require independent clearance on both masks.
    primary_visual = visual_metrics(image, primary, scan_guard=True)
    second_visual = visual_metrics(image, second, scan_guard=True)
    gates["visual_clear"] = primary_visual["clear"] and second_visual["clear"]
    decision, reason, action = "needs-review", "scan-text-disagreement", "retain"
    if all(gates.values()):
        decision, reason, action = "candidate-remove", "scan-dual-ocr-text-covered-no-visual-increment", "omit"
    elif all(gates[name] for name in GATES[:3]):
        reason = "scan-visual-increment-unproven"
    return {"decision": decision, "reason_code": reason, "reader_action": action, "gates": gates,
            "independent_text_evidence": True, "visual": primary_visual, "text": primary_text,
            "scan_comparison": {"primary_gates": primary_gates, "independent_gates": second_gates,
                                "independent_text": second_text, "independent_visual": second_visual,
                                "primary_conflict": _disagreement(primary, body),
                                "independent_conflict": _disagreement(second, body)}}


def verify_scan_recovery(original: bytes, primary: TextEvidence, second: TextEvidence, image: Path,
                         *, locks, proofs) -> tuple[bytes, dict]:
    """Pre-final guard: corroborate each suffix, then recheck the entire page.

    A caller must own the page stage/human approval and keep the original OCR
    and render. This function has no reader-export or package write access.
    """
    try:
        from . import pdf_scan_digit_recovery as recovery
    except ImportError:  # direct script
        import pdf_scan_digit_recovery as recovery
    recovered = recovery.apply_recovery(original, primary, second, locks=locks,
                                        render_bytes=image.read_bytes(), proofs=proofs)
    proposals = recovery.diagnose(original, primary, second, locks=locks).proposals
    rows = [list(row) for row in primary.rows]
    by_proposal = {proof.proposal_sha256: proof for proof in proofs}
    for proposal in proposals:
        proof = by_proposal[proposal.proposal_sha256]
        digit, = proof.payload["candidates"]
        rows[proposal.primary_row].append(Box(digit["text"], digit["confidence"], *proposal.digit_bbox))
    # The added tokens on the Apple side come exclusively from the independent
    # crop reads (not from the Tesseract text that proposed the correction).
    augmented = TextEvidence(primary.engine, rows, primary.layout_uncertain)
    verdict = assess_scan(augmented, second, recovered.decode("utf-8"), image)
    if digest(image) != locks.render_sha256 or verdict["reader_action"] != "omit":
        raise recovery.RecoveryRefused("recovered-page-did-not-pass-whole-page-gates")
    return recovered, verdict


def _audit_scan(root: Path, page: int, body: str, source_sha: str, image: Path,
                primary: TextEvidence, record: dict, *, collect_scan: bool) -> dict:
    try:
        from . import pdf_scan_ocr
    except ImportError:  # direct script
        import pdf_scan_ocr
    ref = (SCAN_CACHE / f"p{page:03d}.json").as_posix()
    record.update(decision="needs-review", reader_action="retain", independent_text_evidence=False,
                  reason_code="scan-independent-ocr-unavailable",
                  gates={**record["gates"], "character_coverage": False, "reading_order_coverage": False})
    try:
        # Do not resolve a cache symlink to another page or write through one.
        path = package_path(root, ref)
        if path != root / ref:
            raise ValueError("scan cache may not be symlinked")
        primary_ref = next(r for r in record["evidence_sha256"] if r.endswith(f"/apple-vision-p{page:03d}.json"))
        raw = read_json(package_path(root, primary_ref))
        if raw.get("engine") != "Apple Vision VNRecognizeTextRequest" or raw.get("status") != "complete":
            raise ValueError("unverified primary OCR engine")
        binding = {"source_sha": source_sha, "page": page, "asset_sha": record["asset_sha256"],
                   "primary_sha": record["evidence_sha256"][primary_ref], "final_sha": record["body_sha256"]}
        if collect_scan and not path.exists():
            pdf_scan_ocr.collect(image, path, **binding)
        evidence_sha = digest(path)
        payload = pdf_scan_ocr.load(path, **binding)
        if digest(path) != evidence_sha:
            raise ValueError("scan OCR evidence changed during validation")
        record["evidence_sha256"][ref] = evidence_sha
        record["scan_evidence_status"] = payload["status"]
        record["scan_evidence_reason"] = payload["reason_code"]
        if payload["status"] not in {"ok", "layout_uncertain"}:
            return record
        if payload.get("engine") != "tesseract":
            raise ValueError("not an independent OCR family")
        second = TextEvidence("tesseract", [[Box(**word) for word in row] for row in payload["rows"]],
                              payload["status"] == "layout_uncertain")
        record.update(assess_scan(primary, second, body, image))
        if record["reason_code"] == "scan-text-disagreement":
            try:
                from . import pdf_scan_digit_recovery as recovery
            except ImportError:  # direct script
                import pdf_scan_digit_recovery as recovery
            # Diagnostics only. The reader path never executes another OCR
            # to supplement text and may not write to a finalized page unit.
            try:
                locks = recovery.PageLocks(source_sha, page, record["asset_sha256"], record["body_sha256"],
                                           recovery.rows_sha256(primary), recovery.rows_sha256(second))
                candidates = recovery.diagnose(body.encode(), primary, second, locks=locks)
                record["digit_recovery"] = {"version": recovery.VERSION, "locks": asdict(locks), **asdict(candidates)}
                # JSON round-trips normalise tuples to arrays; revalidation
                # compares report data exactly, including these diagnostics.
                record["digit_recovery"] = json.loads(json.dumps(record["digit_recovery"]))
            except recovery.RecoveryRefused as exc:
                record["digit_recovery"] = {"version": recovery.VERSION, "status": "needs-review", "reason": str(exc)}
    except FileNotFoundError:
        record["scan_evidence_status"] = "missing"
    except (OSError, ValueError, KeyError, StopIteration, TypeError, AttributeError, RuntimeError) as exc:
        record["scan_evidence_status"] = "invalid"
        record["evidence_diagnostics"] = [f"scan-ocr:{type(exc).__name__}"]
    return record


def _audit_unrendered_native(root: Path, page: int, body: str, source_sha: str, route: dict, record: dict) -> dict:
    """Verify a page already assembled without a render; do not silently approve it."""
    record.update(decision="needs-review", reader_action="no-reference-unverified",
                  reason_code="unproven-native-fast-path", gates=dict.fromkeys(GATES, False),
                  independent_text_evidence=False)
    signals = route.get("signals") or {}
    rendered = route.get("rendered_structure") or {}
    if (route.get("route") != "native-only" or route.get("native_body_sufficient") is not True
            or (route.get("image_stats") or {}).get("image_count") != 0 or signals.get("pdfimages_probe")
            or signals.get("page_image_required") is True
            or (signals.get("native_text_visibility") or {}).get("status") not in {None, "unknown", "visible"}
            or not _no_vector_candidates(signals)
            or rendered.get("status") not in {"not-run", "passed"} or rendered.get("visual_structure_present")
            or (route.get("text_signals") or {}).get("present")
            or (route.get("table_detection") or {}).get("present")):
        return record
    try:
        evidence, hashes = cached_native(root, route, page, source_sha, strict=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError) as exc:
        record["evidence_diagnostics"] = [f"cached_native:{type(exc).__name__}"]
        return record
    gates, metrics = coverage(evidence, body, strict=True)
    gates["visual_clear"] = True
    record.update(gates=gates, text=metrics, evidence_sha256=hashes, independent_text_evidence=True)
    if all(gates.values()):
        record.update(decision="retain", reader_action="omit", reason_code="verified-native-no-page-image")
    else:
        record["reason_code"] = "unproven-native-final-text-or-reading-order"
    return record


def audit_page(root: Path, page: int, body: str, source_sha: str, *, version: str = VERSION,
               collect_scan: bool = False) -> dict:
    strict = version != LEGACY_VERSION
    route_path = package_path(root, f"evidence/page-routes/p{page:03d}.json")
    raw_route = route_path.read_bytes()
    route = json.loads(raw_route)
    unit = package_path(root, f"page-units/p{page:03d}.md")
    if (route.get("page") != page or route.get("status") != "passed" or route.get("source_sha256") != source_sha
            or route.get("page_unit_sha256") != digest(unit)):
        raise ValueError(f"unverified page unit or route evidence: page {page}")
    refs = [unquote(m.group(1) or m.group(2)) for m in IMAGE.finditer(body)]
    refs = [ref for ref in refs if PAGE_IMAGE.search(ref)]
    record = {"page": page, "asset": None, "asset_sha256": None, "gates": {},
              "route_sha256": sha(raw_route), "body_sha256": sha(body.encode()),
              "evidence_sha256": {}, "decision": "retain", "reason_code": "no-page-image-reference"}
    if not refs:
        return _audit_unrendered_native(root, page, body, source_sha, route, record) if strict else record
    asset = f"assets/page-{page:03d}.png"
    if refs != [asset]:
        raise ValueError(f"unexpected, duplicate or cross-page reference: page {page}")
    image_path = package_path(root, asset)
    asset_sha = digest(image_path)
    if route.get("visual_asset_sha256") != asset_sha:
        raise ValueError(f"page render hash mismatch: page {page}")
    record.update(asset=asset, asset_sha256=asset_sha, decision="needs-review",
                  reason_code="text-or-geometry-evidence-unavailable", gates=dict.fromkeys(GATES, False))
    rendered = route.get("rendered_structure") or {}
    route_hint = rendered.get("visual_structure_present") is True
    if strict:
        record.update(reader_action="retain", independent_text_evidence=False)
        route_hint = (route_hint or (route.get("table_detection") or {}).get("present") is True
                      or not _no_vector_candidates(route.get("signals") or {}))
    images = route.get("image_stats") or {}
    methods = []
    signals = route.get("signals") or {}
    if (route.get("native_body_sufficient") is True and images.get("image_count") == 0
            and not signals.get("pdfimages_probe")
            and (not strict or ((signals.get("native_text_visibility") or {}).get("status") in {None, "unknown", "visible"}
                                and signals.get("page_image_required") is not True))):
        methods.append(cached_native)
    # No image-object count test for scanned pages, including PDF with a hidden OCR layer.
    methods.append(cached_apple)
    errors = []
    for method in methods:
        try:
            if method is cached_native:
                evidence, hashes = method(root, route, page, source_sha, strict=strict)
            else:
                evidence, hashes = method(root, route, page, source_sha, asset_sha)
            assessment = classify(evidence, body, image_path, route_hint=route_hint, strict=strict)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, ET.ParseError) as exc:
            errors.append(f"{method.__name__}:{type(exc).__name__}")
            continue
        record.update(assessment, evidence_sha256=hashes)
        if strict:
            record["independent_text_evidence"] = method is cached_native
        if strict and record["decision"] == "candidate-remove":
            if method is cached_native:
                record["reader_action"] = "omit"
            else:
                # Matching the OCR that supplied the Markdown cannot establish
                # that the OCR itself saw every visible source character/line.
                record.update(decision="needs-review", reason_code="independent-text-evidence-missing")
        # Dense unexplained ink alone can be a missed text column (e.g. a
        # scanned contents page). Only positive route/rule evidence skips OCR.
        if (version == VERSION and method is cached_apple and _is_scan_candidate(route)
                and not route_hint and not assessment["visual"]["intersecting_rules"]):
            _audit_scan(root, page, body, source_sha, image_path, evidence, record, collect_scan=collect_scan)
        # Never let a second engine erase missing characters or visual evidence
        # detected by an already trusted native layer. Fall back only when that
        # layer itself is unavailable/inconsistent (caught above).
        break
    if errors:
        record["evidence_diagnostics"] = errors
    return record


def audit_package(package: Path, source: Path | None = None, selected_pages: list[int] | None = None,
                  *, version: str | None = None, collect_scan: bool = False) -> dict:
    root = package.resolve()
    if version is None:
        lock = root / "acceptance.json"
        version = (read_json(lock).get("reader_visual_audit_version") if lock.is_file() else None) or VERSION
    if version not in {VERSION, PREVIOUS_VERSION, LEGACY_VERSION}:
        raise ValueError("unsupported page-hybrid reader audit version")
    content = package_path(root, "index.md").read_bytes()
    index_sha = sha(content)
    markdown = content.decode("utf-8")
    markers = list(MARKER.finditer(markdown))
    sequence = [int(marker.group(1)) for marker in markers]
    if (not sequence or sequence != sorted(set(sequence)) or min(sequence) < 1
            or (selected_pages is not None and selected_pages != sequence)):
        raise ValueError("missing, unordered, duplicate or unexpected Markdown page markers")
    if any(PAGE_IMAGE.search(unquote(m.group(1) or m.group(2))) for m in IMAGE.finditer(markdown[:markers[0].start()])):
        raise ValueError("page render reference outside any page")
    source_path, source_sha, source_mode = source_fingerprint(root, source, index_sha, sequence)
    pages = [audit_page(root, page, markdown[marker.end():markers[i+1].start() if i+1 < len(markers) else len(markdown)], source_sha,
                        version=version, collect_scan=collect_scan)
             for i, (page, marker) in enumerate(zip(sequence, markers))]
    by_decision = {key: [record["page"] for record in pages if record["asset"] and record["decision"] == key]
                   for key in DECISIONS}
    no_ref = [record["page"] for record in pages if not record["asset"]]
    report = {"schema": SCHEMA, "version": version, "status": "complete", "source": str(source_path),
            "source_sha256": source_sha, "source_verification": source_mode, "index_sha256": index_sha,
            "selected_pages": sequence, "pages": pages, "reference_count": len(pages)-len(no_ref),
            "page_numbers_by_decision": by_decision, "counts": {key: len(v) for key, v in by_decision.items()},
            "no_page_image_reference": no_ref}
    if version != LEGACY_VERSION:
        report["verified_no_page_image"] = [row["page"] for row in pages if row["reader_action"] == "omit" and not row["asset"]]
        report["unverified_no_page_image"] = [row["page"] for row in pages if row["reader_action"] == "no-reference-unverified"]
    return report


def validate_audit(package: Path, source: Path | None, report: dict, selected_pages: list[int] | None = None) -> dict:
    """Re-read section bytes and re-evaluate local evidence, never trust stored gates."""
    try:
        if report.get("version") not in {VERSION, PREVIOUS_VERSION, LEGACY_VERSION}:
            raise ValueError("unsupported page-hybrid reader audit version")
        acceptance = package.resolve() / "acceptance.json"
        if acceptance.is_file():
            required = read_json(acceptance).get("reader_visual_audit_version")
            if required is not None and report.get("version") != required:
                raise ValueError("reader audit version differs from the run's acceptance policy")
        actual = audit_package(package, source, selected_pages, version=report.get("version"))
        # An intact PDF can later be moved (or restored). Compare its fingerprint,
        # not whether this particular revalidation could reach the historical path.
        ignore = {"source_verification"}
        if {k: v for k, v in actual.items() if k not in ignore} != {k: v for k, v in report.items() if k not in ignore}:
            raise ValueError("stored classifications or input hashes differ from re-read evidence")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"status": "failed", "diagnostics": [str(exc)]}
    return {"status": "passed", "diagnostics": [], "pages_checked": len(actual["pages"]),
            "source_verification": actual["source_verification"]}


def _write_report(path: Path, data: dict) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        temporary = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--source", type=Path, help="exact source PDF; defaults to the accepted package record")
    parser.add_argument("--report", type=Path, required=True, help="audit output outside the original package")
    parser.add_argument("--verify", action="store_true", help="independently re-evaluate an existing report")
    parser.add_argument("--reader-copy", type=Path, help="with --verify, create or verify a separate reader copy")
    parser.add_argument("--verification-report", type=Path, help="with --verify, write separate acceptance evidence")
    args = parser.parse_args()
    root = args.package.resolve()
    if args.report.resolve().is_relative_to(root):
        parser.error("report must be outside the original engineering package")
    if args.report.is_symlink() or args.report.suffix.lower() != ".json":
        parser.error("report must be a non-symlink .json file")
    if args.source is not None and args.report.resolve() == args.source.resolve():
        parser.error("report must not overwrite the source")
    if (args.reader_copy or args.verification_report) and not args.verify:
        parser.error("--reader-copy and --verification-report require --verify")
    if args.reader_copy and args.reader_copy.resolve().is_relative_to(root):
        parser.error("reader copy must be outside the original engineering package")
    if args.verification_report:
        path = args.verification_report
        if (path.exists() or path.is_symlink() or path.suffix.lower() != ".json"
                or path.resolve().is_relative_to(root) or not path.parent.is_dir()
                or (args.reader_copy and path.resolve().is_relative_to(args.reader_copy.resolve()))):
            parser.error("verification report must be a new .json file outside both packages")
    try:
        if args.verify:
            report = read_json(args.report)
            result = validate_audit(root, args.source, report)
            result["audit_sha256"] = digest(args.report)
            if result["status"] == "passed" and args.reader_copy:
                try:
                    from . import pdf_hybrid_reader_export as reader
                except ImportError:  # direct script
                    import pdf_hybrid_reader_export as reader
                if not args.reader_copy.exists():
                    export = reader.export_reader(root, report, args.reader_copy)
                else:
                    export = {"status": "passed"}
                check = reader.validate_reader(root, report, args.reader_copy) if export["status"] == "passed" else export
                result.update(status=check["status"], reader_validation=check,
                              semantic_status="needs-review" if report["counts"]["needs-review"] or report["counts"]["crop-required"] else "resolved")
            if args.verification_report:
                _write_report(args.verification_report, result)
            if "reader_validation" in result and result["status"] == "passed":
                compact = result["reader_validation"]
                result["reader_validation"] = {"status": compact["status"], "destination": compact["destination"],
                                               "removed_count": len(compact["removed_pages"]), "retained_files": len(compact["retained_files"])}
        else:
            result = audit_package(root, args.source)
            if args.report.exists():
                raise ValueError("report already exists; use --verify or a new path")
            _write_report(args.report, result)
            result = {k: result[k] for k in ("status", "source_verification", "reference_count", "counts")}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"passed", "complete"} else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(1, f"audit failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
