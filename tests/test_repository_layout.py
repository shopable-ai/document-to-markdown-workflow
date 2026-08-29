import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP_PROMPTS = (
    "新对话继续推进_GOAL.md",
    "新对话继续推进_HANDOFF_20260814.md",
    "新对话继续推进_REAL_EXECUTION_GOAL.md",
)


def test_temporary_prompts_are_kept_under_prompt_directory():
    prompt_root = ROOT / ".prompt"
    assert prompt_root.is_dir()
    for name in TEMP_PROMPTS:
        assert not (ROOT / name).exists()
        assert (prompt_root / name).is_file()


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
