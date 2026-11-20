from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest

from adapters import run_pdf_batches as batch


def _synthetic_text_pdf(path: Path) -> None:
    content = (
        b"BT /F1 12 Tf 72 44 Td "
        b"(This synthetic source has three ordinary lines of visible native text.) Tj "
        b"0 -18 Td (The second line keeps its punctuation and position in the page.) Tj "
        b"0 -18 Td (The last line confirms that the reader retains the entire text.) Tj ET"
    )
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 56] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
    )
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, obj)
    startxref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % startxref
    path.write_bytes(pdf)


def test_synthetic_native_pdf_reaches_reader_without_any_page_render(tmp_path, monkeypatch):
    if not all(shutil.which(tool) for tool in ("pdfinfo", "pdftotext", "pdfimages")):
        pytest.skip("Poppler tools unavailable")
    python = batch.BUNDLED_PDF_PYTHON if batch.BUNDLED_PDF_PYTHON.is_file() else sys.executable
    if subprocess.run([str(python), "-c", "import pdfplumber"], capture_output=True, timeout=10).returncode:
        pytest.skip("production PDF extraction runtime unavailable")
    source = tmp_path / "source.pdf"
    _synthetic_text_pdf(source)
    root = tmp_path / "engineering"
    root.mkdir()
    monkeypatch.setattr(batch.page_router, "inspect_rendered_structure",
                        lambda *args: pytest.fail("plain native page should not render"))
    monkeypatch.setattr(batch, "_ollama_healthcheck", lambda: pytest.fail("no remote model should be required"))
    from adapters import pdf_scan_ocr
    monkeypatch.setattr(pdf_scan_ocr, "collect", lambda *a, **k: pytest.fail("no OCR on the native fast path"))
    code, result = batch.run_page_batches(source=source, output_root=tmp_path, pages="all", batch_size=1,
                                          timeout=30, context_mode="independent", context_window=1,
                                          resume_package=root, hybrid=True)
    assert code == 0, result
    audit = json.loads((root / "reports/reader-visual-audit.json").read_bytes())
    assert audit["version"] == "3.0.0"
    assert audit["verified_no_page_image"] == [1]
    assert audit["unverified_no_page_image"] == []
    assert not any((root / "assets").iterdir())
    assert not (root / "reader/assets").exists()
    assert (root / "reader/index.md").read_bytes() == (root / "index.md").read_bytes()
