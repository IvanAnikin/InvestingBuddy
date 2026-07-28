"""
Phase 29A — Source Registry + Connector Framework tests.

Covers the framework in isolation (taxonomy, evidence models, connectors, gaps,
registry), the two read-only API endpoints, the evidence-pack integration, and
the safety/secret guarantees.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.integrations.financial_data_provider import SourceTier as ProviderSourceTier
from app.main import app
from app.services import safety_terms
from app.services.llm import schemas as llm_schemas
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.sources import (
    build_registry,
    registry_gap_messages,
    sec_tier_pair,
)
from app.services.sources.cache import TTLCache
from app.services.sources.connector_base import (
    CompanyContext,
    QueryContext,
    SourceConnector,
)
from app.services.sources.connectors import PlannedConnector, SecEdgarConnector
from app.services.sources.errors import ConnectorError, ConnectorErrorCode
from app.services.sources.evidence import EvidenceItem, build_evidence_item
from app.services.sources.gaps import GapType
from app.services.sources.redaction import strip_url_secrets, url_has_secret
from app.services.sources.registry import assert_registry_safe, tier_legend
from app.services.sources.taxonomy import (
    CANONICAL_TIERS,
    T1_PRIMARY_FILING,
    T2_REGULATOR_OR_GOV,
    T6_MODEL_ESTIMATE,
    VALID_TIER_CODES,
    is_valid_tier,
)

client = TestClient(app)

TOKEN_URL = "https://eodhd.com/api/eod/AAPL.US?api_token=SECRET123&period=d"


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def test_taxonomy_contains_canonical_tiers():
    codes = {t["code"] for t in CANONICAL_TIERS}
    assert codes == {
        "T1_primary_filing",
        "T2_regulator_or_gov",
        "T3_industry_specialist",
        "T4_quality_media",
        "T5_api_aggregator",
        "T6_model_estimate",
    }
    for tier in codes:
        assert is_valid_tier(tier)
    assert not is_valid_tier("T9_made_up")


def test_taxonomy_consistent_with_provider_and_council_constants():
    """The three tier vocabularies in the codebase must not drift apart."""
    provider_values = {t.value for t in ProviderSourceTier}
    canonical = {t["code"] for t in CANONICAL_TIERS}
    assert provider_values == canonical
    # Council schema constants mirror the same strings.
    assert llm_schemas.TIER_T1_PRIMARY_FILING == T1_PRIMARY_FILING
    assert llm_schemas.TIER_T2_REGULATOR_OR_GOV == T2_REGULATOR_OR_GOV
    assert llm_schemas.TIER_T6_MODEL_ESTIMATE == T6_MODEL_ESTIMATE


def test_sec_transport_content_tier_distinction():
    transport, content = sec_tier_pair()
    assert transport == T2_REGULATOR_OR_GOV  # EDGAR = regulator transport
    assert content == T1_PRIMARY_FILING  # the filing itself = primary


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


def test_evidence_item_requires_content_source_tier():
    with pytest.raises(ValidationError):
        EvidenceItem(id="E1", source_id="sec_edgar", content_source_tier="")
    with pytest.raises(ValidationError):
        EvidenceItem(id="E1", source_id="sec_edgar", content_source_tier="not_a_tier")


def test_evidence_item_redacts_tokenized_url():
    item = build_evidence_item(
        id="E1",
        source_id="eodhd",
        content_source_tier="T5_api_aggregator",
        url=TOKEN_URL,
    )
    assert item.url is not None
    assert "SECRET123" not in item.url
    assert "api_token" not in item.url.lower()
    assert not url_has_secret(item.url)
    # Non-secret query params are preserved.
    assert "period=d" in item.url


def test_evidence_item_bounds_excerpt():
    long = "x" * 5000
    item = build_evidence_item(
        id="E1",
        source_id="sec_edgar",
        content_source_tier=T1_PRIMARY_FILING,
        excerpt=long,
    )
    assert item.excerpt is not None
    assert len(item.excerpt) <= 400


def test_evidence_item_transport_content_pair():
    transport, content = sec_tier_pair()
    item = build_evidence_item(
        id="SEC1",
        source_id="sec_edgar",
        content_source_tier=content,
        provider_transport_tier=transport,
        provider_transport="SEC EDGAR / data.sec.gov",
        content_source="Apple Inc. Form 10-K",
    )
    assert item.provider_transport_tier == "T2_regulator_or_gov"
    assert item.content_source_tier == "T1_primary_filing"
    assert item.tier == "T1_primary_filing"  # weighting uses content tier


def test_redaction_helpers():
    assert url_has_secret(TOKEN_URL) is True
    stripped = strip_url_secrets(TOKEN_URL)
    assert "api_token" not in stripped
    assert "SECRET123" not in stripped
    assert strip_url_secrets(None) is None
    assert strip_url_secrets("https://sec.gov/x") == "https://sec.gov/x"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_returns_enabled_and_planned():
    reg = build_registry()
    enabled_ids = {s.source_id for s in reg.enabled_sources()}
    planned_ids = {s.source_id for s in reg.planned_sources()}
    scaffolded_ids = {s.source_id for s in reg.scaffolded_sources()}
    # Migrated, enabled sources. uk_fca_nsm was promoted to a dedicated
    # regulator-reference connector (Phase 29B.4A), so it is now enabled.
    assert {"sec_edgar", "company_ir", "gleif", "eodhd", "stooq", "gdelt"} <= enabled_ids
    assert "uk_fca_nsm" in enabled_ids
    # The remaining filing/regulator connectors are scaffolded (Phase 29B).
    assert {"sedar_plus", "asx_announcements"} <= scaffolded_ids
    assert "uk_fca_nsm" not in scaffolded_ids
    # The reference-only macro sources were promoted to enabled (Phase 29C.1);
    # they are no longer planned.
    assert {
        "fred",
        "imf",
        "eurostat",
        "world_bank_pink_sheet",
        "national_stats_central_banks",
    } <= enabled_ids
    assert not {"fred", "imf", "eurostat"} & planned_ids
    # The commodity / energy references were promoted to enabled (Phase 29C.2);
    # they are no longer planned.
    assert {"usgs", "iea", "irena", "eia", "entsoe"} <= enabled_ids
    assert not {"usgs", "iea", "eia", "entsoe"} & planned_ids
    # The policy / government references were promoted to enabled (Phase 29C.3);
    # they are no longer planned.
    assert {"ustr_taric", "un_comtrade", "nato", "sipri", "oecd"} <= enabled_ids
    assert not {"ustr_taric", "un_comtrade", "nato", "sipri", "oecd"} & planned_ids
    # The procurement / tender EVENT venues were promoted to enabled (Phase 29D.1)
    # and the patent office / index venues were promoted to enabled (Phase 29D.2);
    # they are no longer planned.
    assert {"eu_ted", "usaspending"} <= enabled_ids
    assert not {"eu_ted", "usaspending"} & planned_ids
    assert {"google_patents", "uspto", "epo_espacenet"} <= enabled_ids
    assert not {"google_patents", "uspto", "epo_espacenet"} & planned_ids
    # Only the OpenBB toolkit + local-language business press stay planned.
    assert {"openbb"} <= planned_ids
    assert reg.summary()["total"] == len(reg.all_sources())
    # Every source carries a valid tier.
    for s in reg.all_sources():
        assert s.tier in VALID_TIER_CODES


def test_registry_sec_entry_marks_transport_tier():
    reg = build_registry()
    sec = reg.get("sec_edgar")
    assert sec is not None
    assert sec.tier == T2_REGULATOR_OR_GOV  # source is the regulator transport
    assert "T1_primary_filing" in (sec.reliability_note or "")


def test_registry_is_safe_no_secrets():
    reg = build_registry()
    # Explicit backstop scan (raises if any secret-like token is present).
    assert_registry_safe(reg)


def test_registry_tier_legend():
    legend = tier_legend()
    assert len(legend) == 6
    assert legend[0]["rank"] == 1


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------


def test_planned_connector_returns_gap_not_crash():
    conn = PlannedConnector(
        connector_key="sedar_plus", source_ids=("sedar_plus",), planned_phase="Phase 29B"
    )
    company = CompanyContext(ticker="SHOP", exchange="TO")
    result = asyncio.run(conn.fetch_filings(company, QueryContext()))
    assert result.evidence_items == []
    assert result.error_code == ConnectorErrorCode.not_implemented.value
    assert result.source_gaps
    assert result.source_gaps[0].gap_type == GapType.connector_planned
    assert result.source_gaps[0].blocks_research_complete is False
    # Health is safe + network-free.
    health = conn.healthcheck()
    assert health.enabled is False
    assert health.status.value == "planned"


def test_connector_failure_produces_warning_and_gap():
    class BoomConnector(SourceConnector):
        connector_key = "boom"
        supported_source_ids = ("boom",)

        async def fetch_events(self, company, query):
            raise ConnectorError(
                ConnectorErrorCode.upstream_error, "https://x?token=SECRET"
            )

    conn = BoomConnector()
    result = asyncio.run(
        conn.call_safe(conn.fetch_events, CompanyContext(), QueryContext())
    )
    assert result.error_code == ConnectorErrorCode.upstream_error.value
    assert result.warnings
    assert result.source_gaps
    assert result.source_gaps[0].gap_type == GapType.connector_error
    # The secret in the raised message must not survive into the safe result.
    blob = json.dumps(result.model_dump(mode="json"))
    assert "SECRET" not in blob


def test_sec_connector_maps_filings_with_tier_pair():
    async def fake_fetcher(company, query):
        return [
            {
                "form_type": "10-K",
                "title": "Annual report (Form 10-K)",
                "url": "https://www.sec.gov/Archives/edgar/data/320193/aapl-10k.htm",
                "filed_date": "2025-11-01",
                "summary": "FY2025 annual report.",
                "fields": ["revenue", "net_income"],
            }
        ]

    conn = SecEdgarConnector(filings_fetcher=fake_fetcher)
    company = CompanyContext(ticker="AAPL", company_name="Apple Inc.")
    result = asyncio.run(conn.fetch_filings(company, QueryContext()))
    assert result.ok
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
    assert item.content_source_tier == T1_PRIMARY_FILING
    assert "Apple Inc." in (item.content_source or "")
    assert item.fields_supported == ["revenue", "net_income"]


def test_sec_connector_without_fetcher_returns_info_gap():
    # No fetcher + SEC-eligible (ticker-only) → an honest metadata-unavailable gap.
    conn = SecEdgarConnector()
    result = asyncio.run(conn.fetch_filings(CompanyContext(), QueryContext()))
    assert result.evidence_items == []
    assert result.source_gaps
    assert result.source_gaps[0].gap_type == GapType.primary_filing_unavailable


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def test_registry_endpoint_returns_sources_and_no_secrets():
    resp = client.get("/api/v1/sources/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["enabled"] >= 6
    # uk_fca_nsm (29B.4A), euronext_regulated_info (29B.4B) and deutsche_boerse /
    # nordic_disclosures (29B.4C) were promoted out of the scaffold set, leaving
    # two honest regulator scaffolds (SEDAR+, ASX).
    assert body["summary"]["scaffolded"] >= 2
    # Phase 29C promoted 15 macro / commodity / policy sources, Phase 29D.1/29D.2/
    # 29D.3 promoted the procurement / patent / permit event venues, and Phase 30B
    # promoted the local-language business press out of the planned set, leaving
    # only OpenBB planned.
    assert body["summary"]["planned"] >= 1
    assert len(body["tiers"]) == 6
    ids = {s["source_id"] for s in body["sources"]}
    assert "sec_edgar" in ids
    assert "sedar_plus" in ids
    # No secret residue anywhere in the payload.
    blob = json.dumps(body).lower()
    for needle in ("api_token", "bearer ", "authorization", "password", "postgresql://"):
        assert needle not in blob


def test_health_endpoint_returns_safe_status():
    resp = client.get("/api/v1/sources/health")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["connectors"]) >= 6
    keys = {c["connector_key"] for c in body["connectors"]}
    assert "sec_edgar" in keys
    blob = json.dumps(body).lower()
    for needle in ("api_token", "bearer ", "secret", "password"):
        assert needle not in blob


def test_no_publish_route_added():
    paths = set(app.openapi()["paths"].keys())
    assert not any("publish" in p for p in paths)
    # The two new source routes exist.
    assert "/api/v1/sources/registry" in paths
    assert "/api/v1/sources/health" in paths


# ---------------------------------------------------------------------------
# Evidence-pack integration
# ---------------------------------------------------------------------------


def test_evidence_pack_uses_evidence_item_ids():
    report_content = {
        "company_identity": {"ticker": {"value": "AAPL"}},
        "source_citation_appendix": {
            "sources": {
                "value": [
                    {
                        "source_tier": T1_PRIMARY_FILING,
                        "source_type": "company_filing",
                        "title": "Form 10-K",
                        "url": TOKEN_URL,
                    }
                ]
            }
        },
    }
    pack = build_evidence_pack(report_content=report_content)
    assert pack.evidence_items
    assert pack.evidence_items[0].id == "E1"
    # Council may cite only ids present in the pack.
    assert pack.evidence_ids() == {i.id for i in pack.evidence_items}
    # URL secret-stripping applies inside the pack too.
    assert "SECRET123" not in (pack.evidence_items[0].url or "")


def test_evidence_pack_model_derived_scoring_is_t6():
    report_content = {
        "financial_snapshot": {
            "source_tier": T6_MODEL_ESTIMATE,
            "market_cap_usd_m": {"value": 3200000, "unit": "USD_m"},
        }
    }
    pack = build_evidence_pack(report_content=report_content)
    model_items = [i for i in pack.evidence_items if i.source_tier == T6_MODEL_ESTIMATE]
    assert model_items, "model-derived snapshot datapoints should be T6"


def test_evidence_pack_extra_known_gaps_surface_source_gaps():
    reg = build_registry()
    gaps = registry_gap_messages(reg)
    assert gaps
    pack = build_evidence_pack(
        report_content={"company_identity": {}}, extra_known_gaps=gaps
    )
    assert any("planned" in g for g in pack.known_gaps)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def test_registry_gap_messages_are_safety_clean():
    """Gap strings must never trip the report safety gate."""
    reg = build_registry()
    for msg in registry_gap_messages(reg):
        hits = safety_terms.scan_text(msg)
        assert hits == [], f"unsafe gap message: {msg!r} -> {hits}"


def test_ttl_cache_basic():
    cache: TTLCache[int] = TTLCache(ttl_seconds=100)
    assert cache.get("k") is None
    cache.set("k", 7)
    assert cache.get("k") == 7
    cache.clear()
    assert cache.get("k") is None
