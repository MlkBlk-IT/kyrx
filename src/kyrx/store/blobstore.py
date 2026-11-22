"""Content-addressed blob store.

I2: an artifact is named by the hash of its content and is never modified.
Writing the same bytes twice yields the same handle and keeps one blob.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from blake3 import blake3

from kyrx.errors import NotFoundError
from kyrx.types import Artifact, Handle, Producer

__all__ = ["BlobStore", "compute_handle"]


def compute_handle(data: bytes) -> Handle:
    """Content address of a payload."""
    return f"blake3:{blake3(data).hexdigest()}"


class BlobStore:
    """Immutable blobs on disk, addressed by content."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def paths(self, handle: Handle) -> tuple[Path, Path]:
        """Blob and metadata paths of an artifact."""
        digest = handle.removeprefix("blake3:")
        folder = self._root / digest[:2]
        return folder / f"{digest}.bin", folder / f"{digest}.meta.json"

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        producer: Producer,
        parents: tuple[Handle, ...] = (),
    ) -> Artifact:
        """Store a payload and return its artifact. Existing content is kept as is."""
        handle = compute_handle(data)
        artifact = Artifact(
            id=handle,
            media_type=media_type,
            size=len(data),
            producer=producer,
            parents=parents,
            created_at=datetime.now(UTC).isoformat(),
        )
        blob_path, meta_path = self.paths(handle)
        if blob_path.exists():
            return artifact
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        staging = blob_path.with_suffix(".tmp")
        staging.write_bytes(data)
        staging.replace(blob_path)
        meta_path.write_text(artifact.model_dump_json(), encoding="utf-8")
        return artifact

    def get(self, handle: Handle) -> bytes:
        """Read back the bytes of an artifact."""
        blob_path, _ = self.paths(handle)
        if not blob_path.exists():
            raise NotFoundError(f"unknown artifact {handle}", artifact=handle)
        return blob_path.read_bytes()

    def get_artifact(self, handle: Handle) -> Artifact:
        """Read back the metadata of an artifact."""
        _, meta_path = self.paths(handle)
        if not meta_path.exists():
            raise NotFoundError(f"unknown artifact {handle}", artifact=handle)
        return Artifact.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def has(self, handle: Handle) -> bool:
        """Whether an artifact is stored."""
        blob_path, _ = self.paths(handle)
        return blob_path.exists()

    def iter_handles(self) -> Iterator[Handle]:
        """Every handle currently on disk."""
        for path in sorted(self._root.glob("*/*.bin")):
            yield f"blake3:{path.stem}"
