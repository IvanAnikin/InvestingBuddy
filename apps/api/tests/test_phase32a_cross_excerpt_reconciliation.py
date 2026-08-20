"""Phase 32A dedicated slice — cross-excerpt financial-fact reconciliation +
active fact lifecycle.

Targets the gap left after the excerpt-ranking slice: all target facts could
reach the bounded excerpt set, but the VALIDATOR's cross-excerpt reconciliation
(same-scope conflict detection, period inference, active-fact supersession)
still dropped most of them before they ever reached Council evidence. See
``extracted_fact_validator.py`` / ``primary_fact_parser.py`` /
``extracted_document_service.py`` docstrings for the mechanisms under test.

Fully offline/deterministic — no network, no LLM, no Azure. The persistence
tests use a real in-memory SQLite async DB (mirrors
``test_phase32a_slice5_persistence.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.extracted_document_service import persist_primary_document_artifacts
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.document_text_extractor import DocumentExcerpt
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import (
    FIELD_NET_CASH,
    FIELD_OPERATING_PROFIT,
    FIELD_REVENUE,
    _infer_prose_scope,
    _parse_excerpt,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


def _exc(excerpt_id: str, text: str, *, heading: str | None = None, page: int = 4) -> PrimaryDocumentExcerpt:
    return PrimaryDocumentExcerpt(
        excerpt_id=excerpt_id,
        text=text,
        page_number=page,
        section=heading,
        heading=heading,
        table_location=None,
        extraction_method="native_pdf",
        confidence=0.9,
        char_count=len(text),
        evidence_type="general",
    )


def _extraction(excerpts: list[PrimaryDocumentExcerpt]) -> PrimaryDocumentExtraction:
    return PrimaryDocumentExtraction(
        content_hash="d" * 64,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        page_count=10,
        excerpts=excerpts,
    )


# =========================================================================== #
# Scale normalization — a rounded and a precise mention of the SAME fact      #
# =========================================================================== #


def test_rounded_billion_and_precise_million_mentions_of_same_group_figure_agree():
    """A real live gap: 'Group sales reached EUR22.4 billion' (rounded) and
    'sales increased ... to EUR22,420 million' (precise) both state the SAME
    Group figure — comparing raw digits (22.4 vs 22420) treated them as a
    hard conflict; comparing on a common base unit must agree."""
    excerpts = [
        _exc("X1", "Sales reached EUR22.4 billion in 2026, an increase of 11%."),
        _exc(
            "X2",
            "For the year ended 31 March 2026, sales increased by 5% at actual "
            "exchange rates to EUR22,420 million.",
        ),
    ]
    facts = validate_extracted_facts(
        _extraction(excerpts), issuer_context=IssuerContext(company_name="Issuer")
    )
    validated = [f for f in facts if f.validation_status == VALIDATION_VALIDATED]
    revenue = [f for f in validated if f.label == FIELD_REVENUE]
    assert revenue, "expected the Group revenue figure to validate, not conflict"
    # The more precise (million-scale) representative is preferred once both
    # candidates are known to agree.
    assert revenue[0].value_numeric == 22420.0
    assert revenue[0].scale == "million"


def test_materially_different_scaled_values_still_conflict():
    """Two genuinely different values (not just rounding) for the SAME
    label/period/scope must still be rejected, never silently merged."""
    excerpts = [
        _exc("X1", "Group revenue reached EUR20 billion for the year 2026."),
        _exc("X2", "Group revenue reached EUR22,420 million for the year 2026."),
    ]
    facts = validate_extracted_facts(
        _extraction(excerpts), issuer_context=IssuerContext(company_name="Issuer")
    )
    revenue = [f for f in facts if f.label == FIELD_REVENUE]
    assert revenue
    assert all(f.validation_status != VALIDATION_VALIDATED for f in revenue)


def test_scaled_and_unscaled_mention_of_the_same_raw_digits_still_agree():
    """A table cell (scale known from the table header) and an unscaled prose
    duplicate of the SAME literal digits must not be multiplied lopsidedly
    into a false conflict (a real regression caught during this fix)."""
    from app.services.sources.extracted_fact_validator import _Candidate, _candidates_agree

    a = _Candidate(
        label=FIELD_REVENUE, period="2024", value_numeric=20616.0, value_text="20,616",
        unit="currency_amount", currency="EUR", scale="million", page_number=1,
        table_location="p1:t0", method="native_pdf", base_confidence=0.9,
        fully_qualified=True, status=VALIDATION_VALIDATED,
    )
    b = _Candidate(
        label=FIELD_REVENUE, period="2024", value_numeric=20616.0, value_text="20,616",
        unit="currency_amount", currency=None, scale=None, page_number=1,
        table_location="X1", method="native_pdf", base_confidence=0.5,
        fully_qualified=False, status="excerpt_only",
    )
    assert _candidates_agree(a, b)


# =========================================================================== #
# Prose scope inference extensions                                            #
# =========================================================================== #


def test_possessive_subject_construction_resolves_segment_scope():
    """'the X were ... able to grow their <metric> to <value>' — a common
    real-report shape the reporting-verb list ('reported'/'posted'/...) does
    not cover."""
    sentence = (
        "Led by strong top-line momentum, the Jewellery Maisons were "
        "therefore able to grow their operating profit to EUR5 billion, "
        "reaching an operating margin of 30.5%."
    )
    assert _infer_prose_scope(sentence) == "Jewellery Maisons"


def test_prepositional_group_level_claim_resolves_group_scope():
    """'At Group level, ...' has no named grammatical subject at all, but is
    an explicit Group-scope claim (reuses ``scope_claim_signal``)."""
    sentence = "At Group level, operating profit came in at EUR4.5 billion."
    assert _infer_prose_scope(sentence) == "group"


def test_sentence_with_no_structural_signal_stays_unscoped():
    sentence = "The operating result reached EUR107 million, compared to EUR175 million."
    assert _infer_prose_scope(sentence) is None


def test_net_cash_field_never_matches_a_net_cash_inflow_movement():
    """'net cash inflow of EUR30 million' describes a CASH-FLOW MOVEMENT
    (proceeds from share options), not a net cash POSITION/balance — a real
    live mis-label that polluted the net_cash field."""
    excerpt = DocumentExcerpt(
        excerpt_id="X1",
        heading=None,
        ancestor_heading=None,
        text=(
            "Proceeds from the exercise of share options amounted to a net "
            "cash inflow of EUR30 million during the period."
        ),
        page_number=6,
        char_count=100,
        confidence="medium",
        evidence_type="general",
    )
    facts = _parse_excerpt(excerpt, None)
    assert not any(f.field == FIELD_NET_CASH for f in facts)


def test_revenue_field_never_matches_bare_of_sales_ratio_qualifier():
    """'64.4% of sales, down from 66.9%' (a margin-of-sales ratio phrase) must
    never be mistaken for a headline sales/revenue figure — a real live
    false-positive that let an unrelated percentage become a 'revenue'
    candidate."""
    excerpt = DocumentExcerpt(
        excerpt_id="X1",
        heading=None,
        ancestor_heading=None,
        text=(
            "Gross profit amounted to EUR14,438 million, corresponding to "
            "64.4% of sales, down from 66.9% in the prior year."
        ),
        page_number=5,
        char_count=100,
        confidence="medium",
        evidence_type="general",
    )
    facts = _parse_excerpt(excerpt, None)
    assert not any(f.field == FIELD_REVENUE for f in facts)


# =========================================================================== #
# Cross-excerpt period inference — gated on positive scope evidence           #
# =========================================================================== #


def test_group_scoped_fact_missing_its_own_year_inherits_the_document_dominant_period():
    excerpts = [
        _exc("X1", "The Group reported sales of EUR22,420 million in fiscal 2026."),
        _exc("X2", "At Group level, operating profit came in at EUR4,492 million."),
    ]
    facts = validate_extracted_facts(
        _extraction(excerpts), issuer_context=IssuerContext(company_name="Issuer")
    )
    op_profit = [
        f
        for f in facts
        if f.label == FIELD_OPERATING_PROFIT and f.validation_status == VALIDATION_VALIDATED
    ]
    assert op_profit and op_profit[0].period == "2026"
    assert op_profit[0].value_numeric == 4492.0


def test_unscoped_fact_missing_its_own_year_never_inherits_a_period():
    """An unscoped fact stays exactly as before this fix — it must keep
    requiring its OWN local period, or it could silently collide with a
    different, also-unscoped fact under (label, period, scope=None)."""
    excerpts = [
        _exc("X1", "The Group reported sales of EUR22,420 million in fiscal 2026."),
        _exc("X2", "Operating profit came in at EUR4,492 million."),
    ]
    facts = validate_extracted_facts(
        _extraction(excerpts), issuer_context=IssuerContext(company_name="Issuer")
    )
    op_profit = [f for f in facts if f.label == FIELD_OPERATING_PROFIT]
    assert op_profit
    assert all(f.validation_status != VALIDATION_VALIDATED for f in op_profit)


def test_period_inference_never_lets_an_unrelated_scoped_figure_steal_an_explicit_ones_slot():
    """The real live case: an unrelated 'EUR134 million net one-time charges'
    mention sitting near an incidental 'of Group sales' ratio-base phrase
    must never be allowed to newly conflict with the genuine, explicitly
    -dated 'Group sales reached EUR22.4 billion' figure merely because both
    lack their own stated year after inference."""
    excerpts = [
        _exc(
            "X1",
            "Chairman's review. Group sales reached EUR22.4 billion for the "
            "year ended 31 March 2026, an increase of 11%.",
        ),
        _exc(
            "X2",
            "During the year under review, they represented 2% of Group "
            "sales and included EUR134 million net one-time unallocated "
            "charges mainly related to impairments.",
        ),
    ]
    facts = validate_extracted_facts(
        _extraction(excerpts), issuer_context=IssuerContext(company_name="Issuer")
    )
    validated_revenue = [
        f
        for f in facts
        if f.label == FIELD_REVENUE and f.validation_status == VALIDATION_VALIDATED
    ]
    assert validated_revenue, "the genuine Group sales figure must still validate"
    assert validated_revenue[0].value_numeric != 134.0


# =========================================================================== #
# Active fact lifecycle — atomic supersession, not additive-only              #
# =========================================================================== #


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
def flags_on(monkeypatch):
    monkeypatch.setattr(app_settings, "primary_document_ingestion_enabled", True, raising=False)
    monkeypatch.setattr(app_settings, "report_citation_persistence_enabled", True, raising=False)
    return app_settings


async def _add_company(session) -> Company:
    company = Company(
        id=uuid.uuid4(), ticker="CFR", exchange="SIX", name="Example Issuer",
        country="Switzerland", sector="Consumer Discretionary", industry="Luxury Goods",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_run(session) -> AgentRun:
    run = AgentRun(id=uuid.uuid4(), workflow_name="company_analysis", status="completed")
    session.add(run)
    await session.flush()
    return run


def _fact(*, label: str, value_numeric: float, period: str = "2026") -> ValidatedFact:
    return ValidatedFact(
        label=label, value_numeric=value_numeric, value_text=str(value_numeric),
        unit="currency_amount", currency="EUR", scale="million", period=period,
        page_number=6, table_location="X2", extraction_method="native_pdf",
        confidence=0.9, validation_status=VALIDATION_VALIDATED, needs_human_review=True,
    )


def _artifact(*, content_hash: str, facts: list[ValidatedFact]) -> PrimaryDocumentArtifact:
    extraction = PrimaryDocumentExtraction(
        content_hash=content_hash, mime_type="application/pdf",
        extraction_method="native_pdf", status=STATUS_EXTRACTED, page_count=85,
    )
    return PrimaryDocumentArtifact(
        source_url="https://issuer.example/ar2026.pdf", document_type="annual_report",
        title="FY26 Annual Report", retrieved_at=_utcnow(), status=STATUS_EXTRACTED,
        extraction=extraction, validated_facts=facts,
    )


async def test_stale_fact_absent_from_a_new_generation_is_deactivated(session, flags_on):
    """A. A prior generation's ``total_debt=4880`` (later understood to be a
    since-corrected mislabel of ``operating_cash_flow=4880``) must become
    ``is_active=False`` once a genuinely NEW generation supersedes it."""
    company = await _add_company(session)
    run = await _add_run(session)
    content_hash = "e" * 64

    await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact(content_hash=content_hash, facts=[_fact(label="total_debt", value_numeric=4880.0)])],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()

    await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact(content_hash=content_hash, facts=[_fact(label="operating_cash_flow", value_numeric=4880.0)])],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()

    rows = (await session.execute(select(ExtractedFact))).scalars().all()
    active = [r for r in rows if r.is_active]
    inactive = [r for r in rows if not r.is_active]
    assert [r.label for r in active] == ["operating_cash_flow"]
    assert [r.label for r in inactive] == ["total_debt"]


async def test_unchanged_generation_is_a_no_op_not_a_deactivate_and_reinsert(session, flags_on):
    """D. Re-persisting the SAME facts must not create audit-log churn (no
    new rows, no needless deactivation) — the idempotent-rerun invariant."""
    company = await _add_company(session)
    run = await _add_run(session)
    content_hash = "f" * 64
    facts = [_fact(label="net_cash", value_numeric=8496.0)]

    await persist_primary_document_artifacts(
        session, artifacts=[_artifact(content_hash=content_hash, facts=facts)],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()
    first_count = await session.scalar(select(func.count()).select_from(ExtractedFact))

    result = await persist_primary_document_artifacts(
        session, artifacts=[_artifact(content_hash=content_hash, facts=facts)],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()
    second_count = await session.scalar(select(func.count()).select_from(ExtractedFact))

    assert second_count == first_count  # no duplicate row created
    assert result.facts_created == 0
    rows = (await session.execute(select(ExtractedFact))).scalars().all()
    assert all(r.is_active for r in rows)  # nothing was deactivated either


async def test_a_generation_with_zero_facts_deactivates_the_prior_active_set(session, flags_on):
    """A complete, successful current generation that legitimately produced
    NO structured facts still correctly shrinks the active set to match —
    the active set always equals the LATEST successful generation's output."""
    company = await _add_company(session)
    run = await _add_run(session)
    content_hash = "0" * 64

    await persist_primary_document_artifacts(
        session, artifacts=[_artifact(content_hash=content_hash, facts=[_fact(label="net_cash", value_numeric=8496.0)])],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()

    await persist_primary_document_artifacts(
        session, artifacts=[_artifact(content_hash=content_hash, facts=[])],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()

    rows = (await session.execute(select(ExtractedFact))).scalars().all()
    assert rows and all(not r.is_active for r in rows)


async def test_historical_inactive_facts_are_never_deleted(session, flags_on):
    """E. Superseded rows remain queryable for audit — never deleted."""
    company = await _add_company(session)
    run = await _add_run(session)
    content_hash = "1" * 64

    await persist_primary_document_artifacts(
        session, artifacts=[_artifact(content_hash=content_hash, facts=[_fact(label="total_debt", value_numeric=4880.0)])],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()
    await persist_primary_document_artifacts(
        session, artifacts=[_artifact(content_hash=content_hash, facts=[_fact(label="operating_cash_flow", value_numeric=4880.0)])],
        company_id=company.id, agent_run_id=run.id, cfg=flags_on,
    )
    await session.commit()

    doc = (await session.execute(select(ExtractedDocument))).scalar_one()
    all_rows = (
        await session.execute(
            select(ExtractedFact).where(ExtractedFact.extracted_document_id == doc.id)
        )
    ).scalars().all()
    assert {r.label for r in all_rows} == {"total_debt", "operating_cash_flow"}
