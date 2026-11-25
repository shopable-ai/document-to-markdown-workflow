"""Whole-document native DOCX conversion for the Workflow A reader entry."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from zipfile import ZipFile

try:
    from validate_docx_markdown import ast_nodes, body_from_docx, pandoc_command, validate
except ImportError:  # package-style imports
    from .validate_docx_markdown import ast_nodes, body_from_docx, pandoc_command, validate


def _export_images(tree: dict, archive: ZipFile, package: Path) -> None:
    names = set(archive.namelist())
    exported: dict[str, str] = {}
    used: set[str] = set()
    assets = package / 'assets'
    for node in ast_nodes(tree['blocks']):
        if node['t'] != 'Image':
            continue
        _, alt, target = node['c']
        raw = unquote(target[0])
        path = PurePosixPath(raw)
        archive_name = f'word/{path}'
        if (not path.parts or path.parts[0] != 'media' or '..' in path.parts
                or archive_name not in names or raw.endswith('/')):
            # Do not fetch remote images or write broken links; the separate
            # OOXML comparison reports unresolved or omitted occurrences.
            node.update(t='Span', c=[['', [], []], alt])
            continue
        if archive_name not in exported:
            stem = re.sub(r'[^a-zA-Z0-9_-]+', '-', path.stem).strip('-_')[:80] or 'image'
            suffix = path.suffix.lower()
            if not re.fullmatch(r'\.[a-z0-9]{1,10}', suffix):
                suffix = '.bin'
            name = stem + suffix
            ordinal = 2
            while name.casefold() in used:
                name = f'{stem}-{ordinal:02d}{suffix}'
                ordinal += 1
            used.add(name.casefold())
            assets.mkdir(exist_ok=True)
            with archive.open(archive_name) as original, (assets / name).open('xb') as copy:
                shutil.copyfileobj(original, copy)
            exported[archive_name] = f'assets/{name}'
        # Word width/height are display hints. Use native Markdown image links
        # with the original bytes, rather than HTML tied to Word page geometry.
        node['c'] = [['', [], []], alt, [exported[archive_name], target[1]]]


def execute_docx(source: Path, target: Path, pandoc: str, source_fingerprint: str) -> dict:
    if target.exists() or target.is_symlink():
        raise ValueError(f'output package already exists: {target}')
    with tempfile.TemporaryDirectory(prefix='.workflow-a-docx-', dir=target.parent) as temp:
        staging = Path(temp) / 'package'
        staging.mkdir()
        with ZipFile(source) as archive:
            body_from_docx(archive)
            tree = json.loads(pandoc_command(pandoc, '-f', 'docx', '-t', 'json', str(source)))
            _export_images(tree, archive, staging)
        markdown = pandoc_command(
            pandoc, '-f', 'json', '-t', 'gfm+footnotes', '--wrap=none',
            data=json.dumps(tree, ensure_ascii=False).encode(),
        )
        (staging / 'index.md').write_bytes(markdown)
        result = validate(source, staging, pandoc)
        if result['source_sha256'] != source_fingerprint:
            raise ValueError('DOCX changed during conversion; cannot publish the staged package')
        if result['status'] == 'failed':
            raise ValueError('DOCX Markdown validation failed: ' + '; '.join(result['failures']))
        (staging / 'reports').mkdir()
        (staging / 'reports/validation-result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
        )
        # Reserve the final name without overwriting a concurrent run, then
        # publish the validated tree in one rename on the output filesystem.
        target.mkdir()
        try:
            os.replace(staging, target)
        except OSError:
            target.rmdir()
            raise
    return result
