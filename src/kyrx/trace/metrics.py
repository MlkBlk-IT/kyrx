"""Per-node metrics: tokens consumed, claim latency, projection latency, hit rate.

The metrics channel is the only place where wall-clock values live: they
never enter a projection, so a replay stays byte-identical (D6).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = ["Metrics"]


@dataclass
class Metrics:
    """Counters and latency distributions."""

    counters: dict[str, int] = field(default_factory=dict)
    timers: dict[str, list[float]] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        """Add to a counter."""
        self.counters[name] = self.counters.get(name, 0) + value

    def observe(self, name: str, seconds: float) -> None:
        """Record a latency sample, in seconds."""
        self.timers.setdefault(name, []).append(seconds)

    def counter(self, name: str) -> int:
        """Read a counter."""
        return self.counters.get(name, 0)

    def samples(self, name: str) -> list[float]:
        """Every latency sample of a timer."""
        return list(self.timers.get(name, []))

    def percentile(self, name: str, percentile: float) -> float:
        """Nearest-rank percentile of a timer, zero when there is no sample."""
        values = sorted(self.timers.get(name, []))
        if not values:
            return 0.0
        rank = max(1, math.ceil(percentile / 100 * len(values)))
        return values[rank - 1]

    def snapshot(self) -> dict[str, object]:
        """A serialisable view of every counter and timer."""
        return {
            "counters": dict(sorted(self.counters.items())),
            "timers": {
                name: {
                    "count": len(values),
                    "p50": self.percentile(name, 50),
                    "p95": self.percentile(name, 95),
                    "max": max(values),
                }
                for name, values in sorted(self.timers.items())
            },
        }

    def reset(self) -> None:
        """Drop every sample."""
        self.counters.clear()
        self.timers.clear()

    def extend(self, names: Iterable[str]) -> None:
        """Declare counters that must exist even at zero."""
        for name in names:
            self.counters.setdefault(name, 0)
