"""Native planner invariants: the server derives the plan, the agent does not."""

from __future__ import annotations

import pytest

from kyrx.errors import ValidationError
from kyrx.mcp.schemas import NativeStep
from kyrx.plan.native import NativePlanner


def test_the_dependencies_are_derived_from_what_a_step_consumes() -> None:
    plan = NativePlanner().derive(
        dag_id="plan-1",
        steps=[
            NativeStep(name="collect"),
            NativeStep(name="analyse", consumes=["collect"]),
            NativeStep(name="publish", consumes=["analyse"]),
        ],
        branch="main",
        budget_tokens=1000,
    )

    assert plan.node("plan-1/analyse") is not None
    assert plan.node("plan-1/analyse").deps == ("plan-1/collect",)  # type: ignore[union-attr]
    assert plan.order.index("plan-1/collect") < plan.order.index("plan-1/analyse")


def test_an_unknown_consumed_step_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NativePlanner().derive(
            dag_id="plan-1",
            steps=[NativeStep(name="analyse", consumes=["ghost"])],
            branch="main",
            budget_tokens=1000,
        )


def test_a_step_consuming_itself_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NativePlanner().derive(
            dag_id="plan-1",
            steps=[NativeStep(name="analyse", consumes=["analyse"])],
            branch="main",
            budget_tokens=1000,
        )


def test_a_duplicated_step_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NativePlanner().derive(
            dag_id="plan-1",
            steps=[NativeStep(name="analyse"), NativeStep(name="analyse")],
            branch="main",
            budget_tokens=1000,
        )


def test_a_cycle_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NativePlanner().derive(
            dag_id="plan-1",
            steps=[
                NativeStep(name="a", consumes=["b"]),
                NativeStep(name="b", consumes=["a"]),
            ],
            branch="main",
            budget_tokens=1000,
        )


def test_an_ambiguous_step_must_explain_itself() -> None:
    with pytest.raises(ValueError, match="ambiguity_reason"):
        NativeStep(name="a", ambiguous=True, ambiguity_score=0.9)


def test_a_deterministic_step_carries_no_score() -> None:
    with pytest.raises(ValueError, match="ambiguity score"):
        NativeStep(name="a", ambiguity_score=0.5)


def test_an_empty_plan_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NativePlanner().derive(
            dag_id="plan-1",
            steps=[],
            branch="main",
            budget_tokens=1000,
        )


def test_a_step_name_is_bounded() -> None:
    with pytest.raises(ValueError, match="pattern"):
        NativeStep(name="Not A Valid Name")
