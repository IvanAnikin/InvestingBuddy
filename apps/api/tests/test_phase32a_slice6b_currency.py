"""
Phase 32A Slice 6B — Fix C3: currency/unit contradiction (LSE instrument).

Root cause: THREE independent, uncoordinated currency sources
(eodhd_provider.get_price_history, eodhd_price_only_provider.get_price_history,
llm_provider's narrative prompt) plus zero GBX/pence handling anywhere for
LSE-listed tickers (EODHD /eod quotes LSE prices in pence, not pounds).

These tests pin the fix:
  1. eodhd_provider / eodhd_price_only_provider never hardcode "USD" — currency
     is honestly None when genuinely unknown.
  2. exchange_registry.price_quote_currency_for_exchange resolves a REAL,
     non-fabricated quote currency (LSE -> "GBX", distinct from the GBP
     reporting currency; NASDAQ -> "USD"; unknown exchange -> None).
  3. build_company_snapshot (snapshot_builder.py) never fabricates USD or GBP
     for an LSE instrument — it resolves GBX from the registry, or falls back
     to the honest "not_sourced" marker, never a guessed code.
  4. The LLM-facing prompt context (llm_provider.py) is consistent with the
     readable snapshot — never silently omits currency and never lets the
     model infer/conflate the price currency with the unrelated reporting
     currency.

All tests run OFFLINE — no network, no real Azure credentials.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    PriceHistoryData,
    PricePoint,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceTier,
)
from app.integrations.llm_provider import AzureOpenAIResearchLLMClient, ResearchSectionsOutput
from app.services.exchange_registry import (
    currency_for_exchange,
    price_quote_currency_for_exchange,
)
from app.workflows.snapshot_builder import build_company_snapshot

_RETRIEVED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _meta(provider_name: str = "eodhd") -> ProviderResponseMetadata:
    return ProviderResponseMetadata(
        provider_name=provider_name,
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=_RETRIEVED_AT,
        is_mock=False,
        status=ProviderStatus.ok,
    )


def _brby_profile() -> CompanyProfileData:
    """Burberry Group plc (BRBY.LSE) — real reporting currency is GBP."""
    return CompanyProfileData(
        ticker="BRBY",
        exchange="LSE",
        legal_name="Burberry Group plc",
        country_domicile="United Kingdom",
        reporting_currency="GBP",
        fiscal_year_end="March",
        sector="Consumer Discretionary",
        industry="Apparel, Accessories & Luxury Goods",
        description="British luxury fashion house.",
        website=None,
        isin=None,
        lei=None,
        ipo_date=None,
        source_url=None,
        data_quality=DataQuality.B_single_credible,
        meta=_meta(),
    )


def _brby_prices(currency: str | None = None) -> PriceHistoryData:
    """LSE /eod prices — the provider itself is honestly None (pence, GBX, unconfirmed)."""
    pts = [
        PricePoint(date="2026-07-30", open=1150.0, high=1170.0, low=1140.0, close=1164.5, volume=1200000),
    ]
    return PriceHistoryData(
        ticker="BRBY",
        exchange="LSE",
        currency=currency,
        price_points=pts,
        source_url=None,
        data_quality=DataQuality.B_single_credible,
        meta=_meta(),
    )


# ---------------------------------------------------------------------------
# 1. exchange_registry — real, non-fabricated quote-currency resolution
# ---------------------------------------------------------------------------


def test_lse_price_quote_currency_is_gbx_not_gbp():
    """LSE quotes in pence (GBX) — distinct from the GBP reporting currency."""
    assert price_quote_currency_for_exchange("LSE") == "GBX"
    assert currency_for_exchange("LSE") == "GBP"
    assert price_quote_currency_for_exchange("LSE") != currency_for_exchange("LSE")


def test_nasdaq_price_quote_currency_is_usd():
    assert price_quote_currency_for_exchange("NASDAQ") == "USD"


def test_unknown_exchange_price_quote_currency_is_none_never_guessed():
    assert price_quote_currency_for_exchange("NOT_A_REAL_EXCHANGE") is None
    assert price_quote_currency_for_exchange(None) is None


# ---------------------------------------------------------------------------
# 2. Provider-level: no more hardcoded "USD"
# ---------------------------------------------------------------------------


def test_eodhd_price_only_provider_never_hardcodes_usd():
    import asyncio

    from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider

    with patch.dict("os.environ", {"EODHD_API_KEY": "test-key"}):
        provider = EodhdPriceOnlyProvider()
        with patch.object(provider, "_get_json", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [
                {"date": "2026-07-30", "open": 1150.0, "high": 1170.0, "low": 1140.0, "close": 1164.5, "volume": 1200000}
            ]
            prices = asyncio.run(provider.get_price_history("BRBY", "LSE"))

    assert prices.currency is None


# ---------------------------------------------------------------------------
# 3. build_company_snapshot — honest resolution, never a fabricated code
# ---------------------------------------------------------------------------


def test_lse_snapshot_price_currency_resolves_to_gbx():
    """
    The core BRBY regression: price_history_summary["currency"] must be GBX
    (a real, sourced convention), never USD, never GBP (the unrelated
    reporting currency).
    """
    snapshot = build_company_snapshot(profile=_brby_profile(), prices=_brby_prices())
    price_summary = snapshot["price_history_summary"]

    assert price_summary["currency"] == "GBX"
    assert price_summary["currency"] != "USD"
    # Distinct from — and does not equal — the issuer's reporting currency.
    assert snapshot["profile"]["reporting_currency"] == "GBP"
    assert price_summary["currency"] != snapshot["profile"]["reporting_currency"]


def test_unknown_exchange_snapshot_price_currency_is_not_sourced_never_fabricated():
    """When neither the provider nor the registry knows the quote currency,
    the snapshot must say so honestly — never guess USD or GBP."""
    profile = _brby_profile()
    profile = profile.model_copy(update={"exchange": "NOT_A_REAL_EXCHANGE"})
    prices = _brby_prices()
    prices = prices.model_copy(update={"exchange": "NOT_A_REAL_EXCHANGE"})

    snapshot = build_company_snapshot(profile=profile, prices=prices)
    currency = snapshot["price_history_summary"]["currency"]

    assert currency == "not_sourced"
    assert currency not in ("USD", "GBP")


def test_nasdaq_snapshot_price_currency_still_resolves_to_usd():
    """Regression guard: a genuinely USD-quoted instrument (e.g. AAPL/NASDAQ)
    must still show USD — sourced from the exchange registry now, not a blind
    per-provider hardcode."""
    profile = _brby_profile().model_copy(
        update={"ticker": "AAPL", "exchange": "NASDAQ", "reporting_currency": "USD"}
    )
    prices = _brby_prices().model_copy(update={"ticker": "AAPL", "exchange": "NASDAQ"})

    snapshot = build_company_snapshot(profile=profile, prices=prices)
    assert snapshot["price_history_summary"]["currency"] == "USD"


def test_explicit_provider_currency_always_wins_over_registry_inference():
    """When the provider DOES supply a real currency, it is never overridden."""
    snapshot = build_company_snapshot(profile=_brby_profile(), prices=_brby_prices(currency="GBX"))
    assert snapshot["price_history_summary"]["currency"] == "GBX"


# ---------------------------------------------------------------------------
# 4. llm_provider prompt context — consistent with the readable snapshot
# ---------------------------------------------------------------------------


def _fake_llm_output() -> ResearchSectionsOutput:
    return ResearchSectionsOutput(
        thesis_summary_draft="Draft summary.",
        business_overview_draft="Draft overview.",
        missing_information=[],
        self_critique_limitations="Draft only.",
    )


def _azure_client(monkeypatch) -> AzureOpenAIResearchLLMClient:
    from app.core.config import settings

    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://example.openai.azure.com/")
    monkeypatch.setattr(settings, "azure_openai_api_key", "fake-key")
    monkeypatch.setattr(settings, "azure_openai_deployment_name", "fake-deployment")
    monkeypatch.setattr(settings, "azure_openai_api_version", "2025-01-01-preview")
    # ``langchain-openai`` is deliberately NOT a CI dependency (pyproject.toml's
    # ``llm`` extra — "Not required for CI, default LLM_PROVIDER=mock uses no
    # Azure credentials"). AzureOpenAIResearchLLMClient.__init__ imports it
    # unconditionally, so stub the module rather than requiring the real
    # package: this test only exercises prompt-string assembly, never a real
    # LLM call (``_structured_llm`` is replaced by the caller right after).
    fake_module = types.ModuleType("langchain_openai")
    fake_module.AzureChatOpenAI = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_module)
    return AzureOpenAIResearchLLMClient()


@pytest.mark.asyncio
async def test_llm_prompt_includes_real_lse_price_currency(monkeypatch):
    client = _azure_client(monkeypatch)
    snapshot = build_company_snapshot(profile=_brby_profile(), prices=_brby_prices())

    captured: dict[str, str] = {}

    async def fake_ainvoke(prompt: str):
        captured["prompt"] = prompt
        return _fake_llm_output()

    monkeypatch.setattr(client, "_structured_llm", SimpleNamespace(ainvoke=fake_ainvoke))
    await client.generate_research_sections(snapshot, "{{COMPANY_CONTEXT}}")

    assert "Latest close: 1164.5 GBX" in captured["prompt"]
    # The reporting currency line stays separate and is never conflated with
    # the price-quote currency.
    assert "Reporting currency: GBP" in captured["prompt"]


@pytest.mark.asyncio
async def test_llm_prompt_states_currency_not_confirmed_when_unsourced(monkeypatch):
    client = _azure_client(monkeypatch)
    profile = _brby_profile().model_copy(update={"exchange": "NOT_A_REAL_EXCHANGE"})
    prices = _brby_prices().model_copy(update={"exchange": "NOT_A_REAL_EXCHANGE"})
    snapshot = build_company_snapshot(profile=profile, prices=prices)

    captured: dict[str, str] = {}

    async def fake_ainvoke(prompt: str):
        captured["prompt"] = prompt
        return _fake_llm_output()

    monkeypatch.setattr(client, "_structured_llm", SimpleNamespace(ainvoke=fake_ainvoke))
    await client.generate_research_sections(snapshot, "{{COMPANY_CONTEXT}}")

    assert "Latest close: 1164.5 (currency not confirmed)" in captured["prompt"]
    # Never silently let the model infer USD or GBP from the unrelated line.
    assert "Latest close: 1164.5 USD" not in captured["prompt"]
    assert "Latest close: 1164.5 GBP" not in captured["prompt"]
