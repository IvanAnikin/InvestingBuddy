"""Duplicate detection for web documents — open-web W3 part of spec §14.1.

Three identities, cheapest first:

1. the canonical URL (``fetch.canonical_url`` — tracking parameters removed, a
   same-domain ``rel=canonical`` honoured);
2. the exact ``content_hash`` of the bytes;
3. a 64-bit **SimHash** over normalised main-text shingles: a Hamming distance ≤ 3 means
   the same text re-served (a syndicated article with a different header, a PDF
   re-typeset with the same words).

W3 computes and STORES the fingerprint. W4 adds the clustering (spec §14.1 step 4):
documents that match by ANY identity are one cluster; the earliest-PUBLISHED member is
the representative (undated members sort after dated ones, then by when they were
stored); the others are LINKED to it, never re-chunked. The cluster's origin is decided
by ``trust.py`` (spec §14.2 step 1).

PostgreSQL has no unsigned 64-bit integer, so the fingerprint is stored as the signed
two's-complement value in a ``BIGINT`` (:func:`to_signed64` / :func:`to_unsigned64`).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
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


# ── Clustering (spec §14.1 step 4) ─────────────────────────────────────── #


@dataclass(frozen=True)
class DedupMember:
    """One document as the clustering sees it. ``key`` is the caller's identity."""

    key: str
    simhash: int | None = None
    content_hash: str | None = None
    canonical_url: str | None = None
    published_at: date | None = None
    #: When the platform stored it — the tie-break among equally dated members.
    stored_at: datetime | None = None


@dataclass
class DuplicateCluster:
    """Members that are the same document; ``representative`` is the one kept."""

    representative: DedupMember
    members: list[DedupMember] = field(default_factory=list)

    @property
    def linked(self) -> list[DedupMember]:
        """The members that are linked to the representative rather than chunked."""
        return [m for m in self.members if m.key != self.representative.key]


def representative_order(member: DedupMember) -> tuple[int, date, datetime, str]:
    """Earliest published first; undated after dated; then earliest stored; then key."""
    return (
        0 if member.published_at is not None else 1,
        member.published_at or date.max,
        member.stored_at or datetime.max,
        member.key,
    )


def same_document(a: DedupMember, b: DedupMember) -> bool:
    """Spec §14.1: canonical URL, exact bytes, or SimHash within 3 bits."""
    if a.canonical_url and a.canonical_url == b.canonical_url:
        return True
    if a.content_hash and a.content_hash == b.content_hash:
        return True
    return is_near_duplicate(a.simhash, b.simhash)


def cluster_near_duplicates(members: Sequence[DedupMember]) -> list[DuplicateCluster]:
    """Group ``members`` into duplicate clusters (union-find; transitive by design).

    Deterministic: clusters come back ordered by their representative, members by the
    same order, so the same input always produces the same clusters.
    """
    items = list(members)
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if same_document(items[i], items[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[DedupMember]] = {}
    for index, member in enumerate(items):
        groups.setdefault(find(index), []).append(member)
    clusters = [
        DuplicateCluster(
            representative=min(group, key=representative_order),
            members=sorted(group, key=representative_order),
        )
        for group in groups.values()
    ]
    return sorted(clusters, key=lambda c: representative_order(c.representative))


async def version_with_hash(session: Any, content_hash: str | None) -> Any:
    """Any stored web version with exactly these bytes (the earliest), or None."""
    if not content_hash:
        return None
    from sqlalchemy import select

    from app.models.research_document import ResearchDocumentVersion

    return (
        await session.execute(
            select(ResearchDocumentVersion)
            .where(
                ResearchDocumentVersion.content_hash == content_hash,
                ResearchDocumentVersion.web_extractor_version.is_not(None),
            )
            .order_by(ResearchDocumentVersion.created_at, ResearchDocumentVersion.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def near_duplicate_versions(
    session: Any, simhash: int | None, *, limit: int = 2000
) -> list[Any]:
    """Stored web versions whose SimHash is within 3 bits of ``simhash`` (bounded scan)."""
    return [row.id for row in await near_duplicate_rows(session, simhash, limit=limit)]


@dataclass(frozen=True)
class StoredDuplicate:
    """A stored web version that is a near-duplicate of a new document."""

    id: Any
    research_document_id: Any
    origin_key: str | None
    published_at: date | None
    created_at: datetime | None
    extracted_document_id: Any = None

    def as_member(self) -> DedupMember:
        return DedupMember(
            key=str(self.id), published_at=self.published_at, stored_at=self.created_at
        )


async def near_duplicate_rows(
    session: Any, simhash: int | None, *, limit: int = 2000
) -> list[StoredDuplicate]:
    """Stored web versions within 3 bits of ``simhash``, earliest-published first.

    A bounded scan of the newest ``limit`` fingerprints (the corpus has no Hamming
    index); a duplicate older than the window is simply not linked — it is stored as
    its own document, which is the pre-W4 behaviour, never a wrong merge.
    """
    if simhash is None:
        return []
    from sqlalchemy import select

    from app.models.research_document import ResearchDocumentVersion as V

    rows = (
        await session.execute(
            select(
                V.id,
                V.research_document_id,
                V.origin_key,
                V.published_at,
                V.created_at,
                V.extracted_document_id,
                V.simhash,
            )
            .where(V.simhash.is_not(None), V.web_extractor_version.is_not(None))
            .order_by(V.created_at.desc())
            .limit(max(1, limit))
        )
    ).all()
    found = [
        StoredDuplicate(
            id=row[0],
            research_document_id=row[1],
            origin_key=row[2],
            published_at=row[3],
            created_at=row[4],
            extracted_document_id=row[5],
        )
        for row in rows
        if is_near_duplicate(row[6], simhash)
    ]
    return sorted(found, key=lambda d: representative_order(d.as_member()))


__all__ = [
    "NEAR_DUPLICATE_MAX_DISTANCE",
    "DedupMember",
    "DuplicateCluster",
    "StoredDuplicate",
    "cluster_near_duplicates",
    "near_duplicate_rows",
    "representative_order",
    "same_document",
    "hamming",
    "is_near_duplicate",
    "near_duplicate_versions",
    "normalised_tokens",
    "origin_key_for",
    "simhash64",
    "to_signed64",
    "to_unsigned64",
]
