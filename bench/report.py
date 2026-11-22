"""Comparative report: the eight acceptance criteria.

Each criterion is measured, compared to its threshold, and rendered as
markdown or JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from bench.naive import naive_tokens
from bench.scenario import Measurements

__all__ = ["BenchReport", "Criterion", "build_report"]


@dataclass(frozen=True, slots=True)
class Criterion:
    """One acceptance criterion and its measurement."""

    name: str
    measured: float
    threshold: float
    passed: bool
    unit: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BenchReport:
    """The whole report."""

    criteria: tuple[Criterion, ...]

    @property
    def passed(self) -> bool:
        """Whether every criterion passed."""
        return all(criterion.passed for criterion in self.criteria)

    def to_markdown(self) -> str:
        """Render the report as a markdown table."""
        lines = [
            "| Criterion | Measured | Threshold | Result |",
            "|---|---|---|---|",
        ]
        for criterion in self.criteria:
            measured = f"{criterion.measured:g} {criterion.unit}".strip()
            threshold = f"{criterion.threshold:g} {criterion.unit}".strip()
            verdict = "PASS" if criterion.passed else "FAIL"
            lines.append(f"| {criterion.name} | {measured} | {threshold} | {verdict} |")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Render the report as JSON."""
        return json.dumps(
            {
                "passed": self.passed,
                "criteria": [
                    {
                        "name": criterion.name,
                        "measured": criterion.measured,
                        "threshold": criterion.threshold,
                        "unit": criterion.unit,
                        "passed": criterion.passed,
                        "detail": criterion.detail,
                    }
                    for criterion in self.criteria
                ],
            },
            indent=2,
            sort_keys=True,
        )


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(1, int(len(ordered) * percentile / 100 + 0.999999)) - 1
    return ordered[index]


def build_report(measurements: Measurements) -> BenchReport:
    """Turn the measurements into the acceptance report."""
    dedup_rate = (
        100.0 * measurements.dedup_detected / measurements.dedup_trials
        if measurements.dedup_trials
        else 0.0
    )
    naive = naive_tokens(measurements.published_artifacts)
    substrate = measurements.substrate_tokens
    saving = 100.0 * (1 - substrate / naive) if naive else 0.0
    p95_claim = _percentile(measurements.claim_samples_ms, 95)
    p95_projection = _percentile(measurements.projection_samples_ms, 95)

    criteria = (
        Criterion(
            "Spawn payload",
            measurements.spawn_tokens,
            500,
            measurements.spawn_tokens <= 500,
            "tokens",
        ),
        Criterion(
            "State verification (tier 1)",
            measurements.verify_tokens,
            300,
            measurements.verify_tokens <= 300,
            "tokens",
        ),
        Criterion(
            "Concurrent dedup detection",
            dedup_rate,
            95,
            dedup_rate >= 95,
            "%",
            f"{measurements.dedup_detected}/{measurements.dedup_trials} nodes ran once",
        ),
        Criterion(
            "Model calls (40 nodes, 6 ambiguous)",
            measurements.llm_calls,
            8,
            measurements.llm_calls <= 8,
            "calls",
        ),
        Criterion(
            "Replay byte-identical",
            1.0 if measurements.replay_identical else 0.0,
            1.0,
            measurements.replay_identical,
            "",
        ),
        Criterion(
            "p95 claim latency",
            p95_claim,
            15,
            p95_claim < 15,
            "ms",
        ),
        Criterion(
            "p95 projection latency",
            p95_projection,
            25,
            p95_projection < 25,
            "ms",
        ),
        Criterion(
            "Tier ceilings respected",
            float(measurements.tier_violations),
            0,
            measurements.tier_violations == 0,
            "violations",
        ),
        Criterion(
            "Token saving against a naive fleet",
            saving,
            85,
            saving >= 85,
            "%",
            f"{substrate} tokens against {naive}",
        ),
    )
    return BenchReport(criteria=criteria)
