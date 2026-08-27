"""
Current-period acceptance — PERIOD TRUTH: what period a figure actually has.

Reaching the current-period document (see ``test_current_period_retrieval``)
made a second, more dangerous class of defect reachable. Reproduced live
against the real Richemont FY27 Q1 sales release and the real Pandora Q2 2026
interim report:

P1  **A quarter's sales became a full year.** The release states its headline
    as plain prose — "Group sales at € 6.3 billion" — with no year in the
    sentence. The validator's fallback then supplied the document's DOMINANT
    explicit token, which in that release is the bare ``2026`` (exchange-rate
    table, corporate calendar, copyright line), producing a *validated* annual
    2026 Group revenue of € 6.3 bn to sit beside the € 22.4 bn FY2026 annual
    Group revenue. The fallback was right to exist and wrong to be period-TYPE
    blind.

P2  **An interim table's bare-year columns became full years.** Pandora's Q2
    2026 lease note heads its columns ``| 2026 | 2025 |`` with the qualifier
    "30 June" wrapped onto the row beneath. Both columns are balance dates six
    months into their years; read as full years they produced a *validated*
    "FY2025 revenue" of DKK 248 m — from a row labelled "Variable leases linked
    to revenue" — competing for the canonical slot against the annual report's
    own DKK 32,549 m.

P3  **An interim document was treated as an authority for a full year.** A
    Q2 2026 report cannot state full-year 2026 results, and its restatement of
    last year's figures is a condensed comparative, not the annual report.

P4  **Half-years and quarters were ordered on one ordinal scale.** H2 2026 and
    Q2 2026 both scored 2 although they end six months apart, and H1 2026 lost
    to Q1 2026 although it ends three months later.

Fully offline and deterministic: no network, no LLM, no Azure, no DB. Document
shapes and period wording are real; every figure is fixture data.
"""

from __future__ import annotations

import pytest

from app.services.final_report_generator import (
    _PRIMARY_FINANCIAL_FACT_FIELDS,
    _build_financial_snapshot,
    _current_period_facts_for,
    _high_confidence_facts_for,
)
from app.services.report_consistency import (
    CURRENT_PERIOD_CONTRADICTION,
    INTERIM_AS_ANNUAL,
    SEVERITY_SERIOUS,
    audit_report_consistency,
)
from app.services.sources.document_period import (
    BASIS_FISCAL_LABEL,
    BASIS_PERIOD_END_PHRASE,
    BASIS_PERIOD_LABEL,
    UNKNOWN_DOCUMENT_PERIOD,
    detect_document_period,
    document_period_of,
)
from app.services.sources.extracted_fact_validator import (
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.financial_period import parse_period
from app.services.sources.period_state import (
    build_reporting_period_state,
    period_end_quarter,
    select_latest_annual,
    select_latest_current_period,
    select_latest_interim,
    select_latest_quarter,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)

_ISSUER = IssuerContext(
    company_name="Issuer SA", legal_name="Issuer SA", ticker="ISS"
)


# =========================================================================== #
# The document's own period                                                   #
# =========================================================================== #


def test_a_fiscal_label_in_the_issuers_own_filename_is_read() -> None:
    """The real Richemont filename. Its BODY never says "FY27"."""
    found = detect_document_period(
        title="Download",
        url=(
            "https://issuer.test/media/ad-hoc-announcement-pursuant-to-art-53-lr-"
            "fy27-q1-sales-en.pdf"
        ),
    )
    assert found.period.key == "2027-Q1"
    assert found.basis == BASIS_FISCAL_LABEL
    assert found.is_interim


def test_a_four_digit_period_label_is_read() -> None:
    found = detect_document_period(title="Issuer Q2 2026 Interim Report")
    assert found.period.key == "2026-Q2"
    assert found.basis == BASIS_PERIOD_LABEL


def test_a_period_end_sentence_is_read_when_nothing_else_states_one() -> None:
    """A regulated storage venue files documents under a numeric id, so the
    front page is the only place the period is stated."""
    found = detect_document_period(
        title=None,
        url="https://storage.test/files/20260722_187106.pdf",
        headings=["FOR ITS FIRST QUARTER ENDED 30 JUNE 2026"],
    )
    assert found.period.key == "2026-Q1"
    assert found.basis == BASIS_PERIOD_END_PHRASE


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Results for the six months ended 30 June 2026", "2026-H1"),
        ("Half-year financial report 2026", "2026-H1"),
        ("Report for the third quarter ended 30 September 2026", "2026-Q3"),
        ("First half 2026 results", "2026-H1"),
    ],
)
def test_period_end_vocabulary(phrase: str, expected: str) -> None:
    assert detect_document_period(title=phrase).period.key == expected


def test_an_annual_report_states_no_interim_period() -> None:
    """The overwhelmingly common case: unchanged behaviour everywhere."""
    for title, url in (
        ("Download the full report", "https://cdn.issuer.test/v1/static/Annual Report 2025"),
        ("Issuer FY26 Annual Report", "https://issuer.test/media/issuer-fy26-annual-report.pdf"),
    ):
        found = detect_document_period(title=title, url=url)
        assert found == UNKNOWN_DOCUMENT_PERIOD or not found.is_interim


def test_a_nine_month_period_is_refused_rather_than_mapped() -> None:
    """Neither a quarter nor a half. Inventing one is the mismapping this
    module exists to prevent."""
    assert not detect_document_period(
        title="Nine months 2026 results", url="https://issuer.test/9m-2026.pdf"
    ).is_known


def test_document_period_detection_never_raises() -> None:
    assert detect_document_period() == UNKNOWN_DOCUMENT_PERIOD
    assert document_period_of(title=None, url=None, extraction=None).is_known is False
    assert document_period_of(title="x", url="y", extraction=object()).is_known is False


# =========================================================================== #
# P1 — an undated figure inherits the DOCUMENT's period, typed               #
# =========================================================================== #


def _excerpt(text: str, *, heading: str = "", page: int = 1, eid: str = "e1"):
    return PrimaryDocumentExcerpt(
        excerpt_id=eid,
        text=text,
        heading=heading or None,
        page_number=page,
        char_count=len(text),
        confidence=0.9,
        extraction_method="native_pdf",
    )


def _extraction(*, excerpts=(), tables=()) -> PrimaryDocumentExtraction:
    return PrimaryDocumentExtraction(
        content_hash="h" * 64,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        page_count=4,
        excerpts=list(excerpts),
        tables=list(tables),
    )


_QUARTERLY_RELEASE = _extraction(
    excerpts=[
        _excerpt(
            "Group sales at EUR 6.3 billion, up by 20% at constant exchange rates",
            heading="Highlights for the quarter",
            eid="e1",
        ),
        _excerpt(
            "Average exchange rates for the year ended 31 March 2026 are used to "
            "convert. (c) Issuer 2026.",
            heading="Appendix 1: Foreign exchange rates",
            page=4,
            eid="e2",
        ),
    ]
)


def test_a_quarters_prose_figure_is_never_stamped_as_a_full_year() -> None:
    """THE P1 regression, on the real sentence shape."""
    period = detect_document_period(title="Issuer FY27 Q1 Sales")
    facts = validate_extracted_facts(
        _QUARTERLY_RELEASE, issuer_context=_ISSUER, document_period=period
    )
    revenue = [f for f in facts if f.label == "revenue" and f.value_numeric == 6.3]
    assert revenue, [f.label for f in facts]
    assert parse_period(revenue[0].period).key == "2027-Q1"
    assert parse_period(revenue[0].period).is_interim


def test_without_a_document_period_the_old_fallback_is_unchanged() -> None:
    facts = validate_extracted_facts(_QUARTERLY_RELEASE, issuer_context=_ISSUER)
    revenue = [f for f in facts if f.label == "revenue" and f.value_numeric == 6.3]
    assert revenue
    assert parse_period(revenue[0].period).key == "2026"


# =========================================================================== #
# P2/P3 — an interim document is not an authority for a full year            #
# =========================================================================== #


_LEASE_NOTE = _extraction(
    excerpts=[_excerpt("Note 7 — Leases", heading="Notes", page=38, eid="e1")],
    tables=[
        ExtractedTable(
            table_index=1,
            page_number=38,
            table_location="p38:m1",
            rows=[
                ["", "2026", "2025"],
                ["DKK million", "30", "30"],
                ["Variable leases linked to revenue", "267", "248"],
            ],
            confidence=0.8,
        )
    ],
)


def test_a_bare_year_column_in_an_interim_document_is_not_a_full_year() -> None:
    """THE P2 regression: "30 June" wrapped onto the next row, so the header
    reads as two full years that are really two balance dates."""
    facts = validate_extracted_facts(
        _LEASE_NOTE,
        issuer_context=_ISSUER,
        document_period=detect_document_period(title="Issuer Q2 2026 Interim Report"),
    )
    annual = [f for f in facts if parse_period(f.period).is_annual]
    assert annual == []


def test_a_bare_year_column_in_an_ANNUAL_document_still_works() -> None:
    """The guard must not disturb the path every annual report takes."""
    facts = validate_extracted_facts(_LEASE_NOTE, issuer_context=_ISSUER)
    assert {parse_period(f.period).key for f in facts if f.period} & {"2025", "2026"}


def test_an_interim_document_never_produces_a_validated_annual_fact() -> None:
    extraction = _extraction(
        excerpts=[
            _excerpt(
                "Revenue was DKK 32,549 million in 2025.",
                heading="Comparatives",
                eid="e1",
            )
        ]
    )
    assert all(
        f.validation_status == VALIDATION_VALIDATED
        for f in validate_extracted_facts(extraction, issuer_context=_ISSUER)
    ), "the fixture must validate WITHOUT a document period, or it proves nothing"

    facts = validate_extracted_facts(
        extraction,
        issuer_context=_ISSUER,
        document_period=detect_document_period(title="Issuer Q2 2026 Interim Report"),
    )
    annual = [f for f in facts if parse_period(f.period).is_annual]
    assert annual, "the comparative must be retained, not deleted"
    assert annual[0].value_numeric == 32549
    assert all(f.validation_status == VALIDATION_EXCERPT_ONLY for f in annual)
    assert any("annual report is the authority" in n for n in annual[0].validation_notes)


def test_the_documents_own_unfinished_year_loses_its_period_entirely() -> None:
    extraction = _extraction(
        excerpts=[
            _excerpt(
                "Revenue reached DKK 7.2 billion in 2026 across the Group.",
                heading="Revenue review",
                eid="e1",
            )
        ]
    )
    facts = validate_extracted_facts(
        extraction,
        issuer_context=_ISSUER,
        document_period=detect_document_period(title="Issuer Q2 2026 Interim Report"),
    )
    revenue = [f for f in facts if f.label == "revenue"]
    assert revenue
    assert all(not parse_period(f.period).is_annual for f in revenue)


# =========================================================================== #
# The four reporting states                                                   #
# =========================================================================== #


def _p(label: str):
    return parse_period(label)


def test_the_latest_annual_selector_ignores_interim_and_quarter() -> None:
    """Required test 6."""
    got = select_latest_annual([_p("2025"), _p("H1 2026"), _p("Q2 2026")])
    assert got.key == "2025"


def test_the_latest_annual_selector_never_falls_back_to_an_interim() -> None:
    assert select_latest_annual([_p("H1 2026"), _p("Q2 2026")]).is_unknown


def test_the_current_period_selector_picks_the_newer_interim() -> None:
    """Required test 5."""
    got = select_latest_current_period([_p("H1 2025"), _p("H1 2026")])
    assert got.key == "2026-H1"


def test_recency_is_decided_by_when_a_period_ENDS() -> None:
    """THE P4 regression: an ordinal-only rank put H2 and Q2 on one scale."""
    assert period_end_quarter(_p("H1 2026")) == 2
    assert period_end_quarter(_p("H2 2026")) == 4
    assert period_end_quarter(_p("Q3 2026")) == 3
    assert period_end_quarter(_p("2026")) is None
    assert select_latest_current_period([_p("H2 2026"), _p("Q2 2026")]).key == "2026-H2"
    assert select_latest_current_period([_p("H1 2026"), _p("Q1 2026")]).key == "2026-H1"


def test_a_quarter_wins_the_tie_with_the_half_it_ends_beside() -> None:
    assert select_latest_current_period([_p("H1 2026"), _p("Q2 2026")]).key == "2026-Q2"


def test_all_four_states_coexist() -> None:
    """Required tests 1 and 2 at the STATE level."""
    state = build_reporting_period_state(
        [_p("2024"), _p("2025"), _p("H1 2026"), _p("Q2 2026")]
    )
    assert state.as_labels() == {
        "latest_annual": "FY2025",
        "latest_interim": "H1 2026",
        "latest_quarter": "Q2 2026",
        "latest_current_period": "Q2 2026",
    }
    assert select_latest_interim([_p("H1 2026")]).key == "2026-H1"
    assert select_latest_quarter([_p("Q2 2026")]).key == "2026-Q2"


def test_an_absent_state_is_named_absent_not_blank() -> None:
    state = build_reporting_period_state([_p("2025")])
    labels = state.as_labels()
    assert labels["latest_annual"] == "FY2025"
    assert labels["latest_current_period"] is None
    assert state.has_current_period is False


# =========================================================================== #
# The report surface                                                          #
# =========================================================================== #


def _fact(field: str, value: float, period: str, **kw) -> dict:
    fact = {
        "field": field,
        "numeric_value": value,
        "value": str(value),
        "period": period,
        "confidence": "high",
        "currency": "DKK",
        "scale": "million",
        "source_url": "https://issuer.test/doc.pdf",
        "source_tier": "T1_primary_filing",
    }
    fact.update(kw)
    return fact


def test_annual_and_interim_coexist_in_separate_slots() -> None:
    """Required tests 1 and 2."""
    facts = [
        _fact("revenue", 32549, "2025"),
        _fact("revenue", 14328, "H1 2026"),
        _fact("operating_profit", 7783, "2025"),
        _fact("operating_profit", 2951, "Q2 2026"),
    ]
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    assert section["revenue_primary_filing"]["value"] == "32549"
    assert section["revenue_current_period"]["value"] == "14328"
    assert section["operating_profit_primary_filing"]["value"] == "7783"
    assert section["operating_profit_current_period"]["value"] == "2951"
    assert section["reporting_periods"]["latest_annual"] == "FY2025"
    assert section["reporting_periods"]["latest_current_period"] == "Q2 2026"


def test_a_quarter_can_never_overwrite_annual_revenue() -> None:
    """Required test 3 — the exact Richemont shape."""
    facts = [
        _fact("revenue", 22.4, "2026", currency="EUR", scale="billion"),
        _fact("revenue", 6.3, "2027-Q1", currency="EUR", scale="billion"),
    ]
    selected = dict(_high_confidence_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["numeric_value"] == 22.4
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    assert section["revenue_primary_filing"]["value"] == "22.4"
    assert section["revenue_current_period"]["value"] == "6.3"


def test_an_h1_can_never_overwrite_a_full_year() -> None:
    """Required test 4."""
    facts = [_fact("revenue", 32549, "2025"), _fact("revenue", 14328, "H1 2026")]
    selected = dict(_high_confidence_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["period"] == "2025"


def test_group_and_segment_scope_survive_the_period_split() -> None:
    """Required test 8."""
    facts = [
        _fact("revenue", 14328, "H1 2026", scope="group"),
        _fact("revenue", 5362, "H1 2026", scope="Specialist Watchmakers"),
    ]
    current = dict(_current_period_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert current["revenue"]["numeric_value"] == 14328
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    assert section["revenue_current_period"]["value"] == "14328"
    assert section["revenue_current_period"]["scope"] == "group"


def test_a_missing_current_period_stays_explicitly_missing() -> None:
    """Required test 9 — never a blank, never an annual figure in its place."""
    section = _build_financial_snapshot(
        None, None, primary_facts=[_fact("revenue", 32549, "2025")]
    )
    assert not any(k.endswith("_current_period") for k in section)
    assert "current_period_note" not in section
    assert section["reporting_periods"]["latest_current_period"] is None


def test_the_report_reconciles_annual_and_current_without_contradiction() -> None:
    """Required test 10."""
    facts = [
        _fact("revenue", 32549, "2025"),
        _fact("revenue", 14328, "H1 2026"),
    ]
    content = {"financial_snapshot": _build_financial_snapshot(None, None, primary_facts=facts)}
    audit = audit_report_consistency(content)
    serious = [f for f in audit.findings if f.severity == SEVERITY_SERIOUS]
    assert serious == [], [f.detail for f in serious]


def test_a_contradictory_reporting_state_is_caught() -> None:
    """The invariant must fail when the states stop matching the slots."""
    facts = [_fact("revenue", 32549, "2025")]
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    section["reporting_periods"]["latest_annual"] = "H1 2026"
    audit = audit_report_consistency({"financial_snapshot": section})
    assert any(f.invariant == INTERIM_AS_ANNUAL for f in audit.findings)

    section = _build_financial_snapshot(None, None, primary_facts=facts)
    section["reporting_periods"]["latest_current_period"] = "2025"
    audit = audit_report_consistency({"financial_snapshot": section})
    assert any(f.invariant == CURRENT_PERIOD_CONTRADICTION for f in audit.findings)


def test_no_interim_figure_is_ever_annualised() -> None:
    facts = [_fact("revenue", 14328, "H1 2026")]
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    values = [
        v.get("value") for k, v in section.items()
        if isinstance(v, dict) and k.startswith("revenue")
    ]
    assert "14328" in values
    assert "28656" not in values  # 2x — the annualisation this system never does
    assert not any(k == "revenue_primary_filing" for k in section)


# =========================================================================== #
# Required test 7 — the round trip                                            #
#                                                                             #
# extract -> persist -> reload -> cache reuse -> report, with an ANNUAL and a #
# CURRENT-PERIOD fact for the SAME field in the same document. Period is part #
# of fact identity, so the two must never collapse into one another.          #
# =========================================================================== #

import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import agent_run as _agent_run  # noqa: F401,E402
from app.models import company as _company  # noqa: F401,E402
from app.models import extracted_document as _extracted_document  # noqa: F401,E402
from app.models import report as _report  # noqa: F401,E402
from app.models import scorecard as _scorecard  # noqa: F401,E402
from app.models import source as _source  # noqa: F401,E402
from app.models.agent_run import AgentRun  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.extracted_document import ExtractedFact  # noqa: E402
from app.services.extracted_document_service import (  # noqa: E402
    load_reusable_documents,
    persist_primary_document_artifacts,
)
from app.services.sources.connectors.company_ir import (  # noqa: E402
    PrimaryDocumentArtifact,
)
from app.services.sources.extracted_fact_validator import ValidatedFact  # noqa: E402

_INTERIM_URL = "https://cdn.issuer.test/v1/static/Issuer Q2 2026 Interim Report"


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
async def session(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


def _cfg() -> Settings:
    return Settings(
        primary_document_ingestion_enabled=True,
        report_citation_persistence_enabled=True,
        primary_document_reuse_ttl_hours=24,
    )


def _vf(value: float, period: str, scope: str = "group") -> ValidatedFact:
    return ValidatedFact(
        label="revenue",
        value_numeric=value,
        value_text=str(value),
        currency="DKK",
        scale="million",
        period=period,
        extraction_method="native_pdf",
        confidence=0.9,
        validation_status=VALIDATION_VALIDATED,
        scope=scope,
    )


def _artifact() -> PrimaryDocumentArtifact:
    return PrimaryDocumentArtifact(
        source_url=_INTERIM_URL,
        document_type="company_ir_primary_document",
        title="Issuer Q2 2026 Interim Report",
        retrieved_at=datetime.now(timezone.utc),
        status=STATUS_EXTRACTED,
        extraction=PrimaryDocumentExtraction(
            content_hash="c" * 64,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status=STATUS_EXTRACTED,
            page_count=43,
            excerpts=[_excerpt("Revenue review", heading="Revenue review")],
        ),
        validated_facts=[
            _vf(32549, "2025"),
            _vf(14328, "2026-H1"),
            _vf(7219, "2026-Q2"),
        ],
    )


async def _seed(session):
    company = Company(
        id=uuid.uuid4(), ticker="ISS", exchange="CO", name="Issuer SA",
        country="Denmark", sector="Consumer Discretionary",
        industry="Luxury Goods", status="new",
    )
    run = AgentRun(id=uuid.uuid4(), workflow_name="company_analysis", status="completed")
    session.add_all([company, run])
    await session.flush()
    await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact()],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=_cfg(),
    )
    await session.commit()
    return company, run


@pytest.mark.asyncio
async def test_annual_and_current_period_facts_survive_persistence(session) -> None:
    """Required test 7, first leg: they are three rows, not one."""
    await _seed(session)
    rows = list(
        (
            await session.execute(
                select(ExtractedFact).where(ExtractedFact.is_active.is_(True))
            )
        ).scalars()
    )
    by_period = {r.period: float(r.value_numeric) for r in rows}
    assert by_period == {"2025": 32549.0, "2026-H1": 14328.0, "2026-Q2": 7219.0}


@pytest.mark.asyncio
async def test_current_period_facts_survive_cache_reuse(session) -> None:
    """Required test 7, second leg — the leg that has failed before. A reused
    document must hand back the SAME periods, or a cached report and a fresh
    one describe different periods from identical bytes."""
    company, _run = await _seed(session)
    reusable = await load_reusable_documents(
        session, company_id=company.id, cfg=_cfg()
    )
    assert reusable, "the document must be reusable at all"
    reused = next(iter(reusable.values()))
    assert reused.pipeline_version_matched is True
    periods = {
        f.period: f.value_numeric for f in reused.artifact.validated_facts
    }
    assert periods == {"2025": 32549.0, "2026-H1": 14328.0, "2026-Q2": 7219.0}

    state = build_reporting_period_state(
        [parse_period(p) for p in periods]
    )
    assert state.as_labels() == {
        "latest_annual": "FY2025",
        "latest_interim": "H1 2026",
        "latest_quarter": "Q2 2026",
        "latest_current_period": "Q2 2026",
    }


# =========================================================================== #
# LIVE-ACCEPTANCE CORRECTIVES                                                 #
#                                                                             #
# Both found by running the fixed pipeline against the real issuers, and both #
# invisible to every test above — the first because no fixture put a prior    #
# sentence's year next to a label, the second because no fixture gave one     #
# field a current figure and another only a prior-year comparative.           #
# =========================================================================== #


def test_a_previous_sentences_year_cannot_steal_a_facts_period() -> None:
    """Live Pandora Q2 2026 report, page 27, verbatim.

    The period window reached BACK across a full stop, found the previous
    sentence's 2025, and — with "first half" also in range — stamped this
    year's EBIT as H1 2025.
    """
    text = (
        "Administrative expenses ended at DKK 1,172 million in the first half "
        "of 2026 compared with DKK 1,253 million in 2025, corresponding to "
        "8.2% of revenue in 2026, a touch lower than the 8.7% last year. EBIT "
        "EBIT for the first half of 2026 was DKK 2,951 million, resulting in "
        "an EBIT margin of 20.6% vs. 20.3% in 2025."
    )
    extraction = _extraction(
        excerpts=[_excerpt(text, heading="Income statement review", page=27)]
    )
    facts = validate_extracted_facts(
        extraction,
        issuer_context=_ISSUER,
        document_period=detect_document_period(title="Issuer Q2 2026 Interim Report"),
    )
    ebit = [f for f in facts if f.label == "operating_profit" and f.value_numeric == 2951]
    assert ebit
    assert parse_period(ebit[0].period).key == "2026-H1"


def test_a_sentence_ending_in_a_year_is_still_a_sentence_boundary() -> None:
    """The case a digit-lookbehind guard would miss — and it is exactly the case
    where the previous sentence's year is about to be stolen."""
    text = (
        "Cash conversion improved against H1 2025. Free cash flow in H1 2026 "
        "was equal to EUR 34.0 million after capex."
    )
    extraction = _extraction(excerpts=[_excerpt(text, heading="Cash flow")])
    facts = validate_extracted_facts(
        extraction,
        issuer_context=_ISSUER,
        document_period=detect_document_period(title="Issuer H1 2026 Results"),
    )
    fcf = [f for f in facts if f.label == "free_cash_flow"]
    assert fcf
    assert parse_period(fcf[0].period).key == "2026-H1"


def test_a_decimal_point_is_not_a_sentence_boundary() -> None:
    """Over-clipping would drop the very year the window exists to find."""
    text = "Operating margin of 20.6% and revenue of DKK 14,328 million in H1 2026."
    extraction = _extraction(excerpts=[_excerpt(text, heading="Highlights")])
    facts = validate_extracted_facts(
        extraction,
        issuer_context=_ISSUER,
        document_period=detect_document_period(title="Issuer Q2 2026 Interim Report"),
    )
    revenue = [f for f in facts if f.label == "revenue"]
    assert revenue
    assert parse_period(revenue[0].period).key == "2026-H1"


def test_a_current_period_slot_never_holds_a_prior_period() -> None:
    """Live Moncler report: ``revenue_current_period`` read Q2 2025 beside an
    H1 2026 EBIT, under a heading that says current."""
    facts = [
        _fact("operating_profit", 245.4, "H1 2026", currency="EUR"),
        _fact("net_income", 164.7, "H1 2026", currency="EUR"),
        _fact("revenue", 91.1, "Q2 2025", currency="EUR"),
    ]
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    assert section["operating_profit_current_period"]["value"] == "245.4"
    assert "revenue_current_period" not in section
    assert section["reporting_periods"]["latest_current_period"] == "H1 2026"
    assert section["reporting_periods"]["latest_quarter"] is None


def test_a_half_and_a_quarter_ending_together_are_one_current_period() -> None:
    """H1 2026 and Q2 2026 both end 30 June: an issuer stating both is
    reporting ONE current period two ways, not two different ones."""
    facts = [
        _fact("revenue", 14328, "H1 2026"),
        _fact("operating_profit", 2026, "Q2 2026"),
    ]
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    assert section["revenue_current_period"]["period"] == "H1 2026"
    assert section["operating_profit_current_period"]["period"] == "Q2 2026"
    assert section["reporting_periods"]["latest_interim"] == "H1 2026"
    assert section["reporting_periods"]["latest_quarter"] == "Q2 2026"


def test_the_current_period_note_never_lists_a_prior_period() -> None:
    facts = [
        _fact("operating_profit", 245.4, "H1 2026"),
        _fact("revenue", 91.1, "Q2 2025"),
    ]
    section = _build_financial_snapshot(None, None, primary_facts=facts)
    note = section["current_period_note"]
    assert note["periods"] == ["H1 2026"]
    assert "2025" not in note["value"]


def test_the_older_figure_is_excluded_from_the_slot_not_deleted() -> None:
    """It stays available as evidence; the historical series is where a
    comparison belongs."""
    facts = [
        _fact("revenue", 14328, "H1 2026"),
        _fact("revenue", 13900, "H1 2025"),
    ]
    current = dict(_current_period_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert current["revenue"]["numeric_value"] == 14328
    assert len(facts) == 2  # nothing removed from the caller's own list
