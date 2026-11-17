"""V3.20 slice 11.3c — OpenDART major-matters decisions as typed corporate actions.

Rows are trimmed from real OpenDART responses fetched 2026-11-17 (a KOSDAQ issuer's
convertible bond and third-party capital increase, Samsung Electronics' treasury-share
acquisition, a bond with warrants for debt repayment, an asset acquisition, a business
acquisition from an individual, a lawsuit). Names of private individuals are replaced.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

import pytest

from app.core.config import Settings
from app.models.company import Company
from app.services import safety_terms
from app.services.discovery.change_events import change_events_block
from app.services.pipeline import change_events_step
from app.services.sources.disclosures import opendart
from app.services.sources.disclosures import opendart_major_matters as mm

KEY = "m" * 40
TODAY = date(2026, 11, 17)
CORP = "00103547"
CFG = Settings(v3_opendart_disclosures_enabled=True, opendart_api_key=KEY,
               v3_opendart_major_matters_enabled=True, v3_change_events_enabled=True,
               v3_insider_clusters_enabled=False)

CVBD = {"rcept_no": "20261117000265", "corp_cls": "K", "corp_code": CORP,
        "corp_name": "우진비앤지", "bddd": "2026년 11월 17일", "fdpp_fclt": "-",
        "fdpp_bsninh": "-", "fdpp_op": "6,000,000,000", "fdpp_dtrp": "-", "fdpp_ocsa": "-",
        "fdpp_etc": "-", "bd_fta": "6,000,000,000", "bd_intr_ex": "0.0", "bd_intr_sf": "2.0",
        "bd_mtd": "2030년 01월 29일", "bdis_mthn": "사모", "cv_prc": "2,734",
        "cvisstk_cnt": "2,194,586", "cvisstk_tisstk_vs": "24.04"}
PIIC = {"rcept_no": "20261117000263", "corp_code": CORP, "nstk_ostk_cnt": "2,200,000",
        "nstk_estk_cnt": "-", "bfic_tisstk_ostk": "6,932,982", "fdpp_op": "5,500,000,000",
        "fdpp_dtrp": "-", "ic_mthn": "제3자배정증자"}
BDWT = {"rcept_no": "20261029000401", "corp_code": CORP, "bddd": "2026년 10월 29일",
        "fdpp_op": "-", "fdpp_dtrp": "6,000,000,000", "bd_fta": "6,000,000,000",
        "bd_mtd": "2031년 11월 06일", "bdis_mthn": "사모", "ex_prc": "3,006",
        "nstk_isstk_cnt": "1,996,007", "nstk_isstk_tisstk_vs": "8.97"}
TSSTK = {"rcept_no": "20260129000003", "corp_code": CORP, "aqpln_stk_ostk": "22,000,000",
         "aqpln_prc_ostk": "3,572,800,000,000", "aqexpd_bgd": "2026년 01월 30일",
         "aqexpd_edd": "2026년 04월 29일", "aq_pp": "임직원 주식보상",
         "aq_mth": "유가증권시장을 통한 장내 매수"}
TGAST = {"rcept_no": "20261116001834", "corp_code": CORP, "bddd": "2026년 11월 16일",
         "dlptn_cmpnm": "주식회사 주원빌딩", "inhdtl_inhprc": "16,100,000,000",
         "inhdtl_tast": "157,244,091,441", "inhdtl_tast_vs": "10.24"}
BSNINH = {"rcept_no": "20261116002208", "corp_code": CORP, "bddd": "2026년 11월 16일",
          "inh_prc": "3,300,000,114", "ast_rt": "11.32", "dlptn_cmpnm": "홍길동"}
LWST = {"rcept_no": "20261104000382", "corp_code": CORP, "cpct": "서울중앙지방법원",
        "icnm": "신주발행금지가처분 (2026카합21950)", "ac_ap": "이OO",
        "rq_cn": "청구 내용", "lgd": "2026년 10월 24일"}


def _decision(kind: str) -> mm.Decision:
    return next(d for d in mm.DECISIONS if d.kind == kind)


# ── Names ──────────────────────────────────────────────────────────────────── #


class TestDecisionNames:
    @pytest.mark.parametrize(("listed", "name"), [
        ("주요사항보고서(전환사채권발행결정)", "전환사채권발행결정"),
        ("[기재정정]주요사항보고서(유상증자결정)", "유상증자결정"),
        ("[첨부정정]주요사항보고서 (자기주식취득결정)", "자기주식취득결정"),
        ("[기재정정]회사합병결정", "회사합병결정"),
        ("타법인주식및출자증권취득결정", "타법인주식및출자증권양수결정"),
        ("주요사항보고서(주식교환ㆍ이전결정)", "주식교환ㆍ이전결정"),
    ])
    def test_a_listed_filing_names_its_decision(self, listed: str, name: str) -> None:
        assert mm.decision_name(listed) == name

    def test_only_listed_types_are_read_once_each_and_a_trust_is_not_a_buyback(
            self) -> None:
        wanted = mm.decisions_listed([
            "주요사항보고서(자기주식취득신탁계약체결결정)",
            "주요사항보고서(전환사채권발행결정)",
            "[기재정정]주요사항보고서(전환사채권발행결정)",
            "타인을위한채무보증결정",  # no structured reader
        ])
        assert [d.kind for d in wanted] == ["convertible_bond_issue",
                                            "treasury_share_trust_contract"]

    def test_every_decision_has_a_distinct_name_endpoint_and_kind(self) -> None:
        for attr in ("name", "endpoint", "kind"):
            values = [getattr(d, attr) for d in mm.DECISIONS]
            assert len(values) == len(set(values)), attr


# ── Values ─────────────────────────────────────────────────────────────────── #


class TestValues:
    @pytest.mark.parametrize(("raw", "iso"), [
        ("2026년 11월 17일", "2026-11-17"), ("2030년 01월 29일", "2030-01-29"),
        ("2026년 2월 30일", None), ("-", None), (None, None), ("2026-11-17", None)])
    def test_korean_dates(self, raw: str | None, iso: str | None) -> None:
        assert mm.korean_date(raw) == iso

    @pytest.mark.parametrize(("name", "company"), [
        ("주식회사 주원빌딩", True), ("(주)지아이티아카데미", True), ("광동생활건강 ㈜", True),
        ("Samsung Securities Co., Ltd.", True), ("홍길동", False), ("김OO 외 2인", False),
        ("", False)])
    def test_only_a_company_is_named(self, name: str, company: bool) -> None:
        assert mm.is_company_name(name) is company


# ── Rows ───────────────────────────────────────────────────────────────────── #


class TestActions:
    def test_a_convertible_bond_keeps_its_stated_terms(self) -> None:
        action = mm.action_from_row(_decision("convertible_bond_issue"), CVBD, corp_code=CORP)
        assert action is not None
        assert action["date"] == "2026-11-17"
        assert action["receipt_no"] == "20261117000265"
        assert action["source_url"] == opendart.VIEWER_URL.format(rcept_no="20261117000265")
        assert action["details"] == {
            "face_amount_krw": 6_000_000_000, "maturity": "2030-01-29", "offering": "private",
            "coupon_pct": 0.0, "yield_to_maturity_pct": 2.0, "board_date": "2026-11-17",
            "conversion_price_krw": 2734, "shares_on_conversion": 2_194_586,
            "shares_on_conversion_pct_of_total": 24.04,
            "use_of_funds_krw": {"operating_funds": 6_000_000_000}}

    def test_a_capital_increase_names_its_method_in_english(self) -> None:
        action = mm.action_from_row(_decision("capital_increase"), PIIC, corp_code=CORP)
        assert action is not None
        assert action["details"]["method"] == "third_party_allotment"
        assert action["details"]["new_common_shares"] == 2_200_000
        assert "new_other_shares" not in action["details"]  # "-" is absent

    def test_a_buyback_has_its_plan_and_period(self) -> None:
        action = mm.action_from_row(_decision("treasury_share_acquisition"), TSSTK,
                                    corp_code=CORP)
        assert action is not None
        assert action["details"] == {
            "planned_common_shares": 22_000_000, "planned_amount_krw": 3_572_800_000_000,
            "period_start": "2026-01-30", "period_end": "2026-04-29"}

    def test_a_company_counterparty_is_named_an_individual_is_not(self) -> None:
        asset = mm.action_from_row(_decision("asset_acquisition"), TGAST, corp_code=CORP)
        business = mm.action_from_row(_decision("business_acquisition"), BSNINH,
                                      corp_code=CORP)
        assert asset is not None and business is not None
        assert asset["details"]["counterparty"] == "주식회사 주원빌딩"
        assert asset["details"]["pct_of_total_assets"] == 10.24
        assert business["details"]["counterparty"] == "individual_or_unnamed"
        assert "홍길동" not in json.dumps(business, ensure_ascii=False)

    def test_a_lawsuit_never_carries_the_plaintiff(self) -> None:
        action = mm.action_from_row(_decision("litigation"), LWST, corp_code=CORP)
        assert action is not None
        assert action["details"] == {"court": "서울중앙지방법원",
                                     "case": "신주발행금지가처분 (2026카합21950)",
                                     "filed": "2026-10-24"}
        assert "이OO" not in json.dumps(action, ensure_ascii=False)

    def test_a_row_of_another_filer_or_without_a_receipt_is_dropped(self) -> None:
        decision = _decision("convertible_bond_issue")
        assert mm.action_from_row(decision, {**CVBD, "corp_code": "00000001"},
                                  corp_code=CORP) is None
        assert mm.action_from_row(decision, {**CVBD, "rcept_no": "x"}, corp_code=CORP) is None

    def test_a_kind_only_decision_still_dates_and_cites_itself(self) -> None:
        action = mm.action_from_row(_decision("default"),
                                    {"rcept_no": "20261020000001", "corp_code": CORP},
                                    corp_code=CORP)
        assert action is not None
        assert (action["kind"], action["date"], action["details"]) == (
            "default", "2026-10-20", {})


class TestRefinancing:
    def test_a_bond_issued_to_repay_debt_is_a_refinancing_event(self) -> None:
        decision = _decision("bond_with_warrants_issue")
        action = mm.action_from_row(decision, BDWT, corp_code=CORP)
        assert action is not None
        event = mm.refinancing_event(action, decision)
        assert event is not None
        assert event["trigger_type"] == "maturity_refinanced"
        assert event["date"] == "2026-10-29"
        assert "6,000,000,000" in event["title"]

    def test_a_bond_for_operating_funds_is_not(self) -> None:
        decision = _decision("convertible_bond_issue")
        action = mm.action_from_row(decision, CVBD, corp_code=CORP)
        assert action is not None and mm.refinancing_event(action, decision) is None

    def test_equity_raised_to_repay_debt_is_not_a_bond_refinancing(self) -> None:
        decision = _decision("capital_increase")
        action = mm.action_from_row(decision, {**PIIC, "fdpp_dtrp": "1,000"}, corp_code=CORP)
        assert action is not None and mm.refinancing_event(action, decision) is None


# ── The lookup ─────────────────────────────────────────────────────────────── #


def _list(*names: str, status: str = "000") -> bytes:
    if status != "000":
        return json.dumps({"status": status, "message": "x"}).encode()
    rows = [{"corp_code": CORP, "corp_name": "우진비앤지", "stock_code": "018620",
             "report_nm": n, "rcept_no": f"2026111700{i:04d}", "rcept_dt": "20261117",
             "flr_nm": "x", "rm": ""} for i, n in enumerate(names)]
    return json.dumps({"status": "000", "message": "정상", "page_no": 1, "page_count": 100,
                       "total_count": len(rows), "total_page": 1, "list": rows}).encode()


def _answer(*rows: dict[str, Any]) -> bytes:
    return json.dumps({"status": "000", "message": "정상", "list": list(rows)}).encode()


class FakeApi:
    def __init__(self, listing: bytes, answers: dict[str, bytes]) -> None:
        self.listing, self.answers = listing, answers
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, params: dict[str, str] | None = None,
                  allowed_host: str = "", **_: Any) -> Any:
        assert allowed_host == opendart.OPENDART_HOST
        self.calls.append((url, dict(params or {})))
        if url == opendart.LIST_URL:
            return 200, self.listing, None
        endpoint = url.removeprefix(mm.API_BASE).removesuffix(".json")
        body = self.answers.get(endpoint)
        if body is None:
            return 200, json.dumps({"status": "013", "message": "없음"}).encode(), None
        return 200, body, None

    def endpoints(self) -> list[str]:
        return [u.removeprefix(mm.API_BASE) for u, _ in self.calls if u != opendart.LIST_URL]


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


async def _korean_company(session: Any, *, held: bool = True) -> Company:
    if held:
        await opendart.store_corp_codes(session, [opendart.CorpCodeRow(
            corp_code=CORP, name_ko="우진비앤지", name_en="WOOJIN B&G CO., LTD.",
            stock_code="018620")], now=datetime(2026, 11, 17, tzinfo=timezone.utc))
    company = Company(id=uuid.uuid4(), ticker="018620", exchange="KQ",
                      name="Woojin B&G Co., Ltd.", status="new")
    session.add(company)
    await session.flush()
    return company


class TestLookup:
    async def test_only_the_filed_decision_types_are_read(self, session: Any) -> None:
        company = await _korean_company(session)
        api = FakeApi(_list("주요사항보고서(전환사채권발행결정)",
                            "[기재정정]주요사항보고서(전환사채권발행결정)",
                            "주요사항보고서(유상증자결정)", "타인을위한채무보증결정"),
                      {"cvbdIsDecsn": _answer(CVBD, {**CVBD, "corp_code": "00000001",
                                                     "rcept_no": "20261117000999"}),
                       "piicDecsn": _answer(PIIC, PIIC)})
        out = await mm.corporate_actions(session, company, cfg=CFG, today=TODAY,
                                         window_days=180, getter=api.get)
        assert out is not None
        assert sorted(api.endpoints()) == ["cvbdIsDecsn.json", "piicDecsn.json"]
        assert [a["receipt_no"] for a in out["actions"]] == [
            "20261117000265", "20261117000263"]  # newest first, another filer dropped
        lookup = out["lookup"]
        assert (lookup["corp_code"], lookup["filings_listed"], lookup["endpoints_read"],
                lookup["endpoints_failed"]) == (CORP, 4, 2, 0)
        assert lookup["notes"] == ["1 decision type(s) without a structured reader"]
        for url, params in api.calls:
            assert params["crtfc_key"] == KEY and KEY not in url
            assert (params["corp_code"], params["bgn_de"], params["end_de"]) == (
                CORP, "20260521", "20261117")
        assert KEY not in json.dumps(out, ensure_ascii=False)

    async def test_no_major_matters_filing_costs_one_request(self, session: Any) -> None:
        company = await _korean_company(session)
        api = FakeApi(_list(status="013"), {})
        out = await mm.corporate_actions(session, company, cfg=CFG, today=TODAY,
                                         window_days=180, getter=api.get)
        assert out is not None and out["actions"] == [] and len(api.calls) == 1

    async def test_a_failed_listing_or_endpoint_is_reported(self, session: Any) -> None:
        company = await _korean_company(session)
        out = await mm.corporate_actions(session, company, cfg=CFG, today=TODAY,
                                         window_days=180,
                                         getter=FakeApi(_list(status="020"), {}).get)
        assert out is not None and out["actions"] == []
        assert out["lookup"]["notes"] == ["listing unavailable (status 020)"]

        api = FakeApi(_list("주요사항보고서(전환사채권발행결정)"),
                      {"cvbdIsDecsn": b"<html>busy</html>"})
        out = await mm.corporate_actions(session, company, cfg=CFG, today=TODAY,
                                         window_days=180, getter=api.get)
        assert out is not None and out["lookup"]["endpoints_failed"] == 1

    async def test_not_applicable_without_a_held_code_a_key_or_the_flag(
            self, session: Any) -> None:
        api = FakeApi(_list("주요사항보고서(전환사채권발행결정)"), {})
        unheld = await _korean_company(session, held=False)
        assert await mm.corporate_actions(session, unheld, cfg=CFG, today=TODAY,
                                          window_days=180, getter=api.get) is None
        await opendart.store_corp_codes(session, [opendart.CorpCodeRow(
            corp_code=CORP, name_ko="우진비앤지", name_en="WOOJIN B&G CO., LTD.",
            stock_code="018620")], now=datetime(2026, 11, 17, tzinfo=timezone.utc))
        company = unheld
        for cfg in (CFG.model_copy(update={"opendart_api_key": ""}),
                    CFG.model_copy(update={"v3_opendart_major_matters_enabled": False})):
            assert await mm.corporate_actions(session, company, cfg=cfg, today=TODAY,
                                              window_days=180, getter=api.get) is None
        assert api.calls == []

    async def test_a_name_that_disagrees_with_the_held_code_is_not_resolved(
            self, session: Any) -> None:
        company = await _korean_company(session)
        company.name = "Completely Different Holdings"
        api = FakeApi(_list("주요사항보고서(전환사채권발행결정)"), {})
        assert await mm.corporate_actions(session, company, cfg=CFG, today=TODAY,
                                          window_days=180, getter=api.get) is None


# ── The block and the step ─────────────────────────────────────────────────── #


def _corporate() -> dict[str, Any]:
    bdwt = _decision("bond_with_warrants_issue")
    action = mm.action_from_row(bdwt, BDWT, corp_code=CORP)
    assert action is not None
    event = mm.refinancing_event(action, bdwt)
    return {"actions": [action], "events": [event],
            "lookup": {"source": mm.SOURCE_LABEL, "truncated": False}}


class TestBlock:
    def test_corporate_actions_alone_make_a_block(self) -> None:
        block = change_events_block(classified={"events": [], "negated": []}, insider=None,
                                    corporate={"actions": _corporate()["actions"],
                                               "events": [], "lookup": {}})
        assert block is not None
        assert len(block["corporate_actions"]) == 1
        assert "major-matters" in block["note"]

    def test_a_refinancing_event_is_counted_beside_title_events(self) -> None:
        block = change_events_block(classified={"events": [], "negated": []}, insider=None,
                                    corporate=_corporate())
        assert block is not None
        assert block["counts_by_type"] == {"maturity_refinanced": 1}
        assert block["events"][0]["source_kind"] == "structured_disclosure"

    def test_without_corporate_input_the_block_is_unchanged(self) -> None:
        assert change_events_block(classified={"events": [], "negated": []},
                                   insider=None) is None

    def test_every_decision_passes_the_report_safety_gate(self) -> None:
        actions = []
        for decision in mm.DECISIONS:
            row = {"rcept_no": "20261020000001", "corp_code": CORP,
                   **{src: "1,000" for src, kind in decision.fields.values()
                      if kind == "int"},
                   **{src: "12.5" for src, kind in decision.fields.values() if kind == "pct"},
                   "bdis_mthn": "사모", "ic_mthn": "제3자배정증자", "fdpp_dtrp": "5"}
            action = mm.action_from_row(decision, row, corp_code=CORP)
            assert action is not None
            actions.append(action)
            event = mm.refinancing_event(action, decision)
            if event:
                actions.append(event)
        block = change_events_block(classified={"events": [], "negated": []}, insider=None,
                                    corporate={"actions": actions, "events": [],
                                               "lookup": {"source": mm.SOURCE_LABEL}})
        assert safety_terms.scan_value(block, path="change_events") == []


class TestStep:
    async def test_the_step_adds_corporate_actions(self, session: Any) -> None:
        company = await _korean_company(session)
        api = FakeApi(_list("주요사항보고서(신주인수권부사채권발행결정)"),
                      {"bdwtIsDecsn": _answer(BDWT)})
        block = await change_events_step.change_events_section(
            session, company, cfg=CFG, today=TODAY, dart_getter=api.get)
        assert block is not None
        assert [a["kind"] for a in block["corporate_actions"]] == ["bond_with_warrants_issue"]
        assert block["counts_by_type"] == {"maturity_refinanced": 1}
        assert block["corporate_actions_lookup"]["endpoints_read"] == 1

    async def test_offline_by_default_in_tests(self, session: Any) -> None:
        company = await _korean_company(session)
        block = await change_events_step.change_events_section(
            session, company, cfg=CFG, today=TODAY)
        assert block is None  # the conftest transport answers nothing

    async def test_off_the_flag_no_request(self, session: Any) -> None:
        company = await _korean_company(session)
        api = FakeApi(_list("주요사항보고서(신주인수권부사채권발행결정)"), {})
        cfg = CFG.model_copy(update={"v3_opendart_major_matters_enabled": False})
        assert await change_events_step.change_events_section(
            session, company, cfg=cfg, today=TODAY, dart_getter=api.get) is None
        assert api.calls == []


def test_the_flag_defaults_on() -> None:
    assert Settings().v3_opendart_major_matters_enabled is True
