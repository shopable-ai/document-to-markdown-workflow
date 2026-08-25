#!/usr/bin/env python3
"""Route post-merge Markdown formatting through configured language models."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "markdown-post-merge-models.toml"
DEFAULT_DOTENV_PATH = ROOT / ".env"
PAGE_MARKER_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
PROTECTED_TOKEN_RE = re.compile(r"\[\[WF_(?:PAGE_MARKER|IMAGE_REFERENCE)_\d{4}\]\]")
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
OUTER_FENCE_RE = re.compile(
    r"\A\s*```(?:markdown|md)?\s*\n(?P<body>.*)\n```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")
SSH_HOST_RE = re.compile(r"[A-Za-z0-9_.@-]+")


class ModelRouteError(RuntimeError):
    """A handled provider, configuration, or model-output failure."""


@dataclass(frozen=True)
class ModelRoute:
    route_id: str
    transport: str
    model: str | None = None
    profile: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: int | None = None
    proxy_policy: str = "environment"
    thinking: bool | None = None
    max_output_tokens: int | None = None
    ssh_host: str | None = None
    ssh_endpoint: str | None = None


@dataclass(frozen=True)
class ModelRoutingConfig:
    version: int
    selection: str
    routes: tuple[ModelRoute, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class ModelRunResult:
    status: str
    markdown: str | None
    route_id: str | None
    transport: str | None
    model: str | None
    profile: str | None
    attempts: tuple[dict[str, object], ...]


def _parse_dotenv_value(raw: str) -> str:
    try:
        parts = shlex.split(raw, comments=True, posix=True)
    except ValueError as exc:
        raise ModelRouteError(f"invalid .env value: {exc}") from exc
    return " ".join(parts)


def load_dotenv(path: Path = DEFAULT_DOTENV_PATH) -> list[str]:
    """Load missing variables from a small, shell-free .env subset."""

    if not path.is_file():
        return []
    loaded: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ModelRouteError(f"{path}:{line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise ModelRouteError(f"{path}:{line_number}: invalid environment key")
        if key in os.environ:
            continue
        os.environ[key] = _parse_dotenv_value(raw_value.strip())
        loaded.append(key)
    return loaded


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def redact_diagnostic(value: object) -> str:
    text = BEARER_RE.sub(r"\1[REDACTED]", str(value))
    for key, secret in os.environ.items():
        if not secret or len(secret) < 6:
            continue
        upper_key = key.upper()
        if any(marker in upper_key for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(secret, "[REDACTED]")
    return text


def protect_markdown_structures(markdown: str) -> tuple[str, dict[str, str]]:
    matches = [
        (match.start(), match.end(), "PAGE_MARKER", match.group(0))
        for match in PAGE_MARKER_RE.finditer(markdown)
    ]
    matches.extend(
        (match.start(), match.end(), "IMAGE_REFERENCE", match.group(0))
        for match in IMAGE_RE.finditer(markdown)
    )
    matches.sort(key=lambda item: item[0])
    output: list[str] = []
    mapping: dict[str, str] = {}
    cursor = 0
    counts = {"PAGE_MARKER": 0, "IMAGE_REFERENCE": 0}
    for start, end, kind, original in matches:
        if start < cursor:
            continue
        counts[kind] += 1
        token = f"[[WF_{kind}_{counts[kind]:04d}]]"
        output.append(markdown[cursor:start])
        output.append(token)
        mapping[token] = original
        cursor = end
    output.append(markdown[cursor:])
    return "".join(output), mapping


def restore_markdown_structures(markdown: str, mapping: dict[str, str]) -> str:
    restored = markdown
    for token, original in mapping.items():
        restored = restored.replace(token, original)
    return restored


def _env_or_value(route: dict[str, object], name: str) -> str | None:
    env_name = _optional_text(route.get(f"{name}_env"))
    if env_name and env_name in os.environ:
        return _optional_text(os.environ[env_name])
    return _optional_text(route.get(name))


def _optional_bool(value: object, *, field: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ModelRouteError(f"{field} must be true or false")


def load_routing_config(path: Path = DEFAULT_CONFIG_PATH) -> ModelRoutingConfig:
    if not path.is_file():
        raise ModelRouteError(f"missing model routing config: {path}")
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    version = int(raw.get("version", 0))
    if version != 1:
        raise ModelRouteError(f"unsupported model routing config version: {version}")
    route_definitions = raw.get("routes")
    if not isinstance(route_definitions, dict):
        raise ModelRouteError("model routing config requires [routes.*] tables")

    explicit_route = os.environ.get("MARKDOWN_FORMATTER_ROUTE", "").strip()
    if explicit_route and explicit_route != "auto":
        route_ids = [explicit_route]
        selection = "explicit"
    else:
        configured_order = raw.get("default_route_order", [])
        if not isinstance(configured_order, list) or not configured_order:
            raise ModelRouteError("default_route_order must be a non-empty array")
        route_ids = [str(item) for item in configured_order]
        selection = "auto"

    timeout_seconds = int(os.environ.get("MARKDOWN_FORMATTER_TIMEOUT_SECONDS", raw.get("timeout_seconds", 300)))
    if timeout_seconds <= 0:
        raise ModelRouteError("MARKDOWN_FORMATTER_TIMEOUT_SECONDS must be positive")

    routes: list[ModelRoute] = []
    for route_id in route_ids:
        definition = route_definitions.get(route_id)
        if not isinstance(definition, dict):
            raise ModelRouteError(f"unknown model route: {route_id}")
        transport = _optional_text(definition.get("transport"))
        if not transport:
            raise ModelRouteError(f"route {route_id} has no transport")
        route_timeout = definition.get("timeout_seconds")
        thinking_value = _env_or_value(definition, "thinking")
        max_output_value = _env_or_value(definition, "max_output_tokens")
        routes.append(
            ModelRoute(
                route_id=route_id,
                transport=transport,
                model=_env_or_value(definition, "model"),
                profile=_env_or_value(definition, "profile"),
                base_url=_env_or_value(definition, "base_url"),
                api_key_env=_optional_text(definition.get("api_key_env")),
                timeout_seconds=int(route_timeout) if route_timeout is not None else None,
                proxy_policy=_optional_text(definition.get("proxy_policy"))
                or "environment",
                thinking=_optional_bool(
                    thinking_value,
                    field=f"route {route_id} thinking",
                ),
                max_output_tokens=int(max_output_value)
                if max_output_value is not None
                else None,
                ssh_host=_env_or_value(definition, "ssh_host"),
                ssh_endpoint=_env_or_value(definition, "ssh_endpoint"),
            )
        )
        if routes[-1].proxy_policy not in {"direct", "environment"}:
            raise ModelRouteError(
                f"route {route_id} has unsupported proxy_policy: "
                f"{routes[-1].proxy_policy}"
            )
        if routes[-1].max_output_tokens is not None and routes[-1].max_output_tokens <= 0:
            raise ModelRouteError(
                f"route {route_id} max_output_tokens must be positive"
            )
    return ModelRoutingConfig(
        version=version,
        selection=selection,
        routes=tuple(routes),
        timeout_seconds=timeout_seconds,
    )


def build_codex_command(
    *, route: ModelRoute, output_path: Path, working_directory: Path
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-C",
        str(working_directory),
        "--output-last-message",
        str(output_path),
    ]
    if route.profile:
        command.extend(["--profile", route.profile])
    if route.model:
        command.extend(["--model", route.model])
    command.append("-")
    return command


def call_codex_cli(
    route: ModelRoute,
    prompt: str,
    timeout_seconds: int,
    *,
    working_directory: Path | None = None,
) -> str:
    with tempfile.TemporaryDirectory(prefix="markdown-model-") as temporary:
        temp_root = Path(temporary)
        output_path = temp_root / "last-message.md"
        cwd = (working_directory or temp_root).resolve()
        command = build_codex_command(
            route=route,
            output_path=output_path,
            working_directory=cwd,
        )
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelRouteError(f"Codex CLI unavailable: {exc}") from exc
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout or "unknown error").strip()
            raise ModelRouteError(
                f"Codex CLI exited {completed.returncode}: {redact_diagnostic(diagnostic[-500:])}"
            )
        if not output_path.is_file():
            raise ModelRouteError("Codex CLI did not write --output-last-message")
        return output_path.read_text(encoding="utf-8")


def _endpoint(base_url: str, suffix: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith(suffix):
        return normalized
    return f"{normalized}/{suffix.lstrip('/')}"


def _ollama_payload(route: ModelRoute, prompt: str) -> dict[str, object]:
    if not route.model:
        raise ModelRouteError(f"route {route.route_id} has no model")
    options: dict[str, object] = {"temperature": 0}
    if route.max_output_tokens is not None:
        options["num_predict"] = route.max_output_tokens
    payload: dict[str, object] = {
        "model": route.model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    if route.thinking is not None:
        payload["think"] = route.thinking
    return payload


def _ollama_response_text(decoded: dict[str, object]) -> str:
    content = decoded.get("response")
    if not isinstance(content, str):
        raise ModelRouteError("Ollama response has no text")
    return content


def _post_json(
    *,
    url: str,
    payload: dict[str, object],
    api_key: str | None,
    timeout_seconds: int,
    proxy_policy: str,
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        if proxy_policy == "direct":
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            response_context = opener.open(request, timeout=timeout_seconds)
        else:
            response_context = urllib.request.urlopen(request, timeout=timeout_seconds)
        with response_context as response:
            body = response.read().decode("utf-8")
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise ModelRouteError(f"HTTP provider unavailable: {exc}") from exc
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelRouteError("HTTP provider returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ModelRouteError("HTTP provider returned a non-object response")
    return decoded


def _require_http_route(route: ModelRoute) -> tuple[str, str, str | None]:
    if not route.base_url:
        raise ModelRouteError(f"route {route.route_id} has no base_url")
    if not route.model:
        raise ModelRouteError(f"route {route.route_id} has no model")
    api_key = os.environ.get(route.api_key_env, "") if route.api_key_env else None
    return route.base_url, route.model, api_key or None


def call_openai_chat(route: ModelRoute, prompt: str, timeout_seconds: int) -> str:
    base_url, model, api_key = _require_http_route(route)
    payload: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "stream": False,
    }
    if route.max_output_tokens is not None:
        payload["max_tokens"] = route.max_output_tokens
    decoded = _post_json(
        url=_endpoint(base_url, "chat/completions"),
        payload=payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        proxy_policy=route.proxy_policy,
    )
    try:
        content = decoded["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRouteError("chat-completions response has no message content") from exc
    if not isinstance(content, str):
        raise ModelRouteError("chat-completions message content is not text")
    return content


def call_openai_responses(route: ModelRoute, prompt: str, timeout_seconds: int) -> str:
    base_url, model, api_key = _require_http_route(route)
    payload: dict[str, object] = {"model": model, "input": prompt, "temperature": 0}
    if route.max_output_tokens is not None:
        payload["max_output_tokens"] = route.max_output_tokens
    decoded = _post_json(
        url=_endpoint(base_url, "responses"),
        payload=payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        proxy_policy=route.proxy_policy,
    )
    output_text = decoded.get("output_text")
    if isinstance(output_text, str):
        return output_text
    pieces: list[str] = []
    raw_output = decoded.get("output", [])
    if not isinstance(raw_output, list):
        raw_output = []
    for item in raw_output:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                pieces.append(content["text"])
    if not pieces:
        raise ModelRouteError("responses response has no output text")
    return "".join(pieces)


def call_ollama_generate(route: ModelRoute, prompt: str, timeout_seconds: int) -> str:
    base_url, model, api_key = _require_http_route(route)
    payload = _ollama_payload(route, prompt)
    decoded = _post_json(
        url=_endpoint(base_url, "api/generate"),
        payload=payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        proxy_policy=route.proxy_policy,
    )
    return _ollama_response_text(decoded)


def build_ssh_ollama_command(route: ModelRoute, timeout_seconds: int) -> list[str]:
    host = route.ssh_host or ""
    endpoint = route.ssh_endpoint or ""
    if not SSH_HOST_RE.fullmatch(host):
        raise ModelRouteError(f"route {route.route_id} has invalid ssh_host")
    parsed = urllib.parse.urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.port
        or parsed.path.rstrip("/") != "/api/generate"
    ):
        raise ModelRouteError(
            f"route {route.route_id} ssh_endpoint must be loopback /api/generate"
        )
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        host,
        "/usr/bin/curl",
        "-sS",
        "--max-time",
        str(timeout_seconds),
        endpoint,
        "-H",
        "Content-Type:application/json",
        "--data-binary",
        "@-",
    ]


def call_ollama_generate_ssh(
    route: ModelRoute, prompt: str, timeout_seconds: int
) -> str:
    command = build_ssh_ollama_command(route, timeout_seconds)
    payload = _ollama_payload(route, prompt)
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=timeout_seconds + 20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelRouteError(f"SSH Ollama unavailable: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise ModelRouteError(
            f"SSH Ollama exited {completed.returncode}: "
            f"{redact_diagnostic(stderr[-500:])}"
        )
    try:
        decoded = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelRouteError("SSH Ollama returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ModelRouteError("SSH Ollama returned a non-object response")
    return _ollama_response_text(decoded)


def call_model_route(route: ModelRoute, prompt: str, timeout_seconds: int) -> str:
    if route.transport == "codex-cli":
        return call_codex_cli(route, prompt, timeout_seconds)
    if route.transport == "openai-chat-completions":
        return call_openai_chat(route, prompt, timeout_seconds)
    if route.transport == "openai-responses":
        return call_openai_responses(route, prompt, timeout_seconds)
    if route.transport == "ollama-generate":
        return call_ollama_generate(route, prompt, timeout_seconds)
    if route.transport == "ollama-generate-ssh":
        return call_ollama_generate_ssh(route, prompt, timeout_seconds)
    if route.transport == "current-conversation":
        raise ModelRouteError(
            "current-conversation cannot be called by a child script; use codex-default "
            "or import an explicitly authorized current-conversation result"
        )
    raise ModelRouteError(f"unsupported model transport: {route.transport}")


def _normalize_model_output(text: str) -> str:
    match = OUTER_FENCE_RE.fullmatch(text)
    normalized = match.group("body") if match else text
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def validate_model_markdown(before: str, after: str) -> list[str]:
    errors: list[str] = []
    if not after.strip():
        return ["模型返回空 Markdown"]
    before_markers = PAGE_MARKER_RE.findall(before)
    after_markers = PAGE_MARKER_RE.findall(after)
    if before_markers != after_markers:
        errors.append("页面标记顺序或覆盖改变")
    before_images = [match.group(1).strip() for match in IMAGE_RE.finditer(before)]
    after_images = [match.group(1).strip() for match in IMAGE_RE.finditer(after)]
    if before_images != after_images:
        errors.append("图片引用顺序或路径改变")
    before_tokens = PROTECTED_TOKEN_RE.findall(before)
    after_tokens = PROTECTED_TOKEN_RE.findall(after)
    if before_tokens != after_tokens:
        errors.append("受保护占位符顺序或覆盖改变")
    before_size = max(1, len(before.strip()))
    ratio = len(after.strip()) / before_size
    if ratio < 0.5 or ratio > 2.0:
        errors.append(f"输出长度比例异常: {ratio:.2f}")
    if after.count("```") % 2:
        errors.append("Markdown 代码围栏不成对")
    return errors


def run_model_routes(
    *,
    markdown: str,
    prompt: str,
    config: ModelRoutingConfig,
    call_route: Callable[[ModelRoute, str, int], str] = call_model_route,
) -> ModelRunResult:
    attempts: list[dict[str, object]] = []
    for route in config.routes:
        started = time.monotonic()
        timeout_seconds = route.timeout_seconds or config.timeout_seconds
        try:
            candidate = _normalize_model_output(call_route(route, prompt, timeout_seconds))
            errors = validate_model_markdown(markdown, candidate)
            if errors:
                attempts.append(
                    {
                        "route": route.route_id,
                        "transport": route.transport,
                        "model": route.model,
                        "profile": route.profile,
                        "proxy_policy": route.proxy_policy,
                        "thinking": route.thinking,
                        "max_output_tokens": route.max_output_tokens,
                        "ssh_host": route.ssh_host,
                        "status": "rejected",
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "error": "；".join(errors),
                    }
                )
                continue
            attempts.append(
                {
                    "route": route.route_id,
                    "transport": route.transport,
                    "model": route.model,
                    "profile": route.profile,
                    "proxy_policy": route.proxy_policy,
                    "thinking": route.thinking,
                    "max_output_tokens": route.max_output_tokens,
                    "ssh_host": route.ssh_host,
                    "status": "passed",
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
            return ModelRunResult(
                status="passed",
                markdown=candidate,
                route_id=route.route_id,
                transport=route.transport,
                model=route.model,
                profile=route.profile,
                attempts=tuple(attempts),
            )
        except (ModelRouteError, OSError, ValueError, TypeError) as exc:
            attempts.append(
                {
                    "route": route.route_id,
                    "transport": route.transport,
                    "model": route.model,
                    "profile": route.profile,
                    "proxy_policy": route.proxy_policy,
                    "thinking": route.thinking,
                    "max_output_tokens": route.max_output_tokens,
                    "ssh_host": route.ssh_host,
                    "status": "failed",
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "error": redact_diagnostic(exc),
                }
            )
            if config.selection == "explicit":
                break
    return ModelRunResult(
        status="failed",
        markdown=None,
        route_id=None,
        transport=None,
        model=None,
        profile=None,
        attempts=tuple(attempts),
    )
