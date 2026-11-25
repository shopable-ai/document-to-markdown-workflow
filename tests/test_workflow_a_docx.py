"""Real Pandoc/OOXML regressions for native DOCX conversion and validation."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from adapters.validate_docx_markdown import validate

ROOT = Path(__file__).resolve().parents[1]
PANDOC = shutil.which('pandoc')
pytestmark = pytest.mark.skipif(not PANDOC, reason='local Pandoc is unavailable')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lZ0AAAAASUVORK5CYII=')


def _docx(tmp_path: Path, markdown: str) -> Path:
    (tmp_path / 'original.png').write_bytes(PNG)
    source = tmp_path / '书稿.docx'
    subprocess.run([PANDOC, '-f', 'gfm', '-t', 'docx', '-o', str(source)],
                   cwd=tmp_path, input=markdown, text=True, check=True, capture_output=True)
    return source


def _cli(script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / 'adapters' / script), *map(str, args)],
                          cwd=ROOT, text=True, capture_output=True, timeout=20)


def test_docx_native_export_passes_and_detects_tampering(tmp_path: Path):
    source = _docx(tmp_path, '# 标题\n\n正文 **重点**。\n\n| A | B |\n|---|---|\n| 甲 | 乙 |\n\n![illustration](original.png)\n')
    original = source.read_bytes()
    output = tmp_path / 'runs'
    result = _cli('workflow_a_source_runner.py', '--source', source, '--output-root', output, '--execute')
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    package = Path(summary['package_path'])
    report_path = package / 'reports/validation-result.json'
    report = json.loads(report_path.read_text())
    assert report['status'] == 'passed'
    assert report['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert report['body_text_matches'] and report['image_order_and_bytes_match']
    assert report['source_tables'] == report['markdown_tables'] == 1
    assert report['source_image_occurrences'] == report['markdown_image_occurrences'] == 1
    assert '# 标题' in (package / 'index.md').read_text()
    asset, = (package / 'assets').iterdir()
    assert asset.read_bytes() == PNG
    assert source.read_bytes() == original

    asset.write_bytes(b'changed image')
    changed = validate(source, package, PANDOC)
    assert changed['status'] != 'passed'
    assert not changed['image_order_and_bytes_match']
    asset.unlink()
    assert validate(source, package, PANDOC)['status'] == 'failed'


def test_background_docx_exposes_actual_package_and_review(tmp_path: Path):
    source = _docx(tmp_path, '![scan](original.png)\n')
    state, output = tmp_path / 'state', tmp_path / 'runs'
    started = _cli('pdf_background_task.py', 'start', '--task-type', 'workflow-a', '--source', source,
                   '--output-root', output, '--state-root', state)
    assert started.returncode == 0, started.stdout + started.stderr
    registration = json.loads(started.stdout)
    assert registration['status'] == 'started'
    snapshot_path = Path(registration['task_root']) / 'progress-current.json'
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = json.loads(snapshot_path.read_text())
        if snapshot['status'] in {'completed', 'needs-review', 'failed'}:
            break
        time.sleep(0.05)
    status = _cli('pdf_background_task.py', 'status', registration['run_id'], '--state-root', state)
    payload = json.loads(status.stdout)
    assert status.returncode == 1 and payload['status'] == 'needs-review', payload
    assert payload['run_root'] == payload['package_path'] == registration['run_root']
    assert payload['converted_documents'] == 1
    assert payload['validator_status'] == 'needs-review'
    report = json.loads(Path(payload['validation_result_path']).read_text())
    assert report['image_order_and_bytes_match']
    assert report['source_body_text_characters'] == 0
    assert any('not OCR-transcribed' in warning for warning in report['review'])


def test_invalid_docx_does_not_publish_empty_package(tmp_path: Path):
    source = tmp_path / 'broken.docx'
    source.write_bytes(b'not a DOCX')
    output = tmp_path / 'runs'
    result = _cli('workflow_a_source_runner.py', '--source', source, '--output-root', output, '--execute')
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'failed'
    assert not list(output.iterdir())
