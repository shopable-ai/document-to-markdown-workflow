from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from adapters import lan_markdown_heading_repair as repair
from adapters import lan_pdf_vision_to_markdown as vision


def test_heading_repair_loader_falls_back_when_script_has_no_adapters_package(
    monkeypatch,
):
    fallback = object()
    calls = []

    def fake_import_module(name):
        calls.append(name)
        if name == "adapters.lan_markdown_heading_repair":
            raise ModuleNotFoundError("No module named 'adapters'", name="adapters")
        assert name == "lan_markdown_heading_repair"
        return fallback

    monkeypatch.setattr(vision.importlib, "import_module", fake_import_module)

    assert vision._load_heading_repair_module() is fallback
    assert calls == [
        "adapters.lan_markdown_heading_repair",
        "lan_markdown_heading_repair",
    ]


def page_text(page: int, title: str, body: str) -> str:
    return f"<!-- page:{page} -->\n# {title}\n\n{body}\n"


def write_page(run_root: Path, page: int, text: str) -> None:
    (run_root / "page-units").mkdir(parents=True, exist_ok=True)
    (run_root / "assets").mkdir(parents=True, exist_ok=True)
    (run_root / "page-units" / f"p{page:03d}.md").write_text(text, encoding="utf-8")
    (run_root / "assets" / f"page-{page:03d}.png").write_bytes(f"png-{page}".encode("utf-8"))


def write_page_result(run_root: Path, page: int, action: str) -> None:
    page_unit_reference = None if action == "omit" else vision.page_unit_image_reference(page)
    document_reference = None if action == "omit" else vision.merged_page_image_reference(page)
    payload = {
        "page": page,
        "status": "passed",
        "page_state": "converted",
        "source_sha256": "source-sha256",
        "reader_image": {
            "action": action,
            "decision_source": "test-fixture",
            "reason_codes": ["fixture"],
            "asset_present": True,
            "semantic_text_coverage": "complete" if action == "omit" else "unknown",
            "layout_dependency": "none" if action == "omit" else "unknown",
            "page_unit_reference": page_unit_reference,
            "document_reference": document_reference,
            "metrics": {},
        },
    }
    evidence = run_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"page-result-p{page:03d}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_model_page_marker_is_always_owned_by_pdf_page_number():
    without_marker, changed = vision.normalize_model_page_marker("# 标题\n\n正文", 99)
    assert changed is True
    assert without_marker.startswith("<!-- page:99 -->\n")

    printed_marker, changed = vision.normalize_model_page_marker(
        "<!-- page:91 -->\n# 标题\n\n正文", 99
    )
    assert changed is True
    assert printed_marker.startswith("<!-- page:99 -->\n")
    assert "<!-- page:91 -->" not in printed_marker


def test_model_marker_inside_body_fails_closed():
    text, changed = vision.normalize_model_page_marker(
        "# 标题\n\n<!-- page:91 -->\n正文", 99
    )
    assert changed is False
    assert text.startswith("# 标题")


def test_complete_outer_markdown_fence_is_unwrapped_without_changing_body():
    text, changed = vision.unwrap_outer_markdown_fence(
        "```markdown\n# 标题\n\n正文内容\n```\n"
    )

    assert changed is True
    assert text == "# 标题\n\n正文内容\n"


def test_partial_or_internal_markdown_fence_is_not_unwrapped():
    text, changed = vision.unwrap_outer_markdown_fence(
        "前言\n```markdown\n正文\n```\n"
    )

    assert changed is False
    assert "```markdown" in text


def test_default_page_projection_injects_page_unit_image_reference_for_p024():
    raw = page_text(24, "做事顺序对了", "## 人变富的底层思维模式\n思维决定行为 | 行为决定结果 | 结果决定命运")

    projected = vision.project_page_image_reference(raw, 24, vision.page_unit_image_reference(24))

    assert "![第24页原始页面图](../assets/page-024.png)" in projected
    assert projected.index("![第24页原始页面图](../assets/page-024.png)") > projected.index("## 人变富的底层思维模式")
    validation = vision.validate_page_markdown(
        projected,
        24,
        reader_image_action="retain",
        expected_image_reference=vision.page_unit_image_reference(24),
    )
    assert validation["passed"] is True


def test_validate_page_markdown_fails_closed_for_missing_retained_image_on_p024():
    raw = page_text(24, "做事顺序对了", "## 人变富的底层思维模式\n思维决定行为 | 行为决定结果 | 结果决定命运")

    validation = vision.validate_page_markdown(
        raw,
        24,
        reader_image_action="retain",
        expected_image_reference=vision.page_unit_image_reference(24),
    )

    assert validation["passed"] is False
    assert any("页面原图引用必须且只能为 ../assets/page-024.png" in error for error in validation["errors"])


def test_confirmed_text_only_page_009_can_omit_reader_image_reference():
    raw = page_text(9, "人性", "1、你会说话，就能搞定50%的人。\n2、你会分利，就能搞定70%的人。")

    projected = vision.project_page_image_reference(raw, 9, None)

    assert "page-009.png" not in projected
    validation = vision.validate_page_markdown(
        projected,
        9,
        reader_image_action="omit",
        expected_image_reference=None,
    )
    assert validation["passed"] is True


def test_merge_document_rewrites_page_unit_image_paths_for_p024_then_p028(tmp_path: Path):
    run_root = tmp_path / "run"
    write_page(
        run_root,
        24,
        vision.project_page_image_reference(
            page_text(24, "做事顺序对了", "## 人变富的底层思维模式\n思维决定行为 | 行为决定结果 | 结果决定命运"),
            24,
            vision.page_unit_image_reference(24),
        ),
    )
    write_page(
        run_root,
        28,
        vision.project_page_image_reference(
            page_text(28, "你自律的顺序可能反了", "## 不要把精力浪费在你无法控制的事情上\n### 普通人\n- 最先想到的是习惯"),
            28,
            vision.page_unit_image_reference(28),
        ),
    )

    result = vision.merge_document(run_root=run_root, pages=[24, 28])

    merged = Path(result["path"]).read_text(encoding="utf-8")
    assert result["status"] == "passed"
    assert "assets/page-024.png" in merged
    assert "assets/page-028.png" in merged
    assert "../assets/page-024.png" not in merged
    assert "../assets/page-028.png" not in merged
    assert (run_root / "assets/page-024.png").is_file()
    assert (run_root / "assets/page-028.png").is_file()


def test_merge_document_does_not_backfill_explicit_omit_page_image(tmp_path: Path):
    run_root = tmp_path / "run"
    write_page(run_root, 9, page_text(9, "致谢", "纯文字页正文。"))
    write_page_result(run_root, 9, "omit")

    result = vision.merge_document(run_root=run_root, pages=[9])

    merged = Path(result["path"]).read_text(encoding="utf-8")
    assert result["status"] == "passed"
    assert "page-009.png" not in merged


def test_run_page_cleanup_preserves_page_image_references(tmp_path: Path):
    run_root = tmp_path / "run"
    for page, title in [(24, "做事顺序对了"), (28, "你自律的顺序可能反了")]:
        write_page(
            run_root,
            page,
            vision.project_page_image_reference(
                f"<!-- page:{page} -->\n\n# {title}\n\n正文保持不变。\n\n扫码咨询\n",
                page,
                vision.page_unit_image_reference(page),
            ),
        )
        evidence = run_root / "evidence"
        evidence.mkdir(exist_ok=True)
        (evidence / f"page-result-p{page:03d}.json").write_text("{}", encoding="utf-8")

    result = vision.run_page_cleanup(
        run_root=run_root,
        pages=[24, 28],
        confirmed_noise=["扫码咨询"],
    )

    assert result["status"] == "passed"
    assert set(result["changed_pages"]) == {24, 28}
    for page in (24, 28):
        page_markdown = (run_root / "page-units" / f"p{page:03d}.md").read_text(encoding="utf-8")
        assert f"../assets/page-{page:03d}.png" in page_markdown
        saved = json.loads((run_root / "evidence" / f"page-result-p{page:03d}.json").read_text(encoding="utf-8"))
        assert saved["page_state"] == "cleaned"


def test_heading_repair_preserves_projected_image_reference(tmp_path: Path):
    run_root = tmp_path / "run"
    write_page(
        run_root,
        24,
        vision.project_page_image_reference(
            "<!-- page:24 -->\n\n第一部分\n\n正文段落\n",
            24,
            vision.page_unit_image_reference(24),
        ),
    )
    page_unit = run_root / "page-units" / "p024.md"
    write_page_result(run_root, 24, "retain")
    (run_root / "document-temp.md").write_text(
        repair._deterministic_merge(run_root, [(24, page_unit)]),
        encoding="utf-8",
    )

    result = repair.run_heading_repair(run_root=run_root, use_model=False)

    index_text = (run_root / "index.md").read_text(encoding="utf-8")
    assert result["status"] == "passed"
    assert "assets/page-024.png" in index_text
    assert "../assets/page-024.png" not in index_text


def test_heading_repair_does_not_backfill_explicit_omit_page_image(tmp_path: Path):
    run_root = tmp_path / "run"
    write_page(run_root, 9, "<!-- page:9 -->\n\n第一部分\n\n纯文字页正文。\n")
    write_page_result(run_root, 9, "omit")
    page_unit = run_root / "page-units" / "p009.md"
    (run_root / "document-temp.md").write_text(
        repair._deterministic_merge(run_root, [(9, page_unit)]),
        encoding="utf-8",
    )

    result = repair.run_heading_repair(run_root=run_root, use_model=False)

    index_text = (run_root / "index.md").read_text(encoding="utf-8")
    assert result["status"] == "passed"
    assert "page-009.png" not in index_text


def test_repair_existing_page_image_references_updates_mutable_hash_and_merge(tmp_path: Path):
    run_root = tmp_path / "run"
    originals: dict[int, str] = {}
    original_hashes: dict[int, str] = {}
    for page in (24, 28):
        raw = page_text(page, f"第{page}页", f"正文第{page}页保持不变。")
        originals[page] = raw
        write_page(run_root, page, raw)
        result_path = run_root / "evidence" / f"page-result-p{page:03d}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        original_hashes[page] = vision.sha256_file(run_root / "page-units" / f"p{page:03d}.md")
        result_path.write_text(
            json.dumps(
                {
                    "page": page,
                    "page_state": "converted",
                    "status": "passed",
                    "markdown": {
                        "vision_sha256": original_hashes[page],
                        "current_sha256": original_hashes[page],
                        "size_bytes": len(raw.encode("utf-8")),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    result = vision.repair_page_image_references(run_root=run_root, pages=[24, 28])

    assert result["status"] == "passed"
    assert result["changed_pages"] == [24, 28]
    merged = (run_root / "document-temp.md").read_text(encoding="utf-8")
    for page in (24, 28):
        unit_path = run_root / "page-units" / f"p{page:03d}.md"
        unit = unit_path.read_text(encoding="utf-8")
        image_line = vision.page_image_markdown(page, vision.page_unit_image_reference(page))
        original_nonblank = [line for line in originals[page].splitlines() if line.strip()]
        repaired_nonblank = [
            line for line in unit.splitlines() if line.strip() and line != image_line
        ]
        assert repaired_nonblank == original_nonblank
        assert unit.count(f"../assets/page-{page:03d}.png") == 1
        assert merged.count(f"assets/page-{page:03d}.png") == 1
        page_result = json.loads(
            (run_root / "evidence" / f"page-result-p{page:03d}.json").read_text(
                encoding="utf-8"
            )
        )
        assert page_result["markdown"]["vision_sha256"] == original_hashes[page]
        assert page_result["markdown"]["current_sha256"] == vision.sha256_file(unit_path)
        assert page_result["markdown"]["size_bytes"] == unit_path.stat().st_size

    repeated = vision.repair_page_image_references(run_root=run_root, pages=[24, 28])
    assert repeated["changed_pages"] == []


def test_validate_saved_page_accepts_pre_projection_prompt_evidence(tmp_path: Path):
    run_root = tmp_path / "run"
    page = 1
    source_sha256 = "source-sha256"
    projected = vision.project_page_image_reference(
        page_text(page, "第一页", "正文保持不变。"),
        page,
        vision.page_unit_image_reference(page),
    )
    write_page(run_root, page, projected)
    asset_path = run_root / "assets" / "page-001.png"
    markdown_path = run_root / "page-units" / "p001.md"
    _payload, _body, request_evidence = vision.request_payload(
        page,
        asset_path.read_bytes(),
        prompt=vision.pre_projection_page_prompt(page),
    )
    evidence = run_root / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "request-evidence-p001.json").write_text(
        json.dumps(request_evidence), encoding="utf-8"
    )
    (evidence / "metrics-p001.json").write_text("{}", encoding="utf-8")
    page_result = {
        "page": page,
        "page_state": "converted",
        "status": "passed",
        "source_sha256": source_sha256,
        "asset": {"sha256": vision.sha256_file(asset_path)},
        "markdown": {
            "vision_sha256": "immutable-vision-sha256",
            "current_sha256": vision.sha256_file(markdown_path),
            "size_bytes": markdown_path.stat().st_size,
        },
    }
    (evidence / "page-result-p001.json").write_text(
        json.dumps(page_result), encoding="utf-8"
    )

    assert vision.validate_saved_page(source_sha256, run_root, page) == page_result


def test_repair_image_links_respects_explicit_omit_even_when_asset_exists(tmp_path: Path):
    run_root = tmp_path / "run"
    page = 9
    write_page(
        run_root,
        page,
        vision.project_page_image_reference(
            page_text(page, "致谢", "纯文字页正文。"),
            page,
            vision.page_unit_image_reference(page),
        ),
    )
    write_page_result(run_root, page, "omit")

    result = vision.repair_page_image_references(run_root=run_root, pages=[page])

    assert result["status"] == "passed"
    repaired = (run_root / "page-units" / "p009.md").read_text(encoding="utf-8")
    assert "page-009.png" not in repaired
    merged = (run_root / "document-temp.md").read_text(encoding="utf-8")
    assert "page-009.png" not in merged


def test_classify_reader_images_marks_text_page_omit_and_complex_page_retain(tmp_path: Path):
    run_root = tmp_path / "run"
    write_page(run_root, 9, page_text(9, "致谢", "这是完整的纯文字正文。\n\n这里继续是纯文字叙述。"))
    write_page_result(run_root, 9, "retain")
    write_page(
        run_root,
        69,
        page_text(
            69,
            "信息记录模板",
            "### 联动\n| 核心利益相关者 | 待填写 |\n| :--- | :--- |\n| 共同解决问题的人 | 示例 |\n",
        ),
    )
    write_page_result(run_root, 69, "retain")

    result = vision.classify_reader_images(run_root=run_root, pages=[9, 69])

    assert result["status"] == "passed"
    assert result["counts"] == {"omit": 1, "retain": 1, "needs-review": 0}
    report = json.loads((run_root / "evidence" / "reader-image-policy.json").read_text(encoding="utf-8"))
    actions = {item["page"]: item["action"] for item in report["decisions"]}
    assert actions == {9: "omit", 69: "retain"}


def test_classify_reader_images_omits_confirmed_blank_ocr_page(tmp_path: Path):
    run_root = tmp_path / "run"
    page = 7
    write_page(
        run_root,
        page,
        vision.project_page_image_reference(
            "<!-- page:7 -->\n",
            page,
            vision.page_unit_image_reference(page),
        ),
    )
    image = Image.new("L", (1000, 1400), color=255)
    ImageDraw.Draw(image).rectangle((498, 1360, 502, 1365), fill=0)
    image.save(run_root / "assets/page-007.png")
    write_page_result(run_root, page, "retain")
    result_path = run_root / "evidence/page-result-p007.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update(
        {
            "route": vision.APPLE_VISION_ROUTE,
            "structure_evidence_path": "evidence/ocr-markdown-structure-p007.json",
        }
    )
    result_path.write_text(json.dumps(result), encoding="utf-8")
    (run_root / "evidence/ocr-markdown-structure-p007.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "structure_inventory": {
                    "page_kind": "image-only-no-recognized-text"
                },
            }
        ),
        encoding="utf-8",
    )

    report = vision.classify_reader_images(run_root=run_root, pages=[page])

    assert report["counts"] == {"omit": 1, "retain": 0, "needs-review": 0}
    stored = json.loads(
        (run_root / "evidence/reader-image-policy.json").read_text(encoding="utf-8")
    )
    decision = stored["decisions"][0]
    assert decision["action"] == "omit"
    assert decision["reason_codes"] == ["confirmed-near-blank-page"]

    repaired = vision.repair_page_image_references(run_root=run_root, pages=[page])

    assert repaired["status"] == "passed"
    markdown = (run_root / "page-units/p007.md").read_text(encoding="utf-8")
    assert "page-007.png" not in markdown
    assert "*（空白页）*" in markdown


def test_validate_run_accepts_document_temp_root_image_projection(
    tmp_path: Path, monkeypatch
):
    run_root = tmp_path / "run"
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf-fixture")
    for page in (24, 28):
        write_page(
            run_root,
            page,
            vision.project_page_image_reference(
                page_text(page, f"第{page}页", "正文保持不变。"),
                page,
                vision.page_unit_image_reference(page),
            ),
        )
    vision.merge_document(run_root=run_root, pages=[24, 28])
    monkeypatch.setattr(vision, "validate_saved_page", lambda *_args: {})
    monkeypatch.setattr(vision, "get_pdf_page_count", lambda _source: 28)

    report = vision.validate_run(
        source=source,
        run_root=run_root,
        pages=[24, 28],
        require_final=False,
    )

    assert report["status"] == "passed"
    assert report["errors"] == []


def test_run_visual_conversion_continues_after_a_page_failure(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf-fixture")
    run_root = tmp_path / "run"
    calls: list[int] = []

    def fake_convert_one_page(**kwargs):
        page = kwargs["page"]
        calls.append(page)
        if page == 1:
            return {"page": page, "status": "failed", "error": "transient"}
        return {"page": page, "status": "passed"}

    monkeypatch.setattr(vision, "get_pdf_page_count", lambda _source: 2)
    monkeypatch.setattr(vision, "_convert_one_page", fake_convert_one_page)

    result = vision.run_visual_conversion(
        source=source,
        run_root=run_root,
        page_selector="all",
        progress_every=10,
    )

    assert result["status"] == "failed"
    assert calls == [1, 1, 1, 2]
    assert result["failed_pages"] == [1]
    assert result["completed_pages"] == [2]
