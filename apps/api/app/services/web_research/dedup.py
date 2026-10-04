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
from collections import Counter
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


#: SimHash bands: a Hamming distance <= 3 over 64 bits leaves at least one of four 16-bit
#: bands identical (pigeonhole), so candidates can be bucketed by band instead of compared
#: pairwise (review F5).
_BAND_BITS = 16
_BANDS = SIMHASH_BITS // _BAND_BITS


def _bands(value: int | None) -> list[tuple[int, int]]:
    unsigned = to_unsigned64(value)
    if unsigned is None:
        return []
    mask = (1 << _BAND_BITS) - 1
    return [(i, (unsigned >> (i * _BAND_BITS)) & mask) for i in range(_BANDS)]


def cluster_near_duplicates(members: Sequence[DedupMember]) -> list[DuplicateCluster]:
    """Group ``members`` into duplicate clusters (union-find; transitive by design).

    Candidates are found through the canonical URL, the content hash and the SimHash
    bands, so the cost is linear in the members plus the size of each bucket rather than
    quadratic. Deterministic: clusters come back ordered by their representative, members
    by the same order, so the same input always produces the same clusters.
    """
    items = list(members)
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    buckets: dict[Any, list[int]] = {}
    for index, member in enumerate(items):
        keys: list[Any] = []
        if member.canonical_url:
            keys.append(("url", member.canonical_url))
        if member.content_hash:
            keys.append(("hash", member.content_hash))
        keys.extend(("band", band) for band in _bands(member.simhash))
        for key in keys:
            buckets.setdefault(key, []).append(index)
    for key, bucket in buckets.items():
        for position, i in enumerate(bucket):
            for j in bucket[position + 1:]:
                if find(i) != find(j) and (
                    key[0] != "band" or same_document(items[i], items[j])
                ):
                    union(i, j)
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


# ── Linking at ingest (spec §14.1, review F2/M7) ────────────────────────── #

#: A text shorter than this is never linked by SimHash: three flipped bits are a large
#: fraction of a short text, so "near" proves nothing. Identical bytes still link.
MIN_NEAR_DUPLICATE_TOKENS = 40

#: How much authority a source class carries. A document is never linked to a
#: representative of LOWER authority: an issuer's own release must not be shadowed by a
#: scraper's copy that happened to be stored first.
_AUTHORITY: dict[str, int] = {
    "issuer_filing": 5, "regulatory_filing": 5, "exchange_announcement": 5,
    "government_publication": 4, "regulator_publication": 4, "statistical_agency": 4,
    "specialist_agency": 3, "standards_body": 3, "academic_paper": 3,
    "industry_association": 3, "company_press_release": 3, "investor_presentation": 3,
    "company_web_page": 3, "major_financial_press": 2, "trade_publication": 2,
    "local_press": 2, "research_consultancy": 2, "aggregator": 1, "unknown_web": 1,
}


def authority_of(source_class: str | None) -> int:
    return _AUTHORITY.get(source_class or "", 1)


#: Least to most restrictive (spec §20.1). Linking keeps the STRICTER of the two.
_USE_CONSTRAINT_ORDER: tuple[str, ...] = (
    "public_domain", "open_licence", "commercial_permitted", "private_use_permitted",
    "unknown", "redistribution_prohibited", "requires_licence",
)


def stricter_use_constraint(a: str | None, b: str | None) -> str | None:
    """The more restrictive of two ``use_constraint`` values (an unlisted value wins:
    anything the registry does not know is treated as the most restrictive)."""
    if not a or not b:
        return a or b

    def rank(value: str) -> int:
        return (
            _USE_CONSTRAINT_ORDER.index(value)
            if value in _USE_CONSTRAINT_ORDER
            else len(_USE_CONSTRAINT_ORDER)
        )

    return a if rank(a) >= rank(b) else b


_NUMBER_RE = re.compile(r"\d[\d.,]{0,20}")


def numeric_signature(text: str | None) -> Counter[str]:
    """The multiset of numbers in ``text`` (trailing punctuation stripped).

    Two texts that differ only in WORDS may be re-typeset copies; two that differ in a
    number are different statements — swapping two figures in a 350-word release is a
    Hamming distance of about 3 (review F2), so SimHash alone must never link them.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    tokens = (token.rstrip(".,") for token in _NUMBER_RE.findall(folded))
    return Counter(token for token in tokens if token)


@dataclass(frozen=True)
class StoredDuplicate:
    """A stored web version a new document may be LINKED to."""

    id: Any
    research_document_id: Any
    origin_key: str | None
    created_at: datetime | None
    extracted_document_id: Any = None
    source_class: str | None = None
    use_constraint: str | None = None
    simhash: int | None = None


def _scope_clauses(company_id: Any, theme_key: str | None) -> tuple[Any, ...] | None:
    """The SQL conditions that confine a candidate to the new document's own scope: the
    same company, or the same theme among company-less documents. None = no scope, so no
    candidates (a duplicate in another tenant/theme/run is never linked: review F2)."""
    from sqlalchemy import exists, select

    from app.models.research_document import ResearchDocument as D
    from app.models.research_document import ResearchDocumentSubject as S

    if company_id is not None:
        return (D.company_id == company_id,)
    if theme_key:
        themed = exists(
            select(S.id).where(
                S.research_document_id == D.id,
                S.relation == "theme",
                S.theme_key == theme_key,
            )
        )
        return (D.company_id.is_(None), themed)
    return None


async def near_duplicate_rows(
    session: Any,
    simhash: int | None,
    *,
    company_id: Any = None,
    theme_key: str | None = None,
    limit: int = 200,
) -> list[StoredDuplicate]:
    """Stored web versions of the SAME scope within 3 bits of ``simhash``, in the order
    the PLATFORM stored them (never by a page-declared date: a page can claim any date).

    The SQL prefilter is a band match (one of four 16-bit bands identical), so the scan
    reads only plausible candidates, not every fingerprint ever stored (review M7).
    """
    if simhash is None:
        return []
    clauses = _scope_clauses(company_id, theme_key)
    if clauses is None:
        return []
    from sqlalchemy import literal_column, or_, select

    from app.models.research_document import ResearchDocument as D
    from app.models.research_document import ResearchDocumentVersion as V

    unsigned = to_unsigned64(simhash) or 0
    mask = (1 << _BAND_BITS) - 1
    # Constant integers inline (PostgreSQL has no ``bigint >> bigint``: a shift count is
    # an int4, and a bound parameter would be typed bigint). Both are code constants.
    band_match = or_(
        *(
            V.simhash.op(">>")(literal_column(str(i * _BAND_BITS)))
            .op("&")(literal_column(str(mask)))
            == ((unsigned >> (i * _BAND_BITS)) & mask)
            for i in range(_BANDS)
        )
    )
    rows = (
        await session.execute(
            select(
                V.id, V.research_document_id, V.origin_key, V.created_at,
                V.extracted_document_id, V.source_class, V.use_constraint, V.simhash,
            )
            .join(D, D.id == V.research_document_id)
            .where(
                V.simhash.is_not(None),
                V.web_extractor_version.is_not(None),
                band_match,
                *clauses,
            )
            .order_by(V.created_at, V.id)
            .limit(max(1, limit))
        )
    ).all()
    return [
        StoredDuplicate(
            id=r[0], research_document_id=r[1], origin_key=r[2], created_at=r[3],
            extracted_document_id=r[4], source_class=r[5], use_constraint=r[6], simhash=r[7],
        )
        for r in rows
        if is_near_duplicate(r[7], simhash)
    ]


async def _stored_numbers(session: Any, version_id: Any, *, max_chunks: int = 400) -> Counter[str]:
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk as C

    texts = (
        await session.execute(
            select(C.text)
            .where(C.research_document_version_id == version_id)
            .order_by(C.char_start, C.chunk_id)
            .limit(max_chunks)
        )
    ).scalars().all()
    return numeric_signature("\n".join(texts))


async def find_linkable_duplicate(
    session: Any,
    *,
    simhash: int | None,
    text: str | None,
    source_class: str | None,
    company_id: Any = None,
    theme_key: str | None = None,
) -> StoredDuplicate | None:
    """A stored near-duplicate this document may be LINKED to instead of chunked again.

    All of these must hold (review F2/M7); otherwise the document is stored on its own
    and the two simply coexist:

    * the text is long enough for SimHash to mean something;
    * the candidate is in the same company / theme scope;
    * the candidate carries IDENTICAL numbers (a poisoned copy with two figures swapped
      is a different statement, not a duplicate);
    * the candidate's source class is not of lower authority than the new document's;
    * the earliest-STORED candidate wins, never one chosen by a page-declared date.
    """
    if simhash is None or len(normalised_tokens(text)) < MIN_NEAR_DUPLICATE_TOKENS:
        return None
    wanted = numeric_signature(text)
    new_rank = authority_of(source_class)
    for candidate in await near_duplicate_rows(
        session, simhash, company_id=company_id, theme_key=theme_key
    ):
        if authority_of(candidate.source_class) < new_rank:
            continue
        if await _stored_numbers(session, candidate.id) == wanted:
            return candidate
    return None


async def apply_stricter_use_constraint(
    session: Any, duplicate: StoredDuplicate, use_constraint: str | None
) -> str | None:
    """Linking must not loosen a licence: the representative keeps the stricter of its
    own ``use_constraint`` and the linked document's (review M7). Returns the new value
    when it changed."""
    stricter = stricter_use_constraint(duplicate.use_constraint, use_constraint)
    if stricter is None or stricter == duplicate.use_constraint:
        return None
    from sqlalchemy import update

    from app.models.research_document import ResearchDocumentVersion as V

    await session.execute(
        update(V).where(V.id == duplicate.id).values(use_constraint=stricter)
    )
    return stricter


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


__all__ = [
    "NEAR_DUPLICATE_MAX_DISTANCE",
    "DedupMember",
    "DuplicateCluster",
    "MIN_NEAR_DUPLICATE_TOKENS",
    "StoredDuplicate",
    "apply_stricter_use_constraint",
    "authority_of",
    "cluster_near_duplicates",
    "find_linkable_duplicate",
    "near_duplicate_rows",
    "numeric_signature",
    "stricter_use_constraint",
    "representative_order",
    "same_document",
    "hamming",
    "is_near_duplicate",
    "normalised_tokens",
    "origin_key_for",
    "simhash64",
    "to_signed64",
    "to_unsigned64",
]
