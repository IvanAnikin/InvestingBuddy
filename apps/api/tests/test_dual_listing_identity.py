"""ADR-061 — one issuer, several listed securities (dual / multi-venue listings).

Live regression (Discovery run 7cce7dcd, 2026-10-30): Cellectis's own press release prints
``Cellectis (the "Company") (Euronext Growth: ALCLS – NASDAQ: CLLS)``. The extractor lost
the name before the parenthetical alias, read the FIRST ticker as the company name of the
second ("ALCLS" on NASDAQ: CLLS) and the SEC list rejected the pair as a mismatch — a
European issuer thrown out because its US ADS line exists.

The fixtures are the real public identifiers of real dual-listed issuers (Cellectis,
Sanofi, argenx) and a same-named company that is NOT the same issuer. No issuer name
appears in the code under test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import settings
from app.services.discovery import constraints as cons
from app.services.discovery import directories as d
from app.services.discovery.identity import dedup_key, verify_identity
from app.services.discovery.intent import build_intent
from app.services.discovery.leads import CompanyLead
from app.services.discovery.pipeline import evaluate
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.web_research import candidate_extract as ce

_HEAD = (
    'Name;ISIN;Symbol;Market;Currency;"Open Price";"High Price";"low Price";'
    '"last Price";"last Trade MIC Time";"Time Zone";Volume;Turnover;"Closing Price";'
    '"Closing Price DateTime"\n'
)
EURONEXT_CSV = _HEAD + (
    'CELLECTIS;FR0010425595;ALCLS;"Euronext Growth Paris";EUR;3;3;3;3.10;x;CET;1;1;3.05;x\n'
    'SANOFI;FR0000120578;SAN;"Euronext Paris";EUR;90;91;89;90.20;x;CET;1;1;90.10;x\n'
    'ARGENX SE;NL0010832176;ARGX;"Euronext Brussels";EUR;600;601;599;600;x;CET;1;1;600;x\n'
    # Same name as a US registrant below, but a Luxembourg ISIN: another issuer.
    'ORION;LU0000000009;ORNX;"Euronext Paris";EUR;10;10;10;10;x;CET;1;1;10;x\n'
)
SEC_JSON = json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": [
    [1627281, "Cellectis S.A.", "CLLS", "Nasdaq"],
    [1121404, "Sanofi", "SNY", "Nasdaq"],
    [1697862, "ARGENX SE", "ARGX", "Nasdaq"],
    [1111111, "Orion", "ORN", "Nasdaq"],
    [2222222, "Acme Therapeutics Inc", "ACMT", "Nasdaq"],
]})
SUBMISSIONS = {
    1627281: ("I0", "France"),
    1121404: ("I0", "France"),
    1697862: ("P7", "Netherlands"),
    1111111: ("I0", "France"),  # a FRENCH registrant named Orion — not the LU-ISIN line
    2222222: ("DE", "DELAWARE"),
}


def _isin_ok(isin: str) -> str:
    """Fixture ISINs must pass the checksum the code applies."""
    from app.services.discovery.identity import _isin_valid

    assert _isin_valid(isin), isin
    return isin


requested: list[str] = []


async def fetcher(url: str, **_kw: Any) -> DocumentFetchResult:
    requested.append(url)
    body = None
    if "pd_es/data/stocks/download" in url:
        body = EURONEXT_CSV
    elif "company_tickers_exchange" in url:
        body = SEC_JSON
    elif "/submissions/CIK" in url:
        cik = int(url.rsplit("CIK", 1)[1].split(".")[0])
        if cik in SUBMISSIONS:
            code, desc = SUBMISSIONS[cik]
            body = json.dumps({"stateOfIncorporation": code,
                               "stateOfIncorporationDescription": desc})
    if body is None:
        return DocumentFetchResult(requested_url=url, error="nope", failure_code="http_404")
    return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                               content_type="text/csv", document_type="text",
                               content=body.encode())


@pytest.fixture(autouse=True)
def _clear():
    d.reset_cache()
    requested.clear()
    yield
    d.reset_cache()


def _lead(name: str, *securities: tuple[str, str]) -> CompanyLead:
    ticker, venue = securities[0]
    return CompanyLead(
        name=name, ticker=ticker, exchange_raw=venue, country=None, listing_source_url=None,
        evidence_url=None, why=None, source="external_search", discovery_mode="search",
        securities=[{"ticker": t, "venue_raw": v} for t, v in securities]
        if len(securities) > 1 else None,
    )


def _by_ticker(outcome: Any) -> dict[str, dict[str, Any]]:
    return {(s["ticker"], s["exchange"]): s for s in outcome.securities}


def test_fixture_isins_are_valid():
    for isin in ("FR0010425595", "FR0000120578", "NL0010832176", "LU0000000009"):
        _isin_ok(isin)


# ── A / E: ordinary shares in Europe + an ADS line on Nasdaq → one issuer ──────────── #


async def test_a_european_ordinary_line_and_a_nasdaq_ads_are_one_issuer():
    out = await verify_identity(
        _lead("Cellectis", ("ALCLS", "Euronext Growth Paris"), ("CLLS", "NASDAQ")),
        fetcher=fetcher,
    )
    assert out.status == "verified"
    assert (out.ticker, out.exchange) == ("ALCLS", "PA"), "the home ordinary line is primary"
    rows = _by_ticker(out)
    assert rows[("ALCLS", "PA")]["status"] == "verified"
    assert rows[("ALCLS", "PA")]["role"] == "primary"
    assert rows[("ALCLS", "PA")]["security_form"] == "ordinary"
    assert rows[("ALCLS", "PA")]["isin"] == "FR0010425595"
    assert rows[("CLLS", "US")]["status"] == "verified"
    assert rows[("CLLS", "US")]["role"] == "secondary"
    assert rows[("CLLS", "US")]["security_form"] == "foreign_listing"
    assert out.issuer["legal_name"] == "Cellectis S.A."
    assert out.issuer["domicile_country"] == "France"
    assert "ISIN FR0010425595" in out.issuer["domicile_basis"]
    assert out.identity_record()["securities"] == out.securities


async def test_e_different_tickers_on_two_venues_stay_two_securities():
    out = await verify_identity(
        _lead("Sanofi", ("SNY", "NASDAQ"), ("SAN", "Euronext Paris")), fetcher=fetcher)
    assert (out.ticker, out.exchange) == ("SAN", "PA")
    assert set(_by_ticker(out)) == {("SNY", "US"), ("SAN", "PA")}
    assert out.issuer["domicile_country"] == "France"


# ── B: a European ticker alone is verified on its own venue, never on the SEC list ── #


async def test_b_a_european_ticker_is_verified_on_its_venue_not_routed_to_the_sec():
    out = await verify_identity(_lead("Cellectis", ("ALCLS", "Euronext Growth Paris")),
                                fetcher=fetcher)
    assert out.status == "verified"
    assert (out.ticker, out.exchange) == ("ALCLS", "PA")
    assert out.listing_source["directory"] == "euronext"
    assert not any("company_tickers_exchange" in u for u in requested)
    assert out.issuer["domicile_country"] == "France"


async def test_b_the_mis_paired_lead_of_the_live_run_is_no_longer_produced():
    """What run 7cce7dcd stored — name "ALCLS" on NASDAQ: CLLS — cannot come out of the
    extractor any more; the real sentence yields two listings of one named issuer."""
    text = ('New York (NY) - December 16, 2025 - Cellectis (the “Company”) (Euronext '
            'Growth: ALCLS – NASDAQ: CLLS), a clinical-stage biotechnology company.')
    mentions, stats = ce.extract_mentions([text], [], ce.ThemeVocabulary(("biotechnology",)))
    assert {(m.name, m.ticker, m.venue_raw) for m in mentions} == {
        ("Cellectis", "ALCLS", "Euronext Growth"), ("Cellectis", "CLLS", "NASDAQ")}
    assert all(m.co_listings for m in mentions)
    assert stats.rejected_names == 0


# ── C: the Nasdaq ADS alone still yields the European issuer and home listing ──────── #


async def test_c_a_nasdaq_ads_lead_keeps_the_european_domicile_and_home_listing():
    out = await verify_identity(_lead("Cellectis S.A.", ("CLLS", "NASDAQ")), fetcher=fetcher)
    assert out.status == "verified"
    assert out.issuer["domicile_country"] == "France"
    assert (out.ticker, out.exchange) == ("ALCLS", "PA"), "home ordinary line is primary"
    rows = _by_ticker(out)
    assert rows[("CLLS", "US")]["surfaced"] is True
    assert rows[("CLLS", "US")]["role"] == "secondary"
    assert rows[("ALCLS", "PA")]["surfaced"] is False
    assert rows[("ALCLS", "PA")]["role"] == "primary"
    assert "domicile" in out.issuer["linked_by"]


# ── D: the same name is not the same issuer without strong identifiers ────────────── #


async def test_d_a_us_registrant_does_not_absorb_a_same_named_european_company():
    """The US registrant is French by its SEC record; Euronext's ORION carries a
    Luxembourg ISIN. Same name, different issuer: no sibling."""
    out = await verify_identity(_lead("Orion", ("ORN", "NASDAQ")), fetcher=fetcher)
    assert (out.ticker, out.exchange) == ("ORN", "US")
    assert ("ORNX", "PA") not in _by_ticker(out) if out.securities else True


async def test_d_co_printed_lines_with_conflicting_domiciles_are_not_merged():
    out = await verify_identity(
        _lead("Orion", ("ORNX", "Euronext Paris"), ("ORN", "NASDAQ")), fetcher=fetcher)
    rows = _by_ticker(out)
    assert (out.ticker, out.exchange) == ("ORNX", "PA")
    assert out.issuer["domicile_country"] == "Luxembourg"
    assert rows[("ORN", "US")]["role"] == "unlinked"


async def test_d_a_us_domiciled_registrant_gets_no_foreign_sibling():
    out = await verify_identity(_lead("Acme Therapeutics", ("ACMT", "NASDAQ")), fetcher=fetcher)
    assert (out.ticker, out.exchange) == ("ACMT", "US")
    assert out.issuer["domicile_country"] == "United States"
    assert out.securities == []


# ── F: a ticker shared across exchanges needs its venue ───────────────────────────── #


async def test_f_the_same_ticker_on_two_venues_is_two_securities():
    us = await verify_identity(_lead("argenx", ("ARGX", "NASDAQ")), fetcher=fetcher)
    br = await verify_identity(_lead("argenx", ("ARGX", "Euronext Brussels")), fetcher=fetcher)
    assert dedup_key("ARGX", "US") != dedup_key("ARGX", "BR")
    assert br.listing_source["directory"] == "euronext" and br.exchange == "BR"
    # The Nasdaq lead resolves to the issuer, whose home line is Brussels.
    assert us.issuer["domicile_country"] == "Netherlands"
    # Its home country's exchange (Amsterdam) does not list it, so no sibling is invented:
    # the Brussels line is another venue, linked only when a source prints both.
    assert (us.ticker, us.exchange) == ("ARGX", "US")
    assert us.securities == []


async def test_f_a_ticker_without_a_venue_is_not_an_identity():
    out = await verify_identity(_lead("argenx", ("ARGX", "")), fetcher=fetcher)
    assert out.status == "rejected" and out.rejection_reason == "unknown_venue"


# ── G: geography follows the issuer, not the venue a page happened to print ────────── #


async def test_g_a_european_issuer_with_a_nasdaq_line_passes_a_europe_filter():
    out = await verify_identity(_lead("Cellectis S.A.", ("CLLS", "NASDAQ")), fetcher=fetcher)
    intent = build_intent("European biotechnology companies")
    results, _eligibility = await evaluate(out, None, intent, cfg=settings, fx_fetcher=fetcher)
    geo = next(r for r in results if r.key == "geography")
    assert geo.status == cons.PASS
    assert geo.value["hq_country"] == "France"


async def test_g_the_ads_line_alone_does_not_fail_europe_when_domicile_is_known():
    geo = cons.verify_geography(
        build_intent("European biotechnology companies").geography,
        listing_country="United States", listing_source={"url": "x", "tier": "regulator"},
        hq_country="France", hq_source={"url": "y", "tier": "regulator"},
    )
    assert geo.status == cons.PASS


# ── Extraction: grouping is strict ─────────────────────────────────────────────────── #


def test_two_companies_in_a_list_are_not_grouped():
    text = "Foo Metals Ltd (ASX: FOO), Bar Minerals Ltd (ASX: BAR) and others."
    mentions, _ = ce.extract_mentions([text], [], ce.ThemeVocabulary(("lithium",)))
    assert {(m.name, m.ticker) for m in mentions} == {("Foo Metals Ltd", "FOO"),
                                                     ("Bar Minerals Ltd", "BAR")}
    assert all(not m.co_listings for m in mentions)


def test_the_same_venue_twice_is_never_one_issuer():
    text = "Lithium names ASX: AAA, ASX: BBB were up."
    mentions, _ = ce.extract_mentions([text], [], ce.ThemeVocabulary(("lithium",)))
    assert all(not m.co_listings for m in mentions)


def test_a_dual_listing_in_two_parentheses_is_grouped():
    text = "Pensana Plc (LSE: PRE) (ASX: PM8) is building a rare earth refinery."
    mentions, _ = ce.extract_mentions([text], [], ce.ThemeVocabulary(("rare earth",)))
    assert {(m.name, m.ticker) for m in mentions} == {("Pensana Plc", "PRE"),
                                                     ("Pensana Plc", "PM8")}
    assert all(m.co_listings for m in mentions)


# ── End to end: the live page's sentence, through search → extraction → identity ──── #

from tests.helpers.discovery_web import hit as search_hit  # noqa: E402
from tests.helpers.discovery_web import page, plan_for, serve  # noqa: E402
from tests.test_web_w6b_discovery import H, cfg, pool, session  # noqa: E402, F401

PRESS = "https://www.example-biotech-news.com/cellectis-q3"
PRESS_PAGE = page(
    "Cellectis reports third quarter results",
    [
        "New York (NY) - December 16, 2025 - Cellectis (the “Company”) (Euronext Growth: "
        "ALCLS – NASDAQ: CLLS), a clinical-stage biotechnology company using its pioneering "
        "gene-editing platform to develop life-saving cell and gene therapies, today announced "
        "its results for the quarter.",
        "The biotechnology company said its allogeneic CAR-T programmes remain on schedule "
        "and that its cash position funds operations into the next year.",
    ],
    host="www.example-biotech-news.com",
)


async def test_a_co_listed_press_release_becomes_one_issuer_with_two_securities(
    session: Any, pool: Any  # noqa: F811
) -> None:
    h = H(session, pool)
    h.intent = build_intent("European biotechnology companies")
    h.plan = plan_for(h.intent)
    serve(h.provider, h.plan, {h.plan.queries[0].key: [search_hit(PRESS, "Cellectis Q3")]})
    h.net.pages[PRESS] = PRESS_PAGE
    stage = await h.stage(fetcher=fetcher)
    records = [*stage.candidates, *stage.excluded, *stage.also_surfaced]
    mine = [r for r in records if r.identity.ticker in {"ALCLS", "CLLS"}]
    assert len(mine) == 1, "two listings of one issuer are one candidate"
    ident = mine[0].identity
    assert (ident.ticker, ident.exchange) == ("ALCLS", "PA")
    assert {(s["ticker"], s["exchange"], s["status"]) for s in ident.securities} == {
        ("ALCLS", "PA", "verified"), ("CLLS", "US", "verified")}
    assert ident.issuer["domicile_country"] == "France"
    rejected = json.dumps(stage.to_dict()["rejected_leads"])
    assert "CLLS" not in rejected and "ALCLS" not in rejected
    geo = next(c for c in mine[0].results if c.key == "geography")
    assert geo.status == cons.PASS


async def test_g_the_shortlist_item_carries_the_issuers_country_not_the_venues():
    from app.services.discovery.pipeline import CandidateRecord, universe_item

    out = await verify_identity(_lead("argenx", ("ARGX", "NASDAQ")), fetcher=fetcher)
    intent = build_intent("European biotechnology companies")
    results, eligibility = await evaluate(out, None, intent, cfg=settings, fx_fetcher=fetcher)
    item = universe_item(CandidateRecord(out, results, eligibility, None, {}), intent)
    assert (item["exchange"], item["country"], item["region"]) == ("US", "Netherlands", "Europe")
    assert item["v319"]["identity"]["listing_country"] == "United States"


async def test_a_venue_the_caller_named_stays_the_primary_listing():
    """Requirement 7: a chosen venue wins over the home-line rule. The peer tool names
    CLLS on Nasdaq and needs THAT line (its SEC financials are the comparable ones)."""
    from app.services.discovery.identity import PRIMARY_GIVEN

    out = await verify_identity(_lead("Cellectis S.A.", ("CLLS", "NASDAQ")), fetcher=fetcher,
                                primary=PRIMARY_GIVEN)
    assert (out.ticker, out.exchange) == ("CLLS", "US")
    assert out.issuer["domicile_country"] == "France", "the issuer is still French"
    rows = _by_ticker(out)
    assert rows[("CLLS", "US")]["role"] == "primary"
    assert rows[("ALCLS", "PA")]["role"] == "secondary"
