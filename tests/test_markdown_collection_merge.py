from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.markdown_collection_merge import CollectionMergeError, merge_collection


def write_category_map(path: Path, pages: int) -> None:
    path.write_text(
        json.dumps(
            {
                "categories": ["测试", "待复核"],
                "assignments": {
                    f"{page:03d}": "测试" for page in range(1, pages + 1)
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_collection_merge_preserves_units_and_rewrites_assets(tmp_path: Path):
    units = tmp_path / "page-units"
    assets = tmp_path / "assets"
    destination_root = tmp_path / "output"
    units.mkdir()
    assets.mkdir()
    destination_root.mkdir()
    for page in (1, 2):
        (assets / f"page-{page:03d}.png").write_bytes(bytes([page]))
        (units / f"p{page:03d}.md").write_text(
            f"## {page:02d} 模型 {page}\n\n正文 {page}\n\n"
            f"![原始页面图](<../assets/page-{page:03d}.png>)\n",
            encoding="utf-8",
        )
    category_map = tmp_path / "categories.json"
    write_category_map(category_map, 2)

    result = merge_collection(
        source_page_units=units,
        source_assets=assets,
        destination_root=destination_root,
        destination_dir_name="collection",
        pages_selector="all",
        category_map=category_map,
        title="测试合集",
    )

    target = destination_root / "collection"
    assert result["pages"] == 2
    assert result["assets"] == 2
    assert result["single_page_content"].endswith("/单页内容")
    assert result["page_units"] == result["single_page_content"]
    total = (target / "total.md").read_text(encoding="utf-8")
    index = (target / "index.md").read_text(encoding="utf-8")
    assert result["status"] == "merged"
    assert total.index("正文 1") < total.index("正文 2")
    assert '<a id="model-001"></a>' in total
    assert '<a id="model-002"></a>' in total
    assert "# 测试合集｜合并全文" in total
    assert "# 测试合集｜索引" in index
    assert "保存 2 个页面单元" in index
    assert "100 个模型" not in index
    assert "needs-review" not in total
    assert "needs-review" not in index
    assert "assets/page-001.png" in total
    assert "total.md#model-001" in index
    assert "单页内容/p001.md" in index
    assert (target / "单页内容/p001.md").read_text(encoding="utf-8").endswith(
        "![原始页面图](<../assets/page-001.png>)\n"
    )
    assert (target / "assets/page-001.png").read_bytes() == b"\x01"


def test_collection_merge_rejects_missing_selected_page(tmp_path: Path):
    units = tmp_path / "page-units"
    assets = tmp_path / "assets"
    destination_root = tmp_path / "output"
    units.mkdir()
    assets.mkdir()
    destination_root.mkdir()
    (units / "p001.md").write_text("## 01 模型\n", encoding="utf-8")
    category_map = tmp_path / "categories.json"
    write_category_map(category_map, 2)

    with pytest.raises(CollectionMergeError, match="missing"):
        merge_collection(
            source_page_units=units,
            source_assets=assets,
            destination_root=destination_root,
            destination_dir_name="collection",
            pages_selector="1-2",
            category_map=category_map,
            title="测试合集",
        )


def test_collection_merge_allows_known_workspace_metadata(tmp_path: Path):
    units = tmp_path / "page-units"
    assets = tmp_path / "assets"
    destination_root = tmp_path / "output"
    units.mkdir()
    assets.mkdir()
    destination_root.mkdir()
    (units / "p001.md").write_text("## 01 模型\n", encoding="utf-8")
    category_map = tmp_path / "categories.json"
    write_category_map(category_map, 1)
    target = destination_root / "collection"
    target.mkdir()
    (target / ".shopme").mkdir()
    (target / ".docsignore").write_text("workspace metadata\n", encoding="utf-8")

    result = merge_collection(
        source_page_units=units,
        source_assets=assets,
        destination_root=destination_root,
        destination_dir_name="collection",
        pages_selector="all",
        category_map=category_map,
        title="测试合集",
    )

    assert result["status"] == "merged"
    assert (target / ".shopme").is_dir()
    assert (target / ".docsignore").is_file()


def test_collection_merge_rejects_legacy_collection_directory(tmp_path: Path):
    units = tmp_path / "page-units"
    assets = tmp_path / "assets"
    destination_root = tmp_path / "output"
    units.mkdir()
    assets.mkdir()
    destination_root.mkdir()
    (units / "p001.md").write_text("## 01 模型\n", encoding="utf-8")
    category_map = tmp_path / "categories.json"
    write_category_map(category_map, 1)
    target = destination_root / "collection"
    target.mkdir()
    (target / "page-units").mkdir()
    (target / "index.md").write_text("# 旧集合完整正文\n", encoding="utf-8")

    with pytest.raises(CollectionMergeError, match="legacy collection layout"):
        merge_collection(
            source_page_units=units,
            source_assets=assets,
            destination_root=destination_root,
            destination_dir_name="collection",
            pages_selector="all",
            category_map=category_map,
            title="测试合集",
        )


def test_collection_merge_rejects_unexpected_existing_target_entry(tmp_path: Path):
    units = tmp_path / "page-units"
    assets = tmp_path / "assets"
    destination_root = tmp_path / "output"
    units.mkdir()
    assets.mkdir()
    destination_root.mkdir()
    (units / "p001.md").write_text("## 01 模型\n", encoding="utf-8")
    category_map = tmp_path / "categories.json"
    write_category_map(category_map, 1)
    target = destination_root / "collection"
    target.mkdir()
    (target / "unrelated.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(CollectionMergeError, match="unexpected"):
        merge_collection(
            source_page_units=units,
            source_assets=assets,
            destination_root=destination_root,
            destination_dir_name="collection",
            pages_selector="all",
            category_map=category_map,
            title="测试合集",
        )
