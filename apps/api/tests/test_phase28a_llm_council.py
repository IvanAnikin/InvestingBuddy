"""
Phase 28A — single-company LLM analysis council.

All tests run with the deterministic FAKE client only — no network, no
credentials. They cover the evidence pack builder, the client abstraction, the
council orchestrator (citations + safety + labels), the report-generator
integration (llm_used honesty, schema/safety validation, human-review + no
publication invariants), and safe logging.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.core.structured_logging import format_event
from app.services import safety_terms
from app.services.final_report_generator import (
    FinalReportGeneratorService,
    run_safety_gate,
)
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.client import (
    LLMJsonError,
    LLMTimeoutError,
    get_llm_client,
)
from app.services.llm.council import run_council
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.fake_client import FakeLLMClient
from app.services.llm.schemas import (
    ALLOWED_COMMITTEE_LABELS,
    COUNCIL_AGENT_ORDER,
    CouncilAgentOutput,
)

FORBIDDEN_SUBSTRINGS = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "fair value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
)


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _aapl_report_content() -> dict[str, Any]:
    return {
        "company_identity": {
            "legal_name": {"value": "Apple Inc."},
            "ticker": {"value": "AAPL"},
            "exchange": {"value": "NASDAQ"},
            "country_domicile": {"value": "US"},
            "sector": {"value": "Technology"},
        },
        "financial_snapshot": {
            "source_tier": "T5_api_aggregator",
            "latest_close": {"value": 190.5, "currency": "USD"},
            "revenue_ttm_usd_m": {
                "value": 383285,
                "unit": "USD_m",
                "source_tier": "T5_api_aggregator",
            },
        },
        "data_availability_summary": {"missing_fields": {"value": ["lei", "beta"]}},
        "source_citation_appendix": {
            "sources": {
                "value": [
                    {
                        "source_type": "sec_filing",
                        "source_tier": "T2_regulator_or_gov",
                        "title": "Apple Inc. 10-K FY2023",
                        "url": "https://www.sec.gov/cgi-bin/browse-edgar?...",
                        "source_quote": "Total net sales were $383,285 million.",
                    }
                ]
            }
        },
    }


def _aapl_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "company_identity": {
            "ticker": "AAPL",
            "legal_name": "Apple Inc.",
            "exchange": "NASDAQ",
            "country_domicile": "US",
        },
        "profile": {"sector": "Technology", "industry": "Consumer Electronics"},
        "fundamentals_summary": {
            "revenue_usd_m": 383285.0,
            "net_income_usd_m": 96995.0,
            "operating_income_usd_m": 114301.0,
            "form_type": "10-K",
            "fiscal_year": 2023,
            "fiscal_period": "FY",
            "filed_date": "2023-11-03",
            "accession_number": "0000320193-23-000106",
            "source_tier": "T2_regulator_or_gov",
            "data_quality": "A_verified",
        },
    }


def _uhr_report_content() -> dict[str, Any]:
    """UHR.SW — sparse non-US Swiss watch producer. No SEC fundamentals."""
    return {
        "company_identity": {
            "legal_name": {"value": "The Swatch Group AG"},
            "ticker": {"value": "UHR"},
            "exchange": {"value": "SWX"},
            "country_domicile": {"value": "Switzerland"},
            "sector": {"value": "Consumer Cyclical"},
        },
        "financial_snapshot": {"source_tier": "T6_model_estimate"},
        "data_availability_summary": {
            "missing_fields": {"value": ["revenue", "ebitda", "lei"]}
        },
    }


def _uhr_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "company_identity": {
            "ticker": "UHR",
            "legal_name": "The Swatch Group AG",
            "exchange": "SWX",
            "country_domicile": "Switzerland",
        },
        "profile": {"sector": "Consumer Cyclical", "industry": "Watches & Jewelry"},
    }


def _captured_report(mock_db):
    """Return the Report object handed to db.add() inside the generator."""
    assert mock_db.add.called, "expected a report to be saved"
    return mock_db.add.call_args[0][0]


@pytest.fixture
def enable_council(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    yield


# ===========================================================================
# 1-5  Evidence pack
# ===========================================================================


def test_1_evidence_pack_aapl_sec_rich() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    assert pack.item_count >= 3
    assert pack.company.ticker == "AAPL"
    assert pack.company.company_name == "Apple Inc."
    # A T1 company filing is present in the pack.
    assert any(i.content_tier == "T1_primary_filing" for i in pack.evidence_items)


def test_2_evidence_pack_uhr_sparse_non_us() -> None:
    pack = build_evidence_pack(
        report_content=_uhr_report_content(), company_snapshot=_uhr_snapshot()
    )
    assert pack.company.country == "Switzerland"
    assert pack.company.legal_name == "The Swatch Group AG"
    # Sparse: no fabricated financials, and the known gaps are honest.
    assert "revenue" in pack.known_gaps
    # No SEC/T1 filing evidence was invented for a non-US issuer.
    assert not any(
        i.content_tier == "T1_primary_filing" for i in pack.evidence_items
    )


def test_3_sec_transport_vs_content_tier() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    sec_items = [i for i in pack.evidence_items if i.source_type == "company_filing"]
    assert sec_items, "expected an SEC filing evidence item"
    item = sec_items[0]
    # SEC EDGAR is the transport (T2); the filing content itself is T1.
    assert item.transport_tier == "T2_regulator_or_gov"
    assert item.content_tier == "T1_primary_filing"
    assert item.provider_transport == "SEC EDGAR / data.sec.gov"


def test_4_evidence_ids_stable_and_unique() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    ids = [i.id for i in pack.evidence_items]
    assert ids == [f"E{n}" for n in range(1, len(ids) + 1)]
    assert len(ids) == len(set(ids))


def test_5_evidence_pack_bounded_by_max_items() -> None:
    # Build many sources so truncation is exercised.
    rc = _aapl_report_content()
    rc["source_citation_appendix"]["sources"]["value"] = [
        {
            "source_type": "news",
            "source_tier": "T4_quality_media",
            "title": f"Headline {n}",
            "url": f"https://example.com/{n}",
        }
        for n in range(50)
    ]
    pack = build_evidence_pack(
        report_content=rc, company_snapshot=_aapl_snapshot(), max_items=10
    )
    assert pack.item_count == 10


# ===========================================================================
# 6-9  LLM client
# ===========================================================================


async def test_6_fake_llm_returns_deterministic_json() -> None:
    client = FakeLLMClient()
    out1 = await client.complete_json("agent id: financial_analyst", 'evi "id": "E1"')
    out2 = await client.complete_json("agent id: financial_analyst", 'evi "id": "E1"')
    assert out1 == out2
    assert out1["agent_name"] == "financial_analyst"
    assert out1["status"] == "completed"


async def test_7_invalid_json_handled_safely() -> None:
    # Repaired on the second attempt -> succeeds.
    repaired = await FakeLLMClient(mode="invalid_json_once").complete_json(
        "agent id: catalyst", 'x "id": "E1"'
    )
    assert repaired["agent_name"] == "catalyst"
    # Never valid -> raises a recoverable error (no crash), caught by the council.
    with pytest.raises(LLMJsonError):
        await FakeLLMClient(mode="invalid_json_always").complete_json(
            "agent id: catalyst", "x"
        )


async def test_8_timeout_marks_agent_failed_without_crash() -> None:
    with pytest.raises(LLMTimeoutError):
        await FakeLLMClient(mode="timeout").complete_json("agent id: catalyst", "x")
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(pack, FakeLLMClient(mode="timeout"))
    assert result.llm_used is True
    assert result.agents_failed == len(COUNCIL_AGENT_ORDER)
    assert result.agents_completed == 0  # no crash; every agent failed cleanly


def test_9_provider_disabled_returns_none(monkeypatch) -> None:
    from app.core import config

    # Disabled -> None (deterministic path).
    monkeypatch.setattr(config.settings, "llm_council_enabled", False)
    assert get_llm_client(config.settings) is None
    # Enabled + fake -> a fake client.
    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    assert isinstance(get_llm_client(config.settings), FakeLLMClient)
    # Enabled + unknown provider -> None.
    monkeypatch.setattr(config.settings, "llm_provider_council", "nonsense")
    assert get_llm_client(config.settings) is None
    # Enabled + azure but no creds -> None (never raises).
    monkeypatch.setattr(config.settings, "llm_provider_council", "azure_openai")
    monkeypatch.setattr(config.settings, "azure_openai_endpoint", "")
    assert get_llm_client(config.settings) is None


# ===========================================================================
# 10-16  Council
# ===========================================================================


async def test_10_all_agents_run_in_fake_mode() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(pack, FakeLLMClient())
    assert [a.agent_name for a in result.agents] == list(COUNCIL_AGENT_ORDER)
    assert result.agents_completed == len(COUNCIL_AGENT_ORDER)
    assert result.agents_failed == 0


async def test_11_citation_ids_must_exist_in_pack() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    ids = pack.evidence_ids()
    result = await run_council(pack, FakeLLMClient())
    for agent in result.agents:
        for kp in agent.key_points:
            for cid in kp.citation_ids:
                assert cid in ids


async def test_12_unsupported_citation_id_is_flagged() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(pack, FakeLLMClient(bad_citation_agents={"catalyst"}))
    catalyst = next(a for a in result.agents if a.agent_name == "catalyst")
    cited = [c for kp in catalyst.key_points for c in kp.citation_ids]
    assert "E999" not in cited
    assert any("not present in the evidence pack" in w for w in result.warnings)


async def test_13_uncited_material_claim_is_flagged() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(
        pack, FakeLLMClient(uncited_agents={"business_moat"})
    )
    bm = next(a for a in result.agents if a.agent_name == "business_moat")
    assert bm.unsupported_claims  # moved out of key_points
    assert any("unsupported_claims" in w for w in result.warnings)


async def test_14_forbidden_terms_trigger_safety_failure() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(
        pack, FakeLLMClient(forbidden_agents={"financial_analyst"})
    )
    fa = next(a for a in result.agents if a.agent_name == "financial_analyst")
    assert fa.status == "failed"
    assert "BUY" not in fa.summary  # quarantined, never propagated
    # And nothing forbidden reaches the whole council payload.
    hits = safety_terms.scan_value(result.to_report_dict())
    assert hits == []


async def test_15_committee_chair_uses_only_allowed_labels() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(pack, FakeLLMClient())
    assert result.committee_label in ALLOWED_COMMITTEE_LABELS
    # A rogue (but non-forbidden) label is coerced by the checker to a safe
    # internal default, never emitted as-is.
    rogue = CouncilAgentOutput(
        agent_name="committee_chair", committee_label="maybe_invest_soon"
    )
    sanitized, _ = check_and_sanitize(rogue, pack.evidence_ids())
    assert sanitized.committee_label in ALLOWED_COMMITTEE_LABELS
    assert sanitized.committee_label == "insufficient_data"


async def test_16_valuation_guard_no_price_target_or_fair_value() -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    result = await run_council(pack, FakeLLMClient())
    vg = next(a for a in result.agents if a.agent_name == "valuation_guard")
    assert safety_terms.scan_value(vg.model_dump()) == []
    # Even if the model tried, forbidden valuation language is quarantined.
    forced = await run_council(
        pack, FakeLLMClient(forbidden_agents={"valuation_guard"})
    )
    vg2 = next(a for a in forced.agents if a.agent_name == "valuation_guard")
    assert vg2.status == "failed"


# ===========================================================================
# 17-29  Report integration
# ===========================================================================


async def _generate(mock_db, *, snapshot, catalyst=None, company_record=None):
    service = FinalReportGeneratorService()
    return await service._generate_and_save(
        db=mock_db,
        scorecard=None,
        candidate=None,
        source_report=None,
        company_record=company_record,
        citations=[],
        sources=[],
        state={"company_snapshot": snapshot, "catalyst_discovery": catalyst},
    )


async def test_17_llm_enabled_report_says_llm_used(mock_db, enable_council) -> None:
    resp = await _generate(mock_db, snapshot=_aapl_snapshot())
    assert resp.llm_used is True
    assert resp.llm_provider == "fake"
    assert resp.council_version == "v1"
    assert resp.council_agents_completed > 0
    assert resp.evidence_item_count > 0
    report = _captured_report(mock_db)
    assert report.source_summary_json["llm_council"]["llm_used"] is True


async def test_18_llm_disabled_report_says_not_used(mock_db) -> None:
    resp = await _generate(mock_db, snapshot=_aapl_snapshot())
    assert resp.llm_used is False
    assert resp.council_version is None
    assert resp.council_agents_completed == 0
    report = _captured_report(mock_db)
    assert report.source_summary_json["llm_council"]["llm_used"] is False
    # No council section is added to the deterministic report content.
    assert "llm_council_analysis" not in report.content_markdown


async def test_19_schema_valid_with_council_output(mock_db, enable_council) -> None:
    resp = await _generate(mock_db, snapshot=_aapl_snapshot())
    assert resp.schema_valid is True


async def test_20_safety_valid_for_safe_fake_output(mock_db, enable_council) -> None:
    resp = await _generate(mock_db, snapshot=_aapl_snapshot())
    assert resp.safety_valid is True


async def test_21_unsafe_output_is_blocked(mock_db, enable_council, monkeypatch) -> None:
    from app.services.llm import council as council_mod

    monkeypatch.setattr(
        council_mod,
        "get_llm_client",
        lambda cfg=None: FakeLLMClient(forbidden_agents={"financial_analyst"}),
    )
    resp = await _generate(mock_db, snapshot=_aapl_snapshot())
    report = _captured_report(mock_db)
    # Blocked: the forbidden token never reaches the saved report content...
    assert "BUY" not in report.content_markdown
    # ...the offending agent is marked failed...
    assert resp.council_agents_failed >= 1
    # ...and the report is never publication-ready and always needs review.
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    # Direct backstop: a forbidden term inside a council section fails the gate.
    unsafe_content = {"llm_council_analysis": {"summary": "internal note: BUY"}}
    assert run_safety_gate(unsafe_content).passed is False


async def test_22_human_review_always_required(mock_db, enable_council) -> None:
    resp_on = await _generate(mock_db, snapshot=_aapl_snapshot())
    assert resp_on.human_review_required is True


async def test_23_publication_ready_always_false(mock_db, enable_council) -> None:
    resp = await _generate(mock_db, snapshot=_aapl_snapshot())
    assert resp.publication_ready is False


def test_24_no_publish_route_added() -> None:
    from app.main import app

    publish_routes = [
        r.path for r in app.routes if "publish" in getattr(r, "path", "").lower()
    ]
    assert publish_routes == []


@pytest.mark.parametrize(
    "ticker,name,exchange",
    [("AAPL", "Apple Inc.", "NASDAQ"), ("MSFT", "Microsoft Corp.", "NASDAQ"),
     ("NVDA", "NVIDIA Corp.", "NASDAQ")],
)
async def test_25_deterministic_regression_us_megacaps(
    mock_db, ticker, name, exchange
) -> None:
    snap = _aapl_snapshot()
    snap["company_identity"] = {
        "ticker": ticker,
        "legal_name": name,
        "exchange": exchange,
        "country_domicile": "US",
    }
    resp = await _generate(mock_db, snapshot=snap)  # council disabled by default
    assert resp.llm_used is False
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.human_review_required is True
    assert resp.publication_ready is False


async def test_26_sparse_non_us_report_stays_honest(mock_db, enable_council) -> None:
    resp = await _generate(mock_db, snapshot=_uhr_snapshot())
    assert resp.llm_used is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    # Non-US sparse issuer: research is not falsely marked complete.
    assert resp.research_complete is False
    report = _captured_report(mock_db)
    # No SEC-sourced legal_name / fabricated US filing snuck into the content.
    assert "Boeing" not in report.content_markdown


def test_27_europe_defense_ba_lse_not_boeing() -> None:
    rc = {
        "company_identity": {
            "legal_name": {"value": "BAE Systems plc"},
            "ticker": {"value": "BA"},
            "exchange": {"value": "LSE"},
            "country_domicile": {"value": "United Kingdom"},
        }
    }
    snap = {
        "is_mock": False,
        "company_identity": {
            "ticker": "BA",
            "legal_name": "BAE Systems plc",
            "exchange": "LSE",
            "country_domicile": "United Kingdom",
        },
        "profile": {"sector": "Industrials", "industry": "Aerospace & Defense"},
    }
    pack = build_evidence_pack(report_content=rc, company_snapshot=snap)
    assert pack.company.company_name == "BAE Systems plc"
    assert "Boeing" not in (pack.company.company_name or "")


async def test_28_swiss_watch_country_preserved(mock_db, enable_council) -> None:
    pack = build_evidence_pack(
        report_content=_uhr_report_content(), company_snapshot=_uhr_snapshot()
    )
    # Country stays Switzerland; no US SEC legal_name is invented.
    assert pack.company.country == "Switzerland"
    assert pack.company.legal_name == "The Swatch Group AG"


async def test_29_watch_luxury_names_no_false_positive_safety() -> None:
    assert safety_terms.scan_text("The Swatch Group AG") == []
    assert safety_terms.scan_text("Watches & Jewelry") == []
    pack = build_evidence_pack(
        report_content=_uhr_report_content(), company_snapshot=_uhr_snapshot()
    )
    result = await run_council(pack, FakeLLMClient())
    assert safety_terms.scan_value(result.to_report_dict()) == []


# ===========================================================================
# 30-31  Logging
# ===========================================================================


async def test_30_llm_logs_have_metadata_not_prompts_or_secrets(caplog) -> None:
    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    with caplog.at_level(logging.INFO, logger="app.services.llm.council"):
        await run_council(pack, FakeLLMClient(), ticker="AAPL", report_id="r-1")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "llm_council_started" in text
    assert "llm_council_completed" in text
    assert "provider=fake" in text
    assert "status=completed" in text
    # Never any prompt text, evidence excerpt, or completion body.
    assert "SECURITY:" not in text
    assert "HARD RULES" not in text
    assert "Deterministic fake summary" not in text
    assert "383285" not in text  # a fundamentals value from the evidence pack


def test_31_redaction_still_redacts_sensitive_keys() -> None:
    out = format_event(
        "llm_agent_completed",
        {
            "provider": "fake",
            "model": "fake-council-model",
            "api_key": "sk-should-not-appear",
            "authorization": "Bearer nope",
            "token": "abc123",
        },
    )
    assert "provider=fake" in out
    assert "sk-should-not-appear" not in out
    assert "Bearer nope" not in out
    assert "abc123" not in out
    assert "REDACTED" in out
