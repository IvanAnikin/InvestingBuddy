"""Provider interfaces and the canonical result contract — V3.4 Slice 4.1.

Seven categories exist in the strategy document because they change independently.
Four are defined here — ``ModelProvider``, ``SearchProvider``, ``ResearchProvider`` and
``BrowserProvider`` — and the other three arrive with the sources that need them.

Open-web W1 replaced ``SearchProvider`` with the web search contract (spec §8.1: a
``SearchRequest`` in, a ``SearchExecution`` network fact and ``SearchResultItem`` objects out).
The V3.4 query → candidates protocol survives as ``CandidateSearchProvider`` for the
benchmark and the DeepSeek search leg, which is not a web search provider.

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

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Final, Literal, Protocol, runtime_checkable

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
    #: V3.18.3 — what the claimed number measures ("world mine production", "copper
    #: price") and for where. Metadata about the claim, never a verified fact.
    claimed_metric: str | None = None
    claimed_geography: str | None = None

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
    #: Did the provider actually parse a JSON object out of the reply?
    #:
    #: ``payload={}`` is ambiguous on its own: it means *either* the model returned an
    #: empty object *or* nothing parseable came back and the provider substituted one.
    #: Those are different failures — the first is a model that had nothing to say, the
    #: second is a reply the platform could not read — and a caller that cannot tell them
    #: apart records neither. Defaults ``True`` so a provider that never fails to parse
    #: (and every existing construction) is unchanged.
    payload_parsed: bool = True

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
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        timeout: int = 40,
        json_mode: bool = False,
    ) -> ModelResponse:
        ...  # pragma: no cover - protocol


@runtime_checkable
class CandidateSearchProvider(Protocol):
    """LEGACY: query → ranked **source candidates**, as V3.4 defined it.

    Renamed from ``SearchProvider`` in open-web W1. It is kept for the provider benchmark,
    the legacy ``FakeSearchProvider`` and ``DeepSeekSearchProvider``, whose ``search()`` is
    retained for the benchmark only (spec §8.2). It is **not** a web search provider: it
    reports no network fact, so nothing it returns can be labelled ``discovery_mode=
    "search"``. The web search contract is :class:`SearchProvider` below.
    """

    provider_id: str

    async def search(
        self, *, query: str, top_k: int = 10, domains: Sequence[str] | None = None
    ) -> SearchResponse:
        ...  # pragma: no cover - protocol


# ── The web search contract — open-web W1 (spec §8.1, §22.3) ─────────────── #


class QueryFamily(str, Enum):
    """Why a query was issued (spec §4.2). A closed vocabulary, stored on every row."""

    ENTITY = "entity"
    VALUE_CHAIN = "value_chain"
    VENUE = "venue"
    LOCAL_LANG = "local_lang"
    DEMAND = "demand"
    DOCUMENT = "document"
    COMPANY_DOCS = "company_docs"
    CATALYST = "catalyst"
    COMPETITIVE = "competitive"
    INDUSTRY = "industry"
    RISK = "risk"
    GAP = "gap"


#: Who enforced one requested filter. ``client`` means InvestingBuddy checked every
#: returned result itself (whether or not the vendor was also asked), which is the only
#: guarantee a reader can rely on — ADR-055 recorded a vendor that accepted a domain
#: filter and ignored it.
FILTER_BY_PROVIDER: Final = "provider"
FILTER_BY_CLIENT: Final = "client"
FILTER_UNSUPPORTED: Final = "unsupported"
FilterEnforcement = Literal["provider", "client", "unsupported"]

#: What the provider's terms let the platform keep from a response (provider
#: evaluation §4). ``full``: URL, title and snippet. ``url_only``: no title/snippet.
#: ``transient``: nothing is persisted.
RESULT_STORAGE_FULL = "full"
RESULT_STORAGE_URL_ONLY = "url_only"
RESULT_STORAGE_TRANSIENT = "transient"
RESULT_STORAGE_MODES: frozenset[str] = frozenset(
    {RESULT_STORAGE_FULL, RESULT_STORAGE_URL_ONLY, RESULT_STORAGE_TRANSIENT}
)

#: Why a search did not execute. Closed so an admin page can aggregate on it.
SEARCH_ERROR_TIMEOUT = "timeout"
SEARCH_ERROR_HTTP_429 = "http_429"
SEARCH_ERROR_HTTP_5XX = "http_5xx"
SEARCH_ERROR_HTTP_4XX = "http_4xx"
SEARCH_ERROR_HTTP_3XX = "http_3xx"
SEARCH_ERROR_AUTH = "auth"
SEARCH_ERROR_PARSE = "parse"
SEARCH_ERROR_DISABLED = "disabled"
SEARCH_ERROR_NO_KEY = "no_key"
SEARCH_ERROR_TRANSPORT = "transport"
SEARCH_ERROR_TOO_LARGE = "response_too_large"
SEARCH_ERROR_HOST_NOT_ALLOWED = "host_not_allowed"
SEARCH_ERROR_GOVERNANCE = "governance_refused"
SEARCH_ERROR_CREDENTIAL = "credential_in_payload"
SEARCH_ERROR_UNKNOWN_PROVIDER = "unknown_provider"
SEARCH_ERROR_ADAPTER = "adapter_error"


@dataclass(frozen=True)
class SearchCapabilities:
    """What one adapter can do, declared rather than discovered at run time.

    ``provider_filters`` names the filters the vendor ACCEPTS. Whether it honours them is
    a separate question, answered per call in ``SearchExecution.filters_enforced_by``.
    """

    max_results: int = 10
    provider_filters: frozenset[str] = frozenset()
    max_include_domains: int = 0
    max_exclude_domains: int = 0
    result_storage: str = RESULT_STORAGE_TRANSIENT

    def __post_init__(self) -> None:
        if self.result_storage not in RESULT_STORAGE_MODES:
            raise ValueError(f"{self.result_storage!r} is not a result storage mode.")


@dataclass(frozen=True)
class SearchRequest:
    """One sanitised query plus its filters (spec §8.1).

    ``query`` must already have passed ``web_research.queries.sanitise_query``; the
    orchestrator re-validates it before anything leaves the process. ``origin`` and
    ``template_version`` are provenance for the query row and are never sent anywhere.
    """

    query: str
    family: QueryFamily
    max_results: int = 10
    page: int = 1
    date_from: date | None = None
    date_to: date | None = None
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    country: str | None = None
    language: str | None = None
    topic: Literal["general", "news"] = "general"
    origin: str = "template"
    template_version: str | None = None

    def filters(self) -> dict[str, Any]:
        """The requested filters, only those actually set. Stable key order."""
        out: dict[str, Any] = {
            "max_results": int(self.max_results),
            "topic": self.topic,
        }
        if self.page != 1:
            out["page"] = int(self.page)
        if self.date_from is not None or self.date_to is not None:
            out["date_range"] = {
                "from": self.date_from.isoformat() if self.date_from else None,
                "to": self.date_to.isoformat() if self.date_to else None,
            }
        if self.include_domains:
            out["include_domains"] = sorted({d.lower() for d in self.include_domains})
        if self.exclude_domains:
            out["exclude_domains"] = sorted({d.lower() for d in self.exclude_domains})
        if self.country:
            out["country"] = self.country.upper()
        if self.language:
            out["language"] = self.language.lower()
        return out

    def request_hash(self) -> str:
        """Normalised identity of what is asked, for the 24h search cache (spec §18).

        Whitespace and case in the query do not change the question; the filters do.
        ``origin``/``template_version`` are provenance, not part of the question.
        """
        normal = " ".join(self.query.split()).casefold()
        blob = json.dumps(
            {"q": normal, "family": self.family.value, "filters": self.filters()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SearchExecution:
    """The network fact of one search (spec §22.3). Not a claim about the web.

    ``executed`` is True **only** on a 2xx from the provider's search endpoint that
    carried a provider request id and a parseable result list. ``network_call_count``
    counts real HTTP calls to that endpoint — a refusal, a missing key and a cache serve
    are all 0. Model recall can never produce one of these with ``executed=True``: the
    constructor refuses an executed record with no network call behind it.
    """

    provider: str
    executed: bool
    provider_request_id: str | None
    http_status: int | None
    latency_ms: int
    result_count: int
    cost_units: Mapping[str, float] = field(default_factory=dict)
    error_code: str | None = None
    filters_enforced_by: Mapping[str, FilterEnforcement] = field(default_factory=dict)
    network_call_count: int = 0
    #: Results the client-side filters removed after the vendor returned them.
    client_filtered_count: int = 0
    #: Set only by the search cache: the id of the ``web_search_queries`` row whose
    #: network call this execution re-serves. A cache serve made no call of its own.
    cached_from: str | None = None

    def __post_init__(self) -> None:
        if self.executed:
            if self.network_call_count < 1 and not self.cached_from:
                raise ValueError(
                    "executed=True requires a real network call (or, for a cache "
                    "serve, the row of the call it re-serves)"
                )
            if self.http_status is None or not 200 <= self.http_status < 300:
                raise ValueError("executed=True requires a 2xx response")
            if not self.provider_request_id:
                raise ValueError("executed=True requires a provider request id")
            if self.error_code is not None:
                raise ValueError("an executed search carries no error code")
        elif not self.error_code:
            raise ValueError("a search that did not execute must say why (error_code)")

    @classmethod
    def not_executed(
        cls,
        provider: str,
        error_code: str,
        *,
        http_status: int | None = None,
        latency_ms: int = 0,
        network_call_count: int = 0,
        filters_enforced_by: Mapping[str, FilterEnforcement] | None = None,
    ) -> "SearchExecution":
        return cls(
            provider=provider,
            executed=False,
            provider_request_id=None,
            http_status=http_status,
            latency_ms=latency_ms,
            result_count=0,
            cost_units={},
            error_code=error_code,
            filters_enforced_by=dict(filters_enforced_by or {}),
            network_call_count=network_call_count,
        )


@dataclass(frozen=True)
class SearchResultItem:
    """One normalised search hit. A candidate URL, never evidence (spec §8.4).

    ``title`` and ``snippet`` are **untrusted** third-party text: usable for selection
    scoring, entity hints and admin display, never placed in a prompt and never cited.
    ``published_hint`` is the provider's estimate and never authoritative.
    """

    rank: int
    url: str
    canonical_url: str
    domain: str
    title: str | None = None
    snippet: str | None = None
    published_hint: datetime | None = None
    language_hint: str | None = None
    provider_score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def contains_untrusted_content(self) -> bool:
        return bool(self.title or self.snippet)


@runtime_checkable
class SearchProvider(Protocol):
    """The web search contract (spec §8.1). Query in; a network fact and results out.

    Never raises for a provider failure: every failure is a ``SearchExecution`` with
    ``executed=False`` and an ``error_code``. ``DeepSeekSearchProvider`` does not
    implement this and is not selectable as a web search provider (spec §8.2).
    """

    name: str
    capabilities: SearchCapabilities

    async def search(
        self, request: SearchRequest
    ) -> tuple[SearchExecution, list[SearchResultItem]]:
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
    "FILTER_BY_CLIENT",
    "FILTER_BY_PROVIDER",
    "FILTER_UNSUPPORTED",
    "RESULT_STORAGE_FULL",
    "RESULT_STORAGE_MODES",
    "RESULT_STORAGE_TRANSIENT",
    "RESULT_STORAGE_URL_ONLY",
    "BrowserProvider",
    "BrowserResponse",
    "CandidateSearchProvider",
    "CostEstimate",
    "ModelProvider",
    "ModelResponse",
    "QueryFamily",
    "QueryRecord",
    "ResearchLead",
    "ResearchProvider",
    "ResearchProviderResult",
    "SearchCapabilities",
    "SearchExecution",
    "SearchProvider",
    "SearchRequest",
    "SearchResponse",
    "SearchResultItem",
    "SourceCandidate",
    "reject_lead",
]
