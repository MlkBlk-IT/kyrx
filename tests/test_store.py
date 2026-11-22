"""Store invariants: content addressing, immutability, deduplication, collection."""

from __future__ import annotations

import pytest

from kyrx.errors import NotFoundError
from kyrx.store.blobstore import BlobStore, compute_handle
from kyrx.store.gc import collect_garbage
from kyrx.types import Producer

PRODUCER = Producer(agent_id="agent-1", node_id="node-1")

EMPTY_VECTOR = "blake3:af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
ABC_VECTOR = "blake3:6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"


@pytest.fixture
def store(tmp_path: object) -> BlobStore:
    return BlobStore(tmp_path / "blobs")  # type: ignore[operator]


def test_handle_matches_the_blake3_vectors() -> None:
    assert compute_handle(b"") == EMPTY_VECTOR
    assert compute_handle(b"abc") == ABC_VECTOR


def test_put_deduplicates_identical_content(store: BlobStore) -> None:
    first = store.put(b"payload", media_type="text/plain", producer=PRODUCER)
    second = store.put(b"payload", media_type="text/plain", producer=PRODUCER)

    assert first.id == second.id
    assert len(list(store.iter_handles())) == 1


def test_stored_content_is_never_mutated(store: BlobStore) -> None:
    artifact = store.put(b"v1", media_type="text/plain", producer=PRODUCER)
    store.put(b"v2", media_type="text/plain", producer=PRODUCER)

    assert store.get(artifact.id) == b"v1"


def test_get_unknown_handle_raises(store: BlobStore) -> None:
    with pytest.raises(NotFoundError):
        store.get("blake3:" + "0" * 64)


def test_artifact_metadata_survives_a_round_trip(store: BlobStore) -> None:
    artifact = store.put(b"payload", media_type="application/json", producer=PRODUCER)

    restored = store.get_artifact(artifact.id)

    assert restored.media_type == "application/json"
    assert restored.producer == PRODUCER
    assert restored.size == len(b"payload")


def test_collector_keeps_live_artifacts_and_their_parents(store: BlobStore) -> None:
    parent = store.put(b"parent", media_type="text/plain", producer=PRODUCER)
    child = store.put(b"child", media_type="text/plain", producer=PRODUCER, parents=(parent.id,))
    orphan = store.put(b"orphan", media_type="text/plain", producer=PRODUCER)

    report = collect_garbage(store, [child.id])

    assert report.deleted == 1
    assert store.has(child.id)
    assert store.has(parent.id)
    assert not store.has(orphan.id)


def test_collector_deletes_nothing_when_everything_is_live(store: BlobStore) -> None:
    artifact = store.put(b"payload", media_type="text/plain", producer=PRODUCER)

    report = collect_garbage(store, [artifact.id])

    assert report.deleted == 0
    assert report.scanned == 1
    assert store.has(artifact.id)
