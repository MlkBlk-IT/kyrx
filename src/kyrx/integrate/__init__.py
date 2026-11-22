"""Integration: write KYRX's MCP configuration into the clients that will use it."""

from __future__ import annotations

from kyrx.integrate.targets import (
    CLIENTS,
    DESCRIPTIONS,
    SERVER_NAME,
    build_entry,
    claude_code_argv,
    config_path,
    root_key,
)
from kyrx.integrate.writer import WriteResult, backup_file, merge_server, write_mcp_config

__all__ = [
    "CLIENTS",
    "DESCRIPTIONS",
    "SERVER_NAME",
    "WriteResult",
    "backup_file",
    "build_entry",
    "claude_code_argv",
    "config_path",
    "merge_server",
    "root_key",
    "write_mcp_config",
]
