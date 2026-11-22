"""Tamper-evident audit log.

Every record carries the hash of the previous one, so a missing or altered
record breaks the chain. The log answers the question a premium product gets
asked first: who did what, and can the record be trusted.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from blake3 import blake3

from kyrx.reduce.projection import canonical_bytes

__all__ = ["AuditLog", "AuditRecord"]

GENESIS = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One recorded action."""

    index: int
    ts: float
    tool: str
    actor: str
    outcome: str
    detail: str
    prev_hash: str
    hash: str


class AuditLog:
    """A hash-chained, append-only record of the calls the server served."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._records: list[AuditRecord] = []
        self._last_hash = GENESIS
        # Calls reach the log from several threads: the chain position, the
        # in-memory append and the disk write must be one atomic step.
        self._lock = threading.Lock()

    @property
    def last_hash(self) -> str:
        """Hash of the last record."""
        return self._last_hash

    def record(
        self,
        *,
        tool: str,
        actor: str,
        outcome: str,
        detail: str = "",
        ts: float = 0.0,
    ) -> AuditRecord:
        """Append a record to the chain.

        Safe to call from several threads: the file keeps every record, in
        chain order.
        """
        with self._lock:
            index = len(self._records)
            payload = self._payload(
                index=index,
                ts=ts,
                tool=tool,
                actor=actor,
                outcome=outcome,
                detail=detail,
                prev=self._last_hash,
            )
            digest = blake3(payload).hexdigest()
            record = AuditRecord(
                index=index,
                ts=ts,
                tool=tool,
                actor=actor,
                outcome=outcome,
                detail=detail,
                prev_hash=self._last_hash,
                hash=digest,
            )
            self._records.append(record)
            self._last_hash = digest
            self._write([record])
            return record

    def attach(self, path: Path) -> None:
        """Start persisting to ``path``, keeping the history already held.

        The records recorded so far are written first, so the file holds one
        unbroken chain from the genesis record.
        """
        with self._lock:
            if self._path is not None:
                return
            self._path = path
            self._write(self._records)

    @property
    def persistent(self) -> bool:
        """Whether the chain is written to disk."""
        return self._path is not None

    @staticmethod
    def _payload(
        *,
        index: int,
        ts: float,
        tool: str,
        actor: str,
        outcome: str,
        detail: str,
        prev: str,
    ) -> bytes:
        return canonical_bytes(
            {
                "index": index,
                "ts": ts,
                "tool": tool,
                "actor": actor,
                "outcome": outcome,
                "detail": detail,
                "prev": prev,
            }
        )

    @classmethod
    def _payload_of(cls, record: AuditRecord) -> bytes:
        return cls._payload(
            index=record.index,
            ts=record.ts,
            tool=record.tool,
            actor=record.actor,
            outcome=record.outcome,
            detail=record.detail,
            prev=record.prev_hash,
        )

    def _write(self, records: list[AuditRecord]) -> None:
        """Append records to the file, when there is one. Caller holds the lock."""
        if self._path is None or not records:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(record.hash + " " + self._payload_of(record).decode("utf-8") + "\n")

    def records(self) -> list[AuditRecord]:
        """Every record, oldest first."""
        return list(self._records)

    def verify(self) -> bool:
        """Whether the chain is intact."""
        previous = GENESIS
        for record in self._records:
            if record.prev_hash != previous:
                return False
            payload = self._payload_of(record)
            if blake3(payload).hexdigest() != record.hash:
                return False
            previous = record.hash
        return True
