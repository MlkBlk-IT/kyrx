"""Projection tiers and the budget policy (§9, §11).

A projection has a cost. The tier decides how much of the state crosses the
boundary, and the budget decides the highest tier still allowed. When a node
runs out of budget it degrades to tier 0 and never climbs back inside the same
node.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DEGRADE_BELOW_RATIO",
    "EXCERPT_CHARS",
    "TIER_EXCERPT",
    "TIER_FULL",
    "TIER_LIMITS",
    "select_tier",
    "tier_limit",
    "tier_of",
]

# Tier that carries an excerpt of the content.
TIER_EXCERPT = 2

# Tier that carries the full content.
TIER_FULL = 3

# Token ceiling of each tier. Tier 3 is not capped.
TIER_LIMITS: Final[dict[int, int]] = {0: 100, 1: 250, 2: 600}

# Below this share of the budget remaining, the projection degrades.
DEGRADE_BELOW_RATIO: Final[float] = 0.2

# Tier 3 carries the full content, so it needs the most room.
FULL_CONTENT_THRESHOLD: Final[int] = 10_000

# Characters kept in a tier 2 excerpt.
EXCERPT_CHARS: Final[int] = 200


def tier_limit(tier: int) -> int | None:
    """Token ceiling of a tier, ``None`` when the tier is uncapped."""
    return TIER_LIMITS.get(tier)


def tier_of(remaining_tokens: int, budget_tokens: int) -> int:
    """Highest tier the remaining budget allows."""
    if budget_tokens <= 0:
        return 0
    if remaining_tokens < budget_tokens * DEGRADE_BELOW_RATIO:
        return 0
    if remaining_tokens < TIER_LIMITS[TIER_EXCERPT]:
        return 1
    if remaining_tokens < FULL_CONTENT_THRESHOLD:
        return 2
    return 3


def select_tier(requested: int, remaining_tokens: int, budget_tokens: int) -> int:
    """Clamp a requested tier to what the budget allows.

    The tier never rises on its own: a node that asked for tier 1 and lost its
    budget stays at tier 0 for the rest of its life.
    """
    allowed = tier_of(remaining_tokens, budget_tokens)
    return max(0, min(requested, allowed))
