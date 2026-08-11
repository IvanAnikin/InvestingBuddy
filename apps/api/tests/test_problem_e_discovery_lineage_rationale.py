"""Problem E — discovery rationale not surfaced despite exact linkage.

Proven defect: a staging LVMH/MC report, launched from and exactly linked to a
``DiscoveryCandidate`` (linkage strong enough that
``GET /api/v1/discovery-runs/{run_id}/field-review-eligibility`` correctly
counts it as eligible), nonetheless showed "No screening candidate is linked —
discovery rationale is not available" in the report body.

Root cause: the report-body text (``_build_research_memo``'s ``why_surfaced``
block) only ever read ``report_content["discovery_rationale"]`` — built from
the legacy, unrelated ``ScreeningCandidate`` model — never
``report_content["discovery_lineage"]``, which IS built from the real
``DiscoveryCandidate``/``DiscoveryRun`` FK data for a candidate-launched
analysis (``market_discovery_service.run_candidate_analysis`` deliberately
passes ``candidate=None`` into the ``ScreeningCandidate``-typed parameter, to
avoid an ``AttributeError``, but threads the real lineage through separately
as ``discovery_lineage``).

Fix: ``why_surfaced`` now falls back to ``discovery_lineage`` — exact FK data
only, never ticker/name matching, never another run's candidate for the same
company — whenever ``discovery_rationale`` is unavailable but
``discovery_lineage`` carries real linkage data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

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
from app.models.company import Company
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.report import Report
from app.services.final_report_generator import (
    _build_discovery_lineage_from_dict,
    _build_discovery_rationale,
    _build_research_memo,
)
from app.services.llm.schemas import CouncilResult

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _minimal_report_content(
    *, discovery_rationale: dict[str, Any], discovery_lineage: dict[str, Any] | None
) -> dict[str, Any]:
    """The bare minimum ``report_content`` shape ``_build_research_memo`` reads.

    Every other section defaults to ``{}``/``[]`` inside the memo builder, so
    this stays honest-empty except for the two sections under test.
    """
    return {
        "company_identity": {},
        "discovery_rationale": discovery_rationale,
        "discovery_lineage": _build_discovery_lineage_from_dict(discovery_lineage),
        "data_availability_summary": {},
        "source_quality_review": {},
        "missing_information": {},
        "financial_snapshot": {},
        "bull_case": {},
        "bear_case": {},
        "risk_analysis": {},
        "committee_chair_summary": {},
        "human_review_checklist": [],
        "source_citation_appendix": {},
        "news_catalyst_discovery": {},
    }


def _lineage_dict(
    *,
    discovery_run_id: uuid.UUID,
    candidate_id: uuid.UUID,
    ticker: str = "MC",
    exchange: str = "EPA",
    rank: int = 1,
    candidate_score: float = 82.5,
    candidate_score_grade: str = "high_internal_interest",
    score_explanation: str | None = "Strong momentum + catalyst coverage.",
    thesis_relevance_score: float | None = 0.91,
    thesis_match_json: dict[str, Any] | None = None,
    thesis_text: str | None = "European luxury goods with pricing power.",
) -> dict[str, Any]:
    return {
        "discovery_run_id": str(discovery_run_id),
        "discovery_candidate_id": str(candidate_id),
        "ticker": ticker,
        "exchange": exchange,
        "rank": rank,
        "candidate_score": candidate_score,
        "candidate_score_grade": candidate_score_grade,
        "score_explanation": score_explanation,
        "thesis_relevance_score": thesis_relevance_score,
        "thesis_match_json": thesis_match_json,
        "thesis_text": thesis_text,
    }


# ===========================================================================
# 1. discovery_lineage is authoritative when discovery_rationale is unavailable
# ===========================================================================
def test_why_surfaced_uses_discovery_lineage_when_rationale_unavailable() -> None:
    """The exact MC defect: discovery_rationale.available=False (candidate-
    launched analysis, ``candidate=None`` passed to the generator) but
    discovery_lineage carries real DiscoveryCandidate FK data -> the surfaced
    rationale must use the lineage data, not the "not available" message."""
    run_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    report_content = _minimal_report_content(
        discovery_rationale=_build_discovery_rationale(None),
        discovery_lineage=_lineage_dict(discovery_run_id=run_id, candidate_id=candidate_id),
    )
    assert report_content["discovery_rationale"]["available"] is False

    memo = _build_research_memo(
        report_content, CouncilResult.disabled(), source_tier="T2_regulator_or_gov"
    )
    why_surfaced = memo["why_surfaced"]

    assert why_surfaced["available"] is True
    assert why_surfaced["source"] == "discovery_run_candidate"
    assert why_surfaced["discovery_run_id"] == str(run_id)
    assert why_surfaced["discovery_candidate_id"] == str(candidate_id)
    assert why_surfaced["candidate_score"]["value"] == 82.5
    assert why_surfaced["candidate_score_grade"]["value"] == "high_internal_interest"
    reasons = why_surfaced["discovery_reasons"]["value"]
    assert any("European luxury goods" in r for r in reasons)
    assert "note" not in why_surfaced or "not available" not in str(
        why_surfaced.get("note", {}).get("value", "")
    )


def test_why_surfaced_prefers_discovery_rationale_when_both_available() -> None:
    """discovery_rationale (legacy ScreeningCandidate path) stays authoritative
    when it IS available — discovery_lineage is only the fallback."""
    from types import SimpleNamespace

    candidate = SimpleNamespace(
        id=uuid.uuid4(),
        ticker="MC",
        exchange="EPA",
        candidate_status="screened_in",
        source_tier="T2_regulator_or_gov",
        data_quality="B_single_credible",
        discovery_reasons_json=["Screened via curated luxury seed universe"],
        available_data_json=["price_history"],
        missing_data_json=[],
        warnings_json=[],
    )
    report_content = _minimal_report_content(
        discovery_rationale=_build_discovery_rationale(candidate),
        discovery_lineage=_lineage_dict(
            discovery_run_id=uuid.uuid4(), candidate_id=uuid.uuid4()
        ),
    )
    memo = _build_research_memo(
        report_content, CouncilResult.disabled(), source_tier="T2_regulator_or_gov"
    )
    why_surfaced = memo["why_surfaced"]
    assert why_surfaced["available"] is True
    assert "source" not in why_surfaced  # legacy shape has no "source" marker
    assert why_surfaced["discovery_reasons"]["value"] == [
        "Screened via curated luxury seed universe"
    ]


# ===========================================================================
# 2. Neither source available -> honest "not available" message unchanged
# ===========================================================================
def test_why_surfaced_stays_not_available_with_no_linkage_at_all() -> None:
    report_content = _minimal_report_content(
        discovery_rationale=_build_discovery_rationale(None),
        discovery_lineage=None,
    )
    memo = _build_research_memo(
        report_content, CouncilResult.disabled(), source_tier=None
    )
    why_surfaced = memo["why_surfaced"]
    assert why_surfaced["available"] is False
    assert "not available" in why_surfaced["note"]["value"]
    assert "No screening candidate is linked" in why_surfaced["note"]["value"]


# ===========================================================================
# 3. Cross-run isolation — two DiscoveryCandidate rows, same company, two runs
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


async def test_discovery_lineage_never_leaks_a_sibling_runs_candidate(session) -> None:
    """Two DiscoveryCandidate rows for the SAME company under two DIFFERENT
    discovery runs, each linked (via analysis_report_id) to its own report.
    Building report A's why_surfaced from report A's own discovery_lineage
    must reference ONLY run A's candidate id — never run B's — and vice versa.
    """
    company = Company(
        id=uuid.uuid4(),
        ticker="MC",
        exchange="EPA",
        name="LVMH Moet Hennessy Louis Vuitton SE",
        country="FR",
        sector="Consumer Cyclical",
        industry="Luxury Goods",
        status="new",
    )
    session.add(company)
    await session.flush()

    report_a = Report(
        id=uuid.uuid4(),
        title="Analysis Council Draft — MC (run A)",
        slug=f"mc-a-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# draft",
        company_id=company.id,
        human_review_required=True,
    )
    report_b = Report(
        id=uuid.uuid4(),
        title="Analysis Council Draft — MC (run B)",
        slug=f"mc-b-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# draft",
        company_id=company.id,
        human_review_required=True,
    )
    session.add_all([report_a, report_b])
    await session.flush()

    run_a = DiscoveryRun(
        id=uuid.uuid4(),
        status="completed",
        provider_name="free_real",
        mode="thesis",
        universe_source="thesis_generated",
        universe_count=10,
        thesis_text="European luxury goods with durable pricing power.",
    )
    run_b = DiscoveryRun(
        id=uuid.uuid4(),
        status="completed",
        provider_name="free_real",
        mode="thesis",
        universe_source="thesis_generated",
        universe_count=8,
        thesis_text="Global travel-retail recovery plays.",
    )
    session.add_all([run_a, run_b])
    await session.flush()

    candidate_a = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run_a.id,
        ticker="MC",
        exchange="EPA",
        company_name="LVMH",
        rank=1,
        candidate_score=88.0,
        candidate_score_grade="high_internal_interest",
        score_explanation="Run A: pricing power thesis match.",
        thesis_relevance_score=0.93,
        analysis_report_id=report_a.id,
    )
    candidate_b = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run_b.id,
        ticker="MC",
        exchange="EPA",
        company_name="LVMH",
        rank=4,
        candidate_score=61.0,
        candidate_score_grade="medium_internal_interest",
        score_explanation="Run B: travel-retail recovery thesis match.",
        thesis_relevance_score=0.52,
        analysis_report_id=report_b.id,
    )
    session.add_all([candidate_a, candidate_b])
    await session.flush()
    await session.commit()

    # Rebuild each report's OWN lineage exactly as
    # ``market_discovery_service.run_candidate_analysis`` does — off that
    # report's own DiscoveryCandidate + parent DiscoveryRun ONLY.
    async def _lineage_for(candidate: DiscoveryCandidate) -> dict[str, Any]:
        run = (
            await session.execute(
                select(DiscoveryRun).where(DiscoveryRun.id == candidate.discovery_run_id)
            )
        ).scalar_one()
        return {
            "discovery_run_id": str(candidate.discovery_run_id),
            "discovery_candidate_id": str(candidate.id),
            "ticker": candidate.ticker,
            "exchange": candidate.exchange,
            "rank": candidate.rank,
            "candidate_score": candidate.candidate_score,
            "candidate_score_grade": candidate.candidate_score_grade,
            "score_explanation": candidate.score_explanation,
            "thesis_relevance_score": candidate.thesis_relevance_score,
            "thesis_match_json": candidate.thesis_match_json,
            "thesis_text": run.thesis_text if run else None,
        }

    lineage_a = await _lineage_for(candidate_a)
    lineage_b = await _lineage_for(candidate_b)

    content_a = _minimal_report_content(
        discovery_rationale=_build_discovery_rationale(None), discovery_lineage=lineage_a
    )
    content_b = _minimal_report_content(
        discovery_rationale=_build_discovery_rationale(None), discovery_lineage=lineage_b
    )

    memo_a = _build_research_memo(content_a, CouncilResult.disabled(), source_tier=None)
    memo_b = _build_research_memo(content_b, CouncilResult.disabled(), source_tier=None)

    why_a = memo_a["why_surfaced"]
    why_b = memo_b["why_surfaced"]

    assert why_a["discovery_run_id"] == str(run_a.id)
    assert why_a["discovery_candidate_id"] == str(candidate_a.id)
    assert why_a["discovery_run_id"] != str(run_b.id)
    assert why_a["discovery_candidate_id"] != str(candidate_b.id)
    assert "Run B" not in " ".join(why_a["discovery_reasons"]["value"])

    assert why_b["discovery_run_id"] == str(run_b.id)
    assert why_b["discovery_candidate_id"] == str(candidate_b.id)
    assert why_b["discovery_run_id"] != str(run_a.id)
    assert why_b["discovery_candidate_id"] != str(candidate_a.id)
    assert "Run A" not in " ".join(why_b["discovery_reasons"]["value"])
