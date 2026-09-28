"""The shapes every non-US disclosure source shares, and its closed failure vocabulary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ── Sources (stable ids — never renamed) ───────────────────────────────────── #
SOURCE_UK_FCA_NSM = "uk_fca_nsm"
SOURCE_ASX_ANNOUNCEMENTS = "asx_announcements"

#: Venue codes (``exchange_registry.normalize_exchange``) each source covers.
VENUE_LSE = "LSE"
VENUE_ASX = "AU"

# ── Why a disclosure is not in the corpus — CLOSED ─────────────────────────── #
REASON_CONNECTOR_DISABLED = "connector_disabled"
REASON_VENUE_NOT_COVERED = "venue_not_covered"
#: The official source could not be reached or answered unusably.
REASON_SOURCE_UNAVAILABLE = "source_unavailable"
#: The issuer could not be matched to the source's own identifier, or a fetched
#: document does not name the issuer. Fail closed: nothing is attributed.
REASON_IDENTITY_UNVERIFIED = "identity_unverified"
#: Metadata was found but the document itself could not be fetched.
REASON_PRIMARY_DOCUMENT_UNAVAILABLE = "primary_document_unavailable"
#: The document was fetched but produced no usable text.
REASON_EXTRACTION_FAILED = "extraction_failed"
#: The source lists nothing research-relevant for this issuer in the window.
REASON_DATA_NOT_SOURCED = "data_not_sourced"
#: The run's acquisition budget was spent on higher-ranked documents.
REASON_BUDGET_EXHAUSTED = "acquire_budget_exhausted"
#: Fetched and extracted, but persistence or indexing produced nothing searchable.
REASON_NOT_INDEXED = "no_indexable_content"

REASONS: frozenset[str] = frozenset({
    REASON_CONNECTOR_DISABLED, REASON_VENUE_NOT_COVERED, REASON_SOURCE_UNAVAILABLE,
    REASON_IDENTITY_UNVERIFIED, REASON_PRIMARY_DOCUMENT_UNAVAILABLE,
    REASON_EXTRACTION_FAILED, REASON_DATA_NOT_SOURCED, REASON_BUDGET_EXHAUSTED,
    REASON_NOT_INDEXED,
})

# ── Research value ranks (lower is more useful) ────────────────────────────── #
RANK_ANNUAL = 0
RANK_INTERIM = 1
RANK_PERIODIC = 2
RANK_MATERIAL = 3
RANK_ORDINARY = 4
RANK_ADMINISTRATIVE = 5


@dataclass(frozen=True)
class VerifiedIssuer:
    """An issuer matched to a source's OWN identifier — never by a similar name."""

    company_id: uuid.UUID
    ticker: str
    venue: str
    #: The name the venue's own directory states for this listing.
    name: str
    source_id: str
    #: How identity was established, for the audit trail.
    identity_basis: str
    isin: str | None = None
    lei: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "venue": self.venue, "name": self.name,
            "source_id": self.source_id, "identity_basis": self.identity_basis,
            "isin": self.isin, "lei": self.lei,
        }


@dataclass
class OfficialDocument:
    """One official disclosure as the source's metadata describes it.

    Everything here is METADATA. ``headline`` and ``venue_category`` describe a document;
    they are never its content, and nothing downstream may cite them as evidence of
    what the document says.
    """

    source_id: str
    #: The source's own document id — validated, never a URL.
    document_ref: str
    published_at: datetime | None
    headline: str
    #: The venue's own type label, verbatim (NSM ``type``; "" for the ASX listing).
    venue_category: str
    #: The shared coarse category (``disclosure_events.classify_event``).
    category: str
    #: The corpus document kind (``annual_report`` / ``interim_report`` /
    #: ``results_release`` / ``other``) — only a periodic report is ever more than
    #: ``other``; a technical or project announcement is never an annual report.
    doc_kind: str
    research_rank: int
    price_sensitive: bool | None
    #: ``pdf`` or ``html``.
    media: str
    #: The official address of this disclosure — what a citation opens.
    official_url: str
    #: The address the content is fetched from, when it is known without a request.
    content_url: str | None = None
    pages: int | None = None
    #: When the SOURCE last changed this disclosure (NSM ``last_updated_date``). A
    #: corrected re-filing keeps its address; a holding older than this is stale.
    updated_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def published_on(self) -> date | None:
        return self.published_at.date() if self.published_at else None

    def to_item(self) -> dict[str, Any]:
        """The tool-facing metadata row. Never content.

        The headline is the ISSUER's wording from outside the platform; it is
        neutralised like any external passage before it is stored or shown, because
        the report safety gate matches rating words as substrings ("share buy-back").
        """
        from app.schemas.catalyst import neutralize_forbidden_terms

        return {
            "id": self.document_ref,
            "source_id": self.source_id,
            "form_type": self.venue_category or None,
            "category": self.category,
            "document_kind": self.doc_kind,
            "filing_date": self.published_on.isoformat() if self.published_on else None,
            "headline": neutralize_forbidden_terms(self.headline or ""),
            "price_sensitive": self.price_sensitive,
            "research_rank": self.research_rank,
            "source_url": self.official_url,
        }


@dataclass
class DisclosureListing:
    """What a source said about one issuer, or why it said nothing."""

    issuer: VerifiedIssuer | None
    documents: list[OfficialDocument] = field(default_factory=list)
    reason: str | None = None
    detail: str | None = None
    #: Network requests made to produce this listing (identity + listing).
    requests: int = 0
    #: Records the source returned that were refused (another issuer's LEI, a
    #: superseded version, an unusable link).
    refused: int = 0


@dataclass
class DisclosureEvidenceResult:
    """The acquisition bridge's answer for one document: searchable, or why not."""

    state: str
    document_ref: str
    reason: str | None = None
    chunk_count: int = 0
    fetched: bool = False
    attempted: bool = False
    reused: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "document_ref": self.document_ref, "reason": self.reason,
            "chunk_count": self.chunk_count, "fetched": self.fetched,
            "attempted": self.attempted, "reused": self.reused, "notes": list(self.notes),
        }
