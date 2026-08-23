"""
Phase C2 — the canonical assessments are actually RENDERED.

Phase C built ``SourceQualityAssessment`` and ``ThinEvidenceAssessment`` and
tested them in isolation. They were not wired into the report, so a human still
saw each section's private quality answer and a thin company still got the full
skeleton. These tests assert the WIRING: the report sections exist, carry the
canonical values, and a thin company selects the short form.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from app.services.canonical_evidence import resolve_fundamentals
from app.services.final_report_generator import (
    _build_evidence_quality,
    _build_thin_evidence_state,
    _catalyst_counts,
)


def _rich_us_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "company_identity": {
            "legal_name": "Testco US Corp",
            "ticker": "TSTC",
            "exchange": "NASDAQ",
            "isin": "US0000000001",
        },
        "provider_metadata": {
            "provider_name": "sec_edgar",
            "source_tier": "T2_regulator_or_gov",
        },
        "price_history_summary": {
            "available": True,
            "latest_close": 214.72,
            "currency": "USD",
            "data_points_count": 251,
            "provider_name": "eodhd_price_only",
            "source_tier": "T5_api_aggregator",
        },
        "fundamentals_summary": {
            "revenue_usd_m": 215938.0,
            "net_income_usd_m": 120067.0,
            "form_type": "10-K",
            "fiscal_year": 2026,
            "source": "sec_edgar_xbrl",
            "source_tier": "T2_regulator_or_gov",
        },
    }


def _rich_eu_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "company_identity": {
            "legal_name": "Testco Europa AG",
            "ticker": "TEUR",
            "exchange": "SIX",
            "isin": "CH0000000001",
        },
        "provider_metadata": {
            "provider_name": "issuer_ir",
            "source_tier": "T1_primary_filing",
        },
        "price_history_summary": {
            "available": True,
            "latest_close": 132.4,
            "currency": "CHF",
            "data_points_count": 250,
            "provider_name": "eodhd_price_only",
            "source_tier": "T5_api_aggregator",
        },
        "fundamentals_summary": {
            "revenue_usd_m": 22420.0,
            "operating_income_usd_m": 4492.0,
            "fiscal_year": 2026,
            "source": "issuer_annual_report",
            "source_tier": "T1_primary_filing",
        },
    }


def _thin_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "company_identity": {
            "legal_name": "Testco Thin AS",
            "ticker": "THIN",
            "exchange": "CPH",
        },
        "provider_metadata": {
            "provider_name": "eodhd",
            "source_tier": "T5_api_aggregator",
        },
        "price_history_summary": {
            "available": True,
            "latest_close": 91.5,
            "currency": "DKK",
            "data_points_count": 250,
            "provider_name": "eodhd_price_only",
            "source_tier": "T5_api_aggregator",
        },
    }


def _quality(
    snapshot: dict[str, Any],
    catalysts: dict[str, Any] | None = None,
    available_fields: list[str] | None = None,
):
    return _build_evidence_quality(
        company_snapshot=snapshot,
        financial_data_summary={
            "available_fields": (
                available_fields
                if available_fields is not None
                else ["financials.revenue"]
            )
        },
        canonical_fundamentals=resolve_fundamentals(snapshot, {}),
        catalyst_discovery=catalysts,
    )


def _thin_state(snapshot: dict[str, Any], *, primary_facts=None, catalysts=None):
    return _build_thin_evidence_state(
        company_snapshot=snapshot,
        financial_data_summary={"available_fields": ["identity.ticker"]},
        canonical_fundamentals=resolve_fundamentals(snapshot, {}),
        catalyst_discovery=catalysts,
        primary_facts=primary_facts,
        primary_source_references=[{"url": "https://example.invalid/investors"}],
    )


# ===========================================================================
# A. The canonical assessment is what the report renders
# ===========================================================================
def test_evidence_quality_section_exposes_all_four_dimensions() -> None:
    section = _quality(
        _rich_us_snapshot(),
        {"filing_event_count": 4, "press_release_event_count": 16, "news_event_count": 3},
    )
    assert section["type"] == "evidence_quality"
    for key in (
        "identity_quality",
        "financial_evidence_quality",
        "catalyst_evidence_quality",
        "overall_research_evidence_quality",
    ):
        assert key in section, f"{key} must be rendered"
        assert section[key]["label"]
        assert section[key]["basis"], "a label must never be rendered bare"


def test_rich_us_company_renders_strong_financial_evidence() -> None:
    section = _quality(_rich_us_snapshot(), {"filing_event_count": 4, "press_release_event_count": 16})
    assert section["financial_evidence_quality"]["label"] == "strong"
    assert any(
        "regulator" in b.lower() for b in section["financial_evidence_quality"]["basis"]
    )


def test_rich_eu_issuer_facts_are_strong_without_sec_framing() -> None:
    section = _quality(_rich_eu_snapshot(), {"press_release_event_count": 5})
    assert section["financial_evidence_quality"]["label"] == "strong"
    blob = json.dumps(section).lower()
    assert "sec" not in blob, "a Swiss issuer's quality must not be framed via SEC"


def test_thin_company_quality_is_insufficient_not_contradictory() -> None:
    # A genuinely thin company lists no financial statement fields at all.
    section = _quality(_thin_snapshot(), {}, available_fields=["identity.ticker"])
    assert section["financial_evidence_quality"]["label"] == "insufficient"
    # Overall takes the WEAKEST dimension, so it cannot claim more than the
    # financial evidence supports.
    assert section["overall_research_evidence_quality"]["label"] == "insufficient"


def test_every_rendered_quality_label_comes_from_one_assessment() -> None:
    """No section may independently turn the same evidence into another label."""
    snapshot = _rich_us_snapshot()
    catalysts = {"filing_event_count": 4, "press_release_event_count": 16}
    a = _quality(snapshot, catalysts)
    b = _quality(snapshot, catalysts)
    assert a == b
    labels = {
        a[k]["label"]
        for k in (
            "identity_quality",
            "financial_evidence_quality",
            "catalyst_evidence_quality",
        )
    }
    # Dimensions may legitimately differ from each other; the point is that the
    # OVERALL label is derived from them, never invented separately.
    assert a["overall_research_evidence_quality"]["label"] in labels


# ===========================================================================
# B. Thin state selects the short form
# ===========================================================================
def test_thin_company_is_flagged_with_grouped_missing_evidence() -> None:
    section = _thin_state(_thin_snapshot())
    assert section["type"] == "thin_evidence_state"
    assert section["is_thin"] is True
    groups = section["missing_evidence_groups"]
    categories = [g["category"] for g in groups]
    assert "Financial statements" in categories
    assert "Primary issuer document" in categories
    assert "Catalysts" in categories
    # Grouped ONCE per category — never the same gap repeated per section.
    assert len(categories) == len(set(categories))
    # What IS known is retained so the short form can lead with it.
    assert section["has_price"] is True
    assert section["known_source_locations"]


def test_rich_company_is_not_thin_and_lists_no_missing_groups() -> None:
    section = _thin_state(
        _rich_us_snapshot(), catalysts={"filing_event_count": 4}
    )
    assert section["is_thin"] is False
    assert section["missing_evidence_groups"] == []


def test_borderline_company_with_primary_facts_stays_full_template() -> None:
    """Do not over-trigger: extracted issuer facts are real evidence."""
    section = _thin_state(
        _thin_snapshot(), primary_facts=[{"label": "revenue"}, {"label": "net_income"}]
    )
    assert section["is_thin"] is False


def test_catalysts_alone_prevent_thin_mode() -> None:
    section = _thin_state(_thin_snapshot(), catalysts={"press_release_event_count": 6})
    assert section["is_thin"] is False


def test_thin_trigger_ignores_company_identity() -> None:
    """Deterministic and company-agnostic — no ticker or region logic."""
    for ticker in ("PNDORA", "AAAA", "ZZZZ"):
        snapshot = _thin_snapshot()
        snapshot["company_identity"]["ticker"] = ticker
        assert _thin_state(snapshot)["is_thin"] is True


def test_catalyst_counts_tolerate_missing_and_list_shapes() -> None:
    assert _catalyst_counts(None)["issuer_press_count"] == 0
    assert _catalyst_counts({"press_release_event_count": 3})["issuer_press_count"] == 3
    # A list-valued field counts its entries rather than failing.
    assert _catalyst_counts({"press_release_event_count": [1, 2]})["issuer_press_count"] == 2


# ===========================================================================
# C. The web renderer actually switches template
# ===========================================================================
def _array_literal(src: str, name: str) -> str:
    """The array literal declared for ``name``.

    Anchored on ``= [`` because the TypeScript annotation (``: string[]``)
    also contains a bracket.
    """
    tail = src.split(name, 1)[1]
    start = tail.index("= [") + 2
    return tail[start : tail.index("]", start)]


def _web(*parts: str) -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2].joinpath("web", *parts)


def test_web_defines_a_short_form_order_that_omits_analysis_sections() -> None:
    src = _web("src", "components", "reports", "finalReportContent.ts").read_text(
        encoding="utf-8"
    )
    assert "THIN_SECTION_ORDER" in src
    assert "isThinEvidenceReport" in src
    thin_block = _array_literal(src, "THIN_SECTION_ORDER")
    # The short form must NOT render analysis sections that have no evidence.
    for omitted in ("bull_case", "bear_case", "risk_analysis", "valuation_readiness"):
        assert omitted not in thin_block, f"{omitted} must be omitted when thin"
    # It must still show what is known and what is missing.
    for present in ("thin_evidence_state", "company_identity", "evidence_quality"):
        assert present in thin_block


def test_web_renderer_selects_order_from_backend_judgement() -> None:
    src = _web("src", "components", "reports", "FinalReportRenderer.tsx").read_text(
        encoding="utf-8"
    )
    assert "sectionOrderFor(" in src
    assert "sectionOrder.map(" in src, "the loop must walk the SELECTED order"
    assert "isThinEvidenceReport(" in src


def test_web_full_order_includes_the_canonical_quality_section() -> None:
    src = _web("src", "components", "reports", "finalReportContent.ts").read_text(
        encoding="utf-8"
    )
    full_block = _array_literal(src, "export const SECTION_ORDER")
    assert '"evidence_quality"' in full_block
    assert 'evidence_quality: "Evidence Quality"' in src


# ===========================================================================
# D. No research-semantics change
# ===========================================================================
def test_rendering_does_not_mutate_the_snapshot() -> None:
    snapshot = _rich_us_snapshot()
    before = json.dumps(snapshot, sort_keys=True)
    _quality(snapshot, {"filing_event_count": 2})
    _thin_state(snapshot)
    assert json.dumps(snapshot, sort_keys=True) == before


# ===========================================================================
# E. Regressions caught by LIVE acceptance (both were alias/semantic drift)
# ===========================================================================
def test_catalyst_counts_use_the_real_CatalystSummary_key_names() -> None:
    """Live: an earlier guess at these key names silently yielded zero.

    That rendered "catalyst evidence: insufficient" for a company holding real
    SEC filings and issuer press — the same alias-drift class the campaign
    exists to eliminate, reintroduced in a presentation layer. Pinned against
    the actual schema so a rename breaks CI here.
    """
    from app.schemas.catalyst import CatalystSummary

    fields = CatalystSummary.model_fields
    for name in ("filing_event_count", "press_release_event_count", "news_event_count"):
        assert name in fields, f"CatalystSummary no longer has {name}"

    counts = _catalyst_counts(
        {"filing_event_count": 4, "press_release_event_count": 16, "news_event_count": 3}
    )
    assert counts["regulator_filing_count"] == 4
    assert counts["issuer_press_count"] == 16
    assert counts["independent_news_count"] == 3


def test_catalyst_counts_read_a_nested_summary_block() -> None:
    counts = _catalyst_counts({"summary": {"filing_event_count": 2}})
    assert counts["regulator_filing_count"] == 2


def test_issuer_primary_facts_alone_make_financial_evidence_strong() -> None:
    """Live: CFR reported "insufficient" while citing validated T1 facts.

    Its statement figures come from an extracted primary document rather than
    resolved fundamentals. Requiring fundamentals as well repeats the
    source-METHOD-vs-fact-ABSENCE conflation.
    """
    snapshot = _thin_snapshot()  # no fundamentals_summary at all
    section = _build_evidence_quality(
        company_snapshot=snapshot,
        financial_data_summary={"available_fields": ["identity.ticker"]},
        canonical_fundamentals=resolve_fundamentals(snapshot, {}),
        catalyst_discovery={"filing_event_count": 1},
        primary_facts=[{"label": "revenue"}, {"label": "operating_profit"}],
    )
    assert section["financial_evidence_quality"]["label"] == "strong"
    assert any(
        "primary-document" in b
        for b in section["financial_evidence_quality"]["basis"]
    )


def test_company_with_filings_and_press_is_not_catalyst_insufficient() -> None:
    section = _build_evidence_quality(
        company_snapshot=_rich_us_snapshot(),
        financial_data_summary={"available_fields": ["financials.revenue"]},
        canonical_fundamentals=resolve_fundamentals(_rich_us_snapshot(), {}),
        catalyst_discovery={"filing_event_count": 4, "press_release_event_count": 16},
    )
    assert section["catalyst_evidence_quality"]["label"] != "insufficient"
    assert section["overall_research_evidence_quality"]["label"] != "insufficient"
