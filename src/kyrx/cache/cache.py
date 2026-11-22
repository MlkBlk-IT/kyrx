"""Positive and negative cache with scoped invalidation (I4).

A hit whose scope contains an invalidated edge is a miss. A negative proof is
a scoped piece of evidence that a key holds nothing; it expires with the
edges it depends on, never globally.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from kyrx.cache.invalidation import EdgeGenerations, Scope
from kyrx.types import NegativeProof

__all__ = ["ScopedCache"]


class ScopedCache:
    """A cache whose entries carry the edges they depend on."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._generations = EdgeGenerations()
        self._positive: dict[str, tuple[bytes, Scope, dict[str, int]]] = {}
        self._negative: dict[str, tuple[NegativeProof, dict[str, int]]] = {}
        self._clock = clock
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> bytes | None:
        """Read a cached value, or ``None`` when it is absent or stale."""
        entry = self._positive.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, scope, recorded = entry
        if self._is_stale(scope, recorded):
            del self._positive[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: str, value: bytes, scope: Scope) -> None:
        """Store a value under the edges it depends on."""
        self._positive[key] = (value, scope, self._record(scope))

    def prove_absent(self, key: str, scope: Scope) -> NegativeProof:
        """Record scoped evidence that a key holds nothing."""
        proof = NegativeProof(
            key=key,
            scope=tuple(sorted(scope.edges)),
            proven_at=int(self._clock()),
        )
        self._negative[key] = (proof, self._record(scope))
        return proof

    def is_proven_absent(self, key: str) -> bool:
        """Whether a live negative proof exists for a key."""
        entry = self._negative.get(key)
        if entry is None:
            return False
        proof, recorded = entry
        if self._is_stale(Scope(frozenset(proof.scope)), recorded):
            del self._negative[key]
            return False
        return True

    def proof_of(self, key: str) -> NegativeProof | None:
        """The live negative proof of a key."""
        entry = self._negative.get(key)
        return entry[0] if entry is not None else None

    def invalidate(self, edges: Iterable[str]) -> int:
        """Invalidate edges. Entries depending on them become misses."""
        return self._generations.bump(edges)

    @property
    def generations(self) -> EdgeGenerations:
        return self._generations

    def hit_rate(self) -> float:
        """Ratio of hits over total lookups."""
        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total

    def _record(self, scope: Scope) -> dict[str, int]:
        return {edge: self._generations.generation(edge) for edge in scope.edges}

    def _is_stale(self, scope: Scope, recorded: dict[str, int]) -> bool:
        return any(
            self._generations.generation(edge) > recorded.get(edge, 0)
            for edge in scope.edges
        )
