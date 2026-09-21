"""V3.18.7 — a finding has one owner; anyone else references it.

The SCCO baseline said "operating margin above 50%", "net income grew on higher metal
prices" and "debt-to-equity 0.66" four to eight times each, in eight voices. These tests
pin the deterministic rule that stops a second domain restating what the first owns — and
the over-suppression direction: two findings that share a source and say different things
are two findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.ledger import ResearchFinding, ResearchQuestion
from app.services.agents.investigator import _build_prompt
from app.services.director import loop as loop_module
from app.services.director.loop import FindingDraft, TaskOutcome, run_investigation
from app.services.director.ownership import OwnershipIndex, figures_in
from app.services.director.planner import persist_plan, plan_research
from app.services.ledger import store as ledger

_CFG = SimpleNamespace(
    v3_agent_tools_enabled=True, v3_deepseek_search_enabled=True,
    v3_filings_tool_enabled=True, v3_corpus_enabled=True,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


class TestRestatement:
    def _index(self) -> OwnershipIndex:
        index = OwnershipIndex()
        index.add("F1", domain="financial_capacity", question_key="profitability",
                  statement="Operating margin was 52.2% in FY2025 on revenue of $13,420.0m.",
                  evidence_ids=["calc:om", "ev:is"])
        return index

    def test_the_same_figures_on_the_same_evidence_are_a_restatement(self) -> None:
        owner = self._index().restated_by(
            "SCCO's operating margin of 52.2% reflects strong pricing power.", ["calc:om"]
        )
        assert owner is not None and owner.finding_id == "F1"

    def test_a_different_figure_on_the_same_evidence_is_a_new_finding(self) -> None:
        assert self._index().restated_by(
            "Capex absorbed 27.9% of operating cash flow in FY2025.", ["ev:is"]
        ) is None

    def test_the_same_figure_on_different_evidence_is_not_assumed_the_same(self) -> None:
        """52.2 could be a different quantity in a different document."""
        assert self._index().restated_by(
            "Peru's share of mine output was 52.2%.", ["ev:x:usgs"]
        ) is None

    def test_years_do_not_make_two_statements_equal(self) -> None:
        assert figures_in("In FY2025 and 2024 revenue rose 17.4%") == frozenset({"17.4"})

    def test_prose_restatement_needs_most_distinctive_words(self) -> None:
        index = OwnershipIndex()
        index.add("F2", domain="governance", question_key="g",
                  statement="Grupo Mexico controls the company through a majority stake.",
                  evidence_ids=["ev:proxy"])
        assert index.restated_by("Grupo Mexico controls the company via its majority stake",
                                 ["ev:proxy"]) is not None
        assert index.restated_by("Related-party sales to Grupo Mexico affiliates occur",
                                 ["ev:proxy"]) is None


class TestTheWriterIsToldWhatIsOwned:
    def test_the_prompt_lists_other_domains_findings_to_reference(self) -> None:
        from app.services.director.base_model import base_question
        from app.services.playbooks.schema import planned_from

        question = planned_from(base_question("material_risks"), origin="director")
        _system, user = _build_prompt(
            question, [], "risk_analyst",
            established=[("F1", "Operating margin was 52.2% in FY2025.", "financial_capacity")],
        )
        assert "ALREADY ESTABLISHED BY OTHER SPECIALISTS" in user
        assert "[F1]" in user


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


@dataclass
class _Repeater:
    """Every role writes the SCCO baseline's favourite sentence, citing one excerpt."""

    calls: list[str] = field(default_factory=list)

    async def investigate(self, *, role_id, questions, round_index, remaining_tool_calls,  # noqa: ANN001, ANN201
                          question_context=None):
        self.calls.append(role_id)
        outcome = TaskOutcome(tool_calls=1)
        if round_index:
            return outcome
        for q in questions[:1]:
            outcome.findings.append(FindingDraft(
                statement="Net income rose to $4,348.2 million on higher metal prices.",
                evidence_ids=("ev:is",), question_key=q.key,
            ))
        return outcome


class TestTheLoopWritesItOnce:
    async def test_eight_roles_one_sentence_one_finding(self, session) -> None:
        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        result = await run_investigation(session, run, plan, investigator=_Repeater(),
                                         limits=plan.limits)
        findings = (await session.execute(
            select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
        )).scalars().all()
        assert len(findings) == 1
        assert result.restatements_referenced == len(plan.tasks) - 1
        logged = (await session.execute(
            select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
        )).scalars().all()
        references = [s for q in logged for s in (q.acquisition_log_json or [])
                      if s.get("rung") == "ownership"]
        assert references and all(s["referenced_finding_id"] == str(findings[0].id)
                                  for s in references)

    async def test_mutation_without_the_rule_the_sentence_is_written_eight_times(
        self, session, monkeypatch
    ) -> None:
        monkeypatch.setattr(loop_module, "OwnershipIndex", None, raising=False)
        from app.services.director import ownership

        monkeypatch.setattr(ownership.OwnershipIndex, "restated_by", lambda *_a, **_k: None)
        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        await run_investigation(session, run, plan, investigator=_Repeater(), limits=plan.limits)
        findings = (await session.execute(
            select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
        )).scalars().all()
        assert len(findings) == len(plan.tasks), "this is the SCCO baseline's shape"


class TestOppositesAreNotRestatements:
    """Review of 18.4–18.8: both were swallowed as restatements."""

    def test_a_rise_not_reflected_is_not_the_rise(self) -> None:
        from app.services.director.ownership import OwnershipIndex

        index = OwnershipIndex()
        index.add("f1", domain="industry_economics", question_key="q1",
                  statement="The copper benchmark rose 12.3% over twelve months.",
                  evidence_ids=["mo:1", "mo:2"])
        assert index.restated_by(
            "A 12.3% rise in the benchmark is not reflected in realised prices, which fell.",
            ["mo:2"],
        ) is None

    def test_has_not_secured_is_not_has_secured(self) -> None:
        from app.services.director.ownership import OwnershipIndex

        index = OwnershipIndex()
        index.add("f1", domain="business_model", question_key="q1",
                  statement="The company has secured offtake agreements for its cathode.",
                  evidence_ids=["ev:1"])
        assert index.restated_by(
            "The company has not secured offtake agreements for its cathode.", ["ev:1"]
        ) is None
        assert index.restated_by(
            "The company has secured offtake agreements for its cathode output.", ["ev:1"]
        ) is not None, "the same claim is still a restatement"

    def test_opposite_directions_are_different_findings(self) -> None:
        from app.services.director.ownership import OwnershipIndex

        index = OwnershipIndex()
        index.add("f1", domain="risks", question_key="q1",
                  statement="Operating margin was 52.2% in FY2025.", evidence_ids=["ev:1"],
                  direction="positive")
        assert index.restated_by("Operating margin was 52.2% in FY2025.", ["ev:1"],
                                 direction="negative") is None
        assert index.restated_by("Operating margin was 52.2% in FY2025.", ["ev:1"],
                                 direction="positive") is not None
