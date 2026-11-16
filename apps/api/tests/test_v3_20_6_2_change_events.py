"""V3.20 slice 6.2 — change-event classification and SEC Form 4 insider clusters."""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.integrations.sec_form4 import (
    Form4Report,
    InsiderTransaction,
    fetch_form4_transactions,
    insider_clusters,
    parse_form4,
    raw_xml_name,
)
from app.services.discovery.change_events import (
    LABELS,
    TRIGGER_TYPES,
    change_events_block,
    classify_title,
    events_from_documents,
)
from app.services.safety_terms import scan_value
from tests import test_v3_entity_relationships as _base

session = _base.session

TODAY = date(2026, 11, 16)


# ── Titles ──────────────────────────────────────────────────────────────── #


@pytest.mark.parametrize("title, expected", [
    ("Binding offtake agreement signed with a European utility", "first_offtake"),
    ("Mining lease granted for the Northern Project", "permit_granted"),
    ("Environmental approval received for the converter station", "permit_granted"),
    ("Awarded DOE grant of USD 50m", "government_contract_award"),
    ("Department of Energy loan conditional commitment", "government_contract_award"),
    ("Anti-dumping duties imposed on imported electrical steel", "customs_flow_shift"),
    ("Added to the critical minerals list", "export_control_or_stockpile"),
    ("Refinancing of convertible notes due 2027", "maturity_refinanced"),
    ("Strategic investment by an industrial partner", "strategic_or_state_investor"),
    ("Product qualification completed with tier-1 OEM", "capacity_qualification"),
    ("Commercial operation achieved at 200 MW plant", "capacity_qualification"),
    ("US patent granted for converter design", "patent_cluster"),
])
def test_titles_map_to_the_schema_vocabulary(title: str, expected: str) -> None:
    hit = classify_title(title)
    assert hit is not None and hit[0] == expected
    assert expected in TRIGGER_TYPES and expected in LABELS


@pytest.mark.parametrize("title", [
    "Shareholder approval of issue of securities",  # a corporate action, not a permit
    "Contract award with a private utility",  # not a government award
    "Jane Doe appointed director",  # "Doe" is not the DOE
    "Auditor qualified opinion",
    "Qualified Person statement",
    "Feed-in tariff update",  # a power price, not a trade remedy
    "Notice of Annual General Meeting",
    "Option grant to employees under the federal plan",
    "",
    None,
])
def test_non_events_are_not_labelled(title: str | None) -> None:
    assert classify_title(title) is None


def test_an_ended_event_is_negated_not_counted() -> None:
    assert classify_title("Termination of offtake agreement") == ("negated", "offtake")
    assert classify_title("Permit application refused") is None or \
        classify_title("Permit application refused")[0] == "negated"


def _doc(title: str, published: str, url: str = "https://example.com/d") -> dict:
    return {"title": title, "published_at": published, "canonical_url": url,
            "document_type": "regulatory_announcement", "source_tier": "T1"}


def test_window_negation_and_patent_cluster() -> None:
    docs = [
        _doc("Binding offtake agreement signed", "2026-10-01"),
        _doc("Binding offtake agreement signed", "2025-01-01"),  # outside the window
        _doc("Termination of offtake agreement", "2026-09-01"),
        _doc("Quarterly activities report", "2026-10-20"),
        _doc("Permit granted", "2027-01-01"),  # dated in the future: ignored
        {"title": "Strategic investment", "published_at": None},
        _doc("Patent granted for bushing design", "2026-01-10"),
        _doc("Patent granted for tap changer", "2026-05-10"),
    ]
    out = events_from_documents(docs, today=TODAY)
    assert [e["trigger_type"] for e in out["events"]] == ["first_offtake"]
    assert [e["title"] for e in out["negated"]] == ["Termination of offtake agreement"]
    assert out["undated"] == 1 and out["considered"] == 3
    e = out["events"][0]
    assert e["source_url"] == "https://example.com/d" and e["source_kind"] == \
        "issuer_disclosure" and e["date"] == "2026-10-01"

    # Two grants are not a cluster; a third within a year is.
    docs.append(_doc("US patent issued for converter valve", "2026-11-01"))
    out = events_from_documents(docs, today=TODAY)
    patent = [e for e in out["events"] if e["trigger_type"] == "patent_cluster"]
    assert len(patent) == 1 and len(patent[0]["members"]) == 3


def test_issuer_prose_is_neutralised() -> None:
    out = events_from_documents(
        [_doc("Offtake agreement; analyst says buy", "2026-11-01")], today=TODAY)
    assert scan_value(out) == []


# ── Form 4 ──────────────────────────────────────────────────────────────── #


def _form4(owner_cik: str, owner: str, rows: list[tuple[str, str, str, str, str]],
           *, joint: bool = False, officer: str | None = None) -> bytes:
    txs = "".join(
        f"""<nonDerivativeTransaction>
              <securityTitle><value>Common Stock</value></securityTitle>
              <transactionDate><value>{d}</value></transactionDate>
              <transactionCoding><transactionFormType>4</transactionFormType>
                <transactionCode>{code}</transactionCode></transactionCoding>
              <transactionAmounts>
                <transactionShares><value>{shares}</value></transactionShares>
                <transactionPricePerShare>{price}</transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
              </transactionAmounts>
            </nonDerivativeTransaction>"""
        for d, code, shares, price, ad in rows)
    rel = (f"<isOfficer>1</isOfficer><officerTitle>{officer}</officerTitle>" if officer
           else "<isDirector>1</isDirector>")
    second = ("<reportingOwner><reportingOwnerId><rptOwnerCik>0000000999</rptOwnerCik>"
              "<rptOwnerName>Fund GP</rptOwnerName></reportingOwnerId></reportingOwner>"
              if joint else "")
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000123456</issuerCik></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>{owner_cik}</rptOwnerCik>
      <rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>{rel}</reportingOwnerRelationship>
  </reportingOwner>{second}
  <nonDerivativeTable>{txs}</nonDerivativeTable>
</ownershipDocument>""".encode()


def _price(p: str) -> str:
    return f"<value>{p}</value>" if p else "<footnoteId id='F1'/>"


def test_parse_form4_reads_purchases_and_refuses_entities() -> None:
    body = _form4("0000000101", "Doe Jane", [
        ("2026-10-01", "P", "1,000", _price("12.50"), "A"),
        ("2026-10-02", "S", "500", _price("13"), "D"),
        ("2026-10-03", "A", "2000", _price(""), "A"),  # a grant: no price, not a buy
    ], officer="CFO")
    txs = parse_form4(body)
    assert txs is not None and len(txs) == 3
    buy = txs[0]
    assert buy.is_open_market_purchase and buy.shares == 1000 and buy.price_usd == 12.5
    assert buy.insider_key == "cik:101" and buy.relationship == "officer (CFO)"
    assert [t.is_open_market_purchase for t in txs] == [True, False, False]
    assert txs[2].price_usd is None
    joint = parse_form4(_form4("0000000102", "Fund LP", [], joint=True))
    assert joint == []
    assert parse_form4(b'<!DOCTYPE x [<!ENTITY a "b">]><ownershipDocument/>') is None
    assert parse_form4(b"<html/>") is None and parse_form4(None) is None


def _tx(key: str, day: str, shares: float = 100, price: float | None = 10.0
        ) -> InsiderTransaction:
    return InsiderTransaction(
        insider_key=key, insider_name=key.upper(), relationship="director",
        transaction_date=date.fromisoformat(day), code="P", acquired_disposed="A",
        shares=shares, price_usd=price, security_title="Common Stock",
        source_url=f"https://www.sec.gov/{key}.xml")


def test_cluster_needs_three_distinct_insiders_within_thirty_days() -> None:
    two = [_tx("a", "2026-10-01"), _tx("b", "2026-10-05"), _tx("a", "2026-10-07")]
    assert insider_clusters(two) == []
    spread = [_tx("a", "2026-01-01"), _tx("b", "2026-02-15"), _tx("c", "2026-04-01")]
    assert insider_clusters(spread) == []
    buys = [_tx("a", "2026-10-01"), _tx("b", "2026-10-10"), _tx("c", "2026-10-25"),
            _tx("d", "2026-11-05")]  # overlapping windows merge into one cluster
    clusters = insider_clusters(buys)
    assert len(clusters) == 1
    c = clusters[0]
    assert (c["start_date"], c["end_date"], c["distinct_insiders"], c["purchases"]) == \
        ("2026-10-01", "2026-11-05", 4, 4)
    assert c["value_usd"] == 4000.0 and c["currency"] == "USD"
    assert c["trigger_type"] == "insider_cluster_buy"
    unpriced = insider_clusters([*buys[:2], _tx("c", "2026-10-25", price=None)])
    assert unpriced[0]["value_usd"] is None  # never a partial sum as the value


async def test_fetch_reads_recent_form4s_with_raw_xml() -> None:
    submissions = {"filings": {"recent": {
        "form": ["4", "10-K", "4", "4"],
        "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002",
                            "0000000001-26-000003", "0000000001-20-000004"],
        "filingDate": ["2026-10-02", "2026-03-01", "2026-10-11", "2020-01-01"],
        "primaryDocument": ["xslF345X05/a.xml", "k.htm", "xslF345X05/b.xml",
                            "xslF345X05/old.xml"],
    }}}
    seen: list[str] = []

    async def fetch(url: str, accept: str) -> tuple[int, bytes | None]:
        seen.append(url)
        if url.endswith(".json"):
            return 200, json.dumps(submissions).encode()
        if url.endswith("/a.xml"):
            return 200, _form4("0000000101", "A", [("2026-10-01", "P", "10",
                                                     _price("5"), "A")])
        return 404, None

    report = await fetch_form4_transactions("123456", fetch=fetch, today=TODAY)
    assert report.filings_available == 2 and report.filings_read == 1
    assert len(report.transactions) == 1
    tx = report.transactions[0]
    assert tx.accession == "0000000001-26-000001" and tx.source_url is not None
    assert tx.source_url.endswith("/000000000126000001/a.xml")
    assert not any("old.xml" in u or "k.htm" in u for u in seen)
    assert any("unreadable" in n for n in report.notes)
    assert raw_xml_name("xslF345X05/x.xml") == "x.xml" and raw_xml_name("x.htm") is None


# ── Step + block ────────────────────────────────────────────────────────── #


def test_block_is_none_when_nothing_was_found() -> None:
    empty = {"events": [], "negated": [], "considered": 4, "undated": 0}
    assert change_events_block(classified=empty, insider=None) is None
    assert change_events_block(classified=empty,
                               insider={"clusters": [], "lookup": {}}) is None


async def test_section_flags_and_insider_lookup(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.services.pipeline.change_events_step import change_events_section

    company = Company(id=uuid.uuid4(), ticker="EVT", exchange="NASDAQ", name="Events Inc")
    session.add(company)
    await session.flush()
    assert await change_events_section(session, company, cfg=SimpleNamespace()) is None

    # No held documents and no CIK: nothing to show.
    cfg = SimpleNamespace(v3_change_events_enabled=True, v3_insider_clusters_enabled=True)
    assert await change_events_section(session, company, cfg=cfg, today=TODAY) is None

    from app.models.legal_entity import EntityIdentifier, LegalEntity

    entity = LegalEntity(id=uuid.uuid4(), entity_key="test:evt", legal_name="Events Inc",
                         normalized_name="events inc")
    session.add(entity)
    await session.flush()
    session.add(EntityIdentifier(id=uuid.uuid4(), legal_entity_id=entity.id, scheme="cik",
                                 value="123456", value_normalized="123456", scope_key="*",
                                 source="test"))
    company.legal_entity_id = entity.id
    await session.flush()

    calls: list[str] = []

    async def lookup(cik: str, today: date) -> Form4Report:
        calls.append(cik)
        return Form4Report(cik=cik, filings_available=3, filings_read=3, transactions=[
            _tx("a", "2026-10-01"), _tx("b", "2026-10-03"), _tx("c", "2026-10-09")])

    block = await change_events_section(session, company, cfg=cfg, today=TODAY,
                                        form4_lookup=lookup)
    assert calls == ["123456"]
    assert block is not None and block["counts_by_type"] == {"insider_cluster_buy": 1}
    assert block["insider_lookup"]["filings_read"] == 3
    assert block["insider_lookup"]["retrieved_at"] == TODAY.isoformat()
    assert scan_value(block) == []


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "change_events" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "changeEvents.ts").read_text()
    for key in ("events", "negated", "insider_clusters", "insider_lookup", "window_days",
                "documents_considered", "note", "trigger_type", "label", "date", "title",
                "document_type", "source_url", "source_kind", "rule", "start_date",
                "end_date", "distinct_insiders", "purchases", "shares", "value_usd",
                "insiders", "transactions", "filings_read", "truncated", "name",
                "relationship"):
        assert (f"raw.{key}" in reader or f"r.{key}" in reader or f"t.{key}" in reader
                or f"i.{key}" in reader or f"lookup.{key}" in reader), key
