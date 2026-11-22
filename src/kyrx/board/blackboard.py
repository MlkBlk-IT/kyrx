"""The blackboard: a hierarchical namespace of versioned keys.

Keys look like ``dag/<dagId>/<branch>/<name>``. A write never mutates an
entry: it appends a new version with the next sequence number of the key
(I2), and records the DAG edges the write depends on so that invalidation
stays scoped (I4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import TypeAdapter, ValidationError

from kyrx.board.journal import EntryWrittenEvent, Journal, JournalEvent, NodeStatusEvent
from kyrx.board.sequence import SequenceAllocator
from kyrx.errors import KeyViolationError, NotFoundError
from kyrx.store.blobstore import BlobStore
from kyrx.types import Entry, EntryRef, Handle, Key

__all__ = ["Blackboard"]

_KEY_ADAPTER: TypeAdapter[str] = TypeAdapter(Key)


class Blackboard:
    """Versioned key namespace backed by the append-only journal."""

    def __init__(self, *, store: BlobStore, journal: Journal) -> None:
        self._store = store
        self._journal = journal
        self._sequences = SequenceAllocator()
        self._history: dict[str, list[Entry]] = {}
        self._statuses: dict[str, str] = {}
        self._write_lock = asyncio.Lock()

    @classmethod
    async def replay(cls, *, store: BlobStore, journal: Journal) -> Blackboard:
        """Rebuild a blackboard from the journal."""
        board = cls(store=store, journal=journal)
        for event in await journal.read_all():
            board._apply(event)
        return board
    @property
    def store(self) -> BlobStore:
        return self._store

    async def write(
        self,
        *,
        key: str,
        artifact: Handle,
        invalidation_scope: Sequence[str] = (),
        summary: str = "",
    ) -> Entry:
        """Publish a new version of a key."""
        self._validate_key(key)
        if not self._store.has(artifact):
            raise NotFoundError(f"unknown artifact {artifact}", artifact=artifact)
        async with self._write_lock:
            seq = self._sequences.next(key)
            event = EntryWrittenEvent(
                key=key,
                seq=seq,
                artifact=artifact,
                tombstone=False,
                invalidation_scope=tuple(invalidation_scope),
                summary=summary,
            )
            await self._journal.append(event)
            self._apply(event)
            return self._entries_of(event.key)[-1]

    async def tombstone(self, *, key: str, invalidation_scope: Sequence[str] = ()) -> Entry:
        """Retire a key without publishing an artifact."""
        self._validate_key(key)
        async with self._write_lock:
            seq = self._sequences.next(key)
            event = EntryWrittenEvent(
                key=key,
                seq=seq,
                artifact=None,
                tombstone=True,
                invalidation_scope=tuple(invalidation_scope),
            )
            await self._journal.append(event)
            self._apply(event)
            return self._entries_of(event.key)[-1]

    async def latest(self, key: str) -> Entry | None:
        """Most recent version of a key."""
        versions = self._history.get(key)
        return versions[-1] if versions else None

    async def entry(self, key: str, seq: int) -> Entry | None:
        """One specific version of a key."""
        for entry in self._history.get(key, []):
            if entry.seq == seq:
                return entry
        return None

    async def ref(self, key: str, seq: int | None = None) -> EntryRef | None:
        """Pointer to a version of a key, latest when no sequence is given."""
        entry = await self.latest(key) if seq is None else await self.entry(key, seq)
        if entry is None:
            return None
        return EntryRef(key=entry.key, seq=entry.seq)

    async def keys(self, prefix: str = "") -> list[str]:
        """Keys of the namespace, optionally filtered by prefix."""
        return sorted(key for key in self._history if key.startswith(prefix))

    async def versions(self, key: str) -> list[Entry]:
        """Every version of a key, oldest first."""
        return list(self._history.get(key, []))

    async def versions_prefix(self, prefix: str) -> list[Entry]:
        """Every version of every key under a prefix."""
        found: list[Entry] = []
        for key in sorted(self._history):
            if key.startswith(prefix):
                found.extend(self._history[key])
        return found

    async def set_status(self, *, node_id: str, key: str, status: str) -> None:
        """Record the status of a node, so a replay rebuilds the same state."""
        self._validate_key(key)
        async with self._write_lock:
            await self._journal.append(
                NodeStatusEvent(node_id=node_id, key=key, status=status)
            )
            self._statuses[key] = status

    def statuses_by_key(self) -> dict[str, str]:
        """Latest status of every key that carries one."""
        return dict(self._statuses)

    def _entries_of(self, key: str) -> list[Entry]:
        return self._history.setdefault(key, [])

    async def live_artifacts(self) -> list[Handle]:
        """Artifacts referenced by the live entries, used by the collector."""
        handles: list[Handle] = []
        for versions in self._history.values():
            for entry in versions:
                if not entry.tombstone and entry.artifact is not None:
                    handles.append(entry.artifact)
        return handles

    def _apply(self, event: JournalEvent) -> None:
        if isinstance(event, NodeStatusEvent):
            self._statuses[event.key] = event.status
            return
        entry = Entry(
            key=event.key,
            seq=event.seq,
            artifact=event.artifact,
            tombstone=event.tombstone,
            invalidation_scope=event.invalidation_scope,
        )
        versions = self._history.setdefault(event.key, [])
        versions.append(entry)
        self._sequences.observe(event.key, event.seq)

    @staticmethod
    def _validate_key(key: str) -> None:
        try:
            _KEY_ADAPTER.validate_python(key)
        except ValidationError as exc:
            raise KeyViolationError(f"invalid key {key!r}", key=key, reason=str(exc)) from exc
