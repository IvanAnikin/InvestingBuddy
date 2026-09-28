"""UK (FCA NSM) and ASX primary documents — discovery, identity, acquisition, reuse.

Everything runs against a FAKE of the official sources, shaped exactly like the live
responses measured on 2026-09-27 (NSM search JSON, GLEIF, the LSE instrument record,
the ASX list, the ASX yearly announcements page and display page). The fake enforces the
host allowlist it is called with, so a URL the code should never fetch cannot quietly
succeed in a test.

The mutation tests the brief demands are named ``test_mutation_*``.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.services.sources.disclosures import acquisition as acq
from app.services.sources.document_fetcher import DocumentFetchResult

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
PENSANA_LEI = "213800H4QP6T9499RU64"
PENSANA_ISIN = "GB00BKM0ZJ18"

CFG = Settings(
    v3_corpus_enabled=True, primary_document_ingestion_enabled=True,
    report_citation_persistence_enabled=True, v3_artifact_store_backend="memory",
    v3_uk_nsm_disclosures_enabled=True, v3_asx_announcements_enabled=True,
    v3_search_backend="memory", v3_disclosure_core_max_documents=5,
)


# ── The fake web ───────────────────────────────────────────────────────────── #


def _html(body: str) -> bytes:
    return f"<html><body>{body}</body></html>".encode()


RNS_FINANCING = _html(
    "<h1>Update on Longonjo Financing</h1><p>"
    + "Pensana Plc announces that construction at the Longonjo rare earth mine is over "
      "30% complete and offtake negotiations are advanced. " * 5
    + "First production is expected in Q1 2028, subject to financing.</p>")
RNS_INTERIM = _html(
    "<h1>Unaudited Interim results</h1><p>"
    + "Pensana Plc reports interim results for the six months ended 31 December 2025. "
      "The Longonjo project will produce a mixed rare earth carbonate. " * 5
    + "</p><h2>Segment information: Longonjo mine</h2><table><tr><th>US$ million</th>"
      "<th>2025</th><th>2024</th></tr><tr><td>Revenue</td><td>80.0</td><td>60.0</td></tr>"
      "</table>")
ANNUAL_PDF_AS_HTML = _html(
    "<h1>2025 Annual Report</h1><p>" + "Pensana Plc annual report. " * 30 + "</p>")
OTHER_ISSUER_DOC = _html("<h1>Notice</h1><p>" + "Viva Energy Group Limited notice. " * 20 + "</p>")
ASX_ANNUAL = _html("<h1>Annual Report 2026</h1><p>"
                   + "EcoGraf Limited is developing the Epanko graphite project. " * 20 + "</p>")
ASX_UPDATE = _html("<h1>Epanko Value Engineering</h1><p>"
                   + "EcoGraf Limited (ASX: EGR) identifies a 20% production increase. " * 10
                   + "</p>")


def _nsm_hit(ref, *, headline, nsm_type="Statement re", fmt="Plain text", link=None,
             lei=PENSANA_LEI, date="2026-09-25T06:00:00Z", latest="Y"):
    return {"_source": {
        "disclosure_id": ref, "seq_id": ref, "lei": lei, "company": "PENSANA PLC",
        "headline": headline, "type": nsm_type, "document_format": fmt,
        "download_link": link or f"NSM/PRN/{ref}.html", "html_link": None,
        "publication_date": date, "latest_flag": latest}}


NSM_RESPONSE = {"hits": {"hits": [
    _nsm_hit("91cc0ab0-3199-4dac-8d0f-35a6a5701273",
             headline="Pensana Plc - Update on Longonjo Financing"),
    _nsm_hit("e80e132e-9a3d-463c-90b9-92fd0674b07b", nsm_type="Half-year Financial Report",
             headline="Pensana Plc - Unaudited Interim results for the six months ended "
                      "31 December 2025", date="2026-03-27T06:00:00Z"),
    _nsm_hit("NI-000131364-0", nsm_type="Annual Financial Report", fmt="PDF",
             headline="2025 Annual Report", link="NSM/Portal/NI-000131364/NI-000131364.pdf",
             date="2025-10-15T09:18:00Z"),
    _nsm_hit("c22cd12a-7cbe-4870-9641-88638c783aed", nsm_type="Director/PDMR Shareholding",
             headline="Pensana Plc - Directors Dealings"),
    # Another issuer's record, as a keyword search returned live: must be refused.
    _nsm_hit("aaaaaaaa-1111-2222-3333-444444444444", headline="Alkemy - Annual Report",
             lei="213800NW5GVIRMXSRL48"),
    # A superseded amendment: never current.
    _nsm_hit("bbbbbbbb-1111-2222-3333-444444444444", headline="Pensana Plc - old version",
             latest="N"),
]}}

ASX_LIST_CSV = ("ASX code,Company name,GICs industry group,Listing date,Market Cap\n"
                "EGR,ECOGRAF LIMITED,Materials,,124618828\n")


def _asx_row(ids_id, headline, *, sensitive, date="24/09/2026", time="4:27 pm"):
    ps = ('<img class="pricesens" alt="asterix" title="price sensitive">' if sensitive else "")
    return (f"<tr><td>{date}<br><span class=\"dates-time\">{time}</span></td>"
            f"<td class=\"pricesens\">{ps}</td><td><a href=\"/asx/v2/statistics/"
            f"displayAnnouncement.do?display=pdf&amp;idsId={ids_id}\">{headline}<br>"
            f"<span class=\"page\">5 pages</span></a></td></tr>")


ASX_YEAR_PAGE = ("<table><tr><th>Date</th><th>Price sens.</th><th>Headline</th></tr>"
                 + _asx_row("03143773", "Annual Report to shareholders", sensitive=False,
                            time="8:32 am")
                 + _asx_row("03143470", "Epanko Value Engineering Identifies 20% Production "
                            "Increase", sensitive=True)
                 + _asx_row("03143785", "Trading Halt", sensitive=False)
                 + "</table>")


def _display(pdf):
    return f'<html><form><input name="pdfURL" value="{pdf}" type="hidden"></form></html>'


class FakeWeb:
    """GET/POST by exact URL, the host allowlist ENFORCED, every call recorded."""

    def __init__(self, extra: dict | None = None):
        pdf_annual = "https://announcements.asx.com.au/asxpdf/20260925/pdf/074hv8s0yw9t50.pdf"
        pdf_update = "https://announcements.asx.com.au/asxpdf/20260924/pdf/0743aaaaaaaaaa.pdf"
        self.get_map: dict[str, tuple[str, bytes]] = {
            "https://api.londonstockexchange.com/api/gw/lse/instruments/alldata/PRE":
                ("application/json", json.dumps({
                    "tidm": "PRE", "issuername": "PENSANA PLC", "isin": PENSANA_ISIN,
                    "country": "GB", "marketcapitalization": 212816166}).encode()),
            f"https://api.gleif.org/api/v1/lei-records?filter%5Bisin%5D={PENSANA_ISIN}":
                ("application/vnd.api+json", json.dumps({"data": [{
                    "id": PENSANA_LEI, "attributes": {"entity": {
                        "legalName": {"name": "PENSANA PLC"}}}}]}).encode()),
            "https://data.fca.org.uk/artefacts/NSM/PRN/91cc0ab0-3199-4dac-8d0f-35a6a5701273.html":
                ("text/html", RNS_FINANCING),
            "https://data.fca.org.uk/artefacts/NSM/PRN/e80e132e-9a3d-463c-90b9-92fd0674b07b.html":
                ("text/html", RNS_INTERIM),
            "https://data.fca.org.uk/artefacts/NSM/Portal/NI-000131364/NI-000131364.pdf":
                ("text/html", ANNUAL_PDF_AS_HTML),
            "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file":
                ("text/csv", ASX_LIST_CSV.encode()),
            "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=EGR"
            "&timeframe=Y&year=2026": ("text/html", ASX_YEAR_PAGE.encode()),
            "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=EGR"
            "&timeframe=Y&year=2025": ("text/html", b"<table></table>"),
            "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf"
            "&idsId=03143773": ("text/html", _display(pdf_annual).encode()),
            "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf"
            "&idsId=03143470": ("text/html", _display(pdf_update).encode()),
            pdf_annual: ("text/html", ASX_ANNUAL),
            pdf_update: ("text/html", ASX_UPDATE),
        }
        self.get_map.update(extra or {})
        self.post_map = {"https://api.data.fca.org.uk/search?index=nsm-search":
                         json.dumps(NSM_RESPONSE).encode()}
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _allowed(url, allowed):
        host = (urlsplit(url).hostname or "").lower()
        return urlsplit(url).scheme == "https" and any(
            host == d or host.endswith("." + d) for d in allowed)

    async def get(self, url, *, allowed_domains, **_kw):  # noqa: ANN001, ANN202
        self.calls.append(("GET", url))
        if not self._allowed(url, allowed_domains):
            return DocumentFetchResult(requested_url=url, blocked=True, error="blocked host",
                                       failure_code="blocked_host")
        if url not in self.get_map:
            return DocumentFetchResult(requested_url=url, error="http 404", status_code=404,
                                       failure_code="http_client_error")
        ctype, body = self.get_map[url]
        doc_type = "pdf" if ctype == "application/pdf" else (
            "html" if "html" in ctype else "text")
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type=ctype, document_type=doc_type, content=body)

    async def post(self, url, payload, *, allowed_domains, **_kw):  # noqa: ANN001, ANN202
        self.calls.append(("POST", url))
        assert self._allowed(url, allowed_domains)
        assert payload["criteriaObj"]["criteria"][0]["value"][1] == PENSANA_LEI
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="text",
                                   content=self.post_map[url])

    def extractor(self):
        """``live_primary_document_extractor`` with the network replaced by this fake."""
        from app.services.sources import live_fetchers

        async def _extract(url, *, allowed_domains, title_hint=None, issuer_context=None,  # noqa: ANN001, ANN202
                           cfg=None, period_policy=None, published_at=None, **_kw):
            fetched = await self.get(url, allowed_domains=allowed_domains)
            return await live_fetchers._artifact_from_fetch(
                fetched, title=title_hint, original_language=None,
                issuer_context=issuer_context, cfg=cfg, fetch_ms=1,
                period_policy=period_policy, published_at=published_at)

        return _extract

    def content_fetches(self):
        return [u for m, u in self.calls
                if "artefacts" in u or "announcements.asx.com.au" in u]


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


@pytest.fixture(autouse=True)
def _index_like_postgres(monkeypatch, request):  # noqa: ANN001, ANN202
    """On SQLite, index the acquired version the way the PostgreSQL backend does — by
    stamping its chunks ``indexed_at``. (Only that backend stamps; the in-memory one
    never makes anything READY.) The PostgreSQL class below uses the real backend."""
    if request.node.get_closest_marker("real_postgres"):
        return

    async def _stamp(session, *, company_id, ref, cfg):  # noqa: ANN001, ANN202
        from sqlalchemy import update

        from app.models.research_chunk import ResearchDocumentChunk
        from app.services.corpus.filing_evidence import current_version_for_document_ref

        version = await current_version_for_document_ref(
            session, company_id=company_id, document_ref=ref)
        if version is None:
            return 0
        result = await session.execute(
            update(ResearchDocumentChunk)
            .where(ResearchDocumentChunk.research_document_version_id == version.id,
                   ResearchDocumentChunk.indexable.is_(True))
            .values(indexed_at=datetime.now(timezone.utc)))
        await session.flush()
        return int(result.rowcount or 0)

    monkeypatch.setattr(acq, "_index", _stamp)


@pytest.fixture(autouse=True)
def _fresh_directory_cache():  # noqa: ANN202
    from app.services.discovery import directories

    directories.reset_cache()
    yield
    directories.reset_cache()


async def _company(session, ticker, exchange, name):  # noqa: ANN001, ANN202
    company = Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name,
                      status="new")
    session.add(company)
    await session.flush()
    return company


async def _core(session, company, web):  # noqa: ANN001, ANN202
    return await acq.ensure_core_disclosures(
        session, company=company, cfg=CFG, fetcher=web.get, poster=web.post,
        extractor=web.extractor(), now=NOW)


# ── Discovery and identity ─────────────────────────────────────────────────── #


class TestUkDiscovery:
    async def test_the_issuer_is_matched_by_lei_and_other_issuers_are_refused(self, session):
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             poster=web.post, now=NOW)
        assert listing.issuer is not None and listing.issuer.lei == PENSANA_LEI
        assert "GLEIF" in listing.issuer.identity_basis
        refs = {d.document_ref for d in listing.documents}
        assert "aaaaaaaa-1111-2222-3333-444444444444" not in refs  # another LEI
        assert "bbbbbbbb-1111-2222-3333-444444444444" not in refs  # superseded amendment
        assert listing.refused == 2
        annual = next(d for d in listing.documents if d.doc_kind == "annual_report")
        assert annual.document_ref == "NI-000131364"  # amendment suffix is the version
        assert annual.content_url.endswith("NSM/Portal/NI-000131364/NI-000131364.pdf")

    async def test_a_gleif_name_that_disagrees_fails_closed(self, session):
        web = FakeWeb({f"https://api.gleif.org/api/v1/lei-records?filter%5Bisin%5D={PENSANA_ISIN}":
                       ("application/json", json.dumps({"data": [{
                           "id": PENSANA_LEI, "attributes": {"entity": {
                               "legalName": {"name": "SOMETHING ELSE LTD"}}}}]}).encode())})
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             poster=web.post, now=NOW)
        assert listing.issuer is None and listing.reason == "identity_unverified"
        assert not [c for c in web.calls if c[0] == "POST"]  # nothing was even listed

    @pytest.mark.parametrize("link", [
        "NSM/PRN/../../../etc/passwd.html", "https://evil.example/x.pdf",
        "NSM/PRN/some-other-record.html", "NSM/Portal/NI-000131364/NI-000131364.zip",
    ])
    def test_an_unsafe_or_foreign_link_is_never_a_content_url(self, link):
        from app.services.sources.disclosures.uk_nsm import content_link

        source = {"document_format": "PDF" if link.endswith(".pdf") else "Plain text",
                  "download_link": link}
        assert content_link(source, "91cc0ab0-3199-4dac-8d0f-35a6a5701273") is None


class TestAsxDiscovery:
    async def test_announcements_come_from_the_verified_code_with_the_exchange_marker(
        self, session
    ):
        web = FakeWeb()
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             now=NOW)
        assert listing.issuer is not None and "ASX official list" in listing.issuer.identity_basis
        kinds = {d.document_ref: (d.doc_kind, d.research_rank, d.price_sensitive)
                 for d in listing.documents}
        assert kinds["03143773"] == ("annual_report", 0, False)
        assert kinds["03143470"] == ("other", 3, True)
        assert kinds["03143785"][1] == 5  # trading halt: administrative

    @pytest.mark.parametrize("pdf", [
        "https://evil.example/asxpdf/20260925/pdf/x.pdf",
        "http://announcements.asx.com.au/asxpdf/20260925/pdf/074hv8s0yw9t50.pdf",
        "https://announcements.asx.com.au/../../x.pdf",
        "https://announcements.asx.com.au.evil.example/asxpdf/20260925/pdf/a.pdf",
    ])
    def test_a_display_page_can_never_point_the_fetch_elsewhere(self, pdf):
        from app.services.sources.disclosures.asx import pdf_url_from_display_page

        assert pdf_url_from_display_page(_display(pdf)) is None

    async def test_a_company_not_on_the_asx_list_is_identity_unverified(self, session):
        web = FakeWeb()
        company = await _company(session, "ZZZ", "AU", "Nonexistent Minerals Limited")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             now=NOW)
        assert listing.issuer is None and listing.reason == "identity_unverified"


# ── Selection ──────────────────────────────────────────────────────────────── #


async def test_selection_is_bounded_periodic_first_and_never_administrative(session):
    web = FakeWeb()
    company = await _company(session, "PRE", "LSE", "Pensana Plc")
    listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                         poster=web.post, now=NOW)
    chosen = acq.select_core_documents(listing, max_documents=5, now=NOW)
    assert [d.doc_kind for d in chosen][:2] == ["annual_report", "interim_report"]
    assert all(d.research_rank < 5 for d in chosen)
    assert len(acq.select_core_documents(listing, max_documents=1, now=NOW)) == 1


# ── Acquisition: the full chain ────────────────────────────────────────────── #


class TestAcquisitionChain:
    async def test_uk_documents_become_searchable_corpus_content(self, session):
        from app.models.research_chunk import ResearchDocumentChunk
        from app.models.research_document import ResearchDocumentVersion

        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        out = await _core(session, company, web)
        assert out["source_id"] == "uk_fca_nsm" and out["issuer"]["lei"] == PENSANA_LEI
        states = {d["document_ref"]: d["state"] for d in out["documents"]}
        why = [(d["document_ref"], d["reason"], d["notes"]) for d in out["documents"]]
        assert states and all(s == "ready" for s in states.values()), why
        versions = (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        assert {v.transport for v in versions} == {"uk_fca_nsm"}
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        text = " ".join(c.text for c in chunks)
        assert "30% complete" in text and "mixed rare earth carbonate" in text

    async def test_asx_metadata_leads_to_the_attached_pdf_itself(self, session):
        """MUTATION — attachment skipped: storing ASX metadata without fetching the
        attached document must fail this test."""
        from app.models.research_chunk import ResearchDocumentChunk

        web = FakeWeb()
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        out = await _core(session, company, web)
        assert {d["document_ref"] for d in out["documents"]} == {"03143773", "03143470"}
        assert all(d["state"] == "ready" for d in out["documents"])
        fetched = web.content_fetches()
        assert any("asxpdf/20260925/pdf/074hv8s0yw9t50.pdf" in u for u in fetched)
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert "Epanko graphite project" in " ".join(c.text for c in chunks)

    async def test_mutation_metadata_is_never_content(self, session):
        """A headline describes a document; it is not what the document says. The
        headline's own words must not appear in the corpus unless the document has them."""
        from app.models.research_chunk import ResearchDocumentChunk

        web = FakeWeb()
        NSM_RESPONSE["hits"]["hits"][0]["_source"]["headline"] = (
            "Pensana Plc - Record quarterly production of 9,999 tonnes")
        try:
            company = await _company(session, "PRE", "LSE", "Pensana Plc")
            await _core(session, company, web)
        finally:
            NSM_RESPONSE["hits"]["hits"][0]["_source"]["headline"] = (
                "Pensana Plc - Update on Longonjo Financing")
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert "9,999" not in " ".join(c.text for c in chunks)

    async def test_mutation_wrong_issuer_content_is_refused_and_not_stored(self, session):
        """The Viva Energy case: a document that does not name the issuer is not its
        evidence, whatever the listing said."""
        from app.models.research_document import ResearchDocumentVersion

        pdf = "https://announcements.asx.com.au/asxpdf/20260925/pdf/074hv8s0yw9t50.pdf"
        web = FakeWeb({pdf: ("text/html", OTHER_ISSUER_DOC)})
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        out = await _core(session, company, web)
        annual = next(d for d in out["documents"] if d["document_ref"] == "03143773")
        assert annual["state"] == "unavailable" and annual["reason"] == "identity_unverified"
        titles = [v.title for v in
                  (await session.execute(select(ResearchDocumentVersion))).scalars().all()]
        assert not any("Annual Report" in (t or "") for t in titles)

    async def test_out_of_time_before_the_name_is_extraction_failed_not_identity(
        self, session, monkeypatch
    ):
        """Production (EcoGraf 03070575): a loaded host read the cover page only. That is
        a partial extraction, not another issuer's document — and it is still not stored."""
        from app.models.research_document import ResearchDocumentVersion
        from app.services.sources.primary_document_extractor import PrimaryDocumentExtraction

        pdf = "https://announcements.asx.com.au/asxpdf/20260925/pdf/074hv8s0yw9t50.pdf"
        web = FakeWeb({pdf: ("text/html", OTHER_ISSUER_DOC)})
        real = web.extractor()

        async def out_of_time(url, **kw):  # noqa: ANN001, ANN003, ANN202
            artifact = await real(url, **kw)
            if url == pdf:
                assert isinstance(artifact.extraction, PrimaryDocumentExtraction)
                artifact.extraction.warnings.append(
                    "Extraction time budget exceeded; partial extraction only.")
            return artifact

        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        out = await acq.ensure_core_disclosures(
            session, company=company, cfg=CFG, fetcher=web.get, poster=web.post,
            extractor=out_of_time, now=NOW)
        annual = next(d for d in out["documents"] if d["document_ref"] == "03143773")
        assert annual["state"] == "unavailable" and annual["reason"] == "extraction_failed"
        titles = [v.title for v in
                  (await session.execute(select(ResearchDocumentVersion))).scalars().all()]
        assert not any("Annual Report" in (t or "") for t in titles)

    def test_announcements_get_their_own_extraction_budget(self):
        cfg = acq._extraction_cfg(CFG)
        assert cfg.primary_document_extraction_timeout_seconds == (
            CFG.v3_disclosure_extraction_timeout_seconds) > (
            CFG.primary_document_extraction_timeout_seconds)
        low = CFG.model_copy(update={"v3_disclosure_extraction_timeout_seconds": 10})
        assert acq._extraction_cfg(low) is low  # never LOWERS the generic budget

    async def test_mutation_forecast_period_never_stamps_the_announcement(self, session):
        from app.models.research_document import ResearchDocumentVersion

        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        versions = (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        financing = next(v for v in versions if "Financing" in (v.title or ""))
        assert financing.period_key is None  # NOT 2028-Q1
        interim = next(v for v in versions if "six months ended" in (v.title or ""))
        assert interim.period_type == "half"

    async def test_mutation_scope_is_never_rewritten_to_group(self, session):
        """The connector passes the extractor's scope through unchanged: a figure under a
        segment heading is never stamped Group by this path."""
        from app.models.extracted_document import ExtractedFact
        from app.models.research_chunk import ResearchDocumentChunk

        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        facts = (await session.execute(select(ExtractedFact))).scalars().all()
        # Whatever this path stores from under the segment heading, it is never Group.
        assert all(f.scope_type != "group" for f in facts if f.value_numeric in (80, 60))
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert any("Segment information" in (c.text or "") or "Segment information" in
                   str(getattr(c, "heading_path", "") or "") for c in chunks)

    async def test_mutation_a_ready_document_is_never_fetched_again(self, session):
        """Reuse: the second run answers from the database — no listing-to-content
        fetch, the same versions, nothing duplicated."""
        from app.models.research_document import ResearchDocumentVersion

        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        first = web.content_fetches()
        versions_before = {v.id for v in
                           (await session.execute(select(ResearchDocumentVersion))).scalars()}
        out = await _core(session, company, web)
        assert web.content_fetches() == first  # not one more document fetch
        assert all(d["reused"] and d["state"] == "ready" for d in out["documents"])
        versions_after = {v.id for v in
                          (await session.execute(select(ResearchDocumentVersion))).scalars()}
        assert versions_after == versions_before

    async def test_changed_bytes_become_a_new_current_version(self, session):
        from app.models.research_document import ResearchDocumentVersion
        from app.services.corpus.filing_evidence import current_version_for_document_ref

        ref = "91cc0ab0-3199-4dac-8d0f-35a6a5701273"
        url = f"https://data.fca.org.uk/artefacts/NSM/PRN/{ref}.html"
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        old = await current_version_for_document_ref(session, company_id=company.id,
                                                     document_ref=ref)
        # A correction re-filed at the same address.
        web.get_map[url] = ("text/html", RNS_FINANCING.replace(b"30%", b"35%"))
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             poster=web.post, now=NOW)
        document = next(d for d in listing.documents if d.document_ref == ref)
        # Force re-acquisition of the same document (the READY check would reuse it).
        from app.models.research_document import ResearchDocumentVersion as V
        for v in (await session.execute(select(V))).scalars():
            if v.id == old.id:
                v.is_current = False
        await session.flush()
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=CFG, fetcher=web.get,
            extractor=web.extractor())
        assert result.is_ready
        new = await current_version_for_document_ref(session, company_id=company.id,
                                                     document_ref=ref)
        assert new is not None and new.id != old.id and new.content_hash != old.content_hash
        both = (await session.execute(select(ResearchDocumentVersion).where(
            ResearchDocumentVersion.id.in_([old.id, new.id])))).scalars().all()
        assert len(both) == 2  # the old reading stays auditable

    async def test_a_disabled_connector_contacts_nothing(self, session):
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        cfg = Settings(**{**CFG.model_dump(), "v3_uk_nsm_disclosures_enabled": False})
        out = await acq.ensure_core_disclosures(session, company=company, cfg=cfg,
                                                fetcher=web.get, poster=web.post,
                                                extractor=web.extractor(), now=NOW)
        assert out["skipped"] == "connector_disabled" and web.calls == []

    async def test_a_us_issuer_is_not_covered(self, session):
        web = FakeWeb()
        company = await _company(session, "MP", "US", "MP Materials Corp")
        out = await _core(session, company, web)
        assert out["skipped"] == "venue_not_covered" and web.calls == []


# ── Real PostgreSQL: the actual search backend, and question retrieval ────── #

import os  # noqa: E402

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")


@pytest.fixture
async def pg_session():  # noqa: ANN201
    if not POSTGRES_URL:
        pytest.skip("set V3_TEST_POSTGRES_URL to a PostgreSQL at head")
    engine = create_async_engine(POSTGRES_URL, future=True)
    async with engine.connect() as conn:
        outer = await conn.begin()
        async with async_sessionmaker(bind=conn, expire_on_commit=False,
                                      join_transaction_mode="create_savepoint")() as s:
            yield s
        await outer.rollback()
    await engine.dispose()


@pytest.mark.real_postgres
class TestOnPostgres:
    async def test_acquired_documents_answer_a_research_question(self, pg_session):
        """Producer → consumer: the official documents a run acquires are what corpus
        search returns for the issuer's question — and only for this issuer."""
        from app.services.corpus.search.factory import get_search_backend
        from app.services.corpus.search.types import CorpusFilters, CorpusQuery

        cfg = Settings(**{**CFG.model_dump(), "v3_search_backend": "postgres"})
        web = FakeWeb()
        company = await _company(pg_session, f"P{uuid.uuid4().hex[:4]}".upper(), "LSE",
                                 "Pensana Plc")
        company.ticker = "PRE"  # the listing the fake knows; the id stays unique
        await pg_session.flush()
        out = await acq.ensure_core_disclosures(
            pg_session, company=company, cfg=cfg, fetcher=web.get, poster=web.post,
            extractor=web.extractor(), now=NOW)
        why = [(d["document_ref"], d["reason"], d["notes"]) for d in out["documents"]]
        assert out["documents"] and all(d["state"] == "ready" for d in out["documents"]), why

        backend = get_search_backend(cfg, session=pg_session)
        hits = await backend.search(CorpusQuery(
            text="Longonjo rare earth construction offtake",
            filters=CorpusFilters(company_ids=(company.id,))))
        assert hits and any("30% complete" in h.chunk.text for h in hits)
        other = await backend.search(CorpusQuery(
            text="Longonjo rare earth construction offtake",
            filters=CorpusFilters(company_ids=(uuid.uuid4(),))))
        assert other == []

        # Reuse on the real backend too: nothing is fetched a second time.
        first = web.content_fetches()
        again = await acq.ensure_core_disclosures(
            pg_session, company=company, cfg=cfg, fetcher=web.get, poster=web.post,
            extractor=web.extractor(), now=NOW)
        assert web.content_fetches() == first and all(d["reused"] for d in again["documents"])


# ── The in-research tool: get_recent_filings for LSE / ASX ─────────────────── #


class _Ctx:
    def __init__(self, session, company_id, cfg):  # noqa: ANN001
        self.session = session
        self.company_id = company_id
        self.cfg = cfg


def _tool_cfg():  # noqa: ANN202
    return Settings(**{**CFG.model_dump(), "v3_filings_tool_enabled": True,
                       "v3_filing_body_bridge_max_attempts": 1})


def _wire(monkeypatch, web):  # noqa: ANN001, ANN202
    real_list, real_ensure = acq.list_disclosures, acq.ensure_disclosure_evidence

    async def _list(session, company, *, cfg, **_kw):  # noqa: ANN001, ANN202
        return await real_list(session, company, cfg=cfg, fetcher=web.get, poster=web.post,
                               now=NOW)

    async def _ensure(session, **kw):  # noqa: ANN001, ANN202
        return await real_ensure(session, fetcher=web.get, extractor=web.extractor(), **kw)

    monkeypatch.setattr(acq, "list_disclosures", _list)
    monkeypatch.setattr(acq, "ensure_disclosure_evidence", _ensure)


class TestTool:
    async def test_an_lse_subject_gets_its_official_disclosures_within_budget(
        self, session, monkeypatch
    ):
        from app.services.agent_tools.filings import (
            GET_RECENT_FILINGS_SPEC,
            validate_get_recent_filings,
        )

        web = FakeWeb()
        _wire(monkeypatch, web)
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Ctx(session, company.id, _tool_cfg()),
            validate_get_recent_filings({"ticker": "PRE", "exchange": "LSE",
                                         "topics": ["Longonjo", "../etc"]}))
        assert result["issuer"]["lei"] == PENSANA_LEI
        assert result["corpus"]["attempted"] == 1  # the attempt budget holds
        assert result["contains_untrusted_content"] is True
        # Topic first; administrative notices listed but never fetched.
        assert "Longonjo" in result["items"][0]["headline"]
        admin = [i for i in result["items"] if i["research_rank"] == 5]
        assert admin and all(i["corpus_ready"] is None for i in admin)
        # Metadata only — no item carries document text.
        assert all(set(i) <= {"id", "source_id", "form_type", "category", "document_kind",
                              "filing_date", "headline", "price_sensitive", "research_rank",
                              "source_url", "corpus_ready"} for i in result["items"])

    async def test_a_ticker_that_is_not_the_subject_is_refused(self, session, monkeypatch):
        from app.services.agent_tools.filings import (
            GET_RECENT_FILINGS_SPEC,
            validate_get_recent_filings,
        )

        web = FakeWeb()
        _wire(monkeypatch, web)
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Ctx(session, company.id, _tool_cfg()),
            validate_get_recent_filings({"ticker": "RBW", "exchange": "LSE"}))
        assert result["items"] == [] and web.calls == []

    def test_topics_are_short_plain_words_only(self):
        from app.services.agent_tools.filings import validate_get_recent_filings

        args = validate_get_recent_filings({
            "ticker": "PRE", "exchange": "LSE",
            "topics": ["offtake", "https://evil.example", "a" * 60, "Longonjo", "../x",
                       "funding", "permit", "drilling", "extra"]})
        assert args["topics"] == ("offtake", "Longonjo", "funding", "permit", "drilling")
        with pytest.raises(ValueError):
            validate_get_recent_filings({"ticker": "PRE", "topics": "offtake"})


# ── Evidence-integrity review fixes ────────────────────────────────────────── #


class TestEvidenceIntegrityFixes:
    async def test_a_report_and_its_presentation_stay_two_documents(self, session):
        """HIGH (review): keyed "<kind>:<period>", a half-year presentation fetched
        after the half-year REPORT superseded it and dropped it out of search."""
        from app.models.research_document import ResearchDocument, ResearchDocumentVersion

        report = "https://announcements.asx.com.au/asxpdf/20260225/pdf/0aaaaaaaaaaaaa.pdf"
        deck = "https://announcements.asx.com.au/asxpdf/20260225/pdf/0bbbbbbbbbbbbb.pdf"
        body = lambda t: _html(f"<h1>{t}</h1><p>" + f"EcoGraf Limited {t}. " * 20 + "</p>")  # noqa: E731
        page = ("<table>"
                + _asx_row("03070575", "Appendix 4D and Half Year Report for the half-year "
                           "ended 31 December 2025", sensitive=True, date="25/02/2026")
                + _asx_row("03070576", "Half Year Results Presentation - half-year ended "
                           "31 December 2025", sensitive=True, date="25/02/2026")
                + "</table>")
        base = ("https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do"
                "?display=pdf&idsId=")
        web = FakeWeb({
            "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=EGR"
            "&timeframe=Y&year=2026": ("text/html", page.encode()),
            base + "03070575": ("text/html", _display(report).encode()),
            base + "03070576": ("text/html", _display(deck).encode()),
            report: ("text/html", body("half year report")),
            deck: ("text/html", body("results presentation")),
        })
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             now=NOW)
        for document in listing.documents:
            result = await acq.ensure_disclosure_evidence(
                session, issuer=listing.issuer, document=document, cfg=CFG,
                fetcher=web.get, extractor=web.extractor())
            assert result.is_ready, result
        documents = (await session.execute(select(ResearchDocument))).scalars().all()
        assert len(documents) == 2
        current = (await session.execute(select(ResearchDocumentVersion).where(
            ResearchDocumentVersion.is_current.is_(True)))).scalars().all()
        assert len(current) == 2  # neither superseded the other

    async def test_a_forecast_in_the_official_title_is_refused_too(self, session,
                                                                   monkeypatch):
        """HIGH (review): "Q1 2028 first production update" in a TITLE is a forecast —
        for the corpus version AND the facts' default period (re-review)."""
        from app.models.research_document import ResearchDocumentVersion
        from app.services.sources import live_fetchers
        from app.services.sources.disclosures.relevance import classify_uk

        fact_periods = []
        real = live_fetchers.document_period_for

        def spy(**kw):  # noqa: ANN003, ANN202
            found = real(**kw)
            fact_periods.append((kw.get("title"), found))
            return found

        monkeypatch.setattr(live_fetchers, "document_period_for", spy)

        assert classify_uk(nsm_type="Statement re",
                           headline="Q1 2028 first production update",
                           document_format="Plain text")[1] == "other"
        ref = "91cc0ab0-3199-4dac-8d0f-35a6a5701273"
        NSM_RESPONSE["hits"]["hits"][0]["_source"]["headline"] = (
            "Pensana Plc - Q1 2028 first production update")
        try:
            web = FakeWeb()
            company = await _company(session, "PRE", "LSE", "Pensana Plc")
            await _core(session, company, web)
        finally:
            NSM_RESPONSE["hits"]["hits"][0]["_source"]["headline"] = (
                "Pensana Plc - Update on Longonjo Financing")
        versions = (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        forecast = next(v for v in versions if ref in (v.canonical_url or ""))
        assert forecast.period_key is None  # published 2026-09: 2028-Q1 had not begun
        facts_default = [p for t, p in fact_periods if "Q1 2028" in (t or "")]
        assert facts_default and not any(p.is_known for p in facts_default)

    def test_the_facts_period_rule_is_the_corpus_rule(self):
        """One rule on every path: title only, and nothing that had not begun."""
        from datetime import date as _date

        from app.services.sources.disclosure_period_policy import document_period_for

        title = "Pensana Plc - Q1 2028 first production update"
        assert not document_period_for(title=title, url=None, extraction=None,
                                       title_only=True,
                                       published_at=_date(2026, 9, 1)).is_known
        assert document_period_for(title=title, url=None, extraction=None,
                                   title_only=True,
                                   published_at=_date(2028, 4, 2)).is_known
        # A fiscal quarter of a June year-end began before its calendar reading
        # (code review): kept, while a fiscal year still years away is not.
        for fiscal, published in (("Quarterly Activities Report - Q1 FY2027",
                                   _date(2026, 10, 28)),
                                  ("Q2 FY27 Quarterly Activities Report",
                                   _date(2027, 1, 30))):
            assert document_period_for(title=fiscal, url=None, extraction=None,
                                       title_only=True, published_at=published).is_known
        assert not document_period_for(title="Q1 FY2030 production target", url=None,
                                       extraction=None, title_only=True,
                                       published_at=_date(2026, 10, 28)).is_known
        report = "Half Year Report for the six months ended 31 December 2025"
        assert document_period_for(title=report, url=None, extraction=None,
                                   title_only=True,
                                   published_at=_date(2026, 2, 27)).is_known

    async def test_a_corrected_refiling_is_fetched_again(self, session):
        """MEDIUM (review): an NSM amendment keeps the address; the source's update time
        after the last acquisition makes the holding stale."""
        from app.services.corpus.filing_evidence import current_version_for_document_ref

        ref = "91cc0ab0-3199-4dac-8d0f-35a6a5701273"
        url = f"https://data.fca.org.uk/artefacts/NSM/PRN/{ref}.html"
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        old = await current_version_for_document_ref(session, company_id=company.id,
                                                     document_ref=ref)
        before = len(web.content_fetches())
        # Unchanged at the source: reused, not fetched.
        await _core(session, company, web)
        assert len(web.content_fetches()) == before
        # The regulator records an amendment after our acquisition.
        hit = NSM_RESPONSE["hits"]["hits"][0]["_source"]
        hit["last_updated_date"] = "2099-01-01T00:00:00Z"
        web.post_map = {k: json.dumps(NSM_RESPONSE).encode() for k in web.post_map}
        web.get_map[url] = ("text/html", RNS_FINANCING.replace(b"30%", b"35%"))
        try:
            await _core(session, company, web)
        finally:
            hit.pop("last_updated_date")
        assert len(web.content_fetches()) > before
        new = await current_version_for_document_ref(session, company_id=company.id,
                                                     document_ref=ref)
        assert new.id != old.id and new.content_hash != old.content_hash

    async def test_a_stale_holding_out_of_budget_is_still_ready(self, session):
        """Code review: a correction not fetched for budget leaves the held reading
        searchable — it is reported ready, not unavailable."""
        from app.services.sources.disclosures import acquisition as acq

        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             poster=web.post, now=NOW)
        held = next(d for d in listing.documents if d.research_rank < 5)
        stale = dataclasses.replace(held, updated_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
        before = len(web.content_fetches())
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=stale, cfg=CFG, fetcher=web.get,
            extractor=web.extractor(), ready_only=True)
        assert result.state == "ready" and result.reason is None
        assert len(web.content_fetches()) == before

    @pytest.mark.parametrize(("headline", "kind"), [
        ("Final Results of Retail Offer", "other"),
        ("Notice of Final Results Date", "other"),
        ("Final Results for the year ended 30 June 2026", "results_release"),
    ])
    def test_uk_results_wording(self, headline, kind):
        from app.services.sources.disclosures.relevance import classify_uk

        assert classify_uk(nsm_type="Statement re", headline=headline,
                           document_format="Plain text")[1] == kind

    @pytest.mark.parametrize("headline", [
        "2026 Sustainability Annual Report", "Annual Report on Tenements",
        "Annual Report Webinar",
    ])
    def test_an_asx_annual_something_is_not_the_annual_report(self, headline):
        from app.services.sources.disclosures.relevance import classify_asx

        assert classify_asx(headline=headline, price_sensitive=False)[1] != "annual_report"

    def test_the_listing_parser_is_linear_on_hostile_html(self):
        """MEDIUM (security): a regex over unbalanced tags was quadratic — 40 KB took
        9.6 s, and the parse ran on the event loop."""
        import time

        from app.services.sources.disclosures.asx import parse_asx_listing

        start = time.perf_counter()
        parse_asx_listing("<tr " + "<" * 2_000_000)
        parse_asx_listing("<tr><td>" + "<a" * 1_000_000)
        assert time.perf_counter() - start < 2.0


# ── Code review fixes ──────────────────────────────────────────────────────── #


class TestCodeReviewFixes:
    async def test_a_february_run_reads_every_year_in_the_window(self, session):
        """BLOCKING (review): {now.year, cutoff.year} skipped the middle year, which
        holds the latest annual and half-year reports."""
        web = FakeWeb()
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                   now=datetime(2027, 2, 10, tzinfo=timezone.utc))
        years = [u.rsplit("year=", 1)[1] for _m, u in web.calls if "announcements.do" in u]
        assert years == ["2027", "2026", "2025"]

    async def test_the_same_bytes_are_ready_for_a_second_company(self, session):
        """HIGH (review): documents are deduplicated by content hash globally; a second
        company holding the same issuer's PDF was never READY and re-fetched forever."""
        web = FakeWeb()
        first = await _company(session, "EGR", "AU", "EcoGraf Limited")
        await _core(session, first, web)
        second = await _company(session, "EGR.AX", "ASX", "EcoGraf Limited")
        out = await _core(session, second, web)
        assert out["documents"] and all(d["state"] == "ready" for d in out["documents"]), [
            (d["document_ref"], d["reason"]) for d in out["documents"]]
        fetched = len(web.content_fetches())
        again = await _core(session, second, web)
        assert len(web.content_fetches()) == fetched and all(d["reused"] for d in again["documents"])

    @pytest.mark.parametrize(("issuer_name", "text", "expected"), [
        ("AUSTRALIAN STRATEGIC MATERIALS LTD", "Australian rare earths leader Lynas Rare "
         "Earths Limited reports record output.", False),
        ("IGO LIMITED", "Indigo Minerals announces drilling.", False),
        ("IGO LIMITED", "IGO Limited announces its quarterly report.", True),
        ("RAINBOW RARE EARTHS LIMITED", "Rainbow Rare Earths Limited signs an MoU.", True),
        ("PENSANA PLC", "Pensana Plc (the Company) announces financing.", True),
        ("NICK SCALI LIMITED", "Results for Nick Scali (ASX: NCK).", True),
        ("NICK SCALI LIMITED", "Furniture retailer update (ASX: NCK).", True),
    ])
    def test_the_content_must_name_the_whole_issuer(self, issuer_name, text, expected):
        from types import SimpleNamespace

        from app.services.sources.disclosures.model import VerifiedIssuer

        ticker = {"IGO LIMITED": "IGO", "NICK SCALI LIMITED": "NCK"}.get(issuer_name, "XXX")
        issuer = VerifiedIssuer(company_id=uuid.uuid4(), ticker=ticker, venue="AU",
                                name=issuer_name, source_id="asx_announcements",
                                identity_basis="t")
        artifact = SimpleNamespace(title=issuer_name, extraction=SimpleNamespace(
            blocks=[SimpleNamespace(text=text)], excerpts=[]))
        assert acq.document_names_issuer(artifact, issuer) is expected

    async def test_a_suffixed_ticker_and_an_aim_venue_are_understood(self, session):
        web = FakeWeb()
        asx = await _company(session, "EGR.AX", "ASX", "EcoGraf Limited")
        listing = await acq.list_disclosures(session, asx, cfg=CFG, fetcher=web.get, now=NOW)
        assert listing.issuer is not None and listing.issuer.ticker == "EGR"
        aim = await _company(session, "PRE", "AIM", "Pensana Plc")
        assert acq.source_for(aim, CFG) == ("uk_fca_nsm", None)

    async def test_a_held_lei_that_disagrees_with_gleif_fails_closed(self, session, monkeypatch):
        from app.services.sources.disclosures import uk_nsm

        async def _held(_session, _company):  # noqa: ANN001, ANN202
            return "213800NW5GVIRMXSRL48"

        monkeypatch.setattr(uk_nsm, "_lei_from_entity_master", _held)
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             poster=web.post, now=NOW)
        assert listing.issuer is None and listing.reason == "identity_unverified"

    async def test_an_unreadable_directory_is_source_unavailable_not_identity(self, session):
        web = FakeWeb()
        web.get_map.pop(
            "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file")
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             now=NOW)
        assert listing.issuer is None and listing.reason == "source_unavailable"

    async def test_an_index_failure_is_a_reason_never_an_exception(self, session, monkeypatch):
        async def _boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("index backend down")

        monkeypatch.setattr(acq, "_index", _boom)
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        out = await _core(session, company, web)
        assert out["documents"] and all(d["state"] == "unavailable" for d in out["documents"])

    async def test_the_wall_budget_stops_further_fetches(self, session):
        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        cfg = Settings(**{**CFG.model_dump(), "v3_disclosure_core_budget_seconds": 0.0001})
        out = await acq.ensure_core_disclosures(session, company=company, cfg=cfg,
                                                fetcher=web.get, poster=web.post,
                                                extractor=web.extractor(), now=NOW)
        reasons = [d["reason"] for d in out["documents"]]
        assert "acquire_budget_exhausted" in reasons and len(web.content_fetches()) <= 1

    def test_a_text_annual_financial_report_with_results_is_a_results_release(self):
        from app.services.sources.disclosures.relevance import classify_uk

        assert classify_uk(nsm_type="Annual Financial Report",
                           headline="Final Results for the year ended 30 June 2026",
                           document_format="Plain text")[1] == "results_release"
        assert classify_uk(nsm_type="Annual Financial Report",
                           headline="Publication of Annual Report 2025 and Notice of AGM",
                           document_format="Plain text")[1] == "other"


class TestReportLineage:
    """Live acceptance C (EcoGraf, report 8b246736): the research read four ASX
    documents, and the report's primary-documents view showed none — the acquisition
    recorded its attempts with no run. The consumer is the view; it is read here."""

    async def _run(self, session):  # noqa: ANN001, ANN202
        from app.models.agent_run import AgentRun

        run = AgentRun(id=uuid.uuid4(), workflow_name="company_research",
                       workflow_version="test", status="running")
        session.add(run)
        await session.flush()
        return run.id

    async def _view(self, session, company, run_id):  # noqa: ANN001, ANN202
        from app.services.primary_document_view_service import get_report_primary_documents

        return await get_report_primary_documents(
            session, report_company_id=company.id, report_agent_run_id=run_id,
            report_id=uuid.uuid4())

    async def test_acquired_then_reused_documents_appear_in_each_runs_report(
        self, session
    ):
        from datetime import timedelta

        from app.models.extracted_document import ExtractedDocument

        web = FakeWeb()
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        first = await self._run(session)
        out = await acq.ensure_core_disclosures(
            session, company=company, cfg=CFG, fetcher=web.get, poster=web.post,
            extractor=web.extractor(), now=NOW, agent_run_id=first)
        ready = {d["document_ref"] for d in out["documents"] if d["state"] == "ready"}
        assert ready
        view = await self._view(session, company, first)
        assert {d.canonical_url.rsplit("=", 1)[-1] for d in view.documents} >= ready
        assert view.summary.extracted_count >= len(ready)
        assert not any(d.reused for d in view.documents)
        # Older documents, as they would be on a later day.
        for doc in (await session.execute(select(ExtractedDocument))).scalars().all():
            doc.created_at = doc.created_at - timedelta(hours=1)
        await session.flush()

        fetched = len(web.content_fetches())
        second = await self._run(session)
        again = await acq.ensure_core_disclosures(
            session, company=company, cfg=CFG, fetcher=web.get, poster=web.post,
            extractor=web.extractor(), now=NOW, agent_run_id=second)
        assert len(web.content_fetches()) == fetched  # no network for READY documents
        assert {d["document_ref"] for d in again["documents"] if d.get("reused")} == ready
        rerun = await self._view(session, company, second)
        shown = {d.canonical_url.rsplit("=", 1)[-1] for d in rerun.documents}
        assert shown == ready and all(d.reused for d in rerun.documents)
        assert all(d.pinned is None for d in rerun.documents)  # nothing was fetched

    async def test_a_reuse_row_never_hides_a_pending_correction(self, session):
        """A stale holding answered from the corpus records NO reuse row: its time
        would read as 'acquired after the correction'."""
        from app.models.document_ingestion_attempt import DocumentIngestionAttempt

        web = FakeWeb()
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        await _core(session, company, web)
        listing = await acq.list_disclosures(session, company, cfg=CFG, fetcher=web.get,
                                             poster=web.post, now=NOW)
        held = next(d for d in listing.documents if d.research_rank < 5)
        stale = dataclasses.replace(held, updated_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
        run = await self._run(session)
        await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=stale, cfg=CFG, fetcher=web.get,
            extractor=web.extractor(), ready_only=True, agent_run_id=run)
        rows = (await session.execute(select(DocumentIngestionAttempt).where(
            DocumentIngestionAttempt.agent_run_id == run))).scalars().all()
        assert rows == []
        assert await acq._changed_since_acquired(session, company_id=company.id,
                                                 document=stale)

    async def test_the_front_door_links_the_reports_own_run(self, session, monkeypatch):
        """Caller level (review): the run the documents carry is the REPORT's
        ``created_by_agent_run_id`` — the id its primary-documents view reads — not the
        durable job id the pipeline also receives."""
        from app.models.report import Report
        from app.services import company_research_service as svc
        from app.services.sources.disclosures import acquisition

        seen: dict = {}

        async def spy(session, *, company, cfg, agent_run_id=None, **kw):  # noqa: ANN001, ANN003, ANN202
            seen["agent_run_id"] = agent_run_id
            return {"source_id": None, "documents": [], "skipped": "spy"}

        monkeypatch.setattr(acquisition, "ensure_core_disclosures", spy)
        monkeypatch.setattr(svc, "settings", CFG.model_copy(update={
            "v3_pipeline_enabled": True, "azure_openai_api_key": "",
            "azure_openai_endpoint": "", "deepseek_api_key": ""}))
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        run = await self._run(session)
        report = Report(id=uuid.uuid4(), title="t", slug=f"s-{uuid.uuid4().hex[:8]}",
                        report_type="company_deep_dive", status="draft",
                        created_by_agent_run_id=run, company_id=company.id)
        session.add(report)
        await session.flush()
        await svc._run_v3_pipeline(session, company=company, report_id=report.id,
                                   research_job_id=uuid.uuid4())
        assert seen["agent_run_id"] == run == report.created_by_agent_run_id

    async def test_the_pipeline_links_only_a_real_agent_run(self, session):
        from app.services.pipeline.v3_pipeline import _existing_agent_run_id

        run = await self._run(session)
        assert await _existing_agent_run_id(session, run) == run
        assert await _existing_agent_run_id(session, uuid.uuid4()) is None
        assert await _existing_agent_run_id(session, None) is None


def _ixbrl(*leis: str, text: str = "") -> bytes:
    """An inline-XBRL annual report: contexts naming ``leis``, statements as a table,
    and (like Rainbow's) no narrative text unless ``text`` is given."""
    contexts = "".join(
        f'<xbrli:context id="C{i}"><xbrli:entity><xbrli:identifier '
        f'scheme="http://standards.iso.org/iso/17442">{lei}</xbrli:identifier>'
        f"</xbrli:entity></xbrli:context>" for i, lei in enumerate(leis))
    return _html(
        f'<div style="display:none"><ix:header><ix:resources>{contexts}</ix:resources>'
        f"</ix:header></div><img src=\"data:image/png;base64,AAAA\"/>"
        f"<table><tr><th>US$000</th><th>2025</th></tr><tr><td>Exploration costs</td>"
        f"<td>1,200</td></tr></table>{text}")


class TestRainbowAcceptanceFixes:
    """Live acceptance B (Rainbow, report 00cb55fb): the 2025 annual report is an
    iXBRL filing whose narrative is page images; it was refused as another issuer's
    document, and the year's text narrative (its "Preliminary Results" RNS) was never
    selected."""

    ANNUAL = "https://data.fca.org.uk/artefacts/NSM/Portal/NI-000131364/NI-000131364.pdf"

    async def _annual(self, session, content):  # noqa: ANN001, ANN202
        web = FakeWeb({self.ANNUAL: ("text/html", content)})
        company = await _company(session, "PRE", "LSE", "Pensana Plc")
        out = await _core(session, company, web)
        return next(d for d in out["documents"] if d["document_kind"] == "annual_report")

    async def test_a_filing_whose_own_contexts_name_the_issuers_lei_is_the_issuers(
        self, session
    ):
        annual = await self._annual(session, _ixbrl(
            PENSANA_LEI, text="<p>" + "The Group continued construction of the mine. " * 8
            + "</p>"))
        assert annual["state"] == "ready", annual

    @pytest.mark.parametrize("leis", [("5493001KJTIIGC8Y1R12",),
                                      (PENSANA_LEI, "5493001KJTIIGC8Y1R12")])
    async def test_another_or_a_mixed_lei_is_refused(self, session, leis):
        annual = await self._annual(session, _ixbrl(
            *leis, text="<p>" + "The Group continued construction of the mine. " * 8
            + "</p>"))
        assert annual["state"] == "unavailable"
        assert annual["reason"] == "identity_unverified"

    async def test_a_document_with_no_readable_text_is_not_called_another_issuers(
        self, session
    ):
        # Statements as a table, narrative as images, no identifier: extracted, but
        # nothing readable to name anyone.
        annual = await self._annual(session, _ixbrl())
        assert annual["state"] == "unavailable"
        assert annual["reason"] == "no_indexable_content"

    def test_the_full_year_results_announcement_has_its_own_slot(self):
        from datetime import timedelta

        from app.services.sources.disclosures.model import DisclosureListing, OfficialDocument

        def doc(ref, days, headline, kind, rank):  # noqa: ANN001, ANN202
            return OfficialDocument(
                source_id="uk_fca_nsm", document_ref=ref,
                published_at=NOW - timedelta(days=days), headline=headline,
                venue_category="", category="results", doc_kind=kind, research_rank=rank,
                price_sensitive=None, media="html", official_url=f"https://x/{ref}")

        listing = DisclosureListing(issuer=None, documents=[
            doc("ar", 336, "Annual report 30 June 2025", "annual_report", 0),
            doc("prelim", 336, "Preliminary Results for Year-end 30 June 2025",
                "results_release", 2),
            doc("agm", 320, "Results of Annual General Meeting", "results_release", 5),
            doc("hy", 181, "Interim Results for six months to 31 December 2025",
                "interim_report", 1),
            doc("mou", 4, "MoU with Neo Performance Materials", "other", 3),
        ])
        refs = [d.document_ref for d in acq.select_core_documents(
            listing, max_documents=5, now=NOW)]
        assert refs == ["ar", "prelim", "hy", "mou"]  # material kept; AGM never chosen


class TestProMedicusAcceptanceFixes:
    """Live acceptance D (Pro Medicus, report f9ddd4c3): a council finding read "The
    only Group revenue figure in evidence is FY2024: USD 1,402 million". The figure was
    the "Deferred revenue" row of a deferred-tax table, A$'000, in a half-year's
    comparative column — label, currency, scale and period all wrong."""

    @pytest.mark.parametrize(("text", "code"), [
        ("A$25M contract", "AUD"), ("A$'000", "AUD"), ("US$ million", "USD"),
        ("S$ 3m", "SGD"), ("C$ 10m", "CAD"), ("NZ$ 4m", "NZD"), ("€m", "EUR"),
        # Review: an incidental mention never displaces the table's own currency.
        ("(in millions) $ 4,210 exposure to the Canadian dollar", "USD"),
        ("Revenue $ 1,000 (in thousands) AUD hedges", "USD"),
        ("in millions of euros; the Group issued a US$500 million bond", "EUR"),
        ("company's$ amounts", None),  # not an amount; fails closed
        # Security review: one prefixed aside never relabels bare "$" figures.
        ("Revenue $5.2bn; HK$10m deposit", "USD"),
        ("Revenue $5.2 billion; hedges of the Australian dollar", "USD"),
        ("C$ 10m and A$ 5m", None),  # a mix of prefixes is no currency
    ])
    def test_a_prefixed_dollar_is_its_own_currency(self, text, code):
        from app.services.sources.primary_fact_parser import _find_currency

        assert _find_currency(text) == code

    def test_a_bare_dollar_is_usd_only_where_the_issuer_says_so(self):
        from app.services.sources.extracted_fact_validator import (
            IssuerContext,
            _resolve_dollar,
        )

        us = IssuerContext(company_name="X")  # default: every existing path unchanged
        asx = IssuerContext(company_name="X", bare_dollar_is_usd=False)
        assert _resolve_dollar("USD", "$'000", us) == "USD"
        assert _resolve_dollar("USD", "$'000", asx) is None
        assert _resolve_dollar("USD", "US$ million", asx) == "USD"
        assert _resolve_dollar("AUD", "A$'000", asx) == "AUD"
        # Security review: a "US$" aside does not make a "$'000" table US dollars.
        assert _resolve_dollar(
            "USD", "Revenue $'000 12,345; US$ denominated loan note", asx) is None
        # Re-review: the WORD "dollars" maps to USD; an ASX "Australian dollars" is not.
        from app.services.sources.primary_fact_parser import _find_currency

        for text in ("Revenue A$161.5 million; amounts are presented in Australian dollars",
                     "The financial report is presented in Australian dollars. Revenue 161.5 "
                     "million", "Revenue of 161.5 million Canadian dollars",
                     "Presented in Australian dollars. Revenue 161.5 million; 30% of sales "
                     "are in US dollars"):
            assert _resolve_dollar(_find_currency(text), text, asx) != "USD", text
        assert _resolve_dollar("USD", "revenue in USD millions", asx) == "USD"
        assert _resolve_dollar("USD", "presented in US dollars", asx) == "USD"

    @pytest.mark.parametrize(("text", "scale"), [
        ("$’000", "thousand"), ("$'000", "thousand"), ("£000", "thousand"),
        ("US$ million", "million"), ("2,000", None), ("'000 employees", None),
    ])
    def test_a_thousands_column_header_is_thousands(self, text, scale):
        from app.services.sources.extracted_fact_validator import _find_scale

        assert _find_scale(text) == scale

    @pytest.mark.parametrize(("label", "field"), [
        ("Deferred revenue", None), ("Deferred\xa0revenue", None), ("Unearned revenue", None),
        ("Revenue received in advance", None), ("Revenue", "revenue"),
        ("Total revenue", "revenue"),
    ])
    def test_deferred_revenue_is_not_revenue(self, label, field):
        from app.services.sources.extracted_fact_validator import _match_label

        assert _match_label(label) == field

    async def test_mutation_an_asx_dollar_table_never_becomes_a_usd_fact(self, session):
        """End to end through acquisition: an ASX annual report's "$'000" revenue row is
        held, but never as a validated USD figure."""
        from app.models.extracted_document import ExtractedFact

        pdf = "https://announcements.asx.com.au/asxpdf/20260925/pdf/074hv8s0yw9t50.pdf"
        annual = _html(
            "<h1>Annual Report 2026</h1><p>"
            + "EcoGraf Limited is developing the Epanko graphite project. " * 20
            + "</p><table><tr><th>Consolidated</th><th>2026 $'000</th>"
              "<th>2025 $'000</th></tr><tr><td>Revenue</td><td>5,000</td>"
              "<td>4,000</td></tr><tr><td>Deferred revenue</td><td>1,402</td>"
              "<td>900</td></tr></table>")
        web = FakeWeb({pdf: ("text/html", annual)})
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        out = await _core(session, company, web)
        assert next(d for d in out["documents"]
                    if d["document_ref"] == "03143773")["state"] == "ready"
        facts = (await session.execute(select(ExtractedFact))).scalars().all()
        assert not [f for f in facts if f.currency == "USD"]
        assert not [f for f in facts if f.validation_status == "validated"
                    and f.label == "revenue"]
        assert not [f for f in facts if "1,402" in (f.value_text or "")
                    and f.label == "revenue"]

    @pytest.mark.parametrize("headline", [
        "Trading Update ahead of Full Year Results", "Full Year Results Presentation",
        "Annual Results Webcast", "Notice of Final Results Date",
        "Final Results of Retail Offer", "Results of Annual General Meeting",
    ])
    def test_about_the_results_is_not_the_results(self, headline):
        from app.services.sources.disclosures.relevance import is_full_year_results

        assert not is_full_year_results(headline)

    @pytest.mark.parametrize("headline", [
        "Results for the year ended 30 June 2025", "FY25 Results",
        "Preliminary Final Report FY26 (Appendix 4E)",
        "Preliminary Results for Year-end 30 June 2025",
    ])
    def test_full_year_results_headlines(self, headline):
        from app.services.sources.disclosures.relevance import is_full_year_results

        assert is_full_year_results(headline)


class TestOlderPipelineHoldings:
    """Production already holds facts read under the corrected readings (Pro Medicus:
    "Deferred revenue 1,402" as USD Group revenue). A READY holding derived by an older
    extraction pipeline is read once more, and its facts are superseded — never served
    from the corpus forever."""

    async def test_an_older_pipeline_holding_is_reread_and_its_facts_superseded(
        self, session
    ):
        from app.models.extracted_document import ExtractedDocument, ExtractedFact
        from app.services.sources.extraction_pipeline_version import (
            CURRENT_EXTRACTION_PIPELINE_VERSION,
        )

        web = FakeWeb()
        company = await _company(session, "EGR", "AU", "EcoGraf Limited")
        await _core(session, company, web)
        docs = (await session.execute(select(ExtractedDocument))).scalars().all()
        assert docs and all(d.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION
                            for d in docs)
        held = docs[0]
        bogus = ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=held.id, label="revenue",
            value_numeric=1402.0, value_text="1,402", currency="USD", scale="million",
            period="2024", extraction_method="native_pdf", confidence=0.8,
            validation_status="validated", is_active=True)
        session.add(bogus)
        held.pipeline_version = CURRENT_EXTRACTION_PIPELINE_VERSION - 1
        await session.flush()

        fetched = len(web.content_fetches())
        await _core(session, company, web)
        assert len(web.content_fetches()) == fetched + 1  # only the outdated one
        await session.refresh(bogus)
        await session.refresh(held)
        assert bogus.is_active is False
        assert held.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION
        # Current again: the next run reads nothing.
        await _core(session, company, web)
        assert len(web.content_fetches()) == fetched + 1


def test_the_filing_lei_scan_is_linear_on_a_hostile_page():
    """Security review: a regex scanning from every tag start took 15 s of CPU on a
    hostile 18 MB page. The literal scan is bounded."""
    import time

    from app.services.sources.primary_document_extractor import _xbrl_lei_identifiers

    real = ('<xbrli:identifier scheme="http://standards.iso.org/iso/17442">'
            + PENSANA_LEI + "</xbrli:identifier>")
    assert _xbrl_lei_identifiers("<html>" + real * 3 + "</html>") == {PENSANA_LEI}
    assert _xbrl_lei_identifiers('<other scheme="http://standards.iso.org/iso/17442">'
                                 + PENSANA_LEI + "</xbrli:identifier>") == set()
    hostile = ('<xbrli:identifier ' + "a" * 190
               + ' scheme="http://standards.iso.org/iso/17442" ' + "b" * 190) * 50_000
    started = time.perf_counter()
    _xbrl_lei_identifiers(hostile)
    _xbrl_lei_identifiers("<xbrli:identifier " * 1_000_000)
    assert time.perf_counter() - started < 2.0


class TestReuseRevalidationOfAnnouncements:
    """Live acceptance E (EcoGraf rerun, report a392e32c): the report-regeneration
    reuse path re-validated a stored ASX announcement under the DEFAULT context, so
    "Cash and cash equivalents of $3.8 million" (Australian dollars) stayed a validated
    USD fact and the row was stamped current before the corrected reading could run."""

    async def _doc(self, session, source_type):  # noqa: ANN001, ANN202
        from app.models.extracted_document import ExtractedDocument

        doc = ExtractedDocument(
            id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
            canonical_url="https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do"
                          "?display=pdf&idsId=03120015",
            provider="asx_announcements", source_type=source_type,
            source_tier="T1_PRIMARY_FILING", mime_type="application/pdf",
            extraction_method="native_pdf", status="extracted",
            title="June 2026 Quarterly Activities Report", pipeline_version=16,
            excerpts_json=[{"excerpt_id": "X2", "page_number": 2,
                            "text": "Cash and cash equivalents of $3.8 million at 30 June "
                                    "2026", "extraction_method": "native_pdf",
                            "confidence": 0.9}])
        session.add(doc)
        await session.flush()
        return doc

    async def _revalidate(self, session, doc, extractor=None):  # noqa: ANN001, ANN202
        from app.services.extracted_document_service import _revalidate_document
        from app.services.sources.extracted_fact_validator import IssuerContext

        return await _revalidate_document(
            session, doc, cfg=CFG, existing_active_facts=[],
            issuer_context=IssuerContext(company_name="ECOGRAF LIMITED", ticker="EGR"),
            primary_document_extractor=extractor)

    async def test_an_announcement_is_revalidated_without_the_bare_dollar_as_usd(
        self, session
    ):
        from app.models.extracted_document import ExtractedFact

        announcement = await self._doc(session, "asx_announcement")
        await self._revalidate(session, announcement)
        facts = (await session.execute(select(ExtractedFact).where(
            ExtractedFact.extracted_document_id == announcement.id))).scalars().all()
        assert not [f for f in facts if f.currency == "USD"], facts

    async def test_the_same_text_elsewhere_keeps_the_existing_reading(self, session):
        """Control: the default path is unchanged (a US issuer's "$" is USD)."""
        from app.models.extracted_document import ExtractedFact

        ir = await self._doc(session, "company_ir")
        await self._revalidate(session, ir)
        facts = (await session.execute(select(ExtractedFact).where(
            ExtractedFact.extracted_document_id == ir.id))).scalars().all()
        assert [f for f in facts if f.currency == "USD"]

    async def test_an_announcements_official_page_is_never_refetched_as_content(
        self, session
    ):
        from app.services.extracted_document_service import _attempt_full_reextraction

        called = []

        async def extractor(url, **kw):  # noqa: ANN001, ANN003, ANN202
            called.append(url)

        announcement = await self._doc(session, "asx_announcement")
        assert await _attempt_full_reextraction(
            announcement, cfg=CFG, issuer_context=None,
            primary_document_extractor=extractor) is None
        assert called == []


async def test_a_declined_announcement_reread_retires_its_facts(session):
    """Review of PR #254: when the reuse path declines to re-read an announcement whose
    facts are table-derived, those facts (derived under the corrected reading) must not
    stay active for the facts / calculation tools."""
    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.extracted_document_service import _revalidate_document
    from app.services.sources.extracted_fact_validator import IssuerContext

    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url="https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do"
                      "?display=pdf&idsId=03059473",
        provider="asx_announcements", source_type="asx_announcement",
        source_tier="T1_PRIMARY_FILING", mime_type="application/pdf",
        extraction_method="native_pdf", status="extracted", title="Half Year Accounts",
        pipeline_version=16, excerpts_json=[])
    session.add(doc)
    table_fact = ExtractedFact(
        id=uuid.uuid4(), extracted_document_id=doc.id, label="revenue",
        value_numeric=1402.0, value_text="1,402", currency="USD", scale="million",
        period="2024", extraction_method="native_pdf", confidence=0.8,
        validation_status="validated", is_active=True, table_location="p16:t1")
    session.add(table_fact)
    await session.flush()
    async def never(url, **kw):  # noqa: ANN001, ANN003, ANN202
        raise AssertionError("an announcement is never re-read on the reuse path")

    await _revalidate_document(
        session, doc, cfg=CFG, existing_active_facts=[table_fact],
        issuer_context=IssuerContext(company_name="PRO MEDICUS LIMITED", ticker="PME"),
        primary_document_extractor=never)
    await session.refresh(table_fact)
    assert table_fact.is_active is False
    assert doc.pipeline_version == 16  # not restamped: the acquisition re-reads it


def test_an_announcements_undated_figure_never_takes_a_year_from_elsewhere_in_the_body():
    """Live acceptance E (Pro Medicus rerun, report 49268894): "revenue of $266.6m"
    (FY26) took the period 2027 from "…through to 30 June 2027", an LTI vesting clause
    three sentences later — the excerpt-wide first-year fallback."""
    from app.services.sources.document_period import UNKNOWN_DOCUMENT_PERIOD
    from app.services.sources.extracted_fact_validator import (
        IssuerContext,
        validate_extracted_facts,
    )
    from app.services.sources.primary_document_extractor import (
        PrimaryDocumentExcerpt,
        PrimaryDocumentExtraction,
    )

    text = ("Executive remuneration outcomes for FY26 were closely aligned with Company "
            "performance. The Company delivered underlying EBIT of $199.5m and revenue of "
            "$266.6m, up from $152.4m and $206.3m respectively in FY25. Accordingly, the "
            "STI outcome for participants was between 78% and 80% of target. Pleasingly, "
            "this has resulted in 100% vesting for the FY24-FY26 LTI tranche, which "
            "remains conditional on continued employment through to 30 June 2027.")
    extraction = PrimaryDocumentExtraction(
        content_hash="x" * 64, mime_type="application/pdf", extraction_method="native_pdf",
        status="extracted", excerpts=[PrimaryDocumentExcerpt(
            excerpt_id="X1", text=text, page_number=32, extraction_method="native_pdf",
            confidence=0.9)])

    def facts(title_only):  # noqa: ANN001, ANN202
        return [f for f in validate_extracted_facts(
            extraction, issuer_context=IssuerContext(company_name="PRO MEDICUS LIMITED"),
            cfg=CFG, document_period=UNKNOWN_DOCUMENT_PERIOD,
            title_only_period=title_only) if "266.6" in f.value_text]

    assert facts(True) and all(f.period is None for f in facts(True))
    # Control: the existing (non-announcement) reading takes the body's year.
    assert any(f.period == "2027" for f in facts(False))
