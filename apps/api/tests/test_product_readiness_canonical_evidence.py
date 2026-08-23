"""
Product readiness — canonical final-report evidence state.

Every test here is pinned to a REAL contradiction observed in manual QA of the
NVDA final report ``a42c9295`` (discovery run ``eee7b0c7``), whose LLM council
was quoting genuine FY2026 SEC/XBRL statement facts (revenue $215,938m, net
income $120,067m, OCF $102,718m, assets $206,800m) while other sections of the
SAME report said the fundamentals were unavailable.

Defects covered:

  A. ``fundamentals_available=true`` + ``available_count=0`` + ``available_fields=[]``
  B. "Fundamentals not available. Run with EODHD provider or add T1 filings."
  C. "Price history available from sec_edgar: 251 data points" for an
     ``eodhd_price_only`` price series
  D. "Annual report / 10-K / 40-F — T1_primary_filing required for financials"
     asserted while the 10-K XBRL statements were already sourced
  E. "Forbidden recommendation word detected: SHORT" on "product cycles may
     shorten"; "[rating redacted]-side" for "sell-side"
  F. ``source_classes_successful: [sec_filings]`` beside ``filing_event_count: 0``
  G. A Glassdoor CEO ranking ranked as a high-strength catalyst
  H. "the reports were scanned or JS-gated" for documents that were in fact
     natively extracted

All offline — no network, no credentials, no LLM calls.
"""

from __future__ import annotations

from typing import Any

from app.agents.analysis_council.bear_case_agent import (
    _check_forbidden_content as bear_forbidden,
)
from app.agents.analysis_council.bull_case_agent import (
    _check_forbidden_content as bull_forbidden,
)
from app.agents.analysis_council.bull_case_agent import run_bull_case_agent
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.agents.research_team.source_quality_agent import run_source_quality_agent
from app.schemas.catalyst import (
    CatalystEvent,
    normalize_event_date,
    summarize_events,
)
from app.services import safety_terms
from app.services.canonical_evidence import (
    build_evidence_channels,
    normalize_financial_data_summary,
    resolve_fundamentals,
    resolve_price_provenance,
)
from app.services.catalyst_classifier import apply_classification
from app.services.catalyst_discovery_service import _select_bounded_events
from app.services.final_report_generator import (
    _build_data_availability_summary,
    _build_financial_snapshot,
    _document_gap_state_note,
)

# asyncio_mode = "auto" (see pyproject.toml) — async tests need no marker.


# ---------------------------------------------------------------------------
# The real NVDA shape
# ---------------------------------------------------------------------------


def _nvda_snapshot() -> dict[str, Any]:
    """The exact shape the free_real provider stack produces for NVDA:
    identity/profile from SEC EDGAR (T2), price from EODHD (T5), financial
    statements from SEC EDGAR XBRL (T2). No EODHD fundamentals payload."""
    return {
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "retrieved_at": "2026-08-22T12:53:13+00:00",
        "company_identity": {
            "ticker": "NVDA",
            "legal_name": "NVIDIA CORP",
            "exchange": "Nasdaq",
            "country_domicile": "US",
        },
        "profile": {"sector": "Technology", "reporting_currency": "USD"},
        "provider_metadata": {
            "provider_name": "sec_edgar",
            "source_tier": "T2_regulator_or_gov",
        },
        "price_history_summary": {
            "available": True,
            "data_points_count": 251,
            "latest_close": 214.72,
            "currency": "USD",
            "provider_name": "eodhd_price_only",
            "source_tier": "T5_api_aggregator",
            "date_range": {"start": "2025-08-21", "end": "2026-08-21"},
            "price_data_quality": "B_single_credible",
        },
        "fundamentals_summary": {
            "source_tier": "T2_regulator_or_gov",
            "provider": "sec_edgar",
            "period_basis": "annual",
            "fiscal_year": 2026,
            "form_type": "10-K",
            "revenue_usd_m": 215938.0,
            "gross_profit_usd_m": 153500.0,
            "operating_income_usd_m": 130400.0,
            "net_income_usd_m": 120067.0,
            "operating_cash_flow_usd_m": 102718.0,
            "capital_expenditures_usd_m": 6000.0,
            "total_assets_usd_m": 206800.0,
            "total_liabilities_usd_m": 49500.0,
            "shareholders_equity_usd_m": 157300.0,
            "total_debt_usd_m": 9500.0,
            "eps_diluted": 4.90,
            "shares_outstanding_mln": 24300.0,
        },
        "missing_fields": ["identity.isin", "identity.lei"],
    }


# =========================================================================== #
# A. Key-name mismatch — the availability counts were silently 0             #
# =========================================================================== #


def test_financial_data_summary_emits_reader_facing_count_keys() -> None:
    """``FinancialDataAgent`` emitted ``available_financial_data`` while every
    reader asked for ``available_count`` / ``available_fields``, so the counts
    silently defaulted to 0 / []."""
    summary = financial_data_agent_output_to_dict(
        run_financial_data_agent(_nvda_snapshot(), source_ids=["s1"])
    )
    assert summary["available_count"] == len(summary["available_fields"])
    assert summary["available_count"] > 0
    # Phase B: the legacy agent spelling is no longer re-emitted — it is
    # accepted at ingress only, so consumers cannot pick the wrong one.
    assert "available_financial_data" not in summary
    assert "missing_financial_data" not in summary
    assert summary["missing_count"] == len(summary["missing_fields"])
    assert summary["warnings_count"] == len(summary["warnings"])


def test_normalize_financial_data_summary_is_idempotent() -> None:
    once = normalize_financial_data_summary(
        {"available_financial_data": ["a", "b"], "warnings": ["w"]}
    )
    twice = normalize_financial_data_summary(once)
    assert once == twice
    assert once["available_count"] == 2
    assert once["warnings_count"] == 1


def test_normalize_never_invents_absent_keys() -> None:
    out = normalize_financial_data_summary({"financial_context_summary": "x"})
    assert out == {"financial_context_summary": "x"}
    assert normalize_financial_data_summary(None) is None


def test_data_availability_summary_no_longer_self_contradicts() -> None:
    """The exact NVDA defect: available=Yes, count=0, fields=Not sourced."""
    summary = financial_data_agent_output_to_dict(
        run_financial_data_agent(_nvda_snapshot(), source_ids=["s1"])
    )
    section = _build_data_availability_summary(
        summary,
        True,
        "T2_regulator_or_gov",
        data_provenance="real",
        fundamentals=resolve_fundamentals(_nvda_snapshot()),
    )
    assert section["fundamentals_available"] is True
    assert section["available_count"] > 0
    assert section["available_fields"]["value"]
    # Availability is corroborated by the inventory, not asserted by a bare flag.
    assert section["fundamentals_source"] == "sec_edgar_xbrl"
    assert section["fundamentals_source_tier"] == "T2_regulator_or_gov"
    assert section["fundamentals_period"] == "annual FY2026"
    assert section["fundamentals_field_count"] > 0


def test_data_availability_flag_cannot_claim_more_than_the_inventory() -> None:
    """A bare workflow flag must not survive contradiction by the inventory."""
    bare = {"company_identity": {"ticker": "ZZZ"}, "profile": {}, "missing_fields": []}
    section = _build_data_availability_summary(
        {"available_financial_data": [], "missing_financial_data": [], "warnings": []},
        True,  # workflow claimed fundamentals were available
        "T6_model_estimate",
        fundamentals=resolve_fundamentals(bare),
    )
    assert section["fundamentals_available"] is False


# =========================================================================== #
# B. SEC/XBRL facts are REAL fundamentals                                     #
# =========================================================================== #


def test_sec_xbrl_statements_are_recognised_as_fundamentals() -> None:
    ev = resolve_fundamentals(_nvda_snapshot())
    assert ev.available is True
    assert ev.source == "sec_edgar_xbrl"
    assert ev.source_tier == "T2_regulator_or_gov"
    assert ev.is_regulator_structured is True
    assert ev.values["revenue_usd_m"] == 215938.0
    assert "not available" not in ev.note().lower()


def test_financial_snapshot_carries_the_real_sec_statement_values() -> None:
    section = _build_financial_snapshot(_nvda_snapshot(), None)
    assert section["revenue_usd_m"]["value"] == 215938.0
    assert section["net_income_usd_m"]["value"] == 120067.0
    assert section["operating_cash_flow_usd_m"]["value"] == 102718.0
    assert section["total_assets_usd_m"]["value"] == 206800.0
    assert section["total_liabilities_usd_m"]["value"] == 49500.0
    assert section["shareholders_equity_usd_m"]["value"] == 157300.0
    for key in ("revenue_usd_m", "net_income_usd_m"):
        assert section[key]["source"] == "sec_edgar_xbrl"
        assert section[key]["source_tier"] == "T2_regulator_or_gov"


def test_financial_snapshot_note_never_claims_fundamentals_are_absent() -> None:
    note = _build_financial_snapshot(_nvda_snapshot(), None)["fundamentals_note"]
    assert "Fundamentals not available" not in note["value"]
    assert "Run with EODHD provider" not in note["value"]
    assert note["fundamentals_source"] == "sec_edgar_xbrl"
    assert note["provenance"] == "sourced_fact"


def test_no_fundamentals_anywhere_is_still_reported_honestly() -> None:
    bare = {
        "is_mock": False,
        "company_identity": {"ticker": "ZZZ"},
        "profile": {},
        "missing_fields": [],
    }
    ev = resolve_fundamentals(bare)
    assert ev.available is False
    note = _build_financial_snapshot(bare, None)["fundamentals_note"]
    assert note["provenance"] == "missing_data"
    assert "not sourced" in note["value"].lower()


def test_stronger_source_is_never_overwritten_by_a_weaker_one() -> None:
    """Source-priority rule: T2 regulator facts outrank a T5 aggregator, and
    both channels stay visible so a conflict is exposed rather than hidden."""
    eodhd = {"highlights": {"revenue_ttm": 200000.0, "pe_ratio": 43.5}}
    ev = resolve_fundamentals(_nvda_snapshot(), eodhd)
    assert ev.source == "sec_edgar_xbrl"
    assert ev.source_tier == "T2_regulator_or_gov"
    assert set(ev.channels) == {"sec_edgar_xbrl", "eodhd_fundamentals"}


def test_aggregator_only_company_still_reports_eodhd_fundamentals() -> None:
    snap = _nvda_snapshot()
    snap.pop("fundamentals_summary")
    ev = resolve_fundamentals(snap, {"highlights": {"revenue_ttm": 200000.0}})
    assert ev.available is True
    assert ev.source == "eodhd_fundamentals"
    assert ev.source_tier == "T5_api_aggregator"
    assert ev.is_regulator_structured is False


def test_medium_confidence_or_segment_scoped_facts_never_count_as_fundamentals() -> None:
    """A segment figure must never stand in for the group, and a
    medium-confidence parse is not an established fact."""
    base = {"company_identity": {}, "profile": {}, "missing_fields": []}
    facts = [
        {"field": "revenue", "value": 3100.0, "confidence": "high",
         "scope": "Specialist Watchmakers"},
        {"field": "revenue", "value": 22420.0, "confidence": "medium"},
    ]
    ev = resolve_fundamentals(base, None, facts, financial_fields=frozenset({"revenue"}))
    assert ev.available is False


# =========================================================================== #
# C. Price provenance                                                         #
# =========================================================================== #


def test_price_is_attributed_to_its_own_provider_not_the_company_provider() -> None:
    price = resolve_price_provenance(_nvda_snapshot())
    assert price.provider_name == "eodhd_price_only"
    assert price.source_tier == "T5_api_aggregator"
    assert "sec_edgar" not in price.evidence_sentence()
    assert "eodhd_price_only" in price.evidence_sentence()


def test_bull_case_evidence_never_relabels_eodhd_price_as_sec() -> None:
    out = run_bull_case_agent(
        _nvda_snapshot(),
        {"available_financial_data": []},
        {"overall_source_quality": "strong"},
        {"complete_sections": [], "missing_required_fields": [], "blocking_gaps": []},
    )
    price_lines = [e for e in out.evidence_used if "Price history" in e]
    assert price_lines
    assert "eodhd_price_only" in price_lines[0]
    assert "sec_edgar" not in price_lines[0]


def test_bull_case_stops_claiming_fundamentals_are_unsourced_when_they_are() -> None:
    out = run_bull_case_agent(
        _nvda_snapshot(),
        {"available_financial_data": []},
        {"overall_source_quality": "strong"},
        {"complete_sections": [], "missing_required_fields": [], "blocking_gaps": []},
    )
    joined = " ".join(out.positive_thesis_points)
    assert "fundamentals (not yet sourced)" not in joined
    assert "sec_edgar_xbrl" in joined


def test_financial_snapshot_latest_close_carries_the_price_source() -> None:
    node = _build_financial_snapshot(_nvda_snapshot(), None)["latest_close"]
    assert node["value"] == 214.72
    assert node["source"] == "eodhd_price_only"
    assert node["source_tier"] == "T5_api_aggregator"


def test_price_provenance_absent_is_reported_as_absent() -> None:
    snap = _nvda_snapshot()
    snap["price_history_summary"] = {"available": False}
    price = resolve_price_provenance(snap)
    assert price.available is False
    assert price.evidence_sentence() == "Price history not available."


# =========================================================================== #
# D. No false "primary filings required" gap                                  #
# =========================================================================== #


def test_missing_primary_sources_drops_the_closed_10k_financials_gap() -> None:
    out = run_source_quality_agent(_nvda_snapshot())
    joined = " ".join(out.missing_primary_sources)
    assert "10-K / 40-F — T1_primary_filing required for financials" not in joined
    # The genuinely-open gap is named precisely instead.
    assert "NARRATIVE" in joined
    assert "sec_edgar_xbrl statement facts are already sourced" in joined


def test_missing_primary_sources_still_asserts_the_gap_when_it_is_real() -> None:
    bare = {
        "company_identity": {"ticker": "ZZZ", "exchange": "Nasdaq"},
        "profile": {},
        "price_history_summary": {"available": False},
        "provider_metadata": {"provider_name": "stooq", "source_tier": "T5_api_aggregator"},
        "missing_fields": [],
        "is_mock": False,
    }
    joined = " ".join(run_source_quality_agent(bare).missing_primary_sources)
    assert "T1_primary_filing required for financials" in joined


# =========================================================================== #
# E. Safety lexical false positives                                           #
# =========================================================================== #

_MUST_BLOCK = (
    "BUY NVDA",
    "SELL the stock",
    "rating: HOLD",
    "we recommend SHORT",
    "Analyst issues a strong buy rating",
    "price target of 250",
    "fair value of 300",
)

_MUST_ALLOW = (
    "sell-side analyst estimates",
    "short-term debt",
    "product cycles may shorten",
    "Specialist Watchmakers",
    "watch industry",
    "watch revenue",
    "watch segment",
    "XYZ Holding AG",
    "insiders hold 12% of shares",
    "credit rating agencies reviewed the issuer's short-term debt",
)


def test_safety_gate_blocks_real_recommendation_language() -> None:
    for text in _MUST_BLOCK:
        assert safety_terms.scan_text(text), text


def test_safety_gate_allows_legitimate_finance_terminology() -> None:
    for text in _MUST_ALLOW:
        assert not safety_terms.scan_text(text), text


def test_bull_and_bear_agents_no_longer_false_positive() -> None:
    """"product cycles may shorten" contains the substring SHORT and was
    flagged as a forbidden recommendation word by the old upper()+`in` scan."""
    for text in _MUST_ALLOW:
        assert bull_forbidden(text) == [], text
        assert bear_forbidden(text) == [], text
    for text in _MUST_BLOCK:
        assert bull_forbidden(text), text
        assert bear_forbidden(text), text


def test_neutraliser_leaves_legitimate_terminology_byte_identical() -> None:
    for text in _MUST_ALLOW:
        assert safety_terms.neutralize_text(text) == text, text


def test_neutraliser_output_always_passes_the_gate() -> None:
    for text in _MUST_BLOCK:
        cleaned = safety_terms.neutralize_text(text)
        assert not safety_terms.scan_text(cleaned or ""), text


def test_external_headline_return_claims_are_still_neutralised() -> None:
    """Stricter-than-the-gate rule for third-party text is preserved."""
    out = safety_terms.neutralize_text("Analyst sees upside; shares undervalued") or ""
    assert "upside" not in out.lower()
    assert "undervalued" not in out.lower()


# =========================================================================== #
# F. Catalyst counts                                                          #
# =========================================================================== #


def _press(i: int) -> CatalystEvent:
    return CatalystEvent(
        id=f"p{i}",
        ticker="NVDA",
        normalized_event_type="press_release",
        source_tier="T1_primary_filing",
        event_date="Wed, 29 Jul 2026 21:00:00 GMT",
        headline=f"NVIDIA announces something {i}",
    )


def _filing(i: int) -> CatalystEvent:
    return CatalystEvent(
        id=f"f{i}",
        ticker="NVDA",
        normalized_event_type="sec_filing",
        source_tier="T2_regulator_or_gov",
        event_date=f"2026-08-{10 + i:02d}",
        headline=f"8-K filing {i}",
    )


def test_event_dates_from_different_connectors_are_comparable() -> None:
    assert normalize_event_date("Wed, 29 Jul 2026 21:00:00 GMT") == "2026-07-29"
    assert normalize_event_date("2026-08-17") == "2026-08-17"
    assert normalize_event_date("2026-08-17T12:00:00+00:00") == "2026-08-17"
    assert normalize_event_date("not a date") is None
    assert normalize_event_date(None) is None


def test_sec_filings_are_never_truncated_away_by_press_releases() -> None:
    """The exact NVDA defect: 20 press releases + 4 filings, cap 20 →
    filing_event_count 0 while sec_filings was reported as successful."""
    selected = _select_bounded_events(
        {
            "sec_filing": [_filing(i) for i in range(4)],
            "press_release": [_press(i) for i in range(20)],
            "news_article": [],
        },
        max_events=20,
    )
    summary = summarize_events(selected, 90)
    assert summary.filing_event_count == 4
    assert summary.press_release_event_count == 16
    assert summary.total_events == 20
    # Source-class counts partition the events exactly once — no double count.
    assert (
        summary.filing_event_count
        + summary.press_release_event_count
        + summary.news_event_count
        == summary.total_events
    )


def test_latest_event_date_is_a_real_normalised_date() -> None:
    selected = _select_bounded_events(
        {"sec_filing": [_filing(3)], "press_release": [_press(0)]}, max_events=10
    )
    assert summarize_events(selected, 90).latest_event_date == "2026-08-13"


def test_bounded_selection_never_duplicates_an_event() -> None:
    selected = _select_bounded_events(
        {"sec_filing": [_filing(i) for i in range(3)],
         "press_release": [_press(i) for i in range(3)]},
        max_events=20,
    )
    assert len(selected) == len({id(e) for e in selected}) == 6


# =========================================================================== #
# G. Catalyst materiality                                                     #
# =========================================================================== #


def _classified(headline: str, event_type: str = "press_release") -> CatalystEvent:
    return apply_classification(
        CatalystEvent(
            id="x",
            ticker="NVDA",
            headline=headline,
            source_tier="T1_primary_filing",
            normalized_event_type=event_type,
        )
    )


def test_recognition_posts_are_low_signal_and_demoted() -> None:
    ev = _classified("NVIDIA CEO Tops Glassdoor's 2026 List of Best CEOs")
    assert ev.materiality == "low_signal"
    assert ev.catalyst_strength != "high"
    assert ev.materiality_reason


def test_marketing_posts_are_low_signal() -> None:
    assert _classified("GeForce NOW Shakes Up August With 26 New Games").materiality == (
        "low_signal"
    )


def test_scheduling_notices_are_contextual_not_decision_relevant() -> None:
    ev = _classified("NVIDIA Sets Conference Call for Second-Quarter Financial Results")
    assert ev.materiality == "contextual"


def test_material_corporate_events_stay_decision_relevant() -> None:
    ev = _classified("NVIDIA Announces Acquisition of a Networking Company")
    assert ev.materiality == "decision_relevant"
    assert ev.catalyst_strength == "high"


def test_regulator_filings_are_decision_relevant_by_construction() -> None:
    ev = _classified("Current report", event_type="sec_filing")
    assert ev.materiality == "decision_relevant"


def test_low_signal_items_are_ranked_not_discarded() -> None:
    events = [
        _classified("NVIDIA CEO Tops Glassdoor's 2026 List of Best CEOs"),
        _classified("NVIDIA Announces Acquisition of a Networking Company"),
    ]
    summary = summarize_events(events, 90)
    assert summary.total_events == 2  # nothing dropped
    assert summary.decision_relevant_count == 1
    assert summary.low_signal_count == 1


# =========================================================================== #
# H. Evidence channels + honest document-gap cause                            #
# =========================================================================== #


class _Artifact:
    def __init__(self, status: str, failure_code: str | None = None) -> None:
        self.status = status
        self.failure_code = failure_code


class _Council:
    def __init__(self, artifacts: list[_Artifact]) -> None:
        self.primary_document_artifacts = artifacts


def test_extracted_documents_are_never_called_scanned_or_js_gated() -> None:
    """NVDA: two SEC 8-K HTML documents were natively extracted
    (status="extracted", extraction_method="html"). Telling the reviewer the
    reports were "scanned or JS-gated" is a fabricated diagnosis."""
    note = _document_gap_state_note(
        _Council([_Artifact("extracted"), _Artifact("extracted")]), []
    )
    assert "scanned or JS-gated" not in note
    assert "WERE fetched and extracted successfully" in note


def test_a_genuinely_unreadable_document_keeps_the_scan_framing() -> None:
    note = _document_gap_state_note(_Council([_Artifact("failed", "scanned_no_text")]), [])
    assert "could not be read" in note
    assert "scanned_no_text" in note


def test_no_candidate_discovered_says_exactly_that() -> None:
    note = _document_gap_state_note(_Council([]), [])
    assert "no issuer document candidate was discovered" in note
    assert "scanned" not in note


def test_evidence_channels_report_each_channel_separately() -> None:
    """"issuer primary document" and "regulator structured facts" are different
    things; zero of the first never implies zero of the second."""
    channels = build_evidence_channels(
        fundamentals=resolve_fundamentals(_nvda_snapshot()),
        primary_document_counts={"primary_document_extracted_count": 0},
        catalyst_summary={"filing_event_count": 4, "press_release_event_count": 20},
        citation_count=7,
        council_evidence_count=20,
    )
    by_name = {c["channel"]: c for c in channels["channels"]}
    assert by_name["issuer_primary_document"]["available"] is False
    assert by_name["issuer_primary_document"]["detail"] == "none / not used for this report"
    assert by_name["regulator_structured_facts"]["available"] is True
    assert by_name["regulator_filing_events"]["available"] is True
    assert by_name["regulator_filing_events"]["event_count"] == 4
    assert by_name["issuer_newsroom"]["available"] is True
    assert by_name["db_citations"]["available"] is True


def test_evidence_channels_carry_no_forbidden_language() -> None:
    channels = build_evidence_channels(
        fundamentals=resolve_fundamentals(_nvda_snapshot()),
        catalyst_summary={"filing_event_count": 4},
    )
    assert not safety_terms.scan_value(channels)


# =========================================================================== #
# I. Post-council reconciliation for ISSUER-DOCUMENT issuers (CFR / MC)       #
# =========================================================================== #
#
# STAGING REGRESSION, 2026-08-22. The fresh CFR (13e4ee85) and MC (9e5c7078)
# final reports exposed two defects that the NVDA (SEC/XBRL) path does not hit,
# because a non-US issuer has NO SEC XBRL — its financials arrive only as
# issuer-document facts, which exist ONLY AFTER the council has run:
#
#   1. `data_availability_summary` is assembled BEFORE the council, so its
#      canonical inventory had no issuer facts and reported
#      `fundamentals_available: false` — beside a financial snapshot whose own
#      note said "Fundamentals sourced from issuer_primary_document
#      (T1_primary_filing)". A NEW self-contradiction, introduced by the
#      canonical-evidence slice itself.
#   2. `_recompute_fresh_source_quality_summary` did not propagate
#      `missing_primary_sources`, so the stale pre-ingestion "Annual report /
#      10-K / 40-F — T1_primary_filing required for financials" survived into a
#      report that then rendered three T1 facts read from that very report.


def _cfr_snapshot() -> dict[str, Any]:
    """A non-US issuer: real price + identity, but NO SEC XBRL fundamentals."""
    return {
        "is_mock": False,
        "source_tier": "T5_api_aggregator",
        "company_identity": {
            "ticker": "CFR",
            "legal_name": "Compagnie Financiere Richemont SA",
            "exchange": "SW",
            "country_domicile": "CH",
        },
        "profile": {"sector": "Consumer Cyclical", "reporting_currency": "EUR"},
        "provider_metadata": {
            "provider_name": "free_real",
            "source_tier": "T5_api_aggregator",
        },
        "price_history_summary": {
            "available": True,
            "data_points_count": 250,
            "latest_close": 145.2,
            "currency": "CHF",
            "provider_name": "stooq",
            "source_tier": "T5_api_aggregator",
            "date_range": {"end": "2026-08-21"},
        },
        "missing_fields": [],
    }


def _issuer_facts() -> list[dict[str, Any]]:
    return [
        {"field": "revenue", "value": "sales reached EUR 22.4 billion",
         "confidence": "high"},
        {"field": "net_income", "value": "profit for the year amounted to EUR 3 484 million",
         "confidence": "high"},
    ]


def test_issuer_document_facts_make_fundamentals_available() -> None:
    ev = resolve_fundamentals(
        _cfr_snapshot(), None, _issuer_facts(),
        financial_fields=frozenset({"revenue", "net_income"}),
    )
    assert ev.available is True
    assert ev.source == "issuer_primary_document"
    assert ev.source_tier == "T1_primary_filing"


def test_availability_summary_agrees_with_the_snapshot_note() -> None:
    """The exact CFR/MC contradiction: available=False beside a note saying the
    fundamentals came from the issuer's own primary document."""
    facts = _issuer_facts()
    fundamentals = resolve_fundamentals(
        _cfr_snapshot(), None, facts,
        financial_fields=frozenset({"revenue", "net_income"}),
    )
    das = _build_data_availability_summary(
        {"available_financial_data": ["a"], "missing_financial_data": [], "warnings": []},
        False,  # the workflow flag, computed before ingestion
        "T5_api_aggregator",
        fundamentals=fundamentals,
    )
    snapshot_note = _build_financial_snapshot(
        _cfr_snapshot(), None, primary_facts=facts
    )["fundamentals_note"]

    assert das["fundamentals_available"] is True
    assert das["fundamentals_source"] == snapshot_note["fundamentals_source"]
    assert das["fundamentals_source_tier"] == snapshot_note["fundamentals_source_tier"]


def test_source_quality_gap_narrows_once_the_issuer_report_has_been_read() -> None:
    snap = _cfr_snapshot()
    before = " ".join(run_source_quality_agent(snap).missing_primary_sources)
    after = " ".join(
        run_source_quality_agent(snap, None, _issuer_facts()).missing_primary_sources
    )
    # Genuinely open before the document is read...
    assert "T1_primary_filing required for financials" in before
    # ...and precisely narrowed to the NARRATIVE gap once it has been.
    assert "T1_primary_filing required for financials" not in after
    assert "NARRATIVE" in after
    assert "issuer_primary_document" in after
