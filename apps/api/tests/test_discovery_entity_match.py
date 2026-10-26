"""Path B: an independent source may carry thematic evidence for a verified listed company
WITHOUT an official issuer domain — if it unambiguously identifies that company (owner
direction 2026-10-26)."""

from __future__ import annotations

from app.services.discovery import admission as adm
from app.services.discovery import entity_match as em

ELEVRA = em.EntityMaster(names=("Elevra Lithium Limited",), ticker="ELV", venue="AU")
HASTINGS = em.EntityMaster(names=("Hastings Technology Metals Limited",), ticker="HAS", venue="AU")
GENERIC = em.EntityMaster(names=("Mineral Resources Limited",), ticker="MIN", venue="AU")


def test_a_trade_article_names_the_company_without_a_ticker() -> None:
    text = ("Elevra Lithium is advancing its North American Lithium operations in Quebec, "
            "where spodumene concentrate is produced.")
    got = em.match_entity(text, ELEVRA)
    assert got is not None and got.method == "full_name"


def test_a_short_form_alone_is_not_enough() -> None:
    text = "Hastings said rare earths demand would rise next year."
    assert em.match_entity(text, HASTINGS) is None


def test_a_short_form_with_a_ticker_and_venue_anchor_is_accepted() -> None:
    text = "Hastings (ASX: HAS) said rare earths demand would rise next year."
    got = em.match_entity(text, HASTINGS)
    assert got is not None and got.method == "short_name_anchored"
    assert "ticker:HAS" in got.anchors


def test_a_generic_only_name_never_matches_by_itself() -> None:
    text = "Mineral Resources Limited is a Western Australian firm offering lithium services."
    assert em.match_entity(text, GENERIC) is None
    # A free-standing "ASX-listed" is not an anchor; the ticker beside its venue tag is.
    assert em.match_entity(text + " The ASX-listed group trades as MIN.", GENERIC) is None
    anchored = text + " Mineral Resources (ASX: MIN) said so."
    assert em.match_entity(anchored, GENERIC) is not None


def test_a_same_name_company_in_another_country_does_not_pass_a_short_form() -> None:
    # "Elevra" alone, in a passage about a (hypothetical) Canadian shell: no anchor.
    assert em.match_entity("Elevra Capital raised funds in Toronto.", ELEVRA) is None


def test_a_project_article_supports_the_company_only_with_a_proven_relationship() -> None:
    article = "Drilling at the Ewoyaa Project in Ghana has extended the lithium pegmatite."
    assert em.match_entity(article, ELEVRA) is None
    related = em.EntityMaster(
        names=ELEVRA.names, ticker="ELV", venue="AU",
        projects=(em.ProjectRelation("Ewoyaa", "https://www.asx.com.au/x", "03150002",
                                     "exchange_announcement"),),
    )
    got = em.match_entity(article, related)
    assert got is not None and got.method == "project"
    assert got.relation is not None and got.relation.document_id == "03150002"


def test_a_relationship_is_proven_only_from_a_document_that_ties_company_and_project() -> None:
    doc = ("Elevra Lithium Limited wholly-owned Ewoyaa Project in Ghana drilled.\n\n"
           "Elevra Lithium Limited announces results from the Atlantis Project, a joint "
           "venture partner's asset.\n\n"
           "Unrelated: the Mercury Project belongs to somebody else entirely.")
    rels = em.relations_from_document(
        doc, name_tokens=["elevra"], document_id="D1", url=None, source_class="exchange_announcement"
    )
    assert [r.project for r in rels] == ["Ewoyaa"]


def test_a_search_snippet_alone_is_never_a_passage() -> None:
    snippet_entry = {"theme_terms": ["lithium"], "source_class": "trade_publication",
                     "domain": "mining.com", "passage_ref": None}
    assert adm.is_a3_passage(snippet_entry) is False


# --------------------------------------------------------------------------- #
# The pipeline step: passages in, A3-grade independent evidence out
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

from app.services.discovery import official_domains as od  # noqa: E402
from app.services.discovery import pipeline as pl  # noqa: E402
from app.services.discovery.domain_proofs import EntityIds  # noqa: E402
from app.services.discovery.identity import IDENTITY_VERIFIED, IdentityOutcome  # noqa: E402
from app.services.discovery.leads import CompanyLead  # noqa: E402


class _Gate:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a: object) -> None:
        return None


def _outcome(name: str, ticker: str) -> IdentityOutcome:
    lead = CompanyLead(
        name=name, ticker=ticker, exchange_raw="ASX", country="AU", listing_source_url=None,
        evidence_url=None, why=None, source="search", discovery_mode="search",
        web={"mentions": []},
    )
    return IdentityOutcome(lead=lead, status=IDENTITY_VERIFIED, ticker=ticker, exchange="AU",
                           name=name)


def _passage(text: str, *, source_class: str = "government_publication",
             domain: str = "ga.gov.au") -> dict:
    return {
        "passage": text, "theme_terms": ["lithium"], "chunk_id": "chunk-1",
        "passage_ref": "wp:aaaa:bbbb", "url": f"https://{domain}/report", "domain": domain,
        "hosts": [], "source_class": source_class, "attempt_id": "att-1", "query_id": "q-1",
        "query_key": "entity.0",
    }


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_entity(**kw: object) -> EntityIds:
        return EntityIds(name=str(kw.get("name") or ""), ticker=str(kw.get("ticker") or ""))

    monkeypatch.setattr(od, "resolve_entity", fake_entity)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_network")
async def test_a_government_source_naming_the_exact_issuer_admits_theme_evidence() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV")
    text = "Elevra Lithium Limited operates a lithium concentrate plant in Quebec, Canada."
    await pl._attach_independent_matches([o], [_passage(text)], cfg=None, fetcher=None,
                                         gate=_Gate())
    mentions = o.lead.web["mentions"]
    assert len(mentions) == 1 and mentions[0]["method"] == "independent_name_match"
    assert adm.is_a3_passage(mentions[0]) and not adm.is_issuer_origin(mentions[0])
    assert mentions[0]["entity_match"]["method"] == "full_name"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_network")
async def test_an_aggregator_or_unknown_page_never_carries_it() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV")
    text = "Elevra Lithium Limited operates a lithium concentrate plant in Quebec, Canada."
    await pl._attach_independent_matches(
        [o], [_passage(text, source_class="unknown_web", domain="investingnews.com")],
        cfg=None, fetcher=None, gate=_Gate())
    assert o.lead.web["mentions"] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_network")
async def test_a_lookalike_name_in_a_passage_is_not_the_company() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV")
    text = "Elevra Capital Partners financed a lithium trading desk in Toronto."
    await pl._attach_independent_matches([o], [_passage(text)], cfg=None, fetcher=None,
                                         gate=_Gate())
    assert o.lead.web["mentions"] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_network")
async def test_a_project_article_with_a_proven_relationship_supports_the_theme() -> None:
    o = _outcome("ELEVRA LITHIUM LIMITED", "ELV")
    od._RELATIONS[("AU", "ELV")] = [em.ProjectRelation(
        "Ewoyaa", "https://www.asx.com.au/x", "03150002", "exchange_announcement")]
    try:
        text = "Drilling at the Ewoyaa Project in Ghana has extended the lithium pegmatite."
        await pl._attach_independent_matches([o], [_passage(text, source_class="trade_publication",
                                                            domain="mining.com")],
                                             cfg=None, fetcher=None, gate=_Gate())
    finally:
        od._RELATIONS.clear()
    (m,) = o.lead.web["mentions"]
    assert m["entity_match"]["method"] == "project"
    assert m["entity_match"]["relationship"]["document_id"] == "03150002"


def test_a_bare_geographic_project_token_never_matches() -> None:
    master = em.EntityMaster(
        names=ELEVRA.names, ticker="ELV", venue="AU",
        projects=(em.ProjectRelation("Nevada", None, "D1", "exchange_announcement"),),
    )
    assert em.match_entity("Lithium prospects in Nevada are booming.", master) is None
    assert em.projects_named_in("The Nevada Project is large.") == []


def test_only_the_full_project_phrase_matches_a_proven_project() -> None:
    master = em.EntityMaster(
        names=ELEVRA.names, ticker="ELV", venue="AU",
        projects=(em.ProjectRelation("Ewoyaa", None, "D1", "exchange_announcement"),),
    )
    assert em.match_entity("Ewoyaa is a village in Ghana.", master) is None
    assert em.match_entity("The Ewoyaa Project extends.", master) is not None


def test_venue_words_that_are_english_words_are_not_anchors() -> None:
    master = em.EntityMaster(names=("Hastings Technology Metals Limited",), ticker="HAS", venue="LSE")
    assert em.strong_anchors("we aim to hit HAS targets; six HAS units", master) == ()
