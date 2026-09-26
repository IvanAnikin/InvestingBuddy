"""V3.19.3 — constraints are verified from evidence, eligibility is deterministic, and a
legacy report is never current research.

Mutation guards (spec §10):
* size hallucination      — a requested size never becomes a pass without a verified cap;
* growth hallucination    — price momentum cannot even be expressed as a growth input;
* stale report reuse      — a legacy report never classifies as current.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.discovery import constraints as c
from app.services.discovery.freshness import (
    INFERRED_V3_18,
    LEGACY,
    V3_CURRENT,
    V3_STALE,
    classify_report,
)
from app.services.discovery.fx import (
    FxRate,
    normalise_currency,
    parse_fred_csv,
    rate_from_rows,
    rate_on_or_before,
    to_usd,
    usd_rate,
)
from app.services.discovery.intent import HARD, SOFT, Constraint, build_intent

SMALL_HARD = Constraint("size", ("small_cap",), HARD, "default", "small cap")
SMALL_SOFT = Constraint("size", ("micro_cap", "small_cap", "mid_cap"), SOFT, "comparative", "smaller")
GROWING_HARD = Constraint("growth", ("growing",), HARD, "default", "growing")
EUR = FxRate("EUR", 1.15, "2026-09-18", "DEXUSEU", "https://fred.stlouisfed.org/x")


def _cap(amount, currency="EUR", verified=True):
    return c.MarketCapObservation(
        amount=amount, currency=currency, as_of="2026-09-20", as_of_basis="stated",
        source_url="https://example.org/q", source_tier="T5_api_aggregator", verified=verified,
    )


# ── FX ─────────────────────────────────────────────────────────────────────── #

CSV = "observation_date,DEXUSEU\n2026-09-16,1.1538\n2026-09-17,.\n2026-09-18,1.1464\n"


def test_fred_csv_parse_skips_missing_values():
    rows = parse_fred_csv(CSV)
    assert rows == [(date(2026, 9, 16), 1.1538), (date(2026, 9, 18), 1.1464)]


def test_rate_is_never_taken_from_after_the_date():
    rows = parse_fred_csv(CSV)
    assert rate_on_or_before(rows, date(2026, 9, 17)) == (date(2026, 9, 16), 1.1538)
    assert rate_on_or_before(rows, date(2026, 9, 15)) is None


def test_direction_of_units_per_usd_series():
    rows = [(date(2026, 9, 18), 0.80)]  # CHF per USD
    rate = rate_from_rows("CHF", rows, date(2026, 9, 20))
    assert rate.usd_per_unit == pytest.approx(1.25)
    assert rate_from_rows("EUR", [(date(2026, 9, 18), 1.15)], date(2026, 9, 20)).usd_per_unit == 1.15


def test_pence_are_converted_to_pounds():
    assert normalise_currency("GBX") == ("GBP", 100.0)
    gbp = FxRate("GBP", 1.30, "2026-09-18", "DEXUSUK", "u")
    assert to_usd(100_000, "GBX", gbp) == pytest.approx(1_300)


@pytest.mark.asyncio
async def test_unmapped_currency_is_unknown_not_guessed():
    rate, reason = await usd_rate("XYZ")
    assert rate is None and "no official FX series" in reason


@pytest.mark.asyncio
async def test_usd_rate_uses_the_fetcher_once_per_day():
    calls = []

    async def fetcher(url, **kw):
        calls.append(url)
        return SimpleNamespace(ok=True, content=b"observation_date,DEXDNUS\n2026-09-18,6.5\n")

    from app.services.discovery import fx

    fx._CACHE.clear()
    a, _ = await usd_rate("DKK", date(2026, 9, 20), fetcher=fetcher)
    b, _ = await usd_rate("DKK", date(2026, 9, 20), fetcher=fetcher)
    assert a.usd_per_unit == pytest.approx(1 / 6.5) and b == a
    assert len(calls) == 1


# ── size ───────────────────────────────────────────────────────────────────── #


def test_large_company_fails_a_small_cap_request():
    result = c.verify_size(SMALL_HARD, _cap(27_390e6), EUR)  # ≈ $31.5bn
    assert result.status == c.FAIL
    assert result.value["bucket"] == "large_cap"
    assert result.sources


def test_small_company_passes():
    result = c.verify_size(SMALL_HARD, _cap(900e6), EUR)
    assert result.status == c.PASS and result.value["bucket"] == "small_cap"


def test_size_hallucination_requested_size_is_not_a_pass_without_evidence():
    """MUTATION GUARD: no verified market cap → unknown, never the requested bucket."""
    assert c.verify_size(SMALL_HARD, None, EUR).status == c.UNKNOWN
    assert c.verify_size(SMALL_HARD, _cap(900e6, verified=False), EUR).status == c.UNKNOWN
    assert c.verify_size(SMALL_HARD, _cap(900e6), None).status == c.UNKNOWN


def test_a_pass_cannot_be_constructed_without_value_and_source():
    with pytest.raises(ValueError):
        c.ConstraintResult("size", ["small_cap"], HARD, c.PASS)


def test_borderline_is_flagged():
    result = c.verify_size(SMALL_HARD, _cap(1_800e6 / 1.15), EUR)  # $1.8bn, near $2bn
    assert result.borderline


def test_bands_are_the_single_source_for_deep_research():
    from app.services.director.thesis import SIZE_BANDS

    assert SIZE_BANDS["small_cap"] == c.SIZE_BANDS_USD["small_cap"]


# ── growth ─────────────────────────────────────────────────────────────────── #


def _growth(metric, pct, verified=True):
    return c.GrowthObservation(metric, pct, "FY2025", "FY2024", "https://ir.example/ar.pdf",
                               "T1_primary_filing", verified)


def test_growth_is_not_momentum():
    """MUTATION GUARD: a share-price return cannot be expressed as a growth input."""
    for metric in ("price_return", "momentum", "return_3m", "share_price_growth"):
        with pytest.raises(ValueError, match="share-price"):
            c.GrowthObservation(metric, 25.0, None, None, None, None, True)


def test_organic_growth_is_preferred_and_established():
    status, chosen, basis = c.classify_growth(
        [_growth("reported_revenue_growth", 3.0), _growth("organic_revenue_growth", 7.0)]
    )
    assert status == c.GROWTH_ESTABLISHED and chosen.metric == "organic_revenue_growth"


def test_mixed_when_organic_and_reported_disagree():
    status, _, _ = c.classify_growth(
        [_growth("reported_revenue_growth", -2.0), _growth("organic_revenue_growth", 4.0)]
    )
    assert status == c.GROWTH_MIXED


def test_unverified_growth_is_not_established():
    status, _, _ = c.classify_growth([_growth("organic_revenue_growth", 9.0, verified=False)])
    assert status == c.GROWTH_NOT_ESTABLISHED
    assert c.verify_growth(GROWING_HARD, [_growth("organic_revenue_growth", 9.0, False)]).status == c.UNKNOWN


def test_declining_fails_a_hard_growth_request():
    assert c.verify_growth(GROWING_HARD, [_growth("reported_revenue_growth", -6.0)]).status == c.FAIL


def test_revenue_pair():
    obs = c.growth_from_revenue_pair(110.0, 100.0, period="FY2025", base_period="FY2024",
                                     source_url="u", source_tier="T1", verified=True)
    assert obs.growth_pct == pytest.approx(10.0)
    assert c.growth_from_revenue_pair(1.0, 0.0, period=None, base_period=None,
                                      source_url=None, source_tier=None, verified=True) is None


def test_high_growth_threshold():
    high = Constraint("growth", ("high_growth",), HARD, "d", "high growth")
    assert c.verify_growth(high, [_growth("organic_revenue_growth", 8.0)]).status == c.FAIL
    assert c.verify_growth(high, [_growth("organic_revenue_growth", 22.0)]).status == c.PASS


# ── geography / industry ───────────────────────────────────────────────────── #


def test_geography_union_and_failure():
    intent = build_intent("smaller companies in Europe and Australia developing gallium")
    src = {"url": "https://www.asx.com.au/x", "tier": "exchange"}
    assert c.verify_geography(intent.geography, listing_country="Australia",
                              listing_source=src).status == c.PASS
    assert c.verify_geography(intent.geography, listing_country="France",
                              listing_source=src).status == c.PASS
    assert c.verify_geography(intent.geography, listing_country="United States",
                              listing_source=src).status == c.FAIL


def test_industry_denial_is_not_exposure():
    constraint = Constraint("industry", ("gallium",), HARD, "d", "gallium")
    denied = c.ExposureObservation("gallium", c.EXPOSURE_DENIED, "no gallium is produced",
                                   "u", "T1", True)
    assert c.verify_industry(constraint, [denied]).status == c.FAIL
    direct = c.ExposureObservation("gallium", c.EXPOSURE_DIRECT, "produces gallium",
                                   "u", "T1", True)
    assert c.verify_industry(constraint, [direct]).status == c.PASS
    unverified = c.ExposureObservation("gallium", c.EXPOSURE_DIRECT, "produces gallium",
                                       "u", "T5", False)
    assert c.verify_industry(constraint, [unverified]).status == c.UNKNOWN


# ── eligibility ────────────────────────────────────────────────────────────── #


def _listing_ok():
    return c.verify_listing({"identity_status": "verified", "ticker": "X", "exchange": "PA",
                             "listing_source": {"url": "https://live.euronext.com/x",
                                                "tier": "exchange"}})


def test_hard_fail_excludes_and_council_cannot_readmit():
    results = [_listing_ok(), c.verify_size(SMALL_HARD, _cap(27_390e6), EUR)]
    elig = c.decide_eligibility(results)
    assert elig.status == c.EXCLUDED
    assert "size" in elig.reasons[0]


def test_unverified_identity_excludes():
    elig = c.decide_eligibility([c.verify_listing({"identity_status": "rejected",
                                                   "rejection_reason": "no_listing_evidence"})])
    assert elig.status == c.EXCLUDED and "identity_unverified" in elig.reasons[0]


def test_hard_unknown_is_eligible_unverified_not_eligible():
    elig = c.decide_eligibility([_listing_ok(), c.verify_size(SMALL_HARD, None, EUR)])
    assert elig.status == c.ELIGIBLE_UNVERIFIED and elig.unknown_hard == ["size"]


def test_soft_fail_is_included_with_mismatch():
    elig = c.decide_eligibility([_listing_ok(), c.verify_size(SMALL_SOFT, _cap(27_390e6), EUR)])
    assert elig.status == c.INCLUDED_WITH_MISMATCH


def test_ranking_puts_eligible_first():
    elig = c.decide_eligibility([_listing_ok(), c.verify_size(SMALL_HARD, _cap(900e6), EUR)])
    unver = c.decide_eligibility([_listing_ok(), c.verify_size(SMALL_HARD, None, EUR)])
    assert c.ranking_key(elig) < c.ranking_key(unver)


def test_verified_attributes_never_contain_a_requested_but_unverified_size():
    """MUTATION GUARD (attribute leakage): the requested bucket is not an attribute."""
    attrs = c.verified_attributes([c.verify_size(SMALL_HARD, None, EUR)])
    assert "size_bucket" not in attrs
    attrs = c.verified_attributes([c.verify_size(SMALL_HARD, _cap(27_390e6), EUR)])
    assert attrs["size_bucket"] == "large_cap"


# ── freshness ──────────────────────────────────────────────────────────────── #

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _report(*, v3=None, days=10, version="1.0"):
    return SimpleNamespace(
        id="r1", created_at=NOW - timedelta(days=days), final_report_version=version,
        content_markdown=None,
        source_summary_json=({"v3_research": v3} if v3 is not None else {"llm_council": {}}),
    )


def test_legacy_report_is_not_current():
    """MUTATION GUARD: a V1/V2 report is never current, however recent."""
    freshness = classify_report(_report(days=1), now=NOW)
    assert freshness.status == LEGACY and not freshness.is_current


def test_v3_without_professional_block_is_legacy():
    freshness = classify_report(_report(v3={"findings": []}, days=1), now=NOW)
    assert freshness.status == LEGACY


def test_professional_report_is_current_then_stale():
    v3 = {"professional_research": {"sections": []}}
    fresh = classify_report(_report(v3=v3, days=12), now=NOW)
    assert fresh.status == V3_CURRENT and fresh.research_engine_version == INFERRED_V3_18
    stale = classify_report(_report(v3=v3, days=200), now=NOW)
    assert stale.status == V3_STALE


def test_newer_annual_period_makes_it_stale():
    v3 = {"professional_research": {"sections": []}, "research_engine_version": "v3.19"}
    report = _report(v3=v3, days=5)
    report.content_markdown = (
        '```json\n{"financial_snapshot": {"reporting_periods": {"latest_annual": "FY2024"}}}\n```'
    )
    assert classify_report(report, now=NOW, newer_annual_period="FY2025").status == V3_STALE
    assert classify_report(report, now=NOW).research_engine_version == "v3.19"


def test_excluded_geography_fails_even_inside_a_requested_region():
    intent = build_intent("Europe excluding UK defence companies")
    src = {"url": "https://www.londonstockexchange.com/x", "tier": "exchange"}
    assert c.verify_geography(intent.geography, listing_country="United Kingdom",
                              listing_source=src).status == c.FAIL
    assert c.verify_geography(intent.geography, listing_country="France",
                              listing_source=src).status == c.PASS


def test_exclusion_only_geography_passes_anywhere_else():
    intent = build_intent("non-US defence companies")
    src = {"url": "https://live.euronext.com/x", "tier": "exchange"}
    assert c.verify_geography(intent.geography, listing_country="France",
                              listing_source=src).status == c.PASS
    assert c.verify_geography(intent.geography, listing_country="United States",
                              listing_source=src).status == c.FAIL
