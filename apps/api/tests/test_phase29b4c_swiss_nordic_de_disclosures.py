"""
Phase 29B.4C — Germany / Nordic / Switzerland regulated-disclosure connectors.

Mirrors Phase 29B.4A (UK FCA NSM) / 29B.4B (Euronext). Covers the promotion of
the former ``deutsche_boerse`` / ``nordic_disclosures`` scaffolds and the NEW
``six_swiss`` source into dedicated connectors that each emit a bounded T2
regulator-transport SOURCE REFERENCE (never a fabricated filing) for a verified
issuer, the German / Nordic ``requires_translation`` signals, the deliberate
absence of a translation claim for Switzerland, the tightened DE/DK/CH issuer ->
regulator mapping in ``_relevant_scaffold_ids``, and the registry / health
honesty guarantees (11 enabled / 2 scaffolded). Everything runs offline — this
task adds no live fetch (that is Task 2).
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
from app.services.sources.connectors.deutsche_boerse import (
    DEUTSCHE_BOERSE_DISCLOSURE_URL,
    DeutscheBoerseConnector,
)
from app.services.sources.connectors.nordic_disclosures import (
    NORDIC_DISCLOSURE_URL,
    NordicDisclosuresConnector,
)
from app.services.sources.connectors.six_swiss import (
    SIX_SWISS_DISCLOSURE_URL,
    SixSwissConnector,
)
from app.services.sources.gaps import GapType
from app.services.sources.registry import build_registry
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    ConnectorStatus,
    SourceStatus,
)

client = TestClient(app)


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
# 1–3  Each connector: T2 regulator-transport reference + honest content gap
# ---------------------------------------------------------------------------


def test_1_deutsche_boerse_emits_t2_reference_and_german_translation():
    conn = DeutscheBoerseConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="SAP", exchange="DE"), _q())
    )
    assert len(res.evidence_items) == 1
    item = res.evidence_items[0]
    assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
    assert item.content_source_tier == T2_REGULATOR_OR_GOV
    assert item.source_type == "deutsche_boerse_reference"
    assert item.data_quality == "metadata_only"
    assert item.source_name and item.url == DEUTSCHE_BOERSE_DISCLOSURE_URL
    assert "?" not in (item.url or "")  # fixed venue landing page, no query
    # German-language disclosures → requires_translation + a translation gap.
    assert item.requires_translation is True
    assert (item.original_language or "").lower() == "german"
    assert any(g.gap_type == GapType.primary_filing_unavailable for g in res.source_gaps)
    assert any(g.gap_type == GapType.translation_required for g in res.source_gaps)


def test_2_nordic_emits_t2_reference_and_danish_translation():
    conn = NordicDisclosuresConnector()
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="PNDORA", exchange="CO"), _q())
    )
    assert len(res.evidence_items) == 1
    item = res.evidence_items[0]
    assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
    assert item.content_source_tier == T2_REGULATOR_OR_GOV
    assert item.source_type == "nordic_disclosures_reference"
    assert item.data_quality == "metadata_only"
    assert item.source_name and item.url == NORDIC_DISCLOSURE_URL
    assert "?" not in (item.url or "")
    # Danish-language disclosures → requires_translation + a translation gap.
    assert item.requires_translation is True
    assert (item.original_language or "").lower() == "danish"
    assert any(g.gap_type == GapType.primary_filing_unavailable for g in res.source_gaps)
    assert any(g.gap_type == GapType.translation_required for g in res.source_gaps)


def test_3_six_swiss_emits_t2_reference_and_no_false_translation_claim():
    for ticker in ("CFR", "UHR"):
        conn = SixSwissConnector()
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker=ticker, exchange="SW"), _q())
        )
        assert len(res.evidence_items) == 1, ticker
        item = res.evidence_items[0]
        assert item.provider_transport_tier == T2_REGULATOR_OR_GOV
        assert item.content_source_tier == T2_REGULATOR_OR_GOV
        assert item.source_type == "six_swiss_reference"
        assert item.data_quality == "metadata_only"
        assert item.source_name and item.url == SIX_SWISS_DISCLOSURE_URL
        assert "?" not in (item.url or "")
        # No false translation claim for Switzerland (majors publish English).
        assert item.requires_translation is False, ticker
        assert item.original_language is None, ticker
        assert not any(
            g.gap_type == GapType.translation_required for g in res.source_gaps
        ), ticker
        # But an honest content gap is still present.
        assert any(
            g.gap_type == GapType.primary_filing_unavailable for g in res.source_gaps
        ), ticker
        # It still notes the multilingual possibility neutrally (in a warning; the
        # excerpt is bounded to 400 chars).
        assert any("national language" in w.lower() for w in item.warnings), ticker


# ---------------------------------------------------------------------------
# 4  No fabricated filing on any of the three references
# ---------------------------------------------------------------------------


def test_4_no_fabricated_filing_on_any_reference():
    cases = (
        (DeutscheBoerseConnector(), "SAP", "DE", "bafin"),
        (NordicDisclosuresConnector(), "PNDORA", "CO", "finanstilsynet"),
        (SixSwissConnector(), "CFR", "SW", "six exchange regulation"),
    )
    for conn, ticker, exchange, regulator_needle in cases:
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker=ticker, exchange=exchange), _q())
        )
        item = res.evidence_items[0]
        # A source reference, not a specific notice: no fabricated filing date.
        assert item.date is None, ticker
        excerpt = item.excerpt.lower()
        assert "no individual filing" in excerpt, ticker
        assert "fabricated" in excerpt, ticker
        # The reference cites the home regulator honestly.
        blob = json.dumps(res.model_dump(mode="json")).lower()
        assert regulator_needle in blob, ticker


# ---------------------------------------------------------------------------
# 5  Non-eligible / unresolvable issuers return only an honest gap
# ---------------------------------------------------------------------------


def test_5_non_eligible_issuers_return_only_honest_gap():
    # Right venue, unregistered issuer → source_not_eligible, no reference.
    for conn, exchange in (
        (DeutscheBoerseConnector(), "DE"),
        (NordicDisclosuresConnector(), "CO"),
        (SixSwissConnector(), "SW"),
    ):
        res = asyncio.run(
            conn.fetch_filings(CompanyContext(ticker="ZZZZ", exchange=exchange), _q())
        )
        assert res.evidence_items == []
        assert any(g.gap_type == GapType.source_not_eligible for g in res.source_gaps)
    # Cross-venue: a verified Swiss issuer is not eligible for the German connector,
    # a verified UK issuer is not eligible for the Nordic connector, etc.
    swiss_on_de = asyncio.run(
        DeutscheBoerseConnector().fetch_filings(
            CompanyContext(ticker="CFR", exchange="SW"), _q()
        )
    )
    assert swiss_on_de.evidence_items == []
    uk_on_nordic = asyncio.run(
        NordicDisclosuresConnector().fetch_filings(
            CompanyContext(ticker="BRBY", exchange="LSE"), _q()
        )
    )
    assert uk_on_nordic.evidence_items == []
    de_on_swiss = asyncio.run(
        SixSwissConnector().fetch_filings(
            CompanyContext(ticker="SAP", exchange="DE"), _q()
        )
    )
    assert de_on_swiss.evidence_items == []


# ---------------------------------------------------------------------------
# 6–7  Exchange -> regulator mapping + tightened _relevant_scaffold_ids
# ---------------------------------------------------------------------------


def test_6_exchange_regulator_mapping_de_dk_ch():
    assert regulator_connector_for("XETRA") == "deutsche_boerse"
    assert regulator_connector_for("F") == "deutsche_boerse"
    assert regulator_connector_for("DE", "Germany") == "deutsche_boerse"
    assert regulator_connector_for(None, "Germany") == "deutsche_boerse"
    assert regulator_connector_for("CO") == "nordic_disclosures"
    assert regulator_connector_for(None, "Denmark") == "nordic_disclosures"
    assert regulator_connector_for("SW") == "six_swiss"
    assert regulator_connector_for("VX") == "six_swiss"
    assert regulator_connector_for(None, "Switzerland") == "six_swiss"
    # Unchanged mappings from 29B.4A / 29B.4B.
    assert regulator_connector_for("LSE") == "uk_fca_nsm"
    assert regulator_connector_for("PA") == "euronext_regulated_info"
    assert regulator_connector_for("AS") == "euronext_regulated_info"


def test_7_de_dk_ch_each_map_to_own_regulator_only():
    reg = build_registry()
    expected = {
        ("SAP", "DE"): "deutsche_boerse",
        ("PNDORA", "CO"): "nordic_disclosures",
        ("CFR", "SW"): "six_swiss",
        ("UHR", "SW"): "six_swiss",
    }
    all_regulators = {
        "deutsche_boerse",
        "nordic_disclosures",
        "six_swiss",
        "euronext_regulated_info",
        "uk_fca_nsm",
    }
    for (ticker, exchange), sid in expected.items():
        ids = _relevant_scaffold_ids(
            reg, CompanyContext(ticker=ticker, exchange=exchange), None
        )
        assert ids == [sid], (ticker, ids)
        # No other regulator connector or Europe scaffold leaks in.
        for other in all_regulators - {sid}:
            assert other not in ids, (ticker, other)


def test_7b_uk_and_euronext_mapping_unchanged():
    reg = build_registry()
    uk = _relevant_scaffold_ids(reg, CompanyContext(ticker="BRBY", exchange="LSE"), None)
    assert uk == ["uk_fca_nsm"]
    fr = _relevant_scaffold_ids(reg, CompanyContext(ticker="MC", exchange="PA"), None)
    assert fr == ["euronext_regulated_info"]
    nl = _relevant_scaffold_ids(reg, CompanyContext(ticker="ASML", exchange="AS"), None)
    assert nl == ["euronext_regulated_info"]


def test_7c_us_issuer_gets_none_of_the_new_regulators():
    reg = build_registry()
    ids = _relevant_scaffold_ids(reg, CompanyContext(ticker="AAPL", exchange="US"), None)
    assert ids == []
    for sid in ("deutsche_boerse", "nordic_disclosures", "six_swiss"):
        assert sid not in ids


# ---------------------------------------------------------------------------
# 8–10  Registry / health honesty; connector status; safety vocabulary
# ---------------------------------------------------------------------------


def test_8_registry_promotes_three_connectors_to_enabled():
    reg = build_registry()
    expected = {
        "deutsche_boerse": DeutscheBoerseConnector,
        "nordic_disclosures": NordicDisclosuresConnector,
        "six_swiss": SixSwissConnector,
    }
    scaffolded_ids = {s.source_id for s in reg.scaffolded_sources()}
    for sid, cls in expected.items():
        src = reg.get(sid)
        assert src is not None
        assert src.status == SourceStatus.enabled
        assert src.tier == T2_REGULATOR_OR_GOV
        conn = reg.connectors()[sid]
        assert isinstance(conn, cls)
        assert conn.status == ConnectorStatus.enabled
        # No longer grouped with the honest scaffolds.
        assert sid not in scaffolded_ids
    # Registry now reports 34 enabled / 2 scaffolded sources (Phase 29C.1 added
    # 5 reference-only macro sources, 29C.2 added 5 reference-only commodity /
    # energy sources, 29C.3 added 5 reference-only policy / government sources,
    # 29D.1 added 2 reference-only procurement / tender event sources, 29D.2
    # added 3 reference-only patent office / index event sources, and 29D.3 added
    # 3 reference-only permit / regulatory-event sources to the 11 regulator-layer
    # enabled sources).
    summary = reg.summary()
    assert summary["enabled"] == 34
    assert summary["scaffolded"] == 2
    assert scaffolded_ids == {"sedar_plus", "asx_announcements"}


def test_9_registry_and_health_secret_free_and_honest_about_content():
    for path in ("/api/v1/sources/registry", "/api/v1/sources/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        blob = json.dumps(resp.json()).lower()
        for needle in ("api_token", "bearer ", "authorization", "password", "postgresql://"):
            assert needle not in blob
    reg = build_registry()
    for sid in ("deutsche_boerse", "nordic_disclosures", "six_swiss"):
        note = (reg.get(sid).reliability_note or "").lower()
        assert "content is not fetched at report time" in note
    # German / Nordic notes mention translation; the Swiss note is honest that no
    # translation is asserted.
    assert "translation" in (reg.get("deutsche_boerse").reliability_note or "").lower()
    assert "translation" in (reg.get("nordic_disclosures").reliability_note or "").lower()
    assert "no translation is asserted" in (
        reg.get("six_swiss").reliability_note or ""
    ).lower()


def test_10_connector_output_has_no_forbidden_vocab():
    cases = (
        (DeutscheBoerseConnector(), "SAP", "DE"),
        (NordicDisclosuresConnector(), "PNDORA", "CO"),
        (SixSwissConnector(), "CFR", "SW"),
        (SixSwissConnector(), "UHR", "SW"),
    )
    for conn, ticker, exchange in cases:
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
# 11–13  Collection integration: company IR + regulator reference; US exclusion
# ---------------------------------------------------------------------------


def test_11_sap_de_collects_company_ir_and_deutsche_boerse_reference():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="SAP", exchange="DE"),
            cfg=_enabled_cfg(),
        )
    )
    source_ids = {it.source_id for it in collected.evidence_items}
    assert "company_ir" in source_ids
    assert "deutsche_boerse" in source_ids
    # SEC honestly ineligible for this non-US issuer — never a US SEC filing.
    assert any(
        g.gap_type == GapType.source_not_eligible for g in collected.source_gaps
    )
    blob = json.dumps(collected.model_dump(mode="json")).lower()
    assert "sec.gov" not in blob


def test_12_pndora_co_and_cfr_sw_collect_company_ir_plus_reference():
    pndora = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="PNDORA", exchange="CO"),
            cfg=_enabled_cfg(),
        )
    )
    assert {"company_ir", "nordic_disclosures"} <= {
        it.source_id for it in pndora.evidence_items
    }
    cfr = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW"),
            cfg=_enabled_cfg(),
        )
    )
    cfr_ids = {it.source_id for it in cfr.evidence_items}
    assert {"company_ir", "six_swiss"} <= cfr_ids
    # Every collected item is metadata-only (no fabricated filing content).
    for c in (pndora, cfr):
        assert all(it.data_quality == "metadata_only" for it in c.evidence_items)


def test_13_us_issuer_gets_no_new_regulator_reference():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US"),
            cfg=_enabled_cfg(),
        )
    )
    assert all(
        it.source_id not in ("deutsche_boerse", "nordic_disclosures", "six_swiss")
        for it in collected.evidence_items
    )
