"""V3.20 slice 2.4 — customer concentration from a 10-K's inline XBRL.

The fixture mirrors what three real FY2025 10-Ks tag (read live 2026-11-15): Ultra Clean
names ``uctt:LamResearchCorporationMember`` (37.0 % of revenue) and
``uctt:AppliedMaterialsIncMember`` (21.7 %) and an aggregate ``TotalCustomerMember``;
Ichor tags ``ThreeMajorCustomersMember``; Babcock & Wilcox ``OneCustomerMember`` (10 %).
Powell tags product concentration on a timing axis — which is NOT a customer share.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from app.integrations import sec_relationships as sr
from app.models.legal_entity import EntityRelationship
from app.services.entities.relationship_view import company_relationships
from app.services.entities.sec_relationships import (
    concentration_period,
    ixbrl_already_read,
    record_concentrations,
)
from app.services.pipeline import relationship_step
from app.services.sources.customer_concentration import (
    concentrations_from_inline_xbrl,
    customer_kind,
    member_words,
)
from app.services.sources.inline_xbrl import parse_inline_xbrl
from tests import test_v3_entity_relationships as _base
from tests.test_v3_entity_relationships import _cfg, _entity

session = _base.session

CUST = "srt:MajorCustomersAxis"
BENCH = "us-gaap:ConcentrationRiskByBenchmarkAxis"
TYPE = "us-gaap:ConcentrationRiskByTypeAxis"
REV = "us-gaap:SalesRevenueNetMember"
AR = "us-gaap:AccountsReceivableMember"
CCR = "us-gaap:CustomerConcentrationRiskMember"
FY25 = ("2024-12-28", "2025-12-26")
FY24 = ("2023-12-30", "2024-12-27")
ANNUAL = {"form": "10-K", "accession": "0001628280-26-010744",
          "filing_date": date(2026, 2, 24), "primary_document": "uctt-20251226.htm"}


def _ctx(cid: str, span: tuple[str, str] | None = None, *, instant: str | None = None,
         dims: tuple[tuple[str, str], ...] = ()) -> str:
    seg = ""
    if dims:
        seg = "<xbrli:segment>" + "".join(
            f'<xbrldi:explicitMember dimension="{a}">{m}</xbrldi:explicitMember>'
            for a, m in dims) + "</xbrli:segment>"
    period = (f"<xbrli:instant>{instant}</xbrli:instant>" if instant else
              f"<xbrli:startDate>{span[0]}</xbrli:startDate>"  # type: ignore[index]
              f"<xbrli:endDate>{span[1]}</xbrli:endDate>")  # type: ignore[index]
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier '
            f'scheme="http://www.sec.gov/CIK">0001275014</xbrli:identifier>{seg}'
            f"</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>")


def _pct(cid: str, text: str) -> str:
    return (f'<ix:nonFraction name="us-gaap:ConcentrationRiskPercentage1" contextRef="{cid}" '
            f'unitRef="pure" decimals="3" scale="-2" format="ixt:num-dot-decimal">{text}'
            "</ix:nonFraction>")


def _doc(facts: list[tuple[str, tuple[str, str] | None, tuple[tuple[str, str], ...], str]],
         *, fy: str = "2025", fp: str = "FY") -> bytes:
    contexts = [_ctx("c-doc", FY25)]
    body = []
    for i, (text, span, dims, *rest) in enumerate(facts):
        cid = f"c-{i}"
        contexts.append(_ctx(cid, span, dims=dims) if span else
                        _ctx(cid, instant=rest[0] if rest else "2025-12-26", dims=dims))
        body.append(_pct(cid, text))
    dei = (
        '<ix:nonNumeric name="dei:DocumentType" contextRef="c-doc">10-K</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-doc" '
        'format="ixt:date-monthname-day-year-en">December 26, 2025</ix:nonNumeric>'
        f'<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c-doc">{fy}'
        "</ix:nonNumeric>"
        f'<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" contextRef="c-doc">{fp}'
        "</ix:nonNumeric>"
        '<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c-doc">0001275014'
        "</ix:nonNumeric>"
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body><div style="display:none">'
        f"<ix:header><ix:hidden>{dei}</ix:hidden><ix:resources>{''.join(contexts)}"
        '<xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>'
        "</ix:resources></ix:header></div><table><tr><td>"
        + "</td><td>".join(body) + "</td></tr></table></body></html>"
    ).encode()


def _uctt() -> bytes:
    lam = ((CUST, "uctt:LamResearchCorporationMember"), (BENCH, REV), (TYPE, CCR))
    amat = ((CUST, "uctt:AppliedMaterialsIncMember"), (BENCH, REV), (TYPE, CCR))
    return _doc([
        ("37.0", FY25, lam), ("31.9", FY24, lam), ("21.7", FY25, amat),
        ("58.7", FY25, ((CUST, "uctt:TotalCustomerMember"), (BENCH, REV), (TYPE, CCR))),
        ("17.1", FY25, ((CUST, "uctt:LamResearchCorporationMember"), (BENCH, AR), (TYPE, CCR))),
        # Powell's shape: product concentration by timing — not a customer at all.
        ("96", FY25, ((BENCH, "us-gaap:RevenueFromContractWithCustomerMember"),
                      (TYPE, "us-gaap:ProductConcentrationRiskMember"),
                      ("srt:TimingOfTransferOfGoodOrServiceAxis",
                       "us-gaap:TransferredOverTimeMember"))),
        # A customer × segment intersection: a share of a segment, not of the company.
        ("80", FY25, ((CUST, "uctt:LamResearchCorporationMember"), (BENCH, REV),
                      ("us-gaap:StatementBusinessSegmentsAxis", "uctt:ServicesMember"))),
        # An unknown benchmark (cost of sales) is skipped.
        ("40", FY25, ((CUST, "uctt:LamResearchCorporationMember"),
                      (BENCH, "us-gaap:CostOfGoodsAndServicesSoldMember"))),
        # The same fact tagged twice (a table and a note) is one.
        ("37.0", FY25, lam),
        # Receivables at the balance date, tagged as an instant.
        ("12.5", None, ((CUST, "uctt:AppliedMaterialsIncMember"), (BENCH, AR)), "2025-12-26"),
    ])


# ── The reader ──────────────────────────────────────────────────────────── #


class TestReader:
    def test_named_aggregate_and_receivables(self) -> None:
        items = concentrations_from_inline_xbrl(parse_inline_xbrl(_uctt()))
        got = [(i.customer, i.kind, i.share_pct, i.share_basis, i.period) for i in items]
        assert got == [
            ("Lam Research Corporation", "named", 37.0, "revenue", "FY2025"),
            ("Lam Research Corporation", "named", 31.9, "revenue", "FY2024"),
            ("Applied Materials Inc", "named", 21.7, "revenue", "FY2025"),
            ("Total Customer", "aggregate", 58.7, "revenue", "FY2025"),
            ("Lam Research Corporation", "named", 17.1, "receivables", "FY2025"),
            ("Applied Materials Inc", "named", 12.5, "receivables", "FY2025 year-end"),
        ]
        assert items[0].printed == "37.0" and items[0].end == date(2025, 12, 26)

    def test_a_frame_that_fails_its_check_yields_nothing(self) -> None:
        # Fiscal year focus 2023 for a period ending 2025-12-26: the frame is inconsistent.
        assert concentrations_from_inline_xbrl(parse_inline_xbrl(
            _doc([("37.0", FY25, ((CUST, "x:CustomerAMember"), (BENCH, REV)))], fy="2023"))) == []

    def test_out_of_range_and_future_periods(self) -> None:
        doc = _doc([
            ("0", FY25, ((CUST, "x:CustomerAMember"), (BENCH, REV))),
            ("120", FY25, ((CUST, "x:CustomerBMember"), (BENCH, REV))),
            ("15", ("2025-12-27", "2026-12-25"), ((CUST, "x:CustomerCMember"), (BENCH, REV))),
        ])
        assert concentrations_from_inline_xbrl(parse_inline_xbrl(doc)) == []

    def test_member_words_and_kinds(self) -> None:
        cases = {
            "bw:OneCustomerMember": ("One Customer", "anonymous"),
            "x:CustomerAMember": ("Customer A", "anonymous"),
            "x:LargestCustomerMember": ("Largest Customer", "anonymous"),
            "ichr:ThreeMajorCustomersMember": ("Three Major Customers", "aggregate"),
            "x:TwoCustomersMember": ("Two Customers", "aggregate"),
            "uctt:AppliedMaterialsInc.LamResearchCorporationAndASMLMember": (
                "Applied Materials Inc. Lam Research Corporation And ASML", "aggregate"),
            "x:GeneralElectricCompanyMember": ("General Electric Company", "named"),
            "x:SiemensEnergyAGMember": ("Siemens Energy AG", "named"),
        }
        for member, (words, kind) in cases.items():
            assert member_words(member) == words
            assert customer_kind(words) == kind, member


# ── The writer ──────────────────────────────────────────────────────────── #


class TestWriter:
    async def test_supplier_edges_with_share_and_citation(self, session) -> None:
        cfg = _cfg()
        uctt = await _entity(session, cfg, key="cik:1275014", name="Ultra Clean Holdings")
        items = concentrations_from_inline_xbrl(parse_inline_xbrl(_uctt()))
        url = "https://www.sec.gov/Archives/edgar/data/1275014/000162828026010744/uctt.htm"
        written = await record_concentrations(session, entity_id=uctt.id, concentrations=items,
                                              annual=ANNUAL, source_url=url, cfg=cfg)
        assert len(written) == 6
        rows = list((await session.execute(select(EntityRelationship))).scalars())
        assert {r.relationship_type for r in rows} == {"supplier_of"}
        assert all(r.subject_entity_id == uctt.id and r.object_entity_id is None for r in rows)
        lam = next(r for r in rows if r.counterparty_name_raw == "Lam Research Corporation"
                   and r.period == "FY2025" and r.share_basis == "revenue")
        assert lam.disclosed_share_pct == 37.0 and lam.extraction_method == "xbrl_dimension"
        assert lam.source_kind == "sec_xbrl" and lam.source_url == url
        assert lam.evidence_ref.startswith("sec:0001628280-26-010744:ixbrl:c-")
        assert lam.as_of == date(2026, 2, 24)
        assert "uctt:LamResearchCorporationMember" in lam.evidence_excerpt
        assert "printed '37.0' = 37% of revenue, FY2025" in lam.evidence_excerpt
        assert lam.note == "named customer as tagged by the filer (uctt:LamResearchCorporationMember)"
        # Receivables for the same customer and year is its own row (a year-end balance).
        lam_ar = next(r for r in rows if r.counterparty_name_raw == "Lam Research Corporation"
                      and r.share_basis == "receivables")
        assert lam_ar.period == "FY2025 year-end" and lam_ar.disclosed_share_pct == 17.1

        assert await ixbrl_already_read(session, entity_id=uctt.id,
                                        accession=ANNUAL["accession"])
        # A re-read of the same filing is idempotent.
        await record_concentrations(session, entity_id=uctt.id, concentrations=items,
                                    annual=ANNUAL, source_url=url, cfg=cfg)
        assert await session.scalar(select(func.count()).select_from(EntityRelationship)) == 6

        view = await company_relationships(
            session, company=SimpleNamespace(id=uuid.uuid4(), legal_entity_id=uctt.id), cfg=cfg)
        customers = view.groups["customers"]
        assert customers[0].counterparty_name == "Total Customer"  # largest share first
        assert {c.counterparty_name for c in customers} >= {
            "Lam Research Corporation", "Applied Materials Inc"}
        assert view.groups["suppliers"] == []

    def test_period_keys(self) -> None:
        item = SimpleNamespace(period="FY2025", share_basis="receivables")
        assert concentration_period(item) == "FY2025 year-end"
        item = SimpleNamespace(period="FY2025 year-end", share_basis="receivables")
        assert concentration_period(item) == "FY2025 year-end"
        assert concentration_period(SimpleNamespace(period="FY2025", share_basis="revenue")) \
            == "FY2025"


# ── The research step ───────────────────────────────────────────────────── #


class TestStepPart:
    async def _company(self, session, cfg) -> Any:
        entity = await _entity(session, cfg, key="cik:1275014", name="Ultra Clean Holdings")
        return SimpleNamespace(id=uuid.uuid4(), legal_entity_id=entity.id)

    async def test_reads_once_per_filing(self, session, monkeypatch) -> None:
        cfg = _cfg()
        company = await self._company(session, cfg)
        calls: list[str] = []

        async def read(cik: str, annual: dict[str, Any]) -> Any:
            calls.append(annual["accession"])
            return sr.AnnualInlineXbrl(url="u", document=parse_inline_xbrl(_uctt()))

        monkeypatch.setattr(relationship_step, "_live_annual_ixbrl", read)
        part = relationship_step.customer_concentration_part
        out = await part(session, company_id=company.id, entity_id=company.legal_entity_id,
                         cik="1275014", annual=ANNUAL, cfg=cfg)
        assert out["state"] == "written" and len(out["concentrations"]) == 6
        assert out["source_url"].endswith("/000162828026010744/uctt-20251226.htm")
        again = await part(session, company_id=company.id, entity_id=company.legal_entity_id,
                           cik="1275014", annual=ANNUAL, cfg=cfg)
        assert again == {"state": "already_read", "accession": ANNUAL["accession"]}
        assert calls == [ANNUAL["accession"]]

    async def test_none_tagged_is_not_none(self, session, monkeypatch) -> None:
        cfg = _cfg()
        company = await self._company(session, cfg)

        async def read(cik: str, annual: dict[str, Any]) -> Any:
            return sr.AnnualInlineXbrl(url="u", document=parse_inline_xbrl(_doc([])))

        monkeypatch.setattr(relationship_step, "_live_annual_ixbrl", read)
        out = await relationship_step.customer_concentration_part(
            session, company_id=company.id, entity_id=company.legal_entity_id, cik="1275014",
            annual=ANNUAL, cfg=cfg)
        assert out["state"] == "none_tagged"
        assert "not disclosed in XBRL is not the same as none" in out["notes"][0]

    async def test_states(self, session) -> None:
        cfg = _cfg()
        company = await self._company(session, cfg)
        part = relationship_step.customer_concentration_part
        kw = dict(company_id=company.id, entity_id=company.legal_entity_id, cik="1275014")
        assert await part(session, annual=ANNUAL, cfg=_cfg(v3_xbrl_concentration_enabled=False),
                          **kw) == {}
        assert (await part(session, annual={**ANNUAL, "form": "20-F"}, cfg=cfg, **kw))[
            "state"] == "not_applicable"
        assert (await part(session, annual=None, cfg=cfg, **kw))["state"] == "not_applicable"
        # The conftest keeps the 10-K body offline: an honest error, nothing written.
        out = await part(session, annual=ANNUAL, cfg=cfg, **kw)
        assert out == {"state": "error", "accession": ANNUAL["accession"],
                       "errors": ["offline test"]}
        bad = await part(session, annual={**ANNUAL, "primary_document": "../x.htm"}, cfg=cfg, **kw)
        assert bad["state"] == "error"


class TestFetch:
    async def test_annual_ixbrl_fetch_and_refusals(self) -> None:
        async def ok(url: str, accept: str) -> tuple[int, bytes | None]:
            assert url.endswith("/1275014/000162828026010744/uctt-20251226.htm")
            return 200, _uctt()

        read = await sr.fetch_annual_inline_xbrl("1275014", ANNUAL, fetch=ok)
        assert read.error is None and read.document.fiscal_year_focus == 2025

        async def gone(url: str, accept: str) -> tuple[int, bytes | None]:
            return 404, None

        assert (await sr.fetch_annual_inline_xbrl("1275014", ANNUAL, fetch=gone)).error == \
            "annual report returned HTTP 404"

        async def boom(url: str, accept: str) -> tuple[int, bytes | None]:
            raise TimeoutError

        assert (await sr.fetch_annual_inline_xbrl("1275014", ANNUAL, fetch=boom)).error == \
            "annual report unread (TimeoutError)"
        unsafe = await sr.fetch_annual_inline_xbrl(
            "1275014", {**ANNUAL, "primary_document": "../../etc"}, fetch=ok)
        assert unsafe.url is None and unsafe.error

    async def test_the_report_carries_the_annual_filing(self) -> None:
        from tests.test_v3_20_2_3_sec_relationships import _report

        report = await _report()
        assert report.annual["form"] == "10-K"
        assert report.annual["accession"] == "0000080420-25-000152"
