"""
Private-use production readiness, PR-A — PERSISTED FACT SCOPE (migration 018).

Root cause under test (confirmed against the code at ``bfac6e1``):
``ValidatedFact.scope`` existed only in memory. ``_persist_validated_facts``
wrote every other field and dropped it; ``_rebuild_artifact`` (the cache-reuse
fast path) rebuilt facts with ``scope=None``. Because an ABSENT scope is the
pipeline's implicit "this is the Group figure" convention, a document reused
from cache could promote a SEGMENT figure into a canonical Group slot — the
exact regression Phase 32A fixed for the fresh path only.

The master requirement these tests encode:

    extract -> persist -> reload -> cache reuse -> reconcile -> report

must retain scope EXACTLY. CFR (Group + three business areas, several margins
and profits that only scope tells apart) is the mandatory golden regression.

Fully offline and deterministic: no network, no LLM, no Azure. Uses the same
in-memory SQLite async DB pattern as
``tests/test_phase32a_corrective_cache_and_scope_v2.py``.

No real company name appears in PRODUCTION code — issuer-shaped names/figures
appear ONLY in these fixtures, modelling the real regression shape without
hardcoding an issuer's vocabulary into product logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
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
from app.services.extracted_document_service import (
    load_reusable_documents,
    persist_primary_document_artifacts,
)
from app.services.final_report_generator import (
    _build_financial_snapshot,
    _high_confidence_facts_for,
)
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.fact_scope import (
    GROUP_SCOPE,
    SCOPE_TYPE_GROUP,
    SCOPE_TYPE_SEGMENT,
    UNKNOWN_SCOPE,
    FactScope,
    parse_scope,
    same_scope,
    scope_columns,
    scope_from_columns,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)

_URL = "https://example-issuer.test/reports/fy26-annual-report.pdf"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cfg(**over) -> Settings:
    base = {
        "primary_document_ingestion_enabled": True,
        "report_citation_persistence_enabled": True,
        "primary_document_reuse_ttl_hours": 24,
    }
    base.update(over)
    return Settings(**base)


# =========================================================================== #
# 1. The typed scope value object                                             #
# =========================================================================== #


@pytest.mark.parametrize(
    "raw",
    ["group", "Group", "  the group ", "CONSOLIDATED", "consolidated group", "Groupe"],
)
def test_group_vocabulary_parses_to_group(raw: str) -> None:
    assert parse_scope(raw).scope_type == SCOPE_TYPE_GROUP
    assert parse_scope(raw).scope_key == "group"


@pytest.mark.parametrize("raw", [None, "", "   ", "—", " : "])
def test_absent_or_decorative_label_is_unknown_never_group(raw) -> None:
    scope = parse_scope(raw)
    assert scope.is_unknown
    assert scope.scope_type is None
    assert scope.scope_key is None
    # The critical negative: unknown must never be coerced to group at any layer.
    assert not scope.is_group


def test_unrecognised_business_area_is_a_segment_not_the_group() -> None:
    scope = parse_scope("Specialist Watchmakers")
    assert scope.scope_type == SCOPE_TYPE_SEGMENT
    assert scope.scope_name == "Specialist Watchmakers"
    assert scope.scope_key == "segment:specialist watchmakers"
    assert not scope.is_group


def test_scope_key_is_case_and_whitespace_stable_but_name_is_preserved() -> None:
    a = parse_scope("Specialist Watchmakers")
    b = parse_scope("  SPECIALIST   WATCHMAKERS ")
    assert a.scope_key == b.scope_key
    assert a.scope_name != b.scope_name  # the as-found label is not destroyed
    assert same_scope(a, b)


def test_two_different_segments_are_never_the_same_series() -> None:
    assert not same_scope("Jewellery Maisons", "Specialist Watchmakers")


def test_two_unknown_scopes_are_not_declared_the_same_series() -> None:
    """Fail-closed comparability: "we don't know what either of these is" is
    not evidence that they are the same thing."""
    assert not same_scope(None, None)
    assert not same_scope(UNKNOWN_SCOPE, UNKNOWN_SCOPE)


def test_label_round_trips_through_parse() -> None:
    for raw in ("group", "Specialist Watchmakers", None):
        scope = parse_scope(raw)
        assert parse_scope(scope.label) == scope


def test_scope_columns_shape_matches_migration_018() -> None:
    cols = scope_columns("Jewellery Maisons")
    assert set(cols) == {"scope_type", "scope_name", "scope_key"}
    assert cols == {
        "scope_type": "segment",
        "scope_name": "Jewellery Maisons",
        "scope_key": "segment:jewellery maisons",
    }
    assert scope_columns(None) == {
        "scope_type": None,
        "scope_name": None,
        "scope_key": None,
    }


def test_segment_row_that_lost_its_name_degrades_to_unknown_not_group() -> None:
    """A defensive read of a malformed row. An anonymous segment is not a
    usable identity — and it must certainly not become the Group."""
    assert scope_from_columns("segment", None, None).is_unknown


def test_unrecognised_persisted_scope_type_degrades_to_unknown() -> None:
    assert scope_from_columns("subsidiary", None, None).is_unknown


def test_persisted_scope_key_is_re_derived_not_trusted() -> None:
    """A stale key can never silently re-point a fact at a different series."""
    rebuilt = scope_from_columns("segment", "Jewellery Maisons", "segment:wrong")
    assert rebuilt.scope_key == "segment:jewellery maisons"


def test_human_label_never_emits_none() -> None:
    assert GROUP_SCOPE.human_label() == "Group"
    assert parse_scope("Watches").human_label() == "Watches"
    assert UNKNOWN_SCOPE.human_label() == "Scope not stated"
    assert "None" not in UNKNOWN_SCOPE.human_label()


def test_scope_name_is_clipped_to_the_persisted_column_width() -> None:
    scope = parse_scope("X" * 500)
    assert scope.scope_name is not None
    assert len(scope.scope_name) <= 200
    assert scope.scope_key is not None
    assert len(scope.scope_key) <= 220


# =========================================================================== #
# 2. The ORM column exists and is additive                                    #
# =========================================================================== #


def test_extracted_fact_carries_the_three_scope_columns() -> None:
    cols = ExtractedFact.__table__.columns
    for name in ("scope_type", "scope_name", "scope_key"):
        assert name in cols, f"migration 018 column {name} missing from the model"
        assert cols[name].nullable, f"{name} must be nullable — unknown stays unknown"


# =========================================================================== #
# 3. CFR golden regression — the fixture the whole campaign protects          #
# =========================================================================== #


def _cfr_shaped_extraction() -> PrimaryDocumentExtraction:
    """Group + three business areas. Several figures are distinguishable ONLY
    by scope: two operating margins, two operating profits."""

    def exc(excerpt_id: str, text: str, *, heading: str | None = None) -> PrimaryDocumentExcerpt:
        return PrimaryDocumentExcerpt(
            excerpt_id=excerpt_id,
            text=text,
            page_number=4,
            section=heading,
            heading=heading,
            table_location=None,
            extraction_method="html",
            confidence=0.9,
            char_count=len(text),
            evidence_type="general",
        )

    excerpts = [
        exc(
            "X1",
            "The Group reported sales of EUR22,420 million in fiscal 2026.",
            heading="Group financial highlights",
        ),
        exc(
            "X2",
            "Operating profit for the year grew by 1% to EUR4,492 million. "
            "The Group's operating margin was 20.0% in 2026.",
            heading="Group financial highlights",
        ),
        exc("X3", "Jewellery Maisons generated an operating margin of 30.5% in 2026."),
        exc(
            "X4",
            "Group revenue continued to show resilience amid a challenging "
            "macroeconomic backdrop. The Group's Specialist Watchmakers "
            "reported an operating result of EUR107 million in 2026.",
        ),
        exc(
            "X5",
            "Net cash flow from operating activities was EUR4,880 million in 2026.",
            heading="Group financial highlights",
        ),
        exc(
            "X6",
            "The Group's net cash position reached EUR8,496 million in 2026.",
            heading="Group financial highlights",
        ),
    ]
    return PrimaryDocumentExtraction(
        content_hash="c" * 64,
        mime_type="text/html",
        extraction_method="html",
        status=STATUS_EXTRACTED,
        page_count=1,
        excerpts=excerpts,
    )


def _cfr_validated_facts() -> list[ValidatedFact]:
    return validate_extracted_facts(
        _cfr_shaped_extraction(),
        issuer_context=IssuerContext(company_name="Example Issuer", ticker="XYZ"),
        cfg=Settings(),
    )


def _cfr_artifact() -> PrimaryDocumentArtifact:
    return PrimaryDocumentArtifact(
        source_url=_URL,
        document_type="annual_report",
        title="FY26 Annual Report",
        retrieved_at=_utcnow(),
        status=STATUS_EXTRACTED,
        extraction=_cfr_shaped_extraction(),
        validated_facts=_cfr_validated_facts(),
    )


def test_cfr_fixture_still_separates_group_from_every_segment_in_memory() -> None:
    """Unchanged Phase 32A guarantee — this test exists so PR-A can prove it
    still holds AFTER the round-trip below, not just before it."""
    validated = {
        (f.label, f.scope): f
        for f in _cfr_validated_facts()
        if f.validation_status == VALIDATION_VALIDATED
    }
    assert validated[("revenue", "group")].value_numeric == 22420.0
    assert validated[("operating_profit", "group")].value_numeric == 4492.0
    assert validated[("operating_margin", "group")].value_numeric == 20.0
    assert validated[("operating_margin", "Jewellery Maisons")].value_numeric == 30.5
    assert validated[("operating_profit", "Specialist Watchmakers")].value_numeric == 107.0


# =========================================================================== #
# 4. MASTER ROUND-TRIP: extract -> persist -> reload -> reuse -> report       #
# =========================================================================== #


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
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


async def _add_company(session, *, ticker: str = "CFR") -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="SW",
        name="Example Issuer",
        country="Switzerland",
        sector="Consumer Discretionary",
        industry="Luxury Goods",
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


async def _persist_cfr(session, company, run) -> None:
    await persist_primary_document_artifacts(
        session,
        artifacts=[_cfr_artifact()],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=_cfg(),
    )
    await session.commit()


async def _active_facts(session) -> list[ExtractedFact]:
    rows = (
        await session.execute(
            select(ExtractedFact).where(ExtractedFact.is_active.is_(True))
        )
    ).scalars()
    return list(rows)


@pytest.mark.asyncio
async def test_scope_survives_persistence(session) -> None:
    company = await _add_company(session)
    run = await _add_run(session)
    await _persist_cfr(session, company, run)

    by_key = {(f.label, f.scope_key): f for f in await _active_facts(session)}

    group_profit = by_key[("operating_profit", "group")]
    assert group_profit.scope_type == SCOPE_TYPE_GROUP
    assert group_profit.scope_name is None
    assert float(group_profit.value_numeric) == 4492.0

    watch_profit = by_key[("operating_profit", "segment:specialist watchmakers")]
    assert watch_profit.scope_type == SCOPE_TYPE_SEGMENT
    assert watch_profit.scope_name == "Specialist Watchmakers"
    assert float(watch_profit.value_numeric) == 107.0

    jewel_margin = by_key[("operating_margin", "segment:jewellery maisons")]
    assert jewel_margin.scope_type == SCOPE_TYPE_SEGMENT
    assert jewel_margin.scope_name == "Jewellery Maisons"
    assert float(jewel_margin.value_numeric) == 30.5


@pytest.mark.asyncio
async def test_group_and_segment_facts_are_not_collapsed_by_dedupe(session) -> None:
    """Scope is part of fact IDENTITY. Two facts sharing label/period/value but
    differing in scope must both survive — the old (label, period, value) key
    kept only one, with no scope at all."""
    company = await _add_company(session)
    run = await _add_run(session)

    same_value_facts = [
        ValidatedFact(
            label="operating_profit",
            value_numeric=500.0,
            value_text="500",
            currency="EUR",
            scale="million",
            period="FY2026",
            extraction_method="html",
            confidence=0.9,
            validation_status=VALIDATION_VALIDATED,
            scope="group",
        ),
        ValidatedFact(
            label="operating_profit",
            value_numeric=500.0,
            value_text="500",
            currency="EUR",
            scale="million",
            period="FY2026",
            extraction_method="html",
            confidence=0.9,
            validation_status=VALIDATION_VALIDATED,
            scope="Specialist Watchmakers",
        ),
    ]
    artifact = _cfr_artifact()
    artifact.validated_facts = same_value_facts
    await persist_primary_document_artifacts(
        session,
        artifacts=[artifact],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=_cfg(),
    )
    await session.commit()

    keys = {f.scope_key for f in await _active_facts(session)}
    assert keys == {"group", "segment:specialist watchmakers"}


@pytest.mark.asyncio
async def test_scope_survives_reload_and_cache_reuse(session) -> None:
    """THE regression. Before PR-A this returned ``scope=None`` for every fact,
    which downstream reads as "this is the Group figure"."""
    company = await _add_company(session)
    run = await _add_run(session)
    await _persist_cfr(session, company, run)

    reused = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg()
    )
    assert reused, "the freshly persisted document should be reusable"
    doc = next(iter(reused.values()))
    assert doc.pipeline_version_matched is True

    facts = {(f.label, f.scope): f for f in doc.artifact.validated_facts}
    assert facts[("operating_profit", "group")].value_numeric == 4492.0
    assert facts[("operating_profit", "Specialist Watchmakers")].value_numeric == 107.0
    assert facts[("operating_margin", "Jewellery Maisons")].value_numeric == 30.5
    assert facts[("operating_margin", "group")].value_numeric == 20.0
    # And nothing came back unscoped.
    assert all(f.scope is not None for f in doc.artifact.validated_facts)


@pytest.mark.asyncio
async def test_reused_segment_fact_never_reaches_a_canonical_group_slot(session) -> None:
    """End of the chain: reuse -> reconcile -> report. A cache-reused
    Specialist Watchmakers operating profit must NOT fill the Group
    ``operating_profit_primary_filing`` slot."""
    company = await _add_company(session)
    run = await _add_run(session)
    await _persist_cfr(session, company, run)

    reused = await load_reusable_documents(session, company_id=company.id, cfg=_cfg())
    primary_facts = [
        {
            "field": f.label,
            "value": f.value_text or str(f.value_numeric),
            "numeric_value": f.value_numeric,
            "currency": f.currency,
            "scale": f.scale,
            "period": f.period,
            "scope": f.scope,
            "confidence": "high",
            "source_url": _URL,
        }
        for f in next(iter(reused.values())).artifact.validated_facts
    ]

    section = _build_financial_snapshot(
        {"source_tier": "T1_primary_filing", "is_mock": False},
        None,
        primary_facts,
    )
    op = section.get("operating_profit_primary_filing")
    assert op is not None, "the GROUP operating profit should still be promoted"
    assert op["numeric_value"] == 4492.0
    assert op["numeric_value"] != 107.0, "segment figure reached a Group slot"


@pytest.mark.asyncio
async def test_idempotent_repersist_does_not_churn_scoped_rows(session) -> None:
    """Re-running the same generation must not deactivate/reinsert — the
    scope-aware identity key must compare equal to itself."""
    company = await _add_company(session)
    run = await _add_run(session)
    await _persist_cfr(session, company, run)
    first = {f.id for f in await _active_facts(session)}

    await persist_primary_document_artifacts(
        session,
        artifacts=[_cfr_artifact()],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=_cfg(),
    )
    await session.commit()
    second = {f.id for f in await _active_facts(session)}
    assert first == second


@pytest.mark.asyncio
async def test_legacy_rows_stay_unknown_and_are_never_backfilled_to_group(
    session,
) -> None:
    """Migration 018 deliberately backfills nothing. A pre-018 row carries no
    recoverable scope signal, and guessing ``group`` would manufacture exactly
    the false Group attribution the column exists to prevent."""
    company = await _add_company(session)
    run = await _add_run(session)
    doc = ExtractedDocument(
        id=uuid.uuid4(),
        content_hash="d" * 64,
        canonical_url=_URL,
        provider="company_ir",
        source_type="annual_report",
        source_tier="T1_primary_filing",
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        retrieved_at=_utcnow(),
        excerpts_json=[],
        pipeline_version=None,
        company_id=company.id,
        agent_run_id=run.id,
    )
    session.add(doc)
    await session.flush()
    session.add(
        ExtractedFact(
            id=uuid.uuid4(),
            extracted_document_id=doc.id,
            label="operating_profit",
            value_numeric=107,
            value_text="107",
            currency="EUR",
            scale="million",
            period="FY2026",
            extraction_method="native_pdf",
            confidence=0.9,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
            is_active=True,
        )
    )
    await session.commit()

    row = (await _active_facts(session))[0]
    assert row.scope_type is None
    assert row.scope_key is None
    assert scope_from_columns(row.scope_type, row.scope_name, row.scope_key).is_unknown


@pytest.mark.asyncio
async def test_legacy_row_is_not_trusted_at_the_current_pipeline_version(
    session,
) -> None:
    """A pre-018 row is scope-unknown by construction, so it must not take the
    same-version fast path. The pipeline-version bump is what forces it back
    through the current parser."""
    company = await _add_company(session)
    run = await _add_run(session)
    doc = ExtractedDocument(
        id=uuid.uuid4(),
        content_hash="e" * 64,
        canonical_url=_URL,
        provider="company_ir",
        source_type="annual_report",
        source_tier="T1_primary_filing",
        mime_type="text/html",
        extraction_method="html",
        status=STATUS_EXTRACTED,
        retrieved_at=_utcnow(),
        excerpts_json=[],
        pipeline_version=11,
        company_id=company.id,
        agent_run_id=run.id,
    )
    session.add(doc)
    await session.commit()

    assert CURRENT_EXTRACTION_PIPELINE_VERSION > 11
    reused = await load_reusable_documents(session, company_id=company.id, cfg=_cfg())
    assert reused
    assert next(iter(reused.values())).pipeline_version_matched is False


# =========================================================================== #
# 5. Report-layer scope gate, exercised directly                              #
# =========================================================================== #


def _fact(field: str, value: float, scope: str | None) -> dict:
    return {
        "field": field,
        "value": str(value),
        "numeric_value": value,
        "scope": scope,
        "confidence": "high",
        "source_url": _URL,
    }


def test_segment_fact_is_never_selected_for_a_canonical_field() -> None:
    fields = frozenset({"revenue"})
    selected = _high_confidence_facts_for(
        [_fact("revenue", 3100.0, "Specialist Watchmakers")], fields
    )
    assert selected == []


def test_group_fact_is_selected() -> None:
    fields = frozenset({"revenue"})
    selected = _high_confidence_facts_for([_fact("revenue", 22420.0, "group")], fields)
    assert [f["numeric_value"] for _, f in selected] == [22420.0]


def test_unscoped_fact_retains_the_pre_existing_implicit_group_convention() -> None:
    """Deliberate and documented: an unscoped fact on the FRESH path has always
    meant Group. PR-A does not change that — it stops a SEGMENT fact from
    BECOMING unscoped in the database."""
    fields = frozenset({"revenue"})
    selected = _high_confidence_facts_for([_fact("revenue", 22420.0, None)], fields)
    assert [f["numeric_value"] for _, f in selected] == [22420.0]


def test_group_wins_even_when_the_segment_fact_is_listed_first() -> None:
    fields = frozenset({"revenue"})
    selected = _high_confidence_facts_for(
        [
            _fact("revenue", 3100.0, "Specialist Watchmakers"),
            _fact("revenue", 22420.0, "group"),
        ],
        fields,
    )
    assert [f["numeric_value"] for _, f in selected] == [22420.0]


def test_fact_scope_equality_is_value_based() -> None:
    assert FactScope(SCOPE_TYPE_SEGMENT, "Watches") == FactScope(
        SCOPE_TYPE_SEGMENT, "Watches"
    )
    assert GROUP_SCOPE != UNKNOWN_SCOPE
