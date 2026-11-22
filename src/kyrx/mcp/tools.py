"""The ten MCP tools, bound to the engine.

Parameter names follow the wire contract exactly (camelCase); the Python
attributes of the schema models stay snake_case through aliases. Every call
goes through the engine guard: rate limit, audit trail and economy ledger.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from pydantic import Field

from kyrx.engine import Engine
from kyrx.errors import KyrxError
from kyrx.mcp.schemas import (
    ClaimOutput,
    IntentOutput,
    ManifestOutput,
    NativeStep,
    ProveOutput,
    PublishOutput,
)

__all__ = ["register_tools"]


@asynccontextmanager
async def _expose() -> AsyncIterator[None]:
    """Show callers the typed error (``[code] message``).

    The SDK hides every exception except its own ``ToolError`` behind a generic
    message, so a typed KYRX failure would reach the agent as an opaque string.
    """
    try:
        yield
    except KyrxError as exc:
        raise McpToolError(str(exc)) from exc


def register_tools(server: MCPServer[object], engine: Engine) -> None:
    """Register the ten coordination tools and the economy resources."""

    @server.tool(
        name="intent",
        description="Materialise a plan. The only model-facing entry point.",
    )
    async def intent(
        intent: str,
        budgetTokens: Annotated[int, Field(ge=1, le=10_000_000)],
        steps: list[NativeStep] | None = None,
    ) -> IntentOutput:
        async with _expose(), engine.guard("intent", "server"):
            dag_id = await engine.intent(intent, budgetTokens, steps)
            return IntentOutput(dag_id=dag_id)

    @server.tool(
        name="manifest",
        description="Compact manifest of a branch: node ids, kinds, statuses, scores.",
    )
    async def manifest(dagId: str, branch: str = "main") -> ManifestOutput:
        async with _expose(), engine.guard("manifest", dagId):
            return await engine.manifest(dagId, branch)

    @server.tool(
        name="claim",
        description="Take exclusive lease on a node, or receive the in-flight result handle.",
    )
    async def claim(nodeId: str, agentId: str) -> ClaimOutput:
        async with _expose(), engine.guard("claim", agentId):
            return await engine.claim(nodeId, agentId)

    @server.tool(name="beat", description="Extend a running lease before its TTL expires.")
    async def beat(leaseId: str) -> None:
        async with _expose(), engine.guard("beat", leaseId):
            await engine.beat(leaseId)

    @server.tool(
        name="publish",
        description="Write a node result to the blackboard as a new version.",
    )
    async def publish(nodeId: str, artifactRef: str, summary: str) -> PublishOutput:
        async with _expose(), engine.guard("publish", nodeId):
            return await engine.publish(nodeId, artifactRef, summary)

    @server.tool(
        name="read",
        description="Read an artifact or entry by handle with tier and field selection.",
    )
    async def read(
        handle: str | None = None,
        key: str | None = None,
        seq: int | None = None,
        tier: Annotated[int, Field(ge=0, le=3)] = 1,
        fields: list[str] | None = None,
    ) -> dict[str, object] | str:
        async with _expose(), engine.guard("read", key or handle or "server"):
            result = await engine.read(
                handle=handle,
                key=key,
                seq=seq,
                tier=tier,
                fields=fields,
            )
            if isinstance(result, dict | str):
                return result
            return str(result)

    @server.tool(name="prove", description="Record scoped evidence that a key holds nothing.")
    async def prove(key: str) -> ProveOutput:
        async with _expose(), engine.guard("prove", key):
            return await engine.prove(key)

    @server.tool(
        name="project",
        description="Stream the state projection of a DAG at a given tier.",
    )
    async def project(dagId: str, tier: Annotated[int, Field(ge=0, le=3)] = 1) -> dict[str, object]:
        async with _expose(), engine.guard("project", dagId):
            projection = await engine.project(dagId, tier)
            return dict(json.loads(projection.payload))

    @server.tool(name="fail", description="Release a node for stealing after a failure.")
    async def fail(leaseId: str, reason: str) -> None:
        async with _expose(), engine.guard("fail", leaseId):
            await engine.fail(leaseId, reason)

    @server.tool(name="trace", description="Return the replays of a DAG.")
    async def trace(dagId: str) -> dict[str, object]:
        async with _expose(), engine.guard("trace", dagId):
            payload = await engine.trace(dagId)
            return dict(json.loads(payload))

    @server.resource(
        "kyrx://economy",
        name="economy",
        description="Live token and time economy of the plane.",
        mime_type="application/json",
    )
    def economy() -> str:
        """The measured economy of the plane, model usage included."""
        engine.record_router_usage()
        return json.dumps(engine.ledger.snapshot().to_dict(), indent=2, sort_keys=True)

    @server.resource(
        "kyrx://plans",
        name="plans",
        description="Plans currently materialised, with their nodes.",
        mime_type="application/json",
    )
    def plans() -> str:
        """Every materialised plan."""
        return json.dumps(
            {
                dag_id: [node.id for node in plan.nodes]
                for dag_id, plan in sorted(engine.plans.items())
            },
            indent=2,
            sort_keys=True,
        )
