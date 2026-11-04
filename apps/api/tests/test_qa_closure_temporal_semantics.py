"""ADR-080 — temporal semantics: a forward date never becomes a reporting period.

QA campaign 2026-11, CLLS5-1 / CLLS4-2. A strategic-update 6-K published 2026-10-28
(the pivot press release) said the company's cash runway reached "into H2 2028". The
document-period detector read the first period label in the body and stored the filing
in the corpus as an "interim report for 2028-H2" (another as 2027-H2). The investigator
then discarded the one H1 2026 net-loss statement, because it cited the H1 2026 6-K/A
together with that 6-K — two "disagreeing" periods, one of them a forecast.

Every test here is offline: no SEC, no network, SQLite in memory.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.services.sources import date_semantics as ds
from app.services.sources.document_period import (
    BASIS_FISCAL_PERIOD_END,
    BASIS_PERIOD_END_PHRASE,
    BASIS_PERIOD_LABEL,
    BASIS_SEC_FISCAL_PERIOD_OF_REPORT,
    BASIS_SEC_PERIOD_OF_REPORT,
    DocumentPeriod,
    detect_document_period,
    document_period_of,
    fiscal_year_end_month_of,
    sec_period_of_report,
)
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_HALF,
    ReportingPeriod,
)

PUBLISHED = date(2026, 10, 28)

# --------------------------------------------------------------------------- #
# 1. The classifier — one mention, its own sentence                            #
# --------------------------------------------------------------------------- #


def _classify(sentence: str, mention: str, role: str = ds.ROLE_BODY) -> str:
    from app.services.sources.document_period import _normalise

    text = _normalise(sentence)
    needle = _normalise(mention)
    start = text.index(needle)
    return ds.classify_date_mention(text, start, start + len(needle), role=role)


@pytest.mark.parametrize(
    ("sentence", "mention", "expected"),
    [
        ("We now expect a cash runway into H2 2028.", "H2 2028", ds.FORECAST_HORIZON),
        ("Cash is sufficient to fund operations into Q1 2029.", "Q1 2029",
         ds.FORECAST_HORIZON),
        ("First production in H1 2029 at the project.", "H1 2029", ds.EVENT_DATE),
        ("We reiterate our FY27 Q1 guidance.", "FY27 Q1", ds.GUIDANCE_TARGET_DATE),
        ("The supply contract expires in H2 2030.", "H2 2030", ds.DEADLINE_DATE),
        ("The notes maturing Q1 2031 are unsecured.", "Q1 2031", ds.DEADLINE_DATE),
        ("Topline readout in H2 2027.", "H2 2027", ds.EVENT_DATE),
        ("Topline data expected H2 2027.", "H2 2027", ds.GUIDANCE_TARGET_DATE),
        ("The permit deadline is Q1 2027.", "Q1 2027", ds.DEADLINE_DATE),
        ("Revenue compared with H1 2025.", "H1 2025", ds.HISTORICAL_REFERENCE_DATE),
        ("Results for H1 2026 were in line.", "H1 2026", ds.REPORTING_PERIOD),
        ("Q3 2026 results and FY2027 guidance", "Q3 2026", ds.REPORTING_PERIOD),
        # A measure names the period it was measured in (review of PR #323).
        ("Revenue grew strongly in Q2 2026 across regions.", "Q2 2026", ds.REPORTING_PERIOD),
        # Fail closed: a bare body mention is not a reporting period.
        ("Our teams met investors in Q2 2026 in Paris.", "Q2 2026", ds.UNCLASSIFIED),
    ],
)
def test_a_mention_is_classified_by_its_own_sentence(
    sentence: str, mention: str, expected: str
) -> None:
    assert _classify(sentence, mention) == expected


def test_a_title_names_its_own_period_unless_a_cue_says_otherwise() -> None:
    assert _classify("Issuer Q2 2026", "Q2 2026", ds.ROLE_TITLE) == ds.REPORTING_PERIOD
    assert _classify("Issuer cash runway into H2 2028", "H2 2028",
                     ds.ROLE_TITLE) == ds.FORECAST_HORIZON


def test_the_vocabulary_is_closed() -> None:
    assert ds.FORWARD_ROLES < ds.DATE_ROLES
    assert ds.REPORTING_PERIOD not in ds.FORWARD_ROLES
    assert {
        ds.DOCUMENT_DATE, ds.REPORTING_PERIOD, ds.FACT_PERIOD, ds.EVENT_DATE,
        ds.GUIDANCE_TARGET_DATE, ds.FORECAST_HORIZON, ds.DEADLINE_DATE,
        ds.HISTORICAL_REFERENCE_DATE,
    } <= ds.DATE_ROLES


# --------------------------------------------------------------------------- #
# 2. The document period — a forward mention never becomes it                  #
# --------------------------------------------------------------------------- #

_FORWARD_SENTENCES = [
    "Cellectis now expects its cash runway into H2 2028.",
    "First production in 2029, with first production in H1 2029 at the earliest.",
    "FY2027 guidance is unchanged; we reiterate our FY27 Q1 guidance.",
    "The distribution contract expiring 2030 expires in H2 2030.",
    "The notes maturing 2031 have a maturity date in the first half of 2031.",
    "Topline readout expected H2 2027.",
    "The permit deadline 31 March 2027 means a permit deadline in Q1 2027.",
]


@pytest.mark.parametrize("forward", _FORWARD_SENTENCES)
@pytest.mark.parametrize("as_of", [None, PUBLISHED])
def test_a_forward_date_never_changes_an_h1_documents_period(
    forward: str, as_of: date | None
) -> None:
    body = f"{forward} Results for the half-year ended 30 June 2026 show net loss."
    found = detect_document_period(title="6-K", text=body, as_of=as_of)
    assert found.period.key == "2026-H1", found


@pytest.mark.parametrize("forward", _FORWARD_SENTENCES)
def test_a_forward_date_never_changes_a_q3_documents_period(forward: str) -> None:
    body = f"{forward} Q3 2026 results: revenue grew."
    found = detect_document_period(title="Press release", text=body, as_of=PUBLISHED)
    assert found.period.key == "2026-Q3", found


@pytest.mark.parametrize("forward", _FORWARD_SENTENCES)
def test_a_forward_date_alone_gives_no_period(forward: str) -> None:
    """The pivot press release: a document that reports no period has none."""
    assert not detect_document_period(text=forward, as_of=PUBLISHED).is_known


def test_the_clls_pivot_release_states_no_period() -> None:
    text = (
        "Cellectis announces strategic prioritization. Following the reorganization, "
        "the Company now expects its cash runway to extend into H2 2028, compared with "
        "H2 2027 previously."
    )
    assert not detect_document_period(text=text, as_of=PUBLISHED).is_known


def test_a_genuine_half_year_report_still_yields_h1() -> None:
    found = detect_document_period(
        text="Results for the half-year ended 30 June 2026", as_of=PUBLISHED
    )
    assert found.period.key == "2026-H1"


def test_a_genuine_q3_release_still_yields_q3() -> None:
    assert detect_document_period(
        text="Q3 2026 results", as_of=PUBLISHED
    ).period.key == "2026-Q3"
    assert detect_document_period(title="Q3 2026 results").period.key == "2026-Q3"


def test_a_period_that_had_not_begun_at_publication_is_skipped() -> None:
    """``as_of``: a title period that had not started when the document was published
    is a forecast, and the scan moves on to the next candidate."""
    title = "Programme update H2 2028"
    assert detect_document_period(title=title).period.key == "2028-H2"
    found = detect_document_period(
        title=title, headings=["Interim report Q2 2026"], as_of=PUBLISHED
    )
    assert found.period.key == "2026-Q2"


def test_a_fiscal_label_is_not_mistaken_for_a_forecast() -> None:
    """FY27 Q1 of a March year-end began in April 2026: kept on 17 July 2026."""
    found = detect_document_period(title="Issuer FY27 Q1 Sales", as_of=date(2026, 7, 17))
    assert found.period.key == "2027-Q1"


def test_document_period_of_passes_the_publication_date() -> None:
    extraction = SimpleNamespace(excerpts=[
        SimpleNamespace(page_number=1, heading="Programme update H2 2028", text=""),
        SimpleNamespace(page_number=2, heading=None,
                        text="Results for the half-year ended 30 June 2026."),
    ])
    found = document_period_of(title="6-K", url=None, extraction=extraction, as_of=PUBLISHED)
    assert found.period.key == "2026-H1"


def test_the_fact_period_follows_the_reporting_period_not_the_runway() -> None:
    """An undated figure inherits the DOCUMENT period — so it must be the H1 one."""
    from app.services.sources.disclosure_period_policy import document_period_for
    from app.services.sources.extracted_fact_validator import (
        IssuerContext,
        validate_extracted_facts,
    )
    from app.services.sources.financial_period import parse_period
    from app.services.sources.primary_document_extractor import (
        STATUS_EXTRACTED,
        PrimaryDocumentExcerpt,
        PrimaryDocumentExtraction,
    )

    def _excerpt(text: str, heading: str, eid: str) -> PrimaryDocumentExcerpt:
        return PrimaryDocumentExcerpt(
            excerpt_id=eid, text=text, heading=heading, page_number=1,
            char_count=len(text), confidence=0.9, extraction_method="html",
        )

    extraction = PrimaryDocumentExtraction(
        content_hash="h" * 64, mime_type="text/html", extraction_method="html",
        status=STATUS_EXTRACTED, page_count=1,
        excerpts=[
            _excerpt("The Company now expects its cash runway into H2 2028.",
                     "Strategic update", "e1"),
            _excerpt("Group sales at EUR 6.3 billion, up by 20% at constant exchange rates",
                     "Results for the half-year ended 30 June 2026", "e2"),
        ],
    )
    period = document_period_for(title="6-K", url=None, extraction=extraction,
                                 title_only=False, published_at=PUBLISHED)
    assert period.period.key == "2026-H1"
    facts = validate_extracted_facts(
        extraction, issuer_context=IssuerContext(company_name="ISSUER SA"),
        document_period=period,
    )
    revenue = [f for f in facts if f.label == "revenue" and f.value_numeric == 6.3]
    assert revenue
    assert parse_period(revenue[0].period).key == "2026-H1"


# --------------------------------------------------------------------------- #
# 3. Structured filing metadata — the EDGAR period of report                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "expected_key", "expected_basis"),
    [
        ({"form": "10-K", "report_date": "2025-12-31", "filing_date": "2026-02-20"},
         "2025", BASIS_SEC_PERIOD_OF_REPORT),
        ({"form": "20-F", "report_date": "2025-12-31", "filing_date": "2026-03-10"},
         "2025", BASIS_SEC_PERIOD_OF_REPORT),
        ({"form": "40-F/A", "report_date": "2025-09-30", "filing_date": "2026-01-10"},
         "2025", BASIS_SEC_PERIOD_OF_REPORT),
        # A 52/53-week year ending in the first days of January is the prior year.
        ({"form": "10-K", "report_date": "2027-01-02", "filing_date": "2027-02-20"},
         "2026", BASIS_SEC_PERIOD_OF_REPORT),
        ({"form": "10-Q", "report_date": "2026-06-30", "filing_date": "2026-08-05",
          "fiscal_year_end_month": 12}, "2026-Q2", BASIS_SEC_PERIOD_OF_REPORT),
        # June year-end: the quarter ended September 2026 is Q1 of fiscal 2027.
        ({"form": "10-Q", "report_date": "2026-09-30", "filing_date": "2026-10-29",
          "fiscal_year_end_month": 6}, "2027-Q1", BASIS_SEC_FISCAL_PERIOD_OF_REPORT),
        ({"form": "6-K", "report_date": "2026-06-30", "filing_date": "2026-09-11",
          "results_title": "Cellectis Reports Financial Results for the First Half 2026"},
         "2026-H1", BASIS_SEC_PERIOD_OF_REPORT),
    ],
)
def test_the_period_of_report_is_read_consistently_with_the_form(
    kwargs: dict, expected_key: str, expected_basis: str
) -> None:
    found = sec_period_of_report(**kwargs)
    assert found.period.key == expected_key
    assert found.basis == expected_basis


@pytest.mark.parametrize(
    "kwargs",
    [
        # The provider fills a missing reportDate with the filing date: not a period.
        {"form": "10-K", "report_date": "2026-02-20", "filing_date": "2026-02-20"},
        # A period of report after the filing is nonsense.
        {"form": "10-K", "report_date": "2026-12-31", "filing_date": "2026-02-20"},
        # A January year-end is named both ways (Walmart / Home Depot).
        {"form": "10-K", "report_date": "2026-01-31", "filing_date": "2026-03-20"},
        # A 10-Q's quarter needs the fiscal year end.
        {"form": "10-Q", "report_date": "2026-06-30", "filing_date": "2026-08-05"},
        # A "10-Q" for a fourth quarter does not exist.
        {"form": "10-Q", "report_date": "2026-12-31", "filing_date": "2027-02-05",
         "fiscal_year_end_month": 12},
        # A 6-K that is not known to be an interim-results document.
        {"form": "6-K", "report_date": "2026-06-30", "filing_date": "2026-09-11"},
        # The pivot 6-K: the period of report is the filing date.
        {"form": "6-K", "report_date": "2026-10-28", "filing_date": "2026-10-28",
         "results_title": "Cellectis strategic update"},
        # A results title whose period disagrees with the period of report.
        {"form": "6-K", "report_date": "2026-06-30", "filing_date": "2026-09-11",
         "results_title": "Results for the third quarter 2026"},
        {"form": "8-K", "report_date": "2026-06-30", "filing_date": "2026-08-01"},
        {"form": None, "report_date": None, "filing_date": None},
    ],
)
def test_no_period_of_report_is_invented(kwargs: dict) -> None:
    assert not sec_period_of_report(**kwargs).is_known


def test_the_fiscal_year_end_month_is_read_from_the_annual_period_of_report() -> None:
    assert fiscal_year_end_month_of("2025-12-31") == 12
    assert fiscal_year_end_month_of("2026-06-30") == 6
    assert fiscal_year_end_month_of("2026-10-03") == 9
    assert fiscal_year_end_month_of(None) is None
    assert fiscal_year_end_month_of("2026-06-15") is None


# --------------------------------------------------------------------------- #
# 4. The corpus — persistence, priority and repair                             #
# --------------------------------------------------------------------------- #


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _cfg(**over: object) -> Settings:
    base: dict[str, object] = {
        "v3_corpus_enabled": True,
        "primary_document_ingestion_enabled": True,
        "primary_document_sec_body_enabled": True,
        "v3_filings_tool_enabled": True,
        "v3_filing_body_bridge_enabled": True,
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _label(kind: str, ordinal: int, year: int) -> DocumentPeriod:
    period_type = PERIOD_TYPE_HALF if kind == "H" else "quarter"
    return DocumentPeriod(
        ReportingPeriod(period_type, year, ordinal, f"{kind}{ordinal} {year}"),
        BASIS_PERIOD_LABEL, f"{kind.lower()}{ordinal} {year}",
    )


async def _company(session) -> Company:  # noqa: ANN001
    company = Company(id=uuid.uuid4(), ticker="CLLS", exchange="NASDAQ",
                      name="Cellectis S.A.", status="new")
    session.add(company)
    await session.flush()
    return company


async def test_a_forecast_period_is_refused_on_every_policy(session) -> None:  # noqa: ANN001
    """The began-by rule no longer applies to title-only announcements alone."""
    from app.services.corpus.documents import DocumentVersionInput, upsert_document_version

    company = await _company(session)
    version = await upsert_document_version(
        session,
        DocumentVersionInput(
            content_hash="c" * 64,
            canonical_url="https://www.sec.gov/Archives/edgar/data/1627281/1/pivot.htm",
            transport="sec_edgar", source_tier="T1_primary_filing",
            company_id=company.id, document_type="interim_report", title="6-K",
            period=_label("H", 2, 2028), published_at=PUBLISHED,
        ),
        cfg=_cfg(),
    )
    assert version is not None
    assert version.period_key is None
    assert version.period_type is None


async def test_a_begun_period_is_kept(session) -> None:  # noqa: ANN001
    from app.services.corpus.documents import DocumentVersionInput, upsert_document_version

    company = await _company(session)
    version = await upsert_document_version(
        session,
        DocumentVersionInput(
            content_hash="d" * 64,
            canonical_url="https://www.sec.gov/Archives/edgar/data/1627281/2/h1.htm",
            transport="sec_edgar", source_tier="T1_primary_filing",
            company_id=company.id, document_type="interim_report", title="6-K/A",
            period=_label("H", 1, 2026), published_at=date(2026, 9, 11),
        ),
        cfg=_cfg(),
    )
    assert version is not None and version.period_key == "2026-H1"


async def test_the_sec_period_of_report_wins_over_body_text(session) -> None:  # noqa: ANN001
    from app.models.extracted_document import ExtractedDocument
    from app.services.corpus.documents import ingest_extracted_document
    from app.services.corpus.identity import URL_KEY_PREFIX
    from app.services.sources.primary_document_extractor import (
        STATUS_EXTRACTED,
        PrimaryDocumentExcerpt,
        PrimaryDocumentExtraction,
    )

    company = await _company(session)
    url = "https://www.sec.gov/Archives/edgar/data/1627281/000162828126000001/clls-20f.htm"
    body = "Results for the first quarter 2026 appear in our Q1 2026 report."
    extraction = PrimaryDocumentExtraction(
        content_hash="e" * 64, mime_type="text/html", extraction_method="html",
        status=STATUS_EXTRACTED, page_count=1,
        excerpts=[PrimaryDocumentExcerpt(
            excerpt_id="e1", text=body, heading=None, page_number=1,
            char_count=len(body), confidence=0.9, extraction_method="html")],
    )
    doc = ExtractedDocument(
        id=uuid.uuid4(), company_id=company.id, canonical_url=url, content_hash="e" * 64,
        source_type="sec_filing", provider="sec_edgar", source_tier="T1_primary_filing",
        title="20-F 0001628281-26-000001", mime_type="text/html", status="extracted",
        extraction_method="html", retrieved_at=datetime(2026, 3, 12, tzinfo=timezone.utc),
        pipeline_version=15,
    )
    session.add(doc)
    await session.flush()
    report = sec_period_of_report(form="20-F", report_date="2025-12-31",
                                  filing_date="2026-03-10")
    artifact = SimpleNamespace(
        raw_artifact=None, extraction=extraction, period_policy=None,
        title=doc.title, source_url=url, doc_kind="annual_report", failure_code=None,
        structured_period=report,
    )
    version = await ingest_extracted_document(session, artifact=artifact, document=doc,
                                              cfg=_cfg())
    assert version is not None
    assert version.period_key == "2025"
    assert version.period_type == PERIOD_TYPE_ANNUAL
    assert version.period_basis == BASIS_SEC_PERIOD_OF_REPORT
    # A filing is identified by its address: a 20-F never merges with the issuer's
    # own annual report of the same year.
    from app.models.research_document import ResearchDocument

    document = await session.get(ResearchDocument, version.research_document_id)
    assert document is not None and document.document_key.startswith(URL_KEY_PREFIX)
    # Without the structured period the body would have said 2026-Q1.
    artifact.structured_period = None
    assert document_period_of(title=doc.title, url=url,
                              extraction=extraction).period.key == "2026-Q1"


ACCESSION = "0001171843-26-006873"
CIK = "1627281"
PIVOT_URL = (
    f"https://www.sec.gov/Archives/edgar/data/{CIK}/000117184326006873/clls-6k.htm"
)


async def _held_pivot(session, company, *, period_key: str = "2028-H2",  # noqa: ANN001, ANN202
                      published: date | None = PUBLISHED):
    """The live CLLS rows: a 6-K stored as 'interim report for 2028-H2'."""
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    now = datetime.now(timezone.utc)
    doc = ResearchDocument(
        id=uuid.uuid4(), company_id=company.id,
        document_key=f"interim_report:{period_key}", document_type="interim_report",
        title=f"6-K {ACCESSION}", period_key=period_key, period_type=PERIOD_TYPE_HALF,
    )
    session.add(doc)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(), research_document_id=doc.id, content_hash="f" * 64,
        canonical_url=PIVOT_URL, transport="sec_edgar", source_tier="T1_primary_filing",
        access_class="public_official", extraction_status="extracted", retrieved_at=now,
        is_current=True, period_key=period_key, period_type=PERIOD_TYPE_HALF,
        period_basis=BASIS_PERIOD_LABEL, published_at=published,
    )
    session.add(version)
    await session.flush()
    deriv = ResearchDocumentDerivation(
        id=uuid.uuid4(), research_document_version_id=version.id, pipeline_version=15,
        extraction_profile="live", extraction_method="html", status="complete",
        is_active=True, pages_persisted=1, page_count=1, char_count=90,
    )
    session.add(deriv)
    await session.flush()
    for n in (1, 2):
        session.add(ResearchDocumentChunk(
            id=uuid.uuid4(), chunk_id=f"pivot:{company.id}:{n}", derivation_id=deriv.id,
            research_document_version_id=version.id, company_id=company.id, ordinal=n,
            kind="prose", text="Cash runway into H2 2028.", char_start=0, char_end=25,
            indexable=True, indexed_at=now, source_tier="T1_primary_filing",
            access_class="public_official", period_key=period_key,
            period_type=PERIOD_TYPE_HALF, published_at=published,
        ))
    await session.flush()
    return doc, version


async def _rows(session, doc_id, version_id):  # noqa: ANN001, ANN202
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    doc = await session.get(ResearchDocument, doc_id)
    version = await session.get(ResearchDocumentVersion, version_id)
    await session.refresh(doc)
    await session.refresh(version)
    chunks = (await session.execute(select(ResearchDocumentChunk).where(
        ResearchDocumentChunk.research_document_version_id == version_id))).scalars().all()
    for chunk in chunks:
        await session.refresh(chunk)
    return doc, version, list(chunks)


async def _never(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
    raise AssertionError("a searchable filing must never be re-fetched")


async def test_a_re_encountered_forecast_period_is_cleared(session) -> None:  # noqa: ANN001
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    doc, version = await _held_pivot(session, company)
    result = await ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="6-K",
        cfg=_cfg(), acquire=_never, filing_date="2026-10-28",
    )
    assert result.state == "ready" and result.reused is True
    doc, version, chunks = await _rows(session, doc.id, version.id)
    assert version.period_key is None and version.period_type is None
    assert version.period_basis is None
    assert doc.period_key is None and doc.period_type is None
    assert chunks and all(c.period_key is None and c.period_type is None for c in chunks)


async def test_an_undated_forecast_is_cleared_with_the_filing_date(session) -> None:  # noqa: ANN001
    """The CLLS rows were also first stored undated: the filing date decides."""
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    doc, version = await _held_pivot(session, company, period_key="2027-H2", published=None)
    await ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="6-K",
        cfg=_cfg(), acquire=_never, filing_date="2026-10-28",
    )
    doc, version, chunks = await _rows(session, doc.id, version.id)
    assert version.published_at == PUBLISHED
    assert version.period_key is None
    assert all(c.period_key is None for c in chunks)


async def test_a_repaired_period_is_replaced_by_the_period_of_report(session) -> None:  # noqa: ANN001
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    doc, version = await _held_pivot(session, company)
    report = sec_period_of_report(
        form="6-K", report_date="2026-06-30", filing_date="2026-09-11",
        results_title="Half-year results 2026",
    )
    assert report.period.key == "2026-H1"
    await ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="6-K",
        cfg=_cfg(), acquire=_never, filing_date="2026-10-28", report_period=report,
    )
    doc, version, chunks = await _rows(session, doc.id, version.id)
    assert version.period_key == "2026-H1"
    assert version.period_basis == BASIS_SEC_PERIOD_OF_REPORT
    assert doc.period_key == "2026-H1"
    assert all(c.period_key == "2026-H1" for c in chunks)


async def test_a_period_that_had_begun_is_never_touched(session) -> None:  # noqa: ANN001
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    doc, version = await _held_pivot(session, company, period_key="2026-H1",
                                     published=date(2026, 9, 11))
    await ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="6-K/A",
        cfg=_cfg(), acquire=_never, filing_date="2026-09-11",
    )
    doc, version, chunks = await _rows(session, doc.id, version.id)
    assert version.period_key == "2026-H1" and doc.period_key == "2026-H1"
    assert all(c.period_key == "2026-H1" for c in chunks)


async def test_the_acquirer_is_told_the_period_of_report_only_when_known(session) -> None:  # noqa: ANN001
    from app.services.corpus.filing_evidence import AcquireOutcome, ensure_filing_corpus_evidence

    company = await _company(session)
    seen: list[dict] = []

    async def _acquire(session_, **kw):  # noqa: ANN001, ANN003, ANN202
        seen.append(kw)
        return AcquireOutcome(acquired=False, reason="stub")

    report = sec_period_of_report(form="10-K", report_date="2025-12-31",
                                  filing_date="2026-02-20")
    await ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="10-K",
        cfg=_cfg(), acquire=_acquire, report_period=report,
    )
    await ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="10-K",
        cfg=_cfg(), acquire=_acquire,
    )
    assert seen[0]["report_period"] == report
    assert "report_period" not in seen[1]


async def test_the_acquirer_puts_the_period_of_report_on_the_artifact(
    session, monkeypatch, real_acquire_sec_filing  # noqa: ANN001
) -> None:
    """Offline: the extractor, the writer and the indexer are stubbed."""
    from app.services import extracted_document_service as eds
    from app.services.corpus import filing_acquisition as acq
    from app.services.sources import live_fetchers

    asked: list[list[dict]] = []
    persisted: list = []
    indexed: list[dict] = []

    async def _extract(cik, filings, **_kw):  # noqa: ANN001, ANN003, ANN202
        asked.append(filings)
        return [SimpleNamespace(status="extracted", failure_code=None,
                                structured_period=None)]

    async def _persist(session_, *, artifacts, **_kw):  # noqa: ANN001, ANN003, ANN202
        persisted.extend(artifacts)
        return SimpleNamespace(documents_created=1, documents_reused=0,
                               corpus_versions_created=1)

    async def _index(session_, **kw):  # noqa: ANN001, ANN003, ANN202
        indexed.append(kw)
        return 0

    monkeypatch.setattr(live_fetchers, "live_sec_primary_document_extractor", _extract)
    monkeypatch.setattr(eds, "persist_primary_document_artifacts", _persist)
    monkeypatch.setattr(acq, "_index_acquired_version", _index)

    company = await _company(session)
    report = sec_period_of_report(form="20-F", report_date="2025-12-31",
                                  filing_date="2026-03-10")
    outcome = await real_acquire_sec_filing(
        session, company_id=company.id, cik=CIK, accession=ACCESSION, form="20-F",
        cfg=_cfg(), filing_date=date(2026, 3, 10), report_period=report,
    )
    assert outcome.acquired is True
    assert asked[0][0]["filed_date"] == "2026-03-10"
    assert persisted[0].structured_period == report
    assert indexed[0]["report_period"] == report


async def test_ensure_core_filings_hands_the_period_of_report_to_the_bridge(
    session, monkeypatch  # noqa: ANN001
) -> None:
    from app.services.corpus import filing_evidence as fe

    company = await _company(session)
    calls: list[dict] = []

    async def _ensure(session_, **kw):  # noqa: ANN001, ANN003, ANN202
        calls.append(kw)
        return fe.FilingEvidenceResult(state=fe.STATE_UNAVAILABLE, reason="stub")

    monkeypatch.setattr(fe, "ensure_filing_corpus_evidence", _ensure)

    async def _listing(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        return SimpleNamespace(ticker="ACME", exchange="NASDAQ", cik="1", is_sibling=False)

    from app.services.entities import sec_listing

    monkeypatch.setattr(sec_listing, "sec_listing_for", _listing)

    def _event(form: str, filed: str, report: str, n: int) -> SimpleNamespace:
        return SimpleNamespace(form_type=form, filing_date=filed, report_date=report,
                               accession_number=f"0000000001-26-00000{n}")

    class _Provider:
        async def get_recent_events(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN202
            return SimpleNamespace(cik="1", events=[
                _event("10-K", "2026-08-20", "2026-06-30", 1),
                _event("10-Q", "2026-10-29", "2026-09-30", 2),
            ])

    await fe.ensure_core_filings(session, company=company, cfg=_cfg(), provider=_Provider())
    by_form = {c["form"]: c for c in calls}
    assert by_form["10-K"]["report_period"].period.key == "2026"
    # June year-end: the September quarter is Q1 of fiscal 2027.
    assert by_form["10-Q"]["report_period"].period.key == "2027-Q1"


# --------------------------------------------------------------------------- #
# 5. The investigator's period-agreement guard                                 #
# --------------------------------------------------------------------------- #


def test_a_forecast_period_never_counts_as_a_disagreeing_period() -> None:
    from app.services.agents.investigator import _Evidence, _inherited

    evidence = [
        _Evidence(citation_id="ev:h1", kind="search_company_corpus", text="net loss",
                  period_key="2026-H1", published_at=date(2026, 9, 11)),
        _Evidence(citation_id="ev:pivot", kind="search_company_corpus", text="runway",
                  period_key="2028-H2", published_at=PUBLISHED),
    ]
    period, scope, conflicts = _inherited(["ev:h1", "ev:pivot"], evidence)
    assert conflicts == []
    assert period == "2026-H1"


def test_two_real_periods_still_disagree() -> None:
    from app.services.agents.investigator import _Evidence, _inherited

    evidence = [
        _Evidence(citation_id="a", kind="k", text="", period_key="2026-H1",
                  published_at=date(2026, 9, 11)),
        _Evidence(citation_id="b", kind="k", text="", period_key="2025-H1",
                  published_at=date(2025, 9, 11)),
    ]
    period, _scope, conflicts = _inherited(["a", "b"], evidence)
    assert period is None and conflicts


def test_an_undated_period_still_votes() -> None:
    """No publication date: the guard cannot tell a forecast and keeps the old rule."""
    from app.services.agents.investigator import _Evidence, _inherited

    evidence = [
        _Evidence(citation_id="a", kind="k", text="", period_key="2026-H1"),
        _Evidence(citation_id="b", kind="k", text="", period_key="2028-H2"),
    ]
    _period, _scope, conflicts = _inherited(["a", "b"], evidence)
    assert conflicts


# --------------------------------------------------------------------------- #
# 6. The start-of-run sweep — rows no run will ever re-encounter               #
# --------------------------------------------------------------------------- #


async def _held(  # noqa: ANN202
    session, company, *, period_key: str | None, published: date | None,  # noqa: ANN001
    basis: str | None = BASIS_PERIOD_LABEL, doc_period: str | None = "same",
    is_current: bool = True, url: str | None = None, version_id: uuid.UUID | None = None,
):
    """One document → version → two chunks, as the corpus holds them."""
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    now = datetime.now(timezone.utc)
    period_type = PERIOD_TYPE_HALF if period_key and "H" in period_key else (
        "quarter" if period_key else None)
    stored_doc_period = period_key if doc_period == "same" else doc_period
    doc = ResearchDocument(
        id=uuid.uuid4(), company_id=company.id,
        document_key=f"url:{uuid.uuid4().hex[:16]}", document_type="interim_report",
        period_key=stored_doc_period, period_type=period_type if stored_doc_period else None,
    )
    session.add(doc)
    await session.flush()
    version = ResearchDocumentVersion(
        id=version_id or uuid.uuid4(), research_document_id=doc.id,
        content_hash=uuid.uuid4().hex * 2,
        canonical_url=url or f"https://issuer.test/{uuid.uuid4().hex[:8]}.pdf",
        transport="company_ir", source_tier="T1_primary_company_source",
        access_class="public_issuer", extraction_status="extracted", retrieved_at=now,
        is_current=is_current, period_key=period_key, period_type=period_type,
        period_basis=basis if period_key else None, published_at=published,
    )
    session.add(version)
    await session.flush()
    deriv = ResearchDocumentDerivation(
        id=uuid.uuid4(), research_document_version_id=version.id, pipeline_version=15,
        extraction_profile="live", extraction_method="html", status="complete",
        is_active=True, pages_persisted=1, page_count=1, char_count=30,
    )
    session.add(deriv)
    await session.flush()
    for n in (1, 2):
        session.add(ResearchDocumentChunk(
            id=uuid.uuid4(), chunk_id=f"{version.id}:{n}", derivation_id=deriv.id,
            research_document_version_id=version.id, company_id=company.id, ordinal=n,
            kind="prose", text="Cash runway into H2 2027.", char_start=0, char_end=25,
            indexable=True, indexed_at=now, source_tier="T1_primary_company_source",
            access_class="public_issuer", period_key=period_key, period_type=period_type,
            published_at=published,
        ))
    await session.flush()
    return doc, version


async def test_the_sweep_clears_every_stored_forecast_and_nothing_else(session) -> None:  # noqa: ANN001
    from app.services.corpus.period_sweep import sweep_forecast_periods
    from app.services.sources.document_period import BASIS_FISCAL_LABEL

    company = await _company(session)
    other = Company(id=uuid.uuid4(), ticker="OTHR", exchange="NASDAQ", name="Other",
                    status="new")
    session.add(other)
    await session.flush()
    # The live CLLS row: 2027-H2 on a 6-K published 2026-10-28. The guard's lenient
    # reading lets it vote; the sweep's STRICT calendar reading clears it.
    pivot = await _held(session, company, period_key="2027-H2", published=PUBLISHED)
    far = await _held(session, company, period_key="2028-H2", published=PUBLISHED)
    real = await _held(session, company, period_key="2026-H1", published=date(2026, 9, 11))
    fiscal = await _held(session, company, period_key="2027-Q1", basis=BASIS_FISCAL_LABEL,
                         published=date(2026, 7, 17))
    undated = await _held(session, company, period_key="2028-H2", published=None)
    superseded = await _held(session, company, period_key="2028-H2", published=PUBLISHED,
                             is_current=False)
    other_doc = await _held(session, company, period_key="2027-H2", published=PUBLISHED,
                            doc_period="2026-H1")
    foreign = await _held(session, other, period_key="2028-H2", published=PUBLISHED)

    result = await sweep_forecast_periods(session, company_id=company.id)
    assert result.error is None
    # Current, dated, with a period, this company — and never a fiscal basis.
    assert result.versions_examined == 4
    assert (result.versions_cleared, result.chunks_cleared,
            result.documents_cleared) == (3, 6, 2)

    for doc, version in (pivot, far):
        doc, version, chunks = await _rows(session, doc.id, version.id)
        assert version.period_key is None and version.period_basis is None
        assert doc.period_key is None
        assert all(c.period_key is None and c.period_type is None for c in chunks)
    doc, version, chunks = await _rows(session, other_doc[0].id, other_doc[1].id)
    assert version.period_key is None and all(c.period_key is None for c in chunks)
    assert doc.period_key == "2026-H1", "a document carrying another period is kept"
    for doc, version, key in ((real[0], real[1], "2026-H1"),
                              (fiscal[0], fiscal[1], "2027-Q1"),
                              (undated[0], undated[1], "2028-H2"),
                              (superseded[0], superseded[1], "2028-H2"),
                              (foreign[0], foreign[1], "2028-H2")):
        doc, version, chunks = await _rows(session, doc.id, version.id)
        assert version.period_key == key and doc.period_key == key
        assert all(c.period_key == key for c in chunks)

    again = await sweep_forecast_periods(session, company_id=company.id)
    assert (again.versions_cleared, again.chunks_cleared, again.documents_cleared) == (0, 0, 0)


async def test_the_sweep_is_bounded_and_reports_the_cap(session) -> None:  # noqa: ANN001
    from app.services.corpus.period_sweep import sweep_forecast_periods

    company = await _company(session)
    for _ in range(3):
        await _held(session, company, period_key="2028-H2", published=PUBLISHED)
    first = await sweep_forecast_periods(session, company_id=company.id, limit=2)
    assert first.capped is True and first.versions_cleared == 2
    assert first.to_dict()["capped"] is True
    second = await sweep_forecast_periods(session, company_id=company.id, limit=2)
    assert second.versions_cleared == 1 and second.capped is False


async def test_the_sweep_never_raises() -> None:
    from app.services.corpus.period_sweep import sweep_forecast_periods

    class _Broken:
        def begin_nested(self):  # noqa: ANN202
            raise RuntimeError("no database")

    result = await sweep_forecast_periods(_Broken(), company_id=uuid.uuid4())
    assert result.error == "RuntimeError" and result.versions_cleared == 0
    assert (await sweep_forecast_periods(_Broken(), company_id=None)).error is None


async def test_every_research_run_sweeps_first_even_with_nothing_acquired(
    session, monkeypatch  # noqa: ANN001
) -> None:
    """The V3 pipeline sweeps the company's corpus before anything else touches it."""
    from app.services.corpus import filing_evidence
    from app.services.pipeline import v3_pipeline as v3

    order: list[str] = []
    real_core = filing_evidence.ensure_core_filings

    async def _core(*a, **k):  # noqa: ANN002, ANN003, ANN202
        order.append("core_filings")
        return await real_core(*a, **k)

    monkeypatch.setattr(filing_evidence, "ensure_core_filings", _core)
    from app.services.corpus import period_sweep

    real_sweep = period_sweep.sweep_forecast_periods

    async def _sweep(*a, **k):  # noqa: ANN002, ANN003, ANN202
        order.append("sweep")
        return await real_sweep(*a, **k)

    monkeypatch.setattr(period_sweep, "sweep_forecast_periods", _sweep)

    company = await _company(session)
    pivot = await _held(session, company, period_key="2027-H2", published=PUBLISHED)
    cfg = _cfg(app_env="test", v3_pipeline_enabled=True, v3_agent_tools_enabled=True,
               v3_filings_tool_enabled=False, v3_filing_body_bridge_enabled=False,
               v3_run_max_web_searches=0, v3_artifact_store_backend="none")
    outcome = await v3.run_v3_research(session, company, cfg=cfg)
    assert order[:2] == ["sweep", "core_filings"]
    assert outcome.period_sweep["versions_cleared"] == 1
    assert outcome.period_sweep["chunks_cleared"] == 2
    assert outcome.to_dict()["period_sweep"] == outcome.period_sweep
    _doc, version, _chunks = await _rows(session, pivot[0].id, pivot[1].id)
    assert version.period_key is None


def test_without_the_corpus_the_outcome_has_no_sweep_key() -> None:
    from app.services.pipeline import v3_pipeline as v3

    assert "period_sweep" not in v3.V3ResearchOutcome().to_dict()


# --------------------------------------------------------------------------- #
# 7. Review of PR #323 — recall: every reviewer input, at its real publication  #
#    date                                                                       #
# --------------------------------------------------------------------------- #


def _key_and_basis(found: DocumentPeriod) -> tuple[str | None, str]:
    return found.period.key, found.basis


# HIGH 1 — the issuer STATES the fiscal year: a fiscal basis, so a period that began
# before its calendar reading is not a forecast.
_STATED_FISCAL_TITLES = [
    ("NVIDIA Announces Financial Results for First Quarter Fiscal 2027",
     date(2026, 5, 28), "2027-Q1"),
    ("NVIDIA Announces Financial Results for Second Quarter Fiscal 2027",
     date(2026, 8, 26), "2027-Q2"),
    ("Cisco Reports First Quarter Fiscal Year 2027 Earnings", date(2026, 11, 12), "2027-Q1"),
    ("Salesforce Announces Strong Second Quarter Fiscal 2027 Results",
     date(2026, 9, 3), "2027-Q2"),
    ("Medtronic reports first quarter fiscal 2027 financial results",
     date(2026, 8, 19), "2027-Q1"),
    ("Accenture Reports First-Quarter Fiscal 2027 Results", date(2026, 12, 18), "2027-Q1"),
    ("Accenture Reports Fourth-Quarter and Full-Year Fiscal 2026 Results",
     date(2026, 9, 25), "2026-Q4"),
    ("FedEx Corp. Reports Second Quarter Fiscal 2027 Results", date(2026, 12, 17), "2027-Q2"),
    ("Micron Technology Reports Results for the First Quarter of Fiscal 2027",
     date(2026, 12, 17), "2027-Q1"),
    ("Procter & Gamble first quarter fiscal year 2027 results", date(2026, 10, 20), "2027-Q1"),
    ("Walt Disney Company Reports Fourth Quarter and Full Year Earnings for Fiscal 2026",
     date(2026, 11, 12), "2026-Q4"),
    ("first quarter fiscal 2027 ended August 30, 2026", date(2026, 9, 25), "2027-Q1"),
    ("first quarter of fiscal 2027 ended November 27, 2026", date(2026, 12, 17), "2027-Q1"),
]


@pytest.mark.parametrize(("title", "published", "key"), _STATED_FISCAL_TITLES)
def test_a_stated_fiscal_year_is_read_as_fiscal(title: str, published: date, key: str) -> None:
    found = detect_document_period(title=title, as_of=published)
    assert _key_and_basis(found) == (key, BASIS_FISCAL_PERIOD_END)


@pytest.mark.parametrize(
    ("text", "published", "key"),
    [
        ("Exhibit 99.1\nCisco Reports First Quarter Fiscal Year 2027 Earnings\n"
         "Q1 Revenue: $14.6 billion", date(2026, 11, 12), "2027-Q1"),
        ("Exhibit 99.1\nNVIDIA Announces Financial Results for First Quarter Fiscal 2027\n"
         "Record revenue of $44.1 billion", date(2026, 5, 28), "2027-Q1"),
        ("Exhibit 99.1 PRESS RELEASE Acme Announces Second Quarter Fiscal 2027 Results. "
         "Revenue of $2.1 billion; expects third quarter fiscal 2027 revenue of $2.2 billion",
         date(2026, 12, 17), "2027-Q2"),
    ],
)
def test_a_stated_fiscal_year_in_an_exhibit_body_is_read_as_fiscal(
    text: str, published: date, key: str
) -> None:
    found = detect_document_period(text=text, as_of=published)
    assert _key_and_basis(found) == (key, BASIS_FISCAL_PERIOD_END)


def test_an_end_date_after_the_stated_fiscal_year_must_agree_with_it() -> None:
    # November 2027 ends the first quarter of fiscal 2028, not of fiscal 2027.
    found = detect_document_period(
        title="first quarter of fiscal 2027 ended November 27, 2027")
    assert not found.is_known


def test_a_calendar_quarter_forecast_is_still_refused_beside_the_fiscal_reading() -> None:
    found = detect_document_period(
        text="Guidance for the first quarter ending March 31, 2027 is unchanged.",
        as_of=date(2026, 11, 5))
    assert not found.is_known


# HIGH 2 — a press release's own lead lines still date the document.
_LEAD_LINES = [
    ("Paris, October 28, 2026 - Acme SA (Euronext: ACM) today announced its third quarter "
     "2026 revenue.", date(2026, 10, 28), "2026-Q3"),
    ("Acme today announced third quarter 2026 sales of EUR 1.2 billion.",
     date(2026, 10, 28), "2026-Q3"),
    ("Acme reported third-quarter 2026 net sales of $3.1 billion.",
     date(2026, 11, 5), "2026-Q3"),
    ("Acme posted record Q3 2026 revenue of $3.1 billion.", date(2026, 11, 5), "2026-Q3"),
    ("Third Quarter 2026 Highlights\nRevenue of $3.1 billion", date(2026, 11, 5), "2026-Q3"),
    ("Earnings release Q3 2026\nRevenue of $3.1 billion", date(2026, 11, 5), "2026-Q3"),
    ("Acme Q3 2026 Earnings\nRevenue of $3.1 billion", date(2026, 11, 5), "2026-Q3"),
    ("First-half 2026 sales up 8% to EUR 2.4 billion", date(2026, 7, 30), "2026-H1"),
    ("Sales in the first half of 2026 rose 8%.", date(2026, 7, 30), "2026-H1"),
    ("Q3 2026 highlights: revenue grew 12%.", date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026: revenue grew 12%.", date(2026, 10, 28), "2026-Q3"),
    ("In Q3 2026, revenue grew 12%.", date(2026, 10, 28), "2026-Q3"),
    ("During Q3 2026 revenue grew 12%.", date(2026, 10, 28), "2026-Q3"),
    ("Third quarter 2026 revenue was $5.1 billion.", date(2026, 10, 28), "2026-Q3"),
    ("Revenue in the third quarter of 2026 was $5.1 billion.", date(2026, 10, 28), "2026-Q3"),
    ("Highlights of the six months ended 30 June 2026", date(2026, 8, 20), "2026-H1"),
    ("HIGHLIGHTS Q3 2026 Production of 50kt. Guidance for 2027 reaffirmed.",
     date(2026, 10, 28), "2026-Q3"),
]


@pytest.mark.parametrize(("text", "published", "key"), _LEAD_LINES)
def test_a_press_release_lead_line_dates_the_document(
    text: str, published: date, key: str
) -> None:
    assert detect_document_period(text=text, as_of=published).period.key == key


_CUED_SENTENCES = [
    (text, published, key) for text, published, key in _LEAD_LINES
    if "\n" not in text and not text.startswith(("Q3 2026", "HIGHLIGHTS"))
]


@pytest.mark.parametrize(("text", "published", "key"), _CUED_SENTENCES)
def test_a_cued_sentence_deep_in_the_body_still_dates_the_document(
    text: str, published: date, key: str
) -> None:
    """Not only as the headline line: the cue itself frames the period."""
    body = "Acme SA is a company. " + text
    assert detect_document_period(text=body, as_of=published).period.key == key


def test_a_long_first_body_sentence_is_body_text_not_a_headline() -> None:
    lead = "Acme is a company that " + "builds things and " * 15
    assert not detect_document_period(text=lead + "met investors in Q3 2026.").is_known


@pytest.mark.parametrize(
    "text",
    [
        "The notes maturing 2031 have a maturity date in the first half of 2031.",
        "Guidance for Q4 2026 revenue is unchanged.",
        "We expect Q4 2026 revenue of $3.3 billion.",
        "Cash runway into H2 2028.",
    ],
)
def test_a_forward_cue_still_refuses_a_headline_line(text: str) -> None:
    assert not detect_document_period(text=text).is_known


# MEDIUM 3 — a title's later clause, or an outlook word that does not govern the
# mention, never takes away the period its first clause names.
_HEADLINE_TITLES = [
    ("Interim results for the six months ended 30 June 2026 and outlook",
     date(2026, 8, 20), "2026-H1"),
    ("Q3 2026: Revenue up 12%, full-year guidance raised", date(2026, 10, 28), "2026-Q3"),
    ("Acme Q3 2026 revenue ahead of expectations", date(2026, 10, 28), "2026-Q3"),
    ("Strong H1 2026; outlook upgraded", date(2026, 7, 30), "2026-H1"),
    ("H1 2026: margin expansion, targets confirmed", date(2026, 7, 30), "2026-H1"),
    ("FY27 Q1 sales – outlook", date(2026, 7, 17), "2027-Q1"),
    ("Q1 FY27 update – guidance maintained", date(2026, 7, 17), "2027-Q1"),
    ("Third quarter 2026 update; guidance increased", date(2026, 10, 28), "2026-Q3"),
    ("Construction update Q3 2026", date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026 sales and outlook", date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026 sales: outlook confirmed", date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026 update ahead of permitting decision", date(2026, 10, 28), "2026-Q3"),
    ("Company announces Q3 2026 production; expects record Q4", date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026 production update: FID expected by year end", date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026 Production and Sales Update; 2026 Guidance Reaffirmed",
     date(2026, 10, 28), "2026-Q3"),
    ("Q3 2026 trading update - on track to meet expectations", date(2026, 10, 20), "2026-Q3"),
    ("Acme Provides 2027 Outlook and Reports Q3 2026 Results", date(2026, 10, 28), "2026-Q3"),
]


@pytest.mark.parametrize(("title", "published", "key"), _HEADLINE_TITLES)
def test_a_title_names_its_period_before_its_outlook(
    title: str, published: date, key: str
) -> None:
    assert detect_document_period(title=title, as_of=published).period.key == key


@pytest.mark.parametrize(("title", "published", "key"), _HEADLINE_TITLES)
def test_a_heading_names_its_period_before_its_outlook(
    title: str, published: date, key: str
) -> None:
    assert detect_document_period(headings=[title], as_of=published).period.key == key


def test_a_url_slug_names_its_period_before_its_guidance() -> None:
    found = detect_document_period(
        url="https://ir.example.com/news/q3-2026-update-and-q4-guidance",
        as_of=date(2026, 10, 20))
    assert found.period.key == "2026-Q3"


@pytest.mark.parametrize(
    ("title", "published"),
    [
        ("Q1 2028 first production update", date(2026, 10, 28)),
        ("FY28 Q1 guidance", date(2026, 10, 28)),
        ("Q4 2026 revenue guidance", date(2026, 10, 28)),
        ("Outlook for Q4 2026", date(2026, 10, 28)),
        ("Notes maturing in Q1 2031", date(2026, 10, 28)),
        ("Topline readout expected H2 2027", date(2026, 10, 28)),
    ],
)
def test_a_title_governed_by_its_forward_cue_is_still_refused(
    title: str, published: date
) -> None:
    """Without the publication date too: the cue alone refuses it."""
    assert not detect_document_period(title=title).is_known
    assert not detect_document_period(title=title, as_of=published).is_known


def test_a_heading_that_is_only_an_outlook_does_not_date_the_document() -> None:
    found = detect_document_period(headings=["Outlook for Q4 2026", "Q3 2026 highlights"],
                                   as_of=date(2026, 10, 28))
    assert found.period.key == "2026-Q3"


# LOW 7 — the fiscal/calendar label compares with the year the quarter ENDS in.
def test_a_spilled_10q_quarter_is_labelled_by_its_adjusted_end_year() -> None:
    found = sec_period_of_report(form="10-Q", report_date="2027-01-02",
                                 filing_date="2027-02-05", fiscal_year_end_month=9)
    assert _key_and_basis(found) == ("2027-Q1", BASIS_SEC_FISCAL_PERIOD_OF_REPORT)
    calendar = sec_period_of_report(form="10-Q", report_date="2026-04-03",
                                    filing_date="2026-05-05", fiscal_year_end_month=12)
    assert _key_and_basis(calendar) == ("2026-Q1", BASIS_SEC_PERIOD_OF_REPORT)


def test_a_spilled_prose_quarter_is_labelled_by_its_adjusted_end_year() -> None:
    fiscal = detect_document_period(title="Results for the first quarter ended January 2, 2027",
                                    as_of=date(2027, 1, 29))
    assert _key_and_basis(fiscal) == ("2027-Q1", BASIS_FISCAL_PERIOD_END)
    calendar = detect_document_period(title="Results for the first quarter ended April 3, 2026",
                                      as_of=date(2026, 5, 5))
    assert _key_and_basis(calendar) == ("2026-Q1", BASIS_PERIOD_END_PHRASE)


# MEDIUM 4 — facts whose default period was a forecast are derived again.
def test_the_extraction_pipeline_version_advanced_for_adr_080() -> None:
    from app.services.sources.extraction_pipeline_version import (
        CURRENT_EXTRACTION_PIPELINE_VERSION,
    )

    assert CURRENT_EXTRACTION_PIPELINE_VERSION == 21


# HIGH 1 / LOW 5 — the sweep never clears a fiscal key, and is never starved by the cap.
async def test_the_sweep_never_clears_a_fiscal_key(session) -> None:  # noqa: ANN001
    from app.services.corpus.period_sweep import sweep_forecast_periods

    company = await _company(session)
    nvidia_day = date(2026, 5, 28)
    stated = await _held(session, company, period_key="2027-Q1",
                         basis=BASIS_FISCAL_PERIOD_END, published=nvidia_day)
    # Before ADR-080 "First Quarter Fiscal 2027" was stored under the period-end basis.
    legacy = await _held(session, company, period_key="2027-Q1",
                         basis=BASIS_PERIOD_END_PHRASE, published=nvidia_day)
    sec_fiscal = await _held(session, company, period_key="2027-Q1",
                             basis=BASIS_SEC_FISCAL_PERIOD_OF_REPORT,
                             published=date(2026, 10, 28))
    far = await _held(session, company, period_key="2028-Q1",
                      basis=BASIS_PERIOD_END_PHRASE, published=nvidia_day)

    result = await sweep_forecast_periods(session, company_id=company.id)
    assert result.error is None
    assert result.versions_examined == 2, "a fiscal basis is not even read"
    assert result.versions_cleared == 1
    for doc, version in (stated, legacy, sec_fiscal):
        doc, version, chunks = await _rows(session, doc.id, version.id)
        assert version.period_key == "2027-Q1" and doc.period_key == "2027-Q1"
        assert all(c.period_key == "2027-Q1" for c in chunks)
    _doc, version, _chunks = await _rows(session, far[0].id, far[1].id)
    assert version.period_key is None, "a period-end key over a year ahead is a forecast"


async def test_the_sweep_reaches_rows_past_the_cap(session) -> None:  # noqa: ANN001
    from app.services.corpus.period_sweep import sweep_forecast_periods

    company = await _company(session)
    low = [uuid.UUID(int=n) for n in (1, 2)]
    for version_id in low:  # settled rows that sort FIRST
        await _held(session, company, period_key="2026-H1", published=date(2026, 9, 11),
                    version_id=version_id)
    for n in (3, 4, 5):  # older than their publication year: never even candidates
        await _held(session, company, period_key="2025-H1", published=date(2026, 3, 1),
                    version_id=uuid.UUID(int=n))
    stale = await _held(session, company, period_key="2028-H2", published=PUBLISHED,
                        version_id=uuid.UUID(int=99))

    first = await sweep_forecast_periods(session, company_id=company.id, limit=2,
                                         start_after=uuid.UUID(int=0))
    assert (first.versions_examined, first.versions_cleared, first.capped) == (2, 0, True)
    # Another run starts at another id and wraps around: the stale row is reached.
    second = await sweep_forecast_periods(session, company_id=company.id, limit=2,
                                          start_after=low[1])
    assert second.versions_cleared == 1 and second.capped is True
    _doc, version, _chunks = await _rows(session, stale[0].id, stale[1].id)
    assert version.period_key is None
    third = await sweep_forecast_periods(session, company_id=company.id, limit=2)
    assert (third.versions_examined, third.capped) == (2, False)
    assert third.versions_cleared == 0


def test_the_sweep_prefilter_compiles_for_postgresql() -> None:
    from sqlalchemy import String, cast, extract, func
    from sqlalchemy.dialects import postgresql

    from app.models.research_document import ResearchDocumentVersion as V

    clause = func.substr(V.period_key, 1, 4) >= cast(extract("year", V.published_at), String)
    sql = str(clause.compile(dialect=postgresql.dialect()))
    assert "substr(" in sql and "EXTRACT(year FROM" in sql and "AS VARCHAR" in sql


# --------------------------------------------------------------------------- #
# 8. Re-review of PR #323 — no in-progress period through a weak cue, a clause  #
#    split or a forward verb (the CLLS defect class itself)                     #
# --------------------------------------------------------------------------- #

# (a) A clause that is only a forward label still governs the period beside it.
_LABEL_SPLIT_TITLES = [
    "Outlook: Q4 2026",
    "Guidance – Q4 2026",
    "Production guidance: H2 2026",
    "Trading outlook | H2 2026",
    "Q4 2026: guidance",
    "Guidance: Q4 2026 revenue of $1.2bn",
    "Outlook update: Q4 2026 revenue",
    # Item 2 — "update" and "record" between the cue and the period.
    "Revenue guidance update for Q4 2026",
    "Company expects record Q4 2026 sales",
]


@pytest.mark.parametrize("title", _LABEL_SPLIT_TITLES)
def test_a_split_forward_label_still_refuses_a_title(title: str) -> None:
    assert not detect_document_period(title=title, as_of=PUBLISHED).is_known


@pytest.mark.parametrize("title", _LABEL_SPLIT_TITLES)
def test_a_split_forward_label_still_refuses_a_heading(title: str) -> None:
    assert not detect_document_period(headings=[title], as_of=PUBLISHED).is_known


@pytest.mark.parametrize(
    ("clause", "label"),
    [
        ("outlook", True), ("guidance", True), ("production guidance", True),
        ("trading outlook", True), ("outlook update", True),
        ("outlook upgraded", False), ("guidance raised", False),
        ("targets confirmed", False), ("q3 2026", False), ("revenue up 12", False),
        ("", False),
    ],
)
def test_a_forward_label_clause_is_only_a_cue_and_connectors(clause: str, label: bool) -> None:
    assert ds.is_forward_label(clause) is label


# (b) A forward verb AFTER the mention is a cue too.
@pytest.mark.parametrize(
    ("text", "published"),
    [
        ("Strategic update\nQ4 2026 revenue will reflect the new pricing.", PUBLISHED),
        ("Corporate update\nThe pivot means revenue in H2 2026 will decline.", PUBLISHED),
        ("Corporate update\nH2 2026 sales will decline as the company exits the product.",
         PUBLISHED),
        ("Acme SA is a company. H2 2026 sales will decline as the company exits the product.",
         PUBLISHED),
        ("Company provides corporate update. Sales in H2 2026 to be minimal.", PUBLISHED),
        ("Strategic update\nSecond quarter fiscal 2027 revenue will be $15bn.",
         date(2026, 11, 12)),
        ("Strategic update\nRevenue in Q4 2026 should benefit from the launch.", PUBLISHED),
        ("Strategic update\nQ3 2026 revenue is expected to be restated.", PUBLISHED),
    ],
)
def test_a_forward_verb_after_the_mention_refuses_it(text: str, published: date) -> None:
    assert not detect_document_period(text=text, as_of=published).is_known


def test_a_strong_cue_still_outvotes_a_later_forward_verb() -> None:
    found = detect_document_period(
        text="Q3 2026 results will be discussed on a call today.", as_of=PUBLISHED)
    assert found.period.key == "2026-Q3"


# (c) A weakly framed period must be over — and, in body text, recent.
@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("text", "Pivot update\nRevenue for Q3 2025 was $2m."),
        ("text", "Pivot announcement\nAnnounced Q4 2026 restructuring plan."),
        ("text", "Business update\nThe company targets sales of $5m in H2 2026."),
        ("text", "Highlights: first production targeted Q4 2026"),
        ("title", "Pre-announcement of Q4 2026 sales"),
        ("title", "Acme sets date for Q4 2026 earnings call"),
        ("title", "Update on Q4 2026 revenue expectations"),
        ("headings", "Q4 2026 guidance"),
    ],
)
def test_a_weakly_framed_running_or_stale_period_is_refused(kind: str, text: str) -> None:
    kwargs: dict = {"headings": [text]} if kind == "headings" else {kind: text}
    assert not detect_document_period(as_of=PUBLISHED, **kwargs).is_known


def test_a_weakly_framed_period_is_read_once_it_has_ended() -> None:
    assert detect_document_period(text="Acme posted record Q3 2026 revenue.",
                                  as_of=PUBLISHED).period.key == "2026-Q3"
    assert not detect_document_period(text="Acme posted record Q3 2026 revenue.",
                                      as_of=date(2026, 9, 15)).is_known


def test_a_headline_line_period_must_have_ended() -> None:
    assert not detect_document_period(text="Q4 2026: revenue grew 12%.",
                                      as_of=PUBLISHED).is_known
    assert detect_document_period(text="Q3 2026: revenue grew 12%.",
                                  as_of=PUBLISHED).period.key == "2026-Q3"


def test_body_text_reads_a_weak_cue_only_for_a_recent_period() -> None:
    text = "Acme SA is a company. Revenue in the third quarter of 2026 was $5.1 billion."
    assert detect_document_period(text=text, as_of=date(2027, 4, 1)).period.key == "2026-Q3"
    # More than 200 days after the quarter ended it is a comparative, not the report.
    assert not detect_document_period(text=text, as_of=date(2027, 6, 1)).is_known


def test_a_title_weak_cue_needs_the_period_over_but_not_recent() -> None:
    title = "Q3 2026 sales and FY2027 outlook"
    assert detect_document_period(title=title, as_of=PUBLISHED).period.key == "2026-Q3"
    assert detect_document_period(title=title, as_of=date(2027, 9, 1)).period.key == "2026-Q3"
    assert not detect_document_period(title=title, as_of=date(2026, 9, 1)).is_known


def test_a_strong_or_anchored_cue_keeps_the_began_by_rule() -> None:
    """Strong and anchored framing are unchanged: begun is enough."""
    assert detect_document_period(title="Q4 2026 Trading Update",
                                  as_of=PUBLISHED).period.key == "2026-Q4"
    assert detect_document_period(
        text="Acme SA is a company. Results for the third quarter of 2026 are below.",
        as_of=date(2027, 9, 1)).period.key == "2026-Q3"


def test_a_weakly_framed_fiscal_period_is_judged_by_the_lenient_end() -> None:
    # FY27 Q1 may have ended as early as March 2026 (a January fiscal year end).
    found = detect_document_period(title="FY27 Q1 sales – outlook", as_of=date(2026, 7, 17))
    assert found.period.key == "2027-Q1"
    # Read at its LATEST end for recency, so a fiscal quarter is never "too old" early.
    late = detect_document_period(
        text="Acme SA is a company. Q1 FY27 revenue grew 8% to $3.1bn.",
        as_of=date(2027, 8, 1))
    assert late.period.key == "2027-Q1"


# The sweep's SQL pre-filter reads the first four characters of a key as its year.
@pytest.mark.parametrize(
    "period",
    [
        ReportingPeriod(PERIOD_TYPE_ANNUAL, 2026, None, "2026"),
        ReportingPeriod(PERIOD_TYPE_HALF, 2026, 2, "H2 2026"),
        ReportingPeriod("quarter", 2027, 1, "Q1 2027"),
        ReportingPeriod("split_year", 2025, None, "2025/26"),
    ],
)
def test_every_reporting_period_key_is_year_first(period: ReportingPeriod) -> None:
    import re

    assert re.match(r"^(?:19|20)\d{2}(?:$|[-/])", period.key or "")


@pytest.mark.parametrize(
    "title",
    ["Annual Report 2026", "Annual Report 2025/26", "2026 Annual Report",
     "Annual report and accounts 2025-26"],
)
def test_the_annual_title_fallback_key_is_year_first(title: str) -> None:
    import re

    from app.services.corpus.identity import annual_period_from

    key, _type, _basis = annual_period_from(title=title, url=None)
    assert key is None or re.match(r"^(?:19|20)\d{2}", key)


def test_every_corpus_period_key_writer_takes_a_year_first_key() -> None:
    """Every ``period_key`` a corpus module writes comes from a year-first source:
    ``period_fields`` / ``annual_period_from`` (both above), ``None``, or a key already
    stored. A new writer must be added here deliberately."""
    import ast
    from pathlib import Path

    import app.services.corpus as corpus

    allowed_values = {
        "period_key", "None", "None if address_keyed else period_key", "new[0]",
        "version.period_key", "row.period_key", "chunk.period_key",
    }
    allowed_sources = {"period_fields", "annual_period_from"}
    root = Path(corpus.__file__).parent
    seen: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.keyword) and node.arg == "period_key":
                value = ast.get_source_segment(source, node.value)
                seen.append(f"{path.name}: {value}")
                assert value in allowed_values, f"{path.name}: period_key={value}"
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "period_key":
                        value = ast.get_source_segment(source, v)
                        assert value in allowed_values, f"{path.name}: {value}"
            if isinstance(node, ast.Assign):
                names = [ast.get_source_segment(source, t) or "" for t in node.targets]
                if any("period_key" in n and "period_key_" not in n for n in names):
                    value = ast.get_source_segment(source, node.value) or ""
                    ok = (value in allowed_values or value.startswith(("(None", "None,", "new[0],"))
                          or value == "new"
                          or any(value.startswith(f"{s}(") for s in allowed_sources))
                    assert ok, f"{path.name}: {names} = {value}"
    assert seen, "the scan found no writer at all"


# --------------------------------------------------------------------------- #
# 9. Security review of PR #323 — no regex or loop here is super-linear         #
# --------------------------------------------------------------------------- #

_ADVERSARIAL = {
    "spaces": " " * 10_000,
    "newlines": "\n" * 10_000,
    "tabs": "\t" * 10_000,
    "A-": "A-" * 3_000,
    "a,": "a," * 5_000,
    "spaced dashes": " -" * 5_000,
    "colons": ": " * 5_000,
    "labels": "Q3 2026 " * 1_500,
    "fiscal labels": "fy27 q1 " * 1_500,
    "period ends": "first quarter ended " * 500,
}
_BUDGET_SECONDS = 0.5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": " " * 5_000},
        {"title": " " * 10_000},
        {"headings": ["\n" * 10_000]},
        {"text": " " * 5_000},
    ],
    ids=["title-5k-spaces", "title-10k-spaces", "heading-10k-newlines", "text-5k-spaces"],
)
def test_the_reported_whitespace_inputs_are_fast(kwargs: dict) -> None:
    import time

    started = time.perf_counter()
    detect_document_period(as_of=PUBLISHED, **kwargs)
    assert time.perf_counter() - started < _BUDGET_SECONDS


@pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
@pytest.mark.parametrize("kind", ["title", "url", "headings", "text"])
def test_no_entry_point_is_slow_on_adversarial_text(kind: str, name: str) -> None:
    import time

    value = _ADVERSARIAL[name]
    kwargs: dict = {"headings": [value] * 12} if kind == "headings" else {kind: value}
    started = time.perf_counter()
    detect_document_period(as_of=PUBLISHED, **kwargs)
    assert time.perf_counter() - started < _BUDGET_SECONDS


def test_the_clause_split_scales_linearly() -> None:
    import time

    from app.services.sources.document_period import _CLAUSE_END_RE

    def cost(n: int) -> float:
        started = time.perf_counter()
        for _ in range(5):
            _CLAUSE_END_RE.split(" " * n)
        return time.perf_counter() - started

    small, large = cost(2_000), cost(20_000)
    # Ten times the input; quadratic backtracking would cost about a hundred times more.
    assert large < max(small, 1e-4) * 40


@pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
def test_the_forward_label_check_is_fast(name: str) -> None:
    import time

    started = time.perf_counter()
    ds.is_forward_label(_ADVERSARIAL[name])
    assert time.perf_counter() - started < _BUDGET_SECONDS


def test_a_long_url_is_cut_before_it_is_read() -> None:
    import time

    url = "https://ir.example.com/" + "q3-2026-" * 5_000
    started = time.perf_counter()
    detect_document_period(url=url, as_of=PUBLISHED)
    assert time.perf_counter() - started < _BUDGET_SECONDS


def test_a_clause_split_still_reads_a_spaced_dash_and_a_colon() -> None:
    from app.services.sources.document_period import _CLAUSE_END_RE

    assert _CLAUSE_END_RE.split("Q1 FY27 update – guidance") == ["Q1 FY27 update", "guidance"]
    assert _CLAUSE_END_RE.split("Q3 2026 sales - outlook") == ["Q3 2026 sales", "outlook"]
    assert _CLAUSE_END_RE.split("Q3 2026: Revenue") == ["Q3 2026", "Revenue"]
    assert _CLAUSE_END_RE.split("first-half 10:30 1,234") == ["first-half 10:30 1,234"]


# --------------------------------------------------------------------------- #
# 10. Final re-review of PR #323 — CLLS4-2 itself: a STRONG cue never dates a   #
#     strategic update to a period that is not over                             #
# --------------------------------------------------------------------------- #

#: The Cellectis pivot press release's shape, published 2026-10-28.
CLLS_PIVOT_HEAD = (
    "Cellectis Announces Strategic Update and Pipeline Prioritization\n"
    "NEW YORK, Oct. 28, 2026 -- Cellectis today announced a strategic update. "
    "The company now expects its cash runway to extend into H2 2028. "
)


@pytest.mark.parametrize(
    "tail",
    [
        "Cellectis will report its fourth quarter and full year 2026 financial results in "
        "March 2027.",
        "The Company plans to report fourth quarter 2026 results in February.",
        "Cellectis expects to report Q4 2026 results in early 2027.",
        "H2 2026 results will include a restructuring charge.",
        "The restructuring will be reflected in the Q4 2026 results.",
        "",
    ],
)
def test_the_clls_pivot_release_is_never_dated_by_a_report_still_to_come(tail: str) -> None:
    assert not detect_document_period(text=CLLS_PIVOT_HEAD + tail, as_of=PUBLISHED).is_known


def test_body_text_needs_the_period_over_whatever_the_framing() -> None:
    text = "Acme SA is a company. Results for the third quarter of 2026 are below."
    assert detect_document_period(text=text, as_of=PUBLISHED).period.key == "2026-Q3"
    assert not detect_document_period(text=text, as_of=date(2026, 9, 15)).is_known
    # The last day of the quarter is not after it.
    assert not detect_document_period(text=text, as_of=date(2026, 9, 30)).is_known
    # A fiscal period is judged by its lenient (earliest) end.
    fiscal = "Acme SA is a company. Results for the first quarter fiscal 2027 follow."
    assert detect_document_period(text=fiscal, as_of=date(2026, 8, 19)).period.key == "2027-Q1"


_NOTICE_TITLES = [
    "Acme to report Q4 2026 results",
    "Q4 2026 results expected in January",
    "Acme will report Q4 2026 results on 28 January",
    "Q4 2026 results will be published on 28 January",
    "Invitation to Q4 2026 results presentation",
    "Notice of Q4 2026 results date",
    "Financial calendar: Q4 2026 report on 5 February",
    "H2 2026 report to include impairment",
    "Q4 2026 interim report date changed",
    "Acme to report Q4 2026 sales in January",
    "Acme to host Q4 2026 earnings conference call",
]


@pytest.mark.parametrize("title", _NOTICE_TITLES)
def test_a_notice_of_a_report_to_come_does_not_date_a_title(title: str) -> None:
    assert not detect_document_period(title=title, as_of=PUBLISHED).is_known


@pytest.mark.parametrize("title", _NOTICE_TITLES)
def test_a_notice_of_a_report_to_come_does_not_date_a_heading(title: str) -> None:
    assert not detect_document_period(headings=[title], as_of=PUBLISHED).is_known


@pytest.mark.parametrize(
    ("title", "published", "key"),
    [
        ("Q4 2026 trading update", date(2026, 12, 15), "2026-Q4"),
        ("Pre-close trading update H2 2026", date(2026, 12, 10), "2026-H2"),
        ("Acme to announce Q3 2026 results on 5 November", PUBLISHED, "2026-Q3"),
        ("Q3 2026 results conference call", PUBLISHED, "2026-Q3"),
    ],
)
def test_a_running_or_announced_period_without_a_notice_still_dates(
    title: str, published: date, key: str
) -> None:
    assert detect_document_period(title=title, as_of=published).period.key == key


def test_a_notice_cue_never_decides_a_role_on_its_own() -> None:
    assert _classify("Acme to announce Q3 2026 results on 5 November", "Q3 2026",
                     ds.ROLE_TITLE) == ds.REPORTING_PERIOD
