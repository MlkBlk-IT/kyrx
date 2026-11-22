"""Where each MCP client keeps its configuration, and the block it expects.

The MCP protocol is the same everywhere; the configuration files are not. The
top-level key, the shape of a local entry and the way a remote entry names its
transport all differ per client, so each difference is spelled out here once.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from kyrx.errors import ConfigError

__all__ = [
    "CLIENTS",
    "DESCRIPTIONS",
    "SERVER_NAME",
    "build_entry",
    "claude_code_argv",
    "config_path",
    "root_key",
]

Client = Literal["claude-desktop", "cursor", "vscode", "cline", "zed", "claude-code"]
Mode = Literal["stdio", "http"]

SERVER_NAME = "kyrx"

CLIENTS: tuple[Client, ...] = (
    "claude-desktop",
    "cursor",
    "vscode",
    "cline",
    "zed",
    "claude-code",
)

DESCRIPTIONS: dict[str, str] = {
    "claude-desktop": "claude_desktop_config.json (mcpServers, local stdio only)",
    "cursor": "~/.cursor/mcp.json (mcpServers)",
    "vscode": "user-profile mcp.json (servers)",
    "cline": "Cline VS Code extension, cline_mcp_settings.json (mcpServers)",
    "zed": "settings.json (context_servers, plain JSON only)",
    "claude-code": "runs `claude mcp add` (user scope)",
}

_ROOT_KEYS: dict[str, str] = {
    "claude-desktop": "mcpServers",
    "cursor": "mcpServers",
    "cline": "mcpServers",
    "vscode": "servers",
    "zed": "context_servers",
}


def root_key(client: str) -> str:
    """Top-level JSON key that holds the client's server entries."""
    try:
        return _ROOT_KEYS[client]
    except KeyError:
        raise ConfigError(f"{client} has no JSON configuration file to write") from None


def _app_data(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """Per-user application data directory of the platform."""
    if platform.startswith("win"):
        appdata = env.get("APPDATA")
        return Path(appdata) if appdata else home / "AppData" / "Roaming"
    if platform == "darwin":
        return home / "Library" / "Application Support"
    xdg = env.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else home / ".config"


def config_path(
    client: str,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """Default configuration file of a client, or ``None`` when there is no safe default.

    ``None`` means either the client has no file (``claude-code`` is driven through
    its own CLI) or the location is not known for this platform; the caller then
    asks for ``--path`` instead of guessing.
    """
    plat = platform if platform is not None else sys.platform
    environment = os.environ if env is None else env
    base_home = home if home is not None else Path.home()
    data = _app_data(plat, environment, base_home)
    if client == "claude-desktop":
        return data / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return base_home / ".cursor" / "mcp.json"
    if client == "vscode":
        return data / "Code" / "User" / "mcp.json"
    if client == "cline":
        return (
            data
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "settings"
            / "cline_mcp_settings.json"
        )
    if client == "zed" and not plat.startswith("win"):
        return base_home / ".config" / "zed" / "settings.json"
    return None


def build_entry(
    client: str,
    *,
    transport: Mode,
    command: str,
    data_dir: Path,
    host: str,
    port: int,
    http_path: str,
    token: str | None,
) -> dict[str, object]:
    """The ``kyrx`` server entry in the shape ``client`` expects."""
    if transport == "stdio":
        entry: dict[str, object] = {
            "command": command,
            "args": ["serve", "--transport", "stdio"],
            "env": {"KYRX_DATA_DIR": str(data_dir)},
        }
        return {"type": "stdio", **entry} if client == "vscode" else entry

    if client == "claude-desktop":
        raise ConfigError(
            "claude_desktop_config.json declares local (stdio) servers only; "
            "use --transport stdio for claude-desktop"
        )
    if not token:
        raise ConfigError("an HTTP entry needs a bearer token; generate one with: kyrx token")
    remote: dict[str, object] = {
        "url": f"http://{host}:{port}{http_path}",
        "headers": {"Authorization": f"Bearer {token}"},
    }
    if client == "vscode":
        return {"type": "http", **remote}
    if client == "cline":
        return {"type": "streamableHttp", **remote}
    return remote


def claude_code_argv(
    *,
    transport: Mode,
    command: str,
    data_dir: Path,
    host: str,
    port: int,
    http_path: str,
    token: str | None,
) -> list[str]:
    """The ``claude mcp add`` command line that registers KYRX at user scope."""
    if transport == "stdio":
        return [
            "claude",
            "mcp",
            "add",
            "--scope",
            "user",
            "--env",
            f"KYRX_DATA_DIR={data_dir}",
            SERVER_NAME,
            "--",
            command,
            "serve",
            "--transport",
            "stdio",
        ]
    if not token:
        raise ConfigError("an HTTP entry needs a bearer token; generate one with: kyrx token")
    return [
        "claude",
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "http",
        "--header",
        f"Authorization: Bearer {token}",
        SERVER_NAME,
        f"http://{host}:{port}{http_path}",
    ]
