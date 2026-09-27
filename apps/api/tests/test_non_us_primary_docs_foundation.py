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
        assert not r.ok and r.content is None and r.truncated


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
