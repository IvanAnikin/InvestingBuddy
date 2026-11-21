"""V3.20 slice 11.3 — OpenDART: a Korean issuer's own filings.

Identity from OpenDART's corp-code list (KRX code AND an agreeing name), a per-company
listing by disclosure type, and the filing's ORIGINAL document (DART XML in a zip)
converted to HTML and read through the shared acquisition bridge. The key can only be
a query parameter: it is passed to the transport as one and never reaches a URL that
is built, stored or returned.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings
from app.models.company import Company
from app.services.sources.disclosure_period_policy import is_title_only
from app.services.sources.disclosures import acquisition as acq
from app.services.sources.disclosures import opendart
from app.services.sources.disclosures.model import (
    RANK_ADMINISTRATIVE,
    RANK_ANNUAL,
    RANK_INTERIM,
    RANK_MATERIAL,
    RANK_ORDINARY,
    RANK_PERIODIC,
    SOURCE_OPENDART,
)
from app.services.sources.document_period import detect_document_period
from tests.test_non_us_primary_docs_connectors import (  # noqa: F401 - autouse fixture
    _index_like_postgres,
)

KEY = "d" * 40
NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
CFG = Settings(
    v3_corpus_enabled=True, primary_document_ingestion_enabled=True,
    report_citation_persistence_enabled=True, v3_artifact_store_backend="memory",
    v3_search_backend="memory", v3_opendart_disclosures_enabled=True, opendart_api_key=KEY,
    v3_disclosure_lookback_days=560,
)
SAMSUNG = "00126380"

CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
<corp_eng_name>SAMSUNG ELECTRONICS CO,.LTD</corp_eng_name><stock_code>005930</stock_code>
<modify_date>20260101</modify_date></list>
<list><corp_code>00999999</corp_code><corp_name>비상장회사</corp_name>
<corp_eng_name>UNLISTED CO</corp_eng_name><stock_code> </stock_code>
<modify_date>20260101</modify_date></list>
<list><corp_code>01515323</corp_code><corp_name>LG에너지솔루션</corp_name>
<corp_eng_name>LG Energy Solution, Ltd.</corp_eng_name><stock_code>373220</stock_code>
<modify_date>20260101</modify_date></list>
</result>"""


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


def _row(rcept_no: str, report_nm: str, rcept_dt: str, *, corp_code: str = SAMSUNG
         ) -> dict[str, Any]:
    return {"corp_cls": "Y", "corp_name": "삼성전자", "corp_code": corp_code,
            "stock_code": "005930", "report_nm": report_nm, "rcept_no": rcept_no,
            "flr_nm": "삼성전자", "rcept_dt": rcept_dt, "rm": ""}


def _list(*rows: dict[str, Any], status: str = "000", total_page: int = 1) -> bytes:
    if status != "000":
        return json.dumps({"status": status, "message": "x"}).encode()
    return json.dumps({"status": "000", "message": "정상", "page_no": 1, "page_count": 100,
                       "total_count": len(rows), "total_page": total_page,
                       "list": list(rows)}).encode()


DART_DOC = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<DOCUMENT-NAME ACODE="11013">분기보고서</DOCUMENT-NAME>
<COMPANY-NAME AREGCIK="{corp}">삼성전자주식회사</COMPANY-NAME>
<SUMMARY><EXTRACTION ACODE="RPT_TP">3</EXTRACTION></SUMMARY>
<BODY><COVER><COVER-TITLE ATOC="Y">분 기 보 고 서</COVER-TITLE>
<P>회사명 : 삼성전자주식회사 대표이사 : 홍길동 본점 소재지 : 경기도 수원시 영통구 삼성로 129</P>
</COVER><PGBRK/>
<LIBRARY><SECTION-1><TITLE ATOC="Y">II. 사업의 내용</TITLE>
<P>당사는 메모리 반도체, 스마트폰, 디스플레이 패널 등을 생산하며, 당 분기 연결 기준 매출액은&cr;
86조 617억원으로 전년 동기 대비 증가하였습니다. 메모리 사업은 고대역폭 메모리 수요 확대에
힘입어 견조한 실적을 기록하였습니다.</P>
<P>당사의 사업부문은 DX(Device eXperience) 부문과 DS(Device Solutions) 부문으로 구분되며,
DX 부문은 TV, 모니터, 냉장고, 세탁기, 에어컨, 스마트폰, 네트워크시스템, 컴퓨터 등을 생산ㆍ판매하고,
DS 부문은 DRAM, NAND Flash, 모바일AP 등의 제품을 생산ㆍ판매하고 있습니다. 당 분기 중
메모리 반도체 가격은 서버용 수요 증가로 상승하였으며, 당사는 고부가 제품 중심으로 판매를
확대하였습니다. 또한 파운드리 사업은 첨단 공정의 수율 개선과 신규 고객 확보를 추진하고
있으며, 디스플레이 사업은 스마트폰용 OLED 패널 판매가 증가하였습니다. 회사는 향후에도
기술 경쟁력 강화를 위하여 연구개발 투자를 지속할 계획입니다.</P>
<TABLE-GROUP><TABLE><COLGROUP><COL WIDTH="100"/></COLGROUP><THEAD><TR><TH>구분</TH>
<TH COLSPAN="2">금액</TH></TR></THEAD><TBODY><TR><TD>매출액</TD><TE>86,061,700</TE>
<TU>백만원</TU></TR></TBODY></TABLE></TABLE-GROUP>
<IMAGE><IMG>x.jpg</IMG><IMG-CAPTION>그림</IMG-CAPTION></IMAGE>
</SECTION-1></LIBRARY></BODY></DOCUMENT>"""


class FakeDart:
    """OpenDART's API: corp codes, company, list (per type and page), document."""

    def __init__(self, *, lists: dict[str, list[bytes]] | None = None,
                 corp_zip: bytes | None = None, document: bytes | None = None,
                 acc_mt: str = "12"):
        self.lists = lists if lists is not None else {}
        self.corp_zip = _zip({"CORPCODE.xml": CORP_XML}) if corp_zip is None else corp_zip
        self.document = document
        self.acc_mt = acc_mt
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, *, params: dict[str, str] | None = None,
                  headers: dict[str, str] | None = None, cfg: Any = None,
                  max_bytes: int = 0, allowed_host: str = "",
                  timeout_seconds: float | None = None) -> Any:
        params = dict(params or {})
        self.calls.append({"url": url, "params": params, "host": allowed_host,
                           "headers": dict(headers or {}), "timeout": timeout_seconds})
        assert allowed_host == opendart.OPENDART_HOST
        if url == opendart.CORP_CODE_URL:
            return 200, self.corp_zip, None
        if url == opendart.COMPANY_URL:
            return 200, json.dumps({"status": "000", "acc_mt": self.acc_mt}).encode(), None
        if url == opendart.LIST_URL:
            pages = self.lists.get(params["pblntf_ty"], [_list(status="013")])
            index = int(params["page_no"]) - 1
            return 200, pages[index] if index < len(pages) else _list(status="013"), None
        if url == opendart.DOCUMENT_URL:
            if self.document is None:
                return 200, json.dumps({"status": "014", "message": "없음"}).encode(), None
            return 200, self.document, None
        raise AssertionError(url)

    def calls_to(self, url: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["url"] == url]


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
def _fresh_corp_code_cache():  # noqa: ANN202
    opendart._CORP_CODE_CACHE.clear()
    yield
    opendart._CORP_CODE_CACHE.clear()


async def _company(session: Any, ticker: str = "005930", exchange: str = "KO",
                   name: str = "Samsung Electronics Co., Ltd.") -> Company:
    company = Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name,
                      status="new")
    session.add(company)
    await session.flush()
    return company


SAMSUNG_LISTS = {
    "A": [_list(_row("20261116001482", "분기보고서 (2026.09)", "20261116"),
                _row("20260814003699", "반기보고서 (2026.06)", "20260814"),
                _row("20260310002820", "사업보고서 (2025.12)", "20260310"),
                _row("20260320000111", "[기재정정]사업보고서 (2025.12)", "20260320"),
                _row("20260101000999", "분기보고서 (2025.09)", "20260101",
                     corp_code="00000001"),
                _row("bad", "분기보고서 (2025.09)", "20251114"))],
    "B": [_list(_row("20261001000555", "주요사항보고서(자기주식취득결정)", "20261001"))],
    "I": [_list(_row("20261008000777", "연결재무제표기준영업(잠정)실적(공정공시)", "20261008"),
                _row("20261020000888", "기업설명회(IR)개최(안내공시)", "20261020"))],
}


# ── Identity ───────────────────────────────────────────────────────────────── #


class TestCorpCodes:
    def test_only_listed_companies_are_read(self) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)
        assert [r.corp_code for r in rows] == [SAMSUNG, "01515323"]
        assert rows[0].name_ko == "삼성전자" and rows[0].stock_code == "005930"

    def test_the_zip_is_read_and_a_wrong_zip_refused(self) -> None:
        assert len(opendart.parse_corp_code_zip(_zip({"CORPCODE.xml": CORP_XML}))) == 2
        assert opendart.parse_corp_code_zip(_zip({"a.xml": CORP_XML, "b.xml": ""})) == []
        assert opendart.parse_corp_code_zip(b"PK-not-a-zip") == []

    @pytest.mark.parametrize("ticker", ["005930", "005930.KS", "005930.KQ", "KRX:005930"])
    def test_the_code_and_an_agreeing_name_match(self, ticker: str) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)
        row, why = opendart.match_issuer(rows, ticker=ticker, name="Samsung Electronics")
        assert row is not None and row.corp_code == SAMSUNG and why == ""

    def test_the_korean_name_matches(self) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)
        assert opendart.match_issuer(rows, ticker="005930", name="삼성전자")[0] is not None

    def test_a_disagreeing_name_is_refused(self) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)
        row, why = opendart.match_issuer(rows, ticker="005930", name="Hyundai Motor")
        assert row is None and "SAMSUNG ELECTRONICS" in why

    @pytest.mark.parametrize("ticker", ["AAPL", "7018", "", None])
    def test_a_ticker_that_is_not_a_krx_code_is_refused(self, ticker: Any) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)
        assert opendart.match_issuer(rows, ticker=ticker, name="Samsung")[0] is None


# ── Listing ────────────────────────────────────────────────────────────────── #


class TestHeldCorpCodes:
    """The list is a 3.6 MB download measured at 499 s: held, and read by stock code."""

    async def test_a_download_is_held_and_the_next_run_reads_the_table(
            self, session: Any) -> None:
        from app.models.opendart_corp_code import OpendartCorpCode

        fake = FakeDart(lists=SAMSUNG_LISTS)
        company = await _company(session)
        await opendart.list_opendart_disclosures(session, company, cfg=CFG, now=NOW,
                                                 getter=fake.get)
        held = (await session.execute(select(OpendartCorpCode))).scalars().all()
        assert {h.corp_code for h in held} == {SAMSUNG, "01515323"}
        opendart._CORP_CODE_CACHE.clear()
        again = FakeDart(lists=SAMSUNG_LISTS)
        listing = await opendart.list_opendart_disclosures(
            session, company, cfg=CFG, now=NOW + timedelta(days=3), getter=again.get)
        assert listing.issuer is not None and listing.issuer.source_ref == SAMSUNG
        assert again.calls_to(opendart.CORP_CODE_URL) == []

    async def test_a_fresh_held_list_without_the_code_is_an_answer(self, session: Any
                                                                    ) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)[1:]  # LG Energy Solution only
        await opendart.store_corp_codes(session, rows, now=NOW)
        fake = FakeDart(lists=SAMSUNG_LISTS)
        listing = await opendart.list_opendart_disclosures(
            session, await _company(session), cfg=CFG, now=NOW, getter=fake.get)
        assert listing.reason == "identity_unverified" and "005930" in listing.detail
        assert fake.calls == []

    async def test_a_stale_list_is_refreshed(self, session: Any) -> None:
        rows = opendart.parse_corp_code_xml(CORP_XML)[1:]
        await opendart.store_corp_codes(session, rows, now=NOW - timedelta(days=45))
        fake = FakeDart(lists=SAMSUNG_LISTS)
        listing = await opendart.list_opendart_disclosures(
            session, await _company(session), cfg=CFG, now=NOW, getter=fake.get)
        assert len(fake.calls_to(opendart.CORP_CODE_URL)) == 1
        assert listing.issuer is not None and listing.issuer.source_ref == SAMSUNG

    async def test_a_stale_list_still_answers_when_the_download_fails(
            self, session: Any) -> None:
        await opendart.store_corp_codes(session, opendart.parse_corp_code_xml(CORP_XML),
                                        now=NOW - timedelta(days=45))
        fake = FakeDart(lists=SAMSUNG_LISTS, corp_zip=b"")
        listing = await opendart.list_opendart_disclosures(
            session, await _company(session), cfg=CFG, now=NOW, getter=fake.get)
        assert listing.issuer is not None and listing.documents

    async def test_every_request_gets_the_slow_host_deadline(self, session: Any) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS)
        await opendart.list_opendart_disclosures(session, await _company(session), cfg=CFG,
                                                 now=NOW, getter=fake.get)
        assert {c["timeout"] for c in fake.calls} == {240.0}


class TestListPage:
    def test_no_data_is_an_empty_usable_answer(self) -> None:
        assert opendart.parse_list_page(_list(status="013"), corp_code=SAMSUNG)[0] == []

    @pytest.mark.parametrize("payload", [_list(status="010"), _list(status="020"), b"",
                                         b"<html>"])
    def test_a_refusal_is_not_a_listing(self, payload: bytes) -> None:
        assert opendart.parse_list_page(payload, corp_code=SAMSUNG)[0] is None

    def test_another_companys_row_and_a_bad_id_are_refused(self) -> None:
        rows, refused, _pages, _status = opendart.parse_list_page(
            SAMSUNG_LISTS["A"][0], corp_code=SAMSUNG)
        assert refused == 2 and len(rows or []) == 4


class TestClassification:
    @pytest.mark.parametrize(("ty", "name", "kind", "rank"), [
        ("A", "사업보고서 (2025.12)", "annual_report", RANK_ANNUAL),
        ("A", "반기보고서 (2026.06)", "interim_report", RANK_INTERIM),
        ("A", "분기보고서 (2026.09)", "interim_report", RANK_PERIODIC),
        ("A", "[기재정정]사업보고서 (2025.12)", "other", RANK_PERIODIC),
        ("B", "주요사항보고서(자기주식취득결정)", "other", RANK_MATERIAL),
        ("B", "[기재정정]주요사항보고서(유상증자결정)", "other", RANK_ORDINARY),
        ("I", "연결재무제표기준영업(잠정)실적(공정공시)", "results_release", RANK_PERIODIC),
        ("I", "기업설명회(IR)개최(안내공시)", "other", RANK_ADMINISTRATIVE),
    ])
    def test_kind_and_rank(self, ty: str, name: str, kind: str, rank: int) -> None:
        _category, doc_kind, got_rank, _label = opendart.classify_filing(ty, name)
        assert (doc_kind, got_rank) == (kind, rank)


class TestHeadlines:
    def _period(self, headline: str) -> tuple[Any, Any, Any]:
        found = detect_document_period(title=headline, as_of=date(2026, 11, 17))
        return found.period.period_type, found.period.year, found.period.ordinal

    def test_a_quarterly_report_of_a_december_year(self) -> None:
        head = opendart.filing_headline("A", "분기보고서 (2026.09)", fy_end_month=12)
        assert head.startswith("Q3 FY2026 Quarterly Report (period to September 2026)")
        assert self._period(head) == ("quarter", 2026, 3)

    def test_a_half_year_report_of_a_december_year(self) -> None:
        head = opendart.filing_headline("A", "반기보고서 (2026.06)", fy_end_month=12)
        assert head.startswith("H1 FY2026 Half-year Report")
        assert self._period(head) == ("half", 2026, 1)

    def test_a_march_year_end_names_the_fiscal_year_it_ends_in(self) -> None:
        head = opendart.filing_headline("A", "분기보고서 (2026.06)", fy_end_month=3)
        assert head.startswith("Q1 FY2027 Quarterly Report")

    def test_an_unknown_year_end_names_no_quarter(self) -> None:
        head = opendart.filing_headline("A", "분기보고서 (2026.09)", fy_end_month=None)
        assert head.startswith("Quarterly Report (period to September 2026)")
        assert self._period(head) == (None, None, None)

    def test_an_annual_report_and_an_amendment(self) -> None:
        assert opendart.filing_headline("A", "사업보고서 (2025.12)", fy_end_month=12) \
            .startswith("Annual Business Report for the fiscal year ended December 2025")
        amended = opendart.filing_headline("A", "[기재정정]사업보고서 (2025.12)",
                                           fy_end_month=12)
        assert amended.startswith("Amended Annual Business Report — ")

    def test_the_period_comes_from_the_title_only(self) -> None:
        assert is_title_only(source_type="opendart_filing")


class TestListing:
    async def test_the_issuers_filings_are_listed_newest_first(self, session: Any) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS)
        company = await _company(session)
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG,
                                                           now=NOW, getter=fake.get)
        assert [d.document_ref for d in listing.documents] == [
            "20261116001482", "20261020000888", "20261008000777", "20261001000555",
            "20260814003699", "20260320000111", "20260310002820"]
        assert listing.refused == 2
        assert listing.issuer is not None and listing.issuer.source_ref == SAMSUNG
        assert listing.issuer.local_name == "삼성전자" and listing.issuer.venue == "KO"
        first = listing.documents[0]
        assert first.official_url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261116001482"
        assert first.headline.startswith("Q3 FY2026 Quarterly Report")

    async def test_the_key_is_only_ever_a_parameter(self, session: Any) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS)
        company = await _company(session)
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG,
                                                           now=NOW, getter=fake.get)
        for call in fake.calls:
            assert KEY not in call["url"] and call["params"][opendart.KEY_PARAM] == KEY
        for document in listing.documents:
            assert KEY not in document.official_url + (document.content_url or "")
        assert KEY not in json.dumps([d.to_item() for d in listing.documents])

    async def test_each_type_is_read_with_its_window(self, session: Any) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS)
        company = await _company(session)
        await opendart.list_opendart_disclosures(session, company, cfg=CFG, now=NOW,
                                                 getter=fake.get)
        calls = fake.calls_to(opendart.LIST_URL)
        assert [c["params"]["pblntf_ty"] for c in calls] == ["A", "B", "I"]
        assert {c["params"]["bgn_de"] for c in calls} == {"20250506"}
        assert {c["params"]["corp_code"] for c in calls} == {SAMSUNG}

    async def test_pages_are_followed_and_bounded(self, session: Any) -> None:
        many = [_list(_row(f"2026100100{i:04d}", "주요사항보고서(x)", "20261001"),
                      total_page=9) for i in range(9)]
        fake = FakeDart(lists={"B": many})
        company = await _company(session)
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG,
                                                           now=NOW, getter=fake.get)
        b_calls = [c for c in fake.calls_to(opendart.LIST_URL)
                   if c["params"]["pblntf_ty"] == "B"]
        assert len(b_calls) == opendart.MAX_PAGES_PER_TYPE
        assert len(listing.documents) == opendart.MAX_PAGES_PER_TYPE

    async def test_a_refused_key_stops_and_says_so(self, session: Any) -> None:
        fake = FakeDart(lists={"A": [_list(status="010")]})
        company = await _company(session)
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG,
                                                           now=NOW, getter=fake.get)
        assert listing.reason == "source_unavailable" and "refused the key" in listing.detail
        assert len(fake.calls_to(opendart.LIST_URL)) == 1

    async def test_without_a_key_nothing_is_requested(self, session: Any) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS)
        company = await _company(session)
        cfg = Settings(**{**CFG.model_dump(), "opendart_api_key": ""})
        listing = await opendart.list_opendart_disclosures(session, company, cfg=cfg,
                                                           now=NOW, getter=fake.get)
        assert listing.reason == "connector_disabled" and fake.calls == []

    async def test_a_wrong_name_lists_nothing(self, session: Any) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS)
        company = await _company(session, name="Hyundai Motor Company")
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG,
                                                           now=NOW, getter=fake.get)
        assert listing.reason == "identity_unverified"
        assert fake.calls_to(opendart.LIST_URL) == []

    async def test_an_unreadable_corp_code_list_is_a_source_problem(self, session: Any
                                                                     ) -> None:
        fake = FakeDart(corp_zip=json.dumps({"status": "010"}).encode())
        company = await _company(session)
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG,
                                                           now=NOW, getter=fake.get)
        assert listing.reason == "source_unavailable" and listing.issuer is None


# ── The document ───────────────────────────────────────────────────────────── #


class TestConversion:
    def test_dart_markup_becomes_plain_html(self) -> None:
        page = opendart.dart_xml_to_html(DART_DOC.format(corp=SAMSUNG))
        assert "<h2>II. 사업의 내용</h2>" in page
        assert '<th colspan="2">금액</th>' in page
        assert "<td>86,061,700</td>" in page and "<td>백만원</td>" in page
        assert "<br/>" in page and "<hr/>" in page
        assert "RPT_TP" not in page and "x.jpg" not in page and "그림" not in page
        assert "SECTION-1" not in page and "LIBRARY" not in page

    def test_the_main_member_is_chosen(self) -> None:
        payload = _zip({"20261116001482.xml": DART_DOC.format(corp=SAMSUNG),
                        "20261116001482_00760.xml": "<DOCUMENT>attachment</DOCUMENT>"})
        text = opendart.main_document_xml(payload, rcept_no="20261116001482")
        assert "분기보고서" in text and "attachment" not in text

    def test_a_zip_without_the_main_member_is_unavailable(self) -> None:
        with pytest.raises(opendart.DartDocumentUnavailable):
            opendart.main_document_xml(_zip({"other.xml": "x"}), rcept_no="20261116001482")


class TestAcquisition:
    async def _listing(self, session: Any, fake: FakeDart,
                       monkeypatch: pytest.MonkeyPatch) -> Any:
        monkeypatch.setattr(opendart, "guarded_get", fake.get)
        company = await _company(session)
        return await opendart.list_opendart_disclosures(session, company, cfg=CFG, now=NOW)

    async def test_the_original_document_becomes_korean_searchable_evidence(
            self, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.models.extracted_document import ExtractedDocument
        from app.models.research_chunk import ResearchDocumentChunk
        from app.models.research_document import ResearchDocumentVersion

        fake = FakeDart(lists=SAMSUNG_LISTS, document=_zip(
            {"20261116001482.xml": DART_DOC.format(corp=SAMSUNG)}))
        listing = await self._listing(session, fake, monkeypatch)
        document = listing.documents[0]
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=CFG)
        assert result.is_ready, result.to_dict()
        doc_calls = fake.calls_to(opendart.DOCUMENT_URL)
        assert [c["params"]["rcept_no"] for c in doc_calls] == ["20261116001482"]
        assert doc_calls[0]["timeout"] == 240.0
        stored = (await session.execute(select(ExtractedDocument))).scalars().one()
        assert stored.source_type == "opendart_filing"
        assert stored.canonical_url == document.official_url
        version = (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        assert version.period_key == "2026-Q3"
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert chunks and {c.language for c in chunks} == {"ko"}
        # The key is nowhere in what was stored.
        for row in (stored, version, *chunks):
            assert KEY not in json.dumps({k: str(v) for k, v in vars(row).items()
                                          if not k.startswith("_")})

    async def test_a_second_run_reuses_the_held_filing(
            self, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS, document=_zip(
            {"20261116001482.xml": DART_DOC.format(corp=SAMSUNG)}))
        listing = await self._listing(session, fake, monkeypatch)
        for _ in range(2):
            result = await acq.ensure_disclosure_evidence(
                session, issuer=listing.issuer, document=listing.documents[0], cfg=CFG)
            assert result.is_ready
        assert result.reused is True
        assert len(fake.calls_to(opendart.DOCUMENT_URL)) == 1

    async def test_a_document_of_another_company_is_refused(
            self, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.models.extracted_document import ExtractedDocument

        fake = FakeDart(lists=SAMSUNG_LISTS, document=_zip(
            {"20261116001482.xml": DART_DOC.format(corp="00000001")}))
        listing = await self._listing(session, fake, monkeypatch)
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=listing.documents[0], cfg=CFG)
        assert result.reason == "primary_document_unavailable"
        assert result.notes == ["DartIdentityMismatch"]
        assert (await session.execute(select(ExtractedDocument))).scalars().all() == []

    async def test_an_api_refusal_instead_of_a_zip_is_unavailable(
            self, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeDart(lists=SAMSUNG_LISTS, document=None)
        listing = await self._listing(session, fake, monkeypatch)
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=listing.documents[0], cfg=CFG)
        assert result.reason == "primary_document_unavailable"
        assert result.notes == ["DartDocumentUnavailable"]


class TestSourceFor:
    @pytest.mark.parametrize("exchange", ["KO", "KQ", "KS", "KRX", "KOSPI", "KOSDAQ"])
    def test_a_korean_listing_is_covered_by_opendart(self, exchange: str) -> None:
        company = Company(ticker="005930", exchange=exchange, name="x")
        assert acq.source_for(company, CFG) == (SOURCE_OPENDART, None)

    def test_without_a_key_it_is_disabled(self) -> None:
        company = Company(ticker="005930", exchange="KO", name="x")
        cfg = Settings(**{**CFG.model_dump(), "opendart_api_key": ""})
        assert acq.source_for(company, cfg) == (None, "connector_disabled")


@pytest.mark.parametrize(("url", "held"), [
    ("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261116001482", True),
    ("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202611160014821", False),
    ("https://dart.fss.or.kr/dsaf001/main.do?page=20261116001482", False),
])
def test_the_viewer_address_names_its_filing(url: str, held: bool) -> None:
    from app.services.corpus.filing_evidence import url_has_document_segment

    assert url_has_document_segment(url, "20261116001482") is held


def test_the_key_is_a_hidden_credential() -> None:
    assert "opendart_api_key" in CREDENTIAL_SETTING_FIELDS
    assert KEY not in repr(CFG)


def test_a_logged_request_url_never_shows_the_key() -> None:
    from app.core.log_redaction import redact_text, redact_url

    url = f"{opendart.LIST_URL}?crtfc_key={KEY}&corp_code={SAMSUNG}"
    line = redact_text(f'HTTP Request: GET {url} "HTTP/1.1 200 OK"')
    assert KEY not in line and f"corp_code={SAMSUNG}" in line
    assert KEY not in redact_url(url)


def test_korean_content_is_labelled_korean() -> None:
    from app.services.sources.language import detect_language_with_confidence

    text = ("당사는 메모리 반도체, 스마트폰, 디스플레이 패널 등을 생산하며, 당 분기 연결 기준 "
            "매출액은 86조 617억원으로 전년 동기 대비 증가하였습니다.")
    assert detect_language_with_confidence(text) == ("ko", True)
