"""V3.18.2 — the research question graph.

The SCCO baseline had eight specialists and one subject, because nothing told them a
question about copper demand, one about the Tia Maria project and one about Grupo
Mexico's control of the board were different jobs owned by different people. These tests
pin the graph that does: every question has a domain, an owner, a reason and an evidence
contract; the owner answers it; its verdict is judged over what the tools returned; and a
question still open at the end says why.
"""

from __future__ import annotations

import dataclasses
import uuid
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
from app.services.agents.investigator import _tool_arguments
from app.services.director import contracts as c
from app.services.director import domains as d
from app.services.director.base_model import BASE_QUESTIONS, RETIRED_QUESTION_KEYS
from app.services.director.loop import FindingDraft, GapDraft, TaskOutcome, run_investigation
from app.services.director.planner import PlannedQuestion, persist_plan, plan_research
from app.services.director.roles import ROLES
from app.services.ledger import store as ledger
from app.services.playbooks import schema as playbook_schema
from app.services.playbooks.schema import PlaybookQuestion


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


_CFG = SimpleNamespace(
    v3_agent_tools_enabled=True,
    v3_deepseek_search_enabled=True,
    v3_filings_tool_enabled=True,
    v3_corpus_enabled=True,
)


class TestTheBaseModel:
    def test_every_domain_but_thesis_fit_is_asked_of_every_company(self) -> None:
        """Thesis fit is asked only when a thesis exists (V3.18.8)."""
        covered = {q.domain for q in BASE_QUESTIONS}
        assert covered == d.DOMAINS - {d.THESIS_FIT}

    @pytest.mark.parametrize("question", BASE_QUESTIONS, ids=lambda q: q.key)
    def test_every_question_is_a_complete_node(self, question: PlaybookQuestion) -> None:
        assert question.domain in d.DOMAINS
        assert question.owner_role in ROLES
        assert len(question.why_it_matters) >= 30
        assert question.required_tools <= ROLES[question.owner_role].tools, (
            "the owner must be able to answer its own question"
        )

    def test_financial_statements_are_one_domain_among_many(self) -> None:
        """The baseline this replaces was four-fifths financial statements."""
        financial = [q for q in BASE_QUESTIONS if q.domain in {d.FINANCIAL_CAPACITY,
                                                                d.CAPITAL_ALLOCATION}]
        assert len(financial) / len(BASE_QUESTIONS) < 0.4

    def test_an_industry_claim_needs_an_independent_source(self) -> None:
        industry = next(q for q in BASE_QUESTIONS if q.key == "industry_economics")
        kinds = {k for r in industry.evidence_contract.required_kinds for k in r.kinds}
        assert kinds <= c.INDEPENDENT_AUTHORITY_KINDS
        assert industry.evidence_contract.allow_external

    def test_financial_questions_never_reach_the_web(self) -> None:
        for q in BASE_QUESTIONS:
            if q.domain == d.FINANCIAL_CAPACITY:
                assert not q.evidence_contract.allow_external, q.key


class TestPlanning:
    async def test_the_owner_answers_its_question(self) -> None:
        """`valuation_inputs` needs only `get_financial_facts`, which the always-present
        financial analyst also holds. Least-loaded assignment would hand it over; the
        graph names its owner."""
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        assigned = {k: t.role_id for t in plan.tasks for k in t.question_keys}
        assert assigned["valuation_inputs"] == "valuation_context_analyst"
        assert assigned["industry_economics"] == "industry_analyst"
        assert assigned["competitive_position"] == "competitive_analyst"
        assert assigned["governance_and_control"] == "risk_analyst"

    async def test_eight_specialists_with_distinct_missions(self) -> None:
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        roles = {t.role_id for t in plan.tasks}
        assert {"financial_analyst", "business_analyst", "industry_analyst",
                "competitive_analyst", "event_analyst", "risk_analyst",
                "valuation_context_analyst"} <= roles
        by_role = {t.role_id: set(t.question_keys) for t in plan.tasks}
        assert not by_role["financial_analyst"] & by_role["risk_analyst"]

    async def test_a_dependant_is_scheduled_after_its_parent(self) -> None:
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        order = [q.key for q in plan.questions]
        assert order.index("material_risks") < order.index("counter_thesis")
        assert order.index("growth_pipeline") < order.index("upcoming_catalysts")

    async def test_a_retired_key_is_not_re_asked_from_a_gap(self) -> None:
        plan = await plan_research(
            subject="ANY:US", mode="deep", cfg=_CFG,
            prior_open_gaps=[("identity", "No citable evidence was retrieved for 'identity'.")],
        )
        assert "identity" not in {q.key for q in plan.questions}
        assert "identity" in RETIRED_QUESTION_KEYS

    async def test_a_re_asked_base_question_gets_its_whole_definition_back(self) -> None:
        plan = await plan_research(
            subject="ANY:US", mode="deep", cfg=_CFG,
            prior_open_gaps=[("material_risks", "No citable evidence was retrieved.")],
        )
        q = next(q for q in plan.questions if q.key == "material_risks")
        assert q.origin == ledger.ORIGIN_PRIOR_GAP
        assert q.owner_role == "risk_analyst" and q.domain == d.RISKS
        assert q.evidence_contract.allow_external


class TestNoFieldIsDroppedOnTheWayToThePlanner:
    """S4. `mandatory_questions()` once rebuilt the planned question field by field and
    dropped `required_calculations`, so the calculation leg of every methodology never ran."""

    def test_required_calculations_survive(self) -> None:
        from app.services.playbooks import select

        luxury = select(sector=None, industry="Luxury Goods").playbooks[0]
        planned = {q.key: q for q in luxury.mandatory_questions()}
        assert planned["pricing_power"].required_calculations == ("gross_margin",)

    def test_every_declared_field_survives(self) -> None:
        declared = {f.name for f in dataclasses.fields(PlaybookQuestion)}
        planned = {f.name for f in dataclasses.fields(PlannedQuestion)}
        question = next(q for q in BASE_QUESTIONS if q.key == "cash_generation_and_funding")
        out = playbook_schema.planned_from(question, origin=ledger.ORIGIN_DIRECTOR)
        for name in declared & planned:
            assert getattr(out, name) == getattr(question, name) or name == "required_tools"

    def test_mutation_dropping_a_field_is_caught(self, monkeypatch) -> None:
        original = playbook_schema.planned_from

        def lossy(question, *, origin):  # noqa: ANN001, ANN202
            return dataclasses.replace(original(question, origin=origin), required_calculations=())

        monkeypatch.setattr(playbook_schema, "planned_from", lossy)
        question = next(q for q in BASE_QUESTIONS if q.key == "cash_generation_and_funding")
        assert playbook_schema.planned_from(question, origin="director").required_calculations == ()
        assert question.required_calculations  # the declaration still names them


class TestToolArguments:
    def test_a_series_question_names_its_label(self) -> None:
        """Before V3.18.2 every series call got None, so `revenue_trajectory` could only
        ever end as a gap."""
        question = next(q for q in BASE_QUESTIONS if q.key == "revenue_trajectory")
        planned = playbook_schema.planned_from(question, origin="director")
        args = _tool_arguments("get_financial_series", planned, uuid.uuid4())
        assert args is not None and args["label"] == "revenue" and args["period_type"] == "annual"

    def test_a_calculation_question_names_its_metrics(self) -> None:
        question = next(q for q in BASE_QUESTIONS if q.key == "cash_generation_and_funding")
        planned = playbook_schema.planned_from(question, origin="director")
        args = _tool_arguments("get_calculated_metrics", planned, uuid.uuid4())
        assert args is not None and "cash_conversion" in args["metrics"]


class TestDeclarationsAreValidated:
    def test_an_unknown_domain_cannot_be_declared(self) -> None:
        with pytest.raises(ValueError, match="not a domain"):
            PlaybookQuestion(key="x", text="t", domain="astrology")

    def test_an_unknown_owner_cannot_be_declared(self) -> None:
        with pytest.raises(ValueError, match="not a declared role"):
            PlaybookQuestion(key="x", text="t", owner_role="oracle")

    def test_a_question_cannot_depend_on_itself(self) -> None:
        with pytest.raises(ValueError, match="itself"):
            PlaybookQuestion(key="x", text="t", depends_on=("x",))


def _ref(cid: str, kind: str, tier: str, source: str) -> c.EvidenceRef:
    return c.EvidenceRef(cid, "search_company_corpus", kind, tier, source)


class TestEvidenceContracts:
    INDUSTRY = c.EvidenceContract(
        min_items=2, min_distinct_sources=2, required_kinds=(c.NEEDS_INDEPENDENT_AUTHORITY,)
    )

    def test_the_issuers_own_account_of_its_industry_is_partial(self) -> None:
        """The SCCO shape: one 10-K, several excerpts, no independent source."""
        evidence = [_ref(f"ev:{n}", c.ISSUER_FILING, "T1_primary_filing", "10-K") for n in range(5)]
        verdict = c.evaluate_contract(self.INDUSTRY, evidence)
        assert verdict.status == c.STATUS_PARTIAL
        assert any("distinct sources" in m for m in verdict.missing)
        assert any("independent" in m for m in verdict.missing)

    def test_an_independent_statistical_source_satisfies_it(self) -> None:
        evidence = [
            _ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K"),
            _ref("ev:x:2", c.INDUSTRY_SPECIALIST, "T3_industry_specialist", "usgs.gov"),
        ]
        assert c.evaluate_contract(self.INDUSTRY, evidence).satisfied

    def test_nothing_is_unmet_not_partial(self) -> None:
        assert c.evaluate_contract(self.INDUSTRY, []).status == c.STATUS_UNMET

    def test_a_citation_counted_twice_is_one_item(self) -> None:
        evidence = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "a")] * 3
        verdict = c.evaluate_contract(c.EvidenceContract(min_items=2), evidence)
        assert verdict.items == 1 and not verdict.satisfied

    def test_min_tier(self) -> None:
        contract = c.EvidenceContract(min_tier="T2_regulator_or_gov")
        weak = [_ref("ev:1", c.SECONDARY_WEB, "T5_api_aggregator", "blog")]
        assert not c.evaluate_contract(contract, weak).satisfied

    def test_an_unsatisfiable_contract_cannot_be_declared(self) -> None:
        with pytest.raises(ValueError):
            c.EvidenceContract(min_items=1, min_distinct_sources=2)

    @pytest.mark.parametrize(
        ("tool", "item", "kind"),
        [
            ("search_company_corpus", {"source_tier": "T1_primary_filing"}, c.ISSUER_FILING),
            ("fetch_public_source", {"source_tier": "T3_industry_specialist",
                                     "fetched_url": "https://pubs.usgs.gov/x.pdf"},
             c.INDUSTRY_SPECIALIST),
            ("get_calculated_metrics", {}, c.PLATFORM_CALCULATION),
            ("get_macro_series", {"dataset_key": "fred"}, c.AUTHORITATIVE_STATISTICAL),
            ("fetch_public_source", {"fetched_url": "https://blog.example/x"}, c.SECONDARY_WEB),
        ],
    )
    def test_source_kind_comes_from_tool_and_tier_never_from_prose(self, tool, item, kind) -> None:
        assert c.evidence_ref_for(tool, "id", item).source_kind == kind


@dataclass
class _GraphInvestigator:
    """Returns one finding and some evidence per question; no model, no network."""

    evidence: dict[str, list[c.EvidenceRef]] = field(default_factory=dict)
    contexts_seen: list[dict] = field(default_factory=list)

    async def investigate(  # noqa: ANN201
        self, *, role_id, questions, round_index, remaining_tool_calls, question_context=None  # noqa: ANN001
    ):
        self.contexts_seen.append(dict(question_context or {}))
        outcome = TaskOutcome(tool_calls=len(questions))
        for q in questions:
            prior = (question_context or {}).get(q.key)
            if round_index > 0 and prior is not None and prior.prior_evidence:
                # A follow-up that finds nothing new, as the platform rung would not.
                outcome.question_evidence[q.key] = []
                continue
            refs = self.evidence.get(q.key, [])
            outcome.question_evidence[q.key] = refs
            outcome.acquisition_steps[q.key] = [{"rung": "platform_tools", "round": round_index}]
            if not refs:
                outcome.gaps.append(
                    GapDraft(
                        gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE,
                        description=f"No citable evidence was retrieved for {q.key!r}.",
                        question_key=q.key,
                    )
                )
            if refs:
                outcome.findings.append(
                    FindingDraft(
                        statement=f"{q.key} finding",
                        evidence_ids=(refs[0].citation_id,),
                        question_key=q.key,
                        source_kinds=tuple(sorted({r.source_kind for r in refs})),
                    )
                )
                outcome.answered_question_keys = (*outcome.answered_question_keys, q.key)
        return outcome


class TestFollowUps:
    async def test_a_partial_question_that_allows_external_gets_a_second_round(self, session) -> None:
        """An answered question used to end its research. One issuer excerpt settled
        the industry question for good."""
        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        issuer = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]
        investigator = _GraphInvestigator({"industry_economics": issuer})
        await run_investigation(session, run, plan, investigator=investigator, limits=plan.limits)
        round_two = [ctx for ctx in investigator.contexts_seen if "industry_economics" in ctx]
        assert len(round_two) >= 2, "the industry question was not re-asked"
        prior = round_two[-1]["industry_economics"]
        assert [r.citation_id for r in prior.prior_evidence] == ["ev:1"]

    async def test_a_financial_question_is_not_re_asked_for_the_web(self, session) -> None:
        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        issuer = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]
        investigator = _GraphInvestigator({"balance_sheet_risk": issuer})
        await run_investigation(session, run, plan, investigator=investigator, limits=plan.limits)
        seen = [ctx for ctx in investigator.contexts_seen if "balance_sheet_risk" in ctx]
        assert len(seen) == 1, "a contract that forbids the web must not buy a second round"


class TestTheLoopJudgesContracts:
    async def _run(self, session, evidence):  # noqa: ANN001, ANN202
        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        await run_investigation(
            session, run, plan, investigator=_GraphInvestigator(evidence), limits=plan.limits
        )
        rows = (
            await session.execute(
                select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
            )
        ).scalars().all()
        return run, {r.question_key: r for r in rows}

    async def test_the_graph_is_persisted(self, session) -> None:
        _run, rows = await self._run(session, {})
        q = rows["industry_economics"]
        assert q.domain == d.INDUSTRY_ECONOMICS and q.owner_role == "industry_analyst"
        assert q.evidence_contract_json["min_distinct_sources"] == 2
        assert q.search_intents_json and q.why_it_matters

    async def test_one_issuer_filing_leaves_the_industry_question_partial(self, session) -> None:
        issuer = [_ref(f"ev:{n}", c.ISSUER_FILING, "T1_primary_filing", "10-K") for n in range(3)]
        _run, rows = await self._run(session, {"industry_economics": issuer})
        q = rows["industry_economics"]
        assert q.contract_status == c.STATUS_PARTIAL
        assert q.unresolved_reason == ledger.UNRESOLVED_CONTRACT_UNMET
        assert any("independent" in m for m in q.contract_detail_json["missing"])
        assert q.acquisition_log_json and q.acquisition_log_json[0]["rung"] == "platform_tools"

    async def test_a_satisfied_contract_carries_no_unresolved_reason(self, session) -> None:
        evidence = {
            "industry_economics": [
                _ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K"),
                _ref("ev:x:2", c.INDUSTRY_SPECIALIST, "T3_industry_specialist", "usgs.gov"),
            ]
        }
        _run, rows = await self._run(session, evidence)
        assert rows["industry_economics"].contract_status == c.STATUS_SATISFIED
        assert rows["industry_economics"].unresolved_reason is None

    async def test_a_question_with_nothing_says_not_acquired(self, session) -> None:
        _run, rows = await self._run(session, {})
        assert rows["governance_and_control"].unresolved_reason == ledger.UNRESOLVED_NOT_ACQUIRED

    async def test_findings_carry_their_domain_and_topic(self, session) -> None:
        evidence = {"material_risks": [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]}
        run, _rows = await self._run(session, evidence)
        finding = (
            await session.execute(
                select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
            )
        ).scalars().one()
        assert finding.domain == d.RISKS
        assert finding.topic_key == "risks.material_risks"
        assert finding.source_kinds_json == [c.ISSUER_FILING]

    async def test_gaps_say_whose_gap_they_are(self, session) -> None:
        from app.models.ledger import ResearchGap
        from app.services.knowledge_state import NOT_ACQUIRED_BY_PLATFORM

        run, _rows = await self._run(session, {})
        gaps = (
            await session.execute(select(ResearchGap).where(ResearchGap.research_run_id == run.id))
        ).scalars().all()
        assert gaps and {g.knowledge_state for g in gaps} == {NOT_ACQUIRED_BY_PLATFORM}


# ── The review of #221: budgets that starved the work they bound ──────────── #


def _pq(key: str, *, priority: int = 2, origin: str = ledger.ORIGIN_DIRECTOR,
        depends_on: tuple[str, ...] = (), blocking: bool = False,
        tools: frozenset[str] = frozenset({"get_financial_facts"})) -> PlannedQuestion:
    return PlannedQuestion(key=key, text=key, origin=origin, priority=priority,
                           depends_on=depends_on, blocking=blocking, required_tools=tools)


@dataclass
class _Playbook:
    questions: list[PlannedQuestion]
    playbook_id: str = "pb"
    version: int = 1
    roles: list[str] = field(default_factory=list)

    def mandatory_questions(self):  # noqa: ANN201
        return self.questions

    def specialist_roles(self):  # noqa: ANN201
        return self.roles

    def completion_rules(self):  # noqa: ANN201
        return []


class TestTheBudgetsLeaveRoomForTheWork:
    async def test_standard_mode_holds_tasks_back_for_a_follow_up(self) -> None:
        """Round 0 spent all eight standard tasks, so no follow-up could ever run and
        every standard run stopped on `max_tasks`."""
        from app.services.director.planner import follow_up_reserve

        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG)
        assert follow_up_reserve(plan.limits) >= 1
        assert len(plan.tasks) <= plan.limits.max_tasks - follow_up_reserve(plan.limits)

    async def test_trimming_a_role_moves_its_questions_rather_than_dropping_them(self) -> None:
        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG)
        assigned = {k for t in plan.tasks for k in t.question_keys}
        budget_lost = {k for k, reason in plan.unassignable if "budget" in reason}
        for question in plan.questions:
            assert question.key in assigned or question.key in {
                k for k, _r in plan.unassignable
            }
        assert not budget_lost & {q.key for q in BASE_QUESTIONS if q.priority == 1}, (
            "a core question lost to the task budget while a kept role could answer it"
        )

    async def test_a_standard_run_with_partial_contracts_is_not_stopped_by_a_limit(
        self, session
    ) -> None:
        from app.services.director.loop import STOPPED_MAX_TASKS

        run = await ledger.open_run(session, mode="standard")
        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG)
        await persist_plan(session, run, plan)
        issuer = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]
        investigator = _GraphInvestigator({q.key: issuer for q in plan.questions})
        result = await run_investigation(
            session, run, plan, investigator=investigator, limits=plan.limits
        )
        assert result.stopped_by != STOPPED_MAX_TASKS
        assert not result.stopped_by_a_limit
        assert result.is_complete_analysis
        assert len(result.rounds) == 2, "the improvement round ran"
        # The improvement round re-asked partially met questions, carrying what the
        # first round found, within the tasks the planner held back.
        re_asked = [
            key for ctx in investigator.contexts_seen for key, qc in ctx.items()
            if qc.prior_evidence
        ]
        assert re_asked
        assert result.tasks_run <= plan.limits.max_tasks

    async def test_a_playbook_question_outranks_a_base_one_at_the_same_priority(self) -> None:
        """An alphabetical tie-break dropped `zinc_market` before `business_model`."""
        playbook = _Playbook([_pq(f"zz_playbook_{i}", priority=1, origin=ledger.ORIGIN_PLAYBOOK)
                              for i in range(20)])
        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG,
                                   playbooks=[playbook])
        kept = {q.key for q in plan.questions}
        assert {f"zz_playbook_{i}" for i in range(20)} <= kept
        assert all(not k.startswith("zz_playbook") for k in plan.dropped_for_capacity)

    def test_a_kept_question_keeps_its_prerequisite(self) -> None:
        """Capping the dependency-ordered list dropped exactly the dependants."""
        from app.services.director.planner import _cap_keeping_prerequisites

        ranked = [
            _pq("important", priority=1, depends_on=("parent",)),
            _pq("a", priority=2), _pq("b", priority=2),
            _pq("parent", priority=5),
        ]
        kept, dropped = _cap_keeping_prerequisites(ranked, 2)
        assert [q.key for q in kept] == ["important", "parent"]
        assert dropped == ["a", "b"]

    def test_a_question_whose_prerequisites_do_not_fit_is_dropped_whole(self) -> None:
        from app.services.director.planner import _cap_keeping_prerequisites

        ranked = [_pq("a", priority=1), _pq("child", priority=2, depends_on=("p1", "p2")),
                  _pq("p1", priority=5), _pq("p2", priority=5)]
        kept, dropped = _cap_keeping_prerequisites(ranked, 3)
        assert "child" in dropped
        assert {q.key for q in kept} == {"a", "p1", "p2"} - set(dropped) | {"a"}

    async def test_the_model_refiner_cannot_undo_the_dependency_order(self) -> None:
        @dataclass
        class _Reverse:
            async def refine(self, *, subject, questions, max_new):  # noqa: ANN001, ANN202
                return [q.key for q in reversed(questions)], []

        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG,
                                   refiner=_Reverse())
        order = [q.key for q in plan.questions]
        assert order.index("material_risks") < order.index("counter_thesis")


class TestFollowUpsNeedSomethingToClimb:
    async def test_no_follow_up_when_the_web_cannot_be_reached(self, session) -> None:
        """A follow-up runs only the external rung; without search it re-asked the
        question and did nothing."""

        @dataclass
        class _NoSearch(_GraphInvestigator):
            def can_search_externally(self, role_id: str) -> bool:
                return False

        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        issuer = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]
        investigator = _NoSearch({"industry_economics": issuer})
        await run_investigation(session, run, plan, investigator=investigator,
                                limits=plan.limits)
        seen = [ctx for ctx in investigator.contexts_seen if "industry_economics" in ctx]
        assert len(seen) == 1

    async def test_follow_ups_beyond_the_task_budget_name_the_limit(self, session) -> None:
        """A closable gap left without a follow-up for want of tasks is a LIMIT."""
        from app.services.director.loop import STOPPED_MAX_TASKS
        from app.services.director.planner import PlannedTask

        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)
        roles = sorted({t.role_id for t in plan.tasks})[:3]
        keys = [q.key for q in plan.questions][:3]
        plan.tasks = [PlannedTask(role_id=r, question_keys=[k]) for r, k in zip(roles, keys)]
        limits = dataclasses.replace(plan.limits, max_tasks=4, max_rounds=3)
        # Nothing is ever found: every question keeps a closable gap.
        result = await run_investigation(
            session, run, plan, investigator=_GraphInvestigator({}), limits=limits
        )
        assert result.stopped_by == STOPPED_MAX_TASKS
        assert result.tasks_run <= 4


class TestTheSecondReview:
    """The re-review of #222's fixes."""

    async def test_the_only_role_that_can_search_keeps_its_seat(self) -> None:
        """'Busiest first' trimmed the one role holding `search_web`, and its question
        became 'task budget exhausted' — no kept role could take it."""
        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG)
        assert "recent_external_developments" in {q.key for q in plan.questions}
        assigned = {k: t.role_id for t in plan.tasks for k in t.question_keys}
        assert assigned.get("recent_external_developments") == "external_research_analyst"
        assert "recent_external_developments" not in {k for k, _ in plan.unassignable}

    async def test_improvement_cut_short_is_named(self, session) -> None:
        from app.services.director.loop import LIMIT_STOP_REASONS

        run = await ledger.open_run(session, mode="standard")
        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG)
        await persist_plan(session, run, plan)
        issuer = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]
        result = await run_investigation(
            session, run, plan,
            investigator=_GraphInvestigator({q.key: issuer for q in plan.questions}),
            limits=plan.limits,
        )
        assert result.is_complete_analysis
        assert result.improvement_stopped_by in LIMIT_STOP_REASONS, (
            "partial contracts were still improvable when the rounds ran out"
        )

    async def test_only_partial_contracts_left_at_the_last_round_is_not_a_limit(
        self, session
    ) -> None:
        """No closable gap remains — the round limit cut nothing short. Without this
        branch the run was reported as stopped by max_rounds."""
        from app.services.director.base_model import base_question
        from app.services.director.loop import STOPPED_NOTHING_LEFT
        from app.services.director.planner import PlannedTask
        from app.services.playbooks.schema import planned_from

        run = await ledger.open_run(session, mode="standard")
        plan = await plan_research(subject="ANY:US", mode="standard", cfg=_CFG)
        industry = planned_from(base_question("industry_economics"), origin="director")
        blocker = dataclasses.replace(
            planned_from(base_question("balance_sheet_risk"), origin="playbook"),
            blocking=True,
        )
        plan.questions = [industry, blocker]
        plan.tasks = [
            PlannedTask(role_id="industry_analyst", question_keys=[industry.key]),
            PlannedTask(role_id="financial_analyst", question_keys=[blocker.key]),
        ]
        await persist_plan(session, run, plan)

        class _Investigator(_GraphInvestigator):
            async def investigate(self, *, role_id, questions, round_index,  # noqa: ANN001, ANN202
                                  remaining_tool_calls, question_context=None):
                outcome = await super().investigate(
                    role_id=role_id, questions=questions, round_index=round_index,
                    remaining_tool_calls=remaining_tool_calls,
                    question_context=question_context,
                )
                for gap in outcome.gaps:
                    gap.closable = False  # another round would fail the same way
                return outcome

        issuer = [_ref("ev:1", c.ISSUER_FILING, "T1_primary_filing", "10-K")]
        result = await run_investigation(
            session, run, plan, investigator=_Investigator({industry.key: issuer}),
            limits=dataclasses.replace(plan.limits, max_rounds=2),
            completion_rules=["all_blocking_questions_answered"],
        )
        assert result.stopped_by == STOPPED_NOTHING_LEFT
        assert len(result.rounds) == 2, "the improvement round for the partial one ran"


class TestAQuestionWithNothingLeftIsNotReAsked:
    """Live SCCO (deep): every issuer question returned nothing in round 0 and was
    re-asked in rounds 1 and 2 with identical tools — the run stopped on max_tasks."""

    def test_the_rung_left_rule(self) -> None:
        from app.services.director.base_model import base_question
        from app.services.director.loop import _has_a_rung_left
        from app.services.playbooks.schema import planned_from

        industry = planned_from(base_question("industry_economics"), origin="director")
        intents = len(industry.search_intents)
        assert _has_a_rung_left(industry, 0, 0, can_search=False), "corpus intents left"
        assert _has_a_rung_left(industry, intents, 0, can_search=True), "a search left"
        assert not _has_a_rung_left(industry, intents, 0, can_search=False)
        profitability = planned_from(base_question("profitability"), origin="director")
        assert not _has_a_rung_left(
            profitability, len(profitability.search_intents), 0, can_search=True
        ), "a deterministic question with no web contract has nothing left"

    async def test_an_empty_question_is_not_re_asked_forever(self, session) -> None:
        run = await ledger.open_run(session, mode="deep")
        plan = await plan_research(subject="ANY:US", mode="deep", cfg=_CFG)
        await persist_plan(session, run, plan)

        class _Empty(_GraphInvestigator):
            """Finds nothing, and reports its corpus rung as the real ladder does."""

            def can_search_externally(self, role_id: str) -> bool:
                return False

            async def investigate(self, *, role_id, questions, round_index,  # noqa: ANN001, ANN202
                                  remaining_tool_calls, question_context=None):
                outcome = await super().investigate(
                    role_id=role_id, questions=questions, round_index=round_index,
                    remaining_tool_calls=remaining_tool_calls,
                    question_context=question_context,
                )
                for q in questions:
                    done = (question_context or {}).get(q.key)
                    start = done.corpus_intents_done if done else 0
                    batch = list(q.search_intents)[start:start + 2]
                    outcome.acquisition_steps.setdefault(q.key, []).append(
                        {"rung": "corpus_by_intent", "queries": batch}
                    )
                return outcome

        investigator = _Empty({})
        result = await run_investigation(session, run, plan, investigator=investigator,
                                         limits=plan.limits)
        asked = {}
        for ctx in investigator.contexts_seen:
            for key in ctx:
                asked[key] = asked.get(key, 0) + 1
        most_intents = max(len(q.search_intents) for q in plan.questions)
        assert max(asked.values()) <= 1 + -(-most_intents // 2), (
            "re-asked beyond what its corpus intents could justify"
        )
        assert result.tasks_run < plan.limits.max_tasks
