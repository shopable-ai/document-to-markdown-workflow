import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "native-epub-to-markdown" / "scripts" / "epub_to_markdown.py"


def write_epub_fixture(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "OPS/package.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Fixture EPUB</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>zh-TW</dc:language>
    <dc:identifier id="bookid">fixture-001</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="image1" href="images/diagram.png" media-type="image/png"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
""",
        )
        archive.writestr(
            "OPS/nav.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc" id="toc">
      <ol>
        <li><a href="text/chapter.xhtml">章节一</a></li>
      </ol>
    </nav>
  </body>
</html>
""",
        )
        archive.writestr(
            "OPS/text/chapter.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div class="main">
      <p class="gfont font-1em30">章节标题</p>
      <p id="p1">第一段 <a href="chapter.xhtml#note1">1</a></p>
      <p class="align-center"><img src="../images/diagram.png" /></p>
      <p id="note1">注释内容</p>
    </div>
  </body>
</html>
""",
        )
        archive.writestr("OPS/images/diagram.png", b"\x89PNG\r\n\x1a\nfixture")


def test_epub_to_markdown_converts_fixture(tmp_path: Path):
    source = tmp_path / "fixture.epub"
    output_root = tmp_path / "out"
    write_epub_fixture(source)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--output-root",
            str(output_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["status"] == "passed"

    index_md = (output_root / "index.md").read_text(encoding="utf-8")
    assert "# Fixture EPUB" in index_md
    assert "## 目录" in index_md
    assert "[章节一](#toc-001)" in index_md
    assert "## 章节一" in index_md
    assert "## 章节标题" in index_md
    assert "第一段 [1](#note1)" in index_md
    assert "![diagram](assets/diagram.png)" in index_md
    assert '<a id="note1"></a>' in index_md
    assert "注释内容" in index_md

    assert (output_root / "assets" / "diagram.png").is_file()
    summary = json.loads(
        (output_root / "evidence" / "source-summary.json").read_text(encoding="utf-8")
    )
    assert summary["metadata"]["title"] == "Fixture EPUB"
    assert summary["spine_document_count"] == 1
