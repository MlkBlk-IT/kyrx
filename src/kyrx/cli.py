"""Command line interface.

``kyrx version`` prints the package version, ``kyrx info`` the resolved
configuration, ``kyrx stats`` the measured token and time economy, ``kyrx
bench`` the comparative acceptance report, ``kyrx init`` writes the MCP configuration of a
client, and ``kyrx serve`` runs the MCP
server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from kyrx import __version__
from kyrx.config import Settings, Transport, get_settings
from kyrx.economy.ledger import TokenLedger
from kyrx.errors import ConfigError
from kyrx.integrate import (
    CLIENTS,
    DESCRIPTIONS,
    SERVER_NAME,
    backup_file,
    build_entry,
    claude_code_argv,
    config_path,
    merge_server,
    root_key,
)
from kyrx.mcp.server import run_server
from kyrx.security.token import generate_token

__all__ = ["main"]


def _info_payload(settings: Settings) -> dict[str, object]:
    endpoint = (
        f"http://{settings.host}:{settings.port}{settings.streamable_http_path}"
        if settings.transport == "http"
        else "stdio"
    )
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "data_dir": str(settings.data_dir.resolve()),
        "journal": str(settings.journal_path),
        "blobs": str(settings.blob_dir),
        "transport": settings.transport,
        "endpoint": endpoint,
        "auth_mode": settings.auth_mode,
        "rate_limit": f"{settings.rate_limit_calls}/{settings.rate_limit_window_s:g}s",
        "llm": {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "configured": settings.llm_configured,
        },
        "sandbox": {
            "timeout_s": settings.sandbox_timeout_s,
            "memory_bytes": settings.sandbox_memory_bytes,
            "fuel": settings.sandbox_fuel,
        },
        "leases": {
            "offer_ttl_s": settings.lease_offer_ttl_s,
            "run_ttl_s": settings.lease_run_ttl_s,
            "heartbeat_s": settings.lease_heartbeat_s,
            "steal_after_s": settings.lease_steal_after_s,
        },
    }


def _stats_payload(settings: Settings) -> dict[str, object]:
    ledger = TokenLedger.load(settings.economy_path)
    payload = dict(ledger.snapshot().to_dict())
    payload["dataDir"] = str(settings.data_dir.resolve())
    return payload


def _cmd_bench(settings: Settings) -> int:
    """Run the reference bench and print the comparative report."""
    root = Path(__file__).resolve().parents[2]
    if (root / "bench").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from bench.report import build_report  # noqa: PLC0415 - a source checkout extra
    from bench.scenario import run_scenario  # noqa: PLC0415 - a source checkout extra

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kyrx-bench-", dir=settings.data_dir) as directory:
        measurements = asyncio.run(run_scenario(Path(directory)))
    report = build_report(measurements)
    print(report.to_markdown())
    print()
    print(report.to_json())
    return 0 if report.passed else 1


def _client_command(argv: list[str]) -> str:
    """A shell-pastable rendering of an argument vector."""
    return subprocess.list2cmdline(argv) if sys.platform.startswith("win") else shlex.join(argv)


def _init_claude_code(args: argparse.Namespace, argv: list[str]) -> int:
    """Print, then run, the ``claude mcp add`` command that registers KYRX."""
    print(_client_command(argv))
    if args.print_only:
        return 0
    executable = shutil.which("claude")
    if executable is None:
        print("kyrx init: 'claude' not found on PATH; run the command above", file=sys.stderr)
        return 1
    user_config = Path.home() / ".claude.json"
    if user_config.exists():
        print(f"backup: {backup_file(user_config)}")
    return subprocess.run([executable, *argv[1:]], check=False).returncode  # noqa: S603


def _init_file(args: argparse.Namespace, entry: dict[str, object]) -> int:
    """Print, or merge into the client's JSON file, the KYRX entry."""
    path = args.path or config_path(args.client)
    if args.print_only:
        block = {root_key(args.client): {SERVER_NAME: entry}}
        print(json.dumps(block, indent=2, ensure_ascii=False))
        if path is not None:
            print(f"# target: {path}", file=sys.stderr)
        return 0
    if path is None:
        print(
            f"kyrx init: no default location for {args.client} on this platform; pass --path",
            file=sys.stderr,
        )
        return 2
    result = merge_server(path, root=root_key(args.client), entry=entry)
    if not result.changed:
        print(f"unchanged: {result.path}")
        return 0
    print(f"{'created' if result.created else 'updated'}: {result.path}")
    if result.backup is not None:
        print(f"backup: {result.backup}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    """Write (or print) the KYRX block for one MCP client."""
    if args.list_clients:
        for name in CLIENTS:
            print(f"{name:<15} {DESCRIPTIONS[name]}")
        return 0
    if args.client is None:
        print("kyrx init: a client is required (see: kyrx init --list)", file=sys.stderr)
        return 2

    settings = get_settings()
    transport: Transport = args.transport or "stdio"
    host = args.host or settings.host
    port = args.port if args.port is not None else settings.port
    data_dir = (args.data_dir or settings.data_dir).resolve()
    token = args.token
    if token is None and settings.auth_token is not None:
        token = settings.auth_token.get_secret_value()

    if args.client == "claude-code":
        return _init_claude_code(
            args,
            claude_code_argv(
                transport=transport,
                command=args.server_command,
                data_dir=data_dir,
                host=host,
                port=port,
                http_path=settings.streamable_http_path,
                token=token,
            ),
        )
    entry = build_entry(
        args.client,
        transport=transport,
        command=args.server_command,
        data_dir=data_dir,
        host=host,
        port=port,
        http_path=settings.streamable_http_path,
        token=token,
    )
    return _init_file(args, entry)


def _cmd_token(args: argparse.Namespace) -> int:
    """Generate a bearer token and the environment that makes the HTTP server accept it."""
    token = generate_token()
    print("KYRX_AUTH_MODE=token")
    print(f"KYRX_AUTH_TOKEN={token}")
    if args.env:
        return 0
    settings = get_settings()
    print()
    print("# PowerShell:")
    print(f"#   $env:KYRX_AUTH_MODE='token'; $env:KYRX_AUTH_TOKEN='{token}'")
    print("# bash:")
    print(f"#   export KYRX_AUTH_MODE=token KYRX_AUTH_TOKEN={token}")
    print("# then:")
    print(f"#   kyrx serve --transport http --host {settings.host} --port {settings.port}")
    print("# and give the same token to clients: kyrx init <client> --transport http --token ...")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kyrx",
        description="KYRX — content-addressed coordination plane for LLM agent fleets.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the package version")
    sub.add_parser("info", help="print the resolved runtime configuration")
    sub.add_parser("stats", help="print the measured token and time economy")
    sub.add_parser("bench", help="run the reference bench and print the report")
    serve = sub.add_parser("serve", help="run the MCP server")
    serve.add_argument("--transport", choices=("stdio", "http"), default=None)
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    init = sub.add_parser("init", help="write the KYRX block into an MCP client's configuration")
    init.add_argument("client", nargs="?", choices=CLIENTS, default=None)
    init.add_argument("--list", action="store_true", dest="list_clients", help="list the clients")
    init.add_argument(
        "--print", action="store_true", dest="print_only", help="print, write nothing"
    )
    init.add_argument("--transport", choices=("stdio", "http"), default=None)
    init.add_argument("--host", default=None, help="http: host of the KYRX server")
    init.add_argument("--port", type=int, default=None, help="http: port of the KYRX server")
    init.add_argument("--token", default=None, help="http: bearer token (see: kyrx token)")
    init.add_argument("--data-dir", type=Path, default=None, help="stdio: KYRX_DATA_DIR to write")
    init.add_argument("--path", type=Path, default=None, help="override the client's config file")
    init.add_argument(
        "--command",
        default="kyrx",
        dest="server_command",
        help="stdio: executable the client launches",
    )
    token = sub.add_parser("token", help="generate a bearer token for the HTTP transport")
    token.add_argument("--env", action="store_true", help="print only the two KEY=VALUE lines")
    return parser


def _resolve_settings(args: argparse.Namespace) -> Settings:
    """Settings, with the command line overrides applied."""
    settings = get_settings()
    overrides: dict[str, object] = {}
    if getattr(args, "transport", None) is not None:
        overrides["transport"] = args.transport
    if getattr(args, "host", None) is not None:
        overrides["host"] = args.host
    if getattr(args, "port", None) is not None:
        overrides["port"] = args.port
    if overrides:
        settings = settings.model_copy(update=overrides)
    return settings


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    code = 0
    try:
        if args.command == "init":
            return _cmd_init(args)
        if args.command == "token":
            return _cmd_token(args)
        settings = _resolve_settings(args)
        if args.command == "version":
            print(__version__)
        elif args.command == "info":
            print(json.dumps(_info_payload(settings), indent=2, sort_keys=True))
        elif args.command == "stats":
            print(json.dumps(_stats_payload(settings), indent=2, sort_keys=True))
        elif args.command == "bench":
            code = _cmd_bench(settings)
        else:
            try:
                run_server(settings)
            except ConfigError as exc:
                print(str(exc), file=sys.stderr)
                code = 2
            except KeyboardInterrupt:
                code = 130
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        code = 2
    return code
