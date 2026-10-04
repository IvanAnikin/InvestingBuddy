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
            self.session, fetched, pool=kw.pop("pool", self.pool), store=kw.pop("store", self.store),
            backend=self.backend,
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
        hitachi = CandidateEntity(name="Hitachi Energy Ltd", company_id=uuid.uuid4(),
                                  sector_terms=("Transformers",))
        siemens = CandidateEntity(name="Siemens Energy AG", company_id=uuid.uuid4(),
                                  tickers=(("ENR", "XETRA"),))
        prysmian = CandidateEntity(name="Prysmian S.p.A.", company_id=uuid.uuid4(),
                                   sector_terms=("Cables",))
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
        by_relation = {r.relation: r for r in rows}
        brand = by_relation["mentioned"]
        assert brand.scope_key == "segment:jewellery maisons" and brand.method == "brand_alias"
        assert brand.company_id == richemont.company_id
        # F11: the page names Richemont ONLY through Cartier, so even the primary row
        # and every unscoped chunk carry the segment scope — never Group-eligible.
        assert by_relation["primary"].scope_key == "segment:jewellery maisons"
        assert not any((r.scope_key or "").startswith("group") for r in rows)
        assert result.subjects_written == len(rows)
        scopes = set(
            (await env.session.execute(select(ResearchDocumentChunk.scope_key))).scalars()
        )
        assert scopes == {"segment:jewellery maisons"}

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
        themes = (await env.session.execute(
            select(ResearchDocumentSubject.theme_key).where(
                ResearchDocumentSubject.relation == "theme")
        )).scalars().all()
        assert themes == ["theme:aluminium-demand"]  # one row, not one per version
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
    def __init__(self, content: bytes, url: str, headers: dict | None = None) -> None:
        self.headers = dict(headers or {})
        self.content = content
        self.final_url = url
        self.document_type = "html"
        self.blocked = False
        self.ok = True
        self.error = None
        self.status_class = "2xx"
        self.truncated = False


class TestVerifiedLeadIngestion:
    async def _run(self, env: Env, monkeypatch: pytest.MonkeyPatch, *, allowed: bool,
                   body: bytes | None = None, headers: dict | None = None,
                   claim: str | None = None) -> Any:
        from app.models.company import Company
        from app.services.agent_tools.external import _fetch_public_source
        from app.services.agent_tools.session import ToolContext
        from app.services.providers import leads as leads_mod
        from app.services.web_research import fetch as fetch_mod

        page = body if body is not None else _page("news_article.html")
        url = "https://www.gridweekly.example/news/transformer-shortage-deepens"

        async def _fetcher(u: str, **_kw: Any) -> Any:
            return _FetchResult(page, url, headers)

        async def _clearance(*_a: Any, **_kw: Any) -> Any:
            if allowed:
                return fetch_mod.IngestionClearance(True, "allowed", "tdm_not_reserved")
            return fetch_mod.IngestionClearance(False, "allowed", "tdm_reserved", "tdm_reserved")

        monkeypatch.setattr(leads_mod, "_default_fetcher", _fetcher)
        monkeypatch.setattr(fetch_mod, "ingestion_clearance", _clearance)
        from app.services.web_research import pool as pool_mod

        monkeypatch.setattr(pool_mod, "get_extraction_pool", lambda *_a, **_k: env.pool)
        company_id = uuid.uuid4()
        env.session.add(Company(id=company_id, ticker="GRD", exchange="NYSE",
                                name="Grid Holdings"))
        await env.session.flush()
        context = ToolContext(session=env.session, cfg=env.cfg, company_id=company_id)
        payload = await _fetch_public_source(context, {
            "url": url,
            "claim": claim or ("Lead times for large power transformers have stretched "
                               "to between 120 and 210 weeks"),
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
                     theme_keys=("theme:grid",), subject_company_ids=(a,),
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


# --------------------------------------------------------------------------- #
# Review round 1 (W3)
# --------------------------------------------------------------------------- #


def _three(session_ids: bool = True) -> tuple[CandidateEntity, ...]:
    return (
        CandidateEntity(name="Hitachi Energy Ltd", company_id=uuid.uuid4(),
                        sector_terms=("Transformers",)),
        CandidateEntity(name="Siemens Energy AG", company_id=uuid.uuid4(),
                        tickers=(("ENR", "XETRA"),)),
        CandidateEntity(name="Prysmian S.p.A.", company_id=uuid.uuid4(),
                        sector_terms=("Cables",)),
    )


async def _search(env: Env, **filters: Any) -> list[Any]:
    query = CorpusQuery(text="transformer cable order backlog capacity grid",
                        filters=CorpusFilters(**filters), mode=SearchMode.LEXICAL,
                        top_k=50, allow_cross_entity=True)
    return [hit.chunk for hit in await env.backend.search(query)]


class TestReviewSubjectRetrieval:
    async def test_a_mention_admits_only_its_evidence_chunk_never_the_group_slot(
        self, env: Env
    ) -> None:
        # F2: an article mentioning Hitachi by name is NOT Hitachi's document.
        hitachi, siemens, prysmian = _three()
        body = _page("three_company_article.html").replace(
            b"</article>", b"<p>" + b"Grid order backlog and capacity remain tight. " * 40
            + b"</p></article>")
        await env.ingest(_fetched(body, "https://tdworld.com/grid/x"), subject_scope="industry",
                         candidates=(hitachi, siemens, prysmian))
        everything = await _search(env)
        assert len(everything) > 1
        hits = await _search(env, company_ids=(hitachi.company_id,),
                             subject_company_ids=(hitachi.company_id,))
        assert len(hits) == 1 and "Hitachi" in hits[0].text
        assert hits[0].via_subject and hits[0].scope_type == "mention"
        assert hits[0].scope_type != "group" and hits[0].scope_key.startswith("mention:")
        # A Group-only query never sees it.
        assert await _search(env, company_ids=(hitachi.company_id,),
                             subject_company_ids=(hitachi.company_id,),
                             scope_types=("group",)) == []
        # An exact-identifier subject (ticker + venue) attributes the whole document.
        whole = await _search(env, subject_company_ids=(siemens.company_id,))
        assert len(whole) == len(everything) and all(c.via_subject for c in whole)
        # Another company entirely sees nothing.
        assert await _search(env, subject_company_ids=(uuid.uuid4(),)) == []

    async def test_a_document_is_reused_by_a_second_theme(self, env: Env) -> None:
        # F3: themes are rows; the second theme links and FINDS the same document.
        body = _page("association_page.html")
        url = "https://international-aluminium.org/outlook"
        first = await env.ingest(_fetched(body, url), subject_scope="theme",
                                 theme_key="theme:aluminium")
        second = await env.ingest(_fetched(body, url), subject_scope="theme",
                                  theme_key="theme:lightweighting")
        assert second.state == ingest_mod.STATE_REUSED
        assert second.version_id == first.version_id and second.subjects_written == 1
        rows = (await env.session.execute(
            select(ResearchDocumentSubject.theme_key).where(
                ResearchDocumentSubject.relation == "theme"))).scalars().all()
        assert sorted(rows) == ["theme:aluminium", "theme:lightweighting"]
        # The memory index was refreshed on reuse (F4): the second theme finds it.
        query = CorpusQuery(text="aluminium demand", mode=SearchMode.LEXICAL, top_k=10,
                            filters=CorpusFilters(theme_keys=("theme:lightweighting",)))
        assert await env.backend.search(query)

    async def test_subject_rows_are_unique_even_with_null_columns(self, env: Env) -> None:
        # F10: two identical theme rows (company NULL) cannot both exist.
        result = await env.ingest(_fetched(_page("association_page.html"),
                                           "https://international-aluminium.org/o"),
                                  subject_scope="theme", theme_key="theme:a")
        again = await ingest_mod.write_subjects(env.session, document_id=result.document_id,
                                                version_id=result.version_id,
                                                company_id=None, mentions=[],
                                                theme_key="theme:a")
        assert again == 0
        env.session.add(ResearchDocumentSubject(
            id=uuid.uuid4(), research_document_id=result.document_id, relation="theme",
            method="research_run", theme_key="theme:a"))
        with pytest.raises(IntegrityError):
            await env.session.flush()
        await env.session.rollback()

    async def test_same_company_filing_bytes_are_linked_not_duplicated(
        self, env: Env
    ) -> None:
        # F5: the issuer PDF the filing pipeline already holds is linked, not re-stored.
        from app.services.corpus.documents import DocumentVersionInput, upsert_document_version

        company = uuid.uuid4()
        raw = make_pdf(["Annual Report 2025\nGroup revenue rose in the year under review."])
        filing = await upsert_document_version(env.session, DocumentVersionInput(
            content_hash=hashlib.sha256(raw).hexdigest(),
            canonical_url="https://issuer.example/ar2025.pdf", transport="company_ir",
            source_tier="T1_primary_filing", company_id=company,
            document_type="annual_report", title="Annual Report 2025"), cfg=env.cfg)
        result = await env.ingest(_fetched(raw, "https://mirror.example/ar.pdf",
                                           content_class="pdf"), company_id=company)
        assert result.state == ingest_mod.STATE_REUSED and result.version_id == filing.id
        assert await _count(env.session, ResearchDocumentVersion) == 1

    async def test_a_text_found_date_never_reaches_the_period_rules(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F9: htmldate's content date is stored for "since", labelled, and kept away
        # from the shared ExtractedDocument and the title-period rules.
        from app.services.web_research import extract as ex

        monkeypatch.setattr(ex, "_text_date", lambda _tree: date(2025, 3, 12))
        body = (b"<html><body><article><h1>Grid notes</h1><p>"
                + b"Transformer backlogs persist across utilities. " * 20
                + b"</p></article></body></html>")
        result = await env.ingest(_fetched(body, "https://notes.example/n"),
                                  company_id=uuid.uuid4(), pool=_InlinePool())
        assert result.state == ingest_mod.STATE_INGESTED
        version = await env.session.get(ResearchDocumentVersion, result.version_id)
        assert version.published_at == date(2025, 3, 12)
        assert version.published_at_source == "text"
        shared = await env.session.get(ExtractedDocument, result.extracted_document_id)
        assert shared.doc_date is None

    def test_undated_documents_are_excluded_unless_asked_for(self) -> None:
        undated = _chunk("u", company_id=None)
        window = {"published_from": date(2026, 1, 1)}
        assert not CorpusFilters(**window).matches(undated)
        assert CorpusFilters(include_undated=True, **window).matches(undated)


class _InlinePool:
    """Runs a worker function in-process (for a monkeypatched extractor)."""

    async def run(self, fn: Any, *args: Any, timeout: float) -> Any:
        return fn(*args)


class TestReviewLeadPathRefusals:
    """S-M4: the lead path refuses what open_web_fetch refuses."""

    async def test_a_login_wall_is_not_ingested(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wall = (b"<html><body><main><h1>Sign in</h1><p>Lead times for large power "
                b"transformers have stretched to between 120 and 210 weeks</p>"
                b"<form><input type='password' name='pw'></form></main></body></html>")
        payload, _ = await TestVerifiedLeadIngestion()._run(
            env, monkeypatch, allowed=True, body=wall)
        assert payload["items"][0]["verified"]
        assert payload["items"][0]["corpus_version_id"] is None
        assert await _count(env.session, ResearchDocumentVersion) == 0

    @pytest.mark.parametrize("headers", [{"tdm-reservation": "1"},
                                         {"x-robots-tag": "noai"}])
    async def test_a_header_tdm_reservation_is_not_ingested(
        self, env: Env, monkeypatch: pytest.MonkeyPatch, headers: dict
    ) -> None:
        payload, _ = await TestVerifiedLeadIngestion()._run(
            env, monkeypatch, allowed=True, headers=headers)
        assert payload["items"][0]["verified"]
        assert await _count(env.session, ResearchDocumentVersion) == 0
        assert await _count(env.session, WebFetchAttempt) == 0

    async def test_preparation_writes_nothing(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F6: robots/TDMRep rows and the attempt row are only written by the store
        # phase, inside the caller's savepoint.
        from app.services.web_research import fetch as fetch_mod

        async def _clearance(*_a: Any, session: Any, **_kw: Any) -> Any:
            session.add(WebFetchAttempt(id=uuid.uuid4(), origin="robots",
                                        requested_url="https://x/robots.txt",
                                        status="fetched"))
            return fetch_mod.IngestionClearance(True, "allowed", "tdm_not_reserved")

        monkeypatch.setattr(fetch_mod, "ingestion_clearance", _clearance)
        prepared = await ingest_mod.prepare_verified_lead_document(
            content=_page("news_article.html"), url="https://gridweekly.example/n",
            fetched_url=None, truncated=False, cfg=env.cfg, company_id=uuid.uuid4(),
            pool=env.pool)
        assert isinstance(prepared, ingest_mod.PreparedLeadDocument)
        assert await _count(env.session, WebFetchAttempt) == 0
        assert {r.origin for r in prepared.rows} == {"robots", "lead"}


# --------------------------------------------------------------------------- #
# Re-review robustness items (W3)
# --------------------------------------------------------------------------- #


class _CountingStore(InMemoryArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.puts = 0

    async def put(self, data: bytes, *, media_type: str) -> Any:
        self.puts += 1
        return await super().put(data, media_type=media_type)


class TestRobustnessIngest:
    async def test_the_artifact_upload_happens_in_prepare_not_in_the_savepoint(
        self, env: Env
    ) -> None:
        # #3: an azure_blob PUT of up to 35 MB must not run inside the research
        # transaction's savepoint — prepare does it, store only records lineage.
        store = _CountingStore()
        prepared = await ingest_mod.prepare_web_document(
            _fetched(_page("news_article.html"), "https://www.gridweekly.example/n"),
            cfg=env.cfg, company_id=uuid.uuid4(), pool=env.pool, store=store, now=NOW,
        )
        assert isinstance(prepared, ingest_mod.PreparedWebDocument)
        assert store.puts == 1 and prepared.stored_artifact is not None
        result = await ingest_mod.store_web_document(env.session, prepared, cfg=env.cfg,
                                                     store=_ExplodingStore(), now=NOW)
        assert result.state == ingest_mod.STATE_INGESTED
        assert store.puts == 1  # the store phase uploaded nothing
        artifact = (await env.session.execute(select(ResearchArtifact))).scalar_one()
        assert artifact.storage_key == prepared.stored_artifact.storage_key

    async def test_a_failed_upload_costs_the_bytes_not_the_document(self, env: Env) -> None:
        result = await env.ingest(
            _fetched(_page("news_article.html"), "https://www.gridweekly.example/n"),
            company_id=uuid.uuid4(), store=_ExplodingStore(),
        )
        assert result.state == ingest_mod.STATE_INGESTED
        artifact = (await env.session.execute(select(ResearchArtifact))).scalar_one()
        assert artifact.storage_key is None  # lineage row, no bytes

    async def test_a_pdf_creation_date_is_not_an_authoritative_date(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # #4: /CreationDate is often inherited from the prior year's file; it is stored
        # (labelled) for "since" but never reaches the period rules / ExtractedDocument.
        from app.services.web_research import extract as ex

        original = ex._pdf_creation_date
        monkeypatch.setattr(ex, "_pdf_creation_date", lambda _r: date(2024, 12, 20))
        raw = make_pdf(["Interim report\nGroup revenue rose in the half year."])
        result = await env.ingest(_fetched(raw, "https://issuer.example/ir.pdf",
                                           content_class="pdf"),
                                  company_id=uuid.uuid4(), pool=_InlinePool())
        assert original is not ex._pdf_creation_date
        assert result.state == ingest_mod.STATE_INGESTED
        version = await env.session.get(ResearchDocumentVersion, result.version_id)
        assert version.published_at == date(2024, 12, 20)
        assert version.published_at_source == "pdf_metadata"
        shared = await env.session.get(ExtractedDocument, result.extracted_document_id)
        assert shared.doc_date is None


class _ExplodingStore:
    backend_name = "memory"

    async def put(self, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("storage account unreachable")
