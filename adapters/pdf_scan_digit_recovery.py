"""Page-local digit proposals and an opt-in, pre-final recovery guard.

Callers verify PDF/page/render provenance and supply complete OCR rows, with
normalized top-left (x0, y0, x1, y1) and confidence in [0, 1]. The visual
audit's TextEvidence and Box types are compatible. Hashes detect drift, not
forged provenance: accept proofs only from a trusted local crop reader. This
is neither a full-page OCR completeness check nor an image omission gate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Protocol, Sequence
import unicodedata


VERSION = "1.0.0"
PROOF_SCHEMA = "pdf-scan-digit-apple-vision-crop"
MAX_PROPOSALS = 2
_SHA = re.compile(r"[0-9a-f]{64}")
_DIGITS = re.compile(r"[0-9]{1,4}")
_MARKER = re.compile(r"[ \t]*<!--[ \t]*page:[ \t]*(\d+)[ \t]*-->[ \t]*")
_IMAGE_LINE = re.compile(r" {0,3}!\[[^\[\]\r\n]*\]\((?:<[^<>\r\n]+>|[^\s()<>]+)\)[ \t]*")
_UNSAFE_BODY = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029\\|\x60*_\[\]#&]")
_APPLE_ENGINE = "Apple Vision VNRecognizeTextRequest"
_APPLE_ROW_ENGINES = {"apple-vision", "apple-vision-cached", _APPLE_ENGINE.lower()}
_PROPOSAL_ENGINES = {"tesseract", "tesseract-tsv", "tesseract-cached", "tesseract-tsv-cached"}
_BBox = tuple[float, float, float, float]
_PixelBox = tuple[int, int, int, int]


class RecoveryRefused(ValueError):
    """A required integrity, geometry or independent evidence gate failed."""


class _Box(Protocol):
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float


class _Rows(Protocol):
    engine: str
    rows: Sequence[Sequence[_Box]]
    layout_uncertain: bool


@dataclass(frozen=True)
class PageLocks:
    source_sha256: str  # From the caller's verified PDF provenance.
    page: int
    render_sha256: str
    markdown_sha256: str
    primary_rows_sha256: str
    independent_rows_sha256: str


@dataclass(frozen=True)
class DigitProposal:
    line_number: int  # Physical, 1-based. exact_line excludes its line ending.
    exact_line: str
    anchor: str
    digits: str
    digit_bbox: _BBox
    crop_bbox: _BBox
    anchor_bbox: _BBox  # Tesseract's left text box (or union of words).
    confidence: float
    primary_row: int  # OCR row indices are 0-based.
    independent_anchor_row: int
    independent_digit_row: int
    proposal_sha256: str


@dataclass(frozen=True)
class Diagnosis:
    status: str  # proposals | no-proposals | needs-review
    proposals: tuple[DigitProposal, ...]
    rejected: tuple[dict, ...]


@dataclass(frozen=True)
class CropProof:
    proposal_sha256: str
    crop_sha256: str
    crop_pixels: _PixelBox
    adapter_sha256: str
    payload: dict
    payload_sha256: str
    wall_seconds: float
    schema: str = PROOF_SCHEMA
    version: str = VERSION


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                           separators=(",", ":")).encode("utf-8"))


def _bbox(box: _Box) -> _BBox:
    return float(box.x0), float(box.y0), float(box.x1), float(box.y1)


def _valid_box(bbox: _BBox) -> bool:
    x0, y0, x1, y1 = bbox
    return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def rows_sha256(evidence: _Rows) -> str:
    """Hash the engine, layout flag, exact row order/text/confidence/geometry."""
    if type(evidence.layout_uncertain) is not bool or not isinstance(evidence.engine, str):
        raise RecoveryRefused("invalid-evidence-metadata")
    rows = []
    for row in evidence.rows:
        if not row:
            raise RecoveryRefused("empty-ocr-row")
        items = []
        for box in row:
            bbox = _bbox(box)
            if not (isinstance(box.text, str) and box.text.strip() and
                    type(box.confidence) in (int, float) and 0 <= box.confidence <= 1 and _valid_box(bbox)):
                raise RecoveryRefused("invalid-ocr-box")
            items.append([box.text, float(box.confidence), bbox])
        rows.append(items)
    return _json_sha([evidence.engine, evidence.layout_uncertain, rows])


def _match_text(text: str) -> str:
    # No casefold, compatibility fold, substitutions or fuzzy matching.
    return "".join(c for c in unicodedata.normalize("NFC", text)
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def _matches_anchor(text: str, anchor: str) -> bool:
    return text == anchor or (text.startswith(anchor) and _DIGITS.fullmatch(text[len(anchor):]) is not None)


def _physical_lines(markdown: bytes, page: int) -> tuple[list[str], list[tuple[str, bool]]]:
    try:
        text = markdown.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecoveryRefused("markdown-not-utf8") from exc
    if not text or "\0" in text or text.startswith("\ufeff") or re.search(r"\r(?!\n)", text):
        raise RecoveryRefused("unsupported-markdown")
    # Split only at LF; LF/CRLF and all existing bytes are preserved.
    lines = text.split("\n")
    metadata = []
    markers = []
    fence = None
    comment = False
    frontmatter = lines[0].rstrip("\r") == "---"
    for index, physical in enumerate(lines):
        line = physical.removesuffix("\r")
        stripped = line.strip(" \t")
        if frontmatter:
            frontmatter = not (index > 0 and stripped in {"---", "..."})
            metadata.append(("", False))
            continue
        if not fence and not comment and (marker := _MARKER.fullmatch(line)):
            markers.append(int(marker[1]))
            metadata.append(("", False))
            continue
        if not fence and (comment or "<!--" in line or "-->" in line):
            # Only standalone HTML comments, never mixed body/markup.
            if comment and "-->" not in line:
                metadata.append(("", False))
                continue
            if not comment and stripped.startswith("<!--") and "-->" not in stripped:
                comment = True
            elif ((comment or stripped.startswith("<!--")) and stripped.count("-->") == 1
                  and stripped.endswith("-->")):
                comment = False
            else:
                raise RecoveryRefused("unsupported-inline-comment")
            metadata.append(("", False))
            continue
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line):
                fence = None
            metadata.append(("", False))
            continue
        if opener := re.match(r"^ {0,3}(\x60{3,}|~{3,})", line):
            fence = opener[1]
            metadata.append(("", False))
            continue
        if _IMAGE_LINE.fullmatch(line):
            metadata.append(("", False))
            continue
        if "<" in line or ">" in line:
            raise RecoveryRefused("unsupported-markdown-html-or-blockquote")
        # Plain text and the formatter's single-level TOC bullet are supported.
        # Other body lines can veto duplicates but are not editable.
        body = re.sub(r"^- +", "", stripped)
        safe = bool(re.match(r" {0,3}[^ \t]", line) and not _UNSAFE_BODY.search(body) and
                    not re.match(r"(?:[-+]|\d+[.)])[ \t]", body) and
                    not re.fullmatch(r"第\s*\d+\s*页(?:视觉补充)?", body))
        metadata.append((_match_text(body), safe))
    if fence or comment or frontmatter:
        raise RecoveryRefused("unclosed-markdown-block")
    if markers and markers != [page]:
        raise RecoveryRefused("ambiguous-page-marker")
    return lines, metadata


def _looks_split(lines: list[str], metadata: list[tuple[str, bool]], n: int) -> bool:
    for step in (-1, 1):
        j = n + step
        while 0 <= j < len(lines) and not lines[j].strip():
            j += step
        if 0 <= j < len(lines) and _DIGITS.fullmatch(metadata[j][0]):
            return True
    return n + 1 < len(lines) and re.fullmatch(r" {0,3}(?:-+|=+)[ \t\r]*", lines[n + 1]) is not None


def _union(boxes: Sequence[_Box]) -> _BBox:
    return min(b.x0 for b in boxes), min(b.y0 for b in boxes), max(b.x1 for b in boxes), max(b.y1 for b in boxes)


def _overlap(a: _BBox, b: _BBox, axis: int) -> float:
    return max(0., min(a[axis + 2], b[axis + 2]) - max(a[axis], b[axis]))


def _relative_overlap(a: _BBox, b: _BBox, axis: int) -> float:
    return _overlap(a, b, axis) / max(a[axis + 2] - a[axis], b[axis + 2] - b[axis])


def _near_row(row: Sequence[_Box], digit: _BBox) -> bool:
    return any(_overlap(_bbox(b), digit, 1) > 0 for b in row)


def _row_text(row: Sequence[_Box]) -> str:
    return _match_text("".join(b.text for b in sorted(row, key=lambda b: b.x0)))


def _left_anchor(row: Sequence[_Box], digit: _BBox) -> tuple[str, _BBox]:
    text_boxes = sorted((box for box in row if _match_text(box.text)), key=lambda box: box.x0)
    # Require a real horizontal gap; a row union alone cannot establish y alignment.
    if (not text_boxes or any(b.x1 > digit[0] - .006 for b in row) or
            any(b.confidence < .80 or _relative_overlap(_bbox(b), digit, 1) < .50 for b in text_boxes)):
        raise RecoveryRefused("not-isolated-right-of-a-reliable-anchor")
    if any(b.x0 - a.x1 > .12 or _relative_overlap(_bbox(a), _bbox(b), 0) > .25
           for a, b in zip(text_boxes, text_boxes[1:])):
        raise RecoveryRefused("unreliable-left-anchor-geometry")
    anchor = _row_text(row)
    if len(anchor) < 3 or not anchor.isalnum() or not any(c.isalpha() for c in anchor) or anchor[-1].isnumeric():
        raise RecoveryRefused("unsupported-left-anchor")
    return anchor, _union(text_boxes)


def _proposal_hash(locks: PageLocks, fields: dict) -> str:
    return _json_sha([VERSION, asdict(locks), fields])


def diagnose(markdown: bytes, primary: _Rows, independent: _Rows, *, locks: PageLocks) -> Diagnosis:
    """Return page-local proposals; never call OCR or modify Markdown here."""
    if (type(locks.page) is not int or locks.page < 1 or not all(
            isinstance(value, str) and _SHA.fullmatch(value) for key, value in asdict(locks).items() if key != "page")):
        raise RecoveryRefused("invalid-page-locks")
    if _sha(markdown) != locks.markdown_sha256:
        raise RecoveryRefused("markdown-hash-mismatch")
    if rows_sha256(primary) != locks.primary_rows_sha256 or rows_sha256(independent) != locks.independent_rows_sha256:
        raise RecoveryRefused("ocr-rows-hash-mismatch")
    if primary.engine.lower() not in _APPLE_ROW_ENGINES or independent.engine.lower() not in _PROPOSAL_ENGINES:
        raise RecoveryRefused("unsupported-or-nonindependent-row-engines")
    if not primary.rows or not independent.rows:
        return Diagnosis("needs-review", (), ({"reason": "empty-ocr-evidence"},))
    if primary.layout_uncertain or independent.layout_uncertain:
        return Diagnosis("needs-review", (), ({"reason": "uncertain-ocr-layout"},))
    lines, metadata = _physical_lines(markdown, locks.page)
    proposals = []
    rejected = []
    for digit_row, row in enumerate(independent.rows):
        for offset, box in enumerate(row):
            if not _DIGITS.fullmatch(box.text):
                continue
            digit = _bbox(box)
            try:
                if not (.06 <= digit[1] < digit[3] <= .94 and digit[2] - digit[0] <= .10 and
                        digit[3] - digit[1] <= .06 and box.confidence >= .90):
                    raise RecoveryRefused("digit-confidence-geometry-or-margin")
                p_rows = [i for i, r in enumerate(primary.rows) if _near_row(r, digit)]
                # A digit-only Tesseract row can be a separate TSV line/block.
                other_rows = [[*r[:offset], *r[offset + 1:]] if i == digit_row else r
                              for i, r in enumerate(independent.rows)]
                i_rows = [i for i, r in enumerate(other_rows) if _near_row(r, digit)]
                if len(p_rows) != 1 or len(i_rows) != 1 or (len(row) > 1 and i_rows != [digit_row]):
                    raise RecoveryRefused("nonunique-row-overlap")
                p, = p_rows
                i, = i_rows
                anchor, anchor_box = _left_anchor(other_rows[i], digit)
                primary_anchor, primary_box = _left_anchor(primary.rows[p], digit)
                if primary_anchor != anchor or _relative_overlap(primary_box, anchor_box, 0) < .60:
                    raise RecoveryRefused("left-anchors-disagree")
                if (sum(_matches_anchor(_row_text(r), anchor) for r in primary.rows) != 1 or
                        sum(_matches_anchor(_row_text(r), anchor) for r in other_rows) != 1):
                    raise RecoveryRefused("nonunique-ocr-anchor")
                m_lines = [n for n, (text, _) in enumerate(metadata) if _matches_anchor(text, anchor)]
                if len(m_lines) != 1:
                    raise RecoveryRefused("markdown-anchor-missing-split-or-repeated")
                n, = m_lines
                if metadata[n] != (anchor, True) or _looks_split(lines, metadata, n):
                    raise RecoveryRefused("unsafe-or-already-numbered-markdown-line")
                pad_x = max(.003, .18 * (digit[2] - digit[0]))
                pad_y = max(.003, .20 * (digit[3] - digit[1]))
                crop = (digit[0] - pad_x, digit[1] - pad_y, digit[2] + pad_x, digit[3] + pad_y)
                if not _valid_box(crop) or any(
                        _overlap(crop, _bbox(b), 0) and _overlap(crop, _bbox(b), 1)
                        for r in (*primary.rows, *other_rows) for b in r):
                    raise RecoveryRefused("crop-touches-neighbor-or-page-edge")
                fields = dict(line_number=n + 1, exact_line=lines[n].removesuffix("\r"), anchor=anchor,
                              digits=box.text, digit_bbox=digit, crop_bbox=crop, anchor_bbox=anchor_box,
                              confidence=float(box.confidence), primary_row=p, independent_anchor_row=i,
                              independent_digit_row=digit_row)
                proposals.append(DigitProposal(**fields, proposal_sha256=_proposal_hash(locks, fields)))
            except RecoveryRefused as exc:
                rejected.append({"reason": str(exc), "digits": box.text, "digit_bbox": digit, "independent_row": digit_row})
    proposals.sort(key=lambda proposal: proposal.line_number)
    if (len(proposals) > MAX_PROPOSALS or len({p.line_number for p in proposals}) != len(proposals) or
            any(a.digit_bbox[1] >= b.digit_bbox[1] for a, b in zip(proposals, proposals[1:]))):
        rejected.append({"reason": "too-many-conflicting-or-out-of-order-proposals", "count": len(proposals)})
        return Diagnosis("needs-review", (), tuple(rejected))
    return Diagnosis("proposals" if proposals else "needs-review" if rejected else "no-proposals",
                     tuple(proposals), tuple(rejected))


def _crop_png(render_bytes: bytes, locks: PageLocks, proposal: DigitProposal) -> tuple[bytes, _PixelBox, tuple[int, int]]:
    if _sha(render_bytes) != locks.render_sha256:
        raise RecoveryRefused("render-hash-mismatch")
    try:
        from PIL import Image
    except ImportError as exc:
        raise RecoveryRefused("pillow-unavailable") from exc
    x0, y0, x1, y1 = proposal.crop_bbox
    try:
        with Image.open(io.BytesIO(render_bytes)) as render:
            if render.format != "PNG" or getattr(render, "n_frames", 1) != 1 or render.getexif().get(274, 1) != 1:
                raise RecoveryRefused("unsupported-page-render")
            w, h = render.size
            pixels = math.floor(x0 * w), math.floor(y0 * h), math.ceil(x1 * w), math.ceil(y1 * h)
            if not (0 < pixels[0] < pixels[2] < w and 0 < pixels[1] < pixels[3] < h):
                raise RecoveryRefused("crop-outside-page-interior")
            if min(pixels[2] - pixels[0], pixels[3] - pixels[1]) < 8:
                raise RecoveryRefused("crop-resolution-too-low")
            with render.crop(pixels) as region, region.convert("L") as gray:
                # Do not corroborate a clipped character. Conservative for
                # dark, low-contrast, noisy or inverted scan backgrounds.
                cw, ch = gray.size
                if gray.getextrema()[0] > 140:
                    raise RecoveryRefused("crop-has-no-clear-ink")
                edges = [(0, 0, cw, 1), (0, ch - 1, cw, ch), (0, 0, 1, ch), (cw - 1, 0, cw, ch)]
                for edge in edges:
                    with gray.crop(edge) as border:
                        if border.getextrema()[0] < 190:
                            raise RecoveryRefused("crop-edge-ink-or-dark-background")
                out = io.BytesIO()
                region.save(out, format="PNG")
            return out.getvalue(), pixels, (w, h)
    except (OSError, Image.DecompressionBombError) as exc:
        raise RecoveryRefused("unreadable-page-render") from exc


def _vision_adapter():
    try:
        if __package__:
            from . import apple_vision_pdf_to_markdown
        else:  # Direct adapter imports.
            import apple_vision_pdf_to_markdown
    except ImportError as exc:
        raise RecoveryRefused("apple-vision-adapter-unavailable") from exc
    return apple_vision_pdf_to_markdown


def read_apple_vision_crop(render_bytes: bytes, *, locks: PageLocks, proposal: DigitProposal,
                           timeout_seconds: int = 10) -> CropProof:
    """Run a fresh Apple Vision read on a tight crop, without any expected text.

    Includes the raw local payload and OCR wall time, even on disagreement. No
    crop is left on disk. Disagreements cannot pass apply_recovery.
    """
    fields = asdict(proposal)
    if (fields.pop("proposal_sha256") != _proposal_hash(locks, fields) or not _valid_box(proposal.crop_bbox) or
            not _valid_box(proposal.digit_bbox) or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30):
        raise RecoveryRefused("invalid-crop-request")
    crop, pixels, _ = _crop_png(render_bytes, locks, proposal)
    adapter = _vision_adapter()
    try:
        adapter_sha = _sha(adapter.APPLE_VISION_SCRIPT.read_bytes())
        with tempfile.TemporaryDirectory(prefix="pdf-scan-digit-") as tmp:
            path = Path(tmp) / "crop.png"
            path.write_bytes(crop)
            payload, cost = adapter._run_apple_vision(path, timeout_seconds)
        if _sha(adapter.APPLE_VISION_SCRIPT.read_bytes()) != adapter_sha:
            raise RecoveryRefused("apple-vision-adapter-changed")
    except (OSError, adapter.OcrRunFailure) as exc:
        raise RecoveryRefused("apple-vision-crop-unavailable") from exc
    return CropProof(proposal.proposal_sha256, _sha(crop), pixels, adapter_sha, payload, _json_sha(payload), cost)


def _verify_proof(proof: CropProof, proposal: DigitProposal, render: bytes, locks: PageLocks) -> None:
    crop, pixels, (w, h) = _crop_png(render, locks, proposal)
    try:
        adapter_sha = _sha(_vision_adapter().APPLE_VISION_SCRIPT.read_bytes())
        if (proof.schema != PROOF_SCHEMA or proof.version != VERSION or proof.adapter_sha256 != adapter_sha or
                proof.proposal_sha256 != proposal.proposal_sha256 or proof.crop_sha256 != _sha(crop) or
                tuple(proof.crop_pixels) != pixels or _json_sha(proof.payload) != proof.payload_sha256 or
                not math.isfinite(proof.wall_seconds) or proof.wall_seconds < 0):
            raise RecoveryRefused("crop-proof-lock-mismatch")
        raw = proof.payload
        if (raw["engine"] != _APPLE_ENGINE or raw["status"] != "complete" or
                not isinstance(raw["os"], str) or not raw["os"].strip() or
                raw["languages"] != ["zh-Hans", "en-US"] or raw["uses_language_correction"] is not True or
                type(raw["candidate_count"]) is not int or raw["candidate_count"] != 1 or
                not isinstance(raw["candidates"], list) or len(raw["candidates"]) != 1):
            raise RecoveryRefused("not-an-independent-single-crop-reading")
        candidate, = raw["candidates"]
        confidence = candidate["confidence"]
        if candidate["text"] != proposal.digits or type(confidence) not in (int, float) or not .90 <= confidence <= 1:
            raise RecoveryRefused("crop-digits-disagree-or-low-confidence")
        bbox = candidate["bbox"]
        x, y, bw, bh = (bbox[k] for k in ("x", "y", "width", "height"))
        if not _valid_box((x, y, x + bw, y + bh)):
            raise RecoveryRefused("invalid-crop-reading-geometry")
        left, top, right, bottom = pixels
        mapped = ((left + x * (right - left)) / w, (bottom - (y + bh) * (bottom - top)) / h,
                  (left + (x + bw) * (right - left)) / w, (bottom - y * (bottom - top)) / h)
        if any(_relative_overlap(mapped, proposal.digit_bbox, axis) < .55 for axis in (0, 1)):
            raise RecoveryRefused("crop-reading-does-not-localize-to-proposed-digits")
    except RecoveryRefused:
        raise
    except (AttributeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise RecoveryRefused("invalid-crop-proof") from exc


def apply_recovery(markdown: bytes, primary: _Rows, independent: _Rows, *, locks: PageLocks,
                   render_bytes: bytes, proofs: Sequence[CropProof]) -> bytes:
    """Return bytes with confirmed suffixes inserted, or raise before any edit.

    No production writes. The caller owns the stage gate, final page structural
    checks and independent semantic validation. Do not use in reader projection.
    """
    diagnostic = diagnose(markdown, primary, independent, locks=locks)
    if diagnostic.status != "proposals":
        raise RecoveryRefused("no-safe-digit-proposals")
    by_proposal = {proof.proposal_sha256: proof for proof in proofs}
    if len(proofs) != len(by_proposal) or set(by_proposal) != {p.proposal_sha256 for p in diagnostic.proposals}:
        raise RecoveryRefused("missing-duplicate-or-unrelated-crop-proof")
    for proposal in diagnostic.proposals:
        _verify_proof(by_proposal[proposal.proposal_sha256], proposal, render_bytes, locks)
    lines = markdown.split(b"\n")
    for proposal in diagnostic.proposals:
        n = proposal.line_number - 1
        end = len(lines[n].rstrip(b" \t\r"))
        lines[n] = lines[n][:end] + b" " + proposal.digits.encode("ascii") + lines[n][end:]
    return b"\n".join(lines)
