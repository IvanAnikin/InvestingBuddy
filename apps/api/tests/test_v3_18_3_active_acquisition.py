"""V3.18.3 — active evidence acquisition, and a verification gate that reads context.

The SCCO baseline was built from two filings. External research ran for exactly one
generic question, with a budget of two searches, and a verified external item was a
provider's sentence plus a URL whose page contained the claimed number *somewhere*.

These tests pin the ladder that replaces that: per question, platform tools, then the
corpus by the question's own terms, then — only if the contract allows it and the run's
budget remains — an external search whose every claim InvestingBuddy fetches and checks
itself, IN CONTEXT; every rung logged with why it was taken and why it stopped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.config import Settings
from app.services.agents import investigator as inv
from app.services.agents.investigator import (
    ExternalSearchBudget,
    LLMInvestigator,
    QuestionContext,
    clean_company_name,
    fill_intent,
)
from app.services.director import contracts as c
from app.services.director.base_model import base_question
from app.services.playbooks.schema import planned_from
from app.services.providers import leads as lead_gate
from app.services.providers.contracts import (
    LEAD_REJECTED,
    LEAD_VERIFIED,
    REJECTED_CLAIM_NOT_IN_SOURCE,
    REJECTED_VALUE_MISMATCH,
    ResearchLead,
)
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.sources.publisher_tiers import publisher_tier


def _cfg() -> Settings:
    return Settings(v3_provider_runtime_enabled=True)  # type: ignore[arg-type]


def _fetcher(body: bytes):  # noqa: ANN202
    async def _call(url: str, *, allowed_domains, cfg=None, resolve_ip=False):  # noqa: ANN001
        return DocumentFetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            document_type="html",
            content=body,
        )

    return _call


def _lead(claim: str, value: str | None = None, url: str = "https://pubs.usgs.gov/copper.pdf"):
    return ResearchLead(
        claim_text=claim,
        provider="deepseek",
        model="m",
        claimed_source_url=url,
        claimed_value=value,
    )


# ── The gate reads context ─────────────────────────────────────────────────── #

_FILLER = b"<p>" + b"Methodology notes on survey coverage and revisions. " * 20 + b"</p>"
USGS_PAGE = (
    b"<html><body><p>World Mine and Refinery Production and Reserves.</p>"
    b"<p>Peru mine production 2,740 2,700 thousand tons; Chile 5,510 5,300.</p>"
    + _FILLER
    + b"<p>The report has 250 pages and 2,700 footnotes in the appendix.</p>"
    + _FILLER
    + b"<p>World total mine production was 23,000 thousand metric tons of copper in 2025.</p>"
    b"</body></html>"
)


class TestAValueMustSitNextToWhatItMeasures:
    async def test_a_value_in_context_verifies_and_quotes_the_page(self) -> None:
        lead = _lead("World copper mine production was 23,000 thousand tons in 2025.", "23,000")
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(USGS_PAGE), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED
        assert outcome.verification_basis == "value_in_context"
        assert "World total mine production was 23,000" in outcome.matched_excerpt

    async def test_a_value_only_elsewhere_on_the_page_is_refused(self) -> None:
        """'250' is on the page — as a page count. The first gate accepted this."""
        lead = _lead("Southern Copper's smelter capacity is 250 thousand tons of anodes.", "250")
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(USGS_PAGE), allow_public_web=True
        )
        assert outcome.status == LEAD_REJECTED
        assert outcome.rejection_reason == REJECTED_VALUE_MISMATCH
        assert "never near" in (outcome.detail or "")

    async def test_mutation_without_context_the_page_count_verifies(self, monkeypatch) -> None:
        original = lead_gate.locate_value_in_context

        def anywhere(text, numbers, claim):  # noqa: ANN001, ANN202
            found, _ = original(text, numbers, "")
            return found, ("…" if found else None)

        monkeypatch.setattr(lead_gate, "locate_value_in_context", anywhere)
        lead = _lead("Southern Copper's smelter capacity is 250 thousand tons of anodes.", "250")
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(USGS_PAGE), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED, "this is the defect the context rule closes"

    async def test_a_bare_figure_with_no_terms_keeps_the_old_behaviour(self) -> None:
        """Nothing to anchor it to; refusing would be a new rejection of something the
        gate always accepted."""
        outcome = await lead_gate.verify_lead(
            _lead("23,000", "23,000"), cfg=_cfg(), fetcher=_fetcher(USGS_PAGE),
            allow_public_web=True,
        )
        assert outcome.status == LEAD_VERIFIED


PROJECT_PAGE = (
    b"<html><body><h1>Tia Maria</h1><p>Southern Copper said the Tia Maria copper project "
    b"in Arequipa, Peru, is under construction and is expected to begin production in "
    b"2027, with annual capacity of 120,000 tons of copper cathodes.</p>"
    b"<p>The company has not received the water use permit for the La Tapada deposit.</p>"
    b"</body></html>"
)


class TestProseIsVerifiedAgainstTheDocumentsOwnWords:
    async def test_a_paraphrase_verifies_and_the_evidence_is_the_page(self) -> None:
        lead = _lead(
            "Tia Maria project in Peru is under construction with first copper cathode "
            "production expected in Arequipa",
            url="https://www.southerncoppercorp.com/tia-maria",
        )
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(PROJECT_PAGE), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED
        assert outcome.verification_basis == "document_passage"
        assert "Tia Maria copper project" in outcome.matched_excerpt, "original case kept"

    async def test_a_claim_dropping_a_negation_is_not_verified(self) -> None:
        lead = _lead(
            "The company has received the water use permit for the La Tapada deposit",
            url="https://www.southerncoppercorp.com/tia-maria",
        )
        page = PROJECT_PAGE.replace(b"has not received", b"has not yet been granted")
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(page), allow_public_web=True
        )
        # The page never says it WAS received; the words overlap, but not enough of
        # them sit together for the passage rule, and no substring matches.
        assert outcome.status == LEAD_REJECTED
        assert outcome.rejection_reason == REJECTED_CLAIM_NOT_IN_SOURCE

    async def test_a_claim_with_a_negation_needs_the_negation_on_the_page(self) -> None:
        lead = _lead(
            "The company has not received the water use permit for La Tapada deposit",
            url="https://www.southerncoppercorp.com/tia-maria",
        )
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(PROJECT_PAGE), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED
        without = PROJECT_PAGE.replace(b"has not received", b"has received")
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(without), allow_public_web=True
        )
        assert outcome.status == LEAD_REJECTED

    async def test_a_page_that_negates_the_claim_does_not_verify_it(self) -> None:
        """The words overlap perfectly and the meaning is the opposite."""
        lead = _lead(
            "The company has received the water use permit for the La Tapada deposit",
            url="https://www.southerncoppercorp.com/tia-maria",
        )
        outcome = await lead_gate.verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(PROJECT_PAGE), allow_public_web=True
        )
        assert outcome.status == LEAD_REJECTED
        assert outcome.rejection_reason == REJECTED_CLAIM_NOT_IN_SOURCE

    def test_too_few_distinctive_words_is_not_a_passage(self) -> None:
        assert lead_gate.locate_passage("Copper is big.", "Copper output rose") is None


class TestPublisherTiers:
    @pytest.mark.parametrize(
        ("url", "tier"),
        [
            ("https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-copper.pdf",
             "T3_industry_specialist"),
            ("https://www.iea.org/reports/global-critical-minerals-outlook-2025",
             "T3_industry_specialist"),
            ("https://www.sec.gov/Archives/edgar/data/1/x.htm", "T1_primary_filing"),
            ("https://www.worldbank.org/en/research/commodity-markets", "T2_regulator_or_gov"),
            ("https://www.inei.gob.pe/estadisticas/", "T2_regulator_or_gov"),
            ("https://www.minem.gob.pe/", "T3_industry_specialist"),
            ("https://www.reuters.com/markets/commodities/", "T4_quality_media"),
            ("https://copper-stocks-blog.example/best-picks", "T5_api_aggregator"),
            ("https://gov.example.com/fake", "T5_api_aggregator"),
            (None, "T5_api_aggregator"),
        ],
    )
    def test_cases(self, url, tier) -> None:
        assert publisher_tier(url) == tier

    def test_an_issuer_domain_is_never_guessed(self) -> None:
        """A lookalike read as the company's own is what a tier exists to prevent."""
        assert publisher_tier("https://southerncoppercorp.com/ir") == "T5_api_aggregator"


# ── The ladder ─────────────────────────────────────────────────────────────── #


@dataclass
class _Result:
    ok: bool = True
    payload: dict | None = None
    contains_untrusted_content: bool = False
    refusal_reason: str | None = None


@dataclass
class _Session:
    """Scripted tool results. Records every call so the ladder's order is assertable."""

    corpus_items: list[dict] = field(default_factory=list)
    intent_items: list[dict] = field(default_factory=list)
    leads: list[dict] = field(default_factory=list)
    verified_tier: str = "T3_industry_specialist"
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def call(self, tool: str, arguments: dict, task_ref: str = "") -> _Result:
        self.calls.append((tool, arguments))
        if tool == "search_company_corpus":
            items = self.corpus_items if len([c for c in self.calls if c[0] == tool]) == 1 \
                else self.intent_items
            return _Result(payload={"items": items})
        if tool == "search_web":
            return _Result(payload={"provider": "deepseek", "leads": self.leads})
        if tool == "fetch_public_source":
            return _Result(
                payload={
                    "items": [
                        {
                            "evidence_id": f"ev:x:{uuid.uuid4().hex[:8]}",
                            "source_excerpt": "World mine production was 23,000 kt.",
                            "source_tier": self.verified_tier,
                            "fetched_url": arguments["url"],
                        }
                    ]
                },
                contains_untrusted_content=True,
            )
        return _Result(ok=False)


def _issuer_chunk(n: int) -> dict:
    return {"evidence_id": f"ev:{n}", "source_tier": "T1_primary_filing",
            "canonical_url": "https://sec.gov/10k.htm", "text": "copper"}


def _investigator(session: _Session, budget: int = 5) -> LLMInvestigator:
    return LLMInvestigator(
        session=session,
        company_id=uuid.uuid4(),
        ticker="SCCO",
        exchange="US",
        client=None,
        company_name="Southern Copper Corp",
        industry="Metals & Mining",
        external_budget=ExternalSearchBudget(limit=budget),
    )


def _industry_question():  # noqa: ANN202
    return planned_from(base_question("industry_economics"), origin="director")


def _role(role_id: str):  # noqa: ANN202
    from app.services.director.roles import role_for

    return role_for(role_id)


class TestTheLadder:
    async def test_it_climbs_to_the_web_only_when_the_contract_is_unmet(self) -> None:
        session = _Session(
            corpus_items=[_issuer_chunk(1)],
            intent_items=[_issuer_chunk(2)],
            leads=[{"claim": "World mine production was 23,000 kt", "claimed_value": "23000",
                    "claimed_source_url": "https://pubs.usgs.gov/c.pdf"}],
        )
        worker = _investigator(session)
        evidence, used, steps = await worker._acquire(
            "industry_analyst", _role("industry_analyst"), _industry_question(), 50,
            context=QuestionContext(), round_index=0,
        )
        rungs = [s["rung"] for s in steps]
        assert rungs == ["platform_tools", "corpus_by_intent", "external_search",
                         "contract_after_round"]
        external = steps[2]
        assert any("independent" in why for why in external["why"]), (
            "the log must say WHY the agent went to the web"
        )
        assert external["query"].startswith("Southern Copper Corp (SCCO): ")
        assert steps[-1]["status"] == c.STATUS_SATISFIED
        assert any(e.ref.source_kind == c.INDUSTRY_SPECIALIST for e in evidence)
        assert worker.external_budget.used == 1

    async def test_a_satisfied_contract_never_spends_a_search(self) -> None:
        session = _Session(corpus_items=[_issuer_chunk(1), _issuer_chunk(2)])
        question = planned_from(base_question("recent_disclosure"), origin="director")
        worker = _investigator(session)
        _e, _u, steps = await worker._acquire(
            "event_analyst", _role("event_analyst"), question, 50,
            context=QuestionContext(), round_index=0,
        )
        assert "search_web" not in {tool for tool, _ in session.calls}
        assert [s["rung"] for s in steps] == ["platform_tools", "contract_after_round"]

    async def test_a_contract_that_forbids_the_web_stops_and_says_so(self) -> None:
        session = _Session()
        question = planned_from(base_question("balance_sheet_risk"), origin="director")
        worker = _investigator(session)
        _e, _u, steps = await worker._acquire(
            "financial_analyst", _role("financial_analyst"), question, 50,
            context=QuestionContext(), round_index=0,
        )
        external = next(s for s in steps if s["rung"] == "external_search")
        assert external["stopped"] == "contract_does_not_allow_external_research"
        assert "search_web" not in {tool for tool, _ in session.calls}

    async def test_the_run_budget_is_shared_and_binds(self) -> None:
        session = _Session(leads=[])
        worker = _investigator(session, budget=0)
        _e, _u, steps = await worker._acquire(
            "industry_analyst", _role("industry_analyst"), _industry_question(), 50,
            context=QuestionContext(), round_index=0,
        )
        external = next(s for s in steps if s["rung"] == "external_search")
        assert external["stopped"] == "run_search_budget_exhausted"

    async def test_the_question_cap_binds_across_rounds(self) -> None:
        session = _Session(leads=[])
        worker = _investigator(session)
        cap = _industry_question().evidence_contract.max_external_searches
        _e, _u, steps = await worker._acquire(
            "industry_analyst", _role("industry_analyst"), _industry_question(), 50,
            context=QuestionContext(
                prior_evidence=(c.EvidenceRef("ev:1", "x", c.ISSUER_FILING, None, "a"),),
                external_searches_done=cap,
            ),
            round_index=1,
        )
        external = next(s for s in steps if s["rung"] == "external_search")
        assert external["stopped"] == "question_search_cap_reached"

    async def test_a_follow_up_starts_where_the_last_round_ended(self) -> None:
        """No platform rung (its arguments are deterministic and would return the same
        items) and the NEXT search intent, not the same one again."""
        session = _Session(leads=[])
        worker = _investigator(session)
        question = _industry_question()
        _e, _u, steps = await worker._acquire(
            "industry_analyst", _role("industry_analyst"), question, 50,
            context=QuestionContext(
                prior_evidence=(c.EvidenceRef("ev:1", "x", c.ISSUER_FILING, None, "a"),),
                external_searches_done=1,
            ),
            round_index=1,
        )
        assert steps[0]["rung"] == "external_search"
        second_intent = fill_intent(question.search_intents[1], worker._intent_values())
        assert steps[0]["query"].endswith(second_intent)

    async def test_the_role_without_a_ladder_never_searches(self) -> None:
        session = _Session()
        worker = _investigator(session)
        _e, _u, steps = await worker._acquire(
            "financial_analyst", _role("financial_analyst"), _industry_question(), 50,
            context=QuestionContext(), round_index=0,
        )
        external = next(s for s in steps if s["rung"] == "external_search")
        assert external["stopped"] == "role_holds_no_external_ladder"


class TestWhatTheWriterSees:
    def test_the_source_excerpt_leads_and_the_provider_wording_is_labelled(self) -> None:
        item = {
            "evidence_id": "ev:x:abc",
            "claim": "Copper demand will double",
            "source_excerpt": "Demand for refined copper was 27.4 Mt in 2025.",
            "detail": "noise",
            "content_hash": "noise",
        }
        evidence = inv._evidence_of("fetch_public_source", item, True)
        assert evidence is not None
        assert evidence.text.index("source_excerpt") < evidence.text.index(
            "provider_claim_not_verified_wording"
        )
        assert "content_hash" not in evidence.text


class TestHelpers:
    def test_registrant_names(self) -> None:
        assert clean_company_name("SOUTHERN COPPER CORP/") == "Southern Copper Corp"
        assert clean_company_name("MP Materials Corp.") == "MP Materials Corp."
        assert clean_company_name("") is None

    def test_an_unfilled_placeholder_is_dropped_not_sent(self) -> None:
        assert fill_intent("{company} {commodity} price", {"company": "X"}) == "X price"


class TestReCitation:
    async def test_a_claim_verified_in_an_earlier_run_is_cited_again(self, monkeypatch) -> None:
        """The duplicate rule meant a fact verified last week could never be cited this
        week: every re-run of a company lost its external evidence."""
        from app.services.agent_tools import external

        known = lead_gate.KnownLead(
            lead_key="k",
            slot_key="s",
            status=LEAD_VERIFIED,
            promoted_evidence_id="ev:x:prior",
            fetched_url="https://pubs.usgs.gov/c.pdf",
            claim_text="World mine production was 23,000 kt",
            matched_excerpt="World total mine production was 23,000 thousand tons",
        )

        async def known_leads(_session, **_kw):  # noqa: ANN001, ANN202
            return [known]

        async def must_not_fetch(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("a re-cited claim must not be fetched again")

        monkeypatch.setattr(lead_gate, "known_leads_for", known_leads)
        monkeypatch.setattr(lead_gate, "lead_key_for", lambda *_a, **_kw: "k")
        monkeypatch.setattr(lead_gate, "verify_lead", must_not_fetch)
        context: Any = type(
            "Ctx", (), {"session": object(), "company_id": uuid.uuid4(), "cfg": _cfg(),
                        "research_job_id": None, "legal_entity_id": None},
        )()
        payload = await external._fetch_public_source(
            context,
            {"url": "https://pubs.usgs.gov/c.pdf", "claim": "World mine production was 23,000 kt",
             "provider": "deepseek"},
        )
        record = payload["items"][0]
        assert record["evidence_id"] == "ev:x:prior"
        assert record["reused_from_earlier_verification"] is True
        assert record["source_tier"] == "T3_industry_specialist"
