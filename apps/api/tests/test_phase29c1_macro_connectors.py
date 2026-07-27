"""
Phase 29C.1 — Macro reference connectors + theme collector tests.

Covers the reference-only MACRO evidence category:
  * ``MacroReferenceConnector.fetch_macro_context`` emits a bounded T2
    ``macro_report`` source reference + honest ``data_not_sourced`` gap, with no
    numbers, no fabricated dates, and no forbidden (rating / valuation) vocab.
  * The macro connectors are registered + enabled in the registry with honest
    reliability notes, and the registry summary counts are updated.
  * ``collect_theme_macro_evidence`` returns references for a relevant theme when
    ``source_macro_enabled`` is True, and is completely DARK (empty) when False.
  * Everything stays secret-free; company / regulator evidence is unaffected.
"""

from __future__ import annotations

import asyncio
import json
import re

from app.core.config import Settings
from app.services import safety_terms
from app.services.sources import (
    build_registry,
    collect_company_source_evidence,
    collect_theme_macro_evidence,
)
from app.services.sources.company_evidence import CompanyContext
from app.services.sources.connector_base import QueryContext
from app.services.sources.connectors.macro_reference import (
    MACRO_SOURCES,
    MacroReferenceConnector,
    build_macro_connectors,
    macro_spec_for,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import assert_registry_safe
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    ConnectorStatus,
    ProviderType,
    SourceStatus,
)

MACRO_IDS = {
    "fred",
    "imf",
    "eurostat",
    "world_bank_pink_sheet",
    "national_stats_central_banks",
}

# A digit not part of an http(s):// scheme / path is disallowed in reference text
# (no numeric macro values, no dates). Reference URLs may contain no digits here.
_DIGIT_RE = re.compile(r"\d")


def _macro_cfg(**over) -> Settings:
    base = dict(source_macro_enabled=True, source_macro_max_items=3)
    base.update(over)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Connector: fetch_macro_context
# ---------------------------------------------------------------------------


def test_macro_connector_emits_t2_reference_and_honest_gap():
    spec = macro_spec_for("fred")
    assert spec is not None
    conn = MacroReferenceConnector(spec)
    assert conn.status == ConnectorStatus.enabled
    result = asyncio.run(conn.fetch_macro_context(QueryContext(query="inflation")))
    assert result.ok
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    # Reference-only T2 macro item.
    assert item.source_type == "macro_report"
    assert item.content_source_tier == T2_REGULATOR_OR_GOV
    assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
    assert item.url == spec.url
    assert item.data_quality == "reference_only"
    # Honest gap: figures not fetched, does not block research-complete.
    assert len(result.source_gaps) == 1
    gap = result.source_gaps[0]
    assert gap.gap_type == GapType.data_not_sourced
    assert gap.blocks_research_complete is False
    assert "macro reference only" in gap.message.lower() or (
        "not fetched at report time" in gap.message.lower()
    )


def test_macro_reference_carries_no_numbers_or_fabricated_dates():
    """The reference text must name indicators only — never a value or a date."""
    for spec in MACRO_SOURCES:
        conn = MacroReferenceConnector(spec)
        # Force relevance for every source so we scan its full reference text.
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query=spec.theme_keywords[0]))
        )
        assert result.evidence_items, spec.source_id
        item = result.evidence_items[0]
        # No numeric value / index level / date anywhere in the human text.
        text = " ".join(
            [
                item.title or "",
                item.excerpt or "",
                item.content_source or "",
                item.source_name or "",
                " ".join(item.provenance),
                " ".join(item.warnings),
            ]
        )
        # No digit at all in the human text ⇒ no numeric value and no calendar
        # date (fabricated release dates would show up as digits here).
        assert not _DIGIT_RE.search(text), f"{spec.source_id}: numeric leaked -> {text}"


def test_macro_reference_is_recommendation_free():
    for spec in MACRO_SOURCES:
        conn = MacroReferenceConnector(spec)
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query=spec.theme_keywords[0]))
        )
        item = result.evidence_items[0]
        blob = " ".join(
            [item.title or "", item.excerpt or "", " ".join(item.warnings)]
        )
        hits = safety_terms.scan_text(blob)
        assert hits == [], f"unsafe macro reference text: {blob!r} -> {hits}"
        # The gap message must also pass the report safety gate.
        assert safety_terms.scan_text(result.source_gaps[0].message) == []


def test_macro_connector_quiet_when_not_relevant():
    """A commodity-only source stays quiet for an unrelated macro theme."""
    spec = macro_spec_for("world_bank_pink_sheet")
    assert spec is not None
    conn = MacroReferenceConnector(spec)
    # "inflation" is not a pink-sheet commodity theme.
    result = asyncio.run(conn.fetch_macro_context(QueryContext(query="inflation")))
    assert result.evidence_items == []
    assert result.source_gaps == []


def test_macro_connector_not_a_company_filing_source():
    """fetch_filings / fetch_events return an honest not-eligible gap, no evidence."""
    conn = MacroReferenceConnector(macro_spec_for("fred"))
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


def test_macro_sources_enabled_in_registry_with_honest_note():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert MACRO_IDS <= enabled_ids
    assert not (MACRO_IDS & planned_ids)
    for sid in MACRO_IDS:
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.tier == T2_REGULATOR_OR_GOV
        note = (src.reliability_note or "").lower()
        assert "macro reference only" in note
        assert "live figures not fetched at report time" in note
        conn = reg.connectors()[sid]
        assert isinstance(conn, MacroReferenceConnector)
        assert conn.status == ConnectorStatus.enabled
        assert conn.is_live
    # Provider types are honest: pink sheet is a commodity source, the rest macro.
    assert reg.get("world_bank_pink_sheet").provider_type == ProviderType.commodity
    assert reg.get("fred").provider_type == ProviderType.macro_statistics


def test_registry_summary_counts_include_macro_layer():
    reg = build_registry()
    summary = reg.summary()
    # 11 regulator-layer enabled + 5 reference-only macro sources.
    assert summary["enabled"] == 16
    assert summary["scaffolded"] == 2
    assert summary["total"] == len(reg.all_sources())
    # Health covers every macro connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert MACRO_IDS <= keys


def test_registry_stays_secret_free_with_macro_layer():
    reg = build_registry()
    assert_registry_safe(reg)
    build_macro_connectors()  # importable + constructible without secrets


# ---------------------------------------------------------------------------
# Theme collector
# ---------------------------------------------------------------------------


def test_collect_theme_macro_evidence_returns_references_when_enabled():
    cfg = _macro_cfg()
    inflation = asyncio.run(collect_theme_macro_evidence("inflation", cfg=cfg))
    assert inflation.evidence_items
    assert len(inflation.evidence_items) <= 3  # bounded by source_macro_max_items
    sids = {i.source_id for i in inflation.evidence_items}
    # Macro-rate publishers answer an inflation theme; the commodity pink sheet
    # does not.
    assert "fred" in sids
    assert "world_bank_pink_sheet" not in sids
    for item in inflation.evidence_items:
        assert item.content_source_tier == T2_REGULATOR_OR_GOV
        assert item.source_type == "macro_report"
    # Each reference carries an honest "figures not fetched" gap.
    assert inflation.source_gaps
    assert all(
        g.gap_type == GapType.data_not_sourced for g in inflation.source_gaps
    )
    assert inflation.gap_messages()


def test_collect_theme_macro_evidence_commodity_theme():
    cfg = _macro_cfg()
    copper = asyncio.run(collect_theme_macro_evidence("copper", cfg=cfg))
    sids = {i.source_id for i in copper.evidence_items}
    assert "world_bank_pink_sheet" in sids


def test_collect_theme_macro_evidence_respects_max_items():
    cfg = _macro_cfg(source_macro_max_items=1)
    ev = asyncio.run(collect_theme_macro_evidence("inflation", cfg=cfg))
    assert len(ev.evidence_items) == 1


def test_collect_theme_macro_evidence_is_dark_when_disabled():
    cfg = Settings()  # source_macro_enabled defaults False
    assert cfg.source_macro_enabled is False
    ev = asyncio.run(collect_theme_macro_evidence("inflation", cfg=cfg))
    assert ev.evidence_items == []
    assert ev.source_gaps == []
    assert ev.warnings == []


def test_collect_theme_macro_evidence_secret_free():
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence("inflation", cfg=cfg))
    blob = json.dumps(ev.model_dump(mode="json")).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "secret"):
        assert needle not in blob


# ---------------------------------------------------------------------------
# Company / regulator evidence unaffected
# ---------------------------------------------------------------------------


def test_company_evidence_unaffected_by_macro_layer():
    """The company evidence path must not pick up macro connectors."""
    company = CompanyContext(ticker="AAPL", exchange="US", company_name="Apple Inc.")
    ev = asyncio.run(
        collect_company_source_evidence(
            company=company, cfg=_macro_cfg(source_connector_enabled=True)
        )
    )
    sids = {i.source_id for i in ev.evidence_items}
    assert not (MACRO_IDS & sids)
    # No macro gap bleeds into the single-company gap set either.
    gap_sids = {g.source_id for g in ev.source_gaps}
    assert not (MACRO_IDS & gap_sids)
