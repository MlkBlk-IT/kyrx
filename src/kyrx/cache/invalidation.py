"""Scoped invalidation (I4, §8).

An edge is written ``producer->consumer``. Each edge carries a generation. A
write bumps the generation of the edges it invalidates; a cache entry records
the generations it was computed under and becomes a miss as soon as one of
them moves. Invalidation is computed by walking edges, never by clearing a
global cache.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

__all__ = ["EdgeGenerations", "Scope"]


@dataclass(frozen=True, slots=True)
class Scope:
    """The set of DAG edges an entry depends on."""

    edges: frozenset[str] = field(default_factory=frozenset)

    def intersects(self, other: Scope) -> bool:
        """Whether two scopes share an edge."""
        return bool(self.edges & other.edges)

    @property
    def empty(self) -> bool:
        return not self.edges


class EdgeGenerations:
    """Tracks how many times each edge has been invalidated."""

    def __init__(self) -> None:
        self._generations: dict[str, int] = {}

    def generation(self, edge: str) -> int:
        """Current generation of an edge."""
        return self._generations.get(edge, 0)

    def bump(self, edges: Iterable[str]) -> int:
        """Invalidate edges and return how many were touched."""
        touched = 0
        for edge in edges:
            self._generations[edge] = self.generation(edge) + 1
            touched += 1
        return touched

    def snapshot(self) -> Mapping[str, int]:
        """Current generations, for inspection and replay."""
        return dict(self._generations)

    def restore(self, generations: Mapping[str, int]) -> None:
        """Adopt generations read back from a journal."""
        for edge, generation in generations.items():
            if generation > self.generation(edge):
                self._generations[edge] = generation
