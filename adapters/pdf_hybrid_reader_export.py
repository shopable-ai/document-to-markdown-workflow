"""Build and independently verify a reader copy of a page-hybrid PDF run.

The caller owns the audit report and engineering acceptance. This adapter only
applies its four hard gates to an exact, hash-locked Markdown projection.

The reader layout supports inline links to index.md and flat images under
assets/. Unsupported local targets and ambiguous link syntax fail closed.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


REQUIRED_GATES = ("text_confident", "character_coverage", "reading_order_coverage", "visual_clear")
DECISIONS = ("candidate-remove", "crop-required", "retain", "needs-review")
PAGE_MARKER = re.compile(r"<!--\s*page\s*:\s*([1-9][0-9]*)\s*-->[ \t]*")
SHA256 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".bmp", ".tif", ".tiff"}
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
LABEL = r"(?:\\[^\r\n]|[^\[\]\\])*"
STANDALONE_PAGE_IMAGE = re.compile(rf"!\[{LABEL}\]\((assets/page-[0-9]+\.png)\)[ \t]*")
# Deliberately accept only unambiguous inline links. Unconsumed link syntax,
# reference definitions and raw HTML fail closed rather than dropping a target.
TOKEN = re.compile(
    r"(?P<comment><!--)|(?P<escape>\\[!\[\]\\<>`])|(?P<ticks>`+)"
    rf"|(?P<link>(?P<image>!)?\[{LABEL}\]\([ \t]*"
    r"(?:<(?P<angle>[^<>\\]*)>|(?P<bare>[^\s()<>\\]*))"
    r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*\))"
    r"|(?P<autolink><(?:https?://|mailto:)[^<>\s]+>)"
    r"|(?P<unsupported>!\[|\[\[|\]\(|\]\[|\]:|</?[A-Za-z]|<![A-Za-z]|<\?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Reference:
    target: str
    line: int
    page: int | None
    image: bool


@dataclass(frozen=True)
class Projection:
    index: bytes
    files: dict[str, str]  # export-relative path -> source SHA-256
    removed_pages: list[int]


def _file(root: Path, relative: str) -> Path:
    """Read only regular files without symlinked components below root."""
    path = root
    for part in PurePosixPath(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"symlink not supported: {relative} ({root})")
    if not path.is_file():
        raise ValueError(f"missing or non-regular file: {relative} ({root})")
    return path


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _local_target(raw: str, image: bool) -> str | None:
    if (raw != raw.strip() or any(ord(c) < 32 for c in raw)
            or re.search(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);", raw)):
        raise ValueError(f"unsupported whitespace, control character or entity in link target: {raw!r}")
    parts = urlsplit(raw)
    if not image and parts.scheme in {"http", "https", "mailto"}:
        return None
    path = unquote(parts.path, errors="strict")
    if (parts.scheme or parts.netloc or path.startswith("/") or "\\" in path
            or any(ord(c) < 32 for c in path)):
        raise ValueError(f"unsupported Markdown link target: {raw!r}")
    if not path and not image:
        return "index.md"  # self/fragment link, no file to copy
    relative = PurePosixPath(path)
    if ".." not in relative.parts and not path.endswith(("/", "/.")):
        if not image and relative.parts == ("index.md",):
            return "index.md"
        if (len(relative.parts) == 2 and relative.parts[0] == "assets"
                and relative.suffix.lower() in IMAGE_SUFFIXES):
            return relative.as_posix()
    raise ValueError(f"unsupported package-relative link target: {raw!r}")


def _scan(lines: list[str]) -> tuple[list[int], list[Reference]]:
    """Read page boundaries and supported links without rewriting any Markdown."""
    pages: list[int] = []
    refs: list[Reference] = []
    page = None
    fence = ""
    in_comment = False
    for number, original in enumerate(lines):
        line = original.rstrip("\r\n")
        if fence:
            closing = FENCE.fullmatch(line)
            if (closing and closing[1].startswith(fence)
                    and not closing[2].strip()):
                fence = ""
            continue
        if not in_comment:
            marker = PAGE_MARKER.fullmatch(line)
            if marker:
                page = int(marker[1])
                pages.append(page)
                continue
            opening = FENCE.fullmatch(line)
            if opening and not line.startswith(" ") and (opening[1][0] == "~" or "`" not in opening[2]):
                fence = opening[1]
                continue

        position = 0
        while position < len(line):
            if in_comment:
                end = line.find("-->", position)
                if end < 0:
                    break
                if line[end + 3:].strip():
                    raise ValueError(f"unsupported inline HTML comment on line {number + 1}")
                in_comment = False
                break
            token = TOKEN.search(line, position)
            if token is None:
                break
            position = token.end()
            if token["comment"]:
                if line[:token.start()].strip():
                    raise ValueError(f"unsupported inline HTML comment on line {number + 1}")
                in_comment = True
            elif token["ticks"]:
                ticks = token["ticks"]
                end = re.search(rf"(?<!`){ticks}(?!`)", line[position:])
                if not end:
                    raise ValueError(f"unsupported unpaired inline code delimiter on line {number + 1}")
                position += end.end()
            elif token["link"]:
                if re.match(r" {0,3}\t| {4}", line):
                    raise ValueError(f"ambiguous indented Markdown link on line {number + 1}")
                raw = token["angle"] if token["angle"] is not None else token["bare"]
                image = bool(token["image"])
                target = _local_target(raw, image)
                if target is not None:
                    refs.append(Reference(target, number, page, image))
            elif token["unsupported"]:
                raise ValueError(f"unsupported Markdown link or HTML syntax on line {number + 1}")
    if fence or in_comment:
        raise ValueError("unclosed Markdown fence or comment")
    return pages, refs


def _check_hash(expected: object, actual: str, label: str) -> None:
    if not isinstance(expected, str) or not SHA256.fullmatch(expected) or expected.lower() != actual:
        raise ValueError(f"SHA-256 mismatch or missing hash: {label}")


def _project(package: Path, report: dict) -> Projection:
    index = _file(package, "index.md").read_bytes()
    if not isinstance(report, dict):
        raise ValueError("reader audit report must be an object")
    _check_hash(report.get("index_sha256"), hashlib.sha256(index).hexdigest(), "source index.md")
    # Preserve original line endings, including CRLF, and all other UTF-8 bytes.
    lines = re.split(r"(?<=\n)|(?<=\r)(?!\n)", index.decode("utf-8"))
    pages, refs = _scan(lines)
    records = report.get("pages")
    if not isinstance(records, list) or not records:
        raise ValueError("report.pages must be a nonempty ordered list")
    if any(not isinstance(row, dict) or type(row.get("page")) is not int for row in records):
        raise ValueError("each report page must be an integer")
    if pages != [row["page"] for row in records] or pages != sorted(set(pages)):
        raise ValueError("report page sequence does not match unique ordered index page markers")

    refs_by_target: dict[str, list[Reference]] = defaultdict(list)
    for ref in refs:
        refs_by_target[ref.target].append(ref)

    hashes: dict[str, str] = {}

    def source_hash(relative: str) -> str:
        if relative not in hashes:
            file = _file(package, relative)
            if not file.stat().st_size:
                raise ValueError(f"empty source asset: {relative}")
            hashes[relative] = _sha256(file)
        return hashes[relative]

    omitted: set[str] = set()
    removed_lines: set[int] = set()
    removed_pages: list[int] = []
    for row in records:
        page = row["page"]
        decision = row.get("decision")
        gates = row.get("gates")
        if decision not in DECISIONS or not isinstance(gates, dict):
            raise ValueError(f"page {page}: invalid decision or gates")
        expected_asset = f"assets/page-{page:03d}.png"
        if "asset" not in row or "asset_sha256" not in row:
            raise ValueError(f"page {page}: missing asset or asset_sha256 field")
        asset = row["asset"]
        if asset is not None:
            if asset != expected_asset:
                raise ValueError(f"page {page}: asset is not the canonical page path {expected_asset}")
            _check_hash(row["asset_sha256"], source_hash(asset), asset)
        elif row["asset_sha256"] is not None or expected_asset in refs_by_target:
            raise ValueError(f"page {page}: missing asset or inconsistent asset hash")
        if decision != "candidate-remove":
            continue
        if report.get("version") in {"2.0.0", "3.0.0"} and (
            row.get("reader_action") != "omit" or row.get("independent_text_evidence") is not True
        ):
            raise ValueError(f"page {page}: omission requires independent source text evidence")
        failed = [name for name in REQUIRED_GATES if gates.get(name) is not True]
        if failed:
            raise ValueError(f"page {page}: candidate did not pass required gates: {', '.join(failed)}")
        matches = refs_by_target.get(asset, []) if asset is not None else []
        if len(matches) != 1 or not matches[0].image or matches[0].page != page:
            raise ValueError(f"page {page}: candidate must have exactly one image reference owned by this page")
        number = matches[0].line
        standalone = STANDALONE_PAGE_IMAGE.fullmatch(lines[number].rstrip("\r\n"))
        if not standalone or standalone[1] != asset:
            raise ValueError(f"page {page}: candidate is not an exact standalone page image line")
        # Reject attachment to a surrounding paragraph/container (or setext heading).
        neighbors = lines[max(0, number - 1):number] + lines[number + 1:number + 2]
        if any(line.strip(" \t\r\n") and not PAGE_MARKER.fullmatch(line.rstrip("\r\n"))
               for line in neighbors):
            raise ValueError(f"page {page}: standalone image must be separated from surrounding body")
        removed_lines.add(number)
        removed_pages.append(page)
        omitted.add(asset)

    files = {target: source_hash(target) for target in refs_by_target
             if target != "index.md" and target not in omitted}
    # An alternate spelling or hardlink must not keep the same candidate alive.
    if omitted and files:
        def identity(relative: str) -> tuple[int, int]:
            stat = _file(package, relative).stat()
            return stat.st_dev, stat.st_ino

        omitted_identities = {identity(asset) for asset in omitted}
        for target in files:
            if identity(target) in omitted_identities:
                raise ValueError(f"retained link aliases an omitted page image: {target}")

    projected = "".join(line for number, line in enumerate(lines) if number not in removed_lines).encode("utf-8")
    return Projection(projected, files, removed_pages)


def _paths(package: Path, destination: Path, *, writing: bool) -> tuple[Path, Path]:
    source = package.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source package must be a directory")
    target = destination.expanduser()
    if target.is_symlink():
        raise ValueError("reader destination cannot be a symlink")
    target = target.resolve()
    # Check identities too: case-insensitive filesystems may expose the source
    # directory under a spelling that Path.is_relative_to does not recognize.
    if (source.is_relative_to(target)
            or (target.exists() and any(target.samefile(parent) for parent in [source, *source.parents]))):
        raise ValueError("reader destination cannot contain or equal the source package")
    if writing and (target.is_relative_to(source)
            or any(parent.exists() and parent.samefile(source) for parent in target.parents)):
        raise ValueError("reader export cannot write inside the source package")
    return source, target


def _verify(destination: Path, projection: Projection) -> None:
    expected = {"index.md", "assets"} if projection.files else {"index.md"}
    if {path.name for path in destination.iterdir()} != expected:
        raise ValueError("reader destination has missing or unexpected top-level entries")
    index = _file(destination, "index.md")
    if index.stat().st_nlink != 1 or index.read_bytes() != projection.index:
        raise ValueError("reader index.md differs from the exact independent projection")
    if projection.files:
        assets = destination / "assets"
        if assets.is_symlink() or not assets.is_dir():
            raise ValueError("reader assets must be an ordinary directory")
        if {f"assets/{path.name}" for path in assets.iterdir()} != projection.files.keys():
            raise ValueError("reader assets contain missing or unexpected files")
        for relative, expected_hash in projection.files.items():
            file = _file(destination, relative)
            if file.stat().st_nlink != 1 or _sha256(file) != expected_hash:
                raise ValueError(f"reader asset is not an independent copy or SHA-256 differs: {relative}")


def _passed(destination: Path, projection: Projection) -> dict:
    return {"status": "passed", "diagnostics": [], "destination": str(destination),
            "index_sha256": hashlib.sha256(projection.index).hexdigest(),
            "removed_pages": projection.removed_pages, "retained_files": sorted(projection.files)}


def validate_reader(package: Path, report: dict, destination: Path) -> dict:
    """Verify an existing copy read-only, including a published copy inside the run."""
    try:
        source, target = _paths(package, destination, writing=False)
        projection = _project(source, report)
        _verify(target, projection)
        return _passed(target, projection)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"status": "failed", "diagnostics": [str(exc)]}


def export_reader(package: Path, report: dict, destination: Path) -> dict:
    """Create a verified reader copy at a new destination with an existing parent."""
    created = False
    try:
        source, target = _paths(package, destination, writing=True)
        if target.exists():
            raise ValueError(f"reader destination already exists: {target}")
        projection = _project(source, report)
        target.mkdir()  # reserve a new directory; never overwrite an existing copy
        created = True
        if projection.files:
            (target / "assets").mkdir()
        for relative in projection.files:
            with _file(source, relative).open("rb") as original, (target / relative).open("xb") as copy:
                shutil.copyfileobj(original, copy)
        with (target / "index.md").open("xb") as index:
            index.write(projection.index)
        _verify(target, projection)
        return _passed(target, projection)
    except (OSError, ValueError, RuntimeError) as exc:
        diagnostics = [str(exc)]
        if created:
            try:
                shutil.rmtree(target)
            except OSError as cleanup_error:
                diagnostics.append(f"could not clean up incomplete reader copy: {cleanup_error}")
        return {"status": "failed", "diagnostics": diagnostics}
