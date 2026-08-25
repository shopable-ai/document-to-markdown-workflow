from __future__ import annotations

from pathlib import Path

from adapters import export_markdown_deliverables as exporter


def _package(root: Path, name: str, body: str) -> Path:
    package = root / name
    (package / "assets").mkdir(parents=True)
    (package / "index.md").write_text(body, encoding="utf-8")
    (package / "assets/page-001.png").write_bytes(name.encode("utf-8"))
    (package / "assets/unreferenced.png").write_bytes(b"unused")
    return package


def test_export_collection_writes_four_named_markdown_files_in_one_directory(tmp_path: Path):
    packages = [
        _package(
            tmp_path / "runs",
            f"醒与悟{version}.0",
            f"# 醒与悟{version}.0\n\n![封面](assets/page-001.png)\n",
        )
        for version in range(2, 6)
    ]

    result = exporter.export_collection(packages, tmp_path / "Markdown成品")

    assert result["status"] == "passed"
    delivery = tmp_path / "Markdown成品"
    assert sorted(path.name for path in delivery.glob("*.md")) == [
        "醒与悟2.0.md",
        "醒与悟3.0.md",
        "醒与悟4.0.md",
        "醒与悟5.0.md",
    ]
    for version in range(2, 6):
        markdown = (delivery / f"醒与悟{version}.0.md").read_text(encoding="utf-8")
        assert f"assets/醒与悟{version}.0/page-001.png" in markdown
        assert (delivery / f"assets/醒与悟{version}.0/page-001.png").is_file()
        assert not (delivery / f"assets/醒与悟{version}.0/unreferenced.png").exists()
    assert not (delivery / "page-units").exists()
    assert not (delivery / "pages").exists()


def test_export_package_rejects_missing_referenced_asset(tmp_path: Path):
    package = tmp_path / "醒与悟2.0"
    package.mkdir()
    (package / "index.md").write_text(
        "![缺失图片](assets/missing.png)\n", encoding="utf-8"
    )

    try:
        exporter.export_collection([package], tmp_path / "delivery")
    except exporter.ExportFailure as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("missing image must fail the delivery export")


def test_reexport_removes_stale_book_assets(tmp_path: Path):
    package = _package(
        tmp_path / "runs",
        "醒与悟3.0",
        "![待复核页](assets/page-001.png)\n",
    )
    delivery = tmp_path / "Markdown成品"
    exporter.export_collection([package], delivery)
    assert (delivery / "assets/醒与悟3.0/page-001.png").is_file()

    (package / "index.md").write_text("*（空白页）*\n", encoding="utf-8")
    exporter.export_collection([package], delivery)

    assert not (delivery / "assets/醒与悟3.0/page-001.png").exists()
