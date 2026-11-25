#!/usr/bin/env python3
"""Shared helpers for readable output package directory names."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import tempfile
import unicodedata


_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DASH_RUN = re.compile(r"-{2,}")
_SPACE_RUN = re.compile(r"\s{2,}")


def readable_package_stem(value: str, *, fallback: str = "source", max_chars: int = 48) -> str:
    """Keep source names readable while stripping filesystem-hostile characters."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _FORBIDDEN_FILENAME_CHARS.sub("-", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = _DASH_RUN.sub("-", text)
    text = text.strip(" .-_")
    if max_chars > 0:
        text = text[:max_chars].rstrip(" .-_")
    return text or fallback


def package_timestamp(when: datetime | None = None) -> str:
    """Return a compact local-style timestamp for user-facing package names."""

    instant = when or datetime.now().astimezone()
    if instant.tzinfo is None:
        instant = instant.astimezone()
    return instant.strftime("%Y%m%d-%H%M%S")


def resolve_package_target(
    output_root: Path,
    source: Path,
    *,
    when: datetime | None = None,
    extra_label: str | None = None,
    include_timestamp: bool = False,
) -> tuple[str, Path]:
    """Return the first readable, non-conflicting package directory name.

    Default package roots stay title-first and human-readable.  Timestamps are
    opt-in for the narrow cases that truly need a fresh chronological label.
    """

    parts = [readable_package_stem(source.stem)]
    if include_timestamp:
        parts.append(package_timestamp(when))
    if extra_label:
        label = readable_package_stem(extra_label, fallback="", max_chars=24)
        if label:
            parts.append(label)
    stem = "-".join(part for part in parts if part)
    candidate = output_root / stem
    ordinal = 1
    while candidate.exists():
        ordinal += 1
        candidate = output_root / f"{stem}-{ordinal:02d}"
    return candidate.name, candidate


def readable_markdown_stem(value: str, *, fallback: str = "document", max_chars: int = 120) -> str:
    """Keep reader Markdown filenames close to the source title."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _FORBIDDEN_FILENAME_CHARS.sub(" ", text)
    text = _SPACE_RUN.sub(" ", text)
    text = text.strip(" .")
    if max_chars > 0:
        text = text[:max_chars].rstrip(" .")
    return text or fallback


def source_markdown_filename(source: Path | str, *, fallback: str = "document", max_chars: int = 120) -> str:
    """Return a readable Markdown filename derived from the source title."""

    value = source.stem if isinstance(source, Path) else str(source)
    return f"{readable_markdown_stem(value, fallback=fallback, max_chars=max_chars)}.md"


def book_markdown_filename(source: Path) -> str:
    """Name an editable PDF copy without colliding with package Markdown."""

    title = unicodedata.normalize("NFKC", source.stem).strip()
    wrapped = re.fullmatch(r"《([^《》]+)》", title)
    stem = readable_markdown_stem(wrapped[1] if wrapped else title)
    # Filesystems can limit component length in bytes rather than characters.
    stem = stem.encode("utf-8")[:250].decode("utf-8", errors="ignore").rstrip(" .")
    if stem.casefold() in {"index", "document-temp", "document-temp.raw", "document-temp.pre-heading"}:
        stem += "-书稿"
    return f"{stem}.md"


def write_book_markdown_copy(index: Path, source: Path) -> Path:
    """Publish an independent copy once, keeping any later manual edits."""

    target = index.with_name(book_markdown_filename(source))
    if not target.is_file() or target.is_symlink():
        with tempfile.TemporaryDirectory(prefix=".book-markdown-", dir=index.parent) as directory:
            temporary = Path(directory) / "copy.md"
            shutil.copyfile(index, temporary)
            try:
                # Link the completed temporary copy, not index.md itself. A
                # same-named file appearing meanwhile must not be replaced.
                os.link(temporary, target)
            except FileExistsError:
                if not target.is_file() or target.is_symlink():
                    raise
    if target.samefile(index):
        raise FileExistsError(f"Editable Markdown must not share a file with index.md: {target}")
    return target


def book_markdown_delivery(index: Path, source: Path) -> dict[str, str]:
    """Report working-copy delivery separately from canonical acceptance."""

    try:
        return {"source_named_markdown": str(write_book_markdown_copy(index, source))}
    except OSError as exc:
        return {"source_named_markdown_error": str(exc)}


def resolve_source_markdown_target(
    package_root: Path,
    source: Path,
    *,
    fallback: str = "document",
    max_chars: int = 120,
    reuse_existing: bool = False,
) -> Path:
    """Return a reader Markdown path derived from the source title.

    Fresh packages keep the collision-avoiding behavior.  A caller resuming a
    controlled package may opt into reusing the first existing source alias so
    repeated finalization is idempotent instead of creating ``-02``/``-03``
    copies.  The canonical ``index.md`` is always reserved.
    """

    stem = readable_markdown_stem(source.stem, fallback=fallback, max_chars=max_chars)
    candidate = package_root / f"{stem}.md"
    ordinal = 1
    while candidate.exists():
        if reuse_existing and candidate.name != "index.md":
            return candidate
        ordinal += 1
        candidate = package_root / f"{stem}-{ordinal:02d}.md"
    return candidate
