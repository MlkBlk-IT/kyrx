"""Core data model: handles, artifacts, entries, nodes, leases, negative proofs.

Every model is frozen. An artifact is named by the hash of its content and is
never mutated; a blackboard entry only advances by publishing a new version
(I2). Keys are constrained to a character whitelist (I12 hardening).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

__all__ = [
    "Artifact",
    "Entry",
    "EntryRef",
    "Handle",
    "Key",
    "Lease",
    "LeaseState",
    "NegativeProof",
    "Node",
    "NodeKind",
    "NodeStatus",
    "Producer",
]

Handle = Annotated[
    str,
    StringConstraints(pattern=r"^blake3:[0-9a-f]{64}$"),
    Field(description="Content address of an artifact."),
]

Key = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9/_-]{0,255}$"),
    Field(description="Blackboard key, e.g. dag/<id>/<branch>/<name>."),
]

NodeKind = Literal["deterministic", "ambiguous"]
NodeStatus = Literal["pending", "ready", "leased", "done", "failed", "stolen"]
LeaseState = Literal["offered", "claimed", "running", "done", "failed", "expired", "stolen"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Producer(_Frozen):
    """Identity of whoever produced an artifact."""

    agent_id: str
    node_id: str


class Artifact(_Frozen):
    """An immutable blob, addressed by the hash of its content."""

    id: Handle
    media_type: str
    size: int = Field(ge=0)
    producer: Producer
    parents: tuple[Handle, ...] = ()
    created_at: str


class EntryRef(_Frozen):
    """Pointer to one version of one blackboard key."""

    key: Key
    seq: int = Field(ge=0)


class Entry(_Frozen):
    """One version of a blackboard key. Tombstones carry no artifact."""

    key: Key
    seq: int = Field(ge=0)
    artifact: Handle | None = None
    tombstone: bool = False
    invalidation_scope: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _artifact_matches_tombstone(self) -> Entry:
        if self.tombstone and self.artifact is not None:
            raise ValueError("tombstone entries must not carry an artifact")
        if not self.tombstone and self.artifact is None:
            raise ValueError("live entries require an artifact")
        return self


class Node(_Frozen):
    """A unit of work in a DAG."""

    id: str
    dag_id: str
    kind: NodeKind
    deps: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    ambiguity_score: float = Field(ge=0.0, le=1.0)
    ambiguity_reason: str = ""
    budget_tokens: int = Field(ge=0)
    status: NodeStatus = "pending"

    @model_validator(mode="after")
    def _ambiguity_is_explainable(self) -> Node:
        if self.kind == "ambiguous" and not self.ambiguity_reason.strip():
            raise ValueError("ambiguous nodes require an ambiguity_reason (I10)")
        if self.kind == "deterministic" and self.ambiguity_score > 0.0:
            raise ValueError("deterministic nodes must not carry an ambiguity score (I5)")
        return self


class Lease(_Frozen):
    """Exclusive claim on a node, renewed by heartbeat."""

    id: str
    node_id: str
    agent_id: str
    state: LeaseState
    acquired_at: int
    heartbeat_at: int
    expires_at: int


class NegativeProof(_Frozen):
    """Scoped evidence that a key holds nothing."""

    key: Key
    scope: tuple[str, ...] = ()
    proven_at: int
