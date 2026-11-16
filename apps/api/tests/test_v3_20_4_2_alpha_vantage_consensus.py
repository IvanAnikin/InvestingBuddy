"""V3.20 slice 4.2 — Alpha Vantage consensus snapshots (ADR-097).

Fixtures follow the shape of the public ``apikey=demo`` IBM responses read on
2026-11-16 (strings for numbers, ``null`` / ``"None"`` for missing values); the
figures are made up.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.integrations import alpha_vantage as av
from app.models.consensus import (
    HORIZON_QUARTER,
    HORIZON_REPORTED,
    HORIZON_YEAR,
    ConsensusSnapshot,
    VendorApiCall,
)
from app.models.earnings import EarningsResult
from app.services.pipeline import consensus_step as step
from app.services.pipeline.consensus_step import consensus_snapshot_step  # real (conftest stubs)
from tests import test_v3_entity_relationships as _base

session = _base.session

KEY = "TESTKEY-not-a-real-key"
TODAY = date(2026, 11, 16)

EARNINGS = {
    "symbol": "POWL",
    "annualEarnings": [{"fiscalDateEnding": "2026-09-30", "reportedEPS": "7.79"}],
    "quarterlyEarnings": [
        {"fiscalDateEnding": "2026-06-30", "reportedDate": "2026-08-04", "reportedEPS": "3.10",
         "estimatedEPS": "2.80", "surprise": "0.30", "surprisePercentage": "10.7143",
         "reportTime": "post-market"},
        {"fiscalDateEnding": "2026-03-31", "reportedDate": "2026-05-05", "reportedEPS": "2.50",
         "estimatedEPS": "None", "surprise": "None", "surprisePercentage": "None",
         "reportTime": "pre-market"},
        {"fiscalDateEnding": "2026-03-31", "reportedDate": "2026-05-05", "reportedEPS": "9.99",
         "estimatedEPS": "9.99", "surprise": "0", "surprisePercentage": "0",
         "reportTime": "post-market"},
    ],
}


def _est(end: str, horizon: str, eps: str | None, n: str | None, rev: str | None) -> dict[str, Any]:
    return {"date": end, "horizon": horizon, "eps_estimate_average": eps,
            "eps_estimate_high": None, "eps_estimate_low": None,
            "eps_estimate_analyst_count": n, "eps_estimate_average_7_days_ago": eps,
            "eps_estimate_average_30_days_ago": "2.9000",
            "eps_estimate_average_60_days_ago": None, "eps_estimate_average_90_days_ago": None,
            "eps_estimate_revision_up_trailing_7_days": "0.0000",
            "eps_estimate_revision_down_trailing_7_days": None,
            "eps_estimate_revision_up_trailing_30_days": "3.0000",
            "eps_estimate_revision_down_trailing_30_days": "1.0000",
            "revenue_estimate_average": rev, "revenue_estimate_high": None,
            "revenue_estimate_low": None, "revenue_estimate_analyst_count": "4.00"}


ESTIMATES = {"symbol": "POWL", "estimates": [
    _est("2027-09-30", "fiscal year", "13.2000", "5.0000", "1300000000.00"),
    _est("2026-12-31", "fiscal quarter", "3.0500", "4.0000", "330000000.00"),   # not ended
    _est("2026-09-30", "fiscal quarter", "2.9500", "4.0000", "320000000.00"),   # reported 11-18
    _est("2026-06-30", "fiscal quarter", "2.8000", "4.0000", "310000000.00"),   # reported 08-04
    _est("2026-03-31", "fiscal quarter", None, None, None),                     # no figures
    _est("2026-06-30", "fiscal quarter", "1.0000", "1.0000", "1.00"),           # duplicate
    _est("2026-06-30", "next fiscal year", "1.0", "1", "1"),                    # unknown horizon
]}


def _fetcher(responses: dict[str, Any], calls: list[str]):  # noqa: ANN202
    async def fetch(url: str, accept: str) -> tuple[int, bytes | None]:
        calls.append(url)
        for function, payload in responses.items():
            if f"function={function}&" in url:
                return 200, json.dumps(payload).encode()
        return 404, None

    return fetch


async def _company(session: Any, *, exchange: str = "NASDAQ", ticker: str | None = None) -> Any:
    from app.models.company import Company

    company = Company(id=uuid.uuid4(),
                      ticker=ticker or "P" + uuid.uuid4().hex[:5].upper().replace("0", "Q"),
                      exchange=exchange, name="Powell", status="new")
    session.add(company)
    await session.flush()
    return company


def _cfg(**kw: Any) -> SimpleNamespace:
    base = {"v3_consensus_snapshots_enabled": True, "alpha_vantage_api_key": KEY}
    return SimpleNamespace(**{**base, **kw})


def _open_budget():  # noqa: ANN202
    async def take(_n: int) -> bool:
        return True

    return take


# --------------------------------------------------------------------------- parsers


def test_parse_earnings_reads_provider_basis_and_dedups() -> None:
    rows = av.parse_earnings(EARNINGS)
    assert [r.period_end for r in rows] == [date(2026, 6, 30), date(2026, 3, 31)]
    q2 = rows[0]
    assert q2.reported_eps == Decimal("3.10") and q2.estimated_eps == Decimal("2.80")
    assert q2.surprise == Decimal("0.30") and q2.report_time == "post-market"
    assert q2.reported_date == date(2026, 8, 4)
    q1 = rows[1]
    # "None" strings are missing values, not zero; the duplicate period keeps the first row.
    assert q1.estimated_eps is None and q1.surprise is None and q1.reported_eps == Decimal("2.50")


def test_parse_estimates_keeps_quarter_and_year_rows_with_figures() -> None:
    rows = av.parse_estimates(ESTIMATES)
    keys = [(r.horizon, r.period_end) for r in rows]
    assert keys == [("fiscal year", date(2027, 9, 30)), ("fiscal quarter", date(2026, 12, 31)),
                    ("fiscal quarter", date(2026, 9, 30)), ("fiscal quarter", date(2026, 6, 30))]
    q = rows[1]
    assert q.eps_avg == Decimal("3.0500") and q.eps_analyst_count == 4
    assert q.revenue_avg == Decimal("330000000.00") and q.revenue_analyst_count == 4
    assert q.eps_high is None and q.eps_avg_60d_ago is None
    assert q.eps_revisions_up_30d == 3 and q.eps_revisions_down_30d == 1
    # The duplicate 2026-06-30 row did not overwrite the first.
    assert rows[3].eps_avg == Decimal("2.8000")


@pytest.mark.parametrize(("payload", "reason"), [
    ({"Information": "We have detected your API key as ... 25 requests per day."},
     "vendor_rate_limited"),
    ({"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 5 per minute"},
     "vendor_rate_limited"),
    ({"Information": "This is a premium endpoint."}, "vendor_premium_only"),
    ({"Error Message": "Invalid API call."}, "vendor_rejected_request"),
    ({"Information": "Something else"}, "vendor_information_response"),
    ([], "unreadable_response"),
])
def test_refusals_are_reasons_not_data(payload: Any, reason: str) -> None:
    assert av.refusal_reason(payload) == reason
    with pytest.raises(av.AlphaVantageError) as err:
        av.parse_earnings(payload)
    assert err.value.reason == reason
    with pytest.raises(av.AlphaVantageError):
        av.parse_estimates(payload)


def test_public_url_never_carries_the_key() -> None:
    assert "apikey" not in av.public_url("EARNINGS", "POWL")
    assert av.public_url("EARNINGS", "POWL") == (
        "https://www.alphavantage.co/query?function=EARNINGS&symbol=POWL")
    assert f"apikey={KEY}" in av.request_url("EARNINGS", "POWL", KEY)


async def test_fetch_refuses_without_key_and_hides_transport_errors() -> None:
    calls: list[str] = []
    with pytest.raises(av.AlphaVantageError) as err:
        await av.fetch_function("EARNINGS", "POWL", api_key="", fetch=_fetcher({}, calls))
    assert err.value.reason == "no_api_key" and calls == []

    async def boom(url: str, accept: str) -> tuple[int, bytes | None]:
        raise RuntimeError(f"connection reset while fetching {url}")

    with pytest.raises(av.AlphaVantageError) as err:
        await av.fetch_function("EARNINGS", "POWL", api_key=KEY, fetch=boom)
    assert err.value.reason == "vendor_unreachable"
    assert KEY not in str(err.value) and err.value.__cause__ is None


@pytest.mark.parametrize(("exchange", "ticker", "expected"), [
    ("NASDAQ", "POWL", "POWL"), ("NYSE", "BW", "BW"), ("XNYS", "BRK.B", "BRK.B"),
    ("LSE", "POWL", None), ("", "POWL", None), ("US", "POWL", None), ("OTC", "POWL", None),
    ("NASDAQ", "powl$", None),
])
def test_us_listed_symbol(exchange: str, ticker: str, expected: str | None) -> None:
    assert step.us_listed_symbol(SimpleNamespace(exchange=exchange, ticker=ticker)) == expected


def test_pre_report_rule() -> None:
    assert step.pre_report(date(2026, 9, 30), TODAY, date(2026, 11, 18)) is True
    assert step.pre_report(date(2026, 6, 30), TODAY, date(2026, 8, 4)) is False
    assert step.pre_report(date(2026, 9, 30), date(2026, 11, 18), date(2026, 11, 18)) is False
    assert step.pre_report(date(2026, 12, 31), TODAY, None) is True
    assert step.pre_report(date(2026, 9, 30), TODAY, None) is None


# --------------------------------------------------------------------------- budget


async def test_budget_is_shared_and_capped(session) -> None:  # noqa: ANN001
    for _ in range(12):
        assert await step.take_budget(session, calls=2, day=TODAY)
    assert not await step.take_budget(session, calls=2, day=TODAY)  # 24 + 2 > 25
    assert await step.take_budget(session, calls=1, day=TODAY)       # 25
    assert not await step.take_budget(session, calls=1, day=TODAY)
    assert await step.take_budget(session, calls=2, day=date(2026, 11, 17))  # a new day
    used = (await session.execute(select(VendorApiCall.calls).where(
        VendorApiCall.day == TODAY))).scalar_one()
    assert used == 25


# --------------------------------------------------------------------------- step


async def test_no_key_makes_no_call(session) -> None:  # noqa: ANN001
    company = await _company(session)
    calls: list[str] = []
    out = await consensus_snapshot_step(
        session, company, cfg=_cfg(alpha_vantage_api_key=""),
        fetch=_fetcher({}, calls), budget=_open_budget(), today=TODAY)
    assert out["state"] == "no_api_key" and out["consensus_status"] == "source_unavailable"
    assert calls == []
    disabled = await consensus_snapshot_step(
        session, company, cfg=_cfg(v3_consensus_snapshots_enabled=False), today=TODAY)
    assert disabled == {"state": "disabled"}


async def test_non_us_listing_makes_no_call(session) -> None:  # noqa: ANN001
    company = await _company(session, exchange="ASX")
    calls: list[str] = []
    out = await consensus_snapshot_step(session, company, cfg=_cfg(),
                                             fetch=_fetcher({}, calls),
                                             budget=_open_budget(), today=TODAY)
    assert out["state"] == "not_us_listed" and calls == []


async def test_stores_snapshots_with_pre_report_flags(session) -> None:  # noqa: ANN001
    company = await _company(session)
    # SEC actuals (slice 4.1) know Q3 FY2026 is reported on 2026-11-18 — after today.
    session.add(EarningsResult(id=uuid.uuid4(), company_id=company.id,
                               fiscal_period_label="2026-Q4", fiscal_year=2026,
                               fiscal_quarter=4, period_end=date(2026, 9, 30),
                               report_date=date(2026, 11, 18)))
    await session.flush()
    calls: list[str] = []
    fetch = _fetcher({"EARNINGS": EARNINGS, "EARNINGS_ESTIMATES": ESTIMATES}, calls)
    out = await consensus_snapshot_step(session, company, cfg=_cfg(), fetch=fetch,
                                             budget=_open_budget(), today=TODAY)
    assert out["state"] == "stored", out
    assert out["estimates"] == 4 and out["reported_quarters"] == 2
    assert (out["pre_report"], out["post_report"], out["undetermined"]) == (3, 1, 0)
    assert len(calls) == 2 and all(f"apikey={KEY}" in c for c in calls)

    rows = (await session.execute(select(ConsensusSnapshot).where(
        ConsensusSnapshot.company_id == company.id))).scalars().all()
    by = {(r.horizon, r.period_end): r for r in rows}
    assert by[(HORIZON_YEAR, date(2027, 9, 30))].is_pre_report is True
    assert by[(HORIZON_QUARTER, date(2026, 12, 31))].is_pre_report is True
    sep = by[(HORIZON_QUARTER, date(2026, 9, 30))]
    assert sep.is_pre_report is True and sep.eps_avg == Decimal("2.9500")
    assert sep.detail_json["report_date_used"] == "2026-11-18"
    # Reported on 2026-08-04 (provider date) — read after it, so never an expectation.
    assert by[(HORIZON_QUARTER, date(2026, 6, 30))].is_pre_report is False
    rep = by[(HORIZON_REPORTED, date(2026, 6, 30))]
    assert rep.vendor_reported_eps == Decimal("3.10") and rep.eps_avg == Decimal("2.80")
    assert rep.vendor_surprise == Decimal("0.30") and rep.is_pre_report is False
    assert rep.detail_json["eps_basis"] == "provider"
    for r in rows:
        assert r.as_of == TODAY and r.source == "alpha_vantage" and r.symbol == company.ticker
        assert r.source_url and "apikey" not in r.source_url and KEY not in r.source_url

    # The same day again: cached, no call, no new rows.
    again = await consensus_snapshot_step(session, company, cfg=_cfg(), fetch=fetch,
                                               budget=_open_budget(), today=TODAY)
    assert again["state"] == "cached" and len(calls) == 2
    count = (await session.execute(select(func.count()).select_from(ConsensusSnapshot).where(
        ConsensusSnapshot.company_id == company.id))).scalar_one()
    assert count == len(rows)


async def test_budget_refusal_and_vendor_refusal_store_nothing(session) -> None:  # noqa: ANN001
    company = await _company(session)
    calls: list[str] = []

    async def refuse(_n: int) -> bool:
        return False

    out = await consensus_snapshot_step(session, company, cfg=_cfg(),
                                             fetch=_fetcher({}, calls), budget=refuse,
                                             today=TODAY)
    assert out["state"] == "budget_exhausted" and calls == []

    limited = {"Information": "... standard API rate limit is 25 requests per day ..."}
    out = await consensus_snapshot_step(
        session, company, cfg=_cfg(),
        fetch=_fetcher({"EARNINGS": limited, "EARNINGS_ESTIMATES": limited}, calls),
        budget=_open_budget(), today=TODAY)
    assert out["state"] == "vendor_error" and out["reason"] == "vendor_rate_limited"
    assert KEY not in json.dumps(out)
    count = (await session.execute(select(func.count()).select_from(ConsensusSnapshot).where(
        ConsensusSnapshot.company_id == company.id))).scalar_one()
    assert count == 0


# --------------------------------------------------------------------------- config / registry


def test_key_is_a_hidden_credential() -> None:
    from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings

    assert "alpha_vantage_api_key" in CREDENTIAL_SETTING_FIELDS
    assert Settings.model_fields["alpha_vantage_api_key"].repr is False
    assert KEY not in repr(Settings(alpha_vantage_api_key=KEY))


def test_registry_lists_alpha_vantage_as_private_use() -> None:
    from app.core.config import Settings
    from app.services.sources.registry import assert_registry_safe, build_registry

    reg = build_registry(Settings(alpha_vantage_api_key=KEY))
    src = reg.get("alpha_vantage")
    assert src is not None and src.enabled and "PRIVATE USE ONLY" in (src.reliability_note or "")
    assert_registry_safe(reg)
    assert KEY not in json.dumps([s.model_dump(mode="json") for s in reg.all_sources()])
