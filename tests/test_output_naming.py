from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))

import output_naming as naming  # noqa: E402


def test_source_markdown_filename_preserves_readable_chinese_title():
    source = Path("第009部《绝秘人性天书》.pdf")

    assert naming.source_markdown_filename(source) == "第009部《绝秘人性天书》.md"


def test_resolve_source_markdown_target_avoids_index_collision(tmp_path: Path):
    (tmp_path / "index.md").write_text("# existing\n", encoding="utf-8")

    target = naming.resolve_source_markdown_target(tmp_path, Path("index.pdf"))

    assert target == tmp_path / "index-02.md"


def test_resolve_source_markdown_target_reuses_existing_alias_on_resume(tmp_path: Path):
    existing = tmp_path / "醒与悟2.0.md"
    existing.write_text("旧的断点结果\n", encoding="utf-8")

    target = naming.resolve_source_markdown_target(
        tmp_path,
        Path("醒与悟2.0.pdf"),
        reuse_existing=True,
    )

    assert target == existing


def test_resolve_package_target_uses_source_title_by_default(tmp_path: Path):
    source = Path("第009部《绝秘人性天书》.pdf")

    package_name, target = naming.resolve_package_target(
        tmp_path,
        source,
        when=datetime(2026, 8, 21, 16, 0, 0),
    )

    assert package_name == "第009部《绝秘人性天书》"
    assert target == tmp_path / package_name


def test_resolve_package_target_adds_suffix_for_existing_source_title(tmp_path: Path):
    source = Path("第009部《绝秘人性天书》.pdf")
    existing = tmp_path / "第009部《绝秘人性天书》"
    existing.mkdir()

    package_name, target = naming.resolve_package_target(
        tmp_path,
        source,
        when=datetime(2026, 8, 21, 16, 0, 0),
    )

    assert package_name == "第009部《绝秘人性天书》-02"
    assert target == tmp_path / package_name


def test_resolve_package_target_can_use_readable_timestamp(tmp_path: Path):
    source = Path("100个思维工具详解.pdf")

    package_name, target = naming.resolve_package_target(
        tmp_path,
        source,
        when=datetime(2026, 8, 24, 12, 0, 0),
        include_timestamp=True,
    )

    assert package_name == "100个思维工具详解-20260824-120000"
    assert target == tmp_path / package_name
