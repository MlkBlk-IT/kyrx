"""MCP server wiring.

Transport selection and authentication live here. The ten coordination tools
are registered from :mod:`kyrx.mcp.tools` and bound to the engine built by
:mod:`kyrx.engine`.

Security defaults: a server exposed over HTTP refuses to start without a
bearer token. Failing closed is the only safe default for a product that may
hold a fleet's state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from kyrx import __version__
from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.errors import ConfigError
from kyrx.mcp.tools import register_tools
from kyrx.security.auth import BearerTokenVerifier

__all__ = ["build_server", "run_server"]

INSTRUCTIONS = (
    "KYRX coordinates a fleet of sub-agents. Exchange handles, not prose. "
    "Declare the steps of a plan with intent, inspect it with manifest, take a "
    "node with claim, and publish results with publish. State is read with read "
    "or project. Never paste full contexts into another agent: refer to handles."
)

SERVER_DESCRIPTION = "Content-addressed coordination plane for LLM agent fleets."


def build_server(settings: Settings, engine: Engine | None = None) -> MCPServer[object]:
    """Build the MCP server bound to the resolved settings."""
    resolved = engine if engine is not None else Engine.from_settings(settings)
    token_verifier, auth = _token_verifier(settings)

    @asynccontextmanager
    async def lifespan(_server: MCPServer[object]) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.blob_dir.mkdir(parents=True, exist_ok=True)
        yield

    server: MCPServer[object] = MCPServer(
        name="kyrx",
        title="KYRX",
        description=SERVER_DESCRIPTION,
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
        token_verifier=token_verifier,
        auth=auth,
    )
    register_tools(server, resolved)
    return server


def _token_verifier(settings: Settings) -> tuple[BearerTokenVerifier | None, AuthSettings | None]:
    """Build the bearer verifier, or refuse to expose the server."""
    if settings.auth_mode == "token":
        if settings.auth_token is None:
            raise ConfigError("KYRX_AUTH_MODE=token requires KYRX_AUTH_TOKEN")
        base = f"http://{settings.host}:{settings.port}"
        resource_url = f"{base}{settings.streamable_http_path}"
        verifier = BearerTokenVerifier(
            settings.auth_token.get_secret_value(),
            required_scopes=settings.auth_required_scopes,
            resource=resource_url,
        )
        auth = AuthSettings(
            issuer_url=base,
            resource_server_url=resource_url,
            required_scopes=list(settings.auth_required_scopes) or None,
            validate_token_resource=True,
        )
        return verifier, auth
    if settings.transport == "http" and settings.require_auth_for_http:
        raise ConfigError(
            "the HTTP transport requires KYRX_AUTH_MODE=token; refusing to serve unauthenticated"
        )
    return None, None


def run_server(settings: Settings) -> None:
    """Run the MCP server on the configured transport."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.blob_dir.mkdir(parents=True, exist_ok=True)
    server = build_server(settings)
    if settings.transport == "stdio":
        server.run(transport="stdio")
        return
    server.run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.streamable_http_path,
    )
