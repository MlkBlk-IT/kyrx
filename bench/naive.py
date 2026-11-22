"""Naive baseline: a fleet that exchanges full context in prose.

Each agent reads the whole history before it works. This is the reference the
substrate is measured against.
"""

from __future__ import annotations

from kyrx.reduce.projection import estimate_tokens

__all__ = ["naive_tokens"]


def naive_tokens(artifacts: list[str]) -> int:
    """Tokens a naive fleet spends to produce the same results.

    Agent *i* re-reads every artifact published before it, so an artifact of
    size *t* published at position *j* is paid for by every later agent.
    """
    total = 0
    published = 0
    for index, artifact in enumerate(artifacts):
        size = estimate_tokens(artifact)
        total += size * (len(artifacts) - index)
        published += size
    return total
