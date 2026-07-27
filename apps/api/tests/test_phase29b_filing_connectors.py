"""
Phase 29B — Filing & Regulator Connector Batch 1.

Covers the connector status model, the SEC EDGAR + company-IR live-evidence
connectors, the SEDAR+/ASX/UK/EU scaffold connectors, the single-company
evidence-collection service, its integration into the evidence pack + council,
and the read-only evidence-preview API. Everything runs offline.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.llm.council import run_council
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.fake_client import FakeLLMClient
from app.services.sources.company_evidence import (
    collect_company_source_evidence,
    press_items_from_catalyst,
    sec_filings_from_catalyst,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import CompanyIrConnector
from app.services.sources.connectors.scaffolds import ScaffoldConnector
from app.services.sources.connectors.sec_edgar import SecEdgarConnector
from app.services.sources.gaps import GapType
from app.services.sources.registry import build_registry
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
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


# ---------------------------------------------------------------------------
# 1–4  Taxonomy / registry / status model
# ---------------------------------------------------------------------------


def test_connector_status_includes_scaffolded():
    values = {s.value for s in ConnectorStatus}
    assert {"enabled", "configured", "scaffolded", "planned", "disabled", "error"} <= values
    assert "scaffolded" in {s.value for s in SourceStatus}


def test_registry_sec_and_company_ir_are_live():
    reg = build_registry()
    sec = reg.get("sec_edgar")
    ir = reg.get("company_ir")
    assert sec is not None and sec.status == SourceStatus.enabled
    assert ir is not None and ir.status == SourceStatus.enabled
    # company_ir content is issuer primary material.
    assert ir.tier == T1_PRIMARY_COMPANY_SOURCE
    # Their connectors report as live evidence paths.
    assert reg.connectors()["sec_edgar"].is_live
    assert reg.connectors()["company_ir"].is_live


def test_regulator_connectors_are_scaffolded_honestly():
    reg = build_registry()
    scaffolded_ids = {s.source_id for s in reg.scaffolded_sources()}
    # uk_fca_nsm (29B.4A), euronext_regulated_info (29B.4B) and deutsche_boerse /
    # nordic_disclosures (29B.4C) were promoted to dedicated connectors; only
    # these two remain honest scaffolds.
    assert {"sedar_plus", "asx_announcements"} <= scaffolded_ids
    for promoted in (
        "uk_fca_nsm",
        "euronext_regulated_info",
        "deutsche_boerse",
        "nordic_disclosures",
        "six_swiss",
    ):
        assert promoted not in scaffolded_ids
    for sid in scaffolded_ids:
        conn = reg.connectors()[sid]
        assert conn.status == ConnectorStatus.scaffolded
        assert conn.is_scaffolded and not conn.is_live


def test_registry_and_health_endpoints_secret_free():
    for path in ("/api/v1/sources/registry", "/api/v1/sources/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        blob = json.dumps(resp.json()).lower()
        for needle in ("api_token", "bearer ", "authorization", "password", "postgresql://"):
            assert needle not in blob


# ---------------------------------------------------------------------------
# 5–7  SEC EDGAR connector
# ---------------------------------------------------------------------------


def _fake_filings_fetcher(items: list[dict]):
    async def _f(_c: CompanyContext, _q: QueryContext) -> list[dict]:
        return items

    return _f


def test_sec_connector_emits_t2_transport_t1_content():
    filings = [
        {
            "form_type": "10-K",
            "title": "Apple Inc. 10-K FY2024",
            "url": "https://www.sec.gov/Archives/edgar/x.htm?api_token=SECRET",
            "filed_date": "2024-11-01",
            "summary": "Annual report",
        }
    ]
    conn = SecEdgarConnector(filings_fetcher=_fake_filings_fetcher(filings))
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="AAPL", exchange="US"), QueryContext())
    )
    assert len(res.evidence_items) == 1
    item = res.evidence_items[0]
    assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
    assert item.content_source_tier == T1_PRIMARY_FILING
    assert item.source_type == "company_filing"
    # URL secret is stripped.
    assert "api_token" not in (item.url or "")
    # Metadata-only → full-text gap is present.
    assert any(g.gap_type == GapType.primary_filing_unavailable for g in res.source_gaps)


def test_sec_connector_not_run_for_non_us_exchange():
    conn = SecEdgarConnector(filings_fetcher=_fake_filings_fetcher([{"form_type": "X"}]))
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="UHR", exchange="SW"), QueryContext())
    )
    assert res.evidence_items == []
    assert any(g.gap_type == GapType.source_not_eligible for g in res.source_gaps)


def test_sec_connector_failure_returns_gap_not_crash():
    async def _boom(_c: CompanyContext, _q: QueryContext) -> list[dict]:
        raise RuntimeError("upstream 500 with ?api_token=SECRET in url")

    conn = SecEdgarConnector(filings_fetcher=_boom)
    res = asyncio.run(
        conn.call_safe(conn.fetch_filings, CompanyContext(ticker="AAPL", exchange="US"), QueryContext())
    )
    assert res.evidence_items == []
    assert res.source_gaps
    # The redacted message must not leak the token.
    assert "api_token" not in json.dumps(res.model_dump(mode="json")).lower()


# ---------------------------------------------------------------------------
# 8–10  Company IR connector
# ---------------------------------------------------------------------------


def _fake_press_fetcher(items: list[dict]):
    async def _f(_c: CompanyContext, _q: QueryContext) -> list[dict]:
        return items

    return _f


def test_company_ir_wraps_press_items_as_evidence():
    press = [
        {
            "headline": "Apple reports fourth quarter results",
            "url": "https://www.apple.com/newsroom/x/?utm_token=abc",
            "published_at": "2024-11-01",
            "summary": "Company reports results.",
            "source_name": "Apple Newsroom",
            "source_url_quality": "canonical_article",
        }
    ]
    conn = CompanyIrConnector(press_fetcher=_fake_press_fetcher(press))
    res = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="AAPL"), QueryContext())
    )
    assert len(res.evidence_items) == 1
    item = res.evidence_items[0]
    assert item.content_source_tier == T1_PRIMARY_COMPANY_SOURCE
    assert item.source_type == "company_ir_press_release"


def test_company_ir_strips_url_secrets():
    press = [{"headline": "News", "url": "https://x.com/pr?api_token=SECRET&id=5"}]
    conn = CompanyIrConnector(press_fetcher=_fake_press_fetcher(press))
    res = asyncio.run(conn.fetch_events(CompanyContext(ticker="X"), QueryContext()))
    assert "api_token" not in (res.evidence_items[0].url or "")
    assert "secret" not in (res.evidence_items[0].url or "").lower()


def test_company_ir_bounds_item_count():
    press = [{"headline": f"PR {i}", "url": f"https://x.com/{i}"} for i in range(50)]
    conn = CompanyIrConnector(press_fetcher=_fake_press_fetcher(press))
    res = asyncio.run(
        conn.fetch_events(CompanyContext(ticker="X"), QueryContext(max_items=3))
    )
    assert len(res.evidence_items) == 3


# ---------------------------------------------------------------------------
# 11–14  Scaffold connectors — honest gaps, no fake evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sid",
    ["sedar_plus", "asx_announcements"],
)
def test_scaffold_connector_returns_gap_no_fake_evidence(sid: str):
    reg = build_registry()
    conn = reg.connectors()[sid]
    assert isinstance(conn, ScaffoldConnector)
    res = asyncio.run(
        conn.call_safe(conn.fetch_filings, CompanyContext(ticker="X", exchange="TO"), QueryContext())
    )
    assert res.evidence_items == []  # never a fabricated filing
    assert res.source_gaps
    assert res.source_gaps[0].gap_type == GapType.connector_scaffolded
    # No rating / price-target vocabulary in the honest gap message.
    assert not _has_forbidden(res.source_gaps[0].message)


# ---------------------------------------------------------------------------
# 15–17  Evidence-pack integration
# ---------------------------------------------------------------------------


def _catalyst_with_filing_and_press() -> dict[str, Any]:
    return {
        "filing_events": [
            {
                "form_type": "10-K",
                "headline": "Apple Inc. 10-K",
                "source_url": "https://www.sec.gov/Archives/edgar/aapl-10k.htm",
                "filing_date": "2024-11-01",
                "summary": "Annual report",
            }
        ],
        "press_release_events": [
            {
                "headline": "Apple announces dividend",
                "source_url": "https://www.apple.com/newsroom/pr",
                "event_date": "2024-10-31",
                "summary": "Board declared a dividend.",
                "source_name": "Apple Newsroom",
            }
        ],
    }


def test_aapl_evidence_pack_includes_connector_items():
    cat = _catalyst_with_filing_and_press()
    cfg = _enabled_cfg()
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US", company_name="Apple Inc."),
            filings=sec_filings_from_catalyst(cat),
            press_items=press_items_from_catalyst(cat),
            cfg=cfg,
        )
    )
    assert len(collected.evidence_items) == 2
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "AAPL"}}},
        connector_evidence=collected.evidence_items,
        connector_gap_messages=collected.gap_messages(),
    )
    tiers = {i.content_tier for i in pack.evidence_items}
    assert T1_PRIMARY_FILING in tiers
    assert T1_PRIMARY_COMPANY_SOURCE in tiers
    # Evidence ids are the pack's canonical E# ids.
    assert all(i.id.startswith("E") for i in pack.evidence_items)


def test_uhr_evidence_pack_includes_non_us_gaps():
    cfg = _enabled_cfg()
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="UHR", exchange="SW", country="CH"),
            cfg=cfg,
        )
    )
    msgs = " ".join(collected.gap_messages()).lower()
    assert "sec edgar covers us issuers only" in msgs
    # The non-US home-regulator honesty gap (company_ir path, unchanged) is present.
    assert "regulated-disclosure connector scaffolded" in msgs
    # Phase 29B.1: UHR is a verified issuer, so company-IR *metadata* evidence is
    # present. Phase 29B.4C additionally routes a verified Swiss issuer to the
    # dedicated SIX Swiss regulator-transport reference (metadata only). No SEC
    # filing is fabricated — every item is company_ir or the six_swiss reference.
    assert collected.evidence_items != []
    assert all(
        it.source_id in ("company_ir", "six_swiss") for it in collected.evidence_items
    )
    assert all(it.data_quality == "metadata_only" for it in collected.evidence_items)
    # gaps flow into the pack known_gaps.
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "UHR"}}},
        connector_gap_messages=collected.gap_messages(),
    )
    assert any(
        "regulated-disclosure connector scaffolded" in g.lower()
        for g in pack.known_gaps
    )


def test_discovery_pack_includes_run_level_source_gaps():
    from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
    from app.services.sources.registry import registry_gap_messages

    gaps = registry_gap_messages(build_registry())
    pack = build_discovery_evidence_pack(
        run={"mode": "thesis", "status": "completed"},
        candidates=[{"ticker": "AAPL"}],
        extra_known_gaps=gaps,
    )
    joined = " ".join(pack.known_gaps).lower()
    assert "scaffolded" in joined


# ---------------------------------------------------------------------------
# 18–19  Council still accepts evidence ids; output is safety-clean
# ---------------------------------------------------------------------------


def test_council_accepts_connector_evidence_ids():
    cat = _catalyst_with_filing_and_press()
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US"),
            filings=sec_filings_from_catalyst(cat),
            press_items=press_items_from_catalyst(cat),
            cfg=_enabled_cfg(),
        )
    )
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "AAPL"}}},
        connector_evidence=collected.evidence_items,
        connector_gap_messages=collected.gap_messages(),
    )
    result = asyncio.run(run_council(pack, FakeLLMClient()))
    assert result.llm_used is True
    assert result.agents_completed >= 1


def test_connector_evidence_and_gaps_are_safety_clean():
    cat = _catalyst_with_filing_and_press()
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US"),
            filings=sec_filings_from_catalyst(cat),
            press_items=press_items_from_catalyst(cat),
            cfg=_enabled_cfg(),
        )
    )
    for g in collected.gap_messages():
        assert not _has_forbidden(g)
    # No secrets in the serialized connector evidence.
    blob = json.dumps(
        [i.model_dump(mode="json") for i in collected.evidence_items]
    ).lower()
    assert "api_token" not in blob


# ---------------------------------------------------------------------------
# 20–23  API behaviour
# ---------------------------------------------------------------------------


def test_evidence_preview_offline_bounded_and_secret_free():
    resp = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "AAPL", "exchange": "US"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["connector_layer_enabled"] is False  # flag off by default
    assert isinstance(body["evidence_items"], list)
    assert isinstance(body["source_gaps"], list)
    blob = json.dumps(body).lower()
    for needle in ("api_token", "bearer ", "password", "postgresql://"):
        assert needle not in blob


def test_evidence_preview_live_path_bounded_and_secret_free(monkeypatch):
    # Simulate staging (flag ON) with stubbed live fetchers — no real network.
    import app.api.v1.sources as sources_api

    async def _sec(company, query):
        return [
            {
                "form_type": "10-K",
                "title": "Apple 10-K",
                "url": "https://www.sec.gov/x?api_token=SECRET",
                "filed_date": "2024-11-01",
                "summary": "Annual report",
            }
        ]

    async def _ir(company, query):
        return [{"headline": "Apple PR", "url": "https://apple.com/pr"}]

    monkeypatch.setattr(sources_api.settings, "source_connector_enabled", True)
    monkeypatch.setattr(sources_api, "live_sec_filings_fetcher", _sec)
    monkeypatch.setattr(sources_api, "live_ir_press_fetcher", _ir)

    resp = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "AAPL", "exchange": "US", "source_ids": ["sec_edgar", "company_ir"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["live_fetch_performed"] is True
    assert len(body["evidence_items"]) == 2
    blob = json.dumps(body).lower()
    assert "api_token" not in blob and "secret" not in blob


def test_evidence_preview_rejects_unknown_source_id():
    resp = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "AAPL", "source_ids": ["definitely_not_a_source"]},
    )
    assert resp.status_code == 400


def test_evidence_preview_has_no_url_input_field():
    # The request schema must not accept a URL to fetch (no open proxy / SSRF).
    from app.schemas.source_evidence_preview import EvidencePreviewRequest

    assert "url" not in EvidencePreviewRequest.model_fields


def test_no_publish_route_and_source_routes_present():
    paths = set(app.openapi()["paths"].keys())
    assert not any("publish" in p for p in paths)
    assert "/api/v1/sources/evidence-preview" in paths
    assert "/api/v1/sources/registry" in paths


# ---------------------------------------------------------------------------
# Regression — connector layer OFF keeps Phase 29A behaviour
# ---------------------------------------------------------------------------


def test_connector_layer_off_adds_no_connector_evidence():
    cfg = Settings()  # source_connector_enabled defaults False
    assert cfg.source_connector_enabled is False
    # The default pack (no connector evidence passed) is unchanged.
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "AAPL"}}},
    )
    assert isinstance(pack.evidence_items, list)


def test_ba_lse_not_sec_eligible_no_boeing_confusion():
    # BA on the LSE is BAE Systems (UK), NOT Boeing. The SEC connector must not
    # run a US lookup (honest source_not_eligible gap), and any evidence produced
    # must be BAE's own company-IR material — never a Boeing SEC filing.
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="BA", exchange="LSE"),
            cfg=_enabled_cfg(),
        )
    )
    assert any(
        g.gap_type == GapType.source_not_eligible for g in collected.source_gaps
    )
    # No SEC / Boeing evidence: every item is BAE Systems' own company-IR metadata
    # or the UK FCA NSM regulator-transport reference (Phase 29B.4A) — never SEC.
    assert all(
        it.source_id in ("company_ir", "uk_fca_nsm")
        for it in collected.evidence_items
    )
    blob = " ".join(
        f"{it.source_name} {it.title} {it.url}" for it in collected.evidence_items
    ).lower()
    assert "boeing" not in blob
    assert "sec.gov" not in blob
    assert any("bae" in (it.source_name or "").lower() for it in collected.evidence_items)
