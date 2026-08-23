#!/usr/bin/env python3
"""Validate the workflow specification package without executing external tools.

This validator checks the contract/routing layer only. It deliberately does not
claim that planned parsers, OCR engines, mac96, or GPT-image-2 adapters exist.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def frontmatter_name(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"missing frontmatter name: {path}")
    return match.group(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    workflow_registry_path = ROOT / "workflow-registry.yaml"
    registry_path = ROOT / "skill-registry.yaml"
    tool_registry_path = ROOT / "tool-registry.yaml"
    workflow_registry = load(workflow_registry_path)
    registry = load(registry_path)
    tool_registry = load(tool_registry_path)
    assert (ROOT / "WORKFLOW-MAP.zh.md").is_file()
    assert (ROOT / "references/omitted-gates.zh.md").is_file()
    archive_root = ROOT / "archive/legacy-skills/knowledge-map-image-to-md"
    archived_skill = archive_root / "SKILL.md"
    archive_readme = archive_root / "README.zh.md"
    archive_snapshot = archive_root / "snapshot.yaml"
    assert archived_skill.is_file()
    assert archive_readme.is_file()
    assert archive_snapshot.is_file()
    snapshot = load(archive_snapshot)
    assert snapshot["kind"] == "legacy-skill-snapshot"
    assert snapshot["status"] == "historical_snapshot"
    assert snapshot["runtime_registered"] is False
    assert snapshot["original_file_deleted"] is False
    assert snapshot["line_count"] == len(archived_skill.read_text(encoding="utf-8").splitlines())
    assert snapshot["source_sha256"] == sha256_file(archived_skill)
    for contract_name in (
        "workflow-handoff.yaml",
        "workflow-run-record.yaml",
        "stage-evidence.yaml",
        "failure-report.yaml",
        "reconciliation.yaml",
        "workflow-a-output-layout.yaml",
        "workflow-b-output-layout.yaml",
        "knowledge-map-profile.yaml",
        "visual-reconstruction-profile.yaml",
        "visual-asset-classification.yaml",
        "markdown-content-repair.yaml",
        "vision-page-analysis.yaml",
        "vision-text-transcription.yaml",
        "workflow-a-preflight.yaml",
        "markdown-collection-output-layout.yaml",
        "ebook-page-pruning.yaml",
    ):
        assert (ROOT / "contracts" / contract_name).is_file(), contract_name
    assert (ROOT / "references/audit-rubric.zh.md").is_file()
    assert (ROOT / "references/quality-targets.zh.md").is_file()
    assert workflow_registry["schema"]["path_must_exist"] is True
    workflow_entries = {entry["id"]: entry for entry in workflow_registry["workflows"]}
    assert len(workflow_entries) == len(workflow_registry["workflows"]), "duplicate Workflow IDs"
    assert set(workflow_entries) == {"document-to-markdown-assets", "markdown-to-gpt-image"}
    assert workflow_entries["document-to-markdown-assets"]["aliases"] == ["knowledge-map-image-to-md"]
    assert (ROOT / "WORKFLOWS.zh.md").is_file()
    for entry in workflow_entries.values():
        for field in ("path", "readme", "acceptance"):
            target = ROOT / entry[field]
            assert target.is_file(), f"missing Workflow registry target: {target}"

    assert registry["schema"]["path_must_exist"] is True
    for entrypoint in registry["schema"].get("entrypoint_skills", []):
        assert any(entry["id"] == entrypoint for entry in registry["skills"]), f"unregistered entrypoint Skill: {entrypoint}"
    skills = {entry["id"]: entry for entry in registry["skills"]}
    assert len(skills) == len(registry["skills"]), "duplicate Skill IDs"
    assert all(not Path(entry["path"]).parts[0] == "archive" for entry in registry["skills"])

    skill_dirs = {path.name for path in (ROOT / "skills").iterdir() if path.is_dir()}
    registered_dirs = {Path(entry["path"]).parent.name for entry in registry["skills"]}
    assert skill_dirs == registered_dirs, f"orphan or unregistered Skill dirs: {skill_dirs ^ registered_dirs}"

    for skill_id, entry in skills.items():
        skill_path = ROOT / entry["path"]
        assert skill_path.is_file(), f"missing Skill file: {skill_path}"
        assert frontmatter_name(skill_path) == skill_id, f"frontmatter mismatch: {skill_path}"
        metadata = skill_path.parent / "agents" / "openai.yaml"
        assert metadata.is_file(), f"missing Skill UI metadata: {metadata}"
        load(metadata)
        if entry.get("kind") == "compatibility-entrypoint":
            target_workflow = entry.get("maps_to_workflow")
            assert target_workflow in workflow_entries, f"compatibility Skill maps to unknown Workflow: {skill_id}"
            assert skill_id in workflow_entries[target_workflow].get("aliases", []), f"Workflow registry missing alias: {skill_id}"
            profile = entry.get("profile")
            assert profile and (ROOT / profile).is_file(), f"compatibility Skill missing profile: {skill_id}"
        if entry.get("kind") == "compatibility-alias":
            target = entry.get("alias_of")
            assert target in skills, f"compatibility Skill alias points to unknown Skill: {skill_id}"
            assert target != skill_id, f"compatibility Skill alias cannot point to itself: {skill_id}"

    tools = {entry["id"]: entry for entry in tool_registry["tools"]}
    assert len(tools) == len(tool_registry["tools"]), "duplicate Tool IDs"
    allowed_implementations = set(tool_registry["schema"]["implementation_values"])
    assert all(entry["implementation"] in allowed_implementations for entry in tools.values())
    for entry in tools.values():
        for skill_id in entry["used_by"]:
            assert skill_id in skills, f"Tool references unregistered Skill: {skill_id}"

    for workflow_path in sorted((ROOT / "workflows").glob("*/workflow.yaml")):
        workflow = load(workflow_path)
        if workflow["id"] not in workflow_entries:
            registration = workflow.get("registration") or {}
            assert workflow.get("status") == "independent-unregistered", workflow_path
            assert registration.get("workflow_registry") is False, workflow_path
            assert registration.get("skill_registry") is False, workflow_path
            assert registration.get("tool_registry") is False, workflow_path
            assert (workflow_path.parent / "README.zh.md").is_file(), workflow_path
            continue
        assert workflow["id"] in workflow_entries
        assert workflow_entries[workflow["id"]]["path"] == str(workflow_path.relative_to(ROOT))
        readme_path = workflow_path.parent / workflow["readme"]
        assert readme_path.is_file(), f"missing Workflow README: {readme_path}"
        assert readme_path.read_text(encoding="utf-8").strip(), f"empty Workflow README: {readme_path}"
        assert workflow["stage_type"] == "phase-gate"
        assert (workflow_path.parent / workflow["run_record"]).resolve().is_file()
        assert (workflow_path.parent / workflow["stage_evidence_contract"]).resolve().is_file()
        if workflow["id"] == "document-to-markdown-assets":
            assert (workflow_path.parent / workflow["execution_preflight_contract"]).resolve().is_file()
            page_unit_execution = workflow["page_unit_execution"]
            assert page_unit_execution["stage_order"] == [
                "single-page-1",
                "small-batch-2-5",
                "full-batch-6-total_pages",
            ]
            assert page_unit_execution["gates"]["full_batch"]["pages"] == "6-total_pages"
        for handoff in workflow.get("handoff_exports", []) + workflow.get("handoff_inputs", []):
            assert (workflow_path.parent / handoff["contract"]).resolve().is_file()
        assert len(workflow["stages"]) == 7
        assert len({stage["id"] for stage in workflow["stages"]}) == 7
        stage_skill_ids = {
            skill_id
            for stage in workflow["stages"]
            for skill_id in stage.get("skills", []) + stage.get("optional_skills", [])
        }
        for stage in workflow["stages"]:
            assert stage["type"] == "stage"
            referenced = stage.get("skills", []) + stage.get("optional_skills", [])
            for skill_id in referenced:
                assert skill_id in skills, f"{workflow_path}: unregistered Skill {skill_id}"
            for tool_id in stage.get("tools", []):
                assert tool_id in tools, f"{workflow_path}: unregistered Tool {tool_id}"
            assert len(stage.get("required_fields", [])) >= 4, f"{workflow_path}:{stage['id']} missing required_fields"
            assert len(stage.get("score_dimensions", [])) >= 4, f"{workflow_path}:{stage['id']} missing score_dimensions"
            if stage.get("contract"):
                assert (workflow_path.parent / stage["contract"]).resolve().is_file(), stage["contract"]
        for output in workflow["outputs"].get("secondary", []):
            contract = output.get("contract")
            if contract:
                assert (workflow_path.parent / contract).resolve().is_file(), contract
        for profile in workflow.get("profiles", []):
            profile_skill = profile.get("entrypoint_skill")
            assert profile_skill in skills, f"{workflow_path}: profile Skill is not registered: {profile_skill}"
            assert profile_skill not in stage_skill_ids, f"{workflow_path}: compatibility Skill must not duplicate a stage: {profile_skill}"
            profile_contract = (workflow_path.parent / profile["contract"]).resolve()
            assert profile_contract.is_file(), f"missing profile contract: {profile_contract}"
            profile_policy = (workflow_path.parent / profile["policy_reference"]).resolve()
            assert profile_policy.is_file(), f"missing profile policy reference: {profile_policy}"
            profile_data = load(profile_contract)
            assert profile_data["id"] == profile["id"]
            assert profile_data["workflow"] == workflow["id"]
            assert profile_data["entrypoint_skill"] == profile_skill
            policy_reference = (profile_contract.parent / profile_data["policy_reference"]).resolve()
            assert policy_reference.is_file(), f"missing profile policy reference: {policy_reference}"
            assert profile_data["stage_policy"]["no_independent_stages"] is True
            assert profile_data["stage_policy"]["canonical_stage_order"] == ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]
        if workflow["id"] == "document-to-markdown-assets":
            collection_contract = (workflow_path.parent / workflow["markdown_collection_output_contract"]).resolve()
            assert collection_contract == ROOT / "contracts/markdown-collection-output-layout.yaml"
            assert collection_contract.is_file()
            registry_entry = workflow_entries[workflow["id"]]
            assert registry_entry["reader_collection_contract"] == "contracts/markdown-collection-output-layout.yaml"
            assert registry_entry["reader_collection_scope"] == "downstream-reader-export;does-not-rename-workflow-a-index"
            assert (ROOT / registry_entry["reader_collection_contract"]).is_file()
            assert registry_entry["trigger_keywords"] == ["文档转 Markdown"]
            assert registry_entry["daily_trigger"] == "文档转 Markdown"
            observability = registry_entry["execution_observability"]
            assert observability["final_report"] is None
            assert observability["statuses"] == ["passed", "needs-review", "failed"]
            assert workflow["entrypoint"]["daily_trigger"] == registry_entry["daily_trigger"]
            assert workflow["entrypoint"]["trigger_keyword"] == "文档转 Markdown"
            assert (workflow_path.parent / workflow["visual_asset_classification_contract"]).resolve().is_file()
            prompt_inputs = workflow["entrypoint"]["prompt_inputs"]
            assert prompt_inputs == ["source", "output_root"]
            assert workflow["entrypoint"]["derived_inputs"]["pages"] == "all"
            assert workflow["entrypoint"]["derived_inputs"]["profile"] == "references/profiles/source-runner-generic.yaml"
            assert workflow["entrypoint"]["derived_inputs"]["config"] is None
            assert workflow["execution_observability"]["final_report"] is None
            quality_gate = workflow.get("quality_gate")
            assert quality_gate["minimum_total"] == 95
            assert quality_gate["target_total"] >= quality_gate["minimum_total"]
            assert quality_gate["independent_pass_lines"]["unknown_form_coverage"] == 5
            assert "no_p0_issue" in quality_gate["hard_requirements"]
            output_layout = (workflow_path.parent / workflow["outputs"]["primary"]["output_layout"]).resolve()
            assert output_layout.is_file(), output_layout
            layout = load(output_layout)
            assert set(layout["required_files"]) == {"index.md"}
            assert set(layout["required_directories"]) == set()
            assert set(layout["optional_directories"]) == {"assets/"}
            assert set(layout["forbidden_files"]) >= {"manifest.yaml", "reports/", "evidence/"}
            stages = {stage["id"]: stage for stage in workflow["stages"]}
            assert stages["A4"]["skills"] == [
                "vision-page-analysis",
                "local-ocr",
                "text-source-reconciliation",
                "layout-relation-reconciliation",
            ]
            assert stages["A4"]["optional_skills"] == ["visual-reconstruction-profile"]
            assert stages["A4"]["fallback_skills"] == ["local-ocr"]
            assert stages["A4"]["route"] == "recognition-calibration-then-page-quality-routing"
            vision_contract = (workflow_path.parent / workflow["execution_policy"]["vision_page_analysis"]["contract"]).resolve()
            assert vision_contract.is_file()
            vision_policy = load(vision_contract)
            assert vision_policy["transport"]["mac96_candidate"]["target"] == "mac96"
            assert vision_policy["transport"]["apple_vision_candidate"]["target"] == "apple-vision-ocr"
            assert vision_policy["policy"]["route_selection"]["fixed_priority_forbidden"] is True
            assert vision_policy["policy"]["advertising_default"] == "preserve-only-no-delete-no-rewrite"
            assert vision_policy["policy"]["single_pass_default"] == "full-mode-one-request"
            assert vision_policy["policy"]["dual_pass_order"] == ["text", "layout"]
            assert "mac96-vision-page-analysis" in tools
            assert tools["mac96-vision-page-analysis"]["implementation"] == "available"
            assert tools["language-quality-repair-adapter"]["implementation"] == "available"
            assert tools["language-quality-repair-adapter"]["target"] == "mac96"
            assert tools["language-quality-repair-adapter"]["input_scope"] == "one-current-page-unit-only"
            assert tools["page-markdown-noise-cleanup-adapter"]["implementation"] == "available"
            assert tools["page-markdown-noise-cleanup-adapter"]["image_input"] is False
            noise_policy = workflow["execution_policy"]["page_markdown_noise_cleanup"]
            assert noise_policy["stage"] == "A5-before-language-quality-repair"
            assert noise_policy["scan_before_apply"] is True
            noise_contract = (workflow_path.parent / noise_policy["contract"]).resolve()
            assert noise_contract.is_file()
            assert load(noise_contract)["scope"] == "post-page-unit-pre-language-repair"
            assert stages["A5"]["skills"] == [
                "markdown-structure-builder",
                "page-markdown-unit",
                "clean-page-markdown-noise",
                "markdown-content-repair",
                "source-asset-linker",
                "uncertainty-marking",
                "semantic-visual-asset-pruning",
            ]
            assert stages["A5"]["route"] == "local-deterministic-noise-cleanup-then-lan-language-model-then-explicit-conversation-fallback"
            assert stages["A5"]["output"] == "index.md"
            assert stages["A6"]["skills"] == [
                "semantic-asset-naming",
                "source-occurrence-registration",
                "asset-deduplication",
                "semantic-visual-asset-pruning",
            ]
            assert stages["A6"]["output"] == "optional assets/ (only semantic reader images referenced by index.md)"
            assert stages["A7"]["skills"] == ["asset-package-validation"]
            assert stages["A7"]["output"] == "reader-package structural guard (runner stdout only)"
            assert stages["A7"]["evidence"] == ["index.md"]
            image_output = next(item for item in workflow["outputs"]["secondary"] if item["type"] == "reader-images-referenced-by-markdown")
            assert image_output["status"] == "optional-absent-for-text-only-output"
            assert image_output["contract"] == "../../contracts/reader-markdown-output-layout.yaml"
        if workflow["id"] == "markdown-to-gpt-image":
            output_layout = (workflow_path.parent / workflow["outputs"]["primary"]["output_layout"]).resolve()
            assert output_layout.is_file(), output_layout
            layout = load(output_layout)
            assert "generated-image.png" in layout["required_files"]
        assert (workflow_path.parent / workflow["validation"]["acceptance"]).is_file()

    for markdown_path in ROOT.rglob("*.md"):
        assert "TODO" not in markdown_path.read_text(encoding="utf-8"), markdown_path

    forbidden = {"api_key", "authorization", "cookie", "secret", "subscription_url"}
    request_contract = load(ROOT / "contracts/gpt-image-generation-request.yaml")
    assert forbidden.issubset(set(request_contract["forbidden_fields"]))

    preflight_contract = load(ROOT / "contracts/workflow-a-preflight.yaml")
    assert set(preflight_contract["audit_metadata"]["required"]) == {
        "risk_catalog", "human_reference", "evidence_boundary", "execution_boundary"
    }
    rule_ids = {item["id"] for item in preflight_contract["rules"]}
    assert {"source-inventory", "external-route-policy"}.issubset(rule_ids)
    check_required = set(preflight_contract["check"]["required"])
    assert {"risk_ids", "detectability", "evidence_class", "next_action"}.issubset(check_required)
    known_failures = (ROOT / "references/known-failures.zh.md").read_text(encoding="utf-8")
    preflight_reference = (ROOT / "references/execution-preflight.zh.md").read_text(encoding="utf-8")
    omitted_gates = (ROOT / "references/omitted-gates.zh.md").read_text(encoding="utf-8")
    assert "证据类别" in known_failures and "最早检测点" in known_failures
    assert "机器规则映射" in preflight_reference and "risk_ids" in preflight_reference
    assert "风险目录中不可前移的项目" in omitted_gates

    print(f"PASS: {len(skills)} Skills, {len(tools)} Tools, 2 Workflows")
    print("PASS: every Workflow Skill/Tool reference resolves")
    print("PASS: legacy knowledge-map entry routes through Workflow A without adding stages")
    print("PASS: every Skill has SKILL.md, frontmatter, and agents/openai.yaml")
    print("PASS: contracts, acceptance docs, and Markdown TODO checks")
    print("NOTE: planned Tool implementations are intentionally not executed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    assert workflow_registry["schema"]["path_must_exist"] is True
    workflow_entries = {entry["id"]: entry for entry in workflow_registry["workflows"]}
    assert len(workflow_entries) == len(workflow_registry["workflows"])
    assert set(workflow_entries) == {"document-to-markdown-assets", "markdown-to-gpt-image"}
    assert (ROOT / "WORKFLOWS.zh.md").is_file()

    for entry in workflow_entries.values():
        for field in ("path", "readme", "acceptance"):
            target = ROOT / entry[field]
            assert target.is_file(), f"missing Workflow registry target: {target}"
