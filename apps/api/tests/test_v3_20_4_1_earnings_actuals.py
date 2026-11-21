"""V3.20 slice 4.1 — quarterly earnings actuals from SEC XBRL → ``earnings_results``.

Payload rows copy the shape (and, where stated, the values) of SEC ``companyconcept``
and ``submissions`` responses read live on 2026-11-15:

* Powell Industries (CIK 80420, fiscal year to 30 September): 2025-Q3 diluted EPS 3.96
  as filed on 2025-08-06, 1.32 in the 2026 Q3 10-Q after its share split; Q4 EPS 4.22
  tagged as a three-month fact in the FY2025 10-K; releases on 8-K Item 2.02.
* A June-year-end filer whose 10-K tags no three-month Q4: Q4 revenue derived as full
  year minus the Q3 10-Q's nine-month figure; Q4 EPS left empty.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.models.earnings import EarningsResult
from app.services.pipeline.earnings_step import earnings_actuals_step, record_actuals
from app.services.sources.sec_actuals import (
    BASIS_DERIVED_Q4,
    BASIS_REPORTED_YEAR,
    REPORT_DATE_8K,
    REPORT_DATE_PERIODIC,
    annual_actuals,
    concept_url,
    quarterly_actuals,
)
from tests import test_v3_entity_relationships as _base

session = _base.session


def _r(start: str | None, end: str, val: float, fy: int, fp: str, form: str, filed: str,
       accn: str) -> dict[str, Any]:
    row: dict[str, Any] = {"end": end, "val": val, "fy": fy, "fp": fp, "form": form,
                           "filed": filed, "accn": accn}
    if start:
        row["start"] = start
    return row


Q3_25, FY25, Q1_26, Q3_26 = ("0000080420-25-000060", "0000080420-25-000090",
                             "0000080420-26-000010", "0000080420-26-000107")

POWL_REVENUE = {"units": {"USD": [
    _r("2025-04-01", "2025-06-30", 286273000, 2025, "Q3", "10-Q", "2025-08-06", Q3_25),
    _r("2024-04-01", "2024-06-30", 288168000, 2025, "Q3", "10-Q", "2025-08-06", Q3_25),
    _r("2024-10-01", "2025-06-30", 806335000, 2025, "Q3", "10-Q", "2025-08-06", Q3_25),
    _r("2024-10-01", "2025-09-30", 1104318000, 2025, "FY", "10-K", "2025-11-19", FY25),
    _r("2025-10-01", "2025-12-31", 251184000, 2026, "Q1", "10-Q", "2026-02-04", Q1_26),
    _r("2026-04-01", "2026-06-30", 311740000, 2026, "Q3", "10-Q", "2026-08-04", Q3_26),
    _r("2025-04-01", "2025-06-30", 286273000, 2026, "Q3", "10-Q", "2026-08-04", Q3_26),
]}}
POWL_EPS = {"units": {"USD/shares": [
    _r("2025-04-01", "2025-06-30", 3.96, 2025, "Q3", "10-Q", "2025-08-06", Q3_25),
    _r("2025-07-01", "2025-09-30", 4.22, 2025, "FY", "10-K", "2025-11-19", FY25),
    _r("2024-10-01", "2025-09-30", 14.86, 2025, "FY", "10-K", "2025-11-19", FY25),
    _r("2025-10-01", "2025-12-31", 3.40, 2026, "Q1", "10-Q", "2026-02-04", Q1_26),
    _r("2026-04-01", "2026-06-30", 1.42, 2026, "Q3", "10-Q", "2026-08-04", Q3_26),
    _r("2025-04-01", "2025-06-30", 1.32, 2026, "Q3", "10-Q", "2026-08-04", Q3_26),
]}}
POWL_SUBMISSIONS = {"filings": {"recent": {
    "form": ["10-Q", "8-K", "8-K", "10-K", "8-K", "10-Q", "8-K"],
    "accessionNumber": [Q3_26, "8K-3", "8K-OTHER", FY25, "8K-2", Q3_25, "8K-1"],
    "filingDate": ["2026-08-04", "2026-08-03", "2026-07-06", "2025-11-19", "2025-11-18",
                   "2025-08-06", "2025-08-05"],
    "items": ["", "2.02,8.01,9.01", "5.02", "", "2.02,9.01", "", "2.02,9.01"],
}}}


class TestParse:
    def test_powell(self) -> None:
        rows = quarterly_actuals(
            {"RevenueFromContractWithCustomerExcludingAssessedTax": POWL_REVENUE}, POWL_EPS,
            POWL_SUBMISSIONS)
        by = {q.label: q for q in rows}
        assert [q.label for q in rows] == ["2026-Q3", "2026-Q1", "2025-Q4", "2025-Q3"]
        q3 = by["2025-Q3"]
        # As first reported, and the split-restated latest — both kept.
        assert q3.eps.value == Decimal("3.96") and q3.eps.latest == Decimal("1.32")
        assert q3.eps.restated is True
        assert q3.revenue.value == Decimal("286273000") and q3.revenue.restated is False
        # The comparative prior-year quarter in a 10-Q is not a quarter of its own.
        assert "2024-Q3" not in by
        q4 = by["2025-Q4"]
        assert q4.eps.value == Decimal("4.22")  # tagged three-month Q4 in the 10-K
        assert q4.revenue.basis == BASIS_DERIVED_Q4
        assert q4.revenue.value == Decimal("297983000")  # 1,104,318,000 - 806,335,000
        assert q4.period_start == date(2025, 7, 1) and q4.period_end == date(2025, 9, 30)
        # Report date: the 8-K Item 2.02 between quarter end and the periodic report.
        assert (q3.report_date, q3.report_date_source) == (date(2025, 8, 5), REPORT_DATE_8K)
        assert by["2026-Q3"].report_date == date(2026, 8, 3)  # not the 5.02 8-K
        # No release 8-K for Q1 FY2026 in the list: the 10-Q's filing date, labelled.
        assert (by["2026-Q1"].report_date, by["2026-Q1"].report_date_source) == (
            date(2026, 2, 4), REPORT_DATE_PERIODIC)

    def test_q4_eps_is_never_derived(self) -> None:
        eps = {"units": {"USD/shares": [
            _r("2025-01-01", "2025-03-31", 0.15, 2025, "Q3", "10-Q", "2025-05-06", "A"),
            _r("2024-07-01", "2025-03-31", 0.41, 2025, "Q3", "10-Q", "2025-05-06", "A"),
            _r("2024-07-01", "2025-06-30", 0.60, 2025, "FY", "10-K", "2025-08-13", "B"),
        ]}}
        revenue = {"units": {"USD": [
            _r("2024-07-01", "2025-03-31", 1000, 2025, "Q3", "10-Q", "2025-05-06", "A"),
            _r("2024-07-01", "2025-06-30", 1400, 2025, "FY", "10-K", "2025-08-13", "B"),
        ]}}
        rows = quarterly_actuals({"Revenues": revenue}, eps)
        q4 = next(q for q in rows if q.label == "2025-Q4")
        assert q4.revenue.value == Decimal("400") and q4.eps is None
        assert "q4_eps_not_tagged_as_a_quarter" in q4.notes

    def test_newest_alias_wins_per_quarter(self) -> None:
        stale = {"units": {"USD": [
            _r("2026-01-01", "2026-03-31", 1, 2026, "Q2", "10-Q", "2026-05-01", "OLD")]}}
        current = {"units": {"USD": [
            _r("2026-01-01", "2026-03-31", 2, 2026, "Q2", "10-Q", "2026-05-05", "NEW")]}}
        rows = quarterly_actuals({"Revenues": stale,
                                  "RevenueFromContractWithCustomerExcludingAssessedTax": current},
                                 None)
        assert rows[0].revenue.value == Decimal("2") and rows[0].revenue.accession == "NEW"

    def test_annual_rows_are_the_10k_full_year(self) -> None:
        (year,) = annual_actuals(
            {"RevenueFromContractWithCustomerExcludingAssessedTax": POWL_REVENUE}, POWL_EPS,
            POWL_SUBMISSIONS)
        assert (year.label, year.fiscal_quarter, year.is_annual) == ("2025", 0, True)
        assert year.revenue.value == Decimal("1104318000") and year.revenue.basis == \
            BASIS_REPORTED_YEAR
        assert year.eps.value == Decimal("14.86")
        assert (year.period_start, year.period_end) == (date(2024, 10, 1), date(2025, 9, 30))
        assert year.report_date == date(2025, 11, 18)
        # A nine-month YTD is never a year.
        assert annual_actuals({"Revenues": {"units": {"USD": [
            _r("2024-10-01", "2025-06-30", 9, 2025, "Q3", "10-Q", "2025-08-06", "A")]}}},
            None) == []

    def test_nothing_tagged(self) -> None:
        assert quarterly_actuals({}, None) == []


def _fetch(responses: dict[str, Any]):
    async def fetch(url: str, accept: str) -> tuple[int, bytes | None]:
        if url in responses:
            return 200, json.dumps(responses[url]).encode()
        return 404, None

    return fetch


async def _company(session, *, cik: str | None = "80420") -> Any:
    from app.models.company import Company
    from app.models.legal_entity import EntityIdentifier, LegalEntity

    entity = LegalEntity(id=uuid.uuid4(), entity_key=f"test:{uuid.uuid4()}", legal_name="Powell",
                         normalized_name="powell")
    session.add(entity)
    await session.flush()
    if cik:
        session.add(EntityIdentifier(id=uuid.uuid4(), legal_entity_id=entity.id, scheme="cik",
                                     value=cik, value_normalized=cik, scope_key="*",
                                     source="test"))
    company = Company(id=uuid.uuid4(), ticker=f"P{uuid.uuid4().hex[:6]}", exchange="NASDAQ", name="Powell",
                      status="new", legal_entity_id=entity.id)
    session.add(company)
    await session.flush()
    return company


CFG = SimpleNamespace(v3_earnings_actuals_enabled=True)
RESPONSES = {
    concept_url("80420", "RevenueFromContractWithCustomerExcludingAssessedTax"): POWL_REVENUE,
    concept_url("80420", "EarningsPerShareDiluted"): POWL_EPS,
    "https://data.sec.gov/submissions/CIK0000080420.json": POWL_SUBMISSIONS,
}


class TestStep:
    async def test_writes_and_upserts_without_touching_consensus(self, session) -> None:
        company = await _company(session)
        out = await earnings_actuals_step(session, company, cfg=CFG, fetch=_fetch(RESPONSES))
        assert out["state"] == "written" and (out["quarters"], out["years"]) == (4, 1)
        assert out["restated_eps_quarters"] == ["2025-Q3"]
        rows = {r.fiscal_period_label: r for r in (await session.execute(
            select(EarningsResult))).scalars()}
        q3 = rows["2025-Q3"]
        assert q3.actual_eps == Decimal("3.96") and q3.actual_eps_latest == Decimal("1.32")
        assert q3.eps_currency == "USD" and q3.revenue_currency == "USD"
        assert q3.eps_concept == "us-gaap:EarningsPerShareDiluted"
        assert q3.eps_source_url.startswith("https://www.sec.gov/Archives/edgar/data/80420/")
        assert q3.consensus_status is None and q3.consensus_eps_avg is None
        # A later consensus (4.2) is never overwritten by a re-run of the actuals.
        q3.consensus_status, q3.consensus_eps_avg = "available", Decimal("3.50")
        await session.flush()
        again = await earnings_actuals_step(session, company, cfg=CFG, fetch=_fetch(RESPONSES))
        assert again["state"] == "written"
        rows = (await session.execute(select(EarningsResult))).scalars().all()
        assert len(rows) == 5
        year = next(r for r in rows if r.fiscal_period_label == "2025")
        assert year.fiscal_quarter == 0 and year.actual_revenue == Decimal("1104318000")
        assert year.actual_eps == Decimal("14.86") and year.report_date == date(2025, 11, 18)
        kept = next(r for r in rows if r.fiscal_period_label == "2025-Q3")
        assert kept.consensus_status == "available" and kept.consensus_eps_avg == Decimal("3.5")

    async def test_states(self, session) -> None:
        company = await _company(session, cik=None)
        assert (await earnings_actuals_step(session, company, cfg=CFG,
                                            fetch=_fetch({})))["state"] == "no_cik"
        assert (await earnings_actuals_step(
            session, company, cfg=SimpleNamespace(v3_earnings_actuals_enabled=False)))[
            "state"] == "disabled"
        with_cik = await _company(session)
        assert (await earnings_actuals_step(session, with_cik, cfg=CFG,
                                            fetch=_fetch({})))["state"] == "no_quarters"

    async def test_http_error_is_a_state(self, session) -> None:
        company = await _company(session)

        async def broken(url: str, accept: str) -> tuple[int, bytes | None]:
            return 503, b"busy"

        out = await earnings_actuals_step(session, company, cfg=CFG, fetch=broken)
        assert out["state"] == "error" and "503" in out["error"]

    async def test_record_is_idempotent(self, session) -> None:
        company = await _company(session)
        rows = quarterly_actuals(
            {"RevenueFromContractWithCustomerExcludingAssessedTax": POWL_REVENUE}, POWL_EPS)
        assert await record_actuals(session, company_id=company.id, cik="80420",
                                    quarters=rows) == 4
        assert await record_actuals(session, company_id=company.id, cik="80420",
                                    quarters=rows) == 4
        assert len((await session.execute(select(EarningsResult))).scalars().all()) == 4
