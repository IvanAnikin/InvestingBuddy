"""Path C — a verified, search-found issuer's own REGULATED document ties it to the theme.

No issuer website is involved: the document is fetched from the regulator / exchange, must name
the verified issuer, and its passage carries the *issuer's* regulated words — evidence of what the
company says it does (``issuer_only``), never independent corroboration.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.discovery import admission as adm
from app.services.discovery import official_domains as od
from app.services.discovery import pipeline as pl
from app.services.discovery.domain_proofs import DocText
from app.services.discovery.identity import IDENTITY_VERIFIED, IdentityOutcome
from app.services.discovery.leads import CompanyLead
from app.services.web_research import candidate_extract as ce
from app.services.web_research import discovery_stage as ds

VOCAB = ce.ThemeVocabulary(("lithium", "rare earth", "graphite"))

BODY = (
    "Elevra Lithium Limited ABN 51 824 753 556 Annual Report 2026.\n\n"
    "The Company is advancing the Ewoyaa lithium project and our lithium concentrate plant "
    "continued to ramp up during the year. Production guidance was unchanged.\n\n"
    "Lithium is used in batteries and rare earth magnets are used in motors; demand for these "
    "materials is expected to grow across many industries globally in the coming decade."
)


def doc(*, names_issuer: bool = True, text: str = BODY, ref: str = "03150002") -> DocText:
    return DocText(ref=ref, url="https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do",
                   source_class="exchange_announcement", text=text[:200], names_issuer=names_issuer,
                   full_text=text, content_hash="abc123", headline="Annual Report",
                   published_at="2026-09-10")


def test_a_passage_that_ties_the_issuer_to_the_theme_is_returned() -> None:
    entries, status = ds.regulated_entries(
        [doc()], vocab=VOCAB, name="ELEVRA LITHIUM LIMITED", ticker="ELV", venue="AU")
    assert entries and entries[0]["method"] == ds.METHOD_REGULATED_DOCUMENT
    e = entries[0]
    assert e["source_class"] == "exchange_announcement" and e["issuer_origin"] is True
    assert e["regulated_document"]["document_id"] == "03150002"
    assert e["regulated_document"]["content_hash"] == "abc123"
    assert adm.is_a3_passage(e) and adm.is_issuer_origin(e)
    assert status["naming_issuer"] == 1


def test_a_document_that_does_not_name_the_issuer_is_ignored() -> None:
    entries, status = ds.regulated_entries(
        [doc(names_issuer=False)], vocab=VOCAB, name="ELEVRA LITHIUM LIMITED", ticker="ELV",
        venue="AU")
    assert entries == [] and status["reason"] == "no_document_names_the_issuer"


def test_an_industry_explainer_sentence_does_not_tie_the_company() -> None:
    text = ("Elevra Lithium Limited annual report.\n\n"
            "Lithium is used in batteries and graphite anodes are used in many devices across "
            "industries; global demand for these materials is expected to grow over the decade.")
    entries, _ = ds.regulated_entries(
        [doc(text=text)], vocab=VOCAB, name="Zeta Metals Limited", ticker="ZZZ", venue="AU")
    # No distinctive name token, no ticker, no first-person marker: not the company's.
    assert entries == []


def test_regulated_passages_are_issuer_only_never_independent() -> None:
    entries, _ = ds.regulated_entries(
        [doc()], vocab=VOCAB, name="ELEVRA LITHIUM LIMITED", ticker="ELV", venue="AU")
    summary = adm.theme_evidence_summary(entries)
    assert summary["status"] == adm.THEME_VERIFIED_ISSUER
    assert summary["corroboration"] == adm.CORROBORATION_ISSUER_ONLY
    assert summary["regulated_passages"] >= 1 and summary["independent_passages"] == 0


class _Gate:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a: object) -> None:
        return None


def _outcome(name: str, ticker: str, venue: str = "AU", *, executed: bool = True) -> IdentityOutcome:
    web: dict[str, Any] = {"mentions": [], "sightings": (
        [{"query_id": "q-1", "fetch_attempt_id": "att-1"}] if executed else [])}
    lead = CompanyLead(
        name=name, ticker=ticker, exchange_raw="ASX", country="AU", listing_source_url=None,
        evidence_url=None, why=None, source="search", discovery_mode="search", web=web)
    return IdentityOutcome(lead=lead, status=IDENTITY_VERIFIED, ticker=ticker, exchange=venue,
                           name=name)


class _Intent:
    pass


@pytest.fixture
def _regulated(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_docs(**kw: Any) -> tuple[Any, list[DocText]]:
        return None, [doc()]

    monkeypatch.setattr(od, "regulated_documents", fake_docs)
    monkeypatch.setattr(ds, "theme_vocabulary", lambda _f: VOCAB)
    monkeypatch.setattr(ds.dp, "facts_from_intent", lambda _i: object())


@pytest.mark.asyncio
@pytest.mark.usefixtures("_regulated")
async def test_the_pipeline_step_attaches_passages_and_a_trace() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV")
    await pl._attach_regulated_passages(
        [o], intent=_Intent(), cfg=None, fetcher=None, gate=_Gate(), executed_query_ids={"q-1"})
    mentions = o.lead.web["mentions"]
    assert mentions and {m["method"] for m in mentions} == {"regulated_document_passage"}
    assert o.lead.web["regulated_documents"]["passages"] == len(mentions)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_regulated")
async def test_a_lead_without_an_executed_search_is_never_read() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV", executed=False)
    await pl._attach_regulated_passages(
        [o], intent=_Intent(), cfg=None, fetcher=None, gate=_Gate(), executed_query_ids={"q-1"})
    assert o.lead.web["mentions"] == [] and "regulated_documents" not in o.lead.web


@pytest.mark.asyncio
@pytest.mark.usefixtures("_regulated")
async def test_a_venue_without_a_permitted_source_is_recorded_not_guessed() -> None:
    o = _outcome("Some Canadian Miner Inc", "SCM", venue="TO")
    await pl._attach_regulated_passages(
        [o], intent=_Intent(), cfg=None, fetcher=None, gate=_Gate(), executed_query_ids={"q-1"})
    trace = o.lead.web["regulated_documents"]
    assert trace["read"] is False and trace["reason"] == "no_regulated_source_for_venue"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_regulated")
async def test_a_lead_that_already_has_theme_evidence_is_not_re_read() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV")
    o.lead.web["mentions"] = [{
        "theme_terms": ["lithium"], "source_class": "trade_publication", "domain": "mining.com",
        "passage_ref": "wp:1", "passage": "x"}]
    await pl._attach_regulated_passages(
        [o], intent=_Intent(), cfg=None, fetcher=None, gate=_Gate(), executed_query_ids={"q-1"})
    assert "regulated_documents" not in o.lead.web
