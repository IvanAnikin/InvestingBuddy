"""V3.20 slice 11.2 — EDINET: the Japanese issuer's own statutory filings.

Identity from the FSA's code list (TSE code AND an agreeing English name), a daily
filing index kept in the database (the key in a request HEADER only), the issuer's
listing as a query on its own EDINET code, and the official PDF through the existing
disclosure acquisition bridge.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import date, datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings
from app.models.company import Company
from app.models.edinet_filing import EdinetFiling, EdinetIndexDay
from app.services.sources.disclosure_period_policy import is_title_only
from app.services.sources.disclosures import acquisition as acq
from app.services.sources.disclosures import edinet
from app.services.sources.disclosures.model import (
    RANK_ADMINISTRATIVE,
    RANK_ANNUAL,
    RANK_INTERIM,
    RANK_MATERIAL,
    SOURCE_EDINET,
    VerifiedIssuer,
)
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.sources.document_period import detect_document_period
from tests.test_non_us_primary_docs_connectors import (  # noqa: F401 - autouse fixture
    _index_like_postgres,
)

KEY = "k" * 32
#: 2026-11-17 09:00 JST.
NOW = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)
CFG = Settings(
    v3_corpus_enabled=True, primary_document_ingestion_enabled=True,
    report_citation_persistence_enabled=True, v3_artifact_store_backend="memory",
    v3_search_backend="memory", v3_edinet_disclosures_enabled=True, edinet_api_key=KEY,
    v3_disclosure_lookback_days=10, v3_edinet_index_days_per_run=40,
)

HEADER = ("ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,"
          "提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号")
CODE_LIST = "\n".join([
    "ダウンロード実行日,2026年11月17日現在,件数,3件",
    HEADER,
    "E02131,内国法人・組合,上場,有,3000,3月31日,内海造船株式会社,NAIKAI ZOSEN CORPORATION,"
    "ナイカイゾウセン,広島県,輸送用機器,70180,1234567890123",
    "E99999,内国法人・組合,非上場,有,10,3月31日,非上場株式会社,UNLISTED CO.,,東京都,"
    "サービス業,70180,",
    "E03333,内国法人・組合,上場,有,10,3月31日,テスト株式会社,TEST HOLDINGS INC.,,東京都,"
    "情報・通信業,130A0,",
    "E04444,外国法人・組合,上場,無,0,12月31日,ファンド,SOME FUND,,,,,",
])


def _zip(text: str, *, names: tuple[str, ...] = ("EdinetcodeDlInfo.csv",)) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, text.encode("cp932"))
    return buf.getvalue()


def _row(doc_id: str, *, code: str = "E02131", sec: str | None = "70180",
         doc_type: str = "160", desc: str = "半期報告書－第80期(2026/04/01－2027/03/31)",
         start: str | None = "2026-04-01", end: str | None = "2027-03-31",
         submitted: str = "2026-11-13 15:00", withdrawn: str = "0",
         pdf: str = "1") -> dict[str, Any]:
    return {"seqNumber": 1, "docID": doc_id, "edinetCode": code, "secCode": sec,
            "JCN": None, "filerName": "内海造船株式会社", "fundCode": None,
            "ordinanceCode": "010", "formCode": "043A00", "docTypeCode": doc_type,
            "periodStart": start, "periodEnd": end, "submitDateTime": submitted,
            "docDescription": desc, "issuerEdinetCode": None, "subjectEdinetCode": None,
            "subsidiaryEdinetCode": None, "currentReportReason": None,
            "parentDocID": None, "opeDateTime": None, "withdrawalStatus": withdrawn,
            "docInfoEditStatus": "0", "disclosureStatus": "0", "xbrlFlag": "1",
            "pdfFlag": pdf, "attachDocFlag": "0", "englishDocFlag": "0", "csvFlag": "1",
            "legalStatus": "1"}


def _listing(*rows: dict[str, Any], status: str = "200") -> bytes:
    return json.dumps({"metadata": {"status": status, "message": "OK",
                                    "resultset": {"count": len(rows)}},
                       "results": list(rows)}).encode()


class FakeEdinet:
    """The EDINET hosts: the code list, a listing per day, every call recorded."""

    def __init__(self, days: dict[str, list[dict[str, Any]]] | None = None,
                 *, code_list: bytes | None = None, refuse_key: bool = False,
                 failing: set[str] | None = None):
        self.days = days or {}
        self.code_list = _zip(CODE_LIST) if code_list is None else code_list
        self.refuse_key = refuse_key
        self.failing = failing or set()
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, *, params: dict[str, str] | None = None,
                  headers: dict[str, str] | None = None, cfg: Any = None,
                  max_bytes: int = 0, allowed_host: str = "") -> Any:
        self.calls.append({"url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "host": allowed_host})
        if url == edinet.CODE_LIST_URL:
            return 200, self.code_list, None
        assert url == edinet.DOCUMENT_LIST_URL
        if self.refuse_key:
            return 401, None, "http_401"
        day = (params or {}).get("date", "")
        if day in self.failing:
            return 500, None, "http_500"
        return 200, _listing(*self.days.get(day, [])), None

    def day_calls(self) -> list[str]:
        return [c["params"]["date"] for c in self.calls if c["url"] == edinet.DOCUMENT_LIST_URL]


@pytest.fixture
async def session():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_code_list_cache():  # noqa: ANN202
    edinet._CODE_LIST_CACHE.clear()
    yield
    edinet._CODE_LIST_CACHE.clear()


async def _company(session: Any, ticker: str = "7018", exchange: str = "TSE",
                   name: str = "Naikai Zosen Corporation") -> Company:
    company = Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name,
                      status="new")
    session.add(company)
    await session.flush()
    return company


# ── The code list ──────────────────────────────────────────────────────────── #


class TestCodeList:
    def test_rows_are_read_by_their_header_names(self) -> None:
        rows = edinet.parse_code_list_csv(CODE_LIST)
        naikai = next(r for r in rows if r.edinet_code == "E02131")
        assert (naikai.sec_code, naikai.tse_code, naikai.listed) == ("70180", "7018", True)
        assert naikai.name_ja == "内海造船株式会社"
        assert naikai.name_en == "NAIKAI ZOSEN CORPORATION"
        # A filer without a securities code (a fund) is not a listing.
        assert all(r.edinet_code != "E04444" for r in rows)

    def test_a_file_without_the_expected_header_yields_nothing(self) -> None:
        assert edinet.parse_code_list_csv("a,b,c\n1,2,3") == []
        assert edinet.parse_code_list_csv("") == []

    def test_the_zip_is_decoded_as_shift_jis(self) -> None:
        rows = edinet.parse_code_list_zip(_zip(CODE_LIST))
        assert {r.edinet_code for r in rows} == {"E02131", "E99999", "E03333"}

    def test_a_zip_that_is_not_one_csv_is_refused(self) -> None:
        assert edinet.parse_code_list_zip(_zip(CODE_LIST, names=("a.csv", "b.csv"))) == []
        assert edinet.parse_code_list_zip(b"not a zip") == []

    def test_an_oversized_member_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edinet, "MAX_CODE_LIST_CSV_BYTES", 100)
        assert edinet.parse_code_list_zip(_zip(CODE_LIST)) == []


class TestIdentity:
    rows = edinet.parse_code_list_csv(CODE_LIST)

    @pytest.mark.parametrize("ticker", ["7018", "7018.T", "7018.JP", "TYO:7018"])
    def test_the_tse_code_and_an_agreeing_name_match(self, ticker: str) -> None:
        row, why = edinet.match_issuer(self.rows, ticker=ticker,
                                       name="Naikai Zosen Corp")
        assert row is not None and row.edinet_code == "E02131" and why == ""

    def test_an_unlisted_filer_with_the_same_code_is_never_matched(self) -> None:
        row, _ = edinet.match_issuer(self.rows, ticker="7018", name="Unlisted Co")
        assert row is None

    def test_a_disagreeing_name_is_refused_with_the_exchange_name(self) -> None:
        row, why = edinet.match_issuer(self.rows, ticker="7018", name="Toyota Motor")
        assert row is None and "NAIKAI ZOSEN CORPORATION" in why

    def test_the_official_japanese_name_matches(self) -> None:
        row, _ = edinet.match_issuer(self.rows, ticker="7018", name="内海造船株式会社")
        assert row is not None

    def test_an_alphanumeric_listing_code(self) -> None:
        row, _ = edinet.match_issuer(self.rows, ticker="130A", name="Test Holdings")
        assert row is not None and row.edinet_code == "E03333"

    @pytest.mark.parametrize("ticker", ["AAPL", "", None, "70180"])
    def test_a_ticker_that_is_not_a_tse_code_is_refused(self, ticker: Any) -> None:
        row, why = edinet.match_issuer(self.rows, ticker=ticker, name="Naikai Zosen")
        assert row is None and why


# ── The daily index ────────────────────────────────────────────────────────── #


class TestDocumentList:
    def test_rows_are_parsed_and_the_submission_time_is_utc(self) -> None:
        rows, refused = edinet.parse_document_list(_listing(_row("S100ZFII")))
        assert refused == 0 and len(rows or []) == 1
        row = (rows or [])[0]
        assert row["submitted_at"] == datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)
        assert row["period_end"] == date(2027, 3, 31) and row["pdf_flag"] is True

    def test_a_malformed_document_id_is_refused(self) -> None:
        rows, refused = edinet.parse_document_list(
            _listing(_row("S100ZFII"), _row("../etc/x"), _row("")))
        assert [r["doc_id"] for r in rows or []] == ["S100ZFII"] and refused == 2

    @pytest.mark.parametrize("payload", [b"", b"<html>", _listing(status="404"),
                                         b'{"StatusCode": 401, "message": "Access denied"}'])
    def test_an_unsuccessful_answer_is_not_a_listing(self, payload: bytes) -> None:
        assert edinet.parse_document_list(payload)[0] is None


class TestRefresh:
    async def test_the_key_travels_in_a_header_never_in_the_url(self, session: Any) -> None:
        fake = FakeEdinet()
        await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=3, getter=fake.get,
                                   pause=0)
        for call in fake.calls:
            assert KEY not in call["url"]
            assert KEY not in json.dumps(call["params"])
            assert "Subscription-Key" not in call["params"]
            assert call["headers"] == {edinet.KEY_HEADER: KEY}
            assert call["host"] == edinet.EDINET_API_HOST

    async def test_days_are_read_newest_first_and_capped(self, session: Any) -> None:
        fake = FakeEdinet({"2026-11-13": [_row("S100ZFII")]})
        out = await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=5,
                                         getter=fake.get, pause=0)
        assert fake.day_calls() == ["2026-11-17", "2026-11-16", "2026-11-15",
                                    "2026-11-14", "2026-11-13"]
        assert (out.days_read, out.filings_seen) == (5, 1)
        held = (await session.execute(select(EdinetFiling))).scalars().all()
        assert [f.doc_id for f in held] == ["S100ZFII"]

    async def test_final_days_are_not_read_again_but_today_is(self, session: Any) -> None:
        fake = FakeEdinet()
        await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=3, getter=fake.get,
                                   pause=0)
        days = {d.day: d for d in (await session.execute(select(EdinetIndexDay))).scalars()}
        assert days[date(2026, 11, 17)].final is False  # still today in Japan
        assert days[date(2026, 11, 16)].final is True
        fake.calls.clear()
        await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=2, getter=fake.get,
                                   pause=0)
        assert fake.day_calls() == ["2026-11-17", "2026-11-14"]

    async def test_a_refused_key_stops_after_one_request(self, session: Any) -> None:
        fake = FakeEdinet(refuse_key=True)
        out = await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=10,
                                         getter=fake.get, pause=0)
        assert len(fake.day_calls()) == 1 and out.days_failed == 1 and out.days_read == 0

    async def test_a_failed_day_stays_a_hole(self, session: Any) -> None:
        fake = FakeEdinet(failing={"2026-11-16"})
        out = await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=3,
                                         getter=fake.get, pause=0)
        days = {d.day for d in (await session.execute(select(EdinetIndexDay))).scalars()}
        assert date(2026, 11, 16) not in days and out.days_failed == 1

    async def test_without_a_key_nothing_is_requested(self, session: Any) -> None:
        fake = FakeEdinet()
        cfg = Settings(**{**CFG.model_dump(), "edinet_api_key": ""})
        out = await edinet.refresh_index(session, cfg=cfg, now=NOW, max_days=3,
                                         getter=fake.get, pause=0)
        assert fake.calls == [] and out.days_read == 0

    async def test_a_filing_listed_again_is_updated_not_duplicated(self, session: Any) -> None:
        fake = FakeEdinet({"2026-11-17": [_row("S100ZFII")]})
        await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=1, getter=fake.get,
                                   pause=0)
        fake.days["2026-11-17"] = [_row("S100ZFII", withdrawn="1")]
        await edinet.refresh_index(session, cfg=CFG, now=NOW, max_days=1, getter=fake.get,
                                   pause=0)
        held = (await session.execute(select(EdinetFiling))).scalars().all()
        assert len(held) == 1 and held[0].withdrawal_status == "1"


# ── The listing ────────────────────────────────────────────────────────────── #


ISSUER_DAYS = {
    "2026-11-13": [
        _row("S100ZFII"),  # the half-year report
        _row("S100AAAA", code="E11111", sec=None, doc_type="350",
             desc="大量保有報告書（内海造船株式会社）"),  # a holder's report ABOUT it
        _row("S100WWWW", withdrawn="1"),
        _row("S100NOPD", doc_type="180", desc="臨時報告書", pdf="0"),
    ],
    "2026-11-10": [
        _row("S100EXTR", doc_type="180", desc="臨時報告書", start=None, end=None,
             submitted="2026-11-10 09:30"),
        _row("S100BUYB", doc_type="220", desc="自己株券買付状況報告書", start=None,
             end=None, submitted="2026-11-10 10:00"),
    ],
}


class TestListing:
    async def test_only_the_issuers_own_filings_are_listed(self, session: Any) -> None:
        fake = FakeEdinet(ISSUER_DAYS)
        company = await _company(session)
        listing = await edinet.list_edinet_disclosures(session, company, cfg=CFG, now=NOW,
                                                       getter=fake.get, index_pause=0)
        refs = [d.document_ref for d in listing.documents]
        assert refs == ["S100ZFII", "S100BUYB", "S100EXTR"]  # newest first
        assert listing.refused == 2  # withdrawn, and no PDF
        assert listing.issuer is not None and listing.issuer.local_name == "内海造船株式会社"
        assert listing.issuer.source_id == SOURCE_EDINET and listing.issuer.venue == "TSE"

    async def test_documents_are_classified_and_point_at_the_public_pdf(
            self, session: Any) -> None:
        fake = FakeEdinet(ISSUER_DAYS)
        company = await _company(session)
        listing = await edinet.list_edinet_disclosures(session, company, cfg=CFG, now=NOW,
                                                       getter=fake.get, index_pause=0)
        by_ref = {d.document_ref: d for d in listing.documents}
        half = by_ref["S100ZFII"]
        assert (half.doc_kind, half.research_rank) == ("interim_report", RANK_INTERIM)
        assert half.official_url == half.content_url == (
            "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/S100ZFII.pdf")
        assert by_ref["S100EXTR"].research_rank == RANK_MATERIAL
        assert by_ref["S100BUYB"].research_rank == RANK_ADMINISTRATIVE
        for document in listing.documents:
            assert KEY not in document.official_url

    async def test_unread_window_days_are_reported_as_holes(self, session: Any) -> None:
        fake = FakeEdinet(ISSUER_DAYS)
        company = await _company(session)
        cfg = Settings(**{**CFG.model_dump(), "v3_edinet_index_days_per_run": 5})
        listing = await edinet.list_edinet_disclosures(session, company, cfg=cfg, now=NOW,
                                                       getter=fake.get, index_pause=0)
        # Eleven window days, five read: six holes, and the listing says so.
        assert listing.pages_failed == 6 and "not yet read" in (listing.detail or "")

    async def test_without_a_key_the_source_is_off(self, session: Any) -> None:
        fake = FakeEdinet(ISSUER_DAYS)
        company = await _company(session)
        cfg = Settings(**{**CFG.model_dump(), "edinet_api_key": ""})
        listing = await edinet.list_edinet_disclosures(session, company, cfg=cfg, now=NOW,
                                                       getter=fake.get)
        assert listing.reason == "connector_disabled" and fake.calls == []

    async def test_an_unreadable_code_list_is_a_source_problem(self, session: Any) -> None:
        fake = FakeEdinet(code_list=b"broken")
        company = await _company(session)
        listing = await edinet.list_edinet_disclosures(session, company, cfg=CFG, now=NOW,
                                                       getter=fake.get)
        assert listing.reason == "source_unavailable" and listing.issuer is None

    async def test_a_wrong_name_is_an_identity_refusal(self, session: Any) -> None:
        fake = FakeEdinet(ISSUER_DAYS)
        company = await _company(session, name="Toyota Motor Corporation")
        listing = await edinet.list_edinet_disclosures(session, company, cfg=CFG, now=NOW,
                                                       getter=fake.get)
        assert listing.reason == "identity_unverified" and fake.day_calls() == []


class TestHeadlines:
    def test_an_annual_report_states_its_fiscal_year_end(self) -> None:
        filing = EdinetFiling(doc_id="S1", doc_type_code="120",
                              doc_description="有価証券報告書－第79期",
                              period_start=date(2025, 4, 1), period_end=date(2026, 3, 31))
        head = edinet.filing_headline(filing)
        assert head.startswith("Annual Securities Report for the fiscal year ended "
                               "31 March 2026")
        assert edinet.classify_filing("120")[1:] == ("annual_report", RANK_ANNUAL)

    def test_a_half_year_report_is_read_as_h1_of_the_fiscal_year(self) -> None:
        filing = EdinetFiling(doc_id="S1", doc_type_code="160", doc_description="半期報告書",
                              period_start=date(2026, 4, 1), period_end=date(2027, 3, 31))
        found = detect_document_period(title=edinet.filing_headline(filing),
                                       as_of=date(2026, 11, 13))
        assert (found.period.period_type, found.period.year,
                found.period.ordinal) == ("half", 2027, 1)

    def test_a_quarterly_report_takes_its_quarter_from_the_description(self) -> None:
        filing = EdinetFiling(doc_id="S1", doc_type_code="140",
                              doc_description="四半期報告書－第75期第３四半期",
                              period_start=date(2023, 4, 1), period_end=date(2024, 3, 31))
        assert edinet.filing_headline(filing).startswith("Q3 FY2024 Quarterly Report")

    def test_the_period_comes_from_the_title_only(self) -> None:
        assert is_title_only(source_type="edinet_filing")


# ── The acquisition bridge ─────────────────────────────────────────────────── #


class TestSourceFor:
    @pytest.mark.parametrize("exchange", ["TSE", "TYO", "XTKS", "JP"])
    def test_a_tokyo_listing_is_covered_by_edinet(self, exchange: str) -> None:
        company = Company(ticker="7018", exchange=exchange, name="x")
        assert acq.source_for(company, CFG) == (SOURCE_EDINET, None)

    def test_without_a_key_it_is_disabled(self) -> None:
        company = Company(ticker="7018", exchange="TSE", name="x")
        cfg = Settings(**{**CFG.model_dump(), "edinet_api_key": ""})
        assert acq.source_for(company, cfg) == (None, "connector_disabled")


JA_REPORT = (
    "<html><body><h1>半期報告書</h1>"
    "<p>【表紙】【提出書類】 半期報告書【根拠条文】 金融商品取引法第24条の5第1項"
    "【提出先】 中国財務局長【会社名】 内海造船株式会社"
    "【英訳名】 NAIKAI ZOSEN CORPORATION【代表者の役職氏名】 代表取締役社長</p>"
    "<h2>第一部 企業情報</h2><p>当中間会計期間における売上高は12,340百万円となりました。"
    "受注残高は引き続き高水準で推移しております。新造船の建造は計画どおり進捗しており、"
    "フェリーおよび貨物船の需要は堅調です。</p></body></html>")


class FakeExtractor:
    def __init__(self, body: str):
        self.body = body.encode()
        self.urls: list[tuple[str, tuple[str, ...]]] = []

    async def __call__(self, url: str, *, allowed_domains: tuple[str, ...],
                       title_hint: str | None = None, issuer_context: Any = None,
                       cfg: Any = None, period_policy: str | None = None,
                       published_at: date | None = None, **_: Any) -> Any:
        from app.services.sources import live_fetchers

        self.urls.append((url, allowed_domains))
        fetched = DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                      content_type="text/html", document_type="html",
                                      content=self.body)
        return await live_fetchers._artifact_from_fetch(
            fetched, title=title_hint, original_language=None,
            issuer_context=issuer_context, cfg=cfg, fetch_ms=1,
            period_policy=period_policy, published_at=published_at)


class TestAcquisition:
    async def _listing(self, session: Any) -> Any:
        fake = FakeEdinet(ISSUER_DAYS)
        company = await _company(session)
        return await edinet.list_edinet_disclosures(session, company, cfg=CFG, now=NOW,
                                                    getter=fake.get, index_pause=0)

    async def test_the_official_pdf_becomes_searchable_evidence(self, session: Any) -> None:
        from app.models.extracted_document import ExtractedDocument

        listing = await self._listing(session)
        document = next(d for d in listing.documents if d.document_ref == "S100ZFII")
        extractor = FakeExtractor(JA_REPORT)
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=CFG,
            extractor=extractor)
        assert result.is_ready, result.to_dict()
        assert extractor.urls == [(document.content_url, (edinet.EDINET_DL_HOST,))]
        stored = (await session.execute(select(ExtractedDocument))).scalars().one()
        assert stored.source_type == "edinet_filing"
        assert stored.canonical_url == document.official_url and KEY not in stored.canonical_url
        from app.models.research_document import ResearchDocumentVersion

        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        # H1 of the fiscal year to 31 March 2027, from the listing's own metadata.
        assert version.period_key == "2027-H1"

    async def test_a_document_that_does_not_name_the_issuer_is_not_stored(
            self, session: Any) -> None:
        from app.models.extracted_document import ExtractedDocument

        listing = await self._listing(session)
        document = next(d for d in listing.documents if d.document_ref == "S100ZFII")
        other = JA_REPORT.replace("内海造船株式会社", "別会社株式会社").replace(
            "NAIKAI ZOSEN CORPORATION", "OTHER COMPANY LTD")
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=CFG,
            extractor=FakeExtractor(other))
        assert result.reason == "identity_unverified"
        assert (await session.execute(select(ExtractedDocument))).scalars().all() == []


class TestNamesTheIssuer:
    def _artifact(self, text: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(extraction=SimpleNamespace(
            blocks=[SimpleNamespace(text=text)], excerpts=[], entity_lei_identifiers=[]))

    def _issuer(self, local: str | None) -> VerifiedIssuer:
        return VerifiedIssuer(company_id=uuid.uuid4(), ticker="7018", venue="TSE",
                              name="NAIKAI ZOSEN CORPORATION", source_id=SOURCE_EDINET,
                              identity_basis="test", local_name=local)

    def test_the_japanese_name_is_matched_across_spacing_and_width(self) -> None:
        artifact = self._artifact("【会社名】　内海造船 株式会社　本店")
        assert acq.document_names_issuer(artifact, self._issuer("内海造船株式会社"))

    def test_a_part_of_the_name_is_not_enough(self) -> None:
        artifact = self._artifact("内海造船所の関連会社について")
        assert not acq.document_names_issuer(artifact, self._issuer("内海造船株式会社"))


# ── Safety ─────────────────────────────────────────────────────────────────── #


def test_the_key_is_a_hidden_credential() -> None:
    assert "edinet_api_key" in CREDENTIAL_SETTING_FIELDS
    assert KEY not in repr(CFG)


async def test_the_transport_refuses_any_other_host() -> None:
    status, body, error = await edinet.guarded_get(
        "https://example.com/x", cfg=CFG, max_bytes=10, allowed_host=edinet.EDINET_API_HOST)
    assert (status, body, error) == (None, None, "host_not_allowed")


def test_migration_059_follows_058_and_is_additive() -> None:
    import importlib.util
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "059_add_edinet_filing_index.py")
    spec = importlib.util.spec_from_file_location("m059", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("059", "058")
    source = path.read_text()
    assert "drop_table" in source.split("def downgrade")[1]
    assert "drop_table" not in source.split("def downgrade")[0]


# ── Language: a Japanese filing is labelled Japanese ───────────────────────── #


def test_japanese_content_is_labelled_japanese_not_english() -> None:
    from app.services.sources.language import detect_language_with_confidence

    text = ("EDINET提出書類 内海造船株式会社(E02131) 半期報告書 当中間会計期間における"
            "売上高は12,340百万円となりました。受注残高は引き続き高水準で推移しております。")
    assert detect_language_with_confidence(text) == ("ja", True)


def test_confident_english_keeps_its_label_despite_a_japanese_name() -> None:
    from app.services.sources.language import detect_language_with_confidence

    text = ("The company and its subsidiaries reported that the revenue of the group was "
            "higher than in the prior year, and the board of 内海造船 (ナイカイゾウセン) "
            "expects that the order book will remain strong for the rest of the year.")
    assert detect_language_with_confidence(text)[0] == "en"
