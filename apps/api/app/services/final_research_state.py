"""
ONE authoritative final reconciled research state — Phase 32D2.

WHY THIS MODULE EXISTS
======================
Manual QA on the Pandora (PNDORA) final report found the SAME document
asserting, simultaneously:

  * ``fundamentals_available: true`` / ``fundamentals_source:
    issuer_primary_document`` / ``T1_primary_filing`` / validated FY2025 revenue
    DKK 32.5bn, quoted correctly by the LLM council; AND
  * ``financials.revenue`` listed under ``missing_fields``, ``missing_items``
    and ``missing_valuation_inputs``; "All 18 core financial fundamental
    categories are missing"; "fundamentals (not yet sourced)"; "Source T1
    primary filings (annual report / 10-K) for revenue"; "All current data from
    T6_model_estimate only".

Nothing was mis-extracted. The evidence was real and the council saw it. The
DETERMINISTIC surfaces did not, because they were computed at WORKFLOW time —
before document ingestion, before citations, before the council — and then
rendered verbatim next to post-ingestion state.

Earlier phases fixed this one surface at a time (Problem D recomputed source
quality; Phase C2 recomputed the canonical quality + thin state; a CFR/MC
regression recomputed the availability summary "when ``primary_facts``"). Each
fix was correct and each left the NEXT surface stale, because there was no
single place that owned the answer.

THE RULE THIS MODULE ENCODES
============================
After ingestion and council completion there is exactly ONE reconciled research
state. Every deterministic human-facing surface is rebuilt FROM it. A surface
never re-derives "what evidence do we have" from a pre-ingestion input.

WHAT IT DOES NOT DO
===================
It invents nothing and it upgrades nothing. A category is marked resolved only
when a validated, high-confidence, group-scoped fact for that category exists,
carrying its own source + tier. Everything else stays missing, and stays
missing LOUDLY — the point is to separate "we have this" from "we still do not
have this", not to make the report look better. For Pandora that means revenue
and fiscal year become sourced while EBIT, EBITDA, FCF and debt remain openly
missing, in every section, with the same words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations.financial_data_provider import normalize_source_tier
from app.schemas.evidence_state import FinancialDataSummary
from app.services.canonical_evidence import (
    GROUP_SCOPE_LABELS,
    PRIMARY_FACT_FIELDS,
    TIER_T1,
    TIER_T2,
    FundamentalsEvidence,
    PriceProvenance,
    resolve_fundamentals,
    resolve_price_provenance,
    tier_rank,
)

# Bump when the reconciled payload shape changes in a way a reader must notice.
FINAL_RESEARCH_STATE_VERSION = 1

#: Prefix the FinancialDataAgent uses for statement/valuation categories.
FINANCIAL_FIELD_PREFIX = "financials."


# ---------------------------------------------------------------------------
# Primary-fact  ->  financial-agent category
# ---------------------------------------------------------------------------

#: Issuer primary-document fact field -> the ``financials.<category>`` entry it
#: closes. Only fields that genuinely map onto a category the FinancialDataAgent
#: tracks are listed; an unmapped fact (e.g. ``net_debt``, ``shareholders_equity``)
#: is still real evidence and still reported, it simply closes no category here.
PRIMARY_FACT_TO_CATEGORY: dict[str, str] = {
    "revenue": "revenue",
    "operating_profit": "ebit",
    "ebitda": "ebitda",
    "net_income": "net_income",
    "free_cash_flow": "free_cash_flow",
    "total_assets": "total_assets",
    "total_debt": "total_debt",
    "cash_and_equivalents": "cash_and_equivalents",
    "earnings_per_share": "earnings_per_share",
}

#: Regulator-structured (SEC XBRL) statement key -> category. Mirrors the
#: FinancialDataAgent's own map so the reconciliation and the agent can never
#: disagree about which categories a channel closes.
REGULATOR_FACT_TO_CATEGORY: dict[str, str] = {
    "revenue_usd_m": "revenue",
    "operating_income_usd_m": "ebit",
    "net_income_usd_m": "net_income",
    "total_assets_usd_m": "total_assets",
    "total_debt_usd_m": "total_debt",
    "cash_and_equivalents_usd_m": "cash_and_equivalents",
    "free_cash_flow_usd_m": "free_cash_flow",
    "eps_basic": "earnings_per_share",
    "eps_diluted": "earnings_per_share",
}


#: Categories a filing genuinely CAN close: statement lines an annual report
#: prints. Everything else in the agent's expected list is a MARKET or DERIVED
#: metric (market cap, EV/EBITDA, P/E, dividend yield, ratios) that no annual
#: report states directly, so telling a reviewer to "extract price to earnings
#: from the already-ingested filing" would be a false instruction.
STATEMENT_CATEGORIES: frozenset[str] = frozenset(
    {
        "revenue",
        "ebit",
        "ebitda",
        "net_income",
        "total_assets",
        "total_debt",
        "cash_and_equivalents",
        "free_cash_flow",
        "earnings_per_share",
        "book_value_per_share",
    }
)


def _category_label(category: str) -> str:
    return category.replace("_", " ")


def category_labels(categories: list[str] | tuple[str, ...]) -> str:
    """Human wording for a category list — never a bare machine field path."""
    return ", ".join(_category_label(c) for c in categories)


# ---------------------------------------------------------------------------
# Resolved facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedFinancialFact:
    """ONE financial category, and exactly where its value came from."""

    category: str
    source: str
    source_tier: str
    period: str | None = None
    source_url: str | None = None
    value: Any = None

    @property
    def field_path(self) -> str:
        return f"{FINANCIAL_FIELD_PREFIX}{self.category}"

    @property
    def label(self) -> str:
        return _category_label(self.category)

    def describe(self) -> str:
        period = f" ({self.period})" if self.period else ""
        return f"{self.label}{period} — {self.source} ({self.source_tier})"


@dataclass(frozen=True)
class FinancialEvidenceState:
    """What financial-statement evidence the report holds AFTER ingestion.

    Deliberately SEPARATE from the company-level provider tier. Pandora's
    identity/price come from a T5/T6 aggregator while its revenue comes from the
    issuer's own annual report (T1). Collapsing those into one "source tier" is
    what produced "all current data is T6" beside a validated T1 revenue figure.
    """

    available: bool = False
    best_source: str | None = None
    best_tier: str | None = None
    resolved: tuple[ResolvedFinancialFact, ...] = ()
    open_categories: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    #: Facts that are real evidence but close no tracked category
    #: (e.g. ``fiscal_year``, ``reporting_currency``, ``net_debt``).
    unmapped_fact_count: int = 0

    @property
    def is_primary_backed(self) -> bool:
        """True when the STRONGEST financial evidence is a filing (T1/T2)."""
        return self.available and self.best_tier in (TIER_T1, TIER_T2)

    @property
    def is_issuer_primary(self) -> bool:
        return self.available and self.best_tier == TIER_T1

    @property
    def resolved_categories(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f.category for f in self.resolved))

    @property
    def resolved_field_paths(self) -> tuple[str, ...]:
        return tuple(f"{FINANCIAL_FIELD_PREFIX}{c}" for c in self.resolved_categories)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved_categories)

    @property
    def open_statement_categories(self) -> tuple[str, ...]:
        """Open categories a filing could actually close — see
        :data:`STATEMENT_CATEGORIES`."""
        return tuple(c for c in self.open_categories if c in STATEMENT_CATEGORIES)

    @property
    def open_market_categories(self) -> tuple[str, ...]:
        """Open categories that need MARKET data or derivation, not a filing."""
        return tuple(
            c for c in self.open_categories if c not in STATEMENT_CATEGORIES
        )

    def describe_sourced(self) -> str:
        """One honest sentence about what IS sourced, or the absence of it."""
        if not self.available or not self.resolved:
            return "No financial-statement categories are sourced."
        return (
            f"{self.resolved_count} financial category(ies) sourced from "
            f"{self.best_source} ({self.best_tier}): "
            f"{category_labels(self.resolved_categories)}."
        )

    def describe_open(self) -> str:
        """One honest sentence about what is STILL missing.

        Statement lines and market/derived metrics are named separately: they
        are closed by different work (read more of the filing vs source market
        data), and merging them produced next-step instructions like "extract
        price to earnings from the annual report".
        """
        if not self.open_categories:
            return "No financial categories remain open."
        parts: list[str] = []
        if self.open_statement_categories:
            parts.append(
                f"{len(self.open_statement_categories)} statement line(s) not "
                f"extracted: {category_labels(self.open_statement_categories)}"
            )
        if self.open_market_categories:
            parts.append(
                f"{len(self.open_market_categories)} market/derived metric(s) "
                f"not sourced: {category_labels(self.open_market_categories)}"
            )
        return "; ".join(parts) + "."


def _qualifying_fact_rows(
    primary_facts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """High-confidence, group-scoped, FINANCIAL primary-document fact rows.

    Same admission rule as ``canonical_evidence._qualifying_primary_facts`` —
    a medium-confidence fact, a SEGMENT-scoped figure, or an identity field
    (``reporting_currency``) never closes a group-level financial category.
    """
    rows: list[dict[str, Any]] = []
    for fact in primary_facts or []:
        if not isinstance(fact, dict):
            continue
        name = fact.get("field") or fact.get("field_name")
        if not name or fact.get("value") is None:
            continue
        if str(name) not in PRIMARY_FACT_FIELDS:
            continue
        if fact.get("confidence") != "high":
            continue
        scope = fact.get("scope")
        if scope and str(scope).strip().lower() not in GROUP_SCOPE_LABELS:
            continue
        rows.append(fact)
    return rows


def build_financial_evidence_state(
    *,
    company_snapshot: dict[str, Any] | None,
    fundamentals: FundamentalsEvidence,
    primary_facts: list[dict[str, Any]] | None,
    financial_data_summary: dict[str, Any] | None,
) -> FinancialEvidenceState:
    """Resolve, per CATEGORY, what financial evidence exists and at which tier.

    Fact-centric on purpose: the question a reader asks is "do we have revenue?",
    not "did some channel report something". A category is resolved by the
    STRONGEST channel that produced it; the weaker channel is not deleted, it
    simply does not set the category's tier (the source-priority rule).
    """
    resolved: dict[str, ResolvedFinancialFact] = {}
    channels: list[str] = list(fundamentals.channels)

    # 1. Issuer primary-document facts (T1) — strongest.
    fact_rows = _qualifying_fact_rows(primary_facts)
    unmapped = 0
    for fact in fact_rows:
        name = str(fact.get("field") or fact.get("field_name"))
        category = PRIMARY_FACT_TO_CATEGORY.get(name)
        if category is None:
            unmapped += 1
            continue
        resolved.setdefault(
            category,
            ResolvedFinancialFact(
                category=category,
                source="issuer_primary_document",
                source_tier=TIER_T1,
                period=(
                    str(fact.get("period")) if fact.get("period") is not None else None
                ),
                source_url=fact.get("source_url"),
                value=fact.get("numeric_value", fact.get("value")),
            ),
        )

    # 2. Regulator-structured statement facts (T2).
    snap = company_snapshot or {}
    fs = snap.get("fundamentals_summary")
    fs = fs if isinstance(fs, dict) else {}
    fs_tier = normalize_source_tier(fs.get("source_tier")) or TIER_T2
    fs_period = None
    if fs.get("fiscal_year"):
        fs_period = f"{fs.get('period_basis') or 'annual'} FY{fs.get('fiscal_year')}"
    for key, category in REGULATOR_FACT_TO_CATEGORY.items():
        if fs.get(key) is None:
            continue
        existing = resolved.get(category)
        if existing is not None and tier_rank(existing.source_tier) <= tier_rank(fs_tier):
            continue
        resolved[category] = ResolvedFinancialFact(
            category=category,
            source="sec_edgar_xbrl",
            source_tier=fs_tier,
            period=fs_period,
            value=fs.get(key),
        )

    # 3. Whatever the FinancialDataAgent already listed as available keeps its
    #    place: those categories come from the aggregator channel and are real.
    #    They never OVERWRITE a stronger fact above.
    agent = FinancialDataSummary.from_payload(financial_data_summary)
    agent_tier = _aggregator_tier(company_snapshot)
    for path in agent.available_fields if agent else []:
        if not path.startswith(FINANCIAL_FIELD_PREFIX):
            continue
        category = path[len(FINANCIAL_FIELD_PREFIX) :]
        if category in resolved:
            continue
        resolved[category] = ResolvedFinancialFact(
            category=category,
            source=(fundamentals.source or "provider_snapshot"),
            source_tier=agent_tier,
        )

    ordered = tuple(resolved[c] for c in sorted(resolved))
    best = min(ordered, key=lambda f: tier_rank(f.source_tier), default=None)

    open_categories = tuple(
        path[len(FINANCIAL_FIELD_PREFIX) :]
        for path in (agent.missing_fields if agent else [])
        if path.startswith(FINANCIAL_FIELD_PREFIX)
        and path[len(FINANCIAL_FIELD_PREFIX) :] not in resolved
    )

    if fact_rows and "issuer_primary_document" not in channels:
        channels.append("issuer_primary_document")

    return FinancialEvidenceState(
        available=bool(ordered),
        best_source=best.source if best else None,
        best_tier=best.source_tier if best else None,
        resolved=ordered,
        open_categories=open_categories,
        channels=tuple(dict.fromkeys(channels)),
        unmapped_fact_count=unmapped,
    )


def _aggregator_tier(company_snapshot: dict[str, Any] | None) -> str:
    meta = (company_snapshot or {}).get("provider_metadata")
    meta = meta if isinstance(meta, dict) else {}
    return normalize_source_tier(meta.get("source_tier")) or "T6_model_estimate"


# ---------------------------------------------------------------------------
# Reconcilers
# ---------------------------------------------------------------------------

_STALE_FINANCIAL_WARNING_MARKERS = (
    "financial fundamental categories missing",
)

#: Clause the FinancialDataAgent appends to its aggregator-tier warning. It is
#: true before ingestion and false after: the filing HAS been sourced, it simply
#: has not given up every line. Left in place it reads, verbatim, "Missing
#: primary filing sources." next to a validated T1 revenue figure.
_STALE_PRIMARY_FILING_CLAUSE = "Missing primary filing sources."


def reconcile_financial_data_summary(
    financial_data_summary: dict[str, Any] | None,
    *,
    evidence: FinancialEvidenceState,
) -> dict[str, Any] | None:
    """Move categories the evidence actually closed from missing -> available.

    The FinancialDataAgent runs at workflow node 5, against the provider
    snapshot only. It cannot see an issuer document that is ingested three nodes
    later. Its output is not wrong at the time it is produced; it is stale by
    the time it is rendered. This is the ONE place that staleness is repaired,
    and every consumer (availability summary, missing information, bear case,
    risk agent, valuation guard, research memo) reads the repaired value.

    Returns ``None`` for a genuinely absent summary — "no summary" must stay
    distinguishable from "a summary that found nothing".
    """
    if financial_data_summary is None:
        return None
    typed = FinancialDataSummary.from_payload(financial_data_summary)
    if typed is None:
        return financial_data_summary

    newly_resolved = [
        path
        for path in evidence.resolved_field_paths
        if path in typed.missing_fields and path not in typed.available_fields
    ]
    if not newly_resolved:
        return typed.to_payload()

    available = list(typed.available_fields) + newly_resolved
    missing = [p for p in typed.missing_fields if p not in set(newly_resolved)]

    remaining_financial = [
        p for p in missing if p.startswith(FINANCIAL_FIELD_PREFIX)
    ]
    warnings: list[str] = []
    for warning in typed.warnings:
        if evidence.is_primary_backed and _STALE_PRIMARY_FILING_CLAUSE in warning:
            warning = warning.replace(
                _STALE_PRIMARY_FILING_CLAUSE,
                "A primary filing IS sourced for the financial statement facts "
                f"({category_labels(evidence.resolved_categories)}, "
                f"{evidence.best_tier}); identity and price are not.",
            )
        low = warning.lower()
        if any(marker in low for marker in _STALE_FINANCIAL_WARNING_MARKERS):
            # Rewrite rather than drop: the gap is smaller, not gone.
            if remaining_financial:
                shown = [
                    p[len(FINANCIAL_FIELD_PREFIX) :] for p in remaining_financial[:6]
                ]
                warnings.append(
                    f"{len(remaining_financial)} financial fundamental "
                    "categories still missing after primary-document ingestion "
                    f"({category_labels(shown)}"
                    f"{'…' if len(remaining_financial) > 6 else ''})."
                )
            continue
        warnings.append(warning)

    notes = list(typed.data_quality_notes)
    notes.append(
        "Post-ingestion reconciliation: "
        + evidence.describe_sourced()
        + " "
        + evidence.describe_open()
        + " Each sourced value carries its own source URL and tier and still "
        "requires human confirmation against the underlying filing."
    )

    tier_summary = dict(typed.source_tier_summary)
    for fact in evidence.resolved:
        if fact.source_tier in (TIER_T1, TIER_T2):
            tier_summary[fact.source_tier] = tier_summary.get(fact.source_tier, 0) + 1

    reconciled = FinancialDataSummary(
        available_fields=available,
        missing_fields=missing,
        data_quality_notes=notes,
        source_tier_summary=tier_summary,
        financial_context_summary=typed.financial_context_summary,
        warnings=warnings,
    )
    return reconciled.to_payload()


#: Primary-fact field -> the research-completeness schema entry it satisfies.
#: Superset of the agent's own map (which is applied at workflow time, when no
#: facts exist yet) so a gap closed by ingestion is closed everywhere.
PRIMARY_FACT_SCHEMA_FIELDS: dict[str, str] = {
    "revenue": "snapshot_financials.revenue",
    "net_income": "snapshot_financials.net_income",
    "total_debt": "snapshot_financials.total_debt",
    "cash_and_equivalents": "snapshot_financials.cash",
    "ebitda": "snapshot_financials.ebitda",
    "free_cash_flow": "financials_deep.free_cash_flow",
}

#: Research-task strings whose whole purpose is "go and read the annual report".
#: Once the annual report HAS been read they are not merely redundant, they are
#: false — they tell a reviewer the work is outstanding when it is done.
_ANNUAL_REPORT_TASK_MARKERS = (
    "source latest annual report",
    "source t1 primary filings",
)


def reconcile_research_completeness(
    research_completeness_summary: dict[str, Any] | None,
    *,
    evidence: FinancialEvidenceState,
    primary_facts: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Close the schema gaps that validated primary facts actually satisfy.

    Fact-centric gap reconciliation: a blocking gap survives only while the
    fact behind it is genuinely absent. Gaps for still-missing fields are
    untouched, and the "read the annual report" task is REPLACED (not deleted)
    by a precise statement of what that report still has not given us.
    """
    if research_completeness_summary is None:
        return None
    summary = dict(research_completeness_summary)

    satisfied: set[str] = set()
    for fact in _qualifying_fact_rows(primary_facts):
        name = str(fact.get("field") or fact.get("field_name"))
        if not fact.get("source_url"):
            continue
        entry = PRIMARY_FACT_SCHEMA_FIELDS.get(name)
        if entry:
            satisfied.add(entry)
    if not satisfied and not evidence.is_primary_backed:
        return summary

    def _mentions_satisfied(text: str) -> bool:
        return any(entry in text for entry in satisfied)

    for key in ("blocking_gaps", "non_blocking_gaps"):
        rows = summary.get(key)
        if isinstance(rows, list):
            summary[key] = [g for g in rows if not _mentions_satisfied(str(g))]

    missing_required = summary.get("missing_required_fields")
    if isinstance(missing_required, list):
        summary["missing_required_fields"] = [
            f for f in missing_required if str(f) not in satisfied
        ]

    tasks = summary.get("next_research_tasks")
    if isinstance(tasks, list) and evidence.is_primary_backed:
        rebuilt: list[str] = []
        replacement_added = False
        for task in tasks:
            low = str(task).lower()
            if any(marker in low for marker in _ANNUAL_REPORT_TASK_MARKERS):
                if not replacement_added and evidence.open_statement_categories:
                    rebuilt.append(
                        "The issuer's own primary filing has been ingested and "
                        f"{evidence.resolved_count} category(ies) extracted "
                        f"({category_labels(evidence.resolved_categories)}); "
                        "extract the still-missing statement lines from that "
                        "same filing: "
                        f"{category_labels(evidence.open_statement_categories)}."
                    )
                    replacement_added = True
                continue
            rebuilt.append(str(task))
        summary["next_research_tasks"] = list(dict.fromkeys(rebuilt))

    summary["reconciled_against_primary_facts"] = True
    summary["primary_fact_satisfied_fields"] = sorted(satisfied)
    return summary


# ---------------------------------------------------------------------------
# The aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalResearchState:
    """The ONE post-ingestion state every human-facing surface is built from."""

    fundamentals: FundamentalsEvidence
    financial_evidence: FinancialEvidenceState
    price: PriceProvenance
    financial_data_summary: dict[str, Any] | None = None
    research_completeness_summary: dict[str, Any] | None = None
    source_quality_summary: dict[str, Any] | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    primary_facts: tuple[dict[str, Any], ...] = ()
    version: int = FINAL_RESEARCH_STATE_VERSION

    @property
    def has_primary_financial_evidence(self) -> bool:
        return self.financial_evidence.is_primary_backed

    def to_payload(self) -> dict[str, Any]:
        """Bounded, auditable diagnostics — persisted beside the report."""
        return {
            "version": self.version,
            "fundamentals_available": self.fundamentals.available,
            "fundamentals_source": self.fundamentals.source,
            "fundamentals_source_tier": self.fundamentals.source_tier,
            "financial_evidence_tier": self.financial_evidence.best_tier,
            "financial_evidence_source": self.financial_evidence.best_source,
            "resolved_financial_categories": list(
                self.financial_evidence.resolved_categories
            ),
            "open_financial_categories": list(
                self.financial_evidence.open_categories
            ),
            "primary_fact_count": len(self.primary_facts),
        }


def build_final_research_state(
    *,
    company_snapshot: dict[str, Any] | None,
    fundamentals_data: dict[str, Any] | None,
    primary_facts: list[dict[str, Any]] | None,
    financial_data_summary: dict[str, Any] | None,
    research_completeness_summary: dict[str, Any] | None = None,
    source_quality_summary: dict[str, Any] | None = None,
) -> FinalResearchState:
    """Reconcile every stale deterministic input against post-ingestion truth.

    Called ONCE, after the council has run, by the final-report generator. The
    returned state is then the only input the deterministic rebuilds accept.
    """
    fundamentals = resolve_fundamentals(
        company_snapshot,
        fundamentals_data,
        primary_facts,
        financial_fields=PRIMARY_FACT_FIELDS,
    )
    evidence = build_financial_evidence_state(
        company_snapshot=company_snapshot,
        fundamentals=fundamentals,
        primary_facts=primary_facts,
        financial_data_summary=financial_data_summary,
    )
    return FinalResearchState(
        fundamentals=fundamentals,
        financial_evidence=evidence,
        price=resolve_price_provenance(company_snapshot),
        financial_data_summary=reconcile_financial_data_summary(
            financial_data_summary, evidence=evidence
        ),
        research_completeness_summary=reconcile_research_completeness(
            research_completeness_summary,
            evidence=evidence,
            primary_facts=primary_facts,
        ),
        source_quality_summary=source_quality_summary,
        identity=((company_snapshot or {}).get("company_identity") or {}),
        primary_facts=tuple(primary_facts or []),
    )
