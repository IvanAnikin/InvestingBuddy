"""ADR-090 — the live Honeywell rerun on e8726e4 (report HON3, defects HON3-1/-4/-6).

Defect 1 (P0): a bare year read from text became "Latest annual FY2026". The Q3 2026
release's "sales of $5.0 billion" (a QUARTER) filled the annual revenue slot as FY2026;
the operating-profit slot held the TEXT "EBIT target was also cut to $4.35bn" from a web
article; and ``conflicts[revenue@2026]`` set the quarter, the Q2 full-year guidance, a
web article and a Q1 10-Q figure against each other. Pinned here:

  a. a slot only ever holds a parsed number;
  b. a bare-year (annual) period fills an annual slot only once that fiscal year had
     ENDED by the source's date and the run date — on the issuer's calendar when known
     (SEC anchor, its own fiscal labels, its own annual report), else the calendar year;
  c. a forward statement (target, guidance, outlook, expects) never fills an actual slot
     and never enters a conflict; nor does a figure from a non-issuer document;
  d. a conflict compares like with like (one canonical period and granularity).

Defect 2 (P1): ADR-084's re-read ran for 1 question of 24. Every other question was
satisfied by its item COUNT at the platform rung, and the re-read only ran for an
unsatisfied one; 21 absence gaps were then written over a held 10-Q. Pinned here: a
count-satisfied question whose evidence carries none of the figures it asks for re-reads
the held primary documents (no LLM, no web search), and an 8-K exhibit is dated with its
filing's date.

Fixtures are modelled on the HON3 payload; no network, no model.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.agents import document_reread as dr
from app.services.agents.investigator import LLMInvestigator, QuestionContext
from app.services.director.base_model import base_question
from app.services.director.roles import role_for
from app.services.final_report_generator import _build_financial_snapshot
from app.services.pipeline import issuer_financials as fin
from app.services.pipeline.issuer_financials import (
    WITHHELD_ANNUAL_PERIOD_NOT_ENDED,
    WITHHELD_ANNUAL_PERIOD_UNDATED,
    WITHHELD_FORWARD_STATEMENT,
    WITHHELD_NOT_A_NUMBER,
    WITHHELD_NOT_AN_ISSUER_DOCUMENT,
    WITHHELD_NOT_THE_SUBJECT,
    annual_has_ended,
    build_financial_statements_state,
    slot_number,
)
from app.services.playbooks.industries import PLAYBOOKS
from app.services.playbooks.schema import planned_from
from app.services.statement_kind import FiscalYearAnchor


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


RUN = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
TEN_K = "https://www.sec.gov/Archives/edgar/data/773840/000077384026000013/hon-20251231.htm"
TEN_Q = "https://www.sec.gov/Archives/edgar/data/773840/000077384026000143/hon-20260930.htm"
Q1_10Q = "https://investor.honeywell.example/static-files/q1-10q"
Q3_EX99 = ("https://www.sec.gov/Archives/edgar/data/773840/000077384026000141/"
           "exhibit99-q32026earningsre.htm")
Q2_EX99 = ("https://www.sec.gov/Archives/edgar/data/773840/000077384026000120/"
           "exhibit99-q22026earningsre.htm")
WEB_TARGET = "https://www.marketscreener.example/news/aerospace-profit-warning"
WEB_ARTICLE = "https://scw-mag.example/news/supply-chain"


def _fact(field: str, numeric: float, period: str, *, value: str | None = None,
          url: str = TEN_K, title: str = "10-K 0000773840-26-000013",
          date_: str | None = None, tier: str | None = "T1_primary_filing",
          scale: str = "million", doc: str | None = None) -> dict[str, Any]:
    return {
        "field": field, "value": value if value is not None else f"{numeric:,.0f}",
        "numeric_value": numeric, "unit": "currency_amount", "currency": "USD",
        "scale": scale, "period": period, "scope": "group", "source_url": url,
        "page_number": None, "excerpt_id": None, "confidence": "high",
        "fact_id": f"{doc or url}:{field}:{period}:{numeric}", "document_id": doc or url,
        "document_title": title, "document_date": date_,
        "document_source_tier": tier,
    }


def _hon3_facts() -> list[dict[str, Any]]:
    """The HON3 statements, as the issuer's documents and the web stage stored them."""
    return [
        # The FY2025 10-K (inline XBRL) and the Q3 2026 10-Q: the true figures.
        _fact("revenue", 37442, "2025"),
        _fact("operating_profit", 6151, "2025"),
        _fact("net_income", 4729, "2025"),
        _fact("revenue", 4994, "2026-Q3", url=TEN_Q, title="10-Q 0000773840-26-000143"),
        _fact("operating_profit", 1062, "2026-Q3", url=TEN_Q,
              title="10-Q 0000773840-26-000143"),
        # The Q3 release: quarter sales read with the bare year "2026".
        _fact("revenue", 5.0, "2026", value="sales of $5.0 billion", url=Q3_EX99,
              title="8-K 0000773840-26-000141", date_="2026-10-22", scale="billion"),
        # The Q2 release: the FULL-YEAR guidance, also "2026".
        _fact("revenue", 19.8, "2026", value="sales guidance of $19.8 billion",
              url=Q2_EX99, title="Document", scale="billion"),
        # A Q1 10-Q copy on the issuer's site: a quarter figure read as "2026".
        _fact("revenue", 1064, "2026", url=Q1_10Q,
              title="Form 10-Q for Honeywell International INC filed 04/23/2026",
              tier="T1_primary_company_source"),
        # The web stage: a trade article and an aggregator's target.
        _fact("revenue", 4.52, "2026", value="revenue of $4.52bn", url=WEB_ARTICLE,
              title="Honeywell exposes aerospace's growing supply chain problem",
              date_="2026-08-10", tier="T4_quality_media", scale="billion"),
        _fact("operating_profit", 4.35, "2026", value="EBIT target was also cut to $4.35bn",
              url=WEB_TARGET, title="Honeywell Aerospace plunges after a big 2026 profit "
              "warning | MarketScreener", date_="2026-08-06", tier="T5_api_aggregator",
              scale="billion"),
    ]


# ── Defect 1: the statements view ──────────────────────────────────────────── #


class TestHon3Statements:
    def test_latest_annual_is_the_last_ended_fiscal_year(self) -> None:
        state = build_financial_statements_state(_hon3_facts(), now=RUN)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert state["annual"]["period"] == "FY2025"
        assert state["reporting_periods"]["latest_current_period"] == "Q3 2026"

    def test_no_fy2026_annual_revenue_and_no_text_in_a_slot(self) -> None:
        state = build_financial_statements_state(_hon3_facts(), now=RUN)
        slots = state["slots"]
        assert slots["revenue_primary_filing"]["numeric_value"] == 37442
        assert slots["revenue_primary_filing"]["period"] == "2025"
        assert slots["operating_profit_primary_filing"]["numeric_value"] == 6151
        for dp in slots.values():
            assert slot_number({"value": dp["value"],
                                "numeric_value": dp["numeric_value"]}) == dp["value"]
            assert not any(ch.isalpha() for ch in str(dp["value"]))

    def test_no_conflict_mixes_a_quarter_guidance_and_the_web(self) -> None:
        state = build_financial_statements_state(_hon3_facts(), now=RUN)
        assert not any(c["period"] == "2026" for c in state["conflicts"])
        reasons = state["withheld_counts"]
        assert reasons[WITHHELD_NOT_AN_ISSUER_DOCUMENT] == 2
        assert reasons[WITHHELD_ANNUAL_PERIOD_NOT_ENDED] == 1  # the dated Q3 release
        assert reasons[WITHHELD_ANNUAL_PERIOD_UNDATED] == 1  # the undated Q1 10-Q copy
        assert reasons[WITHHELD_FORWARD_STATEMENT] == 1  # the Q2 full-year guidance
        assert state["fiscal_calendar"] == "calendar_year"
        # The record names the field, period and reason — never the fact's text.
        assert all("value" not in w for w in state["withheld"])

    def test_with_the_issuer_calendar_the_verdict_is_the_same(self) -> None:
        anchor = FiscalYearAnchor(2025, date(2025, 12, 31))
        state = build_financial_statements_state(_hon3_facts(), now=RUN,
                                                 fiscal_anchor=anchor)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert state["fiscal_calendar"] == "issuer_sec_statements"

    def test_after_the_year_ends_the_bare_year_is_annual_again(self) -> None:
        """FY2026's own 10-K figures, filed in February 2027, are annual."""
        facts = [_fact("revenue", 38000, "2026", url=TEN_K.replace("20251231", "20261231"),
                       title="10-K 0000773840-27-000010", date_="2027-02-16")]
        state = build_financial_statements_state(facts, now=datetime(2027, 2, 20, tzinfo=UTC))
        assert state["reporting_periods"]["latest_annual"] == "FY2026"


class TestSlotsHoldNumbers:
    @pytest.mark.parametrize(
        ("value", "numeric", "expected"),
        [
            ("4,994", 4994, "4,994"),
            ("(1,953)", -1953, "(1,953)"),
            ("sales reached € 22.4 billion", 22.4, "22.4"),
            ("profit for the year amounted to € 3 484 million", 3484, "3 484"),
            ("revenue of EUR22,420 million", 22420, "22,420"),
            ("Net loss was $3.2 million", -3.2, "3.2"),
            ("9470", 9470, "9470"),
            ("22,4", 22.4, "22,4"),
            (None, 664.0, "664"),
            ("-", 0.0, "0"),  # an Appendix 5B nil row (LIN3 financing cash flow)
        ],
    )
    def test_the_slot_value_is_the_printed_number(self, value, numeric, expected) -> None:  # noqa: ANN001
        assert slot_number({"value": value, "numeric_value": numeric}) == expected

    @pytest.mark.parametrize(
        ("value", "numeric"),
        [("n/a", 5.0), ("see note 4", 4.35), ("-", 5.0), ("revenue of $4.5bn", 4.35), ("x", None),
         ("4.35", float("nan"))],
    )
    def test_a_value_without_its_number_fills_no_slot(self, value, numeric) -> None:  # noqa: ANN001
        assert slot_number({"value": value, "numeric_value": numeric}) is None
        fact = _fact("revenue", 1.0, "2025", value=value)
        fact["numeric_value"] = numeric
        state = build_financial_statements_state([fact], now=RUN)
        assert state["slots"] == {}
        assert state["withheld_counts"] == {WITHHELD_NOT_A_NUMBER: 1}

    @pytest.mark.parametrize(
        "value",
        ["EBIT target was also cut to $4.35bn", "sales guidance of $19.8 billion",
         "the company expects revenue of $20.1 billion", "outlook for revenue of $19.5bn",
         "forecast revenue of $19bn", "consensus revenue of $19.9bn"],
    )
    def test_a_forward_statement_fills_no_slot(self, value: str) -> None:
        fact = _fact("revenue", float(value.split("$")[1].rstrip("bnilo ")), "2025",
                     value=value, scale="billion")
        state = build_financial_statements_state([fact], now=RUN)
        assert state["slots"] == {}
        assert state["withheld_counts"] == {WITHHELD_FORWARD_STATEMENT: 1}


class TestFiscalCalendar:
    def test_calendar_year_without_an_anchor(self) -> None:
        assert not annual_has_ended(2026, date(2026, 12, 31))
        assert annual_has_ended(2026, date(2027, 1, 1))

    def test_an_anchor_moves_the_year_end(self) -> None:
        june = FiscalYearAnchor(2025, date(2025, 6, 30))
        assert not annual_has_ended(2026, date(2026, 7, 3), june)  # inside the tolerance
        assert annual_has_ended(2026, date(2026, 7, 10), june)

    def test_the_issuers_own_fiscal_label_shows_the_year_ended(self) -> None:
        """A March year end with no SEC calendar: the issuer's "FY27 Q1" release shows
        FY2026 had ended, so its FY2026 annual figures stay annual."""
        q1 = ("https://www.issuer.example/media/x/ad-hoc-announcement-fy27-q1-sales-en.pdf")
        facts = [
            _fact("revenue", 22.4, "2026", value="sales reached € 22.4 billion",
                  url="https://www.issuer.example/media/y/annual-report.pdf",
                  title="Download", scale="billion", tier="T1_primary_filing"),
            _fact("revenue", 6.3, "2027-Q1", value="sales at € 6.3 billion", url=q1,
                  title="Download", scale="billion", tier="T1_primary_filing"),
        ]
        state = build_financial_statements_state(facts, now=datetime(2026, 11, 1, tzinfo=UTC))
        assert state["reporting_periods"]["latest_annual"] == "FY2026"
        assert state["fiscal_calendar"] == "issuer_fiscal_labels"
        assert state["fiscal_year_begun"] == 2027

    def test_without_the_label_the_fiscal_year_is_withheld_not_shown_as_older(self) -> None:
        facts = [_fact("revenue", 22.4, "2026", value="22.4", title="Download",
                       url="https://www.issuer.example/doc.pdf", scale="billion")]
        state = build_financial_statements_state(facts, now=datetime(2026, 11, 1, tzinfo=UTC))
        assert state["slots"] == {}
        assert state["annual"]["state"] == fin.STATE_ACQUIRED_NOT_EXTRACTED
        assert "unverified or mislabelled" in state["annual"]["label"]
        assert "part-year" in state["annual"]["reason"]

    @pytest.mark.parametrize(
        ("url", "title", "latest"),
        [
            ("https://issuer.test/reports/fy26-annual-report.pdf", None, "FY2026"),
            ("https://issuer.test/media/Annual%20Report%202026.pdf", "Download", "FY2026"),
            ("https://issuer.test/reports/fy26-annual-report-notice-of-meeting.pdf", None,
             None),
            ("https://issuer.test/reports/fy27-outlook-annual-report.pdf", None, None),
            ("https://issuer.test/doc.pdf", "Issuer FY26 Annual Report", "FY2026"),
        ],
    )
    def test_the_annual_reports_file_name_names_its_year(self, url, title, latest) -> None:  # noqa: ANN001
        facts = [_fact("operating_profit", 4492, "2026", value="4,492", url=url,
                       title=title, scale="million")]
        state = build_financial_statements_state(facts, now=datetime(2026, 11, 1, tzinfo=UTC))
        assert state["reporting_periods"]["latest_annual"] == latest

    def test_the_issuers_annual_report_for_the_year_shows_it_ended(self) -> None:
        facts = [_fact("cash_and_equivalents", 9470, "2025", value="9,470",
                       title="Annual Report 2025", date_="2025-09-30",
                       url="https://official.example/ar.pdf")]
        state = build_financial_statements_state(facts, now=datetime(2025, 10, 1, tzinfo=UTC))
        assert state["reporting_periods"]["latest_annual"] == "FY2025"

    @pytest.mark.parametrize("title", ["Notice of Annual Report 2026",
                                       "Annual results date 2026", "2026 outlook"])
    def test_a_notice_or_outlook_title_is_not_the_annual_report(self, title: str) -> None:
        facts = [_fact("revenue", 10, "2026", value="10", title=title,
                       url="https://official.example/n.pdf")]
        state = build_financial_statements_state(facts, now=datetime(2026, 10, 1, tzinfo=UTC))
        assert state["slots"] == {}


class TestLikeWithLike:
    def test_a_bare_year_and_its_fy_label_are_one_period(self) -> None:
        facts = [_fact("revenue", 100, "2025", doc="a"),
                 _fact("revenue", 140, "FY2025", doc="b")]
        state = build_financial_statements_state(facts, now=RUN)
        assert "revenue_primary_filing" not in state["slots"]
        assert len(state["conflicts"]) == 1

    def test_a_year_never_meets_a_quarter(self) -> None:
        facts = [_fact("revenue", 37442, "2025"), _fact("revenue", 9437, "2025-Q3")]
        state = build_financial_statements_state(facts, now=RUN)
        assert state["conflicts"] == []
        assert state["slots"]["revenue_primary_filing"]["numeric_value"] == 37442

    def test_a_withheld_period_still_blocks_a_disagreeing_slot(self) -> None:
        """Withholding one side for its period must not hand the slot to the other."""
        facts = [_fact("net_income", 4729, "2025", title="Annual Report 2025",
                       date_="2025-09-30", doc="ar"),
                 _fact("net_income", 5100, "2025", title="Results", date_="2025-09-30",
                       doc="r")]
        state = build_financial_statements_state(facts, now=datetime(2025, 10, 1, tzinfo=UTC))
        assert "net_income_primary_filing" not in state["slots"]
        assert len(state["conflicts"]) == 1


class TestV2Snapshot:
    def test_the_q3_release_never_fills_the_annual_snapshot_slot(self) -> None:
        """The page's "Annual FY2026 Revenue 5 bn USD" came from the V2 snapshot."""
        facts = [
            {"field": "revenue", "value": "sales of $5.0 billion", "numeric_value": 5.0,
             "unit": "currency_amount", "currency": "USD", "scale": "billion",
             "period": "2026", "confidence": "high", "source_url": Q3_EX99},
            {"field": "revenue", "value": "4,994", "numeric_value": 4994.0,
             "unit": "currency_amount", "currency": "USD", "scale": "million",
             "period": "2026-Q3", "scope": "Group", "confidence": "high",
             "source_url": TEN_Q},
        ]
        section = _build_financial_snapshot(None, None, primary_facts=facts)
        assert "revenue_primary_filing" not in section
        assert section["revenue_current_period"]["numeric_value"] == 4994.0
        assert section["reporting_periods"]["latest_annual"] is None
        assert section["reporting_periods"]["latest_current_period"] == "Q3 2026"


# ── ADR-090 review (rv_h1 r1–r5): one test per repro ───────────────────────── #

HON = "Honeywell International Inc."
DEC = FiscalYearAnchor(2025, date(2025, 12, 31))


def _q3_release_undated() -> dict[str, Any]:
    return _fact("revenue", 5.0, "2026", value="sales of $5.0 billion", url=Q3_EX99,
                 title="8-K 0000773840-26-000141", date_=None, scale="billion", doc="ex99")


class TestReviewP1UndatedFactsFailClosed:
    """r1/r2: the HON3 defect came back after 31 December — an undated Q3 release's
    "2026" was judged against the RUN date."""

    @pytest.mark.parametrize("anchor", [None, DEC])
    def test_an_undated_bare_year_stays_withheld_after_the_year_end(self, anchor) -> None:  # noqa: ANN001
        facts = [_q3_release_undated(), _fact("revenue", 37442, "2025")]
        state = build_financial_statements_state(
            facts, now=datetime(2027, 1, 15, tzinfo=UTC), fiscal_anchor=anchor)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert state["slots"]["revenue_primary_filing"]["numeric_value"] == 37442
        assert state["withheld_counts"] == {WITHHELD_ANNUAL_PERIOD_UNDATED: 1}

    def test_an_undated_figure_counts_once_the_years_annual_report_is_held(self) -> None:
        """Another document — the issuer's FY2026 10-K — shows the year ended."""
        ten_k_2026 = TEN_K.replace("20251231", "20261231")
        facts = [
            _fact("revenue", 38100, "2026", url=ten_k_2026, title="10-K 0000773840-27-000010",
                  doc="k26"),
            _fact("revenue", 38100, "2026", url=Q1_10Q, title="Results", doc="undated"),
        ]
        state = build_financial_statements_state(facts, now=datetime(2027, 3, 1, tzinfo=UTC))
        assert state["reporting_periods"]["latest_annual"] == "FY2026"
        assert state["withheld_counts"] == {}

    def test_an_undated_half_year_reports_comparative_is_annual(self) -> None:
        """An H1 2026 report cannot predate the end of H1 2026: on a calendar fiscal year
        its FY2025 year-end figure is annual, dated or not. (Without the calendar, the
        earliest H1 2026 end is mid-2025 and the figure stays withheld.)"""
        facts = [_fact("cash_and_equivalents", 9470, "2025", url=Q1_10Q, title="Half-year",
                       doc="h1"),
                 _fact("cash_and_equivalents", 8100, "H1 2026", url=Q1_10Q, title="Half-year",
                       doc="h1")]
        state = build_financial_statements_state(facts, now=RUN, fiscal_anchor=DEC)
        assert state["slots"]["cash_and_equivalents_primary_filing"]["numeric_value"] == 9470
        state = build_financial_statements_state(facts, now=RUN)
        assert state["withheld_counts"] == {WITHHELD_ANNUAL_PERIOD_UNDATED: 1}

    def test_an_edgar_file_names_period_date_floors_an_undated_10q(self) -> None:
        """HON3's undated 10-Q ("hon-20260930.htm") was filed after 30 September 2026,
        so its 31 December 2025 balance-sheet comparative is an ended year's figure."""
        facts = [_fact("cash_and_equivalents", 12273, "2025", url=TEN_Q,
                       title="10-Q 0000773840-26-000143", doc="q")]
        state = build_financial_statements_state(facts, now=RUN)
        assert state["slots"]["cash_and_equivalents_primary_filing"]["numeric_value"] == 12273
        off_edgar = [dict(facts[0], source_url="https://investor.honeywell.com/hon-20260930.htm")]
        state = build_financial_statements_state(off_edgar, now=RUN)
        assert state["withheld_counts"] == {WITHHELD_ANNUAL_PERIOD_UNDATED: 1}

    def test_v2_primary_facts_carry_their_documents_date_title_and_tier(self) -> None:
        from app.services.llm.council import _structured_facts
        from app.services.sources.evidence import PrimaryFactRef

        ref = PrimaryFactRef(field="revenue", value="sales of $5.0 billion",
                             numeric_value=5.0, period="2026", confidence="high",
                             document_date="2026-10-22",
                             document_title="8-K 0000773840-26-000141")
        item = SimpleNamespace(source_type="company_ir_financial_fact", primary_fact=ref,
                               url=Q3_EX99, content_source_tier="T1_primary_filing")
        [fact] = _structured_facts([item], confidences=frozenset({"high"}))
        assert fact["document_date"] == "2026-10-22"
        assert fact["document_title"] == "8-K 0000773840-26-000141"
        assert fact["document_source_tier"] == "T1_primary_filing"

    def test_the_v2_snapshot_never_reads_an_undated_bare_year_as_annual(self) -> None:
        facts = [{"field": "revenue", "value": "sales of $5.0 billion", "numeric_value": 5.0,
                  "unit": "currency_amount", "currency": "USD", "scale": "billion",
                  "period": "2026", "confidence": "high", "source_url": Q3_EX99}]
        section = _build_financial_snapshot(None, None, primary_facts=facts)
        assert "revenue_primary_filing" not in section

    async def test_a_fetched_document_is_dated_with_the_date_its_caller_knows(self) -> None:
        from app.core.config import Settings
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        fetched = DocumentFetchResult(
            requested_url=Q3_EX99, final_url=Q3_EX99, status_code=200,
            content_type="text/html", document_type="html",
            content=b"<html><body><p>Third quarter 2026 results.</p></body></html>",
        )
        artifact = await live_fetchers._artifact_from_fetch(
            fetched, title="8-K 0000773840-26-000141", original_language=None,
            issuer_context=None, cfg=Settings(), fetch_ms=1,
            published_at=date(2026, 10, 22),
        )
        assert artifact.published_at == date(2026, 10, 22)


class TestReviewP2StrictEarlyYearEnds:
    """r1: a January–March year end was judged on its lenient (earlier) reading."""

    TARGET = FiscalYearAnchor(2025, date(2026, 1, 31))  # FY2025 ends 31 Jan 2026
    WALMART = FiscalYearAnchor(2026, date(2026, 1, 31))  # FY2026 ends 31 Jan 2026

    def test_a_start_year_labelled_year_has_not_ended_at_its_q3(self) -> None:
        assert not annual_has_ended(2026, date(2026, 11, 19), self.TARGET)
        assert not annual_has_ended(2025, date(2025, 11, 19), self.TARGET)
        assert not annual_has_ended(2027, date(2026, 11, 19), self.WALMART)

    def test_it_ends_once_both_readings_have(self) -> None:
        assert annual_has_ended(2025, date(2026, 2, 10), self.TARGET)
        assert not annual_has_ended(2025, date(2026, 2, 5), self.TARGET)

    def test_other_year_ends_are_unchanged(self) -> None:
        june = FiscalYearAnchor(2025, date(2025, 6, 30))
        assert annual_has_ended(2026, date(2026, 7, 8), june)
        assert not annual_has_ended(2025, date(2025, 12, 31))


class TestReviewP2WiresAreNotTheSubjectsOwn:
    """r5: a PR-wire release is T1_primary_company_source whoever issued it."""

    WIRE = "https://www.globenewswire.com/news-release/2026/08/13/odysight-ai-reports-h1.html"

    def _wire_fact(self, title: str, url: str = WIRE) -> dict[str, Any]:
        return _fact("net_income", -9.5, "H1 2026", value="Net loss was 9.5", url=url,
                     title=title, date_="2026-08-13", tier="T1_primary_company_source")

    def test_another_issuers_wire_release_is_withheld(self) -> None:
        state = build_financial_statements_state(
            [self._wire_fact("Odysight.ai Reports H1 2026 Results")], now=RUN,
            subject_name=HON, subject_ticker="HON")
        assert state["slots"] == {}
        assert state["withheld_counts"] == {WITHHELD_NOT_THE_SUBJECT: 1}

    def test_the_subjects_own_wire_release_counts(self) -> None:
        state = build_financial_statements_state(
            [self._wire_fact("Honeywell Reports Second Quarter 2026 Results")], now=RUN,
            subject_name=HON, subject_ticker="HON")
        assert "net_income_current_period" in state["slots"]

    def test_without_a_subject_a_wire_release_is_withheld(self) -> None:
        state = build_financial_statements_state(
            [self._wire_fact("Honeywell Reports Second Quarter 2026 Results")], now=RUN)
        assert state["withheld_counts"] == {WITHHELD_NOT_THE_SUBJECT: 1}

    def test_a_company_source_off_the_subjects_domain_must_name_it(self) -> None:
        own = self._wire_fact("Q2 2026 results", url="https://investor.honeywell.com/q2.pdf")
        other = self._wire_fact("Q2 2026 results", url="https://www.odysight.ai/press/h1")
        state = build_financial_statements_state([own], now=RUN, subject_name=HON)
        assert "net_income_current_period" in state["slots"]
        state = build_financial_statements_state([other], now=RUN, subject_name=HON)
        assert state["withheld_counts"] == {WITHHELD_NOT_THE_SUBJECT: 1}


class TestReviewP2AnnualReportsShowTheirYearEnded:
    """r1: annual reports of non-calendar issuers were withheld with no anchor."""

    NOW = datetime(2026, 11, 8, tzinfo=UTC)

    JUNE = FiscalYearAnchor(2025, date(2025, 6, 30))
    SEPTEMBER = FiscalYearAnchor(2025, date(2025, 9, 27))

    @pytest.mark.parametrize(
        ("title", "url", "date_", "anchor"),
        [
            ("Form 10-K", "https://www.sec.gov/x/10k.htm", "2026-08-01", JUNE),
            ("10-K 0000320193-26-000100", "https://www.sec.gov/x/a.htm", "2026-11-01",
             SEPTEMBER),
            ("Form 20-F", "https://www.sec.gov/x/infy-20260331.htm", "2026-06-20", None),
            ("Appendix 4E and Preliminary Final Report", "https://www.asx.com.au/a.pdf",
             "2026-08-20", JUNE),
            ("Final Results for the year ended 31 March 2026", "https://nsm.example/x.pdf",
             "2026-05-20", None),
            ("Annual results", "https://nsm.example/y.pdf", "2026-07-15", JUNE),
            ("Annual Report 2026", "https://nsm.example/z.pdf", "2026-09-15", None),
        ],
    )
    def test_the_annual_report_keeps_its_years_figures(self, title, url, date_, anchor) -> None:  # noqa: ANN001
        facts = [_fact("revenue", 4994, "2026", title=title, url=url, date_=date_)]
        state = build_financial_statements_state(facts, now=self.NOW, fiscal_anchor=anchor)
        assert state["reporting_periods"]["latest_annual"] == "FY2026", state["withheld"]

    @pytest.mark.parametrize(
        ("title", "url", "date_"),
        [
            ("Form 10-K", "https://www.sec.gov/x/10k.htm", "2026-08-01"),
            ("10-K/A", "https://www.sec.gov/Archives/edgar/data/1/2/doc.htm", "2026-08-15"),
            ("Appendix 4E and Preliminary Final Report", "https://www.asx.com.au/a.pdf",
             "2026-08-20"),
            ("Annual results", "https://nsm.example/y.pdf", "2026-07-15"),
            ("Annual report", "https://ir.x.example/ar.pdf", "2026-07-15"),
        ],
    )
    def test_a_same_year_date_alone_never_ends_the_year(self, title, url, date_) -> None:  # noqa: ANN001
        """Re-review P1-a: an annual document dated in the year it would report, with no
        year in its name, no stated year end and no anchor that ends the year by then,
        does not make that year annual -- on a calendar-year anchor, nor without one."""
        for anchor in (None, DEC):
            facts = [_fact("revenue", 4994, "2026", title=title, url=url, date_=date_)]
            state = build_financial_statements_state(facts, now=self.NOW,
                                                     fiscal_anchor=anchor)
            assert state["slots"] == {}
            assert state["withheld_counts"] == {WITHHELD_ANNUAL_PERIOD_NOT_ENDED: 1}

    def test_the_first_pages_year_end_is_the_fiscal_year_end(self) -> None:
        fact = _fact("revenue", 4994, "2026", title="Final Results",
                     url="https://nsm.example/x.pdf", date_="2026-05-20")
        assert build_financial_statements_state([fact], now=self.NOW)["slots"] == {}
        fact["document_period_end"] = fin.period_end_from_text(
            "Preliminary results for the year ended 31 March 2026")
        fact["document_period_end"] = fact["document_period_end"].isoformat()
        state = build_financial_statements_state([fact], now=self.NOW)
        assert state["reporting_periods"]["latest_annual"] == "FY2026"

    def test_two_statement_columns_do_not_end_the_year(self) -> None:
        """Re-review P1-a: a "2026" column beside "2025" is not evidence that 2026 had
        ended -- only the document's stated year end, its named year or the anchor are."""
        facts = [_fact(field, value, year, title="Final Results",
                       url="https://nsm.example/x.pdf", date_="2026-05-20", doc="fr")
                 for field in ("revenue", "operating_profit", "net_income")
                 for year, value in (("2026", 5000), ("2025", 4500))]
        state = build_financial_statements_state(facts, now=self.NOW)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert state["withheld_counts"] == {WITHHELD_ANNUAL_PERIOD_NOT_ENDED: 3}

    def test_a_calendar_year_10k_never_makes_its_next_year_annual(self) -> None:
        """A 10-K dated February 2026 reports FY2025; a "2026" figure in it is forward."""
        facts = [_fact("capital_expenditure", 1200, "2026", title="Form 10-K",
                       url="https://www.sec.gov/x/10k.htm", date_="2026-02-13", doc="k"),
                 *[_fact(field, value, year, title="Form 10-K", doc="k",
                         url="https://www.sec.gov/x/10k.htm", date_="2026-02-13")
                   for field in ("revenue", "operating_profit", "net_income")
                   for year, value in (("2025", 5000), ("2024", 4500))]]
        state = build_financial_statements_state(facts, now=self.NOW)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"
        assert "capital_expenditure_primary_filing" not in state["slots"]

    def test_the_sec_file_names_period_of_report_dates_an_undated_10k(self) -> None:
        facts = [_fact("revenue", 37442, "2025"), _fact("revenue", 36662, "2024")]
        assert all(f["document_date"] is None for f in facts)
        state = build_financial_statements_state(facts, now=RUN)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"


class TestReviewP3ForwardCuesAndNumbers:
    @pytest.mark.parametrize(
        "value",
        ["Sales to Target 4,994", "Revenue 4,994 (see Outlook)",
         "4,994 (1) Includes amounts expected to be reclassified", "4,994*expected"],
    )
    def test_a_name_heading_or_footnote_is_not_a_forward_cue(self, value: str) -> None:
        state = build_financial_statements_state([_fact("revenue", 4994, "2025", value=value)],
                                                 now=RUN)
        assert state["slots"]["revenue_primary_filing"]["value"] == "4,994"

    @pytest.mark.parametrize("value", ["Guidance revenue of $19.8 billion",
                                       "Expected revenue of $19.8 billion"])
    def test_a_capitalised_cue_still_withholds(self, value: str) -> None:
        state = build_financial_statements_state(
            [_fact("revenue", 19.8, "2025", value=value, scale="billion")], now=RUN)
        assert state["withheld_counts"] == {WITHHELD_FORWARD_STATEMENT: 1}

    @pytest.mark.parametrize(
        ("value", "numeric", "scale", "expected"),
        [("(-)", 0, None, "0"), ("Nil.", 0, None, "0"),
         ("US$1.2 billion", 1200, "million", "1,200"),
         ("€174.5m", 174_500, "thousand", "174,500"), ("€174.5m", 174.5, "million", "174.5"),
         ("€2.1bn", 2.1e9, None, "2,100,000,000")],
    )
    def test_nil_and_scale_words_read_as_numbers(self, value, numeric, scale, expected) -> None:  # noqa: ANN001
        fact = {"value": value, "numeric_value": numeric, "scale": scale}
        assert slot_number(fact) == expected

    @pytest.mark.parametrize(
        ("value", "numeric", "scale"),
        [("US$1.2 billion", 1200, "billion"), ("US$1.2 billion", 1200, None),
         ("€174.5m", 174.5, "thousand"), ("€174.5m", 174_500, "million"),
         ("US$1.2 billion", 1200, "lakh")],
    )
    def test_a_scale_word_must_agree_with_the_facts_own_scale(self, value, numeric, scale) -> None:  # noqa: ANN001
        """Re-review P2: "US$1.2 billion" stored as 1,200 BILLION is 1.2 trillion."""
        assert slot_number({"value": value, "numeric_value": numeric, "scale": scale}) is None

    @pytest.mark.parametrize(("value", "numeric"), [("US$1.2 billion", 1300),
                                                    ("(-)", 5), ("4 487 + 3", 4490)])
    def test_a_different_number_is_still_refused(self, value, numeric) -> None:  # noqa: ANN001
        assert slot_number({"value": value, "numeric_value": numeric}) is None

    def test_period_end_from_text(self) -> None:
        read = fin.period_end_from_text
        assert read("for the year ended 30 June 2026") == date(2026, 6, 30)
        assert read("Fiscal year ended September 27, 2026") == date(2026, 9, 27)
        assert read("for the quarterly period ended September 30, 2026") is None
        assert read("year ended 31 Foo 2026") is None


class TestReReviewPartYearDocuments:
    """Re-review of 151a8b1 (r6): part-year documents read as annual reports."""

    NOW = datetime(2026, 11, 8, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("title", "date_", "url"),
        [
            ("Semi-Annual Report 2026", "2026-08-28", "https://ir.x.example/sa-2026.pdf"),
            ("Semi-annual financial report", "2026-09-10", "https://ir.x.example/rfs.pdf"),
            ("Half-year 2026 update to the Annual Report", "2026-08-01",
             "https://ir.x.example/h1.pdf"),
            ("Quarterly and Annual Report", "2026-10-25", "https://ir.x.example/q3.pdf"),
            ("Interim Annual Report 2026", "2026-08-01", "https://ir.x.example/i.pdf"),
            ("Report", "2026-08-28", "https://ir.x.example/semi-annual-report-2026.pdf"),
            ("Form 10-Q", "2026-10-25", "https://ir.x.example/q.pdf"),
        ],
    )
    def test_a_part_year_document_never_ends_its_year(self, title, date_, url) -> None:  # noqa: ANN001
        for anchor in (None, DEC):
            fact = _fact("revenue", 4994, "2026", title=title, date_=date_, url=url)
            assert fin._ineligibility(fact, as_of=self.NOW.date(), anchor=anchor) == (
                WITHHELD_ANNUAL_PERIOD_NOT_ENDED)

    def test_part_year_names_are_not_annual_names(self) -> None:
        for name in ("Semi-Annual Report 2026", "Semiannual Report 2026",
                     "Half Year Annual Financial Report", "Six months annual report 2026",
                     "Q3 annual results"):
            assert not fin._is_annual_name(name), name
        for name in ("Annual Report 2026", "ON Semiconductor Annual Report 2025",
                     "Form 10-K"):
            assert fin._is_annual_name(name), name

    def test_a_semi_annual_reports_bare_year_never_fills_the_annual_slot(self) -> None:
        facts = [
            _fact("revenue", 4994, "2026", title="Semi-Annual Report 2026",
                  date_="2026-08-28", url="https://ir.x.example/sa.pdf", doc="sa"),
            _fact("revenue", 4994, "H1 2026", title="Semi-Annual Report 2026",
                  date_="2026-08-28", url="https://ir.x.example/sa.pdf", doc="sa"),
        ]
        state = build_financial_statements_state(facts, now=self.NOW, fiscal_anchor=DEC)
        assert state["reporting_periods"].get("latest_annual") is None
        assert "revenue_primary_filing" not in state["slots"]

    def test_a_half_year_end_is_not_a_year_end(self) -> None:
        read = fin.period_end_from_text
        for text in ("for the half-year ended 30 June 2026", "Half year ended 30 June 2026",
                     "for the half\u00a0year ended 30 June 2026",
                     "semi-year ended June 30, 2026"):
            assert read(text) is None, text
        assert read("for the year ended 30 June 2026") == date(2026, 6, 30)
        fact = _fact("revenue", 4994, "2026", title="Annual Financial Report",
                     date_="2026-08-15", url="https://ir.x.example/x.pdf")
        fact["document_title"] = "Annual Financial Report for the half-year ended 30 June 2026"
        assert fin._ineligibility(fact, as_of=self.NOW.date(), anchor=None) == (
            WITHHELD_ANNUAL_PERIOD_NOT_ENDED)

    def test_a_recorded_year_end_off_the_anchor_is_ignored(self) -> None:
        fact = _fact("revenue", 4994, "2026", title="Annual Financial Report",
                     date_="2026-08-15", url="https://ir.x.example/x.pdf")
        fact["document_period_end"] = "2026-06-30"
        assert fin._ineligibility(fact, as_of=self.NOW.date(), anchor=DEC) == (
            WITHHELD_ANNUAL_PERIOD_NOT_ENDED)
        june = FiscalYearAnchor(2025, date(2025, 6, 30))
        assert fin._ineligibility(fact, as_of=self.NOW.date(), anchor=june) is None


class TestReReviewHeldAnnualReportAndPartYearDocument:
    """Re-review P1-b: a held FY2026 10-K let an UNDATED Q3 release's bare "2026"
    operating profit (the quarter's 1,062) in as FY2026."""

    def _facts(self) -> list[dict[str, Any]]:
        return [
            _fact("revenue", 38100, "2026", title="10-K", date_="2027-02-12",
                  url="https://www.sec.gov/Archives/edgar/data/773840/x/hon-20261231.htm",
                  doc="k26"),
            _fact("operating_profit", 1062, "2026", url=Q3_EX99,
                  title="8-K 0000773840-26-000141", doc="ex99"),
            _fact("revenue", 4994, "2026-Q3", url=Q3_EX99,
                  title="8-K 0000773840-26-000141", doc="ex99"),
        ]

    @pytest.mark.parametrize("anchor", [None, DEC])
    def test_the_part_year_documents_bare_year_stays_withheld(self, anchor) -> None:  # noqa: ANN001
        state = build_financial_statements_state(
            self._facts(), now=datetime(2027, 3, 1, tzinfo=UTC), fiscal_anchor=anchor)
        assert state["reporting_periods"]["latest_annual"] == "FY2026"
        assert state["slots"]["revenue_primary_filing"]["value"] == "38,100"
        assert "operating_profit_primary_filing" not in state["slots"]
        assert state["withheld_counts"] == {WITHHELD_ANNUAL_PERIOD_UNDATED: 1}

    def test_a_half_year_reports_prior_year_comparative_still_counts(self) -> None:
        facts = [
            _fact("revenue", 9000, "2025", url=Q3_EX99, title="Half-year report", doc="h1"),
            _fact("revenue", 4994, "H1 2026", url=Q3_EX99, title="Half-year report",
                  doc="h1"),
            _fact("revenue", 4500, "H1 2025", url=Q3_EX99, title="Half-year report",
                  doc="h1"),
        ]
        state = build_financial_statements_state(facts, now=RUN, fiscal_anchor=DEC)
        assert state["reporting_periods"]["latest_annual"] == "FY2025"


class TestReReviewWireLeadEntity:
    """Re-review P3: a wire title naming the subject only in passing, and short tickers."""

    WIRE = "https://www.globenewswire.com/news-release/2026/08/13/x.html"

    def _wire(self, title: str) -> dict[str, Any]:
        return _fact("net_income", -9.5, "H1 2026", value="Net loss was 9.5", url=self.WIRE,
                     title=title, date_="2026-08-13", tier="T1_primary_company_source")

    @pytest.mark.parametrize(
        "title",
        ["Odysight.ai, a Honeywell supplier, reports H1 2026 results",
         "Acme Corp Reports H1 2026 Results; Honeywell Contract Extended",
         "Acme (Honeywell partner) reports H1 2026",
         "Acme Corp and Honeywell sign a supply agreement"],
    )
    def test_a_passing_mention_is_not_the_subjects_release(self, title: str) -> None:
        assert fin._ineligibility(self._wire(title), as_of=RUN.date(), anchor=None,
                                  subject_name=HON, subject_ticker="HON") == (
            WITHHELD_NOT_THE_SUBJECT)

    @pytest.mark.parametrize(
        "title",
        ["Honeywell Reports Third Quarter 2026 Results",
         "Press Release: Honeywell International Inc. Announces Q3 2026 Results",
         "GLOBE NEWSWIRE - Honeywell Reports Q3", "HON: Third quarter 2026 results"],
    )
    def test_the_subject_leading_the_title_counts(self, title: str) -> None:
        assert fin._ineligibility(self._wire(title), as_of=RUN.date(), anchor=None,
                                  subject_name=HON, subject_ticker="HON") is None

    def test_a_two_letter_ticker_never_names_the_subject(self) -> None:
        fact = self._wire("ON THE MOVE: Acme Corp reports H1 2026 results")
        assert fin._ineligibility(fact, as_of=RUN.date(), anchor=None,
                                  subject_name="Ohm Nordic AB", subject_ticker="ON") == (
            WITHHELD_NOT_THE_SUBJECT)
        own = self._wire("ON Semiconductor Reports H1 2026 Results")
        assert fin._ineligibility(own, as_of=RUN.date(), anchor=None,
                                  subject_name="ON Semiconductor", subject_ticker="ON") is None


# ── Defect 2: the re-read of held primary documents ────────────────────────── #


SUBJECT = uuid.UUID("44444444-4444-4444-4444-444444444444")
CORPUS = "search_company_corpus"
INVENTORY = (
    {"title": "hon-20260930.htm", "canonical_url": TEN_Q, "document_type": "10-Q",
     "published_at": "2026-10-22", "chunks": 243, "indexed_chunks": 243,
     "table_chunks": 82, "extraction_status": "extracted"},
    {"title": "exhibit99-q32026earningsre.htm", "canonical_url": Q3_EX99,
     "document_type": "results_release", "published_at": "2026-10-22", "chunks": 25,
     "indexed_chunks": 25, "table_chunks": 6, "extraction_status": "extracted"},
)
NARRATIVE_A = ("Honeywell is a diversified technology company. Its businesses serve "
               "aerospace, building automation and industrial automation customers.")
NARRATIVE_B = ("Management discusses liquidity, which it considers adequate for its needs "
               "over the next twelve months.")
DEBT_TABLE = ("Debt. Long-term debt 24,512 25,106 Commercial paper and other short-term "
              "borrowings 3,214 2,901 Current maturities of long-term debt 1,450 2,180 "
              "Total debt 29,176 30,187")
CASH_FLOW_TABLE = ("Nine months ended September 30, 2026. Net cash provided by operating "
                   "activities 4,512 Capital expenditures (712) Free cash flow 3,800")


def _hit(eid: str, text: str, url: str, *, table: bool = False) -> dict[str, Any]:
    return {"evidence_id": eid, "text": text, "canonical_url": url,
            "title": url.rsplit("/", 1)[-1], "source_tier": "T1_primary_filing",
            "table_location": "table 3" if table else None, "section_path": None,
            "document_type": "filing"}


@dataclass
class _Result:
    ok: bool = True
    payload: dict | None = None
    contains_untrusted_content: bool = False
    refusal_reason: str | None = None
    outcome: str = "ok"


@dataclass
class _Session:
    ordinary: list[dict] = field(default_factory=list)
    primary: list[dict] = field(default_factory=list)
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def call(self, tool: str, arguments: dict, task_ref: str = "") -> _Result:
        self.calls.append((tool, dict(arguments)))
        if tool == CORPUS:
            items = self.primary if arguments.get("primary_documents_only") else self.ordinary
            return _Result(payload={"items": [dict(i) for i in items]},
                           contains_untrusted_content=True)
        if tool == "search_web":
            raise AssertionError("a count-satisfied question must not search the web")
        return _Result(payload={"items": []})


def _worker(session: _Session, *, max_tool_calls: int | None = None) -> LLMInvestigator:
    worker = LLMInvestigator(
        session=session, company_id=SUBJECT, ticker="HON", exchange="NASDAQ", client=None,
        company_name="Honeywell International Inc.", industry="Industrials",
        primary_documents=INVENTORY,
    )
    worker.max_tool_calls = max_tool_calls
    return worker


async def _acquire(worker: LLMInvestigator, question: Any, role_id: str, budget: int = 30):  # noqa: ANN202
    return await worker._acquire(
        role_id, role_for(role_id), question, budget, context=QuestionContext(),
        round_index=0,
    )


def _primary_calls(session: _Session) -> list[dict]:
    return [a for t, a in session.calls if t == CORPUS and a.get("primary_documents_only")]


class TestCountSatisfiedQuestionsReread:
    async def test_the_leverage_question_reads_the_debt_table(self) -> None:
        """HON3: balance_sheet_risk was 'satisfied' by 40 items and still answered "No
        debt balance is available for 2026-Q3"; the 10-Q's debt table was never read."""
        session = _Session(
            ordinary=[_hit("ev:a", NARRATIVE_A, TEN_Q), _hit("ev:b", NARRATIVE_B, Q3_EX99)],
            primary=[_hit("ev:debt", DEBT_TABLE, TEN_Q, table=True)],
        )
        question = planned_from(base_question("balance_sheet_risk"), origin="director")
        evidence, _used, steps = await _acquire(_worker(session), question,
                                                "financial_analyst")
        calls = _primary_calls(session)
        assert calls and calls[0]["query"] == dr.TOPICS_BY_KEY["total_debt"].query
        step = next(s for s in steps if s["rung"] == "primary_document_reread")
        assert step["trigger"] == "topics_not_carried"
        assert step["why_topics"][0] == "total_debt"
        assert step["queries_run"] >= 1
        assert evidence[0].citation_id == "ev:debt"
        final = next(s for s in steps if s["rung"] == "contract_after_round")
        assert final["status"] == "satisfied"
        assert not any(s["rung"] == "external_search" for s in steps)

    async def test_the_cash_generation_question_reads_the_cash_flow_table(self) -> None:
        session = _Session(
            ordinary=[_hit("ev:a", NARRATIVE_A, TEN_Q), _hit("ev:b", NARRATIVE_B, Q3_EX99)],
            primary=[_hit("ev:cf", CASH_FLOW_TABLE, TEN_Q, table=True)],
        )
        question = planned_from(base_question("cash_generation_and_funding"),
                                origin="director")
        evidence, _u, steps = await _acquire(_worker(session), question, "financial_analyst")
        step = next(s for s in steps if s["rung"] == "primary_document_reread")
        assert set(step["topics"]) & {"capex", "operating_cash_flow", "total_debt"}
        assert any(e.citation_id == "ev:cf" for e in evidence)

    async def test_a_question_whose_evidence_carries_its_figures_spends_nothing(self) -> None:
        session = _Session(
            ordinary=[_hit("ev:debt", DEBT_TABLE, TEN_Q, table=True),
                      _hit("ev:b", "Net debt was 23,482 at September 30, 2026; the "
                           "revolving credit facility of 4,000 was undrawn.", Q3_EX99)],
            primary=[_hit("ev:x", CASH_FLOW_TABLE, TEN_Q, table=True)],
        )
        question = planned_from(base_question("balance_sheet_risk"), origin="director")
        _e, _u, steps = await _acquire(_worker(session), question, "financial_analyst")
        assert _primary_calls(session) == []
        assert not any(s["rung"] == "primary_document_reread" for s in steps)

    async def test_a_starved_run_keeps_its_calls_for_unanswered_questions(self) -> None:
        session = _Session(
            ordinary=[_hit("ev:a", NARRATIVE_A, TEN_Q), _hit("ev:b", NARRATIVE_B, Q3_EX99)],
            primary=[_hit("ev:debt", DEBT_TABLE, TEN_Q, table=True)],
        )
        question = planned_from(base_question("balance_sheet_risk"), origin="director")
        _e, _u, steps = await _acquire(_worker(session, max_tool_calls=40), question,
                                       "financial_analyst", budget=6)
        assert _primary_calls(session) == []
        step = next(s for s in steps if s["rung"] == "primary_document_reread")
        assert step["stopped"] == "run_tool_budget_low" and step["tool_calls"] == 0

    async def test_the_valuation_role_rereads_through_its_ladder(self) -> None:
        """HON3 working_capital: ``role_cannot_read_corpus`` — the valuation role holds
        no corpus tool, so the question was written up as not acquired."""
        role = role_for("valuation_context_analyst")
        assert not role.can_use(CORPUS)
        assert role.can_acquire_with(CORPUS)
        playbook = next(p for p in PLAYBOOKS if p.playbook_id == "industrial_defense")
        question = planned_from(
            next(q for q in playbook.questions if q.key == "working_capital"),
            origin="playbook")
        session = _Session(primary=[_hit("ev:cf", CASH_FLOW_TABLE, TEN_Q, table=True)])
        evidence, _u, steps = await _acquire(_worker(session), question,
                                             "valuation_context_analyst")
        step = next(s for s in steps if s["rung"] == "primary_document_reread")
        assert step.get("stopped") != "role_cannot_read_corpus"
        assert _primary_calls(session)
        assert any(e.citation_id == "ev:cf" for e in evidence)

    def test_question_prose_asks_for_topics_documents_name_otherwise(self) -> None:
        asked = dr.topics_asked(
            "How has cash been allocated across capex, dividends, buybacks, debt reduction "
            "and acquisitions?")
        assert {"capex", "capital_returns", "total_debt", "acquisitions"} <= set(asked)
        assert "total_debt" in dr.topics_asked("What is the leverage position?")
        assert {"operating_margin", "gross_margin"} <= set(
            dr.topics_asked("What are the reported margins?"))
        # Evidence is still judged by the lexicon's own mentions only: "leverage" in a
        # passage carries no debt figure.
        assert "total_debt" not in dr.metrics_carried("Leverage was 2.1 times.")

    def test_the_ladder_allowance_covers_the_reread(self) -> None:
        from app.services.pipeline.v3_pipeline import LADDER_CALLS_PER_QUESTION

        assert LADDER_CALLS_PER_QUESTION >= 2 + dr.MAX_REREAD_QUERIES + 1 + 4


# ── Defect 2b: an 8-K exhibit carries its filing's date ────────────────────── #


class TestExhibitDate:
    async def test_the_sec_leg_dates_an_exhibit_with_its_filing_date(self, monkeypatch) -> None:  # noqa: ANN001
        from app.core.config import Settings
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult
        from app.services.sources.sec_filing_documents import SecFilingDocument

        cfg = Settings()
        cfg.primary_document_ingestion_enabled = True
        cfg.primary_document_sec_body_enabled = True
        cfg.primary_document_sec_max_bodies = 2
        doc = SecFilingDocument(
            accession_number="0000773840-26-000141", form_type="8-K",
            filing_date="2026-10-22", canonical_url=Q3_EX99,
            document_name="exhibit99-q32026earningsre.htm", cik="0000773840",
            title="SEC 8-K filing", is_exhibit=True, item_codes=("2.02", "9.01"),
        )

        async def _resolve(cik, filings, *, max_documents, **kwargs):  # noqa: ANN001, ANN003, ANN202
            return [doc]

        async def _fetch(document, **kwargs):  # noqa: ANN001, ANN003, ANN202
            return DocumentFetchResult(
                requested_url=Q3_EX99, final_url=Q3_EX99, status_code=200,
                content_type="text/html", document_type="html",
                content=b"<html><body><p>Third quarter 2026 results.</p></body></html>",
            )

        monkeypatch.setattr(live_fetchers, "resolve_filing_documents", _resolve)
        monkeypatch.setattr(live_fetchers, "fetch_filing_body", _fetch)
        [artifact] = await live_fetchers.live_sec_primary_document_extractor(
            "773840", [{"accession_number": "0000773840-26-000141", "form_type": "8-K",
                        "filed_date": "2026-10-22"}], cfg=cfg, max_documents=1)
        assert artifact.published_at == date(2026, 10, 22)


@pytest.fixture
async def db_session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


class TestUndatedVersionBackfill:
    async def test_a_held_undated_exhibit_takes_its_filing_date(self, db_session) -> None:  # noqa: ANN001
        from app.models.research_document import ResearchDocumentVersion
        from app.services.corpus.documents import DocumentVersionInput, upsert_document_version

        cfg = SimpleNamespace(v3_corpus_enabled=True)
        base = dict(content_hash="a" * 64, canonical_url=Q3_EX99, transport="sec_edgar",
                    source_tier="T1_primary_filing", company_id=SUBJECT,
                    document_type="results_release", title="8-K 0000773840-26-000141")
        first = await upsert_document_version(
            db_session, DocumentVersionInput(**base), cfg=cfg)
        assert first is not None and first.published_at is None
        again = await upsert_document_version(
            db_session, DocumentVersionInput(**base, published_at=date(2026, 10, 22)),
            cfg=cfg)
        assert again is not None and again.id == first.id
        row = (await db_session.execute(
            select(ResearchDocumentVersion).where(ResearchDocumentVersion.id == first.id)
        )).scalar_one()
        await db_session.refresh(row)
        assert row.published_at is not None and str(row.published_at)[:10] == "2026-10-22"
        # Never overwritten once recorded.
        await upsert_document_version(
            db_session, DocumentVersionInput(**base, published_at=date(2027, 1, 1)), cfg=cfg)
        await db_session.refresh(row)
        assert str(row.published_at)[:10] == "2026-10-22"


# ── Every new pattern is linear on adversarial input ───────────────────────── #


_ADVERSARIAL = (" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000,
                "1," * 5000, "1 " * 5000, "9" * 10000)


@pytest.mark.parametrize("text", _ADVERSARIAL, ids=lambda t: repr(t[:4]))
def test_new_patterns_are_linear(text: str) -> None:
    patterns = [fin._SHORT_FY_RE, fin._NAME_SEPARATORS_RE, fin._SCALE_WORD_RE,
                fin._FOOTNOTE_RE, fin._PARENTHETICAL_RE, fin._FILE_DATE_RE,
                fin._YEAR_ENDED_RE,
                fin._FIGURE_TOKEN_RE, fin._FORWARD_FACT_RE, fin._ANNUAL_TITLE_RE,
                fin._NOT_ANNUAL_TITLE_RE, fin._PART_YEAR_TITLE_RE, fin._TITLE_LABEL_RE,
                fin._LEAD_END_RE, *(p for p, _t in dr._QUESTION_CUES)]
    for key in ("capital_returns", "earnings_per_share", "acquisitions"):
        patterns.extend(dr._CARRIED[key])
    def best_of_three(run) -> float:  # noqa: ANN001
        # The fastest of three runs: a loaded machine slows one run, never all three of
        # a linear scan, while a backtracking pattern is slow every time.
        times = []
        for _ in range(3):
            started = time.perf_counter()
            run()
            times.append(time.perf_counter() - started)
        return min(times)

    for pattern in patterns:
        assert best_of_three(lambda p=pattern: list(p.finditer(text))) < 0.2, pattern.pattern
    assert best_of_three(lambda: (slot_number({"value": text, "numeric_value": 1.0}),
                                  dr.topics_asked(text))) < 0.4
    assert best_of_three(lambda: fin._names_subject(text, HON, "HON")) < 0.2
