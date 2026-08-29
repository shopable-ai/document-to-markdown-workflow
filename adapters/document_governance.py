"""Pure validators for the document-driven governance contracts.

These helpers deliberately validate records passed by a caller. They do not
load project files, mutate runs, or promote a candidate by themselves.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
INDEX_GENERATOR_VERSION = "document-governance-index/0.1.0"

def _missing(record: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    return [field for field in fields if field not in record or record[field] in (None, "", [])]


def validate_artifact_ref(reference: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("ref_id", "artifact_type", "locator", "version_or_hash", "authority_class", "status_layer", "scope"):
        if field not in reference or reference[field] in (None, ""):
            errors.append(f"missing:{field}")
    prohibited = {"candidate-only", "reference-only", "test-only", "historical-run"}
    if reference.get("authority_class") in prohibited and reference.get("usable_for_claim") is True:
        errors.append("non-authoritative-reference-cannot-support-claim")
    return errors


def validate_transition(
    layer: str,
    from_status: str,
    to_status: str,
    record: Mapping[str, Any],
    matrix: Mapping[str, Any],
) -> list[str]:
    layer_spec = (matrix.get("layers") or {}).get(layer)
    if not layer_spec:
        return [f"unknown-layer:{layer}"]
    statuses = set(layer_spec.get("statuses", []))
    if from_status not in statuses or to_status not in statuses:
        return ["unknown-status"]
    transition = next(
        (
            item
            for item in layer_spec.get("transitions", [])
            if item.get("from") == from_status and item.get("to") == to_status
        ),
        None,
    )
    if transition is None:
        return [f"forbidden-transition:{layer}.{from_status}->{to_status}"]
    return [f"missing:{field}" for field in _missing(record, transition.get("requires", []))]


def validate_temporal_case(case: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ("case_id", "trigger", "preconditions", "events", "relations", "decision_points", "branches", "recovery", "terminal_states", "unknowns")
    errors.extend(
        f"missing:{field}"
        for field in required
        if field not in case or case[field] in (None, "")
    )
    events = case.get("events") or []
    event_ids = [event.get("event_id") for event in events if isinstance(event, Mapping)]
    if len(event_ids) != len(set(event_ids)):
        errors.append("duplicate:event_id")
    event_required = ("event_id", "event_type", "actor_ref", "action", "stage_ref", "input_refs", "output_refs", "state_before", "state_after", "evidence_refs", "observed_order", "relation_status")
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            errors.append(f"event-not-mapping:{index}")
            continue
        errors.extend(f"events[{index}].missing:{field}" for field in _missing(event, event_required))
    known_events = set(event_ids)
    relation_required = ("relation_id", "from_event", "to_event", "order_type", "basis", "evidence_refs")
    valid_order_types = {"temporal", "causal", "dependency", "priority", "reading", "execution"}
    for index, relation in enumerate(case.get("relations") or []):
        if not isinstance(relation, Mapping):
            errors.append(f"relation-not-mapping:{index}")
            continue
        errors.extend(f"relations[{index}].missing:{field}" for field in _missing(relation, relation_required))
        if relation.get("from_event") not in known_events or relation.get("to_event") not in known_events:
            errors.append(f"relations[{index}].unknown-event")
        if relation.get("order_type") not in valid_order_types:
            errors.append(f"relations[{index}].invalid-order-type")
        if relation.get("order_type") == "causal":
            mechanism_fields = ("mechanism_ref", "rule_ref", "contrast_ref")
            if relation.get("basis") == "observed-order-only":
                errors.append(f"relations[{index}].causal-from-order-only")
            if not any(relation.get(field) for field in mechanism_fields):
                errors.append(f"relations[{index}].causal-missing-basis")
            if not relation.get("evidence_refs"):
                errors.append(f"relations[{index}].causal-missing-evidence")
    return errors


def validate_failure_route(failure: Mapping[str, Any]) -> list[str]:
    required = ("failure_id", "event_ref", "symptom", "evidence_refs", "earliest_owner_layer", "owner_artifact_ref", "reopen_from_stage", "blocking_reason")
    return [f"missing:{field}" for field in _missing(failure, required)]


def validate_projection_record(record: Mapping[str, Any]) -> list[str]:
    required = ("projection_id", "host_stage_id", "baseline_ref", "workflow_ref", "selected_refs", "excluded_refs", "selection_reason", "authorization_basis", "status")
    errors = [f"missing:{field}" for field in _missing(record, required)]
    if record.get("status") == "projected" and record.get("host_stage_id") != "P12":
        errors.append("projected-record-not-hosted-by-P12")
    selected = record.get("selected_refs")
    if isinstance(selected, Mapping):
        for field in ("source_refs", "workflow_refs", "skill_refs", "tool_refs", "model_or_route_refs", "context_refs"):
            if field not in selected:
                errors.append(f"selected_refs.missing:{field}")
    excluded = record.get("excluded_refs")
    if isinstance(excluded, Mapping):
        errors.extend(f"excluded_refs.missing:{field}" for field in ("refs", "reasons") if field not in excluded)
    return errors


def validate_change_record(record: Mapping[str, Any]) -> list[str]:
    required = ("change_id", "upstream_refs", "supersedes", "reason", "impact_classification", "affected_refs", "requires_revalidation", "revalidation_status", "owner")
    errors = [f"missing:{field}" for field in _missing(record, required)]
    if record.get("impact_classification") == "invalidated" and not record.get("affected_refs"):
        errors.append("invalidated-change-missing-affected-refs")
    if record.get("requires_revalidation") is True and record.get("revalidation_status") == "not_required":
        errors.append("revalidation-required-but-marked-not-required")
    return errors


def evaluate_scorecard(scorecard: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    required = ("scorecard_id", "subject_type", "subject_ref", "baseline_ref", "projection_ref", "run_ref", "dimensions", "hard_gates", "evidence_refs", "total_score", "input_digest", "generated_by", "validated_by", "scored_by", "validator_execution_id", "independence_mode")
    errors.extend(f"missing:{field}" for field in _missing(scorecard, required))
    dimensions = scorecard.get("dimensions") or []
    dimension_scores = [item.get("score") for item in dimensions if isinstance(item, Mapping) and isinstance(item.get("score"), int)]
    if len(dimension_scores) != len(dimensions):
        errors.append("invalid:dimension-score")
    if isinstance(scorecard.get("total_score"), int) and dimension_scores and sum(dimension_scores) != scorecard["total_score"]:
        errors.append("total-score-mismatch")
    for index, item in enumerate(dimensions):
        if isinstance(item, Mapping) and item.get("score", 0) < item.get("pass_line", 0):
            errors.append(f"dimension-below-pass-line:{index}")
    hard_gates = scorecard.get("hard_gates") or []
    hard_gate_failed = any(isinstance(item, Mapping) and item.get("passed") is not True for item in hard_gates)
    if hard_gate_failed:
        errors.append("hard-gate-failed")
    independent = (
        scorecard.get("independence_mode") in {"independent-human", "independent-validator", "cross-run-independent-validator"}
        and scorecard.get("generated_by") != scorecard.get("validated_by")
        and scorecard.get("generated_by") != scorecard.get("scored_by")
        and bool(scorecard.get("validator_execution_id"))
    )
    if not independent:
        errors.append("independence-not-proven")
    total = scorecard.get("total_score") if isinstance(scorecard.get("total_score"), int) else -1
    if total < 95:
        errors.append("below-minimum-score")
    if not errors:
        verdict_status = "passed"
    elif "below-minimum-score" in errors or "hard-gate-failed" in errors:
        verdict_status = "failed"
    else:
        verdict_status = "needs-review"
    return {
        "errors": errors,
        "verdict_status": verdict_status,
        "quality_band": "target" if total >= 97 else "minimum-pass" if total >= 95 else "below-minimum",
        "claim_ceiling": "target" if total >= 97 else "bounded" if total >= 95 else "none",
    }


def evaluate_claim_gate(context: Mapping[str, Any]) -> dict[str, Any]:
    invalidating = {"baseline_change", "projection_change", "input_digest_change", "evidence_stale", "validator_retraction"}
    if context.get("invalidation_reason") in invalidating:
        return {"status": "invalidated", "errors": ["upstream-invalidated"]}
    errors = []
    if context.get("baseline_status") != "confirmed":
        errors.append("baseline_status!=confirmed")
    if context.get("projection_status") != "projected":
        errors.append("projection_status!=projected")
    if context.get("run_status") != "succeeded":
        errors.append("run_status!=succeeded")
    if context.get("evidence_status") not in {"passed", "complete"}:
        errors.append("evidence_status!=passed-or-complete")
    if context.get("verdict_status") != "passed":
        errors.append("verdict_status!=passed")
    if context.get("total_score", 0) < 95:
        errors.append("score<95")
    if context.get("hard_gates_passed") is not True:
        errors.append("hard-gates-not-passed")
    total = context.get("total_score", 0)
    return {
        "status": "allowed" if not errors else "blocked",
        "errors": errors,
        "claim_ceiling": "target" if total >= 97 else "bounded" if total >= 95 else "none",
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_markdown_table(path: Path, key_column: str) -> list[dict[str, str]]:
    """Read one authoritative pipe table without treating prose as machine truth."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        headers = [cell.strip() for cell in line.strip("|").split("|")]
        if key_column not in headers:
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("|"):
            continue
        rows: list[dict[str, str]] = []
        for row_line in lines[index + 2 :]:
            if not row_line.startswith("|"):
                break
            values = [cell.strip() for cell in row_line.strip("|").split("|")]
            if len(values) != len(headers):
                raise ValueError(f"{path}: table row width does not match header: {row_line}")
            row = dict(zip(headers, values, strict=True))
            if row[key_column]:
                rows.append(row)
        if not rows:
            raise ValueError(f"{path}: authoritative table {key_column!r} is empty")
        ids = [row[key_column] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{path}: duplicate {key_column}")
        return rows
    raise ValueError(f"{path}: missing authoritative table headed by {key_column!r}")


def build_markdown_index(source: Path, key_column: str) -> dict[str, Any]:
    return {
        "schema": "governance-derived-index",
        "version": "0.1.0",
        "generated_from": str(source.relative_to(ROOT)),
        "source_hash": _sha256(source),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "generator_version": INDEX_GENERATOR_VERSION,
        "do_not_edit": True,
        "item_key": key_column,
        "items": parse_markdown_table(source, key_column),
    }


def write_markdown_index(source: Path, destination: Path, key_column: str) -> None:
    generated = build_markdown_index(source, key_column)
    if destination.exists():
        previous = yaml.safe_load(destination.read_text(encoding="utf-8")) or {}
        if (
            previous.get("source_hash") == generated["source_hash"]
            and previous.get("generator_version") == generated["generator_version"]
            and previous.get("generated_at")
        ):
            generated["generated_at"] = previous["generated_at"]
    destination.write_text(
        yaml.safe_dump(generated, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def validate_markdown_index(source: Path, destination: Path, key_column: str) -> list[str]:
    if not destination.is_file():
        return [f"missing-index:{destination.relative_to(ROOT)}"]
    current = yaml.safe_load(destination.read_text(encoding="utf-8")) or {}
    expected = build_markdown_index(source, key_column)
    errors: list[str] = []
    for field in (
        "schema",
        "version",
        "generated_from",
        "source_hash",
        "generator_version",
        "do_not_edit",
        "item_key",
        "items",
    ):
        if current.get(field) != expected.get(field):
            errors.append(f"derived-index-drift:{destination.name}:{field}")
    if not current.get("generated_at"):
        errors.append(f"derived-index-drift:{destination.name}:generated_at")
    return errors


def governance_index_specs() -> tuple[tuple[Path, Path, str], ...]:
    return (
        (
            ROOT / "requirements/REQUIREMENTS.md",
            ROOT / "requirements/requirements.index.yaml",
            "requirement_id",
        ),
        (
            ROOT / "requirements/FUNCTIONS.md",
            ROOT / "requirements/functions.index.yaml",
            "function_id",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or check Markdown-authoritative governance indices.")
    parser.add_argument("command", choices=("generate-indices", "check-indices"))
    args = parser.parse_args()
    if args.command == "generate-indices":
        for source, destination, key_column in governance_index_specs():
            write_markdown_index(source, destination, key_column)
        return 0
    errors = [
        error
        for source, destination, key_column in governance_index_specs()
        for error in validate_markdown_index(source, destination, key_column)
    ]
    if errors:
        for error in errors:
            print(error)
        return 1
    print("PASS: Markdown-derived requirement and function indices are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
