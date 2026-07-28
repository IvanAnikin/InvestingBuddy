"""
Phase 29C.3 — Policy + government reference connectors + collector tests.

Extends the reference-only MACRO evidence category with POLICY + GOVERNMENT
sources (USTR / EU TARIC, UN Comtrade, NATO defence expenditure, SIPRI military
expenditure, OECD), driven by the *same* generic ``MacroReferenceConnector``
used in Phase 29C.1 / 29C.2. Policy / government is thematic CONTEXT only —
never a company recommendation, catalyst, or geopolitical trading signal.

Covers:
  * ``fetch_macro_context`` emits a bounded T2/T3 ``macro_report`` source
    reference + honest ``data_not_sourced`` gap, with no defence budget /
    spending percentage / tariff rate / subsidy amount numbers, no fabricated
    dates, and no forbidden (rating / valuation) vocab.
  * Each source answers the right policy theme (ustr_taric / un_comtrade →
    tariffs / trade / customs, nato / sipri → defense / military spending, oecd →
    subsidies / industrial policy / energy transition) and stays quiet for an
    unrelated (pure-macro) theme.
  * ustr_taric + un_comtrade were promoted OUT of the planned set; nato / sipri /
    oecd are new enabled reference sources. After Phase 29D.1 promoted the two
    procurement / tender event venues, the registry summary is 28 enabled /
    2 scaffolded / 5 planned and everything stays secret-free.
  * ``collect_theme_macro_evidence`` returns these references for a relevant
    policy theme when ``source_macro_enabled`` is True, and is completely DARK
    when False.
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
    POLICY_GOVERNMENT_SOURCES,
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

# The five 29C.3 policy / government ids and their expected reference tier.
POLICY_GOV_TIERS: dict[str, str] = {
    "ustr_taric": T2_REGULATOR_OR_GOV,
    "un_comtrade": T2_REGULATOR_OR_GOV,
    "nato": T2_REGULATOR_OR_GOV,
    "sipri": T3_INDUSTRY_SPECIALIST,
    "oecd": T2_REGULATOR_OR_GOV,
}
POLICY_GOV_IDS = set(POLICY_GOV_TIERS)

# Every policy / government source uses an existing ProviderType member — the
# trade / tariff / defence sources map to trade_policy, OECD to macro_statistics.
POLICY_GOV_PROVIDERS: dict[str, ProviderType] = {
    "ustr_taric": ProviderType.trade_policy,
    "un_comtrade": ProviderType.trade_policy,
    "nato": ProviderType.trade_policy,
    "sipri": ProviderType.trade_policy,
    "oecd": ProviderType.macro_statistics,
}

# A representative relevant theme for each source (used to force a reference).
RELEVANT_THEME: dict[str, str] = {
    "ustr_taric": "tariffs",
    "un_comtrade": "trade",
    "nato": "defense",
    "sipri": "military spending",
    "oecd": "subsidies",
}

# A digit not part of a URL is disallowed in reference text (no defence budget /
# spending percentage / tariff rate / subsidy amount numbers, no dates).
_DIGIT_RE = re.compile(r"\d")


def _macro_cfg(**over) -> Settings:
    base = dict(source_macro_enabled=True, source_macro_max_items=8)
    base.update(over)
    return Settings(**base)


def _evidence_text(item) -> str:
    # Note: item.url is intentionally excluded — a fixed landing-page URL may
    # legitimately contain a path segment with digits; the reference *text* must
    # not carry a figure or date.
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


@pytest.mark.parametrize("sid", sorted(POLICY_GOV_IDS))
def test_policy_connector_emits_tiered_reference_and_honest_gap(sid):
    spec = macro_spec_for(sid)
    assert spec is not None
    assert spec.provider == POLICY_GOV_PROVIDERS[sid]
    # Policy / government context must not be answered by a generic macro ask.
    assert spec.broad_macro is False
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
    assert item.content_source_tier == POLICY_GOV_TIERS[sid]
    assert item.provider_transport_tier == POLICY_GOV_TIERS[sid]
    assert item.url == spec.url
    assert item.data_quality == "reference_only"
    # Honest gap: figures not fetched, does not block research-complete.
    assert len(result.source_gaps) == 1
    gap = result.source_gaps[0]
    assert gap.gap_type == GapType.data_not_sourced
    assert gap.blocks_research_complete is False
    assert "not fetched at report time" in gap.message.lower()


def test_policy_reference_carries_no_numbers_or_fabricated_dates():
    """Reference text names themes only — never a budget, rate, amount, or date."""
    for spec in POLICY_GOVERNMENT_SOURCES:
        conn = MacroReferenceConnector(spec)
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query=spec.theme_keywords[0]))
        )
        assert result.evidence_items, spec.source_id
        text = _evidence_text(result.evidence_items[0])
        assert not _DIGIT_RE.search(text), f"{spec.source_id}: numeric leaked -> {text}"


def test_policy_reference_is_recommendation_free():
    """No rating / valuation / trading-signal vocab — policy is CONTEXT only."""
    for spec in POLICY_GOVERNMENT_SOURCES:
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
        ("ustr_taric", ["tariffs", "tariff", "trade policy", "customs", "import"]),
        ("un_comtrade", ["trade", "customs", "export", "trade flows", "trade statistics"]),
        ("nato", ["defense", "defence", "nato", "military spending", "procurement"]),
        ("sipri", ["defense", "military expenditure", "arms", "defence spending"]),
        ("oecd", ["subsidies", "industrial policy", "state aid", "energy transition",
                  "grid investment", "tariffs"]),
    ],
)
def test_policy_source_answers_expected_themes(sid, themes):
    conn = MacroReferenceConnector(macro_spec_for(sid))
    for theme in themes:
        result = asyncio.run(conn.fetch_macro_context(QueryContext(query=theme)))
        assert result.evidence_items, f"{sid} should cover theme {theme!r}"
        assert result.evidence_items[0].source_id == sid


def test_policy_source_quiet_for_irrelevant_theme():
    """Every policy / government source stays quiet for an unrelated theme."""
    for sid in POLICY_GOV_IDS:
        conn = MacroReferenceConnector(macro_spec_for(sid))
        result = asyncio.run(
            conn.fetch_macro_context(QueryContext(query="inflation"))
        )
        assert result.evidence_items == [], sid
        assert result.source_gaps == [], sid


def test_policy_connector_not_a_company_filing_source():
    """fetch_filings / fetch_events return an honest not-eligible gap, no evidence."""
    conn = MacroReferenceConnector(macro_spec_for("nato"))
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


def test_policy_sources_enabled_in_registry_with_honest_note():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert POLICY_GOV_IDS <= enabled_ids
    # ustr_taric + un_comtrade were promoted OUT of the planned set.
    assert not (POLICY_GOV_IDS & planned_ids)
    assert not ({"ustr_taric", "un_comtrade"} & planned_ids)
    for sid in POLICY_GOV_IDS:
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.provider_type == POLICY_GOV_PROVIDERS[sid]
        assert src.tier == POLICY_GOV_TIERS[sid]
        note = (src.reliability_note or "").lower()
        assert "reference only" in note
        assert "live figures not fetched at report time" in note
        conn = reg.connectors()[sid]
        assert isinstance(conn, MacroReferenceConnector)
        assert conn.status == ConnectorStatus.enabled
        assert conn.is_live


def test_procurement_and_patents_now_enabled():
    """Procurement (29D.1) and patents (29D.2) are now enabled EVENT venues.

    The procurement / tender EVENT venues (eu_ted, usaspending) were promoted to
    enabled reference-only event sources in Phase 29D.1 and the patent office /
    index venues in Phase 29D.2; only the OpenBB toolkit and the local-language
    business press remain planned.
    """
    reg = build_registry()
    planned_ids = {s.source_id for s in reg.planned_sources()}
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    # Procurement (29D.1) + patents (29D.2) now enabled, no longer planned.
    assert {"usaspending", "eu_ted"} <= enabled_ids
    assert {"google_patents", "uspto", "epo_espacenet"} <= enabled_ids
    assert not ({"usaspending", "eu_ted"} & planned_ids)
    assert not ({"google_patents", "uspto", "epo_espacenet"} & planned_ids)
    # Only the OpenBB toolkit stays planned (Phase 30B promoted the local-language
    # business press to enabled).
    assert planned_ids == {"openbb"}


def test_registry_summary_counts_after_policy_layer():
    reg = build_registry()
    summary = reg.summary()
    # 11 regulator-layer + 5 macro (29C.1) + 5 commodity / energy (29C.2)
    # + 5 policy / government (29C.3) + 2 procurement / tender events (29D.1)
    # + 3 patent office / index events (29D.2) + 3 permit / regulatory-event
    # sources (29D.3) = 34 enabled.
    assert summary["enabled"] == 35  # +1: local-language business press (Phase 30B)
    assert summary["scaffolded"] == 2
    assert summary["planned"] == 1  # only OpenBB remains planned (Phase 30B)
    assert summary["total"] == len(reg.all_sources())
    # Health covers every policy / government connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert POLICY_GOV_IDS <= keys


def test_registry_stays_secret_free_with_policy_layer():
    reg = build_registry()
    assert_registry_safe(reg)
    build_macro_connectors()  # importable + constructible without secrets


# ---------------------------------------------------------------------------
# Theme collector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("theme", "expected_sid"),
    [
        ("tariffs", "ustr_taric"),
        ("customs", "ustr_taric"),
        ("trade flows", "un_comtrade"),
        ("defense budget", "nato"),
        ("procurement", "nato"),
        ("military expenditure", "sipri"),
        ("arms", "sipri"),
        ("subsidies", "oecd"),
        ("industrial policy", "oecd"),
        ("energy transition", "oecd"),
    ],
)
def test_collect_theme_macro_evidence_returns_policy_refs(theme, expected_sid):
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence(theme, cfg=cfg))
    sids = {i.source_id for i in ev.evidence_items}
    assert expected_sid in sids, f"{theme} -> {sids}"
    for item in ev.evidence_items:
        assert item.source_type == "macro_report"
    # Each reference carries an honest "figures not fetched" gap.
    assert ev.source_gaps
    assert all(g.gap_type == GapType.data_not_sourced for g in ev.source_gaps)


def test_collect_theme_macro_evidence_policy_dark_when_disabled():
    cfg = Settings()  # source_macro_enabled defaults False
    assert cfg.source_macro_enabled is False
    ev = asyncio.run(collect_theme_macro_evidence("tariffs", cfg=cfg))
    assert ev.evidence_items == []
    assert ev.source_gaps == []
    assert ev.warnings == []


def test_collect_theme_macro_evidence_policy_secret_free():
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence("defense", cfg=cfg))
    blob = json.dumps(ev.model_dump(mode="json")).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "secret"):
        assert needle not in blob


def test_collect_theme_macro_evidence_policy_quiet_for_macro_theme():
    """A pure-macro theme returns no policy / government references."""
    cfg = _macro_cfg()
    ev = asyncio.run(collect_theme_macro_evidence("inflation", cfg=cfg))
    sids = {i.source_id for i in ev.evidence_items}
    assert not (POLICY_GOV_IDS & sids)
