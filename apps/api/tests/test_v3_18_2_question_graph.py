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

    async def investigate(self, *, role_id, questions, round_index, remaining_tool_calls):  # noqa: ANN001, ANN201
        outcome = TaskOutcome(tool_calls=len(questions))
        for q in questions:
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
