"""
Private-use production readiness, PR-E — LIVE REGULATED DISCLOSURES.

The venue connectors (``nordic_disclosures``, ``six_swiss``,
``euronext_regulated_info``, ``uk_fca_nsm``) were reference-only by design: each
emitted a pointer to the issuer's regulated-disclosure venue plus an honest gap
saying the filing CONTENT is not fetched. For a private research system that is
half an answer — a researcher asking "what did this issuer just announce?" got a
link to a search page.

Researched live on 2026-08-25; two venues offered a legitimate official
machine-readable surface and are upgraded IN PLACE (no parallel architecture):

  * Nasdaq Nordic — the exchange's own company-news service.
  * eMarket Storage — the CONSOB-authorised Italian storage mechanism. Italy
    previously had no connector AND no exchange->regulator mapping at all.

The fixtures below are REAL captured venue payloads, trimmed and bounded.

Fully offline and deterministic: every test injects a fake fetcher or parses a
fixture. No network, no LLM, no Azure, no DB.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.services.sources.company_evidence import regulator_connector_for
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.borsa_italiana import BorsaItalianaConnector
from app.services.sources.connectors.nordic_disclosures import (
    NordicDisclosuresConnector,
)
from app.services.sources.disclosure_events import (
    EVENT_CATEGORY_GOVERNANCE,
    EVENT_CATEGORY_GUIDANCE,
    EVENT_CATEGORY_MANAGEMENT,
    EVENT_CATEGORY_OTHER,
    EVENT_CATEGORY_RESULTS,
    EVENT_CATEGORY_SHAREHOLDING,
    MAX_EVENTS_PER_ISSUER,
    DisclosureEvent,
    DisclosureFeed,
    classify_event,
    merge_events,
    normalize_title,
)
from app.services.sources.registry import build_registry
from app.services.sources.venue_disclosures import (
    EMARKET_ISSUER_IDS,
    NASDAQ_MARKETS,
    VENUE_EMARKET_STORAGE,
    VENUE_NASDAQ_NORDIC,
    disclosure_events_to_evidence,
    fetch_emarket_storage_disclosures,
    fetch_nasdaq_nordic_disclosures,
    issuer_search_term,
    parse_emarket_listing,
    parse_nasdaq_payload,
)
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_NASDAQ_FIXTURE = (_FIXTURES / "nasdaq_nordic_pandora_news.json").read_text()
_EMARKET_FIXTURE = (_FIXTURES / "emarket_storage_moncler_listing.html").read_text()

_CUTOFF = datetime.now(timezone.utc) - timedelta(days=400)


def _cfg(**over) -> Settings:
    base = {"source_live_disclosures_enabled": True, "live_disclosure_max_events": 15}
    base.update(over)
    return Settings(**base)


class _FakeFetch:
    """Records what was requested and returns a canned body."""

    def __init__(self, body: str | None = None, *, error: str | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, url, *, allowed_domains, keywords, cfg=None, resolve_ip=False):
        self.calls.append(
            {
                "url": url,
                "allowed_domains": allowed_domains,
                "resolve_ip": resolve_ip,
            }
        )

        class _R:
            pass

        r = _R()
        r.blocked = False
        r.error = self.error
        r.body_html = self.body
        return r


# =========================================================================== #
# The normalized event model                                                  #
# =========================================================================== #


def test_categories_come_from_the_venue_label_first() -> None:
    assert classify_event("Half Year financial report", "anything") == (
        EVENT_CATEGORY_RESULTS
    )
    assert classify_event("Managers' Transactions", "x") == EVENT_CATEGORY_SHAREHOLDING


def test_a_regulatory_venue_label_lets_the_headline_refine_it() -> None:
    """"Inside information" says how a disclosure is REGULATED, not what it is
    about, so the headline may still supply the content category."""
    assert classify_event(
        "Inside information", "Pandora delivers 3% organic growth - guidance upgraded"
    ) == EVENT_CATEGORY_GUIDANCE


def test_a_content_bearing_venue_label_is_never_overridden_by_a_headline() -> None:
    assert classify_event(
        "Half Year financial report", "Board appoints a new CFO"
    ) == EVENT_CATEGORY_RESULTS


def test_an_unrecognised_disclosure_is_other_never_a_guess() -> None:
    assert classify_event(None, "Some entirely novel announcement") == (
        EVENT_CATEGORY_OTHER
    )


def test_no_category_is_an_investment_judgement() -> None:
    from app.services.sources.disclosure_events import VALID_EVENT_CATEGORIES

    banned = {"buy", "sell", "hold", "watch", "bullish", "bearish", "positive", "negative"}
    assert not (VALID_EVENT_CATEGORIES & banned)


def test_an_event_never_renders_a_none_title() -> None:
    event = DisclosureEvent(
        issuer_ticker=None, issuer_name=None, venue="v", country=None,
        published_at=None, title=None,
    )
    assert "None" not in event.display_title()


def test_attachments_and_titles_are_bounded() -> None:
    event = DisclosureEvent(
        issuer_ticker="X", issuer_name="X", venue="v", country=None,
        published_at=None, title="T" * 5000,
        attachment_urls=tuple(f"https://x/{i}" for i in range(50)),
    )
    assert len(event.title or "") <= 300
    assert len(event.attachment_urls) <= 4


# =========================================================================== #
# Dedupe — the issuer-vs-exchange proof                                       #
# =========================================================================== #


def _issuer_copy() -> DisclosureEvent:
    return DisclosureEvent(
        issuer_ticker="PNDORA",
        issuer_name="Pandora A/S",
        venue="Pandora IR newsroom",
        country="Denmark",
        published_at=datetime(2026, 8, 12, 17, 30, tzinfo=timezone.utc),
        title="Pandora delivers 3% organic growth in Q2 - guidance upgraded",
        source_tier="T1_primary_company_source",
        official_url="https://pandoragroup.com/investor/announcement",
        provenances=("issuer newsroom",),
    )


def _exchange_copy() -> DisclosureEvent:
    return DisclosureEvent(
        issuer_ticker="PNDORA",
        issuer_name="Pandora A/S",
        venue=VENUE_NASDAQ_NORDIC,
        # Minutes apart — the two channels never stamp the same second.
        published_at=datetime(2026, 8, 12, 17, 34, 12, tzinfo=timezone.utc),
        country="Denmark",
        title=(
            "Company Announcement No. 1015: Pandora delivers 3% organic growth "
            "in Q2 — guidance upgraded"
        ),
        venue_category="Inside information",
        source_tier="T2_regulator_or_gov",
        official_url="https://view.news.eu.nasdaq.com/view?id=abc",
        attachment_urls=("https://attachment.news.eu.nasdaq.com/xyz",),
        document_identifier="1015",
        provenances=("Nasdaq Nordic company news (exchange-operated)",),
    )


def test_the_same_announcement_from_issuer_and_exchange_becomes_one_event() -> None:
    merged = merge_events([[_issuer_copy(), _exchange_copy()]])
    assert len(merged) == 1


def test_a_merged_event_keeps_every_provenance() -> None:
    event = merge_events([[_issuer_copy(), _exchange_copy()]])[0]
    assert "issuer newsroom" in event.provenances
    assert any("Nasdaq" in p for p in event.provenances)
    assert "Pandora IR newsroom" in event.venue
    assert VENUE_NASDAQ_NORDIC in event.venue


def test_a_merged_event_loses_no_channel_specific_detail() -> None:
    """The exchange copy carried an attachment and an announcement number the
    issuer copy did not; merging must not discard them."""
    event = merge_events([[_issuer_copy(), _exchange_copy()]])[0]
    assert event.attachment_urls == ("https://attachment.news.eu.nasdaq.com/xyz",)
    assert event.document_identifier == "1015"
    assert event.venue_category == "Inside information"


def test_merge_order_does_not_change_the_result() -> None:
    a = merge_events([[_issuer_copy(), _exchange_copy()]])[0]
    b = merge_events([[_exchange_copy(), _issuer_copy()]])[0]
    assert a.dedupe_key == b.dedupe_key
    assert set(a.provenances) == set(b.provenances)
    assert a.attachment_urls == b.attachment_urls


def test_two_genuinely_different_announcements_are_never_merged() -> None:
    other = _issuer_copy()
    other.title = "Pandora appoints Paulo Garcia as new CFO"
    assert len(merge_events([[_issuer_copy(), other]])) == 2


def test_the_same_headline_on_different_days_is_not_merged() -> None:
    later = _issuer_copy()
    later.published_at = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    assert len(merge_events([[_issuer_copy(), later]])) == 2


def test_an_event_without_enough_identity_is_kept_but_never_merged() -> None:
    anonymous = DisclosureEvent(
        issuer_ticker=None, issuer_name=None, venue="v", country=None,
        published_at=None, title=None,
    )
    merged = merge_events([[anonymous, _issuer_copy()]])
    assert len(merged) == 2


def test_normalize_title_strips_announcement_boilerplate_and_reference_numbers() -> None:
    a = normalize_title("Pandora delivers 3% organic growth in Q2 - guidance upgraded")
    b = normalize_title(
        "Company Announcement No. 1015: Pandora delivers 3% organic growth in "
        "Q2 — guidance upgraded"
    )
    assert a == b
    # A number INSIDE the headline is content and must survive.
    assert "3" in a and "q2" in a


def test_merged_output_is_newest_first_and_bounded() -> None:
    events = [
        DisclosureEvent(
            issuer_ticker="X", issuer_name="X", venue="v", country=None,
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            title=f"Announcement {i}",
        )
        for i in range(60)
    ]
    merged = merge_events([events])
    assert len(merged) == MAX_EVENTS_PER_ISSUER
    assert merged[0].published_at > merged[-1].published_at


# =========================================================================== #
# Nasdaq Nordic — parsing a REAL captured payload                             #
# =========================================================================== #


def _nasdaq_events():
    events, limitations = parse_nasdaq_payload(
        _NASDAQ_FIXTURE,
        issuer_ticker="PNDORA",
        issuer_name="Pandora A/S",
        country="Denmark",
        cutoff=_CUTOFF,
        max_events=25,
    )
    assert limitations == []
    return events


def test_nasdaq_payload_yields_real_events() -> None:
    events = _nasdaq_events()
    assert events
    assert all(e.venue == VENUE_NASDAQ_NORDIC for e in events)
    assert all(e.source_tier == "T2_regulator_or_gov" for e in events)


def test_nasdaq_events_carry_date_official_url_and_attachments() -> None:
    event = _nasdaq_events()[0]
    assert event.published_at is not None
    assert (event.official_url or "").startswith("https://view.news.eu.nasdaq.com/")
    assert all(
        a.startswith("https://attachment.news.eu.nasdaq.com/")
        for a in event.attachment_urls
    )


def test_nasdaq_results_announcement_is_categorised_from_the_headline() -> None:
    events = _nasdaq_events()
    guidance = [e for e in events if e.category == EVENT_CATEGORY_GUIDANCE]
    management = [e for e in events if e.category == EVENT_CATEGORY_MANAGEMENT]
    assert guidance, "the Q2 guidance-upgrade announcement should be categorised"
    assert management, "the CFO appointment should be categorised"


def test_another_issuers_announcement_is_never_attributed_to_this_issuer() -> None:
    """The venue's free-text search is a SEARCH, not a filter: it returns rows
    for other issuers that merely mention this one. Attributing those would be
    a fabricated event. The real payload contains exactly such a row."""
    raw = json.loads(_NASDAQ_FIXTURE)
    companies = {
        item.get("company") for item in raw["results"]["item"] if isinstance(item, dict)
    }
    assert len(companies) > 1, "fixture must contain a foreign issuer to be meaningful"
    assert all(
        (e.issuer_name or "").startswith("Pandora") for e in _nasdaq_events()
    )


def test_the_lookback_window_is_enforced() -> None:
    events, _ = parse_nasdaq_payload(
        _NASDAQ_FIXTURE,
        issuer_ticker="PNDORA",
        issuer_name="Pandora A/S",
        country="Denmark",
        cutoff=datetime(2099, 1, 1, tzinfo=timezone.utc),
        max_events=25,
    )
    assert events == []


def test_the_event_cap_is_enforced() -> None:
    events, _ = parse_nasdaq_payload(
        _NASDAQ_FIXTURE,
        issuer_ticker="PNDORA",
        issuer_name="Pandora A/S",
        country="Denmark",
        cutoff=_CUTOFF,
        max_events=2,
    )
    assert len(events) == 2


def test_an_unparseable_venue_response_yields_a_limitation_not_a_crash() -> None:
    events, limitations = parse_nasdaq_payload(
        "<html>not json</html>",
        issuer_ticker="X", issuer_name="X", country=None,
        cutoff=_CUTOFF, max_events=5,
    )
    assert events == []
    assert any("unparseable" in reason for reason in limitations)


def test_an_unexpected_venue_shape_yields_a_limitation() -> None:
    events, limitations = parse_nasdaq_payload(
        '{"results": {"item": "not-a-list-or-dict"}}',
        issuer_ticker="X", issuer_name="X", country=None,
        cutoff=_CUTOFF, max_events=5,
    )
    assert events == []
    assert any("shape_unexpected" in reason for reason in limitations)


# =========================================================================== #
# eMarket Storage — parsing a REAL captured listing                           #
# =========================================================================== #


def _emarket_events():
    events, limitations = parse_emarket_listing(
        _EMARKET_FIXTURE,
        issuer_ticker="MONC",
        issuer_name="MONCLER",
        cutoff=_CUTOFF,
        max_events=25,
    )
    assert limitations == []
    return events


def test_emarket_listing_yields_real_events_with_official_pdfs() -> None:
    events = _emarket_events()
    assert events
    assert all(e.venue == VENUE_EMARKET_STORAGE for e in events)
    assert any((e.official_url or "").endswith(".pdf") for e in events)


def test_emarket_h1_results_announcement_is_categorised() -> None:
    events = _emarket_events()
    results = [e for e in events if e.category == EVENT_CATEGORY_RESULTS]
    assert results
    assert any("H1 2026" in (e.title or "") for e in results)


def test_emarket_italian_edition_is_categorised_from_venue_vocabulary() -> None:
    """The venue publishes an Italian edition too; classifying only the English
    one would make half the feed look uncategorised."""
    events = _emarket_events()
    italian = [e for e in events if (e.language or "") == "it"]
    assert italian
    assert any(e.category == EVENT_CATEGORY_RESULTS for e in italian)
    assert any(e.category == EVENT_CATEGORY_GOVERNANCE for e in _emarket_events())


def test_html_entities_never_reach_a_human_facing_title() -> None:
    for event in _emarket_events():
        assert "&#" not in (event.title or "")
        assert "&amp;" not in (event.title or "")


def test_the_issuer_name_prefix_is_stripped_so_dedupe_can_match() -> None:
    assert all(
        not (e.title or "").upper().startswith("MONCLER") for e in _emarket_events()
    )


def test_original_titles_are_always_preserved() -> None:
    assert all(e.original_title == e.title for e in _emarket_events())


def test_an_empty_listing_yields_a_limitation_not_an_invented_event() -> None:
    events, limitations = parse_emarket_listing(
        "<html><body></body></html>",
        issuer_ticker="MONC", issuer_name="MONCLER",
        cutoff=_CUTOFF, max_events=5,
    )
    assert events == []
    assert limitations


# =========================================================================== #
# Bounds and security                                                         #
# =========================================================================== #


def test_nasdaq_retrieval_uses_an_exact_host_allowlist() -> None:
    fetch = _FakeFetch(_NASDAQ_FIXTURE)
    asyncio.run(
        fetch_nasdaq_nordic_disclosures(
            issuer_ticker="PNDORA", issuer_name="Pandora A/S", exchange="CO",
            country="Denmark", cfg=_cfg(), max_events=5, lookback_days=400,
            fetcher=fetch,
        )
    )
    allowed = fetch.calls[0]["allowed_domains"]
    assert "api.news.eu.nasdaq.com" in allowed
    assert not any("*" in d for d in allowed)
    assert fetch.calls[0]["url"].startswith("https://api.news.eu.nasdaq.com/")


def test_dns_pinning_is_requested_for_every_venue_call() -> None:
    for fetch, call in (
        (
            f := _FakeFetch(_NASDAQ_FIXTURE),
            lambda: fetch_nasdaq_nordic_disclosures(
                issuer_ticker="PNDORA", issuer_name="Pandora A/S", exchange="CO",
                country="Denmark", cfg=_cfg(), max_events=5, lookback_days=400,
                fetcher=f,
            ),
        ),
        (
            g := _FakeFetch(_EMARKET_FIXTURE),
            lambda: fetch_emarket_storage_disclosures(
                issuer_ticker="MONC", issuer_name="MONCLER", cfg=_cfg(),
                max_events=5, lookback_days=400, fetcher=g,
            ),
        ),
    ):
        asyncio.run(call())
        assert fetch.calls[0]["resolve_ip"] is True


def test_only_one_venue_request_is_ever_made_no_pagination() -> None:
    fetch = _FakeFetch(_NASDAQ_FIXTURE)
    asyncio.run(
        fetch_nasdaq_nordic_disclosures(
            issuer_ticker="PNDORA", issuer_name="Pandora A/S", exchange="CO",
            country="Denmark", cfg=_cfg(), max_events=25, lookback_days=400,
            fetcher=fetch,
        )
    )
    assert len(fetch.calls) == 1


def test_an_unreachable_venue_degrades_to_an_honest_limitation() -> None:
    fetch = _FakeFetch(None, error="http 503")
    feed = asyncio.run(
        fetch_nasdaq_nordic_disclosures(
            issuer_ticker="PNDORA", issuer_name="Pandora A/S", exchange="CO",
            country="Denmark", cfg=_cfg(), max_events=5, lookback_days=400,
            fetcher=fetch,
        )
    )
    assert feed.events == []
    assert any("venue_unreachable" in limit for limit in feed.limitations)
    assert feed.live is False


def test_an_ineligible_venue_makes_no_request_at_all() -> None:
    fetch = _FakeFetch(_NASDAQ_FIXTURE)
    feed = asyncio.run(
        fetch_nasdaq_nordic_disclosures(
            issuer_ticker="MC", issuer_name="LVMH", exchange="PA",
            country="France", cfg=_cfg(), max_events=5, lookback_days=400,
            fetcher=fetch,
        )
    )
    assert fetch.calls == []
    assert any("venue_not_eligible" in limit for limit in feed.limitations)


def test_an_unregistered_italian_issuer_makes_no_request() -> None:
    fetch = _FakeFetch(_EMARKET_FIXTURE)
    feed = asyncio.run(
        fetch_emarket_storage_disclosures(
            issuer_ticker="UNKNOWN", issuer_name="Unknown SpA", cfg=_cfg(),
            max_events=5, lookback_days=400, fetcher=fetch,
        )
    )
    assert fetch.calls == []
    assert any("not_registered" in limit for limit in feed.limitations)


# =========================================================================== #
# Connector wiring                                                            #
# =========================================================================== #


def _q() -> QueryContext:
    return QueryContext(purpose="events")


def test_nordic_connector_is_reference_only_with_the_flag_off() -> None:
    async def never(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("live retrieval must not run with the flag off")

    conn = NordicDisclosuresConnector(
        cfg=Settings(source_live_disclosures_enabled=False),
        disclosure_fetcher=never,
    )
    result = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="PNDORA", exchange="CO"), _q())
    )
    assert [i.source_type for i in result.evidence_items] == [
        "nordic_disclosures_reference"
    ]


def test_nordic_connector_emits_live_events_with_the_flag_on() -> None:
    async def fake(**kwargs):
        events, _ = parse_nasdaq_payload(
            _NASDAQ_FIXTURE, issuer_ticker="PNDORA", issuer_name="Pandora A/S",
            country="Denmark", cutoff=_CUTOFF, max_events=15,
        )
        return DisclosureFeed(venue=VENUE_NASDAQ_NORDIC, events=events, live=True)

    conn = NordicDisclosuresConnector(cfg=_cfg(), disclosure_fetcher=fake)
    result = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="PNDORA", exchange="CO"), _q())
    )
    types = [i.source_type for i in result.evidence_items]
    assert "regulated_disclosure_event" in types
    # The venue reference is retained: it says WHERE these came from.
    assert "nordic_disclosures_reference" in types


def test_a_live_venue_that_returns_nothing_falls_back_honestly() -> None:
    async def empty(**kwargs):
        return DisclosureFeed(
            venue=VENUE_NASDAQ_NORDIC, events=[], limitations=["venue_unreachable: x"]
        )

    conn = NordicDisclosuresConnector(cfg=_cfg(), disclosure_fetcher=empty)
    result = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="PNDORA", exchange="CO"), _q())
    )
    assert [i.source_type for i in result.evidence_items] == [
        "nordic_disclosures_reference"
    ]
    assert any("venue_unreachable" in g.message for g in result.source_gaps)
    # An honest gap, never a fabricated announcement. The reference item's own
    # copy says so explicitly ("no individual filing ... is fetched or
    # fabricated"), which is the guarantee being asserted.
    assert all(
        "regulated_disclosure_event" != i.source_type for i in result.evidence_items
    )
    assert any("fabricated" in (i.excerpt or "") for i in result.evidence_items)


def test_italian_connector_emits_live_events_with_the_flag_on() -> None:
    async def fake(**kwargs):
        events, _ = parse_emarket_listing(
            _EMARKET_FIXTURE, issuer_ticker="MONC", issuer_name="MONCLER",
            cutoff=_CUTOFF, max_events=15,
        )
        return DisclosureFeed(venue=VENUE_EMARKET_STORAGE, events=events, live=True)

    conn = BorsaItalianaConnector(cfg=_cfg(), disclosure_fetcher=fake)
    result = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="MONC", exchange="MI"), _q())
    )
    assert any(i.source_type == "regulated_disclosure_event" for i in result.evidence_items)


def test_a_non_italian_issuer_is_not_eligible_for_the_italian_venue() -> None:
    conn = BorsaItalianaConnector(cfg=_cfg())
    result = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="PNDORA", exchange="CO"), _q())
    )
    assert result.evidence_items == []
    assert any(g.gap_type.value == "source_not_eligible" for g in result.source_gaps)


def test_evidence_items_assert_no_materiality_or_trading_consequence() -> None:
    events, _ = parse_nasdaq_payload(
        _NASDAQ_FIXTURE, issuer_ticker="PNDORA", issuer_name="Pandora A/S",
        country="Denmark", cutoff=_CUTOFF, max_events=5,
    )
    items = disclosure_events_to_evidence(
        events, source_id="nordic_disclosures", transport_label="t",
        id_prefix="E", max_items=5,
    )
    blob = " ".join(
        f"{i.excerpt} {' '.join(i.provenance)} {' '.join(i.warnings)}" for i in items
    ).lower()
    for banned in ("buy", "sell", "price target", "upside", "downside", "undervalued"):
        assert banned not in blob
    assert "no materiality, direction, or trading consequence is asserted" in blob


def test_evidence_items_are_bounded_by_the_configured_cap() -> None:
    events, _ = parse_nasdaq_payload(
        _NASDAQ_FIXTURE, issuer_ticker="PNDORA", issuer_name="Pandora A/S",
        country="Denmark", cutoff=_CUTOFF, max_events=25,
    )
    items = disclosure_events_to_evidence(
        events, source_id="nordic_disclosures", transport_label="t",
        id_prefix="E", max_items=2,
    )
    assert len(items) == 2


# =========================================================================== #
# Italy finally has a regulated-disclosure identity                           #
# =========================================================================== #


@pytest.mark.parametrize("exchange", ["MI", "MIL", "BIT"])
def test_italian_venues_now_resolve_to_a_regulator_connector(exchange: str) -> None:
    assert regulator_connector_for(exchange, "Italy") == "borsa_italiana"


def test_italy_resolves_by_country_too() -> None:
    assert regulator_connector_for(None, "Italy") == "borsa_italiana"


def test_the_italian_source_is_registered_and_enabled() -> None:
    registry = build_registry()
    source = next(
        (s for s in registry.all_sources() if s.source_id == "borsa_italiana"), None
    )
    assert source is not None
    assert source.enabled is True
    assert source.connector_implemented is True
    assert "borsa_italiana" in registry.connectors()


def test_every_curated_venue_id_belongs_to_a_verified_issuer() -> None:
    """A curated id is a trust relationship; it must name an issuer the
    registry already verifies, never an arbitrary string."""
    for ticker in EMARKET_ISSUER_IDS:
        assert get_verified_issuer_source(ticker, "MI") is not None, ticker


# =========================================================================== #
# Live-acceptance corrective (2026-08-26): the SEARCH term                     #
# =========================================================================== #


@pytest.mark.parametrize(
    "legal_name,expected",
    [
        ("Pandora A/S", "Pandora"),
        ("Moncler S.p.A.", "Moncler"),
        ("Kering SA", "Kering"),
        ("Compagnie Financière Richemont SA", "Compagnie Financière Richemont"),
        # "Group" / "International" are part of the NAME, not a legal form.
        ("Burberry Group plc", "Burberry Group"),
        ("The Swatch Group AG", "The Swatch Group"),
        ("Hermès International SCA", "Hermès International"),
    ],
)
def test_only_the_legal_form_suffix_is_stripped_from_a_search_term(
    legal_name: str, expected: str
) -> None:
    """Found by LIVE acceptance, not by a unit test.

    The venue's ``freeText`` matches the announcement BODY. Searching the full
    legal name "Pandora A/S" returned the routine managers'-transaction notices
    — whose boilerplate title literally contains "Pandora A/S shares" — while
    silently DROPPING the Q2 2026 results and the CFO appointment, because no
    headline carries a legal-form suffix. The suffix is the part of a legal
    name LEAST likely to appear in the text being searched.
    """
    assert issuer_search_term(legal_name) == expected


def test_a_search_term_is_never_empty() -> None:
    """An issuer whose name is nothing but a legal form keeps its name rather
    than being searched for ""."""
    assert issuer_search_term("A/S") == "A/S"
    assert issuer_search_term("") == ""


def test_widening_the_search_does_not_widen_what_becomes_an_event() -> None:
    """Precision stays where it belongs: every returned row is still matched
    against the issuer's FULL name before it can become an event."""
    events, _ = parse_nasdaq_payload(
        _NASDAQ_FIXTURE,
        issuer_ticker="PNDORA",
        issuer_name="Pandora A/S",
        country="Denmark",
        cutoff=_CUTOFF,
        max_events=25,
    )
    assert events
    assert all((e.issuer_name or "").startswith("Pandora") for e in events)


def test_the_query_url_carries_the_trimmed_term() -> None:
    fetch = _FakeFetch(_NASDAQ_FIXTURE)
    asyncio.run(
        fetch_nasdaq_nordic_disclosures(
            issuer_ticker="PNDORA", issuer_name="Pandora A/S", exchange="CO",
            country="Denmark", cfg=_cfg(), max_events=5, lookback_days=400,
            fetcher=fetch,
        )
    )
    url = fetch.calls[0]["url"]
    assert "freeText=Pandora&" in url
    assert "A%2FS" not in url


def test_nasdaq_market_map_covers_only_nordic_venues() -> None:
    assert set(NASDAQ_MARKETS) == {"CO", "ST", "HE", "OL"}


def test_the_live_disclosure_flag_is_off_by_default() -> None:
    assert Settings().source_live_disclosures_enabled is False


# =========================================================================== #
# Live-acceptance corrective (2026-08-26): the connector must be CALLED        #
# =========================================================================== #


def test_the_italian_venue_is_runnable_not_merely_mapped() -> None:
    """Found by LIVE acceptance. ``regulator_connector_for`` resolved an
    Italian issuer to ``borsa_italiana``, but the connector was not in
    ``REGULATOR_REFERENCE_IDS``, so the evidence collector never ran it and the
    mapping had no effect whatsoever."""
    from app.services.sources.company_evidence import REGULATOR_REFERENCE_IDS

    assert "borsa_italiana" in REGULATOR_REFERENCE_IDS


def test_every_regulator_connector_id_actually_exists() -> None:
    """A mapped id that no connector implements is a silent dead end."""
    from app.services.sources.company_evidence import REGULATOR_REFERENCE_IDS

    connectors = build_registry().connectors()
    for source_id in REGULATOR_REFERENCE_IDS:
        assert source_id in connectors, source_id


def test_the_evidence_collector_calls_fetch_events_when_live_is_enabled() -> None:
    """The other half of the same live defect: the collector only ever called
    ``fetch_filings``, and ``fetch_events`` is where PR-E put live retrieval —
    so the live-retrieval work reached the connector and stopped there. Every
    unit test passed because they called ``fetch_events`` directly."""
    import inspect

    from app.services.sources import company_evidence

    source = inspect.getsource(company_evidence.collect_company_source_evidence)
    assert "fetch_events" in source
    assert "source_live_disclosures_enabled" in source


# =========================================================================== #
# PR-E follow-through: a HUMAN can see the retrieved disclosures              #
# =========================================================================== #


def test_retrieved_disclosures_reach_a_human_facing_report_section() -> None:
    """Found by inspecting live reports: the connector retrieved fifteen real
    announcements for Pandora — including the Q2 results and the CFO
    appointment — and they informed the council through the evidence pack, but
    a researcher could not SEE them anywhere. The council only persists a
    source it CITES, and ``news_catalyst_discovery`` is built by a different
    agent that never sees connector evidence."""
    from app.services.final_report_generator import _build_regulated_disclosures

    events, _ = parse_nasdaq_payload(
        _NASDAQ_FIXTURE, issuer_ticker="PNDORA", issuer_name="Pandora A/S",
        country="Denmark", cutoff=_CUTOFF, max_events=15,
    )
    payload = [
        {
            "title": e.title,
            "date": e.date_key,
            "venue": e.venue,
            "url": e.official_url,
            "source_tier": e.source_tier,
            "language": e.language,
            "provenance": list(e.provenances),
        }
        for e in events
    ]
    section = _build_regulated_disclosures(payload)
    assert section["available"] is True
    assert section["event_count"] == len(events)
    titles = " ".join(str(r["title"]) for r in section["events"]["value"])
    assert "organic growth in Q2" in titles
    assert section["latest_event_date"]["value"]
    assert section["venues"]["value"] == [VENUE_NASDAQ_NORDIC]


def test_the_disclosure_section_is_present_and_honest_when_empty() -> None:
    """Absent and empty must be distinguishable."""
    from app.services.final_report_generator import _build_regulated_disclosures

    section = _build_regulated_disclosures(None)
    assert section["type"] == "regulated_disclosures"
    assert section["available"] is False
    assert section["events"]["value"] == []
    assert "None" not in str(section["note"]["value"])


def test_the_disclosure_section_asserts_no_materiality_or_advice() -> None:
    from app.services.final_report_generator import _build_regulated_disclosures

    section = _build_regulated_disclosures(
        [{"title": "Q2 results", "date": "2026-08-12", "venue": "v"}]
    )
    blob = str(section).lower()
    for banned in ("buy", "sell", "price target", "upside", "downside", "undervalued"):
        assert banned not in blob
    assert "no materiality, direction, or trading consequence is asserted" in blob


def test_multi_channel_confirmation_is_visible() -> None:
    """A merged issuer+exchange announcement must show it was confirmed twice."""
    from app.services.final_report_generator import _build_regulated_disclosures

    section = _build_regulated_disclosures(
        [
            {
                "title": "Q2 results",
                "date": "2026-08-12",
                "venue": "Issuer newsroom + Nasdaq Nordic",
                "provenance": ["issuer newsroom", "Nasdaq Nordic company news"],
            }
        ]
    )
    assert len(section["events"]["value"][0]["provenance"]) == 2
