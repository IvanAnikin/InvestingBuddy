"""The corpus document and version records — V3.1 Slice 1.2.

WHAT THESE TESTS PIN
====================
The distinction the whole slice exists for: a content hash identifies *bytes*,
and a document is not its bytes.

  * two retrievals of the same declared document are one document, two versions;
  * a restatement supersedes without overwriting — old rows and their citations
    survive, only ``is_current`` moves;
  * "exactly one current version" is enforced by the DATABASE, not by convention;
  * documents are never merged on a guess — no declared period means a
    URL-derived identity, and two different URLs stay two documents;
  * an unstated period stays NULL and is never coerced to a year;
  * ``published_at`` is never filled from ``retrieved_at``;
  * the V2 bridge: every corpus version points back at its ``ExtractedDocument``,
    and the backfill is resumable and idempotent;
  * with ``V3_CORPUS_ENABLED`` off, nothing is written at all.

Everything runs against a real SQLite database — real INSERTs, real unique-index
violations, real partial indexes. A mocked session would let the "exactly one
current version" claim pass while being false, because the assertion would be
about what we asked the mock.

No clock dependence: every ordering test passes explicit timestamps.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_artifact as _research_artifact  # noqa: F401
from app.models import research_document as _research_document  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.extracted_document import ExtractedDocument
from app.models.research_artifact import ResearchArtifact
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.corpus.documents import (
    CorpusIngestResult,
    DocumentVersionInput,
    backfill_from_extracted_documents,
    period_fields,
    upsert_document_version,
)
from app.services.corpus.identity import (
    BASIS_ANNUAL_TITLE_LABEL,
    PERIOD_TYPE_SPLIT_YEAR,
    URL_KEY_PREFIX,
    annual_period_from,
    document_key_for,
    is_url_derived_key,
    normalize_document_type,
)
from app.services.sources.document_period import (
    BASIS_PERIOD_LABEL,
    UNKNOWN_DOCUMENT_PERIOD,
    DocumentPeriod,
    detect_document_period,
)
from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, ReportingPeriod
from app.services.sources.taxonomy import T1_PRIMARY_FILING
from tests.helpers.source_scan import modules_using

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {"v3_corpus_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _annual(year: int = 2025) -> DocumentPeriod:
    return DocumentPeriod(
        period=ReportingPeriod(PERIOD_TYPE_ANNUAL, year, None, str(year)),
        basis=BASIS_PERIOD_LABEL,
        evidence=f"annual report {year}",
    )


def _payload(**overrides: object) -> DocumentVersionInput:
    base: dict[str, object] = {
        "content_hash": "a" * 64,
        "canonical_url": "https://pandoragroup.com/annual-report-2025.pdf",
        "transport": "company_ir",
        "source_tier": T1_PRIMARY_FILING,
        "document_type": "annual_report",
        "title": "Annual Report 2025",
        "media_type": "application/pdf",
        "period": _annual(),
        "retrieved_at": T0,
    }
    base.update(overrides)
    return DocumentVersionInput(**base)  # type: ignore[arg-type]


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


# --------------------------------------------------------------------------- #
# Identity — the module the whole slice rests on
# --------------------------------------------------------------------------- #


class TestDocumentIdentity:
    def test_a_declared_kind_and_period_is_the_identity(self) -> None:
        assert (
            document_key_for(
                document_type="annual_report",
                period_key="2025",
                canonical_url="https://x.example/a.pdf",
            )
            == "annual_report:2025"
        )

    def test_the_same_declared_document_from_a_different_url_is_the_same_key(self) -> None:
        # The whole point: a re-hosted or re-typeset annual report is one document.
        a = document_key_for(
            document_type="annual_report", period_key="2025", canonical_url="https://a/x.pdf"
        )
        b = document_key_for(
            document_type="annual_report", period_key="2025", canonical_url="https://b/y.pdf"
        )
        assert a == b

    def test_different_periods_are_different_documents(self) -> None:
        assert document_key_for(
            document_type="annual_report", period_key="2025", canonical_url="https://a/x.pdf"
        ) != document_key_for(
            document_type="annual_report", period_key="2024", canonical_url="https://a/x.pdf"
        )

    def test_annual_and_interim_for_one_year_are_different_documents(self) -> None:
        assert document_key_for(
            document_type="annual_report", period_key="2026", canonical_url="https://a/x.pdf"
        ) != document_key_for(
            document_type="interim_report", period_key="2026", canonical_url="https://a/x.pdf"
        )

    def test_no_declared_period_falls_back_to_the_address(self) -> None:
        key = document_key_for(
            document_type="annual_report",
            period_key=None,
            canonical_url="https://pandoragroup.com/x.pdf",
        )
        assert is_url_derived_key(key)
        assert key.startswith(URL_KEY_PREFIX)

    def test_two_undeclared_documents_at_different_urls_stay_separate(self) -> None:
        # Merging is the expensive error; splitting is the cheap one. This picks
        # the cheap one deliberately.
        a = document_key_for(document_type=None, period_key=None, canonical_url="https://a/x.pdf")
        b = document_key_for(document_type=None, period_key=None, canonical_url="https://a/y.pdf")
        assert a != b

    def test_a_credential_bearing_url_variant_is_the_same_document(self) -> None:
        plain = document_key_for(
            document_type=None, period_key=None, canonical_url="https://a.example/report.pdf"
        )
        signed = document_key_for(
            document_type=None,
            period_key=None,
            canonical_url="https://a.example/report.pdf?token=SECRETVALUE",
        )
        assert plain == signed

    def test_a_url_key_carries_no_url(self) -> None:
        key = document_key_for(
            document_type=None,
            period_key=None,
            canonical_url="https://a.example/report.pdf?token=SECRETVALUE",
        )
        for leak in ("SECRETVALUE", "a.example", "http", "report.pdf"):
            assert leak not in key

    def test_an_unknown_document_type_is_other_not_a_guess(self) -> None:
        assert normalize_document_type("sustainability_brochure") == "other"
        assert normalize_document_type(None) == "other"

    def test_an_other_typed_document_never_claims_a_period_identity(self) -> None:
        # "other:2025" would assert that every unclassified 2025 document is the
        # same document. It is not.
        key = document_key_for(
            document_type="other", period_key="2025", canonical_url="https://a/x.pdf"
        )
        assert is_url_derived_key(key)


class TestPeriodFields:
    def test_an_unknown_period_stays_null(self) -> None:
        assert period_fields(None) == (None, None, None)
        assert period_fields(UNKNOWN_DOCUMENT_PERIOD) == (None, None, None)

    def test_a_known_period_carries_key_type_and_basis(self) -> None:
        key, ptype, basis = period_fields(_annual(2025))
        assert (key, ptype, basis) == ("2025", PERIOD_TYPE_ANNUAL, BASIS_PERIOD_LABEL)

    def test_an_interim_document_keeps_its_interim_type(self) -> None:
        # The INTERIM_AS_ANNUAL contradiction begins with losing this.
        detected = detect_document_period(title="Q2 2026 sales release")
        key, ptype, _ = period_fields(detected)
        assert key == "2026-Q2"
        assert ptype == "quarter"


class TestAnnualPeriodRule:
    """The narrow corpus-only rule that reads a year off a document's own cover.

    `document_period` refuses a bare year because a bare year beside a FIGURE is a
    guess. A year beside a document's own name for itself is not — and refusing to
    read it would leave every annual report with a URL identity, which is the one
    document class where "version, not overwrite" matters most.
    """

    def test_a_documents_own_name_for_itself_carries_its_year(self) -> None:
        assert annual_period_from(title="Annual Report 2025", url=None) == (
            "2025",
            PERIOD_TYPE_ANNUAL,
            BASIS_ANNUAL_TITLE_LABEL,
        )
        assert annual_period_from(title="2025 Annual Report", url=None)[0] == "2025"
        assert (
            annual_period_from(title="Universal Registration Document 2024", url=None)[0]
            == "2024"
        )
        assert (
            annual_period_from(title="Annual and Sustainability Report 2025", url=None)[0]
            == "2025"
        )

    def test_an_issuer_published_url_slug_counts_as_the_documents_own_words(self) -> None:
        assert (
            annual_period_from(
                title=None, url="https://pandoragroup.com/annual-report-2025.pdf"
            )[0]
            == "2025"
        )

    def test_a_split_fiscal_year_keeps_its_split_type(self) -> None:
        # Richemont's year ends in March. "2025/26" is one period, not two.
        assert annual_period_from(title=None, url="https://x/annual-report-2025-26.pdf") == (
            "2025/26",
            PERIOD_TYPE_SPLIT_YEAR,
            BASIS_ANNUAL_TITLE_LABEL,
        )

    def test_a_year_range_is_not_a_split_year(self) -> None:
        # "2025-2026" is a range of two years; treating it as a split fiscal year
        # would invent a period the issuer never named.
        assert annual_period_from(title="Annual Report 2025-2026", url=None)[1] != (
            PERIOD_TYPE_SPLIT_YEAR
        )

    def test_a_loose_number_in_a_filename_is_not_a_period(self) -> None:
        assert annual_period_from(title="Report", url="https://a/doc/2025-98213.pdf") == (
            None,
            None,
            None,
        )
        assert annual_period_from(title=None, url="https://a/investors/98213.pdf") == (
            None,
            None,
            None,
        )

    def test_two_disagreeing_years_refuse_rather_than_pick_one(self) -> None:
        # An ambiguous cover is not evidence.
        assert annual_period_from(
            title="Annual Report 2024", url="https://x/annual-report-2025.pdf"
        ) == (None, None, None)

    def test_an_interim_document_is_never_read_as_annual(self) -> None:
        assert annual_period_from(title="Q2 2026 sales release", url="https://x/q2-2026.pdf") == (
            None,
            None,
            None,
        )

    def test_the_rule_never_reaches_the_fact_pipeline(self) -> None:
        # The blast radius of being wrong here must stay "a mis-filed document",
        # never "a mis-dated figure". Only the corpus may import it.
        importers = [
            str(p)
            for p in Path("app").rglob("*.py")
            if "annual_period_from" in p.read_text()
            and "app/services/corpus/" not in str(p).replace("\\", "/")
        ]
        assert importers == [], importers


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


class TestUpsert:
    async def test_the_corpus_flag_off_writes_nothing(self, session) -> None:  # noqa: ANN001
        assert (
            await upsert_document_version(
                session, _payload(), cfg=_cfg(v3_corpus_enabled=False)
            )
            is None
        )
        assert (await session.execute(select(ResearchDocument))).scalars().all() == []

    async def test_one_retrieval_creates_one_document_and_one_version(self, session) -> None:  # noqa: ANN001
        result = CorpusIngestResult()
        version = await upsert_document_version(session, _payload(), cfg=_cfg(), result=result)
        assert version is not None
        assert result.documents_created == 1
        assert result.versions_created == 1
        assert version.is_current is True
        doc = (await session.execute(select(ResearchDocument))).scalars().one()
        assert doc.document_key == "annual_report:2025"
        assert doc.period_key == "2025"

    async def test_re_ingesting_the_same_bytes_is_idempotent(self, session) -> None:  # noqa: ANN001
        first = await upsert_document_version(session, _payload(), cfg=_cfg())
        result = CorpusIngestResult()
        second = await upsert_document_version(session, _payload(), cfg=_cfg(), result=result)
        assert first is not None and second is not None
        assert first.id == second.id
        assert result.versions_created == 0
        assert result.versions_reused == 1
        versions = (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        assert len(versions) == 1

    async def test_a_restatement_is_a_new_version_of_the_same_document(self, session) -> None:  # noqa: ANN001
        original = await upsert_document_version(session, _payload(), cfg=_cfg())
        restated = await upsert_document_version(
            session,
            _payload(
                content_hash="b" * 64,
                canonical_url="https://pandoragroup.com/annual-report-2025-restated.pdf",
                retrieved_at=T0 + timedelta(days=200),
            ),
            cfg=_cfg(),
        )
        assert original is not None and restated is not None
        docs = (await session.execute(select(ResearchDocument))).scalars().all()
        assert len(docs) == 1
        await session.refresh(original)
        assert restated.is_current is True
        assert original.is_current is False
        # The original is not deleted and not rewritten — its hash, its URL and
        # therefore every citation into it still resolve.
        assert original.content_hash == "a" * 64
        assert original.canonical_url.endswith("annual-report-2025.pdf")
        assert original.superseded_at is not None

    async def test_re_ingesting_an_older_retrieval_never_rolls_the_corpus_back(
        self, session
    ) -> None:  # noqa: ANN001
        newer = await upsert_document_version(
            session, _payload(retrieved_at=T0 + timedelta(days=10)), cfg=_cfg()
        )
        older = await upsert_document_version(
            session, _payload(content_hash="c" * 64, retrieved_at=T0), cfg=_cfg()
        )
        assert newer is not None and older is not None
        await session.refresh(newer)
        assert newer.is_current is True
        assert older.is_current is False

    async def test_two_current_versions_are_impossible(self, session) -> None:  # noqa: ANN001
        # The invariant as a DATABASE constraint, verified by trying to break it.
        first = await upsert_document_version(session, _payload(), cfg=_cfg())
        assert first is not None
        rogue = ResearchDocumentVersion(
            id=uuid.uuid4(),
            research_document_id=first.research_document_id,
            content_hash="d" * 64,
            canonical_url="https://x/y.pdf",
            transport="company_ir",
            source_tier=T1_PRIMARY_FILING,
            access_class="public_issuer",
            retrieved_at=T0,
            extraction_status="extracted",
            is_current=True,
        )
        session.add(rogue)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_the_same_bytes_for_two_companies_are_two_documents(self, session) -> None:  # noqa: ANN001
        # Company isolation: an artifact is shared, a document is not.
        a, b = uuid.uuid4(), uuid.uuid4()
        await upsert_document_version(session, _payload(company_id=a), cfg=_cfg())
        await upsert_document_version(session, _payload(company_id=b), cfg=_cfg())
        docs = (await session.execute(select(ResearchDocument))).scalars().all()
        assert len(docs) == 2
        assert {d.company_id for d in docs} == {a, b}

    async def test_a_retrieval_without_a_content_hash_is_skipped_not_invented(
        self, session
    ) -> None:  # noqa: ANN001
        result = CorpusIngestResult()
        assert (
            await upsert_document_version(
                session, _payload(content_hash=""), cfg=_cfg(), result=result
            )
            is None
        )
        assert result.skipped == 1
        assert (await session.execute(select(ResearchDocument))).scalars().all() == []

    async def test_a_silent_document_gets_no_period_at_all(self, session) -> None:  # noqa: ANN001
        version = await upsert_document_version(
            session,
            _payload(
                period=None,
                title="Report",
                canonical_url="https://a.example/investors/doc/98213.pdf",
            ),
            cfg=_cfg(),
        )
        assert version is not None
        assert version.period_key is None
        assert version.period_type is None
        doc = (await session.execute(select(ResearchDocument))).scalars().one()
        assert doc.period_key is None
        assert is_url_derived_key(doc.document_key)

    async def test_a_document_that_names_its_own_year_is_identified_by_it(
        self, session
    ) -> None:  # noqa: ANN001
        # `document_period` refuses a bare year on purpose, so without the narrow
        # annual rule every annual report would fall back to a URL identity and a
        # restatement would become a second document rather than a second version.
        version = await upsert_document_version(session, _payload(period=None), cfg=_cfg())
        assert version is not None
        assert version.period_key == "2025"
        assert version.period_type == PERIOD_TYPE_ANNUAL
        assert version.period_basis == BASIS_ANNUAL_TITLE_LABEL
        doc = (await session.execute(select(ResearchDocument))).scalars().one()
        assert doc.document_key == "annual_report:2025"

    async def test_published_at_is_never_filled_from_retrieved_at(self, session) -> None:  # noqa: ANN001
        version = await upsert_document_version(session, _payload(), cfg=_cfg())
        assert version is not None
        assert version.published_at is None
        assert version.retrieved_at is not None

    async def test_a_stated_publication_date_is_kept_verbatim(self, session) -> None:  # noqa: ANN001
        version = await upsert_document_version(
            session, _payload(published_at=date(2026, 2, 4)), cfg=_cfg()
        )
        assert version is not None
        assert version.published_at == date(2026, 2, 4)

    async def test_the_transport_never_becomes_the_tier(self, session) -> None:  # noqa: ANN001
        version = await upsert_document_version(
            session,
            _payload(
                transport="sec_edgar",
                source_tier=T1_PRIMARY_FILING,
                content_origin="Reuters",
            ),
            cfg=_cfg(),
        )
        assert version is not None
        assert version.transport == "sec_edgar"
        assert version.source_tier == T1_PRIMARY_FILING
        assert version.content_origin == "Reuters"

    async def test_an_unknown_access_class_denies_rather_than_defaulting_public(
        self, session
    ) -> None:  # noqa: ANN001
        version = await upsert_document_version(
            session, _payload(access_class="something_new"), cfg=_cfg()
        )
        assert version is not None
        assert version.access_class == "licensed_private"

    async def test_a_failed_retrieval_is_recorded_honestly_not_omitted(self, session) -> None:  # noqa: ANN001
        version = await upsert_document_version(
            session,
            _payload(extraction_status="metadata_only", failure_code="scanned_no_text"),
            cfg=_cfg(),
        )
        assert version is not None
        assert version.extraction_status == "metadata_only"
        assert version.failure_code == "scanned_no_text"

    async def test_a_later_retrieval_fills_a_previously_unknown_title(self, session) -> None:  # noqa: ANN001
        await upsert_document_version(session, _payload(title=None), cfg=_cfg())
        await upsert_document_version(
            session, _payload(content_hash="e" * 64, title="Annual Report 2025"), cfg=_cfg()
        )
        doc = (await session.execute(select(ResearchDocument))).scalars().one()
        assert doc.title == "Annual Report 2025"

    async def test_a_later_retrieval_never_erases_a_known_period(self, session) -> None:  # noqa: ANN001
        # A retrieval whose title happens to omit the year must not blank a period
        # an earlier one read off the document's own title page.
        await upsert_document_version(session, _payload(), cfg=_cfg())
        doc_before = (await session.execute(select(ResearchDocument))).scalars().one()
        assert doc_before.period_key == "2025"
        await upsert_document_version(
            session, _payload(content_hash="f" * 64, period=None), cfg=_cfg()
        )
        docs = (await session.execute(select(ResearchDocument))).scalars().all()
        assert any(d.period_key == "2025" for d in docs)

    async def test_the_artifact_link_is_backfilled_on_a_later_retrieval(self, session) -> None:  # noqa: ANN001
        art = ResearchArtifact(
            id=uuid.uuid4(),
            content_hash="a" * 64,
            byte_size=10,
            media_type="application/pdf",
            storage_backend="memory",
            storage_key="sha256/aa/aa/" + "a" * 64 + ".pdf",
            access_class="public_issuer",
            policy_quoted="full",
            first_seen_at=T0,
            last_seen_at=T0,
        )
        session.add(art)
        await session.flush()
        version = await upsert_document_version(session, _payload(), cfg=_cfg())
        assert version is not None and version.research_artifact_id is None
        again = await upsert_document_version(
            session, _payload(research_artifact_id=art.id), cfg=_cfg()
        )
        assert again is not None
        assert again.id == version.id
        assert again.research_artifact_id == art.id


# --------------------------------------------------------------------------- #
# The V2 bridge and the backfill
# --------------------------------------------------------------------------- #


def _extracted(**overrides: object) -> ExtractedDocument:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "content_hash": "a" * 64,
        "canonical_url": "https://pandoragroup.com/annual-report-2025.pdf",
        "provider": "company_ir",
        "source_type": "annual_report",
        "source_tier": T1_PRIMARY_FILING,
        "mime_type": "application/pdf",
        "title": "Annual Report 2025",
        "retrieved_at": T0,
        "extraction_method": "native_pdf",
        "status": "extracted",
        "page_count": 169,
    }
    base.update(overrides)
    return ExtractedDocument(**base)  # type: ignore[arg-type]


class TestBackfill:
    async def test_the_corpus_flag_off_backfills_nothing(self, session) -> None:  # noqa: ANN001
        session.add(_extracted())
        await session.flush()
        result = await backfill_from_extracted_documents(
            session, cfg=_cfg(v3_corpus_enabled=False)
        )
        assert result.versions_created == 0
        assert (await session.execute(select(ResearchDocument))).scalars().all() == []

    async def test_every_historical_row_becomes_a_corpus_version(self, session) -> None:  # noqa: ANN001
        for i in range(3):
            session.add(
                _extracted(
                    content_hash=f"{i}" * 64,
                    canonical_url=f"https://pandoragroup.com/report-{i}.pdf",
                    title=f"Annual Report {2023 + i}",
                )
            )
        await session.flush()
        result = await backfill_from_extracted_documents(session, cfg=_cfg())
        assert result.versions_created == 3
        versions = (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        assert len(versions) == 3
        assert all(v.extracted_document_id is not None for v in versions)

    async def test_the_backfill_is_idempotent(self, session) -> None:  # noqa: ANN001
        session.add(_extracted())
        await session.flush()
        first = await backfill_from_extracted_documents(session, cfg=_cfg())
        second = await backfill_from_extracted_documents(session, cfg=_cfg())
        assert first.versions_created == 1
        assert second.versions_created == 0
        assert second.versions_reused == 0  # nothing was even selected

    async def test_the_backfill_is_resumable_in_batches(self, session) -> None:  # noqa: ANN001
        for i in range(5):
            session.add(
                _extracted(
                    content_hash=f"{i}" * 64,
                    canonical_url=f"https://pandoragroup.com/r{i}.pdf",
                    retrieved_at=T0 + timedelta(days=i),
                )
            )
        await session.flush()
        a = await backfill_from_extracted_documents(session, cfg=_cfg(), limit=2)
        b = await backfill_from_extracted_documents(session, cfg=_cfg(), limit=2)
        c = await backfill_from_extracted_documents(session, cfg=_cfg(), limit=2)
        assert (a.versions_created, b.versions_created, c.versions_created) == (2, 2, 1)

    async def test_the_backfill_is_company_scoped_when_asked(self, session) -> None:  # noqa: ANN001
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        session.add(_extracted(content_hash="1" * 64, company_id=mine))
        session.add(
            _extracted(
                content_hash="2" * 64,
                company_id=theirs,
                canonical_url="https://other.example/r.pdf",
            )
        )
        await session.flush()
        result = await backfill_from_extracted_documents(session, cfg=_cfg(), company_id=mine)
        assert result.versions_created == 1
        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        doc = await session.get(ResearchDocument, version.research_document_id)
        assert doc is not None and doc.company_id == mine

    async def test_a_historical_row_never_claims_bytes_it_never_had(self, session) -> None:  # noqa: ANN001
        session.add(_extracted())
        await session.flush()
        await backfill_from_extracted_documents(session, cfg=_cfg())
        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        assert version.research_artifact_id is None

    async def test_the_backfill_derives_a_period_from_the_documents_own_words(
        self, session
    ) -> None:  # noqa: ANN001
        session.add(_extracted(title="Q2 2026 sales release"))
        await session.flush()
        await backfill_from_extracted_documents(session, cfg=_cfg())
        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        assert version.period_key == "2026-Q2"
        assert version.period_type == "quarter"

    async def test_a_silent_historical_row_gets_no_invented_period(self, session) -> None:  # noqa: ANN001
        session.add(_extracted(title="Report", canonical_url="https://a.example/doc/98213.pdf"))
        await session.flush()
        await backfill_from_extracted_documents(session, cfg=_cfg())
        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        assert version.period_key is None

    def test_nothing_calls_the_backfill_automatically(self) -> None:
        # A backfill that starts itself on the first request after a deploy is how
        # a migration turns into an outage. It stays operator-invoked.
        #
        # The scan looks for the name in EXECUTABLE position rather than anywhere in
        # the file. A substring grep also fires on the docstrings that explain why
        # this rule exists — V3.2's own backfill cites this one as its precedent —
        # and a test that fails when somebody documents the rule it enforces is a
        # test that gets deleted. See ``tests.helpers.source_scan``.
        callers = modules_using(
            "backfill_from_extracted_documents",
            root=Path("app"),
            exclude=("corpus/documents.py",),
        )
        assert callers == [], callers


class TestIngestionWiring:
    async def test_persisting_a_v2_artifact_records_the_corpus_beside_it(
        self, session
    ) -> None:  # noqa: ANN001
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.corpus.artifacts.service import store_raw_artifact
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
        from app.services.sources.primary_document_extractor import (
            PrimaryDocumentExtraction,
        )

        raw = b"%PDF-1.7 Pandora Annual Report 2025 fixture"
        cfg = Settings(
            v3_corpus_enabled=True,
            v3_artifact_store_backend="memory",
            primary_document_ingestion_enabled=True,
            report_citation_persistence_enabled=True,
        )
        stored = await store_raw_artifact(
            raw,
            media_type="application/pdf",
            access_class="public_issuer",
            cfg=cfg,
            store=InMemoryArtifactStore(),
        )
        assert stored is not None
        artifact = PrimaryDocumentArtifact(
            source_url="https://pandoragroup.com/annual-report-2025.pdf",
            status="extracted",
            title="Annual Report 2025",
            doc_kind="annual_report",
            extraction=PrimaryDocumentExtraction(
                content_hash=stored.content_hash,
                mime_type="application/pdf",
                extraction_method="native_pdf",
                status="extracted",
                page_count=169,
                language="en",
            ),
            raw_artifact=stored,
        )
        company_id = uuid.uuid4()
        result = await persist_primary_document_artifacts(
            session,
            artifacts=[artifact],
            company_id=company_id,
            agent_run_id=None,
            cfg=cfg,
        )
        assert result.corpus_documents_created == 1
        assert result.corpus_versions_created == 1

        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        extracted = (await session.execute(select(ExtractedDocument))).scalars().one()
        artifact_row = (await session.execute(select(ResearchArtifact))).scalars().one()
        # The three records join on ONE identity, not three hashes.
        assert version.content_hash == extracted.content_hash == artifact_row.content_hash
        # And the bridge back to V2 is explicit.
        assert version.extracted_document_id == extracted.id
        assert version.research_artifact_id == artifact_row.id
        assert version.is_current is True
        assert version.period_key == "2025"

        doc = await session.get(ResearchDocument, version.research_document_id)
        assert doc is not None
        assert doc.company_id == company_id
        assert doc.document_key == "annual_report:2025"

    async def test_with_the_corpus_off_v2_persistence_is_unchanged(self, session) -> None:  # noqa: ANN001
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
        from app.services.sources.primary_document_extractor import (
            PrimaryDocumentExtraction,
        )

        cfg = Settings(
            v3_corpus_enabled=False,
            primary_document_ingestion_enabled=True,
            report_citation_persistence_enabled=True,
        )
        artifact = PrimaryDocumentArtifact(
            source_url="https://pandoragroup.com/annual-report-2025.pdf",
            status="extracted",
            extraction=PrimaryDocumentExtraction(
                content_hash="a" * 64,
                mime_type="application/pdf",
                extraction_method="native_pdf",
                status="extracted",
            ),
        )
        result = await persist_primary_document_artifacts(
            session,
            artifacts=[artifact],
            company_id=uuid.uuid4(),
            agent_run_id=None,
            cfg=cfg,
        )
        assert result.documents_created == 1
        assert result.corpus_versions_created == 0
        assert (await session.execute(select(ResearchDocument))).scalars().all() == []
        assert (await session.execute(select(ResearchDocumentVersion))).scalars().all() == []
