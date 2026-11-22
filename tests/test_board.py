"""Blackboard invariants: monotone sequences, total order, replay."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kyrx.board.blackboard import Blackboard
from kyrx.board.journal import Journal
from kyrx.errors import KeyViolationError, NotFoundError
from kyrx.store.blobstore import BlobStore
from kyrx.types import Producer

PRODUCER = Producer(agent_id="agent-1", node_id="node-1")
KEY = "dag/1/main/a"


@pytest.fixture
def store(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path / "blobs")


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.ndjson")


@pytest.fixture
def board(store: BlobStore, journal: Journal) -> Blackboard:
    return Blackboard(store=store, journal=journal)


def _handle(store: BlobStore, payload: bytes = b"payload") -> str:
    return store.put(payload, media_type="text/plain", producer=PRODUCER).id


async def test_sequence_is_monotone_per_key(board: Blackboard, store: BlobStore) -> None:
    handle = _handle(store)

    first = await board.write(key=KEY, artifact=handle)
    second = await board.write(key=KEY, artifact=handle)

    assert (first.seq, second.seq) == (1, 2)


async def test_sequences_are_independent_per_key(
    board: Blackboard, store: BlobStore
) -> None:
    handle = _handle(store)

    await board.write(key=KEY, artifact=handle)
    other = await board.write(key="dag/1/main/b", artifact=handle)

    assert other.seq == 1


async def test_concurrent_writes_on_one_key_get_distinct_sequences(
    board: Blackboard, store: BlobStore
) -> None:
    handle = _handle(store)

    entries = await asyncio.gather(*(board.write(key=KEY, artifact=handle) for _ in range(50)))

    assert sorted(entry.seq for entry in entries) == list(range(1, 51))


async def test_tombstone_carries_no_artifact(board: Blackboard) -> None:
    entry = await board.tombstone(key="dag/1/main/gone")

    assert entry.tombstone
    assert entry.artifact is None


async def test_invalid_key_is_rejected(board: Blackboard) -> None:
    with pytest.raises(KeyViolationError):
        await board.tombstone(key="dag/1/Bad Key")


async def test_write_with_an_unknown_artifact_raises(board: Blackboard) -> None:
    with pytest.raises(NotFoundError):
        await board.write(key=KEY, artifact="blake3:" + "0" * 64)


async def test_replay_rebuilds_the_same_state(
    board: Blackboard, store: BlobStore, journal: Journal
) -> None:
    handle = _handle(store)
    await board.write(key=KEY, artifact=handle)
    await board.tombstone(key="dag/1/main/gone")
    await board.write(key=KEY, artifact=handle)

    replayed = await Blackboard.replay(store=store, journal=journal)

    assert await replayed.keys() == await board.keys()
    assert await replayed.latest(KEY) == await board.latest(KEY)
    assert await replayed.versions(KEY) == await board.versions(KEY)


async def test_journal_is_append_only(
    board: Blackboard, store: BlobStore, journal: Journal
) -> None:
    handle = _handle(store)
    await board.write(key=KEY, artifact=handle)
    await board.write(key=KEY, artifact=handle)

    lines = journal.path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert all(line.startswith('{"type":"entry.written"') for line in lines)


async def test_live_artifacts_exclude_tombstones(
    board: Blackboard, store: BlobStore
) -> None:
    handle = _handle(store)
    await board.write(key=KEY, artifact=handle)
    await board.tombstone(key="dag/1/main/gone")

    assert await board.live_artifacts() == [handle]
