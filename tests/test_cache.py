"""Cache invariants: scoped invalidation and negative proofs."""

from __future__ import annotations

from kyrx.cache.cache import ScopedCache
from kyrx.cache.invalidation import EdgeGenerations, Scope

EDGE_A = "node-a->node-b"
EDGE_B = "node-b->node-c"


def test_an_unrelated_edge_does_not_invalidate() -> None:
    cache = ScopedCache()
    cache.put("key", b"value", Scope(frozenset({EDGE_A})))

    cache.invalidate([EDGE_B])

    assert cache.get("key") == b"value"


def test_an_invalidated_edge_turns_a_hit_into_a_miss() -> None:
    cache = ScopedCache()
    cache.put("key", b"value", Scope(frozenset({EDGE_A})))

    cache.invalidate([EDGE_A])

    assert cache.get("key") is None


def test_a_negative_proof_expires_with_its_edges() -> None:
    cache = ScopedCache()
    cache.prove_absent("dag/d1/main/ghost", Scope(frozenset({EDGE_A})))

    assert cache.is_proven_absent("dag/d1/main/ghost") is True

    cache.invalidate([EDGE_A])

    assert cache.is_proven_absent("dag/d1/main/ghost") is False


def test_the_hit_rate_is_measured() -> None:
    cache = ScopedCache()
    cache.put("key", b"value", Scope(frozenset()))

    cache.get("key")
    cache.get("missing")

    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.hit_rate() == 0.5


def test_generations_only_move_forward() -> None:
    generations = EdgeGenerations()

    assert generations.generation(EDGE_A) == 0
    generations.bump([EDGE_A])
    assert generations.generation(EDGE_A) == 1
    generations.restore({EDGE_A: 5})
    assert generations.generation(EDGE_A) == 5
    generations.restore({EDGE_A: 1})
    assert generations.generation(EDGE_A) == 5


def test_two_scopes_intersect_only_on_a_shared_edge() -> None:
    left = Scope(frozenset({EDGE_A}))
    right = Scope(frozenset({EDGE_A, EDGE_B}))

    assert left.intersects(right) is True
    assert left.intersects(Scope(frozenset({EDGE_B}))) is False
