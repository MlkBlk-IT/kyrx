"""Lease manager: offers, claims, heartbeats, expiry and node stealing.

I3: a write requires an exclusive lease on the node, and a read never blocks.
The manager is the only component allowed to decide who owns a node.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from kyrx.errors import LeaseExpiredError, LeaseNotHeldError, NotFoundError
from kyrx.lease.statemachine import apply_transition
from kyrx.types import Handle, Lease, LeaseState

__all__ = ["ClaimOutcome", "LeaseManager"]


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """Result of a claim request.

    Either the caller owns the node, or another agent is already working on it
    and the result in flight is identified by its handle (§7).
    """

    granted: bool
    lease: Lease | None = None
    inflight_handle: Handle | None = None


class LeaseManager:
    """Owns the leases of every node."""

    def __init__(
        self,
        *,
        offer_ttl_s: float = 5.0,
        run_ttl_s: float = 20.0,
        steal_after_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        inflight_lookup: Callable[[str], Handle | None] | None = None,
    ) -> None:
        self._offer_ttl_ms = int(offer_ttl_s * 1000)
        self._run_ttl_ms = int(run_ttl_s * 1000)
        self._steal_after_ms = int(steal_after_s * 1000)
        self._clock = clock
        self._inflight_lookup = inflight_lookup
        self._by_node: dict[str, Lease] = {}
        self._by_lease: dict[str, str] = {}
        self._results: dict[str, Handle] = {}
        self._failures: dict[str, str] = {}

    def now_ms(self) -> int:
        """Current time in milliseconds, on the injected clock."""
        return int(self._clock() * 1000)

    async def offer(self, node_id: str) -> Lease:
        """Offer a node to the fleet."""
        now = self.now_ms()
        lease = Lease(
            id=f"lease-{uuid4().hex}",
            node_id=node_id,
            agent_id="",
            state="offered",
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + self._offer_ttl_ms,
        )
        self._by_node[node_id] = lease
        self._by_lease[lease.id] = node_id
        return lease

    async def claim(self, node_id: str, agent_id: str) -> ClaimOutcome:
        """Claim a node exclusively, or report the result already in flight.

        A node that is claimed, running, or already done is not handed out
        twice: the caller receives the handle of the result in flight instead
        (§7). This is the silent, fast deduplication path.
        """
        await self.expire_due()
        lease = self._by_node.get(node_id)
        if lease is not None:
            if lease.state in ("claimed", "running", "done"):
                return ClaimOutcome(granted=False, inflight_handle=self.inflight_handle(node_id))
            if lease.state == "expired":
                stolen = await self.steal(node_id, agent_id)
                return ClaimOutcome(granted=True, lease=stolen)
            if lease.state == "failed":
                # The failed lease is over: a new lease cycle starts for the node.
                lease = None
        if lease is None:
            lease = await self.offer(node_id)
        now = self.now_ms()
        claimed = apply_transition(lease, "claimed", now_ms=now).model_copy(
            update={
                "agent_id": agent_id,
                "acquired_at": now,
                "heartbeat_at": now,
                "expires_at": now + self._run_ttl_ms,
            }
        )
        self._by_node[node_id] = claimed
        self._by_lease[claimed.id] = node_id
        return ClaimOutcome(granted=True, lease=claimed)

    async def beat(self, lease_id: str) -> Lease:
        """Renew a lease. The first heartbeat moves it to running."""
        node_id = self._require_lease(lease_id)
        lease = self._by_node[node_id]
        now = self.now_ms()
        if lease.expires_at <= now:
            raise LeaseExpiredError(f"lease {lease_id} expired", lease_id=lease_id)
        target: LeaseState = "running"
        renewed = apply_transition(lease, target, now_ms=now).model_copy(
            update={"expires_at": now + self._run_ttl_ms}
        )
        self._by_node[node_id] = renewed
        return renewed

    async def fail(self, lease_id: str, reason: str) -> Lease:
        """Release a node for stealing after a failure."""
        node_id = self._require_lease(lease_id)
        lease = self._by_node[node_id]
        failed = apply_transition(self._to_running(lease), "failed", now_ms=self.now_ms())
        self._by_node[node_id] = failed
        self._failures[lease_id] = reason
        return failed

    def failure_reason(self, lease_id: str) -> str | None:
        """Why a lease failed, when it did."""
        return self._failures.get(lease_id)

    async def complete(self, lease_id: str, result: Handle) -> Lease:
        """Close a lease and remember the result it produced."""
        node_id = self._require_lease(lease_id)
        lease = self._by_node[node_id]
        done = apply_transition(self._to_running(lease), "done", now_ms=self.now_ms())
        self._by_node[node_id] = done
        self._results[node_id] = result
        return done

    async def attach_result(self, node_id: str, result: Handle) -> None:
        """Record the result of a node before the lease is closed."""
        self._results[node_id] = result

    def _to_running(self, lease: Lease) -> Lease:
        """Move a claimed lease to running: the agent is working on it."""
        if lease.state != "claimed":
            return lease
        moved = apply_transition(lease, "running", now_ms=self.now_ms())
        self._by_node[lease.node_id] = moved
        return moved

    async def expire_due(self) -> list[Lease]:
        """Expire every lease whose TTL elapsed."""
        now = self.now_ms()
        expired: list[Lease] = []
        for node_id, lease in list(self._by_node.items()):
            if lease.state not in ("offered", "claimed", "running"):
                continue
            if lease.expires_at > now:
                continue
            moved = apply_transition(self._to_running(lease), "expired", now_ms=now)
            self._by_node[node_id] = moved
            expired.append(moved)
        return expired

    async def steal(self, node_id: str, agent_id: str) -> Lease:
        """Reassign an expired node to a new agent."""
        lease = self._by_node.get(node_id)
        if lease is None:
            raise NotFoundError(f"no lease on node {node_id}", node_id=node_id)
        if lease.state != "expired":
            raise LeaseNotHeldError(
                f"node {node_id} is not expired and cannot be stolen",
                node_id=node_id,
                state=lease.state,
            )
        now = self.now_ms()
        stolen = apply_transition(lease, "stolen", now_ms=now).model_copy(
            update={
                "agent_id": agent_id,
                "acquired_at": now,
                "heartbeat_at": now,
                "expires_at": now + self._run_ttl_ms,
            }
        )
        self._by_node[node_id] = stolen
        self._by_lease[stolen.id] = node_id
        return stolen

    def lease_of(self, node_id: str) -> Lease | None:
        """Current lease of a node."""
        return self._by_node.get(node_id)

    def inflight_handle(self, node_id: str) -> Handle | None:
        """Handle of the result already produced for a node."""
        known = self._results.get(node_id)
        if known is not None:
            return known
        if self._inflight_lookup is None:
            return None
        return self._inflight_lookup(node_id)

    def _require_lease(self, lease_id: str) -> str:
        node_id = self._by_lease.get(lease_id)
        if node_id is None:
            raise NotFoundError(f"unknown lease {lease_id}", lease_id=lease_id)
        return node_id
