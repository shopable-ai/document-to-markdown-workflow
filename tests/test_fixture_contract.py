from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from urllib.parse import unquote

try:
    from pypdf import PdfReader
except ModuleNotFoundError:  # pragma: no cover - exercised on dependency-light hosts
    PdfReader = None


ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "18个工作思维模型-last-2-slides"
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
POPPLER_BIN = Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin")


def pdf_page_count_and_text(path: Path) -> tuple[int, str]:
    """Use pypdf when available, otherwise use the system PDF utility path."""
    if PdfReader is not None:
        reader = PdfReader(path)
        return len(reader.pages), "\n".join(page.extract_text() or "" for page in reader.pages)

    pdfinfo = shutil.which("pdfinfo") or str(POPPLER_BIN / "pdfinfo")
    pdftotext = shutil.which("pdftotext") or str(POPPLER_BIN / "pdftotext")
    if not pdfinfo or not pdftotext:
        raise RuntimeError("need pypdf or both pdfinfo and pdftotext for PDF fixture checks")
    info = subprocess.run([pdfinfo, str(path)], check=True, capture_output=True, text=True)
    pages = next(int(line.split(":", 1)[1].strip()) for line in info.stdout.splitlines() if line.startswith("Pages:"))
    text = subprocess.run([pdftotext, str(path), "-"], check=True, capture_output=True, text=True).stdout
    return pages, text


def test_fixture_uses_last_two_content_pages():
    text = (FIXTURE / "fixture.yaml").read_text()
    assert "original_slide_numbers: [24, 25]" in text
    assert "exclude: [1, 2]" in text


def test_selected_pptx_contains_two_slides_and_required_native_text():
    path = FIXTURE / "selected" / "18个工作思维模型-第24-25页.pptx"
    with ZipFile(path) as deck:
        presentation = ET.fromstring(deck.read("ppt/presentation.xml"))
        slide_list = presentation.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldIdLst")
        assert slide_list is not None and len(slide_list) == 2
        for slide_number, words in {
            24: ["领导力", "3357", "三零", "三管", "五带", "七抓"],
            25: ["搞定自己", "搞定别人", "世界观", "价值观", "正确人生观"],
        }.items():
            root = ET.fromstring(deck.read(f"ppt/slides/slide{slide_number}.xml"))
            text = "\n".join(t.text or "" for t in root.findall(".//a:t", NS))
            for word in words:
                assert word in text


def test_selected_pdf_contains_two_pages_and_required_text():
    path = FIXTURE / "selected" / "18个工作思维模型-第24-25页.pdf"
    page_count, text = pdf_page_count_and_text(path)
    assert page_count == 2
    for word in ["领导力", "3357", "搞定自己", "搞定别人", "世界观", "价值观"]:
        assert word in text


def test_visual_baselines_exist():
    assert (FIXTURE / "selected" / "pdf-page-24.png").stat().st_size > 10_000
    assert (FIXTURE / "selected" / "pdf-page-25.png").stat().st_size > 10_000


def test_fixture_run_marks_outputs_as_fixture_samples():
    run = ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package"
    document = run / "index.md"
    manifest = run / "manifest.yaml"
    report = (ROOT / "runs" / "fixture-contract-20260814-0118" / "test-report.yaml").read_text()
    manifest_text = manifest.read_text()
    assert document.is_file()
    assert manifest.is_file()
    assert "generated_business_outputs: []" in report
    assert "fixture_sample_outputs:" in report
    assert "real_business_result_package: null" in report
    text = document.read_text()
    assert "fixture-sample" not in text
    assert "不是解析器自动生成的业务结果" not in text
    assert "提升领导力的3357" in text
    assert "搞定自己 vs 搞定别人" in text
    assert "第 24 页" not in text
    assert "第 25 页" not in text
    assert "一句话主题" not in text
    assert "处理状态" not in text
    for name in [
        "leadership-3357.png",
        "self-vs-others.png",
    ]:
        assert (run / "assets" / name).is_file()
        assert name in text
    assert "kind: markdown-asset-package-fixture-sample" in manifest_text
    assert "status: fixture-contract-only" in manifest_text
    assert "not_production_result: true" in manifest_text


def test_fixture_sample_links_and_manifest_paths_exist():
    run = ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package"
    document = run / "index.md"
    manifest = run / "manifest.yaml"
    document_text = document.read_text()
    manifest_text = manifest.read_text()
    linked_assets = re.findall(r"!\[[^]]*\]\(<([^>]+)>\)", document_text)
    assert linked_assets == [
        "assets/leadership-3357.png",
        "assets/self-vs-others.png",
    ]
    for relative_path in linked_assets:
        assert (run / unquote(relative_path)).is_file()
        assert f"file_path: {relative_path}" in manifest_text


def test_fixture_markdown_places_visual_assets_after_semantic_content():
    run = ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package"
    text = (run / "index.md").read_text()
    p24 = text.index("## 提升领导力的3357")
    p24_img = text.index("![", p24)
    p24_structure = text.index("三零：", p24)
    p25 = text.index("## 搞定自己 vs 搞定别人")
    p25_img = text.index("![", p25)
    p25_structure = text.index("搞定自己：", p25)
    assert p24_structure < p24_img < p25
    assert p25_structure < p25_img
    assert "【需要保留的】原始视觉资产" not in text
    assert "开放集合未知形式扫描" not in text


def test_workflow_a_runner_keeps_generic_source_and_page_contract():
    runner = (ROOT.parent / "archive/legacy-adapters/workflow_a_runner.py").read_text(encoding="utf-8")
    assert "--source" in runner
    assert "--pages" in runner
    assert "pages = [24, 25]" not in runner
    assert "args.pptx.name" not in runner
    assert "source_selection" in runner
    assert "assets/regions" in runner


def test_quality_target_contract_is_95_plus_and_has_hard_gates():
    package_root = ROOT.parent
    workflow = (package_root / "workflows" / "document-to-markdown-assets" / "workflow.yaml").read_text()
    targets = (package_root / "references" / "quality-targets.zh.md").read_text()
    assert "minimum_total: 95" in workflow
    assert "target_total: 97" in workflow
    assert "unknown_form_coverage: 5" in workflow
    assert "no_p0_issue" in workflow
    assert "目标分配" in targets
    assert "P0：未完成不得评分通过" in targets


def test_fixture_manifest_uses_a6_dual_name_and_traceable_occurrences():
    run = ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package"
    manifest = (run / "manifest.yaml").read_text()
    filename_pattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:-\d{2})?\.[a-z0-9]+$")
    names = [
        "leadership-3357.png",
        "self-vs-others.png",
    ]
    assert len(re.findall(r"^    file_name:", manifest, re.MULTILINE)) == 2
    for name in names:
        assert filename_pattern.fullmatch(name)
        assert f"file_name: {name}" in manifest
        assert f"file_path: assets/{name}" in manifest
    assert manifest.count("display_name: 18个工作思维模型｜") == 2
    assert manifest.count("source_occurrence_id:") == 2
    assert "page: 24" in manifest
    assert "page: 25" in manifest
    assert manifest.count("status: generated") == 2
    assert (run / "reports" / "processing-report.yaml").is_file()
    assert (run / "reports" / "acceptance-report.yaml").is_file()
    assert manifest.count("mime_type: image/png") == 2
    assert manifest.count("width: 1080") == 2
    assert manifest.count("height: 1440") == 2
    assert "sha256: 4a49480e6a1eb2f25e658cc03220994ea465b3f73df4f420de6519ad7bcbdafa" in manifest
    assert "sha256: 67145986522062904045d9705bcbfc78d5fa5492dbf43a3e20b650c0b1038085" in manifest


def test_fixture_assets_do_not_claim_available_without_media_integrity_fields():
    """Fixture samples must remain explicit samples; available production assets need media evidence."""
    manifest = (ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package" / "manifest.yaml").read_text()
    assert "status: fixture-contract-only" in manifest
    assert "not_production_result: true" in manifest
    assert manifest.count("status: available") == 2


def test_fixture_reports_have_stage_evidence_and_failure_report():
    run = ROOT / "runs" / "fixture-contract-20260814-0118" / "outputs" / "document-asset-package"
    acceptance = (run / "reports" / "acceptance-report.yaml").read_text()
    processing = (run / "reports" / "processing-report.yaml").read_text()
    failure = (run / "reports" / "failure-report.yaml").read_text()
    for report in (acceptance, processing):
        assert "stage_evidence:" in report
        for stage_id in ("A1", "A2", "A3", "A4", "A5", "A6", "A7"):
            assert f"stage_id: {stage_id}" in report
        assert "evidence_level:" in report
        assert "verification_commands:" in report
        assert "diagnostics:" in report
    assert "status: needs-review" in acceptance
    assert "status: needs-review" in processing
    assert "contract: failure-report" in failure
    assert "status: none" in failure
    assert "failures: []" in failure
    for report in (acceptance, processing, failure):
        lowered = report.lower()
        for forbidden in ("api_key", "authorization:", "cookie:", "subscription_url"):
            assert forbidden not in lowered
