"""Plan invariants: dependency validation, cycle rejection, explainable ambiguity."""

from __future__ import annotations

import pytest

from kyrx.errors import AmbiguityViolationError, ValidationError
from kyrx.mcp.schemas import PlanNodeSpec, PlanSpec
from kyrx.plan.ambiguity import classify_node
from kyrx.plan.dag import PlanBuilder, topological_order
from kyrx.types import Node


def _plan(*nodes: PlanNodeSpec) -> PlanSpec:
    return PlanSpec(branch="main", nodes=list(nodes))


def test_builder_namespaces_the_nodes() -> None:
    plan = PlanBuilder().build(
        dag_id="plan-1",
        plan=_plan(PlanNodeSpec(id="a", outputs=["out-a"])),
        budget_tokens=1000,
    )

    assert plan.nodes[0].id == "plan-1/a"
    assert plan.order == ("plan-1/a",)


def test_builder_rejects_an_unknown_dependency() -> None:
    with pytest.raises(ValidationError):
        PlanBuilder().build(
            dag_id="plan-1",
            plan=_plan(PlanNodeSpec(id="a", deps=["ghost"])),
            budget_tokens=1000,
        )


def test_builder_rejects_a_cycle() -> None:
    with pytest.raises(ValidationError):
        PlanBuilder().build(
            dag_id="plan-1",
            plan=_plan(
                PlanNodeSpec(id="a", deps=["b"]),
                PlanNodeSpec(id="b", deps=["a"]),
            ),
            budget_tokens=1000,
        )


def test_builder_rejects_a_duplicated_node() -> None:
    with pytest.raises(ValidationError):
        PlanBuilder().build(
            dag_id="plan-1",
            plan=_plan(
                PlanNodeSpec(id="a"),
                PlanNodeSpec(id="a"),
            ),
            budget_tokens=1000,
        )


def test_budget_falls_back_to_the_plan_budget() -> None:
    plan = PlanBuilder().build(
        dag_id="plan-1",
        plan=_plan(PlanNodeSpec(id="a"), PlanNodeSpec(id="b", budget_tokens=42)),
        budget_tokens=1000,
    )

    assert plan.node("plan-1/a") is not None
    assert plan.node("plan-1/a").budget_tokens == 1000  # type: ignore[union-attr]
    assert plan.node("plan-1/b").budget_tokens == 42  # type: ignore[union-attr]


def test_topological_order_puts_dependencies_first() -> None:
    plan = PlanBuilder().build(
        dag_id="plan-1",
        plan=_plan(
            PlanNodeSpec(id="a"),
            PlanNodeSpec(id="b", deps=["a"]),
            PlanNodeSpec(id="c", deps=["b"]),
        ),
        budget_tokens=1000,
    )

    assert plan.order.index("plan-1/a") < plan.order.index("plan-1/b")
    assert plan.order.index("plan-1/b") < plan.order.index("plan-1/c")


def test_an_ambiguous_flag_without_a_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="ambiguity_reason"):
        PlanNodeSpec(id="a", kind="ambiguous", ambiguity_score=0.9)


def test_a_deterministic_node_cannot_carry_a_score() -> None:
    with pytest.raises(ValueError, match="ambiguity score"):
        PlanNodeSpec(id="a", kind="deterministic", ambiguity_score=0.4)


def test_classify_reports_the_verdict_of_a_node() -> None:
    deterministic = Node(
        id="n1",
        dag_id="d1",
        kind="deterministic",
        ambiguity_score=0.0,
        budget_tokens=10,
    )
    ambiguous = Node(
        id="n2",
        dag_id="d1",
        kind="ambiguous",
        ambiguity_score=0.7,
        ambiguity_reason="two readings are possible",
        budget_tokens=10,
    )

    assert classify_node(deterministic).ambiguous is False
    assert classify_node(ambiguous).kind == "ambiguous"
    assert classify_node(ambiguous).reason == "two readings are possible"


def test_an_ambiguous_node_without_a_reason_is_refused() -> None:
    node = Node.model_construct(
        id="n3",
        dag_id="d1",
        kind="ambiguous",
        ambiguity_score=0.7,
        ambiguity_reason="",
        budget_tokens=10,
    )

    with pytest.raises(AmbiguityViolationError):
        classify_node(node)


def test_topological_order_rejects_a_cycle() -> None:
    nodes = [
        Node(
            id="a",
            dag_id="d",
            kind="deterministic",
            deps=("b",),
            ambiguity_score=0.0,
            budget_tokens=1,
        ),
        Node(
            id="b",
            dag_id="d",
            kind="deterministic",
            deps=("a",),
            ambiguity_score=0.0,
            budget_tokens=1,
        ),
    ]

    with pytest.raises(ValidationError):
        topological_order(nodes)
