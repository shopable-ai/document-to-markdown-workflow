from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_contract():
    with (ROOT / "contracts/document-driven-workflow.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_project_navigation_is_distinct_from_execution_stage_ids():
    contract = load_contract()
    stages = contract["stages"]

    assert len(stages) == 16
    assert [stage["id"] for stage in stages] == [f"P{index:02d}" for index in range(1, 17)]
    assert not {stage["id"] for stage in stages} & {f"A{index}" for index in range(1, 8)}
    assert not {stage["id"] for stage in stages} & {f"B{index}" for index in range(1, 8)}


def test_every_project_stage_has_a_question_artifacts_gate_and_projection():
    contract = load_contract()

    for stage in contract["stages"]:
        assert stage["name"]
        assert stage["required_question"]
        assert stage["artifacts"]
        assert stage["gate"]
        assert stage["projection"]


def test_governance_documents_define_drift_repair_and_preserve_history():
    universe = (ROOT / "AI-WORKFLOW-UNIVERSE.zh.md").read_text(encoding="utf-8")
    navigator = (ROOT / "PROJECT-DELIVERY-WORKFLOW.zh.md").read_text(encoding="utf-8")
    governance = (ROOT / "ARTIFACT-AND-CHANGE-GOVERNANCE.zh.md").read_text(encoding="utf-8")

    for marker in ("控制平面", "认知平面", "设计平面", "执行平面", "证据平面"):
        assert marker in universe
    for marker in (
        "P01",
        "P08",
        "P13",
        "P16",
        "A1–A7",
        "B1–B7",
        "Business Requirement",
        "Business Chain",
        "Functional Requirement",
    ):
        assert marker in navigator
    for marker in ("D1", "D8", "最早责任层", "新版本", "历史 Run"):
        assert marker in governance

    consensus = (ROOT / "EXPERT-CONSENSUS-REVIEW.zh.md").read_text(encoding="utf-8")
    for marker in ("5 个专业角色", "40 次专家发言", "最小对象化调整", "95", "97", "全量图数据库"):
        assert marker in consensus


def test_reasoning_overlay_is_candidate_only_and_keeps_j_stages_out_of_main_flow():
    with (ROOT / "contracts/cognitive-reasoning-overlay.yaml").open(encoding="utf-8") as handle:
        overlay = yaml.safe_load(handle)
    assert overlay["status"] == "reference-candidate"
    assert overlay["authority"] == "non-normative"
    assert overlay["activation"]["mode"] == "opt-in"
    assert overlay["reasoning_artifacts"]["cognitive_observation"]["stage_ids"] == [
        f"J{index:02d}" for index in range(1, 19)
    ]
    universe = (ROOT / "AI-WORKFLOW-UNIVERSE.zh.md").read_text(encoding="utf-8")
    overlay_doc = (ROOT / "COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.zh.md").read_text(encoding="utf-8")
    for marker in ("U21", "U22", "U23", "U24", "认知与时序是叠加层"):
        assert marker in universe
    for marker in ("用户目标", "用户需求", "用户问题", "时间顺序", "因果顺序", "J18", "reference-candidate"):
        assert marker in overlay_doc
