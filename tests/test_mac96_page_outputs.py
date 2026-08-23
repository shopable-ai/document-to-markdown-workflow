from __future__ import annotations

import json
from pathlib import Path

import yaml

import adapters.mac96_pdf_vision_resume as mac96_resume
import adapters.mac96_pdf_vision_runner as mac96_runner
from adapters.mac96_pdf_vision_resume import existing_output_pages, existing_result, page_outputs_complete, page_reader_record
from adapters.mac96_pdf_vision_runner import page_unit_is_present, repair_page_unit_if_needed, write_page_outputs


def complete_result(page: int) -> dict:
    return {
        "contract": "vision-page-analysis",
        "status": "complete",
        "pass_strategy_used": "dual",
        "model_call_count": 2,
        "text": {
            "status": "complete",
            "markdown": f"## {page} 页面标题\n\n页面正文",
        },
        "visual": {
            "status": "complete",
            "visual_analysis": {
                "unknown_forms": [],
                "uncertainties": [],
            },
        },
        "diagnostics": [],
    }


def pure_text_result(page: int) -> dict:
    result = complete_result(page)
    result["pass_strategy_used"] = "single"
    result["model_call_count"] = 1
    result["visual"]["visual_analysis"].update(
        {
            "page_content_type": "text-only",
            "layout_dependency": "markdown-representable",
            "semantic_visuals": [],
            "ignored_elements": [
                {"type": "qr-code", "action": "ignore", "reason": "excluded-from-reader-assets"}
            ],
            "reader_image_policy": {
                "action": "omit",
                "reason": "text-fully-represented-in-markdown",
                "confidence": "high",
            },
            "visual_forms": [
                {"form": "text", "description": "标题使用不同字号和颜色，但没有独立图示语义"}
            ],
            "relationships": [],
            "tables_or_charts": [],
        }
    )
    return result


def stamp_result(result: dict, image: Path, source_fingerprint: str = "a" * 64) -> dict:
    result["image_sha256"] = mac96_runner.digest(image)
    result["source_fingerprint"] = source_fingerprint
    return result


def test_progress_stream_is_stage_only_and_current_snapshot_is_bounded(tmp_path: Path):
    target = tmp_path / "package"
    evidence = target / "evidence"
    evidence.mkdir(parents=True)
    progress_path = evidence / "progress.jsonl"
    first = mac96_runner.progress_event(
        run_id="run-1",
        source_fingerprint="a" * 64,
        stage="execution_preflight",
        task="execution_preflight",
        status="complete",
        pages=[1, 2, 3],
        evidence_paths=["evidence/preflight.json"],
        next_action="process pages",
    )
    second = mac96_runner.progress_event(
        run_id="run-1",
        source_fingerprint="a" * 64,
        stage="markdown_assembly",
        task="markdown_assembly",
        status="complete",
        pages=[1, 2, 3],
        evidence_paths=["index.md"],
        next_action="validate",
    )
    mac96_runner.append_progress_event(progress_path, first)
    mac96_runner.append_progress_event(progress_path, second)
    mac96_runner.write_progress_current(target, {"page": 2, "status": "in-progress"})
    mac96_runner.write_progress_current(target, {"page": 3, "status": "complete"})

    assert len(progress_path.read_text(encoding="utf-8").splitlines()) == 2
    assert json.loads((evidence / "progress-current.json").read_text(encoding="utf-8")) == {"page": 3, "status": "complete"}


def test_pdf_rendering_yields_ready_page_before_later_pages_finish_and_reuses_assets(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    target = tmp_path / "package"
    (target / "assets").mkdir(parents=True)
    (target / "evidence").mkdir()
    render_calls: list[tuple[int, int]] = []
    processes = []

    class FakeProcess:
        returncode = 0

        def __init__(self, command, **_kwargs):
            start = int(command[command.index("-f") + 1])
            end = int(command[command.index("-l") + 1])
            prefix = Path(command[-1])
            render_calls.append((start, end))
            self.prefix = prefix
            self.remaining = list(range(start + 1, end + 1))
            self.completed = False
            prefix.parent.mkdir(parents=True, exist_ok=True)
            (prefix.parent / f"page-{start:03d}.png").write_bytes(f"page-{start}".encode())
            processes.append(self)

        def poll(self):
            if not self.completed:
                for page in self.remaining:
                    (self.prefix.parent / f"page-{page:03d}.png").write_bytes(f"page-{page}".encode())
                self.completed = True
            return self.returncode

        def communicate(self, **_kwargs):
            return "", ""

    def fake_popen(command, **kwargs):
        return FakeProcess(command, **kwargs)

    monkeypatch.setattr(mac96_runner, "find_tool", lambda _name: "pdftoppm")
    monkeypatch.setattr(mac96_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mac96_runner, "Image", None)

    iterator = mac96_runner.render_pages(source, target, [1, 2, 3], batch_size=2)
    first_page = next(iterator)
    assert first_page[0] == 1
    assert processes[0].completed is False
    rendered = [first_page, *iterator]

    assert [page for page, _image in rendered] == [1, 2, 3]
    assert render_calls == [(1, 2), (3, 3)]
    assert all((target / "assets" / f"page-{page:03d}.png").is_file() for page in [1, 2, 3])
    assert not (target / "evidence" / "render-tmp").exists()

    render_calls.clear()
    reused = list(mac96_runner.render_pages(source, target, [1, 2, 3], batch_size=2))
    assert [page for page, _image in reused] == [1, 2, 3]
    assert render_calls == []

    (target / "assets/page-002.png").unlink()
    render_calls.clear()
    interleaved = list(mac96_runner.render_pages(source, target, [1, 2, 3], batch_size=2))
    assert [page for page, _image in interleaved] == [1, 2, 3]
    assert render_calls == [(2, 2)]


def test_page_completion_requires_both_json_layers_and_page_markdown(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-001.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")

    result = stamp_result(complete_result(1), image)
    write_page_outputs(
        target,
        page=1,
        image=image,
        source_fingerprint="a" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+dual",
    )

    assert (target / "evidence/vision-page-analysis-p001.json").is_file()
    assert (target / "evidence/vision-text-transcription-p001.json").is_file()
    assert page_unit_is_present(target, 1)
    assert page_outputs_complete(target, 1)
    assert existing_output_pages(target, 100) == {1}

    (target / "page-units/p001.md").unlink()
    assert not page_outputs_complete(target, 1)


def test_existing_result_requires_the_current_render_hash(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets/page-001.png"
    evidence = target / "evidence"
    image.parent.mkdir(parents=True)
    evidence.mkdir()
    image.write_bytes(b"current-page")
    result = complete_result(1)
    result["image_sha256"] = mac96_runner.digest(image)
    (evidence / "vision-page-analysis-p001.json").write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )

    assert existing_result(target, 1) == result
    image.write_bytes(b"different-page")
    assert existing_result(target, 1) is None


def test_pure_text_page_omits_reader_screenshot_and_ignores_qr(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-001.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image-with-qr")
    result = stamp_result(pure_text_result(1), image)

    decision = mac96_runner.reader_image_decision(result)
    write_page_outputs(
        target,
        page=1,
        image=image,
        source_fingerprint="a" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+single",
    )

    page_unit = (target / "page-units/p001.md").read_text(encoding="utf-8")
    assert decision["action"] == "omit"
    assert decision["reason"] == "text-fully-represented-in-markdown"
    assert "页面正文" in page_unit
    assert "![" not in page_unit
    assert "二维码" not in page_unit
    assert image.is_file(), "source render remains available as engineering evidence"
    assert page_unit_is_present(target, 1)
    assert page_outputs_complete(target, 1)


def test_guarded_page_skips_unnecessary_language_model_call(tmp_path: Path, monkeypatch):
    target = tmp_path / "package"
    image = target / "assets/page-002.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")
    result = stamp_result(pure_text_result(2), image)
    write_page_outputs(
        target,
        page=2,
        image=image,
        source_fingerprint="a" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+single",
    )
    monkeypatch.setattr(
        mac96_runner,
        "repair_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("repair model must not be called")),
    )

    repair = repair_page_unit_if_needed(
        target,
        page=2,
        host="mac96",
        model="qwen3.6:latest",
        timeout=30,
    )

    assert repair["status"] == "skipped"
    assert repair["model_call_count"] == 0
    assert repair["structure_guard"]["status"] == "passed"
    assert repair["final_sha256"] == mac96_runner.digest(target / "page-units/p002.md")


def test_package_capability_reuses_matching_successful_probe(tmp_path: Path, monkeypatch):
    target = tmp_path / "package"
    evidence = target / "evidence"
    evidence.mkdir(parents=True)
    cached = {
        "status": "complete",
        "target": "mac96",
        "model": "vision-model",
        "model_available": True,
        "vision_capability": True,
    }
    (evidence / "mac96-preflight.json").write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(
        mac96_runner,
        "remote_capability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe must be reused")),
    )

    capability, probed = mac96_runner.package_capability(target, "mac96", "vision-model")

    assert capability == cached
    assert probed is False


def test_missing_or_conflicting_classification_keeps_reader_screenshot():
    missing = complete_result(1)
    conflicting = pure_text_result(2)
    conflicting["visual"]["visual_analysis"]["relationships"] = [
        {"from": "A", "to": "B", "relationship": "连接"}
    ]

    assert mac96_runner.reader_image_decision(missing)["action"] == "retain"
    assert mac96_runner.reader_image_decision(conflicting)["action"] == "retain"


def test_page_title_prefers_first_real_markdown_heading():
    markdown = "# 人性\n\n## 话术大师\n\n正文"

    assert mac96_runner.page_title(markdown, 1) == "人性"


def test_page_unit_presence_rejects_full_document_yaml_envelope(tmp_path: Path):
    target = tmp_path / "package"
    page_unit = target / "page-units/p001.md"
    page_unit.parent.mkdir(parents=True)
    page_unit.write_text(
        "---\n页面正文\n\n![原始页面图](<../assets/page-001.png>)\n---\n",
        encoding="utf-8",
    )

    assert not page_unit_is_present(target, 1)


def test_resume_repairs_missing_page_markdown_without_replacing_existing_json(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-007.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")
    evidence = target / "evidence"
    evidence.mkdir()
    result = stamp_result(complete_result(7), image, "b" * 64)
    analysis_path = evidence / "vision-page-analysis-p007.json"
    analysis_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    text_path = evidence / "vision-text-transcription-p007.json"
    text_path.write_text(
        json.dumps(
            {
                "status": "complete",
                "candidate_markdown": result["text"]["markdown"],
                "source_evidence": "evidence/vision-page-analysis-p007.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert not page_outputs_complete(target, 7)
    write_page_outputs(
        target,
        page=7,
        image=image,
        source_fingerprint="b" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+dual",
    )

    assert page_outputs_complete(target, 7)
    assert json.loads(analysis_path.read_text(encoding="utf-8")) == result
    assert "页面正文" in (target / "page-units/p007.md").read_text(encoding="utf-8")


def test_page_projection_preserves_a_valid_existing_markdown_unit(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-002.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")
    custom = target / "page-units/p002.md"
    custom.parent.mkdir()
    custom.write_text(
        "人工确认后的页面 Markdown\n\n![原始页面图](<../assets/page-002.png>)\n",
        encoding="utf-8",
    )

    result = complete_result(2)
    write_page_outputs(
        target,
        page=2,
        image=image,
        source_fingerprint="c" * 64,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+dual",
    )

    assert custom.read_text(encoding="utf-8").startswith("人工确认后的页面 Markdown")


def test_resume_manifest_section_body_uses_repaired_page_unit(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets" / "page-003.png"
    evidence = target / "evidence"
    image.parent.mkdir(parents=True)
    evidence.mkdir()
    image.write_bytes(b"page-image")
    result = complete_result(3)
    (evidence / "vision-page-analysis-p003.json").write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )
    repaired = "## 修复后的页面标题\n\n修复后的页面正文\n\n![原始页面图](<../assets/page-003.png>)\n"
    page_unit = target / "page-units/p003.md"
    page_unit.parent.mkdir()
    page_unit.write_text(repaired, encoding="utf-8")

    section, _trace, _status, _reader_markdown = page_reader_record(target, 3, "package")

    assert section["body"] == repaired.strip()
    assert section["title"] == "修复后的页面标题"


def test_runtime_summary_counts_canonical_archived_and_interrupted_calls(tmp_path: Path):
    target = tmp_path / "package"
    image = target / "assets/page-001.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"page-image")
    result = stamp_result(pure_text_result(1), image)
    result["requests"] = [{"metrics": {"prompt_eval_count": 10, "eval_count": 5}}]
    write_page_outputs(
        target,
        page=1,
        image=image,
        source_fingerprint="a" * 64,
        host="mac96",
        model="vision-model",
        result=result,
        markdown=result["text"]["markdown"],
        extraction_mode="full+single",
    )
    attempts = target / "evidence/attempts"
    attempts.mkdir()
    archived = stamp_result(complete_result(1), image)
    archived["requests"] = [{"metrics": {"wall_seconds": 2.0, "prompt_eval_count": 20, "eval_count": 8}}]
    (attempts / "vision-page-analysis-p001-old.json").write_text(
        json.dumps(archived), encoding="utf-8"
    )
    (target / "evidence/interrupted-vision-attempts.json").write_text(json.dumps({
        "attempted_call_count": 3,
    }), encoding="utf-8")
    (target / "evidence/source-inventory.json").write_text(json.dumps({
        "page_count_or_dimensions": {"page_count": 2},
    }), encoding="utf-8")

    summary = mac96_resume.write_runtime_summary(target, [1])

    assert summary["model_calls"] == {
        "vision_canonical": 1,
        "vision_archived_failed_or_partial": 2,
        "vision_interrupted_without_response_evidence": 3,
        "vision_attempted_total": 6,
        "language_repair": 0,
        "total_attempted": 6,
    }
    assert summary["noncanonical_attempts"]["observed_prompt_eval_count"] == 20
    assert summary["noncanonical_attempts"]["calls_with_unknown_archived_metrics"] == 1
    assert summary["status"] == "needs-review"
    assert summary["coverage"]["unprocessed_pages"] == [2]


def test_resume_renders_missing_pages_in_same_package_and_skips_completed_page(tmp_path: Path, monkeypatch):
    from PIL import Image

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4")
    source_fingerprint = mac96_runner.digest(source)
    target = tmp_path / "package"
    page1 = target / "assets" / "page-001.png"
    page1.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), "white").save(page1)
    page1_result = stamp_result(pure_text_result(1), page1, source_fingerprint)
    write_page_outputs(
        target,
        page=1,
        image=page1,
        source_fingerprint=source_fingerprint,
        host="mac96",
        model="qwen3-vl:30b-a3b-instruct",
        result=page1_result,
        markdown=page1_result["text"]["markdown"],
        extraction_mode="full+single",
    )

    model_pages: list[int] = []
    checkpoint_calls: list[list[int]] = []

    def fake_render_pages(_source: Path, package: Path, pages: list[int], **_kwargs):
        for page in pages:
            if page == 1:
                continue
            image = package / "assets" / f"page-{page:03d}.png"
            Image.new("RGB", (2, 2), "white").save(image)
            yield page, image

    def fake_model_page(image: Path, *, page: int, **_kwargs):
        model_pages.append(page)
        result = stamp_result(pure_text_result(page), image, source_fingerprint)
        return result, result["text"]["markdown"], "full+single"

    gate = target / "reviews/stage-gate-page-1.json"
    gate.parent.mkdir(parents=True)
    gate.write_text(json.dumps({
        "status": "passed",
        "scope": "single-page",
        "pages": [1],
        "structure_guard": "passed",
        "manual_visual_review": "passed",
    }), encoding="utf-8")
    monkeypatch.setattr(mac96_resume, "pdf_page_count", lambda _source: 5)
    monkeypatch.setattr(mac96_resume, "package_capability", lambda *_args, **_kwargs: ({"status": "complete", "diagnostics": []}, False))
    monkeypatch.setattr(mac96_resume, "render_pages", fake_render_pages, raising=False)
    monkeypatch.setattr(mac96_resume, "model_page", fake_model_page)
    monkeypatch.setattr(mac96_resume, "cleanup_page_units_before_language_repair", lambda *_args, **_kwargs: {"status": "passed", "suspects": []})
    monkeypatch.setattr(mac96_resume, "repair_page_unit_if_needed", lambda _target, *, page, **_kwargs: {"status": "applied", "page": page})
    monkeypatch.setattr(mac96_resume, "validate_page_markdown_checkpoint", lambda _target, expected_pages: checkpoint_calls.append(expected_pages))
    monkeypatch.setattr(
        mac96_resume,
        "write_review",
        lambda path, package_id, review: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text("status: complete\n", encoding="utf-8")),
    )

    package = mac96_resume.run(
        mac96_resume.argparse.Namespace(
            source=source,
            package=target,
            pages="2-5",
            host="mac96",
            model="qwen3-vl:30b-a3b-instruct",
            prompt_file=Path("unused"),
            text_prompt_file=Path("unused"),
            timeout=10,
            retries=1,
            pass_strategy="single",
            render_batch_size=8,
            page_concurrency=2,
            prior_gate=gate,
            refresh_capability=False,
            repair_host="mac96",
            repair_model="qwen3.6:latest",
            repair_timeout=10,
            force_language_repair=False,
            noise_profile=Path("unused"),
        )
    )

    manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))
    trace = json.loads((package / "evidence/source-to-final-trace.json").read_text(encoding="utf-8"))

    assert package == target
    assert sorted(model_pages) == [2, 3, 4, 5]
    assert checkpoint_calls == [[1, 2, 3, 4, 5]]
    assert manifest["source"]["total_pages"] == 5
    assert manifest["source"]["selected_pages"] == [1, 2, 3, 4, 5]
    assert trace["coverage"]["transcribed_pages"] == [1, 2, 3, 4, 5]
    assert "![第 1 页原始页面]" not in (package / "index.md").read_text(encoding="utf-8")
