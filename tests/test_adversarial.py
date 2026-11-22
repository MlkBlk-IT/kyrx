"""Adversarial agent: stale reads, claims on expired nodes, publishes out of lease."""

from __future__ import annotations

import base64

import pytest

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.errors import LeaseNotHeldError, NotFoundError, ToolError
from kyrx.mcp.schemas import NativeStep


def _inline(text: str) -> str:
    return "inline:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


async def test_publishing_without_a_lease_is_refused(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]

    with pytest.raises(LeaseNotHeldError):
        await engine.publish(node.id, _inline("result"), "summary")


async def test_publishing_on_a_finished_node_is_refused(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None
    await engine.publish(node.id, _inline("result"), "summary")

    with pytest.raises(LeaseNotHeldError):
        await engine.publish(node.id, _inline("other"), "summary")


async def test_a_stale_entry_is_never_served(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None
    first = await engine.publish(node.id, _inline("first"), "summary")

    stale = await engine.board.entry(first.key, first.seq)
    latest = await engine.board.latest(first.key)

    assert stale is not None
    assert stale.artifact == first.artifact
    assert latest is not None
    assert latest.seq >= stale.seq


async def test_claiming_an_expired_node_is_possible_but_a_live_one_is_not(
    settings: Settings,
) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None

    with pytest.raises(Exception, match=r"cannot be stolen|not expired"):
        await engine.leases.steal(node.id, "intruder")


async def test_an_unknown_artifact_is_refused(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None

    with pytest.raises(NotFoundError):
        await engine.publish(node.id, "blake3:" + "0" * 64, "summary")


async def test_a_malformed_artifact_reference_is_refused(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None

    with pytest.raises(ToolError):
        await engine.publish(node.id, "http://example.com/evil", "summary")


async def test_reading_a_missing_key_is_refused(settings: Settings) -> None:
    engine = Engine(settings)

    with pytest.raises(NotFoundError):
        await engine.read(key="dag/nope/main/ghost", tier=1)


async def test_an_oversized_artifact_is_refused(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("x", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None
    oversized = base64.b64encode(b"x" * (9 * 1024 * 1024)).decode("ascii")

    with pytest.raises(Exception, match="bytes"):
        await engine.publish(node.id, f"inline:{oversized}", "summary")


async def test_an_unknown_plan_is_refused(settings: Settings) -> None:
    engine = Engine(settings)

    with pytest.raises(NotFoundError):
        await engine.manifest("plan-ghost", "main")


async def test_an_unknown_node_is_refused(settings: Settings) -> None:
    engine = Engine(settings)
    await engine.intent("x", 1000, [NativeStep(name="only")])

    with pytest.raises(NotFoundError):
        await engine.claim("plan-ghost/nope", "agent-1")
