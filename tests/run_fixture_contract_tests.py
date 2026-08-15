#!/usr/bin/env python3
"""Dependency-light runner for the fixture contract checks."""

from test_fixture_contract import (
    test_fixture_uses_last_two_content_pages,
    test_selected_pdf_contains_two_pages_and_required_text,
    test_selected_pptx_contains_two_slides_and_required_native_text,
    test_visual_baselines_exist,
    test_fixture_run_marks_outputs_as_fixture_samples,
    test_fixture_sample_links_and_manifest_paths_exist,
    test_fixture_markdown_places_visual_assets_after_semantic_content,
    test_quality_target_contract_is_95_plus_and_has_hard_gates,
    test_fixture_manifest_uses_a6_dual_name_and_traceable_occurrences,
    test_fixture_assets_do_not_claim_available_without_media_integrity_fields,
    test_fixture_reports_have_stage_evidence_and_failure_report,
)


def main() -> None:
    tests = [
        test_fixture_uses_last_two_content_pages,
        test_selected_pptx_contains_two_slides_and_required_native_text,
        test_selected_pdf_contains_two_pages_and_required_text,
        test_visual_baselines_exist,
        test_fixture_run_marks_outputs_as_fixture_samples,
        test_fixture_sample_links_and_manifest_paths_exist,
        test_fixture_markdown_places_visual_assets_after_semantic_content,
        test_quality_target_contract_is_95_plus_and_has_hard_gates,
        test_fixture_manifest_uses_a6_dual_name_and_traceable_occurrences,
        test_fixture_assets_do_not_claim_available_without_media_integrity_fields,
        test_fixture_reports_have_stage_evidence_and_failure_report,
]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} fixture contract tests passed")


if __name__ == "__main__":
    main()
