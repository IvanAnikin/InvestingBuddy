"""
Phase 29D.2 — Patent office / index event-trigger reference connectors + collector.

Extends the Phase 29D.1 EVENT layer to PATENTS, reusing the generic
``EventReferenceConnector``, the ``collect_theme_event_evidence`` collector, and
the ``source_event_enabled`` flag (NO new wiring, NO new flag). A patent
reference is a WEAK internal innovation / R&D research-priority signal only —
never a specific patent, never a materiality claim, never a trade signal, and
CRITICALLY never a legal / infringement / validity / patentability conclusion.

Covers:
  * ``fetch_events`` emits a bounded, correctly-tiered ``government_data`` source
    reference (Google Patents T5 aggregator index; USPTO / EPO T2 offices) + an
    honest ``data_not_sourced`` gap, with no fabricated patent number / inventor /
    assignee / claim / date (digit-scan on the reference text, URL excluded), no
    legal / infringement / validity vocab, a WEAK + needs_human_review marker, a
    populated ``stale_after_days`` (freshness), and no forbidden rating / valuation
    vocab.
  * Theme mapping: all three cover innovation / patent / R&D / technology and stay
    quiet for an unrelated theme (e.g. "tariffs"); patents never surface on a bare
    region query. ``fetch_macro_context`` / ``fetch_filings`` / ``search_company``
    return an honest not-eligible gap.
  * google_patents + uspto + epo_espacenet are enabled patent sources in the
    registry with honest notes and the correct per-source tier; after Phase 29D.3
    added the three permit / regulatory-event venues the summary is 34 enabled / 2
    scaffolded / 2 planned / 38 total.
  * ``collect_theme_event_evidence`` returns patent refs for a relevant theme when
    ``source_event_enabled`` is True and is completely DARK when False; secret-free.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from app.core.config import Settings
from app.services import safety_terms
from app.services.sources import (
    ThemeEventEvidence,
    build_registry,
    collect_theme_event_evidence,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.event_reference import (
    ALL_EVENT_SOURCES,
    EVENT_SOURCES,
    PATENT_SOURCES,
    PERMIT_SOURCES,
    EventReferenceConnector,
    build_event_connectors,
    event_spec_for,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import assert_registry_safe
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    T5_API_AGGREGATOR,
    ConnectorStatus,
    ProviderType,
    SourceStatus,
)

PATENT_IDS = {"google_patents", "uspto", "epo_espacenet"}

# Expected per-source transport/content tier: Google Patents is a T5 aggregator
# INDEX; the USPTO and EPO are T2 government patent offices.
PATENT_TIERS: dict[str, str] = {
    "google_patents": T5_API_AGGREGATOR,
    "uspto": T2_REGULATOR_OR_GOV,
    "epo_espacenet": T2_REGULATOR_OR_GOV,
}

# A digit anywhere in the reference *text* (URL excluded) would mean a fabricated
# patent number, filing / grant date, or count leaked. Reference text must have
# none.
_DIGIT_RE = re.compile(r"\d")

# Positive legal-claim vocabulary the reference must NEVER contain — not even in a
# negated disclaimer. (A "no legal conclusion is drawn" disclaimer that uses the
# bare word "legal" is intentionally allowed; asserting a specific *infringement /
# validity / patentability* claim is not. "priority" is deliberately excluded — it
# collides with the honest "research-priority" weak-signal marker.)
_FORBIDDEN_LEGAL = (
    "infringement",
    "infringe",
    "validity",
    "invalid",
    "patentability",
    "patentable",
)


def _event_cfg(**over) -> Settings:
    base = dict(source_event_enabled=True, source_event_max_items=5)
    base.update(over)
    return Settings(**base)


def _evidence_text(item) -> str:
    # item.url is intentionally excluded — a fixed landing-page URL may carry a
    # path digit; the reference *text* must not carry a patent number or date.
    return " ".join(
        [
            item.title or "",
            item.excerpt or "",
            item.content_source or "",
            item.source_name or "",
            " ".join(item.provenance),
            " ".join(item.warnings),
        ]
    )


# ---------------------------------------------------------------------------
# Connector: fetch_events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sid", sorted(PATENT_IDS))
def test_patent_connector_emits_correctly_tiered_reference_and_honest_gap(sid):
    spec = event_spec_for(sid)
    assert spec is not None
    assert spec.provider_type == ProviderType.patents
    conn = EventReferenceConnector(spec)
    assert conn.status == ConnectorStatus.enabled

    result = asyncio.run(conn.fetch_events(QueryContext(query="innovation")))
    assert result.ok
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    # Reference-only government_data item — patent offices are government; Google
    # Patents is an aggregator INDEX of government patent publications.
    assert item.source_type == "government_data"
    # Correct per-source tier: T5 aggregator index vs T2 patent office.
    assert item.content_source_tier == PATENT_TIERS[sid]
    assert item.provider_transport_tier == PATENT_TIERS[sid]
    assert item.url == spec.url
    assert item.data_quality == "reference_only"
    # Weak internal research-priority signal.
    assert item.confidence == "low"
    # Freshness: stale_after_days populated from the venue refresh cadence.
    assert item.stale_after_days == spec.refresh_cadence_days
    assert item.stale_after_days is not None and item.stale_after_days >= 1
    # Honest gap: patent filings not fetched, does not block research-complete.
    assert len(result.source_gaps) == 1
    gap = result.source_gaps[0]
    assert gap.gap_type == GapType.data_not_sourced
    assert gap.blocks_research_complete is False
    assert "not fetched at report time" in gap.message.lower()
    assert "venue reference only" in gap.message.lower()
    assert "patent" in gap.message.lower()


def test_patent_reference_carries_no_fabricated_patent_data():
    """Reference text names the venue + themes only — never a patent number,
    title, inventor, assignee, claim, or date (which would surface as digits)."""
    for sid in sorted(PATENT_IDS):
        conn = EventReferenceConnector(event_spec_for(sid))
        result = asyncio.run(conn.fetch_events(QueryContext(query="patent")))
        assert result.evidence_items, sid
        text = _evidence_text(result.evidence_items[0])
        assert not _DIGIT_RE.search(text), f"{sid}: numeric leaked -> {text}"


def test_patent_reference_draws_no_legal_conclusion():
    """No infringement / validity / patentability vocab in the excerpt or gap — a
    patent reference is a venue pointer, never a legal / IP-strength conclusion."""
    for spec in PATENT_SOURCES:
        conn = EventReferenceConnector(spec)
        result = asyncio.run(conn.fetch_events(QueryContext(query="innovation")))
        item = result.evidence_items[0]
        gap = result.source_gaps[0]
        blob = (_evidence_text(item) + " " + gap.message).lower()
        for word in _FORBIDDEN_LEGAL:
            assert word not in blob, f"{spec.source_id}: legal vocab leaked -> {word}"
        # The reference states, positively, that it draws no such conclusion.
        assert "no legal" in item.excerpt.lower()
        assert "not a materiality claim" in item.excerpt.lower()


def test_patent_reference_is_weak_needs_review_signal():
    """Every reference is explicitly WEAK + needs_human_review, no materiality."""
    for spec in PATENT_SOURCES:
        conn = EventReferenceConnector(spec)
        item = asyncio.run(
            conn.fetch_events(QueryContext(query="R&D"))
        ).evidence_items[0]
        prov = " ".join(item.provenance).lower()
        warns = " ".join(item.warnings).lower()
        assert "needs_human_review=true" in prov
        assert "weak" in prov
        assert "weak internal research-priority signal" in warns
        # Not a materiality / trade-signal / legal claim.
        assert "not a materiality claim" in warns
        assert "not a trade signal" in warns


def test_patent_reference_is_recommendation_free():
    """No rating / valuation / trading-signal vocab — patents are a weak signal."""
    for spec in PATENT_SOURCES:
        conn = EventReferenceConnector(spec)
        result = asyncio.run(conn.fetch_events(QueryContext(query="technology")))
        item = result.evidence_items[0]
        blob = " ".join(
            [item.title or "", item.excerpt or "", " ".join(item.warnings)]
        )
        assert safety_terms.scan_text(blob) == [], f"unsafe: {blob!r}"
        # The gap message must also pass the report safety gate.
        assert safety_terms.scan_text(result.source_gaps[0].message) == []


@pytest.mark.parametrize("sid", sorted(PATENT_IDS))
def test_patent_source_answers_innovation_themes(sid):
    conn = EventReferenceConnector(event_spec_for(sid))
    for theme in (
        "innovation",
        "patent",
        "patents",
        "R&D",
        "research and development",
        "technology",
        "intellectual property",
        "semiconductor",
        "biotech drug pipeline",
        "battery",
    ):
        result = asyncio.run(conn.fetch_events(QueryContext(query=theme)))
        assert result.evidence_items, f"{sid} should cover theme {theme!r}"
        assert result.evidence_items[0].source_id == sid


def test_patent_source_quiet_for_irrelevant_theme():
    """Every patent venue stays quiet for an unrelated theme (e.g. tariffs)."""
    for sid in sorted(PATENT_IDS):
        conn = EventReferenceConnector(event_spec_for(sid))
        for theme in ("tariffs", "inflation", "defense", "government spending"):
            result = asyncio.run(conn.fetch_events(QueryContext(query=theme)))
            assert result.evidence_items == [], f"{sid} noisy for {theme!r}"
            assert result.source_gaps == [], sid


def test_patent_source_quiet_for_bare_region():
    """Patents are purely thematic — a bare region query never surfaces them
    (unlike the region-scoped procurement venues)."""
    for sid in sorted(PATENT_IDS):
        conn = EventReferenceConnector(event_spec_for(sid))
        for region in ("Europe", "North America"):
            result = asyncio.run(
                conn.fetch_events(QueryContext(query=None, region=region))
            )
            assert result.evidence_items == [], f"{sid} region-matched {region!r}"


def test_patent_connector_not_a_macro_or_company_source():
    """fetch_macro_context / fetch_filings / search_company → honest not-eligible."""
    conn = EventReferenceConnector(event_spec_for("uspto"))
    company = CompanyContext(ticker="AAPL")
    macro = asyncio.run(conn.fetch_macro_context(QueryContext(query="innovation")))
    filings = asyncio.run(conn.fetch_filings(company, QueryContext()))
    search = asyncio.run(conn.search_company(company, QueryContext()))
    for res in (macro, filings, search):
        assert res.evidence_items == []
        assert res.source_gaps
        assert res.source_gaps[0].gap_type == GapType.source_not_eligible


def test_patent_sources_are_separate_from_procurement():
    """The patent layer is additive: procurement stays 29D.1, patents 29D.2, and
    (Phase 29D.3) permits are a third additive kind; ALL = the union of the three."""
    assert {s.source_id for s in PATENT_SOURCES} == PATENT_IDS
    assert {s.source_id for s in EVENT_SOURCES} == {"eu_ted", "usaspending"}
    assert {s.source_id for s in PERMIT_SOURCES} == {"ferc", "us_nrc", "us_epa"}
    assert set(ALL_EVENT_SOURCES) == (
        set(EVENT_SOURCES) | set(PATENT_SOURCES) | set(PERMIT_SOURCES)
    )
    # No source_id collisions.
    ids = [s.source_id for s in ALL_EVENT_SOURCES]
    assert len(ids) == len(set(ids)) == 8


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_patent_sources_enabled_in_registry_with_honest_note():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert PATENT_IDS <= enabled_ids
    # The three patents were promoted OUT of the planned set.
    assert not (PATENT_IDS & planned_ids)
    for sid in sorted(PATENT_IDS):
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.provider_type == ProviderType.patents
        assert src.tier == PATENT_TIERS[sid]
        assert src.capabilities == ["fetch_events"]
        note = (src.reliability_note or "").lower()
        assert "patent office/index venue reference" in note
        assert "live patent filings not fetched at report time" in note
        assert "no legal" in note
        assert "weak internal research-priority signal" in note
        assert "phase 29d" in note
        conn = reg.connectors()[sid]
        assert isinstance(conn, EventReferenceConnector)
        assert conn.status == ConnectorStatus.enabled
        assert conn.is_live


def test_registry_summary_counts_after_patent_layer():
    reg = build_registry()
    summary = reg.summary()
    # 11 regulator-layer + 15 macro/commodity/policy (29C) + 2 procurement /
    # tender (29D.1) + 3 patent office / index (29D.2) + 3 permit /
    # regulatory-event (29D.3) = 34 enabled.
    assert summary["enabled"] == 36  # +1: local-language business press (Phase 30B)  # +1: Italian regulated disclosures (readiness PR-E)
    assert summary["scaffolded"] == 2
    # Only OpenBB remains planned (Phase 30B promoted the local-language press).
    assert summary["planned"] == 1
    assert summary["total"] == 39
    assert summary["total"] == len(reg.all_sources())
    # Health covers every patent connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert PATENT_IDS <= keys


def test_only_openbb_stays_planned():
    reg = build_registry()
    planned_ids = {s.source_id for s in reg.planned_sources()}
    # Phase 30B promoted the local-language business press to enabled, leaving only
    # the OpenBB toolkit planned.
    assert planned_ids == {"openbb"}
    # Patents are no longer planned.
    assert not (PATENT_IDS & planned_ids)


def test_registry_stays_secret_free_with_patent_layer():
    reg = build_registry()
    assert_registry_safe(reg)
    build_event_connectors()  # importable + constructible without secrets


# ---------------------------------------------------------------------------
# Theme collector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "theme",
    [
        "innovation",
        "patent filings",
        "R&D pipeline",
        "semiconductor technology",
        "battery and EV materials",
    ],
)
def test_collect_theme_event_evidence_returns_patent_refs_when_enabled(theme):
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence(theme, cfg=cfg))
    assert isinstance(ev, ThemeEventEvidence)
    sids = {i.source_id for i in ev.evidence_items}
    assert PATENT_IDS <= sids, f"{theme} -> {sids}"
    for item in ev.evidence_items:
        assert item.source_type == "government_data"
        assert item.confidence == "low"
    # Each reference carries an honest "patent filings not fetched" gap.
    assert ev.source_gaps
    assert all(g.gap_type == GapType.data_not_sourced for g in ev.source_gaps)
    assert ev.gap_messages()


def test_collect_theme_event_evidence_patents_dark_when_disabled():
    cfg = Settings()  # source_event_enabled defaults False
    assert cfg.source_event_enabled is False
    ev = asyncio.run(collect_theme_event_evidence("innovation", cfg=cfg))
    assert ev.evidence_items == []
    assert ev.source_gaps == []
    assert ev.warnings == []


def test_collect_theme_event_evidence_patents_quiet_for_irrelevant_theme():
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence("tariffs", cfg=cfg))
    assert ev.evidence_items == []


def test_collect_theme_event_evidence_patents_secret_free():
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence("innovation", cfg=cfg))
    blob = json.dumps(ev.model_dump(mode="json")).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "secret"):
        assert needle not in blob
