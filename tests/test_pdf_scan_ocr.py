from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adapters import pdf_scan_ocr as scan


def tsv(*words):
    records = [scan._TSV_HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t600\t400\t-1\t"]
    records.extend("\t".join(map(str, (5, 1, 1, 1, *word))) for word in words)
    return ("\n".join(records) + "\n").encode()


PLAIN = tsv(
    (1, 1, 22, 40, 32, 14, 85, "目录："),
    (1, 2, 76, 41, 27, 12, 82, "第一"),
    (1, 3, 510, 43, 14, 10, 96, "12"),
    (2, 1, 22, 70, 32, 14, 85, "目录："),
    (2, 2, 76, 71, 27, 12, 82, "第一"),
    (2, 3, 510, 73, 14, 10, 47, "12"),
)


@pytest.fixture
def page(tmp_path):
    image = tmp_path / "page.png"
    image.write_bytes(b"fixture raster for mocked Tesseract")
    evidence = tmp_path / "evidence" / "page-001.json"
    binding = dict(source_sha="a" * 64, page=1, asset_sha=sha256(image.read_bytes()).hexdigest(),
                   primary_sha="b" * 64, final_sha="c" * 64)
    return image, evidence, binding


@pytest.fixture
def engine(tmp_path, monkeypatch):
    executable = tmp_path / "tesseract"
    executable.write_bytes(b"test engine binary")
    data_dir = tmp_path / "tessdata"
    data_dir.mkdir()
    for lang in scan.LANGUAGES:
        (data_dir / f"{lang}.traineddata").write_bytes(lang.encode())
    calls = []
    result = dict(stdout=PLAIN, stderr=b"", returncode=0)

    def run(command, **kwargs):
        assert command[0] == str(executable)
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
        calls.append((command, kwargs))
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout=b"tesseract 5.5.2\n", stderr=b"")
        if command[1] == "--list-langs":
            return subprocess.CompletedProcess(
                command, 0, stdout=f'List of available languages in "{data_dir}/" (2):\nchi_sim\neng\n'.encode(),
                stderr=b"",
            )
        assert command[1:3] == ["stdin", "stdout"]
        assert command[3:7] == ["--tessdata-dir", str(data_dir), "-l", "chi_sim+eng"]
        if isinstance(result["stdout"], Exception):
            raise result["stdout"]
        return subprocess.CompletedProcess(command, **result)

    monkeypatch.setattr(scan.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(scan.subprocess, "run", run)
    return data_dir, calls, result


def test_collect_and_offline_load_preserve_order_repeats_and_punctuation(page, engine, monkeypatch):
    image, evidence, binding = page
    data_dir, calls, _ = engine
    record = scan.collect(image, evidence, **binding)
    assert (record["schema"], record["version"], record["status"]) == ("pdf-scan-ocr", "1.0.0", "ok")
    assert record["engine_version"] == "5.5.2"
    assert record["model_sha256"] == {lang: sha256(lang.encode()).hexdigest() for lang in scan.LANGUAGES}
    assert record["image_size"] == [600, 400]
    assert record["elapsed_ms"] >= 0
    assert [[w["text"] for w in row] for row in record["rows"]] == [["目录：", "第一", "12"]] * 2
    assert record["rows"][1][-1] == dict(text="12", confidence=.47, x0=510/600, y0=73/400,
                                         x1=524/600, y1=83/400)
    assert calls[-1][1]["input"] == image.read_bytes()
    assert scan.collect(image, evidence, **binding) == record
    assert sum(command[1] == "stdin" for command, _ in calls) == 1

    def forbidden(*args, **kwargs):
        pytest.fail("offline load must not inspect the OCR installation")

    monkeypatch.setattr(scan, "_discover", forbidden)
    image.unlink()
    shutil.rmtree(data_dir)
    assert scan.load(evidence, **binding) == record


@pytest.mark.parametrize(("arg", "field", "new"), [
    ("source_sha", "source_sha256", "d" * 64), ("page", "page", 2),
    ("asset_sha", "asset_sha256", "d" * 64), ("primary_sha", "primary_sha256", "d" * 64),
    ("final_sha", "final_sha256", "d" * 64),
])
def test_binding_drift_never_overwrites(page, engine, arg, field, new):
    image, evidence, binding = page
    scan.collect(image, evidence, **binding)
    before = evidence.read_bytes()
    if arg == "asset_sha":
        image.write_bytes(b"different raster")
        new = sha256(image.read_bytes()).hexdigest()
    changed = dict(binding, **{arg: new})
    with pytest.raises(ValueError, match=f"binding mismatch: {field}"):
        scan.load(evidence, **changed)
    with pytest.raises(ValueError, match=f"binding mismatch: {field}"):
        scan.collect(image, evidence, **changed)
    assert evidence.read_bytes() == before


def test_actual_image_hash_is_required_even_for_cache_hit(page, engine):
    image, evidence, binding = page
    scan.collect(image, evidence, **binding)
    before = evidence.read_bytes()
    image.write_bytes(b"moved contents")
    with pytest.raises(ValueError, match="raster SHA-256"):
        scan.collect(image, evidence, **binding)
    assert evidence.read_bytes() == before


@pytest.mark.parametrize("mutation", ["truncated", "changed-token", "duplicate-key", "invalid-geometry"])
def test_corrupted_cache_fails_closed_without_recollecting(page, engine, mutation):
    image, evidence, binding = page
    record = scan.collect(image, evidence, **binding)
    if mutation == "truncated":
        body = "{"
    elif mutation == "duplicate-key":
        body = evidence.read_text().replace('"page": 1,', '"page": 1, "page": 1,')
    else:
        if mutation == "changed-token":
            record["rows"][0][-1]["text"] = "42"
        else:
            record["rows"][0][0]["x1"] = -1
            record["payload_sha256"] = scan._payload_sha(record)
        body = json.dumps(record)
    evidence.write_text(body)
    _, calls, _ = engine
    calls.clear()
    with pytest.raises(ValueError, match="unusable scan OCR cache"):
        scan.load(evidence, **binding)
    with pytest.raises(ValueError, match="unusable scan OCR cache"):
        scan.collect(image, evidence, **binding)
    assert not calls
    assert evidence.read_text() == body


def test_model_drift_fails_at_collection_but_offline_replays_pinned_version(page, engine):
    image, evidence, binding = page
    data_dir, calls, _ = engine
    record = scan.collect(image, evidence, **binding)
    before = evidence.read_bytes()
    (data_dir / "eng.traineddata").write_bytes(b"changed traineddata")
    calls.clear()
    with pytest.raises(ValueError, match="provenance mismatch"):
        scan.collect(image, evidence, **binding)
    assert all(command[1] != "stdin" for command, _ in calls)
    assert evidence.read_bytes() == before
    assert scan.load(evidence, **binding) == record


@pytest.mark.parametrize(("case", "code"), [
    ("no-engine", "tesseract-unavailable"), ("no-model", "tesseract-language-unavailable"),
    ("partial-load", "tesseract-language-unavailable"), ("nonzero", "tesseract-failed"),
    ("timeout", "tesseract-timeout"), ("bad-output", "tesseract-invalid-tsv"),
    ("no-text", "tesseract-no-text"),
])
def test_failure_records_do_not_supply_rows_and_do_not_retry_in_place(page, engine, monkeypatch, case, code):
    image, evidence, binding = page
    data_dir, calls, runner_result = engine
    if case == "no-engine":
        monkeypatch.setattr(scan.shutil, "which", lambda _: None)
    elif case == "no-model":
        (data_dir / "eng.traineddata").unlink()
    elif case == "partial-load":
        runner_result["stderr"] = b"Failed loading language 'eng'"
    elif case == "nonzero":
        runner_result["returncode"] = 1
    elif case == "timeout":
        runner_result["stdout"] = subprocess.TimeoutExpired("tesseract", 30)
    elif case == "bad-output":
        runner_result["stdout"] = tsv((1, 1, 501, 3, 100, 20, 90, "too wide"))
    else:
        runner_result["stdout"] = tsv()
    record = scan.collect(image, evidence, **binding)
    assert (record["status"], record["reason_code"], record["rows"]) == ("failed", code, [])
    assert record["diagnostic"]
    assert scan.load(evidence, **binding) == record
    calls.clear()
    assert scan.collect(image, evidence, **binding) == record
    assert not calls


@pytest.mark.parametrize(("data", "diagnostic"), [
    (tsv((1, 1, 40, 70, 38, 12, 90, "first"), (2, 1, 40, 30, 38, 12, 90, "second")),
     "rows-overlap-or-out-of-order"),
    (tsv((1, 1, 40, 30, 38, 12, 90, "left"), (1, 2, 420, 30, 38, 12, 90, "right"),
         (2, 1, 40, 70, 38, 12, 90, "left"), (2, 2, 420, 70, 38, 12, 90, "right")),
     "repeated-text-gutter"),
    (tsv((1, 1, 40, 30, 38, 12, 90, "left"), (1, 3, 420, 30, 38, 12, 90, "9")),
     "non-contiguous-tsv-word-index"),
])
def test_uncertain_layout_keeps_original_lines_as_diagnostics(page, engine, data, diagnostic):
    image, evidence, binding = page
    engine[2]["stdout"] = data
    record = scan.collect(image, evidence, **binding)
    assert (record["status"], record["reason_code"]) == ("layout_uncertain", "layout-uncertain")
    assert diagnostic in record["diagnostic"]
    assert record["rows"]
    assert scan.load(evidence, **binding) == record


def test_concurrent_collectors_publish_one_complete_cache(page, engine):
    image, evidence, binding = page
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(scan.collect, image, evidence, **binding) for _ in range(2)]
        records = [future.result(timeout=5) for future in futures]
    assert records[0] == records[1] == scan.load(evidence, **binding)
    assert sum(command[1] == "stdin" for command, _ in engine[1]) == 1


@pytest.mark.parametrize("language", ["english", "chinese"])
def test_local_tesseract_smoke_on_synthetic_page(tmp_path, language):
    from PIL import Image, ImageDraw, ImageFont

    if language == "chinese":
        font_path = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
        if not font_path.is_file():
            pytest.skip("no known local Simplified Chinese test font")
        font = ImageFont.truetype(str(font_path), size=48)
        samples = ("第一章 简单测试 12", "第二章 本地识别 34", "第二章 本地识别 34")
    else:
        font = ImageFont.load_default(size=36)
        samples = ("This is the first line: 12.", "This is the second line: 34.", "The end: 56.")
    image = Image.new("RGB", (900, 360), "white")
    draw = ImageDraw.Draw(image)
    for index, sample in enumerate(samples):
        draw.text((50, 45 + index * 90), sample, font=font, fill="black")
    raster = tmp_path / "page.png"
    image.save(raster)
    evidence = tmp_path / "evidence.json"
    binding = dict(source_sha="1" * 64, page=1, asset_sha=sha256(raster.read_bytes()).hexdigest(),
                   primary_sha="2" * 64, final_sha="3" * 64)
    record = scan.collect(raster, evidence, **binding)
    if record["reason_code"] in {"tesseract-unavailable", "tesseract-language-unavailable"}:
        pytest.skip(record["diagnostic"])
    assert record["status"] == "ok", record["diagnostic"]
    actual = [" ".join(w["text"] for w in row) for row in record["rows"]]
    if language == "chinese":
        # Chinese word segmentation need not match input spaces.
        assert ["".join(row.split()) for row in actual] == ["".join(row.split()) for row in samples]
    else:
        assert actual == list(samples)
    assert scan.load(evidence, **binding) == record
