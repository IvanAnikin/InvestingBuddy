"""
Phase 31 — Internal research MEMO builder tests.

Covers the DETERMINISTIC, source-aware ``_build_research_memo`` synthesis:
  * Over a RICH report_content + council, the memo renders every section,
    is citation-bound, cites the council's primary facts, and is safety-clean —
    the forbidden rating / valuation tokens appear ONLY inside the exempt
    ``disallowed_outputs`` field.
  * Over a THIN report_content (no primary facts / no council / blocked
    extraction), the memo degrades honestly — ``what_is_missing`` is prominent,
    the primary-evidence and council sections are honest-empty, nothing is
    fabricated.
  * The memo never mutates the report it reads and its top-level shape is bounded.
  * The full report with the flag ON carries ``research_memo`` while schema_valid,
    safety_valid, human_review_required stay True and publication_ready stays
    False; with the flag OFF there is NO ``research_memo`` key and the rest of the
    report is byte-for-byte identical.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import safety_terms
from app.services.final_report_generator import (
    FinalReportGeneratorService,
    _build_bear_case,
    _build_bull_case,
    _build_committee_chair_summary,
    _build_company_identity,
    _build_data_availability_summary,
    _build_discovery_rationale,
    _build_financial_snapshot,
    _build_human_review_checklist,
    _build_industry_event_context,
    _build_missing_information,
    _build_news_catalyst_discovery,
    _build_research_memo,
    _build_risk_analysis,
    _build_source_citation_appendix,
    _build_source_quality_review,
    run_safety_gate,
)
from app.services.llm.schemas import (
    AGENT_FINANCIAL_ANALYST,
    AGENT_RED_TEAM,
    AgentKeyPoint,
    AgentRiskGap,
    CouncilAgentOutput,
    CouncilResult,
)

_MEMO_SECTION_KEYS = {
    "type",
    "header",
    "company_identity",
    "why_surfaced",
    "what_is_sourced",
    "what_is_missing",
    "primary_evidence_summary",
    "catalyst_event_evidence",
    "financial_facts_summary",
    "business_risk_summary",
    "council_disagreement_red_team",
    "research_next_steps",
    "human_review_checklist",
    "source_appendix",
    "disallowed_outputs",
    "note",
    "disclaimer",
    "human_review_required",
}

# The literal forbidden vocabulary the memo must never emit OUTSIDE the exempt
# ``disallowed_outputs`` notice.
_FORBIDDEN_TOKENS = ("BUY", "SELL", "HOLD", "WATCH")
_FORBIDDEN_PHRASES = ("price target", "fair value", "intrinsic value")


# ---------------------------------------------------------------------------
# Lightweight stand-ins for ORM rows (memo reads assembled dicts, not the DB)
# ---------------------------------------------------------------------------


def _source_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type="sec_filing",
        title="Annual report 2024",
        url="https://www.richemont.com/annual-report-2024.pdf",
        publisher="Compagnie Financiere Richemont",
        credibility_score=0.9,
        retrieved_at=None,
    )


def _candidate() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        ticker="CFR",
        exchange="SWX",
        candidate_status="screened_in",
        source_tier="T5_api_aggregator",
        data_quality="C_moderate",
        discovery_reasons_json=["Screened into luxury-watch thesis universe"],
        available_data_json=["price_history", "market_cap"],
        missing_data_json=["segment_breakdown"],
        warnings_json=[],
    )


# ---------------------------------------------------------------------------
# Rich content — CFR.SW-like, with extracted primary facts + a real council
# ---------------------------------------------------------------------------


def _rich_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T1_primary_filing",
        "retrieved_at": "2026-07-01T00:00:00Z",
        "company_identity": {
            "legal_name": "Compagnie Financiere Richemont SA",
            "ticker": "CFR",
            "exchange": "SWX",
            "country_domicile": "Switzerland",
            "isin": "CH0210483332",
            "lei": "529900XN6D2FYFTQ1H10",
        },
        "profile": {"sector": "Consumer Cyclical", "reporting_currency": "EUR"},
        "price_history_summary": {
            "available": True,
            "latest_close": 120.5,
            "currency": "CHF",
            "date_range": {"end": "2026-06-30"},
        },
        "missing_fields": ["segment_breakdown"],
    }


def _primary_facts() -> list[dict[str, Any]]:
    # One source_url carries a credential-bearing param to prove it is stripped.
    return [
        {
            "field": "revenue",
            "value": "20,616 million",
            "numeric_value": 20616.0,
            "unit": "million",
            "currency": "EUR",
            "scale": "million",
            "period": "FY2024",
            "page_number": 12,
            "excerpt_id": "exc_1",
            "confidence": "high",
            "source_url": "https://www.richemont.com/ar-2024.pdf?api_token=SECRET123",
        },
        {
            "field": "net_income",
            "value": "2,357 million",
            "numeric_value": 2357.0,
            "unit": "million",
            "currency": "EUR",
            "scale": "million",
            "period": "FY2024",
            "page_number": 14,
            "excerpt_id": "exc_2",
            "confidence": "high",
            "source_url": "https://www.richemont.com/ar-2024.pdf",
        },
        {
            "field": "reporting_currency",
            "value": "EUR",
            "period": "FY2024",
            "confidence": "high",
            "source_url": "https://www.richemont.com/ar-2024.pdf",
        },
    ]


def _rich_council() -> CouncilResult:
    return CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-council",
        committee_label="requires_more_evidence",
        primary_facts=_primary_facts(),
        primary_documents=[
            {
                "title": "Annual report 2024",
                "domain": "richemont.com",
                "tier": "T1_primary_filing",
                "excerpt_count": 8,
                "fact_count": 3,
                "requires_translation": False,
                "warnings": ["Some pages omitted to stay within budget"],
            }
        ],
        agents=[
            CouncilAgentOutput(
                agent_name=AGENT_FINANCIAL_ANALYST,
                summary="Top-line grew year on year per the annual report.",
                key_points=[
                    AgentKeyPoint(
                        claim="Revenue was EUR 20,616 million in FY2024",
                        citation_ids=["exc_1"],
                        confidence="high",
                        data_quality="A",
                    )
                ],
                unsupported_claims=["Segment margins will expand next year"],
            ),
            CouncilAgentOutput(
                agent_name=AGENT_RED_TEAM,
                summary="Primary evidence is thin beyond the top-line figures.",
                key_points=[
                    AgentKeyPoint(
                        claim="Segment-level detail is not sourced",
                        citation_ids=[],
                        confidence="low",
                        data_quality="D",
                        is_limitation=True,
                    )
                ],
                risks_or_gaps=[
                    AgentRiskGap(
                        item="No segment-margin evidence in the pack",
                        citation_ids=[],
                        severity="medium",
                    )
                ],
            ),
        ],
    )


def _rich_report_content() -> dict[str, Any]:
    snapshot = _rich_snapshot()
    facts = _primary_facts()
    sources = [_source_row(), _source_row()]
    financial_data_summary = {
        "available_count": 4,
        "missing_count": 2,
        "warnings_count": 0,
        "available_fields": ["revenue", "net_income"],
        "missing_fields": ["free_cash_flow", "segment_breakdown"],
        "warnings": [],
    }
    source_quality_summary = {
        "overall_source_quality": "moderate",
        "weak_sources_count": 1,
        "strong_sources_count": 2,
        "t5_promoted_warnings": [],
        "warnings": [],
    }
    fundamentals_data = {
        "highlights": {
            "market_capitalization": 70000.0,
            "ebitda": 5000.0,
            "revenue_ttm": 20000.0,
            "pe_ratio": 22.5,
        }
    }
    committee_chair_summary = {
        "committee_summary": "Insufficient primary evidence beyond top-line; monitor.",
        "provisional_internal_status": "needs_primary_sources",
        "bull_bear_balance": "balanced",
        "quality_gate_status": {},
        "primary_open_questions": ["What is the segment-level operating margin?"],
        "research_next_steps": ["Obtain segment breakdown from the FY2024 report"],
        "warnings": [],
    }
    candidate = _candidate()

    checklist = [
        item.model_dump()
        for item in _build_human_review_checklist(
            safety_valid=True,
            schema_valid=True,
            has_scorecard=False,
            has_bull_bear=True,
            has_risk=True,
            has_citations=True,
            missing_count=2,
            is_mock=False,
            has_t1_t2=True,
        )
    ]

    return {
        "company_identity": _build_company_identity(snapshot, None, primary_facts=facts),
        "discovery_rationale": _build_discovery_rationale(candidate),
        "data_availability_summary": _build_data_availability_summary(
            financial_data_summary, True, "T1_primary_filing"
        ),
        "financial_snapshot": _build_financial_snapshot(
            snapshot, fundamentals_data, primary_facts=facts
        ),
        "source_quality_review": _build_source_quality_review(
            source_quality_summary, sources, primary_facts=facts
        ),
        "missing_information": _build_missing_information(
            financial_data_summary, None, snapshot, candidate
        ),
        "bull_case": _build_bull_case(
            {
                "positive_thesis_points": ["Strong brand portfolio", "Resilient demand"],
                "potential_tailwinds": ["Travel-retail recovery"],
                "evidence_used": ["Annual report 2024"],
                "assumptions": ["Demand normalises"],
                "missing_evidence": ["Segment margins"],
                "confidence_level": "medium",
                "warnings": [],
            }
        ),
        "bear_case": _build_bear_case(
            {
                "negative_thesis_points": ["China demand exposure", "FX headwinds"],
                "potential_headwinds": ["Weaker luxury cycle"],
                "key_unknowns": ["Segment mix"],
                "evidence_used": ["Annual report 2024"],
                "missing_evidence": ["Regional split"],
                "confidence_level": "medium",
                "warnings": [],
            }
        ),
        "risk_analysis": _build_risk_analysis(
            {
                "business_risks": ["Cyclical luxury demand"],
                "financial_risks": ["FX translation"],
                "market_risks": ["Consumer sentiment"],
                "regulatory_geopolitical_risks": ["Trade policy"],
                "data_quality_risks": ["Limited primary sources"],
                "source_quality_risks": ["Aggregator reliance"],
                "risk_summary": "Cyclical exposure to luxury demand and FX moves.",
                "warnings": [],
            }
        ),
        "committee_chair_summary": _build_committee_chair_summary(
            committee_chair_summary
        ),
        "human_review_checklist": checklist,
        "source_citation_appendix": _build_source_citation_appendix(sources, []),
        "news_catalyst_discovery": _build_news_catalyst_discovery(
            {
                "coverage_quality": "partial",
                "summary": {
                    "total_events": 3,
                    "company_specific_count": 2,
                    "positive_count": 1,
                    "negative_count": 1,
                    "neutral_count": 1,
                },
                "events": [],
                "filing_events": [],
                "industry_events": [],
            }
        ),
        "industry_event_context": _build_industry_event_context(
            [
                {
                    "source_id": "ted_eu",
                    "source_name": "EU TED",
                    "title": "EU public procurement notices",
                    "url": "https://ted.europa.eu",
                    "tier": "T2_regulator_or_gov",
                    "reference": "Tenders and awards for the luxury/retail theme",
                    "gap": "Live tenders/awards not fetched at report time",
                }
            ]
        ),
    }


# ---------------------------------------------------------------------------
# Thin content — UHR/KER-like: company data exists, extraction blocked, no council
# ---------------------------------------------------------------------------


def _thin_report_content() -> dict[str, Any]:
    snapshot = {
        "is_mock": False,
        "source_tier": "T5_api_aggregator",
        "retrieved_at": "2026-07-01T00:00:00Z",
        "company_identity": {
            "legal_name": "The Swatch Group AG",
            "ticker": "UHR",
            "exchange": "SWX",
            "country_domicile": "Switzerland",
        },
        "profile": {"sector": "Consumer Cyclical", "reporting_currency": "CHF"},
        "price_history_summary": {
            "available": True,
            "latest_close": 190.0,
            "currency": "CHF",
            "date_range": {"end": "2026-06-30"},
        },
        "missing_fields": ["annual_report_text", "segment_breakdown"],
    }
    financial_data_summary = {
        "available_count": 1,
        "missing_count": 3,
        "warnings_count": 1,
        "available_fields": ["market_cap"],
        "missing_fields": ["revenue_primary", "operating_profit", "free_cash_flow"],
        "warnings": ["Annual report PDF was scanned / JS-gated; extraction blocked."],
    }
    checklist = [
        item.model_dump()
        for item in _build_human_review_checklist(
            safety_valid=True,
            schema_valid=True,
            has_scorecard=False,
            has_bull_bear=False,
            has_risk=False,
            has_citations=False,
            missing_count=3,
            is_mock=False,
            has_t1_t2=False,
        )
    ]
    return {
        "company_identity": _build_company_identity(snapshot, None),
        "discovery_rationale": _build_discovery_rationale(None),
        "data_availability_summary": _build_data_availability_summary(
            financial_data_summary, False, "T5_api_aggregator"
        ),
        "financial_snapshot": _build_financial_snapshot(snapshot, None),
        "source_quality_review": _build_source_quality_review(None, []),
        "missing_information": _build_missing_information(
            financial_data_summary, None, snapshot, None
        ),
        "bull_case": _build_bull_case(None),
        "bear_case": _build_bear_case(None),
        "risk_analysis": _build_risk_analysis(None),
        "committee_chair_summary": _build_committee_chair_summary(None),
        "human_review_checklist": checklist,
        "source_citation_appendix": _build_source_citation_appendix([], []),
        "news_catalyst_discovery": _build_news_catalyst_discovery(None),
    }


# ---------------------------------------------------------------------------
# Safety helper
# ---------------------------------------------------------------------------


def _assert_memo_safe(memo: dict[str, Any]) -> None:
    """Every memo field EXCEPT ``disallowed_outputs`` is forbidden-term-free."""
    for key, value in memo.items():
        if key == "disallowed_outputs":
            continue
        assert safety_terms.scan_value(value) == [], f"forbidden term in memo.{key}"


# ---------------------------------------------------------------------------
# Direct unit tests — rich content
# ---------------------------------------------------------------------------


def test_memo_over_rich_content_has_all_sections() -> None:
    memo = _build_research_memo(
        _rich_report_content(), _rich_council(), source_tier="T1_primary_filing"
    )
    assert memo["type"] == "research_memo"
    # Every documented section is present and the shape is bounded (no extras).
    assert set(memo.keys()) == _MEMO_SECTION_KEYS
    assert memo["human_review_required"] is True
    # Header reuses the internal NOT-INVESTMENT-ADVICE disclaimer wording.
    assert "NOT INVESTMENT ADVICE" in memo["header"]["value"]


def test_memo_cites_primary_facts_from_council() -> None:
    memo = _build_research_memo(
        _rich_report_content(), _rich_council(), source_tier="T1_primary_filing"
    )
    pes = memo["primary_evidence_summary"]
    # Manual-QA: the key now NAMES its population (report primary facts).
    assert pes["report_primary_fact_count"] == 3
    assert pes["fact_count_label"] == "Report primary facts"
    assert pes["primary_document_count"] == 1

    fact_rows = pes["primary_facts"]["value"]
    revenue = next(f for f in fact_rows if f["field"] == "revenue")
    assert revenue["value"] == "20,616 million"
    assert revenue["currency"] == "EUR"
    assert revenue["period"] == "FY2024"
    # Every cited fact carries its source URL (the citation of record) and it is
    # token-stripped — no credential residue leaks into the memo.
    assert revenue["source_url"].startswith("https://www.richemont.com/ar-2024.pdf")
    assert revenue["provenance"] == "sourced_fact"
    for f in fact_rows:
        assert "api_token" not in (f["source_url"] or "")
        assert "SECRET123" not in (f["source_url"] or "")
    for url in memo["source_appendix"]["primary_fact_source_urls"]["value"]:
        assert "api_token" not in (url or "")

    # T1 primary-filing datapoints flow into the financial-facts summary.
    t1 = memo["financial_facts_summary"]["t1_primary_filing_facts"]["value"]
    assert "revenue_primary_filing" in t1
    assert "net_income_primary_filing" in t1
    assert t1["revenue_primary_filing"]["currency"] == "EUR"


def test_memo_surfaces_red_team_dissent() -> None:
    memo = _build_research_memo(
        _rich_report_content(), _rich_council(), source_tier="T1_primary_filing"
    )
    dissent = memo["council_disagreement_red_team"]
    assert dissent["council_ran"] is True
    assert dissent["red_team_present"] is True
    assert dissent["committee_label"] == "requires_more_evidence"
    assert "thin" in dissent["red_team_summary"]["value"].lower()
    assert dissent["red_team_risks_or_gaps"]["value"]
    # Any agent's unsupported claims are surfaced as the dissent surface.
    unsupported = [
        u["claim"] for u in dissent["unsupported_claims_across_agents"]["value"]
    ]
    assert any("Segment margins" in c for c in unsupported)


def test_memo_research_next_steps_and_checklist_reference() -> None:
    memo = _build_research_memo(
        _rich_report_content(), _rich_council(), source_tier="T1_primary_filing"
    )
    steps = memo["research_next_steps"]["research_next_steps"]["value"]
    assert any("segment breakdown" in s.lower() for s in steps)
    # The checklist is referenced, not recomputed, and surfaces not-completed items.
    hrc = memo["human_review_checklist"]
    assert "does not" in hrc["reference"].lower()
    assert hrc["not_completed_count"] >= 1


def test_memo_rich_is_citation_bound_and_safety_clean() -> None:
    memo = _build_research_memo(
        _rich_report_content(), _rich_council(), source_tier="T1_primary_filing"
    )
    # Citation-bound: every claim-bearing block carries a provenance/citation.
    assert memo["company_identity"]["legal_name"]["provenance"]
    assert memo["what_is_sourced"]["available_fields"]["provenance"] == "sourced_fact"
    assert memo["what_is_missing"]["missing_data_fields"]["provenance"] == "missing_data"
    # Safety-clean everywhere except the exempt disallowed_outputs field.
    _assert_memo_safe(memo)


def test_memo_forbidden_tokens_only_in_disallowed_outputs() -> None:
    memo = _build_research_memo(
        _rich_report_content(), _rich_council(), source_tier="T1_primary_filing"
    )
    # The forbidden vocabulary lives ONLY in the exempt notice.
    hits = safety_terms.scan_value(memo["disallowed_outputs"])
    hit_terms = safety_terms.hit_terms(hits)
    for token in _FORBIDDEN_TOKENS:
        assert token in hit_terms
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase in hit_terms
    # And nowhere else — scanning the whole memo with the same exemption is clean.
    from app.services.final_report_generator import _EXEMPT_FIELD_NAMES

    assert (
        safety_terms.scan_value(
            memo, path="research_memo", exempt_keys=_EXEMPT_FIELD_NAMES
        )
        == []
    )


def test_memo_does_not_mutate_report_content() -> None:
    content = _rich_report_content()
    before = json.dumps(content, sort_keys=True, default=str)
    _build_research_memo(content, _rich_council(), source_tier="T1_primary_filing")
    after = json.dumps(content, sort_keys=True, default=str)
    assert before == after


# ---------------------------------------------------------------------------
# Direct unit tests — thin content degrades honestly
# ---------------------------------------------------------------------------


def test_memo_thin_content_degrades_honestly() -> None:
    memo = _build_research_memo(
        _thin_report_content(), CouncilResult.disabled(), source_tier="T5_api_aggregator"
    )
    # what_is_missing is prominent and carries the blocked-extraction gaps.
    missing = memo["what_is_missing"]
    assert missing["prominent"] is True
    assert missing["missing_data_fields"]["value"]
    assert missing["human_review_required"] is True

    # Primary evidence is honest-empty — no fabricated facts.
    pes = memo["primary_evidence_summary"]
    assert pes["report_primary_fact_count"] == 0
    assert pes["primary_document_count"] == 0
    note = pes["note"]["value"].lower()
    assert "0 primary facts" in note
    assert "ocr" in note or "scanned" in note
    assert pes["note"]["provenance"] == "missing_data"

    # Council did not run — honest-empty dissent surface.
    dissent = memo["council_disagreement_red_team"]
    assert dissent["council_ran"] is False
    assert "did not run" in dissent["note"]["value"].lower()

    # Business analysis is honest-empty (Phase 9 agents did not run).
    brs = memo["business_risk_summary"]
    assert brs["bull_available"] is False
    assert brs["bear_available"] is False
    assert brs["risk_available"] is False
    assert brs["bull_points"]["value"] == []

    # Financial facts summary carries no derived valuation and no T1 facts.
    assert memo["financial_facts_summary"]["t1_primary_filing_facts"]["value"] == {}

    assert memo["human_review_required"] is True
    _assert_memo_safe(memo)


def test_memo_thin_forbidden_tokens_only_in_disallowed_outputs() -> None:
    memo = _build_research_memo(
        _thin_report_content(), CouncilResult.disabled(), source_tier="T5_api_aggregator"
    )
    hit_terms = safety_terms.hit_terms(
        safety_terms.scan_value(memo["disallowed_outputs"])
    )
    assert "BUY" in hit_terms and "SELL" in hit_terms


# ---------------------------------------------------------------------------
# Full-pipeline tests — flag ON / OFF via _generate_and_save
# ---------------------------------------------------------------------------


def _pipeline_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "company_identity": {
            "ticker": "CFR",
            "legal_name": "Compagnie Financiere Richemont SA",
            "exchange": "SWX",
            "country_domicile": "Switzerland",
        },
        "profile": {"sector": "Consumer Cyclical", "industry": "Luxury Goods"},
    }


async def _generate(mock_db: Any, snapshot: dict[str, Any]) -> Any:
    service = FinalReportGeneratorService()
    return await service._generate_and_save(
        db=mock_db,
        scorecard=None,
        candidate=None,
        source_report=None,
        company_record=None,
        citations=[],
        sources=[],
        state={"company_snapshot": snapshot, "catalyst_discovery": None},
    )


def _captured_report_content(mock_db: Any) -> dict[str, Any]:
    assert mock_db.add.called, "expected a report to be saved"
    report = mock_db.add.call_args[0][0]
    content: dict[str, Any] = {}
    pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    for match in pattern.finditer(report.content_markdown or ""):
        block = json.loads(match.group(1))
        if isinstance(block, dict):
            content.update(block)
    return content


@pytest.fixture
def enable_council_and_memo(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    monkeypatch.setattr(config.settings, "source_research_memo_enabled", True)
    yield


@pytest.fixture
def enable_council(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    yield


async def test_full_report_has_research_memo_when_enabled(
    mock_db, enable_council_and_memo
) -> None:
    resp = await _generate(mock_db, _pipeline_snapshot())
    # Additive block never weakens the report invariants.
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    memo = content.get("research_memo")
    assert memo is not None, "memo-on report must carry a research_memo block"
    assert memo["type"] == "research_memo"
    assert memo["human_review_required"] is True
    # The whole stored report (incl. the memo) passes the report safety gate.
    assert run_safety_gate(content).passed is True


async def test_full_report_no_memo_and_additive_when_disabled(
    mock_db, enable_council, monkeypatch
) -> None:
    """Flag off → no research_memo key; the rest is byte-for-byte identical."""
    from app.services import final_report_generator as frg

    fixed = datetime(2026, 7, 29, 0, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(frg, "_utcnow", lambda: fixed)

    # Flag OFF (default) — capture the legacy content.
    monkeypatch.setattr(frg.settings, "source_research_memo_enabled", False)
    resp_off = await _generate(mock_db, _pipeline_snapshot())
    content_off = _captured_report_content(mock_db)
    assert "research_memo" not in content_off
    assert resp_off.schema_valid is True
    assert resp_off.safety_valid is True

    # Flag ON — the memo is the ONLY difference.
    monkeypatch.setattr(frg.settings, "source_research_memo_enabled", True)
    await _generate(mock_db, _pipeline_snapshot())
    content_on = _captured_report_content(mock_db)
    assert "research_memo" in content_on

    stripped = {k: v for k, v in content_on.items() if k != "research_memo"}
    assert stripped == content_off
