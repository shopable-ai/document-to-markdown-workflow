#!/usr/bin/env python3
"""Convert PDF pages with one-image Ollama vision calls and compact evidence.

This is an independent LAN vision entry point.  It does not participate in or
modify Workflow A.  Page content stays in ``page-units/pNNN.md``; engineering
state stays under ``evidence/`` and ``reports/``.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import datetime as dt
import gzip
import hashlib
import http.client
import importlib
import io
import json
import logging
import math
import os
import re
import selectors
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterable
from urllib.parse import urlparse

from PIL import Image

try:
    import pdf_resource_slots as resource_slots
except ImportError:  # pragma: no cover - package-style imports in tests/tools
    from . import pdf_resource_slots as resource_slots


EXPECTED_MODEL = "qwen3-vl:30b-a3b-instruct"
APPLE_VISION_ROUTE = "apple-vision-ocr"
DEFAULT_OLLAMA_URL = "http://shopme.local:11434"
DEFAULT_DPI = 220
DEFAULT_NUM_CTX = 16384
DEFAULT_NUM_PREDICT = 8192
EVIDENCE_LEVELS = ("compact", "diagnostic", "full")
PRUNABLE_VISUAL_REASONS = {"cover", "table-of-contents", "text-covered"}
READER_IMAGE_ACTIONS = {"retain", "omit", "needs-review"}
PAGE_MARKER_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")
PAGE_UNIT_RE = re.compile(r"p(\d{3})\.md$")
ATX_HEADING_RE = re.compile(r"^#{1,6}\s+")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
PLACEHOLDER_VISUAL_RE = re.compile(r"^<!--\s*(Image|Table)\b", re.IGNORECASE)
CAPTION_LINE_RE = re.compile(
    r"^(图|表)\s*[0-9一二三四五六七八九十百千]+(?:[-—–.][0-9一二三四五六七八九十百千]+)?\b"
)


def _load_heading_repair_module():
    try:
        return importlib.import_module("adapters.lan_markdown_heading_repair")
    except ModuleNotFoundError as exc:
        if exc.name != "adapters":
            raise
        return importlib.import_module("lan_markdown_heading_repair")


ISOLATED_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:第\s*)?[—–-]?\s*(?:\d{1,4}|[一二三四五六七八九十百千]+)\s*"
    r"(?:页)?\s*[—–-]?\s*$"
)
FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*$", re.IGNORECASE)
PROTECTED_BOUNDARY_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)、]\s+|>\s*|\||!\[|<!--)"
)
MARKDOWN_IMAGE_RE = re.compile(
    r"^\s*!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)\s*$"
)
DIAGRAM_ARROW_TOKENS = ("->", "→", "←", "↔", "⇒", "➜", "⟶", "─>")
BOX_DRAWING_TOKENS = ("┌", "┐", "└", "┘", "│", "├", "┤", "┬", "┴", "┼", "■", "□")
SENTENCE_PUNCTUATION = "。！？!?；;：:"


class RunFailure(RuntimeError):
    """A handled conversion, resume, cleanup, or validation failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def append_json_line(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def append_log_event(run_root: Path, event: str, **fields: Any) -> None:
    """Append one compact stage-level log event without page content."""
    suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    line = f"{utc_now()} INFO {event}" + (f" {suffix}" if suffix else "") + "\n"
    log_path = run_root / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(line)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def pre_projection_page_prompt(page: int) -> str:
    """Return the exact prompt used before deterministic image-link projection."""
    return f"""你看到的是 PDF 第 {page} 页的完整页面截图。请仅根据当前截图，将本页可见内容忠实转换为 Markdown。
硬性要求：
1. 只输出当前截图中可见的 Markdown 正文，不输出任何页码标记。
2. 不要输出任何 `<!-- page:... -->`、PDF 页码、书内印刷页码或页脚页码。
3. 不要输出 Markdown 图片引用；页面原图引用由程序根据原始图片文件名自动注入。
4. 视觉上存在标题时生成对应的 #、## 或 ###。
5. 原文有标题文字但没有 Markdown 标题符号时补上标题符号。
6. 页面没有可见标题时不得增加标题。
7. 不总结、不解释、不改写、不补写或删除正文，不续写截图之外的内容。
8. 保留原有段落、列表、引用、强调、表格、公式和阅读顺序；表格使用有效 GFM Markdown。
9. 不输出分析过程、说明文字或 Markdown 代码围栏。
10. 不使用 PDF 原生文本；只依据当前这一页图片。"""


def page_prompt(page: int) -> str:
    return (
        pre_projection_page_prompt(page)
        + "\n10. 不要输出当前页面截图本身的 Markdown 图片引用；页面原图引用会由后处理程序确定性注入。"
    )


def page_unit_image_reference(page: int) -> str:
    return f"../assets/page-{page:03d}.png"


def merged_page_image_reference(page: int) -> str:
    return f"assets/page-{page:03d}.png"


def page_image_markdown(page: int, reference: str) -> str:
    return f"![第{page}页原始页面图]({reference})"


def _markdown_image_target(line: str) -> str | None:
    match = MARKDOWN_IMAGE_RE.fullmatch(line.rstrip("\r\n"))
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip()


def _is_current_page_image_target(target: str | None, page: int) -> bool:
    return target in {
        page_unit_image_reference(page),
        merged_page_image_reference(page),
    }


def _collapse_blank_runs(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = blank
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return collapsed


def _page_image_insert_index(lines: list[str]) -> int:
    index = 1 if lines else 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines) and ATX_HEADING_RE.match(lines[index]):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    return index


def _page_lines_without_current_image(text: str, page: int) -> list[str]:
    return [
        line
        for line in text.replace("\r\n", "\n").splitlines()
        if not _is_current_page_image_target(_markdown_image_target(line), page)
    ]


def _page_semantic_lines(text: str, page: int) -> list[str]:
    return [
        line
        for line in _page_lines_without_current_image(text, page)
        if not PAGE_MARKER_RE.fullmatch(line.strip())
    ]


def _has_sentence_punctuation(line: str) -> bool:
    return any(token in line for token in SENTENCE_PUNCTUATION)


def _reader_image_references(page: int, action: str) -> tuple[str | None, str | None]:
    if action == "omit":
        return None, None
    return page_unit_image_reference(page), merged_page_image_reference(page)


def _reader_image_policy_record(
    page: int,
    action: str,
    *,
    decision_source: str,
    reason_codes: list[str] | None = None,
    asset_present: bool | None = None,
    semantic_text_coverage: str | None = None,
    layout_dependency: str | None = None,
    metrics: dict[str, Any] | None = None,
    pruned_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in READER_IMAGE_ACTIONS:
        raise RunFailure(f"未知 reader_image_action：{action}")
    page_unit_reference, document_reference = _reader_image_references(page, action)
    return {
        "reader_image_action": action,
        "page_unit_reference": page_unit_reference,
        "document_reference": document_reference,
        "decision_source": decision_source,
        "reason_codes": sorted(dict.fromkeys(reason_codes or [])),
        "asset_present": asset_present,
        "semantic_text_coverage": semantic_text_coverage,
        "layout_dependency": layout_dependency,
        "metrics": metrics or {},
        "pruned_record": pruned_record,
    }


def project_page_image_reference(text: str, page: int, reference: str | None) -> str:
    """Inject or omit the reader image link deterministically outside the model output."""
    cleaned = _page_lines_without_current_image(text, page)
    cleaned = _collapse_blank_runs(cleaned)
    if reference is None:
        return "\n".join(cleaned).rstrip() + "\n"
    insert_at = _page_image_insert_index(cleaned)
    image_line = page_image_markdown(page, reference)
    projected = list(cleaned[:insert_at])
    if projected and projected[-1].strip():
        projected.append("")
    projected.append(image_line)
    if insert_at < len(cleaned) and cleaned[insert_at].strip():
        projected.append("")
    projected.extend(cleaned[insert_at:])
    return "\n".join(_collapse_blank_runs(projected)).rstrip() + "\n"


def unwrap_outer_markdown_fence(text: str) -> tuple[str, bool]:
    """Remove one complete outer Markdown fence while preserving its body."""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().lower() in {"```", "```md", "```markdown"}
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip() + "\n", True
    return normalized, False


def get_pdf_page_count(source: Path) -> int:
    executable = shutil.which("pdfinfo")
    if not executable:
        raise RunFailure("找不到 pdfinfo；未安装或修改任何依赖")
    completed = subprocess.run(
        [executable, str(source)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RunFailure(f"pdfinfo 失败：{completed.stderr.strip()}")
    match = re.search(r"^Pages:\s+(\d+)\s*$", completed.stdout, re.MULTILINE)
    if not match:
        raise RunFailure("无法从 pdfinfo 确认 PDF 页数")
    return int(match.group(1))


def parse_page_selector(selector: str, total_pages: int) -> list[int]:
    if selector == "all":
        return list(range(1, total_pages + 1))
    pages: set[int] = set()
    for raw in selector.split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start, end = int(start_raw), int(end_raw)
            if start < 1 or end < start:
                raise RunFailure(f"无效页码范围：{token}")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise RunFailure(f"无效页码：{token}")
            pages.add(page)
    result = sorted(pages)
    if not result:
        raise RunFailure("页码选择为空")
    invalid = [page for page in result if page > total_pages]
    if invalid:
        raise RunFailure(f"页码超出 1-{total_pages}：{invalid}")
    return result


def discover_page_units(run_root: Path) -> list[int]:
    pages: list[int] = []
    for path in sorted((run_root / "page-units").glob("p[0-9][0-9][0-9].md")):
        match = PAGE_UNIT_RE.fullmatch(path.name)
        if match:
            pages.append(int(match.group(1)))
    if not pages:
        raise RunFailure(f"没有页面单元：{run_root / 'page-units'}")
    return pages


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RunFailure(f"不是有效 PNG：{path}")
    return struct.unpack(">II", header[16:24])


_FAST_PPM_LIMIT = 64 * 1024 * 1024
_FAST_PPM_TIMEOUT = 30
_POPPLER_PPM_HEADER = re.compile(rb"P6\n([0-9]{1,8}) ([0-9]{1,8})\n255\n")


def _bounded_poppler_ppm(command: list[str]) -> io.BytesIO | None:
    """Capture one raw frame with bounded memory and no intermediate disk write."""
    buffer = io.BytesIO()
    deadline = time.monotonic() + _FAST_PPM_TIMEOUT
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                while ready.select(max(0, deadline - time.monotonic())):
                    block = os.read(process.stdout.fileno(), 256 * 1024)
                    if not block:
                        if process.wait(timeout=max(0, deadline - time.monotonic())) == 0:
                            buffer.seek(0)
                            return buffer
                        break
                    if buffer.tell() + len(block) > _FAST_PPM_LIMIT or time.monotonic() >= deadline:
                        break
                    buffer.write(block)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if process.poll() is None:
                process.kill()
    return None


def _render_fast_png(command: list[str], dpi: int, rendered: Path) -> dict[str, float] | None:
    started = time.monotonic()
    try:
        raw = _bounded_poppler_ppm(command)
    except OSError:
        return None  # The legacy command will report the Poppler error.
    if raw is None:
        return None
    with raw:
        with raw.getbuffer() as data:
            header = _POPPLER_PPM_HEADER.match(data)
            if not header:
                return None
            width, height = (int(value) for value in header.groups())
            if not width or not height or len(data) != header.end() + 3 * width * height:
                return None
        try:
            with Image.open(raw, formats=["PPM"]) as frame:
                if frame.mode != "RGB" or frame.size != (width, height):
                    return None
                frame.load()
                raster_seconds = time.monotonic() - started
                frame.save(rendered, format="PNG", dpi=(dpi, dpi), compress_level=6)
        except (Image.UnidentifiedImageError, ValueError):
            return None
    return {
        "raster_wall_seconds": raster_seconds,
        "png_encode_wall_seconds": time.monotonic() - started - raster_seconds,
    }


def render_or_reuse_page(
    source: Path, page: int, dpi: int, target: Path, *, fast_png: bool = False,
) -> dict[str, Any]:
    if target.exists():
        if not target.is_file() or target.stat().st_size == 0:
            raise RunFailure(f"已有页面图无效，拒绝覆盖：{target}")
        width, height = png_dimensions(target)
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "sha256": sha256_file(target),
            "pixel_width": width,
            "pixel_height": height,
            "dpi": dpi,
            "reused": True,
            "wall_seconds": 0.0,
        }
    executable = shutil.which("pdftoppm")
    if not executable:
        raise RunFailure("找不到 pdftoppm；未安装或修改任何依赖")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"render-p{page:03d}-", dir=target.parent) as raw:
        prefix = Path(raw) / f"page-{page:03d}"
        rendered = prefix.with_suffix(".png")
        command = [executable, "-f", str(page), "-l", str(page), "-r", str(dpi)]
        started = time.monotonic()
        timings = _render_fast_png([*command, "-singlefile", str(source)], dpi, rendered) if fast_png else None
        if timings is None:
            completed = subprocess.run(
                [*command, "-png", "-singlefile", str(source), str(prefix)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 or not rendered.is_file() or rendered.stat().st_size == 0:
                raise RunFailure(
                    f"第 {page} 页渲染失败：exit={completed.returncode} "
                    f"{completed.stderr.strip()}"
                )
        wall_seconds = time.monotonic() - started
        png_dimensions(rendered)
        if target.exists():
            raise RunFailure(f"页面图在渲染期间出现，拒绝覆盖：{target}")
        os.replace(rendered, target)
    width, height = png_dimensions(target)
    return {
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "pixel_width": width,
        "pixel_height": height,
        "dpi": dpi,
        "reused": False,
        "wall_seconds": wall_seconds,
        **({"renderer": "poppler-rgb-pillow-png", **timings} if timings is not None else
           {"renderer": "poppler-png-fallback"} if fast_png else {}),
    }


@resource_slots.uses_visual_model_slot("lan-ollama-vision-request")
def send_streaming_request(
    payload_body: bytes,
    *,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout_seconds: int = 900,
    raw_stream: BinaryIO | None = None,
) -> dict[str, Any]:
    """Send the exact body to Ollama and aggregate its NDJSON response."""
    if base_url.rstrip("/") != DEFAULT_OLLAMA_URL:
        raise RunFailure(f"拒绝使用未授权 Ollama 地址：{base_url}")
    parsed = urlparse(base_url)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout_seconds)
    chunks: list[dict[str, Any]] = []
    response_parts: list[str] = []
    thinking_parts: list[str] = []
    started = time.monotonic()
    status = 0
    try:
        connection.request(
            "POST",
            "/api/generate",
            body=payload_body,
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        )
        response = connection.getresponse()
        status = response.status
        while True:
            raw_line = response.readline()
            if not raw_line:
                break
            if raw_stream is not None:
                raw_stream.write(raw_line)
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                chunk = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise RunFailure(f"Ollama 返回无效 NDJSON：{exc}") from exc
            chunks.append(chunk)
            response_parts.append(str(chunk.get("response", "")))
            thinking_parts.append(str(chunk.get("thinking", "")))
    finally:
        wall_seconds = time.monotonic() - started
        connection.close()
    if status != 200:
        raise RunFailure(f"Ollama HTTP {status}")
    if not chunks:
        raise RunFailure("Ollama 响应为空")
    terminal = next((item for item in reversed(chunks) if item.get("done") is True), chunks[-1])
    return {
        "endpoint": f"{DEFAULT_OLLAMA_URL}/api/generate",
        "http_status": status,
        "chunk_count": len(chunks),
        "models_seen": sorted(
            {str(item["model"]) for item in chunks if item.get("model") is not None}
        ),
        "response": "".join(response_parts).replace("\r\n", "\n"),
        "thinking": "".join(thinking_parts),
        "terminal": terminal,
        "wall_seconds": wall_seconds,
    }


def request_payload(
    page: int,
    image_bytes: bytes,
    *,
    prompt: str | None = None,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    prompt = page_prompt(page) if prompt is None else prompt
    payload: dict[str, Any] = {
        "model": EXPECTED_MODEL,
        "prompt": prompt,
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": True,
        "think": False,
        "options": {
            "temperature": 0,
            "num_ctx": DEFAULT_NUM_CTX,
            "num_predict": DEFAULT_NUM_PREDICT,
        },
    }
    body = canonical_json_bytes(payload)
    evidence = {
        "endpoint": f"{DEFAULT_OLLAMA_URL}/api/generate",
        "model": EXPECTED_MODEL,
        "stream": True,
        "think": False,
        "options": payload["options"],
        "images_count": 1,
        "image_bytes": len(image_bytes),
        "image_sha256": sha256_bytes(image_bytes),
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
        "request_body_sha256": sha256_bytes(body),
        "format_present": "format" in payload,
        "used_pdf_native_text": False,
    }
    return payload, body, evidence


def request_payload_variants(page: int, image_bytes: bytes) -> list[tuple[dict[str, Any], bytes, dict[str, Any]]]:
    variants = [
        request_payload(page, image_bytes),
        request_payload(
            page,
            image_bytes,
            prompt=pre_projection_page_prompt(page),
        ),
    ]
    legacy: list[tuple[dict[str, Any], bytes, dict[str, Any]]] = []
    for payload, body, evidence in variants:
        legacy_payload = dict(payload)
        legacy_options = dict(legacy_payload["options"])
        legacy_options["num_predict"] = 4096
        legacy_payload["options"] = legacy_options
        legacy_body = canonical_json_bytes(legacy_payload)
        legacy_evidence = dict(evidence)
        legacy_evidence["request_body_sha256"] = sha256_bytes(legacy_body)
        legacy_evidence["options"] = legacy_options
        legacy.append((legacy_payload, legacy_body, legacy_evidence))
    return variants + legacy


def validate_page_markdown(
    text: str,
    page: int,
    aggregate: dict[str, Any] | None = None,
    *,
    reader_image_action: str | None = None,
    expected_image_reference: str | None = None,
) -> dict[str, Any]:
    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    expected = f"<!-- page:{page} -->"
    markers = [int(value) for value in PAGE_MARKER_RE.findall(normalized)]
    errors: list[str] = []
    if not lines or lines[0] != expected:
        errors.append(f"第一行不是 {expected}")
    if markers != [page]:
        errors.append(f"页面标记必须恰好为 [{page}]，实际为 {markers}")
    if not "\n".join(lines[1:]).strip():
        errors.append("页面正文为空")
    if "```" in normalized:
        errors.append("页面包含代码围栏")
    if aggregate is not None:
        terminal = aggregate.get("terminal") or {}
        if terminal.get("done") is not True:
            errors.append("Ollama 未返回 done:true")
        if terminal.get("done_reason") == "length":
            errors.append("Ollama 输出因长度上限截断")
        if aggregate.get("models_seen") != [EXPECTED_MODEL]:
            errors.append(f"响应模型不正确：{aggregate.get('models_seen')}")
        if aggregate.get("thinking"):
            errors.append("think:false 请求仍返回 thinking 内容")
    image_targets = [
        _markdown_image_target(line)
        for line in lines
        if _markdown_image_target(line) is not None
    ]
    page_image_targets = [target for target in image_targets if _is_current_page_image_target(target, page)]
    if reader_image_action is None:
        errors.append("缺少显式 reader_image_action")
    elif reader_image_action not in READER_IMAGE_ACTIONS:
        errors.append(f"未知 reader_image_action：{reader_image_action}")
    elif reader_image_action == "omit":
        if page_image_targets:
            errors.append("已确认可省略页面原图时不得保留当前页图片引用")
    else:
        if expected_image_reference is None:
            expected_image_reference = page_unit_image_reference(page)
        if expected_image_reference is None:
            errors.append("缺少保图页的期望图片引用")
        elif page_image_targets != [expected_image_reference]:
            errors.append(
                "页面原图引用必须且只能为 "
                f"{expected_image_reference}，实际为 {page_image_targets or '[]'}"
            )
    return {"passed": not errors, "errors": errors, "markers": markers}


def normalize_model_page_marker(text: str, page: int) -> tuple[str, bool]:
    """Replace an optional model marker with the authoritative PDF page marker."""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    markers = [int(value) for value in PAGE_MARKER_RE.findall(normalized)]
    removed = False
    if lines and PAGE_MARKER_RE.fullmatch(lines[0].strip()):
        lines = lines[1:]
        removed = True
    if any(PAGE_MARKER_RE.search(line) for line in lines):
        return normalized, removed
    result = [f"<!-- page:{page} -->", *lines]
    return "\n".join(result).rstrip() + "\n", True


def performance_from_aggregate(aggregate: dict[str, Any], page_wall_seconds: float) -> dict[str, Any]:
    terminal = aggregate.get("terminal") or {}
    result = {
        key: terminal.get(key)
        for key in (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        )
    }
    for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
        value = result.get(key)
        result[f"{key}_seconds"] = value / 1_000_000_000 if value else None
    eval_count = int(result.get("eval_count") or 0)
    eval_duration = int(result.get("eval_duration") or 0)
    result["output_tokens_per_second"] = (
        eval_count / (eval_duration / 1_000_000_000) if eval_count and eval_duration else None
    )
    result["request_wall_seconds"] = aggregate.get("wall_seconds")
    result["page_wall_seconds"] = page_wall_seconds
    return result


def gzip_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with gzip.open(temporary, "wb") as stream:
        stream.write(value)
    os.replace(temporary, path)


def gzip_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    with source.open("rb") as input_stream, gzip.open(temporary, "wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
    os.replace(temporary, destination)


def compact_stats(values: Iterable[float]) -> dict[str, float | None]:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return {"min": None, "median": None, "p95": None, "max": None}
    p95_index = max(0, math.ceil(len(numbers) * 0.95) - 1)
    return {
        "min": round(numbers[0], 3),
        "median": round(statistics.median(numbers), 3),
        "p95": round(numbers[p95_index], 3),
        "max": round(numbers[-1], 3),
    }


def write_progress(
    run_root: Path,
    *,
    phase: str,
    current_page: int | None,
    total_pages: int,
    passed_count: int,
    failed_pages: list[int],
    next_step: str,
    performance_values: Iterable[float] = (),
) -> None:
    write_json(
        run_root / "evidence" / "progress-current.json",
        {
            "current_phase": phase,
            "current_page": current_page,
            "total_pages": total_pages,
            "passed_count": passed_count,
            "failed_pages": sorted(failed_pages),
            "pid": os.getpid(),
            "updated_at": utc_now(),
            "next_step": next_step,
            "performance": compact_stats(performance_values),
        },
    )


def configure_logger(run_root: Path) -> logging.Logger:
    logger = logging.getLogger(f"lan-pdf-vision-{run_root}")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (
        logging.FileHandler(run_root / "run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def page_paths(run_root: Path, page: int) -> dict[str, Path]:
    evidence = run_root / "evidence"
    return {
        "asset": run_root / "assets" / f"page-{page:03d}.png",
        "markdown": run_root / "page-units" / f"p{page:03d}.md",
        "result": evidence / f"page-result-p{page:03d}.json",
        "metrics": evidence / f"metrics-p{page:03d}.json",
        "request": evidence / f"request-evidence-p{page:03d}.json",
        "debug": evidence / "debug" / f"p{page:03d}",
    }


def classify_reader_image_action(page: int, markdown: str, *, asset_present: bool) -> dict[str, Any]:
    lines = [line for line in _page_semantic_lines(markdown, page) if line.strip()]
    caption_lines = [line for line in lines if CAPTION_LINE_RE.match(line)]
    placeholder_lines = [line for line in lines if PLACEHOLDER_VISUAL_RE.match(line)]
    table_lines = [
        line
        for line in lines
        if line.count("|") >= 2 or TABLE_SEPARATOR_RE.match(line) or r"\begin{tabular}" in line
    ]
    arrow_lines = [
        line for line in lines if any(token in line for token in DIAGRAM_ARROW_TOKENS)
    ]
    box_lines = [
        line for line in lines if any(token in line for token in BOX_DRAWING_TOKENS)
    ]
    heading_lines = [line for line in lines if ATX_HEADING_RE.match(line)]
    sentence_like_lines = [
        line
        for line in lines
        if _has_sentence_punctuation(line) and not CAPTION_LINE_RE.match(line)
    ]
    short_non_heading_lines = [
        line for line in lines if not ATX_HEADING_RE.match(line) and len(line) <= 24
    ]
    char_count = len("\n".join(lines))
    metrics = {
        "char_count": char_count,
        "nonempty_line_count": len(lines),
        "caption_line_count": len(caption_lines),
        "placeholder_line_count": len(placeholder_lines),
        "table_line_count": len(table_lines),
        "arrow_line_count": len(arrow_lines),
        "box_line_count": len(box_lines),
        "heading_line_count": len(heading_lines),
        "sentence_like_line_count": len(sentence_like_lines),
        "short_non_heading_line_count": len(short_non_heading_lines),
    }
    action: str
    reason_codes: list[str]
    semantic_text_coverage: str
    layout_dependency: str
    if table_lines:
        action = "retain"
        reason_codes = ["markdown-table-or-tabular"]
        semantic_text_coverage = "complete"
        layout_dependency = "table-or-matrix"
    elif placeholder_lines:
        action = "retain"
        reason_codes = ["unresolved-visual-placeholder"]
        semantic_text_coverage = "partial"
        layout_dependency = "embedded-visual"
    elif arrow_lines or box_lines:
        action = "retain"
        reason_codes = ["diagram-arrow-or-structure"]
        semantic_text_coverage = "complete" if sentence_like_lines else "partial"
        layout_dependency = "diagram"
    elif caption_lines and len(lines) <= 4:
        action = "retain"
        reason_codes = ["caption-dominant-page"]
        semantic_text_coverage = "sparse"
        layout_dependency = "image-centric-caption-page"
    elif caption_lines and char_count <= 140:
        action = "retain"
        reason_codes = ["sparse-caption-page"]
        semantic_text_coverage = "sparse"
        layout_dependency = "image-centric-caption-page"
    elif not caption_lines and char_count >= 120 and sentence_like_lines:
        action = "omit"
        reason_codes = ["semantic-text-coverage-complete"]
        semantic_text_coverage = "complete"
        layout_dependency = "none"
    elif (
        not caption_lines
        and char_count >= 80
        and sentence_like_lines
        and len(short_non_heading_lines) <= 2
    ):
        action = "omit"
        reason_codes = ["semantic-text-coverage-sufficient"]
        semantic_text_coverage = "sufficient"
        layout_dependency = "none"
    elif not caption_lines and len(sentence_like_lines) >= 2:
        action = "omit"
        reason_codes = ["pure-text-page"]
        semantic_text_coverage = "sufficient"
        layout_dependency = "none"
    else:
        action = "needs-review"
        reason_codes = ["borderline-layout-or-sparse-text"]
        semantic_text_coverage = "needs-review"
        layout_dependency = "needs-review"
    if action != "omit" and not asset_present:
        action = "needs-review"
        reason_codes = ["missing-source-asset"]
        semantic_text_coverage = "needs-review"
        layout_dependency = "needs-review"
    return _reader_image_policy_record(
        page,
        action,
        decision_source="heuristic-reader-image-classification",
        reason_codes=reason_codes,
        asset_present=asset_present,
        semantic_text_coverage=semantic_text_coverage,
        layout_dependency=layout_dependency,
        metrics=metrics,
    )


def _near_blank_page_image(path: Path) -> tuple[bool, dict[str, Any]]:
    """Confirm a near-white page using a bounded grayscale histogram."""

    with Image.open(path) as image:
        grayscale = image.convert("L")
        grayscale.thumbnail((512, 512))
        histogram = grayscale.histogram()
        pixel_count = max(1, grayscale.width * grayscale.height)
        dark_pixels = sum(histogram[:245])
        strong_dark_pixels = sum(histogram[:220])
    dark_ratio = dark_pixels / pixel_count
    strong_dark_ratio = strong_dark_pixels / pixel_count
    metrics = {
        "sample_width": grayscale.width,
        "sample_height": grayscale.height,
        "dark_pixel_ratio_lt_245": round(dark_ratio, 8),
        "strong_dark_pixel_ratio_lt_220": round(strong_dark_ratio, 8),
    }
    return dark_ratio <= 0.001 and strong_dark_ratio <= 0.0005, metrics


def classify_reader_images(
    *, run_root: Path, pages: list[int] | None = None
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    selected = pages or discover_page_units(run_root)
    if selected != sorted(set(selected)):
        raise RunFailure("reader image 分类页码必须为无重复升序")
    decisions: list[dict[str, Any]] = []
    changed_pages: list[int] = []
    counts: Counter[str] = Counter()
    needs_review_pages: list[int] = []
    for page in selected:
        paths = page_paths(run_root, page)
        if not paths["markdown"].is_file() or not paths["result"].is_file():
            raise RunFailure(f"第 {page} 页缺少页面单元或 page-result 证据")
        result = json.loads(paths["result"].read_text(encoding="utf-8"))
        resolution = result.get("review_resolution") or {}
        structure_path = run_root / str(result.get("structure_evidence_path") or "")
        structure = (
            json.loads(structure_path.read_text(encoding="utf-8"))
            if result.get("route") == APPLE_VISION_ROUTE and structure_path.is_file()
            else {}
        )
        blank_page = bool(
            structure.get("status") == "passed"
            and (structure.get("structure_inventory") or {}).get("page_kind")
            == "image-only-no-recognized-text"
            and paths["asset"].is_file()
        )
        blank_metrics: dict[str, Any] = {}
        if blank_page:
            blank_page, blank_metrics = _near_blank_page_image(paths["asset"])
        if blank_page:
            decision = _reader_image_policy_record(
                page,
                "omit",
                decision_source="ocr-near-blank-page-confirmation",
                reason_codes=["confirmed-near-blank-page"],
                asset_present=True,
                semantic_text_coverage="empty-page",
                layout_dependency="none",
                metrics=blank_metrics,
            )
        elif (
            result.get("route") == APPLE_VISION_ROUTE
            and resolution.get("status") == "passed"
            and resolution.get("source_image_retained") is True
        ):
            decision = _reader_image_policy_record(
                page,
                "retain",
                decision_source="ocr-review-source-image-retention",
                reason_codes=["uncertain-ocr-source-image-retained"],
                asset_present=paths["asset"].is_file(),
                semantic_text_coverage="needs-review",
                layout_dependency="source-image-required",
            )
        else:
            decision = classify_reader_image_action(
                page,
                paths["markdown"].read_text(encoding="utf-8"),
                asset_present=paths["asset"].is_file(),
            )
        counts[decision["reader_image_action"]] += 1
        if decision["reader_image_action"] == "needs-review":
            needs_review_pages.append(page)
        stored = result.get("reader_image")
        reader_image_record = {
            "action": decision["reader_image_action"],
            "decision_source": decision["decision_source"],
            "reason_codes": decision["reason_codes"],
            "asset_present": decision["asset_present"],
            "semantic_text_coverage": decision["semantic_text_coverage"],
            "layout_dependency": decision["layout_dependency"],
            "page_unit_reference": decision["page_unit_reference"],
            "document_reference": decision["document_reference"],
            "metrics": decision["metrics"],
        }
        if stored != reader_image_record:
            result["reader_image"] = reader_image_record
            write_json(paths["result"], result)
            changed_pages.append(page)
        decisions.append(
            {
                "page": page,
                "action": decision["reader_image_action"],
                "reason_codes": decision["reason_codes"],
                "decision_source": decision["decision_source"],
                "asset_present": decision["asset_present"],
                "semantic_text_coverage": decision["semantic_text_coverage"],
                "layout_dependency": decision["layout_dependency"],
                "page_unit_reference": decision["page_unit_reference"],
                "document_reference": decision["document_reference"],
                "markdown_path": str(paths["markdown"]),
                "markdown_sha256": sha256_file(paths["markdown"]),
                "asset_path": str(paths["asset"]) if paths["asset"].is_file() else None,
                "asset_sha256": sha256_file(paths["asset"]) if paths["asset"].is_file() else None,
                "metrics": decision["metrics"],
            }
        )
    report = {
        "schema": "reader-image-policy",
        "status": "passed",
        "generated_at": utc_now(),
        "pages": selected,
        "counts": {
            "omit": counts.get("omit", 0),
            "retain": counts.get("retain", 0),
            "needs-review": counts.get("needs-review", 0),
        },
        "needs_review_pages": needs_review_pages,
        "decisions": decisions,
    }
    report_path = run_root / "evidence" / "reader-image-policy.json"
    write_json(report_path, report)
    return {
        "status": "passed",
        "path": str(report_path),
        "sha256": sha256_file(report_path),
        "pages": selected,
        "changed_pages": changed_pages,
        "counts": report["counts"],
        "needs_review_pages": needs_review_pages,
    }


def _reader_image_policy_from_result(run_root: Path, page: int) -> dict[str, Any] | None:
    result_path = page_paths(run_root, page)["result"]
    if not result_path.is_file():
        return None
    result = json.loads(result_path.read_text(encoding="utf-8"))
    raw = result.get("reader_image")
    if not isinstance(raw, dict):
        return None
    action = raw.get("action")
    if action not in READER_IMAGE_ACTIONS:
        return None
    return _reader_image_policy_record(
        page,
        action,
        decision_source=str(raw.get("decision_source") or "page-result-reader-image"),
        reason_codes=list(raw.get("reason_codes") or []),
        asset_present=bool(raw.get("asset_present"))
        if raw.get("asset_present") is not None
        else page_paths(run_root, page)["asset"].is_file(),
        semantic_text_coverage=(
            str(raw.get("semantic_text_coverage"))
            if raw.get("semantic_text_coverage") is not None
            else None
        ),
        layout_dependency=(
            str(raw.get("layout_dependency"))
            if raw.get("layout_dependency") is not None
            else None
        ),
        metrics=raw.get("metrics") if isinstance(raw.get("metrics"), dict) else None,
    )


def _reader_image_policy_from_report(run_root: Path, page: int) -> dict[str, Any] | None:
    report_path = run_root / "evidence" / "reader-image-policy.json"
    if not report_path.is_file():
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") != "reader-image-policy" or report.get("status") != "passed":
        raise RunFailure("reader-image-policy.json 状态无效")
    matches = [
        item
        for item in report.get("decisions") or []
        if isinstance(item, dict) and item.get("page") == page
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RunFailure(f"第 {page} 页 reader image 分类记录不唯一")
    record = matches[0]
    action = record.get("action")
    if action not in READER_IMAGE_ACTIONS:
        raise RunFailure(f"第 {page} 页 reader image 分类 action 无效：{action}")
    return _reader_image_policy_record(
        page,
        str(action),
        decision_source=str(record.get("decision_source") or "reader-image-policy-report"),
        reason_codes=list(record.get("reason_codes") or []),
        asset_present=bool(record.get("asset_present"))
        if record.get("asset_present") is not None
        else page_paths(run_root, page)["asset"].is_file(),
        semantic_text_coverage=(
            str(record.get("semantic_text_coverage"))
            if record.get("semantic_text_coverage") is not None
            else None
        ),
        layout_dependency=(
            str(record.get("layout_dependency"))
            if record.get("layout_dependency") is not None
            else None
        ),
        metrics=record.get("metrics") if isinstance(record.get("metrics"), dict) else None,
    )


def page_reader_image_policy(run_root: Path, page: int) -> dict[str, Any]:
    explicit = _reader_image_policy_from_result(run_root, page)
    if explicit is not None:
        return explicit
    explicit = _reader_image_policy_from_report(run_root, page)
    if explicit is not None:
        return explicit
    pruned = pruned_asset_record(run_root, page)
    if pruned is not None:
        return _reader_image_policy_record(
            page,
            "omit",
            decision_source="visual-asset-pruning",
            reason_codes=["pruned-asset-record"],
            asset_present=False,
            semantic_text_coverage="complete",
            layout_dependency="none",
            pruned_record=pruned,
        )
    asset_path = run_root / "assets" / f"page-{page:03d}.png"
    if not asset_path.is_file():
        return _reader_image_policy_record(
            page,
            "omit",
            decision_source="missing-asset-fallback",
            reason_codes=["asset-not-present"],
            asset_present=False,
            semantic_text_coverage="unknown",
            layout_dependency="unknown",
        )
    return _reader_image_policy_record(
        page,
        "retain",
        decision_source="legacy-asset-fallback",
        reason_codes=["asset-presence-fallback"],
        asset_present=True,
        semantic_text_coverage="unknown",
        layout_dependency="unknown",
    )


def pruned_asset_record(run_root: Path, page: int) -> dict[str, Any] | None:
    """Validate the hash-locked deletion chain for one intentionally absent PNG."""
    report_path = run_root / "evidence" / "visual-asset-pruning.json"
    if not report_path.is_file():
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "passed":
        return None
    plan_path = run_root / str(report.get("plan_path") or "")
    classification_path = run_root / str(report.get("classification_report") or "")
    if (
        not plan_path.is_file()
        or sha256_file(plan_path) != report.get("plan_sha256")
        or not classification_path.is_file()
        or sha256_file(classification_path)
        != report.get("classification_report_sha256")
    ):
        raise RunFailure("视觉资产清理报告的计划或分类哈希链无效")
    matches = [
        item
        for item in report.get("deleted_assets") or []
        if isinstance(item, dict) and item.get("page") == page
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RunFailure(f"第 {page} 页视觉资产删除记录不唯一")
    record = matches[0]
    expected_path = f"assets/page-{page:03d}.png"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    classification = json.loads(classification_path.read_text(encoding="utf-8"))
    plan_matches = [
        item
        for item in plan.get("decisions") or []
        if isinstance(item, dict) and item.get("page") == page
    ]
    classification_matches = [
        item
        for item in classification.get("decisions") or []
        if isinstance(item, dict) and item.get("page") == page
    ]
    plan_decision = plan_matches[0] if len(plan_matches) == 1 else {}
    classification_decision = (
        classification_matches[0] if len(classification_matches) == 1 else {}
    )
    reasons = plan_decision.get("reason_codes")
    classification_reasons = classification_decision.get("reason_codes")
    valid = (
        record.get("image_path") == expected_path
        and record.get("deletion_status") == "deleted"
        and isinstance(record.get("pre_delete_sha256"), str)
        and len(plan_matches) == 1
        and len(classification_matches) == 1
        and plan_decision.get("eligible_for_delete") is True
        and plan_decision.get("planned_action") == "delete"
        and plan_decision.get("decision") == "candidate-remove"
        and classification_decision.get("decision") == "candidate-remove"
        and isinstance(reasons, list)
        and bool(reasons)
        and set(reasons).issubset(PRUNABLE_VISUAL_REASONS)
        and classification_reasons == reasons
        and plan_decision.get("semantic_text_coverage") == "complete"
        and classification_decision.get("semantic_text_coverage") == "complete"
        and plan_decision.get("visual_increment") == "none"
        and classification_decision.get("visual_increment") == "none"
        and plan_decision.get("pre_delete_sha256")
        == record.get("pre_delete_sha256")
        and classification_decision.get("sha256")
        == record.get("pre_delete_sha256")
    )
    if not valid:
        raise RunFailure(f"第 {page} 页视觉资产删除记录未通过严格门")
    if (run_root / expected_path).exists():
        raise RunFailure(f"第 {page} 页已删除图片重新出现")
    for markdown_path in [
        run_root / "page-units" / f"p{page:03d}.md",
        run_root / "index.md",
        run_root / "document-temp.md",
    ]:
        if markdown_path.is_file() and f"page-{page:03d}.png" in markdown_path.read_text(
            encoding="utf-8"
        ):
            raise RunFailure(f"第 {page} 页已删除图片仍被 Markdown 引用：{markdown_path}")
    return record


def _write_page_text(path: Path, text: str) -> None:
    normalized = text.rstrip() + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != normalized:
        raise RunFailure(f"拒绝覆盖内容不同的已有页面单元：{path}")
    if not path.exists():
        atomic_write_text(path, normalized)


def native_page_fallback_markdown(
    source: Path, page: int, image_reference: str
) -> str:
    """Use the same page's native text only after a truncated visual stream."""
    try:
        completed = subprocess.run(
            [
                "pdftotext",
                "-f",
                str(page),
                "-l",
                str(page),
                "-layout",
                str(source),
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunFailure(f"原生文字 fallback 失败：{exc}") from exc
    text = completed.stdout.replace("\r\n", "\n").strip()
    if not text:
        raise RunFailure("原生文字 fallback 为空")
    return (
        f"<!-- page:{page} -->\n\n"
        f"{text}\n\n"
        f"{page_image_markdown(page, image_reference)}\n"
    )


def validate_saved_page(source_sha256: str, run_root: Path, page: int) -> dict[str, Any]:
    paths = page_paths(run_root, page)
    policy = page_reader_image_policy(run_root, page)
    pruned = policy["pruned_record"]
    required = [paths["markdown"], paths["result"], paths["metrics"], paths["request"]]
    if pruned is None:
        required.insert(0, paths["asset"])
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise RunFailure(f"第 {page} 页恢复事实缺失：{missing}")
    result = json.loads(paths["result"].read_text(encoding="utf-8"))
    request = json.loads(paths["request"].read_text(encoding="utf-8"))
    if result.get("status") != "passed" or result.get("source_sha256") != source_sha256:
        raise RunFailure(f"第 {page} 页状态或来源哈希不允许跳过")
    if result.get("page_state") not in {"converted", "cleaned"}:
        raise RunFailure(f"第 {page} 页 page_state 不允许跳过：{result.get('page_state')}")
    if result.get("page") != page:
        raise RunFailure(f"第 {page} 页证据页码不一致")
    is_local_ocr = result.get("route") == APPLE_VISION_ROUTE
    if is_local_ocr:
        if pruned is not None:
            raise RunFailure(f"第 {page} 页本机 OCR 证据不允许缺失来源页面图")
        asset_sha256 = sha256_file(paths["asset"])
        if request.get("route") != APPLE_VISION_ROUTE:
            raise RunFailure(f"第 {page} 页本机 OCR 路由证据不一致")
        if request.get("image_sha256") != asset_sha256:
            raise RunFailure(f"第 {page} 页本机 OCR 输入图片哈希不一致")
        if request.get("remote_model_calls") != []:
            raise RunFailure(f"第 {page} 页本机 OCR 证据包含远程模型调用")
        raw_path = run_root / str(result.get("ocr_evidence_path") or "")
        structure_path = run_root / str(result.get("structure_evidence_path") or "")
        for label, evidence_path, expected_sha in (
            ("OCR", raw_path, result.get("ocr_evidence_sha256")),
            ("结构", structure_path, result.get("structure_evidence_sha256")),
        ):
            if (
                not evidence_path.is_file()
                or evidence_path.stat().st_size == 0
                or sha256_file(evidence_path) != expected_sha
            ):
                raise RunFailure(f"第 {page} 页{label}证据缺失或哈希不一致")
        ocr_payload = json.loads(raw_path.read_text(encoding="utf-8"))
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        if ocr_payload.get("status") != "complete":
            raise RunFailure(f"第 {page} 页本机 OCR 未完成")
        if structure.get("status") != "passed":
            resolution = result.get("review_resolution") or {}
            if not (
                structure.get("status") == "needs-review"
                and resolution.get("status") == "passed"
                and resolution.get("source_image_retained") is True
                and resolution.get("candidate_accounting_complete") is True
                and policy["reader_image_action"] in {"retain", "needs-review"}
            ):
                raise RunFailure(f"第 {page} 页 OCR 结构仍需复核")
        accounting = structure.get("candidate_accounting") or {}
        if accounting.get("unmapped_indices") or accounting.get("duplicate_indices"):
            raise RunFailure(f"第 {page} 页 OCR 候选映射不完整")
    elif pruned is None:
        image_bytes = paths["asset"].read_bytes()
        request_variants = request_payload_variants(page, image_bytes)
        if not any(
            all(request.get(key) == expected for key, expected in expected_request.items())
            and sha256_bytes(body) == request.get("request_body_sha256")
            for _payload, body, expected_request in request_variants
        ):
            current_expected = request_variants[0][2]
            mismatch = next(
                (
                    key
                    for key, expected in current_expected.items()
                    if request.get(key) != expected
                ),
                None,
            )
            if mismatch is not None:
                raise RunFailure(f"第 {page} 页恢复请求字段不一致：{mismatch}")
            raise RunFailure(f"第 {page} 页请求体哈希无法重建")
    else:
        expected_hash = pruned["pre_delete_sha256"]
        if (
            request.get("image_sha256") != expected_hash
            or (result.get("asset") or {}).get("sha256") != expected_hash
            or request.get("images_count") != 1
        ):
            raise RunFailure(f"第 {page} 页删除前图片哈希与请求/页面证据不一致")
    markdown = paths["markdown"].read_text(encoding="utf-8")
    validation = validate_page_markdown(
        markdown,
        page,
        reader_image_action=policy["reader_image_action"],
        expected_image_reference=policy["page_unit_reference"],
    )
    if not validation["passed"]:
        raise RunFailure(f"第 {page} 页 Markdown 重新校验失败：{validation['errors']}")
    recorded_reader_image = result.get("reader_image")
    if isinstance(recorded_reader_image, dict):
        if recorded_reader_image.get("action") != policy["reader_image_action"]:
            raise RunFailure(f"第 {page} 页 reader_image action 与当前策略不一致")
        if recorded_reader_image.get("page_unit_reference") != policy["page_unit_reference"]:
            raise RunFailure(f"第 {page} 页 reader_image page_unit_reference 不一致")
        if recorded_reader_image.get("document_reference") != policy["document_reference"]:
            raise RunFailure(f"第 {page} 页 reader_image document_reference 不一致")
    current_sha = sha256_file(paths["markdown"])
    recorded_sha = (result.get("markdown") or {}).get("current_sha256")
    if current_sha != recorded_sha:
        raise RunFailure(f"第 {page} 页当前 Markdown 哈希不一致")
    if pruned is None and (result.get("asset") or {}).get("sha256") != sha256_file(paths["asset"]):
        raise RunFailure(f"第 {page} 页图片哈希不一致")
    return result


def validate_failed_page_for_retry(source_sha256: str, run_root: Path, page: int) -> None:
    """Prove that a failed page belongs to this source before reusing its PNG."""
    paths = page_paths(run_root, page)
    result = json.loads(paths["result"].read_text(encoding="utf-8"))
    if result.get("status") != "failed":
        raise RunFailure(f"第 {page} 页不是可重试失败状态")
    if result.get("source_sha256") != source_sha256 or result.get("page") != page:
        raise RunFailure(f"第 {page} 页失败证据与当前来源不一致")
    if not paths["asset"].is_file() or not paths["request"].is_file():
        raise RunFailure(f"第 {page} 页失败重试缺少图片或请求摘要")
    asset_sha = sha256_file(paths["asset"])
    if (result.get("asset") or {}).get("sha256") != asset_sha:
        raise RunFailure(f"第 {page} 页失败证据的图片哈希不一致")
    request = json.loads(paths["request"].read_text(encoding="utf-8"))
    request_variants = request_payload_variants(page, paths["asset"].read_bytes())
    if not any(
        all(request.get(key) == expected for key, expected in expected_request.items())
        and sha256_bytes(body) == request.get("request_body_sha256")
        for _payload, body, expected_request in request_variants
    ):
        raise RunFailure(f"第 {page} 页失败重试请求证据不一致")


def _retain_debug(
    *,
    debug_dir: Path,
    body: bytes,
    raw_path: Path,
    aggregate: dict[str, Any],
    keep_request: bool,
    keep_raw: bool,
    keep_aggregate: bool,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    if keep_request:
        gzip_bytes(debug_dir / "ollama-request.json.gz", body)
    if keep_raw:
        if raw_path.exists():
            gzip_file(raw_path, debug_dir / "ollama-response.raw.jsonl.gz")
        else:
            gzip_bytes(debug_dir / "ollama-response.raw.jsonl.gz", b"")
    if keep_aggregate:
        write_json(debug_dir / "ollama-response.aggregate.json", aggregate)


def _convert_one_page(
    *,
    source: Path,
    source_sha256: str,
    run_root: Path,
    page: int,
    evidence_level: str,
    dpi: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    paths = page_paths(run_root, page)
    page_started = time.monotonic()
    render = render_or_reuse_page(source, page, dpi, paths["asset"])
    image_bytes = paths["asset"].read_bytes()
    _payload, body, request_evidence = request_payload(page, image_bytes)
    write_json(paths["request"], request_evidence)
    paths["debug"].mkdir(parents=True, exist_ok=True)
    raw_fd, raw_name = tempfile.mkstemp(
        prefix="ollama-response-", suffix=".jsonl", dir=paths["debug"]
    )
    os.close(raw_fd)
    raw_path = Path(raw_name)
    aggregate: dict[str, Any] = {}
    try:
        with raw_path.open("wb") as raw_stream:
            aggregate = send_streaming_request(
                body, timeout_seconds=timeout_seconds, raw_stream=raw_stream
            )
        model_text, fence_unwrapped = unwrap_outer_markdown_fence(
            str(aggregate.get("response") or "")
        )
        markdown = project_page_image_reference(
            model_text,
            page,
            page_unit_image_reference(page),
        )
        markdown, marker_normalized = normalize_model_page_marker(markdown, page)
        validation = validate_page_markdown(
            markdown,
            page,
            aggregate,
            reader_image_action="retain",
            expected_image_reference=page_unit_image_reference(page),
        )
        if not validation["passed"]:
            raise RunFailure("；".join(validation["errors"]))
        _write_page_text(paths["markdown"], markdown)
        page_wall = time.monotonic() - page_started
        metrics = performance_from_aggregate(aggregate, page_wall)
        write_json(paths["metrics"], metrics)
        result = {
            "page": page,
            "page_state": "converted",
            "status": "passed",
            "source_sha256": source_sha256,
            "model": EXPECTED_MODEL,
            "asset": render,
            "markdown": {
                "path": str(paths["markdown"]),
                "size_bytes": paths["markdown"].stat().st_size,
                "vision_sha256": sha256_file(paths["markdown"]),
                "current_sha256": sha256_file(paths["markdown"]),
            },
            "reader_image": {
                "action": "retain",
                "decision_source": "conversion-default",
                "reason_codes": ["pending-explicit-classification"],
                "asset_present": True,
                "semantic_text_coverage": "unknown",
                "layout_dependency": "unknown",
                "page_unit_reference": page_unit_image_reference(page),
                "document_reference": merged_page_image_reference(page),
                "metrics": {},
            },
            "request_evidence_path": str(paths["request"]),
            "metrics_path": str(paths["metrics"]),
            "validation": validation,
            "marker_normalized": marker_normalized,
            "outer_markdown_fence_unwrapped": fence_unwrapped,
            "finished_at": utc_now(),
        }
        write_json(paths["result"], result)
        _retain_debug(
            debug_dir=paths["debug"],
            body=body,
            raw_path=raw_path,
            aggregate=aggregate,
            keep_request=evidence_level == "full",
            keep_raw=evidence_level in {"diagnostic", "full"},
            keep_aggregate=evidence_level == "full",
        )
        return result
    except Exception as exc:
        failure_aggregate = aggregate or {
            "status": "failed",
            "error": str(exc),
            "response_available": raw_path.exists() and raw_path.stat().st_size > 0,
        }
        _retain_debug(
            debug_dir=paths["debug"],
            body=body,
            raw_path=raw_path,
            aggregate=failure_aggregate,
            keep_request=True,
            keep_raw=True,
            keep_aggregate=True,
        )
        if (
            str(exc) in {"Ollama 未返回 done:true", "页面包含代码围栏"}
            and aggregate.get("response")
        ):
            fallback_markdown = native_page_fallback_markdown(
                source,
                page,
                page_unit_image_reference(page),
            )
            fallback_validation = validate_page_markdown(
                fallback_markdown,
                page,
                None,
                reader_image_action="retain",
                expected_image_reference=page_unit_image_reference(page),
            )
            if not fallback_validation["passed"]:
                raise RunFailure(
                    "原生文字 fallback 页面校验失败："
                    + "；".join(fallback_validation["errors"])
                ) from exc
            _write_page_text(paths["markdown"], fallback_markdown)
            page_wall = time.monotonic() - page_started
            write_json(
                paths["metrics"],
                {
                    "route": "native-text-fallback",
                    "request_wall_seconds": aggregate.get("wall_seconds"),
                    "page_wall_seconds": page_wall,
                    "visual_stream_done": False,
                },
            )
            result = {
                "page": page,
                "page_state": "converted",
                "status": "passed",
                "route": "native-text-fallback",
                "source_sha256": source_sha256,
                "model": EXPECTED_MODEL,
                "asset": render,
                "markdown": {
                    "path": str(paths["markdown"]),
                    "size_bytes": paths["markdown"].stat().st_size,
                    "vision_sha256": sha256_file(paths["markdown"]),
                    "current_sha256": sha256_file(paths["markdown"]),
                },
                "reader_image": {
                    "action": "retain",
                    "decision_source": "conversion-default",
                    "reason_codes": ["pending-explicit-classification"],
                    "asset_present": True,
                    "semantic_text_coverage": "unknown",
                    "layout_dependency": "unknown",
                    "page_unit_reference": page_unit_image_reference(page),
                    "document_reference": merged_page_image_reference(page),
                    "metrics": {},
                },
                "request_evidence_path": str(paths["request"]),
                "metrics_path": str(paths["metrics"]),
                "validation": fallback_validation,
                "fallback_reason": (
                    "visual-stream-missing-done"
                    if str(exc) == "Ollama 未返回 done:true"
                    else "visual-markdown-fence"
                ),
                "finished_at": utc_now(),
            }
            write_json(paths["result"], result)
            return result
        result = {
            "page": page,
            "page_state": "failed",
            "status": "failed",
            "source_sha256": source_sha256,
            "model": EXPECTED_MODEL,
            "asset": render,
            "request_evidence_path": str(paths["request"]),
            "debug_path": str(paths["debug"]),
            "error": str(exc),
            "finished_at": utc_now(),
        }
        write_json(paths["result"], result)
        return result
    finally:
        if raw_path.exists():
            raw_path.unlink()
        try:
            paths["debug"].rmdir()
        except OSError:
            pass


def _summary(
    *,
    source: Path,
    source_sha256: str,
    run_root: Path,
    total_pages: int,
    status: str,
) -> dict[str, Any]:
    passed_pages: list[int] = []
    failed_pages: list[int] = []
    image_match = True
    image_count = 0
    request_seconds: list[float] = []
    routes: set[str] = set()
    for result_path in sorted((run_root / "evidence").glob("page-result-p[0-9][0-9][0-9].json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        page = int(result.get("page") or 0)
        routes.add(str(result.get("route") or "ollama-vision"))
        if result.get("status") == "passed":
            passed_pages.append(page)
        else:
            failed_pages.append(page)
        asset_path = run_root / "assets" / f"page-{page:03d}.png"
        if asset_path.is_file():
            image_count += 1
            image_match = image_match and (result.get("asset") or {}).get("sha256") == sha256_file(asset_path)
        metrics_path = run_root / "evidence" / f"metrics-p{page:03d}.json"
        if metrics_path.is_file():
            value = json.loads(metrics_path.read_text(encoding="utf-8")).get("request_wall_seconds")
            if value is not None:
                request_seconds.append(float(value))
    final_path = run_root / "index.md"
    final_markdown = None
    heading_report_path = run_root / "reports" / "heading-repair.json"
    document_temp_path = run_root / "document-temp.md"
    if final_path.is_file() and heading_report_path.is_file() and document_temp_path.is_file():
        try:
            heading_report = json.loads(heading_report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            heading_report = {}
        if (
            heading_report.get("status") == "passed"
            and heading_report.get("source_sha256") == sha256_file(document_temp_path)
            and heading_report.get("index_sha256") == sha256_file(final_path)
        ):
            final_markdown = {
                "path": str(final_path),
                "size_bytes": final_path.stat().st_size,
                "sha256": sha256_file(final_path),
            }
    pruning_path = run_root / "evidence" / "visual-asset-pruning.json"
    pruning_summary = None
    if pruning_path.is_file():
        pruning = json.loads(pruning_path.read_text(encoding="utf-8"))
        pruning_summary = {
            "status": pruning.get("status"),
            "path": str(pruning_path),
            "sha256": sha256_file(pruning_path),
            "counts": pruning.get("counts"),
        }
    request_summary: dict[str, Any]
    if routes == {APPLE_VISION_ROUTE}:
        request_summary = {
            "route": APPLE_VISION_ROUTE,
            "engine": "Apple Vision VNRecognizeTextRequest",
            "model": "not-applicable",
            "recognition_level": "accurate",
            "languages": ["zh-Hans", "en-US"],
            "uses_language_correction": True,
            "remote_model_calls": [],
            "used_pdf_native_text": False,
        }
    else:
        request_summary = {
            "endpoint": f"{DEFAULT_OLLAMA_URL}/api/generate",
            "model": EXPECTED_MODEL,
            "stream": True,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": DEFAULT_NUM_CTX,
                "num_predict": DEFAULT_NUM_PREDICT,
            },
            "format_present": False,
            "used_pdf_native_text": False,
        }
    return {
        "status": status,
        "source": {"path": str(source), "sha256": source_sha256},
        "total_pages": total_pages,
        "passed_pages": len(passed_pages),
        "failed_pages": sorted(failed_pages),
        "request": request_summary,
        "images": {
            "count": image_count,
            "hash_conclusion": "all-match-page-evidence" if image_match else "mismatch",
        },
        "visual_asset_pruning": pruning_summary,
        "final_markdown": final_markdown,
        "performance_seconds": compact_stats(request_seconds),
        "log_path": str(run_root / "run.log"),
        "pid": os.getpid(),
        "run_directory": str(run_root),
        "validation_report_path": str(run_root / "reports" / "validation-result.json"),
    }


@resource_slots.uses_local_pdf_slot("lan-visual-pdf-to-markdown")
def run_visual_conversion(
    *,
    source: Path,
    run_root: Path,
    page_selector: str = "all",
    evidence_level: str = "compact",
    resume: bool = False,
    dpi: int = DEFAULT_DPI,
    timeout_seconds: int = 900,
    progress_every: int = 10,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    run_root = run_root.expanduser().resolve()
    if evidence_level not in EVIDENCE_LEVELS:
        raise RunFailure(f"无效留证级别：{evidence_level}")
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise RunFailure(f"源文件不是可读 PDF：{source}")
    if not 200 <= dpi <= 250:
        raise RunFailure("DPI 必须位于 200-250")
    had_existing_content = run_root.exists() and any(run_root.iterdir())
    if had_existing_content and not resume:
        raise RunFailure(f"运行目录非空；继续时必须显式使用 --resume：{run_root}")
    for directory in (run_root / "assets", run_root / "page-units", run_root / "evidence", run_root / "reports"):
        directory.mkdir(parents=True, exist_ok=True)
    logger = configure_logger(run_root)
    started_at = utc_now()
    total_pages = get_pdf_page_count(source)
    pages = parse_page_selector(page_selector, total_pages)
    source_hash = sha256_file(source)
    summary_path = run_root / "summary.json"
    if resume and had_existing_content:
        if not summary_path.is_file():
            raise RunFailure("恢复运行缺少 summary.json，无法确认运行根来源")
        existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        existing_source = existing_summary.get("source") or {}
        existing_request = existing_summary.get("request") or {}
        if existing_source.get("sha256") != source_hash:
            raise RunFailure("恢复运行的来源 SHA-256 不一致")
        expected_request = {
            "endpoint": f"{DEFAULT_OLLAMA_URL}/api/generate",
            "model": EXPECTED_MODEL,
            "stream": True,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": DEFAULT_NUM_CTX,
                "num_predict": DEFAULT_NUM_PREDICT,
            },
            "format_present": False,
            "used_pdf_native_text": False,
        }
        legacy_request = dict(expected_request)
        legacy_options = dict(expected_request["options"])
        legacy_options["num_predict"] = 4096
        legacy_request["options"] = legacy_options
        if existing_request != expected_request and existing_request != legacy_request:
            raise RunFailure("恢复运行的模型或请求参数不一致")
    write_json(
        summary_path,
        _summary(
            source=source,
            source_sha256=source_hash,
            run_root=run_root,
            total_pages=total_pages,
            status="running",
        ),
    )
    passed: list[int] = []
    failed: list[int] = []
    skipped: list[int] = []
    request_seconds: list[float] = []
    max_page_attempts = 3
    logger.info(
        "RUN_START pages=%s model=%s evidence=%s resume=%s",
        page_selector,
        EXPECTED_MODEL,
        evidence_level,
        resume,
    )
    write_progress(
        run_root,
        phase="visual-conversion",
        current_page=None,
        total_pages=total_pages,
        passed_count=0,
        failed_pages=[],
        next_step=f"process page {pages[0]}",
    )
    for index, page in enumerate(pages, 1):
        result: dict[str, Any] = {"status": "failed", "error": "page did not run"}
        for attempt in range(1, max_page_attempts + 1):
            try:
                existing_result_path = page_paths(run_root, page)["result"]
                if resume and existing_result_path.is_file():
                    existing = json.loads(existing_result_path.read_text(encoding="utf-8"))
                    if existing.get("status") == "passed":
                        result = validate_saved_page(source_hash, run_root, page)
                        skipped.append(page)
                    else:
                        validate_failed_page_for_retry(source_hash, run_root, page)
                        result = _convert_one_page(
                            source=source,
                            source_sha256=source_hash,
                            run_root=run_root,
                            page=page,
                            evidence_level=evidence_level,
                            dpi=dpi,
                            timeout_seconds=timeout_seconds,
                        )
                else:
                    result = _convert_one_page(
                        source=source,
                        source_sha256=source_hash,
                        run_root=run_root,
                        page=page,
                        evidence_level=evidence_level,
                        dpi=dpi,
                        timeout_seconds=timeout_seconds,
                    )
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)}
            if result.get("status") == "passed":
                break
            if attempt < max_page_attempts:
                logger.warning(
                    "PAGE_RETRY page=%d attempt=%d/%d error=%s",
                    page,
                    attempt,
                    max_page_attempts,
                    result.get("error"),
                )
        if result.get("status") == "passed":
            passed.append(page)
            metrics_path = page_paths(run_root, page)["metrics"]
            if metrics_path.is_file():
                value = json.loads(metrics_path.read_text(encoding="utf-8")).get("request_wall_seconds")
                if value is not None:
                    request_seconds.append(float(value))
        else:
            if page not in failed:
                failed.append(page)
            logger.error("PAGE_FAILED page=%d error=%s", page, result.get("error"))
        next_step = (
            f"process page {pages[index]}"
            if index < len(pages)
            else ("resume failed pages" if failed else "run page cleanup")
        )
        write_progress(
            run_root,
            phase="visual-conversion",
            current_page=page,
            total_pages=total_pages,
            passed_count=len(set(passed) | set(skipped)),
            failed_pages=failed,
            next_step=next_step,
            performance_values=request_seconds,
        )
        if index % max(1, progress_every) == 0 or index == len(pages) or failed:
            logger.info(
                "RUN_PROGRESS current=%d processed=%d/%d passed=%d failed=%s",
                page,
                index,
                len(pages),
                len(passed),
                failed,
            )
    status = "failed" if failed else "converted"
    postprocess: dict[str, Any] | None = None
    if not failed:
        try:
            cleanup = run_page_cleanup(run_root=run_root, pages=pages)
            merged = merge_document(run_root=run_root, pages=pages)
            heading_repair = _load_heading_repair_module()

            heading = heading_repair.run_heading_repair(
                run_root=run_root,
                use_model=False,
            )
            validation = validate_run(
                source=source,
                run_root=run_root,
                pages=pages,
                require_final=True,
            )
            postprocess = {
                "cleanup": cleanup,
                "merge": merged,
                "heading_repair": heading,
                "validation": validation,
            }
            status = "passed" if validation.get("status") == "passed" else "failed"
        except Exception as exc:
            postprocess = {"status": "failed", "error": str(exc)}
            status = "failed"
    summary = _summary(
        source=source,
        source_sha256=source_hash,
        run_root=run_root,
        total_pages=total_pages,
        status=status,
    )
    write_json(run_root / "summary.json", summary)
    append_json_line(
        run_root / "test-history.jsonl",
        {
            "started_at": started_at,
            "finished_at": utc_now(),
            "processed_scope": {
                "selector": page_selector,
                "first": pages[0],
                "last": pages[-1],
                "count": len(pages),
            },
            "status": status,
            "passed_count": len(passed),
            "failed_pages": failed,
            "skipped_pages": skipped,
            "postprocess": postprocess,
        },
    )
    logger.info("RUN_FINISHED status=%s passed=%d failed=%s", status, len(passed), failed)
    return {
        "status": status,
        "source_sha256": source_hash,
        "total_pages": total_pages,
        "completed_pages": passed,
        "failed_pages": failed,
        "skipped_pages": skipped,
        "postprocess": postprocess,
        "summary_path": str(run_root / "summary.json"),
    }


def _boundary_nonempty_indices(lines: list[str], *, first: bool, count: int = 4) -> list[int]:
    indices = [
        index
        for index, line in enumerate(lines)
        if line.strip() and not PAGE_MARKER_RE.fullmatch(line.strip())
    ]
    return indices[:count] if first else indices[-count:]


def _repeated_boundary_lines(page_texts: dict[int, str]) -> tuple[set[str], set[str]]:
    header_counts: Counter[str] = Counter()
    footer_counts: Counter[str] = Counter()
    for text in page_texts.values():
        lines = text.splitlines()
        for index in _boundary_nonempty_indices(lines, first=True):
            value = lines[index].strip()
            if len(value) <= 80 and not PROTECTED_BOUNDARY_RE.match(value):
                header_counts[value] += 1
        for index in _boundary_nonempty_indices(lines, first=False):
            value = lines[index].strip()
            if len(value) <= 80 and not PROTECTED_BOUNDARY_RE.match(value):
                footer_counts[value] += 1
    threshold = max(3, math.ceil(len(page_texts) * 0.1))
    return (
        {value for value, count in header_counts.items() if count >= threshold},
        {value for value, count in footer_counts.items() if count >= threshold},
    )


def _clean_page_text(
    text: str,
    page: int,
    repeated_headers: set[str],
    repeated_footers: set[str],
    confirmed_noise: set[str],
    *,
    reader_image_action: str,
    expected_image_reference: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    lines = text.replace("\r\n", "\n").splitlines()
    header_zone = set(_boundary_nonempty_indices(lines, first=True))
    footer_zone = set(_boundary_nonempty_indices(lines, first=False))
    remove: dict[int, str] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped in confirmed_noise:
            remove[index] = "confirmed-noise"
        elif index in header_zone and stripped in repeated_headers:
            remove[index] = "repeated-header"
        elif index in footer_zone and stripped in repeated_footers:
            remove[index] = "repeated-footer"
        elif (index in header_zone or index in footer_zone) and ISOLATED_PAGE_NUMBER_RE.fullmatch(stripped):
            remove[index] = "isolated-page-number"
    remaining_nonempty = [
        index for index, line in enumerate(lines) if index not in remove and line.strip()
    ]
    body_nonempty = [
        index
        for index in remaining_nonempty
        if not PAGE_MARKER_RE.fullmatch(lines[index].strip())
    ]
    if len(body_nonempty) >= 2:
        first_body, last_body = body_nonempty[0], body_nonempty[-1]
        if FENCE_RE.fullmatch(lines[first_body]) and lines[last_body].strip() == "```":
            remove[first_body] = "wrapper-code-fence"
            remove[last_body] = "wrapper-code-fence"
    kept = [line for index, line in enumerate(lines) if index not in remove]
    while kept and not kept[-1].strip():
        kept.pop()
    cleaned = "\n".join(kept) + "\n"
    validation = validate_page_markdown(
        cleaned,
        page,
        reader_image_action=reader_image_action,
        expected_image_reference=expected_image_reference,
    )
    if not validation["passed"]:
        raise RunFailure(f"第 {page} 页整理后校验失败：{validation['errors']}")
    changes = [
        {"line": index + 1, "reason": reason, "sha256": sha256_bytes(lines[index].encode("utf-8"))}
        for index, reason in sorted(remove.items())
    ]
    return cleaned, changes


def run_page_cleanup(
    *,
    run_root: Path,
    pages: list[int] | None = None,
    confirmed_noise: list[str] | None = None,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    append_log_event(run_root, "PHASE_START", phase="page-cleanup")
    selected = pages or discover_page_units(run_root)
    page_texts = {
        page: page_paths(run_root, page)["markdown"].read_text(encoding="utf-8")
        for page in selected
    }
    headers, footers = _repeated_boundary_lines(page_texts)
    confirmed = {value.strip() for value in (confirmed_noise or []) if value.strip()}
    changed_pages: list[int] = []
    removed_lines = 0
    for page in selected:
        policy = page_reader_image_policy(run_root, page)
        cleaned, changes = _clean_page_text(
            page_texts[page],
            page,
            headers,
            footers,
            confirmed,
            reader_image_action=policy["reader_image_action"],
            expected_image_reference=policy["page_unit_reference"],
        )
        if changes:
            path = page_paths(run_root, page)["markdown"]
            atomic_write_text(path, cleaned)
            result_path = page_paths(run_root, page)["result"]
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["page_state"] = "cleaned"
            result.setdefault("markdown", {})["current_sha256"] = sha256_file(path)
            result["cleanup"] = {"removed_line_count": len(changes), "changes": changes}
            write_json(result_path, result)
            changed_pages.append(page)
            removed_lines += len(changes)
    summary_path = run_root / "summary.json"
    total_pages = 0
    if summary_path.is_file():
        total_pages = int(json.loads(summary_path.read_text(encoding="utf-8")).get("total_pages") or 0)
    write_progress(
        run_root,
        phase="page-cleanup",
        current_page=selected[-1],
        total_pages=total_pages or len(selected),
        passed_count=len(selected),
        failed_pages=[],
        next_step="merge document-temp.md",
    )
    append_log_event(
        run_root,
        "PHASE_FINISHED",
        phase="page-cleanup",
        changed_pages=len(changed_pages),
        removed_lines=removed_lines,
    )
    return {
        "status": "passed",
        "pages": selected,
        "changed_pages": changed_pages,
        "removed_line_count": removed_lines,
        "repeated_header_count": len(headers),
        "repeated_footer_count": len(footers),
    }


def repair_page_image_references(
    *, run_root: Path, pages: list[int] | None = None
) -> dict[str, Any]:
    """Retrofit page-image policy, annotating only independently confirmed blank pages."""
    run_root = run_root.expanduser().resolve()
    selected = pages or discover_page_units(run_root)
    if selected != sorted(set(selected)):
        raise RunFailure("图片引用修复页码必须为无重复升序")
    changed_pages: list[int] = []
    evidence_updated_pages: list[int] = []
    for page in selected:
        paths = page_paths(run_root, page)
        if not paths["markdown"].is_file() or not paths["result"].is_file():
            raise RunFailure(f"第 {page} 页缺少页面单元或 page-result 证据")
        before = paths["markdown"].read_text(encoding="utf-8")
        policy = page_reader_image_policy(run_root, page)
        after = project_page_image_reference(
            before,
            page,
            policy["page_unit_reference"],
        )
        confirmed_blank = "confirmed-near-blank-page" in policy["reason_codes"]
        added_blank_annotation = bool(
            confirmed_blank
            and not [line for line in _page_semantic_lines(after, page) if line.strip()]
        )
        if added_blank_annotation:
            after = f"<!-- page:{page} -->\n\n*（空白页）*\n"
        before_body = [
            line
            for line in before.replace("\r\n", "\n").splitlines()
            if line.strip()
            and not _is_current_page_image_target(_markdown_image_target(line), page)
        ]
        after_body = [
            line
            for line in after.replace("\r\n", "\n").splitlines()
            if line.strip()
            and not _is_current_page_image_target(_markdown_image_target(line), page)
        ]
        if before_body != after_body and not added_blank_annotation:
            raise RunFailure(f"第 {page} 页图片引用修复会改变非图片正文")
        validation = validate_page_markdown(
            after,
            page,
            reader_image_action=policy["reader_image_action"],
            expected_image_reference=policy["page_unit_reference"],
        )
        if not validation["passed"]:
            raise RunFailure(f"第 {page} 页图片引用修复后校验失败：{validation['errors']}")
        if after != before:
            atomic_write_text(paths["markdown"], after)
            changed_pages.append(page)

        result = json.loads(paths["result"].read_text(encoding="utf-8"))
        markdown = result.setdefault("markdown", {})
        result["reader_image"] = {
            "action": policy["reader_image_action"],
            "decision_source": policy["decision_source"],
            "reason_codes": policy["reason_codes"],
            "asset_present": policy["asset_present"],
            "semantic_text_coverage": policy["semantic_text_coverage"],
            "layout_dependency": policy["layout_dependency"],
            "page_unit_reference": policy["page_unit_reference"],
            "document_reference": policy["document_reference"],
            "metrics": policy["metrics"],
        }
        current_sha256 = sha256_file(paths["markdown"])
        current_size = paths["markdown"].stat().st_size
        if (
            markdown.get("current_sha256") != current_sha256
            or markdown.get("size_bytes") != current_size
        ):
            markdown["current_sha256"] = current_sha256
            markdown["size_bytes"] = current_size
            write_json(paths["result"], result)
            evidence_updated_pages.append(page)

    merged = merge_document(run_root=run_root, pages=selected)
    return {
        "status": "passed",
        "pages": selected,
        "changed_pages": changed_pages,
        "evidence_updated_pages": evidence_updated_pages,
        "document_temp": {
            "path": merged["path"],
            "sha256": merged["sha256"],
        },
    }


def _merged_page_unit(run_root: Path, page: int) -> str:
    path = page_paths(run_root, page)["markdown"]
    text = path.read_text(encoding="utf-8")
    policy = page_reader_image_policy(run_root, page)
    validation = validate_page_markdown(
        text,
        page,
        reader_image_action=policy["reader_image_action"],
        expected_image_reference=policy["page_unit_reference"],
    )
    if not validation["passed"]:
        raise RunFailure(f"第 {page} 页不能合并：{validation['errors']}")
    merged = project_page_image_reference(text, page, policy["document_reference"])
    merged_validation = validate_page_markdown(
        merged,
        page,
        reader_image_action=policy["reader_image_action"],
        expected_image_reference=policy["document_reference"],
    )
    if not merged_validation["passed"]:
        raise RunFailure(f"第 {page} 页合并路径投影失败：{merged_validation['errors']}")
    return merged.rstrip()


def merge_document(*, run_root: Path, pages: list[int] | None = None) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    append_log_event(run_root, "PHASE_START", phase="merge-document-temp")
    selected = pages or discover_page_units(run_root)
    if selected != sorted(set(selected)):
        raise RunFailure("合并页码必须为无重复升序")
    parts = [_merged_page_unit(run_root, page) for page in selected]
    merged = "\n\n".join(parts) + "\n"
    markers = [int(value) for value in PAGE_MARKER_RE.findall(merged)]
    if markers != selected:
        raise RunFailure(f"合并页标记错误：{markers}")
    if "../assets/page-" in merged:
        raise RunFailure("document-temp.md 仍包含 page-unit 相对图片路径")
    output = run_root / "document-temp.md"
    atomic_write_text(output, merged)
    write_progress(
        run_root,
        phase="merge-document-temp",
        current_page=selected[-1],
        total_pages=len(selected),
        passed_count=len(selected),
        failed_pages=[],
        next_step="audit and repair headings",
    )
    append_log_event(
        run_root,
        "PHASE_FINISHED",
        phase="merge-document-temp",
        pages=len(selected),
        sha256=sha256_file(output),
    )
    return {
        "status": "passed",
        "path": str(output),
        "sha256": sha256_file(output),
        "size_bytes": output.stat().st_size,
        "pages": selected,
    }


def _reader_image_summary(run_root: Path, pages: list[int]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    by_action: dict[str, list[int]] = {action: [] for action in sorted(READER_IMAGE_ACTIONS)}
    for page in pages:
        policy = page_reader_image_policy(run_root, page)
        action = str(policy["reader_image_action"])
        counts[action] += 1
        by_action.setdefault(action, []).append(page)
    return {
        "counts": {
            "omit": counts.get("omit", 0),
            "retain": counts.get("retain", 0),
            "needs-review": counts.get("needs-review", 0),
        },
        "pages": by_action,
    }


def validate_run(
    *,
    source: Path,
    run_root: Path,
    pages: list[int] | None = None,
    require_final: bool = False,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    run_root = run_root.expanduser().resolve()
    append_log_event(run_root, "PHASE_START", phase="validation")
    selected = pages or discover_page_units(run_root)
    source_hash = sha256_file(source)
    errors: list[str] = []
    failed_page_numbers: list[int] = []
    for page in selected:
        try:
            validate_saved_page(source_hash, run_root, page)
        except Exception as exc:
            errors.append(str(exc))
            failed_page_numbers.append(page)
    merged_expected = "\n\n".join(
        _merged_page_unit(run_root, page) for page in selected
    ) + "\n"
    document_temp = run_root / "document-temp.md"
    if not document_temp.is_file() or document_temp.read_text(encoding="utf-8") != merged_expected:
        errors.append("document-temp.md 不是当前页面单元的确定性合并")
    final_path = run_root / "index.md"
    heading_report = run_root / "reports" / "heading-repair.json"
    if require_final:
        if not final_path.is_file() or final_path.stat().st_size == 0:
            errors.append("缺少最终 index.md")
        if not heading_report.is_file():
            errors.append("缺少 heading-repair.json")
        else:
            heading = json.loads(heading_report.read_text(encoding="utf-8"))
            if heading.get("source_sha256") != sha256_file(document_temp):
                errors.append("标题报告与 document-temp.md 哈希不一致")
            if heading.get("status") not in {"passed", "needs-review"}:
                errors.append("标题修复报告未通过")
            if heading.get("status") == "needs-review":
                errors.append("标题仍需复核")
    report = {
        "status": "passed" if not errors else "failed",
        "source": {"path": str(source), "sha256": source_hash},
        "pages": selected,
        "reader_image_summary": _reader_image_summary(run_root, selected),
        "page_fact_sources": [
            "assets/page-NNN.png",
            "page-units/pNNN.md",
            "evidence/page-result-pNNN.json",
            "evidence/reader-image-policy.json",
            "evidence/visual-asset-pruning.json (for hash-locked intentional PNG deletions)",
        ],
        "document_temp_sha256": sha256_file(document_temp) if document_temp.is_file() else None,
        "final_markdown_sha256": (
            sha256_file(final_path) if final_path.is_file() and not errors else None
        ),
        "errors": errors,
        "visual_asset_pruning": (
            {
                "path": "evidence/visual-asset-pruning.json",
                "sha256": sha256_file(run_root / "evidence" / "visual-asset-pruning.json"),
            }
            if (run_root / "evidence" / "visual-asset-pruning.json").is_file()
            else None
        ),
        "validated_at": utc_now(),
    }
    write_json(run_root / "reports" / "validation-result.json", report)
    summary = _summary(
        source=source,
        source_sha256=source_hash,
        run_root=run_root,
        total_pages=get_pdf_page_count(source),
        status=report["status"],
    )
    write_json(run_root / "summary.json", summary)
    write_progress(
        run_root,
        phase="validation",
        current_page=selected[-1],
        total_pages=summary["total_pages"],
        passed_count=max(0, len(selected) - len(failed_page_numbers)),
        failed_pages=failed_page_numbers,
        next_step="reader delivery ready" if not errors else "inspect validation report",
    )
    append_log_event(
        run_root,
        "RUN_FINISHED",
        phase="validation",
        status=report["status"],
        pages=len(selected),
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="逐页视觉转换并写入紧凑证据")
    convert.add_argument("--source", type=Path, required=True)
    convert.add_argument("--run-root", type=Path, required=True)
    convert.add_argument("--pages", default="all")
    convert.add_argument("--evidence-level", choices=EVIDENCE_LEVELS, default="compact")
    convert.add_argument("--resume", action="store_true")
    convert.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    convert.add_argument("--timeout-seconds", type=int, default=900)
    convert.add_argument("--progress-every", type=int, default=10)
    convert.add_argument("--model", default=EXPECTED_MODEL, help="固定模型；其他值会被拒绝")
    convert.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="固定 LAN Ollama 地址")

    clean = subparsers.add_parser("clean", help="删除页边界重复噪声和明确噪声")
    clean.add_argument("--run-root", type=Path, required=True)
    clean.add_argument("--pages", default="all")
    clean.add_argument("--confirmed-noise", action="append", default=[])

    classify = subparsers.add_parser(
        "classify-reader-images",
        help="基于当前 page-unit 结构生成显式 omit/retain/needs-review 页面策略",
    )
    classify.add_argument("--run-root", type=Path, required=True)
    classify.add_argument("--pages", default="all")

    repair_images = subparsers.add_parser(
        "repair-image-links",
        help="按显式 reader image 策略修正页面原图引用并重建 document-temp.md",
    )
    repair_images.add_argument("--run-root", type=Path, required=True)
    repair_images.add_argument("--pages", default="all")

    merge = subparsers.add_parser("merge", help="确定性生成 document-temp.md")
    merge.add_argument("--run-root", type=Path, required=True)
    merge.add_argument("--pages", default="all")

    validate = subparsers.add_parser("validate", help="校验页面事实和最终输出")
    validate.add_argument("--source", type=Path, required=True)
    validate.add_argument("--run-root", type=Path, required=True)
    validate.add_argument("--pages", default="all")
    validate.add_argument("--require-final", action="store_true")
    return parser


def _selected_existing(run_root: Path, selector: str) -> list[int]:
    available = discover_page_units(run_root)
    if selector == "all":
        return available
    selected = parse_page_selector(selector, max(available))
    missing = sorted(set(selected) - set(available))
    if missing:
        raise RunFailure(f"页面单元缺失：{missing}")
    return selected


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "convert":
            if args.model != EXPECTED_MODEL:
                raise RunFailure(f"拒绝其他模型：{args.model}")
            if args.ollama_url.rstrip("/") != DEFAULT_OLLAMA_URL:
                raise RunFailure(f"拒绝其他 Ollama 地址：{args.ollama_url}")
            result = run_visual_conversion(
                source=args.source,
                run_root=args.run_root,
                page_selector=args.pages,
                evidence_level=args.evidence_level,
                resume=args.resume,
                dpi=args.dpi,
                timeout_seconds=args.timeout_seconds,
                progress_every=args.progress_every,
            )
        elif args.command == "clean":
            result = run_page_cleanup(
                run_root=args.run_root,
                pages=_selected_existing(args.run_root, args.pages),
                confirmed_noise=args.confirmed_noise,
            )
        elif args.command == "classify-reader-images":
            result = classify_reader_images(
                run_root=args.run_root,
                pages=_selected_existing(args.run_root, args.pages),
            )
        elif args.command == "repair-image-links":
            result = repair_page_image_references(
                run_root=args.run_root,
                pages=_selected_existing(args.run_root, args.pages),
            )
        elif args.command == "merge":
            result = merge_document(
                run_root=args.run_root,
                pages=_selected_existing(args.run_root, args.pages),
            )
        else:
            result = validate_run(
                source=args.source,
                run_root=args.run_root,
                pages=_selected_existing(args.run_root, args.pages),
                require_final=args.require_final,
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RunFailure) as exc:
        print(f"ERROR handled=true {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"passed", "converted"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("ERROR handled=true 用户中断", file=sys.stderr)
        raise SystemExit(130)
