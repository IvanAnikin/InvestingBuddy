"""V3.19.4 — dynamic discovery: leads → verified issuers → screening → eligibility.

Everything external is faked (the research provider and the platform's own fetcher), and
every assertion is about what the platform DECIDES from what it fetched:

* a lead enters the universe only with listing evidence from an exchange, regulator or
  the issuer's own site (mutation guard: unverified lead never a candidate);
* a large company requested as small-cap is EXCLUDED on its verified market cap, never
  described as small (mutation guard: size hallucination);
* a company whose claims do not verify stays ``eligible_unverified`` — never quietly
  eligible, never fabricated to fill the quota;
* growth comes from verified business growth.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.integrations.deepseek.transport import DeepSeekResponse
from app.services.discovery import constraints as c
from app.services.discovery import fx
from app.services.discovery.identity import (
    issuer_domain_matches,
    normalise_venue,
    page_evidences_listing,
    publisher_kind,
    verify_identity,
)
from app.services.discovery.intent import build_intent
from app.services.discovery.leads import CompanyLead, parse_company_leads, plan_lead_queries
from app.services.discovery.pipeline import run_dynamic_stage, universe_item
from app.services.providers.contracts import ResearchLead, ResearchProviderResult
from app.services.sources.document_fetcher import DocumentFetchResult

LUXURY = "small cap growing european luxury companies"

PAGES: dict[str, str] = {
    # Kering: exchange page with ticker; aggregator page stating a large market cap.
    "https://www.kering.com/en/finance/": "Kering SA shares (KER) are listed on Euronext Paris.",
    "https://www.kering.com/en/finance/market-cap": (
        "Kering market capitalisation: EUR 27,390 million as of 20 September 2026."
    ),
    # The new company: its own IR site names it and its ticker.
    "https://www.maisonexemple.com/investors": (
        "Maison Exemple SA — shares listed on Euronext Paris under the ticker MEX. "
        "ISIN FR0000000000."
    ),
    "https://www.maisonexemple.com/investors/key-figures": (
        "Market capitalisation: EUR 900 million (20 September 2026). "
        "In FY2025, organic revenue growth was 8.0%. "
        "Maison Exemple designs and sells luxury leather goods and accessories."
    ),
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU": (
        "observation_date,DEXUSEU\n2026-09-17,1.15\n2026-09-18,1.15\n"
    ),
}


async def _fetch(url: str, **_kw):  # noqa: ANN202
    body = PAGES.get(url)
    if body is None:
        return DocumentFetchResult(requested_url=url, error="not found", failure_code="http_404")
    return DocumentFetchResult(
        requested_url=url, final_url=url, status_code=200, content_type="text/html",
        document_type="html", content=f"<html><body><p>{body}</p></body></html>".encode(),
    )


def _lead(metric, claim, url, value=None, currency=None, period=None):
    return ResearchLead(
        claim_text=claim, provider="deepseek", claimed_source_url=url, claimed_value=value,
        claimed_currency=currency, claimed_period=period, claimed_metric=metric,
    )


class _Transport:
    search_tool_name = "web_search"

    async def investigate_with_search(self, **_kw):
        companies = [
            {"legal_name": "Maison Exemple SA", "ticker": "MEX", "exchange": "Euronext Paris",
             "country": "France",
             "listing_source_url": "https://www.maisonexemple.com/investors",
             "evidence_url": "https://www.maisonexemple.com/investors/key-figures",
             "why": "a listed French luxury leather-goods house"},
            # Only an aggregator page: may DISCOVER, can never VERIFY.
            {"legal_name": "Aggregated Luxe plc", "ticker": "AGL", "exchange": "LSE",
             "listing_source_url": "https://stockanalysis.com/quote/lon/AGL/",
             "why": "listed luxury retailer"},
            {"legal_name": "Nowhere Luxury AG", "ticker": "NWL", "exchange": "Moon Exchange",
             "listing_source_url": "https://www.nowhereluxury.com/ir"},
        ]
        return DeepSeekResponse(text=json.dumps({"companies": companies}), prompt_tokens=900,
                                completion_tokens=300, finish_reason="stop")


class _Provider:
    provider_id = "deepseek"
    max_output_tokens = 12_000

    def __init__(self):
        self.transport = _Transport()
        self.questions: list[str] = []

    async def investigate(self, *, question, max_seconds=180, context=None, domains=None):
        self.questions.append(question)
        leads = []
        if "ticker KER" in question:
            leads = [_lead("market capitalisation", "Kering market capitalisation EUR 27,390 million",
                           "https://www.kering.com/en/finance/market-cap", "EUR 27,390 million", "EUR")]
        elif "ticker MEX" in question:
            url = "https://www.maisonexemple.com/investors/key-figures"
            leads = [
                _lead("market capitalisation", "Market capitalisation EUR 900 million", url,
                      "EUR 900 million", "EUR"),
                _lead("organic revenue growth", "organic revenue growth was 8.0% in FY2025", url,
                      "8.0%", period="FY2025"),
                _lead("business description",
                      "Maison Exemple designs and sells luxury leather goods and accessories.",
                      url),
            ]
        elif "ticker MONC" in question:
            # A claim whose page does not state it: never verified.
            leads = [_lead("market capitalisation", "Moncler market capitalisation EUR 1,000 million",
                           "https://www.monclergroup.com/nothing-here", "EUR 1,000 million", "EUR")]
        return ResearchProviderResult(
            provider="deepseek", model="deepseek-v4-flash", task_id="t", status="completed",
            started_at=datetime.now(timezone.utc), research_leads=leads,
        )


def _curated():
    return {"items": [
        {"ticker": "KER", "exchange": "PA", "company_name": "Kering SA", "country": "France",
         "sector": "Consumer Discretionary", "industry": "Luxury Goods", "theme": "luxury_goods",
         "universe_source": "curated_theme_registry", "source_tier": "T3_curated_reference_list"},
        {"ticker": "MONC", "exchange": "MI", "company_name": "Moncler S.p.A.", "country": "Italy",
         "sector": "Consumer Discretionary", "industry": "Luxury Apparel", "theme": "luxury_goods",
         "universe_source": "curated_theme_registry", "source_tier": "T3_curated_reference_list"},
    ]}


class _Session:
    """No held companies: the platform registry source contributes nothing here."""

    def begin_nested(self):  # noqa: ANN201
        raise RuntimeError("no database in this test")


@pytest.fixture(autouse=True)
def _clear_fx_cache():
    fx._CACHE.clear()
    yield
    fx._CACHE.clear()


async def _stage(provider=None):
    return await run_dynamic_stage(
        _Session(), intent=build_intent(LUXURY), run_universe=_curated(), cfg=settings,
        provider=provider or _Provider(), fetcher=_fetch, max_candidates=10,
    )


# ── the whole stage ────────────────────────────────────────────────────────── #


async def test_large_curated_name_is_excluded_on_its_verified_size():
    stage = await _stage()
    kering = next(r for r in stage.excluded if r.identity.ticker == "KER")
    size = next(r for r in kering.results if r.key == "size")
    assert size.status == c.FAIL and size.value["bucket"] == "large_cap"
    assert kering.eligibility.status == c.EXCLUDED
    assert all(r.identity.ticker != "KER" for r in stage.candidates)


async def test_unknown_company_found_verified_and_eligible():
    stage = await _stage()
    mex = next(r for r in stage.candidates if r.identity.ticker == "MEX")
    assert mex.identity.status == "verified"
    assert mex.identity.listing_source["tier"] == "issuer"
    assert mex.eligibility.status == c.ELIGIBLE
    attrs = c.verified_attributes(mex.results)
    assert attrs["size_bucket"] == "small_cap"
    assert attrs["growth_status"] == c.GROWTH_ESTABLISHED
    assert mex.provenance["discovery_source"] == "external_search"
    assert stage.candidates[0].identity.ticker == "MEX"  # eligible ranks first


async def test_unverifiable_claims_leave_a_company_unverified_not_eligible():
    stage = await _stage()
    moncler = next(r for r in stage.candidates if r.identity.ticker == "MONC")
    assert moncler.eligibility.status == c.ELIGIBLE_UNVERIFIED
    assert "size_bucket" not in c.verified_attributes(moncler.results)


async def test_unverified_lead_never_candidate():
    """MUTATION GUARD: an aggregator-only lead and an unknown venue are rejected."""
    stage = await _stage()
    tickers = {r.identity.ticker for r in [*stage.candidates, *stage.excluded]}
    assert "AGL" not in tickers and "NWL" not in tickers
    reasons = {r.lead.ticker: r.rejection_reason for r in stage.rejected}
    assert reasons["AGL"] == "no_listing_evidence"
    assert reasons["NWL"] == "unknown_venue"


async def test_funnel_and_persisted_shapes():
    stage = await _stage()
    assert stage.funnel["raw_leads"] == 5
    assert stage.funnel["verified_issuers"] == 3
    assert stage.funnel["returned"] == 2
    payload = stage.to_dict()
    assert payload["excluded"][0]["eligibility"]["status"] == "excluded"
    assert {r["ticker"] for r in payload["rejected_leads"]} == {"AGL", "NWL"}
    item = universe_item(stage.candidates[0], build_intent(LUXURY))
    assert item["ticker"] == "MEX" and item["exchange"] == "PA"
    assert item["v319"]["schema"] == "discovery_candidate/1"
    assert item["universe_source"] == "external_discovery"


async def test_the_users_text_is_never_sent_to_the_provider():
    provider = _Provider()
    await _stage(provider)
    for question in provider.questions:
        assert LUXURY not in question.lower()


async def test_no_provider_means_registry_only_and_says_so():
    stage = await run_dynamic_stage(
        _Session(), intent=build_intent(LUXURY), run_universe=_curated(),
        cfg=SimpleNamespace(v3_deepseek_search_enabled=False), provider=None, fetcher=_fetch,
        external=True,
    )
    assert stage.external_available is False
    assert stage.warnings and "unavailable" in stage.warnings[0]


# ── units ──────────────────────────────────────────────────────────────────── #


@pytest.mark.parametrize(
    ("raw", "code"),
    [("Euronext Paris", "PA"), ("ASX", "AU"), ("TSX Venture Exchange", "V"),
     ("London Stock Exchange AIM", "LSE"), ("Nasdaq Copenhagen", "CO"), ("Moon", None)],
)
def test_normalise_venue(raw, code):
    assert normalise_venue(raw) == code


def test_issuer_domain_is_equality_not_similarity():
    assert issuer_domain_matches("ir.kering.com", "Kering SA")
    assert issuer_domain_matches("www.mpmaterials.com", "MP Materials Corp.")
    assert not issuer_domain_matches("kering-investors.example", "Kering SA")
    assert not issuer_domain_matches("keringg.com", "Kering SA")


def test_publisher_kind():
    assert publisher_kind("https://live.euronext.com/en/product/x", "Kering") == "exchange"
    assert publisher_kind("https://www.sec.gov/cgi-bin/browse-edgar", "X Corp") == "regulator"
    assert publisher_kind("https://stockanalysis.com/quote/epa/KER/", "Kering") is None


def test_page_must_name_company_and_ticker():
    ok, _ = page_evidences_listing("Kering SA (KER) on Euronext", name="Kering SA", ticker="KER")
    assert ok
    ok, basis = page_evidences_listing("Kering SA on Euronext", name="Kering SA", ticker="KER")
    assert not ok and "ticker" in basis
    ok, _ = page_evidences_listing("Some other firm (KER)", name="Kering SA", ticker="KER")
    assert not ok


async def test_verify_identity_rejects_a_page_that_does_not_name_the_ticker():
    lead = CompanyLead(name="Maison Exemple SA", ticker="ZZZ", exchange_raw="Euronext Paris",
                       country="France",
                       listing_source_url="https://www.maisonexemple.com/investors",
                       evidence_url=None, why=None, source="external_search")
    outcome = await verify_identity(lead, cfg=settings, fetcher=_fetch)
    assert outcome.status == "rejected" and outcome.rejection_reason == "no_listing_evidence"


def test_lead_queries_are_bounded_and_built_from_vocabulary():
    intent = build_intent(
        "Find smaller listed companies in Europe, North America and Australia developing "
        "gallium, germanium, antimony, tungsten, rare-earth or other strategic materials"
    )
    queries = plan_lead_queries(intent, max_queries=4)
    assert len(queries) == 4
    assert all("gallium" in q.text or "tungsten" in q.text or "antimony" in q.text
               or "germanium" in q.text or "rare earths" in q.text for q in queries)
    assert all("Find smaller listed" not in q.text for q in queries)


def test_parse_company_leads_is_defensive():
    query = plan_lead_queries(build_intent(LUXURY), max_queries=1)[0]
    leads, warnings = parse_company_leads("not json", query=query, provider="p", task_id="t")
    assert leads == [] and warnings
    leads, _ = parse_company_leads(
        json.dumps({"companies": [{"legal_name": "X SA", "ticker": "EPA: XSA",
                                   "exchange": "Euronext Paris",
                                   "listing_source_url": "http://insecure.example"}, "junk"]}),
        query=query, provider="p", task_id="t",
    )
    assert leads[0].ticker == "XSA" and leads[0].listing_source_url is None


# ── the run lifecycle (service level) ──────────────────────────────────────── #

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from app.schemas.market_discovery import ThesisDiscoveryRunCreate  # noqa: E402
from app.services import market_discovery_service as mds  # noqa: E402
from tests.test_phase27_thesis_discovery import _fake_extractor  # noqa: E402


def _db():
    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda o: added.append(o))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db, added


async def test_process_run_replaces_universe_with_the_verified_shortlist(monkeypatch):
    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", True)
    db, added = _db()
    run = await mds.create_pending_thesis_run(
        db, ThesisDiscoveryRunCreate(thesis_text=LUXURY, provider_name="free_real")
    )
    run = await mds.process_run(
        db, run, extractor=_fake_extractor(), discovery_provider=_Provider(),
        discovery_fetcher=_fetch,
    )
    stage = run.universe_json["dynamic"]
    assert stage["status"] == "completed"
    # The eight curated large caps are screened and EXCLUDED on verified size where a
    # market cap verified (Kering here); the curated list is kept for audit.
    assert len(run.universe_json["curated_items"]) >= 8
    excluded = {e["identity"]["ticker"] for e in stage["excluded"]}
    assert "KER" in excluded
    candidates = [o for o in added if isinstance(o, mds.DiscoveryCandidate)]
    by_ticker = {cand.ticker: cand for cand in candidates}
    assert "KER" not in by_ticker
    assert by_ticker["MEX"].rank == 1
    v319 = by_ticker["MEX"].thesis_match_json["v319"]
    assert v319["eligibility"]["status"] == "eligible"
    assert v319["verified_attributes"]["size_bucket"] == "small_cap"


async def test_flag_off_keeps_the_curated_path(monkeypatch):
    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", False)
    db, added = _db()
    run = await mds.create_pending_thesis_run(
        db, ThesisDiscoveryRunCreate(thesis_text=LUXURY, provider_name="free_real")
    )
    run = await mds.process_run(db, run, extractor=_fake_extractor(),
                                discovery_provider=_Provider(), discovery_fetcher=_fetch)
    assert "dynamic" not in (run.universe_json or {})
    assert {o.ticker for o in added if isinstance(o, mds.DiscoveryCandidate)} >= {"KER", "MC"}
