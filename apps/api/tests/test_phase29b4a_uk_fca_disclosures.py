"""
Phase 29B.4A — UK FCA / RNS regulated-disclosure reference connector.

Covers the promotion of the former ``uk_fca_nsm`` scaffold into a dedicated
``UkFcaNsmConnector`` that emits a bounded T2 regulator-transport SOURCE
REFERENCE (never a fabricated filing) for verified UK-regulated issuers, the
tightened UK issuer -> regulator mapping in ``_relevant_scaffold_ids``, and the
registry / health honesty guarantees. Everything runs offline — this task adds
no live fetch (that is Task 2).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.sources.company_evidence import (
    _relevant_scaffold_ids,
    collect_company_source_evidence,
    regulator_connector_for,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.uk_fca_nsm import (
    FCA_NSM_URL,
    UkFcaNsmConnector,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import build_registry
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    ConnectorStatus,
    SourceStatus,
)

client = TestClient(app)

_FORBIDDEN = (
    "buy",
    "sell",
    "hold",
    "watch",
    "price target",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
)


def _has_forbidden(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in _FORBIDDEN)


def _enabled_cfg(**over: Any) -> Settings:
    base = dict(source_connector_enabled=True, source_connector_max_items_per_source=5)
    base.update(over)
    return Settings(**base)


def _q() -> QueryContext:
    return QueryContext(max_items=5)


# ---------------------------------------------------------------------------
# 1–4  Connector: T2 regulator-transport source reference + honest content gap
# ---------------------------------------------------------------------------


def test_1_brby_and_ba_emit_t2_source_reference():
    for ticker in ("BRBY", "BA"):
        conn = UkFcaNsmConnector()
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker=ticker, exchange="LSE"), _q())
        )
        assert len(res.evidence_items) == 1, ticker
        item = res.evidence_items[0]
        # Correct tier: a T2 regulator-transport reference (not T1 content).
        assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
        assert item.content_source_tier == T2_REGULATOR_OR_GOV
        assert item.source_type == "uk_fca_nsm_reference"
        assert item.data_quality == "metadata_only"
        # Issuer identity is carried; the reference URL is the FCA NSM venue.
        assert item.source_name and item.url == FCA_NSM_URL
        # Honest content gap: the T1 filing content is not fetched at report time.
        assert any(
            g.gap_type == GapType.primary_filing_unavailable for g in res.source_gaps
        )


def test_2_reference_has_no_fabricated_filing_or_rns_number():
    conn = UkFcaNsmConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="BRBY", exchange="LSE"), _q())
    )
    item = res.evidence_items[0]
    # A source reference, not a specific notice: no fabricated filing date.
    assert item.date is None
    excerpt = item.excerpt.lower()
    # It explicitly states nothing is fetched or fabricated (RNS number is only
    # mentioned inside that negation).
    assert "no individual filing" in excerpt
    assert "fabricated" in excerpt


def test_3_ba_resolves_to_bae_never_boeing_or_sec():
    conn = UkFcaNsmConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="BA", exchange="LSE"), _q())
    )
    item = res.evidence_items[0]
    assert "bae" in (item.source_name or "").lower()
    blob = json.dumps(res.model_dump(mode="json")).lower()
    assert "boeing" not in blob
    assert "sec.gov" not in blob


def test_4_non_uk_or_unresolvable_issuer_returns_only_honest_gap():
    conn = UkFcaNsmConnector()
    # A UK venue but an unregistered/unresolvable issuer.
    unresolved = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="ZZZZ", exchange="LSE"), _q())
    )
    assert unresolved.evidence_items == []
    assert any(
        g.gap_type == GapType.source_not_eligible for g in unresolved.source_gaps
    )
    # A verified non-UK issuer (Swiss) is not eligible for a UK reference.
    swiss = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="CFR", exchange="SW"), _q())
    )
    assert swiss.evidence_items == []
    assert any(g.gap_type == GapType.source_not_eligible for g in swiss.source_gaps)


# ---------------------------------------------------------------------------
# 5–7  Exchange -> regulator mapping + tightened _relevant_scaffold_ids
# ---------------------------------------------------------------------------


def test_5_exchange_regulator_mapping_uk_only():
    assert regulator_connector_for("LSE") == "uk_fca_nsm"
    assert regulator_connector_for(None, "United Kingdom") == "uk_fca_nsm"
    # Euronext Paris/Amsterdam map to the Euronext connector (Phase 29B.4B),
    # never to the UK connector.
    assert regulator_connector_for("PA", "France") == "euronext_regulated_info"
    # German / Swiss venues now map to their own dedicated connectors (29B.4C),
    # never to the UK connector.
    assert regulator_connector_for("XETRA", "Germany") == "deutsche_boerse"
    assert regulator_connector_for("SW", "Switzerland") == "six_swiss"


def test_6_lse_issuer_maps_to_uk_fca_nsm_only():
    reg = build_registry()
    ids = _relevant_scaffold_ids(
        reg, CompanyContext(ticker="BRBY", exchange="LSE"), None
    )
    assert ids == ["uk_fca_nsm"]
    # The other Europe scaffolds are dropped for a UK issuer.
    for other in ("euronext_regulated_info", "deutsche_boerse", "nordic_disclosures"):
        assert other not in ids


def test_7_de_fr_issuer_mapping_unchanged():
    reg = build_registry()
    # A German issuer maps to its own dedicated connector (Phase 29B.4C promoted
    # deutsche_boerse), never to the UK connector — the UK tightening is unchanged.
    de = _relevant_scaffold_ids(reg, CompanyContext(ticker="SAP", exchange="XETRA"), None)
    assert "deutsche_boerse" in de
    assert "euronext_regulated_info" not in de
    assert de != ["uk_fca_nsm"] and "uk_fca_nsm" not in de
    # A French Euronext issuer now maps to the Euronext connector specifically
    # (Phase 29B.4B tightening), never to the UK connector.
    fr = _relevant_scaffold_ids(reg, CompanyContext(ticker="MC", exchange="PA"), None)
    assert fr == ["euronext_regulated_info"] and "uk_fca_nsm" not in fr


# ---------------------------------------------------------------------------
# 8–10  Registry / health honesty; connector status; safety vocabulary
# ---------------------------------------------------------------------------


def test_8_registry_promotes_uk_fca_nsm_to_enabled_reference_connector():
    reg = build_registry()
    src = reg.get("uk_fca_nsm")
    assert src is not None
    assert src.status == SourceStatus.enabled
    assert src.tier == T2_REGULATOR_OR_GOV
    conn = reg.connectors()["uk_fca_nsm"]
    assert isinstance(conn, UkFcaNsmConnector)
    assert conn.status == ConnectorStatus.enabled
    # It is no longer grouped with the honest scaffolds.
    assert "uk_fca_nsm" not in {s.source_id for s in reg.scaffolded_sources()}


def test_9_registry_and_health_secret_free_and_honest_about_content():
    for path in ("/api/v1/sources/registry", "/api/v1/sources/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        blob = json.dumps(resp.json()).lower()
        for needle in ("api_token", "bearer ", "authorization", "password", "postgresql://"):
            assert needle not in blob
    # The registry reliability note is honest: content is not fetched at report time.
    reg = build_registry()
    note = (reg.get("uk_fca_nsm").reliability_note or "").lower()
    assert "content is not fetched" in note or "not fetched at report time" in note


def test_10_connector_output_has_no_forbidden_vocab():
    conn = UkFcaNsmConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="BA", exchange="LSE"), _q())
    )
    for item in res.evidence_items:
        assert not _has_forbidden(
            f"{item.title} {item.excerpt} {' '.join(item.warnings)}"
        )
    for g in res.source_gaps:
        assert not _has_forbidden(g.message)


# ---------------------------------------------------------------------------
# 11  Company IR still works AND the uk_fca_nsm reference is added (BA.LSE)
# ---------------------------------------------------------------------------


def test_11_ba_lse_collects_company_ir_and_uk_fca_nsm_reference():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="BA", exchange="LSE"),
            cfg=_enabled_cfg(),
        )
    )
    source_ids = {it.source_id for it in collected.evidence_items}
    # Company IR path still works (BAE's own metadata) ...
    assert "company_ir" in source_ids
    # ... and the new UK FCA NSM regulator-transport reference is present.
    assert "uk_fca_nsm" in source_ids
    # Every evidence item is BAE / UK material — never a US SEC / Boeing filing.
    blob = " ".join(
        f"{it.source_name} {it.title} {it.url}" for it in collected.evidence_items
    ).lower()
    assert "boeing" not in blob and "sec.gov" not in blob
    # SEC is honestly ineligible for this non-US issuer.
    assert any(
        g.gap_type == GapType.source_not_eligible for g in collected.source_gaps
    )
    # The uk_fca_nsm item carries the T2 regulator-transport tier.
    nsm = next(it for it in collected.evidence_items if it.source_id == "uk_fca_nsm")
    assert nsm.content_source_tier == T2_REGULATOR_OR_GOV


def test_12_us_issuer_gets_no_uk_regulator_reference():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US"),
            cfg=_enabled_cfg(),
        )
    )
    assert all(it.source_id != "uk_fca_nsm" for it in collected.evidence_items)
