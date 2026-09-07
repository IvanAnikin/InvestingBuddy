"""The stage a reader is shown must be the stage that is running — V3.0 Slice 3.1.

THE DEFECT
==========
Two independent inaccuracies, both of which made the stage display say something
untrue about a real run.

**The map put the wrong nodes in the wrong stages.** Eleven of the twenty mapped
graph nodes were `evidence_validation`, including the five deterministic
analysis-council agents (bull, bear, risk, valuation guard, chair) and the two
that write and log the draft. So a run reported "Validating and citing the
evidence" while it was forming and challenging an investment view.

**And the stage that names the longest part of a run was attached to a node that
had already finished.** `build_company_snapshot` was mapped to
`primary_document_ingestion`, but no issuer document is read anywhere in that
graph: deep ingestion happens inside `llm.council.maybe_run_council`, which runs
AFTER the graph inside the final-report generator. Measured live, that call is
~154s of ingestion plus ~145-190s of council against a 261-451s whole-run
envelope — the clear majority of a run — and throughout it the reader was shown
the last graph node's stage. Both stages were then stamped "completed"
retroactively, claiming they had happened at a time they had not.

Naming the stage in flight is the entire value of a stage display. Naming the
wrong one is worse than naming none.

WHAT THESE TESTS PIN
====================
That the map now matches what each node does; that the post-graph phases report
themselves while they run; that a stage is never claimed retroactively; and that
progress reporting can never destroy a research run.
"""

from __future__ import annotations

import inspect

import pytest

from app.services import research_job


class TestTheMapMatchesTheWork:
    def test_the_council_agents_are_in_the_council_stage(self):
        for node in (
            "bull_case_agent",
            "bear_case_agent",
            "risk_agent",
            "valuation_guard_agent",
            "investment_committee_chair",
        ):
            assert research_job.stage_for_node(node) == (
                research_job.STAGE_COUNCIL_ANALYSIS
            ), f"{node} forms a view; it does not validate citations"

    def test_writing_the_draft_is_report_assembly(self):
        assert research_job.stage_for_node("save_draft_report") == (
            research_job.STAGE_REPORT_ASSEMBLY
        )
        assert research_job.stage_for_node("log_agent_steps") == (
            research_job.STAGE_REPORT_ASSEMBLY
        )

    def test_no_graph_node_claims_document_ingestion(self):
        """The graph reads no issuer document, so no node may say it does."""
        offenders = [
            node
            for node, stage in research_job.NODE_TO_STAGE.items()
            if stage == research_job.STAGE_DOCUMENT_INGESTION
        ]
        assert offenders == []

    def test_the_snapshot_node_reports_what_it_actually_does(self):
        """It assembles already-fetched structured data, not documents."""
        assert research_job.stage_for_node("build_company_snapshot") == (
            research_job.STAGE_FINANCIAL_EXTRACTION
        )

    def test_evidence_validation_keeps_the_nodes_that_do_validate(self):
        for node in (
            "source_quality_agent",
            "create_citations",
            "validate_report_schema",
            "research_completeness_agent",
            "citation_validator_v2",
        ):
            assert research_job.stage_for_node(node) == (
                research_job.STAGE_EVIDENCE_VALIDATION
            )

    def test_every_mapped_node_exists_in_the_graph(self):
        """A map entry for a node that does not exist is a silent dead letter."""
        from app.workflows import company_analysis

        source = inspect.getsource(company_analysis)
        for node in research_job.NODE_TO_STAGE:
            assert f'add_node("{node}"' in source, f"{node} is not in the graph"

    def test_every_mapped_stage_is_a_real_stage(self):
        for stage in research_job.NODE_TO_STAGE.values():
            assert stage in research_job.STAGE_ORDER
        for stage in research_job.PHASE_TO_STAGE.values():
            assert stage in research_job.STAGE_ORDER

    def test_an_unmapped_node_moves_nothing(self):
        """Adding a graph node must not silently make the UI claim progress."""
        assert research_job.stage_for_node("a_node_added_next_year") is None
        assert research_job.stage_for_phase("a_phase_added_next_year") is None


class TestPostGraphPhases:
    def test_the_three_long_phases_have_names(self):
        assert research_job.stage_for_phase(
            research_job.PHASE_EVIDENCE_INGESTION
        ) == research_job.STAGE_DOCUMENT_INGESTION
        assert research_job.stage_for_phase(
            research_job.PHASE_COUNCIL_AGENTS
        ) == research_job.STAGE_COUNCIL_ANALYSIS
        assert research_job.stage_for_phase(
            research_job.PHASE_REPORT_ASSEMBLY
        ) == research_job.STAGE_REPORT_ASSEMBLY

    def test_the_council_emits_ingestion_and_agent_phases(self):
        """Pinned against the source, because the alternative is a live run.

        Both call sites sit immediately before the two operations that dominate a
        real run's wall clock. A refactor that drops one would otherwise return
        the reader to a five-minute silent stretch, and nothing offline would
        notice.
        """
        from app.services.llm import council

        source = inspect.getsource(council.maybe_run_council)
        assert "PHASE_EVIDENCE_INGESTION" in source
        assert "PHASE_COUNCIL_AGENTS" in source
        # Ingestion is reported BEFORE the collection it describes.
        assert source.index("PHASE_EVIDENCE_INGESTION") < source.index(
            "collect_company_source_evidence("
        )
        # The agents are reported BEFORE they run.
        assert source.index("PHASE_COUNCIL_AGENTS") < source.index(
            "result = await run_council("
        )

    def test_the_generator_reports_assembly(self):
        from app.services import final_report_generator

        source = inspect.getsource(final_report_generator)
        assert "PHASE_REPORT_ASSEMBLY" in source

    def test_the_council_does_not_import_a_ui_string(self):
        """Producers name what they do; ``research_job`` decides the words."""
        from app.services.llm import council

        source = inspect.getsource(council)
        assert "STAGE_COUNCIL_ANALYSIS" not in source
        assert "Running the research council" not in source


class TestProgressNeverBreaksAResearchRun:
    """A stage display is a convenience. The report is the product."""

    async def test_a_raising_callback_does_not_fail_the_council(self):
        from app.services.llm.council import _report_phase

        async def explodes(_phase: str) -> None:
            raise RuntimeError("the session closed")

        await _report_phase(explodes, research_job.PHASE_COUNCIL_AGENTS)

    async def test_a_raising_callback_does_not_fail_the_generator(self):
        from app.services.final_report_generator import _report_phase

        async def explodes(_phase: str) -> None:
            raise RuntimeError("the lease lapsed")

        await _report_phase(explodes, research_job.PHASE_REPORT_ASSEMBLY)

    async def test_no_callback_is_a_no_op(self):
        from app.services.final_report_generator import _report_phase as gen_phase
        from app.services.llm.council import _report_phase as council_phase

        await council_phase(None, research_job.PHASE_COUNCIL_AGENTS)
        await gen_phase(None, research_job.PHASE_REPORT_ASSEMBLY)


class TestNoRetroactiveStamping:
    """A stage is recorded when it happens, or not at all."""

    async def test_a_run_without_a_council_does_not_claim_one_ran(
        self, monkeypatch
    ) -> None:
        from app.services import company_research_service as svc

        source = inspect.getsource(svc.process_company_research_by_id)
        # The old code appended COUNCIL_ANALYSIS and REPORT_ASSEMBLY to the
        # completed list on the way out, whether or not either had happened.
        tail = source[source.index("The council and the report assembly") :]
        assert "STAGE_COUNCIL_ANALYSIS" not in tail
        assert "STAGE_REPORT_ASSEMBLY" not in tail

    async def test_one_advance_function_serves_both_progress_sources(self):
        """Nodes and phases must not be able to disagree about a transition."""
        from app.services import company_research_service as svc

        source = inspect.getsource(svc.process_company_research_by_id)
        assert "async def advance(stage: str)" in source
        assert "on_stage=advance" in source


class TestStagesInFlight:
    """End to end: what a reader sees WHILE a run is in each phase."""

    @pytest.fixture
    def seen(self):
        return []

    async def test_the_reader_is_told_the_phase_that_is_running(self, seen):
        import uuid

        from app.models.company import Company
        from app.services import company_research_service as svc

        company = Company(
            id=uuid.uuid4(), name="Pandora A/S", ticker="PNDORA", exchange="CPH"
        )
        report_id = uuid.uuid4()

        async def run_analysis(db, **kwargs):
            on_node = kwargs.get("on_node")
            for node in ("load_company", "financial_data_agent", "bull_case_agent"):
                await on_node(node)
            return {"status": "completed", "draft_report_id": None, "agent_run_id": None}

        async def generate_final_report(db, **kwargs):
            on_phase = kwargs["on_phase"]
            for phase in (
                research_job.PHASE_EVIDENCE_INGESTION,
                research_job.PHASE_COUNCIL_AGENTS,
                research_job.PHASE_REPORT_ASSEMBLY,
            ):
                await on_phase(phase)

            class _R:
                def __init__(self):
                    self.report_id = report_id
                    self.llm_used = True

            return _R()

        async def on_stage(stage: str) -> None:
            seen.append(stage)

        result = await svc.execute_company_research(
            None,
            company,
            provider_name="free_real",
            on_node=lambda n: _noop(),
            on_stage=on_stage,
            run_analysis=run_analysis,
            generate_final_report=generate_final_report,
        )
        assert result["analysis_report_id"] == report_id
        # In order, as they happened — not stamped at the end.
        assert seen == [
            research_job.STAGE_DOCUMENT_INGESTION,
            research_job.STAGE_COUNCIL_ANALYSIS,
            research_job.STAGE_REPORT_ASSEMBLY,
        ]

    async def test_no_phase_callback_is_passed_when_nobody_wants_progress(self):
        """Every injected fake in the suite must not have to grow a parameter."""
        import uuid

        from app.models.company import Company
        from app.services import company_research_service as svc

        received: dict = {}

        async def run_analysis(db, **kwargs):
            return {"status": "completed", "draft_report_id": None, "agent_run_id": None}

        async def generate_final_report(db, **kwargs):
            received["keys"] = set(kwargs)

            class _R:
                report_id = uuid.uuid4()
                llm_used = False

            return _R()

        await svc.execute_company_research(
            None,
            Company(id=uuid.uuid4(), name="X", ticker="X", exchange="Y"),
            provider_name="free_real",
            run_analysis=run_analysis,
            generate_final_report=generate_final_report,
        )
        assert "on_phase" not in received["keys"]


async def _noop() -> None:
    return None
