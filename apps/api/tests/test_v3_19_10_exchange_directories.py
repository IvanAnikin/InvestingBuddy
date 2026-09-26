"""V3.19.10 — the exchange's own directory verifies a listing and sizes it.

The external search provider stopped issuing searches (measured 2026-09-26), so leads
arrived with no URL and nothing could be verified. Official exchange directories are the
spec's first-ranked source: a listing is verified by the exchange that lists it, with no
vendor in the loop, and some directories publish the market data size needs.
"""

from __future__ import annotations

import json

import pytest

from app.services.discovery import directories as d
from app.services.discovery.identity import verify_identity
from app.services.discovery.leads import CompanyLead
from app.services.sources.document_fetcher import DocumentFetchResult

EURONEXT_CSV = (
    '﻿Name;ISIN;Symbol;Market;Currency;"Open Price";"High Price";"low Price";'
    '"last Price";"last Trade MIC Time";"Time Zone";Volume;Turnover;"Closing Price";'
    '"Closing Price DateTime"\n"European Equities"\n"26 Sep 2026"\n"note"\n'
    'KERING;FR0000121485;KER;"Euronext Paris";EUR;227;228;222;224.10;"25/09/2026";CET;1;1;223.30;25/09\n'
    'KERING;FR0000121485;1KER;"Euronext Global Equity Market";EUR;1;1;1;1;x;CET;1;1;1;x\n'
    '"SALVATORE FERRAGAMO";IT0004712375;SFER;"Euronext Milan";EUR;6;6;6;6.10;x;CET;1;1;6.05;x\n'
)
ASX_CSV = ('"ASX code","Company name","GICs industry group","Listing date","Market Cap"\n'
           '"LYC","LYNAS RARE EARTHS LIMITED","Materials","01/01/2007",12500000000\n')
SHARES_HTML = "<div>Trading Information Admitted shares 123,420,778 Nominal value 4.00</div>"
SEC_JSON = json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                       "data": [[1801368, "MP Materials Corp. / DE", "MP", "NYSE"]]})


async def fetcher(url: str, **_kw):  # noqa: ANN201
    body = None
    if "pd_es/data/stocks/download" in url:
        body = EURONEXT_CSV
    elif "markitdigital" in url:
        body = ASX_CSV
    elif "fs_tradinginfo_block" in url:
        body = SHARES_HTML
    elif "company_tickers_exchange" in url:
        body = SEC_JSON
    if body is None:
        return DocumentFetchResult(requested_url=url, error="nope", failure_code="http_404")
    return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                               content_type="text/csv", document_type="text",
                               content=body.encode())


@pytest.fixture(autouse=True)
def _clear():
    d.reset_cache()
    yield
    d.reset_cache()


def _lead(name, ticker, venue):
    return CompanyLead(name=name, ticker=ticker, exchange_raw=venue, country=None,
                       listing_source_url=None, evidence_url=None, why=None,
                       source="external_search")


async def test_a_lead_without_any_url_is_verified_by_the_exchange_directory():
    outcome = await verify_identity(_lead("Salvatore Ferragamo S.p.A.", "SFER", "Euronext Milan"),
                                    fetcher=fetcher)
    assert outcome.status == "verified"
    assert outcome.listing_source["tier"] == "exchange"
    assert outcome.directory_listing["isin"] == "IT0004712375"


async def test_a_real_ticker_on_the_wrong_company_is_refused():
    outcome = await verify_identity(_lead("Some Other Maison SA", "KER", "Euronext Paris"),
                                    fetcher=fetcher)
    assert outcome.status == "rejected"
    assert outcome.rejection_reason == "not_in_exchange_directory"
    assert "KERING" in outcome.detail


async def test_a_company_the_directory_does_not_list_is_refused():
    outcome = await verify_identity(_lead("Phantom Luxe SA", "PHX", "Euronext Paris"),
                                    fetcher=fetcher)
    assert outcome.rejection_reason == "not_in_exchange_directory"


async def test_us_listings_are_verified_by_the_sec_file():
    outcome = await verify_identity(_lead("MP Materials Corp.", "MP", "NYSE"), fetcher=fetcher)
    assert outcome.status == "verified" and outcome.listing_source["tier"] == "regulator"


async def test_euronext_market_cap_is_admitted_shares_times_official_price():
    row, _ = await d.find_listing(name="Kering SA", ticker="KER", venue="PA", fetcher=fetcher)
    amount, currency, url, basis = await d.official_market_cap(row, fetcher=fetcher)
    assert currency == "EUR"
    assert amount == pytest.approx(123_420_778 * 223.30)
    assert "admitted shares" in basis and "fs_tradinginfo_block" in url


async def test_asx_market_cap_is_published():
    row, _ = await d.find_listing(name="Lynas Rare Earths Ltd", ticker="LYC", venue="AU",
                                  fetcher=fetcher)
    amount, currency, _url, basis = await d.official_market_cap(row, fetcher=fetcher)
    assert amount == 12.5e9 and currency == "AUD" and "published" in basis


async def test_an_unreadable_directory_is_no_verdict():
    async def broken(url, **_kw):  # noqa: ANN001, ANN202
        return DocumentFetchResult(requested_url=url, error="down", failure_code="timeout")

    row, reason = await d.find_listing(name="Kering SA", ticker="KER", venue="PA",
                                       fetcher=broken)
    assert row is None and reason is None


def test_secondary_euronext_markets_are_not_primary_listings():
    rows = d.parse_euronext(EURONEXT_CSV)
    assert {(r.ticker, r.exchange) for r in rows} == {("KER", "PA"), ("SFER", "MI")}


async def test_evaluate_uses_a_held_companys_own_revenue_pair(monkeypatch):
    """A held issuer's growth comes from its own filing facts, even when screening
    found nothing — and an issuer the platform does not hold never queries them."""
    from app.core.config import settings
    from app.services.discovery import constraints as cons
    from app.services.discovery import pipeline
    from app.services.discovery.identity import IdentityOutcome
    from app.services.discovery.intent import build_intent
    from app.services.discovery.leads import CompanyLead

    calls: list[object] = []

    async def _held(_session, company_id):  # noqa: ANN001, ANN202
        calls.append(company_id)
        return [cons.growth_from_revenue_pair(
            32000, 28000, period="FY2025", base_period="FY2024",
            source_url="https://www.issuer.example/ar.pdf",
            source_tier="T1_primary_filing", verified=True)]

    monkeypatch.setattr(pipeline, "growth_from_held_facts", _held)
    lead = CompanyLead(name="Issuer A/S", ticker="ISS", exchange_raw="CO", country="Denmark",
                       listing_source_url=None, evidence_url=None, why=None,
                       source="curated")
    intent = build_intent("growing european jewellery companies")
    held = IdentityOutcome(lead=lead, status="verified", ticker="ISS", exchange="CO",
                           name="Issuer A/S", listing_country="Denmark",
                           listing_region="Europe", company_id="c-1")
    results, _ = await pipeline.evaluate(held, None, intent, cfg=settings,
                                         session=object())
    growth = next(r for r in results if r.key == "growth")
    assert growth.status == "pass" and calls == ["c-1"]

    unheld = IdentityOutcome(lead=lead, status="verified", ticker="ISS", exchange="CO",
                             name="Issuer A/S", listing_country="Denmark",
                             listing_region="Europe")
    results, _ = await pipeline.evaluate(unheld, None, intent, cfg=settings,
                                         session=object())
    growth = next(r for r in results if r.key == "growth")
    assert growth.status == "unknown" and calls == ["c-1"]


async def test_recall_leads_ask_for_json_without_thinking():
    """With thinking on, the model spent its whole budget reasoning and returned an
    empty message; recall must opt out, ask for JSON, and label itself model_recall."""
    from app.core.config import settings
    from app.integrations.deepseek.transport import FakeDeepSeekTransport
    from app.services.discovery import leads
    from app.services.discovery.intent import build_intent

    transport = FakeDeepSeekTransport(completion_text='{"companies": []}')

    class _P:
        provider_id = "deepseek"

    provider = _P()
    provider.transport = transport  # type: ignore[attr-defined]
    query = leads.plan_lead_queries(build_intent("european luxury companies"),
                                    max_queries=1)[0]
    answer = await leads._ask(provider, query, settings)
    assert transport.thinking_calls == [False]
    assert transport.json_mode_calls == [True]
    assert answer["mode"] == "model_recall"


async def test_issuer_site_verifies_what_the_company_does_only_on_its_own_domain():
    """A recalled URL is read only on the issuer's OWN domain; its text is the company
    speaking, classified per clause; a third-party page is never read."""
    from app.core.config import settings
    from app.services.discovery.identity import IdentityOutcome
    from app.services.discovery.intent import build_intent
    from app.services.discovery.leads import CompanyLead
    from app.services.discovery.screening import _terms, issuer_site_exposures
    from app.services.sources.document_fetcher import DocumentFetchResult

    pages = {
        "https://www.pensana.co.uk/about": "Pensana is developing a rare earth "
        "processing facility producing magnet metal oxides.",
        "https://www.minesnews.example/pensana": "Pensana mines rare earths.",
    }
    fetched: list[str] = []

    async def _fetch(url, **_kw):  # noqa: ANN001, ANN202
        fetched.append(url)
        body = pages.get(url)
        if body is None:
            return DocumentFetchResult(requested_url=url, error="nf", failure_code="http_404")
        return DocumentFetchResult(
            requested_url=url, final_url=url, status_code=200, content_type="text/html",
            document_type="html", content=f"<html><body><p>{body}</p></body></html>".encode())

    intent = build_intent("rare earth miners in Europe")
    terms = _terms(intent)

    def _issuer(evidence):  # noqa: ANN001, ANN202
        lead = CompanyLead(name="Pensana Plc", ticker="PRE", exchange_raw="LSE",
                           country="United Kingdom", listing_source_url=None,
                           evidence_url=evidence, why=None, source="external_search")
        return IdentityOutcome(lead=lead, status="verified", ticker="PRE", exchange="LSE",
                               name="Pensana Plc")

    found, fetches = await issuer_site_exposures(
        _issuer("https://www.pensana.co.uk/about"), terms, cfg=settings, fetcher=_fetch)
    assert fetches == 1 and found
    assert found[0].verified and found[0].source_tier == "issuer"
    assert found[0].source_url == "https://www.pensana.co.uk/about"

    fetched.clear()
    found, fetches = await issuer_site_exposures(
        _issuer("https://www.minesnews.example/pensana"), terms, cfg=settings, fetcher=_fetch)
    assert (found, fetches, fetched) == ([], 0, [])

    found, _ = await issuer_site_exposures(
        _issuer("http://www.pensana.co.uk/about"), terms, cfg=settings, fetcher=_fetch)
    assert found == []  # only https is read


@pytest.mark.parametrize(
    ("floor_usd", "requested", "expected"),
    [
        (61.9e9, ("micro_cap", "small_cap", "mid_cap"), "fail"),  # FCX: ≥ 2 × $10bn
        (15e9, ("micro_cap", "small_cap", "mid_cap"), "unknown"),  # could have halved
        (5e9, ("micro_cap", "small_cap", "mid_cap"), "unknown"),  # floor proves no fit
        (450e9, ("mega_cap",), "pass"),  # even half the floor clears the open top band
        (250e9, ("mega_cap",), "unknown"),  # the price may have fallen since the float date
        (150e9, ("large_cap", "mega_cap"), "unknown"),
    ],
)
def test_public_float_is_only_a_lower_bound(floor_usd, requested, expected):
    from app.services.discovery import constraints as cons
    from app.services.discovery.fx import USD_IDENTITY
    from app.services.discovery.intent import Constraint

    obs = cons.MarketCapObservation(
        amount=floor_usd, currency="USD", as_of="2025-06-30", as_of_basis="stated",
        source_url="https://data.sec.gov/x.json", source_tier="regulator", verified=True,
        method=cons.FLOAT_FLOOR_METHOD)
    constraint = Constraint(key="size", requested=requested, hardness="soft",
                            hardness_basis="t", phrase="smaller")
    result = cons.verify_size(constraint, obs, USD_IDENTITY)
    assert result.status == expected
    assert "bucket" not in (result.value or {})  # a floor never names a size bucket
    assert "size_bucket" not in cons.verified_attributes([result])


async def test_sec_public_float_reads_the_latest_10k_cover():
    import json as _json
    from datetime import date, timedelta

    from app.services.discovery.directories import sec_public_float
    from app.services.sources.document_fetcher import DocumentFetchResult

    recent = (date.today() - timedelta(days=300)).isoformat()
    body = {"units": {"USD": [
        {"end": "2024-06-30", "val": 69.5e9, "form": "10-K", "filed": "2025-02-14"},
        {"end": recent, "val": 61.9e9, "form": "10-K", "filed": "2026-02-13"},
        {"end": "2026-12-31", "val": 1.0, "form": "8-K", "filed": "2026-01-10"},
    ]}}
    seen = {}

    async def _fetch(url, **kw):  # noqa: ANN001, ANN202
        seen.update(url=url, **kw)
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="html",
                                   content=_json.dumps(body).encode())

    assert await sec_public_float("831259", fetcher=_fetch) == (
        61.9e9, recent,
        "https://data.sec.gov/api/xbrl/companyconcept/CIK0000831259/dei/EntityPublicFloat.json")
    assert seen["allowed_domains"] == ("data.sec.gov",)
    assert await sec_public_float("not-a-cik", fetcher=_fetch) is None
    # A lapsed filer's old float proves nothing about today.
    body["units"]["USD"] = [{"end": "2019-06-30", "val": 5e9, "form": "10-K"}]
    assert await sec_public_float("831259", fetcher=_fetch) is None
    # A changed response shape is an absent figure, never an exception.
    body["units"]["USD"] = ["not", "a", "fact"]
    assert await sec_public_float("831259", fetcher=_fetch) is None


def test_shortened_exchange_name_agrees_only_after_a_ticker_match():
    from app.services.discovery.directories import _names_agree
    from app.services.discovery.identity import normalised_name

    legal = normalised_name("Société Anonyme des Bains de Mer et du Cercle des Étrangers à Monaco")
    short = normalised_name("BAINS MER MONACO")
    assert _names_agree(legal, short, ticker_matched=True)
    assert not _names_agree(legal, short)  # a name-only search stays strict
    # One shared DISTINCTIVE word agrees once the ticker matched; generic words never do.
    assert _names_agree(legal, normalised_name("MONACO"), ticker_matched=True)
    assert not _names_agree(legal, normalised_name("MONACO"))
    assert not _names_agree(normalised_name("Global Energy Group"),
                            normalised_name("Energy Global"), ticker_matched=True)
    assert not _names_agree(normalised_name("Gold Mining Corp"),
                            normalised_name("Gold Rock Mining"), ticker_matched=True)


async def test_directory_download_is_shared_bounded_and_path_safe():
    """Concurrent leads download a directory once; a failure is not retried per lead;
    a model-supplied ticker is never interpolated into a request path."""
    import asyncio

    from app.services.discovery import directories as d

    d.reset_cache()
    calls: list[str] = []

    async def _down(url, **_kw):  # noqa: ANN001, ANN202
        calls.append(url)
        await asyncio.sleep(0)
        raise OSError("down")

    results = await asyncio.gather(*(d.load(d.ASX, fetcher=_down) for _ in range(5)))
    assert results == [[]] * 5 and len(calls) == 1
    assert await d.load(d.ASX, fetcher=_down) == [] and len(calls) == 1  # backoff

    calls.clear()
    for bad in ("../../X?Y", "A/B", "X#Y", ""):
        assert await d._lse_listing(name="x", ticker=bad, fetcher=_down) == (None, None)
    assert calls == []
    d.reset_cache()


@pytest.mark.parametrize(("lead", "listed"), [
    ("L'Air Liquide S.A.", "AIR LIQUIDE"),
    ("Compagnie de Saint-Gobain", "SAINT GOBAIN"),
    ("Salvatore Ferragamo S.p.A.", "FERRAGAMO"),
])
def test_exchange_short_names_agree_after_a_ticker_match(lead, listed):
    from app.services.discovery.directories import _names_agree
    from app.services.discovery.identity import normalised_name

    assert _names_agree(normalised_name(lead), normalised_name(listed), ticker_matched=True)


async def test_name_only_search_requires_the_same_name():
    """Without a ticker, "Kering Eyewear" must not inherit Kering's listing."""
    from app.services.discovery import directories as d

    d.reset_cache()
    row = d.DirectoryListing(name="KERING", ticker="KER", exchange="PA", isin=None,
                             country=None, market_cap=None, currency="EUR", price=None,
                             industry=None, mic="XPAR", directory="euronext",
                             source_url=d.EURONEXT.url, tier="exchange", as_of=d._today())
    d._store(("euronext", d._today()), [row])
    assert (await d.find_listing(name="Kering Eyewear", ticker=None, venue="PA"))[0] is None
    assert (await d.find_listing(name="Kering SA", ticker=None, venue="PA"))[0] == row
    d.reset_cache()


def test_venue_suffix_is_stripped_but_a_share_class_is_kept():
    from app.services.discovery.directories import _strip_suffix

    assert _strip_suffix("KER.PA") == "KER"
    assert _strip_suffix("EPA:KER") == "KER"
    assert _strip_suffix("BT.A") == "BT.A"
    assert _strip_suffix("RDSB.L") == "RDSB"


async def test_lse_record_market_cap_is_in_pounds_and_an_error_body_is_no_verdict():
    import json as _json

    from app.services.discovery import directories as d
    from app.services.sources.document_fetcher import DocumentFetchResult

    d.reset_cache()
    bodies = {
        "BRBY": {"tidm": "BRBY", "issuername": "Burberry Group PLC", "isin": "GB0031743007",
                 "marketcapitalization": 3727670338, "lastclose": 1034.5},
        "NOPE": {"status": 429, "message": "Too many requests"},
    }

    async def _fetch(url, **_kw):  # noqa: ANN001, ANN202
        body = bodies[url.rsplit("/", 1)[-1]]
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="html",
                                   content=_json.dumps(body).encode())

    row, reason = await d._lse_listing(name="Burberry Group plc", ticker="BRBY",
                                       fetcher=_fetch)
    assert reason is None and row.market_cap == 3727670338 and row.currency == "GBP"
    assert await d._lse_listing(name="x", ticker="NOPE", fetcher=_fetch) == (None, None)
    assert not any(k[0] == "lse:NOPE" for k in d._CACHE)
    d.reset_cache()


async def test_admitted_shares_never_glue_a_following_number():
    from app.services.discovery import directories as d
    from app.services.sources.document_fetcher import DocumentFetchResult

    async def _fetch(url, **_kw):  # noqa: ANN001, ANN202
        html = "<td>Admitted shares</td><td>123,420,778</td><td>2 026</td>"
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="text/html", document_type="html",
                                   content=html.encode())

    shares, _url = await d.euronext_admitted_shares("FR0000121485", "XPAR", fetcher=_fetch)
    assert shares == 123420778


async def test_a_directory_whose_shape_changed_is_unreadable_not_an_exception():
    from app.services.discovery import directories as d
    from app.services.sources.document_fetcher import DocumentFetchResult

    d.reset_cache()

    async def _fetch(url, **_kw):  # noqa: ANN001, ANN202
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="html",
                                   content=b'["a", "list", "not", "an", "object"]')

    assert await d.load(d.SEC, fetcher=_fetch) == []
    assert await d.load(d.TSX, fetcher=_fetch) == []
    d.reset_cache()


async def test_a_lead_matched_to_a_sibling_company_never_reads_the_siblings_site():
    """Lead "Aker Solutions" with ticker AKER is matched to Aker ASA; akersolutions.com
    must not become a verified description of Aker ASA."""
    from app.core.config import settings
    from app.services.discovery.identity import IdentityOutcome
    from app.services.discovery.intent import build_intent
    from app.services.discovery.leads import CompanyLead
    from app.services.discovery.screening import _terms, issuer_site_exposures

    fetched: list[str] = []

    async def _fetch(url, **_kw):  # noqa: ANN001, ANN202
        fetched.append(url)
        raise AssertionError("must not fetch")

    lead = CompanyLead(name="Aker Solutions ASA", ticker="AKER", exchange_raw="OL",
                       country="Norway", listing_source_url=None,
                       evidence_url="https://www.akersolutions.com/about", why=None,
                       source="external_search")
    issuer = IdentityOutcome(lead=lead, status="verified", ticker="AKER", exchange="OL",
                             name="AKER")
    found, fetches = await issuer_site_exposures(
        issuer, _terms(build_intent("rare earth miners in Europe")), cfg=settings,
        fetcher=_fetch)
    assert (found, fetches, fetched) == ([], 0, [])


def test_rio_tinto_all_caps_directory_name_does_not_make_rio_an_acronym():
    from app.services.discovery.identity import IdentityOutcome
    from app.services.discovery.leads import CompanyLead
    from app.services.discovery.screening import _issuer_site_urls

    lead = CompanyLead(name="RIO TINTO LIMITED", ticker="RIO", exchange_raw="AU",
                       country="Australia", listing_source_url=None,
                       evidence_url="https://www.rio.com/x", why=None, source="x")
    issuer = IdentityOutcome(lead=lead, status="verified", ticker="RIO", exchange="AU",
                             name="RIO TINTO LIMITED")
    # The matched name is title-cased; the lead's own ALL-CAPS name still passes as an
    # acronym, so both must agree — and "Rio Tinto Limited" rejects rio.com.
    assert _issuer_site_urls(issuer) == []


async def test_share_class_symbol_forms_match():
    from app.services.discovery import directories as d

    d.reset_cache()
    row = d.DirectoryListing(name="BERKSHIRE HATHAWAY INC", ticker="BRK-B", exchange="US",
                             isin=None, country=None, market_cap=None, currency="USD",
                             price=None, industry=None, mic="NYSE", directory="sec",
                             source_url=d.SEC.url, tier="regulator", as_of=d._today())
    d._store(("sec", d._today()), [row])
    found, _ = await d.find_listing(name="Berkshire Hathaway Inc.", ticker="BRK.B", venue="US")
    assert found == row
    d.reset_cache()


@pytest.mark.parametrize(("html", "expected"), [
    # The live case: a menu names topics; it does not say what the company does.
    ('<nav><a>Rare Earths</a><a>About us</a></nav>'
     '<div>Pensana PLC | Magnet Metal | Rare Earths | NdPr</div>', {}),
    # Inline markup never splits a sentence …
    ('<p>Pensana is a <a href="/x">rare earths</a> company building a processing hub.</p>',
     {"rare_earths": "direct"}),
    ('<p>We are building a <strong>rare earth</strong> processing facility.</p>',
     {"rare_earths": "direct"}),
    # … so a negation is never cut away from what it negates.
    ('<p>We have no exposure to <a>rare earths mining or processing in any of our '
     'current operations</a> today.</p>', {"rare_earths": "denied"}),
    # A short headline that IS a self-description is kept.
    ("<h1>Europe's rare earth magnet metals producer</h1>", {"rare_earths": "direct"}),
])
def test_a_website_menu_is_not_the_company_describing_itself(html, expected):
    """V3.19.12 — read in production: Pensana's and Eramet's "statement" was each site's
    navigation bar. Only prose blocks of the page may evidence what a company does."""
    from app.services.discovery.intent import build_intent
    from app.services.discovery.screening import (
        _terms,
        exposures_from_text,
        html_blocks,
        site_prose,
    )

    terms = _terms(build_intent("rare earth miners in Europe"))
    found = exposures_from_text(site_prose(html_blocks(html)), terms, source_url=None,
                                source_tier="issuer", verified=True)
    got = {e.term: e.exposure for e in found if e.term in expected or not expected}
    assert got == expected
