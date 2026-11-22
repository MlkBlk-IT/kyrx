"""Wire contracts of the ten MCP tools.

Tool names and field names follow the wire contract exactly (camelCase), while
the Python attributes stay snake_case through aliases. Descriptions stay at or
below twelve words and no schema carries an example (§6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kyrx.types import Handle, Key, NodeKind

__all__ = [
    "TOOL_CONTRACTS",
    "BeatInput",
    "ClaimInput",
    "ClaimOutput",
    "FailInput",
    "IntentInput",
    "IntentOutput",
    "ManifestInput",
    "ManifestOutput",
    "NativeStep",
    "PlanNodeSpec",
    "PlanSpec",
    "ProjectInput",
    "ProveInput",
    "ProveOutput",
    "PublishInput",
    "PublishOutput",
    "ReadInput",
    "Tier",
    "ToolContract",
    "TraceInput",
]

Tier = Literal[0, 1, 2, 3]

# 40 tokens of prose is roughly 280 characters; the server rejects longer
# summaries so the spawn payload stays bounded (I1).
Summary = Annotated[str, StringConstraints(min_length=1, max_length=280)]


class _Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class NativeStep(_Contract):
    """One step of a native plan: what it produces, what it consumes.

    The agent never authors a graph: KYRX derives the dependencies itself.
    """

    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        description="Name of the step, and of the key it publishes.",
    )
    consumes: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="Names of the steps this one needs.",
    )
    ambiguous: bool = Field(
        default=False,
        description="True when the output cannot be decided from what it consumes.",
    )
    ambiguity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _ambiguity_is_explainable(self) -> NativeStep:
        if self.ambiguous and not self.ambiguity_reason.strip():
            raise ValueError("an ambiguous step requires an ambiguity_reason (I10)")
        if not self.ambiguous and self.ambiguity_score > 0.0:
            raise ValueError("a deterministic step must not carry an ambiguity score (I5)")
        return self


class PlanNodeSpec(_Contract):
    """One node of a plan submitted by the agent."""

    id: str = Field(min_length=1, max_length=256)
    kind: NodeKind = "deterministic"
    deps: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    ambiguity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_reason: str = Field(default="", max_length=500)
    budget_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ambiguity_is_explainable(self) -> PlanNodeSpec:
        if self.kind == "ambiguous" and not self.ambiguity_reason.strip():
            raise ValueError("ambiguous nodes require an ambiguity_reason (I10)")
        if self.kind == "deterministic" and self.ambiguity_score > 0.0:
            raise ValueError("deterministic nodes must not carry an ambiguity score (I5)")
        return self


class PlanSpec(_Contract):
    """A plan submitted by the agent, materialised without any model call."""

    branch: str = Field(default="main", min_length=1, max_length=128)
    nodes: list[PlanNodeSpec] = Field(min_length=1)


class IntentInput(_Contract):
    """intent(intent, budgetTokens, steps?)"""

    intent: str = Field(min_length=1, max_length=2000)
    budget_tokens: int = Field(alias="budgetTokens", ge=1, le=10_000_000)
    steps: list[NativeStep] | None = Field(
        default=None,
        max_length=64,
        description="Steps of the plan. Derived by the model when omitted.",
    )


class ManifestInput(_Contract):
    """manifest(dagId, branch)"""

    dag_id: str = Field(alias="dagId", min_length=1, max_length=128)
    branch: str = Field(default="main", min_length=1, max_length=128)


class ClaimInput(_Contract):
    """claim(nodeId, agentId)"""

    node_id: str = Field(alias="nodeId", min_length=1, max_length=256)
    agent_id: str = Field(alias="agentId", min_length=1, max_length=256)


class BeatInput(_Contract):
    """beat(leaseId)"""

    lease_id: str = Field(alias="leaseId", min_length=1, max_length=256)


class PublishInput(_Contract):
    """publish(nodeId, artifactRef, summary)"""

    node_id: str = Field(alias="nodeId", min_length=1, max_length=256)
    artifact_ref: str = Field(alias="artifactRef", min_length=1, max_length=512)
    summary: Summary


class ReadInput(_Contract):
    """read(handle | entryRef, tier, fields?)"""

    handle: Handle | None = None
    key: Key | None = None
    seq: int | None = Field(default=None, ge=0)
    tier: Tier = 1
    fields: list[str] | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> ReadInput:
        by_handle = self.handle is not None
        by_entry = self.key is not None
        if by_handle == by_entry:
            raise ValueError("read takes either a handle or an entry reference")
        if by_entry and self.seq is None:
            raise ValueError("an entry reference requires its sequence number")
        return self


class ProveInput(_Contract):
    """prove(key)"""

    key: Key


class ProjectInput(_Contract):
    """project(dagId, tier)"""

    dag_id: str = Field(alias="dagId", min_length=1, max_length=128)
    tier: Tier = 1


class FailInput(_Contract):
    """fail(leaseId, reason)"""

    lease_id: str = Field(alias="leaseId", min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=500)


class TraceInput(_Contract):
    """trace(dagId)"""

    dag_id: str = Field(alias="dagId", min_length=1, max_length=128)


class IntentOutput(_Contract):
    dag_id: str = Field(alias="dagId")


class ManifestOutput(_Contract):
    handle: Handle
    node_ids: list[str] = Field(alias="nodeIds")


class ClaimOutput(_Contract):
    node_id: str = Field(alias="nodeId")
    agent_id: str = Field(alias="agentId")
    lease_id: str | None = Field(default=None, alias="leaseId")
    expires_at: int | None = Field(default=None, alias="expiresAt")
    inflight_handle: Handle | None = Field(default=None, alias="inflightHandle")


class PublishOutput(_Contract):
    key: Key
    seq: int = Field(ge=0)
    artifact: Handle


class ProveOutput(_Contract):
    key: Key
    scope: list[str]
    proven_at: int = Field(alias="provenAt")


@dataclass(frozen=True, slots=True)
class ToolContract:
    """One tool of the public surface."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None


TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    ToolContract(
        "intent",
        "Materialise a DAG plan. The only model-facing entry point.",
        IntentInput,
        IntentOutput,
    ),
    ToolContract(
        "manifest",
        "Compact manifest of a branch: node ids, kinds, statuses, scores.",
        ManifestInput,
        ManifestOutput,
    ),
    ToolContract(
        "claim",
        "Take exclusive lease on a node, or receive the in-flight result handle.",
        ClaimInput,
        ClaimOutput,
    ),
    ToolContract(
        "beat",
        "Extend a running lease before its TTL expires.",
        BeatInput,
        None,
    ),
    ToolContract(
        "publish",
        "Write a node result to the blackboard as a new version.",
        PublishInput,
        PublishOutput,
    ),
    ToolContract(
        "read",
        "Read an artifact or entry by handle with tier and field selection.",
        ReadInput,
        None,
    ),
    ToolContract(
        "prove",
        "Record scoped evidence that a key holds nothing.",
        ProveInput,
        ProveOutput,
    ),
    ToolContract(
        "project",
        "Stream the state projection of a DAG at a given tier.",
        ProjectInput,
        None,
    ),
    ToolContract(
        "fail",
        "Release a node for stealing after a failure.",
        FailInput,
        None,
    ),
    ToolContract(
        "trace",
        "Return the replayable journal of a DAG.",
        TraceInput,
        None,
    ),
)
