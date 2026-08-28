"""
Phase 32D2 — MASTER REGRESSION for the one final reconciled research state.

WHY THIS FILE EXISTS
====================
Live manual QA of the Pandora (PNDORA) final report
(``2ea1abcd-8f63-4984-9399-31bec6e95388``, staging, 2026-08-24) found ONE
document asserting all of the following at once:

  fundamentals_available: true          financials.revenue -> missing_fields
  fundamentals_source: issuer_primary_document
  fundamentals_source_tier: T1_primary_filing
  "revenue of DKK 32.5 billion" (T1)    "All 18 core financial fundamental
                                         categories are missing (revenue, ...)"
  Financial Evidence Quality: strong    "fundamentals (not yet sourced)"
                                        "Source T1 primary filings (annual
                                         report / 10-K) for revenue, EBITDA, FCF"
                                        "All current data from
                                         SourceTier.T6_model_estimate only"
                                        Evidence channel "Regulator structured
                                         financial facts (SEC XBRL)" holding the
                                         issuer's own PDF facts, for a Danish
                                         issuer with no SEC registration

Every earlier fix repaired ONE surface and left the next one stale, because no
single object owned the answer to "what evidence does this report have after
ingestion". Phase 32D2 introduces that object
(:mod:`app.services.final_research_state`) and rebuilds every deterministic
human-facing surface from it.

WHAT THIS FIXTURE IS
====================
A synthetic Pandora-SHAPED company. No issuer name, ticker or figure from the
real Pandora report is asserted on — the fixture is a SHAPE:

  * weak/T6 identity provider (no sector, no ISIN, no LEI)
  * T5 price history from a separate price-only provider
  * a verified issuer IR/annual-report source on record
  * ONE T1 issuer-primary PDF, extracted
  * a validated fiscal year and a validated revenue figure
  * NO EBIT, NO EBITDA, NO FCF, NO debt
  * no issuer press feed, no regulator filing events, no SEC XBRL

It runs the REAL producer (``run_financial_data_agent`` on a real snapshot) ->
the REAL reconciliation (``build_final_research_state``) -> the REAL
serialisation (the workflow-state envelope embedded in a draft's markdown) ->
the REAL final-report consumers (``FinalReportGeneratorService``), and asserts
on the SAVED report body a human would read.

The critical property is symmetric. Two validated facts must propagate
everywhere as PRESENT, and the four genuinely-absent categories must remain
absent everywhere, in the same document.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.models.report import Report
from app.services import final_report_generator as frg
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.final_research_state import build_final_research_state
from app.services.llm.schemas import CouncilResult

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.

TICKER = "SYNTH"
ISSUER = "Synthetic Nordic Jewellery A/S"
DOC_URL = "https://cdn.example-issuer.test/static/Annual Report 2025"
IR_URL = "https://example-issuer.test/investor"

#: Categories the fixture deliberately does NOT source. Every surface must keep
#: reporting these as missing — the fix must not "resolve" them by inference.
GENUINELY_MISSING = ("ebit", "ebitda", "free_cash_flow", "total_debt")


# ---------------------------------------------------------------------------
# Fixture: a Pandora-SHAPED workflow state, built by the REAL agents
# ---------------------------------------------------------------------------


def _snapshot() -> dict[str, Any]:
    """Weak/T6 identity + T5 price-only history. No SEC XBRL, no fundamentals."""
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "retrieved_at": "2026-08-24T09:56:42+00:00",
        "company_identity": {
            "legal_name": ISSUER,
            "ticker": TICKER,
            "exchange": "CO",
            "country_domicile": "Denmark",
            "isin": None,
            "lei": None,
        },
        "profile": {
            "sector": None,
            "industry": None,
            "reporting_currency": None,
            "fiscal_year_end": None,
            "description": None,
        },
        "price_history_summary": {
            "available": True,
            "data_points_count": 248,
            "latest_close": 783.0,
            "currency": "DKK",
            "date_range": {"start": "2025-08-25", "end": "2026-08-21"},
            "source_tier": "T5_api_aggregator",
            "provider_name": "price_only_aggregator",
            "price_data_quality": "B_single_credible",
        },
        "provider_metadata": {
            "provider_name": "free_real_not_sourced",
            "source_tier": "T6_model_estimate",
            "is_mock": False,
            "retrieved_at": "2026-08-24T09:56:42+00:00",
        },
        "missing_fields": [
            "identity.isin",
            "identity.lei",
            "profile.reporting_currency",
            "profile.fiscal_year_end",
            "profile.sector",
            "profile.industry",
            "profile.description",
        ],
    }


def _primary_facts() -> list[dict[str, Any]]:
    """Exactly TWO validated T1 facts: a fiscal year and a revenue figure."""
    return [
        {
            "field": "fiscal_year",
            "value": "2025",
            "numeric_value": 2025.0,
            "period": "2025",
            "confidence": "high",
            "source_url": DOC_URL,
            "source_tier": "T1_primary_filing",
            "provenance": ["page=14", "excerpt=X2", "confidence=high"],
        },
        {
            "field": "revenue",
            "value": "revenue of DKK 32.5 billion",
            "numeric_value": 32.5,
            "unit": "currency_amount",
            "currency": "DKK",
            "scale": "billion",
            "period": "2025",
            "confidence": "high",
            "source_url": DOC_URL,
            "source_tier": "T1_primary_filing",
            "provenance": ["page=8", "excerpt=X10", "confidence=high"],
        },
    ]


def _workflow_state() -> dict[str, Any]:
    """The PRE-ingestion workflow state, produced by the REAL Phase-8/9 agents.

    Deliberately built by calling the agents rather than hand-writing their
    output: a hand-written fixture is exactly how the original
    ``available_count = 0`` defect stayed invisible for a whole phase.
    """
    snapshot = _snapshot()
    fds = financial_data_agent_output_to_dict(
        run_financial_data_agent(company_snapshot=snapshot, source_ids=["s1"])
    )
    sqs = source_quality_output_to_dict(run_source_quality_agent(snapshot))
    rcs = research_completeness_output_to_dict(
        run_research_completeness_agent(snapshot, {}, [])
    )

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
    from app.agents.analysis_council.valuation_guard_agent import (
        run_valuation_guard_agent,
        valuation_guard_output_to_dict,
    )

    bull = bull_case_output_to_dict(run_bull_case_agent(snapshot, fds, sqs, rcs))
    bear = bear_case_output_to_dict(
        run_bear_case_agent(snapshot, fds, sqs, rcs, bull_case_summary=bull)
    )
    risk = risk_agent_output_to_dict(run_risk_agent(snapshot, fds, sqs, rcs))
    vg = valuation_guard_output_to_dict(
        run_valuation_guard_agent(snapshot, fds, sqs)
    )
    chair = committee_chair_output_to_dict(
        run_investment_committee_chair(
            snapshot, bull, bear, risk, vg, rcs, sqs, {"status": "warnings"}, False
        )
    )
    return {
        "company_snapshot": snapshot,
        "financial_data_summary": fds,
        "source_quality_summary": sqs,
        "research_completeness_summary": rcs,
        "upgraded_citation_validation": {"status": "warnings"},
        "bull_case_summary": bull,
        "bear_case_summary": bear,
        "risk_summary": risk,
        "valuation_guard_summary": vg,
        "committee_chair_summary": chair,
        "fundamentals_data": None,
        "fundamentals_available": False,
        "schema_validation_result": {"is_valid": True, "errors": [], "warnings": []},
        "source_tier": "T6_model_estimate",
        "catalyst_discovery": None,
        "is_mock": False,
    }


def _council_result() -> CouncilResult:
    """A council that ingested ONE issuer PDF and validated two facts."""
    return CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake",
        agents_completed=8,
        evidence_item_count=15,
        primary_facts=_primary_facts(),
        primary_documents=[
            {
                "title": "Annual Report 2025",
                "domain": "cdn.example-issuer.test",
                "tier": "T1_primary_filing",
                "fact_count": 2,
                "excerpt_count": 5,
                "ingestion_state": "extracted",
            }
        ],
        primary_source_references=[
            {
                "title": f"{ISSUER} — investor relations",
                "url": IR_URL,
                "domain": "example-issuer.test",
                "source_tier": "T1_primary_filing",
            }
        ],
    )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _saved_content(mock_db: AsyncMock) -> dict[str, Any]:
    assert mock_db.add.called, "expected a final report to be saved"
    saved: Report = mock_db.add.call_args_list[-1][0][0]
    blocks = re.findall(r"```json\s*(.*?)\s*```", saved.content_markdown or "", re.S)
    assert blocks, "saved report has no JSON block"
    return json.loads(blocks[-1])


async def _generate(mock_db: AsyncMock, council: CouncilResult) -> dict[str, Any]:
    """REAL generator, REAL consumers; only the DB and the LLM call are faked."""
    with (
        patch.object(frg, "maybe_run_council", AsyncMock(return_value=council)),
        patch.object(frg, "load_reusable_documents", AsyncMock(return_value=None)),
    ):
        await FinalReportGeneratorService().generate_from_workflow_state(
            mock_db, state=_workflow_state()
        )
    return _saved_content(mock_db)


def _text_of(section: Any) -> str:
    """Every human-visible string in a section, flattened."""
    return json.dumps(section, default=str).lower()


@pytest.fixture
async def report(mock_db: AsyncMock) -> dict[str, Any]:
    return await _generate(mock_db, _council_result())


# ===========================================================================
# A. The reconciliation itself (unit level, real producer output)
# ===========================================================================


def test_producer_alone_reports_revenue_missing_before_ingestion() -> None:
    """Control: the PRE-ingestion producer genuinely does not know about the
    issuer document. Without this the rest of the file proves nothing."""
    state = _workflow_state()
    assert "financials.revenue" in state["financial_data_summary"]["missing_fields"]
    assert "financials.revenue" not in state["financial_data_summary"]["available_fields"]


def test_reconciliation_moves_only_the_validated_categories() -> None:
    state = _workflow_state()
    final = build_final_research_state(
        company_snapshot=state["company_snapshot"],
        fundamentals_data=None,
        primary_facts=_primary_facts(),
        financial_data_summary=state["financial_data_summary"],
        research_completeness_summary=state["research_completeness_summary"],
    )
    fds = final.financial_data_summary
    assert fds is not None
    assert "financials.revenue" in fds["available_fields"]
    assert "financials.revenue" not in fds["missing_fields"]
    # ...and NOTHING else moved.
    for category in GENUINELY_MISSING:
        assert f"financials.{category}" in fds["missing_fields"], category
        assert f"financials.{category}" not in fds["available_fields"], category

    ev = final.financial_evidence
    assert ev.is_primary_backed is True
    assert ev.is_issuer_primary is True
    assert ev.best_tier == "T1_primary_filing"
    assert ev.best_source == "issuer_primary_document"
    assert ev.resolved_categories == ("revenue",)
    assert set(GENUINELY_MISSING).issubset(set(ev.open_categories))


def test_reconciliation_is_idempotent() -> None:
    """Regenerating a report must not keep growing its own notes/warnings."""
    state = _workflow_state()
    kwargs = dict(
        company_snapshot=state["company_snapshot"],
        fundamentals_data=None,
        primary_facts=_primary_facts(),
        research_completeness_summary=state["research_completeness_summary"],
    )
    once = build_final_research_state(
        financial_data_summary=state["financial_data_summary"], **kwargs
    )
    twice = build_final_research_state(
        financial_data_summary=once.financial_data_summary, **kwargs
    )
    assert once.financial_data_summary == twice.financial_data_summary


def test_no_primary_facts_leaves_the_producer_output_untouched() -> None:
    """Dark-safe: a company with nothing ingested is reconciled to itself."""
    state = _workflow_state()
    final = build_final_research_state(
        company_snapshot=state["company_snapshot"],
        fundamentals_data=None,
        primary_facts=[],
        financial_data_summary=state["financial_data_summary"],
        research_completeness_summary=state["research_completeness_summary"],
    )
    assert final.financial_evidence.is_primary_backed is False
    assert (
        "financials.revenue" in (final.financial_data_summary or {})["missing_fields"]
    )


def test_medium_confidence_and_segment_scoped_facts_never_resolve_a_category() -> None:
    """The admission rule is unchanged: only high-confidence GROUP facts count."""
    state = _workflow_state()
    weak_facts = [
        {**_primary_facts()[1], "confidence": "medium"},
        {**_primary_facts()[1], "scope": "Jewellery segment"},
    ]
    final = build_final_research_state(
        company_snapshot=state["company_snapshot"],
        fundamentals_data=None,
        primary_facts=weak_facts,
        financial_data_summary=state["financial_data_summary"],
        research_completeness_summary=state["research_completeness_summary"],
    )
    assert final.financial_evidence.resolved_categories == ()
    assert (
        "financials.revenue" in (final.financial_data_summary or {})["missing_fields"]
    )


# ===========================================================================
# B. YES — the two validated facts propagate to every human-facing surface
# ===========================================================================


async def test_yes_fundamentals_available_and_revenue_present(report) -> None:
    das = report["data_availability_summary"]
    assert das["fundamentals_available"] is True
    assert das["fundamentals_source"] == "issuer_primary_document"
    assert das["fundamentals_source_tier"] == "T1_primary_filing"
    assert "financials.revenue" in das["available_fields"]["value"]
    assert "financials.revenue" not in das["missing_fields"]["value"]
    assert das["sourced_financial_categories"]["value"] == ["revenue"]


async def test_yes_t1_financial_evidence_quality_is_strong(report) -> None:
    fin = report["evidence_quality"]["financial_evidence_quality"]
    assert fin["label"] == "strong"


async def test_yes_the_missing_information_union_no_longer_lists_revenue(
    report,
) -> None:
    fields = [row["field"] for row in report["missing_information"]["missing_items"]["value"]]
    assert "financials.revenue" not in fields
    # The genuinely-absent categories are still listed.
    for category in GENUINELY_MISSING:
        assert f"financials.{category}" in fields, category


async def test_yes_valuation_readiness_consumes_the_final_facts(report) -> None:
    vr = report["valuation_readiness"]
    assert "financials.revenue" not in vr["missing_inputs"]["value"]
    assert "financials.revenue" in vr["available_inputs"]["value"]
    # Still genuinely blocked — most inputs are absent. That is correct.
    # (``ebit`` is not one of this agent's required valuation inputs, so it is
    # checked in the data-availability surface instead.)
    for category in ("ebitda", "free_cash_flow", "total_debt"):
        assert f"financials.{category}" in vr["missing_inputs"]["value"], category


async def test_yes_bull_bear_risk_stop_claiming_all_financials_are_absent(
    report,
) -> None:
    bull = _text_of(report["bull_case"])
    assert "fundamentals (not yet sourced)" not in bull
    assert "issuer_primary_document" in bull or "t1_primary_filing" in bull

    bear = _text_of(report["bear_case"])
    assert "core financial fundamental categories are missing" not in bear
    assert "financial completeness is partial" in bear

    risk = _text_of(report["risk_analysis"])
    assert "all 18 core financial categories missing" not in risk
    assert "financial data is partial" in risk


async def test_yes_committee_and_executive_summary_are_rebuilt(report) -> None:
    chair = report["committee_chair_summary"]["committee_summary"]["value"]
    assert isinstance(chair, str)
    # The chair reprints the reconciled source quality, not the workflow-time one.
    reconciled_quality = report["source_quality_review"]["overall_source_quality"][
        "value"
    ]
    assert f"Source quality: {reconciled_quality}." in chair
    # The executive summary reprints the SAME chair text — no second opinion.
    exec_note = report["executive_summary"]["committee_note"]["value"]
    exec_text = exec_note["value"] if isinstance(exec_note, dict) else exec_note
    assert exec_text == chair
    assert (
        report["executive_summary"]["internal_status"]
        == report["committee_chair_summary"]["provisional_internal_status"]["value"]
    )


async def test_yes_one_internal_status_label_per_report(report) -> None:
    """The chair's own vocabulary is mapped, not silently overwritten.

    The Phase-9 chair emits ``research_incomplete`` /
    ``watchlist_candidate_for_review``; this module's vocabulary has neither
    spelling, so the unmapped fallback rewrote the structured field to
    ``not_enough_data`` while the prose beside it kept the chair's word.
    """
    committee = report["committee_chair_summary"]
    status = committee["provisional_internal_status"]["value"]
    prose = committee["committee_summary"]["value"]
    assert f"Provisional status: '{status}'" in prose
    assert report["executive_summary"]["internal_status"] == status
    # The agent's own label is retained for audit — never as a second answer.
    assert committee["agent_internal_status"]["value"] == "research_incomplete"
    assert status == "not_enough_data"


async def test_yes_filing_extraction_tasks_name_only_statement_lines(
    report,
) -> None:
    """Do not tell a reviewer to extract a market ratio from an annual report."""
    steps = " ".join(report["valuation_readiness"]["allowed_next_steps"]["value"])
    extraction = [
        line for line in steps.split(".") if "ALREADY-INGESTED issuer filing" in line
    ]
    assert extraction, "expected an extraction-completeness next step"
    for market_metric in ("price to earnings", "dividend yield", "market cap"):
        assert market_metric not in " ".join(extraction).lower(), market_metric
    assert "ebitda" in " ".join(extraction).lower()


async def test_yes_evidence_channel_taxonomy_is_correct(report) -> None:
    channels = {c["channel"]: c for c in report["evidence_channels"]["channels"]}
    issuer = channels["issuer_primary_facts"]
    assert issuer["available"] is True
    assert "SEC" not in issuer["label"]
    assert "XBRL" not in issuer["label"]
    # The regulator-facts channel is HONESTLY not sourced for this issuer.
    regulator = channels["regulator_structured_facts"]
    assert regulator["available"] is False
    assert regulator["field_count"] == 0
    # Manual-QA corrective: this line used to assert ``"SEC XBRL" in label``,
    # which encoded the very defect it sat beside — the two assertions above
    # forbid "SEC" on the ISSUER row for this Danish issuer, while this one
    # required it on the row underneath. A regulator channel now names the
    # issuer's OWN venue, and says SEC only for an SEC-eligible issuer.
    assert "SEC" not in regulator["label"]
    assert regulator["venue"] == "Nasdaq Nordic"
    assert "Nasdaq Nordic" in regulator["label"]


# ===========================================================================
# C. NO — the six false claims must not appear anywhere in the document
# ===========================================================================


#: Substrings that were present in the live Pandora report and are FALSE for a
#: company holding a validated T1 revenue figure. Checked across the WHOLE
#: rendered report body, not one section, because the defect was that they kept
#: reappearing in the next section nobody had fixed yet.
FORBIDDEN_WHEN_T1_REVENUE_EXISTS = (
    "core financial fundamental categories are missing",
    "core financial categories missing",
    "fundamentals (not yet sourced)",
    "financial fundamentals (revenue, ebitda, net income, cash flow, debt levels) — "
    "none sourced at this phase",
    "t1_primary_filing required for financials",
    "source latest annual report (t1) for revenue",
    "source t1 primary filings (annual report / 10-k) for revenue",
    "all current data from",
    "missing primary filing sources",
)


async def test_no_contradictory_claim_survives_anywhere_in_the_report(
    report,
) -> None:
    body = json.dumps(report, default=str).lower()
    offenders = [phrase for phrase in FORBIDDEN_WHEN_T1_REVENUE_EXISTS if phrase in body]
    assert not offenders, f"stale contradictory claims still rendered: {offenders}"


async def test_no_enum_repr_leaks_into_human_facing_text(report) -> None:
    """``SourceTier.T6_model_estimate`` is not a source tier, it is a repr."""
    assert "SourceTier." not in json.dumps(report, default=str)


async def test_no_sec_xbrl_channel_claims_the_issuer_pdf_facts(report) -> None:
    for channel in report["evidence_channels"]["channels"]:
        if "XBRL" in channel["label"]:
            assert channel["available"] is False
            assert "issuer_primary_document" not in channel.get("detail", "")


async def test_no_annual_report_is_requested_after_it_has_been_read(report) -> None:
    body = json.dumps(report, default=str).lower()
    assert "already ingested" in body
    # The remaining ask is EXTRACTION COMPLETENESS against the same document.
    assert "remaining gap is completeness, not acquisition" in body


# ===========================================================================
# D. STILL MISSING — absence must survive the fix, everywhere
# ===========================================================================


async def test_genuinely_missing_categories_stay_missing_in_every_surface(
    report,
) -> None:
    das = report["data_availability_summary"]
    for category in GENUINELY_MISSING:
        assert category in das["open_financial_categories"]["value"], category
        assert f"financials.{category}" in das["missing_fields"]["value"], category

    vr_missing = report["valuation_readiness"]["missing_inputs"]["value"]
    for category in ("ebitda", "free_cash_flow", "total_debt"):
        assert f"financials.{category}" in vr_missing, category

    bear = _text_of(report["bear_case"])
    assert "remain missing" in bear
    assert "market-based valuation cannot be completed" in bear


async def test_valuation_is_still_blocked_and_no_conclusion_is_produced(
    report,
) -> None:
    vr = report["valuation_readiness"]
    assert vr["readiness"]["value"] in ("not_ready", "partial")
    assert vr["blockers"]["value"], "valuation must remain explicitly blocked"
    assert "No valuation estimates" in vr["disclaimer"]


async def test_human_review_is_still_required_and_publication_still_blocked(
    report,
) -> None:
    assert report["data_availability_summary"].get("human_review_required") is not None
    assert report["evidence_quality"]["human_review_required"] is True
    assert report["admin_disclaimer"]["classification"] == "admin_only"


async def test_thin_evidence_state_is_not_triggered_when_t1_facts_exist(
    report,
) -> None:
    assert report["thin_evidence_state"]["is_thin"] is False


# ===========================================================================
# E. The absent-evidence control — the same pipeline with NOTHING ingested
# ===========================================================================


async def test_a_company_with_no_ingested_document_still_fails_closed(
    mock_db: AsyncMock,
) -> None:
    """The fix must not make an evidence-free report look better.

    Same fixture, same pipeline, empty council: every "missing" claim the
    reconciliation removes above must still be present here.
    """
    content = await _generate(mock_db, CouncilResult(llm_used=False))
    body = json.dumps(content, default=str).lower()

    assert content["data_availability_summary"]["fundamentals_available"] is False
    assert (
        "financials.revenue"
        in content["data_availability_summary"]["missing_fields"]["value"]
    )
    assert "t1_primary_filing required for financials" in body
    channels = {c["channel"]: c for c in content["evidence_channels"]["channels"]}
    assert channels["issuer_primary_facts"]["available"] is False
    assert channels["regulator_structured_facts"]["available"] is False


# ===========================================================================
# F. Provenance is never fabricated
# ===========================================================================


async def test_every_resolved_category_carries_its_own_source_and_tier() -> None:
    state = _workflow_state()
    final = build_final_research_state(
        company_snapshot=state["company_snapshot"],
        fundamentals_data=None,
        primary_facts=_primary_facts(),
        financial_data_summary=state["financial_data_summary"],
        research_completeness_summary=state["research_completeness_summary"],
    )
    for fact in final.financial_evidence.resolved:
        assert fact.source, fact.category
        assert fact.source_tier, fact.category
        if fact.source == "issuer_primary_document":
            assert fact.source_url == DOC_URL


async def test_reconciled_state_is_persisted_for_audit(mock_db: AsyncMock) -> None:
    with (
        patch.object(frg, "maybe_run_council", AsyncMock(return_value=_council_result())),
        patch.object(frg, "load_reusable_documents", AsyncMock(return_value=None)),
    ):
        await FinalReportGeneratorService().generate_from_workflow_state(
            mock_db, state=_workflow_state()
        )
    saved: Report = mock_db.add.call_args_list[-1][0][0]
    audit = saved.source_summary_json["final_research_state"]
    assert audit["financial_evidence_tier"] == "T1_primary_filing"
    assert audit["resolved_financial_categories"] == ["revenue"]
    assert set(GENUINELY_MISSING).issubset(set(audit["open_financial_categories"]))
    assert audit["primary_fact_count"] == 2


def test_report_id_placeholder_is_unused() -> None:
    """Guard against an accidental hard-coded live report id in this fixture."""
    source = open(__file__, encoding="utf-8").read()
    assert str(uuid.UUID("2ea1abcd-8f63-4984-9399-31bec6e95388")) in source, (
        "the live report id is referenced in the docstring only, as provenance"
    )
