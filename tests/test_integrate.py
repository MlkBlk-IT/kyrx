"""`kyrx init`: configuration written into MCP clients."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kyrx.cli import main
from kyrx.errors import ConfigError
from kyrx.integrate import (
    CLIENTS,
    build_entry,
    claude_code_argv,
    config_path,
    merge_server,
    root_key,
    write_mcp_config,
)


def test_init_writes_stdio_block(tmp_path: Path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    write_mcp_config(cfg, data_dir=tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["kyrx"]["command"] == "kyrx"
    assert "serve" in data["mcpServers"]["kyrx"]["args"]
    assert data["mcpServers"]["kyrx"]["env"] == {"KYRX_DATA_DIR": str(tmp_path)}


def test_init_preserves_existing_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(
        json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    write_mcp_config(cfg, data_dir=tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert "kyrx" in data["mcpServers"]


def test_init_idempotent(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    write_mcp_config(cfg, data_dir=tmp_path)
    second = write_mcp_config(cfg, data_dir=tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert list(data["mcpServers"]).count("kyrx") == 1
    assert second.changed is False
    assert second.backup is None
    assert list(tmp_path.glob("*.bak")) == []


def test_init_updates_a_changed_entry_and_backs_up_first(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    original = json.dumps({"mcpServers": {"kyrx": {"command": "old"}}})
    cfg.write_text(original, encoding="utf-8")
    result = write_mcp_config(cfg, data_dir=tmp_path, now=datetime(2026, 11, 15, 12, 30, 5))
    assert result.changed is True
    assert result.backup == tmp_path / "c.json.20261115-123005.bak"
    assert result.backup.read_text(encoding="utf-8") == original
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["kyrx"]["command"] == "kyrx"


def test_init_creates_missing_file_and_parents(tmp_path: Path) -> None:
    cfg = tmp_path / "deep" / "er" / "mcp.json"
    result = write_mcp_config(cfg, data_dir=tmp_path)
    assert result.created is True
    assert result.backup is None
    assert cfg.exists()


def test_init_refuses_non_json_and_leaves_it_untouched(tmp_path: Path) -> None:
    cfg = tmp_path / "settings.json"
    text = '{\n  // zed keeps comments here\n  "theme": "One Dark"\n}\n'
    cfg.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        write_mcp_config(cfg, data_dir=tmp_path, client="zed")
    assert cfg.read_text(encoding="utf-8") == text
    assert list(tmp_path.glob("*.bak")) == []


def test_init_refuses_a_non_object_section(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
    with pytest.raises(ConfigError):
        write_mcp_config(cfg, data_dir=tmp_path)


def test_init_tolerates_a_bom_and_writes_none(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode("utf-8"))
    write_mcp_config(cfg, data_dir=tmp_path)
    raw = cfg.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw)["a"] == 1


def test_vscode_uses_servers_key_and_type(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    write_mcp_config(cfg, data_dir=tmp_path, client="vscode")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "mcpServers" not in data
    assert data["servers"]["kyrx"]["type"] == "stdio"


def test_zed_uses_context_servers_key(tmp_path: Path) -> None:
    cfg = tmp_path / "settings.json"
    write_mcp_config(cfg, data_dir=tmp_path, client="zed")
    assert "kyrx" in json.loads(cfg.read_text(encoding="utf-8"))["context_servers"]


def test_custom_command_is_written(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    write_mcp_config(cfg, data_dir=tmp_path, command="C:/venv/Scripts/kyrx.exe")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["kyrx"]["command"] == "C:/venv/Scripts/kyrx.exe"


def test_merge_server_is_generic(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    merge_server(cfg, root="things", entry={"a": 1}, name="n")
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"things": {"n": {"a": 1}}}


@pytest.mark.parametrize(
    ("client", "key"),
    [
        ("claude-desktop", "mcpServers"),
        ("cursor", "mcpServers"),
        ("cline", "mcpServers"),
        ("vscode", "servers"),
        ("zed", "context_servers"),
    ],
)
def test_root_key(client: str, key: str) -> None:
    assert root_key(client) == key


def test_claude_code_has_no_config_file() -> None:
    with pytest.raises(ConfigError):
        root_key("claude-code")
    assert config_path("claude-code") is None


def test_every_client_is_known() -> None:
    assert set(CLIENTS) == {"claude-desktop", "cursor", "vscode", "cline", "zed", "claude-code"}


def test_config_paths_per_platform(tmp_path: Path) -> None:
    win_env = {"APPDATA": str(tmp_path / "Roaming")}
    assert config_path("claude-desktop", platform="win32", env=win_env, home=tmp_path) == (
        tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    )
    assert config_path("vscode", platform="win32", env=win_env, home=tmp_path) == (
        tmp_path / "Roaming" / "Code" / "User" / "mcp.json"
    )
    assert config_path("cursor", platform="win32", env=win_env, home=tmp_path) == (
        tmp_path / ".cursor" / "mcp.json"
    )
    assert config_path("claude-desktop", platform="darwin", env={}, home=tmp_path) == (
        tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    )
    assert config_path("zed", platform="linux", env={}, home=tmp_path) == (
        tmp_path / ".config" / "zed" / "settings.json"
    )
    cline = config_path("cline", platform="linux", env={}, home=tmp_path)
    assert cline is not None
    assert cline.name == "cline_mcp_settings.json"
    assert "saoudrizwan.claude-dev" in cline.parts


def test_zed_on_windows_has_no_guessed_default(tmp_path: Path) -> None:
    assert config_path("zed", platform="win32", env={}, home=tmp_path) is None


def test_claude_code_argv_stdio(tmp_path: Path) -> None:
    argv = claude_code_argv(
        transport="stdio",
        command="kyrx",
        data_dir=tmp_path,
        host="127.0.0.1",
        port=8765,
        http_path="/mcp",
        token=None,
    )
    assert argv[:5] == ["claude", "mcp", "add", "--scope", "user"]
    assert argv.index("kyrx") < argv.index("--")
    assert argv[argv.index("--") + 1 :] == ["kyrx", "serve", "--transport", "stdio"]


def test_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--list"]) == 0
    out = capsys.readouterr().out
    for client in CLIENTS:
        assert client in out


def test_cli_requires_a_client(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init"]) == 2
    assert "client" in capsys.readouterr().err


def test_cli_print_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "mcp.json"
    code = main(
        ["init", "cursor", "--print", "--path", str(target), "--data-dir", str(tmp_path / "var")]
    )
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mcpServers"]["kyrx"]["command"] == "kyrx"
    assert not target.exists()


def test_cli_writes_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "mcp.json"
    code = main(["init", "cursor", "--path", str(target), "--data-dir", str(tmp_path / "var")])
    assert code == 0
    assert "kyrx" in json.loads(target.read_text(encoding="utf-8"))["mcpServers"]
    assert str(target) in capsys.readouterr().out


def test_build_entry_stdio_is_identical_for_json_clients(tmp_path: Path) -> None:
    entries = [
        build_entry(
            client,
            transport="stdio",
            command="kyrx",
            data_dir=tmp_path,
            host="127.0.0.1",
            port=8765,
            http_path="/mcp",
            token=None,
        )
        for client in ("claude-desktop", "cursor", "cline", "zed")
    ]
    assert all(entry == entries[0] for entry in entries)
