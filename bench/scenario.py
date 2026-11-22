"""Reference scenario: forty nodes, six of them ambiguous.

The scenario drives the engine the way a fleet would, and measures the eight
acceptance criteria of the specification.
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from pathlib import Path

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.mcp.schemas import NativeStep
from kyrx.reduce.projection import estimate_tokens
from kyrx.reduce.tiers import tier_limit
from kyrx.trace.replay import compare_projections, replay_projection

__all__ = [
    "AMBIGUOUS_COUNT",
    "NODE_COUNT",
    "FakeClient",
    "Measurements",
    "build_plan_spec",
    "run_scenario",
]

NODE_COUNT = 40
AMBIGUOUS_COUNT = 6
AGENT_COUNT = 8

# A realistic node result: a paragraph an agent would have to re-read.
RESULT_TEXT = "analysis of the node output. " * 40


class FakeClient:
    """A model that answers with a fixed payload and counts its calls."""

    def __init__(self, payload: str = "resolved") -> None:
        self.payload = payload
        self.calls = 0
        self.prompts: list[str] = []

    async def complete(self, *, system: str, prompt: str) -> str:
        """Answer the model call and count it."""
        self.calls += 1
        self.prompts.append(f"{system}\n{prompt}")
        return self.payload


@dataclass(slots=True)
class Measurements:
    """Raw measurements of one scenario run."""

    spawn_tokens: int = 0
    verify_tokens: int = 0
    dedup_detected: int = 0
    dedup_trials: int = 0
    handle_reuse: int = 0
    llm_calls: int = 0
    replay_identical: bool = False
    claim_samples_ms: list[float] = field(default_factory=list)
    projection_samples_ms: list[float] = field(default_factory=list)
    tier_violations: int = 0
    substrate_tokens: int = 0
    naive_tokens: int = 0
    published_artifacts: list[str] = field(default_factory=list)


def build_plan_spec(
    node_count: int = NODE_COUNT,
    ambiguous: int = AMBIGUOUS_COUNT,
) -> list[NativeStep]:
    """A linear plan whose first nodes are ambiguous."""
    steps: list[NativeStep] = []
    for index in range(node_count):
        is_ambiguous = index < ambiguous
        steps.append(
            NativeStep(
                name=f"n{index}",
                consumes=[f"n{index - 1}"] if index else [],
                ambiguous=is_ambiguous,
                ambiguity_score=0.8 if is_ambiguous else 0.0,
                ambiguity_reason="the reducer cannot decide this output" if is_ambiguous else "",
            )
        )
    return steps


async def run_scenario(data_dir: Path) -> Measurements:
    """Run the reference scenario and collect the measurements."""
    settings = Settings(data_dir=data_dir)
    client = FakeClient()
    engine = Engine(settings, client=client)
    measurements = Measurements()

    dag_id = await engine.intent("reference scenario", 20_000, build_plan_spec())
    plan = engine.plans[dag_id]

    manifest = await engine.manifest(dag_id, "main")
    measurements.spawn_tokens = estimate_tokens(",".join(manifest.node_ids))

    payload = base64.b64encode(RESULT_TEXT.encode("utf-8")).decode("ascii")
    for node in plan.nodes:
        outcomes = await asyncio.gather(
            *(engine.claim(node.id, f"agent-{index}") for index in range(AGENT_COUNT))
        )

        grants = [outcome for outcome in outcomes if outcome.lease_id is not None]
        measurements.dedup_trials += 1
        if len(grants) == 1:
            measurements.dedup_detected += 1

        await engine.publish(node.id, f"inline:{payload}", "summary of the node")
        measurements.published_artifacts.append(RESULT_TEXT)

        late = await engine.claim(node.id, "agent-late")
        if late.inflight_handle is not None:
            measurements.handle_reuse += 1

    latency_dag = await engine.intent(
        "claim latency",
        200_000,
        [NativeStep(name=f"lat{index}") for index in range(60)],
    )
    for node in engine.plans[latency_dag].nodes:
        started = time.perf_counter()
        await engine.claim(node.id, "agent-latency")
        measurements.claim_samples_ms.append((time.perf_counter() - started) * 1000)

    for _ in range(20):
        started = time.perf_counter()
        projection = await engine.project(dag_id, 1)
        measurements.projection_samples_ms.append((time.perf_counter() - started) * 1000)

    measurements.verify_tokens = projection.token_estimate
    for tier in (0, 1, 2):
        limit = tier_limit(tier)
        rendered = await engine.project(dag_id, tier)
        if limit is not None and rendered.token_estimate > limit:
            measurements.tier_violations += 1

    if engine.router is not None:
        for node in plan.nodes:
            if node.kind == "ambiguous":
                await engine.router.resolve(node, [])
    measurements.llm_calls = client.calls

    replayed = await replay_projection(dag_id, engine.store, engine.journal, 1)
    live = await engine.project(dag_id, 1)
    compare_projections(live, replayed)
    measurements.replay_identical = True

    measurements.substrate_tokens = (
        measurements.spawn_tokens + measurements.verify_tokens * len(plan.nodes)
    )
    return measurements
