from __future__ import annotations

import hashlib
from base64 import b64decode
from pathlib import Path

import pytest

from adapters.pdf_hybrid_reader_export import export_reader, validate_reader


PAGE_IMAGE = "assets/page-001.png"
PAGE_LINE = b"![Page 1](assets/page-001.png)"
PAGE_START = b"<!-- page:1 -->\n\nPage 1 text\n\n"
FLAT_IMAGE = "assets/flow chart.png"
PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg=="
)
DETAIL_PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNoAAAAggCBd81ytgAAAABJRU5ErkJggg=="
)
GATES = ("text_confident", "character_coverage", "reading_order_coverage", "visual_clear")


def _put(root: Path, relative: str, content: bytes) -> None:
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(content)


def _sha256(root: Path, relative: str) -> str:
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def _package(tmp_path: Path, index: bytes) -> Path:
    package = tmp_path / "engineering"
    for relative, content in {
        "index.md": index,
        PAGE_IMAGE: PNG,
        "assets/unreferenced.png": PNG,
        "manifest.yaml": b"fixture: true\n",
        "reports/validation-result.json": b'{"status":"passed"}\n',
        "evidence/original.json": b'{"fixture":"source evidence"}\n',
        "page-units/p001.md": PAGE_START + PAGE_LINE + b"\n",
    }.items():
        _put(package, relative, content)
    return package


def _decision(package: Path, page: int = 1, asset: str | None = PAGE_IMAGE, **overrides) -> dict:
    return {
        "page": page,
        "asset": asset,
        "asset_sha256": _sha256(package, asset) if asset is not None else None,
        "decision": "candidate-remove",
        "gates": dict.fromkeys(GATES, True),
        **overrides,
    }


def _report(package: Path, *pages: dict) -> dict:
    return {
        "index_sha256": _sha256(package, "index.md"),
        "pages": list(pages),
    }


def _snapshot(root: Path) -> dict:
    tree = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            entry = ("symlink", path.readlink())
        elif path.is_dir():
            entry = ("directory",)
        else:
            entry = ("file", path.read_bytes(), path.stat().st_mtime_ns)
        tree[path.relative_to(root).as_posix()] = entry
    return tree


def _assert_status(result: dict, status: str) -> None:
    assert result["status"] == status, result
    diagnostics = result["diagnostics"]
    assert isinstance(diagnostics, list)
    assert all(isinstance(item, str) for item in diagnostics)
    if status == "failed":
        assert diagnostics, result


def _assert_export_fails(package: Path, report: dict, destination: Path) -> None:
    original = _snapshot(package)
    _assert_status(export_reader(package, report, destination), "failed")
    assert not destination.exists() and not destination.is_symlink()
    assert _snapshot(package) == original


def test_v2_export_refuses_ocr_only_candidate_even_if_old_gates_pass(tmp_path: Path):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    row = _decision(package, reader_action="omit", independent_text_evidence=False)
    report = {**_report(package, row), "version": "2.0.0"}
    destination = tmp_path / "reader"
    _assert_export_fails(package, report, destination)
    row["independent_text_evidence"] = True
    _assert_status(export_reader(package, report, destination), "passed")
    assert (destination / "index.md").read_bytes() == PAGE_START


def test_export_removes_only_the_authorized_line_and_copies_references(tmp_path: Path):
    prefix = "# 阅读\r\n\r\n<!-- page:1 -->\r\n\r\n正文（保留尾随空格）  \r\n\r\n".encode()
    suffix = (
        b"\r\n<!-- page: 2 -->\r\n\r\n"
        b'![Diagram](assets/flow%20chart.png "Diagram title")\r\n'
        b"[Full diagram](assets/flow%20chart.png 'Open separately')\r\n"
        b'[Zoom](assets/detail.png "Linked without embedding")\r\n'
        b"[Back](index.md#section)\r\n"
        b"\r\n![Keep page 2](assets/page-002.png)\r\n\r\n## section"
    )
    package = _package(tmp_path, prefix + PAGE_LINE + b"\r\n" + suffix)
    kept = {FLAT_IMAGE: PNG, "assets/detail.png": DETAIL_PNG, "assets/page-002.png": PNG}
    for relative, content in kept.items():
        _put(package, relative, content)
    report = _report(
        package,
        _decision(package),
        _decision(package, page=2, asset="assets/page-002.png", decision="retain"),
    )
    destination = tmp_path / "reader"
    original = _snapshot(package)

    _assert_status(export_reader(package, report, destination), "passed")
    assert (destination / "index.md").read_bytes() == prefix + suffix
    assert set(_snapshot(destination)) == {"index.md", "assets", *kept}
    for relative, content in kept.items():
        assert not (destination / relative).is_symlink()
        assert (destination / relative).read_bytes() == content
    _assert_status(validate_reader(package, report, destination), "passed")
    assert _snapshot(package) == original


def test_text_only_projection_does_not_create_an_assets_directory(tmp_path: Path):
    original_text = b"<!-- page: 1 -->\n\nText remains.\n\n"
    package = _package(tmp_path, original_text + PAGE_LINE)  # No final newline.
    report = _report(package, _decision(package))
    destination = tmp_path / "reader"

    _assert_status(export_reader(package, report, destination), "passed")
    assert sorted(path.name for path in destination.iterdir()) == ["index.md"]
    assert (destination / "index.md").read_bytes() == original_text
    _assert_status(validate_reader(package, report, destination), "passed")
    (destination / "assets").mkdir()
    _assert_status(validate_reader(package, report, destination), "failed")


def test_text_only_source_and_manually_built_copy_need_no_assets_directory(tmp_path: Path):
    original = b"<!-- page:1 -->\r\n\r\n## Text-only\r\n\r\n[Heading](#text-only)\r\n"
    package, manual_copy, exported = (tmp_path / name for name in ("source", "manual", "exported"))
    _put(package, "index.md", original)
    _put(manual_copy, "index.md", original)
    report = _report(package, _decision(package, asset=None, decision="retain", gates={}))

    _assert_status(validate_reader(package, report, manual_copy), "passed")
    _assert_status(export_reader(package, report, exported), "passed")
    assert list(path.name for path in exported.iterdir()) == ["index.md"]
    assert (exported / "index.md").read_bytes() == original


@pytest.mark.parametrize("decision", ["crop-required", "needs-review"])
def test_undecided_page_image_is_copied_unchanged(tmp_path: Path, decision: str):
    original = PAGE_START + PAGE_LINE + b"\n"
    package = _package(tmp_path, original)
    report = _report(package, _decision(package, decision=decision))
    destination = tmp_path / "reader"

    result = export_reader(package, report, destination)
    _assert_status(result, "passed")
    assert result["removed_pages"] == []
    assert (destination / "index.md").read_bytes() == original
    assert (destination / PAGE_IMAGE).read_bytes() == PNG
    _assert_status(validate_reader(package, report, destination), "passed")


def test_link_like_code_and_comment_do_not_change_page_ownership_or_assets(tmp_path: Path):
    # A fake marker in a code block must not claim ownership of the real image.
    body = (
        b"```markdown\n<!-- page:2 -->\n" + PAGE_LINE + b"\n```\n\n"
        b"`![not a link](assets/missing.png)`\n\n"
        b"<!--\n![a comment](assets/missing.png)\n-->\n\n"
    )
    package = _package(tmp_path, PAGE_START + body + PAGE_LINE + b"\n")
    report = _report(package, _decision(package))
    destination = tmp_path / "reader"

    _assert_status(export_reader(package, report, destination), "passed")
    assert (destination / "index.md").read_bytes() == PAGE_START + body
    assert not (destination / "assets").exists()
    _assert_status(validate_reader(package, report, destination), "passed")


@pytest.mark.parametrize(
    "syntax",
    [b"[Report][r]\n\n[r]: reports/validation-result.json",
     b"<a href='reports/validation-result.json'>report</a>",
     b"[[reports/validation-result.json]]",
     b"[Report](\nreports/validation-result.json)"],
    ids=["reference-link", "html-link", "wiki-link", "multiline-link"],
)
def test_unsupported_markdown_link_syntax_fails_closed(tmp_path: Path, syntax: bytes):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n\n" + syntax + b"\n")
    _assert_export_fails(package, _report(package, _decision(package)), tmp_path / "reader")


def test_candidate_must_be_under_its_declared_page_marker(tmp_path: Path):
    package = _package(tmp_path, PAGE_START + b"<!-- page: 2 -->\n\n" + PAGE_LINE + b"\n")
    report = _report(
        package, _decision(package), _decision(package, page=2, asset=None, decision="retain")
    )
    _assert_export_fails(package, report, tmp_path / "reader")


@pytest.mark.parametrize(
    "duplicate",
    [
        PAGE_LINE,
        b'![Alias](assets/%70age-001.png "Same image")',
        b"[Open image](assets/page-001.png)",
        b"![Alias][page-render]\n\n[page-render]: assets/page-001.png",
    ],
    ids=["same-inline-image", "percent-escaped-image-with-title", "ordinary-link", "reference-style"],
)
def test_candidate_with_another_reference_fails_whole_export(tmp_path: Path, duplicate: bytes):
    index = PAGE_START + PAGE_LINE + b"\n\n<!-- page: 2 -->\n\n" + duplicate + b"\n"
    package = _package(tmp_path, index)
    report = _report(
        package, _decision(package), _decision(package, page=2, asset=None, decision="retain")
    )
    _assert_export_fails(package, report, tmp_path / "reader")


def test_duplicate_candidate_declarations_fail_whole_export(tmp_path: Path):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    _assert_export_fails(
        package,
        _report(package, _decision(package), _decision(package)),
        tmp_path / "reader",
    )


@pytest.mark.parametrize(
    "reference",
    [b"Caption before " + PAGE_LINE, b'![Page 1](assets/page-001.png "Page title")'],
    ids=["inline-in-prose", "noncanonical-titled-image"],
)
def test_candidate_requires_a_canonical_standalone_image_line(tmp_path: Path, reference: bytes):
    package = _package(tmp_path, PAGE_START + reference + b"\n")
    _assert_export_fails(package, _report(package, _decision(package)), tmp_path / "reader")


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("value", [False, 1, "true"], ids=["false", "truthy-int", "truthy-string"])
def test_every_candidate_gate_requires_literal_true(tmp_path: Path, gate: str, value: bool | int | str):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    gates = dict.fromkeys(GATES, True)
    gates[gate] = value
    _assert_export_fails(package, _report(package, _decision(package, gates=gates)), tmp_path / "reader")


def test_missing_candidate_gate_fails_closed(tmp_path: Path):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    gates = dict.fromkeys(GATES[:-1], True)
    _assert_export_fails(package, _report(package, _decision(package, gates=gates)), tmp_path / "reader")


@pytest.mark.parametrize(
    "target",
    ["reports/validation-result.json", "../outside.png", "%2e%2e/outside.png", "assets/missing.png"],
    ids=["engineering-report", "path-traversal", "encoded-traversal", "missing-image"],
)
def test_unsupported_local_link_fails_without_partial_output(tmp_path: Path, target: str):
    index = PAGE_START + PAGE_LINE + b"\n\n" + f'[Other]({target} "Title")\n'.encode()
    package = _package(tmp_path, index)
    _put(tmp_path, "outside.png", PNG)  # Traversal is unsafe even if the referenced file exists.
    _assert_export_fails(package, _report(package, _decision(package)), tmp_path / "reader")


@pytest.mark.parametrize("path", ["index.md", PAGE_IMAGE], ids=["stale-index-hash", "stale-image-hash"])
def test_export_and_validation_fail_on_a_stale_source_hash(tmp_path: Path, path: str):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    report = _report(package, _decision(package))
    destination = tmp_path / "reader"
    _assert_status(export_reader(package, report, destination), "passed")

    # Keep the reference and page scope unchanged when changing the source index.
    original = (package / path).read_bytes()
    _put(package, path, original + b"\n")
    _assert_export_fails(package, report, tmp_path / "second-reader")
    _assert_status(validate_reader(package, report, destination), "failed")


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("index.md", b"<!-- page:1 -->\n\nEdited text\n\n![Keep](assets/keep.png)\n"),
        ("assets/keep.png", b"not the source bytes"),
        ("assets/keep.png", None),
        ("assets/orphan.png", PNG),
        ("reports/unexpected.txt", b"must not leak engineering artifacts"),
    ],
    ids=["edited-index", "edited-image", "missing-image", "unreferenced-image", "extra-report"],
)
def test_validator_detects_output_tampering_without_repairing_it(
    tmp_path: Path, path: str, content: bytes | None
):
    index = PAGE_START + PAGE_LINE + b"\n\n![Keep](assets/keep.png)\n"
    package = _package(tmp_path, index)
    _put(package, "assets/keep.png", PNG)
    report = _report(package, _decision(package))
    destination = tmp_path / "reader"
    original = _snapshot(package)
    _assert_status(export_reader(package, report, destination), "passed")
    _assert_status(validate_reader(package, report, destination), "passed")

    if content is None:
        (destination / path).unlink()
    else:
        _put(destination, path, content)
    tampered = _snapshot(destination)

    _assert_status(validate_reader(package, report, destination), "failed")
    assert _snapshot(destination) == tampered
    assert _snapshot(package) == original


@pytest.mark.parametrize("nonempty", [False, True], ids=["empty", "nonempty"])
def test_existing_destination_is_never_overwritten(tmp_path: Path, nonempty: bool):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    report = _report(package, _decision(package))
    destination = tmp_path / "reader"
    destination.mkdir()
    if nonempty:
        _put(destination, "index.md", b"existing reader bytes")
    original = _snapshot(destination)
    source = _snapshot(package)

    _assert_status(export_reader(package, report, destination), "failed")
    assert destination.is_dir()
    assert _snapshot(destination) == original
    assert _snapshot(package) == source


def test_destination_cannot_be_nested_in_the_source_package(tmp_path: Path):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    _assert_export_fails(package, _report(package, _decision(package)), package / "reader")


def test_read_only_validator_accepts_nested_published_reader(tmp_path: Path):
    package = _package(tmp_path, PAGE_START + PAGE_LINE + b"\n")
    report = _report(package, _decision(package))
    destination = package / "reader"
    _put(destination, "index.md", PAGE_START)
    original = _snapshot(package)

    _assert_status(validate_reader(package, report, destination), "passed")
    assert _snapshot(package) == original
