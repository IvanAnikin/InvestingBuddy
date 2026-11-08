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
    WITHHELD_FORWARD_STATEMENT,
    WITHHELD_NOT_A_NUMBER,
    WITHHELD_NOT_AN_ISSUER_DOCUMENT,
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
        assert reasons[WITHHELD_ANNUAL_PERIOD_NOT_ENDED] == 2
        assert reasons[WITHHELD_FORWARD_STATEMENT] == 1
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
        facts = [_fact("revenue", 38000, "2026", url=TEN_K,
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
                     value=value)
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
        assert "had not ended" in state["annual"]["reason"]

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
    patterns = [fin._SHORT_FY_RE, fin._NAME_SEPARATORS_RE,
                fin._FIGURE_TOKEN_RE, fin._FORWARD_FACT_RE, fin._ANNUAL_TITLE_RE,
                fin._NOT_ANNUAL_TITLE_RE, *(p for p, _t in dr._QUESTION_CUES)]
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
