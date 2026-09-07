"""Provider interfaces and the canonical result contract — V3.4 Slice 4.1.

Seven categories exist in the strategy document because they change independently.
Four are defined here — ``ModelProvider``, ``SearchProvider``, ``ResearchProvider`` and
``BrowserProvider`` — and the other three arrive with the sources that need them.

WHY ABSTRACTION RATHER THAN A GOOD VENDOR
=========================================
Not because vendors are untrustworthy, but because the thing being protected is not the
vendor choice. The entity master, the corpus, period and scope semantics, verification
and the citation lineage are the product. **A vendor that could not be swapped in a week
has become a dependency on someone else's roadmap** — and the capability that justifies
one today is commodity in two quarters.

THE ONE RULE THIS MODULE ENCODES
================================
> External research agents discover and investigate. InvestingBuddy verifies, persists,
> calculates, reconciles, remembers and determines what may enter the canonical
> investment-research record.

So a provider's output is **never** evidence. It is a ``ResearchLead``: attributed,
persisted, and required to survive independent retrieval before it can become a fact.
``ResearchProviderResult`` is the raw output of a *contractor*, and the type says so.

The gate that does this already exists and works — ``entities.claims`` applies exactly
this pattern to identifiers, including the *withheld* case a partial-match source needs.
The rejection-reason vocabulary below is the one
``DATA_AND_EVIDENCE_ARCHITECTURE.md`` §4.2 specifies, and it is closed for the same
reason: a reason invented at a call site is a reason nothing can aggregate on, and
``verification_survival_rate`` per provider is the entire point of keeping rejections.

A SEARCH RESULT IS A SOURCE CANDIDATE, NOT A SOURCE
===================================================
``SearchResult`` deliberately has no ``text`` field beyond a provider-supplied snippet,
and the snippet is labelled untrusted. A URL a search index returned is a *claim that a
page exists*; the platform's own guarded fetcher is what turns it into bytes with a hash,
and only those bytes can be cited.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol, runtime_checkable

from app.services.consumption import UNIT_NAMES, ConsumptionUnits

# ── Provider result status ───────────────────────────────────────────────── #

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_CANCELLED = "cancelled"

PROVIDER_STATUSES: frozenset[str] = frozenset(
    {STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED, STATUS_TIMEOUT, STATUS_CANCELLED}
)

#: A result that did not finish. Kept distinct from ``failed`` because a partial answer
#: from a contractor is still worth verifying, and a timeout is a budgeting fact rather
#: than a provider defect.
INCOMPLETE_STATUSES: frozenset[str] = frozenset(
    {STATUS_PARTIAL, STATUS_FAILED, STATUS_TIMEOUT, STATUS_CANCELLED}
)

# ── Lead lifecycle ──────────────────────────────────────────────────────── #

LEAD_PENDING = "pending"
LEAD_VERIFYING = "verifying"
LEAD_VERIFIED = "verified"
LEAD_REJECTED = "rejected"
LEAD_UNVERIFIABLE = "unverifiable"

LEAD_STATUSES: frozenset[str] = frozenset(
    {LEAD_PENDING, LEAD_VERIFYING, LEAD_VERIFIED, LEAD_REJECTED, LEAD_UNVERIFIABLE}
)

#: The rejection vocabulary from ``DATA_AND_EVIDENCE_ARCHITECTURE.md`` §4.2, closed.
#: A rejected lead is RETAINED with its reason — the "store rejected companies and
#: failed analyses, they are valuable learning data" rule (CLAUDE.md rule 8) applied to
#: provider output, and what makes ``verification_survival_rate`` measurable per vendor.
REJECTED_URL_UNREACHABLE = "url_unreachable"
REJECTED_CLAIM_NOT_IN_SOURCE = "claim_not_in_source"
REJECTED_PERIOD_MISMATCH = "period_mismatch"
REJECTED_SCOPE_MISMATCH = "scope_mismatch"
REJECTED_VALUE_MISMATCH = "value_mismatch"
REJECTED_SOURCE_NOT_PERMITTED = "source_not_permitted"
REJECTED_DUPLICATE = "duplicate"
REJECTED_SUPERSEDED = "superseded"

LEAD_REJECTION_REASONS: frozenset[str] = frozenset(
    {
        REJECTED_URL_UNREACHABLE,
        REJECTED_CLAIM_NOT_IN_SOURCE,
        REJECTED_PERIOD_MISMATCH,
        REJECTED_SCOPE_MISMATCH,
        REJECTED_VALUE_MISMATCH,
        REJECTED_SOURCE_NOT_PERMITTED,
        REJECTED_DUPLICATE,
        REJECTED_SUPERSEDED,
    }
)


@dataclass
class CostEstimate:
    """What a provider call is believed to have cost, and how confidently.

    ``amount_usd`` of ``None`` means **unpriced**, never free. A provider whose price
    this platform has not recorded produces a cost of unknown, and reporting zero would
    make an unpriced vendor look like the cheapest one — which is how a benchmark
    chooses the wrong provider.
    """

    amount_usd: float | None = None
    #: ``price_book`` when derived from recorded prices, ``provider_reported`` when the
    #: vendor returned a figure, ``unknown`` when neither.
    basis: str = "unknown"
    price_book_version: str | None = None

    @property
    def is_priced(self) -> bool:
        return self.amount_usd is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_usd": self.amount_usd,
            "basis": self.basis,
            "price_book_version": self.price_book_version,
            "is_priced": self.is_priced,
        }


@dataclass
class QueryRecord:
    """One query a provider issued on the platform's behalf.

    Kept because provider *behaviour* is data: "it ran forty searches to answer one
    question" is a cost finding, and "it never searched the issuer's own site" is a
    quality finding. Neither is visible from the answer.
    """

    query: str
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result_count: int = 0
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "issued_at": self.issued_at.isoformat(),
            "result_count": self.result_count,
            "note": self.note,
        }


@dataclass
class SourceCandidate:
    """A URL a provider says exists and says is relevant. Not a source.

    The platform's own guarded fetcher turns a candidate into bytes with a hash, and
    only those bytes can be cited. ``SourceCandidate`` is what a search index or a
    managed researcher can honestly produce.
    """

    url: str
    title: str | None = None
    publisher: str | None = None
    published_at: date | None = None
    #: The provider's own relevance score, on the provider's own scale. Never compared
    #: across providers: two vendors' 0.8 are not the same 0.8.
    provider_score: float | None = None
    provider: str = ""
    #: A provider-supplied extract. **Untrusted text** — it came from the open web via a
    #: third party, so it is labelled rather than sanitised.
    snippet: str | None = None

    @property
    def contains_untrusted_content(self) -> bool:
        return self.snippet is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "publisher": self.publisher,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "provider_score": self.provider_score,
            "provider": self.provider,
            "snippet": self.snippet,
            "contains_untrusted_content": self.contains_untrusted_content,
        }


@dataclass
class ResearchLead:
    """A provider's claim, attributed and **not** evidence.

    The promotion path is::

        ResearchLead → source candidate → InvestingBuddy fetch → canonical source
                     → evidence fragment → verified fact

    Each arrow is a gate with a recorded outcome. A lead whose cited URL 404s, or whose
    claimed figure does not appear in the fetched document, is retained as a **rejected
    lead with the reason** rather than deleted.

    ``claimed_*`` naming is deliberate throughout. A field called ``value`` would be read
    as a fact by the next person to touch it; ``claimed_value`` cannot be.
    """

    claim_text: str
    provider: str
    model: str | None = None
    provider_task_id: str | None = None
    lead_id: uuid.UUID = field(default_factory=uuid.uuid4)

    claimed_source_url: str | None = None
    claimed_source_title: str | None = None
    claimed_publisher: str | None = None
    claimed_date: date | None = None
    claimed_value: str | None = None
    claimed_unit: str | None = None
    claimed_currency: str | None = None
    claimed_period: str | None = None
    claimed_scope: str | None = None

    status: str = LEAD_PENDING
    rejection_reason: str | None = None
    rejection_detail: str | None = None
    promoted_evidence_id: str | None = None
    promoted_fact_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_verifiable(self) -> bool:
        """True only when the lead names a source somebody could go and check.

        A claim with no cited URL cannot be verified by construction — it is not
        *wrong*, it is unverifiable, and the two must not be recorded alike.
        """
        return bool((self.claimed_source_url or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_id": str(self.lead_id),
            "claim_text": self.claim_text,
            "provider": self.provider,
            "model": self.model,
            "provider_task_id": self.provider_task_id,
            "claimed_source_url": self.claimed_source_url,
            "claimed_source_title": self.claimed_source_title,
            "claimed_publisher": self.claimed_publisher,
            "claimed_date": self.claimed_date.isoformat() if self.claimed_date else None,
            "claimed_value": self.claimed_value,
            "claimed_unit": self.claimed_unit,
            "claimed_currency": self.claimed_currency,
            "claimed_period": self.claimed_period,
            "claimed_scope": self.claimed_scope,
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "rejection_detail": self.rejection_detail,
            "promoted_evidence_id": self.promoted_evidence_id,
            "promoted_fact_id": self.promoted_fact_id,
            "is_verifiable": self.is_verifiable,
        }


def reject_lead(lead: ResearchLead, reason: str, detail: str = "") -> ResearchLead:
    """Mark a lead rejected, refusing a reason outside the closed vocabulary."""
    if reason not in LEAD_REJECTION_REASONS:
        raise ValueError(
            f"{reason!r} is not a recognised lead rejection reason. A reason invented "
            "at a call site is a reason nothing can aggregate on, and "
            "verification_survival_rate per provider is why rejections are kept. "
            f"Recognised: {', '.join(sorted(LEAD_REJECTION_REASONS))}."
        )
    lead.status = LEAD_REJECTED
    lead.rejection_reason = reason
    lead.rejection_detail = detail or None
    return lead


@dataclass
class ModelResponse:
    """One structured completion, with what it consumed.

    ``consumption`` reports only the units the provider actually measures — a unit it
    does not count must be **absent, not zero**, because a stored zero asserts the thing
    did not happen. Same rule as everywhere else in the platform.
    """

    provider: str
    model: str
    payload: dict[str, Any]
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    instrumented_units: tuple[str, ...] = ()
    cost: CostEstimate = field(default_factory=CostEstimate)
    finish_reason: str | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        for unit in self.instrumented_units:
            if unit not in UNIT_NAMES:
                raise ValueError(f"{unit!r} is not a consumption unit.")


@dataclass
class SearchResponse:
    """Ranked source candidates for one query, plus what the search cost."""

    provider: str
    query: str
    candidates: list[SourceCandidate] = field(default_factory=list)
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    instrumented_units: tuple[str, ...] = ()
    cost: CostEstimate = field(default_factory=CostEstimate)
    warnings: list[str] = field(default_factory=list)
    #: Mirrors ``ModelResponse``. A search has an output ceiling and can be cut off at
    #: it, and a truncated candidate list that reads as exhaustive is a silent claim
    #: that the web holds nothing more.
    finish_reason: str | None = None
    truncated: bool = False
    #: What the provider did, as opposed to what it returned — the same reason
    #: ``ResearchProviderResult`` keeps its own. Added in V3.11.1.2, when DeepSeek's
    #: live contract turned out to expose a *retrieval trace* (which pages it opened,
    #: which opens failed, which queries it ran) and no structured citations at all.
    #: A provider that ignores a domain restriction we asked for is recorded here
    #: rather than in prose, because "it went off-domain" is a number somebody should
    #: be able to watch over time.
    raw_provider_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def contains_untrusted_content(self) -> bool:
        return any(c.contains_untrusted_content for c in self.candidates)


@dataclass
class BrowserResponse:
    """A rendered page. Text, never instructions.

    ``BrowserProvider`` is an **escalation**, not the default crawler: layer 4 of the web
    acquisition ladder, reached only when an official API, the guarded fetcher, a search
    provider and full-page retrieval have all failed.
    """

    provider: str
    url: str
    text: str | None = None
    status_code: int | None = None
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    instrumented_units: tuple[str, ...] = ()
    cost: CostEstimate = field(default_factory=CostEstimate)
    warnings: list[str] = field(default_factory=list)

    @property
    def contains_untrusted_content(self) -> bool:
        # Always. A rendered page is the least trusted input the platform handles.
        return True


@dataclass
class ResearchProviderResult:
    """The raw output of a contractor. **Not a research record.**

    Every claim in it enters as a ``ResearchLead`` and must survive independent
    retrieval and verification. ``raw_provider_metadata`` is kept because provider
    behaviour is itself data — it is what makes a benchmark reproducible six months
    later, when the vendor's defaults have changed and nobody remembers what they were.
    """

    provider: str
    model: str | None
    task_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    research_leads: list[ResearchLead] = field(default_factory=list)
    source_candidates: list[SourceCandidate] = field(default_factory=list)
    cited_urls: list[str] = field(default_factory=list)
    query_log: list[QueryRecord] = field(default_factory=list)
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    instrumented_units: tuple[str, ...] = ()
    cost: CostEstimate = field(default_factory=CostEstimate)
    warnings: list[str] = field(default_factory=list)
    raw_provider_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in PROVIDER_STATUSES:
            raise ValueError(
                f"{self.status!r} is not a recognised provider status. Recognised: "
                f"{', '.join(sorted(PROVIDER_STATUSES))}."
            )

    @property
    def is_complete(self) -> bool:
        return self.status == STATUS_COMPLETED

    @property
    def unverifiable_lead_count(self) -> int:
        """Leads that cite no source. A quality signal about the provider, not the run.

        A provider whose leads mostly cite nothing cannot contribute evidence however
        good its prose is, and this is the number that says so before any verification
        has run.
        """
        return sum(1 for lead in self.research_leads if not lead.is_verifiable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "lead_count": len(self.research_leads),
            "unverifiable_lead_count": self.unverifiable_lead_count,
            "source_candidate_count": len(self.source_candidates),
            "query_count": len(self.query_log),
            "cited_urls": list(self.cited_urls),
            "instrumented_units": list(self.instrumented_units),
            "cost": self.cost.to_dict(),
            "warnings": list(self.warnings),
        }


# ── The four interfaces ──────────────────────────────────────────────────── #


@runtime_checkable
class ModelProvider(Protocol):
    """One prompt in, one structured completion out. **Fetches nothing.**

    Sits above the existing ``LLMClient``, which already has the transient/permanent
    error taxonomy, token accounting and a factory that returns ``None`` rather than
    crashing when a provider is unavailable. V3 adds routing and new vendors behind it;
    it does not replace it.
    """

    provider_id: str
    model: str

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 1200, timeout: int = 40
    ) -> ModelResponse:
        ...  # pragma: no cover - protocol


@runtime_checkable
class SearchProvider(Protocol):
    """Query → ranked **source candidates**. Not bound to one model vendor."""

    provider_id: str

    async def search(
        self, *, query: str, top_k: int = 10, domains: Sequence[str] | None = None
    ) -> SearchResponse:
        ...  # pragma: no cover - protocol


@runtime_checkable
class BrowserProvider(Protocol):
    """Render a JS-gated page. An **escalation**, never the default crawler."""

    provider_id: str

    async def render(self, *, url: str, timeout: int = 30) -> BrowserResponse:
        ...  # pragma: no cover - protocol


@runtime_checkable
class ResearchProvider(Protocol):
    """A managed multi-step investigation. **Must not write to the record.**"""

    provider_id: str

    async def investigate(
        self, *, question: str, context: str | None = None, max_seconds: int = 300
    ) -> ResearchProviderResult:
        ...  # pragma: no cover - protocol


__all__ = [
    "INCOMPLETE_STATUSES",
    "LEAD_PENDING",
    "LEAD_REJECTED",
    "LEAD_REJECTION_REASONS",
    "LEAD_STATUSES",
    "LEAD_UNVERIFIABLE",
    "LEAD_VERIFIED",
    "LEAD_VERIFYING",
    "PROVIDER_STATUSES",
    "REJECTED_CLAIM_NOT_IN_SOURCE",
    "REJECTED_DUPLICATE",
    "REJECTED_PERIOD_MISMATCH",
    "REJECTED_SCOPE_MISMATCH",
    "REJECTED_SOURCE_NOT_PERMITTED",
    "REJECTED_SUPERSEDED",
    "REJECTED_URL_UNREACHABLE",
    "REJECTED_VALUE_MISMATCH",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_TIMEOUT",
    "BrowserProvider",
    "BrowserResponse",
    "CostEstimate",
    "ModelProvider",
    "ModelResponse",
    "QueryRecord",
    "ResearchLead",
    "ResearchProvider",
    "ResearchProviderResult",
    "SearchProvider",
    "SearchResponse",
    "SourceCandidate",
    "reject_lead",
]
