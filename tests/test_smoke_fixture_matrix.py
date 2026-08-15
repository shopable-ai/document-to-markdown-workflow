import json
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tests/fixtures/smoke-matrix/fixture.yaml"
SCRIPT = ROOT / "scripts/materialize_workflow_a_smoke_inputs.py"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REFERENCE_PPTX = ROOT / "fixtures/18个工作思维模型-last-2-slides/selected/18个工作思维模型-第24-25页.pptx"
REFERENCE_PDF = ROOT / "fixtures/18个工作思维模型-last-2-slides/selected/18个工作思维模型-第24-25页.pdf"


def _slide_parts(path: Path) -> list[str]:
    with ZipFile(path) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    rels = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
    slide_list = presentation.find(f"{{{P_NS}}}sldIdLst")
    assert slide_list is not None
    return [
        "ppt/" + rels[item.attrib[f"{{{R_NS}}}id"]]
        for item in slide_list
    ]


def _pptx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        return "\n".join(
            (node.text or "")
            for part in _slide_parts(path)
            for node in ET.fromstring(archive.read(part)).iter(f"{{http://schemas.openxmlformats.org/drawingml/2006/main}}t")
        )


def _pdf_page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def test_smoke_matrix_covers_requested_minimum_cases():
    data = yaml.safe_load(MATRIX.read_text(encoding="utf-8"))
    cases = {item["id"]: item for item in data["cases"]}
    assert set(cases) == {
        "pptx-single-page",
        "pptx-two-pages",
        "pdf-single-page",
        "pdf-two-pages",
        "same-name-pptx-pdf",
        "pptx-layout-anomaly-pdf",
    }
    assert cases["pptx-single-page"]["page_count"] == 1
    assert cases["pptx-two-pages"]["page_count"] == 2
    assert cases["pdf-single-page"]["page_count"] == 1
    assert cases["pdf-two-pages"]["page_count"] == 2
    assert cases["same-name-pptx-pdf"]["candidate_relation"] == "same-logical-document"
    assert cases["same-name-pptx-pdf"]["evidence_fields"] == [
        "semantic_source",
        "visual_source",
        "visual_difference",
        "selection_reason",
        "uncertainties",
    ]
    assert "pdf-visual-fallback" in cases["pptx-layout-anomaly-pdf"]["checks"]


def test_smoke_matrix_is_explicitly_not_a_production_output():
    data = yaml.safe_load(MATRIX.read_text(encoding="utf-8"))
    assert data["source_policy"]["generated_files_are_test_inputs_not_production_outputs"] is True
    assert "自动把两个来源合并成一个业务 package" in data["not_in_scope"]
    assert data["source_policy"]["materialize"].endswith(
        "materialize_workflow_a_smoke_inputs.py"
    )
    text = MATRIX.read_text(encoding="utf-8")
    for forbidden in ("slide24.xml", "slide25.xml", "第24", "第25", "18个工作思维模型"):
        assert forbidden not in text


def test_materializer_creates_six_inputs_without_a_business_package(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    from materialize_workflow_a_smoke_inputs import materialize

    output = tmp_path / "smoke-inputs"
    record = materialize(output, pptx_source=REFERENCE_PPTX, pdf_source=REFERENCE_PDF)

    assert (output / "pptx-single-page.pptx").is_file()
    assert (output / "pptx-two-pages.pptx").is_file()
    assert (output / "pdf-single-page.pdf").is_file()
    assert (output / "pdf-two-pages.pdf").is_file()
    assert (output / "same-name-candidates/same-name.pptx").is_file()
    assert (output / "same-name-candidates/same-name.pdf").is_file()
    assert (output / "pptx-layout-anomaly-pdf-candidates/layout-anomaly.pptx").is_file()
    assert (output / "pptx-layout-anomaly-pdf-candidates/layout-anomaly.pdf").is_file()
    assert (output / "materialization-record.json").is_file()
    assert not (output / "document.md").exists()
    assert not (output / "assets").exists()
    assert record["generated_files_are_test_inputs_not_production_outputs"] is True

    assert len(_slide_parts(output / "pptx-single-page.pptx")) == 1
    assert len(_slide_parts(output / "pptx-two-pages.pptx")) == 2
    assert _pdf_page_count(output / "pdf-single-page.pdf") == 1
    assert _pdf_page_count(output / "pdf-two-pages.pdf") == 2

    candidates = output / "same-name-candidates"
    assert candidates.joinpath("same-name.pptx").stem == candidates.joinpath("same-name.pdf").stem


def test_materializer_discovers_current_slide_parts_and_preserves_current_text(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    from materialize_workflow_a_smoke_inputs import materialize

    marker = "CURRENT_SOURCE_UNIQUE_MARKER"
    mutated = tmp_path / "current-source.pptx"
    with ZipFile(REFERENCE_PPTX) as source_zip:
        entries = {info.filename: source_zip.read(info.filename) for info in source_zip.infolist()}
    first_part = _slide_parts(REFERENCE_PPTX)[0]
    root = ET.fromstring(entries[first_part])
    text_node = root.find(f".//{{{A_NS}}}t")
    assert text_node is not None
    text_node.text = marker
    entries[first_part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with ZipFile(mutated, "w") as output_zip:
        for name, data in entries.items():
            output_zip.writestr(name, data)

    output = tmp_path / "generated"
    materialize(output, pptx_source=mutated, pdf_source=REFERENCE_PDF)
    generated_text = _pptx_text(output / "pptx-single-page.pptx")
    assert marker in generated_text
    assert "slide24.xml" not in SCRIPT.read_text(encoding="utf-8")
    record = json.loads((output / "materialization-record.json").read_text(encoding="utf-8"))
    assert record["inputs"]["pptx"]["path"] == str(mutated)
    assert record["selection"]["ordinal_pages"] == {"single": [1], "pair": [1, 2]}


def test_layout_anomaly_is_recorded_on_current_first_slide_and_keeps_xml(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    from materialize_workflow_a_smoke_inputs import materialize

    output = tmp_path / "generated"
    materialize(output, pptx_source=REFERENCE_PPTX, pdf_source=REFERENCE_PDF)
    normal_parts = _slide_parts(REFERENCE_PPTX)
    anomaly = output / "pptx-layout-anomaly-pdf-candidates/layout-anomaly.pptx"
    anomaly_parts = _slide_parts(anomaly)
    assert anomaly_parts[0] == normal_parts[0]
    with ZipFile(anomaly) as archive:
        normal_xml = ZipFile(REFERENCE_PPTX).read(normal_parts[0])
        anomaly_xml = archive.read(anomaly_parts[0])
    assert anomaly_xml != normal_xml
    anomaly_root = ET.fromstring(anomaly_xml)
    transform = anomaly_root.find(f".//{{{A_NS}}}xfrm")
    assert transform is not None
    offset = transform.find(f"{{{A_NS}}}off")
    extent = transform.find(f"{{{A_NS}}}ext")
    assert (offset is not None and int(offset.attrib["x"]) < 0) or (
        extent is not None and int(extent.attrib["cx"]) >= 100000000
    )
    record = json.loads((output / "materialization-record.json").read_text(encoding="utf-8"))
    assert record["layout_anomaly"]["slide_part"] == normal_parts[0]
