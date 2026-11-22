"""Garbage collection of unreferenced blobs.

I2: blobs are reclaimed by reference counting from the live entries. The
reachable set is the live artifacts closed over their parent edges, so a
parent is never collected while a child referencing it is alive.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from kyrx.errors import NotFoundError
from kyrx.store.blobstore import BlobStore
from kyrx.types import Handle

__all__ = ["GCReport", "collect_garbage"]


@dataclass(frozen=True, slots=True)
class GCReport:
    """Outcome of one collection pass."""

    scanned: int
    deleted: int
    freed_bytes: int


def collect_garbage(store: BlobStore, live: Iterable[Handle]) -> GCReport:
    """Delete every blob that no live entry reaches."""
    reachable: set[str] = set()
    pending: list[Handle] = list(live)
    while pending:
        handle = pending.pop()
        if handle in reachable:
            continue
        reachable.add(handle)
        try:
            artifact = store.get_artifact(handle)
        except NotFoundError:
            continue
        pending.extend(artifact.parents)

    scanned = 0
    deleted = 0
    freed = 0
    for handle in list(store.iter_handles()):
        scanned += 1
        if handle in reachable:
            continue
        blob_path, meta_path = store.paths(handle)
        size = blob_path.stat().st_size if blob_path.exists() else 0
        blob_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        deleted += 1
        freed += size
    return GCReport(scanned=scanned, deleted=deleted, freed_bytes=freed)
