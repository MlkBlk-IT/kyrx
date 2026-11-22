"""Replay: rebuild the state from the journal and compare it byte for byte (I7)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from kyrx.board.blackboard import Blackboard
from kyrx.board.journal import Journal
from kyrx.errors import ReplayMismatchError
from kyrx.reduce.projection import Projection, project_state
from kyrx.store.blobstore import BlobStore

__all__ = ["ReplayLog", "compare_projections", "replay_board", "replay_projection"]


@dataclass(frozen=True, slots=True)
class ReplayLog:
    """The replayable journal of a DAG."""

    dag_id: str
    events: int
    projection: bytes


async def replay_board(store: BlobStore, journal: Journal) -> Blackboard:
    """Rebuild a blackboard from the journal."""
    return await Blackboard.replay(store=store, journal=journal)


async def replay_projection(
    dag_id: str,
    store: BlobStore,
    journal: Journal,
    tier: int,
    *,
    statuses: Mapping[str, str] | None = None,
) -> Projection:
    """Rebuild the projection of a DAG from the journal alone."""
    board = await replay_board(store, journal)
    entries = await board.versions_prefix(f"dag/{dag_id}/")
    return project_state(
        dag_id,
        entries,
        tier,
        reader=store.get,
        statuses=statuses if statuses is not None else board.statuses_by_key(),
    )


def compare_projections(expected: Projection, actual: Projection) -> None:
    """Raise when two projections differ byte for byte."""
    if expected.payload == actual.payload:
        return
    raise ReplayMismatchError(
        "the replayed projection differs from the recorded one",
        dag_id=expected.dag_id,
        tier=expected.tier,
        expected=len(expected.payload),
        actual=len(actual.payload),
    )
