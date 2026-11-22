"""Lease invariants: the state machine, exclusivity, expiry and stealing."""

from __future__ import annotations

import pytest

from kyrx.errors import IllegalTransitionError, LeaseNotHeldError
from kyrx.lease.manager import LeaseManager
from kyrx.lease.statemachine import ALLOWED_TRANSITIONS, apply_transition, can_transition
from kyrx.types import Lease


class FakeClock:
    """A clock the test drives by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _lease(state: str = "offered") -> Lease:
    return Lease(
        id="lease-1",
        node_id="node-1",
        agent_id="agent-1",
        state=state,
        acquired_at=0,
        heartbeat_at=0,
        expires_at=1000,
    )


def test_the_machine_has_exactly_the_documented_transitions() -> None:
    assert len(ALLOWED_TRANSITIONS) == 8
    assert ("running", "running") in ALLOWED_TRANSITIONS
    assert ("expired", "stolen") in ALLOWED_TRANSITIONS
    assert ("done", "running") not in ALLOWED_TRANSITIONS


def test_an_illegal_transition_is_rejected() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_transition(_lease("done"), "running", now_ms=1)


def test_a_legal_transition_is_accepted() -> None:
    moved = apply_transition(_lease("offered"), "claimed", now_ms=42)

    assert moved.state == "claimed"
    assert moved.heartbeat_at == 42


def test_can_transition_mirrors_the_table() -> None:
    assert can_transition("offered", "claimed")
    assert not can_transition("offered", "running")


async def test_a_claim_is_exclusive() -> None:
    clock = FakeClock()
    manager = LeaseManager(clock=clock)

    first = await manager.claim("node-1", "agent-1")
    second = await manager.claim("node-1", "agent-2")

    assert first.granted is True
    assert first.lease is not None
    assert second.granted is False


async def test_a_done_node_hands_back_its_result() -> None:
    clock = FakeClock()
    manager = LeaseManager(clock=clock)
    claimed = await manager.claim("node-1", "agent-1")
    assert claimed.lease is not None
    handle = "blake3:" + "a" * 64
    await manager.complete(claimed.lease.id, handle)

    outcome = await manager.claim("node-1", "agent-2")

    assert outcome.granted is False
    assert outcome.inflight_handle == handle


async def test_a_heartbeat_moves_the_lease_to_running() -> None:
    clock = FakeClock()
    manager = LeaseManager(clock=clock)
    claimed = await manager.claim("node-1", "agent-1")
    assert claimed.lease is not None

    renewed = await manager.beat(claimed.lease.id)

    assert renewed.state == "running"
    clock.advance(1)
    renewed = await manager.beat(claimed.lease.id)
    assert renewed.expires_at > renewed.heartbeat_at


async def test_a_lease_expires_and_can_be_stolen() -> None:
    clock = FakeClock()
    manager = LeaseManager(clock=clock, run_ttl_s=1.0)
    claimed = await manager.claim("node-1", "agent-1")
    assert claimed.lease is not None

    clock.advance(5)
    expired = await manager.expire_due()

    assert len(expired) == 1
    assert expired[0].state == "expired"
    stolen = await manager.steal("node-1", "agent-2")
    assert stolen.state == "stolen"
    assert stolen.agent_id == "agent-2"


async def test_a_running_node_cannot_be_stolen() -> None:
    clock = FakeClock()
    manager = LeaseManager(clock=clock)
    await manager.claim("node-1", "agent-1")

    with pytest.raises(LeaseNotHeldError):
        await manager.steal("node-1", "agent-2")


async def test_a_failure_releases_the_node() -> None:
    clock = FakeClock()
    manager = LeaseManager(clock=clock)
    claimed = await manager.claim("node-1", "agent-1")
    assert claimed.lease is not None

    failed = await manager.fail(claimed.lease.id, "the agent died")

    assert failed.state == "failed"
