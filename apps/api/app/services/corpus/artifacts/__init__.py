"""Content-addressed raw-artifact persistence — V3.1 Slice 1.1.

The bytes exactly as they were retrieved, keyed by their own SHA-256. Nothing in
this package knows what an issuer, a period or a segment is; it stores and
returns bytes, and the only identity it recognises is the content hash.

That narrowness is the point. ``ExtractedDocument.blob_path`` has existed since
migration 013 and has always been ``NULL`` — a hook nothing ever wrote to and
nothing ever read from. Making it a real retrieval path is what turns
re-extraction from "re-fetch the document and hope it is still online" into a
local, deterministic operation, which the repository's own history says is
needed repeatedly: the extraction pipeline version is at 15.
"""

from app.services.corpus.artifacts.keys import (
    artifact_key_for,
    extension_for_media_type,
    is_valid_artifact_key,
)
from app.services.corpus.artifacts.store import (
    ArtifactConflictError,
    ArtifactRef,
    ArtifactStore,
    ArtifactStoreError,
    ArtifactTooLargeError,
    PutResult,
    content_hash_of,
)

__all__ = [
    "ArtifactConflictError",
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactTooLargeError",
    "PutResult",
    "artifact_key_for",
    "content_hash_of",
    "extension_for_media_type",
    "is_valid_artifact_key",
]
