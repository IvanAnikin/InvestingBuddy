"""Corpus search contracts — V3.1 Slice 1.4.

Everything a backend receives and returns, defined by InvestingBuddy and by no
vendor. No module here imports a search SDK, and none ever will: the whole point
of the layer is that swapping Azure AI Search for ``pgvector`` costs one adapter.

WHY A CHUNK CARRIES SO MANY DENORMALIZED FIELDS
===============================================
Because the filters have to be applied *inside* the search, not after it. A
retrieval that returns the top 10 semantic matches and then discards the ones
from the wrong period has returned the top 10 matches from the wrong periods; the
right ten were never retrieved. So entity, period, scope, tier, document type,
language and access class travel with every chunk into the index.

The same fields are what let a hit render a citation without a second lookup,
which is the property Slice 1.6 needs and which stops a retrieval result from
being a foreign key hunt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Protocol, runtime_checkable


class SearchMode(str, Enum):
    """How a search is executed.

    ``SEMANTIC`` exists so a backend can *declare* it and so a hybrid search can
    use it internally. It is deliberately **not** something a caller may ask for
    — see :meth:`CorpusQuery.validate`.
    """

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


SEARCH_MODES: frozenset[SearchMode] = frozenset(SearchMode)

#: The modes a caller is allowed to request.
REQUESTABLE_MODES: frozenset[SearchMode] = frozenset({SearchMode.LEXICAL, SearchMode.HYBRID})

DEFAULT_TOP_K = 10
MAX_TOP_K = 100


class VectorOnlyRetrievalError(ValueError):
    """Raised when a query would be answered by vector similarity alone.

    Named after the rule rather than after the field that violated it, because
    the rule is the thing a reader needs to understand: a semantic match that
    cannot be constrained to a period and a scope is not evidence in this domain.
    """


class UnscopedRetrievalError(ValueError):
    """Raised when a query would search every entity without saying so.

    Cross-entity search is legitimate — "which issuers mention this supplier" is a
    real question — but it must be asked for explicitly, because the accidental
    version of it returns another company's numbers.
    """


@dataclass(frozen=True)
class CorpusChunk:
    """One indexed unit of retrievable text, with its full lineage attached.

    ``chunk_id`` is a STABLE string, not a database row id. A citation made today
    must still resolve after the index is rebuilt, so the identity is derived from
    the document's own coordinates rather than assigned by the index — see Slice
    1.5, which computes it.
    """

    chunk_id: str
    text: str
    # -- lineage: enough to render a citation without a second lookup ---------
    research_document_id: uuid.UUID | None = None
    research_document_version_id: uuid.UUID | None = None
    derivation_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    canonical_url: str | None = None
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    section_path: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    table_location: str | None = None
    # -- the filter keys, denormalized so they apply INSIDE the search --------
    document_type: str | None = None
    source_tier: str | None = None
    access_class: str | None = None
    period_key: str | None = None
    period_type: str | None = None
    scope_type: str | None = None
    scope_name: str | None = None
    scope_key: str | None = None
    language: str | None = None
    published_at: date | None = None
    #: Whether governance permits this text to enter an index at all. A chunk with
    #: ``False`` may be stored and cited but never retrieved by search.
    indexable: bool = True
    #: Present only when a semantic leg is configured. ``None`` is the normal case
    #: and a hybrid search degrades to its lexical leg rather than failing.
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class CorpusFilters:
    """The constraints a query applies. Every one is a first-class argument.

    Empty tuple means "no constraint on this dimension". ``None`` dates mean the
    same. These are deliberately not a free-form dict: a filter the backend does
    not understand must be a type error here rather than a silently ignored key,
    which is how a period constraint stops being applied without anybody noticing.
    """

    company_ids: tuple[uuid.UUID, ...] = ()
    document_types: tuple[str, ...] = ()
    source_tiers: tuple[str, ...] = ()
    access_classes: tuple[str, ...] = ()
    period_keys: tuple[str, ...] = ()
    period_types: tuple[str, ...] = ()
    scope_types: tuple[str, ...] = ()
    scope_keys: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    published_from: date | None = None
    published_to: date | None = None
    #: Governance, on by default: a chunk whose policy forbids indexing is never
    #: returned. Turning it off is not a supported operation — the field exists so
    #: the intent is visible in the type, not so it can be flipped.
    indexable_only: bool = True

    @property
    def is_entity_scoped(self) -> bool:
        return bool(self.company_ids)

    def matches(self, chunk: CorpusChunk) -> bool:
        """Whether ``chunk`` satisfies every constraint. Pure; used by every backend.

        Living here rather than in each adapter means a new filter cannot be
        implemented in one backend and forgotten in another — the failure mode
        where switching backends quietly changes what "FY2025 only" means.
        """
        if self.indexable_only and not chunk.indexable:
            return False
        if self.company_ids and chunk.company_id not in self.company_ids:
            return False
        if self.document_types and chunk.document_type not in self.document_types:
            return False
        if self.source_tiers and chunk.source_tier not in self.source_tiers:
            return False
        if self.access_classes and chunk.access_class not in self.access_classes:
            return False
        if self.period_keys and chunk.period_key not in self.period_keys:
            return False
        if self.period_types and chunk.period_type not in self.period_types:
            return False
        if self.scope_types and chunk.scope_type not in self.scope_types:
            return False
        if self.scope_keys and chunk.scope_key not in self.scope_keys:
            return False
        if self.languages and chunk.language not in self.languages:
            return False
        if self.published_from is not None:
            if chunk.published_at is None or chunk.published_at < self.published_from:
                return False
        if self.published_to is not None:
            if chunk.published_at is None or chunk.published_at > self.published_to:
                return False
        return True


@dataclass(frozen=True)
class CorpusQuery:
    """One retrieval request.

    ``allow_cross_entity`` has to be spelled out because the accidental
    cross-entity search is the one that returns another company's numbers under
    this company's heading. Making it explicit costs one keyword and removes a
    whole class of silent error.
    """

    text: str
    filters: CorpusFilters = field(default_factory=CorpusFilters)
    mode: SearchMode = SearchMode.HYBRID
    top_k: int = DEFAULT_TOP_K
    #: Only consulted for a hybrid search, and optional even then: a hybrid search
    #: with no embedding degrades to its lexical leg, which is a worse answer but
    #: never a wrong one.
    embedding: tuple[float, ...] | None = None
    allow_cross_entity: bool = False

    def validate(self) -> "CorpusQuery":
        """Enforce the domain's retrieval rules. Returns self so it can chain.

        Two rules, both from ``docs/v3/DATA_AND_EVIDENCE_ARCHITECTURE.md`` §2.3:

        * **no vector-only retrieval.** A caller may ask for ``LEXICAL`` or
          ``HYBRID``. ``SEMANTIC`` alone is refused, because "revenue grew
          strongly" is semantically close to every revenue sentence in every
          period and segment, and a hit that cannot be pinned to one is a
          plausible-looking scope error rather than evidence.
        * **no accidental cross-entity search.** Either the query names the
          companies it may return, or it says out loud that it is deliberately
          searching all of them.
        """
        if self.mode not in REQUESTABLE_MODES:
            raise VectorOnlyRetrievalError(
                "semantic-only retrieval is not available: a semantic match that "
                "cannot be constrained by period and scope is not evidence. "
                "Use SearchMode.HYBRID or SearchMode.LEXICAL."
            )
        if not self.filters.is_entity_scoped and not self.allow_cross_entity:
            raise UnscopedRetrievalError(
                "a corpus query must name the companies it may return, or set "
                "allow_cross_entity=True deliberately."
            )
        if not (self.text or "").strip():
            raise ValueError("a corpus query needs query text")
        if self.top_k < 1 or self.top_k > MAX_TOP_K:
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}")
        return self


@dataclass(frozen=True)
class CorpusHit:
    """One result, carrying its chunk and how it was scored.

    The per-leg scores are kept separately from the fused ``score`` because "this
    matched the exact metric name" and "this is topically similar" are different
    claims, and a reader deciding whether to trust a citation wants to know which
    one they are looking at.
    """

    chunk: CorpusChunk
    score: float
    lexical_score: float | None = None
    semantic_score: float | None = None
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexResult:
    """What one indexing call did. Secret-free counts only."""

    indexed: int = 0
    updated: int = 0
    skipped_not_indexable: int = 0


@runtime_checkable
class SearchBackend(Protocol):
    """Index and search corpus chunks. Implemented by adapters, never by domain code.

    ``capabilities`` is how a backend declares what it can actually do, so the
    application can refuse to run a hybrid query against a lexical-only store
    rather than silently returning half the answer.
    """

    name: str
    capabilities: frozenset[SearchMode]

    async def index(self, chunks: "list[CorpusChunk]") -> IndexResult: ...

    async def search(self, query: CorpusQuery) -> "list[CorpusHit]": ...

    async def delete(self, *, research_document_version_id: uuid.UUID) -> int: ...


__all__ = [
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "REQUESTABLE_MODES",
    "SEARCH_MODES",
    "CorpusChunk",
    "CorpusFilters",
    "CorpusHit",
    "CorpusQuery",
    "IndexResult",
    "SearchBackend",
    "SearchMode",
    "UnscopedRetrievalError",
    "VectorOnlyRetrievalError",
]
