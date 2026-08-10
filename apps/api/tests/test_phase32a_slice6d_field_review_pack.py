"""
Phase 32A Slice 6D — Deep Field Review evidence pack.

Field-mapping tests over ``app.services.field_review_evidence_pack``: every
company-summary field must map to the RIGHT already-persisted source, and an
absent source must render as not-available rather than being guessed, defaulted,
or borrowed from another company.

No network, no credentials, no LLM. Plain ORM objects only.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.report import Report
from app.schemas.primary_document import PrimaryDocumentSummary
from app.services import safety_terms
from app.services.field_review_evidence_pack import (
    MAX_LIST_ITEMS,
    build_company_summary,
    build_field_review_pack,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _content_markdown(sections: dict[str, Any]) -> str:
    """Wrap report sections exactly the way the final-report writer does."""
    return "\n".join(
        [
            "# INTERNAL ADMIN DRAFT — FINAL REPORT",
            "",
            "```json",
            json.dumps(sections, indent=2, default=str),
            "```",
        ]
    )


def _full_sections() -> dict[str, Any]:
    return {
        "company_identity": {
            "type": "company_identity",
            "legal_name": {"value": "Sourced Legal Name"},
            "ticker": {"value": "SRC"},
            "exchange": {"value": "US"},
            "country_domicile": {"value": "United States"},
            "sector": {"value": "Technology"},
        },
        "financial_snapshot": {
            "type": "financial_snapshot",
            "data_provenance": "real",
            "latest_close": {
                "value": 123.45,
                "currency": "USD",
                "as_of": "2026-06-30",
                "provenance": "sourced_fact",
                "source": "provider_price_history",
            },
            "revenue_ttm_usd_m": {
                "value": 9000,
                "unit": "USD_m",
                "provenance": "sourced_fact",
                "source": "eodhd_fundamentals",
                "source_tier": "T5_api_aggregator",
            },
            "ebitda_ttm_usd_m": {"value": None, "provenance": "missing_data"},
            "fundamentals_note": {
                "value": "Fundamentals from EODHD (T5 aggregator).",
                "provenance": "assumption",
            },
        },
        "bull_case": {
            "type": "bull_case",
            "available": True,
            "positive_thesis_points": {
                "value": ["Durable brand", "Recurring revenue mix"],
                "provenance": "model_interpretation",
            },
        },
        "risk_analysis": {
            "type": "risk_analysis",
            "available": True,
            "business_risks": {"value": ["Customer concentration"]},
            "financial_risks": {"value": ["Debt maturity wall"]},
            "data_quality_risks": {"value": ["Fundamentals are T5 only"]},
        },
        "valuation_readiness": {
            "type": "valuation_readiness",
            "readiness": {"value": "not_ready", "provenance": "sourced_fact"},
            "disallowed_outputs": {
                "value": ["price target", "fair value", "upside percentage"]
            },
        },
        "source_quality_review": {
            "type": "source_quality_review",
            "total_sources": 7,
            "overall_source_quality": {"value": "B_moderate"},
            "strong_sources_count": 3,
            "weak_sources_count": 4,
            "source_type_distribution": {"value": {"sec_filing": 3, "news": 4}},
        },
        "research_completeness_review": {
            "type": "research_completeness_review",
            "available": True,
            "complete_sections": {"value": ["identity", "financials"]},
            "incomplete_sections": {"value": ["governance"]},
            "blocking_gaps_count": 1,
        },
        "missing_information": {
            "type": "missing_information",
            "missing_items": [{"item": "Board composition not sourced"}],
        },
        "committee_chair_summary": {
            "type": "committee_chair_summary",
            "available": True,
            "provisional_internal_status": {"value": "requires_more_evidence"},
            "quality_gate_status": {"value": "passed_with_gaps"},
            "committee_summary": {"value": "Deterministic chair summary."},
            "primary_open_questions": {"value": ["What drives margin mix?"]},
        },
        "news_catalyst_discovery": {
            "type": "news_catalyst_discovery",
            "coverage_status": "partial",
            "events": [
                {
                    "event_date": "2026-05-01",
                    "catalyst_category": "earnings",
                    "catalyst_direction": "positive",
                    "headline": "Quarterly results published",
                }
            ],
        },
    }


def _source_summary(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "total_sources": 7,
        "total_citations": 12,
        "source_types": ["sec_filing", "news"],
        "data_provenance": "real",
        "llm_council": {
            "llm_used": True,
            "agents_completed": 8,
            "agents_failed": 0,
            "agents_skipped": 0,
            "committee_label": "requires_more_evidence",
            "agents": [
                {
                    "agent_name": "financial_analyst",
                    "status": "completed",
                    "summary": "Stored financial analyst summary.",
                },
                {
                    "agent_name": "source_quality_critic",
                    "status": "completed",
                    "summary": "Stored source critic summary.",
                },
                {
                    "agent_name": "red_team",
                    "status": "completed",
                    "summary": "Stored red team summary.",
                },
                {
                    "agent_name": "committee_chair",
                    "status": "completed",
                    "summary": "Stored chair summary.",
                    "committee_label": "requires_more_evidence",
                },
            ],
        },
    }
    base.update(over)
    return base


def _report(
    *,
    sections: dict[str, Any] | None = None,
    source_summary: dict[str, Any] | None = None,
) -> Report:
    return Report(
        id=uuid.uuid4(),
        title="Final report",
        slug=f"final-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        final_report_version="1.0.0",
        content_markdown=_content_markdown(
            sections if sections is not None else _full_sections()
        ),
        schema_validation_json={"schema_valid": True},
        source_summary_json=(
            source_summary if source_summary is not None else _source_summary()
        ),
    )


def _candidate(**over: Any) -> DiscoveryCandidate:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "discovery_run_id": uuid.uuid4(),
        "ticker": "SRC",
        "exchange": "US",
        "company_name": "Sourced Co",
        "country": "United States",
        "sector": "Technology",
        "industry": "Software",
        "rank": 1,
        "candidate_score": 71.5,
        "candidate_score_grade": "high_internal_interest",
        "thesis_relevance_score": 0.82,
        "combined_internal_score": 74.0,
        "labels_json": ["internal_research_candidate", "fundamentals_available"],
        "source_quality": "B_moderate",
        "catalyst_coverage_status": "partial",
    }
    base.update(over)
    return DiscoveryCandidate(**base)


def _docs(**over: Any) -> PrimaryDocumentSummary:
    base: dict[str, Any] = {
        "discovered_count": 3,
        "attempted_count": 3,
        "extracted_count": 2,
        "metadata_only_count": 1,
        "failed_count": 0,
        "native_count": 2,
        "ocr_count": 0,
        "validated_fact_count": 5,
        "reused_count": 1,
        "evidence_reference_count": 1,
    }
    base.update(over)
    return PrimaryDocumentSummary(**base)


def _run(**over: Any) -> DiscoveryRun:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": "completed",
        "mode": "thesis",
        "candidate_count": 4,
        "thesis_text": "European luxury watch makers",
        "parsed_thesis_json": {"theme": "luxury_watches"},
        "config_json": {"region": "Europe"},
    }
    base.update(over)
    return DiscoveryRun(**base)


# ---------------------------------------------------------------------------
# Field mapping — every value comes from a real persisted source
# ---------------------------------------------------------------------------


def test_identity_prefers_candidate_row_then_report_section() -> None:
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(),
        document_summary=_docs(),
    )
    assert summary.id == "F1"
    # Candidate row wins for identity (it is the run's own resolution).
    assert summary.ticker == "SRC"
    assert summary.company_name == "Sourced Co"
    assert summary.industry == "Software"


def test_identity_falls_back_to_report_when_candidate_field_absent() -> None:
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(company_name=None, legal_name=None, country=None),
        report=_report(),
        document_summary=_docs(),
    )
    assert summary.company_name == "Sourced Legal Name"
    assert summary.country == "United States"


def test_discovery_relevance_is_read_verbatim_never_recomputed() -> None:
    candidate = _candidate()
    summary = build_company_summary(
        citation_ref="F1", candidate=candidate, report=_report()
    )
    assert summary.discovery.rank == candidate.rank
    assert summary.discovery.candidate_score == candidate.candidate_score
    assert summary.discovery.thesis_relevance_score == 0.82
    assert summary.discovery.combined_internal_score == 74.0
    assert summary.discovery.candidate_score_grade == "high_internal_interest"
    assert "internal_research_candidate" in summary.discovery.labels


def test_financial_facts_carry_their_own_provenance_and_units() -> None:
    summary = build_company_summary(
        citation_ref="F1", candidate=_candidate(), report=_report()
    )
    by_field = {f.field: f for f in summary.financial_facts}
    assert by_field["latest_close"].value == "123.45"
    assert by_field["latest_close"].unit == "USD"
    assert by_field["latest_close"].as_of == "2026-06-30"
    assert by_field["latest_close"].source == "provider_price_history"
    assert by_field["revenue_ttm_usd_m"].source_tier == "T5_api_aggregator"


def test_a_null_financial_datapoint_becomes_a_missing_field_not_a_number() -> None:
    summary = build_company_summary(
        citation_ref="F1", candidate=_candidate(), report=_report()
    )
    fields = {f.field for f in summary.financial_facts}
    assert "ebitda_ttm_usd_m" not in fields
    assert "ebitda_ttm_usd_m" in summary.missing_financial_fields


def test_valuation_readiness_carries_only_the_qualitative_label() -> None:
    summary = build_company_summary(
        citation_ref="F1", candidate=_candidate(), report=_report()
    )
    assert summary.valuation_readiness == "not_ready"
    # The report's `disallowed_outputs` notice (which necessarily NAMES the
    # forbidden phrases) must never be carried into the field pack.
    dumped = json.dumps(summary.model_dump(mode="json"))
    assert "price target" not in dumped
    assert "fair value" not in dumped


def test_document_coverage_comes_from_the_persisted_view_service() -> None:
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(),
        document_summary=_docs(extracted_count=2, ocr_count=1, native_count=1),
    )
    assert summary.primary_documents.attempted_count == 3
    assert summary.primary_documents.extracted_count == 2
    assert summary.primary_documents.ocr_count == 1
    assert summary.primary_documents.validated_fact_count == 5


def test_absent_document_summary_yields_honest_zeros_not_borrowed_counts() -> None:
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(),
        document_summary=None,
    )
    assert summary.primary_documents.attempted_count == 0
    assert summary.primary_documents.extracted_count == 0
    assert summary.primary_documents.validated_fact_count == 0


def test_company_council_agent_summaries_are_read_only_and_mapped() -> None:
    summary = build_company_summary(
        citation_ref="F1", candidate=_candidate(), report=_report()
    )
    assert summary.financial_analyst_summary == "Stored financial analyst summary."
    assert summary.source_critic_summary == "Stored source critic summary."
    assert summary.red_team_summary == "Stored red team summary."
    assert summary.company_council_verdict.committee_label == "requires_more_evidence"
    assert (
        summary.company_council_verdict.provisional_internal_status
        == "requires_more_evidence"
    )
    assert summary.company_council_verdict.primary_open_questions == [
        "What drives margin mix?"
    ]


def test_a_failed_company_council_agent_yields_no_summary_not_a_guess() -> None:
    source_summary = _source_summary()
    source_summary["llm_council"]["agents"][0] = {
        "agent_name": "financial_analyst",
        "status": "failed",
        "summary": "[Agent did not complete: provider error or timeout.]",
    }
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(source_summary=source_summary),
    )
    assert summary.financial_analyst_summary is None


def test_council_completion_counts_and_caveats_are_honest() -> None:
    source_summary = _source_summary()
    source_summary["llm_council"]["agents_failed"] = 2
    source_summary["llm_council"]["chair_fallback_used"] = True
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(source_summary=source_summary),
    )
    assert summary.council_completion.agents_failed == 2
    assert summary.council_completion.chair_fallback_used is True
    assert any("2 agent(s) failed" in c for c in summary.caveats)
    assert any("deterministic fallback" in c for c in summary.caveats)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"data_provenance": "real"}, "real"),
        ({"data_provenance": "mock"}, "mock"),
        ({"data_provenance": "mixed"}, "mixed"),
        ({"is_mock": True}, "mock"),
        ({"is_mock": False}, "real"),
        # Absent signal is UNKNOWN — never silently upgraded to "real".
        ({}, "unknown"),
    ],
)
def test_data_provenance_is_read_never_inferred(
    stored: dict[str, Any], expected: str
) -> None:
    source_summary = _source_summary()
    source_summary.pop("data_provenance")
    source_summary.update(stored)
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(source_summary=source_summary),
    )
    assert summary.data_provenance == expected
    if expected != "real":
        assert f"data_provenance={expected}" in summary.caveats


def test_an_empty_report_body_renders_as_not_available_never_fabricated() -> None:
    """A final report with NO parsable sections must produce empty fields."""
    summary = build_company_summary(
        citation_ref="F1",
        candidate=_candidate(),
        report=_report(sections={}, source_summary={}),
    )
    assert summary.financial_facts == []
    assert summary.business_moat_notes == []
    assert summary.risk_notes == []
    assert summary.catalyst_notes == []
    assert summary.valuation_readiness is None
    assert summary.financial_analyst_summary is None
    assert summary.company_council_verdict.committee_label is None
    assert summary.research_completeness_blocking_gaps is None
    assert summary.data_provenance == "unknown"
    # ...and the honest caveats say so.
    assert "data_provenance=unknown" in summary.caveats
    assert any("no sourced financial datapoint" in c for c in summary.caveats)


def test_every_list_sub_field_is_capped() -> None:
    sections = _full_sections()
    sections["risk_analysis"]["business_risks"] = {
        "value": [f"risk number {i}" for i in range(50)]
    }
    sections["bull_case"]["positive_thesis_points"] = {
        "value": [f"point number {i}" for i in range(50)]
    }
    sections["news_catalyst_discovery"]["events"] = [
        {"event_date": "2026-01-01", "headline": f"headline {i}"} for i in range(50)
    ]
    summary = build_company_summary(
        citation_ref="F1", candidate=_candidate(), report=_report(sections=sections)
    )
    assert len(summary.risk_notes) <= MAX_LIST_ITEMS
    assert len(summary.business_moat_notes) <= MAX_LIST_ITEMS
    assert len(summary.catalyst_notes) <= MAX_LIST_ITEMS
    assert len(summary.financial_facts) <= MAX_LIST_ITEMS


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------


def test_pack_assigns_stable_citation_ids_and_run_context() -> None:
    run = _run()
    companies = [
        build_company_summary(
            citation_ref=f"F{i}", candidate=_candidate(ticker=f"T{i}"), report=_report()
        )
        for i in (1, 2)
    ]
    pack = build_field_review_pack(
        run=run, companies=companies, missing=[], analyzed_candidate_count=2
    )
    assert pack.company_ids() == {"F1", "F2"}
    assert "R1" in pack.evidence_ids()
    assert pack.run.discovery_run_id == str(run.id)
    assert pack.run.parsed_theme == "luxury_watches"
    assert pack.run.region == "Europe"
    assert pack.run.included_company_count == 2
    assert pack.company_by_id("F2") is companies[1]


def test_excluded_candidates_become_citeable_run_facts_never_dropped() -> None:
    pack = build_field_review_pack(
        run=_run(),
        companies=[
            build_company_summary(
                citation_ref="F1", candidate=_candidate(), report=_report()
            )
        ],
        missing=[
            {"ticker": "NOPE", "exclusion_reason": "no_analysis_run"},
            {"ticker": "DRAFT", "exclusion_reason": "draft_only"},
        ],
        analyzed_candidate_count=2,
    )
    details = " ".join(f.detail or "" for f in pack.run_facts)
    assert "NOPE" in details and "no_analysis_run" in details
    assert "DRAFT" in details and "draft_only" in details
    assert pack.run.missing_candidate_count == 2
    assert any("could not be compared" in g for g in pack.known_gaps)


def test_pack_surfaces_provenance_and_document_gaps_honestly() -> None:
    source_summary = _source_summary(data_provenance="mock")
    companies = [
        build_company_summary(
            citation_ref="F1",
            candidate=_candidate(),
            report=_report(source_summary=source_summary),
            document_summary=_docs(extracted_count=0),
        )
    ]
    pack = build_field_review_pack(
        run=_run(), companies=companies, missing=[], analyzed_candidate_count=1
    )
    gaps = " ".join(pack.known_gaps)
    assert "not 'real'" in gaps and "F1" in gaps
    assert "No primary document was successfully extracted" in gaps


def test_pack_body_is_free_of_forbidden_language_outside_do_not_infer() -> None:
    """The pack must be safety-clean; ``do_not_infer`` necessarily names the
    forbidden phrases (it enumerates what must NOT be produced) and is the ONLY
    exempt key, exactly as the discovery council does."""
    pack = build_field_review_pack(
        run=_run(),
        companies=[
            build_company_summary(
                citation_ref="F1", candidate=_candidate(), report=_report()
            )
        ],
        missing=[{"ticker": "NOPE", "exclusion_reason": "no_analysis_run"}],
        analyzed_candidate_count=1,
    )
    hits = safety_terms.scan_value(
        pack.model_dump(mode="json"), exempt_keys=frozenset({"do_not_infer"})
    )
    assert hits == [], [h.term for h in hits]
