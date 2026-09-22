"""V3.18.6 — peers from the platform's universe, compared on identical definitions.

`get_peer_set` and `get_peer_financials` were declared and never implemented, so every
report said no peer comparison was available — a statement about this platform. A peer
here is a company the platform HOLDS, never a name a model suggested, and it is verified
by its regulator statements normalising under the SAME own-period rule and SAME defined
metrics as the subject.
"""

from __future__ import annotations

import copy
import json
import pathlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.services.agent_tools import peers
from app.services.director import contracts as c

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_discontinued_concept.json"


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _company(session, ticker, name, industry):  # noqa: ANN001, ANN202
    company = Company(id=uuid.uuid4(), ticker=ticker, exchange="US", name=name,
                      industry=industry, status="new")
    session.add(company)
    await session.flush()
    return company


class TestPeerSet:
    async def test_same_industry_companies_ranked_by_shared_commodity(self, session) -> None:
        subject = await _company(session, "SCCO", "Southern Copper Corp", "Metals & Mining")
        await _company(session, "FCX", "Freeport-McMoRan Copper & Gold", "Metals & Mining")
        await _company(session, "MP", "MP Materials Corp", "Metals & Mining")
        await _company(session, "NVDA", "NVIDIA", "Semiconductors")
        context = SimpleNamespace(session=session)
        payload = await peers._get_peer_set(
            context, peers.validate_get_peer_set(
                {"company_id": str(subject.id), "commodity": "copper"})
        )
        tickers = [i["ticker"] for i in payload["items"]]
        assert tickers[0] == "FCX", "the peer that shares the commodity comes first"
        assert "NVDA" not in tickers and "SCCO" not in tickers
        assert payload["gaps"], "fewer than three copper peers must be said out loud"

    async def test_a_subject_without_an_industry_has_no_peer_set(self, session) -> None:
        subject = await _company(session, "X", "X Corp", None)
        payload = await peers._get_peer_set(
            SimpleNamespace(session=session),
            peers.validate_get_peer_set({"company_id": str(subject.id)}),
        )
        assert payload["items"] == [] and payload["gaps"]


class TestPeerFinancials:
    async def test_every_peer_is_measured_with_the_subjects_definitions(self, monkeypatch) -> None:
        subject_facts = json.loads(FIXTURE.read_text())
        peer_facts = copy.deepcopy(subject_facts)
        for rows in peer_facts["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"].values():
            for row in rows:
                row["val"] = row["val"] // 2

        async def facts_for(ticker, exchange, provider):  # noqa: ANN001, ANN202
            if ticker == "NOPE":
                return None, "not a resolvable SEC registrant (ValueError)"
            return (subject_facts if ticker == "SCCO" else peer_facts), None

        monkeypatch.setattr(peers, "_facts_for", facts_for)
        payload = await peers._get_peer_financials(
            SimpleNamespace(session=None),
            peers.validate_get_peer_financials({"tickers": ["SCCO", "FCX", "NOPE"]}),
        )
        assert payload["verified_peers"] == ["FCX", "SCCO"]
        margin = {i["ticker"]: i["value"] for i in payload["items"]
                  if i.get("metric_id") == "operating_margin"}
        assert margin["SCCO"] == pytest.approx(52.17, abs=0.01)
        assert margin["FCX"] == pytest.approx(26.09, abs=0.02)
        assert any("NOPE" in g for g in payload["gaps"])
        sample = next(i for i in payload["items"] if i.get("metric_id") == "cash_conversion")
        assert sample["directionality"] == "higher_is_better" and sample["interpretation"]

    async def test_a_peer_withheld_line_is_not_compared(self, monkeypatch) -> None:
        """The subject's stale gross profit is withheld; so is any peer's."""
        async def facts_for(ticker, exchange, provider):  # noqa: ANN001, ANN202
            return json.loads(FIXTURE.read_text()), None

        monkeypatch.setattr(peers, "_facts_for", facts_for)
        payload = await peers._get_peer_financials(
            SimpleNamespace(session=None), peers.validate_get_peer_financials({"tickers": ["ANY"]})
        )
        assert not any(i.get("metric_id") == "gross_margin" for i in payload["items"])
        assert any("gross_profit withheld" in g for g in payload["gaps"])


def test_a_peers_filing_is_a_third_party_source_one_per_registrant() -> None:
    a = c.evidence_ref_for("get_peer_financials", "peerfin:FCX:FY2025:net_margin",
                           {"ticker": "FCX", "source_tier": "T1_primary_filing"})
    b = c.evidence_ref_for("get_peer_financials", "peerfin:FCX:FY2025:operating_margin",
                           {"ticker": "FCX", "source_tier": "T1_primary_filing"})
    assert a.source_kind == c.THIRD_PARTY_FILING
    assert a.source_ref == b.source_ref == "sec:FCX"


class TestTheChain:
    async def test_peer_set_is_chained_into_peer_financials_deterministically(self) -> None:
        from app.services.agents.investigator import LLMInvestigator
        from app.services.director.base_model import base_question
        from app.services.director.roles import role_for
        from app.services.playbooks.schema import planned_from

        calls: list[tuple[str, dict]] = []

        class _Session:
            async def call(self, tool, arguments, task_ref=""):  # noqa: ANN001, ANN202
                calls.append((tool, arguments))
                if tool == "get_peer_set":
                    return SimpleNamespace(ok=True, contains_untrusted_content=False, payload={
                        "items": [{"id": "peer:FCX:NYSE", "ticker": "FCX", "exchange": "NYSE"},
                                  {"id": "peer:BHP:LSE", "ticker": "BHP", "exchange": "LSE"}]})
                if tool == "get_peer_financials":
                    return SimpleNamespace(ok=True, contains_untrusted_content=False, payload={
                        "items": [{"id": "peerfin:FCX:FY2025:net_margin", "ticker": "FCX",
                                   "source_tier": "T1_primary_filing"}]})
                return SimpleNamespace(ok=True, contains_untrusted_content=False,
                                       payload={"items": []})

        worker = LLMInvestigator(session=_Session(), company_id=uuid.uuid4(), ticker="SCCO",
                                 exchange="US", available_tools=None)
        question = planned_from(base_question("competitive_position"), origin="director")
        evidence, _used = await worker._gather(
            "competitive_analyst", role_for("competitive_analyst"), question, 20
        )
        financials = next(args for tool, args in calls if tool == "get_peer_financials")
        assert [x["ticker"] for x in financials["listings"]] == ["SCCO", "FCX"], (
            "SEC-eligible listings only — NYSE is a US venue, LSE is not — subject included"
        )
        assert any(e.citation_id.startswith("peerfin:FCX") for e in evidence)

    async def test_a_subject_that_does_not_file_with_the_sec_is_not_compared(self) -> None:
        """Security review (M4): 'MC' on Euronext resolved against SEC's index is
        Moelis & Co — whose figures would have been shown as the subject's."""
        from app.services.agents.investigator import LLMInvestigator
        from app.services.director.base_model import base_question
        from app.services.director.roles import role_for
        from app.services.playbooks.schema import planned_from

        calls: list[tuple[str, dict]] = []

        class _Session:
            async def call(self, tool, arguments, task_ref=""):  # noqa: ANN001, ANN202
                calls.append((tool, arguments))
                payload = {"items": [{"ticker": "RMS", "exchange": "PA"},
                                     {"ticker": "TPR", "exchange": "NYSE"},
                                     {"ticker": "CPRI", "exchange": "NYSE"}]}
                return SimpleNamespace(ok=True, contains_untrusted_content=False,
                                       payload=payload if tool == "get_peer_set" else {})

        worker = LLMInvestigator(session=_Session(), company_id=uuid.uuid4(), ticker="MC",
                                 exchange="PA", available_tools=None)
        question = planned_from(base_question("competitive_position"), origin="director")
        await worker._gather("competitive_analyst", role_for("competitive_analyst"),
                             question, 20)
        financials = next(args for tool, args in calls if tool == "get_peer_financials")
        assert [x["ticker"] for x in financials["listings"]] == ["TPR", "CPRI"]

    async def test_the_tool_refuses_an_ineligible_listing_itself(self) -> None:
        facts, reason = await peers._facts_for("MC", "PA", provider=None)
        assert facts is None and "does not cover" in reason


class TestPeerNetDebtFollowsTheSubjectsRule:
    def _normalised(self, **over):  # noqa: ANN003, ANN202
        base = dict(
            fiscal_year=2025, period_basis="annual", revenue=1000.0, operating_income=200.0,
            net_income=120.0, operating_cash_flow=250.0, capital_expenditures=80.0,
            short_term_debt=100.0, long_term_debt=900.0, total_debt=1000.0,
            cash_and_equivalents=500.0, withheld_fields={}, consistency={},
            reporting_period_end="2025-12-31", source_url="https://www.sec.gov/x",
        )
        base.update(over)
        return SimpleNamespace(**base)

    def _metrics(self, monkeypatch, normalised):  # noqa: ANN001, ANN202
        from app.integrations import sec_fundamentals_normalizer as norm

        monkeypatch.setattr(norm, "normalize_company_facts", lambda facts, ticker: normalised)
        items, _gaps = peers._peer_metrics("ANY", {})
        return {i["metric_id"]: i["value"] for i in items}

    def test_both_legs_give_net_debt(self, monkeypatch) -> None:
        assert self._metrics(monkeypatch, self._normalised())["net_debt"] == 500.0

    def test_a_missing_debt_leg_refuses_net_debt(self, monkeypatch) -> None:
        """A partial total debt made a leveraged peer look like it held net cash."""
        partial = self._normalised(long_term_debt=None, total_debt=100.0)
        assert "net_debt" not in self._metrics(monkeypatch, partial)

    def test_an_inconsistent_statement_withholds_what_it_derives(self, monkeypatch) -> None:
        inconsistent = self._normalised(
            consistency={"inconsistencies": [{"withhold_derived": ["operating_margin"]}]}
        )
        assert "operating_margin" not in self._metrics(monkeypatch, inconsistent)


class TestTheSubjectsOwnStatements:
    """V3.18 live acceptance: SCCO's financial questions were empty — no validated
    extracted facts existed — while its SEC XBRL statements, the report's own source,
    were reachable only through the peers chain."""

    def test_statement_lines_and_metrics_for_the_reporting_period(self) -> None:
        items, gaps = peers.statement_items("SCCO", json.loads(FIXTURE.read_text()))
        by_metric = {i["metric_id"]: i for i in items}
        assert by_metric["revenue"]["value"] == pytest.approx(13420.0, abs=1)
        assert by_metric["revenue"]["id"] == "secfin:SCCO:FY2025:revenue"
        assert by_metric["operating_margin"]["id"].startswith("secfin:SCCO:")
        assert "gross_profit" not in by_metric, "last tagged FY2019: withheld, never shown"
        assert all(i["source_tier"] == "T1_primary_filing" for i in items)

    def test_they_are_the_issuers_own_filing(self) -> None:
        ref = c.evidence_ref_for("get_sec_statements", "secfin:SCCO:FY2025:revenue",
                                 {"ticker": "SCCO", "source_tier": "T1_primary_filing"})
        assert ref.source_kind == c.ISSUER_FILING and ref.source_ref == "sec:SCCO"

    def test_financial_questions_can_reach_them(self) -> None:
        from app.services.director.base_model import base_question
        from app.services.director.roles import role_for

        for key in ("profitability", "cash_generation_and_funding", "balance_sheet_risk",
                    "revenue_trajectory", "capital_allocation"):
            question = base_question(key)
            assert "get_sec_statements" in question.optional_tools, key
            assert role_for(question.owner_role).can_use("get_sec_statements"), key
