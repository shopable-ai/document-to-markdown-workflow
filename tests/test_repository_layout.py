import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TEMP_PROMPT_ROOT = ROOT / ".prompt"
MANUAL_PROMPT_ROOT = ROOT / "prompts"
PROMPT_HISTORY_ROOT = ROOT / "archive/prompt-history"


def test_manual_ebook_prompts_are_directly_accessible_from_the_homepage():
    index_path = MANUAL_PROMPT_ROOT / "README.md"
    index = index_path.read_text(encoding="utf-8")
    homepage = (ROOT / "README.md").read_text(encoding="utf-8")
    assert not list(TEMP_PROMPT_ROOT.glob("ebook-md-*.md"))
    assert not list((ROOT / "提示词").glob("ebook-md-*.md"))
    for edition in ("简版", "完整版"):
        name = f"ebook-md-电子书格式内容修复-{edition}.md"
        prompt = MANUAL_PROMPT_ROOT / name
        assert prompt.is_file()
        assert f"]({name})" in index
        assert f"]({prompt.relative_to(ROOT).as_posix()})" in homepage
    relative_index = index_path.relative_to(ROOT).as_posix()
    assert f"]({relative_index})" in homepage
    project_overview = (ROOT / "项目说明.md").read_text(encoding="utf-8")
    assert f"]({relative_index})" in project_overview
    temporary_index = (TEMP_PROMPT_ROOT / "README.zh.md").read_text(encoding="utf-8")
    assert f"](../{relative_index})" in temporary_index


def test_manual_prompts_are_excluded_from_default_production_context():
    contract_path = ROOT / "contracts/workflow-a-production-runtime-context.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    exclusions = contract["default_context"]["do_not_load_by_default"]
    assert f"{MANUAL_PROMPT_ROOT.name}/**" in exclusions


def test_archived_prompts_are_indexed_without_temporary_copies():
    index_path = PROMPT_HISTORY_ROOT / "README.zh.md"
    index = index_path.read_text(encoding="utf-8")
    snapshots = sorted(path for path in PROMPT_HISTORY_ROOT.glob("*.md") if path != index_path)
    assert snapshots
    for snapshot in snapshots:
        assert f"]({snapshot.name})" in index
        assert not (TEMP_PROMPT_ROOT / snapshot.name).exists()
        assert not (ROOT / snapshot.name).exists()


def test_real_run_directories_are_not_at_repository_root():
    assert not list(ROOT.glob("real-runs-*"))
    runs_root = ROOT / "runs"
    if runs_root.exists():
        assert runs_root.is_dir()
        # New production packages live directly under runs/<source-name>.
        # Older runs/real evidence is historical and is not a current route.
        assert not (ROOT / "runs" / "real-runs-*").exists()


def test_pdf_route_detection_runs_use_scoped_short_names():
    route_root = ROOT / "runs" / "pdf-route-detection"
    assert "runs/pdf-route-detection/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    if not route_root.exists():
        return
    run_dirs = [child for child in route_root.iterdir() if child.is_dir()]
    assert all(re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{10}", child.name) for child in run_dirs)


def test_engineering_audits_are_local_ignored_snapshots():
    assert ".audit/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert not (ROOT / "audit").exists()
    audit_root = ROOT / ".audit"
    if not audit_root.exists():
        return
    for name in ("AUDIT-MATRIX.zh.md", "AUDIT-P24-P25.zh.md", "QUALITY-SCORE.zh.md"):
        assert not (ROOT / name).exists()
        assert (audit_root / name).is_file()


def test_active_stage_gate_docs_do_not_hardcode_6_to_100_for_full_batch():
    expected = ("6-total_pages", "6-total-pages", "6-文档末页", "6-文档末")
    checked = {
        "workflows/document-to-markdown-assets/workflow.yaml": True,
        "contracts/page-markdown-unit.yaml": True,
        "execution/WORKFLOW-A-EXECUTION.md": True,
        "workflows/document-to-markdown-assets/ACCEPTANCE.zh.md": True,
    }

    for relative in checked:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "6-100" not in text, relative
        assert any(marker in text for marker in expected), relative


def test_ebook_page_pruning_is_document_driven_across_requirement_chain():
    required_markers = {
        "requirements/REQUIREMENTS.md": "### REQ-DOC-019 知识库低价值整页清洗只能晚于完整汇合",
        "requirements/REQUIREMENT-TREE.md": "REQ-DOC-019：完整汇合后的知识库工作副本清洗",
        "design/WORKFLOW-MAP.md": "可选 post-complete 清洗",
        "execution/ENGINEERING-TASK-TREE.md": "ET5-01 知识库工作副本清洗",
        "workflows/document-to-markdown-assets/workflow.yaml": "knowledge_base_page_pruning:",
    }

    for relative, marker in required_markers.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert marker in text, relative

    requirement_chain = (ROOT / "contracts/requirement-chain.yaml").read_text(
        encoding="utf-8"
    )
    assert "optional_downstream_knowledge_base_pruning" in requirement_chain
    assert "downstream_knowledge_base_pruning" in requirement_chain
    assert "contracts/ebook-page-pruning.yaml" in requirement_chain
    assert (ROOT / "contracts/ebook-page-pruning.yaml").is_file()
    assert (ROOT / "skills/prune-low-value-ebook-pages/SKILL.md").is_file()
