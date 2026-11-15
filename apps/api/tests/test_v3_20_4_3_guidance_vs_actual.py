"""V3.20 slice 4.3 — guidance (ledger findings) vs actual (``earnings_results``)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.models.earnings import EarningsResult
from app.models.ledger import ResearchFinding, ResearchRun
from app.services.pipeline.guidance_step import (
    GuidanceCandidate,
    compare,
    guidance_step,
    match_rows,
)
from app.services.sources.guidance_figures import GuidedFigure, guided_figures
from tests import test_v3_entity_relationships as _base

session = _base.session
CFG = SimpleNamespace(v3_earnings_guidance_enabled=True)


class TestFigures:
    @pytest.mark.parametrize(("statement", "expected"), [
        ("FY2026 revenue $25,900m to $26,100m; EPS $20.",
         [("revenue", Decimal("25900e6"), Decimal("26100e6"), None, "USD"),
          ("eps", None, None, Decimal("20"), "USD")]),
        ("The company expects full-year net sales between $1.15 billion and $1.20 billion.",
         [("revenue", Decimal("1.15e9"), Decimal("1.20e9"), None, "USD")]),
        ("Fiscal 2026 revenue of $1.15 to $1.20 billion",  # scale written once
         [("revenue", Decimal("1.15e9"), Decimal("1.20e9"), None, "USD")]),
        ("Revenue of $1.1 to $1.2 billion and adjusted EBITDA of $200 million",
         [("revenue", Decimal("1.1e9"), Decimal("1.2e9"), None, "USD")]),
        ("Sales of $500 million and EPS of $1.10 to $1.20",
         [("revenue", None, None, Decimal("500e6"), "USD"),
          ("eps", Decimal("1.10"), Decimal("1.20"), None, "USD")]),
        ("Guidance: revenue €300m–€320m",
         [("revenue", Decimal("300e6"), Decimal("320e6"), None, "EUR")]),
        ("Fiscal 2026 revenue guidance of approximately $1.2bn",
         [("revenue", None, None, Decimal("1.2e9"), "USD")]),
    ])
    def test_reads(self, statement: str, expected: list[tuple[Any, ...]]) -> None:
        got = [(f.metric, f.low, f.high, f.point, f.currency) for f in guided_figures(statement)]
        assert got == expected

    @pytest.mark.parametrize("statement", [
        "Revenue growth of 10% to 12% and EBITDA margin of 15%",  # percentages, not amounts
        "Orders of $500 million expected in 2026",  # not a metric with an actual
        "Revenue expected to grow in 2026",  # no figure
        "Revenue and EBITDA of $1.2 billion",  # two metrics in one clause
        "Operating margin on sales of $40 million",  # another metric names the clause
    ])
    def test_refuses(self, statement: str) -> None:
        assert guided_figures(statement) == []


def _fig(low: str | None = None, high: str | None = None, point: str | None = None,
         metric: str = "revenue", currency: str | None = "USD") -> GuidedFigure:
    return GuidedFigure(metric=metric, low=Decimal(low) if low else None,
                        high=Decimal(high) if high else None,
                        point=Decimal(point) if point else None, currency=currency, text="t")


class TestCompare:
    def test_range(self) -> None:
        assert compare(Decimal("1.17e9"), _fig("1.15e9", "1.20e9")) == ("within", -0.43)
        assert compare(Decimal("1.25e9"), _fig("1.15e9", "1.20e9"))[0] == "above"
        assert compare(Decimal("1.10e9"), _fig("1.15e9", "1.20e9"))[0] == "below"

    def test_point_is_never_rounded_into_in_line(self) -> None:
        assert compare(Decimal("1.201e9"), _fig(point="1.2e9")) == ("above_point", 0.08)
        assert compare(Decimal("1.2e9"), _fig(point="1.2e9")) == ("at_point", 0.0)


def _row(label: str = "2026", *, report: date | None = date(2026, 11, 18),
         revenue: str | None = "1170000000", eps: str | None = "14.86",
         currency: str = "USD") -> Any:
    return SimpleNamespace(fiscal_period_label=label, report_date=report,
                           actual_revenue=Decimal(revenue) if revenue else None,
                           revenue_currency=currency,
                           actual_eps=Decimal(eps) if eps else None, eps_currency=currency)


def _cand(published: date | None, figure: GuidedFigure, period: str = "2026"
          ) -> GuidanceCandidate:
    return GuidanceCandidate(finding_id=uuid.uuid4(), period_key=period,
                             published=published, figure=figure)


class TestMatch:
    def test_newest_guidance_before_the_print_wins(self) -> None:
        early = _cand(date(2025, 11, 18), _fig("1.00e9", "1.10e9"))
        raised = _cand(date(2026, 8, 3), _fig("1.15e9", "1.20e9"))
        after = _cand(date(2026, 11, 20), _fig("2e9", "3e9"))
        report = match_rows([_row()], [early, raised, after])
        (m,) = report.matched
        assert (m.finding_id, m.vs) == (raised.finding_id, "within")
        assert report.skipped == {"guidance_published_on_or_after_the_result": 1}

    def test_period_metric_currency_and_dates_must_agree(self) -> None:
        report = match_rows([_row()], [
            _cand(date(2026, 1, 1), _fig("1e9", "2e9"), period="2027"),
            _cand(date(2026, 1, 1), _fig("1e9", "2e9", currency="EUR")),
            _cand(date(2026, 1, 1), _fig("1e9", "2e9", currency=None)),
            _cand(None, _fig("1e9", "2e9")),
        ])
        assert report.matched == []
        assert report.skipped == {"guidance_currency_differs_or_unstated": 2,
                                  "guidance_publication_date_unknown": 1}

    def test_eps_only_when_revenue_was_not_guided(self) -> None:
        eps = _cand(date(2026, 2, 1), _fig("14", "15", metric="eps", currency=None))
        (m,) = match_rows([_row()], [eps]).matched
        assert (m.metric, m.vs) == ("eps", "within")  # the actual's currency is taken
        revenue = _cand(date(2026, 2, 1), _fig("1e9", "1.1e9"))
        (m,) = match_rows([_row()], [eps, revenue]).matched
        assert (m.metric, m.vs) == ("revenue", "above")

    def test_no_report_date(self) -> None:
        report = match_rows([_row(report=None)], [_cand(date(2026, 1, 1), _fig("1", "2"))])
        assert report.matched == [] and report.skipped == {"row_has_no_report_date": 1}


async def _company_with_actuals(session) -> Any:
    from app.models.company import Company

    company = Company(id=uuid.uuid4(), ticker=f"G{uuid.uuid4().hex[:6]}", exchange="NASDAQ",
                      name="Example Grid", status="new")
    session.add(company)
    await session.flush()
    session.add(EarningsResult(
        id=uuid.uuid4(), company_id=company.id, fiscal_period_label="2026", fiscal_year=2026,
        fiscal_quarter=0, period_end=date(2026, 9, 30), report_date=date(2026, 11, 18),
        actual_revenue=Decimal("1170000000"), revenue_currency="USD"))
    await session.flush()
    return company


async def _finding(session, company: Any, statement: str, *, published: date | None,
                   kind: str = "guidance", status: str = "unverified") -> uuid.UUID:
    run = ResearchRun(id=uuid.uuid4(), company_id=company.id, mode="company", status="complete")
    session.add(run)
    await session.flush()
    finding = ResearchFinding(
        id=uuid.uuid4(), research_run_id=run.id, statement=statement, evidence_ids_json=["ev:1"],
        evidence_count=1, period_key="2026", statement_kind=kind, source_published_at=published,
        verification_status=status)
    session.add(finding)
    await session.flush()
    return finding.id


class TestStep:
    async def test_writes_guidance_columns(self, session) -> None:
        company = await _company_with_actuals(session)
        await _finding(session, company, "FY2026 revenue of $1.00 to $1.10 billion.",
                       published=date(2025, 11, 18))
        raised = await _finding(session, company,
                                "Raised FY2026 revenue guidance to $1.15 to $1.20 billion.",
                                published=date(2026, 8, 3))
        await _finding(session, company, "FY2026 revenue of $5 billion.",
                       published=date(2026, 3, 1), status="withdrawn")
        await _finding(session, company, "FY2026 revenue was $1.17 billion.",
                       published=date(2026, 11, 18), kind="actual")
        out = await guidance_step(session, company, cfg=CFG)
        assert out["state"] == "matched"
        assert out["matched"] == [{"period": "2026", "metric": "revenue", "vs": "within"}]
        row = (await session.execute(select(EarningsResult))).scalar_one()
        assert (row.guidance_low, row.guidance_high) == (Decimal("1.15e9"), Decimal("1.20e9"))
        assert row.guidance_mid == Decimal("1.175e9")
        assert row.guidance_evidence_ref == f"finding:{raised}"
        assert row.vs_guidance == "within" and row.guidance_metric == "revenue"
        assert row.detail_json["guidance"]["published"] == "2026-08-03"

    async def test_states(self, session) -> None:
        from app.models.company import Company

        bare = Company(id=uuid.uuid4(), ticker="BARE1", exchange="NASDAQ", name="Bare",
                       status="new")
        session.add(bare)
        await session.flush()
        assert (await guidance_step(session, bare, cfg=CFG))["state"] == "no_actuals"
        company = await _company_with_actuals(session)
        assert (await guidance_step(session, company, cfg=CFG))["state"] == \
            "no_guidance_figures"
        assert (await guidance_step(session, company, cfg=SimpleNamespace(
            v3_earnings_guidance_enabled=False)))["state"] == "disabled"
