from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(relative_path: str):
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def joined(values) -> str:
    return "\n".join(str(value) for value in values)


def test_lifecycle_reuses_workflow_a_single_manifest_and_flat_assets():
    registry = load("workflow-registry.yaml")
    workflow = load("workflows/document-to-markdown-assets/workflow.yaml")
    reader_layout = load("contracts/reader-markdown-output-layout.yaml")
    tool_registry = load("tool-registry.yaml")

    assert [item["id"] for item in registry["workflows"]] == [
        "document-to-markdown-assets",
        "markdown-to-gpt-image",
    ]
    lifecycle = workflow["execution_policy"]["asset_extraction"]["figure_asset_lifecycle"]
    assert lifecycle["creates_new_workflow"] is False
    assert lifecycle["creates_second_manifest"] is False
    assert lifecycle["batch_scope"] == "one-content-unit-and-1-to-2-figures"
    assert lifecycle["batch_completion_does_not_imply_document_completion"] is True
    assert "raw-or-source-content-units" in lifecycle["coverage_scope"]
    assert lifecycle["order"] == [
        "figure-demand-inventory",
        "source-page-resolution",
        "figure-region-localization",
        "crop-decision-and-acceptance",
        "enhancement-optional",
        "mermaid-optional",
        "manifest-and-reader-projection",
        "markdown-backfill-and-regeneration",
        "batch-validation-and-close",
    ]
    tasks = lifecycle["task_tree"]
    assert [item["id"] for item in tasks] == [f"F{index}" for index in range(9)]
    assert [item["task"] for item in tasks] == lifecycle["order"]
    for task in tasks:
        assert task["children"]
        assert task["evidence"]
        assert task["gate"]
        assert {"skills", "tools", "code", "status"} == set(task["binding"])
        for code_path in task["binding"]["code"]:
            assert (ROOT / code_path).is_file(), (task["id"], code_path)
    assert "procedure-only" in tasks[0]["binding"]["status"]
    assert "no-dedicated-repository-adapter" in tasks[2]["binding"]["status"]
    assert "not-lifecycle-compatible-or-orchestrated" in tasks[3]["binding"]["status"]
    assert "human-approval-required" in tasks[3]["binding"]["status"]
    assert "no-dedicated-enhancement-entrypoint" in tasks[4]["binding"]["status"]
    assert "no-workflow-a-mermaid-generator-or-render-validator" in tasks[5]["binding"]["status"]
    assert "expected_figure_ids" in tasks[0]["evidence"]
    assert "document_status" in tasks[0]["evidence"]
    assert "source_equivalence" in tasks[1]["evidence"]
    assert "source_occurrence_id" in tasks[2]["evidence"]
    assert "diagnostic_artifacts" in tasks[5]["evidence"]
    assert "historical_manifest_migration" in tasks[6]["evidence"]
    assert "demand_diff" in tasks[7]["evidence"]
    assert "unresolved_figure_ids" in tasks[8]["evidence"]
    assert lifecycle["crop_policy"].startswith("candidate-region-required;")
    assert "comparison-evidence-required" in lifecycle["enhancement_policy"]
    assert "syntax-render-semantic-and-png-fallback" in lifecycle["mermaid_policy"]
    assert lifecycle["implementation_claim_policy"] == "code-backed-only-when-entrypoint-and-current-evidence-exist"
    assert lifecycle["reader_asset_layout"] == "flat-assets-v1"
    assert "contact-sheet-evidence-only-and-page-selection-only" in lifecycle["probe_policy"]
    assert "full-resolution-parent" in lifecycle["probe_policy"]
    assert "distinct-figure-region-occurrence" in lifecycle["same_page_multi_figure_policy"]
    assert "merge-into-single-manifest" in lifecycle["historical_manifest_policy"]
    assert "no-missing-reference-missing-or-needs-review" in lifecycle["document_completion_gate"]
    assert reader_layout["optional_directories"] == ["assets/"]
    assert "assets/" in joined(reader_layout["invariants"])
    cropper = next(item for item in tool_registry["tools"] if item["id"] == "image-region-cropper")
    assert (ROOT / cropper["repository_entrypoint"]).is_file()
    assert cropper["orchestration_status"] == "reader-only-adjacent-code-not-wired-to-figure-lifecycle"
    assert cropper["lifecycle_compatibility"] == "needs-review"
    assert "no-single-manifest-update" in cropper["lifecycle_gaps"]
    assert cropper["human_visual_approval"] == "required"


def test_source_asset_contract_closes_page_crop_enhanced_lineage():
    contract = load("contracts/source-visual-asset.yaml")

    assert contract["version"] == "0.5.0"
    assert "enhanced-raster" in contract["required"]["asset_kind"]
    assert "deterministic-enhancement" in contract["required"]["extraction_method"]
    assert {
        "parent_asset_ids",
        "operation",
        "parameters",
        "structure_preserved",
        "fallback_asset_id",
        "status",
    } == set(contract["derivation"]["required"])
    assert contract["derivation"]["crop_parameters"]["geometry"] == "string"
    crop_decision = contract["crop_decision"]
    assert {
        "region_id",
        "candidate_geometry",
        "accepted_geometry",
        "coordinate_space",
        "safety_margins",
        "edge_checks",
        "review_evidence",
        "adjustment_history",
        "status",
    }.issubset(crop_decision["required"])
    assert crop_decision["coordinate_space"]["origin"] == "enum[top-left]"
    assert crop_decision["edge_checks"]["side_status"] == "enum[clear, intentional-contact, clipped, unknown]"
    assert "link_check" in contract["markdown_binding"]["required"]
    assert "printed_page" in contract["source_occurrence"]["fields"]
    assert "content_unit_id" in contract["source_occurrence"]["fields"]
    assert "figure_id" in contract["source_occurrence"]["fields"]
    assert "source_equivalence" in contract["optional"]
    assert contract["source_equivalence"]["status"] == "enum[byte-identical, rendered-equivalent, transformed-with-provenance, needs-review]"
    assert "historical_import" in contract["optional"]

    invariants = joined(contract["invariants"])
    assert "enhanced-raster" in invariants
    assert "structure_preserved=true" in invariants
    assert "同一个 manifest.yaml" in invariants
    assert "Markdown 引用丢失不得触发" in invariants
    assert "不能只接收裸 geometry" in invariants
    assert "top/right/bottom/left" in invariants
    assert "多个 figure/region" in invariants
    assert "第二个活跃 Manifest" in invariants


def test_figure_coverage_probe_diagnostics_and_manifest_migration_contracts():
    extraction = load("contracts/asset-extraction-strategy.yaml")
    package = load("contracts/markdown-asset-package.yaml")

    assert extraction["version"] == "0.4.0"
    assert {"figure_coverage", "source_page_probes", "diagnostic_artifacts", "historical_manifest_migration"}.issubset(
        extraction["optional"]
    )
    coverage = extraction["figure_coverage"]
    assert {
        "scope",
        "demand_sources",
        "expected_figure_ids",
        "records",
        "counts",
        "batch_status",
        "document_status",
    } == set(coverage["required"])
    record = extraction["figure_coverage_record"]
    assert record["asset_status"] == "enum[upgraded, legacy-only, source-page-only, missing, needs-review, not-applicable]"
    assert record["markdown_status"] == "enum[linked, reference-missing, needs-review, not-applicable]"
    assert extraction["source_page_probe"]["contact_sheet"]["required"] == [
        "path",
        "sha256",
        "page_order",
        "preview_dimensions",
        "full_resolution_dimensions",
        "status",
    ]
    assert extraction["source_page_probe"]["coordinate_authority"] == "enum[page-selection-only-not-crop-geometry]"
    assert extraction["diagnostic_artifact"]["reader_projection"] == "enum[evidence-only]"
    assert "coordinate_transform" in extraction["diagnostic_artifact"]["required"]
    migration = extraction["historical_manifest_migration"]
    assert migration["target_manifest_path"] == "enum[manifest.yaml]"
    assert migration["active_identity_store"] == "enum[single-target-manifest]"

    rules = joined(extraction["invariants"])
    assert "原始最小来源单元、清理后单元、派生文档" in rules
    assert "batch_status=passed" in rules and "document_status" in rules
    assert "同一来源页包含多个图" in rules
    assert "历史 success" in rules

    assert package["version"] == "0.4.0"
    lifecycle_evidence = package["visual_asset_lifecycle"]
    assert lifecycle_evidence["required"] == ["enabled", "task_evidence", "status"]
    assert package["visual_asset_lifecycle_task_evidence"]["task_id"] == "enum[F0, F1, F2, F3, F4, F5, F6, F7, F8]"
    package_rules = joined(package["invariants"])
    assert "不是第二个资产 Manifest" in package_rules
    assert "当前批次 passed" in package_rules
    assert "节点子图" in package_rules
    assert "唯一 manifest.yaml" in package_rules
    assert "enabled=true" in package_rules and "F0–F8 task_evidence" in package_rules


def test_mixed_text_visuals_route_to_scored_crop_before_engineering_page_delete():
    workflow = load("workflows/document-to-markdown-assets/workflow.yaml")
    classification = load("contracts/visual-asset-classification.yaml")
    pruning = load("contracts/visual-asset-pruning.yaml")
    tool_registry = load("tool-registry.yaml")

    policy = workflow["execution_policy"]["visual_asset_policy"]
    assert "mixed-text-and-diagram" in policy["crop_examples"]
    assert "minimum-score-95" in policy["crop_required_gate"]
    assert "all-output-assets-referenced" in policy["engineering_page_delete_gate"]
    assert classification["version"] == "0.2.0"
    assert "crop-required" in classification["decision"]["decision"]
    assert "mixed-text-visual" in classification["decision"]["reason_codes"]
    assert "至少 95 分" in joined(classification["invariants"])
    assert pruning["version"] == "0.4.0"
    pruning_rules = joined(pruning["invariants"])
    assert "regenerable-engineering-page-render" in pruning_rules
    assert "原始 PDF/PPTX/独立图片" in pruning_rules
    assert "decision_confidence_score>=95" in pruning_rules
    assert "字符覆盖和顺序覆盖" in pruning_rules
    cropper = next(item for item in tool_registry["tools"] if item["id"] == "image-region-cropper")
    assert cropper["reader_only_quality_gate"] == "minimum-95-with-all-hard-checks-passed"
    assert cropper["original_source_deletion"] == "forbidden"
    auditor = next(item for item in tool_registry["tools"] if item["id"] == "reader-asset-text-auditor")
    assert auditor["implementation"] == "available"
    assert auditor["model_tokens_default"] == 0
    assert (ROOT / auditor["repository_entrypoint"]).is_file()


def test_text_heavy_second_gate_is_wired_to_f3_and_a7():
    workflow = load("workflows/document-to-markdown-assets/workflow.yaml")
    lifecycle = workflow["execution_policy"]["asset_extraction"]["figure_asset_lifecycle"]
    f3 = lifecycle["task_tree"][3]
    a7 = next(stage for stage in workflow["stages"] if stage["id"] == "A7")

    assert "audit-text-heavy-reader-crop" in f3["children"]
    assert "reader-asset-text-auditor" in f3["binding"]["tools"]
    assert "skills/region-cropping/scripts/audit_text_heavy_reader_assets.py" in f3["binding"]["code"]
    assert "text-heavy-reader-asset-audit" in f3["evidence"]
    assert "删除置信分至少 95" in f3["gate"]
    assert "audit evidence score<95" in a7["gate"]


def test_standalone_image_corpus_contract_is_stream_safe_and_markdown_free():
    pruning = load("contracts/visual-asset-pruning.yaml")
    tool_registry = load("tool-registry.yaml")
    standalone = pruning["standalone_image_corpus"]

    assert standalone["markdown_access"] == "forbidden"
    assert standalone["default_concurrency"] == 1
    assert standalone["maximum_concurrency"] == 2
    assert "append-classification-fsync" in standalone["transaction_order"]
    assert "append-delete-intent-fsync" in standalone["transaction_order"]
    assert "release-image-and-ocr-objects" in standalone["transaction_order"]
    assert "input-manifest.jsonl" in standalone["required_files"]
    assert "deletion-ledger.jsonl" in standalone["required_files"]
    auditor = next(item for item in tool_registry["tools"] if item["id"] == "reader-asset-text-auditor")
    assert "standalone-image-corpus" in auditor["modes"]
    assert auditor["standalone_concurrency"] == "default-1-max-2"
    assert auditor["automatic_restart"] == "forbidden"


def test_mermaid_is_optional_evidence_bound_and_has_png_fallback():
    contract = load("contracts/visual-reconstruction-profile.yaml")
    derived = contract["derived_representation"]

    assert contract["version"] == "0.4.0"
    assert "derived_representations" in contract["optional"]
    assert "text_corroboration" in contract["optional"]
    assert contract["text_corroboration"]["required"] == [
        "source_spans",
        "allowed_uses",
        "forbidden_uses",
        "conflicts",
        "status",
    ]
    assert derived["kind"] == "enum[mermaid]"
    assert "fallback_asset_id" in derived["required"]
    assert "markdown_targets" in derived["required"]
    assert "validation" in derived["required"]
    validation = derived["validation"]
    assert validation["required"] == ["syntax", "render", "semantic_comparison", "fallback_link"]
    assert {"nodes", "labels", "edges", "directions", "hierarchy"}.issubset(
        validation["semantic_comparison"]["required"]
    )

    invariants = joined(contract["invariants"])
    assert "明确节点-边关系" in invariants
    assert "每个节点、标签和边必须回指" in invariants
    assert "crop 或 enhanced-raster PNG" in invariants
    assert "不能替代 source page" in invariants
    assert "validation.syntax" in invariants
    assert "标签拼写或消歧" in invariants
    assert "text_corroboration.conflicts" in invariants


def test_page_unit_contract_uses_minimum_edit_surface_and_preserves_fallback():
    contract = load("contracts/page-markdown-unit.yaml")
    backfill = joined(contract["rules"]["visual_asset_backfill"])
    merge = joined(contract["rules"]["deterministic_merge"])

    assert contract["version"] == "0.5.0"
    assert "结构化图示（Mermaid）" in contract["optional_sections"]
    assert "page-unit" in backfill
    assert "Mermaid" in backfill and "PNG fallback" in backfill
    assert "../assets/<file>" in backfill
    assert "不得删除来源资产" in backfill
    assert "逐字保留" in merge and "fallback" in merge
    evidence = contract["visual_asset_backfill_evidence"]
    assert {"before_sha256", "after_sha256", "inserted_asset_ids", "link_checks", "merge_check"}.issubset(
        evidence["required"]
    )
    conditional = contract["conditional_outputs"]["visual_asset_backfill_evidence"]
    assert conditional["when"] == "figure-asset-lifecycle-enabled"
    assert conditional["path"] == "evidence/visual-asset-backfill.json"


def test_reference_keeps_native_first_and_historical_tools_non_default():
    reference = (ROOT / "references/figure-asset-lifecycle.zh.md").read_text(encoding="utf-8")
    workflow = load("workflows/document-to-markdown-assets/workflow.yaml")

    assert "不是新 Workflow" in reference
    assert "不改变 native-first PDF 路由" in reference
    assert "Marker、Surya OCR、固定 8/40 页分块和 GPT 清理仅可作为历史案例或显式配置" in reference
    assert workflow["execution_policy"]["input_policy"] == "native-pdf-text-and-layout-json-first"
    assert workflow["execution_policy"]["pdf_page_routing"]["default_route"] == "page-hybrid"
    assert "F0 图片需求盘点" in reference
    assert "F8 批次验收与闭合" in reference
    assert "文档级覆盖台账" in reference
    assert "同页多图和历史记录迁移" in reference
    assert "contact sheet" in reference
    assert "procedure-only" in reference
    assert "regions.json` 写为 `not-run" in reference
