"""Native planner: the server derives the plan, the agent never authors a graph.

The agent declares *steps*: what a step produces and what it consumes. KYRX
computes the dependencies, the order and the budgets. No graph vocabulary
crosses the boundary, and no model is called to build it.

When the agent declares no step at all, the model is asked to propose the
steps — still in the native shape, never in the shape of a graph.
"""

from __future__ import annotations

from kyrx.errors import ValidationError
from kyrx.mcp.schemas import NativeStep, PlanNodeSpec, PlanSpec
from kyrx.plan.dag import Plan, PlanBuilder

__all__ = ["NativePlanner"]


class NativePlanner:
    """Derives a plan from native steps."""

    def derive(
        self,
        *,
        dag_id: str,
        steps: list[NativeStep],
        branch: str,
        budget_tokens: int,
    ) -> Plan:
        """Turn native steps into a validated plan."""
        if not steps:
            raise ValidationError("a plan needs at least one step", dag_id=dag_id)

        names = [step.name for step in steps]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValidationError(
                "a step name appears twice",
                dag_id=dag_id,
                duplicates=sorted(duplicates),
            )

        known = set(names)
        for step in steps:
            if step.consumes.count(step.name) > 1:
                raise ValidationError(
                    f"step {step.name} consumes itself twice",
                    dag_id=dag_id,
                    step=step.name,
                )
            for consumed in step.consumes:
                if consumed not in known:
                    raise ValidationError(
                        f"step {step.name} consumes the unknown step {consumed}",
                        dag_id=dag_id,
                        step=step.name,
                        consumed=consumed,
                    )

        nodes = [
            PlanNodeSpec(
                id=step.name,
                kind="ambiguous" if step.ambiguous else "deterministic",
                deps=list(step.consumes),
                outputs=[step.name],
                ambiguity_score=step.ambiguity_score,
                ambiguity_reason=step.ambiguity_reason,
            )
            for step in steps
        ]
        return PlanBuilder().build(
            dag_id=dag_id,
            plan=PlanSpec(branch=branch, nodes=nodes),
            budget_tokens=budget_tokens,
        )
