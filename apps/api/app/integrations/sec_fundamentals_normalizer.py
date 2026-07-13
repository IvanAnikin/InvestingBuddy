"""
sec_fundamentals_normalizer — Phase 19.3.

Normalizes SEC EDGAR XBRL companyfacts into a structured set of financial
metrics that the company-analysis workflow can consume directly.

The base ``parse_company_facts`` (sec_edgar_fundamentals.py) extracts ten raw
us-gaap line items as loose FundamentalDataPoints. That was enough to prove the
SEC integration worked, but it left the free_real report saying
"No financial fundamentals sourced at this phase" because nothing mapped the
raw datapoints into the income-statement / cash-flow / balance-sheet fields the
FinancialDataAgent and ValuationGuardAgent look for.

This module fills that gap. It:
  1. Selects the latest annual (10-K / 20-F, fp=FY) value for each concept,
     with the prior fiscal year kept for year-over-year growth.
  2. Falls back to the latest quarterly (10-Q) value with a warning when no
     annual value exists.
  3. Derives margins, ROE, debt-to-equity, free cash flow and YoY growth only
     when the required inputs are present.
  4. Never fabricates values. EBITDA and market-cap are left missing (with a
     warning) when the underlying concepts are unavailable.

Design rules (Phase 19.3):
  - Pure functions — no network calls. Unit-tested with fixture JSON only.
  - Source tier is T2_regulator_or_gov for every raw statement item.
  - Derived metrics are marked C_inferred (computed, not reported).
  - Annual values are labelled annual — never mislabelled as TTM.
  - No BUY/SELL/HOLD/WATCH, price target, fair value or upside is produced.

Scaling:
  - Dollar amounts are scaled to millions (USD_m) to match the existing
    snapshot convention.
  - EPS is left per-share (USD).
  - Margins, ROE and YoY growth are expressed as percentages.
  - Debt-to-equity is expressed as a ratio (x).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.integrations.financial_data_provider import (
    DataQuality,
    FundamentalDataPoint,
    SourceTier,
)
from app.integrations.providers.sec_edgar_provider import _EDGAR_BASE_URL, _pad_cik

_COMPANY_FACTS_URL = f"{_EDGAR_BASE_URL}/api/xbrl/companyfacts/CIK{{cik}}.json"

# Annual and quarterly form types accepted for period selection.
_ANNUAL_FORMS = {"10-K", "20-F", "10-K/A", "20-F/A", "40-F", "40-F/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

# us-gaap concept aliases per financial line item (first match wins).
_REVENUE = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]
_GROSS_PROFIT = ["GrossProfit"]
_OPERATING_INCOME = ["OperatingIncomeLoss"]
_NET_INCOME = ["NetIncomeLoss", "ProfitLoss"]
_EPS_BASIC = ["EarningsPerShareBasic"]
_EPS_DILUTED = ["EarningsPerShareDiluted"]
_OPERATING_CASH_FLOW = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]
_CAPEX = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]
_TOTAL_ASSETS = ["Assets"]
_TOTAL_LIABILITIES = ["Liabilities"]
_SHAREHOLDERS_EQUITY = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
]
_CASH = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
]
_LONG_TERM_DEBT = ["LongTermDebt", "LongTermDebtNoncurrent"]
_SHORT_TERM_DEBT = ["DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"]
# dei concepts (shares outstanding lives under facts.dei, not us-gaap).
_SHARES_OUTSTANDING = ["EntityCommonStockSharesOutstanding"]

_MILLION = 1_000_000


# ---------------------------------------------------------------------------
# Concept selection
# ---------------------------------------------------------------------------


@dataclass
class _Metric:
    """A single selected concept value with its provenance."""

    value: float | None = None
    prior_value: float | None = None
    concept: str | None = None
    fy: int | None = None
    fp: str | None = None
    form: str | None = None
    filed: str | None = None
    accn: str | None = None
    end: str | None = None
    period_type: str | None = None  # "annual" | "quarterly"


def _fy_of(entry: dict) -> int | None:
    fy = entry.get("fy")
    if isinstance(fy, int):
        return fy
    end = entry.get("end") or ""
    if len(end) >= 4 and end[:4].isdigit():
        return int(end[:4])
    return None


def _select_metric(
    facts: dict[str, Any],
    concepts: list[str],
    unit_key: str,
    scale_to_millions: bool,
) -> _Metric:
    """
    Select the latest annual value for the first matching concept.

    Falls back to the latest quarterly value when no annual entry exists.
    Keeps the prior fiscal-year annual value for year-over-year growth.
    """
    for concept in concepts:
        concept_data = facts.get(concept)
        if not concept_data:
            continue
        units = concept_data.get("units", {})
        entries = units.get(unit_key, [])
        if not entries and unit_key == "USD/shares":
            entries = units.get("USD", [])
        if not entries:
            continue

        annual = [
            e
            for e in entries
            if e.get("form") in _ANNUAL_FORMS
            and e.get("fp", "FY") == "FY"
            and e.get("val") is not None
        ]
        period_type = "annual"
        selected_pool = annual
        if not annual:
            selected_pool = [
                e
                for e in entries
                if e.get("form") in _QUARTERLY_FORMS and e.get("val") is not None
            ]
            period_type = "quarterly"
        if not selected_pool:
            continue

        latest = max(selected_pool, key=lambda e: e.get("end", ""))
        raw_val = latest.get("val")
        value = float(raw_val) / _MILLION if scale_to_millions else float(raw_val)

        prior_value: float | None = None
        if period_type == "annual":
            latest_fy = _fy_of(latest)
            if latest_fy is not None:
                prior_entries = [
                    e for e in annual if _fy_of(e) == latest_fy - 1
                ]
                if prior_entries:
                    prior_raw = max(
                        prior_entries, key=lambda e: e.get("end", "")
                    ).get("val")
                    if prior_raw is not None:
                        prior_value = (
                            float(prior_raw) / _MILLION
                            if scale_to_millions
                            else float(prior_raw)
                        )

        return _Metric(
            value=round(value, 2),
            prior_value=round(prior_value, 2) if prior_value is not None else None,
            concept=concept,
            fy=_fy_of(latest),
            fp=latest.get("fp"),
            form=latest.get("form"),
            filed=latest.get("filed"),
            accn=latest.get("accn"),
            end=latest.get("end"),
            period_type=period_type,
        )

    return _Metric()


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    """Return numerator/denominator as a percentage, guarding zero/None."""
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100.0, 2)


def _yoy(current: float | None, prior: float | None) -> float | None:
    """Year-over-year growth percentage, guarding zero/None."""
    if current is None or prior in (None, 0):
        return None
    return round((current - prior) / abs(prior) * 100.0, 2)


# ---------------------------------------------------------------------------
# Normalized output
# ---------------------------------------------------------------------------


@dataclass
class NormalizedSecFinancials:
    """
    Normalized SEC EDGAR fundamentals for a single company.

    Dollar amounts are in millions (USD_m). Margins/ROE/growth are percentages.
    Any field may be None when the underlying SEC concept was unavailable —
    absence is recorded in ``warnings``, never fabricated.
    """

    ticker: str
    cik: str | None = None

    # Income statement
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_basic: float | None = None
    eps_diluted: float | None = None

    # Cash flow
    operating_cash_flow: float | None = None
    capital_expenditures: float | None = None
    free_cash_flow: float | None = None

    # Balance sheet
    total_assets: float | None = None
    total_liabilities: float | None = None
    shareholders_equity: float | None = None
    cash_and_equivalents: float | None = None
    short_term_debt: float | None = None
    long_term_debt: float | None = None
    total_debt: float | None = None
    shares_outstanding: float | None = None

    # Derived
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    debt_to_equity: float | None = None
    free_cash_flow_margin: float | None = None
    revenue_yoy_growth: float | None = None
    net_income_yoy_growth: float | None = None
    free_cash_flow_yoy_growth: float | None = None

    # EBITDA — never fabricated. Left None with a warning when D&A unavailable.
    ebitda: float | None = None

    # Metadata
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form_type: str | None = None
    filed_date: str | None = None
    accession_number: str | None = None
    period_basis: str = "unknown"  # "annual" | "quarterly" | "unknown"
    reporting_currency: str = "USD"
    source_url: str | None = None
    source_tier: str = SourceTier.T2_regulator_or_gov.value

    warnings: list[str] = field(default_factory=list)

    # ---- serialization -------------------------------------------------- #

    _DOLLAR_FIELDS = (
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "operating_cash_flow",
        "capital_expenditures",
        "free_cash_flow",
        "total_assets",
        "total_liabilities",
        "shareholders_equity",
        "cash_and_equivalents",
        "short_term_debt",
        "long_term_debt",
        "total_debt",
    )
    _PCT_FIELDS = (
        "gross_margin",
        "operating_margin",
        "net_margin",
        "return_on_equity",
        "free_cash_flow_margin",
        "revenue_yoy_growth",
        "net_income_yoy_growth",
        "free_cash_flow_yoy_growth",
    )
    _EPS_FIELDS = ("eps_basic", "eps_diluted")
    _DERIVED_FIELDS = _PCT_FIELDS + ("debt_to_equity", "free_cash_flow", "total_debt")

    def has_any_fundamentals(self) -> bool:
        """True when at least one income/balance/cash-flow value was extracted."""
        return any(
            getattr(self, f) is not None
            for f in self._DOLLAR_FIELDS + self._EPS_FIELDS
        )

    def to_dict(self) -> dict[str, Any]:
        """Plain dict of all normalized fields (including None) plus metadata."""
        keys = (
            self._DOLLAR_FIELDS
            + self._EPS_FIELDS
            + self._PCT_FIELDS
            + ("debt_to_equity", "shares_outstanding", "ebitda")
        )
        out: dict[str, Any] = {k: getattr(self, k) for k in keys}
        out.update(
            {
                "ticker": self.ticker,
                "cik": self.cik,
                "fiscal_year": self.fiscal_year,
                "fiscal_period": self.fiscal_period,
                "form_type": self.form_type,
                "filed_date": self.filed_date,
                "accession_number": self.accession_number,
                "period_basis": self.period_basis,
                "reporting_currency": self.reporting_currency,
                "source_tier": self.source_tier,
                "source_url": self.source_url,
                "warnings": list(self.warnings),
            }
        )
        return out

    def to_datapoints(self) -> list[FundamentalDataPoint]:
        """
        Emit FundamentalDataPoint envelopes for the extended + derived fields.

        Field names use the ``sec_edgar.<field>`` convention. Raw statement
        items are B_single_credible; derived metrics are C_inferred to signal
        that they were computed rather than reported. Metadata items carry the
        fiscal period so downstream consumers can label the data honestly.
        """
        source_name = (
            f"SEC EDGAR XBRL companyfacts — {self.ticker.upper()}"
            + (f" (CIK {self.cik})" if self.cik else "")
        )
        as_of = self.end_date_or_today()

        period_note = (
            f"{self.period_basis} data, "
            f"FY{self.fiscal_year} {self.fiscal_period or ''} "
            f"(form {self.form_type or '?'}, filed {self.filed_date or '?'}). "
            "Source tier T2_regulator_or_gov."
        ).strip()

        dps: list[FundamentalDataPoint] = []

        def _add(
            field_name: str,
            value: Any,
            unit: str | None,
            quality: DataQuality,
            note: str,
        ) -> None:
            if value is None:
                return
            dps.append(
                FundamentalDataPoint(
                    field_name=f"sec_edgar.{field_name}",
                    value=value,
                    unit=unit,
                    as_of=as_of,
                    currency=self.reporting_currency
                    if field_name in self._DOLLAR_FIELDS
                    else None,
                    source_tier=SourceTier.T2_regulator_or_gov,
                    source_name=source_name,
                    source_url=self.source_url,
                    data_quality=quality,
                    note=note,
                )
            )

        # Extended raw statement items (base 10 come from parse_company_facts).
        for name in ("gross_profit", "operating_income", "capital_expenditures",
                     "cash_and_equivalents"):
            _add(name, getattr(self, name), "USD_m",
                 DataQuality.B_single_credible, period_note)

        # Derived dollar aggregates.
        _add("free_cash_flow", self.free_cash_flow, "USD_m", DataQuality.C_inferred,
             "Derived: operating_cash_flow - capital_expenditures. " + period_note)
        _add("total_debt", self.total_debt, "USD_m", DataQuality.C_inferred,
             "Derived: short_term_debt + long_term_debt. " + period_note)

        # Derived ratios / growth (percent).
        derived_pct = {
            "gross_margin": "Derived: gross_profit / revenue.",
            "operating_margin": "Derived: operating_income / revenue.",
            "net_margin": "Derived: net_income / revenue.",
            "return_on_equity": "Derived: net_income / shareholders_equity.",
            "free_cash_flow_margin": "Derived: free_cash_flow / revenue.",
            "revenue_yoy_growth": "Derived: revenue vs prior fiscal year.",
            "net_income_yoy_growth": "Derived: net_income vs prior fiscal year.",
            "free_cash_flow_yoy_growth": "Derived: free_cash_flow vs prior fiscal year.",
        }
        for name, desc in derived_pct.items():
            _add(name, getattr(self, name), "%", DataQuality.C_inferred,
                 f"{desc} {period_note}")

        _add("debt_to_equity", self.debt_to_equity, "x", DataQuality.C_inferred,
             "Derived: total_debt / shareholders_equity. " + period_note)

        if self.shares_outstanding is not None:
            _add("shares_outstanding", self.shares_outstanding, "M shares",
                 DataQuality.B_single_credible, period_note)

        # Filing metadata — provenance so consumers can label period honestly.
        for name, value in (
            ("fiscal_year", self.fiscal_year),
            ("fiscal_period", self.fiscal_period),
            ("form_type", self.form_type),
            ("filed_date", self.filed_date),
            ("accession_number", self.accession_number),
            ("period_basis", self.period_basis),
        ):
            _add(name, value, None, DataQuality.B_single_credible,
                 "SEC EDGAR filing metadata.")

        return dps

    def end_date_or_today(self) -> str:
        if self.filed_date:
            return self.filed_date
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def normalize_company_facts(
    data: dict,
    ticker: str,
    cik: str | None = None,
) -> NormalizedSecFinancials:
    """
    Normalize a SEC EDGAR companyfacts JSON payload.

    Pure function (no network) so it can be unit-tested with fixture JSON.

    Args:
        data:   Parsed companyfacts JSON dict.
        ticker: Ticker symbol (for labelling only).
        cik:    Optional CIK (for source URL). Falls back to data["cik"].

    Returns:
        NormalizedSecFinancials — always returned, never raises. Missing
        concepts, quarterly fallbacks and EBITDA absence are recorded in
        ``.warnings``.
    """
    warnings: list[str] = []
    facts = data.get("facts", {})
    us_gaap: dict[str, Any] = facts.get("us-gaap", {})
    dei: dict[str, Any] = facts.get("dei", {})

    resolved_cik = str(cik) if cik else (str(data["cik"]) if data.get("cik") else None)
    source_url = (
        _COMPANY_FACTS_URL.format(cik=_pad_cik(resolved_cik)) if resolved_cik else None
    )

    result = NormalizedSecFinancials(
        ticker=ticker.upper(),
        cik=resolved_cik,
        source_url=source_url,
    )

    if not us_gaap:
        result.warnings.append(
            f"SEC EDGAR: no us-gaap facts present for {ticker.upper()}. "
            "Company may not file XBRL data, or the payload was empty."
        )
        return result

    # ── Extract concepts ─────────────────────────────────────────────────
    revenue = _select_metric(us_gaap, _REVENUE, "USD", True)
    gross_profit = _select_metric(us_gaap, _GROSS_PROFIT, "USD", True)
    operating_income = _select_metric(us_gaap, _OPERATING_INCOME, "USD", True)
    net_income = _select_metric(us_gaap, _NET_INCOME, "USD", True)
    eps_basic = _select_metric(us_gaap, _EPS_BASIC, "USD/shares", False)
    eps_diluted = _select_metric(us_gaap, _EPS_DILUTED, "USD/shares", False)
    ocf = _select_metric(us_gaap, _OPERATING_CASH_FLOW, "USD", True)
    capex = _select_metric(us_gaap, _CAPEX, "USD", True)
    total_assets = _select_metric(us_gaap, _TOTAL_ASSETS, "USD", True)
    total_liabilities = _select_metric(us_gaap, _TOTAL_LIABILITIES, "USD", True)
    equity = _select_metric(us_gaap, _SHAREHOLDERS_EQUITY, "USD", True)
    cash = _select_metric(us_gaap, _CASH, "USD", True)
    ltd = _select_metric(us_gaap, _LONG_TERM_DEBT, "USD", True)
    std = _select_metric(us_gaap, _SHORT_TERM_DEBT, "USD", True)
    shares = _select_metric(dei, _SHARES_OUTSTANDING, "shares", True)

    result.revenue = revenue.value
    result.gross_profit = gross_profit.value
    result.operating_income = operating_income.value
    result.net_income = net_income.value
    result.eps_basic = eps_basic.value
    result.eps_diluted = eps_diluted.value
    result.operating_cash_flow = ocf.value
    result.capital_expenditures = capex.value
    result.total_assets = total_assets.value
    result.total_liabilities = total_liabilities.value
    result.shareholders_equity = equity.value
    result.cash_and_equivalents = cash.value
    result.short_term_debt = std.value
    result.long_term_debt = ltd.value
    result.shares_outstanding = shares.value

    # ── Headline period (prefer revenue, then net income) ────────────────
    headline = next(
        (m for m in (revenue, net_income, ocf, total_assets) if m.value is not None),
        None,
    )
    if headline is not None:
        result.fiscal_year = headline.fy
        result.fiscal_period = headline.fp
        result.form_type = headline.form
        result.filed_date = headline.filed
        result.accession_number = headline.accn
        result.period_basis = headline.period_type or "unknown"
        if headline.period_type == "quarterly":
            warnings.append(
                f"SEC EDGAR: no annual (10-K) filing found for {ticker.upper()}; "
                "used latest quarterly (10-Q) values. Annual figures preferred."
            )

    # ── Derived: total debt ──────────────────────────────────────────────
    debt_parts = [d for d in (result.short_term_debt, result.long_term_debt) if d is not None]
    if debt_parts:
        result.total_debt = round(sum(debt_parts), 2)
        if len(debt_parts) == 1:
            warnings.append(
                "SEC EDGAR: total_debt is a partial sum — only one of "
                "short_term_debt / long_term_debt was available."
            )
    else:
        warnings.append(
            "SEC EDGAR: total_debt unavailable — no short-term or long-term "
            "debt concepts found."
        )

    # ── Derived: free cash flow ──────────────────────────────────────────
    if result.operating_cash_flow is not None and result.capital_expenditures is not None:
        result.free_cash_flow = round(
            result.operating_cash_flow - result.capital_expenditures, 2
        )
    else:
        warnings.append(
            "SEC EDGAR: free_cash_flow not derived — operating_cash_flow and/or "
            "capital_expenditures unavailable."
        )

    # ── Derived: margins ─────────────────────────────────────────────────
    result.gross_margin = _pct(result.gross_profit, result.revenue)
    result.operating_margin = _pct(result.operating_income, result.revenue)
    result.net_margin = _pct(result.net_income, result.revenue)
    result.free_cash_flow_margin = _pct(result.free_cash_flow, result.revenue)
    result.return_on_equity = _pct(result.net_income, result.shareholders_equity)

    if result.total_debt is not None and result.shareholders_equity not in (None, 0):
        result.debt_to_equity = round(result.total_debt / result.shareholders_equity, 3)

    # ── Derived: YoY growth (annual only) ────────────────────────────────
    if result.period_basis == "annual":
        result.revenue_yoy_growth = _yoy(revenue.value, revenue.prior_value)
        result.net_income_yoy_growth = _yoy(net_income.value, net_income.prior_value)
        if (
            ocf.value is not None
            and capex.value is not None
            and ocf.prior_value is not None
            and capex.prior_value is not None
        ):
            fcf_prior = ocf.prior_value - capex.prior_value
            result.free_cash_flow_yoy_growth = _yoy(result.free_cash_flow, fcf_prior)
    else:
        warnings.append(
            "SEC EDGAR: year-over-year growth not computed — quarterly fallback "
            "does not provide comparable prior-year annual figures."
        )

    # ── EBITDA is never fabricated ───────────────────────────────────────
    warnings.append(
        "SEC EDGAR: EBITDA not derived — depreciation & amortization concepts "
        "were not extracted at this phase. Not fabricated."
    )

    # ── Warn on missing key concepts ─────────────────────────────────────
    if result.revenue is None:
        warnings.append("SEC EDGAR: revenue concept not found in us-gaap facts.")
    if result.net_income is None:
        warnings.append("SEC EDGAR: net_income concept not found in us-gaap facts.")
    if result.shares_outstanding is None:
        warnings.append(
            "SEC EDGAR: shares outstanding unavailable (dei concept missing) — "
            "market capitalization cannot be computed."
        )

    result.warnings = warnings
    return result
