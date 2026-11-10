"""ADR-082 — the ledger is the single source of research state.

QA campaign 2026-11 (P-2 / P-4): 16 of 16 audited reports contradicted themselves. The
fixtures below are MINIMAL excerpts of the real stored payload shapes (Cellectis,
PayPal, Antofagasta, Adobe, Lindian, Honeywell) — never whole reports — and every
invariant is tested in both directions: it fires on the violating sentence and does
NOT fire on a near-miss sentence that is true.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.agents.chair import (
    FALLBACK_CHAIR_FAILED,
    FALLBACK_NO_MODEL,
    FALLBACK_NOT_CONVENED,
    LLMChair,
)
from app.services.council_v2.inputs import REFUSED_BLOCKING_QUESTION, assemble
from app.services.ledger import report_invariants as inv
from app.services.ledger import store as ledger
from app.services.ledger.canonical_state import (
    LedgerTally,
    build_canonical_state,
    period_bounds,
    tally_ledger,
)
from app.services.pipeline.v3_pipeline import (
    SOURCE_SUMMARY_KEY,
    V3ResearchOutcome,
    _chair_degraded_reason,
    attach_to_report,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _refused_run(session: Any, *, issuer_findings: int = 2, web_findings: int = 1):
    run = await ledger.open_run(session, mode="standard")
    await ledger.add_question(
        session, run, question_key="cash_runway", text="runway?",
        origin=ledger.ORIGIN_PLAYBOOK, blocking=True,
    )
    for i in range(issuer_findings):
        await ledger.record_finding(
            session, run, statement=f"issuer finding {i}", evidence_ids=[f"ev:{i}"],
            source_kinds=["issuer_filing"],
        )
    for i in range(web_findings):
        await ledger.record_finding(
            session, run, statement=f"web finding {i}", evidence_ids=[f"ev:w{i}"],
            source_kinds=["secondary_web"],
        )
    await session.commit()
    return run


# ---------------------------------------------------------------------------
# Minimal payload excerpts (real shapes)
# ---------------------------------------------------------------------------


def _issuer_finding(statement: str, published: str | None = None,
                    period: str | None = None) -> dict[str, Any]:
    return {
        "finding_id": str(uuid.uuid4()), "statement": statement,
        "source_kinds": ["issuer_filing"], "source_published_at": published,
        "period_key": period, "evidence_ids": ["ev:x"],
        "verification_status": "unverified",
    }


def _payload(**overrides: Any) -> dict[str, Any]:
    """A V3 block the shape of the stored PayPal / Cellectis payloads."""
    base: dict[str, Any] = {
        "council": {"convened": True, "finding_count": 3, "verified_finding_count": 0,
                    "primary_source_finding_count": 2, "unresolved_disagreement_count": 0},
        "chair": {"synthesis": "", "key_points": [], "deterministic_fallback": False,
                  "fallback_reason": None, "unresolved_disagreement_ids": []},
        "routing": {"slots": {
            "chair": {"slot": "chair", "vendor": "azure_openai", "reason": None},
            "red_team": {"slot": "red_team", "vendor": "azure_openai", "reason": None},
            "investigator": {"slot": "investigator", "vendor": "deepseek", "reason": None},
            "follow_up": {"slot": "follow_up", "vendor": "deepseek", "reason": None},
        }},
        "loop": {"findings": 3, "gaps_open": 4, "tasks_run": 8},
        "challenges": {"challenges": 1, "risk_evidence_items": 2},
        "core_filings": {
            "annual": {"form": "10-K", "state": "ready", "accession": "0001633917-26-000024",
                       "filing_date": "2026-02-03"},
            "quarterly": {"form": "10-Q", "state": "ready",
                          "accession": "0001633917-26-000088", "filing_date": "2026-10-27"},
        },
        "core_disclosures": {"skipped": "venue_not_covered", "documents": []},
        "financial_statements_state": {
            "slots": {}, "facts_read": 0,
            "current_period": {"state": "report_acquired_facts_not_extracted",
                               "document": {"headline": "10-Q", "filing_date": "2026-10-27"}},
            "reporting_periods": {"latest_annual": None, "latest_current_period": None},
        },
        "findings": [
            _issuer_finding("FY2025 group revenue was $33,172m.", None, "FY2025"),
            _issuer_finding("FY2025 operating cash flow was $6,416m.", None, "FY2025"),
            {"finding_id": str(uuid.uuid4()), "statement": "Q2 2026 net revenue $8.7bn.",
             "source_kinds": ["secondary_web"], "source_published_at": "2026-10-19",
             "period_key": "2026-Q2", "evidence_ids": ["ev:w"]},
        ],
        "gaps": [],
        "degraded": [],
    }
    base.update(overrides)
    return base


def _state(payload: dict[str, Any] | None = None, *, tally: LedgerTally | None = None,
           llm_council: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_canonical_state(payload or _payload(), llm_council=llm_council,
                                 tally=tally, as_of="2026-11-02T12:00:00Z")


def _tally(findings: int = 3, primary: int = 2, unresolved_ids: tuple[str, ...] = (),
           disagreements: int | None = None) -> LedgerTally:
    return LedgerTally(
        findings=findings, verified=0, primary_source=primary, gaps_open=4,
        disagreements=len(unresolved_ids) if disagreements is None else disagreements,
        unresolved_disagreement_ids=unresolved_ids,
    )


# ---------------------------------------------------------------------------
# 1. The refusal path keeps the true count and the true reason (CLLS2-3)
# ---------------------------------------------------------------------------


class TestRefusalKeepsTheLedgerCount:
    async def test_a_refused_council_carries_the_ledger_counts(self, session) -> None:
        run = await _refused_run(session)
        council = await assemble(session, run)
        assert council.convened is False
        assert council.refusal_reason == REFUSED_BLOCKING_QUESTION
        # Refusal semantics unchanged: the pack the Council would have reasoned over is
        # withheld.
        assert council.findings == ()
        # ... but the counts are the ledger's.
        out = council.to_dict()
        assert out["finding_count"] == 3
        assert out["primary_source_finding_count"] == 2
        assert out["council_finding_count"] == 0

    async def test_the_deterministic_chair_states_the_true_count_and_reason(
        self, session
    ) -> None:
        run = await _refused_run(session)
        council = await assemble(session, run)
        # A chair model IS routed: the fallback is the refusal, not a missing model.
        verdict = await LLMChair(client=object()).deliberate(council)
        assert verdict.deterministic_fallback is True
        assert verdict.fallback_reason == FALLBACK_NOT_CONVENED
        assert verdict.synthesis.startswith("Deterministic synthesis: 3 finding(s)")
        assert "blocking research question is still open" in verdict.synthesis
        assert "No model was available" not in verdict.synthesis
        # The refusal semantics are kept: no key points, insufficient data.
        assert verdict.key_points == []
        assert verdict.label == "insufficient_data"

    async def test_no_model_still_says_so(self, session) -> None:
        run = await ledger.open_run(session, mode="standard")
        await ledger.record_finding(session, run, statement="x", evidence_ids=["ev:1"])
        await session.commit()
        council = await assemble(session, run)
        verdict = await LLMChair(client=None).deliberate(council)
        assert verdict.fallback_reason == FALLBACK_NO_MODEL
        assert "No model was available" in verdict.synthesis

    async def test_a_chair_failure_is_not_called_a_missing_model(self, session) -> None:
        class _Broken:
            async def complete_json(self, *a: Any, **k: Any) -> dict[str, Any]:
                raise RuntimeError("boom")

        run = await ledger.open_run(session, mode="standard")
        await ledger.record_finding(session, run, statement="x", evidence_ids=["ev:1"])
        await session.commit()
        verdict = await LLMChair(client=_Broken()).deliberate(await assemble(session, run))
        assert verdict.fallback_reason == FALLBACK_CHAIR_FAILED
        assert "No model was available" not in verdict.synthesis
        assert "did not return a usable reply" in verdict.synthesis

    def test_the_degraded_line_names_the_refusal_not_no_findings(self) -> None:
        class _V:
            fallback_reason = FALLBACK_NOT_CONVENED

        class _C:
            refusal_reason = REFUSED_BLOCKING_QUESTION

        line = _chair_degraded_reason(_V(), _C())
        assert "no findings to synthesise" not in line
        assert "blocking research question" in line


# ---------------------------------------------------------------------------
# 2. The ledger tally and the tech block count
# ---------------------------------------------------------------------------


class TestTheTally:
    async def test_tally_counts_the_ledger(self, session) -> None:
        run = await _refused_run(session, issuer_findings=2, web_findings=2)
        a = await ledger.record_finding(session, run, statement="a", evidence_ids=["e"])
        b = await ledger.record_finding(session, run, statement="b", evidence_ids=["f"])
        d = await ledger.record_disagreement(session, run, finding_a=a, finding_b=b,
                                             nature="value")
        await ledger.record_finding(session, run, statement="gone", evidence_ids=["g"],
                                    verification_status="withdrawn")
        await ledger.record_gap(session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                                description="no covenant terms")
        await session.commit()
        tally = await tally_ledger(session, run)
        assert tally.findings == 6, "withdrawn excluded"
        assert tally.primary_source == 2
        assert tally.unresolved_disagreement_ids == (str(d.id),)
        assert tally.gaps_open == 1

    def test_the_tech_block_count_is_the_ledger_count(self) -> None:
        payload = _payload(council={"convened": False, "refusal_reason":
                                    REFUSED_BLOCKING_QUESTION, "finding_count": 0,
                                    "primary_source_finding_count": 0,
                                    "unresolved_disagreement_count": 0})
        state = _state(payload, tally=_tally(findings=37, primary=17))
        result = inv.enforce(payload, state)
        assert result.v3["council"]["finding_count"] == 37
        assert result.v3["council"]["primary_source_finding_count"] == 17
        assert result.v3["council"]["council_finding_count"] == 0
        assert {v.rule for v in result.violations} >= {inv.RULE_COUNCIL_COUNTS}
        # The input is never mutated.
        assert payload["council"]["finding_count"] == 0


# ---------------------------------------------------------------------------
# 3. Each invariant fires on its violating example and NOT on a near miss
# ---------------------------------------------------------------------------


def _hit(sentence: str, state: dict[str, Any], site: str = inv.SITE_RECORD) -> str | None:
    hit = inv.check_sentence(sentence, state, site=site)
    return hit.rule if hit else None


#: Withheld in the stored reports and confirmed by review (scratchpad replay): each is an
#: UNQUALIFIED claim that the evidence holds no issuer document, while it held 2-7.
_REAL_INVENTORY_DENIALS = [
    # PYPL3 gaps[13]
    "No issuer-own press release or filing in evidence, so latest issuer disclosure "
    "date/period unconfirmed.",
    # CLLS3/4/5, LIN, LIN2 legacy council
    "No primary filings, financial statements, or company disclosures are included "
    "in the evidence pack.",
    "No primary filings, financial statements, or company disclosures are present in "
    "the evidence pack.",
    "Not yet acquired by InvestingBuddy (platform evidence gap): No company disclosures, "
    "filings, or press releases are present to provide context on business developments "
    "or strategy.",
    "Not yet acquired by InvestingBuddy (platform evidence gap): No company disclosures, "
    "press releases, or regulatory filings are included to provide insight into recent "
    "business developments or strategic initiatives.",
    "Not yet acquired by InvestingBuddy (platform evidence gap): No primary regulatory "
    "filings or company disclosures are included, limiting verification of data and "
    "insight into corporate governance or strategic developments.",
    "Not yet acquired by InvestingBuddy (platform evidence gap): No primary filings or "
    "official company disclosures are included, preventing verification of financial "
    "performance or strategic developments.",
    "No company disclosures or press releases are available to identify potential "
    "strategic developments or partnerships.",
    "The absence of primary financial data and company disclosures limits the ability to "
    "assess CELLECTIS's business performance, financial health, and strategic outlook.",
    "However, the absence of fundamental financial data or company disclosures limits "
    "deeper analysis of business performance or upcoming catalysts.",
    "There is a notable absence of fundamental financial data and primary filings, which "
    "restricts the ability to assess the company's financial health.",
    "The absence of fundamental financial data and primary regulatory filings severely "
    "limits the ability to evaluate the company's operational performance.",
    "There is a complete absence of fundamental financial data and company disclosures, "
    "which prevents a reliable assessment of the company's economic fundamentals.",
    "Not yet acquired by InvestingBuddy (platform evidence gap): Lack of primary filings, "
    "financial statements, and company disclosures severely limits fundamental analysis.",
    # CLLS3 legacy council
    "There is no primary financial data, regulatory filings, or company disclosures "
    "included, which severely restricts the ability to analyze the company's financial "
    "health.",
    "The absence of fundamental financial data and primary disclosures limits the "
    "ability to assess the company's operational performance.",
]

#: QA review N2: claims about a SUBSET of the inventory, or not about it at all. Each is
#: true (or not contradicted) while the issuer's documents are held and cited.
_INVENTORY_NEAR_MISSES = [
    "No issuer filing in evidence breaks out revenue by segment.",
    "No issuer release for Q4 2026 is in evidence.",
    "No issuer filing states the covenant headroom.",
    "Lack of any forthcoming company disclosures or filings.",
    "No 10-Q has been filed yet for Q3.",
    "No 10-Q has been filed yet for Q4 2026.",
    "The company has not issued a press release on the acquisition.",
    "No issuer press release is available on the acquisition.",
    "No issuer press release on the restructuring is in evidence.",
    "No company announcement about the CEO succession is available.",
    "No company filing is available yet for the fourth quarter.",
    "No official guidance document is available.",
    "No company documents are available that quantify the buyback.",
    "No primary filing is available in English.",
    "The evidence includes no issuer press releases with guidance.",
    "The evidence includes no issuer press release with guidance.",
    "The evidence includes no issuer press releases confirming the guidance.",
    # PYPL3 chair.key_points[7] — scoped by "confirming" (withheld before review).
    "The evidence includes no issuer press releases or filings confirming the latest "
    "disclosure dates or forward-looking guidance, limiting temporal context.",
    "The evidence contains no company filings regarding the lawsuit.",
    "The evidence pack contains no company announcements for Q4.",
    "The evidence pack contains no company announcement for Q4.",
    "The record has no issuer filing in German.",
    "The record has no issuer filings in German.",
    "The lack of any company disclosures on pricing limits the analysis.",
    "Lack of primary filings for the subsidiary limits analysis.",
    "The absence of financial statements and issuer filings for the Venmo segment "
    "limits analysis.",
    "Without issuer filings since the merger, the analysis relies on press.",
    "No issuer filings are available, for the December quarter at least.",
    "No issuer press release is available to confirm the Q3 date.",
    # "verified" scopes the claim: the ledger may hold documents it has not verified.
    "No verified company disclosures, filings, or press releases are available to "
    "provide context on operational performance.",
    "No primary company filings, financial statements, or verified company disclosures "
    "are included in the evidence pack.",
    "No audited company filings are available.",
    # QA re-review X3: a scope BEFORE the claim, or another entity as its subject.
    "Iluka has no regulatory filings available.",
    "The JV partner has no company filings present.",
    "The peer company has no regulatory filings available.",
    "For the offtake partner Iluka, no company filings are present.",
    "Unlike the peer, for which no company filings are available, the subject files 10-Qs.",
    "Before the IPO no company filings were available.",
    "Until 2024, no company disclosures were found.",
    "On the acquisition, no issuer press releases are available.",
    "In German, no issuer filings are available.",
    "For Q4, the evidence pack contains no company filings.",
    "Before the listing, the absence of company disclosures limited analysis.",
    "It is not the case that no issuer filings are present.",
    # QA final review R2: real scopes stay kept.
    "Since the merger, no company disclosures were found.",
    "Before the IPO, no company filings were available.",
]


class TestNoIssuerDocument:
    """PYPL3-1, CLLS-4: denying the issuer's documents while they are held and cited."""

    @pytest.mark.parametrize("sentence", [
        *_REAL_INVENTORY_DENIALS,
        # The research itself, or the company under research, as the subject.
        "The company has no regulatory filings available.",
        "The evidence pack contains no company filings.",
        "Overall, no issuer filings are present in the pack.",
        # QA final review R2: an opener about the research itself, about now, or the
        # argument's structure is not a scope; "since / as / because no …" is causal;
        # "not" elsewhere in the sentence does not negate the claim.
        "In this report, no issuer filings are available.",
        "As of today, no company filings are present.",
        "Since no issuer filings are present, the analysis relies on news.",
        "As no issuer filings are present, the assessment is limited.",
        "Because no company filings are available, figures come from press.",
        "In summary, no issuer filings are available.",
        "At present, no company filings are present.",
        "As a result, no issuer filings are in evidence.",
        "In particular, no company filings are present.",
        "If anything, no company filings are available.",
        "In the evidence pack, no issuer filings are present.",
        "Within the evidence, no company disclosures were found.",
        "From the evidence provided, no issuer filings are available.",
        "Currently, no company filings are available.",
        "In 2025 revenue rose 10% and no issuer filings are present.",
        "The company does not publish guidance, and no issuer filings are present.",
    ])
    def test_fires(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_NO_ISSUER_DOC

    @pytest.mark.parametrize("sentence", _INVENTORY_NEAR_MISSES)
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        for site in (inv.SITE_CHAIR, inv.SITE_RECORD, inv.SITE_LEGACY):
            assert _hit(sentence, _state(tally=_tally()), site) is None

    def test_does_not_fire_when_nothing_is_cited(self) -> None:
        sentence = "No primary filings are present in the evidence pack."
        assert _hit(sentence, _state(tally=_tally(primary=0))) is None


def _lin_state() -> dict[str, Any]:
    """An ASX issuer: annual report 24 Sep, quarterly report 25 Oct (core disclosures)."""
    payload = _payload(
        core_filings={},
        core_disclosures={"documents": [
            {"state": "ready", "filing_date": "2026-09-24", "document_kind": "annual_report",
             "headline": "Annual Report 2026", "document_ref": "03140001"},
            {"state": "ready", "filing_date": "2026-10-25",
             "document_kind": "results_release", "headline": "September Quarterly Report",
             "document_ref": "03157011"},
        ]},
        financial_statements_state={
            "current_period": {"state": "report_acquired_facts_not_extracted",
                               "document": {"headline": "September Quarterly Report",
                                            "filing_date": "2026-10-25"}},
        },
    )
    return _state(payload, tally=_tally())


def _hon_state() -> dict[str, Any]:
    """The subject's 10-Q filed 22 Oct, and a cited issuer-IR page of a spun-off
    subsidiary published 28 Oct (QA review N1)."""
    payload = _payload(
        core_filings={"quarterly": {"form": "10-Q", "state": "ready",
                                    "accession": "0000773840-26-000143",
                                    "filing_date": "2026-10-22", "period_end": "2026-09-30"}},
        financial_statements_state={},
        findings=[
            _issuer_finding("Q3 2026 sales rose.", "2026-10-22", "2026-Q3"),
            {"finding_id": "aero", "statement": "Aerospace Q2 2026 sales.",
             "source_kinds": ["issuer_ir", "secondary_web"],
             "source_published_at": "2026-10-28", "period_key": "2026-Q2"},
        ],
    )
    return _state(payload, tally=_tally())


class TestOlderDocumentNamedAsNewest:
    """PYPL2-3, ADBE-5, LIN-5: an older DOCUMENT named as the newest."""

    def test_fires_on_an_older_dated_release(self) -> None:
        sentence = ("Issuer's own latest disclosure in evidence is a press release dated "
                    "2026-07-28 with unaudited condensed consolidated statements.")
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_STALE_LATEST

    def test_fires_on_an_older_accession(self) -> None:
        sentence = ("Newest primary filing in evidence is Form 10-K, accession "
                    "0001633917-26-000024.")
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_STALE_LATEST

    def test_fires_on_an_older_filing_date(self) -> None:
        assert _hit("The most recent filing is the 10-K filed 2026-02-04.",
                    _state(tally=_tally())) == inv.RULE_STALE_LATEST

    def test_fires_on_an_older_publication_date_lin(self) -> None:
        sentence = ("The newest issuer disclosure in the evidence is the Annual Report "
                    "2026, published 24 September 2026, for the annual period 2026.")
        assert _hit(sentence, _lin_state()) == inv.RULE_STALE_LATEST

    @pytest.mark.parametrize("sentence", [
        # The newest IS named (a period-end date older than it is not the document).
        "Newest dated evidence is a 2026-10-27 aggregator article reporting on the "
        "quarter ended 30 Sep 2026.",
        # Newest named first, an older one mentioned as context.
        "The most recent issuer filing in evidence is Form 10-Q 0001633917-26-000088; "
        "the older annual report 10-K 0001633917-26-000024 also appears.",
        # Qualified: the newest filing WITH period figures.
        "Latest primary filing with period figures: 10-K, published 2026-02-03.",
        "The latest filing with segment detail is the 10-K filed 2026-02-04.",
        # Ordinal: not a claim about the newest.
        "Second-newest: 10-K published 2026-02-03.",
        # Not a document claim at all.
        "The newest period in evidence is a quarter, not a year.",
        # A press release may be the latest PRESS RELEASE while a later 10-Q is held.
        "The latest press release dated 2026-07-28 announced Q2 results.",
        "The latest earnings release, dated 2026-07-28, reported Q2 results.",
        # "latest disclosure IN THE 10-K" is a disclosure inside one document.
        "Latest disclosure in the 10-K (2026-02-04) shows 2,000 employees.",
        "The latest annual report is the 10-K for FY2025.",
        "The latest annual report, the FY2025 10-K, shows revenue of USD 31.8bn.",
        "The latest 10-K, filed 2026-02-04, reports revenue of USD 31.8bn.",
        "The newest document in the pack, dated 2026-10-27, is the 10-Q.",
        # QA review B3: covered-period dates and periods are not the document's date.
        "The latest filing covers 1 July 2026 to 30 September 2026.",
        "The latest filing covers the period from 1 July 2026 through 30 September 2026.",
        "The most recent filing, for the quarter ended September 30, 2026, was filed on "
        "October 27, 2026.",
        "The latest filing reports cash of USD 6.1bn at 30 September 2026.",
        "The latest filing shows the share count on 2026-09-30.",
        "The latest filing reports the three months to September 30, 2026 and the nine "
        "months since January 1, 2026.",
        "The most recent disclosure reports Q3 2026 revenue of USD 8.4bn.",
        "The latest filing compares Q3 2026 with Q3 2025.",
        "The latest filing compares revenue with FY2025.",
        "The newest filing restates FY2024 figures.",
        # ANTO: a period in a document's title is not a document date (withheld before
        # review on a secondary finding's period).
        "Most recent disclosure referenced is the 2024 Annual Report and Financial "
        "Statements, group scope.",
        # HON2: the three months ended June 30 is a covered period.
        "Newest results disclosure is for the three months ended June 30, 2026; "
        "discontinued operations shown in Q3.",
        # QA re-review X2: a date of ANOTHER document, outside the anchor's clause, or
        # after the newest form type is named.
        "The latest filing is the Q3 10-Q, which supersedes the 10-K filed 2026-02-04.",
        "The latest disclosure, unlike the 10-K filed 2026-02-04, includes segment data.",
        "The latest issuer disclosure is the 10-Q; an earlier release dated 2026-07-28 "
        "gave Q2 results.",
        "The latest filing in evidence is the Q3 10-Q; the 8-K dated 2026-07-28 "
        "announced the CEO change.",
        "The newest filing (10-Q, 2026-10-27) follows the 10-K (2026-02-04).",
        "The most recent filing is the 10-Q, after the 10-K filed 2026-02-04.",
        "The latest filing replaces the prior 10-K filed 2026-02-04.",
        # QA final review R1: ", and the …" starts another document's clause.
        "The latest disclosure is the Q3 results, and the release dated 2026-07-28 "
        "covered Q2.",
        "The latest filing is the Q3 10-Q and the 8-K dated 2026-07-28 announced the "
        "CEO change.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally()), inv.SITE_CHAIR) is None

    @pytest.mark.parametrize("sentence", [
        "The latest annual report is the Annual Report 2026, published 24 September 2026.",
        "The newest annual report in evidence, published 24 September 2026, covers FY2026.",
        "The latest annual report was published 24 September 2026.",
        "The most recent disclosure is the September Quarterly Report dated 25 Oct 2026, "
        "about the quarter ended 30 Sep 2026.",
        # QA final review R1: the newest held document's own label names it.
        "The newest disclosure is the September Quarterly Report and the Annual Report "
        "dated 24 September 2026 remains the annual reference.",
        "The newest disclosure is the September Quarterly Report, which follows the "
        "Annual Report dated 24 September 2026.",
    ])
    def test_near_miss_does_not_fire_lin(self, sentence: str) -> None:
        assert _hit(sentence, _lin_state(), inv.SITE_CHAIR) is None

    def test_newest_is_a_held_document_not_a_cited_page(self) -> None:
        """QA review N1: a subsidiary's issuer-IR page cited later must not outrank the
        subject's own 10-Q as the newest document."""
        state = _hon_state()
        newest = state["documents"]["newest_issuer_document"]
        assert (newest["form"], newest["date"]) == ("10-Q", "2026-10-22")
        assert state["documents"]["newest_cited_issuer_document"]["date"] == "2026-10-28"
        for sentence in (
            "The latest filing is the 10-Q filed 2026-10-22.",
            "The newest issuer filing in evidence is the 10-Q dated 22 October 2026.",
            "The company's most recent filing is the Q3 2026 10-Q.",
        ):
            assert _hit(sentence, state, inv.SITE_CHAIR) is None

    def test_the_latest_period_comes_from_issuer_or_verified_findings(self) -> None:
        """QA review N3: an unverified secondary finding about 2026-Q3 does not make
        2026-Q3 the latest reported period."""
        payload = _payload(core_filings={}, financial_statements_state={}, findings=[
            _issuer_finding("FY2025 revenue.", "2026-03-01", "FY2025"),
            {"finding_id": "q3", "statement": "Q3 2026 copper output rose.",
             "source_kinds": ["secondary_web"], "source_published_at": "2026-10-15",
             "period_key": "2026-Q3", "verification_status": "unverified"},
        ])
        state = _state(payload, tally=_tally())
        assert state["documents"]["latest_reporting_period"] == "FY2025"
        payload["findings"][1]["verification_status"] = "verified"
        state = _state(payload, tally=_tally())
        assert state["documents"]["latest_reporting_period"] == "2026-Q3"
        assert state["documents"]["latest_reporting_period_source"] == "verified_finding"

    def test_a_forward_period_tag_is_not_the_latest_period(self) -> None:
        """CLLS4-2: 'runway into H2 2028' tagged 2028-H2 must not become the latest
        period held."""
        payload = _payload(findings=[_issuer_finding("runway", "2026-09-14", "2028-H2")])
        state = _state(payload, tally=_tally())
        assert state["documents"]["latest_reporting_period"] != "2028-H2"


class TestLatestPeriodDenied:
    def test_fires_when_the_quarterly_report_is_held(self) -> None:
        # LIN gaps[16]
        sentence = ("No disclosure newer than 24 September 2026 appears in the evidence, "
                    "and no quarterly report is present.")
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_LATEST_PERIOD
        assert _hit(sentence, _lin_state()) == inv.RULE_LATEST_PERIOD
        assert _hit("No quarterly report is present.", _lin_state()) == inv.RULE_LATEST_PERIOD
        assert _hit("No quarterly report is present in the evidence pack.",
                    _lin_state()) == inv.RULE_LATEST_PERIOD
        # QA final review R2: an opener about the research itself is not a scope.
        for sentence in ("Overall, in the evidence, no quarterly report is present.",
                         "In this report, the latest quarter is not available."):
            assert _hit(sentence, _lin_state()) == inv.RULE_LATEST_PERIOD

    def test_fires_on_latest_quarter_not_in_this_report(self) -> None:
        assert _hit("Latest quarter: Not in this report.",
                    _state(tally=_tally())) == inv.RULE_LATEST_PERIOD

    @pytest.mark.parametrize("sentence", [
        # TRUE: the report is held but no figure was extracted from it.
        "Latest quarter figures are not available.",
        # A field of the latest quarter, not the quarter itself.
        "The latest quarter's segment split is not in evidence.",
        "No quarterly report figure for capex is present.",
        # QA review N2: scoped to another period, venue, or a rule — not the inventory.
        "No quarterly report is present for the December quarter.",
        "No quarterly report has been filed with the SEC.",
        "No quarterly report is available on EDGAR because the company is ASX-listed.",
        "No quarterly report is required for ASX main-board issuers.",
        "No interim report is required because the company reports semi-annually.",
        "No interim report is available, for the subsidiary.",
        "The latest quarterly results are not yet available for Q4.",
        "The latest quarter's figures are not available.",
        "Latest quarter figures are not in this report.",
        "The latest quarter: not available for the segment breakdown.",
        # QA re-review X3: the scope comes first.
        "For the December quarter, no quarterly report is present.",
        "For Q4, the latest quarter is not available.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) is None
        assert _hit(sentence, _lin_state()) is None

    def test_does_not_fire_when_nothing_is_held(self) -> None:
        payload = _payload(core_filings={}, financial_statements_state={})
        assert _hit("No quarterly report is present.",
                    _state(payload, tally=_tally())) is None


class TestNoRecentDevelopments:
    def _state(self) -> dict[str, Any]:
        payload = _payload(findings=[_issuer_finding("Q3 release", "2026-10-27", "2026-Q3")])
        return _state(payload, tally=_tally())

    @pytest.mark.parametrize("sentence", [
        "There were no recent developments.",
        # QA final review R2: a discourse opener is not a scope.
        "On balance, there are no recent developments.",
    ])
    def test_fires(self, sentence: str) -> None:
        assert _hit(sentence, self._state()) == inv.RULE_NO_RECENT

    @pytest.mark.parametrize("sentence", [
        "No recent developments in the litigation were disclosed.",
        "No recent developments regarding the litigation.",
        "There were no new announcements on the dividend.",
        "No new disclosures were found about the merger.",
        "On the litigation, there are no recent developments.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, self._state()) is None

    def test_does_not_fire_without_a_current_disclosure(self) -> None:
        payload = _payload(core_filings={}, financial_statements_state={},
                           findings=[_issuer_finding("old", "2025-01-10", "2024")])
        assert _hit("There were no recent developments.",
                    _state(payload, tally=_tally())) is None


class TestNoCashFlow:
    def test_fires(self) -> None:
        assert _hit("No cash flow data is available.",
                    _state(tally=_tally())) == inv.RULE_NO_CASH_FLOW
        assert _hit("On balance, no cash flow data is available.",
                    _state(tally=_tally())) == inv.RULE_NO_CASH_FLOW

    def test_fires_on_free_cash_flow_after_a_discourse_opener(self) -> None:
        payload = _payload(findings=[
            *_payload()["findings"],
            _issuer_finding("FY2025 free cash flow was $5,500m.", None, "FY2025"),
        ])
        assert _hit("On balance, no free cash flow is reported.",
                    _state(payload, tally=_tally())) == inv.RULE_NO_CASH_FLOW

    @pytest.mark.parametrize("sentence", [
        # Operating cash flow is not FREE cash flow.
        "Lack of free cash flow data prevents a full assessment.",
        # Qualified to a period.
        "No cash-flow figure for the latest quarter is in evidence.",
        "No free cash flow guidance is provided.",
        "No cash flow hedge accounting is applied.",
        "There is no operating cash flow data for the segment.",
        "No cash flow data is available for the segment.",
        "Lack of cash flow data for the subsidiary limits analysis.",
        # QA re-review X3: the scope comes first.
        "For the segment, no cash flow data is available.",
        "For the segment, no operating cash flow is reported.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) is None


class TestEvidenceAdequate:
    def test_fires_when_current_period_extraction_failed(self) -> None:
        assert _hit("The evidence is adequate.",
                    _state(tally=_tally())) == inv.RULE_EVIDENCE_ADEQUATE

    @pytest.mark.parametrize("sentence", [
        "The evidence is sufficient to assess current financial health but lacks "
        "segment detail.",
        "The evidence is insufficient.",
        "The evidence is not adequate.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) is None

    def test_does_not_fire_when_issuer_facts_cover_the_period(self) -> None:
        payload = _payload(findings=[_issuer_finding("Q3 revenue", None, "2026-Q3")])
        assert _hit("The evidence is adequate.", _state(payload, tally=_tally())) is None


class TestCountsAndReasons:
    def test_a_wrong_deterministic_count_is_repaired(self) -> None:
        payload = _payload(chair={
            "synthesis": "Deterministic synthesis: 0 finding(s), 0 verified, 60 open "
                         "gap(s), 0 unresolved disagreement(s). No model was available, "
                         "so this is the ledger's own state rather than an interpretation "
                         "of it.",
            "key_points": [], "deterministic_fallback": True,
            "fallback_reason": FALLBACK_NOT_CONVENED, "unresolved_disagreement_ids": [],
        }, council={"convened": False, "refusal_reason": REFUSED_BLOCKING_QUESTION,
                    "finding_count": 0})
        result = inv.enforce(payload, _state(payload, tally=_tally(findings=37)))
        synthesis = result.v3["chair"]["synthesis"]
        assert synthesis.startswith("Deterministic synthesis: 37 finding(s)")
        assert "No model was available" not in synthesis
        assert "blocking research question" in synthesis
        rules = {v.rule for v in result.violations}
        assert {inv.RULE_FINDING_COUNT, inv.RULE_FALLBACK_REASON} <= rules

    def test_a_true_no_model_sentence_is_kept(self) -> None:
        payload = _payload(routing={"slots": {"chair": {"slot": "chair", "vendor": None}}},
                           chair={"synthesis": "Deterministic synthesis: 3 finding(s), 0 "
                                  "verified, 4 open gap(s), 0 unresolved disagreement(s). No "
                                  "model was available, so this is the ledger's own state "
                                  "rather than an interpretation of it.",
                                  "deterministic_fallback": True,
                                  "fallback_reason": FALLBACK_NO_MODEL})
        result = inv.enforce(payload, _state(payload, tally=_tally()))
        assert result.violations == []
        assert result.v3["chair"]["synthesis"] == payload["chair"]["synthesis"]

    def test_the_degraded_reason_is_repaired(self) -> None:
        payload = _payload(
            council={"convened": False, "refusal_reason": REFUSED_BLOCKING_QUESTION},
            chair={"deterministic_fallback": True, "fallback_reason": FALLBACK_NOT_CONVENED},
            degraded=["the chair returned the deterministic verdict because the council "
                      "did not convene (no findings to synthesise)"],
        )
        result = inv.enforce(payload, _state(payload, tally=_tally()))
        assert "no findings" not in result.v3["degraded"][0]
        assert "blocking research question" in result.v3["degraded"][0]


class TestChairDisagreements:
    """LIN-6: an id the ledger does not record; a narrated conflict none backs."""

    def test_an_unknown_disagreement_id_is_dropped_and_recorded(self) -> None:
        known = str(uuid.uuid4())
        invented = str(uuid.uuid4())
        payload = _payload(chair={"synthesis": "", "key_points": [],
                                  "unresolved_disagreement_ids": [known, invented]})
        # ADR-095: the unresolved ids are the computed conflicts'.
        payload["gap_reconciliation"] = {"conflicts": [
            {"finding_a_id": "a", "finding_b_id": "b", "disagreement_id": known}]}
        result = inv.enforce(payload, _state(payload, tally=_tally(unresolved_ids=(known,))))
        assert result.v3["chair"]["unresolved_disagreement_ids"] == [known]
        assert result.v3["chair"]["dropped_disagreement_ids"] == [invented]
        rules = [v.rule for v in result.violations if v.rule != inv.RULE_COUNCIL_COUNTS]
        assert rules == [inv.RULE_UNKNOWN_DISAGREEMENT_ID]

    def test_a_narrated_disagreement_with_none_recorded_is_withheld(self) -> None:
        payload = _payload(chair={
            "synthesis": "Revenue grew 4%. The red team disputed the net debt figure.",
            "key_points": [{"text": "Unresolved disagreements exist on net debt.",
                            "finding_id": "f"}],
            "unresolved_disagreement_ids": [],
        })
        # ADR-095 review: "the red team disputed" is held to the unresolved challenges.
        payload["challenges"] = {"challenges": 2, "resolved": 2, "unresolved": 0}
        result = inv.enforce(payload, _state(payload, tally=_tally()))
        assert result.v3["chair"]["synthesis"] == "Revenue grew 4%."
        assert result.v3["chair"]["key_points"][0]["text"].startswith(
            "[Withheld at report assembly:")

    def test_a_recorded_disagreement_may_be_narrated(self) -> None:
        payload = _payload(chair={"synthesis": "Unresolved disagreements exist on net debt."})
        payload["gap_reconciliation"] = {"conflicts": [
            {"finding_a_id": "a", "finding_b_id": "b", "disagreement_id": "d1"}]}
        state = _state(payload, tally=_tally(unresolved_ids=("d1",)))
        rules = {v.rule for v in inv.enforce(payload, state).violations}
        # Only the stale council block count is repaired; the sentence stands.
        assert rules == {inv.RULE_COUNCIL_COUNTS}

    @pytest.mark.parametrize("sentence", [
        # CFR chair.synthesis — TRUE, and about the evidence, not a ledger disagreement
        # (withheld before review).
        "However, there is conflicting data on FY2026 Group operating profit, reported as "
        "both EUR 4.5 billion and 'to EUR 5 billion'.",
        "Analysts disagree on the timing of the margin recovery.",
        "Management's FY2026 guidance contradicts the consensus estimate.",
        "Revenue and EBITDA figures conflict between the 10-Q and the press release.",
        "There are conflicting reports about the CEO's departure.",
        "Two findings give conflicting figures for FY2026 operating profit.",
        "Peers disagree with the company's pricing strategy.",
        "This contradicts the bear case.",
        "There is a notable disagreement in the evidence regarding revenue.",
        "The red team found no disagreements.",
        "No unresolved disagreements remain.",
        # QA re-review X1: challenges are recorded apart from disagreements (CFR: 5
        # challenges, 0 disagreements).
        "The red team challenged five findings; all challenges were resolved.",
        "The red team challenged the FY2026 operating profit figure, and the challenge "
        "was resolved.",
        "Red-team challenges lowered confidence on two findings.",
        "All unresolved challenges were closed in round two.",
        "There are no unresolved challenges.",
    ])
    def test_a_disagreement_about_the_world_is_kept(self, sentence: str) -> None:
        state = _state(tally=_tally())
        assert state["disagreements"]["total"] == 0
        assert _hit(sentence, state, inv.SITE_CHAIR) is None

    def test_a_negated_disagreement_is_not_narration(self) -> None:
        payload = _payload(chair={"synthesis": "No disagreements were recorded."})
        assert inv.enforce(payload, _state(payload, tally=_tally())).violations == []


# ---------------------------------------------------------------------------
# 4. Council status and the legacy label
# ---------------------------------------------------------------------------


_LEGACY = {
    "agents_completed": 8, "agents_failed": 0, "agents_skipped": 0,
    "committee_label_basis": "llm_chair",
    "agents": [{"agent_name": n, "status": "completed", "summary": "s",
                "unsupported_claims": []}
               for n in ("financial_analyst", "business_moat", "catalyst",
                         "risk_governance", "valuation_guard", "source_quality_critic",
                         "red_team", "committee_chair")],
}


class TestCouncilStatus:
    def test_a_healthy_council_reads_as_before(self) -> None:
        state = _state(tally=_tally(), llm_council=_LEGACY)
        assert state["council"]["status_text"] == "8 agents completed"
        assert state["council"]["degraded"] is False

    def test_a_refused_council_with_a_fallback_chair_is_not_8_completed(self) -> None:
        payload = _payload(
            council={"convened": False, "refusal_reason": REFUSED_BLOCKING_QUESTION},
            chair={"deterministic_fallback": True, "fallback_reason": FALLBACK_NOT_CONVENED},
            challenges={"challenges": 0},
        )
        state = _state(payload, tally=_tally(), llm_council=_LEGACY)
        text = state["council"]["status_text"]
        assert text.startswith("8 of 8 agents completed")
        assert "council did not convene" in text
        assert "chair used the deterministic verdict" in text
        assert "red team reviewed no findings" in text
        result = inv.enforce(payload, state, llm_council=_LEGACY)
        assert inv.RULE_COUNCIL_STATUS in {v.rule for v in result.violations}

    def test_a_withheld_red_team_and_a_substituted_seat_are_named(self) -> None:
        legacy = json.loads(json.dumps(_LEGACY))
        legacy["agents_completed"], legacy["agents_failed"] = 7, 1
        legacy["agents"][6].update(status="failed", summary="[Output withheld: x]")
        payload = _payload()
        payload["routing"]["slots"]["red_team"]["vendor"] = "deepseek"
        council = _state(payload, tally=_tally(), llm_council=legacy)["council"]
        assert council["red_team"]["withheld"] is True
        assert "red team output withheld" in council["status_text"]
        assert "red team seat on deepseek" in council["status_text"]


class TestAttachToReport:
    def _report(self, content: dict[str, Any] | None = None) -> Any:
        class _Row:
            pass

        row = _Row()
        row.created_at = date(2026, 11, 2)
        row.source_summary_json = {"llm_council": json.loads(json.dumps(_LEGACY))}
        row.content_markdown = (
            "# Draft\n\n```json\n" + json.dumps(content) + "\n```\n" if content else ""
        )
        return row

    def _outcome(self) -> V3ResearchOutcome:
        p = _payload()
        return V3ResearchOutcome(
            research_run_id=uuid.uuid4(), council=p["council"], chair=p["chair"],
            routing=p["routing"], loop=p["loop"], challenges=p["challenges"],
            core_filings=p["core_filings"], financial_statements=p["financial_statements_state"],
            findings=p["findings"], ledger_tally=_tally(),
        )

    def test_the_canonical_state_is_persisted_with_a_version(self) -> None:
        report = self._report()
        attach_to_report(report, self._outcome())
        block = report.source_summary_json[SOURCE_SUMMARY_KEY]
        assert block["canonical_state"]["version"] == 1
        assert block["canonical_state"]["derived_from"] == "ledger"
        assert block["canonical_state"]["findings"]["total"] == 3
        assert block["state_invariants"]["version"] == 1
        # The legacy V2 council is labelled, visibly.
        assert block["legacy_council"]["legacy"] is True
        assert "Legacy council (V2)" in block["legacy_council"]["label"]
        # A clean V2 block is left exactly as V2 wrote it.
        assert report.source_summary_json["llm_council"] == _LEGACY

    def test_a_contradicting_legacy_sentence_is_withheld_with_a_reason(self) -> None:
        content = {"llm_council_analysis": {"agents": [{"unsupported_claims": [
            "No primary filings, financial statements, or company disclosures are "
            "included in the evidence pack."]}]}}
        report = self._report(content)
        report.source_summary_json["llm_council"]["agents"][5]["unsupported_claims"] = [
            "No primary filings, financial statements, or company disclosures are "
            "included in the evidence pack."]
        attach_to_report(report, self._outcome())
        legacy = report.source_summary_json["llm_council"]
        assert legacy["agents"][5]["unsupported_claims"][0].startswith(
            "[Withheld at report assembly:")
        assert "Withheld at report assembly" in report.content_markdown
        block = report.source_summary_json[SOURCE_SUMMARY_KEY]
        assert block["state_invariants"]["by_rule"][inv.RULE_NO_ISSUER_DOC] == 2


# ---------------------------------------------------------------------------
# 5. Helpers and the replay
# ---------------------------------------------------------------------------


def test_period_bounds() -> None:
    assert period_bounds("FY2025") == (date(2025, 1, 1), date(2025, 12, 31))
    assert period_bounds("2026-Q3") == (date(2026, 7, 1), date(2026, 9, 30))
    assert period_bounds("H1 2026") == (date(2026, 1, 1), date(2026, 6, 30))
    assert period_bounds("2023/24") is None


def test_the_replay_detects_and_repairs_on_a_stored_shape(tmp_path: Path) -> None:
    from scripts.qa_replay_ledger_state import replay_report

    payload = _payload(
        council={"convened": False, "refusal_reason": REFUSED_BLOCKING_QUESTION,
                 "finding_count": 0},
        chair={"synthesis": "Deterministic synthesis: 0 finding(s), 0 verified, 4 open "
                            "gap(s), 0 unresolved disagreement(s).",
               "deterministic_fallback": True, "fallback_reason": FALLBACK_NOT_CONVENED},
        professional_research={"findings_count": 3, "sections": []},
    )
    out = replay_report({"created_at": "2026-11-02T00:00:00Z",
                         "source_summary_json": {"v3_research": payload}})
    assert out["state"]["derived_from"] == "payload"
    assert out["result"]["by_rule"][inv.RULE_FINDING_COUNT] == 1


# ---------------------------------------------------------------------------
# 6. Withholding removes only the withheld span (QA review M1)
# ---------------------------------------------------------------------------


_DENIAL = "No primary filings, financial statements, or company disclosures are present."


class TestWithheldSpanOnly:
    def _text(self, value: str) -> str:
        enforcer = inv._Enforcer(_state(tally=_tally()))
        return enforcer.text(value, "x", inv.SITE_LEGACY)

    def test_separators_and_newlines_are_kept(self) -> None:
        value = "Revenue rose.  Margins held.\n\nCapex fell. " + _DENIAL + "\nDebt is low."
        assert self._text(value) == "Revenue rose.  Margins held.\n\nCapex fell.\nDebt is low."

    def test_one_false_bullet_does_not_withhold_the_block(self) -> None:
        value = "- Revenue rose 4%.\n- " + _DENIAL + "\n- Net debt fell.\n  * Capex flat."
        assert self._text(value) == "- Revenue rose 4%.\n- Net debt fell.\n  * Capex flat."

    def test_a_bullet_keeps_its_true_sentence(self) -> None:
        value = "1. Revenue rose 4%. " + _DENIAL + "\n2. Net debt fell."
        assert self._text(value) == "1. Revenue rose 4%.\n2. Net debt fell."

    def test_an_untouched_text_is_returned_as_is(self) -> None:
        value = "Revenue rose.\n\n  - Margins held.  \n"
        assert self._text(value) is value

    def test_a_text_with_nothing_left_becomes_a_notice(self) -> None:
        assert self._text("- " + _DENIAL).startswith("[Withheld at report assembly:")

    def test_a_recorded_sentence_is_neutralised_like_its_reason(self) -> None:
        """Security review: the withheld sentence is written into state_invariants after
        the report's safety scan, so it passes the same neutralisation as the reason."""
        from app.schemas.catalyst import neutralize_forbidden_terms

        text = "No issuer filings are present; analysts rate it a strong buy."
        recorded = inv.Violation(inv.RULE_NO_ISSUER_DOC, "x", text, "withheld", "r").to_dict()
        assert recorded["text"] == neutralize_forbidden_terms(text)
        assert "strong buy" not in recorded["text"]

    def test_an_over_long_sentence_is_not_checked(self) -> None:
        sentence = _DENIAL[:-1] + ", " + "and so on, " * 80 + "end."
        assert len(sentence) > inv.MAX_SENTENCE_CHARS
        assert _hit(sentence, _state(tally=_tally())) is None


# ---------------------------------------------------------------------------
# 7. Every pattern and rule runs in linear time (QA review B1)
#
# Not an absolute per-call budget (that flaked under CI load, QA re-review): each
# degenerate input is built at scale 1 and scale 8, and the time at 8x must grow by
# less than 24x (linear ~8x, quadratic ~64x, exponential unbounded), on the fastest of
# five interleaved runs. The ratio is judged only once the large input takes over
# 200 ms — every
# linear pattern stays far below that, and under CI load a millisecond baseline makes a
# ratio noise — and above 1 s anything fails (a quadratic pattern on the 8x inputs,
# 8k-40k characters, takes seconds).
# ---------------------------------------------------------------------------

#: The review's ReDoS repros, and inputs built to make each kind of quantifier
#: backtrack. Each builds its input at a scale ``k``.
_DEGENERATE: list[Any] = [
    lambda k: "No issuer " + "press releases " * (10 * k) + "mention it.",
    lambda k: "No issuer " + "filings " * (14 * k) + "mention it.",
    lambda k: "No issuer " + "press releases " * (100 * k) + "mention it.",
    lambda k: "No issuer " + "filings " * (200 * k) + "x",
    lambda k: "No issuer filings, " * (150 * k) + "x",
    lambda k: "No issuer " + "press releases, or " * (150 * k) + "x",
    lambda k: "The evidence includes no issuer " + "filings or " * (150 * k) + "x",
    lambda k: "absence of " + "financial data and " * (150 * k) + "x",
    lambda k: "lack of " + "any primary " * (200 * k) + "x",
    lambda k: "For the peer, " * (100 * k) + "no issuer filings are present.",
    lambda k: "Iluka has " * (200 * k) + "no issuer filings present.",
    lambda k: "no " * (1000 * k),
    lambda k: " " * (4000 * k) + "x",
    lambda k: "\t" * (4000 * k),
    lambda k: ", or " * (800 * k),
    lambda k: "latest quarter" + " " * (2500 * k) + "x",
    lambda k: "latest quarterly " + "report " * (500 * k),
    lambda k: "newest " * (800 * k) + "filing",
    lambda k: "most recent " + "issuer " * (500 * k) + "filing",
    lambda k: "The latest filing is " + "the 10-K filed 2026-02-04, " * (100 * k),
    lambda k: "a" * (5000 * k),
    lambda k: "1" * (5000 * k),
    lambda k: "2026-" * (1000 * k),
    lambda k: "dated " * (800 * k) + "2026-01-01",
    lambda k: "10-K " * (1000 * k),
    lambda k: "cash flow " * (500 * k),
    lambda k: "no cash flow " * (300 * k),
    lambda k: "evidence is " * (500 * k),
    lambda k: "unresolved " * (800 * k),
    lambda k: "no unresolved " * (500 * k),
    lambda k: "red team " * (500 * k),
    lambda k: "disagreement " * (500 * k),
    lambda k: "." * (5000 * k),
    lambda k: ". " * (2500 * k) + "A",
    lambda k: "\n" * (5000 * k),
    lambda k: "- " * (2500 * k),
    lambda k: "Deterministic synthesis: " + "1 " * (1500 * k),
    lambda k: "September " * (800 * k) + "2026",
    lambda k: "30 " * (1500 * k) + "September 2026",
    lambda k: "in the " * (800 * k) + "evidence",
    lambda k: "second " * (800 * k) + "newest filing",
]
_SCALE = 8
_MAX_RATIO = 24.0
_NOISE_FLOOR_SECONDS = 0.2
_ABSOLUTE_BUDGET_SECONDS = 1.0


def _patterns() -> list[tuple[str, Any]]:
    import re

    from app.services.ledger import canonical_state as cs
    from app.services.ledger import ledger_invariants as li

    out: list[tuple[str, Any]] = []
    # ADR-095: the impossible-combination checker's patterns too.
    for module in (inv, cs, li):
        short = module.__name__.rsplit(".", 1)[1]
        for name, value in vars(module).items():
            if isinstance(value, re.Pattern):
                out.append((f"{short}.{name}", value))
            elif isinstance(value, tuple):
                for i, item in enumerate(value):
                    pattern = item[0] if isinstance(item, tuple) and item else item
                    if isinstance(pattern, re.Pattern):
                        out.append((f"{short}.{name}[{i}]", pattern))
    return out


def _min_of_five_interleaved(fn: Any, small: str, large: str) -> tuple[float, float]:
    """The fastest of five runs of each input, the two interleaved so a burst of load
    hits both. The minimum is the estimate least disturbed by other processes."""
    import time

    best_small = best_large = float("inf")
    for _ in range(5):
        for text, is_large in ((small, False), (large, True)):
            start = time.perf_counter()
            fn(text)
            elapsed = time.perf_counter() - start
            if is_large:
                best_large = min(best_large, elapsed)
            else:
                best_small = min(best_small, elapsed)
    return best_small, best_large


def _assert_scales_linearly(fn: Any, label: str) -> None:
    for make in _DEGENERATE:
        small, large = make(1), make(_SCALE)
        t_small, t_large = _min_of_five_interleaved(fn, small, large)
        assert t_large < _ABSOLUTE_BUDGET_SECONDS, (
            f"{label} took {t_large:.3f}s on {large[:40]!r}")
        if t_large > _NOISE_FLOOR_SECONDS:
            ratio = t_large / max(t_small, 1e-6)
            assert ratio < _MAX_RATIO, (
                f"{label}: {_SCALE}x input took {ratio:.1f}x the time on {large[:40]!r}")


def test_the_pattern_inventory_is_complete() -> None:
    names = {name.split(".", 1)[1] for name, _ in _patterns()}
    assert {"_TOKEN_RE", "_LATEST_DOC_RE", "_DOC_DATE_BEFORE_RE", "_SENTENCE_RE",
            "_BULLET_RE", "_LEDGER_DISAGREEMENT_RE", "_LATEST_UNAVAILABLE_RES[1]",
            "_NO_CASH_FLOW_RES[0]", "_CASH_FLOW_RE", "_ANCHOR_CLAUSE_END_RE",
            "_FORM_RE", "_ORDINAL_BEFORE_RE"} <= names


@pytest.mark.parametrize("name,pattern", _patterns(), ids=[n for n, _ in _patterns()])
def test_every_pattern_is_linear_on_degenerate_input(name: str, pattern: Any) -> None:
    def run(text: str) -> None:
        pattern.search(text)
        for _ in pattern.finditer(text):
            pass
        pattern.match(text)

    _assert_scales_linearly(run, name)


@pytest.mark.parametrize("rule", inv._CHAIR_RULES, ids=lambda r: r.__name__)
def test_every_rule_is_linear_on_degenerate_input(rule: Any) -> None:
    state = _state(tally=_tally())
    # The rules are called directly, past the sentence-length cap.
    _assert_scales_linearly(lambda t: rule(t, state), rule.__name__)


def test_the_token_scan_is_linear_on_degenerate_input() -> None:
    _assert_scales_linearly(inv._denies_issuer_documents, "_denies_issuer_documents")


def test_a_whole_degenerate_text_is_checked_quickly() -> None:
    enforcer = inv._Enforcer(_state(tally=_tally()))
    _assert_scales_linearly(lambda t: enforcer.text(t, "x", inv.SITE_CHAIR), "enforcer")
