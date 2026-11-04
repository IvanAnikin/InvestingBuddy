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
    base: dict[str, Any] = {"ticker": "SUBJ", "name": None, "sic_code": None,
                            "description": None, "market_cap": None, "currency": None,
                            "legal_entity_id": None}
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


class TestDynamicPeersPassTheSameGate:
    """Production runs with V3_DYNAMIC_PEER_DISCOVERY_ENABLED on: an externally discovered
    peer must not be a way around the comparability gate (ADBE-4 through the search)."""

    @pytest.fixture(autouse=True)
    def _search(self, monkeypatch):  # noqa: ANN001, ANN202
        import json

        from app.services.discovery import identity as identity_mod
        from app.services.providers.contracts import ConsumptionUnits

        self.proposed: list[dict[str, str]] = []

        async def _ask(provider, query, cfg):  # noqa: ANN001, ANN202
            return {"text": json.dumps({"companies": self.proposed}), "provider": "ds",
                    "task_id": "t", "truncated": False, "opened_urls": [], "warnings": [],
                    "consumption": ConsumptionUnits(web_search_calls=1, model_calls=1)}

        async def _verify(lead, *, cfg=None, fetcher=None, max_fetches=2, primary=None):  # noqa: ANN001, ANN202
            return identity_mod.IdentityOutcome(
                lead=lead, status="verified", ticker=lead.ticker,
                exchange=identity_mod.normalise_venue(lead.exchange_raw), name=lead.name,
                listing_source={"url": lead.listing_source_url, "tier": "issuer"},
            )

        monkeypatch.setattr("app.services.agents.routing.research_provider_for",
                            lambda cfg: SimpleNamespace(transport=object()))
        monkeypatch.setattr("app.services.discovery.leads._ask", _ask)
        monkeypatch.setattr(identity_mod, "verify_identity", _verify)
        peers._PEER_MEMO.clear()
        yield
        peers._PEER_MEMO.clear()

    def _propose(self, *rows: tuple[str, str, str]) -> None:
        self.proposed[:] = [
            {"legal_name": name, "ticker": ticker, "exchange": "Nasdaq",
             "listing_source_url": f"https://{ticker.lower()}.example/ir",
             "why": "a direct competitor in software"}
            for ticker, name, _ in rows
        ]

    async def _run(self, session, subject):  # noqa: ANN001, ANN202
        cfg = SimpleNamespace(v3_dynamic_peer_discovery_enabled=True,
                              v3_external_search_timeout_seconds=180)
        return await peers._get_peer_set(
            SimpleNamespace(session=session, cfg=cfg),
            peers.validate_get_peer_set({"company_id": str(subject.id)}),
        )

    async def _subject(self, session):  # noqa: ANN001, ANN202
        return await _company(session, "CRSW", sic_code="7372", description=CREATIVE,
                              market_cap=150e9, currency="USD")

    async def test_a_comparable_dynamic_peer_passes(self, session) -> None:  # noqa: ANN001
        subject = await self._subject(session)
        # Held, but outside the subject's bucket, so only the search can find it.
        await _company(session, "DSGN", industry=None, sic_code="7372", description=DESIGN,
                       market_cap=60e9, currency="USD")
        self._propose(("DSGN", "Design Software Inc", ""))
        payload = await self._run(session, subject)
        external = [i for i in payload["items"] if i["commodity_basis"] == "external_discovery"]
        assert [i["ticker"] for i in external] == ["DSGN"]
        assert external[0]["similarity"]["comparable"] is True
        assert "similarity check:" in external[0]["basis"]

    async def test_a_dynamic_megacap_with_an_unrelated_business_is_excluded(self, session) -> None:  # noqa: ANN001
        subject = await self._subject(session)
        await _company(session, "MEGA", industry=None, sic_code="7370", description=SEARCH_ADS,
                       market_cap=2_400e9, currency="USD")
        self._propose(("MEGA", "Mega Search Holdings", ""))
        payload = await self._run(session, subject)
        assert payload["items"] == []
        row = next(r for r in payload["excluded"] if r["ticker"] == "MEGA")
        assert row["source"] == "external_discovery"
        assert row["reason"] == "not comparable: different line of business"
        assert any("externally discovered peer(s) were excluded" in g for g in payload["gaps"])
        selection = pr.peer_selection([payload])
        assert selection is not None and selection["status"] == "unavailable"
        assert [r["ticker"] for r in selection["excluded"]] == ["MEGA"]

    async def test_a_dynamic_peer_with_no_data_is_excluded_as_unverified(self, session) -> None:  # noqa: ANN001
        from app.services.agent_tools.peer_similarity import EXCLUSION_UNVERIFIED

        subject = await self._subject(session)
        self._propose(("NODA", "No Data Corp", ""))
        payload = await self._run(session, subject)
        assert payload["items"] == []
        row = next(r for r in payload["excluded"] if r["ticker"] == "NODA")
        assert row["reason"] == EXCLUSION_UNVERIFIED
        assert row["reason"].startswith("comparability unverified")
        assert row["similarity"]["insufficient_data"] is True
        assert payload["contains_untrusted_content"] is True, "the name came from a provider"

    async def test_a_held_candidate_the_gate_excluded_cannot_return_via_search(
        self, session  # noqa: ANN001
    ) -> None:
        subject = await self._subject(session)
        await _company(session, "CLAI", sic_code="7372", description=CLINICAL_AI,
                       market_cap=10e9, currency="USD")
        self._propose(("CLAI", "Clinical AI Inc", ""))
        payload = await self._run(session, subject)
        assert payload["items"] == []
        assert [r["ticker"] for r in payload["excluded"]] == ["CLAI"], "recorded once"


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


class TestReviewOfPr325:
    """Review repros (rv325/repro_gate.py, repro_e2e.py), each pinned."""

    # 1 — another share class or listing of the subject is never its peer.
    @pytest.mark.parametrize(
        ("subject", "candidate"),
        [
            (dict(ticker="BRK-B", sic_code="6331"), dict(ticker="BRK-A", sic_code="6331")),
            (dict(ticker="HEI", sic_code="3720"), dict(ticker="HEI.A", sic_code="3720")),
            (dict(ticker="GOLD", name="Barrick Gold Corp", sic_code="1040"),
             dict(ticker="ABX", name="Barrick Gold Corporation")),
            (dict(ticker="CLLS", name="Cellectis S.A.", sic_code="2836"),
             dict(ticker="ALCLS", name="CELLECTIS SA")),
        ],
    )
    def test_another_share_class_or_listing_is_the_same_issuer(self, subject, candidate) -> None:  # noqa: ANN001
        verdict = assess_peer(_co(**subject), _co(**candidate), shares_commodity=True,
                              commodity="gold")
        assert not verdict.comparable
        assert verdict.exclusion == "not comparable: the same issuer as the subject"

    def test_base_ticker_strips_only_a_one_letter_class_suffix(self) -> None:
        from app.services.agent_tools.peer_similarity import base_ticker

        assert base_ticker("brk-b") == "BRK" and base_ticker("HEI.A") == "HEI"
        assert base_ticker("BF/B") == "BF" and base_ticker("ADBE") == "ADBE"
        assert base_ticker("A") == "A" and base_ticker(None) == ""

    def test_different_names_and_tickers_are_not_the_same_issuer(self) -> None:
        verdict = assess_peer(_co(ticker="FCX", name="Freeport-McMoRan Inc", sic_code="1000"),
                              _co(ticker="SCCO", name="Southern Copper Corp", sic_code="1000"),
                              shares_commodity=True, commodity="copper")
        assert verdict.comparable

    async def test_share_class_end_to_end(self, session) -> None:  # noqa: ANN001
        brkb = await _company(session, "BRK-B", name="Berkshire Hathaway Inc.",
                              industry="Insurance", sic_code="6331")
        await _company(session, "BRK-A", name="Berkshire Hathaway Inc.", industry="Insurance",
                       sic_code="6331")
        payload = await _peer_set(session, brkb)
        assert payload["items"] == []
        assert payload["excluded"][0]["reason"] == (
            "not comparable: the same issuer as the subject"
        )

    # 2 — conglomerates, negations and legal wording.
    def test_a_conglomerate_sharing_one_theme_is_not_a_peer(self) -> None:
        conglomerate = (
            "A collection of businesses: a search engine, online advertising, a video sharing "
            "service whose creators rely on content creation tools, a cloud platform, "
            "cybersecurity services and consumer devices."
        )
        assert len(business_themes(conglomerate)) >= 3
        verdict = assess_peer(_co(ticker="ADBE", sic_code="7372", description=CREATIVE),
                              _co(ticker="GOOGL", sic_code="7370", description=conglomerate))
        assert not verdict.comparable
        assert "diversified" in str(verdict.exclusion)
        # With a specific four-digit SIC match the same conglomerate is a peer.
        specific = assess_peer(
            _co(sic_code="3829", description=CREATIVE),
            _co(ticker="P", sic_code="3829", description=conglomerate),
        )
        assert specific.comparable

    def test_a_candidate_whose_main_business_differs_is_not_a_peer(self) -> None:
        # repro_e2e case B: two themes only, so not a conglomerate by count — but its
        # first-named line of business is search, which the subject does not name.
        verdict = assess_peer(
            _co(ticker="ADBE", sic_code="7372",
                description="Creative software for photo editing and graphic design; PDF tools."),
            _co(ticker="GOOGL", sic_code="7370",
                description="Search engine, online advertising, video sharing for content "
                            "creation, cloud."),
        )
        assert not verdict.comparable
        assert "main line of business" in str(verdict.exclusion)

    @pytest.mark.parametrize(
        "text",
        [
            "Unlike creative software vendors, we do not offer photo editing or design tools.",
            "We never provide graphic design services.",
            "We have no PDF products and no e-signature products.",
        ],
    )
    def test_a_negated_phrase_names_no_theme(self, text: str) -> None:
        assert business_themes(text) == frozenset()

    def test_negation_does_not_leak_past_its_window(self) -> None:
        text = ("We do not sell hardware. Our products include photo editing software and "
                "graphic design tools.")
        assert "design and creative software" in business_themes(text)

    def test_the_negated_diagnostics_lab_is_still_excluded(self) -> None:
        tem = ("We are a clinical laboratory offering precision medicine and genomic "
               "sequencing. Unlike creative software vendors, we do not offer photo editing "
               "or design tools.")
        verdict = assess_peer(_co(ticker="ADBE", sic_code="7372", description=CREATIVE),
                              _co(ticker="TEM", sic_code="7372", description=tem))
        assert not verdict.comparable
        assert "different line of business" in str(verdict.exclusion)

    @pytest.mark.parametrize(
        "text",
        [
            "We maintain a vigorous defense of our patents and trademarks.",
            "We may incur legal defense costs.",
            "Management watches costs closely; our aircraft lease is immaterial.",
            "Former military personnel make up part of our workforce.",
        ],
    )
    def test_legal_or_ordinary_words_name_no_theme(self, text: str) -> None:
        assert business_themes(text) == frozenset()

    def test_two_software_companies_sharing_only_legal_wording_are_not_peers(self) -> None:
        verdict = assess_peer(
            _co(ticker="A", sic_code="7372",
                description="Workflow software for hospitals. We pursue the defense of our "
                            "patents."),
            _co(ticker="B", sic_code="7371",
                description="Payroll software. We may incur legal defense costs."),
        )
        assert not verdict.comparable

    # 3 — broad SIC codes.
    @pytest.mark.parametrize("sic", ["6798", "7379", "3990", "6794", "8731", "3841"])
    def test_catch_all_codes_are_weak(self, sic: str) -> None:
        assert sic in BROAD_SIC_CODES
        assert not assess_peer(_co(sic_code=sic), _co(ticker="P", sic_code=sic)).comparable

    # 4 — the note says only what happened.
    def test_with_no_candidate_the_note_is_neutral(self) -> None:
        selection = pr.peer_selection([{"items": [], "excluded": [], "excluded_count": 0}])
        assert selection is not None and selection["status"] == "unavailable"
        assert selection["note"] == pr.PEER_COMPARISON_NO_CANDIDATE_NOTE
        assert "did not match" not in selection["note"]
        assert safety_terms.scan_text(selection["note"]) == []

    def test_with_excluded_candidates_the_note_says_they_did_not_match(self) -> None:
        selection = pr.peer_selection([{"items": [], "excluded_count": 2}])
        assert selection is not None
        assert selection["note"] == pr.PEER_COMPARISON_UNAVAILABLE_NOTE

    @pytest.mark.parametrize(
        "payloads",
        [
            [{}],
            [{"items": None, "excluded": [{"ticker": "X", "similarity": "bad"}]}],
            [{"excluded_count": "n/a"}],
            [{"items": "x", "excluded": "y", "excluded_count": -3}],
            [{"excluded": [{"ticker": "X", "similarity": {"reasons": "a string"}}]}],
        ],
    )
    def test_peer_selection_tolerates_malformed_payloads(self, payloads) -> None:  # noqa: ANN001
        selection = pr.peer_selection(payloads)
        assert selection is not None and selection["status"] == "unavailable"
        assert selection["excluded_count"] >= 0
        for row in selection["excluded"]:
            assert isinstance(row["reasons"], list)

    # 1 (external) and 5 — the identity record and a failing lookup.
    async def test_external_peer_whose_identity_lists_the_subject_is_excluded(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.discovery import identity as identity_mod

        subject = await _company(session, "CLLS", name="Cellectis S.A.", sic_code="2836",
                                 description="Gene editing and CAR-T cell therapy.")
        # A held company with a matching business — it would pass on its own record.
        await _company(session, "ZZZ", industry=None, name="Zeta Bio Inc", sic_code="2836",
                       description="Allogeneic CAR-T cell therapy developer.")

        async def _verify(lead, **_kw):  # noqa: ANN001, ANN202
            out = identity_mod.IdentityOutcome(
                lead=lead, status="verified", ticker=lead.ticker,
                exchange=identity_mod.normalise_venue(lead.exchange_raw), name=lead.name,
                listing_source={"url": None, "tier": "issuer"},
            )
            out.securities = [{"ticker": "ZZZ", "exchange": "US"},
                              {"ticker": "CLLS", "exchange": "US"}]
            return out

        payload = await self._run_external(session, monkeypatch, subject, _verify)
        row = next(r for r in payload["excluded"] if r["ticker"] == "ZZZ")
        assert row["reason"] == "not comparable: the same issuer as the subject"
        assert payload["items"] == []

    async def test_external_peer_whose_issuer_name_is_the_subjects_is_excluded(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.discovery import identity as identity_mod

        subject = await _company(session, "CLLS", name="Cellectis S.A.", sic_code="2836")

        async def _verify(lead, **_kw):  # noqa: ANN001, ANN202
            out = identity_mod.IdentityOutcome(
                lead=lead, status="verified", ticker=lead.ticker,
                exchange=identity_mod.normalise_venue(lead.exchange_raw), name=lead.name,
                listing_source={"url": None, "tier": "issuer"},
            )
            out.issuer = {"legal_name": "CELLECTIS"}
            return out

        payload = await self._run_external(session, monkeypatch, subject, _verify)
        row = next(r for r in payload["excluded"] if r["ticker"] == "ZZZ")
        assert row["reason"] == "not comparable: the same issuer as the subject"

    async def test_a_failing_record_lookup_never_raises(self, session, monkeypatch) -> None:  # noqa: ANN001
        from sqlalchemy.exc import OperationalError

        from app.services.agent_tools.peer_similarity import EXCLUSION_UNVERIFIED
        from app.services.discovery import identity as identity_mod

        subject = await _company(session, "CRSW", sic_code="7372", description=CREATIVE)

        async def _boom(*_a, **_kw):  # noqa: ANN202
            raise OperationalError("SELECT", {}, Exception("db down"))

        async def _verify(lead, **_kw):  # noqa: ANN001, ANN202
            return identity_mod.IdentityOutcome(
                lead=lead, status="verified", ticker=lead.ticker,
                exchange=identity_mod.normalise_venue(lead.exchange_raw), name=lead.name,
                listing_source={"url": None, "tier": "issuer"},
            )

        monkeypatch.setattr(peers, "_dynamic_peer_record", _boom)
        payload = await self._run_external(session, monkeypatch, subject, _verify)
        row = next(r for r in payload["excluded"] if r["ticker"] == "ZZZ")
        assert row["reason"] == EXCLUSION_UNVERIFIED
        assert "OperationalError" in row["similarity"]["reasons"][0]

    async def _run_external(self, session, monkeypatch, subject, verify):  # noqa: ANN001, ANN202
        import json

        from app.services.discovery import identity as identity_mod
        from app.services.providers.contracts import ConsumptionUnits

        async def _ask(provider, query, cfg):  # noqa: ANN001, ANN202
            return {"text": json.dumps({"companies": [
                {"legal_name": "Zeta Bio Inc", "ticker": "ZZZ", "exchange": "Nasdaq",
                 "listing_source_url": "https://zzz.example/ir", "why": "a peer"}]}),
                "provider": "ds", "task_id": "t", "truncated": False, "opened_urls": [],
                "warnings": [], "consumption": ConsumptionUnits(web_search_calls=1)}

        monkeypatch.setattr("app.services.agents.routing.research_provider_for",
                            lambda cfg: SimpleNamespace(transport=object()))
        monkeypatch.setattr("app.services.discovery.leads._ask", _ask)
        monkeypatch.setattr(identity_mod, "verify_identity", verify)
        peers._PEER_MEMO.clear()
        try:
            return await peers._get_peer_set(
                SimpleNamespace(session=session, cfg=SimpleNamespace(
                    v3_dynamic_peer_discovery_enabled=True,
                    v3_external_search_timeout_seconds=180)),
                peers.validate_get_peer_set({"company_id": str(subject.id)}),
            )
        finally:
            peers._PEER_MEMO.clear()


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
