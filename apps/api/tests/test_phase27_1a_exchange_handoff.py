"""
Phase 27.1A hotfix — the exchange must survive the discovery→workflow→provider handoff.

WHY THIS FILE EXISTS
--------------------
Phase 27.1A added an exchange-aware SEC gate and 315 tests proving that
``resolve_cik(ticker, exchange)`` refuses a non-US venue. Every one of those
tests passed an exchange explicitly. None of them proved that the real pipeline
*supplies* one — and it did not.

On staging, the Europe defense thesis produced a BA/LSE candidate carrying
Boeing's identity and financials:

    legal_name: "BOEING CO"   revenue_mln: 89463.0   market_cap_mln: 164515.12
    data_coverage: {"exchange": null, "sec_eligible": true, "reason": "sec_covered"}

Root cause: ``extract_signal`` invoked the workflow with ``company_id`` only,
and ``node_load_company`` echoed ``ticker`` back into state but not
``exchange``. State kept ``exchange=None`` all the way to the provider;
``is_sec_eligible(None)`` is True by design for legacy ticker-only flows, so
the gate never fired and the US-registrant index answered "BA" with Boeing.

The tests here therefore drive the SEAM, not the leaf function: the real
workflow, the real provider stack, and a network layer that FAILS THE TEST if
a SEC lookup is attempted for an ineligible venue.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.providers.free_real_provider import (
    REASON_EXCHANGE_MISSING_IN_STATE,
    REASON_NON_US_NO_SEC_MAPPING,
    REASON_SEC_COVERED,
)
from app.workflows.company_analysis import _build_data_coverage

_COMPANY_ID = "11111111-1111-1111-1111-111111111111"

BOEING_LEGAL_NAME = "BOEING CO"


class _SecNetworkTouched(AssertionError):
    """Raised when a test reaches the SEC index — the gate came too late."""


# ---------------------------------------------------------------------------
# Workflow harness — runs the REAL graph with services mocked but the provider
# stack real, so the SEC gate is genuinely exercised.
# ---------------------------------------------------------------------------


def _company(ticker: str, exchange: str, name: str | None = None) -> MagicMock:
    company = MagicMock()
    company.id = uuid.UUID(_COMPANY_ID)
    company.name = name or ticker
    company.ticker = ticker
    company.exchange = exchange
    company.sector = None
    company.description = None
    return company


def _svc_mocks(stack: Any) -> dict[str, Any]:
    run = MagicMock(id=uuid.uuid4())
    step = MagicMock(id=uuid.uuid4())
    report = MagicMock(id=uuid.uuid4(), slug="s")
    source = MagicMock(id=uuid.uuid4())
    citation = MagicMock(id=uuid.uuid4())
    return {"run": run, "step": step, "report": report, "source": source, "citation": citation}


async def _run_workflow(ticker: str, exchange: str, *, forbid_sec_network: bool = True) -> dict:
    """
    Execute the real company-analysis workflow for (ticker, exchange).

    Only persistence services are mocked. FinancialDataService, FreeRealProvider
    and SecEdgarFundamentalsProvider are REAL, so the SEC eligibility gate runs
    exactly as it does in production.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.workflows.company_analysis import run_company_analysis

    company = _company(ticker, exchange)
    m = _svc_mocks(None)
    db = AsyncMock(spec=AsyncSession)

    def _no_network(*args: Any, **kwargs: Any) -> Any:
        raise _SecNetworkTouched(
            "An HTTP client was constructed. For an SEC-ineligible venue the "
            "gate must reject before any network call — reaching here means "
            "the exchange was lost and a ticker-only SEC lookup was attempted."
        )

    with (
        patch("app.workflows.company_analysis.agent_run_service") as run_svc,
        patch("app.workflows.company_analysis.company_service") as co_svc,
        patch("app.workflows.company_analysis.source_service") as src_svc,
        patch("app.workflows.company_analysis.citation_service") as cit_svc,
        patch("app.workflows.company_analysis.report_service") as rpt_svc,
    ):
        run_svc.create_agent_run = AsyncMock(return_value=m["run"])
        run_svc.create_agent_step = AsyncMock(return_value=m["step"])
        run_svc.complete_agent_step = AsyncMock()
        run_svc.complete_agent_run = AsyncMock()
        run_svc.fail_agent_step = AsyncMock()
        run_svc.fail_agent_run = AsyncMock()
        co_svc.get_company = AsyncMock(return_value=company)
        co_svc.get_company_by_ticker = AsyncMock(return_value=company)
        src_svc.get_or_create_source = AsyncMock(return_value=(m["source"], True))
        cit_svc.create_citation = AsyncMock(return_value=m["citation"])
        rpt_svc.create_draft_report = AsyncMock(return_value=m["report"])

        if forbid_sec_network:
            with patch("httpx.AsyncClient", _no_network):
                return await run_company_analysis(
                    db=db, company_id=_COMPANY_ID, provider_name="free_real"
                )
        return await run_company_analysis(
            db=db, company_id=_COMPANY_ID, provider_name="free_real"
        )


# ---------------------------------------------------------------------------
# 1. The seam itself: exchange must reach the workflow state
# ---------------------------------------------------------------------------


async def test_workflow_state_carries_exchange_when_called_with_company_id_only():
    """
    THE REGRESSION. Discovery calls the workflow with company_id only.

    node_load_company must echo the company's exchange into state, or the
    provider is asked for a ticker with no venue.
    """
    state = await _run_workflow("BA", "LSE")
    assert state.get("exchange") == "LSE", (
        "workflow state lost the exchange — this is the exact defect that made "
        "BA.LSE resolve to Boeing on staging"
    )


async def test_extract_signal_passes_exchange_to_the_workflow_runner():
    """The discovery seam must state the venue explicitly, not rely on a DB round-trip."""
    from app.services.discovery_signal_extractor import extract_signal

    captured: dict[str, Any] = {}

    async def _spy_runner(db: Any, **kwargs: Any) -> dict:
        captured.update(kwargs)
        return {"status": "completed", "ticker": "BA", "exchange": kwargs.get("exchange")}

    db = AsyncMock()
    with patch(
        "app.services.discovery_signal_extractor._ensure_company",
        new=AsyncMock(return_value=_company("BA", "LSE")),
    ):
        await extract_signal(
            db,
            ticker="BA",
            exchange="LSE",
            provider_name="free_real",
            run_analysis=_spy_runner,
        )

    assert captured.get("exchange") == "LSE"
    assert captured.get("ticker") == "BA"


# ---------------------------------------------------------------------------
# 2. BA/LSE must never become Boeing — end to end through the real providers
# ---------------------------------------------------------------------------


async def test_ba_lse_never_resolves_to_boeing_through_the_real_pipeline():
    """
    The headline regression, asserted on the real path.

    If the gate is bypassed, the SEC index answers "BA" with Boeing. The mocked
    client turns that into a test failure instead of silent contamination.
    """
    state = await _run_workflow("BA", "LSE")

    coverage = state.get("data_coverage") or {}
    assert coverage.get("exchange") == "LSE"
    assert coverage.get("sec_eligible") is False
    assert coverage.get("reason") == REASON_NON_US_NO_SEC_MAPPING
    assert coverage.get("requires_human_research") is True
    assert coverage.get("fundamentals_source") == "not_sourced"

    snapshot = state.get("free_real_snapshot") or {}
    identity = snapshot.get("identity") or {}
    legal_name = (identity.get("legal_name") or "").upper()
    assert BOEING_LEGAL_NAME not in legal_name, (
        f"BA.LSE picked up Boeing's identity ({legal_name!r}) — wrong-company "
        "data attribution, CLAUDE.md rule 6"
    )


@pytest.mark.parametrize(
    "ticker,exchange",
    [("BA", "LSE"), ("MC", "PA"), ("EL", "PA"), ("CFR", "SW"), ("RHM", "XETRA"), ("LDO", "MI")],
)
async def test_non_us_venues_never_touch_the_sec_index(ticker: str, exchange: str):
    """No SEC-ineligible venue may reach company_tickers.json, and none may crash."""
    state = await _run_workflow(ticker, exchange)
    assert state.get("status") != "failed", (
        f"{ticker}.{exchange} failed the run instead of degrading honestly"
    )
    coverage = state.get("data_coverage") or {}
    assert coverage.get("exchange") == exchange
    assert coverage.get("sec_eligible") is False
    assert coverage.get("requires_human_research") is True


async def test_non_us_candidate_reports_no_fabricated_financials():
    """Degradation must leave financials absent, never borrowed from another issuer."""
    state = await _run_workflow("RHM", "XETRA")
    snapshot = state.get("free_real_snapshot") or {}
    fundamentals = snapshot.get("fundamentals_summary") or {}
    for field in ("revenue_usd_m", "net_income_usd_m", "total_debt_usd_m"):
        assert fundamentals.get(field) in (None, 0, 0.0), (
            f"{field} was populated for an SEC-unreachable issuer"
        )


# ---------------------------------------------------------------------------
# 3. Fail-closed guard
# ---------------------------------------------------------------------------


def test_unresolved_exchange_is_not_treated_as_sec_eligible():
    """
    is_sec_eligible(None) stays True for legacy callers, but an exchange we
    EXPECTED and lost must fail closed rather than default to a US lookup.
    """

    class _P:
        ticker = "BA"
        meta = MagicMock(provider_name="sec_edgar")

    class _F:
        datapoints = [object()]

    coverage = _build_data_coverage(
        exchange=None,
        profile=_P(),
        fundamentals=_F(),
        price_source=None,
        exchange_unresolved=True,
    )
    assert coverage["sec_eligible"] is False
    assert coverage["reason"] == REASON_EXCHANGE_MISSING_IN_STATE
    assert coverage["requires_human_research"] is True
    assert coverage["fundamentals_source"] == "not_sourced"


def test_legacy_absent_exchange_still_permits_sec_lookup():
    """Ticker-only callers (AAPL/MSFT/NVDA) must not regress to not_sourced."""

    class _P:
        ticker = "AAPL"
        meta = MagicMock(provider_name="sec_edgar")

    class _F:
        datapoints = [object()]

    coverage = _build_data_coverage(
        exchange=None, profile=_P(), fundamentals=_F(), price_source="stooq"
    )
    assert coverage["sec_eligible"] is True
    assert coverage["reason"] == REASON_SEC_COVERED
    assert coverage["requires_human_research"] is False


# ---------------------------------------------------------------------------
# 4. US behavior preserved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exchange", ["US", "NYSE", "NASDAQ"])
async def test_us_venues_are_still_sec_eligible_end_to_end(exchange: str):
    """
    A US listing must still be allowed to reach SEC. Network is permitted here;
    the assertion is on eligibility, which is what the gate decides.
    """

    class _P:
        ticker = "AMAT"
        meta = MagicMock(provider_name="sec_edgar")

    class _F:
        datapoints = [object()]

    coverage = _build_data_coverage(
        exchange=exchange, profile=_P(), fundamentals=_F(), price_source="stooq"
    )
    assert coverage["sec_eligible"] is True
    assert coverage["reason"] == REASON_SEC_COVERED


async def test_sec_recent_filings_provider_is_exchange_aware():
    """
    Catalysts must not inherit another issuer's filings.

    A ticker-only CIK resolution here would attach Boeing's 8-Ks to BAE.
    """
    from app.integrations.providers.sec_recent_filings_provider import (
        SecRecentFilingsProvider,
    )

    provider = SecRecentFilingsProvider()

    def _no_network(*args: Any, **kwargs: Any) -> Any:
        raise _SecNetworkTouched("SEC submissions fetched for an ineligible venue")

    with patch("httpx.AsyncClient", _no_network):
        result = await provider.get_recent_events("BA", exchange="LSE")

    assert result.events == []
    assert any("CIK" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 5. Explicit mapping still enables a non-US issuer
# ---------------------------------------------------------------------------


async def test_verified_mapping_restores_sec_coverage_for_a_non_us_venue():
    from app.integrations import sec_issuer_registry
    from app.integrations.sec_issuer_registry import (
        MAPPING_FOREIGN_PRIVATE_ISSUER,
        SecIssuerMapping,
    )

    sec_issuer_registry.clear_mappings()
    try:
        sec_issuer_registry.register_mapping(
            SecIssuerMapping(
                ticker="BA",
                exchange="LSE",
                cik="0000123456",
                issuer_name="BAE Systems plc",
                mapping_type=MAPPING_FOREIGN_PRIVATE_ISSUER,
                source_url="https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000123456",
                verified_on="2026-07-21",
            )
        )

        class _P:
            ticker = "BA"
            meta = MagicMock(provider_name="sec_edgar")

        class _F:
            datapoints = [object()]

        coverage = _build_data_coverage(
            exchange="LSE", profile=_P(), fundamentals=_F(), price_source=None
        )
        assert coverage["has_explicit_cik_mapping"] is True
        assert coverage["requires_human_research"] is False
    finally:
        sec_issuer_registry.clear_mappings()
