"""`kyrx token`, and the HTTP form of `kyrx init`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from kyrx.cli import main
from kyrx.config import Settings
from kyrx.errors import ConfigError
from kyrx.integrate import build_entry, claude_code_argv, write_mcp_config
from kyrx.mcp.server import build_server
from kyrx.security.token import generate_token


def test_token_length() -> None:
    assert len(generate_token()) >= 40


def test_tokens_unique() -> None:
    assert generate_token() != generate_token()


def test_token_is_url_safe() -> None:
    token = generate_token()
    assert token.replace("-", "").replace("_", "").isalnum()


def test_cli_token_prints_a_usable_pair(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "KYRX_AUTH_MODE=token"
    assert lines[1].startswith("KYRX_AUTH_TOKEN=")
    assert len(lines[1].removeprefix("KYRX_AUTH_TOKEN=")) >= 40
    assert "kyrx serve --transport http" in out


def test_cli_token_env_prints_only_the_pair(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token", "--env"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert not any(line.startswith("#") for line in lines)


def test_a_generated_token_is_accepted_by_the_server(data_dir: Path) -> None:
    token = generate_token()
    settings = Settings(
        data_dir=data_dir, transport="http", auth_mode="token", auth_token=SecretStr(token)
    )
    build_server(settings)


def _http(client: str, token: str | None, tmp_path: Path) -> dict[str, object]:
    return build_entry(
        client,
        transport="http",
        command="kyrx",
        data_dir=tmp_path,
        host="10.0.0.5",
        port=8765,
        http_path="/mcp",
        token=token,
    )


def test_http_entry_has_url_and_bearer(tmp_path: Path) -> None:
    entry = _http("cursor", "tok", tmp_path)
    assert entry == {
        "url": "http://10.0.0.5:8765/mcp",
        "headers": {"Authorization": "Bearer tok"},
    }


def test_http_entry_per_client_type_field(tmp_path: Path) -> None:
    assert _http("vscode", "t", tmp_path)["type"] == "http"
    assert _http("cline", "t", tmp_path)["type"] == "streamableHttp"
    assert "type" not in _http("zed", "t", tmp_path)


def test_http_requires_a_token(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _http("cursor", None, tmp_path)


def test_claude_desktop_refuses_http(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _http("claude-desktop", "t", tmp_path)


def test_http_block_is_written_where_stdio_would_be(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    write_mcp_config(
        cfg,
        data_dir=tmp_path,
        client="vscode",
        transport="http",
        host="10.0.0.5",
        token="tok",  # noqa: S106 - a test value
    )
    entry = json.loads(cfg.read_text(encoding="utf-8"))["servers"]["kyrx"]
    assert entry["url"] == "http://10.0.0.5:8765/mcp"
    assert entry["headers"]["Authorization"] == "Bearer tok"


def test_claude_code_http_argv(tmp_path: Path) -> None:
    argv = claude_code_argv(
        transport="http",
        command="kyrx",
        data_dir=tmp_path,
        host="10.0.0.5",
        port=8765,
        http_path="/mcp",
        token="tok",  # noqa: S106 - a test value
    )
    assert argv[-2:] == ["kyrx", "http://10.0.0.5:8765/mcp"]
    assert "Authorization: Bearer tok" in argv
    assert "--" not in argv


def test_cli_init_http_without_token_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KYRX_AUTH_TOKEN", raising=False)
    code = main(
        ["init", "cursor", "--transport", "http", "--print", "--data-dir", str(tmp_path / "var")]
    )
    assert code == 2
    assert "kyrx token" in capsys.readouterr().err


def test_cli_init_http_print(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "init",
            "cursor",
            "--transport",
            "http",
            "--host",
            "10.0.0.5",
            "--port",
            "8765",
            "--token",
            "tok",
            "--print",
        ]
    )
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mcpServers"]["kyrx"]["url"] == "http://10.0.0.5:8765/mcp"
