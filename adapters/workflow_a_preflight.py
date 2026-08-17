#!/usr/bin/env python3
"""Fail-closed checks that must pass before Workflow A does conversion work.

The preflight is deliberately small and side-effect free.  It does not parse
page content, render source files, call OCR, or contact a model.  Its job is to catch
cheap, deterministic mistakes before those expensive stages start and to
leave a machine-readable decision that can be replayed from the run evidence.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

import yaml

try:
    from PIL import Image
except ImportError:  # pragma: no cover - runtime dependency is checked later
    Image = None


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path("/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies")
WORKFLOW = ROOT / "workflows/document-to-markdown-assets/workflow.yaml"
KNOWN_FAILURES = ROOT / "references/known-failures.zh.md"
PREFLIGHT_CONTRACT = ROOT / "contracts/workflow-a-preflight.yaml"

SUPPORTED_SUFFIXES = {".ppt", ".pptx", ".pdf", ".png", ".jpg", ".jpeg"}
REQUIRED_LOCAL_TOOLS = {
    ".ppt": ("soffice", "pdfinfo", "pdftotext", "pdftoppm"),
    ".pptx": ("soffice", "pdftoppm"),
    ".pdf": ("pdfinfo", "pdftotext", "pdftoppm"),
    ".png": (),
    ".jpg": (),
    ".jpeg": (),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_tool(name: str) -> str | None:
    candidates = [
        RUNTIME_ROOT / "bin" / "override" / name,
        RUNTIME_ROOT / "native" / "poppler" / "poppler" / "bin" / name,
    ]
    if name == "soffice":
        candidates.insert(1, RUNTIME_ROOT / "native" / "libreoffice-headless" / "libreoffice" / "LibreOfficeDev.app" / "Contents" / "MacOS" / "soffice")
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


CHECK_METADATA = {
    "governance-files": (["K-009", "K-013"], "preflight-deterministic", "spec-known", "continue"),
    "source-readable": (["K-013"], "preflight-deterministic", "code-inferred", "continue"),
    "authorization-policy": (["K-005", "K-006"], "preflight-deterministic", "spec-known", "continue"),
    "external-route-policy": (["K-005", "K-006"], "preflight-deterministic", "spec-known", "continue"),
    "source-format-supported": (["K-013"], "preflight-deterministic", "code-inferred", "continue"),
    "page-scope-valid": (["K-010"], "preflight-deterministic", "code-inferred", "continue"),
    "smoke-scope-valid": (["K-010", "K-012"], "preflight-deterministic", "spec-known", "continue"),
    "source-container-integrity": (["K-003", "K-013"], "preflight-deterministic", "code-inferred", "continue"),
    "required-render-tools": (["K-012"], "preflight-deterministic", "code-inferred", "continue"),
    "output-root": (["K-008", "K-010"], "preflight-deterministic", "code-inferred", "continue"),
    "output-collision": (["K-008", "K-010"], "preflight-deterministic", "code-inferred", "continue"),
    "candidate-comparison": (["K-007"], "preflight-deterministic", "spec-known", "continue"),
    "candidate-comparison-readable": (["K-007"], "preflight-deterministic", "code-inferred", "continue"),
    "candidate-source-selection": (["K-007"], "preflight-deterministic", "code-inferred", "continue"),
    "candidate-retention": (["K-007"], "preflight-deterministic", "code-inferred", "continue"),
    "candidate-readable": (["K-007"], "preflight-deterministic", "code-inferred", "continue"),
    "candidate-page-scope": (["K-007", "K-010"], "preflight-deterministic", "code-inferred", "continue"),
    "candidate-fingerprint": (["K-007", "K-009"], "preflight-deterministic", "code-inferred", "continue"),
    "source-page-inventory": (["K-010", "K-013"], "stage-required", "unknown", "continue"),
}


def _check(check_id: str, stage_id: str, status: str, message: str, remediation: str, *, severity: str = "P1", blocking: bool | None = None, evidence: list[str] | None = None, risk_ids: list[str] | None = None, detectability: str | None = None, evidence_class: str | None = None, next_action: str | None = None) -> dict:
    if blocking is None:
        blocking = status == "failed"
    default_risks, default_detectability, default_evidence_class, default_next_action = CHECK_METADATA.get(
        check_id, ([], "stage-required", "code-inferred", "continue")
    )
    return {
        "check_id": check_id,
        "stage_id": stage_id,
        "status": status,
        "severity": severity,
        "blocking": blocking,
        "message": message,
        "remediation": remediation,
        "evidence_paths": evidence or [],
        "risk_ids": risk_ids if risk_ids is not None else default_risks,
        "detectability": detectability or default_detectability,
        "evidence_class": evidence_class or default_evidence_class,
        "next_action": next_action or default_next_action,
    }


def _config_secret_keys(value: object, prefix: str = "") -> list[str]:
    """Find secret-bearing configuration keys without printing their values."""
    # ``authorization`` is intentionally a policy enum in workflow.yaml;
    # only credential-bearing keys are forbidden in the configuration itself.
    forbidden = {"api_key", "cookie", "secret", "password", "token", "subscription_url"}
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in forbidden:
                found.append(label)
            found.extend(_config_secret_keys(nested, label))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_config_secret_keys(nested, f"{prefix}[{index}]"))
    return found


def _parse_selector(
    selector: str,
    total_pages: int | None,
    available_pages: list[int] | None = None,
) -> tuple[list[int], list[str]]:
    """Parse selectors against the source's actual page identifiers.

    A standalone PPTX can retain physical slide IDs such as ``slide24.xml``
    and ``slide25.xml``.  A count of two is not an equivalent page universe,
    so explicit source page IDs take precedence over ``total_pages``.
    """
    value = str(selector or "").strip()
    if not value:
        return [], ["pages selector is empty"]
    universe = sorted({int(page) for page in available_pages or []})
    if value.lower() == "all":
        if universe:
            return universe, []
        return (list(range(1, total_pages + 1)), []) if total_pages else ([], ["pages=all requires a known page count"])
    pages: set[int] = set()
    errors: list[str] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            if "-" in token:
                start, end = (int(part.strip()) for part in token.split("-", 1))
                if start > end:
                    raise ValueError("range start is greater than end")
                pages.update(range(start, end + 1))
            else:
                pages.add(int(token))
        except ValueError as exc:
            errors.append(f"invalid page token {token!r}: {exc}")
    if not pages:
        errors.append("pages selector contains no page")
    if any(page < 1 for page in pages):
        errors.append("page numbers must be positive")
    if universe:
        missing = sorted(set(pages) - set(universe))
        if missing:
            errors.append(f"selected pages are absent from source page set: {missing}")
    elif total_pages is not None and any(page > total_pages for page in pages):
        errors.append(f"selected pages exceed source page count {total_pages}")
    return sorted(pages), errors


def _validate_candidate(path: Path, source: Path, selected_pages: list[int]) -> list[dict]:
    checks: list[dict] = []
    try:
        comparison = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_check("candidate-comparison-readable", "A1", "failed", f"候选比较文件不可读：{exc}", "重新生成 candidate comparison；不得直接猜测主来源。", evidence=[str(path)])]
    if not isinstance(comparison, dict):
        return [_check("candidate-comparison-readable", "A1", "failed", "候选比较文件必须是 JSON object。", "重新生成结构化 candidate comparison；不得用数组或纯文本替代。", evidence=[str(path)])]
    preferred_value = Path(str(comparison.get("preferred_source", ""))).expanduser()
    preferred = (path.parent / preferred_value).resolve() if not preferred_value.is_absolute() else preferred_value.resolve()
    if preferred != source:
        checks.append(_check("candidate-source-selection", "A1", "failed", "preferred_source 与当前 source 不一致。", "修正候选选择后重新运行。", evidence=[str(path)]))
    candidates = comparison.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        checks.append(_check("candidate-retention", "A1", "failed", "候选比较没有保留恰好两个可追溯候选。", "保留两份候选及各自指纹，再重新比较。", evidence=[str(path)]))
    else:
        for item in candidates:
            if not isinstance(item, dict):
                checks.append(_check("candidate-readable", "A1", "failed", "候选比较包含非对象候选记录。", "重新生成包含 path、sha256 和页码的候选记录。", evidence=[str(path)]))
                continue
            candidate_value = Path(str(item.get("path", ""))).expanduser()
            candidate = (path.parent / candidate_value).resolve() if not candidate_value.is_absolute() else candidate_value.resolve()
            if not candidate.is_file():
                checks.append(_check("candidate-readable", "A1", "failed", f"候选来源不可读：{candidate}", "修复候选路径或明确声明单一 source。", evidence=[str(path)]))
                continue
            recorded_sha = str(item.get("sha256", ""))
            if not re.fullmatch(r"[0-9a-fA-F]{64}", recorded_sha):
                checks.append(_check("candidate-fingerprint", "A1", "failed", f"候选缺少有效 SHA-256：{candidate}", "重新生成候选比较并保留每份候选的内容指纹。", evidence=[str(path)]))
            else:
                try:
                    actual_sha = sha256(candidate)
                except OSError as exc:
                    checks.append(_check("candidate-fingerprint", "A1", "failed", f"候选指纹无法读取：{candidate} ({exc})", "修复候选文件可读性后重新比较。", evidence=[str(path)]))
                else:
                    if actual_sha.lower() != recorded_sha.lower():
                        checks.append(_check("candidate-fingerprint", "A1", "failed", f"候选 SHA-256 与比较报告不一致：{candidate}", "候选文件发生替换或报告过期；重新生成 candidate comparison。", evidence=[str(path)]))
    recorded_pages = comparison.get("selected_pages") or []
    if not recorded_pages:
        checks.append(_check("candidate-page-scope", "A1", "failed", "候选比较缺少 selected_pages。", "使用同一页码范围重新生成 candidate comparison。", evidence=[str(path)]))
    else:
        try:
            candidate_pages = sorted(int(page) for page in recorded_pages)
        except (TypeError, ValueError):
            candidate_pages = []
            checks.append(_check("candidate-page-scope", "A1", "failed", "候选比较的 selected_pages 不是整数页码。", "重新生成结构化 candidate comparison。", evidence=[str(path)]))
        if candidate_pages and candidate_pages != selected_pages:
            checks.append(_check("candidate-page-scope", "A1", "failed", "候选比较的页码范围与本次执行不一致。", "使用同一页码范围重新生成候选比较。", evidence=[str(path)]))
    return checks


def _infer_total_pages(source: Path, suffix: str) -> int | None:
    """Use only cheap container metadata when the caller has not supplied it."""
    try:
        if suffix == ".pptx":
            with ZipFile(source) as deck:
                root = ET.fromstring(deck.read("ppt/presentation.xml"))
                nodes = root.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldIdLst")
                return len(nodes) if nodes is not None else None
        if suffix == ".pdf":
            tool = runtime_tool("pdfinfo")
            if not tool:
                return None
            result = subprocess.run([tool, str(source)], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                match = re.search(r"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
                return int(match.group(1)) if match else None
        if suffix in {".png", ".jpg", ".jpeg"}:
            return 1
    except (OSError, BadZipFile, ET.ParseError, subprocess.SubprocessError, ValueError):
        return None
    return None


def _infer_available_pages(source: Path, suffix: str, total_pages: int | None) -> list[int] | None:
    """Read source page IDs without rendering or extracting page content."""
    try:
        if suffix == ".pptx":
            with ZipFile(source) as deck:
                return sorted(
                    int(match.group(1))
                    for name in deck.namelist()
                    if (match := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name))
                )
        if suffix in {".pdf", ".ppt"} and total_pages:
            return list(range(1, total_pages + 1))
        if suffix in {".png", ".jpg", ".jpeg"}:
            return [1]
    except (OSError, BadZipFile, ValueError):
        return None
    return None


def run_preflight(
    source: Path,
    output_root: Path,
    pages_selector: str,
    *,
    total_pages: int | None = None,
    available_pages: list[int] | None = None,
    package_name: str | None = None,
    candidate_comparison: Path | None = None,
    smoke_selector: str | None = None,
) -> dict:
    """Return a preflight decision without running conversion stages."""
    source = source.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    suffix = source.suffix.lower()
    if total_pages is None and source.is_file() and suffix in SUPPORTED_SUFFIXES:
        total_pages = _infer_total_pages(source, suffix)
    if available_pages is None and source.is_file():
        available_pages = _infer_available_pages(source, suffix, total_pages)
    if total_pages is None and available_pages:
        total_pages = len(available_pages)
    try:
        source_digest = sha256(source) if source.is_file() and os.access(source, os.R_OK) else None
    except OSError:
        source_digest = None
    selected_pages, page_errors = _parse_selector(pages_selector, total_pages, available_pages)
    package = package_name or (
        f"{source.stem}-{source_digest[:8] if source_digest else 'unreadable'}-{suffix.lstrip('.') or 'file'}"
        if source.is_file()
        else "unreadable-source"
    )
    checks: list[dict] = []

    if not WORKFLOW.is_file() or not PREFLIGHT_CONTRACT.is_file() or not KNOWN_FAILURES.is_file():
        checks.append(_check("governance-files", "cross-cutting", "failed", "Workflow、preflight contract 或已知失败目录缺失。", "先恢复规格包完整性，再启动转换。", severity="P0", evidence=[str(WORKFLOW), str(PREFLIGHT_CONTRACT), str(KNOWN_FAILURES)]))
    else:
        checks.append(_check("governance-files", "cross-cutting", "passed", "Workflow、preflight contract 和已知失败目录均存在。", "", blocking=False, evidence=[str(WORKFLOW), str(PREFLIGHT_CONTRACT), str(KNOWN_FAILURES)]))

    try:
        workflow_policy = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
        execution_policy = workflow_policy.get("execution_policy") or {}
        online_policy = execution_policy.get("online_visual_model")
        if isinstance(online_policy, dict):
            current_policy = (online_policy.get("current_conversation") or {}).get("policy")
            external_policy = (online_policy.get("external_provider") or {}).get("policy")
        else:
            current_policy = None
            external_policy = online_policy
        if current_policy != "fallback-after-mac96-only" or external_policy != "explicit-authorization-only":
            checks.append(_check("authorization-policy", "cross-cutting", "failed", "当前对话 fallback 或外部在线 provider 的授权策略不完整。", "保留 mac96 优先、当前对话仅在 mac96 失败后使用，并保持外部 provider 显式授权。", severity="P0", evidence=[str(WORKFLOW)]))
        else:
            checks.append(_check("authorization-policy", "cross-cutting", "passed", "当前对话在线视觉模型仅作为 mac96 失败后的单图 fallback，外部 provider 保持显式授权。", "", blocking=False, evidence=[str(WORKFLOW)]))
        route_errors: list[str] = []
        capability_probe = execution_policy.get("capability_probe") or {}
        if capability_probe.get("mode") != "read-only":
            route_errors.append("capability_probe.mode must be read-only")
        for route in execution_policy.get("ordered_fallback") or []:
            if not isinstance(route, dict):
                route_errors.append("ordered_fallback contains a non-object route")
                continue
            route_id = str(route.get("id", "unknown"))
            authorization = str(route.get("authorization", ""))
            if route_id in {"conversation-online-vision-text-transcription", "conversation-online-vision-page-analysis"}:
                if authorization != "current-task-explicit" or route.get("transport") != "conversation" or route.get("target") != "current-conversation":
                    route_errors.append("current-conversation vision fallback must declare current-task-explicit authorization and conversation transport")
            elif "online" in route_id and authorization != "explicit-only":
                route_errors.append(f"{route_id} must declare authorization=explicit-only")
            if route_id in {"mac96-ssh-visual-review", "mac96-vision-text-transcription", "mac96-vision-page-analysis"} and (
                route.get("transport") != "ssh" or not route.get("target")
            ):
                route_errors.append("mac96 SSH route must declare transport and target")
            if route_id == "apple-vision-ocr" and not route.get("evidence"):
                route_errors.append("Apple Vision OCR fallback must declare an evidence path")
        secret_keys = _config_secret_keys(workflow_policy)
        if secret_keys:
            route_errors.append("workflow configuration contains forbidden secret-bearing keys: " + ", ".join(secret_keys))
        if route_errors:
            checks.append(_check("external-route-policy", "cross-cutting", "failed", "外部路由策略不完整：" + "；".join(route_errors), "补齐只读 capability probe、明确授权/fallback 和证据路径；不要把秘密写进 Workflow 配置。", severity="P0", evidence=[str(WORKFLOW)]))
        else:
            checks.append(_check("external-route-policy", "cross-cutting", "passed", "SSH/OCR/在线模型路由均有显式边界；本次 preflight 不发起外部调用。", "", blocking=False, evidence=[str(WORKFLOW)]))
    except (OSError, TypeError, yaml.YAMLError) as exc:
        checks.append(_check("authorization-policy", "cross-cutting", "failed", f"无法读取在线模型授权策略：{exc}", "修复 Workflow 策略文件后重试。", severity="P0", evidence=[str(WORKFLOW)]))
        checks.append(_check("external-route-policy", "cross-cutting", "failed", f"无法读取外部路由策略：{exc}", "修复 Workflow 路由策略文件后重试。", severity="P0", evidence=[str(WORKFLOW)]))

    if not source.is_file():
        checks.append(_check("source-readable", "A1", "failed", f"source 不存在或不是普通文件：{source}", "修正 source 路径后重新运行。", severity="P0"))
    elif not os.access(source, os.R_OK) or source.stat().st_size == 0:
        checks.append(_check("source-readable", "A1", "failed", "source 不可读或大小为 0。", "确认文件权限和复制完整性后重试。", severity="P0"))
    else:
        checks.append(_check("source-readable", "A1", "passed", "source 存在、可读且非空。", "", blocking=False))

    if suffix not in SUPPORTED_SUFFIXES:
        checks.append(_check("source-format-supported", "A1", "failed", f"当前生产 runner 不支持来源格式 {suffix or '<none>'}。", "使用 PPT、PPTX、PDF 或图片；DOCX 等未实现格式不能进入 runner。", severity="P0"))
    else:
        checks.append(_check("source-format-supported", "A1", "passed", f"来源格式 {suffix} 在当前 runner 支持范围内。", "", blocking=False))

    if page_errors:
        checks.append(_check("page-scope-valid", "A1", "failed", "页码选择无效：" + "；".join(page_errors), "修正 --pages/--smoke-pages，使其为当前 source 的有效页码。", severity="P0"))
    else:
        checks.append(_check("page-scope-valid", "A1", "passed", f"已锁定页码 {selected_pages}。", "", blocking=False))
    if suffix == ".ppt" and available_pages is None and total_pages is None and not page_errors:
        checks.append(_check(
            "source-page-inventory",
            "A1",
            "needs-review",
            "二进制 PPT 的页数不能在不运行 Office 转换的前提下确定；当前只接受显式页码声明。",
            "通过本次 preflight 后由 A1 Office 读取页数，并对显式页码做范围复核；不得把 pages=all 当作已验证。",
            severity="P1",
            blocking=False,
            evidence_class="unknown",
        ))
    if smoke_selector:
        smoke_pages, smoke_errors = _parse_selector(smoke_selector, total_pages, available_pages)
        if smoke_errors or not set(smoke_pages).issubset(set(selected_pages)):
            detail = smoke_errors or ["smoke pages 不是 selected pages 的子集"]
            checks.append(_check("smoke-scope-valid", "cross-cutting", "failed", "smoke 页码范围无效：" + "；".join(detail), "先修正 smoke 范围，不得用 smoke 以外页面替代冒烟证据。", severity="P0"))
        else:
            checks.append(_check("smoke-scope-valid", "cross-cutting", "passed", f"smoke 页码 {smoke_pages} 是 selected pages 的子集。", "", blocking=False))

    if source.is_file():
        try:
            if suffix == ".pptx":
                with ZipFile(source) as deck:
                    bad = deck.testzip()
                    if bad:
                        raise BadZipFile(f"corrupt member {bad}")
                    if "ppt/presentation.xml" not in deck.namelist():
                        raise BadZipFile("ppt/presentation.xml missing")
            elif suffix == ".pdf":
                tool = runtime_tool("pdfinfo")
                if not tool:
                    raise OSError("pdfinfo is unavailable")
                result = subprocess.run([tool, str(source)], capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    raise OSError(result.stderr.strip() or "pdfinfo could not read the PDF")
            elif suffix in {".png", ".jpg", ".jpeg"}:
                if Image is None:
                    raise OSError("Pillow is unavailable")
                with Image.open(source) as image:
                    image.verify()
            checks.append(_check("source-container-integrity", "A1", "passed", "来源容器完整性预检通过。", "", blocking=False))
        except (OSError, BadZipFile, SyntaxError, subprocess.SubprocessError, ValueError) as exc:
            checks.append(_check("source-container-integrity", "A1", "failed", f"来源容器损坏或结构不完整：{exc}", "重新导出或复制 source；不要继续使用生成的空包。", severity="P0"))

    missing_tools = [name for name in REQUIRED_LOCAL_TOOLS.get(suffix, ()) if runtime_tool(name) is None]
    if missing_tools:
        checks.append(_check("required-render-tools", "A3", "failed", f"执行前缺少必需渲染器：{', '.join(missing_tools)}。", "安装/修复项目声明的运行时工具后再运行；不要让 A2/A4 先消耗时间再失败。", severity="P1"))
    else:
        checks.append(_check("required-render-tools", "A3", "passed", "当前来源所需的页面渲染工具可执行。", "", blocking=False))

    if output_root.exists() and not output_root.is_dir():
        checks.append(_check("output-root", "cross-cutting", "failed", f"output_root 不是目录：{output_root}", "改用可写目录。", severity="P0"))
    elif output_root.exists() and not os.access(output_root, os.W_OK):
        checks.append(_check("output-root", "cross-cutting", "failed", f"output_root 不可写：{output_root}", "修正目录权限或改用其他输出目录。", severity="P0"))
    elif not output_root.exists() and (not output_root.parent.is_dir() or not os.access(output_root.parent, os.W_OK)):
        checks.append(_check("output-root", "cross-cutting", "failed", f"output_root 尚不存在且父目录不可写：{output_root.parent}", "先创建可写输出根目录，或改用其他输出目录。", severity="P0"))
    else:
        checks.append(_check("output-root", "cross-cutting", "passed", "输出根目录可用。", "", blocking=False))
    target = output_root / package
    if target.exists():
        checks.append(_check("output-collision", "cross-cutting", f"failed", f"本次 package 目录已存在：{target}", "使用新的 attempt/package-name；不得覆盖旧 evidence。", severity="P0"))
    else:
        checks.append(_check("output-collision", "cross-cutting", "passed", "目标 package 尚未存在，不会覆盖既有 evidence。", "", blocking=False))

    if candidate_comparison:
        checks.extend(_validate_candidate(candidate_comparison.expanduser().resolve(), source, selected_pages))

    failures = [item for item in checks if item["blocking"]]
    warnings = [item for item in checks if item["status"] not in {"passed"} and not item["blocking"]]
    status = "failed" if failures else "needs-review" if warnings else "passed"
    try:
        fingerprint = sha256(source) if source.is_file() and os.access(source, os.R_OK) else None
    except OSError:
        fingerprint = None
    return {
        "contract": "workflow-a-preflight",
        "preflight_version": "0.1.0",
        "checked_at": now(),
        "status": status,
        "can_start": not failures,
        "source": {"path": str(source), "media_type": mimetypes.guess_type(source.name)[0] if source.is_file() else None, "suffix": suffix, "sha256": fingerprint, "size_bytes": source.stat().st_size if source.is_file() else None},
        "output": {"root": str(output_root), "package": package, "target": str(target)},
        "selected_pages": selected_pages,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "next_action": "修复 failures 后使用新的 package-name 重试；不启动后续阶段。" if failures else "可以启动 A1；后续阶段仍须遵守各自门禁和独立验收。",
        "audit_metadata": {
            "risk_catalog": "references/known-failures.zh.md",
            "human_reference": "references/execution-preflight.zh.md",
            "evidence_boundary": "preflight-only",
            "execution_boundary": "before-A1",
        },
        "policy": {
            "one_source_per_run": True,
            "online_visual_model_default": "external-disabled; current-conversation-fallback-only",
            "known_failures_reference": "references/known-failures.zh.md",
            "not_detectable_before_run": [
                "semantic-title-quality",
                "visual-relation-fidelity",
                "ocr-accuracy",
                "language-quality",
                "human-review-completeness",
            ],
        },
    }
