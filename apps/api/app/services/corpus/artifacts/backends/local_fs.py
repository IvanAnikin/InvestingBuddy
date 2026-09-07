"""Filesystem artifact store — V3.1 Slice 1.1.

For local development and the opt-in local acceptance run, where the point is to
push a real 100+ page annual report through the durable path without touching
Azure or the deployed environment.

WHY THE PATH CHECKS ARE HERE AT ALL
===================================
Keys are produced by :func:`~app.services.corpus.artifacts.keys.artifact_key_for`
and are pure hex, so path traversal is already unreachable by construction. The
checks below exist because "already unreachable" is a property of today's
callers, and a store that writes to whatever path it is handed is one refactor
away from being a real vulnerability. So the key is validated against the closed
pattern, and the resolved path is re-checked to be inside the root — belt and
braces, in the same spirit as the IP-literal check that sits behind the host
allowlist in ``safe_web_fetcher``.

DURABILITY AND EXACTLY-ONE CREATION
==================================
Writes go to a temporary file in the same directory and are then ``os.link``ed
into position. Two properties follow, and the second is why ``link`` is used
instead of the more obvious ``os.replace``:

* a crash mid-write leaves either the previous state or the complete artifact,
  never a truncated file that would later read as a conflict — the failure mode
  this repository has already seen once, when a truncated extraction was stamped
  current and pinned the cache against its own fix;
* ``link`` fails when the destination exists, so of N concurrent writers of the
  same document exactly one reports ``created`` and the rest report a
  deduplication. ``replace`` would let all N claim they created it, which would
  make the "did I pay for a write?" telemetry silently wrong.

A filesystem that cannot hard-link falls back to ``os.replace``. The bytes are
identical either way — only the created/deduplicated attribution degrades.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.services.corpus.artifacts.keys import artifact_key_for, is_valid_artifact_key
from app.services.corpus.artifacts.store import (
    BACKEND_LOCAL,
    DEFAULT_MAX_ARTIFACT_BYTES,
    ArtifactConflictError,
    ArtifactRef,
    ArtifactStoreError,
    ArtifactTooLargeError,
    PutResult,
    content_hash_of,
)


class LocalFilesystemArtifactStore:
    """Content-addressed artifacts under one root directory."""

    backend_name = BACKEND_LOCAL

    def __init__(self, root: str | Path, *, max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES) -> None:
        self._root = Path(root).expanduser().resolve()
        self._max_bytes = max(1, int(max_bytes))

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path | None:
        """Resolve ``key`` to a path inside the root, or ``None`` if it is not ours."""
        if not is_valid_artifact_key(key):
            return None
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            # Unreachable for a validated key; a symlinked ancestor is the one way
            # it could happen, and refusing is the only safe answer.
            return None
        return candidate

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
        return await asyncio.to_thread(self._put_sync, key, bytes(data), ref)

    def _put_sync(self, key: str, data: bytes, ref: ArtifactRef) -> PutResult:
        path = self._path_for(key)
        if path is None:
            raise ArtifactStoreError("refusing to write outside the artifact root")
        if path.exists():
            if path.stat().st_size != len(data):
                raise ArtifactConflictError(
                    "stored artifact disagrees with the bytes being written"
                )
            return PutResult(ref=ref, created=False, deduplicated=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
        created = True
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Atomic AND exclusive: the loser of a race gets FileExistsError
                # rather than silently replacing an identical file and claiming
                # to have created it.
                os.link(tmp_name, path)
            except FileExistsError:
                created = False
            except OSError:
                # No hard-link support on this filesystem. The bytes still land
                # atomically; only the created/deduplicated attribution degrades.
                os.replace(tmp_name, path)
                tmp_name = ""
        except BaseException:
            _unlink_quietly(tmp_name)
            raise
        else:
            _unlink_quietly(tmp_name)
        if not created and path.stat().st_size != len(data):
            raise ArtifactConflictError(
                "stored artifact disagrees with the bytes being written"
            )
        return PutResult(ref=ref, created=created, deduplicated=not created)

    async def get(self, key: str) -> bytes | None:
        path = self._path_for(key)
        if path is None:
            return None
        return await asyncio.to_thread(self._read_sync, path)

    @staticmethod
    def _read_sync(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:  # a real I/O failure is never a silent miss
            raise ArtifactStoreError(f"artifact read failed: {type(exc).__name__}") from exc

    async def exists(self, key: str) -> bool:
        path = self._path_for(key)
        if path is None:
            return False
        return await asyncio.to_thread(path.is_file)

    async def delete(self, key: str) -> bool:
        path = self._path_for(key)
        if path is None:
            return False
        return await asyncio.to_thread(self._delete_sync, path)

    @staticmethod
    def _delete_sync(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ArtifactStoreError(f"artifact delete failed: {type(exc).__name__}") from exc


def _unlink_quietly(name: str) -> None:
    if not name:
        return
    try:
        os.unlink(name)
    except OSError:
        pass


__all__ = ["LocalFilesystemArtifactStore"]
