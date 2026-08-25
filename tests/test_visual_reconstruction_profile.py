from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(relative_path: str):
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def test_profile_contract_preserves_structure_prompt_and_evidence_fields():
    contract = load("contracts/visual-reconstruction-profile.yaml")
    required = contract["required"]

    assert contract["contract"] == "visual-reconstruction-profile"
    assert {"canvas", "composition", "visual_elements", "text_fidelity", "style", "evidence"}.issubset(required)
    assert "structure-and-prompt" in required["reconstruction"]["mode"]
    assert "insufficient-evidence" in required["reconstruction"]["mode"]
    assert "prompt" in required["reconstruction"]
    assert "negative_constraints" in required["reconstruction"]
    assert "Unknown" in " ".join(contract["invariants"])
    assert {"api_key", "authorization", "cookie", "secret", "subscription_url"}.issubset(
        set(contract["forbidden_fields"]) if "forbidden_fields" in contract else set()
    )
    assert "derived_representations" in contract["optional"]
    derived = contract["derived_representation"]
    assert derived["kind"] == "enum[mermaid]"
    assert {"fallback_asset_id", "markdown_targets", "source_asset_ids", "validation"}.issubset(derived["required"])
    assert derived["validation"]["required"] == ["syntax", "render", "semantic_comparison", "fallback_link"]
    invariants = " ".join(contract["invariants"])
    assert "明确节点-边关系" in invariants
    assert "crop 或 enhanced-raster PNG" in invariants


def test_workflow_a_profile_output_can_be_consumed_by_workflow_b():
    workflow_a = load("workflows/document-to-markdown-assets/workflow.yaml")
    workflow_b = load("workflows/markdown-to-gpt-image/workflow.yaml")

    assert workflow_a["visual_reconstruction_profile_contract"].endswith(
        "contracts/visual-reconstruction-profile.yaml"
    )
    a4 = next(stage for stage in workflow_a["stages"] if stage["id"] == "A4")
    assert "visual-reconstruction-profile" in a4["optional_skills"]
    assert "visual-reconstruction-profile.yaml" in a4["output"]
    references = next(item for item in workflow_b["inputs"] if item["name"] == "reference_assets")
    assert "visual-reconstruction-profile" in references["types"]
    assert any(item["name"] == "visual_reconstruction_profile" for item in workflow_b["inputs"])
    lifecycle = workflow_a["execution_policy"]["asset_extraction"]["figure_asset_lifecycle"]
    assert lifecycle["mermaid_policy"] == (
        "confirmed-node-edge-structures-only;"
        "syntax-render-semantic-and-png-fallback-validation-required"
    )
