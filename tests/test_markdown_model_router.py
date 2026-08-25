from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from adapters import markdown_model_router as router


def write_config(path: Path) -> None:
    path.write_text(
        """
version = 1
default_route_order = ["lan", "codex-default"]

[routes.lan]
transport = "openai-chat-completions"
base_url = "http://lan.invalid/v1"
model = "lan-model"

[routes.codex-default]
transport = "codex-cli"
model = ""

[routes.codex-profile]
transport = "codex-cli"
profile = "relay"
model = "profile-model"

[routes.current-conversation]
transport = "current-conversation"
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_load_dotenv_does_not_override_exported_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'MARKDOWN_FORMATTER_ROUTE="lan"\nMARKDOWN_LAN_API_KEY=secret-value # comment\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MARKDOWN_FORMATTER_ROUTE", "codex-default")
    monkeypatch.delenv("MARKDOWN_LAN_API_KEY", raising=False)

    loaded = router.load_dotenv(dotenv)

    assert loaded == ["MARKDOWN_LAN_API_KEY"]
    assert router.os.environ["MARKDOWN_FORMATTER_ROUTE"] == "codex-default"
    assert router.os.environ["MARKDOWN_LAN_API_KEY"] == "secret-value"


def test_explicit_route_disables_automatic_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "routes.toml"
    write_config(config_path)
    monkeypatch.setenv("MARKDOWN_FORMATTER_ROUTE", "codex-profile")

    config = router.load_routing_config(config_path)

    assert [route.route_id for route in config.routes] == ["codex-profile"]
    assert config.selection == "explicit"


def test_codex_profile_command_uses_profile_and_never_provider(tmp_path: Path):
    route = router.ModelRoute(
        route_id="codex-profile",
        transport="codex-cli",
        model="profile-model",
        profile="relay",
    )

    command = router.build_codex_command(
        route=route,
        output_path=tmp_path / "output.md",
        working_directory=tmp_path,
    )

    assert command[:2] == ["codex", "exec"]
    assert command[command.index("--profile") + 1] == "relay"
    assert command[command.index("--model") + 1] == "profile-model"
    assert "--provider" not in command
    assert "--ask-for-approval" not in command
    assert command[-1] == "-"


def test_auto_route_falls_back_and_records_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "routes.toml"
    write_config(config_path)
    monkeypatch.delenv("MARKDOWN_FORMATTER_ROUTE", raising=False)
    config = router.load_routing_config(config_path)
    calls: list[str] = []

    def fake_call(route: router.ModelRoute, prompt: str, timeout_seconds: int) -> str:
        calls.append(route.route_id)
        if route.route_id == "lan":
            raise router.ModelRouteError("LAN unavailable")
        return "<!-- page:1 -->\n\n正文\n"

    result = router.run_model_routes(
        markdown="<!-- page:1 -->\n\n正文\n",
        prompt="整理文档",
        config=config,
        call_route=fake_call,
    )

    assert calls == ["lan", "codex-default"]
    assert result.status == "passed"
    assert result.route_id == "codex-default"
    assert [attempt["status"] for attempt in result.attempts] == ["failed", "passed"]


def test_structural_guard_rejects_changed_image_reference(tmp_path: Path):
    config_path = tmp_path / "routes.toml"
    write_config(config_path)
    config = router.load_routing_config(config_path)
    original = "<!-- page:1 -->\n\n正文\n\n![图](assets/page-001.png)\n"

    def fake_call(route: router.ModelRoute, prompt: str, timeout_seconds: int) -> str:
        return "<!-- page:1 -->\n\n正文\n\n![图](assets/changed.png)\n"

    result = router.run_model_routes(
        markdown=original,
        prompt="整理文档",
        config=config,
        call_route=fake_call,
    )

    assert result.status == "failed"
    assert all(attempt["status"] == "rejected" for attempt in result.attempts)
    assert "图片引用" in result.attempts[0]["error"]


def test_codex_call_reads_output_last_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    route = router.ModelRoute(
        route_id="codex-default",
        transport="codex-cli",
        model=None,
        profile=None,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("整理后的 Markdown\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(router.subprocess, "run", fake_run)

    output = router.call_codex_cli(route, "prompt", 30, working_directory=tmp_path)

    assert output == "整理后的 Markdown\n"


@pytest.mark.parametrize(
    ("transport", "response_body", "expected"),
    [
        (
            "openai-chat-completions",
            {"choices": [{"message": {"content": "chat result"}}]},
            "chat result",
        ),
        ("openai-responses", {"output_text": "responses result"}, "responses result"),
        ("ollama-generate", {"response": "ollama result"}, "ollama result"),
    ],
)
def test_http_transports_parse_text(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    response_body: dict[str, object],
    expected: str,
):
    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(response_body).encode("utf-8")

    monkeypatch.setattr(router.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    route = router.ModelRoute(
        route_id="http",
        transport=transport,
        base_url="http://provider.invalid/v1",
        model="model-a",
    )

    result = router.call_model_route(route, "prompt", 30)

    assert result == expected


def test_diagnostics_redact_secret_environment_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARKDOWN_TEST_API_KEY", "super-secret-value")

    diagnostic = router.redact_diagnostic(
        "failed with super-secret-value and Authorization: Bearer another-token"
    )

    assert "super-secret-value" not in diagnostic
    assert "another-token" not in diagnostic
    assert diagnostic.count("[REDACTED]") == 2


def test_protected_markdown_structures_round_trip_and_are_guarded():
    original = (
        "<!-- page:1 -->\n\n正文\n\n![图](assets/page-001.png)\n"
    )

    protected, mapping = router.protect_markdown_structures(original)

    assert "<!-- page:1 -->" not in protected
    assert "assets/page-001.png" not in protected
    assert router.validate_model_markdown(protected, protected) == []
    assert "受保护占位符" in router.validate_model_markdown(
        protected,
        protected.replace("[[WF_PAGE_MARKER_0001]]", ""),
    )[0]
    assert router.restore_markdown_structures(protected, mapping) == original


def test_repository_route_catalog_matches_default_contract(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MARKDOWN_FORMATTER_ROUTE", raising=False)

    config = router.load_routing_config(router.DEFAULT_CONFIG_PATH)

    assert [route.route_id for route in config.routes] == [
        "lan-ollama",
        "lan-ollama-ssh",
        "codex-default",
    ]
    assert [route.transport for route in config.routes] == [
        "ollama-generate",
        "ollama-generate-ssh",
        "codex-cli",
    ]
    assert [route.proxy_policy for route in config.routes] == [
        "direct",
        "environment",
        "environment",
    ]
    assert config.routes[0].thinking is False
    assert config.routes[0].max_output_tokens == 16384
    assert config.routes[1].ssh_host == "mac96"
    assert config.routes[1].ssh_endpoint == "http://127.0.0.1:11434/api/generate"


def test_direct_proxy_policy_uses_proxy_free_opener(monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    payloads: list[dict[str, object]] = []

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"response": "LAN result"}).encode("utf-8")

    class FakeOpener:
        def open(self, request: object, timeout: int) -> FakeResponse:
            opened.append("direct")
            payloads.append(json.loads(request.data.decode("utf-8")))  # type: ignore[attr-defined]
            return FakeResponse()

    monkeypatch.setattr(
        router.urllib.request,
        "build_opener",
        lambda *handlers: FakeOpener(),
    )
    monkeypatch.setattr(
        router.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("direct route used environment proxy opener"),
    )
    route = router.ModelRoute(
        route_id="lan",
        transport="ollama-generate",
        base_url="http://shopme.local:11434",
        model="model-a",
        proxy_policy="direct",
        thinking=False,
        max_output_tokens=2048,
    )

    assert router.call_model_route(route, "prompt", 30) == "LAN result"
    assert opened == ["direct"]
    assert payloads[0]["think"] is False
    assert payloads[0]["options"] == {"temperature": 0, "num_predict": 2048}


def test_ssh_ollama_uses_stdin_and_loopback_endpoint(monkeypatch: pytest.MonkeyPatch):
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["payload"] = json.loads(kwargs["input"].decode("utf-8"))  # type: ignore[union-attr]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"response": "SSH result"}).encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(router.subprocess, "run", fake_run)
    route = router.ModelRoute(
        route_id="lan-ssh",
        transport="ollama-generate-ssh",
        model="model-a",
        thinking=False,
        max_output_tokens=2048,
        ssh_host="mac96",
        ssh_endpoint="http://127.0.0.1:11434/api/generate",
    )

    assert router.call_model_route(route, "secret prompt body", 30) == "SSH result"
    command = observed["command"]
    assert command[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert "mac96" in command
    assert "http://127.0.0.1:11434/api/generate" in command
    assert "secret prompt body" not in command
    assert observed["payload"]["prompt"] == "secret prompt body"  # type: ignore[index]


def test_repository_auto_route_uses_ssh_before_codex(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("MARKDOWN_FORMATTER_ROUTE", raising=False)
    config = router.load_routing_config(router.DEFAULT_CONFIG_PATH)
    calls: list[str] = []

    def fake_call(route: router.ModelRoute, prompt: str, timeout_seconds: int) -> str:
        calls.append(route.route_id)
        if route.route_id == "lan-ollama":
            raise router.ModelRouteError("direct unavailable")
        return "正文\n"

    result = router.run_model_routes(
        markdown="正文\n",
        prompt="整理文档",
        config=config,
        call_route=fake_call,
    )

    assert result.status == "passed"
    assert result.route_id == "lan-ollama-ssh"
    assert calls == ["lan-ollama", "lan-ollama-ssh"]
