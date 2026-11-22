"""Pure reducers producing the state projection at tier 0 to 3 (I7).

A projection is a function of the blackboard state alone. It carries no
timestamp and no wall-clock value, so replaying the journal reproduces the
same bytes.

A projection never exceeds the ceiling of its tier. When the state does not
fit, the reducer truncates it and returns the handle of the complete state,
so the caller can fetch the rest explicitly instead of receiving an oversized
payload (§14).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from blake3 import blake3

from kyrx.reduce.tiers import EXCERPT_CHARS, TIER_EXCERPT, TIER_FULL, tier_limit
from kyrx.types import Entry, Handle

__all__ = [
    "Projection",
    "ProjectionEntry",
    "canonical_bytes",
    "estimate_tokens",
    "project_state",
    "state_handle",
]


def canonical_bytes(payload: object) -> bytes:
    """Deterministic serialization: sorted keys, no whitespace."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def estimate_tokens(payload: bytes | str) -> int:
    """Token estimate of a payload, at four characters per token (D5)."""
    if isinstance(payload, bytes):
        length = len(payload.decode("utf-8", errors="replace"))
    else:
        length = len(payload)
    return max(1, -(-length // 4))


def state_handle(dag_id: str, entries: Sequence[Entry]) -> Handle:
    """Content address of the complete state of a DAG.

    Pure: the same state always yields the same handle, whatever produced
    it. The engine stores the matching blob so the handle can be read back.
    """
    payload = canonical_bytes(
        {
            "dagId": dag_id,
            "entries": [
                {"key": entry.key, "seq": entry.seq, "artifact": entry.artifact}
                for entry in sorted(entries, key=lambda item: (item.key, item.seq))
            ],
        }
    )
    return f"blake3:{blake3(payload).hexdigest()}"


def _as_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(data).decode("ascii")


@dataclass(frozen=True, slots=True)
class ProjectionEntry:
    """One key, rendered at the requested tier."""

    key: str
    seq: int
    tombstone: bool
    artifact: Handle | None = None
    signature: str | None = None
    status: str | None = None
    excerpt: str | None = None
    content: str | None = None

    def render(self, tier: int) -> dict[str, object]:
        """The wire shape of this entry at a given tier."""
        if tier == 0:
            return {"key": self.key}
        payload: dict[str, object] = {
            "key": self.key,
            "seq": self.seq,
            "tombstone": self.tombstone,
            "signature": self.signature,
            "status": self.status,
        }
        if tier >= TIER_EXCERPT:
            payload["excerpt"] = self.excerpt
        if tier >= TIER_FULL:
            payload["content"] = self.content
        return payload


@dataclass(frozen=True, slots=True)
class Projection:
    """The state of a DAG, rendered at one tier."""

    dag_id: str
    tier: int
    entries: tuple[ProjectionEntry, ...]
    payload: bytes
    token_estimate: int
    truncated: int = 0
    fallback: Handle | None = None

    @classmethod
    def from_payload(cls, dag_id: str, tier: int, payload: bytes) -> Projection:
        """Rebuild a projection from bytes, the way the cache serves it."""
        envelope = json.loads(payload.decode("utf-8"))
        omitted = int(envelope.get("omitted", 0))
        fallback = envelope.get("fallback")
        return cls(
            dag_id=dag_id,
            tier=tier,
            entries=(),
            payload=payload,
            token_estimate=estimate_tokens(payload),
            truncated=omitted,
            fallback=fallback if isinstance(fallback, str) else None,
        )


def project_state(
    dag_id: str,
    entries: Sequence[Entry],
    tier: int,
    *,
    reader: Callable[[Handle], bytes] | None = None,
    statuses: Mapping[str, str] | None = None,
) -> Projection:
    """Render the state of a DAG at a tier.

    The reader is injected so the reducer itself stays pure: it performs no
    I/O of its own.
    """
    rendered: list[ProjectionEntry] = []
    for entry in sorted(entries, key=lambda item: item.key):
        artifact = entry.artifact
        signature = artifact.removeprefix("blake3:")[:12] if artifact else None
        status = statuses.get(entry.key) if statuses else None
        excerpt: str | None = None
        content: str | None = None
        if tier >= TIER_EXCERPT and artifact is not None and reader is not None:
            text = _as_text(reader(artifact))
            excerpt = text[:EXCERPT_CHARS]
            if tier >= TIER_FULL:
                content = text
        rendered.append(
            ProjectionEntry(
                key=entry.key,
                seq=entry.seq,
                tombstone=entry.tombstone,
                artifact=artifact,
                signature=signature,
                status=status,
                excerpt=excerpt,
                content=content,
            )
        )

    fallback = state_handle(dag_id, entries)
    limit = tier_limit(tier)
    kept = _fit_to_limit(dag_id, rendered, tier, limit, fallback)
    payload = _payload(dag_id, tier, kept, len(rendered) - len(kept), fallback)
    truncated = len(rendered) - len(kept)
    return Projection(
        dag_id=dag_id,
        tier=tier,
        entries=tuple(rendered[: len(kept)]),
        payload=payload,
        token_estimate=estimate_tokens(payload),
        truncated=truncated,
        fallback=fallback if truncated else None,
    )


def _fit_to_limit(
    dag_id: str,
    rendered: list[ProjectionEntry],
    tier: int,
    limit: int | None,
    fallback: Handle,
) -> list[dict[str, object]]:
    """Keep as many entries as the ceiling allows, in one pass."""
    if limit is None:
        return [entry.render(tier) for entry in rendered]
    budget = limit * 4 - 32
    used = len(_payload(dag_id, tier, [], 0, fallback))
    kept: list[dict[str, object]] = []
    for entry in rendered:
        rendered_entry = entry.render(tier)
        size = len(canonical_bytes(rendered_entry)) + 1
        if used + size > budget:
            break
        kept.append(rendered_entry)
        used += size
    while kept:
        payload = _payload(dag_id, tier, kept, len(rendered) - len(kept), fallback)
        if estimate_tokens(payload) <= limit:
            break
        kept.pop()
    return kept


def _payload(
    dag_id: str,
    tier: int,
    kept: list[dict[str, object]],
    truncated: int,
    fallback: Handle,
) -> bytes:
    return canonical_bytes(
        {
            "dagId": dag_id,
            "tier": tier,
            "entries": kept,
            "truncated": truncated > 0,
            "omitted": truncated,
            "fallback": fallback,
        }
    )
