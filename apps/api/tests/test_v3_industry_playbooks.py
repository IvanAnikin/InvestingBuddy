"""V3.6 Slice 6.2 — the five industry playbooks.

THE PHASE'S ACCEPTANCE CRITERION
================================
> *Biotech and luxury runs of the same shape produce demonstrably different questions,
> metrics and sources; a blocking question that cannot be answered prevents the Council
> from convening.*

That is what most of this file measures. A playbook layer where every industry produced a
similar question set would be labelling, not methodology — which is exactly what V2 does
today and exactly what V3.6 exists to replace.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.calculations.definitions import DEFINITIONS
from app.services.director.planner import persist_plan, plan_research
from app.services.director.roles import ROLES
from app.services.ledger import store as ledger
from app.services.playbooks import select
from app.services.playbooks.industries import (
    BANKS_FINANCIALS,
    BIOTECH,
    INDUSTRIAL_DEFENSE,
    LUXURY,
    PLAYBOOKS,
    SEMICONDUCTORS,
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


class _Adapter:
    """The `PlaybookLike` shape the Director consumes."""

    def __init__(self, book) -> None:  # noqa: ANN001
        self._book = book
        self.playbook_id = book.playbook_id
        self.version = book.version

    def mandatory_questions(self):  # noqa: ANN201
        return self._book.mandatory_questions()

    def specialist_roles(self):  # noqa: ANN201
        return self._book.specialist_role_ids()

    def completion_rules(self):  # noqa: ANN201
        return self._book.completion_rule_ids()


# --------------------------------------------------------------------------- #
# They are materially different
# --------------------------------------------------------------------------- #


class TestDifferentiation:
    def test_no_two_playbooks_share_a_question(self) -> None:
        """A playbook layer where every industry asked similar questions would be
        labelling, not methodology."""
        seen: dict[str, str] = {}
        for book in PLAYBOOKS:
            for question in book.questions:
                assert question.key not in seen, (
                    f"{question.key} is in both {seen.get(question.key)} and "
                    f"{book.playbook_id}"
                )
                seen[question.key] = book.playbook_id

    def test_biotech_and_luxury_ask_disjoint_questions(self) -> None:
        assert not (
            {q.key for q in BIOTECH.questions} & {q.key for q in LUXURY.questions}
        )

    def test_biotech_requires_no_margin_metric(self) -> None:
        """A company with no revenue is not analysable by revenue growth and margins.
        The absence is the methodology."""
        assert not any("margin" in m for m in BIOTECH.required_metrics)
        assert "revenue" not in BIOTECH.required_metrics

    def test_a_bank_requires_no_generic_industrial_metric(self) -> None:
        """For a bank these are not merely unhelpful, they are meaningless — and
        reporting one lends a number the authority of a metric."""
        forbidden = {
            "gross_margin",
            "operating_margin",
            "capex_intensity",
            "net_debt_to_ebitda",
            "fcf_conversion",
        }
        assert not (set(BANKS_FINANCIALS.required_metrics) & forbidden)
        assert "cet1_ratio" in BANKS_FINANCIALS.required_metrics

    def test_luxury_makes_segment_scope_blocking(self) -> None:
        """The CFR failure mode, as methodology: without segment discipline a luxury
        analysis is about a company that does not exist."""
        blocking = {q.key for q in LUXURY.blocking_questions}
        assert "segment_discipline" in blocking
        question = next(q for q in LUXURY.questions if q.key == "segment_discipline")
        assert "get_segment_facts" in question.required_tools
        assert "not report a segment figure as a Group figure" in question.text

    def test_every_playbook_has_a_distinct_risk_framework(self) -> None:
        frameworks = {book.playbook_id: set(book.risk_framework) for book in PLAYBOOKS}
        for a in frameworks:
            for b in frameworks:
                if a < b:
                    assert frameworks[a] != frameworks[b], (a, b)

    def test_the_risk_frameworks_barely_overlap(self) -> None:
        """Two industries sharing most of their risk taxonomy would mean the taxonomy
        is generic."""
        overlap = set(BIOTECH.risk_framework) & set(BANKS_FINANCIALS.risk_framework)
        assert overlap == set()

    def test_preferred_sources_differ_by_industry(self) -> None:
        assert "clinicaltrials_gov" in BIOTECH.preferred_sources
        assert "clinicaltrials_gov" not in LUXURY.preferred_sources
        assert "sipri" in INDUSTRIAL_DEFENSE.preferred_sources

    def test_specialist_roles_differ_by_industry(self) -> None:
        assert set(LUXURY.specialist_roles) != set(SEMICONDUCTORS.specialist_roles)


# --------------------------------------------------------------------------- #
# Every declaration is real
# --------------------------------------------------------------------------- #


class TestDeclarationsAreReal:
    @pytest.mark.parametrize("book", PLAYBOOKS, ids=lambda b: b.playbook_id)
    def test_every_required_calculation_exists(self, book) -> None:  # noqa: ANN001
        for question in book.questions:
            for key in question.required_calculations:
                assert key in DEFINITIONS, f"{book.playbook_id}: {key}"

    @pytest.mark.parametrize("book", PLAYBOOKS, ids=lambda b: b.playbook_id)
    def test_every_specialist_role_exists(self, book) -> None:  # noqa: ANN001
        assert set(book.specialist_roles) <= set(ROLES)

    @pytest.mark.parametrize("book", PLAYBOOKS, ids=lambda b: b.playbook_id)
    def test_every_playbook_has_at_least_one_blocking_question(self, book) -> None:  # noqa: ANN001
        assert book.blocking_questions, book.playbook_id

    @pytest.mark.parametrize("book", PLAYBOOKS, ids=lambda b: b.playbook_id)
    def test_blocking_is_used_sparingly(self, book) -> None:  # noqa: ANN001
        """A blocking question stops the Council convening. More than two is a
        methodology that mostly refuses to report."""
        assert len(book.blocking_questions) <= 2, book.playbook_id

    @pytest.mark.parametrize("book", PLAYBOOKS, ids=lambda b: b.playbook_id)
    def test_every_playbook_explains_itself(self, book) -> None:  # noqa: ANN001
        assert book.notes and len(book.notes) > 60, book.playbook_id


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


class TestSelection:
    @pytest.mark.parametrize(
        ("sector", "expected"),
        [
            ("Health Care", "biotech"),
            ("Financials", "banks_financials"),
            ("Information Technology", "semiconductors"),
            ("Industrials", "industrial_defense"),
            ("Consumer Discretionary", "luxury"),
        ],
    )
    def test_a_sector_selects_its_playbook(self, sector: str, expected: str) -> None:
        assert expected in select(sector=sector).versions

    def test_a_pre_revenue_signal_selects_biotech_without_a_sector(self) -> None:
        assert "biotech" in select(signals=["pre_revenue"]).versions

    def test_an_unclassifiable_company_gets_none_with_a_reason(self) -> None:
        selection = select(sector="Utilities")
        assert selection.is_empty
        assert "no playbook declares this company" in selection.reason

    def test_a_conglomerate_gets_both_and_is_harder_to_complete(self) -> None:
        """ADR-054: every rule of every applicable playbook must hold."""
        selection = select(
            sector="Industrials", signals=["regulated_capital"]
        )
        assert {"industrial_defense", "banks_financials"} <= set(selection.versions)
        assert set(selection.completion_rules) == {
            "all_blocking_questions_answered",
            "no_council_blocking_gaps",
        }
        # And it is asked BOTH sets of questions.
        keys = {q.key for q in selection.questions}
        assert "backlog_and_orders" in keys
        assert "capital_adequacy" in keys


# --------------------------------------------------------------------------- #
# The phase's own demonstration
# --------------------------------------------------------------------------- #


class TestThroughTheDirector:
    async def test_biotech_and_luxury_produce_different_plans(self) -> None:
        """The V3.6 acceptance criterion, measured."""
        biotech = await plan_research(
            subject="MRNA", playbooks=[_Adapter(BIOTECH)]
        )
        luxury = await plan_research(subject="CFR", playbooks=[_Adapter(LUXURY)])

        biotech_keys = {q.key for q in biotech.questions}
        luxury_keys = {q.key for q in luxury.questions}
        # They share only the Director's baseline; every playbook question differs.
        shared = biotech_keys & luxury_keys
        assert "cash_runway" in biotech_keys - luxury_keys
        assert "segment_discipline" in luxury_keys - biotech_keys
        assert len(biotech_keys - shared) >= 4
        assert biotech.playbook_versions != luxury.playbook_versions

    async def test_a_playbook_instantiates_its_specialists(self) -> None:
        plan = await plan_research(subject="CFR", playbooks=[_Adapter(LUXURY)])
        assigned = {t.role_id for t in plan.tasks}
        assert "management_analyst" in assigned

    async def test_an_unanswerable_blocking_question_stops_the_council(
        self, session
    ) -> None:
        """The second half of the acceptance criterion, end to end through the ledger.

        Biotech's `pipeline_state` needs `get_recent_filings`, which is one of the tool
        names V3.3 reserved and has no implementation — so no role declares it, no role
        can be assigned, and the Council does not convene.
        """
        run = await ledger.open_run(session, mode="standard")
        plan = await plan_research(subject="MRNA", playbooks=[_Adapter(BIOTECH)])
        assert any(key == "pipeline_state" for key, _ in plan.unassignable)
        await persist_plan(session, run, plan)
        await session.commit()
        summary = await ledger.summarise(session, run)
        assert summary.council_may_convene is False

    async def test_an_answerable_blocking_question_does_not_stop_it(
        self, session
    ) -> None:
        run = await ledger.open_run(session, mode="standard")
        plan = await plan_research(subject="CFR", playbooks=[_Adapter(LUXURY)])
        # Luxury's blocking question needs get_segment_facts, which real roles hold.
        assert "segment_discipline" not in {key for key, _ in plan.unassignable}
        await persist_plan(session, run, plan)
        await session.commit()
        summary = await ledger.summarise(session, run)
        assert summary.questions_blocking_open == 1
        # Still blocked, but because the question is UNANSWERED rather than
        # unanswerable — which the loop can fix and a missing tool cannot.
        assert summary.gaps_blocking_council == 0
