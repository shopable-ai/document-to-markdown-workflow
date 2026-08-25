from __future__ import annotations

import gc
import importlib.util
import json
import subprocess
import sys
import tracemalloc
from pathlib import Path

from PIL import Image, ImageDraw
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/semantic-visual-asset-pruning/scripts/standalone_image_corpus.py"
SPEC = importlib.util.spec_from_file_location("standalone_image_corpus", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_text_image(path: Path, text: str = "Section Title") -> None:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((240, 180), text, fill="black")
    draw.text((220, 320), "Paragraph body line one", fill="black")
    draw.text((220, 420), "Paragraph body line two", fill="black")
    image.save(path)


def make_centered_title_image(path: Path) -> None:
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((360, 220), "第十二章", fill="black")
    draw.text((300, 420), "A centered chapter title", fill="black")
    image.save(path)


def make_diagram_image(path: Path) -> None:
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((160, 240, 420, 520), outline="black", width=8)
    draw.rectangle((760, 240, 1040, 520), outline="black", width=8)
    draw.line((420, 380, 760, 380), fill="black", width=12)
    draw.line((600, 250, 600, 620), fill="black", width=10)
    draw.text((220, 340), "Input", fill="black")
    draw.text((840, 340), "Output", fill="black")
    image.save(path)


def make_crop_required_image(path: Path) -> None:
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((140, 180), "正文文字第一段", fill="black")
    draw.text((140, 320), "正文文字第二段", fill="black")
    draw.rectangle((320, 720, 680, 980), outline="black", width=10)
    image.save(path)


def import_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_corpus(tmp_path: Path, builders: dict[str, callable]) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(parents=True)
    for name, builder in builders.items():
        builder(root / name)
    return root


def run_config(
    root: Path,
    *,
    classifier=None,
    evidence_name: str = "evidence",
    resume: bool = False,
    input_manifest: Path | None = None,
    prior_classification: Path | None = None,
    memory_limit_mb: int = 0,
) -> dict:
    return MODULE.run_corpus(
        MODULE.CorpusConfig(
            corpus_root=root,
            evidence_dir=root / evidence_name,
            input_manifest=input_manifest,
            prior_classification=prior_classification,
            resume=resume,
            classifier=classifier,
            memory_limit_mb=memory_limit_mb,
        )
    )


def deletion_candidate(relative_path: str = "sample.png") -> dict:
    return {
        "status": "complete",
        "decision": "candidate-remove",
        "reason_codes": ["text-covered", "ordinary-typography"],
        "semantic_text_coverage": "complete",
        "visual_increment": "none",
        "decision_confidence_score": 98,
        "ocr_summary": {"succeeded": True, "group_count": 2},
        "visual_structure_metrics": {"semantic_structure_detected": False},
        "deterministic_deletion_gates": {
            "ocr_reliable": True,
            "semantic_text_coverage_complete": True,
            "visual_increment_none": True,
            "no_semantic_structure": True,
        },
        "skill_deletion_gates": {
            "candidate_remove": True,
            "confidence_at_least_95": True,
            "sha_locked": True,
        },
        "deletion_eligible": True,
        "rationale": relative_path,
    }


def test_default_classifier_recognizes_simple_text_vs_internal_structure_features() -> None:
    cases = [
        (
            "page-002",
            {
                "ocr_succeeded": True,
                "word_count": 2,
                "average_confidence": 65,
                "group_count": 2,
                "spatial_columns": 1,
                "internal_dark_ratio": 0.00110,
                "internal_horizontal_hits": 0,
                "internal_vertical_hits": 0,
                "explained_foreground_ratio": 0.9088,
                "residual_edge_ratio": 0.00110,
                "residual_texture_ratio": 0.0,
                "residual_color_ratio": 0.0,
                "max_residual_component_ratio": 0.00062,
                "long_line_count": 1,
                "line_intersections": 0,
                "connected_line_count": 0,
            },
            "candidate-remove",
        ),
        (
            "page-012",
            {
                "ocr_succeeded": True,
                "word_count": 3,
                "average_confidence": 65,
                "group_count": 3,
                "spatial_columns": 1,
                "internal_dark_ratio": 0.00210,
                "internal_horizontal_hits": 0,
                "internal_vertical_hits": 0,
                "explained_foreground_ratio": 0.8389,
                "residual_edge_ratio": 0.00210,
                "residual_texture_ratio": 0.00070,
                "residual_color_ratio": 0.0,
                "max_residual_component_ratio": 0.00179,
                "long_line_count": 4,
                "line_intersections": 0,
                "connected_line_count": 0,
            },
            "candidate-remove",
        ),
    ]

    for _name, features, expected in cases:
        decision, _reasons, _rationale = MODULE.standalone_decision_from_features(features)
        assert decision == expected


def test_visual_protection_and_ordinary_layout_rules() -> None:
    base = {
        "ocr_succeeded": True,
        "word_count": 8,
        "average_confidence": 90,
        "group_count": 2,
        "spatial_columns": 1,
        "internal_dark_ratio": 0.002,
        "internal_horizontal_hits": 0,
        "internal_vertical_hits": 0,
        "explained_foreground_ratio": 0.95,
        "residual_edge_ratio": 0.001,
        "residual_texture_ratio": 0.0,
        "residual_color_ratio": 0.0,
        "max_residual_component_ratio": 0.001,
        "line_intersections": 0,
        "connected_line_count": 0,
    }
    cases = [
        ("centered-title", {}, "candidate-remove"),
        ("footer-rule", {"long_line_count": 1}, "candidate-remove"),
        ("table", {"internal_horizontal_hits": 4, "internal_vertical_hits": 4, "line_intersections": 6}, "retain"),
        ("flow", {"connected_line_count": 3, "directional_edge_count": 2}, "retain"),
        ("relation", {"connected_line_count": 4}, "retain"),
        ("photo", {"photo_texture_ratio": 0.2}, "retain"),
        ("screenshot", {"screenshot_ui_score": 0.9}, "retain"),
        ("spatial-text", {"semantic_container_count": 3, "spatial_columns": 3}, "retain"),
        ("local-visual", {"internal_dark_ratio": 0.07}, "crop-required"),
    ]
    for name, updates, expected in cases:
        features = {**base, **updates}
        decision, _reasons, _rationale = MODULE.standalone_decision_from_features(features)
        assert decision == expected, name


def test_candidate_without_both_deletion_gates_is_not_deleted(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    result = run_config(
        root,
        classifier=lambda _entry, _config: {
            "status": "complete",
            "decision": "candidate-remove",
            "semantic_text_coverage": "complete",
            "visual_increment": "none",
            "decision_confidence_score": 94,
            "ocr_summary": {"succeeded": True},
            "visual_structure_metrics": {"semantic_structure_detected": False},
        },
    )
    assert result["status"] == "needs-review"
    assert (root / "alpha.png").is_file()
    row = import_lines(root / "evidence/classification.jsonl")[0]
    assert row["deletion_eligible"] is False


def test_image_only_mode_does_not_modify_markdown_or_register_restart(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    markdown = root / "notes.md"
    markdown.write_bytes(b"# Untouched\n\n![x](alpha.png)\n")
    before = markdown.read_bytes()
    result = run_config(
        root,
        classifier=lambda entry, _config: {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["photo"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": entry["relative_path"],
        },
    )
    assert result["status"] == "complete"
    assert markdown.read_bytes() == before
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert "launchctl" not in source
    assert "keepalive" not in source


def test_prior_classification_is_written_into_locked_manifest(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    prior = root / "prior.jsonl"
    prior.write_text(
        json.dumps(
            {
                "relative_path": "alpha.png",
                "decision": "retain",
                "reason_codes": ["diagram"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    run_config(root, prior_classification=prior)

    manifest_rows = import_lines(root / "evidence/input-manifest.jsonl")
    assert manifest_rows[0]["prior_decision"] == "retain"
    assert manifest_rows[0]["prior_reason_codes"] == ["diagram"]


def test_interrupted_classification_resume_reuses_finished_rows(tmp_path: Path) -> None:
    root = make_corpus(
        tmp_path,
        {
            "a.png": make_text_image,
            "b.png": make_text_image,
            "c.png": make_text_image,
        },
    )
    calls: list[str] = []

    def flaky(entry, _config):
        calls.append(entry["relative_path"])
        if entry["relative_path"] == "b.png":
            raise RuntimeError("boom-after-a")
        return {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["manual"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": "ok",
        }

    result = run_config(root, classifier=flaky)
    assert result["status"] == "needs-review"
    assert calls == ["a.png", "b.png"]

    second_calls: list[str] = []

    def stable(entry, _config):
        second_calls.append(entry["relative_path"])
        return {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["manual"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": "ok",
        }

    result = run_config(root, classifier=stable, resume=True)
    assert result["status"] == "complete"
    assert second_calls == ["b.png", "c.png"]
    rows = import_lines(root / "evidence/classification.jsonl")
    assert rows[0]["relative_path"] == "a.png"


def test_delete_intent_then_resume_completes_delete(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    result = run_config(
        root,
        classifier=lambda entry, _config: deletion_candidate(entry["relative_path"]),
    )
    assert result["status"] == "complete"
    assert not (root / "alpha.png").exists()
    manifest_row = import_lines(root / "evidence/input-manifest.jsonl")[0]
    assert {"sequence", "path", "relative_path", "sha256", "bytes", "width", "height"}.issubset(manifest_row)
    classification_row = import_lines(root / "evidence/classification.jsonl")[0]
    assert {
        "sequence",
        "attempt",
        "path",
        "relative_path",
        "input_sha256",
        "decision",
        "reason_codes",
        "decision_confidence_score",
        "ocr_summary",
        "visual_structure_metrics",
        "semantic_text_coverage",
        "visual_increment",
        "deterministic_deletion_gates",
        "skill_deletion_gates",
        "deletion_eligible",
        "error",
        "completed_at",
    }.issubset(classification_row)

    root2 = make_corpus(tmp_path / "case2", {"beta.png": make_text_image})
    evidence = root2 / "evidence"
    evidence.mkdir()
    manifest = root2 / "evidence/input-manifest.jsonl"
    sha = MODULE.sha256(root2 / "beta.png")
    MODULE.append_jsonl(
        manifest,
        {
            "sequence": 1,
            "path": str((root2 / "beta.png").resolve()),
            "relative_path": "beta.png",
            "sha256": sha,
            "bytes": (root2 / "beta.png").stat().st_size,
            "snapshot_mtime_ns": (root2 / "beta.png").stat().st_mtime_ns,
        },
    )
    MODULE.append_jsonl(
        evidence / "classification.jsonl",
        {
            "completed_at": "2026-08-25T00:00:00Z",
            "sequence": 1,
            "attempt": 1,
            "relative_path": "beta.png",
            "path": str((root2 / "beta.png").resolve()),
            "sha256": sha,
            "status": "complete",
            "decision": "candidate-remove",
            "reason_codes": ["text-covered"],
            "semantic_text_coverage": "complete",
            "visual_increment": "none",
            "rationale": "precomputed",
            **{key: value for key, value in deletion_candidate("beta.png").items() if key not in {"rationale"}},
        },
    )
    MODULE.append_jsonl(
        evidence / "deletion-ledger.jsonl",
        {
            "completed_at": "2026-08-25T00:00:01Z",
            "sequence": 1,
            "event": "delete-intent",
            "relative_path": "beta.png",
            "path": str((root2 / "beta.png").resolve()),
            "sha256": sha,
        },
    )

    resumed = run_config(root2, input_manifest=manifest, resume=True)
    assert resumed["status"] == "complete"
    assert not (root2 / "beta.png").exists()
    ledger_rows = import_lines(evidence / "deletion-ledger.jsonl")
    assert ledger_rows[-1]["state"] == "deleted"


def test_unlink_after_interrupt_is_recovered_without_reclassification(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    evidence = root / "evidence"
    evidence.mkdir()
    manifest = evidence / "input-manifest.jsonl"
    sha = MODULE.sha256(root / "alpha.png")
    MODULE.append_jsonl(
        manifest,
        {
            "sequence": 1,
            "path": str((root / "alpha.png").resolve()),
            "relative_path": "alpha.png",
            "sha256": sha,
            "bytes": (root / "alpha.png").stat().st_size,
            "snapshot_mtime_ns": (root / "alpha.png").stat().st_mtime_ns,
        },
    )
    MODULE.append_jsonl(
        evidence / "classification.jsonl",
        {
            "completed_at": "2026-08-25T00:00:00Z",
            "sequence": 1,
            "attempt": 1,
            "relative_path": "alpha.png",
            "path": str((root / "alpha.png").resolve()),
            "sha256": sha,
            "status": "complete",
            "decision": "candidate-remove",
            "reason_codes": ["text-covered"],
            "semantic_text_coverage": "complete",
            "visual_increment": "none",
            "rationale": "precomputed",
            **{key: value for key, value in deletion_candidate("alpha.png").items() if key not in {"rationale"}},
        },
    )
    MODULE.append_jsonl(
        evidence / "deletion-ledger.jsonl",
        {
            "completed_at": "2026-08-25T00:00:01Z",
            "sequence": 1,
            "event": "delete-intent",
            "relative_path": "alpha.png",
            "path": str((root / "alpha.png").resolve()),
            "sha256": sha,
        },
    )
    (root / "alpha.png").unlink()

    resumed = run_config(root, input_manifest=manifest, resume=True)
    assert resumed["status"] == "complete"
    assert import_lines(evidence / "deletion-ledger.jsonl")[-1]["state"] == "deleted-recovered"


def test_sha_drift_and_ocr_failure_fail_closed(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image, "beta.png": make_text_image})
    result = run_config(
        root,
        classifier=lambda entry, _config: {
            "status": "ocr-failed" if entry["relative_path"] == "beta.png" else "complete",
            "decision": "needs-review" if entry["relative_path"] == "beta.png" else "retain",
            "reason_codes": ["ocr-failed"] if entry["relative_path"] == "beta.png" else ["manual"],
            "semantic_text_coverage": "unknown" if entry["relative_path"] == "beta.png" else "partial",
            "visual_increment": "unknown" if entry["relative_path"] == "beta.png" else "present",
            "rationale": "bad" if entry["relative_path"] == "beta.png" else "ok",
        },
    )
    assert result["status"] == "needs-review"

    root2 = make_corpus(tmp_path / "drift", {"gamma.png": make_text_image})
    evidence = root2 / "evidence"
    evidence.mkdir()
    manifest = evidence / "input-manifest.jsonl"
    sha = MODULE.sha256(root2 / "gamma.png")
    MODULE.append_jsonl(
        manifest,
        {
            "sequence": 1,
            "path": str((root2 / "gamma.png").resolve()),
            "relative_path": "gamma.png",
            "sha256": sha,
            "bytes": (root2 / "gamma.png").stat().st_size,
            "snapshot_mtime_ns": (root2 / "gamma.png").stat().st_mtime_ns,
        },
    )
    make_diagram_image(root2 / "gamma.png")
    resumed = run_config(root2, input_manifest=manifest, resume=True)
    assert resumed["status"] == "needs-review"
    rows = import_lines(evidence / "classification.jsonl")
    assert rows[0]["status"] == "skipped-changed"


def test_delete_failure_is_durable_and_fails_closed(tmp_path: Path, monkeypatch) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    original_unlink = Path.unlink

    def fail_target(path: Path, *args, **kwargs):
        if path.name == "alpha.png":
            raise PermissionError("simulated unlink denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target)
    result = run_config(
        root,
        classifier=lambda entry, _config: deletion_candidate(entry["relative_path"]),
    )

    assert result["status"] == "needs-review"
    assert (root / "alpha.png").is_file()
    ledger = import_lines(root / "evidence/deletion-ledger.jsonl")
    assert [row.get("event") for row in ledger] == ["delete-intent", "delete-result"]
    assert ledger[-1]["state"] == "delete-failed"


def test_memory_stop_and_child_rss_peak_are_reported(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image, "beta.png": make_text_image})

    def memory_classifier(entry, _config):
        subprocess.run(
            [
                sys.executable,
                "-c",
                "buf = bytearray(12 * 1024 * 1024)\nimport time\ntime.sleep(0.15)\nprint(len(buf))",
                "vision_ocr-test-child",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["manual"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": entry["relative_path"],
        }

    result = run_config(root, classifier=memory_classifier)
    assert result["status"] == "complete"
    rows = import_lines(root / "evidence/classification.jsonl")
    assert rows[0]["resource_usage"]["ocr_child_rss_peak_kib"] > 0
    assert result["resource_usage"]["peak_ocr_child_count"] > 0
    assert result["resource_usage"]["peak_single_ocr_child_rss_kib"] > 0

    root2 = make_corpus(tmp_path / "pressure", {"alpha.png": make_text_image})
    result = run_config(root2, classifier=memory_classifier, memory_limit_mb=1)
    assert result["status"] == "needs-review"
    assert result["stop_reason"].startswith("memory-limit-exceeded:")
    summary = json.loads((root2 / "evidence/summary.json").read_text(encoding="utf-8"))
    assert summary["resource_usage"]["tree_rss_kib"] > 0
    assert summary["classified_count"] == 0


def test_streaming_results_do_not_accumulate_linearly(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    for index in range(12):
        make_text_image(root / f"p{index:03d}.png")

    blob = "x" * (256 * 1024)

    def bulky_classifier(entry, _config):
        return {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["manual"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": blob + entry["relative_path"],
        }

    tracemalloc.start()
    result = run_config(root, classifier=bulky_classifier)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()

    assert result["status"] == "complete"
    assert peak < 7 * len(blob)


def test_resume_reuses_completed_rows_and_marks_new_files_after_snapshot(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image, "beta.png": make_text_image})
    calls: list[str] = []

    def classifier(entry, _config):
        calls.append(entry["relative_path"])
        return {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["manual"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": entry["relative_path"],
        }

    first = run_config(root, classifier=classifier)
    assert first["status"] == "complete"
    make_text_image(root / "gamma.png")
    calls.clear()
    second = run_config(root, classifier=classifier, resume=True)
    assert second["status"] == "complete"
    assert calls == []
    skipped = (root / "evidence/skipped-paths.txt").read_text(encoding="utf-8")
    assert skipped.strip() == "gamma.png"


def test_resume_rejects_mutated_locked_input_manifest(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    first = run_config(
        root,
        classifier=lambda entry, _config: {
            "status": "complete",
            "decision": "retain",
            "reason_codes": ["photo"],
            "semantic_text_coverage": "partial",
            "visual_increment": "present",
            "rationale": entry["relative_path"],
        },
    )
    assert first["status"] == "complete"
    manifest = root / "evidence/input-manifest.jsonl"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="locked input manifest changed"):
        run_config(root, resume=True)


def test_resume_delete_intent_without_eligible_classification_never_deletes(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    evidence = root / "evidence"
    evidence.mkdir()
    image = root / "alpha.png"
    digest = MODULE.sha256(image)
    manifest = evidence / "input-manifest.jsonl"
    MODULE.append_jsonl(
        manifest,
        {
            "sequence": 1,
            "path": str(image.resolve()),
            "relative_path": "alpha.png",
            "sha256": digest,
            "bytes": image.stat().st_size,
            "width": 900,
            "height": 1200,
        },
    )
    MODULE.append_jsonl(
        evidence / "deletion-ledger.jsonl",
        {
            "completed_at": "2026-08-25T00:00:00Z",
            "sequence": 1,
            "event": "delete-intent",
            "path": str(image.resolve()),
            "relative_path": "alpha.png",
            "sha256": digest,
            "input_sha256": digest,
        },
    )

    result = run_config(root, input_manifest=manifest, resume=True)

    assert result["status"] == "needs-review"
    assert image.is_file()
    ledger = import_lines(evidence / "deletion-ledger.jsonl")
    assert ledger[-1]["state"] == "delete-failed"


def test_explicit_manifest_cannot_escape_current_corpus_root(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign = foreign_root / "outside.png"
    make_text_image(foreign)
    evidence = corpus / "evidence"
    evidence.mkdir()
    manifest = evidence / "input-manifest.jsonl"
    MODULE.append_jsonl(
        manifest,
        {
            "sequence": 1,
            "path": str(foreign.resolve()),
            "relative_path": "outside.png",
            "sha256": MODULE.sha256(foreign),
            "bytes": foreign.stat().st_size,
            "width": 900,
            "height": 1200,
        },
    )

    with pytest.raises(RuntimeError, match="escapes corpus_root"):
        run_config(corpus, input_manifest=manifest)
    assert foreign.is_file()


def test_cli_main_writes_summary_and_report(tmp_path: Path) -> None:
    root = make_corpus(tmp_path, {"alpha.png": make_text_image})
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(root),
            "--evidence-dir",
            str(root / "cli-evidence"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode in {0, 1}
    assert (root / "cli-evidence/summary.json").is_file()
    assert (root / "cli-evidence/最终验收报告.md").is_file()
