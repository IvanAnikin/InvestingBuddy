"""
Phase 24 — News + Catalyst Discovery contracts.

Source-backed catalyst data model for internal research. Every catalyst has:
  - a real underlying source (SEC filing, company press release, news item)
    that keeps its true source tier, and
  - a model-derived interpretation (category / direction / strength) that is
    ALWAYS tagged as T6_model_estimate.

STRICT PROHIBITION (enforced by the safety gate and by construction here):
  - No BUY / SELL / HOLD / WATCH signals are ever produced from catalysts.
  - No price targets, fair values, upside/downside, or "undervalued/overvalued".
  - A positive catalyst is NOT a recommendation. A negative catalyst is NOT a
    recommendation. Catalyst labels are internal, model-derived, and require
    human review before any use.

Source tier nuance (Phase 24):
  The project's SourceTier enum only defines ``T1_primary_filing`` — there is no
  separate ``T1_primary_company_source``. Company-owned primary sources (an
  issuer's own newsroom / investor-relations feed) therefore use
  ``T1_primary_filing`` and are documented as "company-owned primary source".
  SEC EDGAR filing events use ``T2_regulator_or_gov``. Aggregator / search news
  APIs use ``T5_api_aggregator``. The model-derived catalyst label is always
  ``T6_model_estimate``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.integrations.financial_data_provider import SourceTier

# ---------------------------------------------------------------------------
# Model-derived catalyst label tier (always T6)
# ---------------------------------------------------------------------------

MODEL_LABEL_TIER: str = SourceTier.T6_model_estimate.value


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CatalystCategory(str, Enum):
    earnings = "earnings"
    guidance = "guidance"
    product = "product"
    contract = "contract"
    customer = "customer"
    partnership = "partnership"
    regulatory = "regulatory"
    litigation = "litigation"
    management = "management"
    financing = "financing"
    capital_return = "capital_return"
    mna = "mna"
    operations = "operations"
    macro_sector = "macro_sector"
    filing_event = "filing_event"
    risk_event = "risk_event"
    other = "other"


class CatalystDirection(str, Enum):
    positive = "positive"
    negative = "negative"
    mixed = "mixed"
    neutral = "neutral"
    unknown = "unknown"


class CatalystStrength(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class EvidenceStrength(str, Enum):
    primary_confirmed = "primary_confirmed"
    regulator_confirmed = "regulator_confirmed"
    multi_source_confirmed = "multi_source_confirmed"
    single_source_reported = "single_source_reported"
    aggregator_only = "aggregator_only"
    model_inferred = "model_inferred"
    insufficient = "insufficient"


class CatalystCoverageStatus(str, Enum):
    none_found = "none_found"
    filings_only = "filings_only"
    limited = "limited"
    adequate = "adequate"
    strong = "strong"
    stale = "stale"
    provider_unavailable = "provider_unavailable"


# ---------------------------------------------------------------------------
# Forbidden-term neutralisation for external free text
# ---------------------------------------------------------------------------

# External headlines/snippets (from news aggregators) can contain recommendation
# language ("analyst says buy", "sell rating", "buyback"). Such text must never
# reach a report artifact that the safety gate scans. We neutralise it here at
# the point it is serialised into any report content. This is a safety control,
# not detection evasion — the goal is that InvestingBuddy never surfaces
# recommendation language, even second-hand from a third party.
_COMPOUND_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"buy[\s-]?back", re.IGNORECASE), "share repurchase"),
    (re.compile(r"price target", re.IGNORECASE), "analyst estimate [redacted]"),
    (re.compile(r"target price", re.IGNORECASE), "analyst estimate [redacted]"),
    (re.compile(r"fair value", re.IGNORECASE), "valuation figure [redacted]"),
    (re.compile(r"intrinsic value", re.IGNORECASE), "valuation figure [redacted]"),
    (re.compile(r"upside", re.IGNORECASE), "[redacted]"),
    (re.compile(r"downside", re.IGNORECASE), "[redacted]"),
    (re.compile(r"under\s?valued", re.IGNORECASE), "[redacted]"),
    (re.compile(r"over\s?valued", re.IGNORECASE), "[redacted]"),
]

# Standalone recommendation tokens neutralised on word boundaries so we do not
# corrupt legitimate words (e.g. "shareholder" contains HOLD, "counsel" is safe).
_TOKEN_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bbuy(s|ing)?\b", re.IGNORECASE), "[rating redacted]"),
    (re.compile(r"\bsell(s|ing)?\b", re.IGNORECASE), "[rating redacted]"),
    (re.compile(r"\bhold(s|ing)?\b", re.IGNORECASE), "[rating redacted]"),
    (re.compile(r"\bwatch(list)?\b", re.IGNORECASE), "[rating redacted]"),
]


def neutralize_forbidden_terms(text: str | None) -> str | None:
    """
    Neutralise recommendation/valuation language in externally-sourced text.

    Guarantees the returned string contains none of the safety-gate forbidden
    terms while preserving readability of the surrounding headline. Returns the
    input unchanged when it is None or already clean (SEC titles, our own
    controlled vocabulary).
    """
    if not text:
        return text
    out = text
    for pattern, replacement in _COMPOUND_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _TOKEN_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# Deterministic stable id
# ---------------------------------------------------------------------------


def make_catalyst_event_id(
    ticker: str,
    normalized_event_type: str,
    event_date: str | None,
    key: str | None,
) -> str:
    """
    Build a stable deterministic id for a catalyst event.

    The id is a hash of ticker + normalized_event_type + event_date + a key
    (accession number, source url, or headline). The same event always yields
    the same id, which lets the discovery service deduplicate across runs.
    """
    raw = "|".join(
        [
            (ticker or "").upper().strip(),
            (normalized_event_type or "").strip(),
            (event_date or "").strip(),
            (key or "").strip(),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"cat_{digest}"


# ---------------------------------------------------------------------------
# News item (normalised provider output, pre-classification)
# ---------------------------------------------------------------------------


class NewsItem(BaseModel):
    """A normalised news / press-release item from a provider (pre-classified)."""

    headline: str
    url: str | None = None
    published_at: str | None = None  # ISO date/datetime string
    source_name: str | None = None
    summary: str | None = None
    provider_name: str = "unknown"
    source_tier: str = SourceTier.T5_api_aggregator.value

    # Phase 24.1 — query provenance + relevance (populated after scoring).
    # These are internal research signals; the relevance score/level are
    # model-derived (T6) and never a recommendation.
    raw_query: str | None = None
    query_type: str | None = None  # company | industry | exchange | primary_source | regulatory
    relevance_score: float | None = None
    relevance_level: str | None = None
    is_company_specific: bool | None = None
    is_industry_context: bool | None = None
    # raw_payload is intentionally omitted from report serialisation.


# ---------------------------------------------------------------------------
# Catalyst event
# ---------------------------------------------------------------------------


class CatalystEvent(BaseModel):
    """
    A single source-backed catalyst event with a model-derived classification.

    ``source_tier`` is the tier of the underlying evidence (T1/T2/T4/T5).
    ``model_label_tier`` records that the category/direction/strength are
    model-derived (always T6_model_estimate). The two are kept distinct so a
    reader never mistakes a model interpretation for a source fact.
    """

    id: str
    ticker: str
    company_name: str | None = None
    event_date: str | None = None       # ISO date of the underlying event
    discovered_at: str = ""

    # Source (real evidence)
    source_name: str | None = None
    source_url: str | None = None
    source_tier: str = SourceTier.T5_api_aggregator.value
    provider_name: str = "unknown"

    headline: str = ""
    summary: str | None = None

    # SEC filing metadata (when applicable)
    raw_event_type: str | None = None       # e.g. "8-K", "news_article"
    normalized_event_type: str = "news"     # canonical event kind
    form_type: str | None = None
    accession_number: str | None = None
    filing_date: str | None = None
    report_date: str | None = None
    item_numbers: list[str] = Field(default_factory=list)

    # Model-derived interpretation (T6)
    catalyst_category: str = CatalystCategory.other.value
    catalyst_direction: str = CatalystDirection.unknown.value
    catalyst_strength: str = CatalystStrength.unknown.value
    evidence_strength: str = EvidenceStrength.insufficient.value
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    classification_explanation: str | None = None
    model_label_tier: str = MODEL_LABEL_TIER

    # Links
    related_filing_url: str | None = None
    related_document_url: str | None = None

    # Phase 24.1 — company vs industry-context separation + relevance provenance.
    # An industry-context event describes the sector/industry, NOT the company
    # itself; it must never be treated as direct company evidence.
    is_industry_context: bool = False
    is_company_specific: bool = True
    relevance_score: float | None = None
    relevance_level: str | None = None
    raw_query: str | None = None
    query_type: str | None = None

    # Review
    requires_human_review: bool = True
    warnings: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        if not self.discovered_at:
            object.__setattr__(
                self, "discovered_at", datetime.now(timezone.utc).isoformat()
            )

    def to_report_dict(self) -> dict:
        """
        Serialise for report artifacts with external free text neutralised.

        Used for the workflow markdown JSON block and the final-report catalyst
        section so no recommendation/valuation language can leak into any
        safety-scanned content.
        """
        return {
            "id": self.id,
            "event_date": self.event_date,
            "headline": neutralize_forbidden_terms(self.headline),
            "summary": neutralize_forbidden_terms(self.summary),
            "source_name": self.source_name,
            "source_url": self.source_url,
            "source_tier": self.source_tier,
            "provider_name": self.provider_name,
            "raw_event_type": self.raw_event_type,
            "normalized_event_type": self.normalized_event_type,
            "form_type": self.form_type,
            "accession_number": self.accession_number,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "item_numbers": list(self.item_numbers),
            "catalyst_category": self.catalyst_category,
            "catalyst_direction": self.catalyst_direction,
            "catalyst_strength": self.catalyst_strength,
            "evidence_strength": self.evidence_strength,
            "confidence": self.confidence,
            "model_label_tier": self.model_label_tier,
            "classification_explanation": neutralize_forbidden_terms(
                self.classification_explanation
            ),
            "is_industry_context": self.is_industry_context,
            "is_company_specific": self.is_company_specific,
            "relevance_score": self.relevance_score,
            "relevance_level": self.relevance_level,
            "query_type": self.query_type,
            "requires_human_review": self.requires_human_review,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Summary + discovery result
# ---------------------------------------------------------------------------


class CatalystSummary(BaseModel):
    total_events: int = 0
    positive_count: int = 0
    negative_count: int = 0
    mixed_count: int = 0
    neutral_count: int = 0
    unknown_count: int = 0
    primary_or_regulator_event_count: int = 0
    aggregator_only_count: int = 0
    high_strength_count: int = 0
    latest_event_date: str | None = None
    catalyst_coverage_status: str = CatalystCoverageStatus.none_found.value
    # Phase 24.1 — company vs industry + source-class breakdown.
    company_specific_count: int = 0
    industry_context_count: int = 0
    news_event_count: int = 0
    press_release_event_count: int = 0
    filing_event_count: int = 0


class CatalystDiscoveryResult(BaseModel):
    """Complete output of the catalyst discovery subsystem for one company."""

    ticker: str
    company_name: str | None = None
    lookback_days: int = 90
    generated_at: str = ""

    events: list[CatalystEvent] = Field(default_factory=list)
    filing_events: list[CatalystEvent] = Field(default_factory=list)
    news_events: list[CatalystEvent] = Field(default_factory=list)
    press_release_events: list[CatalystEvent] = Field(default_factory=list)
    # Phase 24.1 — industry/sector context events kept SEPARATE from company
    # catalysts (they are not company-specific evidence).
    industry_events: list[CatalystEvent] = Field(default_factory=list)

    summary: CatalystSummary = Field(default_factory=CatalystSummary)
    warnings: list[str] = Field(default_factory=list)
    source_summary: dict[str, int] = Field(default_factory=dict)
    missing_sources: list[str] = Field(default_factory=list)
    coverage_quality: str = CatalystCoverageStatus.none_found.value
    human_review_required: bool = True
    # Phase 24.1 — company source discovery + attempted/successful source classes.
    company_sources: dict | None = None
    source_classes_attempted: list[str] = Field(default_factory=list)
    source_classes_successful: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        if not self.generated_at:
            object.__setattr__(
                self, "generated_at", datetime.now(timezone.utc).isoformat()
            )

    def to_report_dict(self) -> dict:
        """Serialise for report artifacts (external text neutralised)."""
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "lookback_days": self.lookback_days,
            "generated_at": self.generated_at,
            "coverage_quality": self.coverage_quality,
            "human_review_required": self.human_review_required,
            "summary": self.summary.model_dump(),
            "warnings": list(self.warnings),
            "source_summary": dict(self.source_summary),
            "missing_sources": list(self.missing_sources),
            "events": [e.to_report_dict() for e in self.events],
            "filing_events": [e.to_report_dict() for e in self.filing_events],
            "news_events": [e.to_report_dict() for e in self.news_events],
            "press_release_events": [
                e.to_report_dict() for e in self.press_release_events
            ],
            "industry_events": [e.to_report_dict() for e in self.industry_events],
            "company_sources": self.company_sources,
            "source_classes_attempted": list(self.source_classes_attempted),
            "source_classes_successful": list(self.source_classes_successful),
        }


# ---------------------------------------------------------------------------
# Summarisation helper
# ---------------------------------------------------------------------------

_PRIMARY_OR_REGULATOR_TIERS = {
    SourceTier.T1_primary_filing.value,
    SourceTier.T2_regulator_or_gov.value,
}


_REPUTABLE_NON_FILING_TIERS = {
    SourceTier.T1_primary_filing.value,
    SourceTier.T3_industry_specialist.value,
    SourceTier.T4_quality_media.value,
}


def summarize_events(
    events: list[CatalystEvent],
    lookback_days: int,
    *,
    industry_events: list[CatalystEvent] | None = None,
) -> CatalystSummary:
    """Compute a CatalystSummary from classified company + industry events."""
    industry_events = industry_events or []
    summary = CatalystSummary(total_events=len(events))
    latest: str | None = None

    for ev in events:
        direction = ev.catalyst_direction
        if direction == CatalystDirection.positive.value:
            summary.positive_count += 1
        elif direction == CatalystDirection.negative.value:
            summary.negative_count += 1
        elif direction == CatalystDirection.mixed.value:
            summary.mixed_count += 1
        elif direction == CatalystDirection.neutral.value:
            summary.neutral_count += 1
        else:
            summary.unknown_count += 1

        if ev.source_tier in _PRIMARY_OR_REGULATOR_TIERS:
            summary.primary_or_regulator_event_count += 1
        if ev.evidence_strength == EvidenceStrength.aggregator_only.value:
            summary.aggregator_only_count += 1
        if ev.catalyst_strength == CatalystStrength.high.value:
            summary.high_strength_count += 1

        # Source-class breakdown (company-specific events only).
        net = ev.normalized_event_type
        if net == "sec_filing":
            summary.filing_event_count += 1
        elif net == "press_release":
            summary.press_release_event_count += 1
        elif net == "news_article":
            summary.news_event_count += 1
        summary.company_specific_count += 1

        ev_date = ev.event_date or ev.filing_date
        if ev_date and (latest is None or ev_date > latest):
            latest = ev_date

    summary.industry_context_count = len(industry_events)

    summary.latest_event_date = latest
    summary.catalyst_coverage_status = derive_coverage_status(
        events, latest, lookback_days, has_industry_context=bool(industry_events)
    )
    return summary


def derive_coverage_status(
    events: list[CatalystEvent],
    latest_event_date: str | None,
    lookback_days: int,
    *,
    has_industry_context: bool = False,
) -> str:
    """
    Classify overall catalyst coverage into a CatalystCoverageStatus value.

    Source-class aware (Phase 24.1):
      - none_found : no company events and no industry context
      - filings_only : SEC (T2) only, no company/news/industry source
      - limited : SEC + one weak (e.g. aggregator-only) additional source
      - adequate : a company-owned (T1) source, OR ≥2 reputable non-filing items
      - strong : SEC + a company (T1) source + ≥2 reputable independent items
      - stale : latest company event older than the lookback window
    """
    if not events:
        return (
            CatalystCoverageStatus.limited.value
            if has_industry_context
            else CatalystCoverageStatus.none_found.value
        )

    # Staleness: latest event older than the lookback window.
    if latest_event_date:
        try:
            latest_dt = datetime.fromisoformat(latest_event_date[:10])
            age_days = (datetime.now(timezone.utc).date() - latest_dt.date()).days
            if age_days > lookback_days:
                return CatalystCoverageStatus.stale.value
        except ValueError:
            pass

    t2 = SourceTier.T2_regulator_or_gov.value
    t1 = SourceTier.T1_primary_filing.value
    has_filing = any(e.source_tier == t2 for e in events)
    has_company_source = any(e.source_tier == t1 for e in events)
    non_filing = [e for e in events if e.source_tier != t2]
    reputable_non_filing = [
        e for e in non_filing if e.source_tier in _REPUTABLE_NON_FILING_TIERS
    ]

    # Only SEC filings, and no company/news/industry context at all.
    if has_filing and not non_filing and not has_industry_context:
        return CatalystCoverageStatus.filings_only.value

    # Strong: regulator backbone + company-owned source + independent corroboration.
    if has_filing and has_company_source and len(reputable_non_filing) >= 2:
        return CatalystCoverageStatus.strong.value

    # Adequate: a company-owned source, or multiple reputable independent items.
    if has_company_source or len(reputable_non_filing) >= 2:
        return CatalystCoverageStatus.adequate.value

    # Otherwise a real but weak signal beyond filings (aggregator-only / single).
    return CatalystCoverageStatus.limited.value
