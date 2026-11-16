"""V3.20 slice 4.4 — earnings surprises + the "Earnings track record" block (ADR-097)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.models.consensus import ConsensusSnapshot
from app.models.earnings import EarningsResult
from app.services.pipeline import earnings_track_record as etr
from tests import test_v3_entity_relationships as _base

session = _base.session


async def _company(session: Any) -> Any:
    from app.models.company import Company

    company = Company(id=uuid.uuid4(), ticker="P" + uuid.uuid4().hex[:5].upper(),
                      exchange="NASDAQ", name="Powell", status="new")
    session.add(company)
    await session.flush()
    return company


def _result(company_id: uuid.UUID, label: str, q: int, end: date, reported: date | None,
            revenue: str | None = None, eps: str | None = None, **kw: Any) -> EarningsResult:
    return EarningsResult(
        id=uuid.uuid4(), company_id=company_id, fiscal_period_label=label,
        fiscal_year=int(label[:4]), fiscal_quarter=q, period_end=end, report_date=reported,
        actual_revenue=Decimal(revenue) if revenue else None, revenue_currency="USD",
        actual_eps=Decimal(eps) if eps else None, eps_currency="USD",
        revenue_source_url="https://www.sec.gov/Archives/edgar/data/80420/x-index.htm", **kw)


def _snap(company_id: uuid.UUID, horizon: str, end: date, as_of: date, pre: bool | None,
          **kw: Any) -> ConsensusSnapshot:
    return ConsensusSnapshot(id=uuid.uuid4(), company_id=company_id, source="alpha_vantage",
                             symbol="POWL", horizon=horizon, period_end=end, as_of=as_of,
                             is_pre_report=pre, **kw)


Q3, Q2, Q1 = date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31)


async def _seed(session: Any) -> Any:
    company = await _company(session)
    cid = company.id
    session.add_all([
        _result(cid, "2026-Q3", 3, Q3, date(2026, 8, 4), "311740000", "3.05"),
        _result(cid, "2026-Q2", 2, Q2, date(2026, 5, 5), "280000000", "2.50"),
        _result(cid, "2026-Q1", 1, Q1, date(2026, 2, 4), "251184000", "1.80"),
        _result(cid, "2025", 0, date(2025, 9, 30), date(2025, 11, 19), "1104318000", "13.00",
                vs_guidance="within", guidance_metric="revenue",
                guidance_low=Decimal("1050000000"), guidance_high=Decimal("1150000000"),
                guidance_mid=Decimal("1100000000"), guidance_evidence_ref="finding:abc"),
        # Q3: two pre-report snapshots (the later one wins) and one read after the report.
        _snap(cid, "fiscal_quarter", Q3, date(2026, 7, 1), True, eps_avg=Decimal("2.70"),
              eps_analyst_count=4, revenue_avg=Decimal("300000000"), revenue_analyst_count=4),
        _snap(cid, "fiscal_quarter", Q3, date(2026, 8, 1), True, eps_avg=Decimal("2.80"),
              eps_analyst_count=5, revenue_avg=Decimal("305000000"), revenue_analyst_count=5),
        _snap(cid, "fiscal_quarter", Q3, date(2026, 8, 10), False, eps_avg=Decimal("9.99"),
              eps_analyst_count=5, revenue_avg=Decimal("999000000")),
        # Snapshot flagged pre-report on the day of the report: not STRICTLY before → unused.
        _snap(cid, "fiscal_quarter", Q2, date(2026, 5, 5), True, eps_avg=Decimal("2.40"),
              eps_analyst_count=2, revenue_avg=Decimal("270000000")),
        _snap(cid, "fiscal_quarter", Q2, date(2026, 4, 20), True, eps_avg=Decimal("2.45"),
              eps_analyst_count=2, revenue_avg=Decimal("290000000")),
        # Provider records (provider EPS basis).
        _snap(cid, "reported_quarter", Q3, date(2026, 11, 16), False, eps_avg=Decimal("2.80"),
              vendor_reported_eps=Decimal("3.10"), vendor_surprise=Decimal("0.30"),
              vendor_surprise_pct=Decimal("10.7143"), vendor_reported_date=date(2026, 8, 4)),
        _snap(cid, "reported_quarter", Q2, date(2026, 11, 16), False, eps_avg=Decimal("2.40"),
              vendor_reported_eps=Decimal("2.50"), vendor_surprise=Decimal("0.10"),
              vendor_surprise_pct=Decimal("4.1667")),
        _snap(cid, "reported_quarter", Q1, date(2026, 11, 16), False, eps_avg=Decimal("1.90"),
              vendor_reported_eps=Decimal("1.80"), vendor_surprise=Decimal("-0.10"),
              vendor_surprise_pct=Decimal("-5.2632")),
    ])
    await session.flush()
    return company


def _rows(rows: list[EarningsResult]) -> dict[str, EarningsResult]:
    return {r.fiscal_period_label: r for r in rows}


async def test_surprises_use_only_dated_pre_report_snapshots(session) -> None:  # noqa: ANN001
    company = await _seed(session)
    out = await etr.earnings_surprise_step(
        session, company, cfg=SimpleNamespace(v3_earnings_track_record_enabled=True))
    assert out["state"] == "applied" and out["rows"] == 4
    rows = _rows(list((await session.execute(select(EarningsResult).where(
        EarningsResult.company_id == company.id))).scalars()))

    q3 = rows["2026-Q3"]
    assert q3.consensus_as_of == date(2026, 8, 1)          # latest strictly before 08-04
    assert q3.consensus_eps_avg == Decimal("2.80") and q3.analyst_count_eps == 5
    assert q3.consensus_status == "available" and q3.consensus_source == "alpha_vantage"
    assert q3.surprise_revenue_abs == Decimal("6740000")   # 311.74M − 305M, filed figure
    assert q3.surprise_revenue_pct == Decimal("2.2098")
    # EPS surprise is the provider's, never filed EPS − provider estimate.
    assert q3.surprise_eps_abs == Decimal("0.30")
    eps_detail = q3.detail_json["surprise"]["eps_surprise"]
    assert eps_detail["basis"] == "provider" and eps_detail["eps_basis_difference"] is True

    q2 = rows["2026-Q2"]
    assert q2.consensus_as_of == date(2026, 4, 20)         # the same-day one is not "before"
    assert q2.consensus_status == "thin" and q2.surprise_revenue_abs == Decimal("-10000000")
    assert q2.detail_json["surprise"]["eps_surprise"]["eps_basis_difference"] is False

    q1 = rows["2026-Q1"]   # provider record only: no dated expectation
    assert q1.consensus_status == "none" and q1.consensus_eps_avg is None
    assert q1.consensus_as_of is None and q1.surprise_revenue_abs is None
    assert q1.surprise_eps_abs == Decimal("-0.10")

    fy = rows["2025"]
    assert fy.consensus_status == "none" and fy.surprise_eps_abs is None


async def test_no_provider_data_is_source_unavailable(session) -> None:  # noqa: ANN001
    company = await _company(session)
    session.add(_result(company.id, "2026-Q3", 3, Q3, date(2026, 8, 4), "1", "1"))
    await session.flush()
    out = await etr.apply_surprises(session, company_id=company.id)
    assert out["covered"] is False and out["consensus_status"] == {"source_unavailable": 1}
    off = await etr.earnings_surprise_step(
        session, company, cfg=SimpleNamespace(v3_earnings_track_record_enabled=False))
    assert off == {"state": "disabled"}


def test_status_thresholds() -> None:
    assert etr.status_for(3, has_snapshot=True, covered=True) == "available"
    assert etr.status_for(2, has_snapshot=True, covered=True) == "thin"
    assert etr.status_for(None, has_snapshot=True, covered=True) == "thin"
    assert etr.status_for(0, has_snapshot=True, covered=True) == "none"
    assert etr.status_for(None, has_snapshot=False, covered=True) == "none"
    assert etr.status_for(None, has_snapshot=False, covered=False) == "source_unavailable"


async def test_block_shape_streak_and_safety(session) -> None:  # noqa: ANN001
    from app.services.safety_terms import scan_value

    company = await _seed(session)
    await etr.apply_surprises(session, company_id=company.id)
    rows = list((await session.execute(select(EarningsResult).where(
        EarningsResult.company_id == company.id))).scalars())
    block = etr.earnings_track_record_block(rows)
    assert block["state"] == "held" and block["title"] == "Earnings track record"
    assert [q["period"] for q in block["quarters"]] == ["2026-Q3", "2026-Q2", "2026-Q1"]
    q3 = block["quarters"][0]
    assert q3["expectation"] == {
        "source": "Alpha Vantage", "as_of": "2026-08-01", "eps_avg": "2.8", "eps_high": None,
        "eps_low": None, "eps_analyst_count": 5, "revenue_avg": "305000000",
        "revenue_high": None, "revenue_low": None, "revenue_analyst_count": 5}
    assert q3["eps_vs_provider_estimate"]["basis"] == "provider EPS basis"
    assert q3["eps_vs_provider_estimate"]["eps_basis_difference"] is True
    assert q3["revenue_vs_expectation"] == {"difference": "6740000", "pct": "2.2098"}
    assert block["quarters"][2]["expectation"] is None
    # EPS: above, above, below → a streak of two quarters above.
    assert block["eps_streak"] == {"direction": "above", "quarters": 2}
    assert block["revenue_streak"] == {"direction": "above", "quarters": 1}
    year = block["years"][0]
    assert year["guidance"]["result"] == "within" and year["guidance"]["mid"] == "1100000000"
    assert block["expectations_available"] == 2
    # Typed values only, and nothing the gate bans anywhere in the block.
    assert scan_value(block) == []
    json.dumps(block)


def test_block_without_rows_is_not_researched() -> None:
    block = etr.earnings_track_record_block([])
    assert block["state"] == "not_researched" and block["reason"] == "no_filed_quarterly_results"


def test_report_attaches_block_only_when_given() -> None:
    from app.services.pipeline import professional_research as pr

    src = pr.assemble.__code__.co_names
    assert "earnings_track_record" in src
    assert "earnings_track_record" in pr.ReportInputs.__dataclass_fields__
    assert pr.ReportInputs.__dataclass_fields__["earnings_track_record"].default is None


def test_web_reader_pins_the_block_keys() -> None:
    import pathlib

    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "earningsTrackRecord.ts").read_text()
    for key in ("earnings_track_record", "quarters", "years", "eps_streak", "revenue_streak",
                "expectation", "eps_vs_provider_estimate", "revenue_vs_expectation",
                "consensus_status", "eps_analyst_count", "as_of", "eps_basis_difference",
                "guidance", "note"):
        assert f'"{key}"' in reader or f"{key}:" in reader or f".{key}" in reader, key
