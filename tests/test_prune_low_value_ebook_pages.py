from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRUNE = ROOT / "skills/prune-low-value-ebook-pages/scripts/prune_ebook_pages.py"


def create_page_units(tmp_path: Path, count: int = 4) -> Path:
    page_units = tmp_path / "page-units"
    page_units.mkdir()
    for page in range(1, count + 1):
        (page_units / f"p{page:03d}.md").write_text(
            f"第 {page} 页私有正文标记\n\n![原始页面图](<../assets/page-{page:03d}.png>)\n",
            encoding="utf-8",
        )
    return page_units


def run_prune(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRUNE), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def preview(page_units: Path, plan: Path) -> subprocess.CompletedProcess[str]:
    return run_prune(
        "preview",
        "--page-units",
        str(page_units),
        "--pages",
        "1-2",
        "--expected-page-count",
        "4",
        "--reason",
        "1=promotion-or-cta",
        "--reason",
        "2=table-of-contents",
        "--plan",
        str(plan),
    )


def test_preview_writes_body_free_plan_without_deleting(tmp_path: Path):
    page_units = create_page_units(tmp_path)
    plan = tmp_path / "evidence/pruning.json"

    result = preview(page_units, plan)

    assert result.returncode == 0, result.stderr
    assert len(list(page_units.glob("p*.md"))) == 4
    payload = json.loads(plan.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["selected_pages"] == [1, 2]
    assert payload["retained_page_count"] == 2
    assert payload["files"][0]["reason_code"] == "promotion-or-cta"
    assert "私有正文标记" not in plan.read_text(encoding="utf-8")


def test_apply_deletes_only_planned_files_and_is_idempotent(tmp_path: Path):
    page_units = create_page_units(tmp_path)
    plan = tmp_path / "evidence/pruning.json"
    before_p3 = (page_units / "p003.md").read_bytes()
    assert preview(page_units, plan).returncode == 0

    first = run_prune("apply", "--plan", str(plan))
    second = run_prune("apply", "--plan", str(plan))

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert not (page_units / "p001.md").exists()
    assert not (page_units / "p002.md").exists()
    assert (page_units / "p003.md").read_bytes() == before_p3
    assert (page_units / "p004.md").is_file()
    payload = json.loads(plan.read_text(encoding="utf-8"))
    assert payload["status"] == "applied"
    assert payload["deleted_file_count"] == 2
    assert payload["checks"]["retained_files_unchanged"] is True


def test_apply_fails_closed_when_inventory_drifted(tmp_path: Path):
    page_units = create_page_units(tmp_path)
    plan = tmp_path / "evidence/pruning.json"
    assert preview(page_units, plan).returncode == 0
    (page_units / "p004.md").write_text("并发写入后的内容\n", encoding="utf-8")

    result = run_prune("apply", "--plan", str(plan))

    assert result.returncode == 2
    assert "deleted 0 files" in result.stderr
    assert all((page_units / f"p{page:03d}.md").is_file() for page in range(1, 5))


def test_preview_requires_reason_for_every_selected_page(tmp_path: Path):
    page_units = create_page_units(tmp_path)
    plan = tmp_path / "evidence/pruning.json"

    result = run_prune(
        "preview",
        "--page-units",
        str(page_units),
        "--pages",
        "1-2",
        "--expected-page-count",
        "4",
        "--reason",
        "1=promotion-or-cta",
        "--plan",
        str(plan),
    )

    assert result.returncode == 2
    assert "missing reasons" in result.stderr
    assert not plan.exists()
    assert len(list(page_units.glob("p*.md"))) == 4


def test_preview_rejects_incomplete_premerge_directory(tmp_path: Path):
    page_units = create_page_units(tmp_path, count=3)
    plan = tmp_path / "evidence/pruning.json"

    result = run_prune(
        "preview",
        "--page-units",
        str(page_units),
        "--pages",
        "1",
        "--expected-page-count",
        "4",
        "--reason",
        "1=promotion-or-cta",
        "--plan",
        str(plan),
    )

    assert result.returncode == 2
    assert "wait for all page groups" in result.stderr
    assert len(list(page_units.glob("p*.md"))) == 3
