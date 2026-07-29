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
    honest notes; after Phase 29D.2 promoted the three patent venues and 29D.3 the
    three permit venues the summary is 34 enabled / 2 scaffolded / 2 planned / 38
    total.
  * ``collect_theme_event_evidence`` returns event refs for a relevant theme when
    ``source_event_enabled`` is True and is completely DARK when False; secret-free.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.services import safety_terms
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.llm import discovery_council as discovery_council_mod
from app.services.llm.discovery_council import (
    _event_discovery_facts,
    maybe_run_discovery_council,
)
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient
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
    # tender event venues (29D.1) + 3 patent office / index venues (29D.2) + 3
    # permit / regulatory-event venues (29D.3) = 34 enabled.
    assert summary["enabled"] == 35  # +1: local-language business press (Phase 30B)
    assert summary["scaffolded"] == 2
    assert summary["planned"] == 1  # only OpenBB remains planned (Phase 30B)
    assert summary["total"] == 38
    assert summary["total"] == len(reg.all_sources())
    # Health covers every event connector, network-free.
    keys = {h.connector_key for h in reg.health()}
    assert EVENT_IDS <= keys


def test_patents_promoted_by_phase_29d2():
    """The patent venues were promoted to enabled in Phase 29D.2; after Phase 30B
    promoted the local-language business press, only OpenBB remains planned."""
    reg = build_registry()
    planned_ids = {s.source_id for s in reg.planned_sources()}
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    assert {"google_patents", "uspto", "epo_espacenet"} <= enabled_ids
    assert not ({"google_patents", "uspto", "epo_espacenet"} & planned_ids)
    assert planned_ids == {"openbb"}
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


# ===========================================================================
# Task 2 — discovery council cites event references (as run facts R#)
# ===========================================================================


def _discovery_cfg(
    event: bool = True, macro: bool = False, max_items: int = 3
) -> Settings:
    return Settings(
        llm_council_enabled=True,
        llm_discovery_council_enabled=True,
        llm_provider_council="fake",
        source_event_enabled=event,
        source_event_max_items=max_items,
        source_macro_enabled=macro,
    )


def _event_run(
    theme: str = "defense", region: str | None = "Europe"
) -> dict[str, Any]:
    return {
        "run_id": "event-run",
        "mode": "thesis",
        "status": "completed",
        "thesis_text": f"{theme} procurement exposed producers",
        "parsed_thesis": {"theme": theme, "region": region},
        "config": {"region": region},
        "provider": "free_real",
        "lookback_days": 90,
        "universe_count": 3,
        "candidate_count": 1,
        "error_count": 0,
        "warnings": [],
    }


def _event_cands() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "cand-1",
            "ticker": "XYZ",
            "exchange": "US",
            "company_name": "XYZ Defense Corp",
            "country": "United States",
            "sector": "Industrials",
            "data_coverage": {},
        }
    ]


def _spy_builder():
    """A side_effect wrapper capturing the pack + event/macro_evidence kwargs."""
    captured: dict[str, Any] = {}
    real = discovery_council_mod.build_discovery_evidence_pack

    def _spy(**kwargs: Any):
        pack = real(**kwargs)
        captured["pack"] = pack
        captured["event_evidence"] = kwargs.get("event_evidence")
        captured["macro_evidence"] = kwargs.get("macro_evidence")
        return pack

    return captured, _spy


def test_event_discovery_facts_are_weak_context():
    cfg = _discovery_cfg()
    events = asyncio.run(collect_theme_event_evidence("defense", "Europe", cfg))
    facts = _event_discovery_facts(events)
    assert facts  # defense is a procurement-relevant theme
    for f in facts:
        assert f["label"] == "event_context"
        detail = f["detail"].lower()
        assert "weak" in detail
        assert "not a candidate" in detail
        assert "no specific award" in detail
        # The only digit permitted in the detail is the "(T2)" tier label; no
        # fabricated award amount / contract number / date leaks (the reference
        # excerpt itself is proven digit-free at the connector level above).
        assert not _DIGIT_RE.search(f["detail"].replace("(T2)", "")), f["detail"]
        # Recommendation-free.
        assert safety_terms.scan_text(f["detail"]) == []


def test_discovery_pack_appends_citeable_event_run_facts():
    cfg = _discovery_cfg()
    run, cands = _event_run("defense"), _event_cands()
    events = asyncio.run(collect_theme_event_evidence("defense", "Europe", cfg))
    event_facts = _event_discovery_facts(events)
    assert event_facts

    pack = build_discovery_evidence_pack(
        run=run,
        candidates=cands,
        event_evidence=event_facts,
        extra_known_gaps=events.gap_messages(),
    )
    event_rf = [f for f in pack.run_facts if f.label == "event_context"]
    assert event_rf
    ids = pack.evidence_ids()
    for f in event_rf:
        # Every event reference is a citeable R# run fact.
        assert re.fullmatch(r"R\d+", f.id)
        assert f.id in ids
        assert "no specific award" in (f.detail or "").lower()
        assert safety_terms.scan_text(f.detail or "") == []
    # Honest "tenders / awards not fetched" gaps are threaded into known_gaps.
    assert any("not fetched at report time" in g.lower() for g in pack.known_gaps)


def test_discovery_pack_byte_identical_without_event():
    run, cands = _event_run("defense"), _event_cands()
    base = build_discovery_evidence_pack(run=run, candidates=cands)
    with_none = build_discovery_evidence_pack(
        run=run, candidates=cands, event_evidence=None
    )
    assert base.model_dump() == with_none.model_dump()
    assert not any(f.label == "event_context" for f in base.run_facts)


def test_maybe_run_discovery_council_threads_event_when_enabled():
    run, cands = _event_run("defense"), _event_cands()
    captured, spy = _spy_builder()
    with patch.object(
        discovery_council_mod, "build_discovery_evidence_pack", side_effect=spy
    ):
        result = asyncio.run(
            maybe_run_discovery_council(
                run=run,
                candidates=cands,
                cfg=_discovery_cfg(event=True),
                client=FakeDiscoveryLLMClient(),
            )
        )
    assert result.llm_used is True
    assert captured["event_evidence"]  # non-empty event references were passed
    pack = captured["pack"]
    event_rf = [f for f in pack.run_facts if f.label == "event_context"]
    assert event_rf
    ids = pack.evidence_ids()
    assert all(f.id in ids for f in event_rf)  # citeable
    assert any("not fetched at report time" in g.lower() for g in pack.known_gaps)


def test_maybe_run_discovery_council_dark_when_event_disabled():
    run, cands = _event_run("defense"), _event_cands()
    captured, spy = _spy_builder()
    with patch.object(
        discovery_council_mod, "build_discovery_evidence_pack", side_effect=spy
    ):
        asyncio.run(
            maybe_run_discovery_council(
                run=run,
                candidates=cands,
                cfg=_discovery_cfg(event=False),
                client=FakeDiscoveryLLMClient(),
            )
        )
    # No event references passed and no event run facts in the pack.
    assert captured["event_evidence"] is None
    assert not [f for f in captured["pack"].run_facts if f.label == "event_context"]


def test_maybe_run_discovery_council_macro_unchanged_when_only_event_toggles():
    """Independence: toggling ONLY the event flag never adds macro run facts."""
    run, cands = _event_run("defense"), _event_cands()
    # Macro off + event on: event facts present, macro NOT threaded (None).
    captured, spy = _spy_builder()
    with patch.object(
        discovery_council_mod, "build_discovery_evidence_pack", side_effect=spy
    ):
        asyncio.run(
            maybe_run_discovery_council(
                run=run,
                candidates=cands,
                cfg=_discovery_cfg(event=True, macro=False),
                client=FakeDiscoveryLLMClient(),
            )
        )
    assert captured["event_evidence"]
    assert captured["macro_evidence"] is None
    assert not [
        f for f in captured["pack"].run_facts if f.label == "macro_context"
    ]


# ===========================================================================
# Task 2 — company report optional event-context block
# ===========================================================================


def _defense_snapshot() -> dict[str, Any]:
    """A defense contractor: its sector/industry make the procurement venues
    relevant, so an event reference surfaces for the company theme."""
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "company_identity": {
            "ticker": "DEF",
            "legal_name": "Defense Systems PLC",
            "exchange": "LSE",
            "country_domicile": "United Kingdom",
        },
        "profile": {"sector": "Industrials", "industry": "Aerospace & Defense"},
    }


@pytest.fixture
def enable_council(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    yield


@pytest.fixture
def enable_council_and_event(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    monkeypatch.setattr(config.settings, "source_event_enabled", True)
    yield


async def _generate(mock_db, snapshot):
    service = FinalReportGeneratorService()
    return await service._generate_and_save(
        db=mock_db,
        scorecard=None,
        candidate=None,
        source_report=None,
        company_record=None,
        citations=[],
        sources=[],
        state={"company_snapshot": snapshot, "catalyst_discovery": None},
    )


def _captured_report_content(mock_db) -> dict[str, Any]:
    assert mock_db.add.called, "expected a report to be saved"
    report = mock_db.add.call_args[0][0]
    content: dict[str, Any] = {}
    pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    for match in pattern.finditer(report.content_markdown or ""):
        block = json.loads(match.group(1))
        if isinstance(block, dict):
            content.update(block)
    return content


async def test_company_report_renders_event_block_when_enabled(
    mock_db, enable_council_and_event
) -> None:
    resp = await _generate(mock_db, _defense_snapshot())
    # Invariants hold on the event-on path.
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    block = content.get("industry_event_context")
    assert block is not None, "event-on report must carry an industry_event_context block"
    assert block["value"], "expected at least one procurement / tender reference"
    assert block["human_review_required"] is True
    # Honest WEAK CONTEXT note — not company-specific, not a catalyst/trade signal.
    note = block["note"].lower()
    assert "not " in note and "company-specific evidence" in note
    assert "never a direct company catalyst" in note
    assert "trade signal" in note
    assert "weak" in note
    # Reference-only: a URL + tenders reference but NO specific award / amount /
    # contract number / date (which would surface as digits) in the reference text.
    for item in block["value"]:
        assert item["url"]
        assert item["tenders_reference"]
        assert not _DIGIT_RE.search(item["tenders_reference"])
    # No forbidden rating / valuation vocab anywhere in the block.
    assert safety_terms.scan_value(block) == []

    # The compact council metadata path also carries the event context.
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["event_context"]


async def test_company_report_no_event_block_when_disabled(
    mock_db, enable_council
) -> None:
    """Council on but event flag off → block absent, report unchanged + safe."""
    resp = await _generate(mock_db, _defense_snapshot())
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    assert "industry_event_context" not in content
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["event_context"] == []


async def test_company_report_macro_untouched_when_only_event_toggles(
    mock_db, enable_council_and_event
) -> None:
    """Independence: with only the event flag on, the macro block/context stay
    absent/empty while the event block/context are present."""
    await _generate(mock_db, _defense_snapshot())
    content = _captured_report_content(mock_db)
    assert "industry_event_context" in content
    assert "industry_macro_context" not in content
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["event_context"]
    assert report.source_summary_json["llm_council"]["macro_context"] == []
