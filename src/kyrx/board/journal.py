"""Append-only journal of blackboard entries.

I7: the journal is the source of truth for replay. A projection rebuilt from
the journal must be byte-identical to the original, so the journal records
sequence numbers and never relies on the wall clock to order anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from kyrx.types import Handle, Key

__all__ = ["EntryWrittenEvent", "Journal", "JournalEvent"]


class EntryWrittenEvent(BaseModel):
    """One blackboard write, as recorded in the journal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["entry.written"] = "entry.written"
    key: Key
    seq: int = Field(ge=1)
    artifact: Handle | None = None
    tombstone: bool = False
    invalidation_scope: tuple[str, ...] = ()
    summary: str = ""


class NodeStatusEvent(BaseModel):
    """One node status change, as recorded in the journal.

    The status is journalled next to the key it concerns, so a replay rebuilds
    the same projection bytes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["node.status"] = "node.status"
    node_id: str
    key: Key
    status: str


JournalEvent = Annotated[EntryWrittenEvent | NodeStatusEvent, Field(discriminator="type")]

_EVENT_ADAPTER: TypeAdapter[JournalEvent] = TypeAdapter(JournalEvent)


class Journal:
    """Append-only NDJSON journal."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._handle: TextIO | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def append(self, event: JournalEvent) -> None:
        """Append one event. The append is atomic with respect to other appends."""
        line = event.model_dump_json() + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append_line, line)

    def _append_line(self, line: str) -> None:
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8")
        self._handle.write(line)
        self._handle.flush()

    def close(self) -> None:
        """Close the journal file."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    async def read_all(self) -> list[JournalEvent]:
        """Read every event, in the order they were appended."""
        async with self._lock:
            raw = await asyncio.to_thread(self._read_raw)
        events: list[JournalEvent] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            events.append(_EVENT_ADAPTER.validate_json(line))
        return events

    def _read_raw(self) -> str:
        if self._handle is not None:
            self._handle.flush()
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")
