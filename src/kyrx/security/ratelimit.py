"""Rate limiting: a sliding window per actor and per tool."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

__all__ = ["RateLimiter"]


class RateLimiter:
    """Allows a bounded number of calls per window, per key."""

    def __init__(
        self,
        *,
        capacity: int = 120,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Whether a call is allowed, and record it when it is."""
        now = self._clock()
        window = self._hits[key]
        while window and now - window[0] >= self._window_s:
            window.popleft()
        if len(window) >= self._capacity:
            return False
        window.append(now)
        return True

    def remaining(self, key: str) -> int:
        """How many calls are still allowed in the current window."""
        now = self._clock()
        window = self._hits[key]
        while window and now - window[0] >= self._window_s:
            window.popleft()
        return max(0, self._capacity - len(window))
