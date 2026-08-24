"""
Phase 32D2b — the verified issuer registry reaches the surfaces that describe it.

WHY THIS FILE EXISTS
====================
The platform maintained TWO independent issuer registries:

  * ``app/services/sources/verified_issuer_sources.py`` — code-defined,
    safety-validated (HTTPS, host allowlist, no credentials in URLs), covering
    the European issuers this product actually researches. Read ONLY by the
    connector / document-ingestion layer.
  * ``app/integrations/exchange_source_registry.KNOWN_ISSUER_SOURCES`` — seven
    US mega-caps. The ONLY registry the company-source / catalyst / news path
    consulted.

Live consequence, observed on the Pandora final report: the report cited the
issuer's own annual report, located through the verified registry's IR page,
while News & Catalyst Discovery rendered
``has_verified_company_source: false`` and warned that "no company-owned
website / IR / newsroom source could be confidently discovered for this
issuer".

The same split made discovery describe eight European issuers as having no
known IR source when their IR and annual-report URLs are on record. That is a
different claim from "we did not fetch them", and the difference decides
whether a human goes looking.

These tests are registry-driven: they assert the BRIDGE, not a hard-coded
issuer's URLs (which are curated data and change when a site reorganises).
"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.company_sources import VerificationMethod
from app.services.company_source_discovery_service import discover_company_sources
from app.services.llm.discovery_evidence_pack import (
    ISSUER_SOURCE_KNOWN_NOT_FETCHED,
    ISSUER_SOURCE_UNKNOWN,
    build_discovery_evidence_pack,
    issuer_primary_source_state,
)
from app.services.sources.verified_issuer_sources import (
    all_verified_issuer_sources,
    get_verified_issuer_source,
)


def _any_registry_issuer():
    """An arbitrary registry entry that has an IR URL — never a named issuer."""
    for entry in all_verified_issuer_sources():
        if entry.investor_relations_url:
            return entry
    pytest.skip("verified issuer registry has no entry with an IR URL")


# ---------------------------------------------------------------------------
# 1. Company-source discovery consults the verified issuer registry
# ---------------------------------------------------------------------------


def test_every_registry_issuer_resolves_a_verified_company_source() -> None:
    """No registry entry may be invisible to the catalyst/news layer.

    This is the bridge assertion: if a future entry is added and this path is
    not updated, THIS fails rather than a live report quietly claiming the
    issuer has no known sources.
    """
    for entry in all_verified_issuer_sources():
        result = asyncio.run(
            discover_company_sources(
                ticker=entry.ticker,
                company_name=entry.company_name,
                exchange=entry.exchange,
                country=entry.country,
            )
        )
        assert result.has_verified_company_source is True, entry.ticker
        assert result.company_website, entry.ticker
        assert result.confidence >= 0.9, entry.ticker
        methods = {c.verification_method for c in result.verified_sources}
        assert VerificationMethod.verified_issuer_registry.value in methods, entry.ticker


def test_registry_urls_are_carried_through_verbatim() -> None:
    entry = _any_registry_issuer()
    result = asyncio.run(
        discover_company_sources(
            ticker=entry.ticker, company_name=entry.company_name, exchange=entry.exchange
        )
    )
    assert result.investor_relations_url == entry.investor_relations_url
    assert result.annual_reports_url == entry.annual_reports_url
    assert result.newsroom_url == entry.press_releases_url
    # Every promoted URL stays on the issuer's own allowlisted domains.
    for candidate in result.verified_sources:
        if candidate.verification_method != (
            VerificationMethod.verified_issuer_registry.value
        ):
            continue
        assert candidate.domain.endswith(entry.official_website_domain), candidate.url
        assert candidate.url.startswith("https://"), candidate.url


def test_document_cdn_is_never_promoted_to_a_news_source() -> None:
    """``document_domains`` is a narrow FETCH authority, not a publication venue.

    Phase D1a deliberately scoped the CDN host to retrieving artifacts linked
    from the issuer's verified pages. Turning it into a discovered "company
    source" would widen that authority through the back door.
    """
    for entry in all_verified_issuer_sources():
        if not entry.document_domains:
            continue
        result = asyncio.run(
            discover_company_sources(
                ticker=entry.ticker,
                company_name=entry.company_name,
                exchange=entry.exchange,
            )
        )
        promoted = {c.domain for c in result.verified_sources}
        for cdn in entry.document_domains:
            assert cdn not in promoted, f"{entry.ticker}: {cdn} promoted as a source"


def test_an_unknown_issuer_still_reports_no_verified_source() -> None:
    """Fail-closed control: the bridge must not invent coverage."""
    result = asyncio.run(
        discover_company_sources(
            ticker="ZZZZNOTREAL", company_name="Not A Real Issuer", exchange="XX"
        )
    )
    assert result.has_verified_company_source is False
    assert result.company_website is None
    assert result.confidence == 0.0
    assert any("unavailable" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# 2. Discovery distinguishes KNOWN-BUT-NOT-FETCHED from UNKNOWN
# ---------------------------------------------------------------------------


def test_known_but_not_fetched_is_a_distinct_state() -> None:
    entry = _any_registry_issuer()
    state = issuer_primary_source_state(
        {"ticker": entry.ticker, "exchange": entry.exchange}
    )
    assert state["issuer_primary_source_state"] == ISSUER_SOURCE_KNOWN_NOT_FETCHED
    assert state["issuer_primary_source_locations"]
    note = state["issuer_primary_source_note"].lower()
    assert "not-yet-fetched" in note
    assert "not as absent" in note


def test_unknown_issuer_is_reported_as_unknown_not_as_fetched() -> None:
    state = issuer_primary_source_state({"ticker": "ZZZZNOTREAL", "exchange": "XX"})
    assert state["issuer_primary_source_state"] == ISSUER_SOURCE_UNKNOWN
    assert "no verified issuer" in state["issuer_primary_source_note"].lower()


def test_evidence_pack_states_both_populations_separately() -> None:
    entry = _any_registry_issuer()
    pack = build_discovery_evidence_pack(
        run={"id": "r1", "thesis_text": "European luxury goods companies"},
        candidates=[
            {
                "ticker": entry.ticker,
                "exchange": entry.exchange,
                "company_name": entry.company_name,
                "data_coverage": {"sec_eligible": False},
            },
            {
                "ticker": "ZZZZNOTREAL",
                "exchange": "XX",
                "company_name": "Not A Real Issuer",
                "data_coverage": {"sec_eligible": False},
            },
        ],
    )
    gaps = " ".join(pack.known_gaps)
    assert "KNOWN-BUT-NOT-FETCHED, not absent" in gaps
    assert "genuinely unknown" in gaps
    assert "1 candidate(s) have VERIFIED issuer" in gaps
    assert "1 candidate(s) have NO verified issuer" in gaps

    known = pack.candidates[0].data_coverage
    unknown = pack.candidates[1].data_coverage
    assert known["issuer_primary_source_state"] == ISSUER_SOURCE_KNOWN_NOT_FETCHED
    assert unknown["issuer_primary_source_state"] == ISSUER_SOURCE_UNKNOWN


def test_pack_instructs_the_council_not_to_flatten_the_tri_state() -> None:
    pack = build_discovery_evidence_pack(run={"id": "r1"}, candidates=[])
    assert any(
        "known_not_fetched" in rule for rule in pack.do_not_infer
    ), "the council must be told not to report a known source as absent"


# ---------------------------------------------------------------------------
# 3. The registry's own safety invariants are unchanged by this bridge
# ---------------------------------------------------------------------------


def test_registry_entries_remain_https_and_on_allowlisted_hosts() -> None:
    for entry in all_verified_issuer_sources():
        for url in entry.urls():
            assert url.startswith("https://"), url
        assert entry.official_website_domain in entry.allowed_domains


def test_lookup_still_requires_the_matching_exchange() -> None:
    """A ticker collision across venues must not resolve the wrong issuer."""
    entry = _any_registry_issuer()
    assert get_verified_issuer_source(entry.ticker, "NOT_A_VENUE") is None
