"""Write the KYRX entry into a client's JSON configuration without harming the rest.

Four guarantees: a timestamped backup is taken before any change; every other key
and server entry of the file is preserved; the file is replaced atomically; and a
second run is an update, never a duplicate (and, when nothing changes, not even a
new backup).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kyrx.errors import ConfigError
from kyrx.integrate.targets import SERVER_NAME, Mode, build_entry, root_key

__all__ = ["WriteResult", "backup_file", "merge_server", "write_mcp_config"]


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What a write did."""

    path: Path
    backup: Path | None
    changed: bool
    created: bool


def backup_file(path: Path, *, now: datetime | None = None) -> Path:
    """Copy ``path`` to ``<name>.<YYYYmmdd-HHMMSS>.bak`` next to it and return the copy."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, target)
    return target


def _load(path: Path) -> dict[str, object]:
    """The existing JSON object, ``{}`` for a missing or empty file."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} is not plain JSON ({exc.msg}, line {exc.lineno}); left untouched. "
            "Run again with --print and paste the block by hand."
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not hold a JSON object; left untouched")
    return data


def _atomic_write(path: Path, text: str) -> None:
    """UTF-8 (no BOM) write through a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_server(
    path: Path,
    *,
    root: str,
    entry: dict[str, object],
    name: str = SERVER_NAME,
    now: datetime | None = None,
) -> WriteResult:
    """Set ``<root>.<name>`` to ``entry`` in the JSON file at ``path``."""
    config = _load(path)
    section = config.get(root)
    if section is None:
        servers: dict[str, object] = {}
    elif isinstance(section, dict):
        servers = section
    else:
        raise ConfigError(f"{path}: '{root}' is not an object; left untouched")

    existed = path.exists()
    if servers.get(name) == entry:
        return WriteResult(path=path, backup=None, changed=False, created=False)

    backup = backup_file(path, now=now) if existed else None
    servers[name] = entry
    config[root] = servers
    _atomic_write(path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return WriteResult(path=path, backup=backup, changed=True, created=not existed)


def write_mcp_config(
    path: Path,
    *,
    data_dir: Path,
    client: str = "claude-desktop",
    transport: Mode = "stdio",
    command: str = "kyrx",
    host: str = "127.0.0.1",
    port: int = 8765,
    http_path: str = "/mcp",
    token: str | None = None,
    now: datetime | None = None,
) -> WriteResult:
    """Build the entry for ``client`` and merge it into the file at ``path``."""
    entry = build_entry(
        client,
        transport=transport,
        command=command,
        data_dir=data_dir,
        host=host,
        port=port,
        http_path=http_path,
        token=token,
    )
    return merge_server(path, root=root_key(client), entry=entry, now=now)
