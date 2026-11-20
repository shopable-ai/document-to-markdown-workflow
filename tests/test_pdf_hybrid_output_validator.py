from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters import pdf_hybrid_output_validator as validator
from adapters import run_pdf_batches as batch


@pytest.fixture
def engineering_package(tmp_path: Path, monkeypatch):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"fixture PDF")
    monkeypatch.setattr(validator.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="Pages: 2\n"))
    root = tmp_path / "result"

    def put(name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_report(name, value):
        put(name, json.dumps(value).encode())

    digest = lambda value: hashlib.sha256(value).hexdigest()
    content = b"<!-- page:1 -->\n\nBody\n\n![Page](assets/page-001.png)\n\n<!-- page:2 -->\n\nMore\n"
    put("index.md", content)
    put("assets/page-001.png", b"fixture image")
    put_report("reports/heading-repair.json", {"status": "passed", "index_sha256": digest(content),
               "validation": {"heading_prefix_only": True}, "formatting_validation": {"passed": True}})
    put_report("reports/page-completion.json", {"status": "passed", "expected_pages": [1, 2],
               "completed_pages": [1, 2], **dict.fromkeys(validator.COVERAGE_EXCEPTIONS, [])})
    for page in (1, 2):
        body = f"<!-- page:{page} -->\n\nBody\n".encode()
        put(f"page-units/p{page:03d}.md", body)
        put_report(f"evidence/page-routes/p{page:03d}.json", {"page": page, "status": "passed",
                   "source_sha256": digest(source.read_bytes()), "page_unit_sha256": digest(body)})
    return root, source, content


def test_hybrid_validator_rederives_coverage_and_rejects_damaged_output(engineering_package, monkeypatch):
    root, source, content = engineering_package
    monkeypatch.setattr(validator, "reader_components", lambda: pytest.fail("legacy validation imported reader workers"))
    assert validator.validate_package(root, source) == validator.validate_engineering_package(root, source)
    assert validator.validate_package(root, source)["status"] == "passed"
    (root / "index.md").write_bytes(content.replace(b"page:2", b"page:1"))
    (root / "page-units/p002.md").write_bytes(b"tampered page")
    (root / "assets/page-001.png").unlink()
    result = validator.validate_package(root, source)
    assert result["status"] == "failed"
    assert result["missing_pages"] == [2]
    assert result["duplicate_pages"] == [1]
    assert result["unverified_page_units"] == [2]
    assert result["invalid_image_references"] == ["assets/page-001.png"]


@pytest.fixture
def reader_workers(engineering_package, monkeypatch):
    root, source, content = engineering_package
    asset = Path("assets/page-001.png")
    counts = {"audit": 0, "export": 0}

    def expected_report(package, pdf, selected_pages):
        assert (package, pdf, selected_pages) == (root, source, [1, 2])
        return {"pages": [{"page": 1, "decision": "crop-required"}, {"page": 2, "decision": "needs-review"}],
                "index_sha256": batch.sha256(root / "index.md"), "asset_sha256": batch.sha256(root / asset)}

    def audit_package(package, pdf, selected_pages):
        counts["audit"] += 1
        return expected_report(package, pdf, selected_pages)

    def validate_audit(package, pdf, report, selected_pages):
        return {"status": "passed" if report == expected_report(package, pdf, selected_pages) else "failed"}

    def export_reader(package, report, destination):
        assert not destination.is_relative_to(package)
        counts["export"] += 1
        (destination / asset.parent).mkdir(parents=True)
        (destination / asset).write_bytes((package / asset).read_bytes())
        (destination / "index.md").write_bytes(content)
        return {"status": "passed"}

    def validate_reader(package, report, destination):
        assert (package, destination) == (root, root / "reader")
        if (destination / "index.md").read_bytes() != content or batch.sha256(destination / asset) != report["asset_sha256"]:
            return {"status": "failed"}
        return {"status": "passed", "index_sha256": batch.sha256(destination / "index.md")}

    monkeypatch.setattr(validator, "reader_components", lambda: (
        SimpleNamespace(audit_package=audit_package, validate_audit=validate_audit),
        SimpleNamespace(export_reader=export_reader, validate_reader=validate_reader),
    ))
    return counts


def test_reader_post_stage_resume_keeps_one_copy_and_preserves_engineering(engineering_package, reader_workers):
    root, source, _ = engineering_package
    source_hash = batch.sha256(source)
    acceptance = {"status": "passed", validator.READER_REQUIRED: True, "source_sha256": source_hash, "selected_pages": [1, 2]}
    for name, value in (("acceptance.json", acceptance), ("summary.json", {"status": "running"}),
                        ("evidence.json", {"status": "running"}), ("progress.json", {
                            "status": "running", "stages": [{"stage": "independent_acceptance", "status": "passed"}],
                        })):
        batch._write_json_atomic(root / name, value)
    protected = [root / "index.md", root / "acceptance.json", *root.glob("page-units/*"), *root.glob("assets/*")]
    before = {item: (item.read_bytes(), item.stat().st_mtime_ns) for item in protected}
    code, result = batch._resume_accepted_hybrid(root, root.name, source, source_hash, [1, 2])
    assert code == 0
    assert result["summary"]["engineering_status"] == "passed"
    stage = result["summary"]["reader_visual"]
    assert stage["bytes_verified"] is True and stage["semantic_status"] == "needs-review"
    assert stage["public_delivery"] is False
    assert stage["unresolved_pages"] == [{"page": 1, "decision": "crop-required"}, {"page": 2, "decision": "needs-review"}]
    assert (root / "reader/assets/page-001.png").is_file()
    assert batch._read_json(root / "progress.json")["stages"][-1]["stage"] == "independent_acceptance"
    assert validator.validate_package(root, source)["status"] == "passed"
    tracked = protected + [root / validator.READER_AUDIT, root / validator.READER_VALIDATION, root / "reader/index.md"]
    checkpoint = {item: (item.read_bytes(), item.stat().st_mtime_ns) for item in tracked}
    assert all(checkpoint[item] == before[item] for item in protected)
    assert batch._resume_accepted_hybrid(root, root.name, source, source_hash, [1, 2]) == (code, result)
    assert {item: (item.read_bytes(), item.stat().st_mtime_ns) for item in tracked} == checkpoint
    assert reader_workers == {"audit": 1, "export": 1}
    (root / validator.READER_VALIDATION).unlink()
    assert batch._resume_accepted_hybrid(root, root.name, source, source_hash, [1, 2])[0] == 1
    assert batch._read_json(root / "summary.json")["status"] == "failed"
    assert batch._resume_accepted_hybrid(root, root.name, source, source_hash, [1, 2])[0] == 1
    assert not (root / validator.READER_VALIDATION).exists()
    assert batch._read_json(root / "acceptance.json") == acceptance


@pytest.mark.parametrize("damaged", ["audit", "forged-audit", "acceptance", "asset", "missing-report", "symlink"])
def test_hybrid_validator_rejects_tampered_or_partial_reader_stage(engineering_package, reader_workers, damaged):
    root, source, _ = engineering_package
    assert batch._finalize_hybrid_reader(root, source, [1, 2])["status"] == "passed"
    audit_path = root / validator.READER_AUDIT
    validation_path = root / validator.READER_VALIDATION
    asset_path = root / "reader/assets/page-001.png"
    if damaged in {"audit", "forged-audit"}:
        audit = batch._read_json(audit_path)
        audit["pages"][0]["decision"] = "candidate-remove"
        batch._write_json_atomic(audit_path, audit)
        if damaged == "forged-audit":
            record = batch._read_json(validation_path)
            batch._write_json_atomic(validation_path, {**record, "audit_sha256": batch.sha256(audit_path)})
    elif damaged == "acceptance":
        record = batch._read_json(validation_path)
        batch._write_json_atomic(validation_path, {**record, "semantic_status": "resolved"})
    elif damaged == "asset":
        asset_path.write_bytes(b"tampered asset")
    elif damaged == "symlink":
        audit_path.rename(audit_path.with_suffix(".orig"))
        audit_path.symlink_to(audit_path.with_suffix(".orig"))
    else:
        validation_path.unlink()
    result = validator.validate_package(root, source)
    assert result["status"] == "failed"
    assert result["engineering_status"] == "passed"
    assert result["reader_visual"]["bytes_verified"] is False


def test_required_reader_stage_cannot_be_absent(engineering_package):
    root, source, _ = engineering_package
    batch._write_json_atomic(root / "acceptance.json", {validator.READER_REQUIRED: True})
    assert validator.validate_package(root, source)["status"] == "failed"


def test_uncommitted_reader_resume_verifies_existing_copy_without_reexport(engineering_package, reader_workers):
    root, source, _ = engineering_package
    verified = batch._finalize_hybrid_reader(root, source, [1, 2])
    assert verified["status"] == "passed"
    (root / validator.READER_VALIDATION).unlink()  # Simulate interruption after the copy's atomic rename.
    assert validator.validate_package(root, source)["status"] == "failed"
    assert batch._finalize_hybrid_reader(root, source, [1, 2]) == verified
    assert reader_workers == {"audit": 1, "export": 1}


def test_real_audit_and_export_workers_verify_nested_reader_on_synthetic_package(engineering_package):
    root, source, content = engineering_package
    asset = root / "assets/page-001.png"
    route_path = root / "evidence/page-routes/p001.json"
    route = batch._read_json(route_path)
    batch._write_json_atomic(route_path, {**route, "visual_asset_sha256": batch.sha256(asset)})

    reader = batch._finalize_hybrid_reader(root, source, [1, 2])
    assert reader["status"] == "needs-review", reader
    assert reader["bytes_verified"] is True
    assert reader["unresolved_pages"] == [{"page": 1, "decision": "needs-review"},
                                          {"page": 2, "decision": "needs-review"}]
    assert (root / "reader/index.md").read_bytes() == content
    assert (root / "reader/assets/page-001.png").read_bytes() == asset.read_bytes()
    assert (root / "index.md").read_bytes() == content
    result = validator.validate_package(root, source)
    assert result["status"] == "needs-review"
    assert result["reader_visual"] == reader
