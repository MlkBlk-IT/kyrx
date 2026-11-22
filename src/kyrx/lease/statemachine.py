"""The lease state machine (§7).

Only the transitions listed in :data:`ALLOWED_TRANSITIONS` exist. Every other
transition is rejected, which is what makes the lease history auditable.
"""

from __future__ import annotations

from typing import Final

from kyrx.errors import IllegalTransitionError
from kyrx.types import Lease, LeaseState

__all__ = ["ALLOWED_TRANSITIONS", "apply_transition", "can_transition"]

ALLOWED_TRANSITIONS: Final[frozenset[tuple[LeaseState, LeaseState]]] = frozenset(
    {
        ("offered", "claimed"),
        ("claimed", "running"),
        ("running", "running"),
        ("running", "done"),
        ("running", "failed"),
        ("running", "expired"),
        ("expired", "stolen"),
        ("offered", "expired"),
    }
)


def can_transition(current: LeaseState, target: LeaseState) -> bool:
    """Whether a transition is part of the machine."""
    return (current, target) in ALLOWED_TRANSITIONS


def apply_transition(lease: Lease, target: LeaseState, *, now_ms: int) -> Lease:
    """Move a lease to a new state, or raise."""
    if not can_transition(lease.state, target):
        raise IllegalTransitionError(
            f"cannot move a lease from {lease.state} to {target}",
            lease_id=lease.id,
            node_id=lease.node_id,
            state=lease.state,
            target=target,
        )
    acquired_at = lease.acquired_at
    heartbeat_at = now_ms if target in ("running", "claimed") else lease.heartbeat_at
    return lease.model_copy(
        update={
            "state": target,
            "acquired_at": acquired_at,
            "heartbeat_at": heartbeat_at,
        }
    )
