"""
Company Analysis Workflow — Phase 9: Analysis Council MVP.

Node structure (18 nodes + error handler):
  1.  load_company                — resolve company from DB; create agent_run record
  2.  fetch_provider_data         — call FinancialDataService (default: MockProvider)
  3.  create_source_records       — build Source DB records from provider metadata
  4.  build_company_snapshot      — assemble structured snapshot + schema draft
  5.  financial_data_agent        — structured financial data summary (deterministic)
  6.  source_quality_agent        — T1–T6 source quality assessment (deterministic)
  7.  generate_research_sections  — (OPTIONAL) LLM draft sections; skipped by default
  8.  create_citations            — create Citation records with field_path/source_tier/data_quality
  9.  validate_report_schema      — call validate_real_asset_report(); store result
  10. research_completeness_agent — schema-gap analysis; next research tasks
  11. citation_validator_v2       — upgraded citation + datapoint source validation
  12. bull_case_agent             — positive thesis elements from research package (Phase 9)
  13. bear_case_agent             — negative thesis elements; challenges bull case (Phase 9)
  14. risk_agent                  — structured risk categories incl. data/source risks (Phase 9)
  15. valuation_guard_agent       — blocks premature valuation conclusions (Phase 9)
  16. investment_committee_chair  — synthesises council; assigns provisional status (Phase 9)
  17. save_draft_report           — save draft report with all council outputs
  18. log_agent_steps             — mark agent_run completed; final step logging
  handle_error                    — marks agent_run failed on any unhandled error

Design rules enforced:
  - All Phase 9 Analysis Council nodes (12–16) are deterministic — no LLM calls.
  - Phase 9 nodes are non-fatal — exceptions are caught; workflow always completes.
  - No BUY/SELL/HOLD/WATCH/REJECT/SHORTLIST from any node.
  - No price target, fair value, or valuation conclusion from any node.
  - Provisional internal status must be one of the five allowed internal workflow statuses.
  - LLM calls are opt-in: use_llm=False by default; all CI tests run offline.
  - Mock provider is the default; all CI tests run offline with no credentials.
  - Every node logs an agent_step (input + output JSON).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_council.bear_case_agent import (
    bear_case_output_to_dict,
    run_bear_case_agent,
)
from app.agents.analysis_council.bull_case_agent import (
    bull_case_output_to_dict,
    run_bull_case_agent,
)
from app.agents.analysis_council.investment_committee_chair import (
    committee_chair_output_to_dict,
    run_investment_committee_chair,
)
from app.agents.analysis_council.risk_agent import (
    risk_agent_output_to_dict,
    run_risk_agent,
)
from app.agents.analysis_council.score_research_attractiveness import (
    run_score_research_attractiveness,
)
from app.agents.analysis_council.valuation_guard_agent import (
    run_valuation_guard_agent,
    valuation_guard_output_to_dict,
)
from app.agents.base import CompanyAnalysisState
from app.agents.research_team.catalyst_agent import (
    catalyst_agent_output_to_dict,
    run_catalyst_agent,
)
from app.agents.research_team.citation_validator_v2 import (
    run_upgraded_citation_validator,
    upgraded_citation_validation_to_dict,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.agents.research_team.research_completeness_agent import (
    research_completeness_output_to_dict,
    run_research_completeness_agent,
)
from app.agents.research_team.source_quality_agent import (
    run_source_quality_agent,
    source_quality_output_to_dict,
)
from app.core.config import settings
from app.core.log_redaction import REDACTED, is_sensitive_key, redact_mapping
from app.core.structured_logging import log_event
from app.integrations.company_profile_enrichment import enrich_company_profile
from app.integrations.financial_data_provider import (
    DataQuality,
    FundamentalsData,
    PriceHistoryData,
    build_source_record,
)
from app.integrations.financial_data_service import FinancialDataService
from app.integrations.free_real_snapshot import (
    CompanyIdentity,
    compose_free_real_snapshot,
    summarize_price_provider_warning,
)
from app.integrations.llm_provider import get_llm_client, validate_llm_sections
from app.integrations.market_metrics_enrichment import derive_market_metrics
from app.integrations.providers.free_real_provider import (
    REASON_EXCHANGE_MISSING_IN_STATE,
    REASON_EXPLICIT_CIK_MAPPING,
    REASON_NON_US_NO_SEC_MAPPING,
    REASON_SEC_COVERED,
    REASON_TICKER_NOT_IN_SEC_INDEX,
    SOURCE_NOT_SOURCED,
)
from app.integrations.sec_issuer_registry import lookup_sec_issuer
from app.schemas.evidence_state import FinancialDataSummary
from app.schemas.report import ReportCreate
from app.schemas.source import CitationCreate, SourceCreate
from app.services import (
    agent_run_service,
    citation_service,
    company_service,
    report_service,
    source_service,
)
from app.services.catalyst_discovery_service import discover_catalysts
from app.services.exchange_registry import is_sec_eligible
from app.services.report_validation_service import validate_real_asset_report
from app.services.sources.redaction import strip_url_secrets
from app.workflows.snapshot_builder import (
    build_company_snapshot,
    build_schema_draft,
    enrich_snapshot_with_free_real,
    enrich_snapshot_with_market_metrics,
    enrich_snapshot_with_profile_enrichment,
    get_price_citation_fields,
    get_profile_citation_fields,
)

WORKFLOW_NAME = "company_analysis"
WORKFLOW_VERSION = "5.0.0"

_logger = logging.getLogger(__name__)

#: Awaited with one graph node's name as that node completes. Used by the async
#: company-research job to persist honest stage progress; never used to compute
#: a percentage, because the graph does not know how long a node will take.
NodeProgressCallback = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Phase 32A — machine-readable analysis-state envelope (lossless round-trip)
# ---------------------------------------------------------------------------
#
# Historically the Phase-9 draft serialised ONLY the catalyst JSON block, so
# regenerating a current-schema report by re-parsing this markdown lost the
# company snapshot, real/mock provenance, deterministic council sections and
# financial facts. This bounded, secret-stripped envelope carries exactly the
# structured keys the final-report adapter reads
# (``_extract_workflow_state_from_report``) so the round-trip is lossless.
#
# Controls: the block is FLAT (adapter keys at the top level, so a plain
# ``content.update(block)`` merges them), it EXCLUDES ``catalyst_discovery``
# (that stays in its own block — RC-3), it fetches no network, and it serialises
# only structured/bounded fields — never raw document text, prompts or evidence
# bodies. Forbidden-term neutralisation is deliberately NOT applied: the restored
# sections stay UNDER the final-report safety gate so a poisoned council summary
# is caught there (RC-1).

# Keys the adapter reads, minus catalyst_discovery (its own block, RC-3).
_STATE_ENVELOPE_KEYS: tuple[str, ...] = (
    "company_snapshot",
    "financial_data_summary",
    "source_quality_summary",
    "research_completeness_summary",
    "upgraded_citation_validation",
    "bull_case_summary",
    "bear_case_summary",
    "risk_summary",
    "valuation_guard_summary",
    "committee_chair_summary",
    "fundamentals_data",
    "fundamentals_available",
    "schema_validation_result",
    "source_tier",
)

_ENVELOPE_LIST_CAP = 20             # mirror the [:20] cap the writer already uses
_ENVELOPE_STR_CAP = 4000            # bound any single string (no raw document bodies)
_ENVELOPE_MAX_DEPTH = 8             # bound recursion on pathological structures
_ENVELOPE_TOTAL_CHAR_CAP = 120_000  # bound the whole serialised block
# Heaviest-first shed order when the whole block is over the size cap. Identity /
# provenance-critical keys (company_snapshot, schema_validation_result,
# fundamentals_available, source_tier) are never shed.
_ENVELOPE_SHED_ORDER: tuple[str, ...] = (
    "research_completeness_summary",
    "upgraded_citation_validation",
    "source_quality_summary",
    "financial_data_summary",
    "valuation_guard_summary",
    "risk_summary",
    "bear_case_summary",
    "bull_case_summary",
    "committee_chair_summary",
    "fundamentals_data",
)


def _bound_envelope_value(value, *, depth: int = 0):
    """Recursively bound one structured value.

    Truncates lists and strings, strips credential query params from URL-bearing
    strings, redacts sensitive-keyed dict values (defence in depth beyond the
    top-level ``redact_mapping``), and drops over-deep branches. Never raises.
    """
    if depth > _ENVELOPE_MAX_DEPTH:
        return None
    if isinstance(value, str):
        stripped = strip_url_secrets(value) if "://" in value else value
        if stripped and len(stripped) > _ENVELOPE_STR_CAP:
            return stripped[:_ENVELOPE_STR_CAP] + "…[truncated]"
        return stripped
    if isinstance(value, dict):
        out: dict = {}
        for key, sub in value.items():
            ks = str(key)
            if is_sensitive_key(ks):
                out[ks] = REDACTED
            else:
                out[ks] = _bound_envelope_value(sub, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [
            _bound_envelope_value(item, depth=depth + 1)
            for item in value[:_ENVELOPE_LIST_CAP]
        ]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    # Anything else (datetime, UUID, custom object) → stringified + bounded.
    return _bound_envelope_value(str(value), depth=depth + 1)


def _envelope_size(obj) -> int:
    try:
        return len(json.dumps(obj, default=str))
    except (TypeError, ValueError):
        return _ENVELOPE_TOTAL_CHAR_CAP + 1


def build_analysis_state_envelope(state) -> dict:
    """Build the bounded, secret-stripped structured-state envelope (Phase 32A).

    Reads the same ``state`` keys the live final-report path consumes, so a
    regenerated report is byte-equivalent to the live one for these sections.
    Applies ``redact_mapping`` (null sensitive-keyed values) then a recursive
    URL-secret strip + list/string/depth/size bound. Absent keys are omitted so
    the adapter's ``.get`` reads see ``None`` (honest degradation).

    Dark-safe: returns an empty dict for a mock / unknown-provenance run (only an
    EXPLICIT real-data run, ``state["is_mock"] is False``, emits the envelope) or
    when the state carries none of the envelope keys — so the Phase-9 markdown
    stays byte-identical to the pre-Phase-32A output for those runs.
    """
    if state.get("is_mock") is not False:
        return {}
    raw: dict = {}
    for key in _STATE_ENVELOPE_KEYS:
        val = state.get(key)
        if val is not None:
            raw[key] = val
    if not raw:
        return {}
    # (a) redact values whose KEY name is sensitive (tokens / secrets / …).
    redacted = redact_mapping(raw)
    # (b) bound lists/strings/depth + strip credential query params from URLs.
    envelope = {k: _bound_envelope_value(v) for k, v in redacted.items()}
    # (c) bound the whole serialised block — shed the heaviest optional keys
    # honestly rather than emit an unbounded payload.
    dropped: list[str] = []
    shed = list(_ENVELOPE_SHED_ORDER)
    while _envelope_size(envelope) > _ENVELOPE_TOTAL_CHAR_CAP and shed:
        key = shed.pop(0)
        if key in envelope:
            del envelope[key]
            dropped.append(key)
    if dropped:
        envelope["_envelope_truncated_keys"] = dropped
    return envelope


async def _lookup_gleif_profile(legal_name: str | None):
    """
    Best-effort GLEIF LEI lookup by legal name (Phase 19.4).

    Non-fatal: any network / parse error returns None so the workflow proceeds
    without an LEI. GLEIF is a free public registry (no API key). The enrichment
    layer guards attribution with a legal-name match before accepting the LEI.
    """
    if not legal_name:
        return None
    try:
        from app.integrations.providers.gleif_provider import GleifProvider

        results = await GleifProvider().search_by_name(legal_name, page_size=1)
        return results[0] if results else None
    except Exception:
        return None


async def _apply_phase19_4_enrichment(
    snapshot: dict,
    profile,
    prices,
    company,
    ticker: str,
) -> tuple[dict, dict | None]:
    """
    Apply Phase 19.4 identity/profile + derived market-metric enrichment.

    Returns the enriched snapshot and the market-metrics dict (or None). All
    steps are non-fatal — enrichment never blocks the workflow.
    """
    reporting_currency = (snapshot.get("profile") or {}).get("reporting_currency") or "USD"
    legal_name = (snapshot.get("company_identity") or {}).get("legal_name")

    # ── Identity / profile enrichment (SEC profile + best-effort GLEIF) ──
    try:
        gleif_profile = await _lookup_gleif_profile(legal_name)
        prof = enrich_company_profile(
            ticker=ticker,
            legal_name=legal_name,
            exchange=(snapshot.get("company_identity") or {}).get("exchange"),
            country=(snapshot.get("company_identity") or {}).get("country_domicile"),
            cik=getattr(company, "sec_cik", None) if company else None,
            db_sector=getattr(company, "sector", None) if company else None,
            db_industry=getattr(company, "industry", None) if company else None,
            sec_profile=profile,
            gleif_profile=gleif_profile,
        )
        snapshot = enrich_snapshot_with_profile_enrichment(snapshot, prof.to_dict())
    except Exception:
        # Enrichment is advisory only — never fail the workflow on it.
        pass

    # ── Derived market metrics (free price history + SEC fundamentals) ───
    market_metrics_dict: dict | None = None
    try:
        mm = derive_market_metrics(
            ticker=ticker,
            fundamentals_summary=snapshot.get("fundamentals_summary"),
            price_history=prices if (prices and prices.price_points) else None,
            reporting_currency=reporting_currency,
        )
        market_metrics_dict = mm.to_dict()
        snapshot = enrich_snapshot_with_market_metrics(snapshot, market_metrics_dict)
    except Exception:
        market_metrics_dict = None

    return snapshot, market_metrics_dict


def _resolve_prompt_path() -> pathlib.Path:
    # Walk up the directory tree looking for packages/prompts/; avoids hard-coded
    # depth which breaks on Azure App Service (shallower extraction path).
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = (
            parent / "packages" / "prompts" / "research" / "phase7_company_research_v1.md"
        )
        if candidate.exists():
            return candidate
    return here  # not found — _load_prompt_template falls back to inline prompt


_PROMPT_PATH = _resolve_prompt_path()


def _load_prompt_template() -> str:
    """Load the versioned prompt template from packages/prompts/."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    # Fallback inline minimal prompt if file not found (should not happen in normal usage)
    return (
        "Generate research sections for the following company:\n\n"
        "{{COMPANY_CONTEXT}}\n\n"
        "Output JSON with: thesis_summary_draft, business_overview_draft, "
        "missing_information, self_critique_limitations. "
        "No rating. No price target. No invented numbers."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report_slug(ticker: str, run_id: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")
    short_id = run_id.replace("-", "")[:8]
    return f"company-analysis-{base}-{short_id}"


def _clean_exchange(value: object) -> str | None:
    """
    Return ``value`` only if it is a usable exchange code.

    SEC eligibility is decided from this value, so anything that is not a
    non-empty string is treated as "no exchange" rather than being coerced.
    A non-string here would otherwise flow into ``is_sec_eligible`` and produce
    an arbitrary answer about whether a foreign ticker may hit the US index.
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _build_data_coverage(
    *,
    exchange: str | None,
    profile: object,
    fundamentals: object,
    price_source: str | None,
    exchange_unresolved: bool = False,
) -> dict:
    """
    Describe how sourced this company's data actually is.

    Distinguishes "we looked and there is nothing" from "we cannot look here",
    so downstream scoring never reads an unsupported venue as a negative
    judgement about the company. See docs/PHASE_27_1_SPEC.md §3.6.
    """
    profile_not_sourced = (
        getattr(profile, "meta", None) is not None
        and profile.meta.provider_name == "free_real_not_sourced"
    )
    fundamentals_not_sourced = fundamentals is None or not fundamentals.datapoints

    has_mapping = lookup_sec_issuer(profile.ticker if profile else "", exchange) is not None

    # Fail closed when the venue is unknown. is_sec_eligible(None) is True so
    # legacy ticker-only callers keep working, but that default is only safe
    # when the caller genuinely has no exchange concept — never when we expected
    # one and lost it. An unresolved exchange is reported as not eligible.
    sec_eligible = (not exchange_unresolved) and is_sec_eligible(exchange)

    if has_mapping:
        reason = REASON_EXPLICIT_CIK_MAPPING
    elif exchange_unresolved:
        reason = REASON_EXCHANGE_MISSING_IN_STATE
    elif not sec_eligible:
        reason = REASON_NON_US_NO_SEC_MAPPING
    elif fundamentals_not_sourced:
        reason = REASON_TICKER_NOT_IN_SEC_INDEX
    else:
        reason = REASON_SEC_COVERED

    # Only an unreachable venue counts as requiring human research. A US issuer
    # that simply has thin XBRL data is a different, already-handled case.
    requires_human_research = profile_not_sourced or (not sec_eligible and not has_mapping)

    return {
        "exchange": exchange,
        "sec_eligible": sec_eligible,
        "has_explicit_cik_mapping": has_mapping,
        "profile_source": SOURCE_NOT_SOURCED if profile_not_sourced else "sec_edgar",
        "fundamentals_source": (
            SOURCE_NOT_SOURCED if (fundamentals_not_sourced or requires_human_research)
            else "sec_edgar_xbrl"
        ),
        "price_source": price_source,
        "reason": reason,
        "requires_human_research": requires_human_research,
    }


def _build_placeholder_analysis(state: CompanyAnalysisState) -> dict:
    """Kept for backward-compatibility with existing tests."""
    ticker = state.get("ticker") or "UNKNOWN"
    company_name = state.get("company_name") or ticker
    sector = state.get("company_sector") or "Unknown sector"

    return {
        "ticker": ticker,
        "company_name": company_name,
        "rating": "WATCH",
        "confidence_score": 0.50,
        "risk_score": 0.50,
        "investment_horizon_months": 24,
        "thesis": (
            f"{company_name} is being added to the research pipeline for "
            f"initial review. Sector: {sector}. "
            "Full LLM-powered analysis will run once Azure OpenAI is configured."
        ),
        "bull_case": [
            "Company has been identified as a candidate for further research.",
            "Sector exposure may align with macro tailwinds.",
        ],
        "bear_case": [
            "No financial data has been verified yet.",
            "Analysis is placeholder — do not use for investment decisions.",
        ],
        "catalysts": [
            "Completion of full research workflow.",
            "Analyst review and data sourcing.",
        ],
        "financial_metrics": {},
        "citations": [],
        "missing_information": [
            "Financial metrics not yet sourced.",
            "LLM analysis not yet run.",
            "Filings not yet reviewed.",
        ],
        "decision_explanation": (
            "WATCH rating assigned as default pending full analysis. "
            "This is a placeholder output — human review required before any action."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_placeholder": True,
    }


# ---------------------------------------------------------------------------
# Workflow factory
# ---------------------------------------------------------------------------


def build_company_analysis_graph(
    db: AsyncSession,
    provider_name: str | None = None,
    use_llm: bool = False,
    llm_provider: str | None = None,
):
    """
    Return a compiled LangGraph graph with all Phase 7 nodes.

    provider_name — override config default (None = use FINANCIAL_DATA_PROVIDER config).
    Default is "mock" so all CI tests run offline.

    use_llm — when True, the generate_research_sections node runs after build_company_snapshot.
    Default False — safe offline mode, no LLM calls, CI-safe.

    llm_provider — override config default for LLM (None = use LLM_PROVIDER config).
    Default is "mock" so all CI tests run without Azure credentials.
    """

    _run_holder: dict = {}

    # ------------------------------------------------------------------ #
    # Node 1: load_company                                                #
    # ------------------------------------------------------------------ #
    async def node_load_company(state: CompanyAnalysisState) -> dict:
        run = await agent_run_service.create_agent_run(
            db,
            workflow_name=WORKFLOW_NAME,
            workflow_version=WORKFLOW_VERSION,
            trigger_type="manual",
        )
        _run_holder["run"] = run

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="WorkflowController",
            step_name="load_company",
            input_data={
                "company_id": state.get("company_id"),
                "ticker": state.get("ticker"),
                "exchange": state.get("exchange"),
                "provider_name": state.get("provider_name"),
            },
        )

        company = None
        company_id = state.get("company_id")
        ticker = state.get("ticker")
        exchange = state.get("exchange")

        if company_id:
            company = await company_service.get_company(db, uuid.UUID(company_id))
        elif ticker and exchange:
            company = await company_service.get_company_by_ticker(db, ticker, exchange)

        if not company:
            await agent_run_service.fail_agent_step(db, step, "Company not found in database")
            await agent_run_service.fail_agent_run(db, run, "Company not found in database")
            return {"status": "failed", "error": "Company not found in database"}

        _run_holder["company"] = company

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={"company_id": str(company.id), "company_name": company.name},
        )

        return {
            "agent_run_id": str(run.id),
            "company_id": str(company.id),
            "company_name": company.name,
            "company_sector": company.sector,
            "company_description": company.description,
            "ticker": company.ticker,
            # Phase 27.1A hotfix: the exchange MUST be present in state.
            # Callers that pass only company_id (every discovery run does) left
            # state["exchange"] as None, so node_fetch_provider_data asked the
            # providers for a ticker with no venue. is_sec_eligible(None) is
            # True by design for legacy ticker-only flows, so the SEC gate never
            # fired and BA.LSE resolved against the US ticker index to Boeing.
            #
            # An exchange the caller stated explicitly wins; the company row
            # (exchange is NOT NULL) only fills the gap. Never overwrite a
            # caller's venue with the stored one — they may legitimately differ,
            # and the caller is the more specific intent.
            "exchange": _clean_exchange(exchange) or _clean_exchange(company.exchange),
            "status": "running",
            "error": None,
        }

    # ------------------------------------------------------------------ #
    # Node 2: fetch_provider_data                                         #
    # ------------------------------------------------------------------ #
    async def node_fetch_provider_data(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        pname = state.get("provider_name") or provider_name
        # Preserve the originally-requested provider name throughout the workflow.
        requested_pname: str = pname or "mock"
        ticker = state.get("ticker") or "UNKNOWN"

        # Phase 27.1A hotfix — fail-closed exchange resolution.
        #
        # The venue decides whether SEC EDGAR may be consulted at all, so it
        # must never be silently absent here: a missing exchange reads as the
        # legacy US default and sends a foreign ticker into the US-registrant
        # index, which is how BA.LSE became Boeing. The loaded Company row is
        # authoritative (exchange is NOT NULL and the table is keyed on
        # (ticker, exchange)), so recover from it rather than trusting state.
        company_row = _run_holder.get("company")
        state_exchange = _clean_exchange(state.get("exchange"))
        row_exchange = _clean_exchange(getattr(company_row, "exchange", None))
        exchange = state_exchange or row_exchange
        exchange_recovered = bool(not state_exchange and row_exchange)
        # Only fail closed when we had a company row that should have carried a
        # venue. A pure ticker-only caller with no company context keeps the
        # legacy behavior so AAPL/MSFT/NVDA do not regress.
        exchange_unresolved = bool(
            not exchange and company_row is not None and row_exchange is None
        )

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="FinancialDataAgent",
            step_name="fetch_provider_data",
            input_data={
                "ticker": ticker,
                "exchange": exchange,
                "provider_name": requested_pname,
            },
        )

        try:
            svc = FinancialDataService(provider_name=pname)
            profile = await svc.get_company_profile(ticker, exchange)

            prices: PriceHistoryData | None = None
            caps = [c.value if hasattr(c, "value") else c for c in svc.get_capabilities()]
            if "price_history" in caps:
                try:
                    prices = await svc.get_price_history(ticker, exchange)
                except NotImplementedError:
                    prices = None
                except Exception as price_exc:
                    # Non-fatal — workflow continues without price data
                    prices = None
                    _run_holder.setdefault("provider_warnings_pre", []).append(
                        f"Price fetch failed (non-fatal): {price_exc}"
                    )

            # Phase 13+19.2: fetch fundamentals for eodhd, free_real, eodhd_free_real
            fundamentals: FundamentalsData | None = None
            fundamentals_warnings: list[str] = []
            if pname in ("eodhd", "free_real", "eodhd_free_real") and "fundamentals" in caps:
                try:
                    fundamentals = await svc.get_fundamentals(ticker, exchange)
                except NotImplementedError:
                    fundamentals_warnings.append(
                        f"{pname} fundamentals: NotImplementedError — skipped."
                    )
                except Exception as fund_exc:
                    fundamentals_warnings.append(
                        f"{pname} fundamentals fetch failed (non-fatal): {fund_exc}"
                    )

            # ── Phase 19.2: compose FreeRealSnapshot for composite providers ──
            free_real_snap_dict: dict | None = None
            trend_signal_summary: dict | None = None
            contributing_providers: list[str] = []
            provider_warnings: list[str] = list(fundamentals_warnings)
            provider_warnings.extend(_run_holder.pop("provider_warnings_pre", []))

            # Phase 19.2.1: surface the Stooq→EODHD price fallback reason from the
            # raw price fetch. This must happen against the raw `prices` object so
            # the "no usable price history" case (empty price_data → None passed to
            # the composer) still reaches provider_warnings. The composer de-dupes
            # against the non-empty (fallback-succeeded) case.
            price_fallback_warning = summarize_price_provider_warning(prices)
            if price_fallback_warning:
                provider_warnings.append(price_fallback_warning)

            is_mock: bool

            if pname in ("free_real", "eodhd_free_real"):
                identity = CompanyIdentity(
                    ticker=ticker,
                    legal_name=profile.legal_name,
                    exchange=exchange,
                    country_domicile=profile.country_domicile,
                    sector=profile.sector,
                    industry=profile.industry,
                )
                snap = await compose_free_real_snapshot(
                    identity=identity,
                    price_data=prices if (prices and prices.price_points) else None,
                    fundamentals_data=fundamentals,
                    provider_stack=pname,
                    extra_warnings=provider_warnings,
                )
                free_real_snap_dict = snap.to_dict()
                if snap.trend_signals:
                    ts = snap.trend_signals
                    trend_signal_summary = {
                        "momentum_label": ts.momentum_label,
                        "return_1m": ts.return_1m,
                        "return_3m": ts.return_3m,
                        "return_6m": ts.return_6m,
                        "pct_above_ma50": ts.pct_above_ma50,
                        "pct_above_ma200": ts.pct_above_ma200,
                        "relative_strength": ts.relative_strength,
                        "source_tier": ts.source_tier,
                        "computed_at": ts.computed_at,
                        "data_warnings": ts.data_warnings,
                    }
                contributing_providers = snap.contributing_providers
                provider_warnings = snap.warnings
                is_mock = snap.is_mock
            else:
                is_mock = profile.meta.is_mock

            # ── Phase 27.1A: data_coverage — how sourced is this company? ──
            # A non-US venue with no verified CIK mapping degrades to
            # not_sourced rather than borrowing an unrelated US issuer's data.
            # Recorded so downstream scoring reads sparse data as "not sourced",
            # not as a negative judgement about the company.
            data_coverage = _build_data_coverage(
                exchange=exchange,
                profile=profile,
                fundamentals=fundamentals,
                price_source=(
                    prices.meta.provider_name if (prices and prices.price_points) else None
                ),
                exchange_unresolved=exchange_unresolved,
            )
            if exchange_recovered:
                provider_warnings = list(provider_warnings) + [
                    f"exchange_recovered_from_company_record: workflow state carried "
                    f"no exchange for {ticker}; used '{exchange}' from the company "
                    "record so SEC eligibility was evaluated against the real venue."
                ]
            if exchange_unresolved:
                provider_warnings = list(provider_warnings) + [
                    f"exchange_missing_in_provider_state: no exchange could be "
                    f"determined for {ticker}. SEC data is treated as not_sourced "
                    "rather than assuming a US listing, because a ticker-only SEC "
                    "lookup can return an unrelated US issuer."
                ]
            if data_coverage["requires_human_research"]:
                provider_warnings = list(provider_warnings) + [
                    f"Fundamentals not_sourced for {ticker} on exchange "
                    f"'{exchange}': {data_coverage['reason']}. SEC EDGAR does not "
                    "cover this venue and no verified CIK mapping exists. "
                    "Company identity and financials require human research."
                ]

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "provider_name": requested_pname,
                    "is_mock": is_mock,
                    "ticker": profile.ticker,
                    "legal_name": profile.legal_name,
                    "price_points_count": len(prices.price_points) if prices else 0,
                    "fundamentals_datapoints_count": (
                        len(fundamentals.datapoints) if fundamentals else 0
                    ),
                    "fundamentals_warnings": fundamentals_warnings,
                    "contributing_providers": contributing_providers,
                    "trend_signal_available": trend_signal_summary is not None,
                    "provider_warnings_count": len(provider_warnings),
                },
            )

            # Stash provider objects in holder for later nodes
            _run_holder["profile"] = profile
            _run_holder["prices"] = prices
            _run_holder["fundamentals"] = fundamentals
            _run_holder["free_real_snapshot"] = free_real_snap_dict
            _run_holder["trend_signal_summary"] = trend_signal_summary

            return {
                "provider_name": requested_pname,   # preserve requested provider name
                "requested_provider_name": requested_pname,
                "contributing_providers": contributing_providers,
                "free_real_snapshot": free_real_snap_dict,
                "trend_signal_summary": trend_signal_summary,
                "provider_warnings": provider_warnings or None,
                "is_mock": is_mock,
                "analysis_output": _build_placeholder_analysis(state),
                "fundamentals_available": (
                    fundamentals is not None and not data_coverage["requires_human_research"]
                ),
                "fundamentals_warnings": fundamentals_warnings or None,
                "data_coverage": data_coverage,
            }

        except (ValueError, Exception) as exc:
            error_msg = f"fetch_provider_data failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            await agent_run_service.fail_agent_run(db, run, error_msg)
            return {"status": "failed", "error": error_msg}

    # ------------------------------------------------------------------ #
    # Node 3: create_source_records                                       #
    # ------------------------------------------------------------------ #
    async def node_create_source_records(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        fundamentals = _run_holder.get("fundamentals")
        pname = state.get("provider_name") or "mock"
        ticker = state.get("ticker") or "UNKNOWN"

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SourceRecordAgent",
            step_name="create_source_records",
            input_data={"ticker": ticker, "provider_name": pname},
        )

        source_ids: list[str] = []
        provider_source_id: str | None = None
        price_source_id: str | None = None
        fundamentals_source_id: str | None = None

        # Source record for company profile data
        profile_attrs = build_source_record(
            meta=profile.meta,
            source_url=profile.source_url,
            title=f"{profile.meta.provider_name} — company profile: {ticker}",
            data_quality=profile.data_quality
            if isinstance(profile.data_quality, DataQuality)
            else DataQuality(profile.data_quality),
        )
        profile_source, _ = await source_service.get_or_create_source(
            db,
            SourceCreate(
                source_type=profile_attrs.source_type,
                title=profile_attrs.title,
                url=profile_attrs.url,
                publisher=profile_attrs.publisher,
                retrieved_at=profile_attrs.retrieved_at,
                credibility_score=profile_attrs.credibility_score,
            ),
        )
        source_ids.append(str(profile_source.id))
        provider_source_id = str(profile_source.id)

        # Source record for price history (if fetched with data points, T5)
        if prices and prices.price_points:
            price_attrs = build_source_record(
                meta=prices.meta,
                source_url=prices.source_url,
                title=f"{prices.meta.provider_name} — price history: {ticker}",
                data_quality=prices.data_quality
                if isinstance(prices.data_quality, DataQuality)
                else DataQuality(prices.data_quality),
            )
            price_source, _ = await source_service.get_or_create_source(
                db,
                SourceCreate(
                    source_type=price_attrs.source_type,
                    title=price_attrs.title,
                    url=price_attrs.url,
                    publisher=price_attrs.publisher,
                    retrieved_at=price_attrs.retrieved_at,
                    credibility_score=price_attrs.credibility_score,
                ),
            )
            source_ids.append(str(price_source.id))
            price_source_id = str(price_source.id)

        # Phase 19.2: source record for SEC EDGAR fundamentals (T2) for composite providers
        if fundamentals and fundamentals.datapoints and pname in ("free_real", "eodhd_free_real"):
            fund_attrs = build_source_record(
                meta=fundamentals.meta,
                source_url=None,
                title=f"SEC EDGAR XBRL — fundamentals: {ticker} (T2_regulator_or_gov)",
                data_quality=DataQuality.B_single_credible,
            )
            fund_source, _ = await source_service.get_or_create_source(
                db,
                SourceCreate(
                    source_type=fund_attrs.source_type,
                    title=fund_attrs.title,
                    url=fund_attrs.url,
                    publisher=fund_attrs.publisher,
                    retrieved_at=fund_attrs.retrieved_at,
                    credibility_score=fund_attrs.credibility_score,
                ),
            )
            source_ids.append(str(fund_source.id))
            fundamentals_source_id = str(fund_source.id)

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "source_ids": source_ids,
                "provider_source_id": provider_source_id,
                "fundamentals_source_id": fundamentals_source_id,
            },
        )

        _run_holder["provider_source_id"] = provider_source_id
        _run_holder["price_source_id"] = price_source_id
        _run_holder["fundamentals_source_id"] = fundamentals_source_id

        return {
            "source_ids": source_ids,
            "provider_source_id": provider_source_id,
            "price_source_id": price_source_id,
            "placeholder_source_id": None,
        }

    # ------------------------------------------------------------------ #
    # Node 4: build_company_snapshot                                      #
    # ------------------------------------------------------------------ #
    async def node_build_company_snapshot(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        fundamentals = _run_holder.get("fundamentals")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SnapshotBuilder",
            step_name="build_company_snapshot",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "is_mock": state.get("is_mock"),
                "fundamentals_available": fundamentals is not None,
            },
        )

        snapshot = build_company_snapshot(profile=profile, prices=prices, fundamentals=fundamentals)

        # Phase 19.2: enrich snapshot with composite free_real data
        # (trend signals, contributing providers, SEC-EDGAR fundamentals, T5 price metadata)
        free_real_snap = _run_holder.get("free_real_snapshot")
        if free_real_snap:
            snapshot = enrich_snapshot_with_free_real(snapshot, free_real_snap)

        # Phase 19.4: identity/profile + derived market-metric enrichment.
        pname = state.get("provider_name") or "mock"
        market_metrics_dict: dict | None = None
        if pname in ("free_real", "eodhd_free_real"):
            snapshot, market_metrics_dict = await _apply_phase19_4_enrichment(
                snapshot=snapshot,
                profile=profile,
                prices=prices,
                company=_run_holder.get("company"),
                ticker=state.get("ticker") or "UNKNOWN",
            )
        _run_holder["market_metrics_summary"] = market_metrics_dict

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "missing_fields_count": len(snapshot.get("missing_fields", [])),
                "missing_fields": snapshot.get("missing_fields", []),
                "is_mock": snapshot.get("is_mock"),
                "price_history_available": snapshot.get("price_history_summary", {}).get(
                    "available", False
                ),
                "fundamentals_summary_available": snapshot.get("fundamentals_summary") is not None,
                "trend_signal_available": snapshot.get("trend_signal_summary") is not None,
                "contributing_providers": (
                    (snapshot.get("provider_metadata") or {}).get("contributing_providers") or []
                ),
            },
        )

        _run_holder["snapshot"] = snapshot
        return {"company_snapshot": snapshot}

    # ------------------------------------------------------------------ #
    # Node 5: financial_data_agent  (Phase 8 Research Team)              #
    # ------------------------------------------------------------------ #
    async def node_financial_data_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        source_ids = state.get("source_ids") or []

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="FinancialDataAgent",
            step_name="financial_data_agent",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "source_ids_count": len(source_ids),
            },
        )

        try:
            output = run_financial_data_agent(
                company_snapshot=snapshot,
                source_ids=source_ids,
            )
            _typed_fds = FinancialDataSummary.from_agent_output(output)
            output_dict = financial_data_agent_output_to_dict(output)
            _run_holder["financial_data_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    # Phase B: counts come from the typed contract (which
                    # derives them), not from a second hand-rolled len().
                    "available_count": _typed_fds.available_count,
                    "missing_count": _typed_fds.missing_count,
                    "warnings_count": _typed_fds.warnings_count,
                    "source_tier_summary": _typed_fds.source_tier_summary,
                },
            )

            return {
                "financial_data_summary": output_dict,
            }

        except Exception as exc:
            error_msg = f"financial_data_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            # Non-fatal — workflow continues
            # Phase B: the failure fallback emits the CANONICAL shape too, so
            # a degraded run and a healthy run have the same contract.
            fallback = {
                **FinancialDataSummary(
                    data_quality_notes=[error_msg],
                    financial_context_summary=f"FinancialDataAgent failed: {exc}",
                    warnings=[error_msg],
                ).to_payload(),
            }
            _run_holder["financial_data_summary"] = fallback
            return {"financial_data_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 6: source_quality_agent  (Phase 8 Research Team)              #
    #                                                                    #
    # NOTE (Problem D): this node runs before Node 8's citations exist,  #
    # before document ingestion, and before any LLM council runs, so    #
    # ``source_quality_summary`` here is only an early best-effort       #
    # estimate from the company snapshot alone (no ``citation_source_    #
    # tiers`` to pass yet). It is intentionally superseded at final-     #
    # report-assembly time (``final_report_generator._generate_and_     #
    # save``, after the council runs) by a fresh recomputation from real #
    # citation/evidence state — see                                     #
    # ``_recompute_fresh_source_quality_summary``. Do not treat this     #
    # node's output as the report's authoritative source-quality state. #
    # ------------------------------------------------------------------ #
    async def node_source_quality_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SourceQualityAgent",
            step_name="source_quality_agent",
            input_data={
                "ticker": state.get("ticker"),
                "provider_name": state.get("provider_name"),
                "is_mock": state.get("is_mock"),
            },
        )

        try:
            output = run_source_quality_agent(company_snapshot=snapshot)
            output_dict = source_quality_output_to_dict(output)
            _run_holder["source_quality_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "overall_source_quality": output.overall_source_quality,
                    "strong_sources_count": len(output.strong_sources),
                    "weak_sources_count": len(output.weak_sources),
                    "aggregator_only_claims_count": len(output.aggregator_only_claims),
                    "warnings_count": len(output.warnings),
                },
            )

            return {
                "source_quality_summary": output_dict,
            }

        except Exception as exc:
            error_msg = f"source_quality_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "overall_source_quality": "insufficient",
                "strong_sources": [],
                "weak_sources": [],
                "missing_primary_sources": [],
                "aggregator_only_claims": [],
                "recommended_source_upgrades": [],
                "warnings": [error_msg],
            }
            _run_holder["source_quality_summary"] = fallback
            return {"source_quality_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 7: generate_research_sections  (optional LLM node)            #
    # ------------------------------------------------------------------ #
    async def node_generate_research_sections(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        resolved_llm_provider = state.get("llm_provider") or llm_provider

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ResearchLLMAgent",
            step_name="generate_research_sections",
            input_data={
                "llm_provider": resolved_llm_provider or "config_default",
                "use_llm": state.get("use_llm"),
                "snapshot_keys": list(snapshot.keys()),
            },
        )

        # If use_llm is False, skip without calling LLM
        if not state.get("use_llm"):
            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={"skipped": True, "reason": "use_llm=False"},
            )
            return {"llm_used": False, "llm_provider": "none", "llm_sections": None}

        try:
            client = get_llm_client(resolved_llm_provider)
            prompt_template = _load_prompt_template()
            sections = await client.generate_research_sections(
                company_snapshot=snapshot,
                prompt_template=prompt_template,
            )
            safety = validate_llm_sections(sections)

            sections_dict = sections.model_dump()

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "llm_provider": client.provider_name,
                    "is_mock": client.is_mock,
                    "safety_passed": safety.passed,
                    "safety_warnings": safety.warnings,
                    "thesis_length": len(sections.thesis_summary_draft),
                    "missing_info_count": len(sections.missing_information),
                },
            )

            return {
                "llm_used": True,
                "llm_provider": client.provider_name,
                "llm_sections": sections_dict,
                "llm_section_warnings": safety.warnings if not safety.passed else [],
            }

        except Exception as exc:
            error_msg = f"generate_research_sections failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            # LLM failure is non-fatal — workflow continues without LLM sections
            return {
                "llm_used": False,
                "llm_provider": "failed",
                "llm_sections": None,
                "llm_section_warnings": [error_msg],
            }

    # ------------------------------------------------------------------ #
    # Node 8: create_citations  (was Node 6 in Phase 7)                  #
    # ------------------------------------------------------------------ #
    async def node_create_citations(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        agent_run_id = state.get("agent_run_id")
        provider_source_id = _run_holder.get("provider_source_id")
        price_source_id = _run_holder.get("price_source_id")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="CitationAgent",
            step_name="create_citations",
            input_data={
                "provider_source_id": provider_source_id,
                "price_source_id": price_source_id,
            },
        )

        citation_ids: list[str] = []

        # Citations from company profile fields
        if profile and provider_source_id:
            for desc in get_profile_citation_fields(profile):
                cit = await citation_service.create_citation(
                    db,
                    CitationCreate(
                        source_id=uuid.UUID(provider_source_id),
                        agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                        claim_text=desc["claim_text"],
                        source_quote=desc["source_quote"],
                        retrieved_at=desc["retrieved_at"],
                        field_path=desc["field_path"],
                        source_tier=desc["source_tier"],
                        data_quality=desc["data_quality"],
                    ),
                )
                citation_ids.append(str(cit.id))

        # Citations from price history (if available)
        if prices and prices.price_points and price_source_id:
            for desc in get_price_citation_fields(prices):
                cit = await citation_service.create_citation(
                    db,
                    CitationCreate(
                        source_id=uuid.UUID(price_source_id),
                        agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                        claim_text=desc["claim_text"],
                        source_quote=desc["source_quote"],
                        retrieved_at=desc["retrieved_at"],
                        field_path=desc["field_path"],
                        source_tier=desc["source_tier"],
                        data_quality=desc["data_quality"],
                    ),
                )
                citation_ids.append(str(cit.id))

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={"citation_ids": citation_ids, "citation_count": len(citation_ids)},
        )

        return {"citation_ids": citation_ids}

    # ------------------------------------------------------------------ #
    # Node 9: validate_report_schema  (was Node 7 in Phase 7)            #
    # ------------------------------------------------------------------ #
    async def node_validate_report_schema(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        profile = _run_holder.get("profile")
        prices = _run_holder.get("prices")
        agent_run_id = state.get("agent_run_id")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="SchemaValidator",
            step_name="validate_report_schema",
            input_data={"report_id_attempt": agent_run_id},
        )

        # Build a minimal schema-draft using provider data (Phase 13: includes fundamentals)
        draft = build_schema_draft(
            report_id=agent_run_id or str(uuid.uuid4()),
            snapshot=_run_holder.get("snapshot", {}),
            profile=profile,
            prices=prices,
            fundamentals=_run_holder.get("fundamentals"),
        )

        # Validate — expected to fail at this phase (many required sections absent)
        result = validate_real_asset_report(draft)
        validation_dict = result.to_dict()

        _run_holder["schema_draft"] = draft
        _run_holder["validation_result"] = validation_dict

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "schema_valid": result.is_valid,
                "error_count": len(result.errors),
                "warning_count": len(result.warnings),
                "first_error": result.errors[0] if result.errors else None,
            },
        )

        return {
            "schema_validation_result": validation_dict,
            "schema_valid": result.is_valid,
        }

    # ------------------------------------------------------------------ #
    # Node 10: research_completeness_agent  (Phase 8 Research Team)      #
    # ------------------------------------------------------------------ #
    async def node_research_completeness_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        schema_draft = _run_holder.get("schema_draft")
        validation_result = _run_holder.get("validation_result", {})
        schema_errors = validation_result.get("errors", [])

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ResearchCompletenessAgent",
            step_name="research_completeness_agent",
            input_data={
                "ticker": state.get("ticker"),
                "schema_valid": state.get("schema_valid"),
                "schema_error_count": len(schema_errors),
                "draft_sections": list(schema_draft.keys()) if schema_draft else [],
            },
        )

        try:
            output = run_research_completeness_agent(
                company_snapshot=snapshot,
                schema_draft=schema_draft,
                schema_validation_errors=schema_errors,
            )
            output_dict = research_completeness_output_to_dict(output)
            _run_holder["research_completeness_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "complete_sections": output.complete_sections,
                    "incomplete_sections_count": len(output.incomplete_sections),
                    "missing_required_fields_count": len(output.missing_required_fields),
                    "blocking_gaps_count": len(output.blocking_gaps),
                    "next_tasks_count": len(output.next_research_tasks),
                },
            )

            return {
                "research_completeness_summary": output_dict,
            }

        except Exception as exc:
            error_msg = f"research_completeness_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "complete_sections": [],
                "incomplete_sections": [],
                "missing_required_fields": [],
                "next_research_tasks": [],
                "blocking_gaps": [error_msg],
                "non_blocking_gaps": [],
            }
            _run_holder["research_completeness_summary"] = fallback
            return {"research_completeness_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 11: citation_validator_v2  (Phase 8 Research Team)            #
    # ------------------------------------------------------------------ #
    async def node_citation_validator_v2(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        schema_draft = _run_holder.get("schema_draft")
        agent_run_id = state.get("agent_run_id")

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="CitationValidatorV2",
            step_name="citation_validator_v2",
            input_data={
                "ticker": state.get("ticker"),
                "citation_ids_count": len(state.get("citation_ids") or []),
                "schema_draft_sections": list(schema_draft.keys()) if schema_draft else [],
            },
        )

        try:
            # Fetch citation records created in this run for source_tier info
            citation_records: list[dict] = []
            if agent_run_id:
                try:
                    run_citations = await citation_service.list_citations_for_agent_run(
                        db, uuid.UUID(agent_run_id)
                    )
                    citation_records = [
                        {
                            "id": str(c.id),
                            "field_path": c.field_path,
                            "source_tier": c.source_tier,
                            "data_quality": c.data_quality,
                        }
                        for c in run_citations
                    ]
                except Exception:
                    pass  # Non-fatal if citation fetch fails

            output = run_upgraded_citation_validator(
                company_snapshot=snapshot,
                schema_draft=schema_draft,
                citation_records=citation_records,
            )
            output_dict = upgraded_citation_validation_to_dict(output)
            _run_holder["upgraded_citation_validation"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "status": output.status,
                    "approved_claims_count": len(output.approved_claims),
                    "missing_citations_count": len(output.missing_citations),
                    "weak_warnings_count": len(output.weak_citation_warnings),
                    "unsupported_numbers_count": len(output.unsupported_number_warnings),
                    "tier_warnings_count": len(output.source_tier_warnings),
                },
            )

            return {
                "upgraded_citation_validation": output_dict,
            }

        except Exception as exc:
            error_msg = f"citation_validator_v2 failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "status": "warnings",
                "approved_claims": [],
                "missing_citations": [],
                "weak_citation_warnings": [error_msg],
                "unsupported_number_warnings": [],
                "source_tier_warnings": [],
            }
            _run_holder["upgraded_citation_validation"] = fallback
            return {"upgraded_citation_validation": fallback}

    # ------------------------------------------------------------------ #
    # Node 12: bull_case_agent  (Phase 9 Analysis Council)               #
    # ------------------------------------------------------------------ #
    async def node_bull_case_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        llm_sections = state.get("llm_sections") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="BullCaseAgent",
            step_name="bull_case_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "llm_used": state.get("llm_used"),
            },
        )

        try:
            output = run_bull_case_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
                research_completeness_summary=research_completeness_summary,
                llm_sections=llm_sections if llm_sections else None,
            )
            output_dict = bull_case_output_to_dict(output)
            _run_holder["bull_case_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "positive_thesis_points_count": len(output.positive_thesis_points),
                    "potential_tailwinds_count": len(output.potential_tailwinds),
                    "confidence_level": output.confidence_level,
                    "warnings_count": len(output.warnings),
                },
            )
            return {"bull_case_summary": output_dict}

        except Exception as exc:
            error_msg = f"bull_case_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "positive_thesis_points": [],
                "potential_tailwinds": [],
                "evidence_used": [],
                "assumptions": [],
                "missing_evidence": [error_msg],
                "confidence_level": "low",
                "warnings": [error_msg],
            }
            _run_holder["bull_case_summary"] = fallback
            return {"bull_case_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 13: bear_case_agent  (Phase 9 Analysis Council)               #
    # ------------------------------------------------------------------ #
    async def node_bear_case_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        bull_case_summary = _run_holder.get("bull_case_summary") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="BearCaseAgent",
            step_name="bear_case_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "bull_case_confidence": bull_case_summary.get("confidence_level", "low"),
            },
        )

        try:
            output = run_bear_case_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
                research_completeness_summary=research_completeness_summary,
                bull_case_summary=bull_case_summary if bull_case_summary else None,
            )
            output_dict = bear_case_output_to_dict(output)
            _run_holder["bear_case_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "negative_thesis_points_count": len(output.negative_thesis_points),
                    "key_unknowns_count": len(output.key_unknowns),
                    "confidence_level": output.confidence_level,
                    "warnings_count": len(output.warnings),
                },
            )
            return {"bear_case_summary": output_dict}

        except Exception as exc:
            error_msg = f"bear_case_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "negative_thesis_points": [],
                "potential_headwinds": [],
                "key_unknowns": [error_msg],
                "evidence_used": [],
                "missing_evidence": [],
                "confidence_level": "low",
                "warnings": [error_msg],
            }
            _run_holder["bear_case_summary"] = fallback
            return {"bear_case_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 14: risk_agent  (Phase 9 Analysis Council)                    #
    # ------------------------------------------------------------------ #
    async def node_risk_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        upgraded_citation_validation = _run_holder.get("upgraded_citation_validation") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="RiskAgent",
            step_name="risk_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "citation_status": upgraded_citation_validation.get("status", "unknown"),
            },
        )

        try:
            output = run_risk_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
                research_completeness_summary=research_completeness_summary,
                upgraded_citation_validation=upgraded_citation_validation or None,
            )
            output_dict = risk_agent_output_to_dict(output)
            _run_holder["risk_summary"] = output_dict

            total_risks = (
                len(output.business_risks) + len(output.financial_risks) +
                len(output.market_risks) + len(output.regulatory_geopolitical_risks) +
                len(output.data_quality_risks) + len(output.source_quality_risks)
            )
            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "total_risk_flags": total_risks,
                    "data_quality_risks_count": len(output.data_quality_risks),
                    "source_quality_risks_count": len(output.source_quality_risks),
                    "warnings_count": len(output.warnings),
                },
            )
            return {"risk_summary": output_dict}

        except Exception as exc:
            error_msg = f"risk_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "business_risks": [],
                "financial_risks": [],
                "market_risks": [],
                "regulatory_geopolitical_risks": [],
                "data_quality_risks": [error_msg],
                "source_quality_risks": [],
                "risk_summary": f"RiskAgent failed: {error_msg}",
                "warnings": [error_msg],
            }
            _run_holder["risk_summary"] = fallback
            return {"risk_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 15: valuation_guard_agent  (Phase 9 Analysis Council)         #
    # ------------------------------------------------------------------ #
    async def node_valuation_guard_agent(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ValuationGuardAgent",
            step_name="valuation_guard_agent",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "source_tier": (snapshot.get("provider_metadata") or {}).get("source_tier"),
            },
        )

        try:
            output = run_valuation_guard_agent(
                company_snapshot=snapshot,
                financial_data_summary=financial_data_summary,
                source_quality_summary=source_quality_summary,
            )
            output_dict = valuation_guard_output_to_dict(output)
            _run_holder["valuation_guard_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "valuation_readiness": output.valuation_readiness,
                    "blockers_count": len(output.valuation_blockers),
                    "available_inputs_count": len(output.available_valuation_inputs),
                    "missing_inputs_count": len(output.missing_valuation_inputs),
                    "warnings_count": len(output.warnings),
                },
            )
            return {"valuation_guard_summary": output_dict}

        except Exception as exc:
            error_msg = f"valuation_guard_agent failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "valuation_readiness": "not_ready",
                "available_valuation_inputs": [],
                "missing_valuation_inputs": [],
                "valuation_blockers": [error_msg],
                "allowed_next_steps": [],
                "disallowed_outputs": [],
                "warnings": [error_msg],
            }
            _run_holder["valuation_guard_summary"] = fallback
            return {"valuation_guard_summary": fallback}

    # ------------------------------------------------------------------ #
    # Node 16: investment_committee_chair  (Phase 9 Analysis Council)    #
    # ------------------------------------------------------------------ #
    async def node_investment_committee_chair(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        bull_case_summary = _run_holder.get("bull_case_summary") or {}
        bear_case_summary = _run_holder.get("bear_case_summary") or {}
        risk_summary = _run_holder.get("risk_summary") or {}
        valuation_guard_summary = _run_holder.get("valuation_guard_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        upgraded_citation_validation = _run_holder.get("upgraded_citation_validation") or {}

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="InvestmentCommitteeChair",
            step_name="investment_committee_chair",
            input_data={
                "ticker": state.get("ticker"),
                "bull_confidence": bull_case_summary.get("confidence_level", "low"),
                "bear_confidence": bear_case_summary.get("confidence_level", "low"),
                "valuation_readiness": valuation_guard_summary.get(
                    "valuation_readiness", "not_ready"
                ),
                "schema_valid": state.get("schema_valid"),
            },
        )

        try:
            output = run_investment_committee_chair(
                company_snapshot=snapshot,
                bull_case_summary=bull_case_summary,
                bear_case_summary=bear_case_summary,
                risk_summary=risk_summary,
                valuation_guard_summary=valuation_guard_summary,
                research_completeness_summary=research_completeness_summary,
                source_quality_summary=source_quality_summary,
                upgraded_citation_validation=upgraded_citation_validation or None,
                schema_valid=state.get("schema_valid"),
            )
            output_dict = committee_chair_output_to_dict(output)
            _run_holder["committee_chair_summary"] = output_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "provisional_internal_status": output.provisional_internal_status,
                    "bull_bear_balance": output.bull_bear_balance,
                    "human_review_required": output.human_review_required,
                    "open_questions_count": len(output.primary_open_questions),
                    "warnings_count": len(output.warnings),
                },
            )

            # Aggregate analysis council warnings
            analysis_council_warnings: list[str] = []
            for summary_key in ["bull_case_summary", "bear_case_summary", "risk_summary",
                                 "valuation_guard_summary"]:
                s = _run_holder.get(summary_key) or {}
                analysis_council_warnings.extend(s.get("warnings", []))
            analysis_council_warnings.extend(output.warnings)

            return {
                "committee_chair_summary": output_dict,
                "analysis_council_warnings": analysis_council_warnings,
                "quality_gate_status": output.quality_gate_status,
                "provisional_internal_status": output.provisional_internal_status,
                "human_review_required": output.human_review_required,
            }

        except Exception as exc:
            error_msg = f"investment_committee_chair failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "committee_summary": f"InvestmentCommitteeChair failed: {error_msg}",
                "bull_bear_balance": "insufficient_data",
                "primary_open_questions": [],
                "research_next_steps": [],
                "quality_gate_status": {},
                "provisional_internal_status": "research_incomplete",
                "human_review_required": True,
                "warnings": [error_msg],
            }
            _run_holder["committee_chair_summary"] = fallback
            return {
                "committee_chair_summary": fallback,
                "analysis_council_warnings": [error_msg],
                "quality_gate_status": {},
                "provisional_internal_status": "research_incomplete",
                "human_review_required": True,
            }

    # ------------------------------------------------------------------ #
    # Node 16b: catalyst_discovery_agent  (Phase 24)                     #
    # ------------------------------------------------------------------ #
    async def node_catalyst_discovery(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        company = _run_holder.get("company")
        pname = state.get("provider_name") or "mock"
        ticker = state.get("ticker") or "UNKNOWN"
        exchange = state.get("exchange")
        company_name = state.get("company_name") or ticker

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="CatalystDiscoveryAgent",
            step_name="catalyst_discovery",
            input_data={
                "ticker": ticker,
                "provider_name": pname,
                "is_mock": state.get("is_mock"),
            },
        )

        # Phase 24: catalyst discovery runs for real-data providers only. Mock
        # provider keeps deterministic behaviour with no catalyst data attached.
        if pname not in ("free_real", "eodhd_free_real"):
            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "skipped": True,
                    "reason": f"provider={pname} (no catalyst discovery)",
                },
            )
            return {
                "catalyst_discovery": None,
                "catalyst_agent": None,
                "catalyst_summary": None,
                "catalyst_warnings": None,
                "catalyst_citations": None,
                "catalyst_coverage_status": None,
            }

        try:
            snap_profile = snapshot.get("profile") or {}
            identity = snapshot.get("company_identity") or {}
            website = snap_profile.get("website")
            sector = snap_profile.get("sector") or getattr(company, "sector", None)
            industry = snap_profile.get("industry") or getattr(company, "industry", None)
            country = identity.get("country_domicile") or snap_profile.get(
                "country_domicile"
            )
            cik = getattr(company, "sec_cik", None) if company else None
            # News/press/industry lookback is configurable (NEWS_LOOKBACK_DAYS);
            # SEC filings keep the 90-day window. Bounded to a sane range.
            try:
                news_lb = int(os.environ.get("NEWS_LOOKBACK_DAYS", "90"))
            except (TypeError, ValueError):
                news_lb = 90
            news_lb = max(1, min(news_lb, 365))
            result = await discover_catalysts(
                ticker=ticker,
                exchange=exchange,
                company_name=company_name,
                cik=cik,
                website=website,
                sector=sector,
                industry=industry,
                country=country,
                lookback_days=90,
                news_lookback_days=news_lb,
                max_events=20,
                include_source_discovery=True,
            )
            agent_output = run_catalyst_agent(result)

            catalyst_discovery = result.to_report_dict()
            catalyst_agent = catalyst_agent_output_to_dict(agent_output)
            catalyst_citations = [
                e["source_url"] for e in catalyst_discovery["events"] if e.get("source_url")
            ]

            _run_holder["catalyst_discovery"] = catalyst_discovery
            _run_holder["catalyst_agent"] = catalyst_agent

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "coverage_status": result.coverage_quality,
                    "total_events": result.summary.total_events,
                    "filing_events": len(result.filing_events),
                    "news_events": len(result.news_events),
                    "press_release_events": len(result.press_release_events),
                    "warnings_count": len(result.warnings),
                },
            )

            return {
                "catalyst_discovery": catalyst_discovery,
                "catalyst_agent": catalyst_agent,
                "catalyst_summary": catalyst_discovery["summary"],
                "catalyst_warnings": result.warnings or None,
                "catalyst_citations": catalyst_citations or None,
                "catalyst_coverage_status": result.coverage_quality,
            }

        except Exception as exc:
            error_msg = f"catalyst_discovery failed (non-fatal): {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            _run_holder["catalyst_discovery"] = None
            _run_holder["catalyst_agent"] = None
            return {
                "catalyst_discovery": None,
                "catalyst_agent": None,
                "catalyst_summary": None,
                "catalyst_warnings": [error_msg],
                "catalyst_citations": None,
                "catalyst_coverage_status": "provider_unavailable",
            }

    # ------------------------------------------------------------------ #
    # Node 17: score_research_attractiveness  (Phase 15)                 #
    # ------------------------------------------------------------------ #
    async def node_score_research_attractiveness(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ScoringEngine",
            step_name="score_research_attractiveness",
            input_data={
                "ticker": state.get("ticker"),
                "is_mock": state.get("is_mock"),
                "provider_name": state.get("provider_name"),
            },
        )

        try:
            scorecard_dict = run_score_research_attractiveness(
                company_snapshot=snapshot,
                financial_data_summary=_run_holder.get("financial_data_summary"),
                source_quality_summary=_run_holder.get("source_quality_summary"),
                research_completeness_summary=_run_holder.get("research_completeness_summary"),
                citation_validation_summary=_run_holder.get("upgraded_citation_validation"),
                bull_case_summary=_run_holder.get("bull_case_summary"),
                bear_case_summary=_run_holder.get("bear_case_summary"),
                risk_summary=_run_holder.get("risk_summary"),
                valuation_guard_summary=_run_holder.get("valuation_guard_summary"),
                committee_chair_summary=_run_holder.get("committee_chair_summary"),
            )
            _run_holder["research_attractiveness_scorecard"] = scorecard_dict

            await agent_run_service.complete_agent_step(
                db,
                step,
                output_data={
                    "overall_score": scorecard_dict.get("overall_score", 0),
                    "internal_status": scorecard_dict.get("internal_status", "not_enough_data"),
                    "warnings_count": len(scorecard_dict.get("warnings", [])),
                },
            )
            return {"research_attractiveness_scorecard": scorecard_dict}

        except Exception as exc:
            error_msg = f"score_research_attractiveness failed: {exc}"
            await agent_run_service.fail_agent_step(db, step, error_msg)
            fallback = {
                "overall_score": 0,
                "internal_status": "not_enough_data",
                "scores": {},
                "warnings": [error_msg],
                "missing_data": [],
                "reasoning": f"Scoring node failed: {error_msg}",
                "source_quality_summary": {},
                "next_research_steps": [],
                "disclaimer": (
                    "INTERNAL SCORE ONLY. Not investment advice. "
                    "Not a public recommendation. Human review required."
                ),
            }
            _run_holder["research_attractiveness_scorecard"] = fallback
            return {"research_attractiveness_scorecard": fallback}

    # ------------------------------------------------------------------ #
    # Node 18: save_draft_report  (Phase 9 — includes Analysis Council)  #
    # ------------------------------------------------------------------ #
    async def node_save_draft_report(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        snapshot = _run_holder.get("snapshot", {})
        validation = _run_holder.get("validation_result", {})

        ticker = state.get("ticker") or "UNKNOWN"
        company_name = state.get("company_name") or ticker
        agent_run_id = state.get("agent_run_id")
        is_mock = state.get("is_mock", True)
        schema_valid = state.get("schema_valid", False)
        provider_name_used = state.get("provider_name") or "mock"
        missing_fields = snapshot.get("missing_fields", [])
        llm_used = state.get("llm_used", False)
        llm_sections = state.get("llm_sections") or {}
        llm_section_warnings = state.get("llm_section_warnings") or []
        llm_provider_used = state.get("llm_provider") or "none"

        # Phase 8: Research Team outputs
        financial_data_summary = _run_holder.get("financial_data_summary") or {}
        source_quality_summary = _run_holder.get("source_quality_summary") or {}
        research_completeness_summary = _run_holder.get("research_completeness_summary") or {}
        upgraded_citation_validation = _run_holder.get("upgraded_citation_validation") or {}

        # Phase 9: Analysis Council outputs
        bull_case_summary = _run_holder.get("bull_case_summary") or {}
        bear_case_summary = _run_holder.get("bear_case_summary") or {}
        risk_summary_dict = _run_holder.get("risk_summary") or {}
        valuation_guard_summary = _run_holder.get("valuation_guard_summary") or {}
        committee_chair_summary = _run_holder.get("committee_chair_summary") or {}
        analysis_council_warnings = state.get("analysis_council_warnings") or []
        # Phase 24: News + Catalyst Discovery
        catalyst_discovery = _run_holder.get("catalyst_discovery")
        catalyst_agent = _run_holder.get("catalyst_agent") or {}
        provisional_status = state.get("provisional_internal_status") or "research_incomplete"
        human_review_req = state.get("human_review_required", True)

        # Aggregate research team warnings
        research_team_warnings: list[str] = []
        research_team_warnings.extend(financial_data_summary.get("warnings", []))
        research_team_warnings.extend(source_quality_summary.get("warnings", []))
        research_team_warnings.extend(
            upgraded_citation_validation.get("weak_citation_warnings", [])
        )
        research_team_warnings.extend(
            upgraded_citation_validation.get("source_tier_warnings", [])
        )

        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="ReportWriter",
            step_name="save_draft_report",
            input_data={
                "ticker": ticker,
                "company_name": company_name,
                "schema_valid": schema_valid,
                "provider_name": provider_name_used,
                "is_mock": is_mock,
                "llm_used": llm_used,
                "llm_provider": llm_provider_used,
                "research_team_warnings_count": len(research_team_warnings),
                "analysis_council_warnings_count": len(analysis_council_warnings),
                "provisional_internal_status": provisional_status,
                "human_review_required": human_review_req,
            },
        )

        slug = _make_report_slug(ticker, agent_run_id or "")

        # Build human-readable markdown content
        mode_tag = "[MOCK DATA]" if is_mock else "[LIVE DATA]"
        schema_tag = "SCHEMA VALID" if schema_valid else "SCHEMA INVALID"
        llm_tag = f"[LLM: {llm_provider_used}]" if llm_used else "[LLM: not used]"
        errors = validation.get("errors", [])
        warnings = validation.get("warnings", [])
        source_quality = source_quality_summary.get("overall_source_quality", "unknown")
        citation_v2_status = upgraded_citation_validation.get("status", "unknown")

        content_md = (
            f"# {company_name} — Phase 9 Analysis Council Draft {mode_tag}\n\n"
            f"**Provider:** {provider_name_used}  \n"
            f"**Ticker:** {ticker}  \n"
            f"**Schema Validation:** {schema_tag}  \n"
            f"**LLM:** {llm_tag}  \n"
            f"**Source Quality:** {source_quality}  \n"
            f"**Citation Validation:** {citation_v2_status}  \n"
            f"**Provisional Internal Status:** `{provisional_status}`  \n"
            f"**Human Review Required:** {human_review_req}  \n\n"
            "> **INTERNAL ADMIN DRAFT ONLY.** "
            "This is not investment advice and must not be published without human admin review. "
            "No investment recommendation has been made. "
            "All analysis council outputs are internal workflow artefacts only.\n\n"
        )

        # ── Company Snapshot ──────────────────────────────────────────
        identity = snapshot.get("company_identity", {})
        snap_profile = snapshot.get("profile", {})
        content_md += "## Company Snapshot\n\n"
        content_md += f"- **Legal Name:** {identity.get('legal_name', 'N/A')}  \n"
        content_md += f"- **Exchange:** {identity.get('exchange', 'N/A')}  \n"
        content_md += f"- **Country:** {identity.get('country_domicile', 'N/A')}  \n"
        content_md += f"- **Sector:** {snap_profile.get('sector') or 'N/A (not sourced)'}  \n"
        content_md += f"- **Industry:** {snap_profile.get('industry') or 'N/A (not sourced)'}  \n"
        content_md += f"- **Website:** {snap_profile.get('website') or 'N/A (not sourced)'}  \n"
        content_md += f"- **LEI:** {identity.get('lei') or 'N/A (not sourced)'}  \n"
        content_md += f"- **ISIN:** {identity.get('isin') or 'N/A (not sourced)'}  \n\n"

        # ── Identity/Profile Enrichment provenance (Phase 19.4) ───────
        ip_enrich = snapshot.get("identity_profile_enrichment") or {}
        if ip_enrich:
            if ip_enrich.get("sector_is_inferred") and ip_enrich.get("sector"):
                content_md += (
                    f"> Sector `{ip_enrich['sector']}` is a DERIVED ESTIMATE "
                    "(T6_model_estimate) inferred from the SEC SIC classification — "
                    "not a sourced fact.\n\n"
                )
            ip_warnings = ip_enrich.get("warnings") or []
            if ip_warnings:
                content_md += "**Identity/Profile enrichment notes:**\n\n"
                content_md += "\n".join(f"- {w}" for w in ip_warnings[:6])
                content_md += "\n\n"

        if missing_fields:
            content_md += "### Missing Fields (Provider Data)\n\n"
            content_md += "\n".join(f"- `{f}`" for f in missing_fields)
            content_md += "\n\n"

        # ── Provider Data Summary ────────────────────────────────────
        provider_meta = snapshot.get("provider_metadata", {})
        content_md += "## Provider Data Summary\n\n"
        content_md += (
            f"- **Provider:** {provider_meta.get('provider_name', 'N/A')}  \n"
            f"- **Source Tier:** {provider_meta.get('source_tier', 'N/A')}  \n"
            f"- **Retrieved:** {provider_meta.get('retrieved_at', 'N/A')}  \n"
            f"- **Mock Data:** {provider_meta.get('is_mock', True)}  \n\n"
        )

        # ── Financial Data Agent Summary ─────────────────────────────
        content_md += "## Financial Data Agent Summary\n\n"
        content_md += financial_data_summary.get("financial_context_summary", "N/A") + "\n\n"
        fda_warnings = financial_data_summary.get("warnings", [])
        if fda_warnings:
            content_md += "**Warnings:**\n\n"
            content_md += "\n".join(f"- {w}" for w in fda_warnings)
            content_md += "\n\n"
        fda_missing = (
            FinancialDataSummary.from_payload(financial_data_summary)
            or FinancialDataSummary()
        ).missing_fields
        if fda_missing:
            content_md += f"**Missing financial data categories:** {len(fda_missing)} total.  \n\n"

        # ── Source Quality Agent Summary ─────────────────────────────
        content_md += "## Source Quality Agent Summary\n\n"
        content_md += f"**Overall source quality:** {source_quality}  \n\n"
        sq_weak = source_quality_summary.get("weak_sources", [])
        if sq_weak:
            content_md += "**Weak sources:**\n\n"
            content_md += "\n".join(f"- {s}" for s in sq_weak)
            content_md += "\n\n"
        sq_agg = source_quality_summary.get("aggregator_only_claims", [])
        if sq_agg:
            content_md += f"**Aggregator-only claims:** {len(sq_agg)}  \n\n"
        sq_upgrades = source_quality_summary.get("recommended_source_upgrades", [])
        if sq_upgrades:
            content_md += "**Recommended source upgrades:**\n\n"
            content_md += "\n".join(f"- {u}" for u in sq_upgrades[:5])
            content_md += "\n\n"
        cat_sq_recs = catalyst_agent.get("source_quality_recommendations", [])
        if cat_sq_recs:
            content_md += "**Catalyst source upgrades (Phase 24):**\n\n"
            content_md += "\n".join(f"- {u}" for u in cat_sq_recs[:6])
            content_md += "\n\n"

        # ── LLM Research Draft ───────────────────────────────────────
        if llm_used and llm_sections:
            if llm_section_warnings:
                content_md += "## LLM Safety Warnings\n\n"
                for w in llm_section_warnings:
                    content_md += f"> **WARNING:** {w}\n\n"

            content_md += (
                "## LLM Research Draft (Admin Review Required)\n\n"
                "> Generated by LLM using provider identity data only. "
                "NOT investment advice. No rating or price target assigned.\n\n"
            )
            content_md += (
                "### Thesis Summary\n\n"
                + llm_sections.get("thesis_summary_draft", "") + "\n\n"
            )
            content_md += (
                "### Business Overview\n\n"
                + llm_sections.get("business_overview_draft", "") + "\n\n"
            )
            llm_missing = llm_sections.get("missing_information", [])
            if llm_missing:
                content_md += "### Missing Information (LLM Assessment)\n\n"
                content_md += "\n".join(f"- {m}" for m in llm_missing)
                content_md += "\n\n"
            content_md += (
                "### Limitations (LLM Self-Critique)\n\n"
                + llm_sections.get("self_critique_limitations", "") + "\n\n"
            )

        # ── Bull Case Draft ───────────────────────────────────────────────
        content_md += "## Bull Case Draft (Analysis Council — Internal)\n\n"
        bc_points = bull_case_summary.get("positive_thesis_points", [])
        bc_confidence = bull_case_summary.get("confidence_level", "low")
        content_md += f"**Confidence Level:** {bc_confidence}  \n\n"
        if bc_points:
            content_md += "**Positive Thesis Points:**\n\n"
            content_md += "\n".join(f"- {p}" for p in bc_points)
            content_md += "\n\n"
        bc_tailwinds = bull_case_summary.get("potential_tailwinds", [])
        if bc_tailwinds:
            content_md += "**Potential Tailwinds:**\n\n"
            content_md += "\n".join(f"- {t}" for t in bc_tailwinds[:5])
            content_md += "\n\n"
        bc_missing = bull_case_summary.get("missing_evidence", [])
        if bc_missing:
            content_md += f"**Missing Evidence:** {len(bc_missing)} items.  \n\n"
        bc_warnings = bull_case_summary.get("warnings", [])
        if bc_warnings:
            content_md += "**Warnings:**\n\n"
            content_md += "\n".join(f"> {w}" for w in bc_warnings[:3])
            content_md += "\n\n"
        cat_bull = catalyst_agent.get("bull_context", [])
        if cat_bull:
            content_md += "**Recent Catalyst Context (model-derived — human review required):**\n\n"
            content_md += "\n".join(f"- {c}" for c in cat_bull[:4])
            content_md += "\n\n"

        # ── Bear Case Draft ───────────────────────────────────────────────
        content_md += "## Bear Case Draft (Analysis Council — Internal)\n\n"
        br_points = bear_case_summary.get("negative_thesis_points", [])
        br_confidence = bear_case_summary.get("confidence_level", "low")
        content_md += f"**Confidence Level:** {br_confidence}  \n\n"
        if br_points:
            content_md += "**Negative Thesis Points:**\n\n"
            content_md += "\n".join(f"- {p}" for p in br_points)
            content_md += "\n\n"
        br_unknowns = bear_case_summary.get("key_unknowns", [])
        if br_unknowns:
            content_md += "**Key Unknowns:**\n\n"
            content_md += "\n".join(f"- {u}" for u in br_unknowns[:5])
            content_md += "\n\n"
        cat_bear = catalyst_agent.get("bear_context", [])
        if cat_bear:
            content_md += "**Recent Catalyst Context (model-derived — human review required):**\n\n"
            content_md += "\n".join(f"- {c}" for c in cat_bear[:4])
            content_md += "\n\n"

        # ── Risk Review ───────────────────────────────────────────────────
        content_md += "## Risk Review (Analysis Council — Internal)\n\n"
        content_md += risk_summary_dict.get("risk_summary", "N/A") + "\n\n"
        dq_risks = risk_summary_dict.get("data_quality_risks", [])
        if dq_risks:
            content_md += "**Data Quality Risks:**\n\n"
            content_md += "\n".join(f"- {r}" for r in dq_risks)
            content_md += "\n\n"
        sq_risks = risk_summary_dict.get("source_quality_risks", [])
        if sq_risks:
            content_md += "**Source Quality Risks:**\n\n"
            content_md += "\n".join(f"- {r}" for r in sq_risks[:5])
            content_md += "\n\n"
        cat_risks = catalyst_agent.get("risk_flags", [])
        if cat_risks:
            content_md += "**Catalyst Data-Quality Risks (Phase 24):**\n\n"
            content_md += "\n".join(f"- {r}" for r in cat_risks[:6])
            content_md += "\n\n"

        # ── Valuation Guard ───────────────────────────────────────────────
        content_md += "## Valuation Guard (Analysis Council — Internal)\n\n"
        vg_readiness = valuation_guard_summary.get("valuation_readiness", "not_ready")
        content_md += f"**Valuation Readiness:** `{vg_readiness}`  \n\n"
        vg_blockers = valuation_guard_summary.get("valuation_blockers", [])
        if vg_blockers:
            content_md += "**Valuation Blockers:**\n\n"
            content_md += "\n".join(f"- {b}" for b in vg_blockers)
            content_md += "\n\n"
        vg_disallowed = valuation_guard_summary.get("disallowed_outputs", [])
        if vg_disallowed:
            content_md += "**Disallowed Outputs at This Phase:**\n\n"
            content_md += "\n".join(f"- {d}" for d in vg_disallowed)
            content_md += "\n\n"

        # ── Investment Committee Chair Summary ────────────────────────────
        content_md += "## Investment Committee Chair Summary (Admin Only)\n\n"
        content_md += committee_chair_summary.get("committee_summary", "N/A") + "\n\n"
        cc_questions = committee_chair_summary.get("primary_open_questions", [])
        if cc_questions:
            content_md += "**Primary Open Questions:**\n\n"
            content_md += "\n".join(f"- {q}" for q in cc_questions[:6])
            content_md += "\n\n"
        cc_next = committee_chair_summary.get("research_next_steps", [])
        if cc_next:
            content_md += "**Research Next Steps:**\n\n"
            content_md += "\n".join(f"- {s}" for s in cc_next[:6])
            content_md += "\n\n"
        cat_questions = catalyst_agent.get("committee_open_questions", [])
        if cat_questions:
            content_md += "**Catalyst Open Questions (Phase 24):**\n\n"
            content_md += "\n".join(f"- {q}" for q in cat_questions[:5])
            content_md += "\n\n"

        # ── Research Completeness Review ─────────────────────────────
        content_md += "## Research Completeness Review\n\n"
        rc = research_completeness_summary
        complete = rc.get("complete_sections", [])
        incomplete = rc.get("incomplete_sections", [])
        content_md += (
            f"**Complete sections:** {', '.join(complete) if complete else 'none'}  \n"
            f"**Incomplete sections:** {len(incomplete)}  \n"
            f"**Blocking gaps:** {len(rc.get('blocking_gaps', []))}  \n\n"
        )
        next_tasks = rc.get("next_research_tasks", [])
        if next_tasks:
            content_md += "**Next research tasks:**\n\n"
            content_md += "\n".join(f"- {t}" for t in next_tasks[:8])
            content_md += "\n\n"

        # ── Market Metrics (Phase 19.4 — derived internal estimates) ─────
        market_metrics = _run_holder.get("market_metrics_summary") or {}
        if market_metrics:
            content_md += "## Market Metrics (Derived — Internal)\n\n"
            content_md += (
                "> **INTERNAL ONLY.** Market cap, enterprise value and P/E are "
                "DERIVED ESTIMATES (T6_model_estimate) from free price data (T5) "
                "and SEC fundamentals (T2). Not official figures, not a valuation "
                "conclusion, not investment advice. Margins are annual (not TTM).\n\n"
            )
            currency = market_metrics.get("currency", "USD")
            for key, label, unit in [
                ("latest_close", "Latest Close", currency),
                ("week52_high", "52-Week High", currency),
                ("week52_low", "52-Week Low", currency),
                ("shares_outstanding_mln", "Shares Outstanding", "M"),
                ("market_cap_mln", "Market Cap (derived)", f"{currency}_m"),
                ("enterprise_value_mln", "Enterprise Value (derived)", f"{currency}_m"),
                ("pe_ratio", "P/E (derived)", "x"),
            ]:
                val = market_metrics.get(key)
                if val is not None:
                    content_md += f"**{label}:** {val} {unit}  \n"
            if market_metrics.get("pe_basis"):
                content_md += f"**P/E basis:** {market_metrics['pe_basis']}  \n"
            content_md += (
                "**Not derived (never fabricated):** EBITDA, EV/EBITDA, beta.  \n"
            )
            mm_warnings = market_metrics.get("warnings") or []
            if mm_warnings:
                content_md += "\n**Market metric notes:**\n\n"
                content_md += "\n".join(f"- {w}" for w in mm_warnings[:8])
            content_md += "\n\n"

        # ── Trend Signal Summary (Phase 19.2 — T6_model_estimate) ───────
        trend_sig = _run_holder.get("trend_signal_summary") or {}
        if trend_sig:
            content_md += "## Trend Signal Summary (Internal — T6 Model Estimate)\n\n"
            content_md += (
                "> **INTERNAL ONLY.** Momentum labels are T6_model_estimate derived from price "
                "history. No investment recommendation. Not investment advice.\n\n"
            )
            momentum = trend_sig.get("momentum_label", "N/A")
            content_md += f"**Momentum Label:** `{momentum}`  \n"
            for metric, label in [
                ("return_1m", "1M Return (%)"),
                ("return_3m", "3M Return (%)"),
                ("return_6m", "6M Return (%)"),
                ("pct_above_ma50", "% above MA50"),
                ("pct_above_ma200", "% above MA200"),
                ("relative_strength", "Relative Strength"),
            ]:
                val = trend_sig.get(metric)
                if val is not None:
                    content_md += f"**{label}:** {val}  \n"
            src_tier = trend_sig.get("source_tier", "T6_model_estimate")
            content_md += f"**Source Tier:** {src_tier}  \n"
            ts_warnings = trend_sig.get("data_warnings") or []
            if ts_warnings:
                content_md += "**Trend Data Warnings:**\n\n"
                content_md += "\n".join(f"- {w}" for w in ts_warnings)
            content_md += "\n\n"

        # ── News & Catalyst Discovery (Phase 24) ─────────────────────
        if catalyst_agent.get("markdown"):
            content_md += catalyst_agent["markdown"]
            content_md += "\n"

        # ── Provider Warnings (Phase 19.2) ───────────────────────────
        prov_warnings = state.get("provider_warnings") or []
        if prov_warnings:
            content_md += "## Provider Warnings\n\n"
            content_md += "\n".join(f"- {w}" for w in prov_warnings)
            content_md += "\n\n"

        # ── Contributing Providers (Phase 19.2) ─────────────────────
        contrib = state.get("contributing_providers") or []
        if contrib:
            content_md += "## Contributing Providers\n\n"
            content_md += ", ".join(f"`{p}`" for p in contrib) + "\n\n"

        # ── Citation Validation Review ───────────────────────────────
        content_md += "## Citation Validation Review (v2)\n\n"
        content_md += f"**Status:** {citation_v2_status}  \n"
        unsup_nums = upgraded_citation_validation.get("unsupported_number_warnings", [])
        if unsup_nums:
            content_md += "\n**Unsupported number warnings:**\n\n"
            content_md += "\n".join(f"- {w}" for w in unsup_nums)
            content_md += "\n\n"
        tier_warns = upgraded_citation_validation.get("source_tier_warnings", [])
        if tier_warns:
            content_md += "\n**Source tier warnings:**\n\n"
            content_md += "\n".join(f"- {w}" for w in tier_warns[:5])
            content_md += "\n\n"

        # ── Schema Errors / Warnings ─────────────────────────────────
        if errors:
            content_md += "## Schema Errors\n\n"
            content_md += "\n".join(f"- `{e}`" for e in errors[:10])
            if len(errors) > 10:
                content_md += f"\n- ... ({len(errors) - 10} more errors)\n"
            content_md += "\n\n"
        if warnings:
            content_md += "## Data Quality Warnings\n\n"
            content_md += "\n".join(f"- {w}" for w in warnings)
            content_md += "\n\n"

        # ── Missing Information ──────────────────────────────────────
        all_missing = list(dict.fromkeys(
            missing_fields
            + (
                FinancialDataSummary.from_payload(financial_data_summary)
                or FinancialDataSummary()
            ).missing_fields
        ))
        if all_missing:
            content_md += f"## Missing Information ({len(all_missing)} items)\n\n"
            content_md += "\n".join(f"- `{m}`" for m in all_missing[:20])
            if len(all_missing) > 20:
                content_md += f"\n- ... ({len(all_missing) - 20} more)\n"
            content_md += "\n\n"

        # ── Machine-readable catalyst data (Phase 24) ────────────────
        # Embedded as a JSON block so the Final Report Generator can attach the
        # catalyst section. External headlines are already neutralised via
        # CatalystDiscoveryResult.to_report_dict(). Emitted only when catalyst
        # discovery ran (real-data providers); mock reports are unchanged.
        if catalyst_discovery:
            import json as _json

            content_md += "## Machine-Readable Catalyst Data (Internal)\n\n"
            content_md += (
                "> Machine-readable catalyst payload for the Final Report Generator. "
                "Model-derived labels (T6). Not investment advice.\n\n"
            )
            content_md += "```json\n"
            content_md += _json.dumps(
                {"catalyst_discovery": catalyst_discovery}, indent=2, default=str
            )
            content_md += "\n```\n\n"

        # ── Machine-readable analysis state (Phase 32A) ──────────────
        # A SECOND JSON block carrying the bounded, secret-stripped structured
        # state the final-report adapter needs to regenerate a current-schema
        # report losslessly (identity, provenance, deterministic council
        # sections, financial snapshot). FLAT keys + excludes catalyst_discovery
        # (RC-3). ``build_analysis_state_envelope`` self-gates on real data, so
        # mock/catalyst-only drafts get {} here and stay byte-identical to the
        # pre-Phase-32A markdown (dark-safe).
        state_envelope = build_analysis_state_envelope(state)
        if state_envelope:
            content_md += "## Machine-Readable Analysis State (Internal)\n\n"
            content_md += (
                "> Machine-readable analysis-state payload for the Final Report "
                "Generator (identity, provenance, council sections, financial "
                "snapshot). Secret-stripped and size-bounded. Internal artefact; "
                "not investment advice.\n\n"
            )
            content_md += "```json\n"
            content_md += json.dumps(state_envelope, indent=2, default=str)
            content_md += "\n```\n\n"

        content_md += (
            "---\n\n"
            "> **INTERNAL ADMIN DRAFT — PHASE 9 ANALYSIS COUNCIL.** "
            "This is not investment advice. "
            "No public investment recommendation has been made. "
            "No price target, fair value, or valuation conclusion has been produced. "
            "Human admin review is required before any further use. "
            "Do not publish or share externally.\n"
        )

        summary = (
            f"Phase 9 Analysis Council draft for {company_name} ({ticker}). "
            f"Provider: {provider_name_used}. "
            f"{'MOCK DATA' if is_mock else 'LIVE DATA'}. "
            f"LLM: {llm_provider_used if llm_used else 'not used'}. "
            f"Schema: {schema_tag}. "
            f"Source quality: {source_quality}. "
            f"Internal status: {provisional_status}. "
            f"Human review: {human_review_req}. "
            "No investment recommendation."
        )

        # Phase 32A hotfix — link the draft to the company it is about so the
        # ``from-company`` final-report route can select this report by company
        # (not the globally-newest completed report). Prefer the resolved company
        # ORM object; fall back to the state's company_id string. Defensive: an
        # unparsable/absent id yields None (never crash the report save).
        _company = _run_holder.get("company")
        _company_id: uuid.UUID | None
        if _company is not None:
            _company_id = _company.id
        elif state.get("company_id"):
            try:
                _company_id = uuid.UUID(str(state["company_id"]))
            except (ValueError, TypeError):
                _company_id = None
        else:
            _company_id = None

        report = await report_service.create_draft_report(
            db,
            ReportCreate(
                title=f"{company_name} — Analysis Council Draft {mode_tag}",
                slug=slug,
                report_type="company_deep_dive",
                summary=summary,
                content_markdown=content_md,
                created_by_agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                company_id=_company_id,
            ),
        )

        # Phase 32A Slice 3 — link this run's deterministic citations to the draft
        # report. They were created earlier (Node 8), before the report row existed,
        # with ``agent_run_id`` set and ``report_id`` NULL. Now that ``report.id`` is
        # live, a scoped idempotent UPDATE links them: keyed by this run's
        # ``agent_run_id`` (which pins the run ⇒ company-safe) and guarded by
        # ``report_id IS NULL`` (⇒ idempotent, a re-run is a no-op, never a
        # duplicate). OFF by default: with the flag off the historic no-op stands
        # and the draft's citations keep ``report_id`` NULL (byte-identical). Logs a
        # count only — never the citations, URLs, or evidence.
        if settings.report_citation_persistence_enabled and agent_run_id:
            _linked = await citation_service.link_citations_to_report(
                db, uuid.UUID(agent_run_id), report.id
            )
            await db.commit()
            log_event(
                _logger,
                "citation_report_backfill",
                report_id=str(report.id),
                citations_linked=_linked,
            )

        await agent_run_service.complete_agent_step(
            db,
            step,
            output_data={
                "report_id": str(report.id),
                "slug": report.slug,
                "schema_valid": schema_valid,
                "missing_fields_count": len(missing_fields),
                "source_quality": source_quality,
                "citation_v2_status": citation_v2_status,
                "research_team_warnings_count": len(research_team_warnings),
                # Phase 32A — counts/labels only, NEVER the envelope content/URLs.
                "state_envelope_keys": len(state_envelope),
                "state_envelope_bytes": (
                    _envelope_size(state_envelope) if state_envelope else 0
                ),
            },
        )

        return {
            "draft_report_id": str(report.id),
            "research_team_warnings": research_team_warnings,
            "research_team_complete": True,
        }

    # (save_draft_report ends here)

    # ------------------------------------------------------------------ #
    # Node 18: log_agent_steps  (was Node 13 in Phase 8)                  #
    # ------------------------------------------------------------------ #
    async def node_log_agent_steps(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        step = await agent_run_service.create_agent_step(
            db,
            run=run,
            agent_name="WorkflowController",
            step_name="log_agent_steps",
            input_data={
                "draft_report_id": state.get("draft_report_id"),
                "schema_valid": state.get("schema_valid"),
                "citation_count": len(state.get("citation_ids") or []),
                "source_count": len(state.get("source_ids") or []),
            },
        )
        await agent_run_service.complete_agent_step(db, step, output_data={"status": "completed"})
        await agent_run_service.complete_agent_run(db, run)
        return {"status": "completed"}

    # ------------------------------------------------------------------ #
    # Error handler
    # ------------------------------------------------------------------ #
    async def node_handle_error(state: CompanyAnalysisState) -> dict:
        run = _run_holder.get("run")
        if run:
            error = state.get("error") or "Unknown error"
            await agent_run_service.fail_agent_run(db, run, error)
        return {"status": "failed"}

    # ------------------------------------------------------------------ #
    # Conditional routing
    # ------------------------------------------------------------------ #
    def route_after_load_company(state: CompanyAnalysisState) -> str:
        if state.get("status") == "failed":
            return "handle_error"
        return "fetch_provider_data"

    def route_after_fetch(state: CompanyAnalysisState) -> str:
        if state.get("status") == "failed":
            return "handle_error"
        return "create_source_records"

    # ------------------------------------------------------------------ #
    # Build graph
    # ------------------------------------------------------------------ #
    graph = StateGraph(CompanyAnalysisState)

    graph.add_node("load_company", node_load_company)
    graph.add_node("fetch_provider_data", node_fetch_provider_data)
    graph.add_node("create_source_records", node_create_source_records)
    graph.add_node("build_company_snapshot", node_build_company_snapshot)
    # Phase 8: Research Team nodes (deterministic)
    graph.add_node("financial_data_agent", node_financial_data_agent)
    graph.add_node("source_quality_agent", node_source_quality_agent)
    # Phase 7: optional LLM node
    graph.add_node("generate_research_sections", node_generate_research_sections)
    graph.add_node("create_citations", node_create_citations)
    graph.add_node("validate_report_schema", node_validate_report_schema)
    # Phase 8: post-validation Research Team nodes
    graph.add_node("research_completeness_agent", node_research_completeness_agent)
    graph.add_node("citation_validator_v2", node_citation_validator_v2)
    # Phase 9: Analysis Council nodes (deterministic)
    graph.add_node("bull_case_agent", node_bull_case_agent)
    graph.add_node("bear_case_agent", node_bear_case_agent)
    graph.add_node("risk_agent", node_risk_agent)
    graph.add_node("valuation_guard_agent", node_valuation_guard_agent)
    graph.add_node("investment_committee_chair", node_investment_committee_chair)
    # Phase 24: catalyst discovery node (real-data providers only)
    graph.add_node("catalyst_discovery_agent", node_catalyst_discovery)
    # Phase 15: scoring node
    graph.add_node("score_research_attractiveness", node_score_research_attractiveness)
    graph.add_node("save_draft_report", node_save_draft_report)
    graph.add_node("log_agent_steps", node_log_agent_steps)
    graph.add_node("handle_error", node_handle_error)

    graph.set_entry_point("load_company")
    graph.add_conditional_edges("load_company", route_after_load_company)
    graph.add_conditional_edges("fetch_provider_data", route_after_fetch)
    graph.add_edge("create_source_records", "build_company_snapshot")
    graph.add_edge("build_company_snapshot", "financial_data_agent")
    graph.add_edge("financial_data_agent", "source_quality_agent")
    graph.add_edge("source_quality_agent", "generate_research_sections")
    graph.add_edge("generate_research_sections", "create_citations")
    graph.add_edge("create_citations", "validate_report_schema")
    graph.add_edge("validate_report_schema", "research_completeness_agent")
    graph.add_edge("research_completeness_agent", "citation_validator_v2")
    # Phase 9: Analysis Council chain
    graph.add_edge("citation_validator_v2", "bull_case_agent")
    graph.add_edge("bull_case_agent", "bear_case_agent")
    graph.add_edge("bear_case_agent", "risk_agent")
    graph.add_edge("risk_agent", "valuation_guard_agent")
    graph.add_edge("valuation_guard_agent", "investment_committee_chair")
    # Phase 24: catalyst discovery after the council, before scoring
    graph.add_edge("investment_committee_chair", "catalyst_discovery_agent")
    graph.add_edge("catalyst_discovery_agent", "score_research_attractiveness")
    # Phase 15: insert scoring between council and report save
    graph.add_edge("score_research_attractiveness", "save_draft_report")
    graph.add_edge("save_draft_report", "log_agent_steps")
    graph.add_edge("log_agent_steps", END)
    graph.add_edge("handle_error", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_company_analysis(
    db: AsyncSession,
    company_id: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    provider_name: str | None = None,
    require_schema_valid: bool = False,
    use_llm: bool = False,
    llm_provider: str | None = None,
    on_node: NodeProgressCallback | None = None,
) -> CompanyAnalysisState:
    """
    Execute the company analysis workflow and return the final state.

    Either company_id (UUID string) or (ticker + exchange) must be provided.
    The company must already exist in the database.

    provider_name — override the default provider (None = use config, default: mock).
    require_schema_valid — if True and schema validation fails, status will be "failed".
    use_llm — if True, the generate_research_sections LLM node runs. Default False.
    llm_provider — override config LLM provider (None = use LLM_PROVIDER config, default: mock).
    on_node — optional progress callback, awaited with each graph node's name as
        that node completes. Supplied by the async company-research job so a
        reader watching a five-minute run sees WHICH stage is in flight instead
        of a spinner. It is the graph's OWN node names — no stage vocabulary is
        invented, and no percentage is fabricated. When it is None the graph is
        invoked exactly as before.
    """
    initial_state: CompanyAnalysisState = {
        "company_id": company_id,
        "ticker": ticker,
        "exchange": exchange,
        "agent_run_id": None,
        "company_name": None,
        "company_sector": None,
        "company_description": None,
        "provider_name": provider_name,
        "is_mock": None,
        "analysis_output": None,
        "draft_report_id": None,
        "placeholder_source_id": None,
        "citation_ids": None,
        "company_snapshot": None,
        "provider_source_id": None,
        "price_source_id": None,
        "source_ids": None,
        "schema_validation_result": None,
        "schema_valid": None,
        "use_llm": use_llm,
        "llm_provider": llm_provider,
        "llm_used": None,
        "llm_sections": None,
        "llm_section_warnings": None,
        # Phase 8: Research Team
        "financial_data_summary": None,
        "source_quality_summary": None,
        "research_completeness_summary": None,
        "upgraded_citation_validation": None,
        "research_team_warnings": None,
        "research_team_complete": None,
        # Phase 9: Analysis Council
        "bull_case_summary": None,
        "bear_case_summary": None,
        "risk_summary": None,
        "valuation_guard_summary": None,
        "committee_chair_summary": None,
        "analysis_council_warnings": None,
        "quality_gate_status": None,
        "provisional_internal_status": None,
        "human_review_required": None,
        # Phase 15: Research Attractiveness Scorecard
        "research_attractiveness_scorecard": None,
        # Phase 19.2: composite provider tracking
        "requested_provider_name": provider_name,
        "contributing_providers": None,
        "free_real_snapshot": None,
        "trend_signal_summary": None,
        "provider_warnings": None,
        # Phase 24: News + Catalyst Discovery
        "catalyst_discovery": None,
        "catalyst_agent": None,
        "catalyst_summary": None,
        "catalyst_warnings": None,
        "catalyst_citations": None,
        "catalyst_coverage_status": None,
        "error": None,
        "status": "running",
    }

    graph = build_company_analysis_graph(
        db,
        provider_name=provider_name,
        use_llm=use_llm,
        llm_provider=llm_provider,
    )
    if on_node is None:
        final_state: CompanyAnalysisState = await graph.ainvoke(initial_state)
    else:
        # Same graph, same reducers, same result — the multi-mode stream simply
        # also reports which node just finished. The last ``values`` payload is
        # the final state (asserted equal to ``ainvoke``'s in
        # ``test_async_company_research``), so nothing is re-merged here.
        final_state = initial_state
        async for mode, chunk in graph.astream(
            initial_state, stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final_state = chunk
            elif mode == "updates" and isinstance(chunk, dict):
                for node_name in chunk:
                    try:
                        await on_node(str(node_name))
                    except Exception:  # noqa: BLE001 - progress is never fatal
                        _logger.warning(
                            "Company analysis progress callback failed for node %s.",
                            node_name,
                        )

    # If caller requires schema-valid output and we got an invalid draft, fail
    if require_schema_valid and not final_state.get("schema_valid"):
        final_state["status"] = "failed"
        final_state["error"] = (
            "Schema validation failed — draft does not satisfy report schema. "
            f"Errors: {(final_state.get('schema_validation_result') or {}).get('errors', [])}"
        )

    return final_state
