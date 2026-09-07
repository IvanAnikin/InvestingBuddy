"""The deterministic artifact key scheme — V3.1 Slice 1.1.

    sha256/<aa>/<bb>/<64-hex-digest>.<ext>

THREE PROPERTIES, EACH DELIBERATE
=================================
**Deterministic.** The key is a pure function of the bytes and the media type.
The same document stored twice produces the same key, which is what makes
``put`` idempotent without a "have I seen this?" round trip and what makes
deduplication a property of the scheme rather than of a lookup table.

**Opaque.** No URL, ticker, issuer name, company id, run id or date appears in a
key. A storage listing therefore discloses nothing about what is being
researched, and no credential-bearing URL fragment can leak into a path or a log
line. This is a governance requirement (§9: secrets never in logs), and it is
cheaper to guarantee structurally than to enforce by review.

**Fanned out.** Two levels of two hex characters give 65,536 prefixes, so a
corpus of tens of thousands of documents never becomes one flat directory —
which matters for the local filesystem backend and costs nothing on Blob.

The extension comes from a CLOSED media-type map. An unrecognised media type
gets ``.bin`` rather than anything derived from the response, so a hostile
``Content-Type`` can never choose the filename suffix on disk.
"""

from __future__ import annotations

import re

#: Closed media-type → extension map. Extensions are cosmetic (the hash is the
#: identity) but a correct one makes a stored artifact openable by hand during an
#: incident, which is when anybody actually looks.
_MEDIA_TYPE_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "txt",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/json": "json",
}

DEFAULT_EXTENSION = "bin"

#: The exact shape :func:`artifact_key_for` produces. Backends validate against
#: this before touching a filesystem or a container: a key is never caller-chosen,
#: so anything that does not match is a bug or an attack, and both fail closed.
ARTIFACT_KEY_RE = re.compile(r"^sha256/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}\.[a-z0-9]{1,8}$")

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def extension_for_media_type(media_type: str | None) -> str:
    """Extension for a media type, from the closed map. Never caller-supplied."""
    base = (media_type or "").split(";")[0].strip().lower()
    return _MEDIA_TYPE_EXTENSIONS.get(base, DEFAULT_EXTENSION)


def artifact_key_for(content_hash: str, *, media_type: str | None = None) -> str:
    """The storage key for a SHA-256 hex digest. Pure; raises on a bad digest.

    Raising rather than coercing is intentional: a caller that reaches here with
    something that is not a SHA-256 hex digest has lost the content identity, and
    storing those bytes under a made-up key would create an artifact that can
    never be found again by hash.
    """
    digest = (content_hash or "").strip().lower()
    if not _HEX64_RE.match(digest):
        raise ValueError("content_hash must be a 64-character SHA-256 hex digest")
    ext = extension_for_media_type(media_type)
    return f"sha256/{digest[0:2]}/{digest[2:4]}/{digest}.{ext}"


def hash_from_artifact_key(key: str) -> str | None:
    """The content hash a key encodes, or ``None`` when the key is not one of ours."""
    if not is_valid_artifact_key(key):
        return None
    return key.rsplit("/", 1)[-1].split(".", 1)[0]


def is_valid_artifact_key(key: str | None) -> bool:
    """True only for a key this module could have produced."""
    return bool(key) and bool(ARTIFACT_KEY_RE.match(key or ""))


__all__ = [
    "ARTIFACT_KEY_RE",
    "DEFAULT_EXTENSION",
    "artifact_key_for",
    "extension_for_media_type",
    "hash_from_artifact_key",
    "is_valid_artifact_key",
]
