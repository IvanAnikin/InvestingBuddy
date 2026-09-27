"""Non-US primary documents — the foundation (no behaviour change for existing paths).

1. ``safe_post_json``: a regulator SEARCH API that only answers POST (FCA NSM) is queried
   through the same guards as a document fetch, and refuses redirects and non-JSON.
2. Provenance: an artifact delivered by ``uk_fca_nsm`` / ``asx_announcements`` records
   THAT transport (it used to be stamped ``company_ir``) and its official publication
   date — which is never read as the period it covers.
3. Period integrity: an announcement's body states forecasts. Read live on real text:
   "first production expected in Q1 2028" stamped the announcement 2028-Q1, and so did a
   quarterly report for the quarter ENDED 30 June 2026. ``period_policy="title_only"``
   reads the period from the official title alone.
4. Readiness: the single "searchable" predicate also answers for a non-SEC official
   document id — company-scoped, current-version-required, indexed-chunks-required.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.services.sources.document_fetcher import safe_post_json


def _public_resolver(host, *_a, **_k):  # noqa: ANN001, ANN202
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


class _Resp:
    def __init__(self, *, status_code=200, headers=None, body=b"", is_redirect=False):
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for i in range(0, max(1, len(self._body)), 1024):
            yield self._body[i : i + 1024]


class _Client:
    def __init__(self, resp, seen, **kw):
        self._resp = resp
        self._seen = seen
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        self._seen.append((method, url, kw))
        return self._resp


def _patch(monkeypatch, resp):  # noqa: ANN001, ANN202
    import httpx

    seen: list = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(resp, seen, **kw))
    return seen


def _post(url="https://api.data.fca.org.uk/search?index=nsm-search", **kw):  # noqa: ANN001, ANN202
    return asyncio.run(safe_post_json(
        url, {"from": 0, "size": 1}, allowed_domains=("api.data.fca.org.uk",),
        resolver=_public_resolver, **kw))


class TestSafePostJson:
    def test_a_json_listing_is_returned_and_the_body_is_the_callers_query(self, monkeypatch):
        seen = _patch(monkeypatch, _Resp(headers={"content-type": "application/json"},
                                         body=b'{"hits": []}'))
        r = _post()
        assert r.ok and r.content == b'{"hits": []}'
        method, _url, kw = seen[0]
        assert method == "POST" and kw["content"] == b'{"from": 0, "size": 1}'

    @pytest.mark.parametrize("url", [
        "http://api.data.fca.org.uk/search", "https://evil.example/search",
        "https://127.0.0.1/search", "https://localhost/search",
    ])
    def test_off_allowlist_or_unsafe_hosts_are_never_contacted(self, monkeypatch, url):
        seen = _patch(monkeypatch, _Resp(headers={"content-type": "application/json"}))
        r = _post(url)
        assert r.blocked and not seen

    def test_a_redirect_is_refused_not_followed(self, monkeypatch):
        seen = _patch(monkeypatch, _Resp(status_code=307, is_redirect=True,
                                         headers={"location": "https://evil.example/x"}))
        r = _post()
        assert r.blocked and "redirect" in (r.error or "") and len(seen) == 1

    def test_non_json_is_refused(self, monkeypatch):
        _patch(monkeypatch, _Resp(headers={"content-type": "text/html"}, body=b"<html>"))
        assert _post().blocked

    def test_an_oversized_listing_is_refused_not_truncated(self, monkeypatch):
        _patch(monkeypatch, _Resp(headers={"content-type": "application/json"},
                                  body=b"x" * 5000))
        r = _post(max_bytes=1000)
        assert not r.ok and r.content is None and r.blocked
        assert not r.truncated  # "truncated" means partial content IS present
        assert r.failure_code == "response_too_large"

    def test_a_3xx_without_location_is_still_a_refused_redirect(self, monkeypatch):
        _patch(monkeypatch, _Resp(status_code=303, headers={"content-type": "application/json"},
                                  body=b"{}"))
        assert _post().blocked

    @pytest.mark.parametrize("ctype", ["application/jsonp", "text/json-ish", "text/html"])
    def test_only_a_json_media_type_is_accepted(self, monkeypatch, ctype):
        _patch(monkeypatch, _Resp(headers={"content-type": ctype}, body=b"{}"))
        assert _post().blocked

    def test_a_json_suffix_media_type_is_accepted(self, monkeypatch):
        _patch(monkeypatch, _Resp(headers={"content-type": "application/vnd.api+json"},
                                  body=b"{}"))
        assert _post().ok


# --------------------------------------------------------------------------- #
# Persistence: transport, publication date, period policy, readiness
# --------------------------------------------------------------------------- #


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


CFG = Settings(v3_corpus_enabled=True, v3_artifact_store_backend="memory",
               primary_document_ingestion_enabled=True,
               report_citation_persistence_enabled=True)

FORECAST_BODY = (
    b"<html><body><h1>Project update</h1><p>"
    + b"The Company is pleased to report construction progress at the mine. " * 6
    + b"First production is expected in Q1 2028, subject to financing. "
    + b"</p></body></html>"
)


async def _persist(session, *, title, body=FORECAST_BODY, policy="title_only",  # noqa: ANN001, ANN202
                   url="https://data.fca.org.uk/artefacts/NSM/PRN/91cc0ab0-3199-4dac-8d0f-35a6a5701273.html",
                   company_id=None, transport="uk_fca_nsm"):
    from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
    from app.services.corpus.artifacts.service import store_raw_artifact
    from app.services.extracted_document_service import persist_primary_document_artifacts
    from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
    from app.services.sources.primary_document_extractor import extract_html

    extraction = extract_html(body, cfg=CFG, capture_blocks=True)
    stored = await store_raw_artifact(body, media_type="text/html",
                                      access_class="public_issuer", cfg=CFG,
                                      store=InMemoryArtifactStore())
    extraction.content_hash = stored.content_hash
    company_id = company_id or uuid.uuid4()
    await persist_primary_document_artifacts(
        session,
        artifacts=[PrimaryDocumentArtifact(
            source_url=url, status="extracted", title=title, doc_kind="other",
            extraction=extraction, raw_artifact=stored, transport=transport,
            published_at=date(2026, 9, 25), period_policy=policy)],
        company_id=company_id, agent_run_id=None, cfg=CFG)
    return company_id


class TestProvenanceAndPeriod:
    async def test_the_transport_and_publication_date_are_the_official_ones(self, session):
        from app.models.extracted_document import ExtractedDocument
        from app.models.research_document import ResearchDocumentVersion

        await _persist(session, title="Pensana Plc - Update on Longonjo Financing")
        doc = (await session.execute(select(ExtractedDocument))).scalar_one()
        assert doc.provider == "uk_fca_nsm" and doc.doc_date == date(2026, 9, 25)
        version = (await session.execute(select(ResearchDocumentVersion))).scalar_one()
        assert version.transport == "uk_fca_nsm"
        assert version.published_at == date(2026, 9, 25)

    async def test_a_forecast_in_the_body_never_becomes_the_documents_period(self, session):
        from app.models.research_document import ResearchDocumentVersion

        await _persist(session, title="Epanko Value Engineering Identifies 20% Production")
        version = (await session.execute(select(ResearchDocumentVersion))).scalar_one()
        assert version.period_key is None  # NOT 2028-Q1: missing means missing

    async def test_without_the_policy_the_body_forecast_would_stamp_it(self, session):
        """The guard is load-bearing: the default body read DOES take the forecast."""
        from app.models.research_document import ResearchDocumentVersion

        await _persist(session, title="Project update", policy=None)
        version = (await session.execute(select(ResearchDocumentVersion))).scalar_one()
        assert version.period_key is not None and "2028" in version.period_key

    async def test_the_official_title_still_states_a_real_period(self, session):
        from app.models.research_document import ResearchDocumentVersion

        await _persist(session, title=("Pensana Plc - Unaudited Interim results for the "
                                       "six months ended 31 December 2025"))
        version = (await session.execute(select(ResearchDocumentVersion))).scalar_one()
        assert version.period_type == "half" and "2025" in (version.period_key or "")

    async def test_the_default_transport_is_unchanged(self, session):
        from app.models.extracted_document import ExtractedDocument

        await _persist(session, title="Annual Report 2025", transport=None, policy=None)
        doc = (await session.execute(select(ExtractedDocument))).scalar_one()
        assert doc.provider == "company_ir"


class TestReadinessByDocumentRef:
    REF = "91cc0ab0-3199-4dac-8d0f-35a6a5701273"

    async def _index_all(self, session):  # noqa: ANN001, ANN202
        from app.models.research_chunk import ResearchDocumentChunk

        await session.execute(update(ResearchDocumentChunk).values(
            indexable=True, indexed_at=datetime.now(timezone.utc)))
        await session.flush()

    async def test_ready_only_for_the_owning_company_and_only_when_indexed(self, session):
        from app.services.corpus.filing_evidence import document_evidence_state

        company = await _persist(session, title="Update on Longonjo Financing")
        before = await document_evidence_state(session, company_id=company,
                                               document_ref=self.REF)
        assert not before.is_ready  # chunks exist but nothing is indexed
        await self._index_all(session)
        after = await document_evidence_state(session, company_id=company,
                                              document_ref=self.REF)
        assert after.is_ready and after.indexable_chunk_count > 0
        other = await document_evidence_state(session, company_id=uuid.uuid4(),
                                              document_ref=self.REF)
        assert not other.is_ready  # another company's copy never satisfies this one

    @pytest.mark.parametrize("ref", ["", "../../etc", "a%2Fb", "abc", "x" * 200, "a b c d e"])
    async def test_an_unvalidated_reference_is_absent_never_a_wildcard(self, session, ref):
        from app.services.corpus.filing_evidence import document_evidence_state

        state = await document_evidence_state(session, company_id=uuid.uuid4(),
                                              document_ref=ref)
        assert state.state == "absent"


class TestReadinessIsWholeSegmentOnly:
    """HIGH-1 (review): a ref matched as a substring let one document answer for another."""

    @pytest.mark.parametrize(("url", "ref", "expected"), [
        ("https://data.fca.org.uk/artefacts/NSM/Portal/NI-000131364/NI-000131364.pdf",
         "NI-000131364", True),
        ("https://data.fca.org.uk/artefacts/NSM/Portal/NI-0001313641/NI-0001313641.pdf",
         "NI-000131364", False),
        ("https://data.fca.org.uk/artefacts/NSM/PRN/91cc0ab0-3199-4dac-8d0f-35a6a5701273.html",
         "91cc0ab0-3199-4dac-8d0f-35a6a5701273", True),
        ("https://asx.api.markitdigital.com/asx-research/1.0/file/2924-03139714-6A1345626",
         "2924-03139714-6A1345626", True),
        ("https://data.fca.org.uk/artefacts/NSM/PRN/x.html", "artefacts", True),
    ])
    def test_segment_rule(self, url, ref, expected):
        from app.services.corpus.filing_evidence import url_has_document_segment

        assert url_has_document_segment(url, ref) is expected

    async def test_a_colliding_id_is_never_ready_for_another(self, session):
        from app.services.corpus.filing_evidence import document_evidence_state

        company = await _persist(
            session, title="Annual Report",
            url="https://data.fca.org.uk/artefacts/NSM/Portal/NI-0001313641/NI-0001313641.pdf")
        await TestReadinessByDocumentRef()._index_all(session)
        state = await document_evidence_state(session, company_id=company,
                                              document_ref="NI-000131364")
        assert not state.is_ready and state.version_id is None

    async def test_an_underscore_is_not_a_wildcard(self, session):
        from app.services.corpus.filing_evidence import document_evidence_state

        company = await _persist(
            session, title="Update",
            url="https://data.fca.org.uk/artefacts/NSM/PRN/ABCDEFxH.html")
        await TestReadinessByDocumentRef()._index_all(session)
        state = await document_evidence_state(session, company_id=company,
                                              document_ref="ABCDEF_H")
        assert not state.is_ready

    async def test_a_document_ref_is_never_reported_as_an_accession(self, session):
        from app.services.corpus.filing_evidence import document_evidence_state

        state = await document_evidence_state(session, company_id=uuid.uuid4(),
                                              document_ref="NI-000131364")
        assert state.accession is None and state.document_ref == "NI-000131364"


class TestSameBytesFromAnotherAddress:
    """HIGH-2 (review): bytes first stored under the issuer's URL keep that URL, so a
    lookup by the regulator's id never found them and re-acquired every run."""

    async def test_readiness_follows_the_ingestion_attempt_to_the_version(self, session):
        from app.models.document_ingestion_attempt import DocumentIngestionAttempt
        from app.models.research_document import ResearchDocumentVersion
        from app.services.corpus.filing_evidence import document_evidence_state

        ref = "NI-000131364"
        company = await _persist(session, title="2025 Annual Report",
                                 url="https://pensana.co.uk/wp-content/ar2025.pdf")
        await TestReadinessByDocumentRef()._index_all(session)
        version = (await session.execute(select(ResearchDocumentVersion))).scalar_one()
        before = await document_evidence_state(session, company_id=company,
                                               document_ref=ref)
        assert not before.is_ready  # nothing records that the NSM copy is these bytes
        session.add(DocumentIngestionAttempt(
            id=uuid.uuid4(), company_id=company,
            canonical_url=f"https://data.fca.org.uk/artefacts/NSM/Portal/{ref}/{ref}.pdf",
            url_hash=uuid.uuid4().hex, source_type="uk_nsm_disclosure",
            source_tier="T1_primary_filing", doc_kind="annual_report",
            discovery_strategy="uk_fca_nsm", attempted_at=datetime.now(timezone.utc),
            status="extracted", mime_type="application/pdf", http_status_class="2xx",
            extraction_method="native_pdf", page_count=1, content_hash=version.content_hash,
            fetch_ms=1, extraction_ms=1, total_ms=2, pinned=True))
        await session.flush()
        after = await document_evidence_state(session, company_id=company, document_ref=ref)
        assert after.is_ready and after.version_id == version.id
        other = await document_evidence_state(session, company_id=uuid.uuid4(),
                                              document_ref=ref)
        assert not other.is_ready


class TestTitleOnlyEverywhere:
    """MEDIUM-3 (review): the policy must hold on every path that computes a period."""

    async def test_the_persisted_source_type_carries_the_policy_to_backfill(self, session):
        from app.models.extracted_document import ExtractedDocument
        from app.models.research_document import ResearchDocumentVersion
        from app.services.corpus.documents import backfill_from_extracted_documents

        session.add(ExtractedDocument(
            id=uuid.uuid4(), content_hash="b" * 64,
            canonical_url="https://data.fca.org.uk/artefacts/NSM/PRN/2028-Q1-forecast.html",
            provider="uk_fca_nsm", source_type="uk_nsm_disclosure",
            source_tier="T1_primary_filing", mime_type="text/html",
            title="Update on Longonjo Financing", extraction_method="html",
            status="extracted", retrieved_at=datetime.now(timezone.utc),
            excerpts_json=[], company_id=uuid.uuid4()))
        await session.flush()
        await backfill_from_extracted_documents(session, cfg=CFG)
        version = (await session.execute(select(ResearchDocumentVersion))).scalar_one()
        assert version.period_key is None  # the URL's "2028-Q1" is not read

    def test_the_title_only_helper_ignores_url_and_body(self):
        from app.services.sources.disclosure_period_policy import document_period_for
        from app.services.sources.primary_document_extractor import extract_html

        extraction = extract_html(FORECAST_BODY, cfg=CFG, capture_blocks=True)
        period = document_period_for(title="Project update",
                                     url="https://x.example/q1-2028.html",
                                     extraction=extraction, title_only=True)
        assert period.basis == "unknown" and period.period.year is None
        body_read = document_period_for(title="Project update", url=None,
                                        extraction=extraction, title_only=False)
        assert body_read.period.year == 2028  # what the policy prevents

    async def test_the_facts_default_period_is_title_only_on_the_live_path(self, monkeypatch):
        """``_artifact_from_fetch`` validates facts against the document period; with
        the policy that period comes from the title, never the forecast in the body."""
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        seen: list = []

        def _validate(extraction, **kw):  # noqa: ANN001, ANN202
            seen.append(kw.get("document_period"))
            return []

        monkeypatch.setattr(live_fetchers, "validate_extracted_facts", _validate)
        fetched = DocumentFetchResult(
            requested_url="https://data.fca.org.uk/artefacts/NSM/PRN/x.html",
            final_url="https://data.fca.org.uk/artefacts/NSM/PRN/x.html", status_code=200,
            content_type="text/html", document_type="html", content=FORECAST_BODY)
        artifact = await live_fetchers._artifact_from_fetch(
            fetched, title="Project update", original_language=None, issuer_context=None,
            cfg=CFG, fetch_ms=1, period_policy="title_only")
        assert artifact.period_policy == "title_only"
        assert seen and seen[0].basis == "unknown" and seen[0].period.year is None
