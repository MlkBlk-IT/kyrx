"""The plan: a validated DAG of nodes.

A plan is materialised either from a plan submitted by the agent or from the
decomposition returned by the model. Both paths converge here, so validation
is identical: dependencies must exist, the graph must be acyclic, and every
ambiguous node must be explainable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from kyrx.errors import ValidationError
from kyrx.mcp.schemas import PlanNodeSpec, PlanSpec
from kyrx.plan.ambiguity import classify_node
from kyrx.types import Node

__all__ = ["Plan", "PlanBuilder", "node_id", "topological_order"]


def node_id(dag_id: str, local_id: str) -> str:
    """Global identifier of a node inside a DAG."""
    return f"{dag_id}/{local_id}"


@dataclass(frozen=True, slots=True)
class Plan:
    """A materialised DAG."""

    dag_id: str
    branch: str
    nodes: tuple[Node, ...]
    order: tuple[str, ...]
    _by_id: dict[str, Node] = field(default_factory=dict, repr=False, compare=False)

    def node(self, node_id_: str) -> Node | None:
        """One node by identifier."""
        return self._by_id.get(node_id_)

    def dependencies_met(self, node_id_: str) -> bool:
        """Whether every dependency of a node is done."""
        node = self._by_id.get(node_id_)
        if node is None:
            return False
        return all(
            (dep := self._by_id.get(dependency)) is not None and dep.status == "done"
            for dependency in node.deps
        )

    def edges(self) -> frozenset[str]:
        """Every dependency edge of the plan, as ``producer->consumer``."""
        found: set[str] = set()
        for node in self.nodes:
            for dependency in node.deps:
                found.add(f"{dependency}->{node.id}")
        return frozenset(found)

    def consumers_of(self, producer_id: str) -> frozenset[str]:
        """Edges that depend on a node."""
        return frozenset(edge for edge in self.edges() if edge.startswith(f"{producer_id}->"))

    def edges_touching(self, node_id: str) -> frozenset[str]:
        """Every edge the node takes part in, on either side."""
        return frozenset(
            edge
            for edge in self.edges()
            if edge.startswith(f"{node_id}->") or edge.endswith(f"->{node_id}")
        )


def topological_order(nodes: Iterable[Node]) -> tuple[str, ...]:
    """Order nodes so that every dependency comes first. Rejects cycles."""
    remaining = {node.id: set(node.deps) for node in nodes}
    emitted: set[str] = set()
    ordered: list[str] = []
    while remaining:
        ready = sorted(name for name, deps in remaining.items() if deps <= emitted)
        if not ready:
            raise ValidationError("the plan contains a dependency cycle", cycle=sorted(remaining))
        for name in ready:
            ordered.append(name)
            emitted.add(name)
            del remaining[name]
    return tuple(ordered)


class PlanBuilder:
    """Materialises a plan from a submitted plan specification."""

    def build(
        self,
        *,
        dag_id: str,
        plan: PlanSpec,
        budget_tokens: int,
    ) -> Plan:
        """Turn a plan specification into a validated DAG."""
        local_ids = [spec.id for spec in plan.nodes]
        if len(set(local_ids)) != len(local_ids):
            raise ValidationError("the plan declares the same node twice", dag_id=dag_id)

        nodes: list[Node] = []
        for spec in plan.nodes:
            node = self._node(dag_id=dag_id, spec=spec, budget_tokens=budget_tokens)
            classify_node(node)
            nodes.append(node)

        for node in nodes:
            for dependency in node.deps:
                if dependency not in {n.id for n in nodes}:
                    raise ValidationError(
                        f"node {node.id} depends on unknown node {dependency}",
                        dag_id=dag_id,
                    )

        order = topological_order(nodes)
        by_id = {node.id: node for node in nodes}
        return Plan(
            dag_id=dag_id,
            branch=plan.branch,
            nodes=tuple(nodes),
            order=order,
            _by_id=by_id,
        )

    @staticmethod
    def _node(*, dag_id: str, spec: PlanNodeSpec, budget_tokens: int) -> Node:
        node_budget = spec.budget_tokens if spec.budget_tokens is not None else budget_tokens
        return Node(
            id=node_id(dag_id, spec.id),
            dag_id=dag_id,
            kind=spec.kind,
            deps=tuple(node_id(dag_id, dependency) for dependency in spec.deps),
            inputs=tuple(spec.inputs),
            outputs=tuple(spec.outputs),
            ambiguity_score=spec.ambiguity_score,
            ambiguity_reason=spec.ambiguity_reason,
            budget_tokens=node_budget,
            status="pending",
        )


def node_inputs(nodes: Sequence[Node]) -> dict[str, tuple[str, ...]]:
    """Map of node identifiers to their declared input keys."""
    return {node.id: node.inputs for node in nodes}
