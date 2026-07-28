"""
Phase 29D.3 — Permit / regulatory-event event-trigger reference connectors.

Extends the Phase 29D EVENT layer to PERMITS / regulatory-event venues, reusing
the generic ``EventReferenceConnector``, the ``collect_theme_event_evidence``
collector, the discovery / report wiring, and the ``source_event_enabled`` flag
(NO new wiring, NO new flag). A permit reference is a WEAK internal
research-priority signal only — never a specific docket / permit, never a
materiality claim, never a trade signal, and CRITICALLY never a
regulatory-outcome / approval conclusion.

Covers:
  * ``fetch_events`` emits a bounded T2 ``government_data`` source reference (US
    federal FERC / NRC / EPA) + an honest ``data_not_sourced`` gap, with no
    fabricated docket / case / permit number / applicant / date (digit-scan on the
    reference text, URL excluded), no regulatory-outcome / approval / decision
    vocab, a WEAK + needs_human_review marker, a populated ``stale_after_days``
    (freshness), and no forbidden rating / valuation vocab.
  * Theme mapping: ferc → grid / transmission / pipeline / energy; us_nrc →
    nuclear; us_epa → environmental / emissions; all quiet for an unrelated theme
    (e.g. "tariffs" / "patents") and quiet on a bare region query (permits are
    purely thematic). ``fetch_macro_context`` / ``fetch_filings`` /
    ``search_company`` return an honest not-eligible gap.
  * ProviderType.permits exists; ferc + us_nrc + us_epa are enabled permit sources
    in the registry with honest notes and the correct T2 tier; the summary is 34
    enabled / 2 scaffolded / 2 planned / 38 total.
  * ``collect_theme_event_evidence`` returns permit refs for a relevant theme when
    ``source_event_enabled`` is True and is completely DARK when False; secret-free.
  * The discovery-council run-fact label is per-provider: procurement is unchanged
    ("procurement / tender venue reference"), patents and permits get their own
    corrected labels.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from app.core.config import Settings
from app.services import safety_terms
from app.services.llm.discovery_council import _event_discovery_facts
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
    ConnectorStatus,
    ProviderType,
    SourceStatus,
)

PERMIT_IDS = {"ferc", "us_nrc", "us_epa"}

# Every permit / regulatory-event venue is a T2 government publisher.
PERMIT_TIERS: dict[str, str] = {
    "ferc": T2_REGULATOR_OR_GOV,
    "us_nrc": T2_REGULATOR_OR_GOV,
    "us_epa": T2_REGULATOR_OR_GOV,
}

# A representative relevant theme for each venue (forces a reference). Chosen so
# each isolates its own regulatory domain (grid/energy vs nuclear vs environmental).
RELEVANT_THEME: dict[str, str] = {
    "ferc": "transmission",
    "us_nrc": "nuclear reactor",
    "us_epa": "environmental emissions",
}

# A digit anywhere in the reference *text* (URL excluded) would mean a fabricated
# docket / case / permit number, applicant, or date leaked. Reference text must
# have none.
_DIGIT_RE = re.compile(r"\d")

# Positive regulatory-outcome vocabulary the reference must NEVER contain — not
# even inside a negated disclaimer. (The honest "no regulatory-outcome conclusion
# is drawn" disclaimer uses the compound "regulatory-outcome"; asserting a
# specific approval / denial / grant is what is forbidden, mirroring the patent
# layer's treatment of infringement / validity.)
_FORBIDDEN_OUTCOME = (
    "approved",
    "approval",
    "denied",
    "denial",
    "granted",
    "rejected",
    "revoked",
    "authorized",
    "authorization",
    "decision",
    "ruling",
)


def _event_cfg(**over) -> Settings:
    base = dict(source_event_enabled=True, source_event_max_items=8)
    base.update(over)
    return Settings(**base)


def _evidence_text(item) -> str:
    # item.url is intentionally excluded — a fixed landing-page URL may carry a
    # path digit; the reference *text* must not carry a docket / permit number or
    # date.
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
# Taxonomy
# ---------------------------------------------------------------------------


def test_provider_type_permits_exists():
    assert ProviderType.permits.value == "permits"


# ---------------------------------------------------------------------------
# Connector: fetch_events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sid", sorted(PERMIT_IDS))
def test_permit_connector_emits_t2_reference_and_honest_gap(sid):
    spec = event_spec_for(sid)
    assert spec is not None
    assert spec.provider_type == ProviderType.permits
    conn = EventReferenceConnector(spec)
    assert conn.status == ConnectorStatus.enabled

    result = asyncio.run(conn.fetch_events(QueryContext(query=RELEVANT_THEME[sid])))
    assert result.ok
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    # Reference-only T2 government_data item — NOT a specific permit / approval.
    assert item.source_type == "government_data"
    assert item.content_source_tier == PERMIT_TIERS[sid]
    assert item.provider_transport_tier == PERMIT_TIERS[sid]
    assert item.url == spec.url
    assert item.data_quality == "reference_only"
    # Weak internal research-priority signal.
    assert item.confidence == "low"
    # Freshness: stale_after_days populated from the venue refresh cadence.
    assert item.stale_after_days == spec.refresh_cadence_days
    assert item.stale_after_days is not None and item.stale_after_days >= 1
    # Honest gap: permit filings / dockets not fetched, does not block completion.
    assert len(result.source_gaps) == 1
    gap = result.source_gaps[0]
    assert gap.gap_type == GapType.data_not_sourced
    assert gap.blocks_research_complete is False
    assert "not fetched at report time" in gap.message.lower()
    assert "venue reference only" in gap.message.lower()
    assert "permit" in gap.message.lower()


def test_permit_reference_carries_no_fabricated_docket_data():
    """Reference text names the venue + themes only — never a docket / case /
    permit number / applicant / date (which would surface as digits)."""
    for sid in sorted(PERMIT_IDS):
        conn = EventReferenceConnector(event_spec_for(sid))
        result = asyncio.run(conn.fetch_events(QueryContext(query="permit")))
        assert result.evidence_items, sid
        text = _evidence_text(result.evidence_items[0])
        assert not _DIGIT_RE.search(text), f"{sid}: numeric leaked -> {text}"


def test_permit_reference_draws_no_regulatory_outcome_conclusion():
    """No approval / denial / grant / decision vocab in the reference text or gap —
    a permit reference is a venue pointer, never a regulatory-outcome conclusion."""
    for spec in PERMIT_SOURCES:
        conn = EventReferenceConnector(spec)
        result = asyncio.run(conn.fetch_events(QueryContext(query="permitting")))
        item = result.evidence_items[0]
        gap = result.source_gaps[0]
        blob = (_evidence_text(item) + " " + gap.message).lower()
        for word in _FORBIDDEN_OUTCOME:
            assert word not in blob, f"{spec.source_id}: outcome vocab leaked -> {word}"
        # The reference states, positively, that it draws no such conclusion.
        assert "no regulatory-outcome" in item.excerpt.lower()
        assert "not a materiality claim" in item.excerpt.lower()


def test_permit_reference_is_weak_needs_review_signal():
    """Every reference is explicitly WEAK + needs_human_review, no materiality."""
    for spec in PERMIT_SOURCES:
        conn = EventReferenceConnector(spec)
        item = asyncio.run(
            conn.fetch_events(QueryContext(query=RELEVANT_THEME[spec.source_id]))
        ).evidence_items[0]
        prov = " ".join(item.provenance).lower()
        warns = " ".join(item.warnings).lower()
        assert "needs_human_review=true" in prov
        assert "weak" in prov
        assert "weak internal research-priority signal" in warns
        # Not a materiality / trade-signal / regulatory-outcome claim.
        assert "not a materiality claim" in warns
        assert "not a trade signal" in warns


def test_permit_reference_is_recommendation_free():
    """No rating / valuation / trading-signal vocab — permits are a weak signal."""
    for spec in PERMIT_SOURCES:
        conn = EventReferenceConnector(spec)
        result = asyncio.run(
            conn.fetch_events(QueryContext(query=RELEVANT_THEME[spec.source_id]))
        )
        item = result.evidence_items[0]
        blob = " ".join(
            [item.title or "", item.excerpt or "", " ".join(item.warnings)]
        )
        assert safety_terms.scan_text(blob) == [], f"unsafe: {blob!r}"
        # The gap message must also pass the report safety gate.
        assert safety_terms.scan_text(result.source_gaps[0].message) == []


@pytest.mark.parametrize(
    ("sid", "themes"),
    [
        ("ferc", ["grid", "transmission", "pipeline", "energy", "lng",
                  "power plant", "permit", "licensing"]),
        ("us_nrc", ["nuclear", "reactor", "permit", "licensing"]),
        ("us_epa", ["environmental", "emissions", "pollution", "mining",
                    "permit", "licensing"]),
    ],
)
def test_permit_source_answers_expected_themes(sid, themes):
    conn = EventReferenceConnector(event_spec_for(sid))
    for theme in themes:
        result = asyncio.run(conn.fetch_events(QueryContext(query=theme)))
        assert result.evidence_items, f"{sid} should cover theme {theme!r}"
        assert result.evidence_items[0].source_id == sid


def test_permit_source_quiet_for_irrelevant_theme():
    """Every permit venue stays quiet for an unrelated theme (tariffs, patents)."""
    for sid in sorted(PERMIT_IDS):
        conn = EventReferenceConnector(event_spec_for(sid))
        for theme in ("tariffs", "patents", "inflation", "defense"):
            result = asyncio.run(conn.fetch_events(QueryContext(query=theme)))
            assert result.evidence_items == [], f"{sid} noisy for {theme!r}"
            assert result.source_gaps == [], sid


def test_permit_source_quiet_for_bare_region():
    """Permits are purely thematic — a bare region query never surfaces them
    (unlike the region-scoped procurement venues)."""
    for sid in sorted(PERMIT_IDS):
        conn = EventReferenceConnector(event_spec_for(sid))
        for region in ("North America", "Europe"):
            result = asyncio.run(
                conn.fetch_events(QueryContext(query=None, region=region))
            )
            assert result.evidence_items == [], f"{sid} region-matched {region!r}"


def test_permit_connector_not_a_macro_or_company_source():
    """fetch_macro_context / fetch_filings / search_company → honest not-eligible."""
    conn = EventReferenceConnector(event_spec_for("ferc"))
    company = CompanyContext(ticker="AAPL")
    macro = asyncio.run(conn.fetch_macro_context(QueryContext(query="grid")))
    filings = asyncio.run(conn.fetch_filings(company, QueryContext()))
    search = asyncio.run(conn.search_company(company, QueryContext()))
    for res in (macro, filings, search):
        assert res.evidence_items == []
        assert res.source_gaps
        assert res.source_gaps[0].gap_type == GapType.source_not_eligible


def test_permit_sources_are_additive_to_procurement_and_patents():
    """The permit layer is additive: procurement 29D.1 + patents 29D.2 + permits
    29D.3; ALL_EVENT_SOURCES is the union of the three, no id collisions."""
    assert {s.source_id for s in PERMIT_SOURCES} == PERMIT_IDS
    assert set(ALL_EVENT_SOURCES) == (
        set(EVENT_SOURCES) | set(PATENT_SOURCES) | set(PERMIT_SOURCES)
    )
    ids = [s.source_id for s in ALL_EVENT_SOURCES]
    assert len(ids) == len(set(ids)) == 8


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_permit_sources_enabled_in_registry_with_honest_note():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert PERMIT_IDS <= enabled_ids
    # The three permit venues are new enabled sources, never planned.
    assert not (PERMIT_IDS & planned_ids)
    for sid in sorted(PERMIT_IDS):
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.provider_type == ProviderType.permits
        assert src.tier == PERMIT_TIERS[sid]
        assert src.capabilities == ["fetch_events"]
        note = (src.reliability_note or "").lower()
        assert "permit / regulatory-event venue reference" in note
        assert "live permit filings / dockets not fetched at report time" in note
        assert "no regulatory-outcome conclusions" in note
        assert "weak internal research-priority signal" in note
        assert "phase 29d" in note
        conn = reg.connectors()[sid]
        assert isinstance(conn, EventReferenceConnector)
        assert conn.status == ConnectorStatus.enabled
        assert conn.is_live


def test_registry_summary_counts_after_permit_layer():
    reg = build_registry()
    summary = reg.summary()
    # 11 regulator-layer + 15 macro/commodity/policy (29C) + 2 procurement /
    # tender (29D.1) + 3 patent office / index (29D.2) + 3 permit /
    # regulatory-event (29D.3) = 34 enabled.
    assert summary["enabled"] == 34
    assert summary["scaffolded"] == 2
    assert summary["planned"] == 2
    assert summary["total"] == 38
    assert summary["total"] == len(reg.all_sources())
    # Health covers every permit connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert PERMIT_IDS <= keys


def test_only_openbb_and_local_press_stay_planned_after_permits():
    reg = build_registry()
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert planned_ids == {"openbb", "local_language_business_press"}
    assert not (PERMIT_IDS & planned_ids)


def test_registry_stays_secret_free_with_permit_layer():
    reg = build_registry()
    assert_registry_safe(reg)
    build_event_connectors()  # importable + constructible without secrets


# ---------------------------------------------------------------------------
# Theme collector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("theme", "expected_sid"),
    [
        ("electric grid transmission", "ferc"),
        ("energy pipeline lng", "ferc"),
        ("nuclear reactor licensing", "us_nrc"),
        ("environmental emissions", "us_epa"),
        ("industrial pollution permit", "us_epa"),
    ],
)
def test_collect_theme_event_evidence_returns_permit_refs_when_enabled(
    theme, expected_sid
):
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence(theme, cfg=cfg))
    assert isinstance(ev, ThemeEventEvidence)
    sids = {i.source_id for i in ev.evidence_items}
    assert expected_sid in sids, f"{theme} -> {sids}"
    for item in ev.evidence_items:
        assert item.source_type == "government_data"
        assert item.confidence == "low"
    # Each reference carries an honest "permit filings / dockets not fetched" gap.
    assert ev.source_gaps
    assert all(g.gap_type == GapType.data_not_sourced for g in ev.source_gaps)
    assert ev.gap_messages()


def test_collect_theme_event_evidence_permits_dark_when_disabled():
    cfg = Settings()  # source_event_enabled defaults False
    assert cfg.source_event_enabled is False
    ev = asyncio.run(collect_theme_event_evidence("nuclear reactor", cfg=cfg))
    assert ev.evidence_items == []
    assert ev.source_gaps == []
    assert ev.warnings == []


def test_collect_theme_event_evidence_permits_quiet_for_irrelevant_theme():
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence("tariffs", cfg=cfg))
    assert not ({i.source_id for i in ev.evidence_items} & PERMIT_IDS)


def test_collect_theme_event_evidence_permits_secret_free():
    cfg = _event_cfg()
    ev = asyncio.run(collect_theme_event_evidence("nuclear reactor", cfg=cfg))
    blob = json.dumps(ev.model_dump(mode="json")).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "secret"):
        assert needle not in blob


# ---------------------------------------------------------------------------
# Discovery-council run-fact labelling (per provider_type)
# ---------------------------------------------------------------------------


def test_event_discovery_facts_label_permit_and_leave_procurement_unchanged():
    """The run-fact label reflects each source's provider_type: procurement stays
    "procurement / tender venue reference" (byte-identical to 29D.1), patents get
    "patent office / index venue reference", permits get "permit / regulatory-event
    venue reference". Each is weak, non-fabricating and recommendation-free."""
    cfg = _event_cfg()

    # Procurement theme → procurement label, unchanged.
    proc = asyncio.run(collect_theme_event_evidence("defense", "Europe", cfg))
    proc_facts = _event_discovery_facts(proc)
    assert proc_facts
    for f in proc_facts:
        detail = f["detail"].lower()
        assert f["label"] == "event_context"
        assert "procurement / tender venue reference" in detail
        assert "permit" not in detail and "patent" not in detail
        assert "no specific award" in detail
        assert safety_terms.scan_text(f["detail"]) == []

    # Patent theme → patent label.
    pat = asyncio.run(collect_theme_event_evidence("innovation", None, cfg))
    pat_facts = _event_discovery_facts(pat)
    assert pat_facts
    for f in pat_facts:
        detail = f["detail"].lower()
        assert "patent office / index venue reference" in detail
        assert safety_terms.scan_text(f["detail"]) == []

    # Permit theme → permit label, no regulatory-outcome vocab, digit-free.
    perm = asyncio.run(collect_theme_event_evidence("nuclear reactor", None, cfg))
    perm_facts = _event_discovery_facts(perm)
    assert perm_facts
    for f in perm_facts:
        detail = f["detail"].lower()
        assert "permit / regulatory-event venue reference" in detail
        assert "weak" in detail
        assert "not a candidate" in detail
        assert "no regulatory-outcome conclusion" in detail
        for word in _FORBIDDEN_OUTCOME:
            assert word not in detail, f"outcome vocab leaked -> {word}"
        # Only the "(T2)" tier label may carry a digit; nothing fabricated.
        assert not _DIGIT_RE.search(f["detail"].replace("(T2)", "")), f["detail"]
        assert safety_terms.scan_text(f["detail"]) == []
