"""V3.21.10 (ADR-120): the European discovery run and the remaining report defects.

* Run ``cf72de91`` ("European small-cap cybersecurity software companies with growing
  recurring revenue, under-followed by analysts") returned nothing. Every LSE lead failed
  listing or size: ``api.londonstockexchange.com`` no longer resolves. The run also listed
  "cybersecurity", "recurring", "revenue", "under", "followed", "analysts" as not
  understood, although the segment and the growth request had been read.
* From the report review of 2026-11-24 (docs/validation/):
  - an auto-report carried no discovery lineage;
  - a cybersecurity company was shown US electricity demand;
  - net debt came from a ten-month-old annual balance sheet, while the newest 10-Q held
    599m of convertibles due within a year.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.discovery.intent import SOFT, build_intent, intent_from_dict
from app.services.industry_data import context as ctx
from app.services.pipeline.enterprise_value_step import (
    newest_balance,
    quarterly_balance_from_facts,
)
from app.services.valuation.enterprise_value import enterprise_value_block

API = Path(__file__).resolve().parents[1]
RPD_DEBT = Path(__file__).parent / "fixtures" / "sec_growth" / "rpd_debt_cash.json"
EU = ("European small-cap cybersecurity software companies with growing recurring revenue, "
      "under-followed by analysts")


# ── The thesis as read ──────────────────────────────────────────────────── #


def test_under_followed_is_a_soft_preference_never_a_filter() -> None:
    intent = build_intent(EU, max_market_cap_usd=2_000_000_000)
    assert intent.coverage is not None
    assert intent.coverage.hardness == SOFT and intent.coverage.requested == ("under_followed",)
    assert intent.coverage not in intent.hard()
    assert intent_from_dict(intent.to_dict()).coverage == intent.coverage


@pytest.mark.parametrize("text", [
    "UK software companies under the radar",
    "small caps with low analyst coverage",
    "Nordic industrials overlooked by the market",
    "companies under-covered by sell-side analysts",
])
def test_other_ways_of_saying_it(text: str) -> None:
    assert build_intent(text).coverage is not None


def test_a_negated_request_is_not_a_preference() -> None:
    assert build_intent("companies that are not under the radar").coverage is None


def test_words_the_thesis_was_understood_by_are_not_listed_as_not_understood() -> None:
    intent = build_intent(EU, max_market_cap_usd=2_000_000_000)
    assert intent.segment_terms == ("cybersecurity",)
    leftover = set(intent.unmatched_terms)
    assert not leftover & {"cybersecurity", "recurring", "revenue", "under", "followed",
                           "analysts", "growing", "european", "small", "cap"}


def test_the_pipeline_reports_coverage_as_unknown_and_never_fails_it() -> None:
    src = (API / "app/services/discovery/pipeline.py").read_text()
    block = src[src.index("if intent.coverage is not None:"):][:700]
    assert "cons.UNKNOWN" in block and "never excludes" in block


# ── Auto-report lineage ─────────────────────────────────────────────────── #


async def test_a_job_started_from_a_candidate_gets_its_lineage(monkeypatch) -> None:
    from app.services import company_research_service as svc
    from app.services import market_discovery_service as mds

    run_id, cand_id = uuid.uuid4(), uuid.uuid4()
    candidate = SimpleNamespace(
        id=cand_id, discovery_run_id=run_id, ticker="OSPN", exchange="US", rank=1,
        candidate_score=57.4, candidate_score_grade="medium_internal_interest",
        score_explanation="x", thesis_relevance_score=13.0, thesis_match_json={})

    class _Session:
        async def get(self, model, key):  # noqa: ANN001, ANN202
            assert key == cand_id
            return candidate

    async def _run(db, rid):  # noqa: ANN001, ANN202
        return SimpleNamespace(thesis_text="US cybersecurity")

    monkeypatch.setattr(mds, "get_run", _run)
    lineage = await svc._lineage_for_request(_Session(), str(cand_id))
    assert lineage["discovery_run_id"] == str(run_id)
    assert lineage["discovery_candidate_id"] == str(cand_id)
    assert lineage["thesis_text"] == "US cybersecurity"
    assert await svc._lineage_for_request(_Session(), None) is None


async def test_a_missing_candidate_or_a_failure_is_no_lineage() -> None:
    from app.services import company_research_service as svc

    class _Empty:
        async def get(self, model, key):  # noqa: ANN001, ANN202
            return None

    class _Boom:
        async def get(self, model, key):  # noqa: ANN001, ANN202
            raise RuntimeError("db")

    assert await svc._lineage_for_request(_Empty(), str(uuid.uuid4())) is None
    assert await svc._lineage_for_request(_Boom(), str(uuid.uuid4())) is None


def test_the_job_passes_the_lineage_to_the_report() -> None:
    src = (API / "app/services/company_research_service.py").read_text()
    call = src[src.index("discovery_lineage = await _lineage_for_request("):][:1200]
    assert "discovery_lineage=discovery_lineage," in call


# ── Electricity context only for the electricity stack ──────────────────── #


async def test_a_company_outside_the_electricity_stack_gets_no_industry_context() -> None:
    cfg = SimpleNamespace(v3_industry_data_ember_enabled=True,
                          v3_industry_data_irena_enabled=True,
                          v3_industry_data_eia_enabled=True)
    company = SimpleNamespace(exchange="US", country="United States", name="Rapid7")
    block = await ctx.industry_context_block(
        None, company, cfg=cfg, layers_block={"status": "not_electricity"})
    assert block is None


# ── Net debt from the newest 10-Q ───────────────────────────────────────── #


def test_rapid7_net_debt_at_its_q3_2026_balance_sheet() -> None:
    balance = quarterly_balance_from_facts(json.loads(RPD_DEBT.read_text()))
    assert balance is not None
    assert balance["period_end"] == "2026-09-30" and balance["filed"] == "2026-11-04"
    assert float(balance["total_debt"]) == pytest.approx(895_240_000)
    assert float(balance["current_debt"]) == pytest.approx(598_850_000)
    assert float(balance["cash"]) == 335_714_000
    block = enterprise_value_block(
        market_cap={"amount": 980_061_482, "currency": "USD", "as_of": "2026-11-20"},
        balance=balance)
    assert block["net_debt"]["value"] == 559_526_000
    assert block["net_debt"]["debt_due_within_one_year"] == 598_850_000
    assert block["enterprise_value"]["balance_sheet_age_days"] == 16


def test_the_newer_filing_wins() -> None:
    annual = {"filed": "2026-02-19", "period": "FY2025"}
    quarter = {"filed": "2026-11-04", "period": "2026-09-30 (10-Q)"}
    assert newest_balance(annual, quarter) is quarter
    assert newest_balance({"filed": "2026-11-10"}, quarter)["filed"] == "2026-11-10"
    assert newest_balance(annual, None) is annual and newest_balance(None, quarter) is quarter


def test_no_10q_or_no_debt_concept_gives_no_quarterly_balance() -> None:
    assert quarterly_balance_from_facts({}) is None
    only_cash = {"facts": {"us-gaap": {"CashAndCashEquivalentsAtCarryingValue": {"units": {
        "USD": [{"end": "2026-09-30", "val": 10, "form": "10-Q", "accn": "A",
                 "filed": "2026-11-04"}]}}}}}
    assert quarterly_balance_from_facts(only_cash) is None  # zero debt is never assumed
