"""Final-report REGENERATION must preserve the source report's EXACT lineage.

THE DEFECT
==========
Regenerating the accepted staging Pandora report
(``a17f94b2-987c-4ccd-8a38-81b431cd92aa`` -> ``06b2f640-2991-4714-8ce1-186d3c1e5295``,
both on agent run ``f472b983-ea3c-4afb-b6da-3bac0dbcb06c``) produced a report
that had lost, in its BODY:

    legal_name:            "PNDORA"   (was "Pandora A/S")
    Why It Surfaced:       unavailable
    Discovery Rationale:   "No screening candidate is linked"
    discovery_run_id:      dropped    (was 48837187-…)
    candidate_id:          dropped    (was 34ac619a-…)

while the same regenerated row still carried the right ``company_id`` and
``created_by_agent_run_id`` in its metadata. The linkage was never missing —
``generate_from_report`` simply never asked for it:

  * ``candidate=None`` was hardcoded, and no ``discovery_lineage`` was passed,
    so both discovery sections rendered "not available";
  * ``company_record`` was resolved ONLY when the re-parsed workflow state
    carried no company snapshot — and after the 32D2d lineage-draft recovery it
    always does, so the snapshot's ``legal_name`` was used verbatim. For a venue
    SEC EDGAR does not cover that value is deliberately the TICKER
    (``free_real_provider._not_sourced_profile``), which is exactly why
    ``_build_company_identity`` has a company-record repair — it just never had
    a company record to repair from.

THESE TESTS
===========
Run against a REAL in-memory SQLite async database, so the lineage really is
resolved through WHERE clauses over real FK columns rather than through a mock
that answers every query the same way. The LLM council is patched off (offline).

Two issuer fixtures, both non-US, from two different jurisdictions — PNDORA
(Nasdaq Copenhagen) and MONC (Borsa Italiana) — so nothing here can pass by
special-casing one ticker.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

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
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.report import Report
from app.services import final_report_generator as frg
from app.services.final_report_generator import (
    FINAL_REPORT_VERSION,
    FinalReportGeneratorService,
)
from app.services.llm.schemas import CouncilResult
from app.services.report_lineage import (
    AmbiguousReportLineageError,
    resolve_display_company_name,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Issuer fixtures — two jurisdictions, neither of them US
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issuer:
    ticker: str
    exchange: str
    country: str
    legal_name: str
    currency: str
    thesis: str


PNDORA = Issuer(
    ticker="PNDORA",
    exchange="CO",
    country="Denmark",
    legal_name="Pandora A/S",
    currency="DKK",
    thesis="European luxury goods companies",
)
MONC = Issuer(
    ticker="MONC",
    exchange="MI",
    country="Italy",
    legal_name="Moncler S.p.A.",
    currency="EUR",
    thesis="European premium outerwear brands",
)

ALL_ISSUERS = [PNDORA, MONC]


def _snapshot(issuer: Issuer) -> dict[str, Any]:
    """A workflow snapshot whose ``legal_name`` IS the ticker.

    This is not a contrived fixture: ``free_real_provider._not_sourced_profile``
    sets ``legal_name = ticker`` on purpose for any venue SEC EDGAR does not
    cover, rather than guessing a company from an unrelated SEC index entry.
    Every non-US issuer in this product reaches the final-report generator with
    this stub in its snapshot.
    """
    return {
        "is_mock": False,
        "source_tier": "T5_api_aggregator",
        "company_identity": {
            "legal_name": issuer.ticker,
            "ticker": issuer.ticker,
            "exchange": issuer.exchange,
            "country_domicile": issuer.country,
        },
        "profile": {
            "sector": "Consumer Discretionary",
            "reporting_currency": issuer.currency,
        },
        "price_history_summary": {
            "available": True,
            "latest_close": 100.0,
            "currency": issuer.currency,
            "data_points_count": 250,
            "source_tier": "T5_api_aggregator",
            "provider_name": "price_only_aggregator",
        },
        "provider_metadata": {
            "provider_name": "price_only_aggregator",
            "source_tier": "T5_api_aggregator",
            "is_mock": False,
        },
        "missing_fields": ["identity.isin", "identity.lei"],
    }


def _workflow_state(issuer: Issuer) -> dict[str, Any]:
    from app.agents.analysis_council.bull_case_agent import (
        bull_case_output_to_dict,
        run_bull_case_agent,
    )
    from app.agents.research_team.financial_data_agent import (
        financial_data_agent_output_to_dict,
        run_financial_data_agent,
    )
    from app.agents.research_team.source_quality_agent import (
        run_source_quality_agent,
        source_quality_output_to_dict,
    )

    snap = _snapshot(issuer)
    fds = financial_data_agent_output_to_dict(run_financial_data_agent(snap))
    sqs = source_quality_output_to_dict(run_source_quality_agent(snap))
    return {
        "company_snapshot": snap,
        "financial_data_summary": fds,
        "source_quality_summary": sqs,
        "research_completeness_summary": {
            "blocking_gaps": [],
            "next_research_tasks": [],
        },
        "bull_case_summary": bull_case_output_to_dict(
            run_bull_case_agent(snap, fds, sqs, {})
        ),
        "schema_validation_result": {"is_valid": True, "errors": [], "warnings": []},
    }


# ---------------------------------------------------------------------------
# Real async SQLite fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def lineage_persistence_on(monkeypatch):
    """``company_id`` / ``created_by_agent_run_id`` are stamped behind this flag.

    It is the same flag production staging runs with; with it off the stamped
    lineage columns are NULL by design and there is nothing to preserve.
    """
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )


# ---------------------------------------------------------------------------
# Seeding — one accepted, discovery-launched final report per issuer
# ---------------------------------------------------------------------------


@dataclass
class Seeded:
    issuer: Issuer
    company: Company
    agent_run: AgentRun
    discovery_run: DiscoveryRun
    candidate: DiscoveryCandidate
    draft: Report
    final: Report

    @property
    def lineage_dict(self) -> dict[str, Any]:
        return {
            "discovery_run_id": str(self.discovery_run.id),
            "discovery_candidate_id": str(self.candidate.id),
            "ticker": self.issuer.ticker,
            "exchange": self.issuer.exchange,
            "rank": self.candidate.rank,
            "candidate_score": self.candidate.candidate_score,
            "candidate_score_grade": self.candidate.candidate_score_grade,
            "score_explanation": self.candidate.score_explanation,
            "thesis_relevance_score": self.candidate.thesis_relevance_score,
            "thesis_match_json": self.candidate.thesis_match_json,
            "thesis_text": self.discovery_run.thesis_text,
        }


async def _seed(
    session,
    issuer: Issuer,
    *,
    link_candidate_to: str = "final",
    persist_lineage_on_final: bool = True,
) -> Seeded:
    """Seed the shape a discovery-launched, accepted report really has.

    ``link_candidate_to`` mirrors the two real linkage states: the candidate's
    ``analysis_report_id`` points at the generated FINAL report (the ordinary
    "Run Full Analysis" outcome) or, when final-report routing fell back, at the
    Phase-9 analysis DRAFT.
    """
    company = Company(
        id=uuid.uuid4(),
        ticker=issuer.ticker,
        exchange=issuer.exchange,
        name=issuer.legal_name,
        country=issuer.country,
        sector="Consumer Discretionary",
        industry="Luxury Goods",
        status="new",
    )
    session.add(company)
    await session.flush()

    agent_run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        status="completed",
        started_at=_utcnow(),
    )
    session.add(agent_run)
    await session.flush()

    discovery_run = DiscoveryRun(
        id=uuid.uuid4(),
        status="completed",
        provider_name="free_real",
        mode="thesis",
        universe_source="thesis_generated",
        universe_count=12,
        thesis_text=issuer.thesis,
    )
    session.add(discovery_run)
    await session.flush()

    draft = Report(
        id=uuid.uuid4(),
        title=f"Analysis Council Draft — {issuer.ticker}",
        slug=f"draft-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        human_review_required=True,
        final_report_version=None,
        company_id=company.id,
        created_by_agent_run_id=agent_run.id,
        content_markdown="# Draft\n\n```json\n"
        + json.dumps(_workflow_state(issuer), default=str)
        + "\n```\n",
    )
    session.add(draft)
    await session.flush()

    final = Report(
        id=uuid.uuid4(),
        title=f"LLM Council Analysis Draft — {issuer.ticker} — {issuer.ticker}",
        slug=f"final-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="approved",
        human_review_required=True,
        final_report_version=FINAL_REPORT_VERSION,
        company_id=company.id,
        created_by_agent_run_id=agent_run.id,
        # A FINAL report's markdown carries RENDERED SECTIONS, not the workflow
        # state envelope — the whole reason 32D2d recovers state from the draft.
        content_markdown="# FINAL\n\n```json\n"
        + json.dumps(
            {
                "company_identity": {
                    "type": "company_identity",
                    "legal_name": {"value": issuer.legal_name},
                },
                "financial_snapshot": {"type": "financial_snapshot"},
                "bull_case": {"type": "bull_case", "available": True},
            },
            default=str,
        )
        + "\n```\n",
    )
    session.add(final)
    await session.flush()

    candidate = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=discovery_run.id,
        ticker=issuer.ticker,
        exchange=issuer.exchange,
        company_name=issuer.ticker,
        legal_name=issuer.legal_name,
        country=issuer.country,
        sector="Consumer Discretionary",
        rank=6,
        candidate_score=8.9,
        candidate_score_grade="low_internal_interest",
        score_explanation=(
            "Internal prioritization score only. It ranks a candidate for "
            "internal human research triage and implies no investment action."
        ),
        thesis_relevance_score=0.77,
        agent_run_id=agent_run.id,
        analysis_report_id=(final.id if link_candidate_to == "final" else draft.id),
    )
    session.add(candidate)
    await session.flush()

    seeded = Seeded(
        issuer=issuer,
        company=company,
        agent_run=agent_run,
        discovery_run=discovery_run,
        candidate=candidate,
        draft=draft,
        final=final,
    )
    if persist_lineage_on_final:
        # Every final report this generator saves records the lineage it was
        # built with under source_summary_json.
        final.source_summary_json = {
            "total_sources": 0,
            "total_citations": 0,
            "discovery_lineage": seeded.lineage_dict,
        }
    await session.commit()
    return seeded


# ---------------------------------------------------------------------------
# Regeneration driver
# ---------------------------------------------------------------------------


async def _regenerate(session, source_report: Report) -> tuple[Report, dict[str, Any]]:
    """Run ``generate_from_report`` offline and return (saved row, content)."""
    with (
        patch.object(
            frg,
            "maybe_run_council",
            AsyncMock(return_value=CouncilResult(llm_used=False)),
        ),
        patch.object(frg, "load_reusable_documents", AsyncMock(return_value=None)),
    ):
        response = await FinalReportGeneratorService().generate_from_report(
            session, source_report.id
        )
    saved = (
        await session.execute(select(Report).where(Report.id == response.report_id))
    ).scalar_one()
    content = _content_of(saved)
    return saved, content


def _content_of(report: Report) -> dict[str, Any]:
    import re

    blocks = re.findall(r"```json\s*(.*?)\s*```", report.content_markdown or "", re.S)
    assert blocks, "a generated final report always embeds its report_content"
    return json.loads(blocks[-1])


# ===========================================================================
# 1. company_id
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_regeneration_preserves_company_id(session, issuer: Issuer) -> None:
    seeded = await _seed(session, issuer)
    saved, content = await _regenerate(session, seeded.final)

    assert saved.company_id == seeded.company.id
    assert saved.id != seeded.final.id  # it IS a new draft, not an edit
    assert content["company_identity"]["ticker"]["value"] == issuer.ticker
    # And the auditable trail records WHICH signal supplied it.
    provenance = (saved.source_summary_json or {})["regenerated_from"]
    assert provenance["parent_report_id"] == str(seeded.final.id)
    assert provenance["company_id"] == str(seeded.company.id)
    assert "company:report.company_id" in provenance["resolved_from"]


# ===========================================================================
# 2. Legal name — never silently replaced by the ticker
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_regeneration_preserves_the_legal_name_not_the_ticker(
    session, issuer: Issuer
) -> None:
    """The exact Pandora symptom: legal_name came back as "PNDORA"."""
    seeded = await _seed(session, issuer)
    _saved, content = await _regenerate(session, seeded.final)

    legal_name = content["company_identity"]["legal_name"]
    assert legal_name["value"] == issuer.legal_name
    assert legal_name["value"] != issuer.ticker
    # Sourced from the company record, and said so — not fabricated.
    assert legal_name["source"] == "company_db_record"
    assert content["company_identity"]["data_provenance"] != "mock"


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_the_whole_report_agrees_about_the_name(
    session, issuer: Issuer
) -> None:
    """Header, title and identity section resolve the name through ONE rule."""
    seeded = await _seed(session, issuer)
    saved, content = await _regenerate(session, seeded.final)

    assert issuer.legal_name in saved.title
    assert issuer.legal_name in (saved.summary or "")
    assert content["executive_summary"]["company_name"] == issuer.legal_name


def test_a_genuine_snapshot_name_is_never_displaced_by_the_company_row() -> None:
    """The repair is narrow: it only fires for a ticker-as-name placeholder."""
    record = {"name": "Stale Legacy Stub Ltd"}
    assert (
        resolve_display_company_name("Pandora A/S", "PNDORA", record) == "Pandora A/S"
    )
    assert resolve_display_company_name("PNDORA", "PNDORA", record) == (
        "Stale Legacy Stub Ltd"
    )
    # A company row that is ITSELF a bare ticker displaces nothing.
    assert (
        resolve_display_company_name("PNDORA", "PNDORA", {"name": "PNDORA"})
        == "PNDORA"
    )
    assert resolve_display_company_name("PNDORA", "PNDORA", None) == "PNDORA"


# ===========================================================================
# 3 + 4. candidate_id and discovery_run_id
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_regeneration_preserves_candidate_id(session, issuer: Issuer) -> None:
    seeded = await _seed(session, issuer)
    saved, content = await _regenerate(session, seeded.final)

    lineage = content["discovery_lineage"]
    assert lineage["available"] is True
    assert lineage["discovery_candidate_id"] == str(seeded.candidate.id)
    assert content["discovery_rationale"]["candidate_id"] == str(seeded.candidate.id)
    persisted = (saved.source_summary_json or {})["discovery_lineage"]
    assert persisted["discovery_candidate_id"] == str(seeded.candidate.id)


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_regeneration_preserves_discovery_run_id(
    session, issuer: Issuer
) -> None:
    seeded = await _seed(session, issuer)
    saved, content = await _regenerate(session, seeded.final)

    assert content["discovery_lineage"]["discovery_run_id"] == str(
        seeded.discovery_run.id
    )
    assert content["discovery_rationale"]["discovery_run_id"] == str(
        seeded.discovery_run.id
    )
    persisted = (saved.source_summary_json or {})["discovery_lineage"]
    assert persisted["discovery_run_id"] == str(seeded.discovery_run.id)
    # The agent run is the third leg of the same lineage.
    assert saved.created_by_agent_run_id == seeded.agent_run.id


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_lineage_survives_a_SECOND_regeneration(
    session, issuer: Issuer
) -> None:
    """Regenerating the regenerated report keeps the same lineage.

    The chain must not depend on the candidate row still pointing at the report
    being regenerated — a real second regeneration is always one hop further
    from the candidate's ``analysis_report_id``.
    """
    seeded = await _seed(session, issuer)
    first, _ = await _regenerate(session, seeded.final)
    second, content = await _regenerate(session, first)

    assert content["discovery_lineage"]["discovery_run_id"] == str(
        seeded.discovery_run.id
    )
    assert content["discovery_lineage"]["discovery_candidate_id"] == str(
        seeded.candidate.id
    )
    assert second.company_id == seeded.company.id
    assert content["company_identity"]["legal_name"]["value"] == issuer.legal_name


# ===========================================================================
# 5. Discovery rationale (the human-facing "Why It Surfaced")
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_regeneration_preserves_discovery_rationale(
    session, issuer: Issuer
) -> None:
    seeded = await _seed(session, issuer)
    _saved, content = await _regenerate(session, seeded.final)

    rationale = content["discovery_rationale"]
    assert rationale["available"] is True
    assert rationale["source"] == "discovery_run"
    assert "No screening candidate" not in json.dumps(rationale)
    assert rationale["score_explanation"]["value"] == (
        seeded.candidate.score_explanation
    )

    # And the memo's reader-facing "Why It Surfaced" resolves from the same data.
    memo_why = frg._build_research_memo(
        content, CouncilResult.disabled(), source_tier=None
    )["why_surfaced"]
    assert memo_why["available"] is True
    assert memo_why["discovery_run_id"] == str(seeded.discovery_run.id)
    assert memo_why["discovery_candidate_id"] == str(seeded.candidate.id)


# ===========================================================================
# 6. The same agent run cannot regenerate into a detached candidate state
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
@pytest.mark.parametrize(
    "linkage",
    [
        # Ordinary state: candidate linked to the final report + persisted dict.
        pytest.param({}, id="fk_to_final+persisted"),
        # The candidate row was re-pointed at the Phase-9 draft instead.
        pytest.param({"link_candidate_to": "draft"}, id="fk_to_draft"),
        # The source report predates lineage persistence — only the FK exists.
        pytest.param({"persist_lineage_on_final": False}, id="fk_only"),
    ],
)
async def test_same_agent_run_cannot_regenerate_into_a_detached_candidate_state(
    session, issuer: Issuer, linkage: dict
) -> None:
    """The regression, stated as an invariant.

    A source report whose run HAS a candidate can never produce a regenerated
    report that says it has none — through any of the exact signals, in any
    combination. Each variant below removes one of them.
    """
    seeded = await _seed(session, issuer, **linkage)
    _saved, content = await _regenerate(session, seeded.final)

    assert content["discovery_lineage"]["available"] is True
    assert content["discovery_rationale"]["available"] is True
    assert content["discovery_lineage"]["discovery_candidate_id"] == str(
        seeded.candidate.id
    )


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_lineage_survives_the_candidate_row_being_deleted(
    session, issuer: Issuer
) -> None:
    """Deleted candidate row, but the source report recorded its lineage.

    Research history is never deleted (rule 15), but a candidate row CAN be
    re-pointed or removed. The persisted lineage is the durable record and is
    still exact FK data — it is not a name match.
    """
    seeded = await _seed(session, issuer)
    await session.delete(seeded.candidate)
    await session.commit()

    saved, content = await _regenerate(session, seeded.final)
    assert content["discovery_lineage"]["discovery_candidate_id"] == str(
        seeded.candidate.id
    )
    provenance = (saved.source_summary_json or {})["regenerated_from"]
    assert "discovery:source_report.source_summary_json" in provenance["resolved_from"]


# ===========================================================================
# 7. Genuine absence stays absent — nothing is invented to fill the hole
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_absent_candidate_lineage_is_preserved_honestly(
    session, issuer: Issuer
) -> None:
    """A run that genuinely had no discovery candidate must still say so.

    A same-company candidate from an UNRELATED run exists in the database and
    must not be attached to this report: the resolver has no "latest candidate
    for this company" arm, by design.
    """
    seeded = await _seed(session, issuer)
    # An unrelated, later run for the SAME company — a tempting wrong answer.
    other_run = DiscoveryRun(
        id=uuid.uuid4(),
        status="completed",
        provider_name="free_real",
        mode="thesis",
        universe_source="thesis_generated",
        universe_count=5,
        thesis_text="A completely different thesis",
    )
    session.add(other_run)
    await session.flush()
    session.add(
        DiscoveryCandidate(
            id=uuid.uuid4(),
            discovery_run_id=other_run.id,
            ticker=issuer.ticker,
            exchange=issuer.exchange,
            company_name=issuer.legal_name,
            legal_name=issuer.legal_name,
            rank=1,
            candidate_score=91.0,
        )
    )

    # A DIFFERENT report, on its own run, with no discovery origin at all.
    orphan_run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        status="completed",
        started_at=_utcnow(),
    )
    session.add(orphan_run)
    await session.flush()
    orphan = Report(
        id=uuid.uuid4(),
        title=f"LLM Council Analysis Draft — {issuer.ticker}",
        slug=f"orphan-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        human_review_required=True,
        final_report_version=FINAL_REPORT_VERSION,
        company_id=seeded.company.id,
        created_by_agent_run_id=orphan_run.id,
        content_markdown="# Draft\n\n```json\n"
        + json.dumps(_workflow_state(issuer), default=str)
        + "\n```\n",
    )
    session.add(orphan)
    await session.commit()

    saved, content = await _regenerate(session, orphan)

    assert content["discovery_lineage"]["available"] is False
    assert content["discovery_rationale"]["available"] is False
    assert (saved.source_summary_json or {})["discovery_lineage"] is None
    # Identity is still recovered — absence of a CANDIDATE is not absence of a
    # COMPANY, and the two must not be conflated.
    assert content["company_identity"]["legal_name"]["value"] == issuer.legal_name
    assert saved.company_id == seeded.company.id


# ===========================================================================
# 8. Ambiguity fails closed
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_ambiguous_candidate_lineage_fails_closed(
    session, issuer: Issuer
) -> None:
    """Two candidates, two discovery runs, both linked by an exact FK.

    There is no correct way to choose. Regeneration refuses rather than
    attributing the report to whichever row sorted first.
    """
    seeded = await _seed(session, issuer)
    rival_run = DiscoveryRun(
        id=uuid.uuid4(),
        status="completed",
        provider_name="free_real",
        mode="thesis",
        universe_source="thesis_generated",
        universe_count=9,
        thesis_text="A rival thesis that also surfaced this issuer",
    )
    session.add(rival_run)
    await session.flush()
    rival = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=rival_run.id,
        ticker=issuer.ticker,
        exchange=issuer.exchange,
        company_name=issuer.legal_name,
        legal_name=issuer.legal_name,
        rank=2,
        candidate_score=44.0,
        # The SAME exact signal as the real candidate — this is genuine
        # ambiguity, not a weaker match to be outranked.
        agent_run_id=seeded.agent_run.id,
    )
    session.add(rival)
    await session.commit()

    with pytest.raises(AmbiguousReportLineageError) as excinfo:
        await _regenerate(session, seeded.final)

    message = str(excinfo.value)
    assert str(seeded.candidate.id) in message
    assert str(rival.id) in message
    # No report was written — failing closed means producing NOTHING.
    reports = (
        (await session.execute(select(Report).where(Report.company_id == seeded.company.id)))
        .scalars()
        .all()
    )
    assert {r.id for r in reports} == {seeded.draft.id, seeded.final.id}


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_persisted_lineage_contradicting_the_candidate_row_fails_closed(
    session, issuer: Issuer
) -> None:
    """Two exact signals that disagree is a conflict, not a preference order."""
    seeded = await _seed(session, issuer)
    stale = dict(seeded.lineage_dict)
    stale["discovery_candidate_id"] = str(uuid.uuid4())
    seeded.final.source_summary_json = {"discovery_lineage": stale}
    await session.commit()

    with pytest.raises(AmbiguousReportLineageError):
        await _regenerate(session, seeded.final)


async def test_the_route_maps_ambiguous_lineage_to_409() -> None:
    """404 would say "no such report"; the problem is too MUCH linkage."""
    from fastapi import HTTPException

    from app.api.v1 import final_reports as route

    svc = AsyncMock()
    svc.generate_from_report.side_effect = AmbiguousReportLineageError(
        "discovery candidate", ["a", "b"]
    )
    with patch.object(route, "_svc", svc), pytest.raises(HTTPException) as excinfo:
        await route.generate_from_report(uuid.uuid4(), db=AsyncMock())

    assert excinfo.value.status_code == 409
    assert "Ambiguous discovery candidate lineage" in str(excinfo.value.detail)


# ===========================================================================
# 9. A DFR-linked report keeps its exact candidate/company identity
# ===========================================================================


@pytest.mark.parametrize("issuer", ALL_ISSUERS, ids=lambda i: i.ticker)
async def test_dfr_linked_report_regeneration_retains_exact_identity(
    session, issuer: Issuer
) -> None:
    """The candidate's ``analysis_report_id`` points at the FINAL report (DFR).

    Regenerating that report must carry the candidate and company through
    unchanged — and must NOT re-point the candidate at the new draft, which
    would silently detach the accepted report from its own discovery origin.
    """
    seeded = await _seed(session, issuer, link_candidate_to="final")
    assert seeded.candidate.analysis_report_id == seeded.final.id

    saved, content = await _regenerate(session, seeded.final)

    assert content["discovery_lineage"]["discovery_candidate_id"] == str(
        seeded.candidate.id
    )
    assert content["discovery_lineage"]["discovery_run_id"] == str(
        seeded.discovery_run.id
    )
    assert content["company_identity"]["legal_name"]["value"] == issuer.legal_name
    assert saved.company_id == seeded.company.id
    assert saved.created_by_agent_run_id == seeded.agent_run.id

    refreshed = (
        await session.execute(
            select(DiscoveryCandidate).where(
                DiscoveryCandidate.id == seeded.candidate.id
            )
        )
    ).scalar_one()
    assert refreshed.analysis_report_id == seeded.final.id


# ===========================================================================
# Cross-issuer isolation — nothing here is keyed on a ticker
# ===========================================================================


async def test_two_issuers_never_borrow_each_others_lineage(session) -> None:
    dk = await _seed(session, PNDORA)
    it = await _seed(session, MONC)

    _, dk_content = await _regenerate(session, dk.final)
    _, it_content = await _regenerate(session, it.final)

    assert dk_content["discovery_lineage"]["discovery_candidate_id"] == str(
        dk.candidate.id
    )
    assert it_content["discovery_lineage"]["discovery_candidate_id"] == str(
        it.candidate.id
    )
    assert dk_content["company_identity"]["legal_name"]["value"] == PNDORA.legal_name
    assert it_content["company_identity"]["legal_name"]["value"] == MONC.legal_name
    assert PNDORA.legal_name not in json.dumps(it_content)
    assert MONC.legal_name not in json.dumps(dk_content)
