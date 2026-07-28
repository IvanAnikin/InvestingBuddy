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
from typing import Any
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.services import safety_terms
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.llm import discovery_council as discovery_council_mod
from app.services.llm.discovery_council import (
    _macro_discovery_facts,
    _run_theme_region,
    maybe_run_discovery_council,
)
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient
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
    # 11 regulator-layer enabled + 5 reference-only macro sources (29C.1) + 5
    # reference-only commodity / energy sources (29C.2) + 5 reference-only policy /
    # government sources (29C.3) + 2 reference-only procurement / tender event
    # sources (29D.1) + 3 reference-only patent office / index sources (29D.2) + 3
    # reference-only permit / regulatory-event sources (29D.3) = 34 enabled.
    assert summary["enabled"] == 35  # +1: local-language business press (Phase 30B)
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


# ===========================================================================
# Task 2 — discovery council cites macro references (as run facts R#)
# ===========================================================================


def _discovery_cfg(macro: bool = True, max_items: int = 3) -> Settings:
    return Settings(
        llm_council_enabled=True,
        llm_discovery_council_enabled=True,
        llm_provider_council="fake",
        source_macro_enabled=macro,
        source_macro_max_items=max_items,
    )


def _macro_run(theme: str = "inflation", region: str | None = "North America") -> dict[str, Any]:
    return {
        "run_id": "macro-run",
        "mode": "thesis",
        "status": "completed",
        "thesis_text": f"{theme} exposed producers",
        "parsed_thesis": {"theme": theme, "region": region},
        "config": {"region": region},
        "provider": "free_real",
        "lookback_days": 90,
        "universe_count": 3,
        "candidate_count": 1,
        "error_count": 0,
        "warnings": [],
    }


def _macro_cands() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "cand-1",
            "ticker": "XYZ",
            "exchange": "US",
            "company_name": "XYZ Corp",
            "country": "United States",
            "sector": "Basic Materials",
            "data_coverage": {},
        }
    ]


def _spy_builder():
    """A side_effect wrapper capturing the pack + macro_evidence kwarg."""
    captured: dict[str, Any] = {}
    real = discovery_council_mod.build_discovery_evidence_pack

    def _spy(**kwargs: Any):
        pack = real(**kwargs)
        captured["pack"] = pack
        captured["macro_evidence"] = kwargs.get("macro_evidence")
        return pack

    return captured, _spy


def test_run_theme_region_extraction():
    theme, region = _run_theme_region(_macro_run("copper", "North America"))
    assert theme == "copper"
    assert region == "North America"
    # A plain ticker run with no parsed thesis has no theme (macro stays quiet).
    theme2, region2 = _run_theme_region({"mode": "ticker", "parsed_thesis": None})
    assert theme2 is None
    assert region2 is None


def test_discovery_pack_appends_citeable_macro_run_facts():
    cfg = _discovery_cfg()
    run, cands = _macro_run("inflation"), _macro_cands()
    macro = asyncio.run(collect_theme_macro_evidence("inflation", "North America", cfg))
    macro_facts = _macro_discovery_facts(macro)
    assert macro_facts  # inflation is a macro-relevant theme

    pack = build_discovery_evidence_pack(
        run=run,
        candidates=cands,
        macro_evidence=macro_facts,
        extra_known_gaps=macro.gap_messages(),
    )
    macro_rf = [f for f in pack.run_facts if f.label == "macro_context"]
    assert macro_rf
    ids = pack.evidence_ids()
    for f in macro_rf:
        # Every macro reference is a citeable R# run fact.
        assert re.fullmatch(r"R\d+", f.id)
        assert f.id in ids
        # Reference-only + recommendation-free: the detail states figures are not
        # fetched/fabricated and carries no forbidden rating / valuation vocab.
        assert "no figures" in (f.detail or "").lower()
        assert safety_terms.scan_text(f.detail or "") == []
    # Honest "figures not fetched" gaps are threaded into known_gaps.
    assert any("not fetched at report time" in g.lower() for g in pack.known_gaps)


def test_discovery_pack_byte_identical_without_macro():
    run, cands = _macro_run("inflation"), _macro_cands()
    base = build_discovery_evidence_pack(run=run, candidates=cands)
    with_none = build_discovery_evidence_pack(
        run=run, candidates=cands, macro_evidence=None
    )
    assert base.model_dump() == with_none.model_dump()
    assert not any(f.label == "macro_context" for f in base.run_facts)


def test_maybe_run_discovery_council_threads_macro_when_enabled():
    run, cands = _macro_run("inflation"), _macro_cands()
    captured, spy = _spy_builder()
    with patch.object(
        discovery_council_mod, "build_discovery_evidence_pack", side_effect=spy
    ):
        result = asyncio.run(
            maybe_run_discovery_council(
                run=run,
                candidates=cands,
                cfg=_discovery_cfg(macro=True),
                client=FakeDiscoveryLLMClient(),
            )
        )
    assert result.llm_used is True
    assert captured["macro_evidence"]  # non-empty macro references were passed
    pack = captured["pack"]
    macro_rf = [f for f in pack.run_facts if f.label == "macro_context"]
    assert macro_rf
    ids = pack.evidence_ids()
    assert all(f.id in ids for f in macro_rf)  # citeable
    assert any("not fetched at report time" in g.lower() for g in pack.known_gaps)


def test_maybe_run_discovery_council_dark_when_macro_disabled():
    run, cands = _macro_run("inflation"), _macro_cands()
    captured, spy = _spy_builder()
    with patch.object(
        discovery_council_mod, "build_discovery_evidence_pack", side_effect=spy
    ):
        asyncio.run(
            maybe_run_discovery_council(
                run=run,
                candidates=cands,
                cfg=_discovery_cfg(macro=False),
                client=FakeDiscoveryLLMClient(),
            )
        )
    # No macro references passed and no macro run facts in the pack.
    assert captured["macro_evidence"] is None
    assert not [f for f in captured["pack"].run_facts if f.label == "macro_context"]


# ===========================================================================
# Task 2 — company report optional macro-context block
# ===========================================================================


def _copper_snapshot() -> dict[str, Any]:
    """A commodity producer: its sector/industry make the Pink Sheet relevant."""
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "company_identity": {
            "ticker": "COPX",
            "legal_name": "Copper Mines PLC",
            "exchange": "LSE",
            "country_domicile": "United Kingdom",
        },
        "profile": {"sector": "Basic Materials", "industry": "Copper Mining"},
    }


@pytest.fixture
def enable_council(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    yield


@pytest.fixture
def enable_council_and_macro(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    monkeypatch.setattr(config.settings, "source_macro_enabled", True)
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


async def test_company_report_renders_macro_block_when_enabled(
    mock_db, enable_council_and_macro
) -> None:
    resp = await _generate(mock_db, _copper_snapshot())
    # Invariants hold on the macro-on path.
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    block = content.get("industry_macro_context")
    assert block is not None, "macro-on report must carry an industry_macro_context block"
    assert block["value"], "expected at least one macro reference (Pink Sheet for copper)"
    # Honest CONTEXT note — not company-specific evidence, not a catalyst.
    note = block["note"].lower()
    assert "not company-specific evidence" in note
    assert "never a direct company catalyst" in note
    # Reference-only: a URL + indicator text but NO figures / index levels / dates
    # in the indicator reference itself (the honest gap may name the "Phase 29C"
    # follow-up, which is not a macro figure).
    for item in block["value"]:
        assert item["url"]
        assert item["indicators_reference"]
        assert not _DIGIT_RE.search(item["indicators_reference"])
    # No forbidden rating / valuation vocab anywhere in the block.
    assert safety_terms.scan_value(block) == []

    # The compact council metadata path also carries the macro context.
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["macro_context"]


async def test_company_report_no_macro_block_when_disabled(
    mock_db, enable_council
) -> None:
    """Council on but macro flag off → block absent, report unchanged + safe."""
    resp = await _generate(mock_db, _copper_snapshot())
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    assert "industry_macro_context" not in content
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["macro_context"] == []
