"""
Phase 29C.2 — Commodity + energy reference connectors + collector tests.

Extends the reference-only MACRO evidence category with COMMODITY + ENERGY
sources (USGS, IEA, IRENA, US EIA, ENTSO-E), driven by the *same* generic
``MacroReferenceConnector`` used in Phase 29C.1. Covers:
  * ``fetch_macro_context`` emits a bounded T2/T3 ``macro_report`` source
    reference + honest ``data_not_sourced`` gap, with no tonnage / price /
    capacity / production / reserve numbers, no fabricated dates, and no
    forbidden (rating / valuation) vocab.
  * Each source answers the right commodity / energy theme (usgs→copper /
    lithium / rare-earths / critical minerals, eia→uranium / nuclear, iea→energy /
    power grid, irena→renewables / solar, entsoe→power grid / transmission) and
    stays quiet for an unrelated (macro) theme.
  * The sources are registered + enabled in the registry with honest reliability
    notes; after the 29C.3 policy / government layer the registry summary is
    26 enabled / 2 scaffolded / 7 planned and the commodity / energy ids are no
    longer planned; everything stays secret-free.
  * ``collect_theme_macro_evidence`` returns these references for a relevant
    theme when ``source_macro_enabled`` is True, and is completely DARK when
    False.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from app.core.config import Settings
from app.services import safety_terms
from app.services.sources import build_registry, collect_theme_macro_evidence
from app.services.sources.company_evidence import CompanyContext
from app.services.sources.connector_base import QueryContext
from app.services.sources.connectors.macro_reference import (
    COMMODITY_ENERGY_SOURCES,
    MacroReferenceConnector,
    build_macro_connectors,
    macro_spec_for,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import assert_registry_safe
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    T3_INDUSTRY_SPECIALIST,
    ConnectorStatus,
    ProviderType,
    SourceStatus,
)

# The five 29C.2 commodity / energy ids and their expected reference tier.
COMMODITY_ENERGY_TIERS: dict[str, str] = {
    "usgs": T3_INDUSTRY_SPECIALIST,
    "iea": T3_INDUSTRY_SPECIALIST,
    "irena": T3_INDUSTRY_SPECIALIST,
    "entsoe": T3_INDUSTRY_SPECIALIST,
    "eia": T2_REGULATOR_OR_GOV,
}
COMMODITY_ENERGY_IDS = set(COMMODITY_ENERGY_TIERS)

# A representative relevant theme for each source (used to force a reference).
RELEVANT_THEME: dict[str, str] = {
    "usgs": "copper",
    "eia": "uranium",
    "iea": "energy",
    "irena": "renewables",
    "entsoe": "power grid",
}

# A digit not part of a URL is disallowed in reference text (no tonnage / price /
# capacity / production / reserve numbers, no fabricated dates).
_DIGIT_RE = re.compile(r"\d")


def _macro_cfg(**over) -> Settings:
    base = dict(source_macro_enabled=True, source_macro_max_items=5)
    base.update(over)
    return Settings(**base)


def _evidence_text(item) -> str:
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
# Connector: fetch_macro_context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sid", sorted(COMMODITY_ENERGY_IDS))
def test_commodity_connector_emits_tiered_reference_and_honest_gap(sid):
    spec = macro_spec_for(sid)
    assert spec is not None
    assert spec.provider == ProviderType.commodity
    conn = MacroReferenceConnector(spec)
    assert conn.status == ConnectorStatus.enabled

    result = asyncio.run(
        conn.fetch_macro_context(QueryContext(query=RELEVANT_THEME[sid]))
    )
    assert result.ok
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    # Reference-only, correctly-tiered macro_report item pointing at the fixed URL.
    assert item.source_type == "macro_report"
    assert item.content_source_tier == COMMODITY_ENERGY_TIERS[sid]
    assert item.provider_transport_tier == COMMODITY_ENERGY_TIERS[sid]
    assert item.url == spec.url
    assert item.data_quality == "reference_only"
    # Honest gap: figures not fetched, does not block research-complete.
    assert len(result.source_gaps) == 1
    gap = result.source_gaps[0]
    assert gap.gap_type == GapType.data_not_sourced
    assert gap.blocks_research_complete is False
    assert "not fetched at report time" in gap.message.lower()


def test_commodity_reference_carries_no_numbers_or_fabricated_dates():
    """Reference text names indicators only — never a value, tonnage, or date."""
    for spec in COMMODITY_ENERGY_SOURCES:
        conn = MacroReferenceConnector(spec)
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query=spec.theme_keywords[0]))
        )
        assert result.evidence_items, spec.source_id
        text = _evidence_text(result.evidence_items[0])
        assert not _DIGIT_RE.search(text), f"{spec.source_id}: numeric leaked -> {text}"


def test_commodity_reference_is_recommendation_free():
    for spec in COMMODITY_ENERGY_SOURCES:
        conn = MacroReferenceConnector(spec)
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query=spec.theme_keywords[0]))
        )
        item = result.evidence_items[0]
        blob = " ".join([item.title or "", item.excerpt or "", " ".join(item.warnings)])
        assert safety_terms.scan_text(blob) == [], f"unsafe: {blob!r}"
        # The gap message must also pass the report safety gate.
        assert safety_terms.scan_text(result.source_gaps[0].message) == []


@pytest.mark.parametrize(
    ("sid", "themes"),
    [
        ("usgs", ["copper", "lithium", "rare-earths", "critical minerals", "cobalt"]),
        ("eia", ["uranium", "nuclear", "oil", "natural gas"]),
        ("iea", ["energy", "power grid", "electricity demand", "renewables"]),
        ("irena", ["renewables", "solar", "wind", "hydrogen", "energy transition"]),
        ("entsoe", ["power grid", "electricity", "grid", "transmission"]),
    ],
)
def test_commodity_source_answers_expected_themes(sid, themes):
    conn = MacroReferenceConnector(macro_spec_for(sid))
    for theme in themes:
        result = asyncio.run(conn.fetch_macro_context(QueryContext(query=theme)))
        assert result.evidence_items, f"{sid} should cover theme {theme!r}"
        assert result.evidence_items[0].source_id == sid


def test_commodity_source_quiet_for_irrelevant_macro_theme():
    """Every commodity / energy source stays quiet for a pure-macro theme."""
    for sid in COMMODITY_ENERGY_IDS:
        conn = MacroReferenceConnector(macro_spec_for(sid))
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query="inflation"))
        )
        assert result.evidence_items == [], sid
        assert result.source_gaps == [], sid


def test_commodity_connector_not_a_company_filing_source():
    """fetch_filings / fetch_events return an honest not-eligible gap, no evidence."""
    conn = MacroReferenceConnector(macro_spec_for("eia"))
    company = CompanyContext(ticker="AAPL")
    filings = asyncio.run(conn.fetch_filings(company, QueryContext()))
    events = asyncio.run(conn.fetch_events(company, QueryContext()))
    for res in (filings, events):
        assert res.evidence_items == []
        assert res.source_gaps
        assert res.source_gaps[0].gap_type == GapType.source_not_eligible


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_commodity_sources_enabled_in_registry_with_honest_note():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert COMMODITY_ENERGY_IDS <= enabled_ids
    assert not (COMMODITY_ENERGY_IDS & planned_ids)
    for sid in COMMODITY_ENERGY_IDS:
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.provider_type == ProviderType.commodity
        assert src.tier == COMMODITY_ENERGY_TIERS[sid]
        note = (src.reliability_note or "").lower()
        assert "macro reference only" in note
        assert "live figures not fetched at report time" in note
        conn = reg.connectors()[sid]
        assert isinstance(conn, MacroReferenceConnector)
        assert conn.status == ConnectorStatus.enabled
        assert conn.is_live


def test_registry_summary_counts_after_commodity_layer():
    reg = build_registry()
    summary = reg.summary()
    # 11 regulator-layer + 5 macro (29C.1) + 5 commodity / energy (29C.2)
    # + 5 policy / government (29C.3) + 2 procurement / tender events (29D.1)
    # + 3 patent office / index events (29D.2) = 31 enabled; the procurement and
    # patent venues were promoted out of the planned set (7 -> 2).
    assert summary["enabled"] == 31
    assert summary["scaffolded"] == 2
    assert summary["planned"] == 2
    assert summary["total"] == len(reg.all_sources())
    # Health covers every commodity / energy connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert COMMODITY_ENERGY_IDS <= keys


def test_registry_stays_secret_free_with_commodity_layer():
    reg = build_registry()
    assert_registry_safe(reg)
    build_macro_connectors()  # importable + constructible without secrets


# ---------------------------------------------------------------------------
# Theme collector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("theme", "expected_sid"),
    [
        ("uranium", "eia"),
        ("nuclear", "eia"),
        ("copper", "usgs"),
        ("rare-earths", "usgs"),
        ("power grid", "entsoe"),
        ("renewables", "irena"),
        ("solar", "irena"),
    ],
)
def test_collect_theme_macro_evidence_returns_commodity_refs(theme, expected_sid):
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence(theme, cfg=cfg))
    sids = {i.source_id for i in ev.evidence_items}
    assert expected_sid in sids, f"{theme} -> {sids}"
    for item in ev.evidence_items:
        assert item.source_type == "macro_report"
    # Each reference carries an honest "figures not fetched" gap.
    assert ev.source_gaps
    assert all(g.gap_type == GapType.data_not_sourced for g in ev.source_gaps)


def test_collect_theme_macro_evidence_commodity_dark_when_disabled():
    cfg = Settings()  # source_macro_enabled defaults False
    assert cfg.source_macro_enabled is False
    ev = asyncio.run(collect_theme_macro_evidence("uranium", cfg=cfg))
    assert ev.evidence_items == []
    assert ev.source_gaps == []
    assert ev.warnings == []


def test_collect_theme_macro_evidence_commodity_secret_free():
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence("power grid", cfg=cfg))
    blob = json.dumps(ev.model_dump(mode="json")).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "secret"):
        assert needle not in blob


def test_collect_theme_macro_evidence_commodity_quiet_for_macro_theme():
    """A pure-macro theme returns no commodity / energy references."""
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence("inflation", cfg=cfg))
    sids = {i.source_id for i in ev.evidence_items}
    assert not (COMMODITY_ENERGY_IDS & sids)
