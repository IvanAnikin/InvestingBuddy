"""In-process artifact store — V3.1 Slice 1.1.

The default in unit tests. It is a real implementation of the contract, not a
mock: it deduplicates, it enforces the size ceiling, it raises on a genuine
conflict and it returns ``None`` for a missing key. A test that passes against
this store is testing the contract, not its own expectations of a mock.

Not durable, obviously — the dict dies with the process. That is why it is never
the configured backend outside tests.
"""

from __future__ import annotations

import asyncio

from app.services.corpus.artifacts.keys import artifact_key_for, is_valid_artifact_key
from app.services.corpus.artifacts.store import (
    BACKEND_MEMORY,
    DEFAULT_MAX_ARTIFACT_BYTES,
    ArtifactConflictError,
    ArtifactRef,
    ArtifactTooLargeError,
    PutResult,
    content_hash_of,
)


class InMemoryArtifactStore:
    """A dict behind the ``ArtifactStore`` protocol."""

    backend_name = BACKEND_MEMORY

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES) -> None:
        self._objects: dict[str, bytes] = {}
        self._max_bytes = max(1, int(max_bytes))
        # Writes are serialised so a concurrent put of the same document cannot
        # interleave the existence check with the write. The same race is what
        # the conditional write in the other two backends closes.
        self._lock = asyncio.Lock()

    async def put(self, data: bytes, *, media_type: str) -> PutResult:
        if len(data) > self._max_bytes:
            raise ArtifactTooLargeError(
                f"artifact of {len(data)} bytes exceeds the {self._max_bytes}-byte ceiling"
            )
        digest = content_hash_of(data)
        key = artifact_key_for(digest, media_type=media_type)
        ref = ArtifactRef(
            content_hash=digest,
            storage_key=key,
            backend=self.backend_name,
            byte_size=len(data),
            media_type=media_type,
        )
        async with self._lock:
            existing = self._objects.get(key)
            if existing is not None:
                if len(existing) != len(data):
                    raise ArtifactConflictError(
                        "stored artifact disagrees with the bytes being written"
                    )
                return PutResult(ref=ref, created=False, deduplicated=True)
            self._objects[key] = bytes(data)
        return PutResult(ref=ref, created=True, deduplicated=False)

    async def get(self, key: str) -> bytes | None:
        if not is_valid_artifact_key(key):
            return None
        return self._objects.get(key)

    async def exists(self, key: str) -> bool:
        return is_valid_artifact_key(key) and key in self._objects

    async def delete(self, key: str) -> bool:
        if not is_valid_artifact_key(key):
            return False
        async with self._lock:
            return self._objects.pop(key, None) is not None


__all__ = ["InMemoryArtifactStore"]
