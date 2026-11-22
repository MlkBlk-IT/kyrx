"""Ambiguity classification (I5, I10).

A node is ambiguous when the reducer cannot decide its output from its inputs
alone. Every verdict carries a score and a reason: a flag without a reason is
rejected rather than silently accepted.
"""

from __future__ import annotations

from dataclasses import dataclass

from kyrx.errors import AmbiguityViolationError
from kyrx.types import Node, NodeKind

__all__ = ["AMBIGUITY_THRESHOLD", "AmbiguityVerdict", "classify_node", "require_explainable"]

# At or above this score the node cannot be decided automatically.
AMBIGUITY_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class AmbiguityVerdict:
    """Why a node is deterministic or ambiguous."""

    kind: NodeKind
    score: float
    reason: str

    @property
    def ambiguous(self) -> bool:
        return self.kind == "ambiguous"


def require_explainable(score: float, reason: str) -> None:
    """Reject an ambiguity flag that carries no reason (I10)."""
    if not reason.strip():
        raise AmbiguityViolationError(
            "an ambiguous flag requires a textual reason",
            score=score,
        )


def classify_node(node: Node) -> AmbiguityVerdict:
    """Return the verdict of a node, checking that it is explainable."""
    if node.kind == "ambiguous":
        require_explainable(node.ambiguity_score, node.ambiguity_reason)
        return AmbiguityVerdict("ambiguous", node.ambiguity_score, node.ambiguity_reason)
    return AmbiguityVerdict("deterministic", 0.0, "")
