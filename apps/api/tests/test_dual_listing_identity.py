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
from dataclasses import replace
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
    [1736541, "NIO Inc.", "NIO", "NYSE"],
]})
SUBMISSIONS = {
    1627281: ("I0", "France"),
    1121404: ("I0", "France"),
    1697862: ("P7", "Netherlands"),
    1111111: ("I0", "France"),  # a FRENCH registrant named Orion — not the LU-ISIN line
    2222222: ("DE", "DELAWARE"),
}
#: Real EDGAR shapes (data.sec.gov, read 2026-10-30), including its two traps: Shell plc is
#: recorded as incorporated in "DC" with a UK business address, and Cameco's incorporation
#: is empty with "Canada (Federal Level)" as its address country.
RAW_SUBMISSIONS: dict[int, dict[str, Any]] = {
    1306965: {"stateOfIncorporation": "DC", "stateOfIncorporationDescription": "DC",
              "addresses": {"business": {"isForeignLocation": 1, "country": "United Kingdom",
                                         "countryCode": "X0"}}},
    1009001: {"stateOfIncorporation": "", "stateOfIncorporationDescription": "",
              "addresses": {"business": {"isForeignLocation": 1,
                                         "country": "Canada (Federal Level)",
                                         "countryCode": "Z4"}}},
    1445930: {"stateOfIncorporation": "M5",
              "stateOfIncorporationDescription": "Korea, Republic of",
              "addresses": {"business": {"stateOrCountry": "M5",
                                         "stateOrCountryDescription": "Korea, Republic of"}}},
    1736541: {"stateOfIncorporation": "E9", "stateOfIncorporationDescription": "Cayman Islands",
              "addresses": {"business": {"isForeignLocation": 1, "country": "China",
                                         "countryCode": "F4"}}},
    320193: {"stateOfIncorporation": "CA", "stateOfIncorporationDescription": "CA",
             "addresses": {"business": {"stateOrCountry": "CA",
                                        "stateOrCountryDescription": "CA"}}},
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
        if cik in RAW_SUBMISSIONS:
            body = json.dumps(RAW_SUBMISSIONS[cik])
        elif cik in SUBMISSIONS:
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
    assert out.issuer["domicile_country"] == "France"
    assert out.securities == [], "the Luxembourg-ISIN ORION on Paris is not its home line"


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
    assert geo.value["domicile_country"] == "France"
    assert geo.value["listing_country"] == "France", "the home line is primary"


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
    assert out.securities == [], "no home-line scan is spent when the venue is given"
    assert not any("pd_es/data/stocks/download" in u for u in requested)


# ── Review follow-ups: where an issuer IS, from records that are not always tidy ───── #


@pytest.mark.parametrize(("cik", "incorporation", "business"), [
    (1306965, None, "United Kingdom"),  # "DC" + a UK address: not a US company
    (1009001, None, "Canada"),  # empty incorporation; "Canada (Federal Level)"
    (1445930, "South Korea", "South Korea"),  # "Korea, Republic of"
    (1736541, "Cayman Islands", "China"),  # incorporated in Cayman, run from China
    (320193, "United States", "United States"),
])
async def test_the_sec_record_is_read_as_incorporation_and_business_address(
    cik: int, incorporation: str | None, business: str | None
) -> None:
    record = await d.sec_registrant_domicile(str(cik), fetcher=fetcher)
    assert record is not None
    assert (record.incorporation, record.business) == (incorporation, business)


async def test_a_cik_that_is_not_plain_digits_is_no_verdict():
    assert await d.sec_registrant_domicile("²", fetcher=fetcher) is None
    assert not requested


def test_a_depositary_receipts_us_isin_is_not_the_issuers_domicile():
    """An LSE GDR carries its US depositary bank's ISIN: that says nothing about where the
    company is."""
    from app.services.discovery.identity import IdentityOutcome, _isin_country

    gdr = IdentityOutcome(lead=_lead("Some Miner", ("KAP", "LSE")), status="verified",
                          ticker="KAP", exchange="LSE", listing_country="United Kingdom",
                          directory_listing={"isin": _isin_ok("US0378331005")})
    assert _isin_country(gdr) is None
    home = replace(gdr, listing_country="United States")
    assert _isin_country(home) == ("United States", "US0378331005")


async def test_a_cayman_company_run_from_china_is_chinese_by_headquarters():
    out = await verify_identity(_lead("NIO Inc.", ("NIO", "NYSE")), fetcher=fetcher)
    assert out.issuer["domicile_country"] == "Cayman Islands"
    assert out.issuer["hq_country"] == "China"
    results, _ = await evaluate(out, None, build_intent("Chinese electric vehicle companies"),
                                cfg=settings, fx_fetcher=fetcher)
    geo = next(r for r in results if r.key == "geography")
    assert geo.status == cons.PASS, geo.basis


async def test_a_dutch_issuer_seen_through_its_us_line_is_non_us():
    """"non-US" names the company, not the venue: argenx's only verified line here is
    Nasdaq, and it is still a Dutch company."""
    out = await verify_identity(_lead("argenx", ("ARGX", "NASDAQ")), fetcher=fetcher)
    results, _ = await evaluate(out, None, build_intent("non-US biotechnology companies"),
                                cfg=settings, fx_fetcher=fetcher)
    geo = next(r for r in results if r.key == "geography")
    assert geo.status == cons.PASS, geo.basis


async def test_a_us_company_stays_excluded_by_non_us():
    out = await verify_identity(_lead("Acme Therapeutics", ("ACMT", "NASDAQ")), fetcher=fetcher)
    results, _ = await evaluate(out, None, build_intent("non-US biotechnology companies"),
                                cfg=settings, fx_fetcher=fetcher)
    assert next(r for r in results if r.key == "geography").status == cons.FAIL


def test_a_bare_ticker_list_after_one_name_is_not_one_issuer():
    text = "Tickers mentioned: Foo Corp NASDAQ: FOO, TSX: BAR, ASX: BAZ in lithium."
    mentions, _ = ce.extract_mentions([text], [], ce.ThemeVocabulary(("lithium",)))
    assert all(not m.co_listings for m in mentions)


async def test_the_regulated_line_falls_back_to_a_linked_us_sibling():
    from app.services.discovery.pipeline import _REGULATED_VENUES, _issuer_keys, _line_on

    out = await verify_identity(
        _lead("Cellectis", ("ALCLS", "Euronext Growth Paris"), ("CLLS", "NASDAQ")),
        fetcher=fetcher)
    assert _line_on(out, _REGULATED_VENUES) == ("CLLS", "US")
    assert _line_on(out, ("PA",)) == ("ALCLS", "PA")
    assert _issuer_keys(out) == {"PA:ALCLS", "US:CLLS"}
    orion = await verify_identity(
        _lead("Orion", ("ORNX", "Euronext Paris"), ("ORN", "NASDAQ")), fetcher=fetcher)
    assert _line_on(orion, ("US",)) == ("ORNX", "PA"), "an unlinked line is another company"
    assert _issuer_keys(orion) == {"PA:ORNX"}


async def test_two_pages_naming_two_lines_of_one_issuer_are_one_candidate(
    session: Any, pool: Any  # noqa: F811
) -> None:
    """One page prints the Paris line, another the Nasdaq line: two leads, one issuer —
    the second is "the same issuer's other listing", never a second candidate."""
    h = H(session, pool)
    h.intent = build_intent("European biotechnology companies")
    h.plan = plan_for(h.intent)
    paris = "https://www.example-biotech-news.com/paris"
    nasdaq = "https://www.example-biotech-news.com/nasdaq"
    serve(h.provider, h.plan, {h.plan.queries[0].key: [search_hit(paris, "a"),
                                                        search_hit(nasdaq, "b")]})
    h.net.pages[paris] = page("Gene editing in Paris", [
        "Cellectis (Euronext Growth: ALCLS) is a clinical-stage biotechnology company "
        "developing gene-edited cell therapies, the company said in its quarterly update."],
        host="www.example-biotech-news.com")
    h.net.pages[nasdaq] = page("Gene editing on Nasdaq", [
        "Cellectis S.A. (NASDAQ: CLLS) is a clinical-stage biotechnology company whose "
        "allogeneic CAR-T programmes were presented at a medical conference this week."],
        host="www.example-biotech-news.com")
    stage = await h.stage(fetcher=fetcher)
    records = [*stage.candidates, *stage.excluded, *stage.also_surfaced]
    mine = [r for r in records if r.identity.ticker in {"ALCLS", "CLLS"}]
    assert len(mine) == 1
    assert (mine[0].identity.ticker, mine[0].identity.exchange) == ("ALCLS", "PA")
    dupes = [r for r in stage.to_dict()["rejected_leads"]
             if r.get("rejection_reason") == "duplicate"]
    assert any("the same issuer's other listing" in (r.get("detail") or "") for r in dupes), stage.to_dict()["rejected_leads"]


async def test_d_two_namesakes_on_two_listings_stay_two_companies(
    session: Any, pool: Any  # noqa: F811
) -> None:
    """Same name, different issuers (their own records disagree on domicile): neither is
    dropped as the other's duplicate, and neither absorbs the other."""
    h = H(session, pool)
    h.intent = build_intent("European biotechnology companies")
    h.plan = plan_for(h.intent)
    one = "https://www.example-biotech-news.com/orion-paris"
    two = "https://www.example-biotech-news.com/orion-nasdaq"
    serve(h.provider, h.plan, {h.plan.queries[0].key: [search_hit(one, "a"),
                                                        search_hit(two, "b")]})
    h.net.pages[one] = page("Orion in Paris", [
        "Orion (Euronext Paris: ORNX) is a biotechnology company developing antibody "
        "therapies, the company said in its quarterly statement this week."],
        host="www.example-biotech-news.com")
    h.net.pages[two] = page("Orion on Nasdaq", [
        "Orion (NASDAQ: ORN) is a biotechnology company that presented trial data at a "
        "medical conference, according to its announcement."],
        host="www.example-biotech-news.com")
    stage = await h.stage(fetcher=fetcher)
    records = [*stage.candidates, *stage.excluded, *stage.also_surfaced]
    assert {(r.identity.ticker, r.identity.exchange) for r in records
            if r.identity.ticker in {"ORNX", "ORN"}} == {("ORNX", "PA"), ("ORN", "US")}
    assert not [r for r in stage.to_dict()["rejected_leads"]
                if r.get("rejection_reason") == "duplicate"]
