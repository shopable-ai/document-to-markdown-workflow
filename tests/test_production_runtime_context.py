from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts/workflow-a-production-runtime-context.yaml"
LONG_TASK_CONTRACT_PATH = ROOT / "contracts/long-task-interaction.yaml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflow_a_entry():
    registry = _load(ROOT / "workflow-registry.yaml")
    return next(
        item
        for item in registry["workflows"]
        if item["id"] == "document-to-markdown-assets"
    )


def test_registry_exposes_the_production_runtime_context():
    registry = _load(ROOT / "workflow-registry.yaml")
    entry = _workflow_a_entry()

    assert "production_runtime_contract" in registry["schema"]["optional_entry_fields"]
    assert entry["production_runtime_contract"] == str(
        CONTRACT_PATH.relative_to(ROOT)
    )
    assert entry["background_task_contract"] == str(
        LONG_TASK_CONTRACT_PATH.relative_to(ROOT)
    )
    assert (
        "--task-type "
        "<workflow-a|native-pdf|native-pdf-batch|local-ocr-pdf|hybrid-pdf|visual-pdf>"
        in entry["background_task_entrypoint"]
    )
    assert CONTRACT_PATH.is_file()
    assert LONG_TASK_CONTRACT_PATH.is_file()


def test_production_context_keeps_requirement_history_and_source_code_out():
    contract = _load(CONTRACT_PATH)
    excluded = set(contract["default_context"]["do_not_load_by_default"])

    assert {
        ".prompt/**",
        "GOAL.zh.md",
        "TASK-TREE.zh.md",
        "workflows/document-to-markdown-assets/workflow.yaml",
        "adapters/**/*.py",
        "tests/**",
        "archive/**",
    } <= excluded
    assert contract["execution"]["source_code"]["default"] == "do-not-inspect"
    assert contract["execution"]["parameter_discovery"].endswith("--help")
    assert len(contract["execution"]["source_code"]["inspection_triggers"]) == 3


def test_production_context_uses_compact_progress_and_independent_acceptance():
    contract = _load(CONTRACT_PATH)
    workflow = _load(ROOT / "workflows/document-to-markdown-assets/workflow.yaml")
    during_run = contract["observability"]["during_run"]

    assert during_run["first_read"] == "progress-current.json"
    assert during_run["never_bulk_read"] == "progress.jsonl"
    assert contract["acceptance"]["passed_requires"] == "independent-validator-passed"
    assert workflow["execution_observability"]["status_meanings"]["passed"].startswith(
        "independent-validator-passed;"
    )


def test_agents_routes_daily_execution_through_the_compact_contract():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "contracts/workflow-a-production-runtime-context.yaml" in agents
    assert "正常生产执行不得读取 `adapters/*.py`" in agents


def test_long_tasks_default_to_detached_silent_mode():
    production = _load(CONTRACT_PATH)
    contract = _load(LONG_TASK_CONTRACT_PATH)

    assert production["execution"]["long_tasks"]["threshold_seconds"] == 30
    assert production["execution"]["long_tasks"]["default_mode"] == "detached-silent"
    assert contract["scope"]["default_mode"] == "detached-silent"
    assert contract["dialogue_policy"]["start"]["max_messages"] == 1
    assert contract["dialogue_policy"]["periodic_progress"]["max_messages"] == 0
    assert contract["dialogue_policy"]["terminal"]["max_messages"] == 1


def test_long_task_status_is_latest_only_and_agent_polling_is_forbidden():
    contract = _load(LONG_TASK_CONTRACT_PATH)
    query = contract["dialogue_policy"]["user_status_query"]
    orchestration = contract["agent_orchestration"]

    assert query["read"] == "progress-current.json"
    assert query["never_read_for_summary"] == "progress.jsonl"
    assert query["response"] == "latest-snapshot-only"
    assert orchestration["autonomous_polling"] == "forbidden"
    assert orchestration["tool_stdout_forwarding"] == "forbidden"
    assert orchestration["task_survives_conversation_close"] is True
    assert orchestration["automatic_polling_resume_on_new_conversation"] is False
    assert contract["commands"]["resume_support"] == {
        "native-pdf": "supported-reuse-existing-run-root",
        "native-pdf-batch": "supported-reuse-existing-run-root",
        "local-ocr-pdf": "supported-reuse-existing-run-root",
        "hybrid-pdf": "supported-reuse-existing-run-root-with-page-route-validation",
        "visual-pdf": "supported-reuse-existing-run-root",
        "workflow-a": "unsupported-until-source-runner-has-a-verified-in-place-resume-contract",
    }


def test_pdf_production_route_defaults_to_page_hybrid_not_full_visual():
    contract = _load(CONTRACT_PATH)
    entry = _workflow_a_entry()
    pdf_route = contract["execution"]["media_routes"]["pdf"]

    assert pdf_route["default_mode"] == "page-hybrid"
    assert pdf_route["page_routes"] == [
        "native-only",
        "native-plus-visual",
        "visual-only",
    ]
    assert pdf_route["explicit_full_visual"] == "--visual-only"
    assert "--visual-only" not in entry["pdf_execution_entrypoint"]
    assert "--visual-only" in entry["pdf_visual_only_entrypoint"]
    assert contract["execution"]["long_tasks"]["pdf_default_task_type"] == "hybrid-pdf"


def test_long_task_contract_separates_the_four_consumption_categories():
    contract = _load(LONG_TASK_CONTRACT_PATH)

    assert set(contract["scope"]["problem_boundary"]) == {
        "background_task_resource_consumption",
        "current_conversation_context_consumption",
        "agent_polling_consumption",
        "user_query_consumption",
    }
