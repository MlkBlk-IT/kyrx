"""Monotone per-key sequence.

I9: every key carries a monotone sequence, and concurrent writes on the same
key are ordered by that sequence, never by the wall clock.
"""

from __future__ import annotations

__all__ = ["SequenceAllocator"]


class SequenceAllocator:
    """Hands out the next sequence number of a key."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, key: str) -> int:
        """Allocate the next sequence number of a key."""
        value = self._counters.get(key, 0) + 1
        self._counters[key] = value
        return value

    def current(self, key: str) -> int:
        """Last sequence number allocated for a key, zero when untouched."""
        return self._counters.get(key, 0)

    def observe(self, key: str, seq: int) -> None:
        """Adopt a sequence number read back from the journal."""
        if seq > self._counters.get(key, 0):
            self._counters[key] = seq
