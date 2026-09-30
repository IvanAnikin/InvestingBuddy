"""Report reconciliation — gap closure (18), temporal supersession (19), "0 verified" (22).

A report must never say "no capital expenditure was acquired" beside a finding that
states the capex, never show last year's guidance as current beside this quarter's, and
never headline "0 verified" when nothing in the pipeline verifies findings at all.
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services import research_fields as rf
from app.services.council_v2 import inputs as council_inputs
from app.services.ledger import store as ledger
from app.services.pipeline import gap_reconciliation as gr
from app.services.pipeline import professional_research as pr


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 043"
)
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / (
    "043_add_report_reconciliation.py"
)

ISSUER = ("issuer_filing",)


def _finding(fid: str, statement: str, **kw) -> gr.FindingFacts:  # noqa: ANN003
    fields, project = gr.finding_fields(statement)
    return gr.FindingFacts(
        finding_id=fid,
        statement=statement,
        fields=kw.pop("fields", fields),
        project=kw.pop("project", project),
        source_kinds=kw.pop("source_kinds", ISSUER),
        **kw,
    )


def _gap(description: str, **kw) -> gr.GapFacts:  # noqa: ANN003
    return gr.GapFacts(
        gap_id=kw.pop("gap_id", "g1"),
        gap_type=kw.pop("gap_type", ledger.GAP_EVIDENCE_UNAVAILABLE),
        description=description,
        **kw,
    )


# ── The field vocabulary ────────────────────────────────────────────────────── #


class TestFieldClassifier:
    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("No capex figure was acquired", "metric:capex"),
            ("capital expenditure is not disclosed", "metric:capex"),
            ("initial capital estimate unavailable", "metric:capex"),
            ("production capacity was not found", "metric:production_capacity"),
            ("nameplate capacity is unknown", "metric:production_capacity"),
            ("design capacity missing", "metric:production_capacity"),
            ("first production date not stated", "milestone:first_production"),
            ("start of production is unclear", "milestone:first_production"),
            ("commissioning timetable not acquired", "milestone:commissioning"),
            ("no offtake agreement found", "commercial:offtake"),
            ("off-take terms undisclosed", "commercial:offtake"),
            ("cash and cash equivalents not acquired", "metric:cash"),
            ("cash balance unknown", "metric:cash"),
            ("cash runway could not be computed", "metric:cash_runway"),
            ("the runway is unclear", "metric:cash_runway"),
            ("NPV not found in the study", "metric:npv"),
            ("net present value missing", "metric:npv"),
            ("capital_expenditure", "metric:capex"),
        ],
    )
    def test_a_gap_is_about_the_field_it_names(self, text: str, field: str) -> None:
        assert field in rf.fields_mentioned(text)

    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("Capex of US$268m for Phase 1", "metric:capex"),
            ("Capital expenditure was A$1.2bn in FY2025", "metric:capex"),
            ("Nameplate capacity of 12,000 tpa NdPr oxide", "metric:production_capacity"),
            ("Production capacity of 40 Mt a year", "metric:production_capacity"),
            ("First production is expected in 2027", "milestone:first_production"),
            ("Commissioning is scheduled for H2 2026", "milestone:commissioning"),
            ("A binding offtake agreement was signed with a major OEM", "commercial:offtake"),
            ("Cash and cash equivalents of A$45.2m at 30 June", "metric:cash"),
            ("The company held US$45m in cash", "metric:cash"),
            ("Funded into 2027 with a runway of 18 months", "metric:cash_runway"),
            ("Post-tax NPV8 of US$1.1bn", "metric:npv"),
        ],
    )
    def test_a_finding_states_a_field_only_affirmatively(self, text: str, field: str) -> None:
        assert field in rf.fields_stated(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Capex has not been disclosed",
            "The company does not state its capital expenditure of any kind",
            "No production capacity figure of any size was found",
            "Capex was discussed at length",  # a metric needs a figure
            "First production timing is under review",  # a milestone needs a date
        ],
    )
    def test_negation_or_no_value_states_nothing(self, text: str) -> None:
        assert rf.fields_stated(text) == ()

    def test_a_denial_scopes_to_its_clause(self) -> None:
        stated = rf.fields_stated("Capex is not disclosed; nameplate capacity of 20,000 tpa")
        assert stated == ("metric:production_capacity",)

    def test_no_issuer_or_project_name_decides_a_field(self) -> None:
        assert rf.fields_mentioned("The Longonjo Project in Angola") == ()
        assert rf.project_key("first production at the Longonjo NdPr Project in 2027") == (
            "longonjo ndpr"
        )
        assert rf.projects_compatible("longonjo", "longonjo ndpr") is True
        assert rf.projects_compatible("longonjo", "saltend") is False
        assert rf.projects_compatible(None, "saltend") is None

    def test_the_claim_key_round_trips(self) -> None:
        key = rf.claim_key_for("Capex of US$268m for the Longonjo Project")
        assert key == "metric:capex@longonjo"
        assert rf.parse_claim_key(key) == (("metric:capex",), "longonjo")
        assert rf.parse_claim_key("something:else") is None


# ── 18. Gap reconciliation ─────────────────────────────────────────────────── #


class TestGapReconciliation:
    def test_a_gap_under_one_question_is_closed_by_a_finding_under_another(self) -> None:
        gap = _gap("No capital expenditure figure was acquired", question_key="growth_projects")
        finding = _finding("f-capex", "Capex of US$268m for Phase 1",
                           question_key="capex_and_capacity")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_CLOSED
        assert verdict.closed_by_finding_id == "f-capex"

    def test_a_negated_finding_never_closes(self) -> None:
        gap = _gap("No capex figure was acquired")
        finding = _finding("f1", "The company has not disclosed capex of any amount")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_a_withdrawn_finding_never_closes(self) -> None:
        gap = _gap("No capex figure was acquired")
        finding = _finding("f1", "Capex of US$268m", withdrawn=True)
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_a_segment_finding_does_not_close_a_group_gap(self) -> None:
        gap = _gap("Group capex was not acquired")
        finding = _finding("f1", "Capex of US$40m", scope_key="segment:rare earths")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_SCOPE_DIFFERS in verdict.reasons
        assert verdict.finding_ids == ["f1"]

    def test_an_older_period_only_partially_closes(self) -> None:
        gap = _gap("FY2025 capital expenditure was not acquired")
        finding = _finding("f1", "Capex of US$30m", period_key="FY2024")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_OLDER_PERIOD in verdict.reasons

    def test_the_requested_period_or_later_closes(self) -> None:
        gap = _gap("FY2025 capital expenditure was not acquired")
        finding = _finding("f1", "Capex of US$30m", period_key="FY2025")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_CLOSED

    def test_third_party_evidence_only_partially_closes(self) -> None:
        gap = _gap("No capex figure was acquired")
        finding = _finding("f1", "Capex of US$268m", source_kinds=("secondary_web",))
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_THIRD_PARTY_ONLY in verdict.reasons

    def test_another_projects_figure_does_not_address_it(self) -> None:
        gap = _gap("No capex for the Saltend Refinery was acquired")
        finding = _finding("f1", "Capex of US$268m for the Longonjo Project")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_every_field_must_be_stated_to_close(self) -> None:
        gap = _gap("Neither capex nor production capacity was acquired")
        finding = _finding("f1", "Capex of US$268m")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        both = _finding("f2", "Nameplate capacity of 12,000 tpa")
        (verdict,) = gr.reconcile([gap], [finding, both])
        assert verdict.status == ledger.RECONCILED_CLOSED

    def test_an_unknown_field_fails_closed(self) -> None:
        gap = _gap("The model returned an empty JSON object")
        finding = _finding("f1", "Capex of US$268m")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN
        assert verdict.reasons == [gr.REASON_FIELD_UNKNOWN]

    def test_a_field_inferred_from_the_question_never_fully_closes(self) -> None:
        gap = _gap("The model returned an empty JSON object", question_key="capex_q")
        finding = _finding("f1", "Capex of US$268m")
        (verdict,) = gr.reconcile(
            [gap], [finding], question_fields={"capex_q": ("metric:capex",)}
        )
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert verdict.reasons[0] == gr.REASON_FIELD_INFERRED

    def test_an_unreachable_source_is_superseded_by_a_ready_document(self) -> None:
        gap = _gap("The annual report could not be fetched",
                   gap_type=ledger.GAP_SOURCE_UNREACHABLE)
        doc = gr.DocumentFacts(kind=rf.DOC_ANNUAL, ref="NSM-1", published="2026-04-30")
        (verdict,) = gr.reconcile([gap], [], documents=[doc])
        assert verdict.status == ledger.RECONCILED_SUPERSEDED
        assert verdict.documents[0]["ref"] == "NSM-1"
        # Without the document it stays open.
        (verdict,) = gr.reconcile([gap], [])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_a_document_does_not_answer_a_field_it_may_not_state(self) -> None:
        gap = _gap("The annual report does not give the capex figure")
        doc = gr.DocumentFacts(kind=rf.DOC_ANNUAL)
        (verdict,) = gr.reconcile([gap], [], documents=[doc])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_a_validated_group_fact_supersedes_an_acquisition_gap(self) -> None:
        gap = _gap("Cash and cash equivalents were not acquired")
        fact = gr.FactFacts("fact-1", "cash_and_equivalents", "metric:cash", "FY2025", "group")
        (verdict,) = gr.reconcile([gap], [], facts=[fact])
        assert verdict.status == ledger.RECONCILED_SUPERSEDED
        assert verdict.fact["fact_id"] == "fact-1"
        segment = gr.FactFacts("fact-2", "cash_and_equivalents", "metric:cash", "FY2025",
                               "segment")
        (verdict,) = gr.reconcile([gap], [], facts=[segment])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_current_guidance_is_preferred_as_the_closer(self) -> None:
        gap = _gap("First production date was not acquired")
        old = _finding("old", "First production is expected in 2028", superseded_by="new")
        new = _finding("new", "First production is expected in 2027")
        (verdict,) = gr.reconcile([gap], [old, new])
        assert verdict.closed_by_finding_id == "new"


# ── 19. Temporal supersession ──────────────────────────────────────────────── #


class TestSupersession:
    def test_the_newer_guidance_is_current(self) -> None:
        old = _finding("old", "First production at the Foo Project is expected in 2028",
                       published_at=date(2025, 11, 3))
        new = _finding("new", "First production at the Foo Project is expected in 2027",
                       published_at=date(2026, 8, 12))
        supersessions, disagreements = gr.supersede([old, new])
        assert disagreements == []
        (s,) = supersessions
        assert (s.older_id, s.newer_id) == ("old", "new")
        assert s.to_dict()["superseded_on"] == "2026-08-12"

    def test_different_projects_are_both_current(self) -> None:
        a = _finding("a", "First production at the Foo Project is expected in 2028",
                     published_at=date(2025, 1, 1))
        b = _finding("b", "First production at the Bar Project is expected in 2027",
                     published_at=date(2026, 1, 1))
        assert gr.supersede([a, b]) == ([], [])

    def test_different_scopes_are_both_current(self) -> None:
        a = _finding("a", "Capex of US$100m", scope_key="segment:a",
                     published_at=date(2025, 1, 1))
        b = _finding("b", "Capex of US$200m", scope_key="segment:b",
                     published_at=date(2026, 1, 1))
        assert gr.supersede([a, b]) == ([], [])

    def test_a_missing_date_is_a_disagreement_not_a_supersession(self) -> None:
        a = _finding("a", "First production is expected in 2028", published_at=None)
        b = _finding("b", "First production is expected in 2027", published_at=None)
        supersessions, disagreements = gr.supersede([a, b])
        assert supersessions == []
        assert {(d.finding_a_id, d.finding_b_id) for d in disagreements} == {("a", "b")}

    def test_an_equal_date_is_a_disagreement(self) -> None:
        same = date(2026, 1, 1)
        a = _finding("a", "First production is expected in 2028", published_at=same)
        b = _finding("b", "First production is expected in 2027", published_at=same)
        supersessions, disagreements = gr.supersede([a, b])
        assert supersessions == [] and len(disagreements) == 1

    def test_real_financial_periods_are_never_superseded(self) -> None:
        fy24 = _finding("a", "Capex of US$30m", period_key="FY2024",
                        published_at=date(2025, 3, 1))
        fy25 = _finding("b", "Capex of US$45m", period_key="FY2025",
                        published_at=date(2026, 3, 1))
        assert gr.supersede([fy24, fy25]) == ([], [])
        # Two values for ONE reporting period: a conflict, never ordered by date.
        restated = _finding("c", "Capex of US$47m", period_key="FY2025",
                            published_at=date(2026, 6, 1))
        supersessions, disagreements = gr.supersede([fy25, restated])
        assert supersessions == [] and len(disagreements) == 1

    def test_the_same_value_restated_is_not_a_change(self) -> None:
        a = _finding("a", "First production is expected in 2027", published_at=date(2025, 1, 1))
        b = _finding("b", "First production is expected in 2027", published_at=date(2026, 1, 1))
        assert gr.supersede([a, b]) == ([], [])

    def test_a_clause_naming_two_fields_is_never_ordered(self) -> None:
        a = _finding("a", "Capex of US$100m and capacity of 10,000 tpa",
                     published_at=date(2025, 1, 1))
        b = _finding("b", "Capex of US$200m", published_at=date(2026, 1, 1))
        assert gr.supersede([a, b]) == ([], [])


class TestPublicationDatesFlow:
    def test_evidence_carries_its_publication_date(self) -> None:
        from app.services.agents.investigator import _evidence_of, _inherited_published_at

        chunk = _evidence_of(
            "search_company_corpus",
            {"evidence_id": "ev:1", "published_at": "2026-08-12T00:00:00+00:00"}, False,
        )
        filing = _evidence_of(
            "get_recent_filings", {"id": "f:2", "filing_date": "2025-11-03"}, False
        )
        undated = _evidence_of("search_company_corpus", {"evidence_id": "ev:3"}, False)
        assert chunk.published_at == date(2026, 8, 12)
        assert filing.published_at == date(2025, 11, 3)
        assert undated.published_at is None
        assert _inherited_published_at(["ev:1", "f:2"], [chunk, filing]) == date(2026, 8, 12)
        # One undated citation makes the finding undated: no ordering on a guess.
        assert _inherited_published_at(["ev:1", "ev:3"], [chunk, undated]) is None

    async def test_the_date_and_claim_key_reach_the_ledger_row(self, session) -> None:
        from app.models.ledger import ResearchFinding
        from app.services.director.loop import FindingDraft, TaskOutcome, run_investigation
        from app.services.director.planner import PlannedQuestion, PlannedTask, ResearchPlan
        from app.services.research_mode import ModeLimits, ResearchMode

        limits = ModeLimits(
            max_rounds=1, max_tasks=4, max_tool_calls=50, max_web_searches=1,
            max_provider_research_runs=1, max_documents=5, max_model_calls=5,
            max_model_tokens=10_000, max_wall_seconds=60.0,
        )
        plan = ResearchPlan(subject="X", mode=ResearchMode.STANDARD, limits=limits)
        plan.questions = [PlannedQuestion(key="q", text="q", origin=ledger.ORIGIN_DIRECTOR)]
        plan.tasks = [PlannedTask(role_id="financial_analyst", question_keys=["q"])]

        class _Investigator:
            async def investigate(self, *, role_id, questions, round_index,  # noqa: ANN001, ANN202
                                  remaining_tool_calls):
                return TaskOutcome(
                    findings=[FindingDraft(
                        statement="Capex of US$268m for the Foo Project",
                        evidence_ids=("ev:1",), question_key="q",
                        source_published_at=date(2026, 8, 12),
                    )],
                    answered_question_keys=("q",), tool_calls=1,
                )

        run = await ledger.open_run(session, mode="standard")
        await run_investigation(session, run, plan, investigator=_Investigator(),
                                limits=limits)
        from sqlalchemy import select

        row = (await session.execute(select(ResearchFinding))).scalar_one()
        assert row.source_published_at == date(2026, 8, 12)
        assert row.claim_key == "metric:capex@foo"


# ── The ledger path ─────────────────────────────────────────────────────────── #


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", future=True, poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _seed(session):  # noqa: ANN001, ANN202
    run = await ledger.open_run(session, mode="standard")
    capex = await ledger.record_finding(
        session, run, statement="Capex of US$268m for Phase 1", evidence_ids=["ev:1"],
        question_key="capex_and_capacity", source_kinds=ISSUER,
        claim_key=rf.claim_key_for("Capex of US$268m for Phase 1"),
    )
    old = await ledger.record_finding(
        session, run, statement="First production is expected in 2028",
        evidence_ids=["ev:2"], question_key="growth_projects", source_kinds=ISSUER,
        source_published_at=date(2025, 11, 3),
    )
    new = await ledger.record_finding(
        session, run, statement="First production is expected in 2027",
        evidence_ids=["ev:3"], question_key="growth_projects", source_kinds=ISSUER,
        source_published_at=date(2026, 8, 12),
    )
    closed_gap = await ledger.record_gap(
        session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
        description="No capital expenditure figure was acquired", question_key="growth_projects",
    )
    open_gap = await ledger.record_gap(
        session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
        description="No offtake agreement was found", question_key="growth_projects",
    )
    superseded_gap = await ledger.record_gap(
        session, run, gap_type=ledger.GAP_SOURCE_UNREACHABLE,
        description="The annual report could not be fetched",
    )
    return SimpleNamespace(run=run, capex=capex, old=old, new=new, closed_gap=closed_gap,
                           open_gap=open_gap, superseded_gap=superseded_gap)


async def _exercise_ledger_path(session) -> None:  # noqa: ANN001
    seeded = await _seed(session)
    result = await gr.reconcile_run(
        # A real company id, so the validated-fact query runs (and finds none).
        session, seeded.run, company_id=uuid.uuid4(),
        core_disclosures={"documents": [
            {"state": "ready", "document_kind": "annual_report", "document_ref": "N1"}
        ]},
    )
    await session.refresh(seeded.closed_gap)
    await session.refresh(seeded.superseded_gap)
    await session.refresh(seeded.old)
    # close_gap has a caller, and it names the finding.
    assert seeded.closed_gap.status == ledger.GAP_CLOSED
    assert seeded.closed_gap.closed_by_finding_id == seeded.capex.id
    assert seeded.closed_gap.reconciliation_status == ledger.RECONCILED_CLOSED
    assert seeded.superseded_gap.reconciliation_status == ledger.RECONCILED_SUPERSEDED
    assert seeded.old.superseded_by_finding_id == seeded.new.id
    assert result["counts"] == {"closed": 1, "still_open": 1, "superseded": 1}

    council = await council_inputs.assemble(session, seeded.run)
    shown = {g.description for g in council.gaps}
    assert shown == {"No offtake agreement was found"}
    by_id = {str(f.finding_id): f for f in council.findings}
    assert by_id[str(seeded.old.id)].superseded_by_finding_id == str(seeded.new.id)
    assert by_id[str(seeded.old.id)].source_published_at == "2025-11-03"


class TestLedgerPath:
    async def test_reconciliation_persists_on_sqlite(self, session) -> None:
        await _exercise_ledger_path(session)

    async def test_reconcile_gap_refuses_closed_without_a_finding(self, session) -> None:
        run = await ledger.open_run(session, mode="standard")
        gap = await ledger.record_gap(session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                                      description="x")
        with pytest.raises(ValueError):
            await ledger.reconcile_gap(session, gap, status="closed", detail={})
        with pytest.raises(ValueError):
            await ledger.reconcile_gap(session, gap, status="vanished", detail={})


# ── 22. "0 verified" ────────────────────────────────────────────────────────── #


def _ref(kinds: tuple[str, ...], status: str = "unverified") -> council_inputs.FindingRef:
    return council_inputs.FindingRef(
        finding_id=uuid.uuid4(), statement="s", mechanism=None, direction=None,
        confidence=None, evidence_ids=("ev:1",), calculation_ids=(), period_key=None,
        scope_key=None, originating_role=None, verification_status=status,
        source_kinds=kinds,
    )


class TestPrimarySourceCount:
    def test_it_counts_findings_citing_the_issuers_own_documents(self) -> None:
        council = council_inputs.CouncilInput(
            research_run_id=uuid.uuid4(), convened=True,
            findings=(_ref(("issuer_filing",)), _ref(("issuer_ir", "secondary_web")),
                      _ref(("secondary_web",)), _ref(())),
        )
        payload = council.to_dict()
        assert payload["primary_source_finding_count"] == 2
        # The old key stays, unchanged, for reports already written.
        assert payload["verified_finding_count"] == 0

    def test_the_chair_verdict_logic_is_unchanged(self) -> None:
        from app.services.agents.chair import deterministic_verdict

        council = council_inputs.CouncilInput(
            research_run_id=uuid.uuid4(), convened=True, findings=(_ref(("issuer_filing",)),),
        )
        assert deterministic_verdict(council).label == "requires_more_evidence"


# ── The Chair and the professional report ──────────────────────────────────── #


class TestChairAndReport:
    def test_the_chair_prompt_names_the_finding_behind_a_partial_gap(self) -> None:
        from app.services.agents.chair import LLMChair

        gap = council_inputs.GapRef(
            gap_id=uuid.uuid4(), gap_type="evidence_unavailable",
            description="Group capex was not acquired", why_it_matters=None, status="open",
            closable=True, reconciliation_status="partially_closed",
            addressed_by_finding_ids=("f-seg",), reconciliation_reasons=("scope_differs",),
        )
        council = council_inputs.CouncilInput(
            research_run_id=uuid.uuid4(), convened=True, findings=(_ref(ISSUER),), gaps=(gap,),
        )
        prompt = LLMChair(client=None)._prompt(council)
        assert "PARTIALLY ADDRESSED by finding f-seg; scope_differs" in prompt

    def _inputs(self, gaps) -> pr.ReportInputs:  # noqa: ANN001
        return pr.ReportInputs(
            subject={"ticker": "X"},
            questions=[pr.QuestionView(key="q", text="q", domain="financial_capacity")],
            findings=[
                pr.FindingView(finding_id="old", statement="First production in 2028",
                               domain="financial_capacity", question_key="q",
                               source_published_at="2025-11-03", superseded_by="new"),
                pr.FindingView(finding_id="new", statement="First production in 2027",
                               domain="financial_capacity", question_key="q",
                               source_published_at="2026-08-12"),
            ],
            gaps=gaps,
        )

    def test_closed_and_superseded_gaps_are_not_listed(self) -> None:
        report = pr.assemble(self._inputs([
            pr.GapView("No capex acquired", "q", None, "platform_evidence_gap", "closed"),
            pr.GapView("Annual report unreachable", "q", None, "platform_evidence_gap",
                       "superseded"),
            pr.GapView("Group capex not acquired", "q", None, "platform_evidence_gap",
                       "partially_closed", ("new",), ("scope_differs",)),
            pr.GapView("No offtake found", "q", None, "platform_evidence_gap", "still_open"),
        ]))
        evidence = next(s for s in report["sections"] if s["key"] == "evidence_quality_and_gaps")
        listed = {g["description"]: g for g in evidence["platform_evidence_gaps"]}
        assert set(listed) == {"Group capex not acquired", "No offtake found"}
        assert listed["Group capex not acquired"]["partially_addressed_by"] == ["F2"]
        assert evidence["platform_evidence_gaps_reconciled"] == 2

    def test_prior_guidance_is_kept_and_names_the_current(self) -> None:
        report = pr.assemble(self._inputs([]))
        section = next(s for s in report["sections"] if s["key"] == "financial_capacity")
        by_label = {f["label"]: f for f in section["findings"]}
        assert by_label["F1"]["guidance_status"] == "prior"
        assert by_label["F1"]["superseded_by_label"] == "F2"
        assert by_label["F1"]["superseded_on"] == "2026-08-12"
        assert by_label["F2"]["guidance_status"] == "current"
        assert by_label["F2"]["supersedes"] == [
            {"label": "F1", "source_published_at": "2025-11-03"}
        ]


# ── V2 items ────────────────────────────────────────────────────────────────── #


class TestV2Labels:
    closers = [
        gr.FindingFacts("f-capex", "", ("metric:capex",), source_kinds=ISSUER),
    ]

    def test_a_missing_field_a_finding_states_is_closed(self) -> None:
        out = gr.label_v2_items(
            missing_items=[{"field": "capital_expenditure", "source": "financial_data_agent"},
                           {"field": "identity.isin", "source": "company_snapshot"}],
            concerns=[], closers=self.closers,
        )
        (item,) = out["missing_information"]  # the ISIN names no field: left as V2 wrote it
        assert item["field"] == "capital_expenditure"
        assert item["status"] == "closed" and item["finding_ids"] == ["f-capex"]

    def test_only_gap_shaped_concerns_are_labelled(self) -> None:
        out = gr.label_v2_items(
            missing_items=[],
            concerns=[
                {"text": "Capex is not disclosed for the expansion", "agent": "red_team"},
                {"text": "Capex overruns could erode returns", "agent": "risk_governance"},
            ],
            closers=self.closers,
        )
        (concern,) = out["council_concerns"]
        assert concern["status"] == "closed"
        assert concern["key"] == "capex is not disclosed for the expansion"

    def test_attach_to_report_labels_the_v2_report(self) -> None:
        from app.services.pipeline.v3_pipeline import V3ResearchOutcome, attach_to_report

        content = {"missing_information": {"missing_items": {"value": [
            {"field": "capital_expenditure", "source": "financial_data_agent"}]}}}
        report = SimpleNamespace(
            content_markdown="# R\n```json\n" + json.dumps(content) + "\n```\n",
            source_summary_json={"llm_council": {"agents": [{
                "agent_name": "red_team",
                "risks_or_gaps": [{"item": "Capital expenditure is not disclosed"}],
            }]}},
        )
        outcome = V3ResearchOutcome(gap_reconciliation={
            "closing_findings": [c.to_dict() for c in self.closers],
        })
        attach_to_report(report, outcome)
        v2 = report.source_summary_json["v3_research"]["gap_reconciliation"]["v2"]
        assert v2["missing_information"][0]["status"] == "closed"
        assert v2["council_concerns"][0]["status"] == "closed"
        assert report.source_summary_json["llm_council"]  # V2 state untouched


# ── Migration 043 ───────────────────────────────────────────────────────────── #


def _migration():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("_m043", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigration043:
    def test_it_is_additive_and_nullable(self) -> None:
        from app.services import schema_readiness

        m = _migration()
        assert m.revision == "043"
        declared = {(t, c) for t, cols in schema_readiness.MIGRATION_043_COLUMNS.items()
                    for c in cols}
        assert declared == {(t, c) for t, c, _type in m._COLUMNS}
        source = MIGRATION.read_text()
        upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
        assert "drop_" not in upgrade and "alter_column" not in upgrade

    async def test_the_pipeline_degrades_without_043(self, monkeypatch) -> None:
        from app.services import schema_readiness
        from app.services.pipeline import v3_pipeline

        async def ready(_session, **_kw):  # noqa: ANN001, ANN202
            return schema_readiness.Readiness(True)

        async def not_ready(_session, **_kw):  # noqa: ANN001, ANN202
            return schema_readiness.Readiness(False, ("research_gaps.reconciliation_status",))

        monkeypatch.setattr(schema_readiness, "migration_041_readiness", ready)
        monkeypatch.setattr(schema_readiness, "migration_043_readiness", not_ready)
        company = SimpleNamespace(id=uuid.uuid4(), ticker="ANY", exchange="US", name="Any")
        outcome = await v3_pipeline.run_v3_research(object(), company, cfg=SimpleNamespace())
        assert outcome.error == "schema_not_ready"
        assert any("migration 043" in reason for reason in outcome.degraded)


@requires_postgres
class TestOnPostgres:
    async def _session(self):  # noqa: ANN202
        engine = create_async_engine(POSTGRES_URL, future=True)
        return engine, async_sessionmaker(engine, expire_on_commit=False)

    async def test_the_database_is_at_043(self) -> None:
        from app.services import schema_readiness

        engine, maker = await self._session()
        try:
            async with maker() as session:
                schema_readiness.reset_cache()
                assert (await schema_readiness.migration_043_readiness(session)).ready
        finally:
            await engine.dispose()

    async def test_the_ledger_path_on_postgres(self) -> None:
        engine, maker = await self._session()
        try:
            async with maker() as session:
                await _exercise_ledger_path(session)
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_the_check_refuses_a_reconciled_closed_gap_with_no_finding(self) -> None:
        engine, maker = await self._session()
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                gap = await ledger.record_gap(
                    session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE, description="x"
                )
                gap.reconciliation_status = "closed"
                with pytest.raises(IntegrityError, match="reconciled_closed_names_a_finding"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_the_check_refuses_a_finding_superseding_itself(self) -> None:
        engine, maker = await self._session()
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                finding = await ledger.record_finding(
                    session, run, statement="s", evidence_ids=["ev:1"]
                )
                finding.superseded_by_finding_id = finding.id
                with pytest.raises(IntegrityError, match="not_superseded_by_itself"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()
