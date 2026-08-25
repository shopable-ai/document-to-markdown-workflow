from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import adapters.export_pdf_markdown_collection as exporter
from tests.test_lan_pdf_markdown_batch import _write_complete_run, _write_pdf


def _route_report(path: Path, source_root: Path, sources: list[Path]) -> Path:
    records = []
    for source in sources:
        records.append(
            {
                "status": "complete",
                "source_path": str(source),
                "source_sha256": exporter._sha256(source),
                "decision": {"route": "native-only"},
            }
        )
    path.write_text(json.dumps({"records": records}), encoding="utf-8")
    return path


def _results(path: Path, items: list[dict]) -> Path:
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return path


def _passed_item(source: Path, run_root: Path) -> dict:
    _write_complete_run(run_root, source=source, selected_pages=[1, 2], total_pages=2)
    return {
        "status": "passed",
        "source_path": str(source),
        "source_sha256": exporter._sha256(source),
        "run_root": str(run_root),
        "finished_at": "2026-08-24T01:00:00+08:00",
    }


def test_collection_preserves_nested_unicode_names_and_records_sizes(tmp_path: Path):
    source_root = tmp_path / "私域话术"
    source = _write_pdf(source_root / "3、社群" / "原始 文件名.PDF", b"pdf-one")
    report = _route_report(tmp_path / "report.json", source_root, [source])
    item = _passed_item(source, tmp_path / "run-one")
    results = _results(tmp_path / "results.json", [item])
    delivery = tmp_path / "Markdown成品"

    manifest = exporter.build_collection(
        route_report=report,
        result_paths=[results],
        source_root=source_root,
        delivery_root=delivery,
    )

    output = delivery / "3、社群" / "原始 文件名.md"
    assert manifest["status"] == "passed"
    assert manifest["expected_count"] == manifest["published_count"] == 1
    assert output.read_bytes() == (tmp_path / "run-one" / "index.md").read_bytes()
    assert manifest["items"][0]["markdown_sha256"] == exporter._sha256(output)
    assert manifest["items"][0]["package_total_bytes"] > manifest["items"][0]["index_bytes"] > 0
    with (delivery / "source-output-map.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["delivery_relative_path"] == "3、社群/原始 文件名.md"


def test_collection_rewrites_and_publishes_local_image_references(tmp_path: Path):
    source_root = tmp_path / "books"
    source = _write_pdf(source_root / "图解.pdf", b"source")
    report = _route_report(tmp_path / "report.json", source_root, [source])
    run_root = tmp_path / "run"
    item = _passed_item(source, run_root)
    asset = run_root / "assets" / "page-001.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png-bytes")
    index = run_root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8")
        + "\n![第1页原始页面图](assets/page-001.png)\n",
        encoding="utf-8",
    )
    results = _results(tmp_path / "results.json", [item])
    delivery = tmp_path / "delivery"

    manifest = exporter.build_collection(
        route_report=report,
        result_paths=[results],
        source_root=source_root,
        delivery_root=delivery,
    )

    output = delivery / "图解.md"
    published_asset = delivery / "assets" / "图解" / "page-001.png"
    assert "assets/图解/page-001.png" in output.read_text(encoding="utf-8")
    assert published_asset.read_bytes() == asset.read_bytes()
    assert manifest["asset_count"] == 1
    assert manifest["items"][0]["delivery_asset_count"] == 1


def test_collection_publishes_nothing_when_any_source_is_unresolved(tmp_path: Path):
    source_root = tmp_path / "books"
    passed = _write_pdf(source_root / "passed.pdf", b"passed")
    missing = _write_pdf(source_root / "missing.pdf", b"missing")
    report = _route_report(tmp_path / "report.json", source_root, [passed, missing])
    results = _results(
        tmp_path / "results.json",
        [
            _passed_item(passed, tmp_path / "run-passed"),
            {"status": "needs-review", "source_path": str(missing)},
        ],
    )
    delivery = tmp_path / "delivery"

    with pytest.raises(ValueError, match="1/2 sources lack a validated passed result"):
        exporter.build_collection(
            route_report=report,
            result_paths=[results],
            source_root=source_root,
            delivery_root=delivery,
        )

    assert not delivery.exists()


def test_collection_revalidates_passed_package_content(tmp_path: Path):
    source_root = tmp_path / "books"
    source = _write_pdf(source_root / "empty-body.pdf", b"source")
    report = _route_report(tmp_path / "report.json", source_root, [source])
    item = _passed_item(source, tmp_path / "run")
    (tmp_path / "run" / "index.md").write_text("# 只有标题\n", encoding="utf-8")
    results = _results(tmp_path / "results.json", [item])

    with pytest.raises(ValueError, match="lack a validated passed result"):
        exporter.build_collection(
            route_report=report,
            result_paths=[results],
            source_root=source_root,
            delivery_root=tmp_path / "delivery",
        )


def test_collection_rejects_destination_collision(tmp_path: Path):
    source_root = tmp_path / "books"
    lower = _write_pdf(source_root / "same.pdf", b"lower")
    upper = _write_pdf(source_root / "same.PDF", b"upper")
    report = _route_report(tmp_path / "report.json", source_root, [lower, upper])
    results = _results(
        tmp_path / "results.json",
        [
            _passed_item(lower, tmp_path / "run-lower"),
            _passed_item(upper, tmp_path / "run-upper"),
        ],
    )

    with pytest.raises(ValueError, match="same Markdown destination"):
        exporter.build_collection(
            route_report=report,
            result_paths=[results],
            source_root=source_root,
            delivery_root=tmp_path / "delivery",
        )


def test_collection_rejects_unexpected_existing_markdown(tmp_path: Path):
    source_root = tmp_path / "books"
    source = _write_pdf(source_root / "one.pdf", b"one")
    report = _route_report(tmp_path / "report.json", source_root, [source])
    results = _results(
        tmp_path / "results.json", [_passed_item(source, tmp_path / "run")]
    )
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    (delivery / "stale.md").write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected Markdown"):
        exporter.build_collection(
            route_report=report,
            result_paths=[results],
            source_root=source_root,
            delivery_root=delivery,
        )
