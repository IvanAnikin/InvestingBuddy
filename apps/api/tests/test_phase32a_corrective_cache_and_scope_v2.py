"""
Phase 32A corrective (v2) — derived-fact cache correctness, prose scope
inference, parser trend-clause/period fixes, LVMH vocabulary, and discovery
rationale linkage.

Fully offline and deterministic: no network, no LLM, no Azure. Reuses the
same in-memory SQLite async DB pattern as
``tests/test_phase32a_evidence_quality_corrective.py``.

Root cause under test (live CFR/MC staging acceptance, 2026-08-13):
  * MC: a document's persisted 7 TABLE-derived facts were silently dropped
    and the document was wrongly restamped "current" after an
    excerpts-only (prose-only) revalidation could only recover 2 of 9
    facts (PR #104 regression).
  * CFR: "The Group's Specialist Watchmakers reported sales of EUR3.1
    billion" became an unscoped/Group "revenue" fact; "Operating profit
    for the year grew by 1% to EUR4,492 million" parsed as ``1`` (the
    percentage), not ``4,492`` (the absolute value).

No real company name is used in PRODUCTION code — Richemont/LVMH-style
names/figures appear ONLY in test fixtures, modelling the real regression
shape without hardcoding an issuer's actual vocabulary into product logic.
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
from app.services.final_report_generator import _build_discovery_rationale
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.document_text_extractor import DocumentExcerpt, DocumentTextExtraction
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
    EXTRACTION_TEXT_LAYER_MIN_VERSION,
    LEGACY_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    STATUS_EXTRACTION_FAILED,
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import (
    FIELD_NET_CASH,
    FIELD_OPERATING_CASH_FLOW,
    FIELD_OPERATING_MARGIN,
    FIELD_OPERATING_PROFIT,
    FIELD_RECURRING_OPERATING_PROFIT,
    FIELD_REVENUE,
    FIELD_TOTAL_DEBT,
    parse_primary_facts,
)
from app.services.sources.redaction import canonicalize_source_url

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_URL = "https://www.example-issuer.example/reports/ar2026.pdf"


def _cfg(**over) -> Settings:
    base = dict(primary_document_ingestion_enabled=True, report_citation_persistence_enabled=True)
    base.update(over)
    return Settings(**base)


def _extraction(text: str) -> DocumentTextExtraction:
    return DocumentTextExtraction(
        source_url=_URL,
        document_type="html",
        excerpts=[
            DocumentExcerpt(
                excerpt_id="X1",
                heading=None,
                text=text,
                page_number=3,
                char_count=len(text),
                confidence="high",
                evidence_type="general",
            )
        ],
    )


def _facts(text: str) -> dict:
    return {f.field: f for f in parse_primary_facts(_extraction(text))}


# =========================================================================== #
# PARSER — trend-clause year-less qualifier                                   #
# =========================================================================== #


def test_trend_clause_grew_year_less_qualifier_promotes_absolute_value():
    facts = _facts("Operating profit for the year grew by 1% to EUR4,492 million.")
    fact = facts[FIELD_OPERATING_PROFIT]
    assert fact.numeric_value == 4492.0
    assert fact.numeric_value != 1.0
    assert fact.scale == "million"
    assert fact.currency == "EUR"


@pytest.mark.parametrize(
    "verb,expected",
    [
        ("rose", 4492.0),
        ("grew", 4492.0),
        ("increased", 4492.0),
        ("fell", 4492.0),
        ("decreased", 4492.0),
    ],
)
def test_trend_clause_analogous_verbs(verb, expected):
    text = f"Operating profit for the year {verb} by 1% to EUR4,492 million."
    fact = _facts(text)[FIELD_OPERATING_PROFIT]
    assert fact.numeric_value == expected


@pytest.mark.parametrize(
    "qualifier",
    [
        "for the year",
        "for the period",
        "in the year",
        "during the year",
        "for fiscal 2026",
        "for the year ended",
    ],
)
def test_trend_clause_period_qualifier_variants(qualifier):
    text = f"Operating profit {qualifier} grew by 1% to EUR4,492 million."
    fact = _facts(text)[FIELD_OPERATING_PROFIT]
    assert fact.numeric_value == 4492.0


def test_wrong_percentage_delta_never_becomes_the_absolute_value():
    fact = _facts("Net income for the year rose by 3% to EUR1,204 million.").get("net_income")
    assert fact is not None
    assert fact.numeric_value == 1204.0
    assert fact.numeric_value != 3.0


def test_trailing_bare_percentage_mention_does_not_falsely_flag_ambiguity():
    """A live, real-issuer regression (2026-08-17 CFR staging acceptance): a
    second, later mention of the SAME label in one excerpt that states only a
    bare percentage CHANGE with no absolute value nearby (e.g. "operating
    profit was up by 23%", with no scale/currency of its own) was previously
    counted as a second competing "magnitude" for the ambiguity check,
    silently discarding the correctly-parsed FIRST value entirely — even
    though that second match could never itself have produced a valid fact
    (it has neither a scale word nor a currency)."""
    text = (
        "Operating profit for the year grew by 1% to EUR4,492 million, "
        "corresponding to 20.0% of sales. Excluding the unfavourable impact "
        "of foreign exchange rates, operating profit was up by 23%."
    )
    fact = _facts(text)[FIELD_OPERATING_PROFIT]
    assert fact.numeric_value == 4492.0
    assert fact.scale == "million"
    assert fact.currency == "EUR"


def test_two_genuinely_qualified_magnitudes_are_still_ambiguous():
    """The false-positive fix must never weaken GENUINE ambiguity refusal: two
    fully-qualified (scale + currency) mentions of the same label with
    different magnitudes in one excerpt are still refused, not silently
    picked."""
    text = (
        "Operating profit was EUR4,492 million in the period. "
        "Operating profit was EUR9,000 million in the period."
    )
    facts = _facts(text)
    assert FIELD_OPERATING_PROFIT not in facts


def test_ocf_not_parsed_as_debt_from_nearby_borrowings():
    text = (
        "Net cash flow from operating activities was EUR4,880 million, "
        "comprised primarily of collections net of borrowings repayments."
    )
    facts = _facts(text)
    assert facts[FIELD_OPERATING_CASH_FLOW].numeric_value == 4880.0
    assert FIELD_TOTAL_DEBT not in facts


def test_net_cash_period_derived_from_nearest_year_not_first_in_excerpt():
    text = (
        "Group revenue performance in fiscal 2025 was reviewed extensively by "
        "the board and management team over several quarters before the "
        "annual general meeting concluded its business. "
        "The Group's net cash position reached EUR8,496 million in 2026."
    )
    fact = _facts(text)[FIELD_NET_CASH]
    assert fact.numeric_value == 8496.0
    assert fact.period == "2026"


# =========================================================================== #
# LVMH vocabulary gap                                                        #
# =========================================================================== #


def test_profit_from_recurring_operations_canonicalizes_to_recurring_operating_profit():
    text = "Profit from recurring operations amounted to EUR22,806 million in 2026."
    fact = _facts(text)[FIELD_RECURRING_OPERATING_PROFIT]
    assert fact.numeric_value == 22806.0


def test_lvmh_rich_results_fixture_survives():
    text = (
        "Revenue amounted to EUR86,153 million in fiscal 2026. "
        "Profit from recurring operations amounted to EUR22,806 million in 2026. "
        "The recurring operating margin was 26.5% in 2026. "
        "Net profit attributable to the Group was EUR12,036 million in 2026. "
        "Operating free cash flow was EUR9,072 million in 2026. "
        "Net debt stood at EUR12,481 million in 2026. "
        "Total equity was EUR58,730 million in 2026."
    )
    facts = _facts(text)
    assert facts["revenue"].numeric_value == 86153.0
    assert facts["recurring_operating_profit"].numeric_value == 22806.0
    assert facts["recurring_operating_margin"].numeric_value == 26.5
    assert facts["net_income"].numeric_value == 12036.0
    assert facts["operating_free_cash_flow"].numeric_value == 9072.0
    assert facts["net_debt"].numeric_value == 12481.0
    assert facts["total_equity"].numeric_value == 58730.0


# =========================================================================== #
# Prose scope inference                                                      #
# =========================================================================== #


def test_groups_owned_segment_reported_sales_scope_is_the_segment_not_group():
    text = "The Group's Specialist Watchmakers reported sales of EUR3,100 million in 2026."
    fact = _facts(text)[FIELD_REVENUE]
    assert fact.scope == "Specialist Watchmakers"
    assert fact.scope != "group"


def test_named_segment_subject_generated_margin_scope_is_the_segment():
    text = "Jewellery Maisons generated an operating margin of 30.5% in 2026."
    fact = _facts(text)[FIELD_OPERATING_MARGIN]
    assert fact.scope == "Jewellery Maisons"


def test_bare_group_subject_reported_scope_is_group():
    text = "The Group reported sales of EUR22,420 million in fiscal 2026."
    fact = _facts(text)[FIELD_REVENUE]
    assert fact.scope == "group"


def test_unknown_scope_stays_none_when_no_structural_signal():
    text = "Sales were EUR500 million in 2026."
    fact = _facts(text)[FIELD_REVENUE]
    assert fact.scope is None


def test_segment_metric_as_only_regex_match_stays_segment_scoped_despite_group_mention():
    """Section 8 test C — the segment fact is the ONLY regex-matchable
    candidate in an excerpt that ALSO discusses Group performance (no
    number attached to the Group mention). Scope must not depend on two
    matches existing."""
    text = (
        "Group revenue continued to show resilience amid a challenging "
        "macroeconomic backdrop, according to management commentary. "
        "The Group's Specialist Watchmakers reported an operating result "
        "of EUR107 million in 2026."
    )
    facts = _facts(text)
    assert FIELD_REVENUE not in facts  # no number attached to the Group mention
    fact = facts[FIELD_OPERATING_PROFIT]
    assert fact.scope == "Specialist Watchmakers"
    assert fact.numeric_value == 107.0


# =========================================================================== #
# CFR-shaped fixture — generic, company-neutral runtime logic                 #
# =========================================================================== #


def _cfr_shaped_extraction() -> PrimaryDocumentExtraction:
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
        exc(
            "X3",
            "Jewellery Maisons generated an operating margin of 30.5% in 2026.",
        ),
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
        content_hash="f" * 64,
        mime_type="text/html",
        extraction_method="html",
        status=STATUS_EXTRACTED,
        page_count=1,
        excerpts=excerpts,
    )


def test_cfr_shaped_fixture_all_target_facts_reach_validated_status():
    extraction = _cfr_shaped_extraction()
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name="Example Issuer", ticker="XYZ"),
        cfg=Settings(),
    )
    validated = {
        (f.label, f.scope): f for f in facts if f.validation_status == VALIDATION_VALIDATED
    }

    revenue = validated[("revenue", "group")]
    assert revenue.value_numeric == 22420.0

    op_profit = validated[("operating_profit", "group")]
    assert op_profit.value_numeric == 4492.0

    op_margin = validated[("operating_margin", "group")]
    assert op_margin.value_numeric == 20.0

    jewellery_margin = validated[("operating_margin", "Jewellery Maisons")]
    assert jewellery_margin.value_numeric == 30.5

    watchmakers_result = validated[("operating_profit", "Specialist Watchmakers")]
    assert watchmakers_result.value_numeric == 107.0

    ocf = validated[("operating_cash_flow", "group")]
    assert ocf.value_numeric == 4880.0

    net_cash = validated[("net_cash", "group")]
    assert net_cash.value_numeric == 8496.0


def test_cfr_shaped_watchmakers_result_never_conflated_with_group_operating_profit():
    extraction = _cfr_shaped_extraction()
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name="Example Issuer"),
        cfg=Settings(),
    )
    op_profit_facts = [f for f in facts if f.label == "operating_profit"]
    scopes = {f.scope for f in op_profit_facts}
    values = {f.scope: f.value_numeric for f in op_profit_facts}
    assert "group" in scopes and "Specialist Watchmakers" in scopes
    assert values["group"] == 4492.0
    assert values["Specialist Watchmakers"] == 107.0
    assert values["group"] != values["Specialist Watchmakers"]


# =========================================================================== #
# CACHE / DERIVATION — Case A/B/C (MC table-loss regression)                  #
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


async def _add_company(session, *, ticker: str = "MC") -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="EPA",
        name="Example Issuer",
        country="France",
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


def _prose_artifact(*, content_hash: str, text: str = "Revenue was EUR1,250 million in 2026.") -> PrimaryDocumentArtifact:
    """A minimal freshly-``extracted`` artifact with ONE prose excerpt and NO
    structured facts of its own — the caller adds a legacy TABLE-derived
    ``ExtractedFact`` row by hand afterwards (mirrors a real document whose
    original ingestion recognised a table row the current excerpts-only
    replay path cannot reconstruct)."""
    extraction = PrimaryDocumentExtraction(
        content_hash=content_hash,
        mime_type="text/html",
        extraction_method="html",
        status=STATUS_EXTRACTED,
        page_count=1,
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="X1",
                text=text,
                page_number=3,
                section=None,
                heading=None,
                table_location=None,
                extraction_method="html",
                confidence=0.9,
                char_count=len(text),
                evidence_type="general",
            )
        ],
    )
    return PrimaryDocumentArtifact(
        source_url=_URL,
        document_type="annual_report",
        title="FY26 Results",
        retrieved_at=_utcnow(),
        status=STATUS_EXTRACTED,
        extraction=extraction,
        validated_facts=[],
    )


async def _seed_legacy_document_with_table_fact(session, *, company, run, content_hash: str) -> ExtractedDocument:
    """Persist a document, then simulate a LEGACY row: one table-derived
    ExtractedFact (``table_location`` does NOT match the persisted excerpt's
    own id — see ``_is_table_derived_fact``) plus a stale pipeline_version.
    Mirrors the exact real-world MC shape: 7 table-derived facts + 2
    prose-derived facts, only the prose ones survivable from cache alone.
    """
    art = _prose_artifact(content_hash=content_hash)
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=_cfg()
    )
    doc = (
        await session.execute(
            select(ExtractedDocument).where(ExtractedDocument.content_hash == content_hash)
        )
    ).scalar_one()
    session.add(
        ExtractedFact(
            id=uuid.uuid4(),
            extracted_document_id=doc.id,
            label="net_debt",
            value_numeric=8245,
            value_text="8,245",
            unit="currency_amount",
            currency="EUR",
            scale="million",
            period="2026",
            page_number=12,
            table_location="p12:t2",  # a TRUE table locator, not an excerpt id
            extraction_method="native_pdf",
            confidence=0.85,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
            is_active=True,
        )
    )
    doc.pipeline_version = LEGACY_EXTRACTION_PIPELINE_VERSION
    await session.flush()
    return doc


def _full_reextraction_artifact(*, content_hash: str) -> PrimaryDocumentArtifact:
    """A COMPLETE artifact (prose + table) as a real full re-extraction would
    produce — the recovered table fact plus the still-available prose fact."""
    from app.services.sources.extracted_fact_validator import ValidatedFact

    table = ExtractedTable(
        table_location="p12:t2",
        table_index=2,
        page_number=12,
        rows=[["Net debt", "8,245"]],
        row_count=1,
        col_count=2,
        extraction_method="native_pdf",
        confidence=0.85,
        scope="group",
    )
    extraction = PrimaryDocumentExtraction(
        content_hash=content_hash,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        page_count=20,
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="X1",
                text="Revenue was EUR1,250 million in 2026.",
                page_number=3,
                extraction_method="html",
                confidence=0.9,
                char_count=40,
                evidence_type="general",
            )
        ],
        tables=[table],
    )
    validated_facts = [
        ValidatedFact(
            label="net_debt",
            value_numeric=8245.0,
            value_text="8,245",
            unit="currency_amount",
            currency="EUR",
            scale="million",
            period="2026",
            page_number=12,
            table_location="p12:t2",
            extraction_method="native_pdf",
            confidence=0.85,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
            scope="group",
        ),
        ValidatedFact(
            label="revenue",
            value_numeric=1250.0,
            value_text="EUR1,250 million",
            unit="currency_amount",
            currency="EUR",
            scale="million",
            period="2026",
            page_number=3,
            table_location="X1",
            extraction_method="html",
            confidence=0.8,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
        ),
    ]
    return PrimaryDocumentArtifact(
        source_url=_URL,
        document_type="annual_report",
        title="FY26 Results",
        retrieved_at=_utcnow(),
        status=STATUS_EXTRACTED,
        extraction=extraction,
        validated_facts=validated_facts,
    )


async def test_cache_case_b_table_derived_fact_triggers_full_reextraction_on_success(session):
    company = await _add_company(session)
    run = await _add_run(session)
    doc = await _seed_legacy_document_with_table_fact(
        session, company=company, run=run, content_hash="m" * 64
    )

    calls: list[str] = []

    async def fake_extractor(url, *, allowed_domains, title_hint=None, issuer_context=None, cfg=None):
        calls.append(url)
        assert url == _URL
        assert allowed_domains  # scoped to this document's own host only
        return _full_reextraction_artifact(content_hash="m" * 64)

    lookup = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg(), primary_document_extractor=fake_extractor
    )
    reused = lookup[canonicalize_source_url(_URL)]

    assert calls == [_URL]  # exactly one bounded re-extraction attempt
    assert reused.revalidated is True
    labels = {f.label for f in reused.artifact.validated_facts if f.validation_status == VALIDATION_VALIDATED}
    assert {"net_debt", "revenue"} <= labels  # the table fact IS recovered

    await session.refresh(doc)
    assert doc.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION

    rows = (
        await session.execute(
            select(ExtractedFact).where(ExtractedFact.extracted_document_id == doc.id)
        )
    ).scalars().all()
    active = {r.label: r for r in rows if r.is_active}
    inactive = [r for r in rows if not r.is_active]
    assert "net_debt" in active and "revenue" in active
    # The OLD net_debt row is superseded (historical), not deleted, not mixed
    # in with the new active set.
    assert any(r.label == "net_debt" for r in inactive)
    assert len(inactive) >= 1


async def test_cache_case_b_failed_reextraction_does_not_restamp_or_expose_partial_facts(session):
    company = await _add_company(session)
    run = await _add_run(session)
    doc = await _seed_legacy_document_with_table_fact(
        session, company=company, run=run, content_hash="n" * 64
    )

    async def failing_extractor(url, *, allowed_domains, title_hint=None, issuer_context=None, cfg=None):
        extraction = PrimaryDocumentExtraction(
            content_hash="n" * 64,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status=STATUS_EXTRACTION_FAILED,
        )
        return PrimaryDocumentArtifact(
            source_url=_URL,
            status=STATUS_EXTRACTION_FAILED,
            extraction=extraction,
            validated_facts=[],
        )

    lookup = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg(), primary_document_extractor=failing_extractor
    )
    reused = lookup[canonicalize_source_url(_URL)]

    # Case C: fail closed — no structured facts exposed for THIS report...
    assert reused.artifact.validated_facts == []
    # ...but the document is NOT restamped current from a partial result.
    await session.refresh(doc)
    assert doc.pipeline_version == LEGACY_EXTRACTION_PIPELINE_VERSION

    # The prior active facts are left untouched in the database (never
    # silently deleted or re-judged) — just not exposed as current evidence.
    rows = (
        await session.execute(
            select(ExtractedFact).where(ExtractedFact.extracted_document_id == doc.id)
        )
    ).scalars().all()
    assert all(r.is_active for r in rows)


async def test_cache_case_b_content_hash_mismatch_handled_safely(session):
    company = await _add_company(session)
    run = await _add_run(session)
    doc = await _seed_legacy_document_with_table_fact(
        session, company=company, run=run, content_hash="p" * 64
    )

    async def drifted_extractor(url, *, allowed_domains, title_hint=None, issuer_context=None, cfg=None):
        # The document at this URL has genuinely changed since it was last
        # read — a DIFFERENT content hash than the persisted one.
        return _full_reextraction_artifact(content_hash="q" * 64)

    lookup = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg(), primary_document_extractor=drifted_extractor
    )
    reused = lookup[canonicalize_source_url(_URL)]

    # Safe, fail-closed handling: no crash, no fabricated merge of two
    # different filings' facts, no restamp.
    assert reused.artifact.validated_facts == []
    await session.refresh(doc)
    assert doc.pipeline_version == LEGACY_EXTRACTION_PIPELINE_VERSION
    assert doc.content_hash == "p" * 64  # identity of the original row is untouched


async def test_cache_stale_extraction_text_layer_forces_full_reextraction_even_without_table_facts(session):
    """A document with ONLY prose-derived active facts (no table-derived
    fact at all — the exact shape that used to qualify for the excerpts
    -only Case A fast path) but stamped BELOW
    ``EXTRACTION_TEXT_LAYER_MIN_VERSION`` must still go through Case B (one
    bounded re-fetch + re-extraction), never a same-stale-text replay.

    Regression for a live CFR staging finding (PR #107 follow-up,
    2026-08-18): the two-column PDF reading-order fix changed what RAW TEXT
    ``primary_document_extractor`` produces, not just how that text is
    interpreted. A version bump alone (3→4) is not sufficient if Case A's
    "no table-derived fact ⇒ safe to replay the persisted excerpts" branch
    is left untouched — a prose-only document written under the OLD,
    column-interleaved extractor would still be silently re-derived from
    its own stale, garbled excerpt text and keep serving the same wrong
    values (e.g. an operating-cash-flow figure mislabeled as debt).
    """
    company = await _add_company(session)
    run = await _add_run(session)
    content_hash = "n" * 64
    art = _prose_artifact(content_hash=content_hash)
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=_cfg()
    )
    doc = (
        await session.execute(
            select(ExtractedDocument).where(ExtractedDocument.content_hash == content_hash)
        )
    ).scalar_one()
    # Stamped at 3: predates the extraction-text-layer bump to 4, but is
    # NOT the legacy/unstamped baseline either — this is exactly the shape
    # a document persisted by the PREVIOUS (pre-PR-107) deploy would have.
    doc.pipeline_version = EXTRACTION_TEXT_LAYER_MIN_VERSION - 1
    await session.flush()

    calls: list[str] = []

    async def fake_extractor(url, *, allowed_domains, title_hint=None, issuer_context=None, cfg=None):
        calls.append(url)
        return _full_reextraction_artifact(content_hash=content_hash)

    lookup = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg(), primary_document_extractor=fake_extractor
    )
    reused = lookup[canonicalize_source_url(_URL)]

    assert calls == [_URL]  # Case B triggered — a real re-fetch happened, not a replay
    assert reused.revalidated is True

    await session.refresh(doc)
    assert doc.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION


async def test_cache_current_version_document_with_table_facts_retains_fast_path(session):
    """Once a document is stamped current, a document with table-derived
    active facts is NOT re-extracted again — the invariant is about
    REVALIDATION, not about permanently distrusting table-derived facts."""
    company = await _add_company(session)
    run = await _add_run(session)
    doc = await _seed_legacy_document_with_table_fact(
        session, company=company, run=run, content_hash="r" * 64
    )
    doc.pipeline_version = CURRENT_EXTRACTION_PIPELINE_VERSION
    await session.flush()

    async def never_called(*args, **kwargs):
        raise AssertionError("full re-extraction must not run on a current-version document")

    lookup = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg(), primary_document_extractor=never_called
    )
    reused = lookup[canonicalize_source_url(_URL)]
    assert reused.pipeline_version_matched is True
    assert reused.revalidated is False
    labels = {f.label for f in reused.artifact.validated_facts}
    assert "net_debt" in labels  # the table fact IS served from the fast path


# =========================================================================== #
# DISCOVERY rationale — cross-run isolation at the rationale-section level    #
# =========================================================================== #


def test_discovery_rationale_built_from_exact_fk_lineage_isolates_across_runs():
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    candidate_a_id = uuid.uuid4()
    candidate_b_id = uuid.uuid4()

    lineage_a = {
        "discovery_run_id": str(run_a_id),
        "discovery_candidate_id": str(candidate_a_id),
        "ticker": "MC",
        "exchange": "EPA",
        "rank": 1,
        "candidate_score": 88.0,
        "score_explanation": "Run A: pricing power thesis match.",
    }
    lineage_b = {
        "discovery_run_id": str(run_b_id),
        "discovery_candidate_id": str(candidate_b_id),
        "ticker": "MC",
        "exchange": "EPA",
        "rank": 4,
        "candidate_score": 61.0,
        "score_explanation": "Run B: travel-retail recovery thesis match.",
    }

    rationale_a = _build_discovery_rationale(None, lineage_a)
    rationale_b = _build_discovery_rationale(None, lineage_b)

    assert rationale_a["available"] is True
    assert rationale_b["available"] is True
    assert rationale_a["discovery_run_id"] == str(run_a_id)
    assert rationale_a["candidate_id"] == str(candidate_a_id)
    assert rationale_b["discovery_run_id"] == str(run_b_id)
    assert rationale_b["candidate_id"] == str(candidate_b_id)
    # No cross-run contamination — each report's rationale reflects ONLY its
    # own exact candidate/run linkage.
    assert rationale_a["discovery_run_id"] != rationale_b["discovery_run_id"]
    assert rationale_a["candidate_id"] != rationale_b["candidate_id"]
    assert "Run B" not in rationale_a["score_explanation"]["value"]
    assert "Run A" not in rationale_b["score_explanation"]["value"]


def test_discovery_rationale_unavailable_without_candidate_or_lineage():
    section = _build_discovery_rationale(None, None)
    assert section["available"] is False
