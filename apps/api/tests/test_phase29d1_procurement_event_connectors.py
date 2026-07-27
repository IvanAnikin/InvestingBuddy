"""
Phase 29D.1 — Procurement / tender event-trigger reference connectors + collector.

Establishes a PARALLEL event-trigger layer (cloning the 29C macro reference
pattern) for procurement / tender venues (EU TED, USAspending.gov), driven by the
generic ``EventReferenceConnector``. A procurement / tender reference is a WEAK
internal research-priority signal only — never a specific award, never a
materiality claim, never a trade signal.

Covers:
  * ``fetch_events`` emits a bounded T2 ``government_data`` source reference +
    honest ``data_not_sourced`` gap, with no fabricated award / contractor /
    amount / contract number / date (digit-scan on the reference text, URL
    excluded), a WEAK + needs_human_review marker, a populated ``stale_after_days``
    (freshness), and no forbidden (rating / valuation) vocab.
  * The connector stays quiet for an irrelevant theme;
    ``fetch_macro_context`` / ``fetch_filings`` / ``search_company`` return an
    honest not-eligible gap.
  * eu_ted + usaspending are enabled procurement / T2 sources in the registry with
    honest notes; the summary is 28 enabled / 2 scaffolded / 5 planned / 35 total.
  * ``collect_theme_event_evidence`` returns event refs for a relevant theme when
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
    EVENT_SOURCES,
    EventReferenceConnector,
    build_event_connectors,
    event_spec_for,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import assert_registry_safe
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    ConnectorStatus,
    ProviderType,
    SourceStatus,
)

EVENT_IDS = {"eu_ted", "usaspending"}

# A representative relevant theme for each venue (forces a reference).
RELEVANT_THEME: dict[str, str] = {
    "eu_ted": "defense",
    "usaspending": "federal award",
}

# A digit anywhere in the reference *text* (URL excluded) would mean a fabricated
# award amount, contract number, or date leaked. Reference text must have none.
_DIGIT_RE = re.compile(r"\d")


def _event_cfg(**over) -> Settings:
    base = dict(source_event_enabled=True, source_event_max_items=3)
    base.update(over)
    return Settings(**base)


def _evidence_text(item) -> str:
    # item.url is intentionally excluded — a fixed landing-page URL may carry a
    # path digit; the reference *text* must not carry an amount, number, or date.
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


@pytest.mark.parametrize("sid", sorted(EVENT_IDS))
def test_event_connector_emits_t2_reference_and_honest_gap(sid):
    spec = event_spec_for(sid)
    assert spec is not None
    assert spec.provider_type == ProviderType.procurement
    conn = EventReferenceConnector(spec)
    assert conn.status == ConnectorStatus.enabled

    result = asyncio.run(conn.fetch_events(QueryContext(query=RELEVANT_THEME[sid])))
    assert result.ok
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    # Reference-only T2 government_data item — NOT government_contract (must not
    # imply a real award).
    assert item.source_type == "government_data"
    assert item.content_source_tier == T2_REGULATOR_OR_GOV
    assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
    assert item.url == spec.url
    assert item.data_quality == "reference_only"
    # Weak internal research-priority signal.
    assert item.confidence == "low"
    # Freshness: stale_after_days populated from the venue refresh cadence.
    assert item.stale_after_days == spec.refresh_cadence_days
    assert item.stale_after_days is not None and item.stale_after_days >= 1
    # Honest gap: tenders / awards not fetched, does not block research-complete.
    assert len(result.source_gaps) == 1
    gap = result.source_gaps[0]
    assert gap.gap_type == GapType.data_not_sourced
    assert gap.blocks_research_complete is False
    assert "not fetched at report time" in gap.message.lower()
    assert "venue reference only" in gap.message.lower()


def test_event_reference_carries_no_fabricated_award_data():
    """Reference text names the venue + themes only — never an award / amount /
    contractor / contract number / date (which would surface as digits)."""
    for sid in ("eu_ted", "usaspending"):
        conn = EventReferenceConnector(event_spec_for(sid))
        result = asyncio.run(
            conn.fetch_events(QueryContext(query=RELEVANT_THEME[sid]))
        )
        assert result.evidence_items, sid
        text = _evidence_text(result.evidence_items[0])
        assert not _DIGIT_RE.search(text), f"{sid}: numeric leaked -> {text}"


def test_event_reference_is_weak_needs_review_signal():
    """Every reference is explicitly WEAK + needs_human_review, no materiality."""
    for spec in EVENT_SOURCES:
        conn = EventReferenceConnector(spec)
        item = asyncio.run(
            conn.fetch_events(QueryContext(query=RELEVANT_THEME[spec.source_id]))
        ).evidence_items[0]
        prov = " ".join(item.provenance).lower()
        warns = " ".join(item.warnings).lower()
        assert "needs_human_review=true" in prov
        assert "weak" in prov
        assert "weak internal research-priority signal" in warns
        # Not a materiality / trade-signal claim.
        assert "not a materiality claim" in warns
        assert "not a trade signal" in warns


def test_event_reference_is_recommendation_free():
    """No rating / valuation / trading-signal vocab — events are a weak signal."""
    for spec in EVENT_SOURCES:
        conn = EventReferenceConnector(spec)
        result = asyncio.run(
            conn.fetch_events(QueryContext(query=RELEVANT_THEME[spec.source_id]))
        )
        item = result.evidence_items[0]
        blob = " ".join([item.title or "", item.excerpt or "", " ".join(item.warnings)])
        assert safety_terms.scan_text(blob) == [], f"unsafe: {blob!r}"
        # The gap message must also pass the report safety gate.
        assert safety_terms.scan_text(result.source_gaps[0].message) == []


@pytest.mark.parametrize(
    ("sid", "themes"),
    [
        ("eu_ted", ["defense", "infrastructure", "procurement", "tenders",
                    "contracts", "rail", "grid", "energy"]),
        ("usaspending", ["federal award", "federal contract", "defense",
                         "government spending", "infrastructure", "grid", "grants"]),
    ],
)
def test_event_source_answers_expected_themes(sid, themes):
    conn = EventReferenceConnector(event_spec_for(sid))
    for theme in themes:
        result = asyncio.run(conn.fetch_events(QueryContext(query=theme)))
        assert result.evidence_items, f"{sid} should cover theme {theme!r}"
        assert result.evidence_items[0].source_id == sid


def test_eu_ted_answers_region_even_without_theme():
    """A European ask surfaces the EU TED procurement venue."""
    conn = EventReferenceConnector(event_spec_for("eu_ted"))
    result = asyncio.run(
        conn.fetch_events(QueryContext(query=None, region="Europe"))
    )
    assert result.evidence_items
    assert result.evidence_items[0].source_id == "eu_ted"


def test_event_source_quiet_for_irrelevant_theme():
    """Every event venue stays quiet for an unrelated theme (no theme/region)."""
    for sid in EVENT_IDS:
        conn = EventReferenceConnector(event_spec_for(sid))
        result = asyncio.run(conn.fetch_events(QueryContext(query="inflation")))
        assert result.evidence_items == [], sid
        assert result.source_gaps == [], sid


def test_event_connector_not_a_macro_or_company_source():
    """fetch_macro_context / fetch_filings / search_company → honest not-eligible."""
    conn = EventReferenceConnector(event_spec_for("eu_ted"))
    company = CompanyContext(ticker="AAPL")
    macro = asyncio.run(conn.fetch_macro_context(QueryContext(query="defense")))
    filings = asyncio.run(conn.fetch_filings(company, QueryContext()))
    search = asyncio.run(conn.search_company(company, QueryContext()))
    for res in (macro, filings, search):
        assert res.evidence_items == []
        assert res.source_gaps
        assert res.source_gaps[0].gap_type == GapType.source_not_eligible


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_event_sources_enabled_in_registry_with_honest_note():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert EVENT_IDS <= enabled_ids
    # eu_ted + usaspending were promoted OUT of the planned set.
    assert not (EVENT_IDS & planned_ids)
    for sid in EVENT_IDS:
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.provider_type == ProviderType.procurement
        assert src.tier == T2_REGULATOR_OR_GOV
        assert src.capabilities == ["fetch_events"]
        note = (src.reliability_note or "").lower()
        assert "procurement / tender venue reference" in note
        assert "live tenders / awards not fetched at report time" in note
        assert "weak internal research-priority signal" in note
        conn = reg.connectors()[sid]
        assert isinstance(conn, EventReferenceConnector)
        assert conn.status == ConnectorStatus.enabled
        assert conn.is_live


def test_registry_summary_counts_after_event_layer():
    reg = build_registry()
    summary = reg.summary()
    # 11 regulator-layer + 15 macro/commodity/policy (29C) + 2 procurement /
    # tender event venues (29D.1) = 28 enabled.
    assert summary["enabled"] == 28
    assert summary["scaffolded"] == 2
    assert summary["planned"] == 5
    assert summary["total"] == 35
    assert summary["total"] == len(reg.all_sources())
    # Health covers every event connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert EVENT_IDS <= keys


def test_patents_stay_planned_after_event_layer():
    """Patents remain Phase 29D planned; only procurement was promoted."""
    reg = build_registry()
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert {"google_patents", "uspto", "epo_espacenet"} <= planned_ids
    assert "openbb" in planned_ids
    assert not (EVENT_IDS & planned_ids)


def test_registry_stays_secret_free_with_event_layer():
    reg = build_registry()
    assert_registry_safe(reg)
    build_event_connectors()  # importable + constructible without secrets


# ---------------------------------------------------------------------------
# Theme collector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("theme", "expected_sid"),
    [
        ("defense", "eu_ted"),
        ("infrastructure", "eu_ted"),
        ("EU tenders", "eu_ted"),
        ("federal award", "usaspending"),
        ("government spending", "usaspending"),
        ("US federal contract", "usaspending"),
    ],
)
def test_collect_theme_event_evidence_returns_refs_when_enabled(theme, expected_sid):
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence(theme, cfg=cfg))
    assert isinstance(ev, ThemeEventEvidence)
    sids = {i.source_id for i in ev.evidence_items}
    assert expected_sid in sids, f"{theme} -> {sids}"
    for item in ev.evidence_items:
        assert item.source_type == "government_data"
        assert item.content_source_tier == T2_REGULATOR_OR_GOV
        assert item.confidence == "low"
    # Each reference carries an honest "tenders / awards not fetched" gap.
    assert ev.source_gaps
    assert all(g.gap_type == GapType.data_not_sourced for g in ev.source_gaps)
    assert ev.gap_messages()


def test_collect_theme_event_evidence_respects_max_items():
    cfg = _event_cfg(source_event_max_items=1)
    ev = asyncio.run(collect_theme_event_evidence("defense", cfg=cfg))
    assert len(ev.evidence_items) == 1


def test_collect_theme_event_evidence_is_dark_when_disabled():
    cfg = Settings()  # source_event_enabled defaults False
    assert cfg.source_event_enabled is False
    ev = asyncio.run(collect_theme_event_evidence("defense", cfg=cfg))
    assert ev.evidence_items == []
    assert ev.source_gaps == []
    assert ev.warnings == []


def test_collect_theme_event_evidence_independent_of_macro_flag():
    """The event layer is gated by its OWN flag, not source_macro_enabled."""
    # Macro on, event off → dark.
    cfg = Settings(source_macro_enabled=True, source_event_enabled=False)
    ev = asyncio.run(collect_theme_event_evidence("defense", cfg=cfg))
    assert ev.evidence_items == []
    # Event on, macro off → live.
    cfg2 = Settings(source_macro_enabled=False, source_event_enabled=True)
    ev2 = asyncio.run(collect_theme_event_evidence("defense", cfg=cfg2))
    assert ev2.evidence_items


def test_collect_theme_event_evidence_quiet_for_irrelevant_theme():
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence("inflation", cfg=cfg))
    assert ev.evidence_items == []


def test_collect_theme_event_evidence_secret_free():
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence("defense", cfg=cfg))
    blob = json.dumps(ev.model_dump(mode="json")).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "secret"):
        assert needle not in blob
