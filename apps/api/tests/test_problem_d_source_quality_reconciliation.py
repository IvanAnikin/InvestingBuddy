"""Problem D — stale source-quality reconciliation.

Proven defect: a real staging report (Compagnie Financiere Richemont) had real
extracted T1 official-document evidence, T1 excerpts, and Council claims citing
that evidence — while the report's deterministic quality surfaces still said
"strong sources = 0", "T6 only", "no T1/T2 source backs a claim". These are
stale.

Root cause (two independent stale-snapshot bugs):
  1. ``source_quality_summary`` is computed ONCE, early, at workflow node 6
     (``source_quality_agent``) — BEFORE citations exist, BEFORE the council
     runs, BEFORE document ingestion.
  2. The ``human_review_checklist`` T1/T2 item is recomputed at the end of
     ``_generate_and_save``, but the ``citations`` variable it used was the
     STALE, pre-council snapshot (loaded before ``maybe_run_council`` ran).

Fix: ``final_report_generator.py`` now recomputes source-quality +ǃ the T1/T2
checklist item from FRESH evidence (real DB citations + this run's council
claim-cited evidence) after the council runs — while STRICTLY excluding
metadata-only references (no extracted content) from ever counting as
"strong"/"T1/T2-backed".

This file covers both:
  * fast, DB-free unit tests directly on the new helper functions
    (``_council_evidence_source_tiers``, ``_fresh_citation_source_tiers``,
    ``_recompute_fresh_source_quality_summary``, ``_has_t1_t2_evidence``); and
  * one full end-to-end test against a real in-memory SQLite async DB driving
    ``FinalReportGeneratorService.generate_from_company`` — the exact path the
    staging CFR report was generated through — proving the SAVED report no
    longer carries a stale "strong_sources_count = 0" / "no T1/T2" state when
    real T1 evidence backs a council claim, while a metadata-only-only
    scenario still HONESTLY reports insufficient T1/T2 evidence (no
    stale-positive introduced by the fix either).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import backtest as _backtest  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import financial_snapshot as _financial_snapshot  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import review_event as _review_event  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.report import Report
from app.services import final_report_generator
from app.services.final_report_generator import (
    FinalReportGeneratorService,
    _council_evidence_source_tiers,
    _fresh_citation_source_tiers,
    _has_t1_t2_evidence,
    _recompute_fresh_source_quality_summary,
)
from app.services.llm.schemas import (
    AgentKeyPoint,
    CouncilAgentOutput,
    CouncilResult,
    PersistableEvidence,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Lightweight stand-ins for ORM rows (unit tests read only .source_tier /
# .data_quality, so a SimpleNamespace is a faithful, cheap Citation stand-in).
# ---------------------------------------------------------------------------


def _citation_row(*, source_tier: str, data_quality: str | None) -> SimpleNamespace:
    return SimpleNamespace(source_tier=source_tier, data_quality=data_quality)


def _pe(
    alias: str,
    *,
    source_tier: str,
    excerpt: str | None,
    data_quality: str | None,
) -> PersistableEvidence:
    return PersistableEvidence(
        uid=uuid.uuid4().hex,
        alias=alias,
        source_tier=source_tier,
        content_tier=source_tier,
        source_type="company_ir_annual_report" if excerpt else "company_ir_index",
        title="Richemont evidence",
        url="https://www.richemont.com/ar-2024.pdf",
        excerpt=excerpt,
        data_quality=data_quality,
        fields_supported=[],
    )


def _council_with(
    persistable: list[PersistableEvidence], cited_aliases: list[str]
) -> CouncilResult:
    cr = CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-council",
        evidence_item_count=len(persistable),
        agents=[
            CouncilAgentOutput(
                agent_name="financial_analyst",
                summary="Council claim citing extracted evidence.",
                key_points=[
                    AgentKeyPoint(
                        claim="Revenue figures were confirmed against the primary filing.",
                        citation_ids=cited_aliases,
                    )
                ],
            )
        ],
        persistable_evidence=persistable,
    )
    cr.recount()
    return cr


# ===========================================================================
# 1. Unit tests — _council_evidence_source_tiers / _fresh_citation_source_tiers
# ===========================================================================
class TestCouncilEvidenceSourceTiers:
    def test_extracted_and_cited_t1_evidence_counts(self) -> None:
        """T1 extracted AND actually cited by a claim -> counts as real evidence."""
        pe = _pe(
            "E1",
            source_tier="T1_primary_filing",
            excerpt="Total revenue for FY2024 was EUR 20,616 million.",
            data_quality="B_single_credible",
        )
        council = _council_with([pe], cited_aliases=["E1"])
        tiers = _council_evidence_source_tiers(council)
        assert tiers == ["T1_primary_filing"]

    def test_extracted_but_never_cited_evidence_is_excluded(self) -> None:
        """T1 extracted but NEVER cited by any claim -> does not back a claim,
        so it is excluded here (extraction-progress state is tracked
        separately by the appendix's ``extracted_evidence_count``, not by this
        claim-cited tier list)."""
        pe = _pe(
            "E1",
            source_tier="T1_primary_filing",
            excerpt="Segment margin detail not referenced by any agent.",
            data_quality="B_single_credible",
        )
        council = _council_with([pe], cited_aliases=[])  # no claim cites E1
        tiers = _council_evidence_source_tiers(council)
        assert tiers == []

    def test_metadata_only_t1_reference_never_counts_even_if_cited(self) -> None:
        """A T1 METADATA-ONLY reference (URL, no extracted content) must NOT
        count as a strong source, even when a council claim cites it."""
        pe = _pe(
            "E1",
            source_tier="T1_primary_filing",
            excerpt=None,
            data_quality="metadata_only",
        )
        council = _council_with([pe], cited_aliases=["E1"])
        tiers = _council_evidence_source_tiers(council)
        assert tiers == []

    def test_fresh_citation_source_tiers_excludes_metadata_only_db_citation(
        self,
    ) -> None:
        citations = [
            _citation_row(source_tier="T1_primary_filing", data_quality="metadata_only"),
            _citation_row(source_tier="T5_api_aggregator", data_quality="B_single_credible"),
        ]
        council = CouncilResult.disabled()
        tiers = _fresh_citation_source_tiers(citations, council)
        assert "T1_primary_filing" not in tiers
        assert "T5_api_aggregator" in tiers

    def test_fresh_citation_source_tiers_includes_real_content_bearing_citation(
        self,
    ) -> None:
        citations = [
            _citation_row(source_tier="T1_primary_filing", data_quality="B_single_credible"),
        ]
        council = CouncilResult.disabled()
        tiers = _fresh_citation_source_tiers(citations, council)
        assert tiers == ["T1_primary_filing"]

    def test_fresh_citation_source_tiers_combines_db_and_council_evidence(self) -> None:
        citations = [
            _citation_row(source_tier="T5_api_aggregator", data_quality="B_single_credible"),
        ]
        pe = _pe(
            "E1",
            source_tier="T1_primary_filing",
            excerpt="Total revenue for FY2024 was EUR 20,616 million.",
            data_quality="B_single_credible",
        )
        council = _council_with([pe], cited_aliases=["E1"])
        tiers = _fresh_citation_source_tiers(citations, council)
        assert "T1_primary_filing" in tiers
        assert "T5_api_aggregator" in tiers


# ===========================================================================
# 2. Unit tests — _recompute_fresh_source_quality_summary
# ===========================================================================
class TestRecomputeFreshSourceQualitySummary:
    def test_real_t1_citation_produces_nonzero_strong_sources_count(self) -> None:
        """The exact regression: strong_sources_count must never stay 0 once a
        real T1 citation backs the report."""
        citations = [
            _citation_row(source_tier="T1_primary_filing", data_quality="B_single_credible"),
        ]
        council = CouncilResult.disabled()
        stale_base = {
            "overall_source_quality": "insufficient",
            "strong_sources_count": 0,
            "weak_sources_count": 0,
        }
        fresh = _recompute_fresh_source_quality_summary(
            {"is_mock": False, "provider_metadata": {"provider_name": "unknown"}},
            citations,
            council,
            stale_base,
        )
        assert fresh["strong_sources_count"] >= 1
        assert fresh["overall_source_quality"] != "insufficient"

    def test_metadata_only_citation_alone_keeps_honest_zero_strong_sources(self) -> None:
        """A report with ONLY a metadata-only T1 reference must stay honest —
        no stale-positive introduced by the fix."""
        citations = [
            _citation_row(source_tier="T1_primary_filing", data_quality="metadata_only"),
        ]
        council = CouncilResult.disabled()
        fresh = _recompute_fresh_source_quality_summary(
            {"is_mock": True, "provider_metadata": {"provider_name": "mock"}},
            citations,
            council,
            {},
        )
        assert fresh["strong_sources_count"] == 0


# ===========================================================================
# 3. Unit tests — _has_t1_t2_evidence (human_review_checklist T1/T2 item)
# ===========================================================================
class TestHasT1T2Evidence:
    def test_metadata_only_citation_does_not_satisfy_t1_t2(self) -> None:
        citations = [
            _citation_row(source_tier="T1_primary_filing", data_quality="metadata_only"),
        ]
        assert _has_t1_t2_evidence(None, citations) is False

    def test_real_content_bearing_citation_satisfies_t1_t2(self) -> None:
        citations = [
            _citation_row(source_tier="T2_regulator_or_gov", data_quality="B_single_credible"),
        ]
        assert _has_t1_t2_evidence(None, citations) is True

    def test_extra_source_tiers_from_council_satisfy_t1_t2(self) -> None:
        """Pre-council ``citations`` alone is stale/empty; the council's
        FRESH claim-cited evidence (not yet reloadable from the DB) must still
        flip the checklist item."""
        assert (
            _has_t1_t2_evidence(
                None, [], primary_facts=None, extra_source_tiers=["T1_primary_filing"]
            )
            is True
        )

    def test_no_evidence_anywhere_stays_false(self) -> None:
        assert _has_t1_t2_evidence(None, [], primary_facts=None, extra_source_tiers=[]) is False


# ===========================================================================
# 4. End-to-end — real in-memory SQLite DB, generate_from_company (CFR-like)
# ===========================================================================
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
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    return True


async def _add_company(session, *, ticker: str, exchange: str, name: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        name=name,
        country="CH",
        sector="Consumer Cyclical",
        industry="Luxury Goods",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_completed_run(session) -> AgentRun:
    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        workflow_version="1.0.0",
        status="completed",
        started_at=_utcnow(),
        trigger_type="manual",
    )
    session.add(run)
    await session.flush()
    return run


async def _add_draft_report(session, *, company_id, agent_run_id) -> Report:
    report = Report(
        id=uuid.uuid4(),
        title="Analysis Council Draft — CFR",
        slug=f"cfr-draft-{uuid.uuid4().hex[:12]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# Analysis Council Draft (no envelope)",
        created_by_agent_run_id=agent_run_id,
        company_id=company_id,
        human_review_required=True,
        created_at=_utcnow(),
    )
    session.add(report)
    await session.flush()
    return report


def _patch_council(monkeypatch, council: CouncilResult):
    async def _fake(*args, **kwargs):
        return council

    monkeypatch.setattr(final_report_generator, "maybe_run_council", _fake)


def _cfr_council_with_real_t1_evidence() -> CouncilResult:
    """CFR-like council: one T1 excerpt extracted AND cited (real evidence),
    one T1 excerpt extracted but NEVER cited (extraction-progress only), and
    one T1 METADATA-ONLY reference cited by a different claim (must never
    count as strong)."""
    e1 = _pe(
        "E1",
        source_tier="T1_primary_filing",
        excerpt="Total revenue for FY2024 was EUR 20,616 million.",
        data_quality="B_single_credible",
    )
    e2 = _pe(
        "E2",
        source_tier="T1_primary_filing",
        excerpt="Segment-level detail not referenced by any agent this run.",
        data_quality="B_single_credible",
    )
    e3 = _pe(
        "E3",
        source_tier="T1_primary_company_source",
        excerpt=None,
        data_quality="metadata_only",
    )
    cr = CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-council",
        evidence_item_count=3,
        agents=[
            CouncilAgentOutput(
                agent_name="financial_analyst",
                summary="Revenue confirmed against the FY2024 annual report.",
                key_points=[
                    AgentKeyPoint(
                        claim="Revenue was EUR 20,616 million in FY2024",
                        citation_ids=["E1"],
                        confidence="high",
                    )
                ],
            ),
            CouncilAgentOutput(
                agent_name="source_quality_critic",
                summary="A verified issuer investor-relations index page was located.",
                key_points=[
                    AgentKeyPoint(
                        claim="A verified issuer IR index page was located.",
                        citation_ids=["E3"],
                    )
                ],
            ),
        ],
        persistable_evidence=[e1, e2, e3],
    )
    cr.recount()
    return cr


def _cfr_council_metadata_only() -> CouncilResult:
    """CFR-like council with ONLY a metadata-only T1 reference — the honest
    "insufficient T1/T2" state must be preserved (no stale-positive)."""
    e1 = _pe(
        "E1",
        source_tier="T1_primary_company_source",
        excerpt=None,
        data_quality="metadata_only",
    )
    cr = CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-council",
        evidence_item_count=1,
        agents=[
            CouncilAgentOutput(
                agent_name="source_quality_critic",
                summary="A verified issuer investor-relations index page was located.",
                key_points=[
                    AgentKeyPoint(
                        claim="A verified issuer IR index page was located.",
                        citation_ids=["E1"],
                    )
                ],
            ),
        ],
        persistable_evidence=[e1],
    )
    cr.recount()
    return cr


def _parse_report_content(report: Report) -> dict:
    md = report.content_markdown or ""
    blocks = re.findall(r"```json\s*(.*?)\s*```", md, re.DOTALL)
    assert blocks, "saved final report has no JSON block"
    return json.loads(blocks[-1])


def _t1_t2_checklist_item(content: dict) -> dict:
    for item in content["human_review_checklist"]:
        if item["item"].startswith("Data quality: T1/T2 sources present"):
            return item
    raise AssertionError("T1/T2 checklist item not found")


async def test_cfr_real_t1_evidence_no_longer_shows_stale_zero_strong_sources(
    session, flag_on, monkeypatch
) -> None:
    """The proven defect, reproduced end to end: a report with real extracted
    T1 evidence CITED by a council claim must no longer show
    strong_sources_count = 0 / "T6 only" / "no T1/T2 source backs a claim"."""
    _patch_council(monkeypatch, _cfr_council_with_real_t1_evidence())
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")
    run = await _add_completed_run(session)
    await _add_draft_report(session, company_id=cfr.id, agent_run_id=run.id)
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, cfr.id)
    final = (
        await session.execute(select(Report).where(Report.id == resp.report_id))
    ).scalar_one()
    content = _parse_report_content(final)

    sqr = content["source_quality_review"]
    assert sqr["strong_sources_count"] >= 1
    assert sqr["overall_source_quality"]["value"] not in ("insufficient", "weak")

    checklist_item = _t1_t2_checklist_item(content)
    assert checklist_item["completed"] is True
    assert checklist_item["note"] is None


async def test_cfr_metadata_only_only_evidence_stays_honestly_insufficient(
    session, flag_on, monkeypatch
) -> None:
    """No stale-positive regression: with ONLY a metadata-only T1 reference,
    the checklist and source-quality review must still honestly report no
    real T1/T2 evidence backs a claim."""
    _patch_council(monkeypatch, _cfr_council_metadata_only())
    cfr = await _add_company(session, ticker="CFR2", exchange="SW", name="Richemont SA 2")
    run = await _add_completed_run(session)
    await _add_draft_report(session, company_id=cfr.id, agent_run_id=run.id)
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, cfr.id)
    final = (
        await session.execute(select(Report).where(Report.id == resp.report_id))
    ).scalar_one()
    content = _parse_report_content(final)

    sqr = content["source_quality_review"]
    assert sqr["strong_sources_count"] == 0

    checklist_item = _t1_t2_checklist_item(content)
    assert checklist_item["completed"] is False
    assert "no T1/T2" in (checklist_item["note"] or "")
