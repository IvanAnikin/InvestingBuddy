"""
Phase B — typed evidence contracts + REAL producer→serialize→consumer fixtures.

WHY THESE TESTS LOOK DIFFERENT FROM THE ONES THEY REPLACE
=========================================================
The ``available_count=0`` defect shipped with full green tests. Producer tests
built their own dict; consumer tests built a DIFFERENT dict with the reader's
key names. Both passed. Neither ever ran the real producer's output through a
real consumer, so the rename between them was invisible.

Every fixture below therefore runs:

    REAL PRODUCER -> serialisation boundary -> JSON round trip -> REAL CONSUMER

and asserts on what the CONSUMER actually sees. No hand-built intermediate
dicts for the decision-critical path. If someone renames a producer field
without updating the contract, these fail at the boundary rather than in
production.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.analysis_council.valuation_guard_agent import (
    run_valuation_guard_agent,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.schemas.evidence_state import (
    EVIDENCE_STATE_SCHEMA_VERSION,
    EvidenceInventory,
    FieldProvenance,
    FinancialDataSummary,
    FundamentalsResolution,
    PriceSummary,
)
from app.services.canonical_evidence import (
    resolve_fundamentals,
    resolve_price_provenance,
)
from app.services.final_report_generator import _build_data_availability_summary
from app.services.scoring_engine import ScoringEngine


# ===========================================================================
# Synthetic companies. Generic by design — no real issuer is required to
# exercise a contract, and issuer-specific fixtures rot.
# ===========================================================================
def _us_structured_filing_company() -> dict[str, Any]:
    """A: rich US company — SEC/XBRL fundamentals + a SEPARATE price feed."""
    return {
        "is_mock": False,
        "company_identity": {
            "legal_name": "Testco US Corp",
            "ticker": "TSTC",
            "exchange": "NASDAQ",
            "country_domicile": "US",
            "isin": "US0000000001",
        },
        "profile": {"sector": "Technology", "industry": "Software"},
        # Company-level provider is the REGULATOR ...
        "provider_metadata": {
            "provider_name": "sec_edgar",
            "source_tier": "T2_regulator_or_gov",
        },
        # ... while the price feed is a DIFFERENT provider. The historical bug
        # labelled this price as coming from sec_edgar.
        "price_history_summary": {
            "available": True,
            "latest_close": 214.72,
            "currency": "USD",
            "data_points_count": 251,
            "provider_name": "eodhd_price_only",
            "source_tier": "T5_api_aggregator",
            "date_range": {"start": "2025-08-01", "end": "2026-08-01"},
        },
        "fundamentals_summary": {
            "revenue_usd_m": 215938.0,
            "net_income_usd_m": 120067.0,
            "operating_cash_flow_usd_m": 102718.0,
            "total_assets_usd_m": 206800.0,
            "form_type": "10-K",
            "fiscal_year": 2026,
            "fiscal_period": "FY",
            "source": "sec_edgar_xbrl",
            "source_tier": "T2_regulator_or_gov",
            "data_quality": "A_verified",
        },
        "missing_fields": ["identity.lei"],
    }


def _thin_evidence_company() -> dict[str, Any]:
    """E: identity + price only. No fundamentals of any kind."""
    return {
        "is_mock": False,
        "company_identity": {
            "legal_name": "Testco Thin AS",
            "ticker": "THIN",
            "exchange": "CPH",
            "country_domicile": "DK",
        },
        "profile": {"sector": "Consumer Discretionary"},
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
            "date_range": {"start": "2025-08-01", "end": "2026-08-01"},
        },
        "missing_fields": ["identity.isin", "identity.lei"],
    }


def _issuer_primary_company(channel: str) -> dict[str, Any]:
    """B/C: European issuer whose statement facts come from a primary document.

    ``channel`` distinguishes the PDF and HTML cases. The point of having both
    is that neither should be treated as more "real" than the other: absence of
    a PDF must not manufacture a financial gap when HTML facts exist.
    """
    return {
        "is_mock": False,
        "company_identity": {
            "legal_name": "Testco Europa AG",
            "ticker": "TEUR",
            "exchange": "SIX",
            "country_domicile": "CH",
            "isin": "CH0000000001",
        },
        "profile": {"sector": "Consumer Discretionary", "industry": "Luxury Goods"},
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
            "date_range": {"start": "2025-08-01", "end": "2026-08-01"},
        },
        "fundamentals_summary": {
            "revenue_usd_m": 22400.0,
            "operating_income_usd_m": 4492.0,
            "operating_cash_flow_usd_m": 4880.0,
            "period_basis": "annual",
            "fiscal_year": 2026,
            "source": f"issuer_{channel}",
            "source_tier": "T1_primary_filing",
            "data_quality": "A_verified",
        },
        "missing_fields": [],
    }


def _round_trip(payload: dict[str, Any]) -> dict[str, Any]:
    """Force the real serialisation boundary: dict -> JSON text -> dict."""
    return json.loads(json.dumps(payload))


def _produce(snapshot: dict[str, Any]) -> dict[str, Any]:
    """REAL producer -> REAL serializer -> JSON round trip."""
    output = run_financial_data_agent(snapshot, source_ids=["s1"])
    return _round_trip(financial_data_agent_output_to_dict(output))


# ===========================================================================
# §9 ACCEPTANCE TEST — the bug that escaped before
# ===========================================================================
def test_producer_output_reaches_consumers_with_real_counts() -> None:
    """THE Phase B acceptance test.

    Nothing here hand-writes ``available_count`` or ``available_fields``. The
    real agent runs, its real serializer emits, JSON round-trips, and the real
    report consumer reads. If a producer field is renamed without updating the
    contract, the count collapses to 0 and this fails.
    """
    snapshot = _us_structured_filing_company()
    output = run_financial_data_agent(snapshot, source_ids=["s1"])
    payload = _round_trip(financial_data_agent_output_to_dict(output))

    # Ground truth comes from the PRODUCER OBJECT, never from a literal.
    expected_fields = list(output.available_financial_data)
    assert expected_fields, "fixture must produce some available fields"

    # Serialised payload agrees with the producer.
    assert payload["available_fields"] == expected_fields
    assert payload["available_count"] == len(expected_fields)

    # The REAL consumer agrees with the producer.
    section = _build_data_availability_summary(
        financial_data_summary=payload,
        fundamentals_available=None,
        source_tier="T2_regulator_or_gov",
        data_provenance="real",
        fundamentals=resolve_fundamentals(snapshot, {}),
    )
    assert section["available_count"] == len(expected_fields)
    assert section["available_fields"]["value"] == expected_fields
    assert section["available_count"] > 0

    # And the scoring consumer sees the same non-zero completeness.
    score = ScoringEngine()._score_financial_strength_from_summary(
        payload, "T2_regulator_or_gov"
    )
    assert score.score > 0


def test_count_can_never_contradict_its_own_field_list() -> None:
    """The structural guarantee: counts are DERIVED, not stored.

    A stale count in a payload is ignored when the list is present, so the
    exact shipped state ("Available Count = 0" beside populated fields) is
    unrepresentable.
    """
    summary = FinancialDataSummary.from_payload(
        {"available_fields": ["a", "b", "c"], "available_count": 0}
    )
    assert summary is not None
    assert summary.available_count == 3
    assert summary.to_payload()["available_count"] == 3


# ===========================================================================
# A. RICH US STRUCTURED-FILING COMPANY
# ===========================================================================
def test_us_company_fundamentals_and_split_provenance() -> None:
    snapshot = _us_structured_filing_company()
    payload = _produce(snapshot)

    section = _build_data_availability_summary(
        financial_data_summary=payload,
        fundamentals_available=None,
        source_tier="T2_regulator_or_gov",
        data_provenance="real",
        fundamentals=resolve_fundamentals(snapshot, {}),
    )
    assert section["fundamentals_available"] is True
    assert section["available_count"] > 0
    assert section["available_fields"]["value"]

    fields = section["available_fields"]["value"]
    for metric in ("financials.revenue", "financials.net_income"):
        assert metric in fields, f"{metric} must survive producer->consumer"

    # Provenance stays split: financials from the regulator, price from EODHD.
    price = PriceSummary.from_provenance(resolve_price_provenance(snapshot))
    assert price.available is True
    assert price.provenance.provider_name == "eodhd_price_only"
    assert price.provenance.source_tier == "T5_api_aggregator"

    fundamentals = FundamentalsResolution.from_evidence(
        resolve_fundamentals(snapshot, {})
    )
    assert fundamentals.available is True
    assert fundamentals.regulator_facts_available is True
    assert fundamentals.provenance.provider_name == "sec_edgar_xbrl"

    inventory = EvidenceInventory(
        financial_data=FinancialDataSummary.from_payload(payload),
        price=price,
        fundamentals=fundamentals,
    )
    assert inventory.has_financial_evidence is True
    assert inventory.to_payload()["price"]["provenance"]["provider_name"] == (
        "eodhd_price_only"
    )


def test_valuation_consumer_sees_the_price() -> None:
    """The valuation guard must see a current price that the report contains."""
    snapshot = _us_structured_filing_company()
    payload = _produce(snapshot)
    result = run_valuation_guard_agent(
        company_snapshot=snapshot,
        financial_data_summary=payload,
        source_quality_summary={"overall_source_quality": "adequate"},
    )
    blob = json.dumps(result if isinstance(result, dict) else result.__dict__).lower()
    assert "price history not available" not in blob
    assert "no current price" not in blob


# ===========================================================================
# B / C. ISSUER PRIMARY FACTS — PDF and HTML are equivalent fact sources
# ===========================================================================
def test_issuer_primary_facts_survive_for_pdf_and_html_alike() -> None:
    for channel in ("pdf", "html"):
        snapshot = _issuer_primary_company(channel)
        payload = _produce(snapshot)

        section = _build_data_availability_summary(
            financial_data_summary=payload,
            fundamentals_available=None,
            source_tier="T1_primary_filing",
            data_provenance="real",
            fundamentals=resolve_fundamentals(snapshot, {}),
        )
        assert section["fundamentals_available"] is True, channel
        assert section["available_count"] > 0, channel

        fundamentals = FundamentalsResolution.from_evidence(
            resolve_fundamentals(snapshot, {})
        )
        assert fundamentals.available is True, channel
        assert fundamentals.issuer_primary_facts_available is True, channel
        # Absence of a PDF must never invent a financial gap when HTML facts
        # exist — the two channels resolve identically.
        assert fundamentals.provenance.source_tier == "T1_primary_filing", channel


def test_html_and_pdf_channels_produce_the_same_availability() -> None:
    pdf = _produce(_issuer_primary_company("pdf"))
    html = _produce(_issuer_primary_company("html"))
    assert pdf["available_fields"] == html["available_fields"]
    assert pdf["available_count"] == html["available_count"]


# ===========================================================================
# D. MIXED-SOURCE — every field keeps its OWN provenance
# ===========================================================================
def test_mixed_source_company_keeps_per_field_provenance() -> None:
    snapshot = _us_structured_filing_company()
    price = PriceSummary.from_provenance(resolve_price_provenance(snapshot))
    fundamentals = FundamentalsResolution.from_evidence(
        resolve_fundamentals(snapshot, {})
    )
    container = snapshot["provider_metadata"]["provider_name"]

    assert price.provenance.provider_name != container
    assert fundamentals.provenance.provider_name != price.provenance.provider_name
    assert price.provenance.source_tier != fundamentals.provenance.source_tier


def test_price_provenance_falls_back_visibly_not_silently() -> None:
    """A price feed with NO provenance of its own may inherit the container's.

    That fallback is legitimate, but the tier must not become the company's
    (which is how "price history from sec_edgar (T2)" was produced).
    """
    snapshot = _us_structured_filing_company()
    snapshot["price_history_summary"].pop("provider_name")
    snapshot["price_history_summary"].pop("source_tier")
    price = PriceSummary.from_provenance(resolve_price_provenance(snapshot))
    assert price.provenance.provider_name == "sec_edgar"  # inherited
    assert price.provenance.source_tier == "T5_api_aggregator"  # price-appropriate


# ===========================================================================
# E. THIN EVIDENCE — honest emptiness, never fabricated richness
# ===========================================================================
def test_thin_company_reports_honest_absence() -> None:
    snapshot = _thin_evidence_company()
    payload = _produce(snapshot)

    section = _build_data_availability_summary(
        financial_data_summary=payload,
        fundamentals_available=None,
        source_tier="T5_api_aggregator",
        data_provenance="real",
        fundamentals=resolve_fundamentals(snapshot, {}),
    )
    assert section["fundamentals_available"] is False
    fields = section["available_fields"]["value"]
    assert not [f for f in fields if f.startswith("financials.")]
    assert section["missing_financial_fields_count"] > 0

    inventory = EvidenceInventory(
        financial_data=FinancialDataSummary.from_payload(payload),
        price=PriceSummary.from_provenance(resolve_price_provenance(snapshot)),
        fundamentals=FundamentalsResolution.from_evidence(
            resolve_fundamentals(snapshot, {})
        ),
    )
    assert inventory.fundamentals.available is False
    # Identity/price fields exist, so the agent legitimately found SOMETHING —
    # but no statement facts were fabricated.
    assert inventory.price.available is True


# ===========================================================================
# F. LEGACY PAYLOAD — one normalization boundary, no aliases beyond it
# ===========================================================================
def test_legacy_payload_normalizes_once_and_then_reads_canonically() -> None:
    legacy = _round_trip(
        {
            "available_financial_data": ["financials.revenue", "financials.net_income"],
            "missing_financial_data": ["financials.ebitda"],
            "data_quality_notes": ["note"],
            "source_tier_summary": {"T2_regulator_or_gov": 2},
            "financial_context_summary": "legacy summary",
            "warnings": ["w"],
        }
    )
    summary = FinancialDataSummary.from_payload(legacy)
    assert summary is not None
    assert summary.available_count == 2
    assert summary.missing_count == 1
    assert summary.warnings_count == 1

    # Beyond the boundary the canonical spelling is the ONLY spelling.
    canonical = summary.to_payload()
    assert "available_financial_data" not in canonical
    assert "missing_financial_data" not in canonical
    assert canonical["available_fields"] == legacy["available_financial_data"]

    # A historical report payload still renders.
    section = _build_data_availability_summary(
        financial_data_summary=legacy,
        fundamentals_available=None,
        source_tier="T2_regulator_or_gov",
        data_provenance="real",
        fundamentals=resolve_fundamentals(_us_structured_filing_company(), {}),
    )
    assert section["available_count"] == 2


def test_absent_summary_stays_absent() -> None:
    """"No summary" must remain distinguishable from "a summary that found 0"."""
    assert FinancialDataSummary.from_payload(None) is None
    assert FinancialDataSummary.from_payload({}) is not None
    assert FinancialDataSummary.from_payload({}).available_count == 0


def test_compact_counts_only_payload_is_preserved() -> None:
    """A compact payload carries counts without names — that is information.

    Discarding it would score a company as having nothing, which is the failure
    mode this phase exists to prevent.
    """
    summary = FinancialDataSummary.from_payload(
        {"available_count": 2, "missing_count": 8}
    )
    assert summary is not None
    assert summary.available_count == 2
    assert summary.missing_count == 8
    # A real list always wins over a count.
    assert (
        FinancialDataSummary.from_payload(
            {"available_count": 99, "available_fields": ["only-one"]}
        ).available_count
        == 1
    )


# ===========================================================================
# Contract hygiene
# ===========================================================================
def test_deprecated_alias_shim_has_no_production_callers() -> None:
    """The emergency dual-spelling shim must not creep back into production."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if "normalize_financial_data_summary(" in path.read_text(encoding="utf-8")
        and path.name != "canonical_evidence.py"
    ]
    assert offenders == [], f"use FinancialDataSummary instead: {offenders}"


def test_schema_version_is_emitted_for_new_payloads() -> None:
    payload = FinancialDataSummary(available_fields=["a"]).to_payload()
    assert payload["evidence_state_schema_version"] == EVIDENCE_STATE_SCHEMA_VERSION


def test_field_provenance_rejects_unknown_keys() -> None:
    """extra=forbid: a typo becomes an error, not a silently ignored field."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FieldProvenance(provider_nmae="typo")  # type: ignore[call-arg]
