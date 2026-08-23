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
        "WORKFLOW-MAP.zh.md": True,
        "WORKFLOW-A-EXECUTION.zh.md": True,
        "workflows/document-to-markdown-assets/ACCEPTANCE.zh.md": True,
    }

    for relative in checked:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "6-100" not in text, relative
        assert any(marker in text for marker in expected), relative


def test_ebook_page_pruning_is_document_driven_across_requirement_chain():
    required_markers = {
        "GOAL.zh.md": "### 4.8 知识库输入可以选择性剔除整页低价值内容",
        "TASK-TREE.zh.md": "十二、知识库低价值页清洗必须晚于完整汇合",
        "WORKFLOW-MAP.zh.md": "知识库导入前可选剔除整页低价值内容",
        "EXECUTION-TASK-TREE.zh.md": "#### ET5.4 可选知识库低价值页清洗与下游重建门",
        "WORKFLOW-A-EXECUTION.zh.md": "### 知识库低价值页清洗（可选下游步骤）",
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
