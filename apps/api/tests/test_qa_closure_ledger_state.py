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


class TestNoIssuerDocument:
    """PYPL3-1, CLLS-4: denying the issuer's documents while they are held and cited."""

    @pytest.mark.parametrize("sentence", [
        "No issuer-own press release or filing in evidence, so latest issuer disclosure "
        "date/period unconfirmed.",
        "No primary filings, financial statements, or company disclosures are included "
        "in the evidence pack.",
        "The evidence includes no issuer press releases or filings confirming the latest "
        "disclosure dates or forward-looking guidance, limiting temporal context.",
        "The absence of fundamental financial data and primary regulatory filings "
        "severely limits the ability to evaluate the company.",
        "No company disclosures, filings, or press releases are present to provide "
        "context on business operations.",
    ])
    def test_fires(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_NO_ISSUER_DOC

    @pytest.mark.parametrize("sentence", [
        "No issuer filing in evidence breaks out revenue by segment.",
        "No issuer release for Q4 2026 is in evidence.",
        "No issuer filing states the covenant headroom.",
        "Lack of any forthcoming company disclosures or filings.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) is None

    def test_does_not_fire_when_nothing_is_cited(self) -> None:
        sentence = "No primary filings are present in the evidence pack."
        assert _hit(sentence, _state(tally=_tally(primary=0))) is None


class TestOlderDocumentNamedAsNewest:
    """PYPL2-3, ADBE-5, ANTO-5, LIN-5."""

    def test_fires_on_an_older_dated_release(self) -> None:
        sentence = ("Issuer's own latest disclosure in evidence is a press release dated "
                    "2026-07-28 with unaudited condensed consolidated statements.")
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_STALE_LATEST

    def test_fires_on_an_older_accession(self) -> None:
        sentence = ("Newest primary filing in evidence is Form 10-K, accession "
                    "0001633917-26-000024.")
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_STALE_LATEST

    def test_fires_on_an_older_period(self) -> None:
        payload = _payload(findings=[
            *_payload()["findings"],
            {"finding_id": "q3", "statement": "Q3 2026 copper output rose.",
             "source_kinds": ["secondary_web"], "source_published_at": "2026-10-15",
             "period_key": "2026-Q3"},
        ])
        sentence = ("Most recent disclosure referenced is the 2024 Annual Report and "
                    "Financial Statements, group scope.")
        assert _hit(sentence, _state(payload, tally=_tally())) == inv.RULE_STALE_LATEST

    @pytest.mark.parametrize("sentence", [
        # The newest IS named (a period-end date older than it is not the document).
        "Newest dated evidence is a 2026-10-27 aggregator article reporting on the "
        "quarter ended 30 Sep 2026.",
        # Newest named first, an older one mentioned as context.
        "The most recent issuer filing in evidence is Form 10-Q 0001633917-26-000088; "
        "the older annual report 10-K 0001633917-26-000024 also appears.",
        # Qualified: the newest filing WITH period figures.
        "Latest primary filing with period figures: 10-K, published 2026-02-03.",
        # Ordinal: not a claim about the newest.
        "Second-newest: 10-K published 2026-02-03.",
        # Not a document claim at all.
        "The newest period in evidence is a quarter, not a year.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) is None

    def test_a_forward_period_tag_is_not_the_latest_period(self) -> None:
        """CLLS4-2: 'runway into H2 2028' tagged 2028-H2 must not become the latest
        period held."""
        payload = _payload(findings=[_issuer_finding("runway", "2026-09-14", "2028-H2")])
        state = _state(payload, tally=_tally())
        assert state["documents"]["latest_reporting_period"] != "2028-H2"


class TestLatestPeriodDenied:
    def test_fires_when_the_quarterly_report_is_held(self) -> None:
        sentence = ("No disclosure newer than 24 September 2026 appears in the evidence, "
                    "and no quarterly report is present.")
        assert _hit(sentence, _state(tally=_tally())) == inv.RULE_LATEST_PERIOD

    def test_fires_on_latest_quarter_not_in_this_report(self) -> None:
        assert _hit("Latest quarter: Not in this report.",
                    _state(tally=_tally())) == inv.RULE_LATEST_PERIOD

    @pytest.mark.parametrize("sentence", [
        # TRUE: the report is held but no figure was extracted from it.
        "Latest quarter figures are not available.",
        # A field of the latest quarter, not the quarter itself.
        "The latest quarter's segment split is not in evidence.",
        "No quarterly report figure for capex is present.",
    ])
    def test_near_miss_does_not_fire(self, sentence: str) -> None:
        assert _hit(sentence, _state(tally=_tally())) is None

    def test_does_not_fire_when_nothing_is_held(self) -> None:
        payload = _payload(core_filings={}, financial_statements_state={})
        assert _hit("No quarterly report is present.",
                    _state(payload, tally=_tally())) is None


class TestNoRecentDevelopments:
    def _state(self) -> dict[str, Any]:
        payload = _payload(findings=[_issuer_finding("Q3 release", "2026-10-27", "2026-Q3")])
        return _state(payload, tally=_tally())

    def test_fires(self) -> None:
        assert _hit("There were no recent developments.", self._state()) == inv.RULE_NO_RECENT

    def test_near_miss_does_not_fire(self) -> None:
        assert _hit("No recent developments in the litigation were disclosed.",
                    self._state()) is None

    def test_does_not_fire_without_a_current_disclosure(self) -> None:
        payload = _payload(core_filings={}, financial_statements_state={},
                           findings=[_issuer_finding("old", "2025-01-10", "2024")])
        assert _hit("There were no recent developments.",
                    _state(payload, tally=_tally())) is None


class TestNoCashFlow:
    def test_fires(self) -> None:
        assert _hit("No cash flow data is available.",
                    _state(tally=_tally())) == inv.RULE_NO_CASH_FLOW

    @pytest.mark.parametrize("sentence", [
        # Operating cash flow is not FREE cash flow.
        "Lack of free cash flow data prevents a full assessment.",
        # Qualified to a period.
        "No cash-flow figure for the latest quarter is in evidence.",
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
        result = inv.enforce(payload, _state(payload, tally=_tally(unresolved_ids=(known,))))
        assert result.v3["chair"]["unresolved_disagreement_ids"] == [known]
        assert result.v3["chair"]["dropped_disagreement_ids"] == [invented]
        rules = [v.rule for v in result.violations if v.rule != inv.RULE_COUNCIL_COUNTS]
        assert rules == [inv.RULE_UNKNOWN_DISAGREEMENT_ID]

    def test_a_narrated_disagreement_with_none_recorded_is_withheld(self) -> None:
        payload = _payload(chair={
            "synthesis": "Revenue grew 4%. There is a notable disagreement in the evidence "
                         "regarding revenue from mineral sales.",
            "key_points": [{"text": "Unresolved disagreements exist on net debt.",
                            "finding_id": "f"}],
            "unresolved_disagreement_ids": [],
        })
        result = inv.enforce(payload, _state(payload, tally=_tally()))
        assert result.v3["chair"]["synthesis"] == "Revenue grew 4%."
        assert result.v3["chair"]["key_points"][0]["text"].startswith(
            "[Withheld at report assembly:")

    def test_a_recorded_disagreement_may_be_narrated(self) -> None:
        payload = _payload(chair={"synthesis": "Unresolved disagreements exist on net debt."})
        state = _state(payload, tally=_tally(unresolved_ids=("d1",)))
        rules = {v.rule for v in inv.enforce(payload, state).violations}
        # Only the stale council block count is repaired; the sentence stands.
        assert rules == {inv.RULE_COUNCIL_COUNTS}

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
