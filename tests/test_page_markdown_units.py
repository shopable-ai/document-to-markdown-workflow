import json
from pathlib import Path

import pytest

from adapters.page_markdown_units import PageUnitError, merge_units, parse_page_selector, split_pages, validate_unit_text


def page_unit(page: int, title: str, image: str) -> str:
    return f"""<!-- page-number: {page} -->
<!-- unit-status: passed -->
<!-- semantic-title: {title} -->
<!-- relation-status: needs-review -->

# {title}

## 正文

还指厂商把相同的生产要素投入到其他行业当中去可以获得的最高收益。

看两者在时间和银子维度为你创造价值的大小来判断.显然追剧在时间和银子维度并不能为你创造价值.

## 列表/图示文字

- 选择A
- 选择B
- 机会
- 成本
- 收益
- 读书
- 追剧

## 表格与公式

实用公式

（所放弃的最佳选择的价值）-（实际选择价值）=（机会成本）

## 解读

看两者在时间和银子维度为你创造价值的大小来判断.显然追剧在时间和银子维度并不能为你创造价值.

## 举例与思考

比如你放弃读书选择追剧，那么你的机会成本为负，为什么为负？

## 模型结构

- 机会成本

## 已确认的关系说明

暂无已确认的关系说明。

## 原始页面图

![原始页面图]({image})

## 待核对项

- 放弃读书可能取得的
- 放弃追剧可能取得
- 的收益（学习、成长、等）
- 是实行“读书”的
- 图示关系：Unknown/needs-review。
"""


def test_page_selector_is_deterministic():
    assert parse_page_selector("5,2-4,4") == [2, 3, 4, 5]


def test_page_selector_supports_large_documents_beyond_100_pages():
    assert parse_page_selector("6-135") == list(range(6, 136))


def test_page_selector_supports_total_pages_marker():
    assert parse_page_selector("6-total_pages", total_pages=135) == list(range(6, 136))


def test_page_selector_requires_total_pages_for_dynamic_marker():
    with pytest.raises(PageUnitError, match="requires total_pages"):
        parse_page_selector("6-total_pages")


def test_validate_page_unit_preserves_required_boundaries():
    result = validate_unit_text(page_unit(1, "机会成本模型", "assets/pages/p001.png"), expected_page=1, expected_image="assets/pages/p001.png")
    assert result["status"] == "passed"
    assert result["relation_status"] == "needs-review"
    assert result["checks"]["formula_standalone"] is True


def test_validate_text_only_page_unit_without_image():
    text = page_unit(1, "机会成本模型", "assets/pages/p001.png")
    text = text.replace("<!-- relation-status: needs-review -->", "<!-- relation-status: needs-review -->\n<!-- reader-image-action: omit -->")
    text = text.replace("\n## 原始页面图\n\n![原始页面图](assets/pages/p001.png)\n", "")

    result = validate_unit_text(text, expected_page=1)

    assert result["reader_image_action"] == "omit"
    assert result["image"] is None


def test_merge_sorts_units_without_rewriting_text(tmp_path: Path):
    p1 = tmp_path / "p001.md"
    p2 = tmp_path / "p002.md"
    p1.write_text(page_unit(1, "一", "assets/pages/p001.png"), encoding="utf-8")
    p2.write_text(page_unit(2, "二", "assets/pages/p002.png"), encoding="utf-8")
    output = tmp_path / "document-temp.md"
    result = merge_units([p2, p1], output)
    merged = output.read_text(encoding="utf-8")
    assert result["pages"] == [1, 2]
    assert merged.index("<!-- page-number: 1 -->") < merged.index("<!-- page-number: 2 -->")
    assert "assets/pages/p001.png" in merged
    assert "assets/pages/p002.png" in merged


def test_later_pages_require_a_passed_prior_gate(tmp_path: Path):
    source = tmp_path / "index.md"
    source.write_text("## 一\n\n正文\n\n![图](assets/pages/p001.png)\n\n## 二\n\n正文\n\n![图](assets/pages/p002.png)\n", encoding="utf-8")
    with pytest.raises(PageUnitError, match="prior gate"):
        split_pages(source, [2], tmp_path / "units", None, None)
    gate = tmp_path / "p001-validation.json"
    gate.write_text(json.dumps({"status": "passed", "scope": "single-page"}), encoding="utf-8")
    paths = split_pages(source, [2], tmp_path / "units", None, gate)
    assert paths == [tmp_path / "units/p002.md"]


def test_single_page_split_stops_before_later_page_body(tmp_path: Path):
    source = tmp_path / "index.md"
    source.write_bytes(
        "## 一\n\n正文\n\n![图](assets/pages/p001.png)\n\n## 二\n\n".encode("utf-8")
        + (b"later-page-body " * 100000)
        + b"\xff\xfe invalid later-page bytes"
    )
    output = tmp_path / "p001.md"
    split_pages(source, [1], None, output, None)
    assert "assets/pages/p001.png" in output.read_text(encoding="utf-8")


def test_split_cli_can_infer_total_pages_for_dynamic_full_batch_selector(tmp_path: Path):
    source = tmp_path / "index.md"
    source.write_text(
        "## 一\n\n正文\n\n![图](assets/pages/p001.png)\n\n"
        "## 二\n\n正文\n\n![图](assets/pages/p002.png)\n\n"
        "## 三\n\n正文\n\n![图](assets/pages/p003.png)\n\n"
        "## 四\n\n正文\n\n![图](assets/pages/p004.png)\n\n"
        "## 五\n\n正文\n\n![图](assets/pages/p005.png)\n\n"
        "## 六\n\n正文\n\n![图](assets/pages/p006.png)\n",
        encoding="utf-8",
    )
    gate = tmp_path / "p002-p005-validation.json"
    gate.write_text(json.dumps({"status": "passed", "scope": "small-batch-2-5"}), encoding="utf-8")
    paths = split_pages(
        source,
        parse_page_selector("6-total_pages", total_pages=6),
        tmp_path / "units",
        None,
        gate,
    )
    assert paths == [tmp_path / "units/p006.md"]
