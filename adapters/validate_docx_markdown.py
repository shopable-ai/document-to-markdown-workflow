#!/usr/bin/env python3
"""Independently compare a native DOCX export with the original OOXML."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
V = '{urn:schemas-microsoft-com:vml}'


def pandoc_command(pandoc: str, *args: str, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            [pandoc, '--sandbox', *args], input=data, capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError('DOCX Pandoc command timed out') from exc
    if result.returncode:
        raise ValueError('DOCX Pandoc command failed: ' + result.stderr.decode(errors='replace').strip()[-1500:])
    return result.stdout


def ast_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from ast_nodes(item)
    elif isinstance(value, dict) and 't' in value:
        yield value
        if value['t'] not in {'Image', 'Code', 'CodeBlock', 'Math'}:
            yield from ast_nodes(value.get('c'))


def word_page_hint(archive: ZipFile) -> int | None:
    try:
        properties = ET.fromstring(archive.read('docProps/app.xml'))
        value = int(next((e.text or '0' for e in properties if e.tag.rsplit('}', 1)[-1] == 'Pages'), '0'))
    except (KeyError, ValueError, ET.ParseError):
        return None
    return value if value > 0 else None


def body_from_docx(archive: ZipFile) -> ET.Element:
    if 'word/document.xml' not in archive.namelist():
        raise ValueError('DOCX is missing word/document.xml')
    body = ET.fromstring(archive.read('word/document.xml')).find(W + 'body')
    if body is None:
        raise ValueError('DOCX is missing its Word document body')
    return body


def _compact(parts) -> str:
    return re.sub(r'\s+', '', ''.join(parts))


def _image_digests(archive: ZipFile, body: ET.Element) -> tuple[list[str], int]:
    rels = {}
    if 'word/_rels/document.xml.rels' in archive.namelist():
        rels = {e.get('Id'): e for e in ET.fromstring(archive.read('word/_rels/document.xml.rels'))}
    digests, unresolved = [], 0
    hashes = {}
    for node in body.iter():
        if node.tag not in {A + 'blip', V + 'imagedata'}:
            continue
        rel = rels.get(node.get(R + 'embed') or node.get(R + 'id') or node.get(R + 'link'))
        target = rel.get('Target', '') if rel is not None and rel.get('TargetMode') != 'External' else ''
        path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else f'word/{target}')
        if not target or not path.startswith('word/media/') or path not in archive.namelist():
            unresolved += 1
            continue
        if path not in hashes:
            hashes[path] = hashlib.sha256(archive.read(path)).hexdigest()
        digests.append(hashes[path])
    return digests, unresolved


def validate(source: Path, package: Path, pandoc: str) -> dict:
    markdown = package / 'index.md'
    with source.open('rb') as handle:
        fingerprint = hashlib.file_digest(handle, 'sha256').hexdigest()
    with ZipFile(source) as archive:
        body = body_from_docx(archive)
        page_hint = word_page_hint(archive)
        original_text = _compact(x.text or '' for x in body.iter(W + 't'))
        source_tables = sum(1 for _ in body.iter(W + 'tbl'))
        original_images, unresolved = _image_digests(archive, body)
        other_parts = 0
        for name in archive.namelist():
            if not re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes)\.xml', name):
                continue
            part = ET.fromstring(archive.read(name))
            if any((x.text or '').strip() for x in part.iter(W + 't')) or any(part.iter(A + 'blip')) or any(part.iter(V + 'imagedata')):
                other_parts += 1
        unsupported = sum(
            node.tag in {W + 'altChunk', W + 'object'}
            or (node.tag in {W + 'drawing', W + 'pict'} and not any(
                x.tag in {A + 'blip', V + 'imagedata'} for x in node.iter()
            ))
            for node in body.iter()
        )

    rendered = json.loads(pandoc_command(pandoc, '-f', 'gfm+footnotes', '-t', 'json', str(markdown)))
    nodes = list(ast_nodes(rendered['blocks']))
    texts = []
    for node in nodes:
        kind = node['t']
        if kind == 'Str':
            texts.append(node['c'])
        elif kind in {'Code', 'CodeBlock', 'Math'}:
            texts.append(node['c'][1])
    rendered_text = _compact(texts)
    rendered_tables = sum(node['t'] == 'Table' for node in nodes)
    actual_images, broken, referenced = [], [], set()
    asset_root = package / 'assets'
    for node in nodes:
        if node['t'] != 'Image':
            continue
        raw = node['c'][2][0]
        relative = PurePosixPath(raw)
        path = package / raw
        if (len(relative.parts) != 2 or relative.parts[0] != 'assets'
                or raw != relative.as_posix() or relative.name == '..' or path.is_symlink()
                or not path.resolve().is_relative_to(package.resolve()) or not path.is_file()):
            broken.append(raw)
            continue
        referenced.add(path)
        with path.open('rb') as handle:
            actual_images.append(hashlib.file_digest(handle, 'sha256').hexdigest())

    failures, review = [], []
    if broken or (asset_root.is_dir() and set(asset_root.iterdir()) != referenced):
        failures.append('missing, invalid, or unreferenced image assets')
    if not rendered_text and not actual_images and not rendered_tables:
        failures.append('no readable text, images or tables in the export')
    if original_text != rendered_text:
        review.append('native body text or order differs from OOXML (whitespace ignored)')
    if source_tables != rendered_tables:
        review.append('table count differs from OOXML')
    if original_images != actual_images or unresolved:
        review.append('body image order, coverage or bytes differ from OOXML')
    if other_parts:
        review.append('headers, footers or notes contain content outside the body comparison')
    if unsupported:
        review.append('unverified Word drawings, embedded objects or alternate content')
    if original_images and not original_text:
        review.append('image-only body: original images preserved when verified; image text was not OCR-transcribed')
    return {
        'status': 'failed' if failures else 'needs-review' if review else 'passed',
        'validator': 'validate_docx_markdown.py',
        'scope': 'whole-document-native-body; no Word pagination, visual layout or OCR claim',
        'source': str(source), 'source_sha256': fingerprint,
        'word_metadata_pages': page_hint,
        'source_body_text_characters': len(original_text),
        'markdown_text_characters': len(rendered_text),
        'body_text_matches': original_text == rendered_text,
        'source_tables': source_tables, 'markdown_tables': rendered_tables,
        'source_image_occurrences': len(original_images) + unresolved,
        'markdown_image_occurrences': len(actual_images),
        'image_order_and_bytes_match': not unresolved and original_images == actual_images,
        'distinct_assets': len(referenced), 'unresolved_source_images': unresolved,
        'unsupported_body_objects': unsupported, 'nonempty_auxiliary_parts': other_parts,
        'failures': failures, 'review': review,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--pandoc', default='pandoc')
    args = parser.parse_args()
    try:
        result = validate(args.source.resolve(), args.package.resolve(), args.pandoc)
    except (OSError, ValueError, BadZipFile, ET.ParseError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
