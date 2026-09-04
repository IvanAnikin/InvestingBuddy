"""The ``ArtifactStore`` interface — V3.1 Slice 1.1.

ONE INTERFACE, THREE BACKENDS, NO VENDOR SDK ABOVE THIS LINE
============================================================
Research-domain code calls :class:`ArtifactStore`. It never imports
``azure.storage.blob``, never constructs a credential and never knows a container
name. The architecture spec states the rule directly: *the research domain
imports provider interfaces, never provider SDKs* — if swapping a backend
required editing domain code, the abstraction would have failed.

IDEMPOTENCY IS STRUCTURAL, NOT BEST-EFFORT
==========================================
``put`` does not take a key. The key is derived from the bytes
(:mod:`app.services.corpus.artifacts.keys`), so:

* storing the same document twice is the same key and the second call is a
  recorded deduplication rather than a second copy;
* two callers racing on the same document cannot produce two artifacts;
* an artifact can never be overwritten with *different* bytes, because different
  bytes are a different key.

The one case the scheme cannot rule out by construction is a stored object whose
byte length disagrees with what is being written under the same digest — a
truncated earlier write, or a SHA-256 collision. That is a
:class:`ArtifactConflictError` and it fails closed: the platform does not
silently pick one of two disagreeing artifacts. This repository has already paid
for the opposite behaviour once, when a truncated extraction was stamped current
and pinned the cache against its own fix.

WHAT A BACKEND MUST GUARANTEE
=============================
* ``put`` is idempotent and returns whether it created or deduplicated.
* ``get`` returns exactly the stored bytes, or ``None`` when the key is absent.
  A missing artifact is a normal outcome — bytes expire, and lineage outlives
  them — never an exception.
* ``delete`` is idempotent and reports whether anything was removed.
* No method raises for an absent key.
* No method logs a key, a byte, or anything derived from content.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

#: A ceiling every backend applies before it writes anything. The fetch layer
#: already caps a document far below this (``primary_document_max_download_bytes``
#: is 8 MB); this exists so a *bug* upstream cannot push an unbounded blob into
#: storage, not as a business rule.
DEFAULT_MAX_ARTIFACT_BYTES = 32_000_000

BACKEND_MEMORY = "memory"
BACKEND_LOCAL = "local"
BACKEND_AZURE_BLOB = "azure_blob"
#: The honest name for "nothing was stored". Recorded on lineage rows whose bytes
#: were deliberately not retained, so a NULL storage key is never ambiguous
#: between "not retained" and "we forgot".
BACKEND_NONE = "none"


class ArtifactStoreError(Exception):
    """Base class. A store failure is always surfaced, never swallowed."""


class ArtifactConflictError(ArtifactStoreError):
    """Stored bytes disagree with the bytes being written under the same digest."""


class ArtifactTooLargeError(ArtifactStoreError):
    """The payload exceeds the backend's configured ceiling. Nothing was written."""


class ArtifactRef(BaseModel):
    """Where an artifact lives and what it is. Never carries a URL or a secret.

    This is the value that travels back to the ingestion layer, so it is
    deliberately free of anything issuer-identifying: a ref names bytes, and the
    document that *cites* those bytes is a separate record (Slice 1.2).
    """

    model_config = ConfigDict(frozen=True)

    content_hash: str
    storage_key: str
    backend: str
    byte_size: int
    media_type: str


class PutResult(BaseModel):
    """The outcome of one ``put``, with deduplication made explicit.

    ``created`` and ``deduplicated`` are separate booleans rather than one
    enum-ish flag because callers ask two different questions: "did I pay for a
    write?" (telemetry) and "was this document already in the corpus?" (research
    reuse). Exactly one of them is ``True``.
    """

    model_config = ConfigDict(frozen=True)

    ref: ArtifactRef
    created: bool
    deduplicated: bool


class StoredArtifact(BaseModel):
    """What one retention decision produced, flattened onto the lineage columns.

    Carried from the fetch layer (which has the bytes but no database session) to
    the persistence layer (which has the session but deliberately never sees the
    bytes — ``PrimaryDocumentArtifact`` has always refused to carry raw content).
    Splitting it this way keeps that invariant intact: what travels is a hash, a
    key and a policy, none of which can leak a document.

    ``backend == 'none'`` with ``storage_key is None`` is the honest shape for
    "the lineage was recorded and the bytes deliberately were not" — a policy
    refusal, or a store failure that ``failure_code`` names.
    """

    model_config = ConfigDict(frozen=True)

    content_hash: str
    byte_size: int
    media_type: str
    backend: str
    storage_key: str | None = None
    access_class: str
    policy_stored: bool
    policy_indexed: bool
    policy_external_model: bool
    policy_quoted: str
    policy_retained_long_term: bool
    retention_expires_at: datetime | None = None
    #: This call wrote the bytes.
    created: bool = False
    #: The bytes were already stored — the same document, seen again.
    deduplicated: bool = False
    #: A member of :data:`ARTIFACT_FAILURE_CODES` when bytes were NOT stored, else
    #: ``None``. Never a raw exception message: an exception string can embed a
    #: path, an endpoint or a credential.
    failure_code: str | None = None

    @property
    def bytes_retained(self) -> bool:
        return self.storage_key is not None


#: Closed vocabulary for "why are there no bytes". Mirrors the ``ingestion_status``
#: pattern: a member of a known set is safe to persist and to render, where an
#: exception message is neither.
FAILURE_POLICY_DENIED = "artifact_policy_denied"
FAILURE_STORE_UNAVAILABLE = "artifact_store_unavailable"
FAILURE_TOO_LARGE = "artifact_too_large"
FAILURE_CONFLICT = "artifact_conflict"
FAILURE_STORE_ERROR = "artifact_store_error"

ARTIFACT_FAILURE_CODES: frozenset[str] = frozenset(
    {
        FAILURE_POLICY_DENIED,
        FAILURE_STORE_UNAVAILABLE,
        FAILURE_TOO_LARGE,
        FAILURE_CONFLICT,
        FAILURE_STORE_ERROR,
    }
)


@runtime_checkable
class ArtifactStore(Protocol):
    """Persist and retrieve raw bytes by content hash.

    Implementations live in ``app.services.corpus.artifacts.backends``. The
    protocol is ``runtime_checkable`` so a test can assert an adapter satisfies
    it without importing the adapter's dependencies.
    """

    #: One of the ``BACKEND_*`` constants. Recorded on the lineage row so a later
    #: read knows which store to ask.
    backend_name: str

    async def put(self, data: bytes, *, media_type: str) -> PutResult:
        """Store ``data``; return its ref and whether this call created it."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Return the stored bytes, or ``None`` when the key is absent."""
        ...

    async def exists(self, key: str) -> bool:
        """True when ``key`` currently holds bytes."""
        ...

    async def delete(self, key: str) -> bool:
        """Remove ``key``; return whether anything was removed. Idempotent."""
        ...


def content_hash_of(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes — the artifact identity.

    Deliberately the same function ``primary_document_extractor.content_hash_of``
    already applies to a fetched document, so an ``ExtractedDocument.content_hash``
    and its artifact key are the *same* identity rather than two hashes that
    happen to agree today.
    """
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "ARTIFACT_FAILURE_CODES",
    "BACKEND_AZURE_BLOB",
    "BACKEND_LOCAL",
    "BACKEND_MEMORY",
    "BACKEND_NONE",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "FAILURE_CONFLICT",
    "FAILURE_POLICY_DENIED",
    "FAILURE_STORE_ERROR",
    "FAILURE_STORE_UNAVAILABLE",
    "FAILURE_TOO_LARGE",
    "ArtifactConflictError",
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactTooLargeError",
    "PutResult",
    "StoredArtifact",
    "content_hash_of",
]
