"""Open-web W3 — web documents into the Research Corpus (SQLite, offline).

The write path end to end: ``open_web_fetch`` (offline mock network) → extraction in the
real process pool → classification → ``ExtractedDocument`` → corpus version, derivation,
chunks → subjects → retrieval filters. Also ``fetch_public_source`` joining the same path
after a VERIFIED outcome, so an ``ev:x:`` id resolves to a stored version.

SQLite runs with foreign keys OFF; ``test_web_w3_postgres.py`` repeats the write path on
PostgreSQL, where FKs, the partial unique index and ``BIGINT`` are real.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.config import Settings
from app.db.base import Base
from app.models.extracted_document import ExtractedDocument
from app.models.research_artifact import ResearchArtifact
from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_derivation import ResearchDocumentPage
from app.models.research_document import (
    ResearchDocument,
    ResearchDocumentSubject,
    ResearchDocumentVersion,
)
from app.models.research_lead import ResearchLeadRecord
from app.models.web_research import WebFetchAttempt
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.types import (
    CorpusChunk,
    CorpusFilters,
    CorpusQuery,
    SearchMode,
)
from app.services.web_research import ingest as ingest_mod
from app.services.web_research.entities import CandidateEntity
from app.services.web_research.fetch import (
    STATUS_FETCHED,
    STATUS_NOT_RETRIEVABLE,
    OpenWebFetchResult,
    ingestion_clearance,
)
from app.services.web_research.pool import ExtractionPool
from tests.helpers.pdf_fixtures import make_pdf
from tests.test_web_w2_fetch_policy import (  # noqa: F401 - fixtures
    FakeClock,
    FakeDNS,
    Harness,
    Web,
    clock,
    h,
    respond,
    runtime,
    web,
)

FIXTURES = Path(__file__).parent / "fixtures" / "web"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _cfg(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "v3_corpus_enabled": True,
        "v3_web_corpus_ingest_enabled": True,
        "v3_web_fetch_enabled": True,
        "source_connector_allowlist_only": True,
        "primary_document_pin_dns_enabled": True,
        "source_connector_timeout_seconds": 10,
        "source_fetch_total_deadline_seconds": 30.0,
        "source_document_total_deadline_seconds": 60.0,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="module")
def pool() -> Any:
    p = ExtractionPool(1)
    yield p
    p.shutdown()


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


def _fetched(
    body: bytes,
    url: str,
    *,
    content_class: str = "html",
    status: str = STATUS_FETCHED,
    truncated: bool = False,
    tdm_decision: str | None = "tdm_not_reserved",
    js_required: bool = False,
) -> OpenWebFetchResult:
    return OpenWebFetchResult(
        status=status,
        origin="search",
        requested_url=url,
        final_url=url,
        canonical_url=url,
        content=body,
        content_class=content_class,
        charset="utf-8",
        content_hash=hashlib.sha256(body).hexdigest(),
        bytes=len(body),
        truncated=truncated,
        tdm_decision=tdm_decision,
        js_required=js_required,
    )


def _page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes().replace(b"{{TAG_CHARS}}", b"")


async def _count(session: Any, model: Any) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


class Env:
    def __init__(self, session: Any, pool: Any) -> None:
        self.session = session
        self.pool = pool
        self.store = InMemoryArtifactStore()
        self.backend = InMemorySearchBackend()
        self.cfg = _cfg()

    async def ingest(self, fetched: Any, **kw: Any) -> ingest_mod.WebIngestResult:
        kw.setdefault("cfg", self.cfg)
        return await ingest_mod.ingest_web_document(
            self.session, fetched, pool=self.pool, store=self.store, backend=self.backend,
            now=NOW, **kw,
        )


@pytest.fixture
def env(session: Any, pool: Any) -> Env:
    return Env(session, pool)


# --------------------------------------------------------------------------- #
# The flag
# --------------------------------------------------------------------------- #


class TestTheFlag:
    def test_it_defaults_off(self) -> None:
        assert Settings.model_fields["v3_web_corpus_ingest_enabled"].default is False
        assert Settings.model_fields["v3_web_artifact_retention_days"].default == 30
        assert Settings.model_fields["v3_web_extraction_workers"].default == 1

    async def test_off_means_no_extraction_and_no_row(self, env: Env) -> None:
        result = await ingest_mod.ingest_web_document(
            env.session, _fetched(_page("news_article.html"), "https://gridweekly.example/n"),
            cfg=_cfg(v3_web_corpus_ingest_enabled=False), company_id=uuid.uuid4(),
            pool=object(),  # would raise if it were ever used
        )
        assert result.state == ingest_mod.STATE_DISABLED
        assert await _count(env.session, ResearchDocumentVersion) == 0
        assert await _count(env.session, ExtractedDocument) == 0

    async def test_the_corpus_flag_is_also_required(self, env: Env) -> None:
        result = await ingest_mod.ingest_web_document(
            env.session, _fetched(b"<html></html>", "https://x.example/"),
            cfg=_cfg(v3_corpus_enabled=False), company_id=uuid.uuid4(), pool=object(),
        )
        assert result.state == ingest_mod.STATE_DISABLED

    def test_ingest_is_the_flags_only_consumer(self) -> None:
        app_root = Path(__file__).resolve().parents[1] / "app"
        users = sorted(
            str(p.relative_to(app_root))
            for p in app_root.rglob("*.py")
            if "v3_web_corpus_ingest_enabled" in p.read_text()
        )
        assert users == ["core/config.py", "services/web_research/ingest.py"], users


# --------------------------------------------------------------------------- #
# The write path
# --------------------------------------------------------------------------- #


class TestWritePath:
    async def test_news_article_becomes_a_public_web_version_with_every_w3_field(
        self, env: Env
    ) -> None:
        company = uuid.uuid4()
        attempt = WebFetchAttempt(id=uuid.uuid4(), origin="search",
                                  requested_url="https://www.gridweekly.example/n",
                                  status="fetched")
        env.session.add(attempt)
        await env.session.flush()
        fetched = _fetched(_page("news_article.html"), "https://www.gridweekly.example/news/x")
        fetched.attempt_id = attempt.id
        result = await env.ingest(fetched, provider="tavily", company_id=company)
        assert result.state == ingest_mod.STATE_INGESTED, result
        assert result.chunks > 0 and result.indexed > 0

        version = await env.session.get(ResearchDocumentVersion, result.version_id)
        assert version.access_class == "public_web"
        assert version.transport == "open_web:tavily"
        assert version.content_origin == "gridweekly.example"
        assert version.source_tier == "T5_api_aggregator"
        assert version.source_class == "unknown_web"
        assert version.use_constraint == "unknown"
        assert version.injection_suspect is False
        assert version.simhash is not None and version.origin_key == "gridweekly.example"
        assert version.published_at == date(2026, 3, 4)
        assert version.published_at_source == "json_ld"
        assert version.web_fetch_attempt_id == attempt.id
        assert version.web_extractor_version == 1
        assert version.language == "en"
        document = await env.session.get(ResearchDocument, version.research_document_id)
        assert document.company_id == company and document.subject_scope == "company"
        assert document.document_type == "news_article"
        assert document.document_key.startswith("url:")  # a web page is keyed by address

        shared = await env.session.get(ExtractedDocument, result.extracted_document_id)
        assert shared.source_type == "open_web" and shared.company_id == company
        assert shared.excerpts_json  # bounded excerpts for V2-style reuse
        artifact = (await env.session.execute(select(ResearchArtifact))).scalar_one()
        # use_constraint unknown → the 30-day web TTL (decision U4).
        assert artifact.retention_expires_at is not None
        expires = artifact.retention_expires_at.replace(tzinfo=timezone.utc)
        assert expires - NOW == timedelta(days=30)
        assert artifact.storage_key  # bytes retained in the (memory) store

        chunk = (await env.session.execute(select(ResearchDocumentChunk).limit(1))).scalar_one()
        assert chunk.chunk_id.startswith("ev:c:") or chunk.chunk_id
        assert chunk.company_id == company and chunk.access_class == "public_web"

    async def test_same_bytes_are_linked_never_rechunked(self, env: Env) -> None:
        body = _page("news_article.html")
        first = await env.ingest(_fetched(body, "https://gridweekly.example/a"),
                                 company_id=(a := uuid.uuid4()))
        chunks = await _count(env.session, ResearchDocumentChunk)
        second = await env.ingest(_fetched(body, "https://mirror.example/copy"),
                                  company_id=(b := uuid.uuid4()))
        assert second.state == ingest_mod.STATE_REUSED
        assert second.version_id == first.version_id
        assert await _count(env.session, ResearchDocumentChunk) == chunks
        assert await _count(env.session, ExtractedDocument) == 1
        shared = (await env.session.execute(select(ExtractedDocument))).scalar_one()
        assert shared.company_id == a  # the FIRST extractor, never overwritten
        subjects = (await env.session.execute(select(ResearchDocumentSubject))).scalars().all()
        assert {(s.company_id, s.relation) for s in subjects} == {(a, "primary"), (b, "primary")}

    async def test_three_company_article_writes_three_subject_rows(self, env: Env) -> None:
        hitachi = CandidateEntity(name="Hitachi Energy Ltd", company_id=uuid.uuid4())
        siemens = CandidateEntity(name="Siemens Energy AG", company_id=uuid.uuid4(),
                                  tickers=(("ENR", "XETRA"),))
        prysmian = CandidateEntity(name="Prysmian S.p.A.", company_id=uuid.uuid4())
        premier = CandidateEntity(name="Premier plc", company_id=uuid.uuid4())
        result = await env.ingest(
            _fetched(_page("three_company_article.html"), "https://tdworld.com/grid/x"),
            subject_scope="industry",
            candidates=(hitachi, siemens, prysmian, premier),
        )
        assert result.state == ingest_mod.STATE_INGESTED
        rows = (await env.session.execute(select(ResearchDocumentSubject))).scalars().all()
        assert len(rows) == 3
        assert {r.company_id for r in rows} == {
            hitachi.company_id, siemens.company_id, prysmian.company_id
        }
        by_company = {r.company_id: r for r in rows}
        assert by_company[siemens.company_id].confidence == "exact_identifier"
        assert by_company[siemens.company_id].method == "ticker_venue"
        assert all(r.relation == "mentioned" for r in rows)
        assert all(r.evidence_chunk_id for r in rows)
        document = await env.session.get(ResearchDocument, result.document_id)
        assert document.company_id is None and document.subject_scope == "industry"

    async def test_brand_mention_is_parent_segment_scope_never_group(self, env: Env) -> None:
        richemont = CandidateEntity(name="Compagnie Financiere Richemont SA",
                                    company_id=uuid.uuid4())
        result = await env.ingest(
            _fetched(_page("cartier_article.html"), "https://luxury-news.example/cartier"),
            company_id=richemont.company_id, candidates=(richemont,),
        )
        rows = (await env.session.execute(select(ResearchDocumentSubject))).scalars().all()
        scoped = [r for r in rows if r.scope_key]
        assert scoped and scoped[0].scope_key == "segment:jewellery maisons"
        assert scoped[0].company_id == richemont.company_id
        assert scoped[0].method == "brand_alias"
        assert not any((r.scope_key or "").startswith("group") for r in rows)
        assert result.subjects_written == len(rows)

    async def test_companyless_theme_document_dedups_under_the_partial_index(
        self, env: Env
    ) -> None:
        url = "https://international-aluminium.org/outlook"
        v1 = _page("association_page.html")
        v2 = v1.replace(b"latest edition", b"newest edition")
        r1 = await env.ingest(_fetched(v1, url), subject_scope="theme",
                              theme_key="theme:aluminium-demand")
        r2 = await env.ingest(_fetched(v2, url), subject_scope="theme",
                              theme_key="theme:aluminium-demand")
        assert r1.state == r2.state == ingest_mod.STATE_INGESTED
        assert r1.document_id == r2.document_id  # one company-less document, two versions
        assert await _count(env.session, ResearchDocument) == 1
        document = await env.session.get(ResearchDocument, r1.document_id)
        assert document.theme_key == "theme:aluminium-demand"
        assert r1.source_class == "unknown_web"  # international-aluminium.org not curated
        # The database itself refuses a second company-less row with the same key.
        env.session.add(ResearchDocument(
            id=uuid.uuid4(), company_id=None, document_key=document.document_key,
            document_type="web_page", first_seen_at=NOW, last_seen_at=NOW,
        ))
        with pytest.raises(IntegrityError):
            await env.session.flush()
        await env.session.rollback()

    async def test_injection_page_is_stored_inert_and_flagged(self, env: Env) -> None:
        result = await env.ingest(
            _fetched(_page("injection_page.html"), "https://copper-blog.example/u"),
            company_id=uuid.uuid4(),
        )
        assert result.state == ingest_mod.STATE_INGESTED
        assert result.injection_suspect and "ignore_previous" in result.injection_signals
        version = await env.session.get(ResearchDocumentVersion, result.version_id)
        assert version.injection_suspect is True
        texts = " ".join(
            (await env.session.execute(select(ResearchDocumentChunk.text))).scalars()
        )
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in texts  # stored verbatim, as data
        assert "BUY rating" not in texts  # hidden text never becomes a chunk

    async def test_whitepaper_pdf_is_ingested_with_page_lineage(self, env: Env) -> None:
        raw = make_pdf([
            "Mineral Commodity Summaries 2026 Copper\nWorld mine production estimates.",
            "Domestic production and use\nCopper was mined in five states in 2025.",
            "World mine production and reserves\nChile and Peru remain the largest producers.",
        ])
        result = await env.ingest(
            _fetched(raw, "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-copper.pdf",
                     content_class="pdf"),
            company_id=uuid.uuid4(),
        )
        assert result.state == ingest_mod.STATE_INGESTED, result
        assert result.source_class == "specialist_agency"
        assert result.use_constraint == "public_domain"
        assert result.document_kind == "government_report"
        version = await env.session.get(ResearchDocumentVersion, result.version_id)
        assert version.access_class == "public_official"
        assert version.media_type == "application/pdf"
        pages = (await env.session.execute(select(ResearchDocumentPage.page_number))).scalars()
        assert sorted(pages) == [1, 2, 3]
        chunk_pages = {
            c.page_start
            for c in (await env.session.execute(select(ResearchDocumentChunk))).scalars()
        }
        assert chunk_pages <= {1, 2, 3} and chunk_pages
        artifact = (await env.session.execute(select(ResearchArtifact))).scalar_one()
        assert artifact.retention_expires_at is None  # known constraint: global default

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({"status": STATUS_NOT_RETRIEVABLE, "tdm_decision": "tdm_reserved"}, "tdm_reserved"),
            ({"tdm_decision": "tdm_reserved"}, "tdm_reserved"),
            ({"truncated": True}, "truncated_body"),
            ({"content_class": "office"}, "unsupported_type"),
        ],
    )
    async def test_refusals_store_nothing(self, env: Env, kwargs: dict, reason: str) -> None:
        fetched = _fetched(_page("news_article.html"), "https://x.example/a", **kwargs)
        if kwargs.get("status") == STATUS_NOT_RETRIEVABLE:
            fetched.content = None
            fetched.failure_code = "tdm_reserved"
        result = await env.ingest(fetched, company_id=uuid.uuid4())
        assert result.state == ingest_mod.STATE_NOT_INGESTED and result.reason == reason
        assert await _count(env.session, ResearchDocumentVersion) == 0

    async def test_js_shell_is_not_ingested_and_invents_nothing(self, env: Env) -> None:
        result = await env.ingest(
            _fetched(_page("spa_shell.html"), "https://portal.example/", js_required=True),
            company_id=uuid.uuid4(),
        )
        assert result.state == ingest_mod.STATE_NOT_INGESTED and result.reason == "js_required"
        assert await _count(env.session, ExtractedDocument) == 0

    async def test_a_document_needs_a_subject(self, env: Env) -> None:
        result = await env.ingest(_fetched(b"<html></html>", "https://x.example/"))
        assert result.reason == ingest_mod.REASON_NO_SUBJECT


class TestEndToEndFromTheFetchPolicy:
    async def test_open_web_fetch_then_ingest(self, h: Harness, env: Env) -> None:  # noqa: F811
        body = _page("news_article.html")
        h.web.site("example.com", **{"/robots.txt": respond(404, b""),
                                     "/news/x": respond(body=body)})
        fetched = await h.fetch("https://example.com/news/x")
        assert fetched.status == STATUS_FETCHED
        env.session = h.session
        # The W2 harness session only carries the web tables; build the rest.
        await h.session.run_sync(lambda s: Base.metadata.create_all(s.connection()))
        result = await env.ingest(fetched, provider="fake", company_id=uuid.uuid4())
        assert result.state == ingest_mod.STATE_INGESTED
        version = await h.session.get(ResearchDocumentVersion, result.version_id)
        assert version.web_fetch_attempt_id == fetched.attempt_id

    async def test_a_tdm_reserved_page_is_discovered_not_ingested(
        self, h: Harness, env: Env  # noqa: F811
    ) -> None:
        body = (b"<html><head><meta name='tdm-reservation' content='1'></head><body><p>"
                + b"Reserved text. " * 50 + b"</p></body></html>")
        h.web.site("example.com", **{"/robots.txt": respond(404, b""), "/r": respond(body=body)})
        fetched = await h.fetch("https://example.com/r")
        assert fetched.status == STATUS_NOT_RETRIEVABLE and fetched.content is None
        await h.session.run_sync(lambda s: Base.metadata.create_all(s.connection()))
        env.session = h.session
        result = await env.ingest(fetched, company_id=uuid.uuid4())
        assert result.state == ingest_mod.STATE_NOT_INGESTED
        assert result.reason == "tdm_reserved"

    async def test_ingestion_clearance_honours_robots_and_tdmrep(self, h: Harness) -> None:  # noqa: F811
        h.web.site("example.com", **{"/robots.txt": respond(
            200, b"User-agent: *\nDisallow: /private/", content_type="text/plain")})
        h.web.site("news.example.org", **{
            "/robots.txt": respond(404, b""),
            "/.well-known/tdmrep.json": respond(
                200, b'[{"location": "/*", "tdm-reservation": 1}]',
                content_type="application/json"),
        })
        async def clear(url: str, **kw: Any) -> Any:
            kw.setdefault("cfg", h.cfg)
            return await ingestion_clearance(url, session=h.session, resolver=h.dns,
                                             runtime=h.runtime, **kw)

        denied = await clear("https://example.com/private/x")
        assert not denied.allowed and denied.failure_code == "robots_disallowed"
        reserved = await clear("https://news.example.org/a")
        assert not reserved.allowed and reserved.failure_code == "tdm_reserved"
        ok = await clear("https://example.com/public")
        assert ok.allowed
        # The policy-file requests are audited exactly as inside open_web_fetch (W2 M5).
        origins = {row.origin for row in await h.all_rows()}
        assert {"robots", "tdm"} <= origins
        off = await clear("https://example.com/public", cfg=_cfg(v3_web_fetch_enabled=False))
        assert not off.allowed and off.failure_code == "web_fetch_disabled"


# --------------------------------------------------------------------------- #
# fetch_public_source: a verified lead joins the corpus; ev:x: resolves
# --------------------------------------------------------------------------- #


class _FetchResult:
    def __init__(self, content: bytes, url: str) -> None:
        self.content = content
        self.final_url = url
        self.document_type = "html"
        self.blocked = False
        self.ok = True
        self.error = None
        self.status_class = "2xx"
        self.truncated = False


class TestVerifiedLeadIngestion:
    async def _run(self, env: Env, monkeypatch: pytest.MonkeyPatch, *, allowed: bool) -> Any:
        from app.models.company import Company
        from app.services.agent_tools.external import _fetch_public_source
        from app.services.agent_tools.session import ToolContext
        from app.services.providers import leads as leads_mod
        from app.services.web_research import fetch as fetch_mod

        body = _page("news_article.html")
        url = "https://www.gridweekly.example/news/transformer-shortage-deepens"

        async def _fetcher(u: str, **_kw: Any) -> Any:
            return _FetchResult(body, url)

        async def _clearance(*_a: Any, **_kw: Any) -> Any:
            if allowed:
                return fetch_mod.IngestionClearance(True, "allowed", "tdm_not_reserved")
            return fetch_mod.IngestionClearance(False, "allowed", "tdm_reserved", "tdm_reserved")

        monkeypatch.setattr(leads_mod, "_default_fetcher", _fetcher)
        monkeypatch.setattr(fetch_mod, "ingestion_clearance", _clearance)
        monkeypatch.setattr(ingest_mod, "ingest_web_document", _with_env(env))
        company_id = uuid.uuid4()
        env.session.add(Company(id=company_id, ticker="GRD", exchange="NYSE",
                                name="Grid Holdings"))
        await env.session.flush()
        context = ToolContext(session=env.session, cfg=env.cfg, company_id=company_id)
        payload = await _fetch_public_source(context, {
            "url": url,
            "claim": "Lead times for large power transformers have stretched to between "
                     "120 and 210 weeks",
            "provider": "tavily",
        })
        return payload, company_id

    async def test_ev_x_resolves_to_a_stored_version(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload, _company = await self._run(env, monkeypatch, allowed=True)
        record = payload["items"][0]
        assert record["verified"], record
        evidence_id = record["evidence_id"]
        assert evidence_id.startswith("ev:x:")
        lead = (await env.session.execute(select(ResearchLeadRecord))).scalar_one()
        assert lead.research_document_version_id is not None
        version = await ingest_mod.version_for_external_evidence(env.session, evidence_id)
        assert version is not None and version.id == lead.research_document_version_id
        assert version.content_hash == lead.fetched_content_hash
        assert version.transport == "open_web:lead"
        attempt = await env.session.get(WebFetchAttempt, version.web_fetch_attempt_id)
        assert attempt.origin == "lead" and attempt.content_hash == version.content_hash

    async def test_a_tdm_reserved_origin_keeps_the_verification_but_stores_nothing(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload, _company = await self._run(env, monkeypatch, allowed=False)
        assert payload["items"][0]["verified"]
        lead = (await env.session.execute(select(ResearchLeadRecord))).scalar_one()
        assert lead.research_document_version_id is None
        assert await _count(env.session, ResearchDocumentVersion) == 0

    async def test_flag_off_never_touches_the_corpus(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env.cfg = _cfg(v3_web_corpus_ingest_enabled=False)
        payload, _company = await self._run(env, monkeypatch, allowed=True)
        assert payload["items"][0]["verified"]
        assert await _count(env.session, ResearchDocumentVersion) == 0
        assert await _count(env.session, WebFetchAttempt) == 0


_REAL_INGEST = ingest_mod.ingest_web_document


def _with_env(env: Env) -> Any:
    """The real write path, with this test's pool, artifact store and search backend."""

    async def _ingest(session: Any, fetched: Any, **kw: Any) -> Any:
        kw.update(pool=env.pool, store=env.store, backend=env.backend)
        return await _REAL_INGEST(session, fetched, **kw)

    return _ingest


# --------------------------------------------------------------------------- #
# Retrieval filters (spec §12.3)
# --------------------------------------------------------------------------- #


def _chunk(cid: str, **kw: Any) -> CorpusChunk:
    return CorpusChunk(chunk_id=cid, text="transformer lead times copper", **kw)


class TestFilters:
    def test_matches_every_web_dimension(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        web_chunk = _chunk("w", company_id=None, source_class="trade_publication",
                     use_constraint="unknown", injection_suspect=True, subject_scope="theme",
                     theme_key="theme:grid", subject_company_ids=(a,),
                     published_at=date(2026, 5, 1))
        filing = _chunk("f", company_id=a)
        assert CorpusFilters(source_classes=("trade_publication",)).matches(web_chunk)
        assert not CorpusFilters(source_classes=("trade_publication",)).matches(filing)
        assert not CorpusFilters(use_constraints=("public_domain",)).matches(web_chunk)
        assert not CorpusFilters(exclude_injection_suspect=True).matches(web_chunk)
        assert CorpusFilters(exclude_injection_suspect=True).matches(filing)
        assert CorpusFilters(theme_keys=("theme:grid",)).matches(web_chunk)
        assert CorpusFilters(subject_scopes=("theme",)).matches(web_chunk)
        assert not CorpusFilters(published_from=date(2026, 6, 1)).matches(web_chunk)  # "since"
        # Company-scoped: own documents, plus documents naming the company as subject.
        assert not CorpusFilters(company_ids=(a,)).matches(web_chunk)
        assert CorpusFilters(company_ids=(a,), subject_company_ids=(a,)).matches(web_chunk)
        assert CorpusFilters(company_ids=(a,), subject_company_ids=(a,)).matches(filing)
        assert not CorpusFilters(company_ids=(b,), subject_company_ids=(b,)).matches(web_chunk)

    def test_a_theme_key_scopes_a_query(self) -> None:
        CorpusQuery(text="grid", filters=CorpusFilters(theme_keys=("theme:grid",))).validate()
        assert CorpusFilters(subject_company_ids=(uuid.uuid4(),)).is_entity_scoped

    async def test_memory_backend_applies_the_filters_to_ingested_documents(
        self, env: Env
    ) -> None:
        company = uuid.uuid4()
        await env.ingest(_fetched(_page("news_article.html"), "https://www.reuters.com/x"),
                         company_id=company)
        await env.ingest(_fetched(_page("injection_page.html"), "https://blog.example/x"),
                         company_id=company)
        await env.ingest(_fetched(_page("government_page.html"), "https://www.usgs.gov/c"),
                         subject_scope="theme", theme_key="theme:copper")

        async def hits(**filters: Any) -> set[str]:
            query = CorpusQuery(text="copper transformer lead times demand",
                                filters=CorpusFilters(**filters), mode=SearchMode.LEXICAL,
                                top_k=50, allow_cross_entity=True)
            return {hit.chunk.source_class for hit in await env.backend.search(query)}

        assert await hits(source_classes=("major_financial_press",)) == {
            "major_financial_press"
        }
        assert "unknown_web" not in await hits(exclude_injection_suspect=True)
        assert await hits(use_constraints=("public_domain",)) == {"specialist_agency"}
        assert await hits(theme_keys=("theme:copper",)) == {"specialist_agency"}
        assert await hits(published_from=date(2026, 3, 1)) == {"major_financial_press"}
