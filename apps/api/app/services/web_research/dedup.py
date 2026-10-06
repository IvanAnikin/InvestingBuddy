"""Duplicate detection for web documents — open-web W3 part of spec §14.1.

Three identities, cheapest first:

1. the canonical URL (``fetch.canonical_url`` — tracking parameters removed, a
   same-domain ``rel=canonical`` honoured);
2. the exact ``content_hash`` of the bytes;
3. a 64-bit **SimHash** over normalised main-text shingles: a Hamming distance ≤ 3 means
   the same text re-served (a syndicated article with a different header, a PDF
   re-typeset with the same words).

W3 computes and STORES the fingerprint and a provisional ``origin_key`` (the
publisher's registrable domain). Clustering near-duplicates, choosing the earliest as
the representative and wire/syndication attribution are W4 (spec §14.2).

PostgreSQL has no unsigned 64-bit integer, so the fingerprint is stored as the signed
two's-complement value in a ``BIGINT`` (:func:`to_signed64` / :func:`to_unsigned64`).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

SIMHASH_BITS = 64
NEAR_DUPLICATE_MAX_DISTANCE = 3
SHINGLE_SIZE = 4
#: Bound on the text a fingerprint reads: enough to identify an article or a report.
MAX_FINGERPRINT_CHARS = 200_000

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MASK = (1 << SIMHASH_BITS) - 1


def normalised_tokens(text: str | None) -> list[str]:
    """NFKC, case-folded word tokens (punctuation and spacing ignored)."""
    folded = unicodedata.normalize("NFKC", (text or "")[:MAX_FINGERPRINT_CHARS]).casefold()
    return _TOKEN_RE.findall(folded)


def _hash64(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def simhash64(text: str | None, *, shingle: int = SHINGLE_SIZE) -> int | None:
    """The unsigned 64-bit SimHash of ``text``'s word shingles, or None when empty."""
    tokens = normalised_tokens(text)
    if not tokens:
        return None
    if len(tokens) < shingle:
        grams = [" ".join(tokens)]
    else:
        grams = [" ".join(tokens[i : i + shingle]) for i in range(len(tokens) - shingle + 1)]
    weights = [0] * SIMHASH_BITS
    for gram in grams:
        h = _hash64(gram)
        for bit in range(SIMHASH_BITS):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            value |= 1 << bit
    return value


def to_signed64(value: int | None) -> int | None:
    if value is None:
        return None
    value &= _MASK
    return value - (1 << SIMHASH_BITS) if value >= 1 << (SIMHASH_BITS - 1) else value


def to_unsigned64(value: int | None) -> int | None:
    return None if value is None else value & _MASK


def hamming(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return bin((a ^ b) & _MASK).count("1")


def is_near_duplicate(
    a: int | None, b: int | None, *, max_distance: int = NEAR_DUPLICATE_MAX_DISTANCE
) -> bool:
    distance = hamming(to_unsigned64(a), to_unsigned64(b))
    return distance is not None and distance <= max_distance


def origin_key_for(host: str | None) -> str | None:
    """Provisional origin (spec §14.2 step 6): the publisher's registrable domain."""
    from app.services.sources.public_suffix import registrable_domain

    clean = (host or "").strip().lower().strip(".")
    if not clean:
        return None
    return (registrable_domain(clean) or clean)[:255]


async def near_duplicate_versions(
    session: Any, simhash: int | None, *, limit: int = 2000
) -> list[Any]:
    """Stored web versions whose SimHash is within 3 bits of ``simhash`` (bounded scan)."""
    if simhash is None:
        return []
    from sqlalchemy import select

    from app.models.research_document import ResearchDocumentVersion

    rows = (
        await session.execute(
            select(ResearchDocumentVersion.id, ResearchDocumentVersion.simhash)
            .where(ResearchDocumentVersion.simhash.is_not(None))
            .order_by(ResearchDocumentVersion.created_at.desc())
            .limit(max(1, limit))
        )
    ).all()
    return [row_id for row_id, value in rows if is_near_duplicate(value, simhash)]


__all__ = [
    "NEAR_DUPLICATE_MAX_DISTANCE",
    "hamming",
    "is_near_duplicate",
    "near_duplicate_versions",
    "normalised_tokens",
    "origin_key_for",
    "simhash64",
    "to_signed64",
    "to_unsigned64",
]
