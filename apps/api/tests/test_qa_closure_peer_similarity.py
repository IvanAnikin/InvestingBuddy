"""ADR-089 — a same-industry candidate is a peer only when it is COMPARABLE.

QA campaign defect ADBE-4 (P1): a creative-software company's peer set was a clinical-AI
diagnostics company and a search-and-advertising megacap, because peer selection took
every held company in the same coarse canonical industry ("Software & IT Services") and
checked nothing else. The same rule made an mRNA vaccine maker ~70x larger the only peer
of a gene-editing company (CLLS) and the subject itself one of its own peers (HON2).

The fixtures below are synthetic and modelled on the defect: SIC codes, descriptions and
market capitalisations are illustrative, not any company's real record.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.services import safety_terms
from app.services.agent_tools import peers
from app.services.agent_tools.peer_similarity import (
    BROAD_SIC_CODES,
    MAX_SIZE_RATIO,
    PASS_SCORE,
    assess_peer,
    business_themes,
)
from app.services.pipeline import professional_research as pr

INDUSTRY = "Software & IT Services"

CREATIVE = (
    "We provide creative software for photo editing, video editing and graphic design, "
    "and document software for PDF and e-signature workflows used by creative "
    "professionals and enterprises."
)
CLINICAL_AI = (
    "We apply artificial intelligence to precision medicine: our clinical laboratory "
    "performs genomic sequencing and molecular testing for oncology patients."
)
SEARCH_ADS = (
    "A collection of businesses, the largest of which operates a search engine, online "
    "advertising products, a video sharing service, a cloud platform and consumer devices."
)
DESIGN = (
    "We develop design software for architects and engineers, including computer-aided "
    "design and 3D modeling tools."
)


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):  # noqa: ANN001, ANN202
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


async def _company(session, ticker: str, **kw: Any) -> Company:  # noqa: ANN001
    company = Company(id=uuid.uuid4(), ticker=ticker, exchange=kw.pop("exchange", "US"),
                      name=kw.pop("name", f"{ticker} Inc"), industry=kw.pop("industry", INDUSTRY),
                      status="new", **kw)
    session.add(company)
    await session.flush()
    return company


def _co(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"ticker": "SUBJ", "sic_code": None, "description": None,
                            "market_cap": None, "currency": None, "legal_entity_id": None}
    base.update(kw)
    return SimpleNamespace(**base)


async def _peer_set(session, subject: Company, **args: Any) -> dict[str, Any]:  # noqa: ANN001
    return await peers._get_peer_set(
        SimpleNamespace(session=session),
        peers.validate_get_peer_set({"company_id": str(subject.id), **args}),
    )


class TestTheDefect:
    """ADBE-4 — only the design-software company is a peer of the creative-software one."""

    async def _universe(self, session, *, with_design: bool = True) -> Company:  # noqa: ANN001
        subject = await _company(session, "CRSW", sic_code="7372", description=CREATIVE,
                                 market_cap=150e9, currency="USD")
        await _company(session, "CLAI", sic_code="7372", description=CLINICAL_AI,
                       market_cap=10e9, currency="USD")
        await _company(session, "MEGA", sic_code="7370", description=SEARCH_ADS,
                       market_cap=2_400e9, currency="USD")
        if with_design:
            await _company(session, "DSGN", sic_code="7372", description=DESIGN,
                           market_cap=60e9, currency="USD")
        await _company(session, "CHIP", industry="Semiconductors", sic_code="3674")
        return subject

    async def test_only_the_design_software_company_passes(self, session) -> None:  # noqa: ANN001
        subject = await self._universe(session)
        payload = await _peer_set(session, subject)
        assert [i["ticker"] for i in payload["items"]] == ["DSGN"]
        design = payload["items"][0]
        assert design["similarity"]["comparable"] is True
        assert design["similarity"]["score"] >= PASS_SCORE
        assert any("design and creative software" in r for r in design["similarity"]["reasons"])
        assert "similarity check:" in design["basis"]

    async def test_excluded_candidates_are_recorded_with_reasons(self, session) -> None:  # noqa: ANN001
        subject = await self._universe(session)
        payload = await _peer_set(session, subject)
        excluded = {row["ticker"]: row for row in payload["excluded"]}
        assert set(excluded) == {"CLAI", "MEGA"}, "an other-industry company is not a candidate"
        assert payload["excluded_count"] == 2
        for row in excluded.values():
            assert row["reason"].startswith("not comparable")
            assert row["similarity"]["comparable"] is False
            assert row["similarity"]["reasons"]
        # The clinical-AI company shares the SIC code: the descriptions still separate them.
        clai = excluded["CLAI"]["similarity"]["reasons"]
        assert any("different businesses" in r for r in clai)
        assert "different line of business" in excluded["CLAI"]["reason"]
        assert any("2 held candidate(s)" in g for g in payload["gaps"])
        assert "excluded as not comparable" in payload["summary"]

    async def test_excluded_candidates_are_never_citable_items(self, session) -> None:  # noqa: ANN001
        from app.services.agent_tools.contracts import TOOL_GET_PEER_SET
        from app.services.agents.investigator import _harvest

        subject = await self._universe(session)
        payload = await _peer_set(session, subject)
        cited = {e.citation_id for e in _harvest(TOOL_GET_PEER_SET, payload, False)}
        assert not any("CLAI" in c or "MEGA" in c for c in cited)

    async def test_no_comparable_candidate_means_no_peer_comparison(self, session) -> None:  # noqa: ANN001
        subject = await self._universe(session, with_design=False)
        payload = await _peer_set(session, subject)
        assert payload["items"] == []
        assert {row["ticker"] for row in payload["excluded"]} == {"CLAI", "MEGA"}
        assert any("peer comparison is" in g and "unavailable" in g for g in payload["gaps"])
        selection = pr.peer_selection([payload])
        assert selection is not None and selection["status"] == "unavailable"
        assert selection["note"] == pr.PEER_COMPARISON_UNAVAILABLE_NOTE
        assert {row["ticker"] for row in selection["excluded"]} == {"CLAI", "MEGA"}

    async def test_an_empty_peer_set_is_not_chained_into_peer_financials(self) -> None:
        from app.services.agents.investigator import LLMInvestigator
        from app.services.director.base_model import base_question
        from app.services.director.roles import role_for
        from app.services.playbooks.schema import planned_from

        calls: list[str] = []

        class _Session:
            async def call(self, tool, arguments, task_ref=""):  # noqa: ANN001, ANN202
                calls.append(tool)
                payload = {"items": [], "excluded": [{"ticker": "CLAI", "exchange": "US"}]}
                return SimpleNamespace(ok=True, contains_untrusted_content=False,
                                       payload=payload if tool == "get_peer_set" else {})

        worker = LLMInvestigator(session=_Session(), company_id=uuid.uuid4(), ticker="CRSW",
                                 exchange="US", available_tools=None)
        question = planned_from(base_question("competitive_position"), origin="director")
        await worker._gather("competitive_analyst", role_for("competitive_analyst"),
                             question, 20)
        assert "get_peer_set" in calls and "get_peer_financials" not in calls


class TestOtherDefectShapes:
    def test_a_second_listing_of_the_subject_is_not_its_own_peer(self) -> None:
        """HON2 — 'peer:HON:US' sat in the subject's own peer set."""
        entity = uuid.uuid4()
        subject = _co(ticker="ACME", legal_entity_id=entity, sic_code="3823")
        assert not assess_peer(subject, _co(ticker="ACME", sic_code="3823")).comparable
        verdict = assess_peer(subject, _co(ticker="ACMEY", legal_entity_id=entity,
                                           sic_code="3823"))
        assert not verdict.comparable and "same issuer" in str(verdict.exclusion)

    def test_a_much_larger_company_in_the_same_sic_is_not_a_peer(self) -> None:
        """CLLS — a gene-editing company's only 'peer' was a vaccine maker ~70x its size."""
        subject = _co(sic_code="2836", market_cap=300e6, currency="USD",
                      description="Clinical-stage gene editing company developing "
                                  "allogeneic CAR-T cell therapies.")
        larger = _co(ticker="BIGV", sic_code="2836", market_cap=21e9, currency="USD",
                     description="Gene editing and cell therapy platform.")
        verdict = assess_peer(subject, larger)
        assert not verdict.comparable
        assert "size" in str(verdict.exclusion)
        assert any(f"{MAX_SIZE_RATIO:.0f}x" in r for r in verdict.reasons)

    def test_different_biotech_modalities_are_not_peers_on_a_broad_sic(self) -> None:
        subject = _co(sic_code="2836", description="Gene editing and CAR-T cell therapy.")
        vaccines = _co(ticker="VACC", sic_code="2836",
                       description="We develop mRNA vaccines against infectious diseases.")
        assert not assess_peer(subject, vaccines).comparable


class TestAssessPeer:
    def test_a_specific_four_digit_sic_match_is_enough(self) -> None:
        verdict = assess_peer(_co(sic_code="3823"), _co(ticker="P", sic_code="3823"))
        assert verdict.comparable and verdict.score >= PASS_SCORE
        assert "same SIC code 3823" in verdict.reasons

    def test_a_broad_sic_match_alone_is_not_enough(self) -> None:
        assert "7372" in BROAD_SIC_CODES
        verdict = assess_peer(_co(sic_code="7372"), _co(ticker="P", sic_code="7372"))
        assert not verdict.comparable and verdict.score < PASS_SCORE
        assert "beyond the industry label" in str(verdict.exclusion)

    def test_a_three_digit_sic_match_alone_is_not_enough(self) -> None:
        """The megacap shape: 7370 beside 7372 is the industry group, not the business."""
        verdict = assess_peer(_co(sic_code="7372"), _co(ticker="P", sic_code="7370"))
        assert not verdict.comparable
        assert any("industry group 737" in r for r in verdict.reasons)

    def test_a_description_match_passes_without_sic(self) -> None:
        verdict = assess_peer(_co(description=CREATIVE), _co(ticker="P", description=DESIGN))
        assert verdict.comparable
        assert any("both business descriptions name" in r for r in verdict.reasons)

    def test_nothing_held_is_not_a_peer(self) -> None:
        """Missing data never makes a peer: the bucket alone is not a reason."""
        verdict = assess_peer(_co(), _co(ticker="P"))
        assert not verdict.comparable and verdict.score == 0
        assert any("SIC not compared" in r for r in verdict.reasons)

    def test_a_shared_commodity_is_a_business_match(self) -> None:
        verdict = assess_peer(_co(sic_code="1000"), _co(ticker="P", sic_code="1000"),
                              shares_commodity=True, commodity="copper")
        assert verdict.comparable and any("copper" in r for r in verdict.reasons)
        unknown = assess_peer(_co(sic_code="1000"), _co(ticker="P", sic_code="1000"),
                              shares_commodity=None, commodity="copper")
        assert not unknown.comparable

    def test_a_theme_conflict_overrides_a_specific_sic_match(self) -> None:
        subject = _co(sic_code="3812", description="Aircraft and missile systems.")
        other = _co(ticker="P", sic_code="3812", description="Industrial automation software.")
        verdict = assess_peer(subject, other)
        assert not verdict.comparable and "line of business" in str(verdict.exclusion)

    def test_size_in_different_currencies_is_not_compared(self) -> None:
        verdict = assess_peer(
            _co(sic_code="3823", market_cap=1e6, currency="EUR"),
            _co(ticker="P", sic_code="3823", market_cap=1e12, currency="USD"),
        )
        assert verdict.comparable
        assert any("size not compared" in r for r in verdict.reasons)

    def test_a_malformed_sic_or_market_cap_is_ignored(self) -> None:
        verdict = assess_peer(_co(sic_code="73-72x", market_cap="n/a", currency="USD"),
                              _co(ticker="P", sic_code="123456", market_cap=-5, currency="USD"))
        assert not verdict.comparable


class TestBusinessThemes:
    def test_punctuation_and_case_are_ignored(self) -> None:
        assert "document and e-signature software" in business_themes("E-Signature and PDF")

    def test_generic_words_name_no_theme(self) -> None:
        assert business_themes("A cloud software platform powered by AI.") == frozenset()

    def test_whole_words_only(self) -> None:
        assert business_themes("We sell computer chips for modems") == frozenset()
        assert business_themes(None) == frozenset()

    @pytest.mark.parametrize(
        "hostile", [" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000]
    )
    def test_linear_on_hostile_input(self, hostile: str) -> None:
        start = time.perf_counter()
        business_themes(hostile)
        assess_peer(_co(description=hostile), _co(ticker="P", description=hostile))
        assert time.perf_counter() - start < 0.2


class TestReport:
    def test_no_peer_set_means_no_selection_block(self) -> None:
        assert pr.peer_selection([]) is None

    def test_selected_peers_remove_them_from_excluded(self) -> None:
        selection = pr.peer_selection([
            {"items": [], "excluded": [{"ticker": "dsgn", "similarity": {"score": 1}}],
             "excluded_count": 1},
            {"items": [{"ticker": "DSGN"}], "excluded": [
                {"ticker": "CLAI", "reason": "not comparable: x",
                 "similarity": {"score": 0, "reasons": ["r1"]}}]},
        ])
        assert selection is not None
        assert selection["status"] == "selected" and selection["note"] is None
        assert selection["selected"] == ["DSGN"]
        assert [row["ticker"] for row in selection["excluded"]] == ["CLAI"]
        assert selection["excluded"][0]["reasons"] == ["r1"]

    def _assemble(self, **over: Any) -> dict[str, Any]:
        inputs = pr.ReportInputs(subject={"ticker": "CRSW"}, questions=(), findings=(), **over)
        report = pr.assemble(inputs)
        return next(s for s in report["sections"] if s["key"] == "competitive_position")

    def test_the_report_carries_the_selection_only_when_a_peer_set_ran(self) -> None:
        assert "peer_selection" not in self._assemble()
        selection = pr.peer_selection([{"items": [], "excluded": [
            {"ticker": "CLAI", "reason": "not comparable: different line of business",
             "similarity": {"score": 0, "reasons": ["the business descriptions name "
                                                    "different businesses"]}}]}])
        competitive = self._assemble(peer_selection=selection)
        assert competitive["peer_table"] == []
        assert competitive["peer_selection"]["status"] == "unavailable"
        assert "No peer comparison is shown" in competitive["peer_selection"]["note"]

    def test_the_note_and_reasons_pass_the_safety_screen(self) -> None:
        from app.services.agent_tools.peer_similarity import BUSINESS_THEMES

        assert safety_terms.scan_text(pr.PEER_COMPARISON_UNAVAILABLE_NOTE) == []
        verdict = assess_peer(_co(sic_code="7372", description=CREATIVE, market_cap=1e9,
                                  currency="USD"),
                              _co(ticker="P", sic_code="7370", description=CLINICAL_AI,
                                  market_cap=1e12, currency="USD"))
        assert safety_terms.scan_value(verdict.to_dict()) == []
        assert safety_terms.scan_value(list(BUSINESS_THEMES)) == []
