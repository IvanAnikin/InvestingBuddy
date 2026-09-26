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
    d._CACHE.clear()
    yield
    d._CACHE.clear()


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
        (250e9, ("mega_cap",), "pass"),  # open-ended top band, cleared by the floor
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

    from app.services.discovery.directories import sec_public_float
    from app.services.sources.document_fetcher import DocumentFetchResult

    body = {"units": {"USD": [
        {"end": "2024-06-30", "val": 69.5e9, "form": "10-K", "filed": "2025-02-14"},
        {"end": "2025-06-30", "val": 61.9e9, "form": "10-K", "filed": "2026-02-13"},
        {"end": "2025-12-31", "val": 1.0, "form": "8-K", "filed": "2026-01-10"},
    ]}}
    seen = {}

    async def _fetch(url, **kw):  # noqa: ANN001, ANN202
        seen.update(url=url, **kw)
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="html",
                                   content=_json.dumps(body).encode())

    assert await sec_public_float("831259", fetcher=_fetch) == (
        61.9e9, "2025-06-30",
        "https://data.sec.gov/api/xbrl/companyconcept/CIK0000831259/dei/EntityPublicFloat.json")
    assert seen["allowed_domains"] == ("data.sec.gov",)
    assert await sec_public_float("not-a-cik", fetcher=_fetch) is None


def test_shortened_exchange_name_agrees_only_after_a_ticker_match():
    from app.services.discovery.directories import _names_agree
    from app.services.discovery.identity import normalised_name

    legal = normalised_name("Société Anonyme des Bains de Mer et du Cercle des Étrangers à Monaco")
    short = normalised_name("BAINS MER MONACO")
    assert _names_agree(legal, short, ticker_matched=True)
    assert not _names_agree(legal, short)  # a name-only search stays strict
    # Out of order, a two-letter word, or a single word is never enough.
    assert not _names_agree(legal, normalised_name("MONACO BAINS"), ticker_matched=True)
    assert not _names_agree(legal, normalised_name("MONACO"), ticker_matched=True)
    assert not _names_agree(normalised_name("Gold Mining Corp"),
                            normalised_name("Gold Rock Mining"), ticker_matched=True)
