"""Economy invariants: the ledger measures, and the cache serves."""

from __future__ import annotations

import base64
from pathlib import Path

from kyrx.config import Settings
from kyrx.economy.ledger import TokenLedger
from kyrx.engine import Engine
from kyrx.mcp.schemas import NativeStep


def _inline(text: str) -> str:
    return "inline:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_the_ledger_measures_the_saving() -> None:
    ledger = TokenLedger()
    ledger.record("project", served=100, avoided=900)

    snapshot = ledger.snapshot()

    assert snapshot.tokens_served == 100
    assert snapshot.tokens_avoided == 900
    assert snapshot.saving_ratio == 0.9
    assert snapshot.calls == 1


def test_the_ledger_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "economy.json"
    ledger = TokenLedger(path)
    ledger.record("project", served=100, avoided=900)

    reloaded = TokenLedger.load(path)

    assert reloaded.snapshot().tokens_served == 100
    assert reloaded.snapshot().tokens_avoided == 900


def test_the_ledger_counts_model_calls() -> None:
    ledger = TokenLedger()
    ledger.record_model_call()
    ledger.record_model_call()

    assert ledger.snapshot().model_calls == 2


async def test_a_repeated_projection_is_served_from_the_cache(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent(
        "the thing",
        1000,
        [NativeStep(name="only")],
    )
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None
    await engine.publish(node.id, _inline("a result"), "summary")

    first = await engine.project(dag_id, 1)
    second = await engine.project(dag_id, 1)

    assert first.payload == second.payload
    assert engine.metrics.counter("project.cache_hit") == 1


async def test_a_publish_invalidates_the_cached_projection(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent(
        "the thing",
        1000,
        [NativeStep(name="first"), NativeStep(name="second", consumes=["first"])],
    )
    first_node = engine.plans[dag_id].nodes[0]
    await engine.claim(first_node.id, "agent-1")
    await engine.publish(first_node.id, _inline("a result"), "summary")
    await engine.project(dag_id, 1)

    second_node = engine.plans[dag_id].nodes[1]
    claim = await engine.claim(second_node.id, "agent-1")
    assert claim.lease_id is not None
    await engine.publish(second_node.id, _inline("another result"), "summary")
    await engine.project(dag_id, 1)

    assert engine.metrics.counter("project.cache_hit") == 0


async def test_the_economy_measures_what_the_plane_saved(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("the thing", 1000, [NativeStep(name="only")])
    node = engine.plans[dag_id].nodes[0]
    claim = await engine.claim(node.id, "agent-1")
    assert claim.lease_id is not None
    await engine.publish(node.id, _inline("x" * 4000), "summary")

    snapshot = engine.ledger.snapshot()

    assert snapshot.tokens_served > 0
    assert snapshot.tokens_avoided >= 0
    assert snapshot.calls > 0
