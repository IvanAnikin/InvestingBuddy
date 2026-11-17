"""V3.20 slice 11.3b — OpenDART consolidated statements as statement facts.

The row shapes below are trimmed from Samsung Electronics' real ``fnlttSinglAcntAll``
responses (2025 annual, 2026 Q1 / H1 / Q3, fetched 2026-11-17): the income statement's
``thstrm`` is the three months and ``thstrm_add`` the year to date; the cash-flow
statement's ``thstrm`` is the year to date; the balance sheet's ``frmtrm`` in an interim
report is the last fiscal year end.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.services.sources.disclosures import acquisition as acq
from app.services.sources.disclosures import opendart
from app.services.sources.disclosures import opendart_statements as st
from app.services.sources.disclosures.model import (
    SOURCE_OPENDART,
    VENUE_KRX,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.inline_xbrl_facts import TAGGED_EXTRACTION_METHODS
from tests.test_non_us_primary_docs_connectors import (  # noqa: F401 - autouse fixture
    _index_like_postgres,
)
from tests.test_v3_20_11_3_opendart import (  # noqa: F401 - fixtures
    CFG,
    DART_DOC,
    KEY,
    NOW,
    SAMSUNG,
    SAMSUNG_LISTS,
    FakeDart,
    _company,
    _fresh_corp_code_cache,
    _zip,
)


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

Q3_RCEPT, H1_RCEPT, Q1_RCEPT, FY_RCEPT = (
    "20261116001482", "20260814003699", "20260515002181", "20260310002820")


def _r(sj_div: str, account_id: str, thstrm_nm: str, *, rcept: str = Q3_RCEPT,
       reprt: str = st.REPORT_CODE_Q3, corp: str = SAMSUNG, currency: str = "KRW",
       account_nm: str = "계정", **amounts: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "rcept_no": rcept, "reprt_code": reprt, "bsns_year": "2026", "corp_code": corp,
        "sj_div": sj_div, "sj_nm": sj_div, "account_id": account_id,
        "account_nm": account_nm, "account_detail": "-", "thstrm_nm": thstrm_nm,
        "ord": "1", "currency": currency}
    row.update(amounts)
    return row


def q3_rows(**kw: Any) -> list[dict[str, Any]]:
    return [
        _r("BS", "ifrs-full_Assets", "제 58 기 3분기말", account_nm="자산총계",
           thstrm_amount="791841881000000", frmtrm_nm="제 57 기말",
           frmtrm_amount="566942110000000", **kw),
        _r("IS", "ifrs-full_Revenue", "제 58 기 3분기", account_nm="매출액",
           thstrm_amount="195253209000000", thstrm_add_amount="500626123000000",
           frmtrm_q_amount="86061747000000", frmtrm_add_amount="239768567000000", **kw),
        _r("IS", "dart_OperatingIncomeLoss", "제 58 기 3분기", account_nm="영업이익",
           thstrm_amount="107440482000000", thstrm_add_amount="254165691000000",
           frmtrm_q_amount="12166062000000", frmtrm_add_amount="23527391000000", **kw),
        _r("IS", "ifrs-full_ProfitLoss", "제 58 기 3분기", account_nm="분기순이익",
           thstrm_amount="75136138000000", thstrm_add_amount="193985871000000",
           frmtrm_q_amount="12225747000000", **kw),
        _r("CIS", "ifrs-full_ProfitLoss", "제 58 기 3분기", account_nm="분기순이익",
           thstrm_amount="75136138000000", thstrm_add_amount="193985871000000",
           frmtrm_q_amount="12225747000000", **kw),
        # The cash-flow statement opens with the nine months' profit: never a profit fact.
        _r("CF", "ifrs-full_ProfitLoss", "제 58 기 3분기", thstrm_amount="193985871000000",
           frmtrm_amount="25565060000000", frmtrm_q_amount="25565060000000", **kw),
        _r("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "제 58 기 3분기",
           thstrm_amount="243942116000000", frmtrm_amount="56515496000000",
           frmtrm_q_amount="56515496000000", **kw),
        _r("IS", "dart_OtherGains", "제 58 기 3분기", thstrm_amount="1000", **kw),
        _r("CF", "-표준계정코드 미사용-", "제 58 기 3분기", thstrm_amount="5", **kw),
        _r("SCE", "ifrs-full_Equity", "제 58 기 3분기", thstrm_amount="9", **kw),
    ]


def h1_rows() -> list[dict[str, Any]]:
    kw = {"rcept": H1_RCEPT, "reprt": st.REPORT_CODE_HALF}
    return [
        _r("BS", "ifrs-full_Assets", "제 58 기 반기말", thstrm_amount="759480516000000",
           frmtrm_amount="566942110000000", **kw),
        _r("IS", "ifrs-full_Revenue", "제 58 기 반기", thstrm_amount="171499470000000",
           thstrm_add_amount="305372914000000", frmtrm_q_amount="74566317000000",
           frmtrm_add_amount="153706820000000", **kw),
        _r("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "제 58 기 반기",
           thstrm_amount="145355192000000", frmtrm_q_amount="33941002000000",
           frmtrm_amount="33941002000000", **kw),
        _r("CF", "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
           "제 58 기 반기", thstrm_amount="31235000000000",
           frmtrm_q_amount="26831000000000", **kw),
    ]


def annual_rows() -> list[dict[str, Any]]:
    kw = {"rcept": FY_RCEPT, "reprt": st.REPORT_CODE_ANNUAL}
    return [
        _r("BS", "ifrs-full_Assets", "제 57 기", thstrm_amount="566942110000000",
           frmtrm_amount="514531948000000", bfefrmtrm_amount="455905980000000", **kw),
        _r("IS", "ifrs-full_Revenue", "제 57 기", thstrm_amount="333605938000000",
           thstrm_add_amount="", frmtrm_amount="300870903000000",
           bfefrmtrm_amount="258935494000000", **kw),
        _r("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "제 57 기",
           thstrm_amount="85315148000000", frmtrm_amount="72982621000000",
           bfefrmtrm_amount="44137427000000", **kw),
    ]


def _by(facts: list[Any]) -> dict[tuple[str, str], Any]:
    return {(f.label, f.period): f for f in facts}


Q3 = st.report_frame("분기보고서 (2026.09)", fy_end_month=12)
H1 = st.report_frame("반기보고서 (2026.06)", fy_end_month=12)
FY = st.report_frame("사업보고서 (2025.12)", fy_end_month=12)


# ── The frame ──────────────────────────────────────────────────────────────── #


class TestReportFrame:
    @pytest.mark.parametrize(("name", "fy_end", "code", "fiscal_year", "months"), [
        ("분기보고서 (2026.09)", 12, "11014", 2026, 9),
        ("분기보고서 (2026.03)", 12, "11013", 2026, 3),
        ("반기보고서 (2026.06)", 12, "11012", 2026, 6),
        ("사업보고서 (2025.12)", 12, "11011", 2025, 12),
        ("사업보고서 (2025.12)", None, "11011", 2025, 12),
        # A March year end: the fiscal year is named by the year it ends in.
        ("분기보고서 (2026.06)", 3, "11013", 2027, 3),
        ("반기보고서 (2026.09)", 3, "11012", 2027, 6),
        ("분기보고서 (2026.12)", 3, "11014", 2027, 9),
        ("사업보고서 (2026.03)", 3, "11011", 2026, 12),
    ])
    def test_a_periodic_report_is_placed_on_the_fiscal_calendar(
            self, name: str, fy_end: int | None, code: str, fiscal_year: int,
            months: int) -> None:
        frame = st.report_frame(name, fy_end_month=fy_end)
        assert frame is not None
        assert (frame.report_code, frame.fiscal_year, frame.months) == (
            code, fiscal_year, months)

    @pytest.mark.parametrize(("name", "fy_end"), [
        ("반기보고서 (2026.03)", 12),  # a half-year report three months in
        ("분기보고서 (2026.06)", 12),  # a quarterly report six months in
        ("사업보고서 (2026.03)", 12),  # an annual report off the year end
        ("분기보고서 (2026.09)", None),  # interim, year end unknown
        ("[기재정정]분기보고서 (2026.09)", 12),
        ("분기보고서", 12),
        ("주요사항보고서(자기주식취득결정)", 12),
        ("분기보고서 (2026.13)", 12),
    ])
    def test_anything_else_has_no_frame(self, name: str, fy_end: int | None) -> None:
        assert st.report_frame(name, fy_end_month=fy_end) is None

    def test_a_december_year_asks_for_one_business_year_others_for_two(self) -> None:
        assert Q3 is not None and Q3.bsns_years() == (2026,)
        march = st.report_frame("분기보고서 (2026.06)", fy_end_month=3)
        assert march is not None and march.bsns_years() == (2027, 2026)


# ── The rows ───────────────────────────────────────────────────────────────── #


class TestQuarter:
    def test_a_q3_report_gives_the_quarter_and_its_comparative(self) -> None:
        assert Q3 is not None
        facts = _by(st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG))
        assert facts[("revenue", "2026-Q3")].value_numeric == 195253209000000.0
        assert facts[("revenue", "2025-Q3")].value_numeric == 86061747000000.0
        # Nine months to date is not a stored period.
        assert {p for (label, p) in facts if label == "revenue"} == {"2026-Q3", "2025-Q3"}

    def test_the_balance_sheet_is_this_period_end_and_the_last_year_end(self) -> None:
        assert Q3 is not None
        facts = _by(st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG))
        assert facts[("total_assets", "2026-Q3")].value_numeric == 791841881000000.0
        assert facts[("total_assets", "2025")].value_numeric == 566942110000000.0

    def test_q3_cash_flows_are_nine_months_and_are_not_read(self) -> None:
        assert Q3 is not None
        facts = st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG)
        assert not [f for f in facts if f.label == "operating_cash_flow"]

    def test_profit_comes_from_the_income_statement_never_the_cash_flow(self) -> None:
        assert Q3 is not None
        facts = _by(st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG))
        assert facts[("net_income", "2026-Q3")].value_numeric == 75136138000000.0

    def test_operating_income_is_the_dart_concept_under_its_own_label(self) -> None:
        assert Q3 is not None
        fact = _by(st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT,
                                      corp_code=SAMSUNG))[("operating_profit", "2026-Q3")]
        assert fact.value_numeric == 107440482000000.0
        assert "taxonomy label: Operating income (loss) (DART taxonomy)" in \
            fact.validation_notes
        assert fact.table_location == \
            "opendart:dart:OperatingIncomeLoss@IS/thstrm_amount"

    def test_other_dart_concepts_unstandard_rows_and_equity_statement_are_skipped(
            self) -> None:
        assert Q3 is not None
        facts = st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG)
        assert {f.label for f in facts} == {
            "total_assets", "revenue", "operating_profit", "net_income"}
        assert all("SCE" not in (f.table_location or "") for f in facts)

    def test_a_fact_carries_its_filing_currency_and_provenance(self) -> None:
        assert Q3 is not None
        fact = _by(st.statement_facts(q3_rows(), Q3, rcept_no=Q3_RCEPT,
                                      corp_code=SAMSUNG))[("revenue", "2026-Q3")]
        assert (fact.currency, fact.scale, fact.unit) == ("KRW", None, "currency_amount")
        assert fact.value_text == "195253209000000"
        assert fact.extraction_method == st.EXTRACTION_METHOD_DART_XBRL
        assert fact.scope == "Group"
        assert fact.validation_status == "validated"
        assert any(Q3_RCEPT in note for note in fact.validation_notes)

    def test_income_statements_that_disagree_fill_nothing(self) -> None:
        assert Q3 is not None
        rows = q3_rows()
        for row in rows:
            if row["sj_div"] == "CIS":
                row["thstrm_amount"] = "1"
        facts = _by(st.statement_facts(rows, Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG))
        assert ("net_income", "2026-Q3") not in facts
        assert ("net_income", "2025-Q3") in facts  # the comparative still agrees

    def test_a_row_naming_another_period_is_skipped(self) -> None:
        assert Q3 is not None
        rows = q3_rows()
        rows[1]["thstrm_nm"] = "제 58 기 반기"
        facts = _by(st.statement_facts(rows, Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG))
        assert ("revenue", "2026-Q3") not in facts

    @pytest.mark.parametrize("value", ["", None, "1,000", "12.5", "-"])
    def test_an_unreadable_amount_is_skipped(self, value: str | None) -> None:
        assert Q3 is not None
        rows = q3_rows()
        rows[1]["thstrm_amount"] = value
        facts = _by(st.statement_facts(rows, Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG))
        assert ("revenue", "2026-Q3") not in facts

    def test_a_non_iso_currency_is_skipped(self) -> None:
        assert Q3 is not None
        facts = st.statement_facts(q3_rows(currency="원"), Q3, rcept_no=Q3_RCEPT,
                                   corp_code=SAMSUNG)
        assert facts == []


class TestThisFilingOnly:
    def test_rows_of_another_receipt_produce_nothing(self) -> None:
        assert Q3 is not None
        assert st.statement_facts(q3_rows(rcept="20261201000001"), Q3,
                                  rcept_no=Q3_RCEPT, corp_code=SAMSUNG) == []

    def test_a_mixed_answer_produces_nothing(self) -> None:
        assert Q3 is not None
        rows = q3_rows()
        rows[0]["rcept_no"] = "20261201000001"
        assert st.statement_facts(rows, Q3, rcept_no=Q3_RCEPT, corp_code=SAMSUNG) == []

    def test_rows_of_another_company_produce_nothing(self) -> None:
        assert Q3 is not None
        assert st.statement_facts(q3_rows(corp="00000001"), Q3, rcept_no=Q3_RCEPT,
                                  corp_code=SAMSUNG) == []

    def test_rows_of_another_report_code_produce_nothing(self) -> None:
        assert Q3 is not None
        assert st.statement_facts(q3_rows(reprt=st.REPORT_CODE_Q1), Q3,
                                  rcept_no=Q3_RCEPT, corp_code=SAMSUNG) == []


class TestHalfYearAndAnnual:
    def test_a_half_year_report_gives_q2_h1_and_h1_cash_flows(self) -> None:
        assert H1 is not None
        facts = _by(st.statement_facts(h1_rows(), H1, rcept_no=H1_RCEPT, corp_code=SAMSUNG))
        assert facts[("revenue", "2026-Q2")].value_numeric == 171499470000000.0
        assert facts[("revenue", "2026-H1")].value_numeric == 305372914000000.0
        assert facts[("revenue", "2025-Q2")].value_numeric == 74566317000000.0
        assert facts[("revenue", "2025-H1")].value_numeric == 153706820000000.0
        assert facts[("operating_cash_flow", "2026-H1")].value_numeric == 145355192000000.0
        assert facts[("operating_cash_flow", "2025-H1")].value_numeric == 33941002000000.0
        assert facts[("capital_expenditure", "2026-H1")].value_numeric == 31235000000000.0
        assert facts[("total_assets", "2026-H1")].value_numeric == 759480516000000.0

    def test_a_negative_spend_is_skipped_not_flipped(self) -> None:
        assert H1 is not None
        rows = h1_rows()
        rows[3]["thstrm_amount"] = "-31235000000000"
        facts = _by(st.statement_facts(rows, H1, rcept_no=H1_RCEPT, corp_code=SAMSUNG))
        assert ("capital_expenditure", "2026-H1") not in facts
        assert ("capital_expenditure", "2025-H1") in facts

    def test_an_annual_report_gives_three_years(self) -> None:
        assert FY is not None
        facts = _by(st.statement_facts(annual_rows(), FY, rcept_no=FY_RCEPT,
                                       corp_code=SAMSUNG))
        assert {p for (label, p) in facts if label == "revenue"} == {"2025", "2024", "2023"}
        assert facts[("operating_cash_flow", "2023")].value_numeric == 44137427000000.0
        assert facts[("total_assets", "2024")].value_numeric == 514531948000000.0

    def test_a_q1_report_whose_quarter_and_year_to_date_differ_is_not_read(self) -> None:
        frame = st.report_frame("분기보고서 (2026.03)", fy_end_month=12)
        assert frame is not None
        rows = [_r("IS", "ifrs-full_Revenue", "제 58 기 1분기", rcept=Q1_RCEPT,
                   reprt=st.REPORT_CODE_Q1, thstrm_amount="133873444000000",
                   thstrm_add_amount="133873444000000")]
        assert st.statement_facts(rows, frame, rcept_no=Q1_RCEPT, corp_code=SAMSUNG)
        rows[0]["thstrm_add_amount"] = "200000000000000"
        assert st.statement_facts(rows, frame, rcept_no=Q1_RCEPT, corp_code=SAMSUNG) == []


# ── The request ────────────────────────────────────────────────────────────── #


def _document(report_nm: str, rcept_no: str = Q3_RCEPT) -> OfficialDocument:
    return OfficialDocument(
        source_id=SOURCE_OPENDART, document_ref=rcept_no, published_at=NOW,
        headline=report_nm, venue_category=report_nm, category="results",
        doc_kind="interim_report", research_rank=1, price_sensitive=None, media="html",
        official_url=opendart.VIEWER_URL.format(rcept_no=rcept_no))


ISSUER = VerifiedIssuer(company_id=None, ticker="005930", venue=VENUE_KRX,  # type: ignore[arg-type]
                        name="SAMSUNG ELECTRONICS CO,.LTD", source_id=SOURCE_OPENDART,
                        identity_basis="test", source_ref=SAMSUNG)


class StatementsApi:
    def __init__(self, answers: dict[str, bytes], *, acc_mt: str = "12") -> None:
        self.answers, self.acc_mt = answers, acc_mt
        self.calls: list[dict[str, str]] = []

    async def get(self, url: str, *, params: dict[str, str] | None = None, **_: Any) -> Any:
        params = dict(params or {})
        self.calls.append({"url": url, **params})
        if url == opendart.COMPANY_URL:
            return 200, json.dumps({"status": "000", "acc_mt": self.acc_mt}).encode(), None
        assert url == st.STATEMENTS_URL
        body = self.answers.get(params["bsns_year"])
        if body is None:
            return 200, json.dumps({"status": "013", "message": "없음"}).encode(), None
        return 200, body, None


def _answer(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps({"status": "000", "message": "정상", "list": rows}).encode()


class TestFetch:
    async def test_the_request_names_the_filing_and_the_key_only_as_a_parameter(self) -> None:
        api = StatementsApi({"2026": _answer(q3_rows())})
        facts, outcome = await st.fetch_statement_facts(
            _document("분기보고서 (2026.09)"), ISSUER, cfg=CFG, getter=api.get)
        assert outcome == "read" and facts
        call = [c for c in api.calls if c["url"] == st.STATEMENTS_URL][0]
        assert call["crtfc_key"] == KEY
        assert (call["corp_code"], call["bsns_year"], call["reprt_code"], call["fs_div"]) == (
            SAMSUNG, "2026", "11014", "CFS")
        for fact in facts:
            assert KEY not in json.dumps(fact.model_dump(), ensure_ascii=False)

    async def test_an_annual_report_needs_no_company_request(self) -> None:
        api = StatementsApi({"2025": _answer(annual_rows())})
        facts, outcome = await st.fetch_statement_facts(
            _document("사업보고서 (2025.12)", FY_RCEPT), ISSUER, cfg=CFG, getter=api.get)
        assert outcome == "read" and len(facts) == 9
        assert [c["url"] for c in api.calls] == [st.STATEMENTS_URL]

    async def test_a_march_year_end_tries_the_second_business_year(self) -> None:
        rows = [_r("IS", "ifrs-full_Revenue", "제 30 기 1분기", rcept=Q1_RCEPT,
                   reprt=st.REPORT_CODE_Q1, thstrm_amount="10", thstrm_add_amount="10")]
        api = StatementsApi({"2026": _answer(rows)}, acc_mt="3")
        facts, outcome = await st.fetch_statement_facts(
            _document("분기보고서 (2026.06)", Q1_RCEPT), ISSUER, cfg=CFG, getter=api.get)
        assert outcome == "read"
        assert [f.period for f in facts] == ["2027-Q1"]
        assert [c["bsns_year"] for c in api.calls if c["url"] == st.STATEMENTS_URL] == [
            "2027", "2026"]

    async def test_another_filings_answer_is_not_this_filing(self) -> None:
        api = StatementsApi({"2026": _answer(q3_rows(rcept="20261201000001"))})
        facts, outcome = await st.fetch_statement_facts(
            _document("분기보고서 (2026.09)"), ISSUER, cfg=CFG, getter=api.get)
        assert (facts, outcome) == ([], "not_this_filing")

    async def test_no_data_and_unreachable(self) -> None:
        facts, outcome = await st.fetch_statement_facts(
            _document("분기보고서 (2026.09)"), ISSUER, cfg=CFG,
            getter=StatementsApi({}).get)
        assert (facts, outcome) == ([], "no_data")

        async def down(url: str, **_: Any) -> Any:
            if url == opendart.COMPANY_URL:
                return 200, json.dumps({"status": "000", "acc_mt": "12"}).encode(), None
            return None, None, "timeout"

        assert await st.fetch_statement_facts(
            _document("분기보고서 (2026.09)"), ISSUER, cfg=CFG, getter=down) == (
            [], "unavailable")

    async def test_a_getter_that_raises_never_raises_here(self) -> None:
        async def boom(url: str, **_: Any) -> Any:
            raise RuntimeError("x")

        assert await st.fetch_statement_facts(
            _document("분기보고서 (2026.09)"), ISSUER, cfg=CFG, getter=boom) == (
            [], "unavailable")

    async def test_off_without_a_key_or_flag_and_not_for_other_filings(self) -> None:
        api = StatementsApi({"2026": _answer(q3_rows())})
        off = CFG.model_copy(update={"v3_opendart_statements_enabled": False})
        no_key = CFG.model_copy(update={"opendart_api_key": ""})
        for cfg in (off, no_key):
            assert await st.fetch_statement_facts(
                _document("분기보고서 (2026.09)"), ISSUER, cfg=cfg, getter=api.get) == (
                [], "off")
        assert await st.fetch_statement_facts(
            _document("주요사항보고서(자기주식취득결정)"), ISSUER, cfg=CFG,
            getter=api.get) == ([], "not_periodic")
        assert api.calls == []


def test_dart_facts_rank_with_inline_xbrl_as_tagged() -> None:
    assert st.EXTRACTION_METHOD_DART_XBRL in TAGGED_EXTRACTION_METHODS


def test_the_statements_flag_defaults_on() -> None:
    assert Settings().v3_opendart_statements_enabled is True


# ── Through the acquisition bridge ─────────────────────────────────────────── #


class FakeDartWithStatements(FakeDart):
    def __init__(self, *, statements: bytes | None, **kw: Any) -> None:
        super().__init__(**kw)
        self.statements = statements

    async def get(self, url: str, **kw: Any) -> Any:
        if url == st.STATEMENTS_URL:
            self.calls.append({"url": url, "params": dict(kw.get("params") or {})})
            if self.statements is None:
                return 200, json.dumps({"status": "013", "message": "x"}).encode(), None
            return 200, self.statements, None
        return await super().get(url, **kw)


class TestBridge:
    async def _ready(self, session: Any, monkeypatch: pytest.MonkeyPatch,
                     statements: bytes | None) -> tuple[Any, FakeDartWithStatements]:
        fake = FakeDartWithStatements(statements=statements, lists=SAMSUNG_LISTS,
                                      document=_zip({f"{Q3_RCEPT}.xml":
                                                     DART_DOC.format(corp=SAMSUNG)}))
        monkeypatch.setattr(opendart, "guarded_get", fake.get)
        company = await _company(session)
        listing = await opendart.list_opendart_disclosures(session, company, cfg=CFG, now=NOW)
        document = listing.documents[0]
        assert document.document_ref == Q3_RCEPT and listing.issuer is not None
        result = await acq.ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=CFG)
        assert result.is_ready, result.to_dict()
        return result, fake

    async def test_the_statements_become_facts_of_the_filing(
            self, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.models.extracted_document import ExtractedDocument, ExtractedFact

        _result, fake = await self._ready(session, monkeypatch, _answer(q3_rows()))
        document = (await session.execute(select(ExtractedDocument))).scalars().one()
        facts = (await session.execute(
            select(ExtractedFact).where(ExtractedFact.is_active.is_(True)))).scalars().all()
        tagged = {(f.label, f.period): f for f in facts
                  if f.extraction_method == st.EXTRACTION_METHOD_DART_XBRL}
        assert tagged[("revenue", "2026-Q3")].value_numeric == 195253209000000.0
        assert tagged[("revenue", "2026-Q3")].currency == "KRW"
        assert all(f.extracted_document_id == document.id for f in tagged.values())
        # The text reading of the same quarter's revenue yields to the tagged figure.
        assert [f for f in facts if f.label == "revenue" and f.period == "2026-Q3"] == [
            tagged[("revenue", "2026-Q3")]]
        assert len(fake.calls_to(st.STATEMENTS_URL)) == 1
        for fact in facts:
            assert KEY not in json.dumps({k: str(v) for k, v in vars(fact).items()
                                          if not k.startswith("_")})

    async def test_without_statements_the_filing_is_still_read(
            self, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.models.extracted_document import ExtractedFact

        await self._ready(session, monkeypatch, None)
        facts = (await session.execute(select(ExtractedFact))).scalars().all()
        assert not [f for f in facts if f.extraction_method == st.EXTRACTION_METHOD_DART_XBRL]


# ── Downstream: a DART statement figure is a tagged statement read ─────────── #


class TestTaggedLocator:
    @pytest.mark.parametrize(("locator", "concept"), [
        ("ixbrl:us-gaap:Revenues@c-1", "us-gaap:Revenues"),
        ("opendart:ifrs-full:Revenue@IS/thstrm_amount", "ifrs-full:Revenue"),
        ("opendart:dart:OperatingIncomeLoss@IS/thstrm_amount", "dart:OperatingIncomeLoss"),
        ("p14:m0", None),
        ("X12", None),
        (None, None),
        ("opendart:", None),
    ])
    def test_the_concept_of_a_tagged_locator(self, locator: str | None,
                                             concept: str | None) -> None:
        from app.integrations.xbrl_statement_concepts import tagged_concept

        assert tagged_concept(locator) == concept

    def test_the_dart_concept_has_its_taxonomy_label(self) -> None:
        from app.integrations.xbrl_statement_concepts import (
            STATEMENT_CONCEPT_BY_QNAME,
            taxonomy_label,
        )

        assert taxonomy_label("dart:OperatingIncomeLoss") == \
            "Operating income (loss) (DART taxonomy)"
        # It is not in the SEC-shared map: the companyfacts normalizer never reads it.
        assert "dart:OperatingIncomeLoss" not in STATEMENT_CONCEPT_BY_QNAME

    def test_a_dart_figure_ranks_as_a_table_read_and_a_statement_read(self) -> None:
        from app.services.pipeline.issuer_financials import _is_statement_read, _tagged_concept
        from app.services.sources.statement_line_selector import LINE_RANK_TABLE, line_rank

        locator = "opendart:ifrs-full:Revenue@IS/thstrm_amount"
        assert line_rank(locator) == LINE_RANK_TABLE
        assert _is_statement_read({"excerpt_id": locator, "line_rank": None})
        assert _tagged_concept({"excerpt_id": locator}) == "ifrs-full:Revenue"
        assert not _is_statement_read({"excerpt_id": "X12", "line_rank": None})
