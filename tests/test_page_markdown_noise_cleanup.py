from __future__ import annotations

import json
from pathlib import Path

from adapters import page_markdown_noise_cleanup as cleanup


def write_page(root: Path, page: int, body: str) -> Path:
    path = root / f"p{page:03d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def explicit_profile() -> dict:
    return {
        "version": "1.0.0",
        "zones": {
            "header_nonempty_lines": 6,
            "footer_nonempty_lines": 6,
            "adjacent_line_window": 4,
        },
        "discovery": {"enabled": False},
        "header_rules": [
            {
                "id": "management-book",
                "kind": "regex",
                "pattern": r"^318\s*个\s*管理思维\s*(?:[-—–−一]|--+).+$",
            },
            {
                "id": "english-and-library-combined",
                "kind": "regex",
                "pattern": r"^Management\s+Thinking\s+[A-Za-z]+\s+管理知识库$",
                "ignore_case": True,
            },
            {
                "id": "english-banner",
                "kind": "fuzzy",
                "value": "Management Thinking Frameworks",
                "minimum_similarity": 0.72,
            },
            {"id": "knowledge-library", "kind": "exact", "value": "管理知识库"},
        ],
    }


def test_plan_then_apply_removes_only_confirmed_template_lines(tmp_path: Path):
    page_units = tmp_path / "page-units"
    p1 = write_page(
        page_units,
        1,
        """---
## 318个管理思维 — 管理提升
Management Thinking Fromworks
管理知识库
---

# 真正标题

正文保留“318个管理思维”这一书名，不是独立页眉。

---

- -13-

![原始页面图](<../assets/page-001.png>)
---
""",
    )
    p2 = write_page(
        page_units,
        2,
        """<!-- page-number: 2 -->
<!-- unit-status: needs-review -->
<!-- original-page-image: ../assets/page-002.png -->

# 员工成长

## 正文

318个管理思维 — 管理提升
Management Thinking Fromworks 管理知识库

真正正文。

## 原始页面图

![原始页面图](<../assets/page-002.png>)

## 待核对项

Unknown/needs-review
""",
    )
    p3 = write_page(
        page_units,
        3,
        """# 真正标题

第一段正文。

318个管理思维 — 管理提升
Management Thinking Frameworks
管理知识库

第二段正文。

![原始页面图](<../assets/page-003.png>)
""",
    )
    before = {path.name: path.read_bytes() for path in (p1, p2, p3)}

    planned = cleanup.cleanup_directory(page_units, profile=explicit_profile(), apply=False)

    assert planned["mode"] == "scan"
    assert planned["status"] == "passed"
    assert planned["counts"]["planned_deletions"] == 12
    assert {path.name: path.read_bytes() for path in (p1, p2, p3)} == before

    result = cleanup.cleanup_directory(page_units, profile=explicit_profile(), apply=True)

    assert result["mode"] == "apply"
    assert result["status"] == "passed"
    assert result["counts"]["changed_files"] == 3
    assert result["counts"]["deleted_lines"] == 12
    assert result["checks"]["delete_only_reconstruction"] is True
    assert result["checks"]["image_links_unchanged"] is True
    assert result["checks"]["all_files_nonempty"] is True
    assert result["residual_high_confidence"] == []

    cleaned1 = p1.read_text(encoding="utf-8")
    cleaned2 = p2.read_text(encoding="utf-8")
    cleaned3 = p3.read_text(encoding="utf-8")
    assert "正文保留“318个管理思维”这一书名" in cleaned1
    assert "- -13-" not in cleaned1
    assert "\n---\n" in cleaned1  # 正文内部横线保留
    assert "![原始页面图](<../assets/page-001.png>)" in cleaned1
    assert "<!-- original-page-image: ../assets/page-002.png -->" in cleaned2
    assert "Unknown/needs-review" in cleaned2
    assert "Management Thinking" not in cleaned2
    assert "第一段正文。" in cleaned3 and "第二段正文。" in cleaned3


def test_standalone_header_word_inside_body_is_retained_for_review(tmp_path: Path):
    page_units = tmp_path / "page-units"
    page = write_page(
        page_units,
        1,
        """# 真正标题

第一段正文。

管理知识库

第二段正文。

![原始页面图](<../assets/page-001.png>)
""",
    )
    before = page.read_bytes()

    result = cleanup.cleanup_directory(page_units, profile=explicit_profile(), apply=True)

    assert result["status"] == "needs-review"
    assert page.read_bytes() == before
    assert result["counts"]["changed_files"] == 0
    assert result["suspects"] == [
        {
            "file": "p001.md",
            "line": 5,
            "kind": "header-like-outside-safe-zone",
            "rule_id": "knowledge-library",
            "raw": "管理知识库",
        }
    ]


def test_auto_discovery_learns_repeated_boundary_headers_and_ocr_variants(tmp_path: Path):
    page_units = tmp_path / "page-units"
    banners = [
        "ACME Management Handbook",
        "ACME Management Handbook",
        "ACME Management Handbook",
        "ACME Management Handbook",
        "ACME Managernent Handbook",
        "ACME Management Handbok",
    ]
    for page, banner in enumerate(banners, 1):
        write_page(
            page_units,
            page,
            f"""{banner}
Corporate Knowledge Base

# 第 {page} 页真正标题

正文 {page}。

![原始页面图](<../assets/page-{page:03d}.png>)
""",
        )
    profile = {
        "version": "1.0.0",
        "discovery": {
            "enabled": True,
            "minimum_occurrences": 3,
            "minimum_page_ratio": 0,
            "fuzzy_similarity": 0.78,
        },
    }

    result = cleanup.cleanup_directory(page_units, profile=profile, apply=True)

    assert result["status"] == "passed"
    assert result["counts"]["changed_files"] == 6
    assert result["counts"]["deleted_lines"] == 12
    assert result["discovered_rules"]
    for page in range(1, 7):
        text = (page_units / f"p{page:03d}.md").read_text(encoding="utf-8")
        assert "ACME" not in text
        assert "Corporate Knowledge Base" not in text
        assert f"# 第 {page} 页真正标题" in text
        assert f"../assets/page-{page:03d}.png" in text


def test_page_number_is_deleted_only_in_footer_before_original_image(tmp_path: Path):
    page_units = tmp_path / "page-units"
    page = write_page(
        page_units,
        1,
        """# 真正标题

- 12 -

这是正文编号，不是页脚。

–175–

![原始页面图](<../assets/page-001.png>)
""",
    )

    result = cleanup.cleanup_directory(
        page_units,
        profile={"version": "1.0.0", "discovery": {"enabled": False}},
        apply=True,
    )

    assert result["status"] == "passed"
    text = page.read_text(encoding="utf-8")
    assert "- 12 -" in text
    assert "–175–" not in text
    assert result["counts_by_kind"] == {"footer-page-number": 1}


def test_hybrid_visual_supplement_and_other_semantic_headings_are_not_discovered(tmp_path: Path):
    for page in range(1, 7):
        write_page(tmp_path, page, f"# 重复章节标题\n\n正文 {page}。\n\n- {page} -\n\n"
                   f"### 视觉补充\n\n- -{page}-\n\n![原始页面图](../assets/page-{page:03d}.png)\n")

    result = cleanup.cleanup_directory(tmp_path, apply=True)

    assert result["status"] == "passed"
    assert result["discovered_rules"] == []
    assert result["counts_by_kind"] == {"footer-page-number": 12}
    assert result["residual_high_confidence"] == []
    text = (tmp_path / "p001.md").read_text(encoding="utf-8")
    assert "# 重复章节标题" in text and "### 视觉补充" in text
    assert "正文 1。" in text and "../assets/page-001.png" in text


def test_adjacent_different_numbers_are_not_assumed_duplicate_footers(tmp_path: Path):
    path = write_page(tmp_path, 1, "正文。\n\n- 12 -\n- 13 -\n\n![原始页面图](../assets/page-001.png)\n")

    result = cleanup.cleanup_directory(tmp_path, apply=True)

    assert result["status"] == "needs-review"
    assert "- 12 -" in path.read_text(encoding="utf-8")
    assert [entry["raw"] for entry in result["residual_high_confidence"]] == ["- 12 -"]


def test_cli_writes_auditable_evidence_without_rewriting_scan_mode(tmp_path: Path):
    page_units = tmp_path / "page-units"
    page = write_page(
        page_units,
        1,
        """管理知识库

# 真正标题

正文。

![原始页面图](<../assets/page-001.png>)
""",
    )
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(explicit_profile(), ensure_ascii=False), encoding="utf-8")
    evidence_path = tmp_path / "noise-cleanup.json"
    before = page.read_bytes()

    exit_code = cleanup.main(
        [
            "--page-units",
            str(page_units),
            "--profile",
            str(profile_path),
            "--evidence",
            str(evidence_path),
        ]
    )

    assert exit_code == 0
    assert page.read_bytes() == before
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["mode"] == "scan"
    assert evidence["files"][0]["input_sha256"]
    assert evidence["files"][0]["deletions"][0]["raw"] == "管理知识库"


def test_contract_and_workflow_place_cleanup_before_language_repair():
    import yaml

    root = Path(__file__).resolve().parents[1]
    contract = yaml.safe_load((root / "contracts/page-markdown-noise-cleanup.yaml").read_text(encoding="utf-8"))
    workflow = yaml.safe_load(
        (root / "workflows/document-to-markdown-assets/workflow.yaml").read_text(encoding="utf-8")
    )

    assert contract["scope"] == "post-page-unit-pre-language-repair"
    assert contract["execution"]["order"][-1] == "markdown-content-repair"
    assert workflow["page_unit_execution"]["repair"]["pre_language_noise_cleanup"]["scan_before_apply"] is True
    assert workflow["execution_policy"]["language_quality_repair"]["requires"].startswith(
        "page-markdown-noise-cleanup"
    )
    assert "--noise-profile" in workflow["entrypoint"]["page_vision_pdf"]
    assert "--noise-profile" in workflow["entrypoint"]["page_vision_resume"]


def test_runtime_gate_records_scan_then_applies_identical_plan(tmp_path: Path):
    from adapters.mac96_pdf_vision_runner import cleanup_page_units_before_language_repair

    target = tmp_path / "package"
    page_units = target / "page-units"
    (target / "evidence").mkdir(parents=True)
    write_page(
        page_units,
        1,
        """管理知识库

# 真正标题

正文。

![原始页面图](<../assets/page-001.png>)
""",
    )
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(explicit_profile(), ensure_ascii=False), encoding="utf-8")

    result = cleanup_page_units_before_language_repair(
        target,
        pages=[1],
        profile_path=profile_path,
    )

    assert result["mode"] == "apply"
    assert result["status"] == "passed"
    assert result["scan_evidence"] == "evidence/page-markdown-noise-cleanup-scan.json"
    assert "管理知识库" not in (page_units / "p001.md").read_text(encoding="utf-8")
    scan = json.loads(
        (target / "evidence/page-markdown-noise-cleanup-scan.json").read_text(encoding="utf-8")
    )
    applied = json.loads(
        (target / "evidence/page-markdown-noise-cleanup.json").read_text(encoding="utf-8")
    )
    assert scan["files"][0]["input_sha256"] == applied["files"][0]["input_sha256"]
    assert scan["files"][0]["deletions"] == applied["files"][0]["deletions"]
