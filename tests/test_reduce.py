"""Projection invariants: tiers, ceilings, truncation and determinism."""

from __future__ import annotations

from kyrx.reduce.projection import canonical_bytes, estimate_tokens, project_state, state_handle
from kyrx.reduce.tiers import DEGRADE_BELOW_RATIO, select_tier, tier_limit, tier_of
from kyrx.types import Entry


def _entry(key: str, seq: int = 1) -> Entry:
    return Entry(
        key=key,
        seq=seq,
        artifact="blake3:" + "a" * 64,
        tombstone=False,
    )


def test_the_tier_ceilings_are_the_documented_ones() -> None:
    assert tier_limit(0) == 100
    assert tier_limit(1) == 250
    assert tier_limit(2) == 600
    assert tier_limit(3) is None


def test_a_drained_budget_degrades_to_tier_zero() -> None:
    assert tier_of(10, 1000) == 0
    assert tier_of(int(1000 * (DEGRADE_BELOW_RATIO - 0.01)), 1000) == 0


def test_the_tier_never_rises_on_its_own() -> None:
    assert select_tier(3, 500, 1000) == 1
    assert select_tier(1, 500, 1000) == 1
    assert select_tier(3, 5000, 1000) == 2
    assert select_tier(3, 50_000, 1000) == 3


def test_tier_zero_returns_keys_only() -> None:
    projection = project_state("d1", [_entry("dag/d1/main/a")], 0)

    assert b'"key":"dag/d1/main/a"' in projection.payload
    assert b'"signature"' not in projection.payload


def test_a_projection_never_exceeds_its_ceiling() -> None:
    entries = [_entry(f"dag/d1/main/out-{index}") for index in range(200)]

    projection = project_state("d1", entries, 1)

    assert projection.token_estimate <= 250
    assert projection.truncated > 0
    assert projection.fallback is not None


def test_a_truncated_projection_carries_a_fallback_handle() -> None:
    entries = [_entry(f"dag/d1/main/out-{index}") for index in range(200)]

    projection = project_state("d1", entries, 1)

    assert b'"fallback"' in projection.payload
    assert projection.fallback == state_handle("d1", entries)


def test_the_same_state_produces_the_same_bytes() -> None:
    entries = [_entry("dag/d1/main/a"), _entry("dag/d1/main/b")]

    first = project_state("d1", entries, 1)
    second = project_state("d1", list(reversed(entries)), 1)

    assert first.payload == second.payload


def test_the_projection_carries_no_timestamp() -> None:
    projection = project_state("d1", [_entry("dag/d1/main/a")], 3)

    assert b"created_at" not in projection.payload
    assert b"20" + b"T" not in projection.payload


def test_the_token_estimate_is_about_four_characters_per_token() -> None:
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens(b"a" * 41) == 11


def test_canonical_bytes_are_stable() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
