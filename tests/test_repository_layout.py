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
    real_root = ROOT / "runs" / "real"
    if real_root.exists():
        assert real_root.is_dir()
        # macOS metadata files may exist beside ignored run directories; they
        # are not run records and must not make the layout check fail.
        run_dirs = [child for child in real_root.iterdir() if child.is_dir()]
        assert all(child.name.startswith("real-runs-") for child in run_dirs)


def test_engineering_audits_are_local_ignored_snapshots():
    assert ".audit/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert not (ROOT / "audit").exists()
    audit_root = ROOT / ".audit"
    if not audit_root.exists():
        return
    for name in ("AUDIT-MATRIX.zh.md", "AUDIT-P24-P25.zh.md", "QUALITY-SCORE.zh.md"):
        assert not (ROOT / name).exists()
        assert (audit_root / name).is_file()
