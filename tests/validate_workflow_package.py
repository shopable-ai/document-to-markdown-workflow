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

    tools = {entry["id"]: entry for entry in tool_registry["tools"]}
    assert len(tools) == len(tool_registry["tools"]), "duplicate Tool IDs"
    allowed_implementations = set(tool_registry["schema"]["implementation_values"])
    assert all(entry["implementation"] in allowed_implementations for entry in tools.values())
    for entry in tools.values():
        for skill_id in entry["used_by"]:
            assert skill_id in skills, f"Tool references unregistered Skill: {skill_id}"

    for workflow_path in sorted((ROOT / "workflows").glob("*/workflow.yaml")):
        workflow = load(workflow_path)
        assert workflow["id"] in workflow_entries
        assert workflow_entries[workflow["id"]]["path"] == str(workflow_path.relative_to(ROOT))
        readme_path = workflow_path.parent / workflow["readme"]
        assert readme_path.is_file(), f"missing Workflow README: {readme_path}"
        assert readme_path.read_text(encoding="utf-8").strip(), f"empty Workflow README: {readme_path}"
        assert workflow["stage_type"] == "phase-gate"
        assert (workflow_path.parent / workflow["run_record"]).resolve().is_file()
        assert (workflow_path.parent / workflow["stage_evidence_contract"]).resolve().is_file()
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
            registry_entry = workflow_entries[workflow["id"]]
            assert registry_entry["trigger_keywords"] == ["文档转 Markdown"]
            assert registry_entry["daily_trigger"] == "文档转 Markdown"
            observability = registry_entry["execution_observability"]
            assert observability["final_report"] == "reports/validation-result.json"
            assert observability["statuses"] == ["passed", "needs-review", "failed"]
            assert workflow["entrypoint"]["daily_trigger"] == registry_entry["daily_trigger"]
            assert workflow["entrypoint"]["trigger_keyword"] == "文档转 Markdown"
            assert workflow["entrypoint"]["prompt_inputs"] == ["source", "output_root", "pages"]
            assert workflow["execution_observability"]["final_report"] == observability["final_report"]
            quality_gate = workflow.get("quality_gate")
            assert quality_gate["minimum_total"] == 95
            assert quality_gate["target_total"] >= quality_gate["minimum_total"]
            assert quality_gate["independent_pass_lines"]["unknown_form_coverage"] == 5
            assert "no_p0_issue" in quality_gate["hard_requirements"]
            output_layout = (workflow_path.parent / workflow["outputs"]["primary"]["output_layout"]).resolve()
            assert output_layout.is_file(), output_layout
            layout = load(output_layout)
            assert set(layout["required_files"]) == {
                "document.md",
                "manifest.yaml",
                "reports/processing-report.yaml",
                "reports/acceptance-report.yaml",
                "reports/failure-report.yaml",
            }
            assert set(layout["required_directories"]) == {
                "assets/pages/",
                "assets/regions/",
                "assets/embedded/",
            }
            assert "reports/validation-result.json" in layout["optional_files"]
            stages = {stage["id"]: stage for stage in workflow["stages"]}
            assert stages["A4"]["skills"] == [
                "mac96-visual-review",
                "local-ocr",
                "text-source-reconciliation",
                "layout-relation-reconciliation",
            ]
            assert stages["A4"]["fallback_skills"] == ["local-ocr"]
            assert stages["A4"]["route"] == "ssh-mac96-first-apple-ocr-fallback"
            assert stages["A5"]["skills"] == [
                "markdown-structure-builder",
                "source-asset-linker",
                "uncertainty-marking",
            ]
            assert stages["A5"]["output"] == "document.md"
            assert stages["A6"]["skills"] == [
                "semantic-asset-naming",
                "source-occurrence-registration",
                "asset-deduplication",
            ]
            assert stages["A6"]["output"] == "manifest.yaml"
            assert stages["A7"]["skills"] == ["asset-package-validation"]
            assert stages["A7"]["output"] == "reports/processing-report.yaml, reports/acceptance-report.yaml, reports/failure-report.yaml, reports/validation-result.json"
            assert "stage_evidence" in stages["A7"]["required_fields"]
            failure_output = next(item for item in workflow["outputs"]["secondary"] if item["type"] == "failure-report")
            assert failure_output["contract"] == "../../contracts/failure-report.yaml"
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
