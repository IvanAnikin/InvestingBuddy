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
            ("A binding offtake agreement was signed with Acme Corp", "commercial:offtake"),
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
        finding = _finding("f-capex", "Capex of US$268m", question_key="capex_and_capacity")
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

    def test_only_the_requested_period_closes(self) -> None:
        """Review round 1 (H5): the first version closed "FY2023 revenue" with an FY2025
        figure. A gap naming a period is answered by THAT period; any other is partial."""
        gap = _gap("FY2025 capital expenditure was not acquired")
        (verdict,) = gr.reconcile([gap], [_finding("f1", "Capex of US$30m", period_key="FY2025")])
        assert verdict.status == ledger.RECONCILED_CLOSED
        older = _gap("FY2023 revenue was not acquired")
        (verdict,) = gr.reconcile([older], [_finding("f2", "Revenue of US$50m",
                                                      period_key="FY2025")])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_OTHER_PERIOD in verdict.reasons
        quarter = _gap("2025-Q3 revenue was not acquired")
        (verdict,) = gr.reconcile([quarter], [_finding("f3", "Revenue of US$50m",
                                                        period_key="2025-H1")])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED

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

    def test_a_validated_group_fact_only_partially_closes(self) -> None:
        """A fact is shown beside the gap, never instead of it (review round 1)."""
        gap = _gap("Cash and cash equivalents were not acquired")
        fact = gr.FactFacts("fact-1", "cash_and_equivalents", "metric:cash", "FY2025", "group")
        (verdict,) = gr.reconcile([gap], [], facts=[fact])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
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
        fy24 = _finding("a", "Post-tax NPV of US$30m", period_key="FY2024",
                        published_at=date(2025, 3, 1))
        fy25 = _finding("b", "Post-tax NPV of US$45m", period_key="FY2025",
                        published_at=date(2026, 3, 1))
        assert gr.supersede([fy24, fy25]) == ([], [])
        # Two values for ONE reporting period: a conflict, never ordered by date.
        restated = _finding("c", "Post-tax NPV of US$47m", period_key="FY2025",
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
        near = _evidence_of(
            "get_recent_filings", {"id": "f:4", "filing_date": "2026-06-30"}, False
        )
        assert _inherited_published_at(["ev:1", "f:4"], [chunk, near]) == date(2026, 8, 12)
        # Review round 1 (H8): citations nine months apart do not date a finding — a
        # restated old estimate citing a new report would otherwise look current.
        assert _inherited_published_at(["ev:1", "f:2"], [chunk, filing]) is None
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
        session, run, statement="Capex of US$268m", evidence_ids=["ev:1"],
        question_key="capex_and_capacity", source_kinds=ISSUER,
        claim_key=rf.claim_key_for("Capex of US$268m"),
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
        gr.FindingFacts("f-cap", "", ("metric:production_capacity",), source_kinds=ISSUER),
    ]

    def test_a_missing_field_a_finding_states_is_closed(self) -> None:
        out = gr.label_v2_items(
            missing_items=[{"field": "profile.production_capacity", "source": "x"},
                           {"field": "identity.isin", "source": "company_snapshot"}],
            concerns=[], closers=self.closers,
        )
        (item,) = out["missing_information"]  # the ISIN names no field: left as V2 wrote it
        assert item["field"] == "profile.production_capacity"
        assert item["status"] == "closed" and item["finding_ids"] == ["f-cap"]

    def test_an_unperiodised_period_metric_is_never_hidden(self) -> None:
        # Review round 2 (4): "capital_expenditure" / "revenue" with no period.
        out = gr.label_v2_items(
            missing_items=[{"field": "capital_expenditure", "source": "x"}],
            concerns=[], closers=self.closers,
        )
        (item,) = out["missing_information"]
        assert item["status"] == "partially_closed"
        assert gr.REASON_PERIOD_UNSPECIFIED in item["reasons"]

    def test_only_gap_shaped_concerns_are_labelled(self) -> None:
        out = gr.label_v2_items(
            missing_items=[],
            concerns=[
                {"text": "Capex is not disclosed", "agent": "red_team"},
                {"text": "Capex overruns could erode returns", "agent": "risk_governance"},
            ],
            closers=self.closers,
        )
        (concern,) = out["council_concerns"]
        assert concern["status"] == "closed"
        assert concern["key"] == "capex is not disclosed"

    def test_attach_to_report_labels_the_v2_report(self) -> None:
        from app.services.pipeline.v3_pipeline import V3ResearchOutcome, attach_to_report

        content = {"missing_information": {"missing_items": {"value": [
            {"field": "production_capacity", "source": "financial_data_agent"}]}}}
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


# ── Review round 1: every blocking / high / medium case, as a regression ──────── #


D = date


def _pf(fid: str, statement: str, pub: date | None = None, **kw) -> gr.FindingFacts:  # noqa: ANN003
    fields, project = gr.finding_fields(statement, rf.claim_key_for(statement))
    return gr.FindingFacts(
        finding_id=fid, statement=statement, fields=fields, project=project,
        source_kinds=kw.pop("source_kinds", ISSUER), published_at=pub,
        stated_period=rf.statement_period(statement), **kw,
    )


def _status(description: str, findings=(), gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE, **kw):  # noqa: ANN001, ANN003, ANN202
    (verdict,) = gr.reconcile([_gap(description, gap_type=gap_type)], list(findings), **kw)
    return verdict


class TestReviewBlocking:
    # B1 — only an ABSENCE gap closes; a segment gap never takes a group figure.
    def test_a_conflict_gap_is_not_closed_by_one_more_figure(self) -> None:
        verdict = _status("Capex figures conflict between the DFS and the quarterly",
                          [_pf("f", "The DFS estimates capex of US$1.2bn")],
                          ledger.GAP_CONFLICTING_SOURCES)
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_GAP_TYPE_NOT_ABSENCE in verdict.reasons

    def test_a_segment_gap_is_not_answered_by_a_group_figure(self) -> None:
        verdict = _status("Mining segment revenue split unknown",
                          [_pf("f", "Revenue of US$50m", scope_key="group")],
                          ledger.GAP_SCOPE_UNKNOWN)
        assert verdict.status == ledger.RECONCILED_STILL_OPEN
        verdict = _status("Capex for the Rare Earths segment not acquired",
                          [_pf("f", "The DFS estimates capex of US$302m")])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    # B2 — a held document supersedes only a failed FETCH that names no open field.
    @pytest.mark.parametrize(
        ("description", "gap_type"),
        [
            ("The 2024 annual report does not disclose segment EBITDA",
             ledger.GAP_EVIDENCE_UNAVAILABLE),
            ("Annual report could not be retrieved, so capex could not be verified",
             ledger.GAP_SOURCE_UNREACHABLE),
            ("The annual report could not be fetched", ledger.GAP_EVIDENCE_UNAVAILABLE),
            ("The annual report does not state a cash runway", ledger.GAP_SOURCE_UNREACHABLE),
        ],
    )
    def test_a_held_document_does_not_hide_an_open_gap(self, description, gap_type) -> None:  # noqa: ANN001
        docs = [gr.DocumentFacts(kind=rf.DOC_ANNUAL, ref="AR")]
        verdict = _status(description, [], gap_type, documents=docs)
        assert verdict.status != ledger.RECONCILED_SUPERSEDED

    def test_a_question_field_does_not_open_the_document_path(self) -> None:
        (verdict,) = gr.reconcile(
            [gr.GapFacts("g", ledger.GAP_EVIDENCE_UNAVAILABLE,
                         "The annual report does not disclose segment margins", "q1")],
            [], documents=[gr.DocumentFacts(kind=rf.DOC_ANNUAL)],
            question_fields={"q1": ("metric:capex",)},
        )
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    # B3 — a business risk is never labelled; a concern is never dropped by the page.
    @pytest.mark.parametrize(
        "text",
        [
            "Capex overruns could strain funding given the lack of committed debt financing",
            "Capital cost inflation remains a risk; final cost unknown until EPC contracts are let",
            "Offtake terms are not disclosed, so pricing risk is unclear",
        ],
    )
    def test_a_risk_concern_is_never_labelled(self, text: str) -> None:
        closers = [gr.FindingFacts("f", "", ("metric:capex", "commercial:offtake"),
                                   source_kinds=ISSUER)]
        out = gr.label_v2_items(missing_items=[], concerns=[{"text": text}], closers=closers)
        assert out["council_concerns"] == []

    def test_the_gap_cue_and_field_must_share_a_clause(self) -> None:
        closers = [gr.FindingFacts("f", "", ("metric:capex",), source_kinds=ISSUER)]
        out = gr.label_v2_items(
            missing_items=[],
            concerns=[{"text": "Capex was US$10m; the customer list is not disclosed"}],
            closers=closers,
        )
        assert out["council_concerns"] == []

    # B4 — history is never "prior guidance".
    @pytest.mark.parametrize(
        ("older", "newer"),
        [
            ("Revenue of US$5.2m", "Revenue of US$1.1m for the quarter"),
            ("Revenue for FY2024 was £3.1m", "Revenue for FY2025 was £4.0m"),
            ("Cash and cash equivalents of £6.1m at 31 December 2025",
             "Cash and cash equivalents of £4.2m at 30 June 2026"),
            ("Initial capital of US$1.2bn", "Sustaining capital of US$50m a year"),
            ("Capex for FY2024 was US$30m", "Capex for FY2025 was US$45m"),
        ],
    )
    def test_period_bound_metrics_are_never_superseded(self, older: str, newer: str) -> None:
        a = _pf("a", older, D(2025, 3, 1))
        b = _pf("b", newer, D(2026, 7, 1))
        supersessions, _ = gr.supersede([a, b])
        assert supersessions == []


class TestReviewHigh:
    # H1 — offtake needs a signed commitment with a counterparty or a volume.
    @pytest.mark.parametrize(
        "text",
        ["Securing offtake remains a key risk", "Offtake discussions continue; nothing signed",
         "The company is negotiating offtake with several parties"],
    )
    def test_offtake_talk_is_not_an_offtake(self, text: str) -> None:
        assert "commercial:offtake" not in rf.fields_stated(text)
        assert _status("Offtake status unknown", [_pf("f", text)]).status == (
            ledger.RECONCILED_STILL_OPEN)

    # H2 — a figure must be the field's VALUE.
    @pytest.mark.parametrize(
        "text",
        [
            "Capex estimate updated on 12 March 2026",
            "Capex is described in section 4",
            "Capex for the 2 phase approach is being studied",
            "Capex increased by 10%",
            "The IRR is sensitive to a 10% change in basket price",
            "The NPV10 is sensitive to the basket price",
            "The company has a long runway of 3 growth options",
            "Drilling in 2025 intersected 12m at 3% TREO outside the mineral resource",
            "The pilot plant was commissioned in 2022",
            "First production was delayed from 2025",
        ],
    )
    def test_a_number_that_is_not_the_value_states_nothing(self, text: str) -> None:
        assert rf.fields_stated(text) == ()

    # H3 — capex sub-types.
    def test_period_capex_does_not_answer_a_project_estimate(self) -> None:
        verdict = _status("The initial capital estimate for the project was not acquired",
                          [_pf("f", "Capex for FY2024 was US$30m")])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_SUBTYPE_DIFFERS in verdict.reasons

    def test_working_capital_is_not_capex(self) -> None:
        assert rf.fields_mentioned("Working capital requirements not quantified") == ()
        assert rf.fields_stated("Working capital of US$5m") == ()

    # H4 — withdrawn / stale.
    @pytest.mark.parametrize(
        "text",
        [
            "The company withdrew its capex estimate of US$302m pending a review",
            "The capex estimate of US$302m is under review",
            "First production deferred indefinitely from 2026",
            "Commissioning is suspended; it had been expected in 2026",
        ],
    )
    def test_withdrawn_or_deferred_guidance_states_nothing(self, text: str) -> None:
        assert rf.fields_stated(text) == ()

    def test_a_gap_asking_for_the_current_value_needs_a_newer_source(self) -> None:
        old = _pf("f", "The 2023 DFS estimated capex of US$302m")
        verdict = _status("No updated capex estimate since the 2023 DFS", [old])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_RECENCY_UNCONFIRMED in verdict.reasons
        new = _pf("n", "The DFS estimates capex of US$320m", D(2026, 5, 1))
        assert _status("No updated capex estimate since the 2023 DFS", [new]).status == (
            ledger.RECONCILED_CLOSED)

    # H6 — one side naming a project; the gap's own project and stage.
    def test_an_unnamed_gap_is_only_partly_answered_by_a_project_figure(self) -> None:
        verdict = _status("Capex was not acquired",
                          [_pf("f", "Capex of US$1.2bn for the Foo Project")])
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_PROJECT_UNNAMED in verdict.reasons

    def test_an_expansion_is_not_its_parent_mine(self) -> None:
        verdict = _status("Capex for the Foo Mine expansion not acquired",
                          [_pf("f", "Capex for the Foo Mine is US$20m")])
        assert verdict.status != ledger.RECONCILED_CLOSED

    def test_the_project_named_without_an_asset_noun_still_matches(self) -> None:
        # M5: "The Foo DFS estimates …" names Foo without "Project".
        verdict = _status("Capex for the Foo Project was not acquired",
                          [_pf("f", "The Foo DFS estimates capex of US$302m")])
        assert verdict.status == ledger.RECONCILED_CLOSED

    # H7 — a fact never answers a project or another period.
    def test_a_group_fact_never_answers_a_project_or_another_period(self) -> None:
        fact = gr.FactFacts("x", "capital_expenditure", "metric:capex", "FY2025", "group")
        assert _status("Capex for the Foo Project was not acquired", [],
                       facts=[fact]).status == ledger.RECONCILED_STILL_OPEN
        assert _status("FY2023 capital expenditure was not acquired", [],
                       facts=[fact]).status == ledger.RECONCILED_STILL_OPEN

    # H9 — a stage is its own project.
    def test_stages_and_phases_are_never_superseded_by_each_other(self) -> None:
        pairs = [
            ("First production from the Foo Stage 1 Project is expected in 2026",
             "First production from the Foo Stage 2 Project is expected in 2030"),
            ("Phase 1 nameplate capacity of 5,000 tpa", "Phase 2 nameplate capacity of 12,500 tpa"),
            ("The Foo Project has a nameplate capacity of 12,500 tpa",
             "The Foo West Project has a nameplate capacity of 5,000 tpa"),
        ]
        for older, newer in pairs:
            supersessions, _ = gr.supersede([_pf("a", older, D(2025, 11, 1)),
                                             _pf("b", newer, D(2026, 8, 1))])
            assert supersessions == [], (older, newer)

    # H10 — only the TARGET year is the value.
    def test_an_incidental_year_is_not_a_change_of_guidance(self) -> None:
        a = _pf("a", "First production is targeted for 2028", D(2025, 11, 1))
        for later in ("Following the 2026 DFS, first production is targeted for 2028",
                      "After drilling in 2026, first production is targeted for 2028"):
            assert gr.supersede([a, _pf("b", later, D(2026, 8, 1))]) == ([], []), later

    # H11 — third parties never supersede the issuer.
    def test_a_broker_never_supersedes_issuer_guidance(self) -> None:
        issuer = _pf("i", "First production is expected in 2027", D(2026, 8, 1))
        broker = _pf("b", "A broker note expects first production in 2029", D(2026, 9, 1),
                     source_kinds=("quality_media",))
        supersessions, disagreements = gr.supersede([issuer, broker])
        assert supersessions == []
        assert [d.reason for d in disagreements] == ["non_issuer_source"]

    # H12 — scale and currency.
    def test_a_rescaled_amount_is_the_same_and_a_currency_is_not_comparable(self) -> None:
        a = _pf("a", "Capex estimate is US$302m", D(2025, 11, 1))
        same = _pf("b", "Capex estimate is US$0.3bn", D(2026, 8, 1))
        assert gr.supersede([a, same]) == ([], [])
        other = _pf("c", "Capex estimate is A$450m", D(2026, 8, 1))
        supersessions, disagreements = gr.supersede([a, other])
        assert supersessions == []
        assert [d.reason for d in disagreements] == ["values_not_comparable"]

    def test_a_clause_quoting_an_older_study_never_supersedes(self) -> None:
        # H8: the newest-dated finding restating the 2022 PFS is not current guidance.
        quoted = _pf("q", "The 2022 PFS estimated capex of US$250m", D(2026, 8, 1))
        dfs = _pf("d", "The DFS estimates capex of US$302m", D(2025, 11, 1))
        assert gr.supersede([quoted, dfs])[0] == []

    def test_a_genuine_change_of_guidance_still_supersedes(self) -> None:
        old = _pf("o", "The DFS estimates capex of US$302m", D(2025, 11, 1))
        new = _pf("n", "The updated capex estimate is US$350m", D(2026, 8, 1))
        (s,), _ = gr.supersede([old, new])
        assert (s.older_id, s.newer_id, s.field_key) == ("o", "n", "metric:capex_project")


class TestReviewMedium:
    def test_disagreements_are_capped(self) -> None:
        # M2
        many = [_pf(f"f{i}", f"First production is expected in {2027 + i}") for i in range(8)]
        _, disagreements = gr.supersede(many)
        assert len(disagreements) <= gr.MAX_DISAGREEMENTS_PER_GROUP

    def test_no_change_is_not_a_negation(self) -> None:
        # M5
        assert "metric:capex" in rf.fields_stated("Capex of US$302m with no change from the PFS")

    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("The plant will produce 12,500 tpa of MREC", "metric:production_capacity"),
            ("Design throughput of 1.2Mtpa", "metric:production_capacity"),
            ("The company is funded into 2027", "metric:cash_runway"),
            ("Mineral resource of 25.9Mt at 2.4% TREO", "metric:mineral_resource"),
            ("Post-tax IRR of 24%", "metric:irr"),
        ],
    )
    def test_common_phrasings_state_their_field(self, text: str, field: str) -> None:
        assert field in rf.fields_stated(text)

    def test_half_years_and_quarters_rank_correctly(self) -> None:
        assert rf.period_rank("2025-H1") == (2025, 5)
        assert rf.period_rank("2025-Q3") == (2025, 3)
        assert rf.statement_period("revenue for H1 2026") == "2026-H1"


class TestReviewCounts:
    async def test_gaps_open_counts_what_is_shown(self, session) -> None:  # noqa: ANN001
        # M3: a superseded gap is neither listed nor counted as open.
        run = await ledger.open_run(session, mode="standard")
        gap = await ledger.record_gap(session, run, gap_type=ledger.GAP_SOURCE_UNREACHABLE,
                                      description="The annual report could not be fetched")
        await ledger.record_gap(session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                                description="No offtake agreement was found")
        await ledger.reconcile_gap(session, gap, status="superseded", detail={})
        assert (await ledger.summarise(session, run)).gaps_open == 1


class TestReviewLow:
    async def test_counts_follow_a_downgraded_verdict(self, session, monkeypatch) -> None:  # noqa: ANN001
        # L1: a closed verdict with no finding row is downgraded, and counted as such.
        run = await ledger.open_run(session, mode="standard")
        await ledger.record_gap(session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                                description="No capex figure was acquired")

        def fake(gaps, findings, **_kw):  # noqa: ANN001, ANN202
            return [gr.GapVerdict(g.gap_id, ledger.RECONCILED_CLOSED,
                                  closed_by_finding_id=str(uuid.uuid4())) for g in gaps]

        monkeypatch.setattr(gr, "reconcile", fake)
        result = await gr.reconcile_run(session, run)
        assert result["counts"] == {"still_open": 1}

    def test_the_producer_keys_are_what_the_web_reads(self) -> None:
        # L4: the V3-payload-never-read-by-the-web defect, guarded from the backend side.
        web = Path(__file__).resolve().parents[2] / "web"
        reader = (web / "src/components/research/v3Research.ts").read_text()
        for key in ("partially_addressed_by", "reconciliation_status", "guidance_status",
                    "superseded_by_label", "superseded_on", "supersedes",
                    "superseded_fields", "source_published_at", "gap_reconciliation",
                    "council_concerns", "missing_information", "finding_ids",
                    "primary_source_finding_count", "useful_findings",
                    "cost_per_useful_finding"):
            assert key in reader, key
        fixture = json.loads((web / "tests/fixtures/professional-research-payload.json")
                             .read_text())
        report = pr.assemble(pr.ReportInputs(
            subject={"ticker": "X"},
            questions=[pr.QuestionView(key="q", text="q", domain="business_model")],
            findings=[pr.FindingView(finding_id="a", statement="s", domain="business_model",
                                     question_key="q")],
        ))
        produced = next(s for s in report["sections"] if s["key"] == "business_model")
        pinned = next(s for s in fixture["sections"] if s["key"] == "business_model")
        assert set(produced["findings"][0]) == set(pinned["findings"][0])
        produced_ev = next(s for s in report["sections"]
                           if s["key"] == "evidence_quality_and_gaps")
        pinned_ev = next(s for s in fixture["sections"]
                         if s["key"] == "evidence_quality_and_gaps")
        assert set(produced_ev) - {"lead"} <= set(pinned_ev)


@requires_postgres
class TestReconcileStepOnPostgres:
    async def test_a_failed_reconciliation_leaves_the_transaction_usable(
        self, monkeypatch
    ) -> None:
        # L3: an SQL error inside step 7b must cost the step only.
        from sqlalchemy import text as sql

        from app.services.pipeline import v3_pipeline

        async def broken(session, *_a, **_kw):  # noqa: ANN001, ANN202
            await session.execute(sql("SELECT * FROM no_such_table_reconciliation"))

        monkeypatch.setattr(gr, "reconcile_run", broken)
        engine = create_async_engine(POSTGRES_URL, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                outcome = v3_pipeline.V3ResearchOutcome()
                await v3_pipeline.reconcile_step(
                    session, run, company=SimpleNamespace(id=None), outcome=outcome
                )
                assert any("not reconciled" in d for d in outcome.degraded)
                assert (await session.execute(sql("SELECT 1"))).scalar_one() == 1
                await ledger.record_gap(session, run, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                                        description="still writable")
                await session.rollback()
        finally:
            await engine.dispose()


# ── Review round 2: false closes and false supersessions ──────────────────────── #

AS_OF = D(2026, 9, 30)


def _st(description: str, *findings, gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE):  # noqa: ANN001, ANN002, ANN202
    (verdict,) = gr.reconcile([_gap(description, gap_type=gap_type)], list(findings),
                              as_of=AS_OF)
    return verdict


class TestRound2FalseCloses:
    # (1) a reporting-period cue outranks a study word; spent/incurred is period capex.
    @pytest.mark.parametrize(
        "statement",
        [
            "Capex for FY2024 was £3.1m, mostly on DFS work",
            "Capex of £3.1m was spent during the year on feasibility work",
            "Capex of £3.1m was incurred on the PFS",
        ],
    )
    def test_period_spend_on_a_study_is_not_a_project_estimate(self, statement: str) -> None:
        assert "metric:capex_period" in rf.fields_stated(statement)
        assert "metric:capex_project" not in rf.fields_stated(statement)
        assert _st("Project capex estimate not acquired", _pf("f", statement)).status == (
            ledger.RECONCILED_PARTIALLY_CLOSED)

    # (2) "capital expenditure estimate" / "capital cost" ask for the project estimate;
    #     a family gap answered by period spend is only partial.
    def test_an_estimate_gap_is_not_closed_by_period_spend(self) -> None:
        assert "metric:capex_project" in rf.fields_mentioned(
            "No capital expenditure estimate acquired for the Foo Project")
        verdict = _st("No capital expenditure estimate acquired for the Foo Project",
                      _pf("f", "Capex at the Foo Project in FY2025 was £3.1m",
                          period_key="FY2025"))
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        family = _st("Capex not acquired", _pf("f", "Capex for FY2024 was £3.1m",
                                                period_key="FY2024"))
        assert family.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_SUBTYPE_DIFFERS in family.reasons

    # (3) period parsing.
    @pytest.mark.parametrize(
        ("text", "period"),
        [
            ("Revenue for the half-year ended 31 December 2025 was A$3.1m", "HYE2025-12-31"),
            ("Revenue for the six months to 30 June 2026 was £1m", "HYE2026-06-30"),
            ("Revenue for the year ended 30 June 2026 was A$3.1m", "FYE2026-06-30"),
            ("H1 FY26 revenue was A$1m", "FY2026-H1"),
            ("Revenue in Q3 FY2026 was A$1m", "FY2026-Q3"),
            ("2025 revenue not acquired", "CY2025"),
            ("Revenue for calendar year 2025 not acquired", "CY2025"),
            ("Cash at 30 June 2026 not acquired", "2026-06-30"),
        ],
    )
    def test_periods_are_parsed_literally(self, text: str, period: str) -> None:
        assert rf.statement_period(text) == period

    @pytest.mark.parametrize(
        ("gap", "statement", "period_key"),
        [
            ("FY2025 revenue not acquired",
             "Revenue for the half-year ended 31 December 2025 was A$3.1m", None),
            ("2025 revenue not acquired", "Revenue for FY2019 was £3.1m", "FY2019"),
            ("H1 2026 revenue not acquired", "Revenue for FY2026 was A$3.1m", "FY2026"),
            ("Cash at 30 June 2026 not acquired",
             "Cash and cash equivalents of £6.1m at 31 December 2025", None),
        ],
    )
    def test_another_period_never_closes(self, gap: str, statement: str, period_key) -> None:  # noqa: ANN001
        verdict = _st(gap, _pf("f", statement, period_key=period_key))
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED, verdict.reasons

    # (4) V2 missing items: exact names; derived metrics never labelled.
    @pytest.mark.parametrize("name", ["revenue_growth", "net_debt_to_ebitda", "gross_margin",
                                      "capex_to_revenue", "revenue_cagr"])
    def test_a_derived_metric_is_never_labelled(self, name: str) -> None:
        closers = [gr.FindingFacts("r", "", ("metric:revenue", "metric:net_debt",
                                             "metric:capex"), source_kinds=ISSUER)]
        out = gr.label_v2_items(missing_items=[name], concerns=[], closers=closers)
        assert out["missing_information"] == []
        assert gr.exact_field_for_item(name) is None

    def test_a_derived_concern_is_never_labelled(self) -> None:
        closers = [gr.FindingFacts("r", "", ("metric:revenue",), source_kinds=ISSUER)]
        out = gr.label_v2_items(missing_items=[],
                                concerns=[{"text": "Revenue growth is unknown"}],
                                closers=closers)
        assert out["council_concerns"] == []

    # (5) stale values.
    def test_a_current_value_needs_recent_evidence(self) -> None:
        old = _pf("f", "Cash and cash equivalents of £6.1m at 31 December 2022",
                  D(2023, 3, 1))
        verdict = _st("Current cash position unknown", old)
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_RECENCY_UNCONFIRMED in verdict.reasons
        fresh = _pf("n", "Cash and cash equivalents of £4.2m at 30 June 2026", D(2026, 9, 1))
        assert _st("Current cash position unknown", fresh).status == ledger.RECONCILED_CLOSED

    def test_a_former_or_passed_target_does_not_close_a_milestone_gap(self) -> None:
        former = _pf("f", "First production was expected in 2021 according to the 2018 "
                          "scoping study", D(2026, 8, 1))
        assert _st("First production date not acquired", former).status == (
            ledger.RECONCILED_STILL_OPEN)
        passed = _pf("p", "First production is expected in 2021", D(2019, 8, 1))
        verdict = _st("First production date not acquired", passed)
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_TARGET_PASSED in verdict.reasons
        stale_target = _pf("s", "First production is expected in 2021", D(2023, 8, 1))
        assert _st("First production date not acquired", stale_target).status == (
            ledger.RECONCILED_STILL_OPEN)  # a target before its own source is no target

    def test_a_historical_quote_only_partly_answers(self) -> None:
        verdict = _st("No capex estimate acquired",
                      _pf("f", "The 2019 PFS estimated capex of US$250m", D(2026, 8, 1)))
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_HISTORICAL_CITATION in verdict.reasons

    def test_a_gap_qualifier_must_be_met(self) -> None:
        verdict = _st("Post-tax NPV not acquired",
                      _pf("f", "Pre-tax NPV8 of US$700m", D(2026, 8, 1)))
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_QUALIFIER_DIFFERS in verdict.reasons


class TestRound2FalseSupersessions:
    @pytest.mark.parametrize(
        ("older", "newer"),
        [
            # (6) valuation basis
            ("Post-tax NPV10 of US$400m", "Post-tax NPV8 of US$500m"),
            ("Post-tax NPV8 of US$500m", "Pre-tax NPV8 of US$700m"),
            ("Post-tax IRR of 24%", "Pre-tax IRR of 30%"),
            # (7) resource category
            ("Mineral Resource of 50Mt at 1.5% TREO", "Ore Reserve of 20Mt at 1.2% TREO"),
            ("Inferred Resources of 30Mt at 2% TREO",
             "Measured and Indicated Resources of 20Mt at 3% TREO"),
            # (8) historical quotes without a year
            ("The DFS estimates capex of US$302m", "The PFS estimated capex of US$250m"),
            ("The DFS estimates capex of US$302m", "The previous capex estimate was US$250m"),
            # (9) product, plant type, stage mentioned in the clause
            ("Nameplate capacity of 4,000 tpa NdPr oxide", "Nameplate capacity of 12,500 tpa MREC"),
            ("Commissioning of the commercial plant is expected in 2028",
             "Commissioning of the pilot plant is expected in 2026"),
            ("First production at the Foo Project is expected in 2028",
             "First production at the Foo Project is expected in 2027, ahead of the Stage 2 "
             "expansion"),
            # a refinement of the same target is not a change
            ("FID is targeted for 2026", "FID is targeted for H2 2026"),
        ],
    )
    def test_different_quantities_are_never_ordered_in_time(self, older, newer) -> None:  # noqa: ANN001
        supersessions, _ = gr.supersede([_pf("o", older, D(2025, 11, 1)),
                                         _pf("n", newer, D(2026, 8, 1))])
        assert supersessions == [], (older, newer)

    def test_a_scale_rounded_amount_is_the_same_only_within_five_percent(self) -> None:
        # (14)
        rounded = rf.money_values("us$0.3bn")
        precise = rf.money_values("us$302m")
        far = rf.money_values("us$340m")
        assert gr.compare_values(gr._Value("money", rounded), gr._Value("money", precise)) == gr.SAME  # noqa: SLF001
        assert gr.compare_values(gr._Value("money", rounded), gr._Value("money", far)) == gr.DIFFERENT  # noqa: SLF001


class TestRound2Formats:
    @pytest.mark.parametrize(
        ("text", "currency", "value"),
        [
            ("302 million us dollars", "usd", 302e6),
            ("302m usd", "usd", 302e6),
            ("r4.2bn", "zar", 4.2e9),
            ("rmb 2.1bn", "cny", 2.1e9),
        ],
    )
    def test_money_formats(self, text: str, currency: str, value: float) -> None:
        ((cur, amount, _tol),) = rf.money_values(text)
        assert (cur, amount) == (currency, pytest.approx(value))

    @pytest.mark.parametrize(
        "text",
        [
            "Nameplate capacity of 12,500 t per annum",
            "Design capacity of 40,000 oz per annum",
            "Plant capacity of 1.5 million tonnes per annum",
            "Nameplate capacity 12,500 tonnes of oxide per year",
        ],
    )
    def test_capacity_formats(self, text: str) -> None:
        assert "metric:production_capacity" in rf.fields_stated(text)

    def test_ramp_up_output_is_not_capacity(self) -> None:
        # (13)
        assert rf.fields_stated(
            "The company expects to produce 2,000 tpa in the first year of ramp-up") == ()

    def test_an_exploration_target_is_not_a_resource(self) -> None:
        # (10)
        assert rf.fields_stated("An Exploration Target of 50-100Mt at 1-2% TREO sits beside "
                                "the Mineral Resource") == ()

    @pytest.mark.parametrize(
        "text",
        [
            "The DFS estimates capex of US$302m, which is not expected to change",
            "The DFS estimates capex of US$302m for the plant outside Johannesburg",
            "Capex of US$302m includes no contingency",
            "The DFS estimates capex of US$302m, not including US$20m of owner's costs",
            "The DFS estimates capex of US$302m, and the PFS figure is no longer valid",
        ],
    )
    def test_a_subordinate_negation_does_not_negate_the_value(self, text: str) -> None:
        # (11)
        assert "metric:capex" in rf.fields_stated(text)

    @pytest.mark.parametrize(
        "text",
        [
            "First production is targeted for 2027 after the delayed FID",
            "First production is targeted for 2027, with no further delays expected",
        ],
    )
    def test_a_subordinate_negation_does_not_negate_a_milestone(self, text: str) -> None:
        assert rf.fields_stated(text) == ("milestone:first_production",)

    def test_two_milestones_in_one_clause_state_neither(self) -> None:
        assert rf.fields_stated("First production and commissioning are expected in 2027") == ()


class TestRound2Pointer:
    async def test_the_column_points_at_the_latest_newer_finding(self, session) -> None:  # noqa: ANN001
        run = await ledger.open_run(session, mode="standard")
        rows = []
        for statement, published in (
            ("First production is expected in 2028", D(2024, 1, 1)),
            ("First production is expected in 2027", D(2025, 1, 1)),
            ("First production is expected in 2029", D(2026, 1, 1)),
        ):
            rows.append(await ledger.record_finding(
                session, run, statement=statement, evidence_ids=["ev"],
                source_kinds=ISSUER, source_published_at=published,
                claim_key=rf.claim_key_for(statement)))
        result = await gr.reconcile_run(session, run, as_of=AS_OF)
        await session.refresh(rows[0])
        assert rows[0].superseded_by_finding_id == rows[2].id
        assert {s["superseded_by_finding_id"] for s in result["supersessions"]} == {
            str(rows[2].id)}


class TestRound2Pins:
    """Cases that pin each round-2 rule on its own (no other rule masks them)."""

    @pytest.mark.parametrize(
        "statement",
        ["Sustaining capex of US$12m per annum", "Capex of £3.1m was spent in the quarter"],
    )
    def test_a_capex_gap_is_only_partly_answered_by_spend_or_sustaining(self, statement) -> None:  # noqa: ANN001
        verdict = _st("Capex not acquired", _pf("f", statement))
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_SUBTYPE_DIFFERS in verdict.reasons

    def test_current_needs_evidence_from_the_last_year(self) -> None:
        stale = _pf("f", "The DFS estimates capex of US$302m", D(2024, 1, 1))
        verdict = _st("Current capex estimate not acquired", stale)
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert gr.REASON_RECENCY_UNCONFIRMED in verdict.reasons

    def test_a_past_tense_target_is_a_former_target(self) -> None:
        assert rf.fields_stated("First production was expected in 2027") == ()
        assert _st("First production date not acquired",
                   _pf("f", "First production was expected in 2027")).status == (
            ledger.RECONCILED_STILL_OPEN)

    @pytest.mark.parametrize("name", ["capex_estimate", "cash_flow_statement", "revenue_by_segment"])
    def test_only_an_exact_field_name_is_labelled(self, name: str) -> None:
        assert gr.exact_field_for_item(name) is None
        closers = [gr.FindingFacts("f", "", ("metric:capex", "metric:revenue", "metric:cash"),
                                   source_kinds=ISSUER)]
        assert gr.label_v2_items(missing_items=[name], concerns=[],
                                 closers=closers)["missing_information"] == []
        assert gr.exact_field_for_item("fundamentals.capital_expenditure") == "metric:capex"


# ── Review round 3: withdrawn values in relative clauses; stale balances ──────── #


class TestRound3Withdrawal:
    WITHDRAWN = [
        "The DFS estimates capex of US$302m, which was withdrawn in March",
        "The DFS estimates capex of US$302m, that has since been withdrawn",
        "The DFS estimates capex of US$302m, which the board has not approved",
        "The DFS capex of US$302m excluding contingency has not been confirmed",
        "The DFS estimates capex of US$302m, which is under review",
        "The DFS estimates capex of US$302m, which was replaced by the updated plan",
    ]

    @pytest.mark.parametrize("statement", WITHDRAWN)
    def test_a_withdrawn_value_is_not_stated(self, statement: str) -> None:
        assert rf.fields_stated(statement) == ()
        assert _st("Current capex estimate not acquired",
                   _pf("f", statement, D(2026, 8, 1))).status == ledger.RECONCILED_STILL_OPEN

    def test_a_deferred_milestone_does_not_close(self) -> None:
        statement = "First production is targeted for 2027, which has been deferred indefinitely"
        assert rf.fields_stated(statement) == ()
        assert _st("First production date not acquired",
                   _pf("f", statement, D(2026, 8, 1))).status == ledger.RECONCILED_STILL_OPEN

    def test_a_withdrawn_value_never_supersedes(self) -> None:
        valid = _pf("v", "The DFS estimates capex of US$302m", D(2025, 11, 1))
        withdrawn = _pf("w", "The DFS estimates capex of US$410m, which was withdrawn in March",
                        D(2026, 8, 1))
        assert gr.supersede([valid, withdrawn]) == ([], [])

    def test_excluding_is_set_aside_only_up_to_its_noun(self) -> None:
        # The main verb after the excluded noun still negates the value.
        assert rf.fields_stated(
            "The DFS capex of US$302m excluding contingency is not disclosed") == ()
        assert "metric:capex" in rf.fields_stated(
            "The DFS capex of US$302m excluding contingency is the base estimate")

    def test_a_forward_no_change_clause_is_still_set_aside(self) -> None:
        assert "metric:capex" in rf.fields_stated(
            "The DFS estimates capex of US$302m, which is not expected to change")


class TestRound3Balances:
    @pytest.mark.parametrize(
        ("statement", "period_key", "reason"),
        [
            ("Cash and cash equivalents of £6.1m at 31 December 2019", None,
             gr.REASON_VALUE_STALE),
            ("Cash and cash equivalents of £6.1m", "FY2019", gr.REASON_VALUE_STALE),
            ("Cash and cash equivalents of £6.1m", None, gr.REASON_VALUE_DATE_UNKNOWN),
            ("Net debt of £2m at 31 December 2023", None, gr.REASON_VALUE_STALE),
        ],
    )
    def test_an_old_or_undated_balance_only_partly_answers(
        self, statement: str, period_key, reason: str  # noqa: ANN001
    ) -> None:
        gap = "Net debt not acquired" if "Net debt" in statement else "Cash balance not acquired"
        verdict = _st(gap, _pf("f", statement, period_key=period_key))
        assert verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED
        assert reason in verdict.reasons

    def test_a_recent_balance_closes(self) -> None:
        verdict = _st("Cash balance not acquired",
                      _pf("f", "Cash and cash equivalents of £4.2m at 30 June 2026",
                          D(2026, 9, 1)))
        assert verdict.status == ledger.RECONCILED_CLOSED
