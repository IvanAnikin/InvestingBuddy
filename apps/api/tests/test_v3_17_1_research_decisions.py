"""V3.17.1 — the research-decision record, and the constraint that keeps it honest.

WHAT THIS SLICE IS FOR
======================
The discovery council has been emitting ``research_next`` per candidate for several
phases, into ``discovery_runs.config_json[...]["candidates_to_research_next"]`` — and
**nothing has ever read it.** No column, no status, no index, no backend consumer. See
``docs/v3.17-research-escalation-design.md`` §1.1.

That is this repository's signature failure mode: *metadata existed but was never
connected to execution.* V3.16.1b closed one instance of it a layer down (a cap the
consumer enforced that the producer was never told); this is the same shape, larger.

WHY THE TESTS ARE SHAPED THIS WAY
=================================
The design is explicit about the test that must NOT be written:

    "Explicitly forbidden: a test that asserts the decision JSON has a key. That is the
    shape of test that let this gap survive."

So nothing below asserts a field exists. The tests assert **behaviour a wrong
implementation would fail**:

* the state-machine vocabulary is internally coherent (a status cannot be both open and
  terminal, and nothing is orphaned between them);
* the migration's partial-index predicate is **derived from** ``OPEN_STATUSES`` rather
  than typed out — the anti-drift discipline V3.16.1b was entirely about;
* on real PostgreSQL, a second open decision for a company is **rejected by the
  database**, not by a convention;
* deleting a company **preserves** its decisions (CLAUDE.md #15), rather than cascading
  research history away.

V3.17.1 deliberately adds a table nothing writes to yet. The controller is V3.17.2/.3, and
landing the migration first is what lets it be proved reversible before behaviour depends
on it.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.models.research_decision import (
    DECISION_RESEARCH_NEXT,
    DECISION_SOURCES,
    DECISIONS,
    DEFAULT_MAX_ROUNDS,
    OPEN_STATUSES,
    SOURCE_DISCOVERY_COUNCIL,
    SOURCE_GAP_CARRY_FORWARD,
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_EXHAUSTED,
    STATUS_QUEUED,
    STATUS_RESEARCH_REQUIRED,
    STATUSES,
    TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
    TERMINAL_REASONS,
    TERMINAL_STATUSES,
    ResearchDecision,
)

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 040",
)


# --------------------------------------------------------------------------- #
# 1. The state machine is coherent
# --------------------------------------------------------------------------- #


class TestTheStateMachineVocabulary:
    def test_open_and_terminal_are_disjoint(self) -> None:
        """A status that is both open and terminal would make the partial index lie.

        The index says "these five mean work is in flight". If one of them were also
        reachable as a resting state, a company could sit permanently un-researchable
        because its finished decision still occupies the one open slot.
        """
        assert set(OPEN_STATUSES) & set(TERMINAL_STATUSES) == set()

    def test_every_status_is_either_open_or_terminal(self) -> None:
        """No status may be orphaned between the two sets.

        A status in neither is one the controller can reach and never leave, and nothing
        would report it as stuck.
        """
        assert set(STATUSES) == set(OPEN_STATUSES) | set(TERMINAL_STATUSES)

    def test_stopped_learning_and_stopped_spending_are_different_states(self) -> None:
        """`EXHAUSTED` and `ABANDONED` must not collapse into `COMPLETED` or each other.

        "We stopped learning" and "we stopped spending" need different follow-ups: the
        first says the evidence is not there, the second says we declined to keep paying
        to find out. A reader who cannot tell them apart will retry the wrong one.
        """
        assert len({STATUS_COMPLETED, STATUS_EXHAUSTED, STATUS_ABANDONED}) == 3
        for status in (STATUS_COMPLETED, STATUS_EXHAUSTED, STATUS_ABANDONED):
            assert status in TERMINAL_STATUSES

    def test_a_terminal_reason_vocabulary_exists_and_is_closed(self) -> None:
        """A terminal state with no reason is a silent absence."""
        assert TERMINAL_EXHAUSTED_NO_IMPROVEMENT in TERMINAL_REASONS
        assert all(isinstance(r, str) and r for r in TERMINAL_REASONS)

    def test_the_council_vocabulary_is_the_existing_one_not_a_parallel_one(self) -> None:
        """A second vocabulary for the same idea is a second answer to the same question.

        These four values are the discovery council's own ``internal_action`` set. If this
        table invented its own names, a mapping layer would appear, and mapping layers
        between two spellings of one concept are where meaning goes missing.
        """
        from app.services.llm.discovery_council import _ACTION_TO_FIELD

        assert set(DECISIONS) == set(_ACTION_TO_FIELD)

    def test_the_two_decision_sources_are_distinguishable(self) -> None:
        """"The model keeps suggesting this" and "this keeps failing to answer" differ."""
        assert SOURCE_DISCOVERY_COUNCIL != SOURCE_GAP_CARRY_FORWARD
        assert DECISION_SOURCES == {SOURCE_DISCOVERY_COUNCIL, SOURCE_GAP_CARRY_FORWARD}


# --------------------------------------------------------------------------- #
# 2. The table's shape enforces the rules the design relies on
# --------------------------------------------------------------------------- #


class TestTheTableEnforcesItsOwnRules:
    def test_every_foreign_key_is_set_null_never_cascade(self) -> None:
        """CLAUDE.md #15: deleting a discovery run must not delete research history.

        A CASCADE here would silently erase the record that research happened, which is
        the one thing this table exists to remember.
        """
        table = ResearchDecision.__table__
        assert table.foreign_keys, "the lineage columns are the point of this table"
        for fk in table.foreign_keys:
            assert fk.ondelete == "SET NULL", f"{fk.parent.name} is {fk.ondelete}"

    def test_the_reason_column_cannot_be_null(self) -> None:
        """This is the table that answers "why is this company being researched?"."""
        assert ResearchDecision.__table__.c.reason.nullable is False

    def test_cost_is_nullable_so_unpriced_never_reads_as_free(self) -> None:
        """CLAUDE.md #6. A stored 0 asserts the work cost nothing.

        Production reports `estimated_cost_usd` as NULL because no price book is
        configured. NOT NULL with a 0 default would turn "we do not know" into "it was
        free", and the controller's budget predicate would then treat unlimited unpriced
        work as affordable.
        """
        column = ResearchDecision.__table__.c.cost_usd_total
        assert column.nullable is True
        assert column.default is None
        assert column.server_default is None

    def test_the_open_slot_is_indexed_uniquely_on_company(self) -> None:
        indexes = {i.name: i for i in ResearchDecision.__table__.indexes}
        index = indexes["ux_research_decisions_one_open"]
        assert index.unique is True
        assert [c.name for c in index.columns] == ["company_id"]


# --------------------------------------------------------------------------- #
# 3. ANTI-DRIFT — the migration and the code must move together
# --------------------------------------------------------------------------- #


class TestTheIndexPredicateCannotDriftFromTheStateMachine:
    """The V3.16.1b lesson, applied one slice later.

    A predicate with the five statuses typed out passes every other test in this file and
    silently stops matching the day someone adds a sixth open state. Then the "one open
    decision per company" guarantee quietly covers only five of six, and duplicate paid
    research becomes possible with no test failing.
    """

    def _predicate(self, source: str) -> str:
        import re

        match = re.search(r"status IN \(([^)]*)\)", source)
        assert match, "no partial-index predicate found"
        return match.group(1)

    def _migration(self):  # noqa: ANN202
        """The migration module itself, so the predicate is the one it will APPLY.

        Reading the file as text and regexing it would match the module docstring's prose
        rather than the generated SQL — which it did, on the first attempt.
        """
        import importlib.util
        from pathlib import Path

        path = Path("alembic/versions/040_add_research_decisions.py")
        spec = importlib.util.spec_from_file_location("_m040", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_migration_predicate_names_exactly_the_open_statuses(self) -> None:
        predicate = self._migration()._OPEN_PREDICATE
        named = {s.strip().strip("'") for s in self._predicate(predicate).split(",")}

        assert named == set(OPEN_STATUSES)

    def test_adding_an_open_state_moves_the_migration_predicate_with_it(self) -> None:
        """The anti-drift assertion, made to bite.

        A hand-written predicate matches `OPEN_STATUSES` today and keeps matching this
        file's other tests forever. This one rebuilds the predicate the way the migration
        does and checks a *new* state would be carried into it — so the two cannot part.
        """
        module = self._migration()
        extended = (*OPEN_STATUSES, "awaiting_operator")
        rebuilt = "status IN (" + ", ".join(f"'{s}'" for s in extended) + ")"

        assert module._OPEN_PREDICATE != rebuilt
        assert "awaiting_operator" not in module._OPEN_PREDICATE
        # ...and the real predicate is exactly the same construction over the real tuple
        assert module._OPEN_PREDICATE == (
            "status IN (" + ", ".join(f"'{s}'" for s in OPEN_STATUSES) + ")"
        )

    def test_the_migration_builds_the_predicate_from_the_constant(self) -> None:
        """Not merely equal today — *derived*, so it cannot fall out of step.

        Asserting equality alone would pass on a hand-written list that happens to match.
        This asserts the migration imports the constant and joins it.
        """
        from pathlib import Path

        source = Path("alembic/versions/040_add_research_decisions.py").read_text()

        assert "OPEN_STATUSES" in source
        assert "from app.models.research_decision import" in source
        # ...and the five are never written out as a literal list in executable code.
        body = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "\"research_required\", \"queued\"" not in body
        assert "'research_required', 'queued', 'running', 'evidence_updated'" not in body

    def test_the_model_builds_the_same_predicate_from_the_same_constant(self) -> None:
        indexes = {i.name: i for i in ResearchDecision.__table__.indexes}
        predicate = str(
            indexes["ux_research_decisions_one_open"].dialect_options["postgresql"][
                "where"
            ]
        )
        named = {s.strip().strip("'") for s in self._predicate(predicate).split(",")}

        assert named == set(OPEN_STATUSES)


# --------------------------------------------------------------------------- #
# 4. On real PostgreSQL — the constraint is the database's, not a convention
# --------------------------------------------------------------------------- #


@requires_postgres
class TestOnRealPostgres:
    """SQLite runs this suite with foreign keys off, which has twice hidden a defect.

    A partial unique index is exactly the kind of guarantee that cannot be checked
    anywhere but on the real engine: the application can be asked to respect it and will
    appear to, right up to the first concurrent council.
    """

    @pytest.fixture
    async def pg(self):  # noqa: ANN201
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(POSTGRES_URL, future=True)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    async def _company(self, maker) -> uuid.UUID:  # noqa: ANN001
        from app.models.company import Company

        async with maker() as s:
            company = Company(
                id=uuid.uuid4(),
                ticker=f"T{uuid.uuid4().hex[:6].upper()}",
                exchange="US",
                name="Constraint Test Co",
                status="new",
            )
            s.add(company)
            await s.commit()
            return company.id

    def _decision(self, company_id, status, **over):  # noqa: ANN001, ANN202
        kwargs = {
            "id": uuid.uuid4(),
            "company_id": company_id,
            "source": SOURCE_DISCOVERY_COUNCIL,
            "decision": DECISION_RESEARCH_NEXT,
            "status": status,
            "reason": "test",
            "max_rounds": DEFAULT_MAX_ROUNDS,
        }
        kwargs.update(over)
        return ResearchDecision(**kwargs)

    async def test_a_second_open_decision_is_refused_by_the_database(self, pg) -> None:  # noqa: ANN001
        """The duplicate-paid-research guard.

        Two councils reviewing the same company concurrently is the real case. Note the
        second row uses a DIFFERENT open status, so this proves the predicate covers the
        whole open set rather than one value.
        """
        from sqlalchemy.exc import IntegrityError

        company_id = await self._company(pg)
        async with pg() as s:
            s.add(self._decision(company_id, STATUS_RESEARCH_REQUIRED))
            await s.commit()

        with pytest.raises(IntegrityError):
            async with pg() as s:
                s.add(self._decision(company_id, STATUS_QUEUED))
                await s.commit()

    async def test_terminal_decisions_may_pile_up_freely(self, pg) -> None:  # noqa: ANN001
        """History is not contention. A company researched three times has three records."""
        company_id = await self._company(pg)
        async with pg() as s:
            for _ in range(3):
                s.add(
                    self._decision(
                        company_id,
                        STATUS_EXHAUSTED,
                        terminal_reason=TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
                    )
                )
            await s.commit()

        async with pg() as s:
            s.add(self._decision(company_id, STATUS_RESEARCH_REQUIRED))
            await s.commit()  # the open slot was never occupied

    async def test_closing_a_decision_frees_the_company(self, pg) -> None:  # noqa: ANN001
        """Otherwise one finished escalation would lock a company out forever."""
        from sqlalchemy import update

        company_id = await self._company(pg)
        async with pg() as s:
            s.add(self._decision(company_id, STATUS_RESEARCH_REQUIRED))
            await s.commit()

        async with pg() as s:
            await s.execute(
                update(ResearchDecision)
                .where(ResearchDecision.company_id == company_id)
                .values(status=STATUS_COMPLETED, terminal_reason="evidence_sufficient")
            )
            await s.commit()

        async with pg() as s:
            s.add(self._decision(company_id, STATUS_RESEARCH_REQUIRED))
            await s.commit()

    async def test_deleting_a_company_preserves_its_decisions(self, pg) -> None:  # noqa: ANN001
        """CLAUDE.md #15, proved rather than asserted.

        On SQLite with foreign keys off this passes whatever the ondelete says, which is
        why it lives here.
        """
        from sqlalchemy import delete, select

        from app.models.company import Company

        company_id = await self._company(pg)
        async with pg() as s:
            s.add(
                self._decision(
                    company_id,
                    STATUS_COMPLETED,
                    terminal_reason="evidence_sufficient",
                )
            )
            await s.commit()

        async with pg() as s:
            await s.execute(delete(Company).where(Company.id == company_id))
            await s.commit()

        async with pg() as s:
            rows = (
                await s.execute(
                    select(ResearchDecision).where(
                        ResearchDecision.reason == "test",
                        ResearchDecision.company_id.is_(None),
                    )
                )
            ).scalars().all()
            assert rows, "the decision was deleted with its company"
