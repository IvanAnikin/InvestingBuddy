"""V3.20 slice 11.4 — TWSE material announcements (重大訊息).

TWSE's open-data feed ``t187ap04_L`` states one announcement day, each row a complete
announcement. Every read is HELD; a listing reads the held rows for a company verified on
TWSE's own directory (code AND an agreeing name); the announcement's text — rendered
from the held row, every field escaped — goes through the shared acquisition bridge.
Rows are attributed by the feed's own company-code field, never by a name in the text.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.company import Company
from app.models.twse_material_announcement import TwseMaterialAnnouncement
from app.services.discovery import directories as d
from app.services.sources.disclosure_period_policy import is_title_only
from app.services.sources.disclosures import acquisition as acq
from app.services.sources.disclosures import twse_material as tm
from app.services.sources.disclosures.model import (
    RANK_MATERIAL,
    SOURCE_TWSE_MATERIAL,
)
from app.services.sources.document_fetcher import DocumentFetchResult
from tests.test_non_us_primary_docs_connectors import (  # noqa: F401 - autouse fixture
    _index_like_postgres,
)

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
CFG = Settings(
    v3_corpus_enabled=True, primary_document_ingestion_enabled=True,
    report_citation_persistence_enabled=True, v3_artifact_store_backend="memory",
    v3_search_backend="memory", v3_twse_material_info_enabled=True,
    v3_directory_twse_enabled=True, v3_disclosure_lookback_days=560,
)

BODY = ("1.事實發生日:115/11/14\n2.公司名稱:世紀能源設備股份有限公司\n"
        "3.與公司關係(請輸入本公司或子公司):本公司\n4.相互持股比例:不適用\n"
        "5.發生緣由:本公司董事會通過取得離岸風電水下基礎製造設備，交易總金額\n"
        "新台幣1,250,000仟元。\n6.因應措施:無\n7.其他應敘明事項:無")


def _row(code: str = "2072", name: str = "世紀能源", day: str = "1151116",
         clock: str = "70003", subject: str = "公告本公司董事會決議取得\r\n機器設備",
         body: str = BODY, clause: str = "第20款", fact: str = "1151114") -> dict[str, str]:
    # The feed's own keys, including the measured stray space in "主旨 ".
    return {"出表日期": "1151117", "發言日期": day, "發言時間": clock, "公司代號": code,
            "公司名稱": name, "主旨 ": subject, "符合條款": clause, "事實發生日": fact,
            "說明": body}


FEED = [
    _row(),
    _row(code="2330", name="台積電", clock="173012",
         subject="代子公司公告取得使用權資產", body="1.標的物之名稱及性質:廠房\n2.交易金額:新台幣3,000,000仟元"),
    _row(code="<b>", name="x"),            # not a code
    _row(day="1151340"),                   # not a date
    _row(clock="246000"),                  # not a time
    _row(code="1101", name="", clock="80000"),  # no name
]

DIRECTORY = json.dumps([
    {"出表日期": "1151116", "公司代號": "2072", "公司名稱": "世紀能源設備股份有限公司",
     "公司簡稱": "世紀能源", "外國企業註冊地國": "", "英文簡稱": "Century Energy",
     "已發行普通股數或TDR原股發行股數": "100000000"},
    {"出表日期": "1151116", "公司代號": "2330", "公司名稱": "台灣積體電路製造股份有限公司",
     "公司簡稱": "台積電", "外國企業註冊地國": "", "英文簡稱": "TSMC",
     "已發行普通股數或TDR原股發行股數": "1000000000"},
], ensure_ascii=False)


async def directory_fetcher(url: str, **_kw: Any) -> DocumentFetchResult:
    if not url.endswith("/opendata/t187ap03_L"):
        return DocumentFetchResult(requested_url=url, error="nope", failure_code="http_404")
    return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                               content_type="application/json", document_type="text",
                               content=DIRECTORY.encode())


class FakeFeed:
    def __init__(self, rows: list[dict[str, str]] | None = None, *, status: int = 200,
                 body: bytes | None = None, error: str | None = None) -> None:
        self.body = body if body is not None else json.dumps(
            rows if rows is not None else FEED, ensure_ascii=False).encode()
        self.status, self.error = status, error
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kw: Any) -> tuple[int | None, bytes | None, str | None]:
        self.calls.append({"url": url, **kw})
        assert url == tm.FEED_URL and kw["allowed_host"] == tm.TWSE_OPENAPI_HOST
        if self.error:
            return None, None, self.error
        return self.status, self.body, None


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
def _fresh() -> Any:
    d.reset_cache()
    tm._last_refresh.clear()
    yield
    d.reset_cache()
    tm._last_refresh.clear()


async def _company(session: Any, ticker: str = "2072", name: str = "Century Energy",
                   exchange: str = "TW") -> Company:
    company = Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name,
                      status="new")
    session.add(company)
    await session.flush()
    return company


async def _listing(session: Any, fake: FakeFeed, **kw: Any) -> Any:
    company = kw.pop("company", None) or await _company(session)
    return await tm.list_twse_announcements(session, company, cfg=CFG, getter=fake.get,
                                            fetcher=directory_fetcher, now=NOW, **kw)


# ── Parsing ────────────────────────────────────────────────────────────────── #


class TestParse:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("1151116", date(2026, 11, 16)), ("991231", date(2010, 12, 31)),
        ("1150229", None), ("20261116", None), ("", None), (None, None),
    ])
    def test_roc_dates(self, raw: Any, expected: date | None) -> None:
        assert tm.roc_date(raw) == expected

    @pytest.mark.parametrize(("raw", "expected"), [
        ("70003", (7, 0, 3)), ("92016", (9, 20, 16)), ("173012", (17, 30, 12)),
        ("0", (0, 0, 0)), ("246000", None), ("9:20", None), ("1234567", None),
    ])
    def test_announcement_times_lose_their_leading_zeros(self, raw: str,
                                                         expected: Any) -> None:
        assert tm.announcement_time(raw) == expected

    def test_only_attributable_dated_rows_are_read(self) -> None:
        rows, refused = tm.parse_feed(json.dumps(FEED, ensure_ascii=False).encode())
        assert rows is not None and refused == 4
        assert [r.code for r in rows] == ["2072", "2330"]

    def test_a_row_states_its_own_time_in_taipei(self) -> None:
        row = tm.parse_row(_row())
        assert row is not None
        assert row.announced_at == datetime(2026, 11, 15, 23, 0, 3, tzinfo=timezone.utc)
        assert row.fact_date == date(2026, 11, 14) and row.clause == "第20款"
        assert row.ref.startswith("2072-20261116-070003-") and len(row.ref) == 31
        assert "\r" not in row.subject

    def test_a_chinese_line_wrap_is_not_a_space_in_the_headline(self) -> None:
        row = tm.parse_row(_row(subject="公告本公司名稱由「甲」更名\r\n為「乙」 (English\nwrap)"))
        assert row is not None and row.headline == "公告本公司名稱由「甲」更名為「乙」 (English wrap)"

    def test_the_ref_is_stable_and_changes_with_the_content(self) -> None:
        a, b = tm.parse_row(_row()), tm.parse_row(_row())
        c = tm.parse_row(_row(body=BODY + "\n8.更正"))
        assert a and b and c and a.ref == b.ref and a.ref != c.ref

    @pytest.mark.parametrize("payload", [b"", b"not json", b'{"a": 1}', b"\xff\xfe"])
    def test_a_payload_that_is_not_the_feed_is_refused(self, payload: bytes) -> None:
        assert tm.parse_feed(payload)[0] is None

    def test_control_characters_are_dropped(self) -> None:
        row = tm.parse_row(_row(body="1.說明\x00\x1b[31m紅\x07"))
        assert row is not None and row.body == "1.說明[31m紅"

    @pytest.mark.parametrize(("ticker", "code"), [
        ("2072", "2072"), ("2072.TW", "2072"), ("TWSE:2330", "2330"), ("00878", "00878"),
        ("AAPL", ""), ("005930.KS", "005930"), ("12", ""), (None, ""),
    ])
    def test_twse_codes(self, ticker: Any, code: str) -> None:
        assert tm.twse_code(ticker) == code


# ── Holding the feed ───────────────────────────────────────────────────────── #


class TestHolding:
    async def test_a_read_is_held_and_a_reread_adds_nothing(self, session: Any) -> None:
        fake = FakeFeed()
        first = await tm.refresh_feed(session, cfg=CFG, getter=fake.get, now=NOW)
        assert first == {"read": True, "stored": 2, "rows": 2, "refused": 4, "error": None}
        again = await tm.refresh_feed(session, cfg=CFG, getter=fake.get, now=NOW, force=True)
        assert again["stored"] == 0 and again["rows"] == 2
        held = (await session.execute(select(TwseMaterialAnnouncement))).scalars().all()
        assert {h.company_code for h in held} == {"2072", "2330"}

    async def test_one_process_reads_the_feed_once_an_hour(self, session: Any) -> None:
        fake = FakeFeed()
        await tm.refresh_feed(session, cfg=CFG, getter=fake.get, now=NOW)
        skipped = await tm.refresh_feed(session, cfg=CFG, getter=fake.get, now=NOW)
        assert skipped["read"] is False and skipped["skipped"] == "read recently"
        assert len(fake.calls) == 1

    async def test_a_failed_read_is_said_and_retried(self, session: Any) -> None:
        fake = FakeFeed(error="timeout")
        outcome = await tm.refresh_feed(session, cfg=CFG, getter=fake.get, now=NOW)
        assert outcome["read"] is False and outcome["error"] == "timeout"
        await tm.refresh_feed(session, cfg=CFG, getter=fake.get, now=NOW)
        assert len(fake.calls) == 2

    async def test_a_corrected_announcement_is_a_second_row(self, session: Any) -> None:
        await tm.refresh_feed(session, cfg=CFG, getter=FakeFeed([_row()]).get, now=NOW)
        corrected = FakeFeed([_row(body=BODY + "\n8.更正前金額誤植")])
        await tm.refresh_feed(session, cfg=CFG, getter=corrected.get, now=NOW, force=True)
        held = await tm.held_announcements(session, "2072", since=NOW - timedelta(days=9))
        assert len(held) == 2

    async def test_history_accumulates_across_days(self, session: Any) -> None:
        await tm.refresh_feed(session, cfg=CFG, getter=FakeFeed([_row()]).get, now=NOW)
        await tm.refresh_feed(session, cfg=CFG, now=NOW, force=True,
                              getter=FakeFeed([_row(day="1151117", clock="90000")]).get)
        held = await tm.held_announcements(session, "2072", since=NOW - timedelta(days=9))
        assert [h.announced_at.day for h in held] == [17, 15]


# ── Identity and listing ───────────────────────────────────────────────────── #


class TestListing:
    async def test_a_verified_company_lists_its_own_held_rows(self, session: Any) -> None:
        listing = await _listing(session, FakeFeed())
        assert listing.reason is None and listing.requests == 2
        assert listing.issuer.source_ref == "2072" and listing.issuer.venue == "TW"
        assert listing.issuer.local_name == "世紀能源"
        assert "t187ap03_L" in listing.issuer.identity_basis
        [document] = listing.documents
        assert document.source_id == SOURCE_TWSE_MATERIAL
        assert document.research_rank == RANK_MATERIAL and document.doc_kind == "other"
        assert document.venue_category == "重大訊息 第20款"
        assert document.official_url == (f"{tm.FEED_URL}?announcement={document.document_ref}")
        assert document.headline == "公告本公司董事會決議取得機器設備"

    async def test_another_companys_rows_are_never_listed(self, session: Any) -> None:
        listing = await _listing(session, FakeFeed())
        assert all(doc.document_ref.startswith("2072-") for doc in listing.documents)

    async def test_a_disagreeing_name_is_refused(self, session: Any) -> None:
        company = await _company(session, name="Hon Hai Precision")
        listing = await _listing(session, FakeFeed(), company=company)
        assert listing.issuer is None and listing.reason == "identity_unverified"
        assert listing.documents == []

    async def test_a_ticker_that_is_not_a_twse_code_is_refused(self, session: Any) -> None:
        company = await _company(session, ticker="AAPL")
        listing = await _listing(session, FakeFeed(), company=company)
        assert listing.reason == "identity_unverified"

    async def test_nothing_held_is_not_sourced_and_says_why(self, session: Any) -> None:
        listing = await _listing(session, FakeFeed([_row(code="2330", name="台積電")]))
        assert listing.issuer is not None and listing.documents == []
        assert listing.reason == "data_not_sourced" and "first read" in (listing.detail or "")

    async def test_a_failed_read_still_lists_held_rows_and_marks_the_hole(
            self, session: Any) -> None:
        await tm.refresh_feed(session, cfg=CFG, getter=FakeFeed().get, now=NOW)
        tm._last_refresh.clear()
        listing = await _listing(session, FakeFeed(error="http_503"))
        assert len(listing.documents) == 1 and listing.pages_failed == 1
        assert "held rows only" in (listing.detail or "")

    async def test_rows_outside_the_window_are_not_listed(self, session: Any) -> None:
        old = _row(day="1130101")
        listing = await _listing(session, FakeFeed([old, _row()]))
        assert len(listing.documents) == 1

    async def test_off_is_off(self, session: Any) -> None:
        company = await _company(session)
        for flags in ({"v3_twse_material_info_enabled": False},
                      {"v3_directory_twse_enabled": False}):
            cfg = Settings(**{**CFG.model_dump(), **flags})
            fake = FakeFeed()
            listing = await tm.list_twse_announcements(session, company, cfg=cfg,
                                                       getter=fake.get, now=NOW)
            assert listing.reason == "connector_disabled" and fake.calls == []


# ── The document ───────────────────────────────────────────────────────────── #


class TestDocument:
    def test_every_field_is_escaped(self) -> None:
        row = tm.parse_row(_row(name="<b>甲</b>", subject="<script>x</script>",
                                body="1.<img src=x onerror=y>"))
        assert row is not None
        page = tm.announcement_html(row)
        assert "<script>" not in page and "<img" not in page and "<b>甲" not in page
        assert "&lt;script&gt;" in page

    def test_the_page_states_the_code_name_time_and_clause(self) -> None:
        row = tm.parse_row(_row())
        assert row is not None
        page = tm.announcement_html(row)
        for part in ("公司代號：2072", "公司名稱：世紀能源", "2026-11-16 07:00:03（臺北時間）",
                     "符合條款：第20款", "事實發生日：2026-11-14", "<h2>說明</h2>",
                     "主旨：公告本公司董事會決議取得"):
            assert part in page

    async def test_a_held_announcement_becomes_searchable_chinese_content(
            self, session: Any) -> None:
        from app.models.extracted_document import ExtractedDocument
        from app.models.research_chunk import ResearchDocumentChunk

        fake = FakeFeed()
        listing = await _listing(session, fake)
        document = listing.documents[0]
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=CFG)
        assert result.is_ready, result.to_dict()
        assert len(fake.calls) == 1  # the content is the held row: no second request
        stored = (await session.execute(select(ExtractedDocument))).scalars().one()
        assert stored.source_type == "twse_material_info"
        assert stored.canonical_url == document.official_url
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert chunks and {c.language for c in chunks} == {"zh"}
        assert any("離岸風電水下基礎" in c.text for c in chunks)
        assert any("主旨：公告本公司董事會決議取得" in c.text for c in chunks)

    async def test_a_second_run_reuses_the_held_document(self, session: Any) -> None:
        listing = await _listing(session, FakeFeed())
        for _ in range(2):
            result = await acq.ensure_disclosure_evidence(
                session, issuer=listing.issuer, document=listing.documents[0], cfg=CFG)
            assert result.is_ready
        assert result.reused is True

    async def test_a_row_of_another_code_is_refused(self, session: Any) -> None:
        from app.models.extracted_document import ExtractedDocument

        listing = await _listing(session, FakeFeed())
        other = tm.parse_row(FEED[1])  # held: TSMC's row of the same feed
        assert other is not None
        forged = tm.to_document(other)
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=forged, cfg=CFG)
        assert result.reason == "primary_document_unavailable"
        assert result.notes == ["TwseIdentityMismatch"]
        assert (await session.execute(select(ExtractedDocument))).scalars().all() == []

    async def test_an_announcement_not_held_is_unavailable(self, session: Any) -> None:
        listing = await _listing(session, FakeFeed())
        missing = tm.to_document(tm.parse_row(_row(clock="235959")))  # type: ignore[arg-type]
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=missing, cfg=CFG)
        assert result.notes == ["TwseAnnouncementUnavailable"]


# ── Wiring ─────────────────────────────────────────────────────────────────── #


class TestWiring:
    @pytest.mark.parametrize("exchange", ["TW", "TWSE", "TPE", "XTAI"])
    def test_a_taiwanese_listing_is_covered(self, exchange: str) -> None:
        company = Company(ticker="2072", exchange=exchange, name="x")
        assert acq.source_for(company, CFG) == (SOURCE_TWSE_MATERIAL, None)

    def test_off_is_connector_disabled(self) -> None:
        company = Company(ticker="2072", exchange="TW", name="x")
        cfg = Settings(**{**CFG.model_dump(), "v3_twse_material_info_enabled": False})
        assert acq.source_for(company, cfg) == (None, "connector_disabled")

    async def test_list_disclosures_routes_to_twse(self, session: Any,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tm, "guarded_get", FakeFeed().get)
        company = await _company(session)
        listing = await acq.list_disclosures(session, company, cfg=CFG,
                                             fetcher=directory_fetcher, now=NOW)
        assert listing.issuer is not None and len(listing.documents) == 1

    def test_the_title_is_the_period_source(self) -> None:
        assert is_title_only(source_type="twse_material_info")

    def test_the_citation_address_names_its_announcement(self) -> None:
        from app.services.corpus.filing_evidence import url_has_document_segment

        ref = "2072-20261116-070003-8fbccd9511"
        assert url_has_document_segment(tm.official_url(ref), ref)
        assert not url_has_document_segment(tm.official_url(ref + "0"), ref)
        assert not url_has_document_segment(f"{tm.FEED_URL}#{ref}", ref)

    def test_the_conftest_keeps_tests_offline(self) -> None:
        assert tm.guarded_get.__name__ == "_no_dart"

    def test_the_flag_defaults_on(self) -> None:
        assert Settings().v3_twse_material_info_enabled is True

    def test_the_attribution_names_the_licence(self) -> None:
        assert "Open Government Data License" in tm.ATTRIBUTION


def test_headlines_pass_the_report_safety_gate() -> None:
    """A headline is the issuer's own wording; ``to_item`` neutralises it."""
    from app.services.safety_terms import scan_value

    row = tm.parse_row(_row(subject="說明媒體報導 target price BUY rating"))
    assert row is not None
    item = tm.to_document(row).to_item()
    assert scan_value(item) == []
