"""V3.20 slice 2.3 — Exhibit 21 subsidiaries and Schedule 13D/13G holders.

Fixtures are trimmed copies of real EDGAR documents fetched 2026-11-15: Powell Industries'
FY2025 Exhibit 21.1 (it lists itself first), Tennant's Exhibit 21 (a "Joint Ventures"
section), Babcock & Wilcox's Exhibit 21.1 (a % column, one 15 % holding), a First Trust
SCHEDULE 13G on Powell (three joint filers, 5.32 %), Vanguard's SCHEDULE 13G/A exit
(0.0 %) and a BRC SCHEDULE 13D/A on Babcock & Wilcox (18.9 %).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.integrations import sec_relationships as sr
from app.models.legal_entity import EntityRelationship
from app.services.entities.identifiers import SCHEME_CIK
from app.services.entities.master import record_identifier
from app.services.entities.relationship_view import company_relationships
from app.services.entities.sec_relationships import record_sec_relationships
from app.services.pipeline import relationship_step
from tests import test_v3_entity_relationships as _base
from tests.test_v3_entity_relationships import _cfg, _entity

session = _base.session

POWELL_CIK = "0000080420"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data/80420"

POWELL_EX21 = b"""<html><body><p>EXHIBIT 21.1</p><p>SUBSIDIARIES OF POWELL INDUSTRIES, INC.</p>
<table><tr><td><b>Name of Subsidiary</b></td><td></td><td><b>Incorporated</b></td></tr>
<tr><td>Powell Industries, Inc.</td><td></td><td>Delaware</td></tr>
<tr><td>Powell Electrical Systems, Inc.</td><td>&#160;</td><td>Delaware</td></tr>
<tr><td>Powell (UK) Limited</td><td></td><td>United Kingdom</td></tr>
<tr><td>Powell Canada Inc.</td><td></td><td>Canada</td></tr>
<tr><td>Powell Equipment (Mexico) SA de CV</td><td></td><td>Mexico</td></tr>
</table></body></html>"""

TENNANT_EX21 = """<table><tr><td>Subsidiary</td><td>Jurisdiction of Organization</td></tr>
<tr><td>Applied Kehrmaschinen GmbH</td><td>Federal Republic of Germany</td></tr>
<tr><td>Tennant Magyarorsz&#225;g Korl&#225;tolt Felel&#337;ss&#233;g&#369; T&#225;rsas&#225;g</td>
<td>Hungary</td></tr>
<tr><td>Joint Ventures</td><td></td></tr>
<tr><td>I-Team North America B.V.</td><td>Netherlands</td></tr></table>""".encode()

BW_EX21 = b"""<table><tr><td>Name of Company</td><td>Jurisdiction of Organization</td>
<td>Percentage of Ownership Interest</td></tr>
<tr><td>1867BW, LLC</td><td>Delaware</td><td>100</td></tr>
<tr><td>Great Arrow Builders, LLC</td><td>Delaware</td><td>15</td></tr></table>"""

PAREN_EX21 = b"""<p>Subsidiaries</p><p>Acme Grid Ltd (England and Wales)</p>
<p>Acme Cable GmbH (Germany)</p><p>Some unrelated sentence.</p>"""


def _13g(percent: str, event: str = "09/30/2026", *, issuer: str = POWELL_CIK,
         form: str = "SCHEDULE 13G", names: tuple[str, ...] = (
             "First Trust Portfolios L.P.", "First Trust Advisors L.P.",
             "The Charger Corporation"), filer: str = "0001184765") -> bytes:
    persons = "".join(
        f"""<coverPageHeaderReportingPersonDetails><reportingPersonName>{n}</reportingPersonName>
        <classPercent>{percent}</classPercent><typeOfReportingPerson>IA</typeOfReportingPerson>
        </coverPageHeaderReportingPersonDetails>""" for n in names)
    return f"""<?xml version="1.0" encoding="UTF-8"?><edgarSubmission
    xmlns="http://www.sec.gov/edgar/schedule13g" xmlns:com="http://www.sec.gov/edgar/common">
    <headerData><submissionType>{form}</submissionType><filerInfo><filer><filerCredentials>
    <cik>{filer}</cik><ccc>XXXXXXXX</ccc></filerCredentials></filer></filerInfo></headerData>
    <formData><coverPageHeader><securitiesClassTitle>Common</securitiesClassTitle>
    <eventDateRequiresFilingThisStatement>{event}</eventDateRequiresFilingThisStatement>
    <issuerInfo><issuerCik>{issuer}</issuerCik><issuerName>Powell Industries, Inc.</issuerName>
    </issuerInfo></coverPageHeader>{persons}</formData></edgarSubmission>""".encode()


BRC_13D = b"""<?xml version="1.0" encoding="UTF-8"?><edgarSubmission
xmlns="http://www.sec.gov/edgar/schedule13D"><headerData><submissionType>SCHEDULE 13D/A
</submissionType><filerInfo><filer><filerCredentials><cik>0001464790</cik></filerCredentials>
</filer></filerInfo></headerData><formData><coverPageHeader><amendmentNo>21</amendmentNo>
<securitiesClassTitle>Common Stock, par value $0.01</securitiesClassTitle>
<dateOfEvent>11/12/2026</dateOfEvent><issuerInfo><issuerCIK>0001630805</issuerCIK>
</issuerInfo></coverPageHeader><reportingPersons>
<reportingPersonInfo><reportingPersonCIK>0001464790</reportingPersonCIK>
<reportingPersonName>BRC Group Holdings, Inc.</reportingPersonName>
<percentOfClass>18.9</percentOfClass><typeOfReportingPerson>HC</typeOfReportingPerson>
</reportingPersonInfo><reportingPersonInfo><reportingPersonNoCIK>Y</reportingPersonNoCIK>
<reportingPersonName>BRF Investments, LLC</reportingPersonName>
<percentOfClass>8.2</percentOfClass></reportingPersonInfo></reportingPersons></formData>
</edgarSubmission>"""

INDEX_PAGE = b"""<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>10-K</td><td><a href="/x/powl-20250930.htm">powl-20250930.htm</a></td>
<td>10-K</td><td>1</td></tr>
<tr><td>5</td><td>EX-21.1</td><td><a href="/x/ex21.htm">exhibit21110k2025.htm</a></td>
<td>EX-21.1</td><td>12904</td></tr></table>"""


def _submissions(*rows: tuple[str, str, str]) -> bytes:
    return json.dumps({"filings": {"recent": {
        "form": [r[0] for r in rows], "accessionNumber": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows], "primaryDocument": ["x" for _ in rows],
    }}}).encode()


def _acc_dir(acc: str) -> str:
    return f"{ARCHIVES}/{acc.replace('-', '')}"


def _routes(**extra: Any) -> dict[str, Any]:
    routes: dict[str, Any] = {
        sr.submissions_url(POWELL_CIK): (200, _submissions(
            ("SCHEDULE 13G", "0001445546-26-008034", "2026-10-27"),
            ("SCHEDULE 13G/A", "0000102909-26-002105", "2026-03-27"),
            ("10-K", "0000080420-25-000152", "2025-11-19"),
            ("SCHEDULE 13G", "0001445546-25-007228", "2025-10-31"),
            ("SC 13G/A", "0000354204-24-004692", "2024-10-31"),
        )),
        f"{_acc_dir('0000080420-25-000152')}/0000080420-25-000152-index.htm": (200, INDEX_PAGE),
        f"{_acc_dir('0000080420-25-000152')}/exhibit21110k2025.htm": (200, POWELL_EX21),
        f"{_acc_dir('0001445546-26-008034')}/primary_doc.xml": (200, _13g("5.32")),
        f"{_acc_dir('0000102909-26-002105')}/primary_doc.xml": (200, _13g(
            "0.0", "03/13/2026", names=("The Vanguard Group",), filer="0000102909",
            form="SCHEDULE 13G/A")),
        # An older First Trust 13G — superseded by the 2026 one, never read as current.
        f"{_acc_dir('0001445546-25-007228')}/primary_doc.xml": (200, _13g("6.1", "09/30/2025")),
    }
    routes.update(extra)
    return routes


def _fetcher(routes: dict[str, Any]):
    calls: list[str] = []

    async def fetch(url: str, accept: str) -> tuple[int, bytes | None]:
        calls.append(url)
        hit = routes.get(url)
        if isinstance(hit, Exception):
            raise hit
        return hit if hit is not None else (404, None)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


async def _report(**extra: Any) -> sr.SecRelationshipReport:
    return await sr.fetch_sec_relationships(
        POWELL_CIK, registrant_name="Powell Industries, Inc.", fetch=_fetcher(_routes(**extra)))


# ── Parsers ─────────────────────────────────────────────────────────────── #


class TestExhibitParser:
    def test_powell_skips_its_header_and_itself(self) -> None:
        rows, truncated = sr.parse_subsidiary_exhibit(
            POWELL_EX21, registrant_name="Powell Industries, Inc.")
        assert not truncated
        assert [(r.name, r.jurisdiction) for r in rows] == [
            ("Powell Electrical Systems, Inc.", "Delaware"),
            ("Powell (UK) Limited", "United Kingdom"),
            ("Powell Canada Inc.", "Canada"),
            ("Powell Equipment (Mexico) SA de CV", "Mexico"),
        ]

    def test_sections_entities_and_unicode(self) -> None:
        rows, _ = sr.parse_subsidiary_exhibit(TENNANT_EX21, registrant_name="Tennant Company")
        assert rows[1].name.startswith("Tennant Magyarország Korlátolt")
        assert (rows[-1].name, rows[-1].section) == ("I-Team North America B.V.", "Joint Ventures")
        assert rows[0].section is None

    def test_percentage_column(self) -> None:
        rows, _ = sr.parse_subsidiary_exhibit(BW_EX21)
        assert [(r.name, r.ownership_pct) for r in rows] == [
            ("1867BW, LLC", 100.0), ("Great Arrow Builders, LLC", 15.0)]

    def test_paren_lines_fallback(self) -> None:
        rows, _ = sr.parse_subsidiary_exhibit(PAREN_EX21)
        assert [(r.name, r.jurisdiction) for r in rows] == [
            ("Acme Grid Ltd", "England and Wales"), ("Acme Cable GmbH", "Germany")]

    def test_total_on_garbage_and_capped(self) -> None:
        assert sr.parse_subsidiary_exhibit(None) == ([], False)
        assert sr.parse_subsidiary_exhibit(b"<table><tr><td>") == ([], False)
        many = b"<table>" + b"".join(
            f"<tr><td>Sub {i} LLC</td><td>Delaware</td></tr>".encode() for i in range(600)
        ) + b"</table>"
        rows, truncated = sr.parse_subsidiary_exhibit(many)
        assert len(rows) == sr.MAX_SUBSIDIARIES and truncated


class TestScheduleParser:
    def test_13g_joint_filers(self) -> None:
        parsed = sr.parse_schedule13(_13g("5.32"))
        assert parsed["event_date"] == date(2026, 9, 30)
        assert parsed["filer_cik"] == "0001184765" and parsed["issuer_cik"] == POWELL_CIK
        assert [p.percent_of_class for p in parsed["persons"]] == [5.32, 5.32, 5.32]

    def test_13d(self) -> None:
        parsed = sr.parse_schedule13(BRC_13D)
        assert parsed["event_date"] == date(2026, 11, 12)
        lead = sr.HolderFiling("SCHEDULE 13D/A", "a", None, "u", "0001464790", None, None,
                               persons=parsed["persons"]).lead
        assert (lead.name, lead.percent_of_class, lead.cik) == (
            "BRC Group Holdings, Inc.", 18.9, "0001464790")

    def test_refuses_entities_and_garbage(self) -> None:
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
        assert sr.parse_schedule13(bomb) is None
        assert sr.parse_schedule13(b"<not-xml") is None
        assert sr.parse_schedule13(b"<edgarSubmission/>") is None
        assert sr.parse_schedule13(None) is None


# ── The lookup ──────────────────────────────────────────────────────────── #


class TestLookup:
    async def test_reads_exhibit_and_newest_filing_per_holder(self) -> None:
        report = await _report()
        assert report.errors == []
        subs = report.subsidiaries
        assert (subs.form, subs.exhibit_type, subs.accession) == (
            "10-K", "EX-21.1", "0000080420-25-000152")
        assert subs.source_url.endswith("/000008042025000152/exhibit21110k2025.htm")
        assert len(subs.rows) == 4
        by_filer = {h.filer_cik: h for h in report.holders}
        assert set(by_filer) == {"0001184765", "0000102909"}
        assert by_filer["0001184765"].accession == "0001445546-26-008034"  # newest wins
        assert report.legacy_holder_filings_skipped == 1
        assert "pre-2025 SC 13D/13G" in report.notes[-1]

    async def test_a_filing_about_another_issuer_is_skipped(self) -> None:
        report = await _report(**{
            f"{_acc_dir('0001445546-26-008034')}/primary_doc.xml": (
                200, _13g("9.0", issuer="0000000001"))})
        assert "0001445546-26-008034 is about another issuer; skipped" in report.notes

    async def test_failures_are_contained(self) -> None:
        report = await sr.fetch_sec_relationships(POWELL_CIK, fetch=_fetcher({}))
        assert report.errors == ["EDGAR submissions returned HTTP 404"]
        report = await _report(**{
            f"{_acc_dir('0000080420-25-000152')}/0000080420-25-000152-index.htm":
                TimeoutError("slow"),
            f"{_acc_dir('0001445546-26-008034')}/primary_doc.xml": (200, b"<html>"),
        })
        assert "subsidiary exhibit unread (TimeoutError)" in report.errors
        assert "SCHEDULE 13G 0001445546-26-008034 not readable XML" in report.errors
        assert report.subsidiaries is None and len(report.holders) == 2  # the 2025 one stands

    async def test_no_exhibit_is_a_note(self) -> None:
        report = await _report(**{
            f"{_acc_dir('0000080420-25-000152')}/0000080420-25-000152-index.htm": (
                200, INDEX_PAGE.replace(b"EX-21.1", b"EX-23.1"))})
        assert report.subsidiaries is None
        assert any("has no EX-21 exhibit" in n for n in report.notes)

    async def test_a_bad_cik_asks_nothing(self) -> None:
        fetch = _fetcher({})
        report = await sr.fetch_sec_relationships("not-a-cik", fetch=fetch)
        assert report.errors == ["no usable CIK"] and fetch.calls == []  # type: ignore[attr-defined]


# ── The writer ──────────────────────────────────────────────────────────── #


async def _open(session, **where: Any) -> list[EntityRelationship]:
    stmt = select(EntityRelationship).where(EntityRelationship.effective_to.is_(None))
    for k, v in where.items():
        stmt = stmt.where(getattr(EntityRelationship, k) == v)
    return list((await session.execute(stmt)).scalars())


class TestWriter:
    async def test_subsidiaries_and_holders(self, session) -> None:
        cfg = _cfg()
        powell = await _entity(session, cfg, key="cik:80420", name="Powell Industries, Inc.")
        result = await record_sec_relationships(
            session, entity_id=powell.id, report=await _report(), cfg=cfg,
            today=date(2026, 11, 15))
        assert result.state == "written" and result.subsidiaries_written == 4

        subs = await _open(session, subject_entity_id=powell.id, relationship_type="parent_of")
        canada = next(e for e in subs if e.counterparty_name_raw == "Powell Canada Inc.")
        assert canada.object_entity_id is None and canada.note == "Jurisdiction: Canada"
        assert canada.extraction_method == "exhibit_21" and canada.source_kind == "sec_exhibit"
        assert canada.evidence_ref == "sec:0000080420-25-000152:EX-21.1"
        assert canada.as_of == date(2025, 11, 19)
        assert canada.evidence_excerpt == "10-K EX-21.1 filed 2025-11-19: Powell Canada Inc. | Canada"

        (holder,) = await _open(session, object_entity_id=powell.id,
                                relationship_type="significant_holder_of")
        assert holder.counterparty_name_raw == "First Trust Portfolios L.P."
        assert (holder.disclosed_share_pct, holder.share_basis) == (5.32, "share_class")
        assert holder.as_of == date(2026, 9, 30) and holder.source_kind == "sec_13g"
        assert holder.extraction_method == "filing_xml"
        assert "filed jointly with First Trust Advisors L.P., The Charger Corporation" \
            in holder.evidence_excerpt
        assert holder.note.startswith("passive holder (Schedule 13G)")
        assert [h["state"] for h in result.holders] == ["recorded", "at_or_below_5pct"]

        view = await company_relationships(
            session, company=SimpleNamespace(id=uuid.uuid4(), legal_entity_id=powell.id),
            cfg=cfg)
        assert len(view.groups["subsidiaries"]) == 4
        assert view.groups["significant_holders"][0].disclosed_share_pct == 5.32

    async def test_a_minority_row_and_a_jv_section(self, session) -> None:
        cfg = _cfg()
        bw = await _entity(session, cfg, key="cik:1630805", name="Babcock & Wilcox")
        rows = (sr.parse_subsidiary_exhibit(BW_EX21)[0]
                + sr.parse_subsidiary_exhibit(TENNANT_EX21)[0][-1:])
        listing = sr.SubsidiaryList("10-K", "acc-1", date(2026, 3, 1), "EX-21.1", "u", rows=rows)
        result = await record_sec_relationships(
            session, entity_id=bw.id, cfg=cfg,
            report=sr.SecRelationshipReport(cik="1630805", subsidiaries=listing))
        assert (result.subsidiaries_written, result.minority_holdings_written,
                result.non_subsidiary_rows) == (1, 1, 1)
        (stake,) = await _open(session, subject_entity_id=bw.id,
                               relationship_type="significant_holder_of")
        assert (stake.counterparty_name_raw, stake.disclosed_share_pct, stake.share_basis) == (
            "Great Arrow Builders, LLC", 15.0, "capital")

    async def test_a_dropped_subsidiary_is_closed_not_deleted(self, session) -> None:
        cfg = _cfg()
        co = await _entity(session, cfg, key="cik:1", name="Co")

        def listing(acc: str, day: date, names: list[str], **kw: Any) -> Any:
            return sr.SecRelationshipReport(cik="1", subsidiaries=sr.SubsidiaryList(
                "10-K", acc, day, "EX-21", f"u/{acc}",
                rows=[sr.SubsidiaryRow(n, "Delaware") for n in names], **kw))

        await record_sec_relationships(session, entity_id=co.id, cfg=cfg,
                                       report=listing("a1", date(2025, 3, 1), ["A LLC", "B LLC"]))
        # A truncated list proves nothing about what is missing.
        await record_sec_relationships(session, entity_id=co.id, cfg=cfg, report=listing(
            "a2", date(2026, 3, 1), ["A LLC"], truncated=True))
        assert len(await _open(session, subject_entity_id=co.id)) == 2
        result = await record_sec_relationships(session, entity_id=co.id, cfg=cfg,
                                                report=listing("a3", date(2026, 3, 2), ["A LLC"]))
        assert result.subsidiaries_closed == 1
        (left,) = await _open(session, subject_entity_id=co.id)
        assert left.counterparty_name_raw == "A LLC" and left.source_url == "u/a3"
        assert left.as_of == date(2026, 3, 2)  # the citation moved to the newer exhibit
        closed = (await session.execute(select(EntityRelationship).where(
            EntityRelationship.effective_to.is_not(None)))).scalar_one()
        assert closed.counterparty_name_raw == "B LLC"

    async def test_an_empty_exhibit_closes_nothing(self, session) -> None:
        cfg = _cfg()
        co = await _entity(session, cfg, key="cik:1", name="Co")
        await record_sec_relationships(session, entity_id=co.id, cfg=cfg, report=sr.SecRelationshipReport(
            cik="1", subsidiaries=sr.SubsidiaryList("10-K", "a", date(2025, 1, 1), "EX-21", "u",
                                                    rows=[sr.SubsidiaryRow("A LLC")])))
        result = await record_sec_relationships(session, entity_id=co.id, cfg=cfg, report=sr.SecRelationshipReport(
            cik="1", subsidiaries=sr.SubsidiaryList("10-K", "b", date(2026, 1, 1), "EX-21", "u",
                                                    state="empty")))
        assert result.subsidiaries_closed == 0 and "no subsidiary row" in result.notes[0]

    async def test_a_holder_amendment_updates_and_an_exit_closes(self, session) -> None:
        cfg = _cfg()
        co = await _entity(session, cfg, key="cik:80420", name="Powell Industries, Inc.")
        trust = await _entity(session, cfg, key="cik:1184765", name="First Trust")
        await record_identifier(session, scheme=SCHEME_CIK, value="1184765", cfg=cfg,
                                legal_entity_id=trust.id, source="sec_edgar")

        def filing(pct: str, event: str, acc: str) -> Any:
            parsed = sr.parse_schedule13(_13g(pct, event, names=("First Trust Advisors L.P.",)))
            return sr.HolderFiling("SCHEDULE 13G/A", acc, None, f"u/{acc}", "0001184765",
                                   parsed["event_date"], "Common", persons=parsed["persons"])

        for pct, event, acc in (("6.1", "09/30/2025", "a1"), ("7.4", "03/31/2026", "a2")):
            await record_sec_relationships(session, entity_id=co.id, cfg=cfg,
                                           report=sr.SecRelationshipReport(
                                               cik="80420", holders=[filing(pct, event, acc)]))
        (edge,) = await _open(session, object_entity_id=co.id)
        assert edge.subject_entity_id == trust.id  # resolved by CIK
        assert (edge.disclosed_share_pct, edge.source_url) == (7.4, "u/a2")
        result = await record_sec_relationships(
            session, entity_id=co.id, cfg=cfg, report=sr.SecRelationshipReport(
                cik="80420", holders=[filing("4.2", "09/30/2026", "a3")]))
        assert result.holders_closed == ["First Trust Advisors L.P."]
        assert await _open(session, object_entity_id=co.id) == []

    async def test_disabled(self, session) -> None:
        result = await record_sec_relationships(
            session, entity_id=uuid.uuid4(), report=await _report(),
            cfg=_cfg(v3_entity_master_enabled=False))
        assert result.state == "disabled"


# ── The research step ───────────────────────────────────────────────────── #


class TestResearchStep:
    async def test_offline_by_default_and_states(self, session, monkeypatch) -> None:
        cfg = _cfg()
        co = await _entity(session, cfg, key="cik:80420", name="Powell Industries, Inc.")
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=co.id, ticker="POWL",
                                  exchange="NASDAQ")
        assert await relationship_step.sec_relationships_step(
            session, company, cfg=_cfg(v3_sec_relationships_enabled=False)) == {}
        assert (await relationship_step.sec_relationships_step(
            session, SimpleNamespace(legal_entity_id=None), cfg=cfg))["state"] == \
            "entity_unresolved"

        async def basis(*_a: Any, **_kw: Any) -> Any:
            return SimpleNamespace(cik=POWELL_CIK, ticker="POWL", exchange="NASDAQ")

        monkeypatch.setattr("app.services.entities.sec_listing.sec_listing_for", basis)
        out = await relationship_step.sec_relationships_step(session, company, cfg=cfg)
        assert out["state"] == "written" and out["notes"] == ["offline test"]
        assert out["cik"] == POWELL_CIK

        async def live(cik: str, name: Any) -> Any:
            assert name == "Powell Industries, Inc."
            return await _report()

        monkeypatch.setattr(relationship_step, "_live_sec_lookup", live)
        out = await relationship_step.sec_relationships_step(session, company, cfg=cfg)
        assert out["subsidiaries_written"] == 4 and len(out["holders"]) == 2

    async def test_not_a_registrant_and_failure(self, session, monkeypatch) -> None:
        cfg = _cfg()
        co = await _entity(session, cfg, key="cik:80420", name="Powell")
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=co.id)

        async def none(*_a: Any, **_kw: Any) -> Any:
            return None

        monkeypatch.setattr("app.services.entities.sec_listing.sec_listing_for", none)
        assert (await relationship_step.sec_relationships_step(
            session, company, cfg=cfg))["state"] == "not_sec_registrant"

        async def boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("x")

        monkeypatch.setattr("app.services.entities.sec_listing.sec_listing_for", boom)
        assert await relationship_step.sec_relationships_step(session, company, cfg=cfg) == {
            "state": "error", "errors": ["SEC relationship step failed (RuntimeError)"]}
