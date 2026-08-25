"""
Phase 30B — allowlisted local-language business-press references.

Covers the new reference-only ``local_language_press`` connector and its wiring:

  * For a verified non-US issuer whose home market is French / German / Italian /
    Danish (FR / DE / IT / DA), the connector emits ONE bounded T4 quality-media
    SOURCE REFERENCE with a GENUINE local-language descriptive excerpt (never a
    fabricated news story), ``requires_translation=True``, the correct
    ``original_language``, an honest ``translation_required`` gap and a
    content-not-fetched gap.
  * A US / SEC-eligible / Swiss / unregistered issuer gets NO reference — only an
    honest ``source_not_eligible`` gap (or nothing, in the collector).
  * ``collect_company_source_evidence`` surfaces the reference for FR / DE / IT /
    DA issuers alongside the existing regulator/company-IR items, and never for a
    US issuer.
  * The Phase 30A translation layer consumes it: with ``source_translation_enabled``
    on, ``_collect_translated_excerpts`` / ``maybe_run_council`` produce a bounded,
    machine-assisted English rendering (original preserved + needs human review);
    with the flag off the reference still appears but is not translated.
  * The registry promotes the former ``local_language_business_press`` planned row
    to enabled, keeps the payload secret-free, and reports the new counts.
  * No fabricated news (no digit / headline / quote / date) and no forbidden
    recommendation / valuation vocabulary anywhere.

Everything runs offline — this connector makes no network call at report time.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services import safety_terms
from app.services.llm import council as council_mod
from app.services.llm.council import _collect_translated_excerpts, maybe_run_council
from app.services.llm.fake_client import FakeLLMClient
from app.services.sources.company_evidence import (
    LOCAL_LANGUAGE_PRESS_ID,
    LOCAL_LANGUAGE_REFERENCE_IDS,
    CompanySourceEvidence,
    collect_company_source_evidence,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.local_language_press import (
    LOCAL_LANGUAGE_PRESS_SOURCES,
    LocalLanguagePressConnector,
    local_language_press_source_for,
)
from app.services.sources.gaps import GapType
from app.services.sources.language import detect_language
from app.services.sources.registry import build_registry
from app.services.sources.taxonomy import (
    T4_QUALITY_MEDIA,
    ConnectorStatus,
    SourceStatus,
    tier_rank,
)
from app.services.sources.translation import (
    FAKE_TRANSLATION_MARKER,
    MACHINE_TRANSLATION_WARNING,
)

client = TestClient(app)

# Verified issuer -> (expected language code, expected original_language label).
_ELIGIBLE: dict[tuple[str, str], tuple[str, str]] = {
    ("MC", "PA"): ("fr", "French"),
    ("SAP", "DE"): ("de", "German"),
    ("MONC", "MI"): ("it", "Italian"),
    ("PNDORA", "CO"): ("da", "Danish"),
}


def _q() -> QueryContext:
    return QueryContext(max_items=5)


def _enabled_cfg(**over: object) -> Settings:
    base = dict(source_connector_enabled=True, source_connector_max_items_per_source=5)
    base.update(over)
    return Settings(**base)


def _council_cfg(**over: object) -> Settings:
    base = dict(
        llm_council_enabled=True,
        llm_provider_council="fake",
        source_connector_enabled=True,
    )
    base.update(over)
    return Settings(**base)


def _safe(*texts: str | None) -> bool:
    blob = " ".join(t for t in texts if t)
    return safety_terms.scan_text(blob) == []


def _reference(ticker: str, exchange: str):
    res = asyncio.run(
        LocalLanguagePressConnector().fetch_filings(
            CompanyContext(ticker=ticker, exchange=exchange), _q()
        )
    )
    return res


def _fr_snapshot() -> dict:
    return {
        "is_mock": False,
        "company_identity": {
            "ticker": "MC",
            "legal_name": "LVMH Moët Hennessy Louis Vuitton SE",
            "exchange": "PA",
            "country_domicile": "France",
        },
        "profile": {"sector": "Consumer Cyclical", "industry": "Luxury Goods"},
    }


def _fake_collect(items: list):
    async def _collect(**_kwargs: object) -> CompanySourceEvidence:
        return CompanySourceEvidence(evidence_items=list(items))

    return _collect


# ---------------------------------------------------------------------------
# 1–2  The connector: a bounded T4 local-language reference, no fabrication
# ---------------------------------------------------------------------------


def test_1_eligible_issuer_emits_t4_local_language_reference():
    for (ticker, exchange), (code, name) in _ELIGIBLE.items():
        res = _reference(ticker, exchange)
        assert len(res.evidence_items) == 1, ticker
        item = res.evidence_items[0]
        # Deliberately a T4 (quality-media) reference, not a regulator/filing.
        assert item.content_source_tier == T4_QUALITY_MEDIA, ticker
        assert item.provider_transport_tier == T4_QUALITY_MEDIA, ticker
        assert item.source_type == "news_article", ticker
        assert item.data_quality == "metadata_only", ticker
        assert item.confidence == "low", ticker
        # Fixed public HTTPS landing page, no query / token.
        assert item.url and item.url.startswith("https://"), ticker
        assert "?" not in item.url, ticker
        # Non-English: flagged for the Phase 30A translation layer.
        assert item.requires_translation is True, ticker
        assert item.original_language == name, ticker
        # The excerpt is GENUINELY in the local language.
        assert detect_language(item.excerpt) == code, (ticker, item.excerpt)
        # Honest gaps: content-not-fetched + translation required.
        assert any(g.gap_type == GapType.translation_required for g in res.source_gaps), ticker
        assert any(g.gap_type == GapType.data_not_sourced for g in res.source_gaps), ticker


def test_2_no_fabricated_news_headline_quote_or_figure():
    for (ticker, exchange), _ in _ELIGIBLE.items():
        res = _reference(ticker, exchange)
        item = res.evidence_items[0]
        # No fabricated figure / date: no digit anywhere in the local-language excerpt.
        assert not any(ch.isdigit() for ch in item.excerpt), (ticker, item.excerpt)
        # A source reference, not a specific article — no fabricated notice date.
        assert item.date is None, ticker
        # No quotation marks that would imply a fabricated quote / headline.
        for mark in ('"', "“", "”", "«", "»"):
            assert mark not in item.excerpt, (ticker, mark)
        # Honesty is stated on the reference (in a warning; the excerpt is bounded).
        joined = " ".join(item.warnings).lower()
        assert "no article content is fetched" in joined, ticker
        assert "fabricated" in joined, ticker


def test_3_non_eligible_issuers_return_only_honest_gap():
    # US / SEC-eligible, Swiss (publishes English), and an unregistered issuer.
    for ticker, exchange in (("AAPL", "US"), ("CFR", "SW"), ("ZZZZ", "PA")):
        res = _reference(ticker, exchange)
        assert res.evidence_items == [], ticker
        assert any(
            g.gap_type == GapType.source_not_eligible for g in res.source_gaps
        ), ticker


def test_4_resolver_requires_verified_eligible_issuer():
    assert (
        local_language_press_source_for(CompanyContext(ticker="MC", exchange="PA"))
    ).language_code == "fr"
    assert (
        local_language_press_source_for(CompanyContext(ticker="SAP", exchange="DE"))
    ).language_code == "de"
    # US, Swiss (not FR/DE/IT/DA) and unregistered issuers resolve to None.
    assert local_language_press_source_for(CompanyContext(ticker="AAPL", exchange="US")) is None
    assert local_language_press_source_for(CompanyContext(ticker="CFR", exchange="SW")) is None
    assert local_language_press_source_for(CompanyContext(ticker="ZZZZ", exchange="PA")) is None


# ---------------------------------------------------------------------------
# 5–7  Registry promotion + secret-free honesty + updated counts
# ---------------------------------------------------------------------------


def test_5_registry_promotes_local_language_press_to_enabled():
    reg = build_registry()
    src = reg.get("local_language_business_press")
    assert src is not None
    assert src.status == SourceStatus.enabled
    assert src.tier == T4_QUALITY_MEDIA
    conn = reg.connectors()["local_language_business_press"]
    assert isinstance(conn, LocalLanguagePressConnector)
    assert conn.status == ConnectorStatus.enabled
    # No longer planned — only OpenBB remains planned.
    planned_ids = {s.source_id for s in reg.planned_sources()}
    assert planned_ids == {"openbb"}
    assert "local_language_business_press" not in planned_ids
    # The regulator-layer promotions (11) + 29C/29D reference sources (23) + this
    # local-language press promotion => 35 enabled; OpenBB is the last planned row.
    summary = reg.summary()
    assert summary["enabled"] == 36  # +1: Italian regulated disclosures (readiness PR-E)
    assert summary["planned"] == 1
    assert summary["scaffolded"] == 2
    assert summary["total"] == 39
    assert summary["total"] == len(reg.all_sources())


def test_6_registry_and_health_secret_free_and_honest():
    for path in ("/api/v1/sources/registry", "/api/v1/sources/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        blob = json.dumps(resp.json()).lower()
        for needle in ("api_token", "bearer ", "authorization", "password", "postgresql://"):
            assert needle not in blob
    note = (
        build_registry().get("local_language_business_press").reliability_note or ""
    ).lower()
    assert "not fetched" in note
    assert "translation" in note
    assert "fabricat" in note  # honest: no news is fabricated


def test_7_local_language_reference_ids_constant():
    assert LOCAL_LANGUAGE_PRESS_ID == "local_language_business_press"
    assert LOCAL_LANGUAGE_REFERENCE_IDS == frozenset({LOCAL_LANGUAGE_PRESS_ID})


# ---------------------------------------------------------------------------
# 8–9  Collection integration: eligible issuers get it; US issuers do not
# ---------------------------------------------------------------------------


def test_8_collection_includes_local_language_reference_for_eligible():
    for (ticker, exchange), (code, name) in _ELIGIBLE.items():
        col = asyncio.run(
            collect_company_source_evidence(
                company=CompanyContext(ticker=ticker, exchange=exchange),
                cfg=_enabled_cfg(),
            )
        )
        press = [it for it in col.evidence_items if it.source_id == LOCAL_LANGUAGE_PRESS_ID]
        assert len(press) == 1, ticker
        assert press[0].requires_translation is True, ticker
        assert press[0].original_language == name, ticker
        # Everything collected stays metadata-only (no fabricated filing content).
        assert all(it.data_quality == "metadata_only" for it in col.evidence_items), ticker
        # An honest translation gap is present in the collected gaps.
        assert any(
            g.gap_type == GapType.translation_required for g in col.source_gaps
        ), ticker


def test_9_collection_excludes_local_language_reference_for_us_and_swiss():
    for ticker, exchange in (("AAPL", "US"), ("CFR", "SW")):
        col = asyncio.run(
            collect_company_source_evidence(
                company=CompanyContext(ticker=ticker, exchange=exchange),
                cfg=_enabled_cfg(),
            )
        )
        assert all(
            it.source_id != LOCAL_LANGUAGE_PRESS_ID for it in col.evidence_items
        ), ticker


# ---------------------------------------------------------------------------
# 10–12  Phase 30A translation layer consumes the reference
# ---------------------------------------------------------------------------


def test_10_translation_layer_consumes_local_language_reference():
    item = _reference("MC", "PA").evidence_items[0]
    cfg = Settings(source_translation_enabled=True)  # fake provider by default
    out = asyncio.run(_collect_translated_excerpts([item], cfg))
    assert len(out) == 1
    entry = out[0]
    assert entry["original_language"] == "fr"
    assert entry["original_language_name"] == "French"
    # The ORIGINAL excerpt is preserved verbatim (citation of record).
    assert entry["original_excerpt"] == item.excerpt
    # A clearly-marked machine placeholder — never fabricated fluent English.
    assert entry["translated_excerpt"].startswith(FAKE_TRANSLATION_MARKER)
    assert entry["needs_human_review"] is True
    assert entry["warning"] == MACHINE_TRANSLATION_WARNING
    # Bounded per-excerpt + secret-free source URL preserved.
    assert len(entry["translated_excerpt"]) <= cfg.source_translation_max_chars
    assert entry["source_url"] and entry["source_url"].startswith("https://")


async def test_11_maybe_run_council_translates_local_language_reference(monkeypatch):
    res = await LocalLanguagePressConnector().fetch_filings(
        CompanyContext(ticker="MC", exchange="PA"), _q()
    )
    item = res.evidence_items[0]
    monkeypatch.setattr(
        council_mod, "collect_company_source_evidence", _fake_collect([item])
    )
    result = await maybe_run_council(
        report_content={"company_identity": {}},
        company_snapshot=_fr_snapshot(),
        cfg=_council_cfg(source_translation_enabled=True),
        client=FakeLLMClient(),
    )
    assert result.llm_used is True
    assert len(result.translated_excerpts) == 1
    entry = result.translated_excerpts[0]
    assert entry["original_language"] == "fr"
    assert entry["original_excerpt"] == item.excerpt
    assert entry["translated_excerpt"].startswith(FAKE_TRANSLATION_MARKER)
    assert entry["needs_human_review"] is True
    assert entry["warning"] == MACHINE_TRANSLATION_WARNING


async def test_12_maybe_run_council_dark_when_translation_off(monkeypatch):
    res = await LocalLanguagePressConnector().fetch_filings(
        CompanyContext(ticker="MC", exchange="PA"), _q()
    )
    item = res.evidence_items[0]
    monkeypatch.setattr(
        council_mod, "collect_company_source_evidence", _fake_collect([item])
    )
    result = await maybe_run_council(
        report_content={"company_identity": {}},
        company_snapshot=_fr_snapshot(),
        cfg=_council_cfg(source_translation_enabled=False),  # OFF
        client=FakeLLMClient(),
    )
    assert result.translated_excerpts == []
    # But the reference itself is still collected (flags visible), independent of
    # the translation flag — collection is gated by ``source_connector_enabled``.
    col = await collect_company_source_evidence(
        company=CompanyContext(ticker="MC", exchange="PA"), cfg=_enabled_cfg()
    )
    press = [it for it in col.evidence_items if it.source_id == LOCAL_LANGUAGE_PRESS_ID]
    assert len(press) == 1
    assert press[0].requires_translation is True


# ---------------------------------------------------------------------------
# 13  Source quality is honestly lowered + no forbidden vocabulary
# ---------------------------------------------------------------------------


def test_13_source_quality_lowered_and_no_forbidden_vocab():
    assert tier_rank(T4_QUALITY_MEDIA) >= 4  # T4 or weaker
    for (ticker, exchange), _ in _ELIGIBLE.items():
        res = _reference(ticker, exchange)
        item = res.evidence_items[0]
        # Deliberately weak evidence: T4 media, low confidence, needs review.
        assert item.tier_rank >= 4, ticker
        assert item.confidence == "low", ticker
        assert item.data_quality == "metadata_only", ticker
        assert any("human review" in w.lower() for w in item.warnings), ticker
        # No recommendation / valuation vocabulary anywhere on the item or gaps.
        assert _safe(
            item.title, item.excerpt, item.source_name, " ".join(item.warnings)
        ), ticker
        for g in res.source_gaps:
            assert _safe(g.message), ticker


def test_14_every_allowlisted_venue_is_https_no_query_and_local_language():
    for country, spec in LOCAL_LANGUAGE_PRESS_SOURCES.items():
        assert spec.venue_url.startswith("https://"), country
        assert "?" not in spec.venue_url, country
        assert detect_language(spec.excerpt) == spec.language_code, country
        # The descriptive excerpt fabricates no figure.
        assert not any(ch.isdigit() for ch in spec.excerpt), country
