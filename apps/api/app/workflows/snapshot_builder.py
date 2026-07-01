"""
snapshot_builder — Phase 6/13 utility.

Transforms raw provider data (CompanyProfileData, PriceHistoryData,
FundamentalsData) into:
  1. A structured company snapshot dict suitable for DB storage.
  2. A minimal schema-attempt dict that follows the real-asset equity report
     schema datapoint convention, ready for validate_real_asset_report().

Phase 13 adds fundamentals enrichment: when FundamentalsData is provided
(EODHD provider), snapshot_financials fields are populated with datapoint
wrappers, and the schema draft gains more filled sections.

The schema-attempt will still fail full validation (many required sections
are absent) but has meaningfully more data when fundamentals are available.

No LLM calls. No network calls. No database access. Pure data transformation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    FundamentalsData,
    PriceHistoryData,
    ProviderResponseMetadata,
)

# ---------------------------------------------------------------------------
# Datapoint builder
# ---------------------------------------------------------------------------

_TODAY = date.today().isoformat()


def _make_datapoint(
    value: Any,
    unit: str | None,
    as_of: str,
    source_tier: str,
    source_name: str,
    source_url: str | None,
    data_quality: str,
    note: str | None = None,
) -> dict:
    """Build a schema-compliant datapoint envelope dict."""
    dp: dict = {
        "value": value,
        "as_of": as_of,
        "source_tier": source_tier,
        "source_name": source_name,
        "data_quality": data_quality,
    }
    if unit is not None:
        dp["unit"] = unit
    else:
        dp["unit"] = None
    if source_url is not None:
        dp["source_url"] = source_url
    else:
        dp["source_url"] = None
    if note is not None:
        dp["note"] = note
    else:
        dp["note"] = None
    return dp


def _provider_note(meta: ProviderResponseMetadata) -> str:
    tier = meta.source_tier if isinstance(meta.source_tier, str) else meta.source_tier.value
    base = f"Data from {meta.provider_name} (tier {tier})"
    if meta.is_mock:
        base += " — MOCK DATA: not real financial data, not investment advice"
    return base


# ---------------------------------------------------------------------------
# Company snapshot
# ---------------------------------------------------------------------------


def build_company_snapshot(
    profile: CompanyProfileData,
    prices: PriceHistoryData | None,
    fundamentals: FundamentalsData | None = None,
) -> dict:
    """
    Build a structured company snapshot from provider data.

    Returns a dict capturing:
    - company identity
    - provider metadata + source tier
    - retrieved timestamp
    - basic profile fields (with explicit None for unavailable)
    - price history summary (if prices provided)
    - list of explicitly missing fields
    - mock/live flag
    - no investment recommendation
    """
    meta = profile.meta
    retrieved_at = meta.retrieved_at.isoformat() if meta.retrieved_at else None
    tier_value = meta.source_tier if isinstance(meta.source_tier, str) else meta.source_tier.value

    missing_fields: list[str] = []

    def _field(val: Any, name: str) -> Any:
        if val is None:
            missing_fields.append(name)
        return val

    # Build price history summary if available
    price_summary: dict | None = None
    if prices and prices.price_points:
        pts = prices.price_points
        dates = [p.date for p in pts]
        closes = [p.close for p in pts]
        price_summary = {
            "available": True,
            "currency": prices.currency,
            "data_points_count": len(pts),
            "date_range": {"start": min(dates), "end": max(dates)},
            "latest_close": closes[-1] if closes else None,
            "price_data_quality": prices.data_quality
            if isinstance(prices.data_quality, str)
            else prices.data_quality.value,
            "provider_name": prices.meta.provider_name,
        }
    else:
        price_summary = {"available": False, "reason": "price_history not fetched or empty"}
        missing_fields.append("price_history")

    # Build fundamentals summary when EODHD fundamentals are available
    fundamentals_summary: dict | None = None
    if fundamentals and fundamentals.datapoints:
        dp_by_field = {dp.field_name: dp for dp in fundamentals.datapoints}
        fundamentals_summary = _build_fundamentals_summary(dp_by_field, missing_fields)

    snapshot = {
        "company_identity": {
            "ticker": profile.ticker,
            "exchange": _field(profile.exchange, "identity.exchange"),
            "legal_name": profile.legal_name,
            "country_domicile": _field(profile.country_domicile, "identity.country_domicile"),
            "isin": _field(profile.isin, "identity.isin"),
            "lei": _field(profile.lei, "identity.lei"),
        },
        "provider_metadata": {
            "provider_name": meta.provider_name,
            "source_tier": tier_value,
            "retrieved_at": retrieved_at,
            "is_mock": meta.is_mock,
            "note": meta.note,
        },
        "source_tier": tier_value,
        "retrieved_at": retrieved_at,
        "is_mock": meta.is_mock,
        "profile": {
            "reporting_currency": _field(profile.reporting_currency, "profile.reporting_currency"),
            "fiscal_year_end": _field(profile.fiscal_year_end, "profile.fiscal_year_end"),
            "sector": _field(profile.sector, "profile.sector"),
            "industry": _field(profile.industry, "profile.industry"),
            "website": _field(profile.website, "profile.website"),
            "ipo_date": _field(profile.ipo_date, "profile.ipo_date"),
            "description": _field(profile.description, "profile.description"),
            "data_quality": profile.data_quality
            if isinstance(profile.data_quality, str)
            else profile.data_quality.value,
        },
        "price_history_summary": price_summary,
        "fundamentals_summary": fundamentals_summary,
        "missing_fields": missing_fields,
        "investment_recommendation": None,
        "snapshot_generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return snapshot


def _build_fundamentals_summary(
    dp_by_field: dict,
    missing_fields: list[str],
) -> dict:
    """
    Build a condensed fundamentals summary dict from EODHD datapoints.

    Only extracts key financial snapshot fields. Each present field is noted
    as available; absent fields are added to missing_fields.
    """

    def _get(field_name: str, missing_label: str) -> Any:
        dp = dp_by_field.get(field_name)
        if dp is None:
            missing_fields.append(missing_label)
            return None
        return dp.value

    summary: dict[str, Any] = {
        "market_cap_mln": _get("highlights.market_cap_mln", "fundamentals.market_cap_mln"),
        "enterprise_value_mln": _get("valuation.enterprise_value_mln", "fundamentals.enterprise_value_mln"), # noqa: E501
        "ebitda_mln": _get("highlights.ebitda", "fundamentals.ebitda_mln"),
        "revenue_ttm_mln": _get("highlights.revenue_ttm_mln", "fundamentals.revenue_ttm_mln"),
        "ev_ebitda_x": _get("valuation.ev_ebitda", "fundamentals.ev_ebitda_x"),
        "pe_ratio": _get("highlights.pe_ratio", "fundamentals.pe_ratio"),
        "profit_margin": _get("highlights.profit_margin", "fundamentals.profit_margin"),
        "operating_margin_ttm": _get("highlights.operating_margin_ttm", "fundamentals.operating_margin_ttm"), # noqa: E501
        "return_on_equity_ttm": _get("highlights.return_on_equity_ttm", "fundamentals.return_on_equity_ttm"), # noqa: E501
        "shares_outstanding_mln": _get("shares.outstanding_mln", "fundamentals.shares_outstanding_mln"), # noqa: E501
        "beta": _get("technicals.beta", "fundamentals.beta"),
        "52_week_high": _get("technicals.52_week_high", "fundamentals.52_week_high"),
        "52_week_low": _get("technicals.52_week_low", "fundamentals.52_week_low"),
        "source_tier": "T5_api_aggregator",
        "data_quality": "B_single_credible",
        "note": (
            "Fundamentals from EODHD (T5_api_aggregator). "
            "Do not promote to T1/T2. "
            "Values are in USD_m where noted; verify currency for non-USD companies."
        ),
    }
    # Remove None values from summary (they were already added to missing_fields)
    return {k: v for k, v in summary.items() if v is not None or k in ("source_tier", "data_quality", "note")} # noqa: E501


# ---------------------------------------------------------------------------
# Schema draft (minimal datapoint-wrapped report attempt)
# ---------------------------------------------------------------------------


def build_schema_draft(
    report_id: str,
    snapshot: dict,
    profile: CompanyProfileData,
    prices: PriceHistoryData | None,
    fundamentals: FundamentalsData | None = None,
) -> dict:
    """
    Build a minimal schema-attempt dict using the real-asset equity report contract.

    Populates report_meta and identity using provider data with proper datapoint
    wrappers. All other required sections are absent — the draft will fail full
    schema validation, which is expected at this phase.

    The caller validates with validate_real_asset_report() and stores the result.
    """
    meta = profile.meta
    tier_value = meta.source_tier if isinstance(meta.source_tier, str) else meta.source_tier.value
    dq_value = (
        profile.data_quality
        if isinstance(profile.data_quality, str)
        else profile.data_quality.value
    )
    retrieved_date = (
        meta.retrieved_at.strftime("%Y-%m-%d") if meta.retrieved_at else _TODAY
    )
    source_name = f"{meta.provider_name} company profile"
    source_url = profile.source_url

    provider_note = _provider_note(meta)

    def _dp(value: Any, unit: str | None = None) -> dict:
        return _make_datapoint(
            value=value,
            unit=unit,
            as_of=retrieved_date,
            source_tier=tier_value,
            source_name=source_name,
            source_url=source_url,
            data_quality=dq_value,
            note=provider_note if meta.is_mock else None,
        )

    ticker = profile.ticker
    exchange = profile.exchange or "UNKNOWN"
    legal_name = profile.legal_name

    # Determine conviction — no analysis yet, always WATCHLIST at snapshot stage
    conviction = "WATCHLIST"

    draft: dict = {
        "report_meta": {
            "schema_version": "1.0.0",
            "report_id": report_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_emerged_from": (
                f"Provider data snapshot for {ticker} via {meta.provider_name}. "
                "No LLM analysis yet — Phase 6 snapshot only."
            ),
            "core_target_profile": (
                "PENDING: no LLM thesis yet. "
                f"Snapshot covers company identity and profile data for {legal_name} ({ticker})."
            ),
            "theme_tags": ["energy_transition"],
            "conviction": conviction,
        },
        "identity": {
            "legal_name": _dp(legal_name),
            "ticker": _dp(ticker),
            "exchange": _dp(exchange),
            "country_domicile": _dp(profile.country_domicile or "UNKNOWN"),
        },
    }

    # Add price snapshot to draft if available
    if prices and prices.price_points:
        price_meta = prices.meta
        price_tier = (
            price_meta.source_tier
            if isinstance(price_meta.source_tier, str)
            else price_meta.source_tier.value
        )
        price_dq = (
            prices.data_quality
            if isinstance(prices.data_quality, str)
            else prices.data_quality.value
        )
        latest = prices.price_points[-1]
        price_note = _provider_note(price_meta)
        draft["_phase6_price_snapshot"] = {
            "latest_close": _make_datapoint(
                value=latest.close,
                unit=prices.currency,
                as_of=latest.date,
                source_tier=price_tier,
                source_name=f"{price_meta.provider_name} OHLCV",
                source_url=prices.source_url,
                data_quality=price_dq,
                note=price_note if price_meta.is_mock else None,
            ),
            "data_points_count": len(prices.price_points),
            "currency": prices.currency,
        }

    # Phase 13: populate snapshot_financials from EODHD fundamentals
    if fundamentals and fundamentals.datapoints:
        dp_by_field = {dp.field_name: dp for dp in fundamentals.datapoints}
        fund_meta = fundamentals.meta
        fund_tier = (
            fund_meta.source_tier
            if isinstance(fund_meta.source_tier, str)
            else fund_meta.source_tier.value
        )
        fund_source_name = f"EODHD fundamentals — {ticker}.{exchange}"
        fund_source_url = f"https://eodhd.com/financial-apis/fundamental-api/?s={ticker}.{exchange}"

        def _fund_dp(field_name: str, unit: str | None, note: str | None = None) -> dict | None:
            dp = dp_by_field.get(field_name)
            if dp is None or dp.value is None:
                return None
            return _make_datapoint(
                value=dp.value,
                unit=unit,
                as_of=dp.as_of or retrieved_date,
                source_tier=fund_tier,
                source_name=fund_source_name,
                source_url=fund_source_url,
                data_quality=dp.data_quality
                if isinstance(dp.data_quality, str)
                else dp.data_quality.value,
                note=note,
            )

        snapshot_financials: dict = {}
        _fields_map = [
            ("highlights.market_cap_mln", "market_cap_usd_m", "USD_m",
             "Native currency — verify FX conversion. eodhd_mapping.json: snapshot_financials.market_cap_usd_m"), # noqa: E501
            ("valuation.enterprise_value_mln", "enterprise_value_usd_m", "USD_m", None),
            ("highlights.ebitda", "ebitda_ttm_usd_m", "USD_m", None),
            ("highlights.revenue_ttm_mln", "revenue_ttm_usd_m", "USD_m", None),
            ("valuation.ev_ebitda", "ev_ebitda_x", "x", None),
            ("shares.outstanding_mln", "shares_out_m", "M shares", None),
            ("shares.percent_insiders", "free_float_pct", "%",
             "Computed as 100 - percent_insiders. Approximate only."),
        ]
        for src_field, dst_field, unit, note in _fields_map:
            dp_val = _fund_dp(src_field, unit, note)
            if dp_val is not None:
                snapshot_financials[dst_field] = dp_val

        if snapshot_financials:
            draft["snapshot_financials"] = snapshot_financials
            draft["_phase13_fundamentals_available"] = True

    return draft


# ---------------------------------------------------------------------------
# Citation field descriptors for provider data items
# ---------------------------------------------------------------------------


def get_profile_citation_fields(profile: CompanyProfileData) -> list[dict]:
    """
    Return a list of citation descriptor dicts for each field retrieved from
    the provider's company profile.

    Each dict carries: field_path, claim_text, source_tier, data_quality,
    source_quote, retrieved_at — ready to populate CitationCreate.
    """
    meta = profile.meta
    tier_value = meta.source_tier if isinstance(meta.source_tier, str) else meta.source_tier.value
    dq_value = (
        profile.data_quality
        if isinstance(profile.data_quality, str)
        else profile.data_quality.value
    )
    retrieved_at = meta.retrieved_at

    def _c(field_path: str, claim_text: str, quote: str) -> dict:
        return {
            "field_path": field_path,
            "claim_text": claim_text,
            "source_quote": quote,
            "source_tier": tier_value,
            "data_quality": dq_value,
            "retrieved_at": retrieved_at,
        }

    citations = [
        _c("identity.legal_name", "legal_name", profile.legal_name),
        _c("identity.ticker", "ticker", profile.ticker),
    ]
    if profile.exchange:
        citations.append(_c("identity.exchange", "exchange", profile.exchange))
    if profile.country_domicile:
        citations.append(
            _c("identity.country_domicile", "country_domicile", profile.country_domicile)
        )
    if profile.reporting_currency:
        citations.append(
            _c("profile.reporting_currency", "reporting_currency", profile.reporting_currency)
        )
    if profile.sector:
        citations.append(_c("profile.sector", "sector", profile.sector))
    if profile.industry:
        citations.append(_c("profile.industry", "industry", profile.industry))
    return citations


def get_price_citation_fields(prices: PriceHistoryData) -> list[dict]:
    """
    Return citation descriptors for the price history data.
    """
    meta = prices.meta
    tier_value = meta.source_tier if isinstance(meta.source_tier, str) else meta.source_tier.value
    dq_value = (
        prices.data_quality
        if isinstance(prices.data_quality, str)
        else prices.data_quality.value
    )
    retrieved_at = meta.retrieved_at

    if not prices.price_points:
        return []

    latest = prices.price_points[-1]
    return [
        {
            "field_path": "price_history.latest_close",
            "claim_text": "latest_close",
            "source_quote": (
                f"Latest close {latest.close} {prices.currency} on {latest.date} "
                f"from {meta.provider_name}"
            ),
            "source_tier": tier_value,
            "data_quality": dq_value,
            "retrieved_at": retrieved_at,
        }
    ]
