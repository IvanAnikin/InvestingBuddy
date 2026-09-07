"""Run consumption telemetry and budgets — V3.0 Slice 5.

WHAT THESE TESTS PIN
====================
Three properties, and the middle one is the reason the file exists.

* Units are vendor-neutral and money is DERIVED, so a price change is a config
  change and historical runs stay comparable.
* **A zero is never mistaken for a measurement.** Most units have no producer on
  this path yet, and a record reporting ``web_search_calls: 0`` would be
  asserting no searches happened rather than that nothing counts searches. Every
  record names what it measured, and these tests fail if it stops.
* A budget is checked BEFORE the spend and names the limit that stopped the run.
  Every limit is unbounded by default, because the numbers are OPEN DECISIONS
  #13/#14 and belong to the user.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_job as _research_job  # noqa: F401
from app.models import research_run_consumption as _rrc  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.company import Company
from app.models.research_run_consumption import ResearchRunConsumption
from app.services import consumption as c
from app.services import consumption_recorder as recorder


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


class _Cfg:
    """A settings stand-in. Everything unbounded and unpriced, as shipped."""

    v3_run_consumption_enabled = True
    v3_run_max_model_calls = 0
    v3_run_max_model_tokens = 0
    v3_run_max_web_searches = 0
    v3_run_max_documents = 0
    v3_run_max_browser_minutes = 0.0
    v3_run_max_wall_seconds = 0.0
    v3_run_max_external_cost_usd = 0.0
    v3_price_per_million_input_tokens = 0.0
    v3_price_per_million_output_tokens = 0.0
    v3_price_per_thousand_web_searches = 0.0
    v3_price_per_thousand_url_fetches = 0.0
    v3_price_per_provider_research_run = 0.0
    v3_price_per_thousand_index_queries = 0.0
    v3_price_per_browser_minute = 0.0


class TestUnits:
    def test_units_add(self):
        a = c.ConsumptionUnits(model_calls=3, model_input_tokens=100)
        b = c.ConsumptionUnits(model_calls=2, model_output_tokens=50)
        total = a + b
        assert total.model_calls == 5
        assert total.model_tokens == 150

    def test_adding_merges_what_was_measured(self):
        a = c.ConsumptionUnits(model_calls=1, instrumented=frozenset({"model_calls"}))
        b = c.ConsumptionUnits(
            documents_downloaded=2, instrumented=frozenset({"documents_downloaded"})
        )
        assert (a + b).instrumented == {"model_calls", "documents_downloaded"}

    def test_an_estimate_anywhere_marks_the_whole_run(self):
        exact = c.ConsumptionUnits(model_calls=1)
        guessed = c.ConsumptionUnits(model_calls=1, tokens_estimated=True)
        assert (exact + guessed).tokens_estimated is True

    def test_a_record_names_what_it_did_not_measure(self):
        """The property everything else in this table depends on.

        There is no SearchProvider, no browser and no per-page counter on this
        path. A record reporting ``web_search_calls: 0`` would be asserting that
        no searches happened, which is a different claim from "nothing here
        counts searches" — and averaging the two together silently corrupts
        every later provider comparison.
        """
        record = c.ConsumptionUnits(
            model_calls=8, instrumented=frozenset({"model_calls"})
        ).to_dict()
        assert record["model_calls"] == 8
        assert "web_search_calls" in record["not_instrumented"]
        assert "model_calls" in record["instrumented"]
        assert "model_calls" not in record["not_instrumented"]

    def test_measured_answers_the_question_directly(self):
        units = c.ConsumptionUnits(instrumented=frozenset({"model_calls"}))
        assert units.measured("model_calls") is True
        assert units.measured("ocr_pages") is False

    def test_a_round_trip_preserves_what_was_measured(self):
        units = c.ConsumptionUnits(
            model_calls=4, instrumented=frozenset({"model_calls"})
        )
        assert c.ConsumptionUnits.from_dict(units.to_dict()) == units

    def test_an_unknown_dict_reads_as_nothing_measured(self):
        assert c.ConsumptionUnits.from_dict({}).instrumented == frozenset()
        assert c.ConsumptionUnits.from_dict(None).model_calls == 0


class TestDerivedCost:
    def test_no_price_book_means_unknown_not_free(self):
        """NULL and 0.0 are different statements and only one of them is true."""
        cost = c.derive_cost(c.ConsumptionUnits(model_input_tokens=1_000_000), c.PriceBook())
        assert cost.estimated_usd is None

    def test_cost_is_derived_from_units(self):
        prices = c.PriceBook(
            usd_per_million_input_tokens=2.0, usd_per_million_output_tokens=10.0
        )
        cost = c.derive_cost(
            c.ConsumptionUnits(
                model_input_tokens=6_000_000, model_output_tokens=2_200_000
            ),
            prices,
        )
        assert cost.estimated_usd == pytest.approx(6 * 2.0 + 2.2 * 10.0)

    def test_a_price_change_is_a_config_change(self):
        units = c.ConsumptionUnits(model_input_tokens=1_000_000)
        cheap = c.derive_cost(units, c.PriceBook(usd_per_million_input_tokens=1.0))
        dear = c.derive_cost(units, c.PriceBook(usd_per_million_input_tokens=4.0))
        assert (cheap.estimated_usd, dear.estimated_usd) == (1.0, 4.0)

    def test_an_unpriced_unit_is_named_not_dropped(self):
        """An estimate missing its dominant term is worse than no estimate."""
        cost = c.derive_cost(
            c.ConsumptionUnits(model_input_tokens=1_000_000, web_search_calls=60),
            c.PriceBook(usd_per_million_input_tokens=2.0),
        )
        assert cost.estimated_usd == pytest.approx(2.0)
        assert "web_search_calls" in cost.unpriced_units

    def test_estimated_and_actual_are_separate_fields(self):
        cost = c.derive_cost(
            c.ConsumptionUnits(model_input_tokens=1_000_000),
            c.PriceBook(usd_per_million_input_tokens=2.0),
        )
        assert cost.estimated_usd is not None
        assert cost.actual_usd is None


class TestBudget:
    def test_every_limit_is_unbounded_by_default(self):
        """The numbers are OPEN DECISIONS #13/#14 and belong to the user."""
        budget = c.ResearchBudget()
        assert budget.is_unbounded
        assert budget.active_limits == ()
        budget.check(c.ConsumptionUnits(model_calls=10**9))

    def test_settings_defaults_are_unbounded(self):
        from app.core.config import Settings

        assert c.budget_from_settings(Settings()).is_unbounded

    def test_settings_defaults_are_unpriced(self):
        from app.core.config import Settings

        assert c.price_book_from_settings(Settings()).is_empty

    def test_a_limit_names_itself_when_it_stops_a_run(self):
        budget = c.ResearchBudget(max_model_calls=10)
        with pytest.raises(c.BudgetExceeded) as excinfo:
            budget.check(c.ConsumptionUnits(model_calls=11))
        assert excinfo.value.limit == "max_model_calls"
        assert excinfo.value.allowed == 10

    def test_the_check_is_before_the_spend_not_after(self):
        """A run that discovers it is over budget afterwards has been audited."""
        budget = c.ResearchBudget(max_model_calls=10)
        spent = c.ConsumptionUnits(model_calls=9)
        budget.check(spent)  # fine as it stands
        with pytest.raises(c.BudgetExceeded):
            budget.check(spent, about_to_spend=c.ConsumptionUnits(model_calls=5))

    def test_cost_is_bounded_separately(self):
        budget = c.ResearchBudget(max_external_cost_usd=1.0)
        budget.check(c.EMPTY_CONSUMPTION, estimated_cost_usd=0.5)
        with pytest.raises(c.BudgetExceeded) as excinfo:
            budget.check(c.EMPTY_CONSUMPTION, estimated_cost_usd=2.0)
        assert excinfo.value.limit == "max_external_cost_usd"

    def test_an_unknown_cost_cannot_break_a_cost_limit(self):
        """Refusing to spend because we cannot price it would be a guess too."""
        c.ResearchBudget(max_external_cost_usd=1.0).check(
            c.EMPTY_CONSUMPTION, estimated_cost_usd=None
        )

    def test_a_budget_breach_is_permanent_for_the_worker(self):
        """Retrying spends the budget again to reach the same ceiling."""
        from app.services.jobs.worker import is_transient_failure

        assert is_transient_failure(c.BudgetExceeded("max_model_calls", 11, 10)) is False

    def test_active_limits_are_reported(self):
        budget = c.ResearchBudget(max_model_calls=10, max_wall_seconds=600)
        assert set(budget.active_limits) == {"max_model_calls", "max_wall_seconds"}
        assert not budget.is_unbounded


class TestProducers:
    class _Tracker:
        prompt_tokens = 6000
        completion_tokens = 2200
        any_estimated = False
        attempts_by_agent = {"bull": 1, "bear": 1, "chair": 2}

    def test_the_council_tracker_becomes_units(self):
        units = c.from_usage_tracker(self._Tracker(), documents=3)
        assert units.model_input_tokens == 6000
        assert units.model_output_tokens == 2200
        assert units.model_calls == 4  # retries are calls
        assert units.documents_downloaded == 3
        assert "model_calls" in units.instrumented
        # And what it does NOT measure stays unmeasured.
        assert "web_search_calls" not in units.instrumented

    def test_no_tracker_is_nothing_measured(self):
        assert c.from_usage_tracker(None) == c.EMPTY_CONSUMPTION

    def test_a_council_result_without_consumption_reports_nothing_measured(self):
        """A result produced before this field existed is not zero-consumption."""

        class _Old:
            pass

        assert c.council_consumption(_Old()).instrumented == frozenset()

    def test_a_council_result_carries_its_consumption(self):
        from app.services.llm.schemas import CouncilResult

        result = CouncilResult()
        assert result.consumption == {}
        result.consumption = c.ConsumptionUnits(
            model_calls=8, instrumented=frozenset({"model_calls"})
        ).to_dict()
        assert c.council_consumption(result).model_calls == 8

    def test_the_run_record_carries_units_cost_and_budget(self):
        record = c.run_record(c.ConsumptionUnits(model_calls=8))
        assert set(record) == {"units", "cost", "budget"}
        assert record["cost"]["estimated_usd"] is None
        assert record["budget"]["active_limits"] == []


class TestPersistence:
    async def test_nothing_is_written_when_the_flag_is_off(self, factory):
        class _Off(_Cfg):
            v3_run_consumption_enabled = False

        async with factory() as s:
            written = await recorder.record_run(
                s,
                run_type=recorder.RUN_TYPE_COMPANY_RESEARCH,
                units=c.ConsumptionUnits(model_calls=8),
                cfg=_Off(),
            )
            assert written is None
            rows = (await s.execute(select(ResearchRunConsumption))).scalars().all()
        assert rows == []

    async def test_the_flag_is_off_by_default(self):
        from app.core.config import Settings

        assert Settings().v3_run_consumption_enabled is False

    async def test_a_run_is_recorded(self, factory):
        async with factory() as s:
            company = Company(name="Pandora A/S", ticker="PNDORA", exchange="CPH")
            s.add(company)
            await s.commit()
            company_id = company.id

        async with factory() as s:
            await recorder.record_run(
                s,
                run_type=recorder.RUN_TYPE_COMPANY_RESEARCH,
                units=c.ConsumptionUnits(
                    model_input_tokens=6000,
                    model_output_tokens=2200,
                    model_calls=8,
                    documents_downloaded=3,
                    elapsed_seconds=312.5,
                    instrumented=c.COUNCIL_INSTRUMENTED | c.RUN_INSTRUMENTED,
                ),
                company_id=company_id,
                outcome="completed",
                cfg=_Cfg(),
            )
        async with factory() as s:
            row = (await s.execute(select(ResearchRunConsumption))).scalar_one()
        assert row.model_calls == 8
        assert row.model_tokens == 8200
        assert row.elapsed_seconds == pytest.approx(312.5)
        assert row.company_id == company_id
        assert row.outcome == "completed"
        # No price book configured, so cost is UNKNOWN rather than free.
        assert row.estimated_cost_usd is None
        assert row.consumption_json["units"]["model_calls"] == 8
        assert "web_search_calls" in row.consumption_json["units"]["not_instrumented"]

    async def test_a_failed_run_is_recorded_too(self, factory):
        """Averaging only the successes produces the one number nobody needs."""
        async with factory() as s:
            await recorder.record_run(
                s,
                run_type=recorder.RUN_TYPE_COMPANY_RESEARCH,
                units=c.ConsumptionUnits(model_calls=6, documents_downloaded=11),
                outcome="failed",
                cfg=_Cfg(),
            )
        async with factory() as s:
            row = (await s.execute(select(ResearchRunConsumption))).scalar_one()
        assert row.outcome == "failed"
        assert row.model_calls == 6

    async def test_recording_never_fails_a_run(self, factory):
        """The measurement is the least valuable thing the run produced."""

        class _Broken:
            def add(self, _row):
                raise RuntimeError("database gone")

            async def commit(self):  # pragma: no cover - never reached
                raise AssertionError

            async def rollback(self):
                return None

        assert (
            await recorder.record_run(
                _Broken(),  # type: ignore[arg-type]
                run_type=recorder.RUN_TYPE_COMPANY_RESEARCH,
                units=c.ConsumptionUnits(model_calls=1),
                cfg=_Cfg(),
            )
            is None
        )

    async def test_a_price_book_produces_a_cost(self, factory):
        class _Priced(_Cfg):
            v3_price_per_million_input_tokens = 2.0
            v3_price_per_million_output_tokens = 10.0

        async with factory() as s:
            await recorder.record_run(
                s,
                run_type=recorder.RUN_TYPE_COMPANY_RESEARCH,
                units=c.ConsumptionUnits(
                    model_input_tokens=1_000_000, model_output_tokens=1_000_000
                ),
                cfg=_Priced(),
            )
        async with factory() as s:
            row = (await s.execute(select(ResearchRunConsumption))).scalar_one()
        assert row.estimated_cost_usd == pytest.approx(12.0)

    async def test_lineage_is_set_null_never_cascade(self):
        """Research history is preserved on deletion (CLAUDE.md rule 15)."""
        for fk in ResearchRunConsumption.__table__.foreign_keys:
            assert fk.ondelete == "SET NULL"

    async def test_every_lineage_column_is_nullable(self):
        """A run that failed before producing a report still consumed a budget."""
        cols = ResearchRunConsumption.__table__.columns
        for name in ("research_job_id", "agent_run_id", "company_id", "report_id"):
            assert cols[name].nullable is True


class TestWiring:
    def test_the_executor_returns_consumption(self):
        import inspect

        from app.services import company_research_service as svc

        source = inspect.getsource(svc.execute_company_research)
        assert '"consumption": units' in source
        # Wall time is measured at THIS level: it spans the workflow and the
        # final-report step, which no inner component sees the whole of.
        assert "elapsed_seconds=round(time.perf_counter() - started_at, 3)" in source

    def test_the_council_attaches_its_own_consumption(self):
        import inspect

        from app.services.llm import council

        assert "consumption.from_usage_tracker(tracker)" in inspect.getsource(council)

    def test_the_job_path_records_it(self):
        import inspect

        from app.services import company_research_service as svc

        source = inspect.getsource(svc.process_company_research_by_id)
        assert "_record_consumption(" in source

    def test_migration_020_is_additive_only(self):
        from pathlib import Path

        # Read as text: ``alembic/versions`` is not a package, and a migration is
        # a file Alembic loads by path rather than a module anything imports.
        source = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "020_add_research_run_consumption.py"
        ).read_text()
        assert 'down_revision: str | None = "019"' in source
        assert "op.create_table(" in source
        # Additive-only through V3.2: no drops, no renames, no NOT NULL
        # tightening of anything that already exists.
        assert "op.drop_column(" not in source
        assert "op.alter_column(" not in source
        # And a real downgrade, not ``pass``.
        assert "op.drop_table(\"research_run_consumption\")" in source

    def test_the_new_row_is_indexed_for_the_questions_it_answers(self):
        idx = {i.name for i in ResearchRunConsumption.__table__.indexes}
        assert "ix_research_run_consumption_created_at" in idx
        assert "ix_research_run_consumption_company_id" in idx
        assert "ix_research_run_consumption_run_type" in idx
