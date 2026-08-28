"""
Canonical post-ingestion evidence inventory.

ONE definition of "what evidence does this report actually have", shared by
every quality surface in the final report (executive summary, data-availability
summary, financial snapshot, bull/bear/risk, valuation readiness, source
quality, research completeness, next steps, internal research memo, LLM council
evidence summary).

WHY THIS EXISTS — real manual QA on NVDA (discovery run ``eee7b0c7``) produced a
single report that simultaneously claimed:

  * LLM council: FY2026 revenue $215.9B, net income $120.1B, OCF $102.7B,
    total assets $206.8B — real SEC/XBRL regulator-backed structured facts;
  * Data Availability Summary: ``fundamentals_available=true`` **and**
    ``available_count=0`` **and** ``available_fields=[]``;
  * Financial Snapshot: "Fundamentals not available. Run with EODHD provider or
    add T1 filings.";
  * Bull case: "cross-referencing with fundamentals (not yet sourced)";
  * Bull case evidence: "Price history available from **sec_edgar**: 251 data
    points" while the source list correctly said ``eodhd_price_only``.

Three independent root causes, all fixed here in ONE place:

1. **Key-name mismatch.** ``financial_data_agent_output_to_dict`` emits
   ``available_financial_data`` / ``missing_financial_data``; every downstream
   reader asked for ``available_count`` / ``available_fields`` /
   ``missing_count`` / ``missing_fields`` / ``warnings_count`` and silently got
   the ``0`` / ``[]`` defaults. Every test that covered those readers passed a
   hand-built dict using the READER's key names, so the mismatch was invisible.
   ``normalize_financial_data_summary`` closes it for all consumers at once
   (report sections, research memo, scoring engine).

2. **EODHD-only fundamentals.** The financial snapshot only recognised
   ``state["fundamentals_data"]`` (the EODHD/T5 shape). NVDA's fundamentals come
   from SEC EDGAR XBRL and live in ``company_snapshot["fundamentals_summary"]``
   (T2, regulator-backed). ``resolve_fundamentals`` recognises BOTH, at their
   TRUE tiers, and never reports "not available" when regulator-backed
   structured statement facts exist.

3. **Price provenance inferred from the company-level provider.**
   ``price_history_summary`` already records its OWN ``provider_name`` /
   ``source_tier`` (e.g. ``eodhd_price_only`` / T5) but agents read the
   *snapshot-level* ``provider_metadata.provider_name`` (``sec_edgar`` / T2)
   instead. ``resolve_price_provenance`` reads the price summary's own
   attribution first and only falls back when the price summary genuinely
   carries none.

SOURCE-PRIORITY RULE (matches the project's source tiers): for a decision-
critical field a validated regulator/issuer structured fact (T1/T2) supersedes a
lower-tier estimate (T5/T6). A weaker source NEVER overwrites a stronger current
fact; when both exist BOTH are retained with their own provenance so a conflict
is exposed honestly rather than silently resolved.

This module invents nothing. Every value is read from an already-sourced
structure; absence is reported as absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.sources.primary_fact_parser import FINANCIAL_STATEMENT_FIELDS

TIER_T1 = "T1_primary_filing"
TIER_T2 = "T2_regulator_or_gov"
TIER_T5 = "T5_api_aggregator"
TIER_T6 = "T6_model_estimate"

# Tier ordering for the source-priority rule (lower rank == stronger source).
_TIER_RANK = {
    TIER_T1: 1,
    TIER_T2: 2,
    "T3_industry_specialist": 3,
    "T4_quality_media": 4,
    TIER_T5: 5,
    TIER_T6: 6,
}


def tier_rank(tier: str | None) -> int:
    """Rank a source tier; unknown tiers sort last (weakest)."""
    return _TIER_RANK.get(str(tier or ""), 99)


# ---------------------------------------------------------------------------
# 1. financial_data_summary key normalisation
# ---------------------------------------------------------------------------

# Agent-facing key -> the reader-facing key every downstream surface asks for.
_LIST_KEY_ALIASES = (
    ("available_financial_data", "available_fields", "available_count"),
    ("missing_financial_data", "missing_fields", "missing_count"),
)


def normalize_financial_data_summary(
    financial_data_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """DEPRECATED (Phase B) — superseded by ``FinancialDataSummary``.

    This dual-spelling shim was the correct EMERGENCY fix for the
    ``available_count=0`` incident, but it left the contract ambiguous: two
    spellings were authoritative at once and every consumer had to pick one.
    ``app.schemas.evidence_state.FinancialDataSummary`` replaces it — legacy
    spellings normalise at ONE ingress boundary, counts are DERIVED from their
    lists, and consumers read attributes.

    It has NO production callers (enforced by
    ``test_phase32b_evidence_contracts.py``) and is retained only so historical
    tooling and tests that predate Phase B keep working. Do not call it from
    new code.

    Return the summary with BOTH key spellings populated and consistent.

    ``FinancialDataAgent`` serialises ``available_financial_data`` /
    ``missing_financial_data`` / ``warnings``; report/memo/scoring readers ask
    for ``available_fields`` / ``available_count`` / ``missing_fields`` /
    ``missing_count`` / ``warnings_count``. Both spellings are emitted here so
    no consumer silently falls back to a ``0`` / ``[]`` default that reads as
    "verified empty" when the data is actually present.

    Never invents values: a key that is genuinely absent on both spellings stays
    absent. Idempotent — safe to apply to an already-normalised dict.
    """
    if not financial_data_summary:
        return financial_data_summary

    out = dict(financial_data_summary)
    for agent_key, reader_key, count_key in _LIST_KEY_ALIASES:
        values = out.get(reader_key)
        if not isinstance(values, list):
            values = out.get(agent_key)
        if isinstance(values, list):
            out[reader_key] = values
            out.setdefault(agent_key, values)
            out[count_key] = len(values)

    warnings = out.get("warnings")
    if isinstance(warnings, list):
        out["warnings_count"] = len(warnings)
    return out


# ---------------------------------------------------------------------------
# 2. Price provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceProvenance:
    """Where the price history ACTUALLY came from — never inferred from the
    company-level provider."""

    available: bool
    latest_close: float | None = None
    currency: str | None = None
    as_of: str | None = None
    data_points_count: int = 0
    provider_name: str | None = None
    source_tier: str | None = None
    price_data_quality: str | None = None

    @property
    def provider_label(self) -> str:
        return self.provider_name or "unknown provider"

    def evidence_sentence(self) -> str:
        """One honest sentence naming the REAL price provider and tier."""
        if not self.available:
            return "Price history not available."
        close = (
            f"{self.latest_close} {self.currency}".strip()
            if self.latest_close is not None
            else "not sourced"
        )
        return (
            f"Price history available from {self.provider_label}: "
            f"{self.data_points_count} data points. Latest close: {close}"
            f" (source tier: {self.source_tier or 'not sourced'})."
        )


def resolve_price_provenance(
    company_snapshot: dict[str, Any] | None,
) -> PriceProvenance:
    """Resolve the price history's OWN provider/tier from the snapshot.

    ``price_history_summary`` carries ``provider_name`` / ``source_tier`` for the
    price feed itself (e.g. ``eodhd_price_only`` / T5). Only when it carries
    neither do we fall back to the snapshot-level provider — and the tier
    fallback is the price-appropriate T5, never the company profile's tier
    (which is how "price history from sec_edgar" was produced).
    """
    snap = company_snapshot or {}
    price = snap.get("price_history_summary")
    if not isinstance(price, dict) or not price.get("available"):
        return PriceProvenance(available=False)

    provider_meta = snap.get("provider_metadata")
    provider_meta = provider_meta if isinstance(provider_meta, dict) else {}

    provider = price.get("provider_name") or provider_meta.get("provider_name")
    tier = price.get("source_tier") or TIER_T5

    currency = price.get("currency")
    if currency in ("not_sourced", ""):
        currency = None

    date_range = price.get("date_range")
    as_of = date_range.get("end") if isinstance(date_range, dict) else None

    return PriceProvenance(
        available=True,
        latest_close=price.get("latest_close"),
        currency=currency,
        as_of=as_of,
        data_points_count=int(price.get("data_points_count") or 0),
        provider_name=str(provider) if provider else None,
        source_tier=str(tier) if tier else None,
        price_data_quality=price.get("price_data_quality"),
    )


# ---------------------------------------------------------------------------
# 3. Fundamentals (regulator-structured vs aggregator vs issuer document)
# ---------------------------------------------------------------------------

# Statement fields sourced from SEC EDGAR XBRL (``fundamentals_summary``).
# Values are ANNUAL (10-K / 20-F), never TTM.
SEC_STATEMENT_FIELDS: tuple[str, ...] = (
    "revenue_usd_m",
    "gross_profit_usd_m",
    "operating_income_usd_m",
    "net_income_usd_m",
    "eps_basic",
    "eps_diluted",
    "operating_cash_flow_usd_m",
    "capital_expenditures_usd_m",
    "free_cash_flow_usd_m",
    "total_assets_usd_m",
    "total_liabilities_usd_m",
    "shareholders_equity_usd_m",
    "cash_and_equivalents_usd_m",
    "short_term_debt_usd_m",
    "long_term_debt_usd_m",
    "total_debt_usd_m",
    "shares_outstanding_mln",
)


# Issuer-primary-document fact fields that count as FINANCIAL fundamentals.
# Kept here (not in the report generator) so the source-quality agent and the
# report builders admit exactly the same facts — a gap the report says is closed
# must be the same gap the agent stops asserting.
#
# Private-use readiness PR-C: this is now DERIVED from the parser's own exported
# vocabulary rather than restated. The hand-maintained copy had drifted from
# reality in both directions — it listed ``shareholders_equity`` and
# ``earnings_per_share``, which the parser has never emitted, and omitted
# ``total_equity`` and ``net_cash``, which it emits routinely, so a real
# ``total_equity`` fact counted as no fundamental anywhere. ``ebitda`` is
# retained as an ALIAS the parser does not currently produce but a future
# vocabulary addition might, and which some aggregator payloads use.
PRIMARY_FACT_FIELDS: frozenset[str] = FINANCIAL_STATEMENT_FIELDS | frozenset({"ebitda"})


@dataclass(frozen=True)
class FundamentalsEvidence:
    """What financial-statement fundamentals this report actually holds.

    ``available`` is True when ANY recognised channel produced at least one
    statement value — a regulator-backed XBRL fact counts exactly as much as an
    aggregator field, and MORE for the source-priority rule.
    """

    available: bool
    source: str | None = None
    source_tier: str | None = None
    period_label: str | None = None
    form_type: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    channels: tuple[str, ...] = ()

    @property
    def is_regulator_structured(self) -> bool:
        """A REGULATOR published these as structured facts (SEC XBRL, T2).

        Narrowed in Phase 32D2. This used to include ``TIER_T1``, which meant a
        fact extracted from the ISSUER's own annual report was reported under
        the channel labelled "Regulator structured financial facts (SEC XBRL)".
        Pandora's live report did exactly that: "available — 1 statement
        field(s) from issuer_primary_document (T1_primary_filing)" under an SEC
        XBRL heading, for a Danish issuer with no SEC registration at all.
        Issuer-primary facts are STRONGER, not the same thing — see
        :attr:`is_issuer_primary`.
        """
        return self.source_tier == TIER_T2

    @property
    def is_issuer_primary(self) -> bool:
        """The ISSUER published these in its own primary document (T1)."""
        return self.source_tier == TIER_T1

    @property
    def is_filing_backed(self) -> bool:
        """Either of the two above — a filing stands behind the numbers."""
        return self.source_tier in (TIER_T1, TIER_T2)

    def note(self) -> str:
        """The honest ``fundamentals_note`` for the financial snapshot."""
        if not self.available:
            return (
                "Financial-statement fundamentals not sourced. No regulator "
                "XBRL facts, issuer-filing facts, or aggregator fundamentals "
                "are available for this company at this phase."
            )
        if self.source == "sec_edgar_xbrl":
            period = self.period_label or "the latest annual period"
            form = f" ({self.form_type})" if self.form_type else ""
            return (
                f"Financial statements from SEC EDGAR XBRL for {period}{form} — "
                "regulator-backed structured facts (T2_regulator_or_gov). "
                "Annual figures; not TTM. EBITDA, market cap and enterprise "
                "value are not part of SEC statement data and remain missing."
            )
        if self.source == "issuer_primary_document":
            return (
                "Financial-statement facts extracted from the issuer's OWN "
                "primary document (annual report or equivalent) — "
                "T1_primary_filing. Each value carries its own page reference "
                "and source URL and requires human confirmation against the "
                "filing. This is NOT regulator-structured XBRL data, and the "
                "statement lines not extracted remain missing."
            )
        if self.source == "eodhd_fundamentals":
            return (
                "Fundamentals from EODHD (T5 aggregator). "
                "Must be validated against T1/T2 filings before use."
            )
        return (
            f"Fundamentals sourced from {self.source} "
            f"({self.source_tier or 'tier not sourced'})."
        )


def _period_label(fs: dict[str, Any]) -> str | None:
    basis = fs.get("period_basis") or "annual"
    fy = fs.get("fiscal_year")
    return f"{basis} FY{fy}" if fy else basis


# A scope label that means "the consolidated group" (or no scope at all). A
# SEGMENT-scoped fact must never stand in for the group figure.
GROUP_SCOPE_LABELS: frozenset[str] = frozenset(
    {"group", "consolidated", "total group", "the group", "total"}
)


def _qualifying_primary_facts(
    primary_facts: list[dict[str, Any]] | None,
    financial_fields: frozenset[str] | None,
) -> dict[str, Any]:
    """High-confidence, group-scoped, FINANCIAL primary-document facts only.

    Mirrors the report's own admission rule so this inventory can never claim
    issuer-document fundamentals from a medium-confidence fact, a segment-scoped
    figure, or an identity field (e.g. ``reporting_currency``).
    """
    out: dict[str, Any] = {}
    for fact in primary_facts or []:
        if not isinstance(fact, dict):
            continue
        name = fact.get("field") or fact.get("field_name")
        if not name or fact.get("value") is None:
            continue
        if financial_fields is not None and name not in financial_fields:
            continue
        if fact.get("confidence") != "high":
            continue
        scope = fact.get("scope")
        if scope and str(scope).strip().lower() not in GROUP_SCOPE_LABELS:
            continue
        out.setdefault(str(name), fact.get("value"))
    return out


def resolve_fundamentals(
    company_snapshot: dict[str, Any] | None,
    fundamentals_data: dict[str, Any] | None = None,
    primary_facts: list[dict[str, Any]] | None = None,
    *,
    financial_fields: frozenset[str] | None = None,
) -> FundamentalsEvidence:
    """Resolve financial-statement fundamentals across ALL recognised channels.

    Priority (strongest first), per the project source tiers:

      1. issuer primary-document facts (T1) — ``primary_facts``
      2. SEC EDGAR XBRL structured statements (T2) —
         ``company_snapshot["fundamentals_summary"]``
      3. EODHD aggregator fundamentals (T5) — ``fundamentals_data``

    The strongest channel that produced values sets ``source``/``source_tier``;
    weaker channels are still recorded in ``channels`` so nothing is hidden and
    the caller can surface a conflict. A weaker channel NEVER overwrites a
    stronger current fact.
    """
    snap = company_snapshot or {}
    channels: list[str] = []

    fs = snap.get("fundamentals_summary")
    fs = fs if isinstance(fs, dict) else {}
    sec_values = {
        k: fs.get(k) for k in SEC_STATEMENT_FIELDS if fs.get(k) is not None
    }
    if sec_values:
        channels.append("sec_edgar_xbrl")

    eodhd_values: dict[str, Any] = {}
    if isinstance(fundamentals_data, dict):
        highlights = fundamentals_data.get("highlights")
        if isinstance(highlights, dict):
            eodhd_values = {
                k: v for k, v in highlights.items() if v is not None
            }
    if eodhd_values:
        channels.append("eodhd_fundamentals")

    filing_values = _qualifying_primary_facts(primary_facts, financial_fields)
    if filing_values:
        channels.append("issuer_primary_document")

    if filing_values:
        return FundamentalsEvidence(
            available=True,
            source="issuer_primary_document",
            source_tier=TIER_T1,
            values=filing_values,
            channels=tuple(channels),
        )
    if sec_values:
        return FundamentalsEvidence(
            available=True,
            source="sec_edgar_xbrl",
            source_tier=fs.get("source_tier") or TIER_T2,
            period_label=_period_label(fs),
            form_type=fs.get("form_type"),
            values=sec_values,
            channels=tuple(channels),
        )
    if eodhd_values:
        return FundamentalsEvidence(
            available=True,
            source="eodhd_fundamentals",
            source_tier=TIER_T5,
            values=eodhd_values,
            channels=tuple(channels),
        )
    return FundamentalsEvidence(available=False, channels=tuple(channels))


# ---------------------------------------------------------------------------
# 4. Evidence channels (terminology — Problem: "primary document" conflation)
# ---------------------------------------------------------------------------

# These are FIVE DIFFERENT THINGS. Conflating them is what produced the NVDA
# report's "primary filings required" / "the reports were scanned or JS-gated"
# text next to a full set of real SEC XBRL statement facts.
CHANNEL_ISSUER_DOCUMENT = "issuer_primary_document"
CHANNEL_ISSUER_PRIMARY_FACTS = "issuer_primary_facts"
CHANNEL_REGULATOR_FACTS = "regulator_structured_facts"
CHANNEL_AGGREGATOR_FUNDAMENTALS = "aggregator_fundamentals"
CHANNEL_REGULATOR_FILINGS = "regulator_filing_events"
CHANNEL_ISSUER_NEWSROOM = "issuer_newsroom"
CHANNEL_DB_CITATIONS = "db_citations"

#: Manual-QA corrective — the regulator channels used to be labelled
#: "Regulator structured financial facts (SEC XBRL)" and "Regulator filing
#: events (SEC EDGAR)" for EVERY issuer. On a Danish or Italian report that
#: names a venue the issuer has no relationship with, and reads as though the
#: platform looked in the wrong place. SEC EDGAR / XBRL remain exactly right
#: for an SEC-eligible issuer, so the venue is now stated when it is KNOWN to
#: apply and the label is source-neutral otherwise; the concrete provider is
#: carried separately in ``detail``/``venue`` so nothing is lost.
_CHANNEL_LABELS = {
    CHANNEL_ISSUER_DOCUMENT: "Issuer-primary document extraction",
    CHANNEL_ISSUER_PRIMARY_FACTS: (
        "Issuer primary-document financial facts (T1 filing)"
    ),
    CHANNEL_REGULATOR_FACTS: "Regulator structured financial facts",
    CHANNEL_AGGREGATOR_FUNDAMENTALS: "Aggregator fundamentals (T5)",
    CHANNEL_REGULATOR_FILINGS: "Official regulated disclosures / filing events",
    CHANNEL_ISSUER_NEWSROOM: "Issuer newsroom / press releases",
    CHANNEL_DB_CITATIONS: "Persisted citations / council evidence",
}

#: The venue suffix added to a regulator channel label when the issuer's own
#: venue is known. ``None`` venue ⇒ the source-neutral label above, never a
#: guessed jurisdiction.
_SEC_VENUE_FACTS = "SEC XBRL"
_SEC_VENUE_FILINGS = "SEC EDGAR"


def _regulator_event_detail(filing_events: int, regulated_disclosures: int) -> str:
    """State each official-event source separately, never as one blurred total."""
    parts: list[str] = []
    if filing_events:
        parts.append(f"{filing_events} regulator filing event(s)")
    if regulated_disclosures:
        parts.append(f"{regulated_disclosures} live regulated disclosure(s)")
    return f"available — {', '.join(parts)}" if parts else "not sourced"


def build_evidence_channels(
    *,
    fundamentals: FundamentalsEvidence,
    primary_document_counts: dict[str, int] | None = None,
    catalyst_summary: dict[str, Any] | None = None,
    citation_count: int = 0,
    council_evidence_count: int = 0,
    sec_eligible: bool = False,
    regulator_facts_venue: str | None = None,
    regulator_filings_venue: str | None = None,
    regulated_disclosure_count: int = 0,
) -> dict[str, Any]:
    """An explicit, non-contradictory inventory of the evidence channels.

    Each channel reports its OWN state. A report with zero extracted issuer PDFs
    but full SEC XBRL statements says exactly that — "issuer-primary document
    extraction: none / not used" AND "regulator structured facts: available" —
    instead of a blanket "primary filings required".
    """
    doc_counts = primary_document_counts or {}
    extracted = int(doc_counts.get("primary_document_extracted_count") or 0)
    metadata_only = int(doc_counts.get("primary_document_metadata_only_count") or 0)
    failed = int(doc_counts.get("primary_document_failed_count") or 0)

    summary = catalyst_summary if isinstance(catalyst_summary, dict) else {}
    filing_events = int(summary.get("filing_event_count") or 0)
    press_events = int(summary.get("press_release_event_count") or 0)
    regulated_disclosures = max(0, int(regulated_disclosure_count or 0))
    regulator_events = filing_events + regulated_disclosures

    facts_venue = regulator_facts_venue or (_SEC_VENUE_FACTS if sec_eligible else None)
    filings_venue = (
        regulator_filings_venue or (_SEC_VENUE_FILINGS if sec_eligible else None)
    )
    venue_by_channel = {
        CHANNEL_REGULATOR_FACTS: facts_venue,
        CHANNEL_REGULATOR_FILINGS: filings_venue,
    }

    def _channel(key: str, available: bool, detail: str, **extra: Any) -> dict:
        label = _CHANNEL_LABELS[key]
        venue = venue_by_channel.get(key)
        if venue:
            label = f"{label} ({venue})"
        entry = {
            "channel": key,
            "label": label,
            "available": available,
            "detail": detail,
            **extra,
        }
        if key in venue_by_channel:
            # Stated even when None, so a reader can tell "no venue resolved"
            # from "this row simply does not have one".
            entry["venue"] = venue
        return entry

    return {
        "type": "evidence_channels",
        "note": (
            "These channels are DISTINCT evidence types and are reported "
            "separately on purpose. The absence of one never implies the "
            "absence of another — in particular, a company can have complete "
            "regulator-backed structured financial statements with zero "
            "separately-extracted issuer documents."
        ),
        "channels": [
            _channel(
                CHANNEL_ISSUER_DOCUMENT,
                extracted > 0,
                (
                    f"{extracted} issuer/filing document(s) extracted"
                    if extracted
                    else "none / not used for this report"
                ),
                extracted_count=extracted,
                metadata_only_count=metadata_only,
                failed_count=failed,
            ),
            # Phase 32D2 — three DISTINCT financial-fact channels. The issuer
            # publishing its own annual report, a regulator publishing XBRL,
            # and an aggregator republishing either are not the same evidence
            # and must never share a row. Pandora's live report rendered
            # "1 statement field from issuer_primary_document" under the SEC
            # XBRL heading, for a Danish issuer with no SEC registration.
            _channel(
                CHANNEL_ISSUER_PRIMARY_FACTS,
                fundamentals.available and fundamentals.is_issuer_primary,
                (
                    f"available — {len(fundamentals.values)} statement field(s) "
                    f"extracted from the issuer's own primary document "
                    f"({fundamentals.source_tier})"
                    if fundamentals.available and fundamentals.is_issuer_primary
                    else "not sourced"
                ),
                field_count=(
                    len(fundamentals.values) if fundamentals.is_issuer_primary else 0
                ),
                period=(
                    fundamentals.period_label
                    if fundamentals.is_issuer_primary
                    else None
                ),
            ),
            _channel(
                CHANNEL_REGULATOR_FACTS,
                fundamentals.available and fundamentals.is_regulator_structured,
                (
                    f"available — {len(fundamentals.values)} statement field(s) "
                    f"from {fundamentals.source} ({fundamentals.source_tier})"
                    if fundamentals.available and fundamentals.is_regulator_structured
                    else "not sourced"
                ),
                field_count=(
                    len(fundamentals.values)
                    if fundamentals.is_regulator_structured
                    else 0
                ),
                period=(
                    fundamentals.period_label
                    if fundamentals.is_regulator_structured
                    else None
                ),
                form_type=(
                    fundamentals.form_type
                    if fundamentals.is_regulator_structured
                    else None
                ),
            ),
            _channel(
                CHANNEL_AGGREGATOR_FUNDAMENTALS,
                fundamentals.available and not fundamentals.is_filing_backed,
                (
                    f"available — {len(fundamentals.values)} field(s) from "
                    f"{fundamentals.source} ({fundamentals.source_tier}); "
                    "not filing-verified"
                    if fundamentals.available and not fundamentals.is_filing_backed
                    else "not sourced"
                ),
                field_count=(
                    len(fundamentals.values)
                    if fundamentals.available and not fundamentals.is_filing_backed
                    else 0
                ),
            ),
            # Manual-QA corrective — this counted SEC filing events ONLY. Once
            # the label stopped saying "SEC EDGAR" and named the issuer's own
            # venue, the row read "Official regulated disclosures / filing
            # events (eMarket Storage (CONSOB)) — not sourced, 0 events" on a
            # report DISPLAYING five live disclosures from that venue. The
            # channel is "official regulated disclosures / filing events", so
            # it counts both; the two sources stay separately visible.
            _channel(
                CHANNEL_REGULATOR_FILINGS,
                regulator_events > 0,
                _regulator_event_detail(filing_events, regulated_disclosures),
                event_count=regulator_events,
                filing_event_count=filing_events,
                regulated_disclosure_count=regulated_disclosures,
            ),
            _channel(
                CHANNEL_ISSUER_NEWSROOM,
                press_events > 0,
                (
                    f"available — {press_events} issuer press item(s)"
                    if press_events
                    else "not sourced"
                ),
                event_count=press_events,
            ),
            _channel(
                CHANNEL_DB_CITATIONS,
                (citation_count + council_evidence_count) > 0,
                (
                    f"{citation_count} persisted citation(s), "
                    f"{council_evidence_count} council evidence item(s)"
                ),
                citation_count=citation_count,
                council_evidence_count=council_evidence_count,
            ),
        ],
        "human_review_required": True,
    }
