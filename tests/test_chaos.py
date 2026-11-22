"""Chaos: eight agents, random failures, no corruption.

The journal must stay replayable and every published entry must point at a
blob that exists, whatever the agents do to each other.
"""

from __future__ import annotations

import asyncio
import base64
from random import Random

import pytest

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.mcp.schemas import NativeStep
from kyrx.trace.replay import replay_board

AGENTS = 8
NODES = 20


def _inline(text: str) -> str:
    return "inline:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _steps(count: int) -> list[NativeStep]:
    return [
        NativeStep(
            name=f"n{index}",
            consumes=[f"n{index - 1}"] if index else [],
        )
        for index in range(count)
    ]


@pytest.mark.chaos
async def test_eight_agents_with_random_failures_never_corrupt_the_state(
    settings: Settings,
) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("chaos", 100_000, _steps(NODES))
    plan = engine.plans[dag_id]
    chaos = Random(20261105)  # noqa: S311 - chaos, not cryptography

    async def worker(agent_id: str) -> None:
        for node in plan.nodes:
            if chaos.random() < 0.25:
                continue
            outcome = await engine.claim(node.id, agent_id)
            if outcome.lease_id is None:
                continue
            if chaos.random() < 0.2:
                await engine.fail(outcome.lease_id, "chaos")
                continue
            await engine.publish(node.id, _inline(f"result of {node.id}"), "summary")

    await asyncio.gather(*(worker(f"agent-{index}") for index in range(AGENTS)))

    replayed = await replay_board(engine.store, engine.journal)
    entries = await replayed.versions_prefix(f"dag/{dag_id}/")
    assert entries, "the scenario published nothing"

    for entry in entries:
        assert entry.artifact is not None
        assert engine.store.has(entry.artifact)
        assert engine.store.get(entry.artifact).startswith(b"result of ")


@pytest.mark.chaos
async def test_the_sequence_numbers_stay_unique_under_chaos(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("chaos", 100_000, _steps(5))
    plan = engine.plans[dag_id]
    chaos = Random(7)  # noqa: S311 - chaos, not cryptography

    async def worker(agent_id: str) -> None:
        for node in plan.nodes:
            outcome = await engine.claim(node.id, agent_id)
            if outcome.lease_id is None:
                continue
            await asyncio.sleep(chaos.random() / 1000)
            await engine.publish(node.id, _inline("result"), "summary")

    await asyncio.gather(*(worker(f"agent-{index}") for index in range(AGENTS)))

    entries = await engine.board.versions_prefix(f"dag/{dag_id}/")
    by_key: dict[str, list[int]] = {}
    for entry in entries:
        by_key.setdefault(entry.key, []).append(entry.seq)
    for key, sequences in by_key.items():
        assert sequences == sorted(sequences), key
        assert len(sequences) == len(set(sequences)), key


@pytest.mark.chaos
async def test_the_audit_chain_survives_chaos(settings: Settings) -> None:
    engine = Engine(settings)
    dag_id = await engine.intent("chaos", 100_000, _steps(5))

    async def worker(agent_id: str) -> None:
        node = engine.plans[dag_id].nodes[0]
        async with engine.guard("claim", agent_id):
            outcome = await engine.claim(node.id, agent_id)
            if outcome.lease_id is None:
                raise RuntimeError("lost the node")

    results = await asyncio.gather(
        *(worker(f"agent-{index}") for index in range(AGENTS)),
        return_exceptions=True,
    )

    assert any(isinstance(result, RuntimeError) for result in results)
    assert engine.audit.verify() is True
