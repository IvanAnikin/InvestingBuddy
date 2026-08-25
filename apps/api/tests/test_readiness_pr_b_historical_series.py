"""
Private-use production readiness, PR-B — HISTORICAL FINANCIAL SERIES.

Phase 32D taught the extractor to rebuild borderless multi-year tables (a real
Pandora annual report yields ~52 period-scoped facts covering FY2021-FY2025),
but every downstream consumer took ONE representative value per field and
dropped the rest. A council could truthfully be handed a complete five-year
revenue series and still report "no historical revenue trend information".

These tests encode the ten required comparability cases from the campaign brief
plus the propagation path into the council pack and the report section.

Fully offline and deterministic: no network, no LLM, no Azure, no DB.
Issuer-shaped figures appear only in fixtures.
"""

from __future__ import annotations

import pytest

from app.services.final_report_generator import _build_historical_trends
from app.services.llm.evidence_pack import (
    _add_historical_series,
    _Builder,
    _facts_from_connector_evidence,
)
from app.services.sources.evidence import PrimaryFactRef, build_evidence_item
from app.services.sources.financial_history import (
    CALC_PERCENT_CHANGE,
    CALC_PERCENTAGE_POINT_CHANGE,
    COMPARABLE,
    COMPLETENESS_COMPLETE,
    COMPLETENESS_PARTIAL,
    NOT_COMPARABLE,
    REASON_PERIOD_UNKNOWN,
    REASON_RESTATED_PERIOD,
    REASON_SCOPE_UNKNOWN,
    REASON_SINGLE_PERIOD,
    build_financial_history,
    history_evidence_lines,
)
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_HALF,
    UNKNOWN_PERIOD,
    is_more_recent,
    latest,
    parse_period,
)
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
)


def fact(
    metric: str,
    value: float,
    period: str,
    *,
    scope: str | None = "group",
    currency: str | None = "DKK",
    scale: str | None = "million",
    confidence: str = "high",
    unit: str | None = None,
) -> dict:
    return {
        "field": metric,
        "value": str(value),
        "numeric_value": value,
        "period": period,
        "scope": scope,
        "currency": currency,
        "scale": scale,
        "unit": unit,
        "confidence": confidence,
        "source_url": "https://issuer.test/annual-report.pdf",
        "page_number": 14,
        "table_location": "page=14;table=1;row=1;col=2",
    }


def _revenue_5y(values=(23400, 26463, 28133, 31673, 32549), years=range(2021, 2026)):
    return [fact("revenue", v, str(y)) for y, v in zip(years, values)]


# =========================================================================== #
# Period model                                                                #
# =========================================================================== #


@pytest.mark.parametrize(
    "raw,ptype,year,ordinal",
    [
        ("2025", PERIOD_TYPE_ANNUAL, 2025, None),
        ("FY2025", PERIOD_TYPE_ANNUAL, 2025, None),
        ("H1 2026", PERIOD_TYPE_HALF, 2026, 1),
        ("2026 H1", PERIOD_TYPE_HALF, 2026, 1),
        ("H1 FY2026", PERIOD_TYPE_HALF, 2026, 1),
        ("Q2 2026", "quarter", 2026, 2),
        ("2025/26", "split_year", 2025, None),
    ],
)
def test_period_parsing(raw, ptype, year, ordinal) -> None:
    p = parse_period(raw)
    assert (p.period_type, p.year, p.ordinal) == (ptype, year, ordinal)


@pytest.mark.parametrize("raw", [None, "", "sometime", "1H26", "FY", "20255"])
def test_unparseable_period_is_unknown_never_guessed(raw) -> None:
    assert parse_period(raw).is_unknown


def test_period_label_never_emits_none() -> None:
    assert "None" not in UNKNOWN_PERIOD.label()
    assert UNKNOWN_PERIOD.label() == "Period not stated"


def test_annual_and_interim_are_never_comparable() -> None:
    assert not parse_period("2025").comparable_with(parse_period("H1 2026"))
    assert not parse_period("H1 2026").comparable_with(parse_period("2026"))


def test_h1_and_h2_are_not_comparable_but_h1_across_years_is() -> None:
    assert not parse_period("H1 2026").comparable_with(parse_period("H2 2026"))
    assert parse_period("H1 2025").comparable_with(parse_period("H1 2026"))


def test_unknown_period_is_comparable_with_nothing_including_itself() -> None:
    assert not UNKNOWN_PERIOD.comparable_with(UNKNOWN_PERIOD)


def test_is_more_recent_refuses_across_period_types() -> None:
    """"Is H1 2026 more recent than FY2025?" is not answered by comparing
    numbers — an interim result sits beside an annual one, not above it."""
    assert not is_more_recent(parse_period("H1 2026"), parse_period("2025"))
    assert is_more_recent(parse_period("2026"), parse_period("2025"))


def test_latest_refuses_a_mixed_type_input() -> None:
    assert latest([parse_period("2025"), parse_period("H1 2026")]).is_unknown
    assert latest([parse_period("2024"), parse_period("2025")]).year == 2025


# =========================================================================== #
# Required comparability cases A-J                                            #
# =========================================================================== #


def test_case_a_normal_five_year_annual_series() -> None:
    history = build_financial_history(_revenue_5y())
    assert len(history.series) == 1
    series = history.series[0]
    assert series.period_count == 5
    assert [p.period.year for p in series.points] == [2021, 2022, 2023, 2024, 2025]
    assert series.comparability == COMPARABLE
    assert series.completeness == COMPLETENESS_COMPLETE


def test_case_b_newest_period_supplied_first_still_orders_oldest_first() -> None:
    facts = list(reversed(_revenue_5y()))
    series = build_financial_history(facts).series[0]
    assert [p.period.year for p in series.points] == [2021, 2022, 2023, 2024, 2025]


def test_case_c_newest_period_supplied_last_orders_identically() -> None:
    forwards = build_financial_history(_revenue_5y()).series[0]
    backwards = build_financial_history(list(reversed(_revenue_5y()))).series[0]
    assert [p.value for p in forwards.points] == [p.value for p in backwards.points]


def test_case_d_missing_middle_year_is_named_not_filled() -> None:
    facts = [f for f in _revenue_5y() if f["period"] != "2023"]
    series = build_financial_history(facts).series[0]
    assert series.completeness == COMPLETENESS_PARTIAL
    assert series.missing_periods == ["FY2023"]
    assert [p.period.year for p in series.points] == [2021, 2022, 2024, 2025]
    # The gap is declared, not interpolated.
    assert all(p.value in {23400, 26463, 31673, 32549} for p in series.points)


def test_case_e_currency_mismatch_yields_two_series_never_one_trend() -> None:
    facts = [
        fact("revenue", 100, "2024", currency="EUR"),
        fact("revenue", 750, "2025", currency="DKK"),
    ]
    history = build_financial_history(facts)
    assert len(history.series) == 2
    # Neither can support a trend on its own, and no cross-currency change
    # was computed anywhere.
    assert all(s.comparability == NOT_COMPARABLE for s in history.series)
    assert all(REASON_SINGLE_PERIOD in s.comparability_reasons for s in history.series)
    assert all(not s.changes for s in history.series)


def test_case_f_group_and_segment_are_independent_series() -> None:
    facts = _revenue_5y() + [
        fact("revenue", v, str(y), scope="Specialist Watchmakers")
        for y, v in zip(range(2021, 2026), [3800, 3900, 4000, 3200, 3100])
    ]
    history = build_financial_history(facts)
    assert len(history.series) == 2
    group = next(s for s in history.series if s.scope.is_group)
    segment = next(s for s in history.series if s.scope.is_segment)
    assert group.points[-1].value == 32549
    assert segment.points[-1].value == 3100
    assert segment.scope_label == "Specialist Watchmakers"
    # No value from one appears in the other.
    assert not ({p.value for p in group.points} & {p.value for p in segment.points})


def test_case_g_fy_and_h1_values_are_independent_series() -> None:
    annual = build_financial_history(_revenue_5y(), period_type=PERIOD_TYPE_ANNUAL)
    interim_facts = [
        fact("revenue", 14421, "H1 2025"),
        fact("revenue", 14328, "H1 2026"),
    ]
    # Interim facts do not leak into the annual build...
    assert build_financial_history(
        _revenue_5y() + interim_facts, period_type=PERIOD_TYPE_ANNUAL
    ).series[0].points[-1].value == 32549
    # ...and are a separate series when explicitly requested.
    interim = build_financial_history(
        _revenue_5y() + interim_facts, period_type=PERIOD_TYPE_HALF
    )
    assert len(interim.series) == 1
    assert interim.series[0].period_type == PERIOD_TYPE_HALF
    assert [p.value for p in interim.series[0].points] == [14421, 14328]
    assert annual.series[0].points[-1].value == 32549


def test_case_h_a_larger_historical_value_does_not_become_the_latest() -> None:
    """Selection is by PERIOD, never by magnitude."""
    facts = [fact("revenue", 99999, "2022"), fact("revenue", 32549, "2025")]
    series = build_financial_history(facts).series[0]
    assert series.points[-1].period.year == 2025
    assert series.points[-1].value == 32549


def test_case_i_restated_period_resolves_deterministically_by_source_strength() -> None:
    facts = [
        fact("revenue", 31673, "2024"),
        fact("revenue", 31500, "2024", confidence="medium"),
        fact("revenue", 32549, "2025"),
    ]
    series = build_financial_history(facts).series[0]
    active = [p for p in series.points if not p.superseded]
    assert [p.value for p in active] == [31673, 32549]
    # The losing restatement is kept for audit, not deleted.
    assert any(p.superseded and p.value == 31500 for p in series.points)
    assert REASON_RESTATED_PERIOD in series.comparability_reasons
    # A restatement is declared but does not make the series unusable.
    assert series.comparability == COMPARABLE


def test_case_j_exact_table_value_and_narrative_duplicate_raise_no_conflict() -> None:
    """The same figure stated in a table AND in prose is one observation."""
    facts = [
        fact("revenue", 32549, "2025"),
        fact("revenue", 32549, "2025", confidence="medium"),
        fact("revenue", 31673, "2024"),
    ]
    series = build_financial_history(facts).series[0]
    assert len([p for p in series.points if not p.superseded]) == 2
    assert not any(p.superseded for p in series.points)
    assert REASON_RESTATED_PERIOD not in series.comparability_reasons


# =========================================================================== #
# Fail-closed behaviour                                                       #
# =========================================================================== #


def test_unscoped_facts_never_form_a_series() -> None:
    """An unscoped fact may be the Group's or a segment's. Guessing is how a
    segment trend gets presented as the Group's."""
    facts = [fact("revenue", v, str(y), scope=None) for y, v in zip((2024, 2025), (1, 2))]
    history = build_financial_history(facts)
    assert history.series == []
    assert history.skipped_reasons[REASON_SCOPE_UNKNOWN] == 2


def test_facts_with_unknown_periods_are_counted_not_silently_dropped() -> None:
    history = build_financial_history([fact("revenue", 1, "sometime")])
    assert history.series == []
    assert history.skipped_reasons[REASON_PERIOD_UNKNOWN] == 1


def test_series_is_capped_to_the_configured_period_count() -> None:
    facts = [fact("revenue", 100 + y, str(y)) for y in range(2015, 2026)]
    series = build_financial_history(facts, max_periods=5).series[0]
    assert series.period_count == 5
    # The NEWEST periods win; older ones are dropped, never averaged.
    assert [p.period.year for p in series.points] == [2021, 2022, 2023, 2024, 2025]


def test_a_margin_change_is_percentage_points_never_a_percent_of_a_percent() -> None:
    facts = [
        fact("operating_margin", 23.9, "2025", currency=None, scale=None),
        fact("operating_margin", 24.9, "2024", currency=None, scale=None),
    ]
    series = build_financial_history(facts).series[0]
    calcs = {c.calculation for c in series.changes}
    assert calcs == {CALC_PERCENTAGE_POINT_CHANGE}
    change = series.changes[0]
    assert change.value == pytest.approx(-1.0)
    assert change.unit == "pp"


def test_percent_change_is_not_emitted_for_a_zero_base() -> None:
    facts = [fact("net_income", 0, "2024"), fact("net_income", 500, "2025")]
    series = build_financial_history(facts).series[0]
    assert CALC_PERCENT_CHANGE not in {c.calculation for c in series.changes}


def test_every_derived_change_carries_its_inputs_and_formula() -> None:
    series = build_financial_history(_revenue_5y()).series[0]
    assert series.changes
    for change in series.changes:
        assert change.from_period and change.to_period
        assert change.from_value is not None and change.to_value is not None
        assert change.formula
        assert change.provenance == "derived"


def test_no_forecast_vocabulary_reaches_a_series_line() -> None:
    lines = " ".join(history_evidence_lines(build_financial_history(_revenue_5y())))
    for banned in (
        "forecast",
        "projected",
        "target",
        "upside",
        "downside",
        "expected return",
        "estimate",
    ):
        assert banned not in lines.lower()


def test_unrecognised_metrics_are_ignored_rather_than_charted() -> None:
    facts = [fact("shoe_size", 42, "2025"), *_revenue_5y()]
    history = build_financial_history(facts)
    assert {s.metric for s in history.series} == {"revenue"}


# =========================================================================== #
# Council propagation                                                         #
# =========================================================================== #


def _fact_item(metric: str, value: float, period: str, *, confidence: str) -> object:
    return build_evidence_item(
        id=f"IRFACT-{metric}-{period}",
        source_id="company_ir",
        source_name="Issuer IR",
        provider_transport="issuer website",
        provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
        content_source="Annual Report",
        content_source_tier=T1_PRIMARY_FILING,
        source_type="company_ir_financial_fact",
        title=f"Annual Report: {metric}",
        url="https://issuer.test/ar.pdf",
        date=period,
        excerpt=f"{metric} = {value}",
        scope="group",
        data_quality="B",
        confidence=confidence,
        fields_supported=[metric],
        primary_fact=PrimaryFactRef(
            field=metric,
            value=str(value),
            numeric_value=value,
            currency="DKK",
            scale="million",
            period=period,
            scope="group",
            confidence=confidence,
        ),
    )


def test_medium_confidence_facts_reach_the_series_but_not_a_canonical_slot() -> None:
    items = [
        _fact_item("revenue", 23400, "2021", confidence="medium"),
        _fact_item("revenue", 32549, "2025", confidence="high"),
    ]
    facts = _facts_from_connector_evidence(items)
    assert len(facts) == 2
    series = build_financial_history(facts).series[0]
    assert [p.value for p in series.points] == [23400.0, 32549.0]


def test_low_confidence_facts_are_excluded_from_the_series() -> None:
    items = [_fact_item("revenue", 1, "2025", confidence="low")]
    assert _facts_from_connector_evidence(items) == []


def test_council_pack_carries_one_dense_line_per_series_not_fifty_facts() -> None:
    """The design constraint: ~50 period-scoped facts must not become ~50
    evidence items and crowd out every other kind of evidence."""
    items = [
        _fact_item(metric, float(1000 + i), str(year), confidence="high")
        for metric in ("revenue", "operating_profit", "net_income")
        for i, year in enumerate(range(2021, 2026))
    ]
    builder = _Builder(max_items=40)
    _add_historical_series(builder, items, max_lines=8, max_periods=5)
    assert len(builder.items) == 3
    for item in builder.items:
        assert item.source_type == "historical_financial_series"
        assert item.excerpt is not None
        assert "FY2021" in item.excerpt and "FY2025" in item.excerpt
        assert "Group" in item.excerpt


def test_council_pack_history_respects_the_line_bound() -> None:
    items = [
        _fact_item(metric, float(100 + i), str(year), confidence="high")
        for metric in (
            "revenue",
            "operating_profit",
            "net_income",
            "total_assets",
            "total_equity",
            "free_cash_flow",
        )
        for i, year in enumerate(range(2021, 2026))
    ]
    builder = _Builder(max_items=40)
    _add_historical_series(builder, items, max_lines=2, max_periods=5)
    assert len(builder.items) == 2


def test_council_pack_history_is_a_no_op_without_multi_period_facts() -> None:
    builder = _Builder(max_items=40)
    _add_historical_series(
        builder, [_fact_item("revenue", 1, "2025", confidence="high")],
        max_lines=8, max_periods=5,
    )
    assert builder.items == []


# =========================================================================== #
# Report section                                                              #
# =========================================================================== #


def test_report_section_is_present_and_honest_when_empty() -> None:
    section = _build_historical_trends([])
    assert section["type"] == "historical_trends"
    assert section["available"] is False
    assert section["series"]["value"] == []
    assert section["series"]["provenance"] == "missing_data"
    assert "None" not in str(section["note"]["value"])


def test_report_section_renders_a_five_year_series_with_provenance() -> None:
    section = _build_historical_trends(_revenue_5y())
    assert section["available"] is True
    assert section["series_count"] == 1
    row = section["series"]["value"][0]
    assert row["metric"] == "revenue"
    assert row["scope"] == "Group"
    assert row["scope_type"] == "group"
    assert row["period_type"] == PERIOD_TYPE_ANNUAL
    assert row["unit"] == "DKK million"
    assert [p["period"] for p in row["periods"]] == [
        "FY2021", "FY2022", "FY2023", "FY2024", "FY2025"
    ]
    assert all(p["source_url"] for p in row["periods"])
    assert all(p["page_number"] == 14 for p in row["periods"])
    assert row["comparability"] == COMPARABLE


def test_report_section_keeps_group_and_segment_rows_apart() -> None:
    facts = _revenue_5y() + [
        fact("revenue", v, str(y), scope="Jewellery Maisons")
        for y, v in zip(range(2024, 2026), [15000, 16000])
    ]
    rows = _build_historical_trends(facts)["series"]["value"]
    scopes = {r["scope"] for r in rows}
    assert scopes == {"Group", "Jewellery Maisons"}
    group_row = next(r for r in rows if r["scope"] == "Group")
    assert group_row["periods"][-1]["value"] == 32549


def test_report_section_omits_a_single_observation_pseudo_series() -> None:
    rows = _build_historical_trends([fact("revenue", 32549, "2025")])["series"]["value"]
    assert rows == []


def test_report_section_records_why_there_is_no_trend() -> None:
    section = _build_historical_trends([fact("revenue", 1, "2025", scope=None)])
    assert section["not_series_reasons"]["value"] == {REASON_SCOPE_UNKNOWN: 1}


def test_report_section_never_claims_publication_readiness() -> None:
    section = _build_historical_trends(_revenue_5y())
    assert section["human_review_required"] is True
    text = str(section).lower()
    for banned in ("buy", "sell", "price target", "fair value", "upside"):
        assert banned not in text


# =========================================================================== #
# Live-acceptance correctives (2026-08-26)                                    #
# =========================================================================== #


def test_the_series_sees_every_extracted_fact_not_only_the_capped_items() -> None:
    """Found by LIVE acceptance, not by a unit test.

    The council pack derived its series from the EVIDENCE ITEMS, and those are
    capped per document (``primary_document_evidence_cap``, default 10) so a
    rich document cannot flood the prompt. That cap is right for the prompt and
    fatal for a series: the real Pandora annual report yields 52 period-scoped
    facts covering FY2021-FY2025, of which only ~10 became items — so every
    metric arrived as a single FY2025 observation and the live report said
    "no multi-period financial series was reconstructed" while the database held
    five years of them for nine metrics.

    The complete set now reaches the series builder explicitly; the prompt stays
    bound because a series is still ONE dense line.
    """
    complete = [
        fact(metric, float(1000 + i), str(year))
        for metric in ("revenue", "operating_profit", "net_income")
        for i, year in enumerate(range(2021, 2026))
    ]
    # Only two of those fifteen facts survived the per-document cap.
    capped_items = [
        _fact_item("revenue", 32549.0, "2025", confidence="high"),
        _fact_item("operating_profit", 7783.0, "2025", confidence="high"),
    ]

    builder = _Builder(max_items=40)
    _add_historical_series(builder, capped_items, max_lines=8, max_periods=5)
    assert builder.items == [], "capped items alone cannot support a trend"

    builder = _Builder(max_items=40)
    _add_historical_series(
        builder, capped_items, max_lines=8, max_periods=5, historical_facts=complete
    )
    assert len(builder.items) == 3
    for item in builder.items:
        assert "FY2021" in (item.excerpt or "") and "FY2025" in (item.excerpt or "")


def test_a_pandora_shaped_five_year_fact_set_yields_a_full_series() -> None:
    """The exact live shape: nine metrics x five years, Group-scoped."""
    metrics = {
        "revenue": (23400, 26463, 28133, 31673, 32549),
        "operating_profit": (5510, 6395, 6871, 7749, 7783),
        "net_income": (4142, 4665, 4864, 5343, 5241),
        "operating_cash_flow": (6100, 6300, 6500, 7000, 7361),
        "free_cash_flow": (4100, 4300, 4500, 4800, 5022),
        "total_assets": (25000, 26000, 27000, 28500, 29603),
        "total_equity": (4000, 4200, 4500, 5000, 5282),
        "net_debt": (9000, 10000, 11000, 12500, 13719),
        "employees": (30533, 34299, 37142, 41326, 42281),
    }
    facts = [
        fact(
            metric,
            float(value),
            str(year),
            currency=None if metric == "employees" else "DKK",
            scale=None if metric == "employees" else "million",
        )
        for metric, values in metrics.items()
        for year, value in zip(range(2021, 2026), values)
    ]
    history = build_financial_history(facts)
    assert len(history.series) == len(metrics)
    assert all(s.period_count == 5 for s in history.series)
    assert all(s.comparability == COMPARABLE for s in history.series)
    revenue = history.for_metric("revenue")[0]
    assert revenue.points[-1].value == 32549.0
    assert revenue.points[0].value == 23400.0
    # And the council would be told the direction, not just the endpoints.
    assert any(c.calculation == CALC_PERCENT_CHANGE for c in revenue.changes)
