"""
Phase 29B.4B — Euronext regulated-disclosure reference connector.

Mirrors Phase 29B.4A (UK FCA NSM). Covers the promotion of the former
``euronext_regulated_info`` scaffold into a dedicated ``EuronextRegulatedConnector``
that emits a bounded T2 regulator-transport SOURCE REFERENCE (never a fabricated
filing) for verified Euronext Paris (FR) / Amsterdam (NL) issuers, the French
``requires_translation`` signal, the tightened FR/NL issuer -> regulator mapping
in ``_relevant_scaffold_ids``, and the registry / health honesty guarantees.
Everything runs offline — this task adds no live fetch (that is Task 2).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services import safety_terms
from app.services.sources.company_evidence import (
    _relevant_scaffold_ids,
    collect_company_source_evidence,
    regulator_connector_for,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.euronext_regulated_info import (
    EURONEXT_REGULATED_INFO_URL,
    EuronextRegulatedConnector,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import build_registry
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    ConnectorStatus,
    SourceStatus,
)

client = TestClient(app)

# French-language issuers on Euronext Paris; Dutch issuer on Euronext Amsterdam.
_FR_ISSUERS = (("MC", "PA"), ("RMS", "PA"), ("KER", "PA"))
_NL_ISSUER = ("ASML", "AS")


def _q() -> QueryContext:
    return QueryContext(max_items=5)


def _enabled_cfg(**over: Any) -> Settings:
    base = dict(source_connector_enabled=True, source_connector_max_items_per_source=5)
    base.update(over)
    return Settings(**base)


def _safe(*texts: str | None) -> bool:
    """True when the concatenated text trips no forbidden-output rule."""
    blob = " ".join(t for t in texts if t)
    return safety_terms.scan_text(blob) == []


# ---------------------------------------------------------------------------
# 1–4  Connector: T2 regulator-transport source reference + honest content gap
# ---------------------------------------------------------------------------


def test_1_euronext_issuers_emit_t2_source_reference():
    for ticker, exchange in (*_FR_ISSUERS, _NL_ISSUER):
        conn = EuronextRegulatedConnector()
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker=ticker, exchange=exchange), _q())
        )
        assert len(res.evidence_items) == 1, ticker
        item = res.evidence_items[0]
        # Correct tier: a T2 regulator-transport reference (not T1 content).
        assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
        assert item.content_source_tier == T2_REGULATOR_OR_GOV
        assert item.source_type == "euronext_regulated_info_reference"
        assert item.data_quality == "metadata_only"
        # Issuer identity is carried; the reference URL is the fixed Euronext venue.
        assert item.source_name and item.url == EURONEXT_REGULATED_INFO_URL
        # A fixed venue landing page — no query string / per-filing path.
        assert "?" not in (item.url or "")
        # Honest content gap: the T1 filing content is not fetched at report time.
        assert any(
            g.gap_type == GapType.primary_filing_unavailable for g in res.source_gaps
        )


def test_2_fr_issuers_require_translation_nl_issuer_does_not():
    # French issuers: item is marked requires_translation AND a translation gap.
    for ticker, exchange in _FR_ISSUERS:
        conn = EuronextRegulatedConnector()
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker=ticker, exchange=exchange), _q())
        )
        item = res.evidence_items[0]
        assert item.requires_translation is True, ticker
        assert any(
            g.gap_type == GapType.translation_required for g in res.source_gaps
        ), ticker
    # Dutch issuer (English disclosures): no translation signal at all.
    conn = EuronextRegulatedConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="ASML", exchange="AS"), _q())
    )
    item = res.evidence_items[0]
    assert item.requires_translation is False
    assert not any(g.gap_type == GapType.translation_required for g in res.source_gaps)


def test_3_reference_has_no_fabricated_filing_or_notice_number():
    conn = EuronextRegulatedConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="MC", exchange="PA"), _q())
    )
    item = res.evidence_items[0]
    # A source reference, not a specific notice: no fabricated filing date.
    assert item.date is None
    excerpt = item.excerpt.lower()
    # It explicitly states nothing is fetched or fabricated.
    assert "no individual filing" in excerpt
    assert "fabricated" in excerpt
    # The reference cites the home regulator (AMF for France).
    assert "amf" in json.dumps(res.model_dump(mode="json")).lower()


def test_4_non_euronext_or_unresolvable_issuer_returns_only_honest_gap():
    conn = EuronextRegulatedConnector()
    # A Euronext venue but an unregistered/unresolvable issuer.
    unresolved = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="ZZZZ", exchange="PA"), _q())
    )
    assert unresolved.evidence_items == []
    assert any(
        g.gap_type == GapType.source_not_eligible for g in unresolved.source_gaps
    )
    # A verified non-Euronext issuer (Swiss) is not eligible for a Euronext ref.
    swiss = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="CFR", exchange="SW"), _q())
    )
    assert swiss.evidence_items == []
    assert any(g.gap_type == GapType.source_not_eligible for g in swiss.source_gaps)
    # A verified UK issuer is likewise not eligible for a Euronext reference.
    uk = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="BRBY", exchange="LSE"), _q())
    )
    assert uk.evidence_items == []
    assert any(g.gap_type == GapType.source_not_eligible for g in uk.source_gaps)


# ---------------------------------------------------------------------------
# 5–7  Exchange -> regulator mapping + tightened _relevant_scaffold_ids
# ---------------------------------------------------------------------------


def test_5_exchange_regulator_mapping_euronext():
    # Both Euronext venues + both countries resolve to the Euronext connector.
    assert regulator_connector_for("PA") == "euronext_regulated_info"
    assert regulator_connector_for("AS") == "euronext_regulated_info"
    assert regulator_connector_for(None, "France") == "euronext_regulated_info"
    assert regulator_connector_for(None, "Netherlands") == "euronext_regulated_info"
    # UK behaviour unchanged (Phase 29B.4A); Germany now maps to its own dedicated
    # connector (Phase 29B.4C).
    assert regulator_connector_for("LSE") == "uk_fca_nsm"
    assert regulator_connector_for("XETRA", "Germany") == "deutsche_boerse"


def test_6_fr_nl_issuer_maps_to_euronext_only():
    reg = build_registry()
    for ticker, exchange in (*_FR_ISSUERS, _NL_ISSUER):
        ids = _relevant_scaffold_ids(
            reg, CompanyContext(ticker=ticker, exchange=exchange), None
        )
        assert ids == ["euronext_regulated_info"], (ticker, ids)
        # The other Europe scaffolds + the UK connector are dropped.
        for other in ("deutsche_boerse", "nordic_disclosures", "uk_fca_nsm"):
            assert other not in ids


def test_7_uk_and_de_mapping_unchanged():
    reg = build_registry()
    # A UK issuer still maps to uk_fca_nsm only (Phase 29B.4A unchanged).
    uk = _relevant_scaffold_ids(reg, CompanyContext(ticker="BRBY", exchange="LSE"), None)
    assert uk == ["uk_fca_nsm"]
    assert "euronext_regulated_info" not in uk
    # A German issuer maps to its own dedicated connector (Phase 29B.4C);
    # euronext / uk are not among the resolved ids.
    de = _relevant_scaffold_ids(reg, CompanyContext(ticker="SAP", exchange="XETRA"), None)
    assert de == ["deutsche_boerse"]
    assert "euronext_regulated_info" not in de and "uk_fca_nsm" not in de


# ---------------------------------------------------------------------------
# 8–10  Registry / health honesty; connector status; safety vocabulary
# ---------------------------------------------------------------------------


def test_8_registry_promotes_euronext_to_enabled_reference_connector():
    reg = build_registry()
    src = reg.get("euronext_regulated_info")
    assert src is not None
    assert src.status == SourceStatus.enabled
    assert src.tier == T2_REGULATOR_OR_GOV
    conn = reg.connectors()["euronext_regulated_info"]
    assert isinstance(conn, EuronextRegulatedConnector)
    assert conn.status == ConnectorStatus.enabled
    # It is no longer grouped with the honest scaffolds.
    assert "euronext_regulated_info" not in {
        s.source_id for s in reg.scaffolded_sources()
    }
    # Registry reports 21 enabled / 2 scaffolded sources: 11 regulator-layer
    # enabled sources (29B.4C promoted deutsche_boerse + nordic_disclosures and
    # added six_swiss) plus 5 reference-only macro sources (Phase 29C.1) plus 5
    # reference-only commodity / energy sources (Phase 29C.2) plus 5 reference-only
    # policy / government sources (Phase 29C.3).
    summary = reg.summary()
    assert summary["enabled"] == 26
    assert summary["scaffolded"] == 2


def test_9_registry_and_health_secret_free_and_honest_about_content():
    for path in ("/api/v1/sources/registry", "/api/v1/sources/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        blob = json.dumps(resp.json()).lower()
        for needle in ("api_token", "bearer ", "authorization", "password", "postgresql://"):
            assert needle not in blob
    # The registry reliability note is honest: content is not fetched at report time.
    reg = build_registry()
    note = (reg.get("euronext_regulated_info").reliability_note or "").lower()
    assert "content is not fetched at report time" in note
    assert "translation" in note


def test_10_connector_output_has_no_forbidden_vocab():
    # Include the Dutch issuer whose name ("ASML Holding N.V.") contains the
    # substring "hold" — the real gate is word-bounded ALL-CAPS, so it passes.
    for ticker, exchange in (*_FR_ISSUERS, _NL_ISSUER):
        conn = EuronextRegulatedConnector()
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker=ticker, exchange=exchange), _q())
        )
        for item in res.evidence_items:
            assert _safe(
                item.title, item.excerpt, item.source_name, " ".join(item.warnings)
            ), ticker
        for g in res.source_gaps:
            assert _safe(g.message), ticker


# ---------------------------------------------------------------------------
# 11–12  Collection integration: company IR + Euronext reference; US exclusion
# ---------------------------------------------------------------------------


def test_11_mc_pa_collects_company_ir_and_euronext_reference():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="MC", exchange="PA"),
            cfg=_enabled_cfg(),
        )
    )
    source_ids = {it.source_id for it in collected.evidence_items}
    # Company IR path still works (LVMH's own metadata) ...
    assert "company_ir" in source_ids
    # ... and the new Euronext regulator-transport reference is present.
    assert "euronext_regulated_info" in source_ids
    # SEC is honestly ineligible for this non-US issuer — never a US SEC filing.
    assert any(
        g.gap_type == GapType.source_not_eligible for g in collected.source_gaps
    )
    blob = json.dumps(collected.model_dump(mode="json")).lower()
    assert "sec.gov" not in blob
    # The Euronext item carries the T2 regulator-transport tier + FR translation.
    euro = next(
        it for it in collected.evidence_items if it.source_id == "euronext_regulated_info"
    )
    assert euro.content_source_tier == T2_REGULATOR_OR_GOV
    assert euro.requires_translation is True


def test_12_us_issuer_gets_no_euronext_reference():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US"),
            cfg=_enabled_cfg(),
        )
    )
    assert all(
        it.source_id != "euronext_regulated_info" for it in collected.evidence_items
    )
