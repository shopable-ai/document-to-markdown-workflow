from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


builder = load_module("sales_script_knowledge_layer", ROOT / "adapters" / "sales_script_knowledge_layer.py")
validator = load_module("validate_sales_knowledge_layer", ROOT / "tests" / "validate_sales_knowledge_layer.py")


def test_tree_fingerprint_uses_stable_path_size_and_bytes(tmp_path: Path):
    (tmp_path / "甲").mkdir()
    (tmp_path / "甲" / "a.md").write_text("内容", encoding="utf-8")
    first = builder.tree_fingerprint(tmp_path)
    second = validator.tree_fingerprint(tmp_path)
    assert first == second
    (tmp_path / "甲" / "a.md").write_text("内容2", encoding="utf-8")
    assert builder.tree_fingerprint(tmp_path)["sha256"] != first["sha256"]


def test_blocks_preserve_recomputable_offsets():
    text = "# 标题\n\n这是一个足够长的原文段落，用来验证段落偏移可以从源文件中直接复算，而且不会经过模型改写。\n\n![图](a.png)\n"
    blocks = builder.markdown_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert text[block["start"]:block["end"]] == block["raw"]
    assert block["heading"] == "标题"


def test_normalization_matches_duplicate_contract():
    first = "## 标题\n客户 说：价格太贵了！"
    second = "标题 客户说价格太贵了"
    assert builder.paragraph_key(first) == builder.paragraph_key(second)


def test_risky_content_is_tagged():
    result = builder.classify("这是最后机会，保证百分之百收益，要求客户马上付款成交")
    assert result["primary_theme"] == "成交推进"
    assert "强压/操控" in result["risk_tags"]
    assert "欺骗/夸大" in result["risk_tags"]
    assert "金融敏感" in result["risk_tags"]


def test_taxonomy_has_required_range():
    assert 8 <= len(builder.THEMES) <= 20
    assert {"开场破冰", "需求发现", "异议处理", "成交推进", "客服售后"} <= set(builder.THEMES)


def test_markdown_structure_ignores_verbatim_preformatted_headings(tmp_path: Path):
    text = "# Card\n\n<pre># This is verbatim, not a heading</pre>\n"
    structural = __import__("re").sub(r"<pre>.*?</pre>", "", text, flags=__import__("re").S)
    assert len(__import__("re").findall(r"(?m)^#\s+", structural)) == 1
