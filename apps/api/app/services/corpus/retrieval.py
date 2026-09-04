"""The corpus retrieval service — V3.1 Slice 1.6.

The one way anything in InvestingBuddy asks the corpus a question. Everything
above it — the Research Director, a specialist investigator, V3.3's
``search_company_corpus`` tool — calls :func:`search_corpus` and receives results
that are already citable.

WHY THE SIGNATURE IS TYPED PRIMITIVES AND NOT A QUERY OBJECT
============================================================
``search_corpus`` takes strings, UUIDs and dates. It does not take a query
expression, a filter dict, a backend-specific option bag or a raw
``CorpusQuery`` a caller assembled itself.

That matters most once the caller is an agent. A retrieval tool that accepts
free-form query syntax is a tool whose behaviour is decided by whatever text
reached the model — including text that came out of a fetched document, which is
attacker-influenced in the general case. ``docs/v3/SECURITY_DATA_GOVERNANCE_AND_LICENSING.md``
§4 states it as "tool arguments are validated, not interpolated"; this is that
rule at the corpus boundary. There is deliberately nowhere to put a raw
expression, so there is nothing to inject into.

It also keeps the backend swappable in practice rather than in principle. A
caller that could pass backend options would couple to the backend through the
option names, and OPEN DECISION #1 would stop being one adapter's worth of work.

WHAT A RESULT CARRIES
=====================
Everything a citation needs, without a second lookup: the document, the version,
the derivation, the chunk, the page span, the section path, the character offsets,
the canonical URL, the source tier, the period and the scope — plus the scores,
kept per leg so "this matched the metric name exactly" stays distinguishable from
"this is topically similar".

STABLE EVIDENCE IDENTITY
========================
``EvidenceReference.evidence_id`` is the chunk's derived id, so it means the same
thing tomorrow, after a reindex, and in a report written six months from now.
It replaces the run-local ``E1``/``E2`` positional handles for new research —
those handles keep resolving for existing reports, which are not touched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.research_chunk import CHUNK_KIND_TABLE, ResearchDocumentChunk
from app.models.research_derivation import (
    ResearchDocumentPage,
    ResearchDocumentTable,
)
from app.models.research_document import ResearchDocumentVersion
from app.services.corpus.indexing import to_corpus_chunks
from app.services.corpus.search.types import (
    DEFAULT_TOP_K,
    CorpusFilters,
    CorpusHit,
    CorpusQuery,
    SearchMode,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.core.config import Settings
    from app.services.corpus.search.types import SearchBackend

#: Prefix on a stable evidence identifier. Present so a reader can tell one from a
#: run-local ``E1`` handle at a glance, and so a malformed id fails loudly.
EVIDENCE_ID_PREFIX = "ev:"


@dataclass(frozen=True)
class EvidenceReference:
    """Where one retrieved span came from. Complete enough to cite unaided."""

    evidence_id: str
    chunk_id: str
    research_document_id: uuid.UUID | None
    research_document_version_id: uuid.UUID | None
    derivation_id: uuid.UUID | None
    company_id: uuid.UUID | None
    canonical_url: str | None
    title: str | None
    document_type: str | None
    source_tier: str | None
    access_class: str | None
    period_key: str | None
    period_type: str | None
    scope_type: str | None
    scope_name: str | None
    scope_key: str | None
    language: str | None
    page_start: int | None
    page_end: int | None
    section_path: str | None
    table_location: str | None
    char_start: int | None
    char_end: int | None
    published_at: date | None

    @property
    def is_table(self) -> bool:
        return self.table_location is not None

    def citation_label(self) -> str:
        """A short human label. Never claims a page number the document has none of.

        The scope and the period are part of the label rather than an afterthought,
        because "Revenue 32,549" and "Group revenue, FY2025: 32,549" are different
        claims and only the second one is checkable.
        """
        parts: list[str] = [self.title or self.canonical_url or "Source document"]
        if self.table_location:
            parts.append(f"table {self.table_location}")
        elif self.page_start is not None:
            if self.page_end is not None and self.page_end != self.page_start:
                parts.append(f"pp. {self.page_start}-{self.page_end}")
            else:
                parts.append(f"p. {self.page_start}")
        if self.section_path:
            parts.append(self.section_path)
        qualifiers = [
            q
            for q in (
                self.scope_name or (self.scope_type.title() if self.scope_type else None),
                self.period_key,
            )
            if q
        ]
        label = ", ".join(parts)
        return f"{label} ({', '.join(qualifiers)})" if qualifiers else label


@dataclass(frozen=True)
class CorpusSearchResult:
    """One retrieval result: the text, where it came from, and how it scored."""

    reference: EvidenceReference
    text: str
    score: float
    lexical_score: float | None = None
    semantic_score: float | None = None
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceContext:
    """A resolved evidence id, with enough around it to show a human.

    ``page_text`` answers the question the architecture spec poses directly —
    "show me where this number came from" renders the surrounding page — without
    the caller having to know that pages and chunks are different tables.
    """

    reference: EvidenceReference
    text: str
    page_text: str | None = None
    table_rows: list[list[str]] | None = None


def evidence_id_for(chunk_id: str) -> str:
    """The stable evidence identifier for a chunk id."""
    return chunk_id if chunk_id.startswith(EVIDENCE_ID_PREFIX) else EVIDENCE_ID_PREFIX + chunk_id


def chunk_id_from_evidence_id(evidence_id: str) -> str:
    value = (evidence_id or "").strip()
    return value[len(EVIDENCE_ID_PREFIX) :] if value.startswith(EVIDENCE_ID_PREFIX) else value


def _reference_from_hit(hit: CorpusHit) -> EvidenceReference:
    chunk = hit.chunk
    return EvidenceReference(
        evidence_id=evidence_id_for(chunk.chunk_id),
        chunk_id=chunk.chunk_id,
        research_document_id=chunk.research_document_id,
        research_document_version_id=chunk.research_document_version_id,
        derivation_id=chunk.derivation_id,
        company_id=chunk.company_id,
        canonical_url=chunk.canonical_url,
        title=chunk.title,
        document_type=chunk.document_type,
        source_tier=chunk.source_tier,
        access_class=chunk.access_class,
        period_key=chunk.period_key,
        period_type=chunk.period_type,
        scope_type=chunk.scope_type,
        scope_name=chunk.scope_name,
        scope_key=chunk.scope_key,
        language=chunk.language,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section_path=chunk.section_path,
        table_location=chunk.table_location,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        published_at=chunk.published_at,
    )


def _tuple(values: "Sequence[Any] | None") -> tuple[Any, ...]:
    return tuple(values) if values else ()


async def search_corpus(
    session: "Any",
    *,
    backend: "SearchBackend",
    cfg: "Settings",
    query: str,
    company_ids: "Sequence[uuid.UUID] | None" = None,
    document_types: "Sequence[str] | None" = None,
    source_tiers: "Sequence[str] | None" = None,
    period_keys: "Sequence[str] | None" = None,
    period_types: "Sequence[str] | None" = None,
    scope_types: "Sequence[str] | None" = None,
    scope_keys: "Sequence[str] | None" = None,
    languages: "Sequence[str] | None" = None,
    access_classes: "Sequence[str] | None" = None,
    published_from: date | None = None,
    published_to: date | None = None,
    top_k: int = DEFAULT_TOP_K,
    mode: SearchMode = SearchMode.HYBRID,
    embedding: "Sequence[float] | None" = None,
    allow_cross_entity: bool = False,
) -> list[CorpusSearchResult]:
    """Search the corpus and return citable results.

    Every argument is a typed primitive. There is no parameter that accepts a
    backend query expression, a filter dict or an option bag — see the module
    docstring for why that matters once an agent is the caller.

    Raises ``VectorOnlyRetrievalError`` for a semantic-only request and
    ``UnscopedRetrievalError`` for a query that neither names its companies nor
    declares itself cross-entity. Both refusals come from the query contract, so
    they are identical whichever backend is behind this.

    Returns ``[]`` — never an error — when the corpus is disabled. A caller that
    gets no results falls back to whatever it did before the corpus existed.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return []

    corpus_query = CorpusQuery(
        text=query,
        filters=CorpusFilters(
            company_ids=_tuple(company_ids),
            document_types=_tuple(document_types),
            source_tiers=_tuple(source_tiers),
            access_classes=_tuple(access_classes),
            period_keys=_tuple(period_keys),
            period_types=_tuple(period_types),
            scope_types=_tuple(scope_types),
            scope_keys=_tuple(scope_keys),
            languages=_tuple(languages),
            published_from=published_from,
            published_to=published_to,
        ),
        mode=mode,
        top_k=top_k,
        embedding=tuple(embedding) if embedding else None,
        allow_cross_entity=allow_cross_entity,
    ).validate()

    hits = await backend.search(corpus_query)
    return [
        CorpusSearchResult(
            reference=_reference_from_hit(hit),
            text=hit.chunk.text,
            score=hit.score,
            lexical_score=hit.lexical_score,
            semantic_score=hit.semantic_score,
            matched_terms=hit.matched_terms,
        )
        for hit in hits
    ]


async def resolve_evidence(
    session: "Any",
    *,
    evidence_id: str,
    cfg: "Settings",
    include_page: bool = True,
) -> EvidenceContext | None:
    """Reconstruct full provenance for a stable evidence id, from the database.

    This is what makes a citation durable rather than a formatting convention: an
    id recorded in a report months ago resolves back to the exact span, its page,
    and — when it came from a table — the grid itself rather than the flattened
    text it was found by.

    Returns ``None`` when the id is unknown. A citation that no longer resolves is
    a real answer and must not be papered over with an approximation.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return None
    chunk_id = chunk_id_from_evidence_id(evidence_id)
    if not chunk_id:
        return None

    row = (
        await session.execute(
            select(ResearchDocumentChunk)
            .where(ResearchDocumentChunk.chunk_id == chunk_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    version = await session.get(
        ResearchDocumentVersion, row.research_document_version_id
    )
    reference = _reference_from_hit(
        CorpusHit(chunk=to_corpus_chunks([row], version=version)[0], score=0.0)
    )

    page_text: str | None = None
    if include_page and row.page_start is not None:
        page = (
            await session.execute(
                select(ResearchDocumentPage)
                .where(
                    ResearchDocumentPage.derivation_id == row.derivation_id,
                    ResearchDocumentPage.page_number == row.page_start,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        page_text = page.text if page is not None else None

    table_rows: list[list[str]] | None = None
    if row.kind == CHUNK_KIND_TABLE and row.research_document_table_id is not None:
        grid = await session.get(ResearchDocumentTable, row.research_document_table_id)
        # The grid, not the flattened rendering the chunk was found by — the
        # column→period map is the part that makes a figure checkable.
        table_rows = list(grid.rows_json or []) if grid is not None else None

    return EvidenceContext(
        reference=reference,
        text=row.text,
        page_text=page_text,
        table_rows=table_rows,
    )


__all__ = [
    "EVIDENCE_ID_PREFIX",
    "CorpusSearchResult",
    "EvidenceContext",
    "EvidenceReference",
    "chunk_id_from_evidence_id",
    "evidence_id_for",
    "resolve_evidence",
    "search_corpus",
]
