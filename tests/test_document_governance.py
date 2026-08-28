from io import StringIO
from pathlib import Path

import pytest
import yaml

from adapters.document_governance import (
    evaluate_claim_gate,
    evaluate_scorecard,
    validate_artifact_ref,
    validate_change_record,
    validate_failure_route,
    validate_projection_record,
    validate_temporal_case,
    validate_transition,
)
from tests.validate_workflow_package import UniqueKeyLoader


ROOT = Path(__file__).resolve().parents[1]


def load_contract(name: str):
    with (ROOT / "contracts" / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def make_event(event_id: str, order: int) -> dict:
    return {
        "event_id": event_id,
        "event_type": "stage-completed",
        "actor_ref": "role:executor",
        "action": "完成阶段",
        "stage_ref": "A4",
        "input_refs": ["source:1"],
        "output_refs": [f"evidence:{event_id}"],
        "state_before": "running",
        "state_after": "complete",
        "evidence_refs": [f"evidence:{event_id}"],
        "observed_order": order,
        "relation_status": "observed",
    }


def make_scorecard(**overrides) -> dict:
    contract = load_contract("acceptance-scorecard.yaml")
    dimensions = [
        {"id": item["id"], "score": item["maximum"], "pass_line": item["pass_line"], "evidence_refs": [f"evidence:{item['id']}"]}
        for item in contract["dimension_policy"]
    ]
    scorecard = {
        "scorecard_id": "scorecard:001",
        "subject_type": "package",
        "subject_ref": "package:001",
        "baseline_ref": "baseline:001",
        "projection_ref": "projection:001",
        "run_ref": "run:001",
        "dimensions": dimensions,
        "hard_gates": [
            {"id": gate_id, "passed": True, "evidence_refs": [f"evidence:{gate_id}"], "blocking_reason": None}
            for gate_id in contract["hard_gate"]["ids"]
        ],
        "evidence_refs": ["evidence:001"],
        "total_score": 100,
        "input_digest": "sha256:current",
        "generated_by": "executor:001",
        "validated_by": "validator:001",
        "scored_by": "validator:001",
        "validator_execution_id": "validation-run:001",
        "independence_mode": "independent-validator",
    }
    scorecard.update(overrides)
    return scorecard


def test_duplicate_yaml_keys_are_rejected():
    with pytest.raises(AssertionError, match="duplicate YAML key"):
        yaml.load(StringIO("id: first\nid: second\n"), Loader=UniqueKeyLoader)


def test_candidate_cannot_be_confirmed_without_promotion_evidence():
    matrix = load_contract("promotion-matrix.yaml")
    assert validate_transition("reasoning", "candidate", "confirmed", {}, matrix)
    record = {
        field: field
        for field in matrix["layers"]["reasoning"]["transitions"][0]["requires"]
    }
    assert validate_transition("reasoning", "candidate", "confirmed", record, matrix) == []
    assert validate_transition("reasoning", "confirmed", "allowed", record, matrix)


def test_temporal_case_rejects_order_only_causality():
    case = {
        "case_id": "temporal:001",
        "trigger": "source received",
        "preconditions": ["source-readable"],
        "events": [make_event("E1", 1), make_event("E2", 2)],
        "relations": [
            {
                "relation_id": "R1",
                "from_event": "E1",
                "to_event": "E2",
                "order_type": "causal",
                "basis": "observed-order-only",
                "evidence_refs": ["evidence:E1-E2"],
            }
        ],
        "decision_points": [],
        "branches": [],
        "recovery": [],
        "terminal_states": [],
        "unknowns": [],
    }
    errors = validate_temporal_case(case)
    assert "relations[0].causal-from-order-only" in errors
    assert "relations[0].causal-missing-basis" in errors


def test_temporal_case_accepts_causal_relation_with_mechanism_evidence():
    case = {
        "case_id": "temporal:002",
        "trigger": "source received",
        "preconditions": [],
        "events": [make_event("E1", 1), make_event("E2", 2)],
        "relations": [
            {
                "relation_id": "R1",
                "from_event": "E1",
                "to_event": "E2",
                "order_type": "causal",
                "basis": "inferred",
                "mechanism_ref": "contract:route-timeout",
                "evidence_refs": ["evidence:E1-E2"],
            }
        ],
        "decision_points": [],
        "branches": [],
        "recovery": [],
        "terminal_states": [],
        "unknowns": [],
    }
    assert validate_temporal_case(case) == []


def test_failure_route_requires_earliest_owner_and_reopen_stage():
    assert validate_failure_route({"failure_id": "F1"})
    failure = {
        "failure_id": "F1",
        "event_ref": "E2",
        "symptom": "broken link",
        "evidence_refs": ["evidence:F1"],
        "earliest_owner_layer": "D7",
        "owner_artifact_ref": "contract:stage-evidence",
        "reopen_from_stage": "P14",
        "blocking_reason": "current package cannot be claimed",
    }
    assert validate_failure_route(failure) == []


def test_scorecard_requires_score_sum_hard_gates_and_independence():
    result = evaluate_scorecard(make_scorecard())
    assert result["errors"] == []
    assert result["verdict_status"] == "passed"
    assert result["claim_ceiling"] == "target"

    hard_gate_failed = make_scorecard(
        hard_gates=[
            {"id": "provenance-complete", "passed": False, "evidence_refs": ["evidence:bad"], "blocking_reason": "missing"}
        ]
    )
    failed_result = evaluate_scorecard(hard_gate_failed)
    assert failed_result["verdict_status"] == "failed"
    assert "hard-gate-failed" in failed_result["errors"]

    self_checked = make_scorecard(validated_by="executor:001", scored_by="executor:001")
    self_result = evaluate_scorecard(self_checked)
    assert self_result["verdict_status"] == "needs-review"
    assert "independence-not-proven" in self_result["errors"]


def test_claim_gate_allows_bounded_95_plus_and_invalidates_stale_context():
    context = {
        "baseline_status": "confirmed",
        "projection_status": "projected",
        "run_status": "succeeded",
        "evidence_status": "passed",
        "verdict_status": "passed",
        "total_score": 96,
        "hard_gates_passed": True,
    }
    assert evaluate_claim_gate(context) == {"status": "allowed", "errors": [], "claim_ceiling": "bounded"}
    assert evaluate_claim_gate({**context, "invalidation_reason": "input_digest_change"})["status"] == "invalidated"
    assert evaluate_claim_gate({**context, "evidence_status": "stale"})["status"] == "blocked"


def test_artifact_reference_rejects_non_authoritative_claim_source():
    reference = {
        "ref_id": "fixture:001",
        "artifact_type": "evidence",
        "locator": "tests/fixtures/example",
        "version_or_hash": "sha256:fixture",
        "authority_class": "test-only",
        "status_layer": "evidence",
        "scope": "fixture",
        "usable_for_claim": True,
    }
    assert "non-authoritative-reference-cannot-support-claim" in validate_artifact_ref(reference)


def test_projection_and_change_records_fail_closed_on_missing_impact_data():
    assert validate_projection_record({"projection_id": "P1", "status": "projected"})
    projection = {
        "projection_id": "projection:001",
        "host_stage_id": "P12",
        "baseline_ref": "baseline:001",
        "workflow_ref": "workflow:A",
        "selected_refs": {field: [] for field in ("source_refs", "workflow_refs", "skill_refs", "tool_refs", "model_or_route_refs", "context_refs")},
        "excluded_refs": {"refs": ["prompt:001"], "reasons": ["prompt-only"]},
        "selection_reason": "confirmed baseline and available local route",
        "authorization_basis": "baseline:001",
        "status": "projected",
    }
    assert validate_projection_record(projection) == []
    assert validate_change_record({"change_id": "C1", "impact_classification": "invalidated", "affected_refs": []})
    change = {
        "change_id": "C1",
        "upstream_refs": ["baseline:001"],
        "supersedes": ["baseline:000"],
        "reason": "acceptance rule changed",
        "impact_classification": "invalidated",
        "affected_refs": ["claim:001"],
        "requires_revalidation": True,
        "revalidation_status": "pending",
        "owner": "role:maintainer",
    }
    assert validate_change_record(change) == []
