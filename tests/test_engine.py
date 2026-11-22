"""End-to-end invariants of the engine and of the MCP surface."""

from __future__ import annotations

import base64

import pytest

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.errors import LeaseNotHeldError, ValidationError
from kyrx.mcp.schemas import NativeStep
from kyrx.mcp.server import build_server
from kyrx.trace.replay import compare_projections, replay_projection


def _plan(node_count: int = 3, ambiguous: int = 1) -> list[NativeStep]:
    steps = []
    for index in range(node_count):
        is_ambiguous = index < ambiguous
        steps.append(
            NativeStep(
                name=f"n{index}",
                consumes=[f"n{index - 1}"] if index else [],
                ambiguous=is_ambiguous,
                ambiguity_score=0.9 if is_ambiguous else 0.0,
                ambiguity_reason="the reducer cannot decide" if is_ambiguous else "",
            )
        )
    return steps


def _inline(text: str) -> str:
    return "inline:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


async def test_a_malformed_inline_payload_is_a_validation_error(settings: Settings) -> None:
    engine = Engine(settings)

    with pytest.raises(ValidationError):
        await engine.publish("plan/x", "inline:not base64!", "summary")


async def test_a_plan_is_materialised_from_the_agent(settings: Settings) -> None:
    engine = Engine(settings)

    dag_id = await engine.intent("do the thing", 1000, _plan())

    assert dag_id in engine.plans
    assert len(engine.plans[dag_id].nodes) == 3


async def test_a_manifest_lists_the_nodes(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _plan())

    manifest = await engine.manifest(dag_id, "main")

    assert len(manifest.node_ids) == 3
    assert manifest.handle.startswith("blake3:")


async def test_publishing_requires_a_lease(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _plan())
    node = engine.plans[dag_id].nodes[0]

    with pytest.raises(LeaseNotHeldError):
        await engine.publish(node.id, _inline("result"), "summary")


async def test_the_full_cycle_works(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _plan())
    node = engine.plans[dag_id].nodes[0]

    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None

    published = await engine.publish(node.id, _inline("the result"), "summary of the node")
    assert published.seq == 1

    content = await engine.read(handle=published.artifact, tier=3)
    assert content == "the result"


async def test_a_second_claim_receives_the_result_in_flight(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _plan())
    node = engine.plans[dag_id].nodes[0]

    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None
    published = await engine.publish(node.id, _inline("the result"), "summary")

    second = await engine.claim(node.id, "agent-2")

    assert second.lease_id is None
    assert second.inflight_handle == published.artifact


async def test_a_proof_is_scoped_to_its_key(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _plan())
    key = f"dag/{dag_id}/main/out-0"

    proof = await engine.prove(key)

    assert proof.key == key
    assert engine.cache.is_proven_absent(key) is True


async def test_the_projection_is_replayable(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _plan())
    node = engine.plans[dag_id].nodes[0]
    await engine.claim(node.id, "agent-1")
    await engine.publish(node.id, _inline("the result"), "summary")

    live = await engine.project(dag_id, 1)
    replayed = await replay_projection(dag_id, engine.store, engine.journal, 1)

    compare_projections(live, replayed)
    assert live.payload == replayed.payload


async def test_the_surface_exposes_the_ten_tools(settings: Settings) -> None:
    server = build_server(settings)

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {
        "intent",
        "manifest",
        "claim",
        "beat",
        "publish",
        "read",
        "prove",
        "project",
        "fail",
        "trace",
    }
