"""Phase 32A Slice 3 — source/citation persistence + reconciliation.

These tests exercise the FULL persistence path behind the single default-OFF flag
``report_citation_persistence_enabled`` against a REAL in-memory SQLite async
database (``aiosqlite``), so real INSERT / UPDATE / SELECT + the new
Source/Citation rows are genuinely persisted and read back (the shared conftest
uses a mock AsyncSession, which cannot exercise a WHERE clause). The same
dialect-scoped ``JSONB -> JSON`` compiler shim as the from-company hotfix tests
lets ``Base.metadata.create_all`` build the Postgres-flavoured schema on SQLite.

The LLM council is disabled offline, so tests that need real council claim→evidence
links patch ``final_report_generator.maybe_run_council`` to return a deterministic
``CouncilResult`` carrying ``persistable_evidence`` + agent claims (no network /
Azure). Nothing here touches auth, publishing, or app settings; the invariants
(publication_ready False, human_review_required True) are asserted preserved.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# --- Import every model module so Base.metadata is complete for create_all. ---
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
from app.models.source import Citation, Source
from app.services import citation_service, final_report_generator
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.llm.schemas import (
    AgentKeyPoint,
    AgentRiskGap,
    CouncilAgentOutput,
    CouncilResult,
    PersistableEvidence,
)
from app.services.sources.redaction import canonicalize_source_url

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


@pytest.fixture
def flag_on(monkeypatch):
    """Turn the Slice-3 persistence flag ON for one test (auto-restored)."""
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    return True


# ---------------------------------------------------------------------------
# Seed helpers (add + flush only — the caller controls commit)
# ---------------------------------------------------------------------------
async def _add_company(session, *, ticker: str, exchange: str, name: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        name=name,
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_completed_run(session, *, status: str = "completed") -> AgentRun:
    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        workflow_version="1.0.0",
        status=status,
        started_at=_utcnow(),
        trigger_type="manual",
    )
    session.add(run)
    await session.flush()
    return run


async def _add_draft_report(
    session,
    *,
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    created_at: datetime | None = None,
    title: str = "Draft",
) -> Report:
    report = Report(
        id=uuid.uuid4(),
        title=title,
        slug=f"draft-{uuid.uuid4().hex[:12]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# Analysis Council Draft (no envelope)",
        created_by_agent_run_id=agent_run_id,
        company_id=company_id,
        human_review_required=True,
        created_at=created_at or _utcnow(),
    )
    session.add(report)
    await session.flush()
    return report


async def _add_source(session, *, source_type: str, title: str, url: str | None) -> Source:
    src = Source(
        id=uuid.uuid4(),
        source_type=source_type,
        title=title,
        url=url,
        retrieved_at=_utcnow(),
    )
    session.add(src)
    await session.flush()
    return src


async def _add_citation(
    session,
    *,
    source_id: uuid.UUID,
    report_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    claim_text: str,
    field_path: str,
    source_tier: str,
    data_quality: str = "B_single_credible",
) -> Citation:
    cit = Citation(
        id=uuid.uuid4(),
        source_id=source_id,
        report_id=report_id,
        agent_run_id=agent_run_id,
        claim_text=claim_text,
        source_quote="deterministic quote",
        field_path=field_path,
        source_tier=source_tier,
        data_quality=data_quality,
        retrieved_at=_utcnow(),
    )
    session.add(cit)
    await session.flush()
    return cit


# ---------------------------------------------------------------------------
# Council fixtures (deterministic — no network)
# ---------------------------------------------------------------------------
def _pe(
    alias: str,
    *,
    source_type: str,
    source_tier: str,
    title: str,
    url: str | None,
    excerpt: str | None = None,
    data_quality: str | None = "B_single_credible",
    fields_supported: list[str] | None = None,
    primary_fact: dict | None = None,
    content_tier: str | None = None,
    provider_transport: str | None = None,
) -> PersistableEvidence:
    return PersistableEvidence(
        uid=uuid.uuid4().hex,
        alias=alias,
        source_tier=source_tier,
        content_tier=content_tier or source_tier,
        source_type=source_type,
        provider_transport=provider_transport,
        title=title,
        url=url,
        excerpt=excerpt,
        data_quality=data_quality,
        fields_supported=fields_supported or [],
        primary_fact=primary_fact,
    )


def _council(*, persistable: list[PersistableEvidence], agents: list[CouncilAgentOutput]):
    cr = CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-model",
        evidence_item_count=len(persistable),
        agents=agents,
        persistable_evidence=persistable,
    )
    cr.recount()
    return cr


def _empty_council() -> CouncilResult:
    """A council that RAN but produced NO resolvable claim→evidence link."""
    cr = CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-model",
        agents=[
            CouncilAgentOutput(
                agent_name="red_team",
                status="failed",
                summary="[Agent did not complete: provider error or timeout.]",
                key_points=[],
            )
        ],
        persistable_evidence=[],
    )
    cr.recount()
    return cr


def _patch_council_sequence(monkeypatch, results: list[CouncilResult]):
    """Patch maybe_run_council to return each result on successive calls (ticker
    independent), so two regenerations from the SAME source draft can get
    different councils regardless of identity resolution."""
    state = {"n": 0}

    async def _fake(*args, **kwargs):
        i = state["n"]
        state["n"] += 1
        return results[i] if i < len(results) else CouncilResult.disabled()

    monkeypatch.setattr(final_report_generator, "maybe_run_council", _fake)


def _patch_council(monkeypatch, by_ticker: dict[str, CouncilResult]):
    """Patch maybe_run_council to return a deterministic result keyed on ticker."""

    async def _fake(*args, **kwargs):
        ticker = kwargs.get("ticker")
        return by_ticker.get(ticker) or CouncilResult.disabled()

    monkeypatch.setattr(final_report_generator, "maybe_run_council", _fake)


def _aapl_council() -> CouncilResult:
    persistable = [
        _pe(
            "E1",
            source_type="sec_financial_statement",
            source_tier="T1_primary_filing",
            content_tier="T1_primary_filing",
            provider_transport="SEC EDGAR / data.sec.gov",
            title="FY2024 ANNUAL 10-K — income statement",
            url=None,  # SEC/XBRL facts carry no per-filing URL
            excerpt="Total net sales rose year over year in fiscal 2024.",
            data_quality="B_single_credible",
            fields_supported=["revenue_usd_m", "net_income_usd_m"],
        ),
        _pe(
            "E2",
            source_type="company_filing",
            source_tier="T1_primary_filing",
            title="Apple Inc. Form 10-K (FY2024)",
            url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
            excerpt="Apple filed its annual report on Form 10-K.",
            data_quality="B_single_credible",
        ),
        _pe(
            "E3",
            source_type="company_press_release",
            source_tier="T1_primary_company_source",
            title="Apple newsroom — product announcement",
            url="https://www.apple.com/newsroom/2024/",
            excerpt="Apple announced a new product line.",
            data_quality="C",
        ),
    ]
    agents = [
        CouncilAgentOutput(
            agent_name="financial_analyst",
            status="completed",
            summary="Financial review.",
            key_points=[
                AgentKeyPoint(claim="Net sales increased in fiscal 2024.", citation_ids=["E1"]),
                AgentKeyPoint(claim="The company filed its annual report.", citation_ids=["E2"]),
            ],
        ),
        CouncilAgentOutput(
            agent_name="catalyst",
            status="completed",
            summary="Catalysts.",
            key_points=[
                AgentKeyPoint(claim="A new product line was announced.", citation_ids=["E3"]),
            ],
        ),
        CouncilAgentOutput(
            agent_name="red_team",
            status="failed",
            summary="[Agent did not complete: provider error or timeout.]",
            key_points=[],
        ),
    ]
    return _council(persistable=persistable, agents=agents)


def _cfr_council() -> CouncilResult:
    persistable = [
        _pe(
            "E1",
            source_type="company_ir_annual_reports_index",
            source_tier="T1_primary_company_source",
            title="Richemont — annual reports index",
            url="https://www.richemont.com/en/home/investors/",
            excerpt="Annual report index page.",
            data_quality="metadata_only",
        ),
        _pe(
            "E2",
            source_type="company_ir_profile",
            source_tier="T2_regulator_or_gov",
            title="SIX Swiss Exchange — issuer profile CFR",
            url="https://www.six-group.com/en/market-data/shares/",
            excerpt=None,
            data_quality="link_metadata_only",
        ),
    ]
    agents = [
        CouncilAgentOutput(
            agent_name="source_quality_critic",
            status="completed",
            summary="Source review.",
            key_points=[
                AgentKeyPoint(
                    claim="A verified issuer investor-relations page was located.",
                    citation_ids=["E1"],
                ),
            ],
            risks_or_gaps=[
                AgentRiskGap(
                    item="No extracted financial statement was available.",
                    citation_ids=["E2"],
                ),
            ],
        ),
    ]
    return _council(persistable=persistable, agents=agents)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------
def _parse_report_content(report: Report) -> dict:
    md = report.content_markdown or ""
    blocks = re.findall(r"```json\s*(.*?)\s*```", md, re.DOTALL)
    assert blocks, "saved final report has no JSON block"
    return json.loads(blocks[-1])


async def _load_report(session, report_id: uuid.UUID) -> Report:
    return (
        await session.execute(select(Report).where(Report.id == report_id))
    ).scalar_one()


async def _council_citations(session, report_id: uuid.UUID) -> list[Citation]:
    rows = (
        await session.execute(
            select(Citation).where(
                Citation.report_id == report_id,
                Citation.field_path.like("council:%"),
            )
        )
    ).scalars().all()
    return list(rows)


def _appendix(content: dict) -> dict:
    return content["source_citation_appendix"]


# ===========================================================================
# 1. AAPL real-data persistence
# ===========================================================================
async def test_aapl_council_evidence_persisted_with_provenance(
    session, flag_on, monkeypatch
) -> None:
    _patch_council(monkeypatch, {"AAPL": _aapl_council()})
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    source_report = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="AAPL draft"
    )
    # Seed the draft's deterministic profile/price citations (already linked).
    prof_src = await _add_source(
        session, source_type="financial_data_api", title="Profile", url="https://p"
    )
    price_src = await _add_source(
        session, source_type="financial_data_api", title="Price", url="https://q"
    )
    await _add_citation(
        session, source_id=prof_src.id, report_id=source_report.id, agent_run_id=run.id,
        claim_text="identity.legal_name", field_path="identity.legal_name",
        source_tier="T5_api_aggregator",
    )
    await _add_citation(
        session, source_id=price_src.id, report_id=source_report.id, agent_run_id=run.id,
        claim_text="price.latest_close", field_path="price.latest_close",
        source_tier="T5_api_aggregator",
    )
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, aapl.id)
    final = await _load_report(session, resp.report_id)
    content = _parse_report_content(final)

    # Lineage: the final report is owned by AAPL + the company-pinned run.
    assert final.company_id == aapl.id
    assert final.created_by_agent_run_id == run.id

    # Council claim→evidence citations persisted on the final report.
    council_cits = await _council_citations(session, final.id)
    assert len(council_cits) == 3
    assert all(c.report_id == final.id for c in council_cits)
    assert all(c.agent_run_id == run.id for c in council_cits)

    # SEC/XBRL financial evidence carries its tier onto the citation.
    sec = [c for c in council_cits if c.source_tier == "T1_primary_filing"]
    assert len(sec) == 2  # E1 statement + E2 filing

    # Company press-release + SEC filing sources are persisted (deduped by hash).
    persisted_sources = (
        await session.execute(
            select(Source).where(Source.id.in_([c.source_id for c in council_cits]))
        )
    ).scalars().all()
    stypes = {s.source_type for s in persisted_sources}
    assert "company_press_release" in stypes
    assert "company_filing" in stypes
    assert "sec_financial_statement" in stypes
    # The SEC/XBRL source (url=None) still deduped via synthesized content_hash.
    assert all(s.content_hash for s in persisted_sources)

    # Appendix reports honest non-zero counts.
    ap = _appendix(content)
    assert ap["council_claim_citation_count"] == 3
    assert ap["db_persisted_citation_count"] == 2 + 3  # deterministic + council
    assert ap["db_persisted_source_count"] >= 3
    assert ap["structured_financial_fact_count"] == 2
    assert ap["extracted_evidence_count"] == 1  # E3 press release excerpt
    # Existing envelopes still work (non-zero via the fallback loader).
    assert ap["sources"]["total"] == 2
    assert ap["citations"]["total"] == 2

    # Invariants preserved.
    assert resp.publication_ready is False
    assert resp.human_review_required is True
    assert final.status == "draft"


# ===========================================================================
# 2. CFR metadata-only fallback
# ===========================================================================
async def test_cfr_metadata_only_never_becomes_a_fact(
    session, flag_on, monkeypatch
) -> None:
    _patch_council(monkeypatch, {"CFR": _cfr_council()})
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")
    run = await _add_completed_run(session)
    await _add_draft_report(
        session, company_id=cfr.id, agent_run_id=run.id, title="CFR draft"
    )
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, cfr.id)
    final = await _load_report(session, resp.report_id)
    content = _parse_report_content(final)

    council_cits = await _council_citations(session, final.id)
    assert len(council_cits) == 2  # one key_point + one risk/gap
    # Metadata-only references keep the sentinel + assert NO fact-like quote.
    assert {c.data_quality for c in council_cits} == {"metadata_only", "link_metadata_only"}
    assert all(c.source_quote is None for c in council_cits)

    # Their Sources are references, never a financial-fact source_type.
    ref_sources = (
        await session.execute(
            select(Source).where(Source.id.in_([c.source_id for c in council_cits]))
        )
    ).scalars().all()
    fact_types = {"sec_financial_statement", "company_filing", "company_ir_financial_fact"}
    assert not ({s.source_type for s in ref_sources} & fact_types)

    ap = _appendix(content)
    assert ap["structured_financial_fact_count"] == 0
    assert ap["extracted_evidence_count"] == 0  # metadata-only ≠ extracted
    assert ap["council_claim_citation_count"] == 2
    # Honest reconciling wording — never "no sources", never a fact for references.
    assert "metadata-only" in ap["note"].lower()
    assert "financial fact" in ap["note"].lower()


# ===========================================================================
# 3. Cross-company isolation
# ===========================================================================
async def test_no_cross_company_linkage(session, flag_on, monkeypatch) -> None:
    _patch_council(monkeypatch, {"AAPL": _aapl_council(), "CFR": _cfr_council()})
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    cfr = await _add_company(session, ticker="CFR", exchange="SW", name="Richemont SA")
    aapl_run = await _add_completed_run(session)
    cfr_run = await _add_completed_run(session)
    await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=aapl_run.id, title="AAPL draft"
    )
    await _add_draft_report(
        session, company_id=cfr.id, agent_run_id=cfr_run.id, title="CFR draft"
    )
    await session.commit()

    aapl_resp = await FinalReportGeneratorService().generate_from_company(session, aapl.id)
    cfr_resp = await FinalReportGeneratorService().generate_from_company(session, cfr.id)

    aapl_final = await _load_report(session, aapl_resp.report_id)
    cfr_final = await _load_report(session, cfr_resp.report_id)

    aapl_cits = await _council_citations(session, aapl_final.id)
    cfr_cits = await _council_citations(session, cfr_final.id)

    # Ownership never crosses the company boundary.
    assert aapl_final.company_id == aapl.id and cfr_final.company_id == cfr.id
    assert all(c.agent_run_id == aapl_run.id for c in aapl_cits)
    assert all(c.agent_run_id == cfr_run.id for c in cfr_cits)
    # No AAPL citation points at a CFR source (and vice versa).
    aapl_source_ids = {c.source_id for c in aapl_cits}
    cfr_source_ids = {c.source_id for c in cfr_cits}
    assert not (aapl_source_ids & cfr_source_ids)


# ===========================================================================
# 4. Idempotency — regeneration + dedup, no duplicate rows
# ===========================================================================
async def test_regeneration_dedups_sources_and_council_citations(
    session, flag_on, monkeypatch
) -> None:
    _patch_council(monkeypatch, {"AAPL": _aapl_council()})
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="AAPL draft"
    )
    await session.commit()

    svc = FinalReportGeneratorService()
    r1 = await svc.generate_from_company(session, aapl.id)
    src_count_1 = len((await session.execute(select(Source))).scalars().all())
    r2 = await svc.generate_from_company(session, aapl.id)
    src_count_2 = len((await session.execute(select(Source))).scalars().all())

    # Sources are deduped by content_hash — the second run reuses them.
    assert src_count_1 == 3
    assert src_count_2 == 3

    # Each final report owns exactly its own 3 council citations (no accumulation).
    assert len(await _council_citations(session, r1.report_id)) == 3
    assert len(await _council_citations(session, r2.report_id)) == 3


async def test_from_company_regeneration_sources_draft_and_count_is_honest(
    session, flag_on, monkeypatch
) -> None:
    """B1 regression: the 2nd ``from-company`` must source from the analysis DRAFT
    (which carries the analysis-state envelope + only deterministic citations), NOT
    from the 1st generated final report. Otherwise the prior final report's own
    ``council:%`` citations pollute the reconciliation counts and the stored
    ``db_persisted_citation_count`` diverges from a fresh loader read."""
    _patch_council(monkeypatch, {"AAPL": _aapl_council()})
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    draft = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="AAPL draft"
    )
    prof_src = await _add_source(
        session, source_type="financial_data_api", title="Profile", url="https://p"
    )
    await _add_citation(
        session, source_id=prof_src.id, report_id=draft.id, agent_run_id=run.id,
        claim_text="identity.legal_name", field_path="identity.legal_name",
        source_tier="T5_api_aggregator",
    )
    await session.commit()

    svc = FinalReportGeneratorService()
    r1 = await svc.generate_from_company(session, aapl.id)
    r2 = await svc.generate_from_company(session, aapl.id)
    assert r1.report_id != r2.report_id

    final2 = await _load_report(session, r2.report_id)
    # Sourced from the DRAFT (not r1): the final report carries a version; the
    # draft's lineage/company are preserved, and r2 owns exactly its own 3 council
    # citations.
    assert final2.company_id == aapl.id
    assert final2.created_by_agent_run_id == run.id
    r2_council = await _council_citations(session, final2.id)
    assert len(r2_council) == 3

    # The stored appendix count equals a fresh loader read — never inflated by r1's
    # council rows (the B1 symptom was stored=7 vs fresh=4).
    ap2 = _appendix(_parse_report_content(final2))
    fresh2 = await final_report_generator._load_citations_for_report(
        session, final2.id, run.id
    )
    assert ap2["db_persisted_citation_count"] == len(fresh2)
    assert ap2["db_persisted_citation_count"] == 3 + 1  # council + 1 deterministic
    assert ap2["db_persisted_source_count"] == 3 + 1

    # r2's loaded citations never include r1's council rows (no cross-report leak).
    r1_council_ids = {c.id for c in await _council_citations(session, r1.report_id)}
    assert not ({c.id for c in fresh2} & r1_council_ids)


async def test_repeated_citation_id_on_one_claim_dedups(session, flag_on) -> None:
    """N3 regression: a claim that repeats the same citation_id (``["E1", "E1"]``)
    persists ONE citation, not two — a claim cannot cite the same evidence twice."""
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    report = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="final"
    )
    await session.commit()

    cr = _council(
        persistable=[
            _pe("E1", source_type="company_filing", source_tier="T1_primary_filing",
                title="10-K", url="https://www.sec.gov/x", excerpt="Filed."),
        ],
        agents=[
            CouncilAgentOutput(
                agent_name="financial_analyst", status="completed", summary="ok",
                key_points=[
                    AgentKeyPoint(claim="Filed the annual report.", citation_ids=["E1", "E1"]),
                ],
            )
        ],
    )
    _, citations_added = await final_report_generator._persist_council_evidence_citations(
        session, report.id, run.id, cr
    )
    await session.commit()
    assert citations_added == 1
    assert len(await _council_citations(session, report.id)) == 1


async def test_persist_helper_delete_before_insert_is_idempotent(
    session, flag_on
) -> None:
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    report = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="final"
    )
    await session.commit()

    cr = _aapl_council()
    # Persist twice on the SAME report_id → delete-before-insert keeps it stable.
    await final_report_generator._persist_council_evidence_citations(
        session, report.id, run.id, cr
    )
    await session.commit()
    await final_report_generator._persist_council_evidence_citations(
        session, report.id, run.id, cr
    )
    await session.commit()

    assert len(await _council_citations(session, report.id)) == 3


# ===========================================================================
# 4b. Loader isolation — lineage surfaces deterministic rows on every final
#     report WITHOUT leaking a sibling final report's council citations.
# ===========================================================================
async def test_loader_no_cross_report_council_leak(session, flag_on) -> None:
    """The lineage fallback must surface the draft's DETERMINISTIC citations on
    every final report of the lineage, but a ``council:%`` citation belongs to
    exactly ONE report by ``report_id`` and must never leak into a SIBLING final
    report of the same lineage (same company-pinned agent_run)."""
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    draft = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="draft"
    )
    prof_src = await _add_source(
        session, source_type="financial_data_api", title="Profile", url="https://p"
    )
    det = await _add_citation(
        session, source_id=prof_src.id, report_id=draft.id, agent_run_id=run.id,
        claim_text="identity.legal_name", field_path="identity.legal_name",
        source_tier="T5_api_aggregator",
    )
    # Two final reports of the SAME lineage (company + run): A gets a council,
    # B's council produced nothing.
    final_a = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="final A"
    )
    final_b = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="final B"
    )
    await session.commit()

    await final_report_generator._persist_council_evidence_citations(
        session, final_a.id, run.id, _aapl_council()
    )
    await session.commit()

    a_cits = await final_report_generator._load_citations_for_report(
        session, final_a.id, run.id
    )
    b_cits = await final_report_generator._load_citations_for_report(
        session, final_b.id, run.id
    )

    def _split(cits):
        council = [c for c in cits if (c.field_path or "").startswith("council:")]
        det_rows = [c for c in cits if not (c.field_path or "").startswith("council:")]
        return council, det_rows

    a_council, a_det = _split(a_cits)
    b_council, b_det = _split(b_cits)

    # final_B (no council of its own) must NOT pull final_A's council rows.
    assert b_council == []
    # final_A surfaces exactly its OWN 3 council citations.
    assert len(a_council) == 3
    assert all(c.report_id == final_a.id for c in a_council)
    # The deterministic citation is shared across BOTH final reports of the lineage.
    assert [c.id for c in a_det] == [det.id]
    assert [c.id for c in b_det] == [det.id]

    # Loader agrees with the stored count basis: this-report council + lineage
    # deterministic (no double count, no leak).
    assert len(a_cits) == 3 + 1
    assert len(b_cits) == 0 + 1


async def test_loader_off_is_report_id_only(session) -> None:
    """Flag OFF (default) ⇒ the loader is a ``report_id`` filter only — no lineage
    fallback, byte-identical to pre-Slice-3 behaviour."""
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    draft = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="draft"
    )
    final = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="final"
    )
    prof_src = await _add_source(
        session, source_type="financial_data_api", title="Profile", url="https://p"
    )
    await _add_citation(
        session, source_id=prof_src.id, report_id=draft.id, agent_run_id=run.id,
        claim_text="identity.legal_name", field_path="identity.legal_name",
        source_tier="T5_api_aggregator",
    )
    await session.commit()

    # No fallback ⇒ the final report (its own report_id has no rows) sees NONE of
    # the draft's lineage citations.
    got = await final_report_generator._load_citations_for_report(
        session, final.id, run.id
    )
    assert got == []


# ===========================================================================
# 5. Partial council failure
# ===========================================================================
async def test_only_completed_agent_citations_persist(
    session, flag_on, monkeypatch
) -> None:
    persistable = [
        _pe("E1", source_type="company_filing", source_tier="T1_primary_filing",
            title="10-K", url="https://www.sec.gov/x", excerpt="Filed."),
        _pe("E2", source_type="company_press_release",
            source_tier="T1_primary_company_source", title="PR",
            url="https://www.apple.com/nr", excerpt="News."),
    ]
    agents = [
        CouncilAgentOutput(
            agent_name="financial_analyst", status="completed", summary="ok",
            key_points=[AgentKeyPoint(claim="Filed the annual report.", citation_ids=["E1"])],
        ),
        # Failed + skipped agents carry EMPTY key_points → contribute NOTHING.
        CouncilAgentOutput(agent_name="red_team", status="failed",
                           summary="[withheld]", key_points=[]),
        CouncilAgentOutput(agent_name="valuation_guard", status="skipped",
                           summary="skipped", key_points=[]),
    ]
    cr = _council(persistable=persistable, agents=agents)
    _patch_council(monkeypatch, {"AAPL": cr})

    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    source_report = await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="AAPL draft"
    )
    # A deterministic profile citation must still surface (fallback loader).
    prof_src = await _add_source(
        session, source_type="financial_data_api", title="Profile", url="https://p"
    )
    await _add_citation(
        session, source_id=prof_src.id, report_id=source_report.id, agent_run_id=run.id,
        claim_text="identity.legal_name", field_path="identity.legal_name",
        source_tier="T5_api_aggregator",
    )
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, aapl.id)
    final = await _load_report(session, resp.report_id)
    content = _parse_report_content(final)

    council_cits = await _council_citations(session, final.id)
    assert len(council_cits) == 1  # only the completed agent's cited claim
    assert council_cits[0].field_path == "council:financial_analyst"

    ap = _appendix(content)
    assert ap["council_claim_citation_count"] == 1
    # Deterministic profile citation still surfaces on the appendix envelope.
    assert ap["citations"]["total"] == 1
    assert ap["db_persisted_citation_count"] == 1 + 1


# ===========================================================================
# 6. Legacy compatibility — OFF byte-identical + safe legacy render
# ===========================================================================
async def test_flag_off_appendix_has_no_slice3_counts(session, monkeypatch) -> None:
    # Flag OFF (default). Even with a council patched in, no persistence / counts.
    _patch_council(monkeypatch, {"AAPL": _aapl_council()})
    aapl = await _add_company(session, ticker="AAPL", exchange="NASDAQ", name="Apple Inc.")
    run = await _add_completed_run(session)
    await _add_draft_report(
        session, company_id=aapl.id, agent_run_id=run.id, title="AAPL draft"
    )
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, aapl.id)
    final = await _load_report(session, resp.report_id)
    content = _parse_report_content(final)
    ap = _appendix(content)

    # No six-count keys, no council citations, no lineage set → byte-identical path.
    for key in (
        "db_persisted_source_count",
        "db_persisted_citation_count",
        "council_claim_citation_count",
        "extracted_evidence_count",
        "structured_financial_fact_count",
    ):
        assert key not in ap
    assert len(await _council_citations(session, final.id)) == 0
    assert final.company_id is None
    assert final.created_by_agent_run_id is None


async def test_legacy_null_citations_and_null_company_render_safely(
    session, flag_on, monkeypatch
) -> None:
    # No council, a legacy source report with NULL company + NULL agent_run and a
    # dangling report_id-NULL citation → honest zero, no crash.
    _patch_council(monkeypatch, {})
    legacy = await _add_draft_report(
        session, company_id=None, agent_run_id=None, title="legacy"
    )
    orphan_src = await _add_source(
        session, source_type="financial_data_api", title="Old", url="https://old"
    )
    await _add_citation(
        session, source_id=orphan_src.id, report_id=None, agent_run_id=None,
        claim_text="legacy.claim", field_path="legacy.claim", source_tier="T5_api_aggregator",
    )
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_report(session, legacy.id)
    final = await _load_report(session, resp.report_id)
    content = _parse_report_content(final)
    ap = _appendix(content)

    assert ap["db_persisted_citation_count"] == 0
    assert ap["db_persisted_source_count"] == 0
    assert ap["council_claim_citation_count"] == 0
    assert final.company_id is None  # reports.company_id NULL stays NULL


# ===========================================================================
# 7. Layer-1 backfill (the writer node_save_draft_report uses)
# ===========================================================================
async def test_link_citations_to_report_backfill(session) -> None:
    run = await _add_completed_run(session)
    other_run = await _add_completed_run(session)
    draft = await _add_draft_report(
        session, company_id=None, agent_run_id=run.id, title="draft"
    )
    src = await _add_source(
        session, source_type="financial_data_api", title="Profile", url="https://p"
    )
    # Two citations for THIS run (report_id NULL), one for another run.
    await _add_citation(
        session, source_id=src.id, report_id=None, agent_run_id=run.id,
        claim_text="a", field_path="identity.legal_name", source_tier="T5_api_aggregator",
    )
    await _add_citation(
        session, source_id=src.id, report_id=None, agent_run_id=run.id,
        claim_text="b", field_path="price.latest_close", source_tier="T5_api_aggregator",
    )
    await _add_citation(
        session, source_id=src.id, report_id=None, agent_run_id=other_run.id,
        claim_text="c", field_path="identity.legal_name", source_tier="T5_api_aggregator",
    )
    await session.commit()

    linked = await citation_service.link_citations_to_report(session, run.id, draft.id)
    await session.commit()
    assert linked == 2  # scoped to this run's agent_run_id only

    this_run = (
        await session.execute(select(Citation).where(Citation.agent_run_id == run.id))
    ).scalars().all()
    assert all(c.report_id == draft.id for c in this_run)
    # The other run's citation is untouched (company-safe scope).
    other = (
        await session.execute(
            select(Citation).where(Citation.agent_run_id == other_run.id)
        )
    ).scalar_one()
    assert other.report_id is None

    # Idempotent: a re-run links nothing new (report_id IS NULL guard).
    linked_again = await citation_service.link_citations_to_report(session, run.id, draft.id)
    await session.commit()
    assert linked_again == 0


# ===========================================================================
# 8. canonicalize_source_url
# ===========================================================================
def test_canonicalize_source_url_strips_secrets_userinfo_fragment() -> None:
    out = canonicalize_source_url(
        "HTTPS://User:Pass@Example.COM/Path?api_token=SECRET&keep=1#frag"
    )
    assert out == "https://example.com/Path?keep=1"
    # No userinfo, no fragment, no credential param, host lowercased, path case kept.
    assert "Pass" not in out
    assert "SECRET" not in out
    assert "#frag" not in out


def test_canonicalize_source_url_edge_cases() -> None:
    assert canonicalize_source_url(None) is None
    assert canonicalize_source_url("") == ""
    # A benign URL with no query/fragment/userinfo is only host-lowercased.
    assert (
        canonicalize_source_url("https://WWW.SEC.gov/Archives/x.htm")
        == "https://www.sec.gov/Archives/x.htm"
    )
    # A signed Azure SAS-style token param (sig) is dropped.
    assert "sig=" not in (
        canonicalize_source_url("https://blob.example.com/f?sig=abc&x=2") or ""
    )
