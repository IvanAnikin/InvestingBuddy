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
  1. Selects the latest annual (10-K / 20-F, fp=FY) value per line item by
     freshness across ALL alias concepts — the entry with the latest fiscal
     year wins (filed date breaks ties), so a stale taxonomy tag (e.g. Apple's
     ``Revenues`` ending FY2018) can never shadow the current tag's FY2019+
     values. The prior fiscal year (same concept) is kept for YoY growth, and
     full-year periods are preferred over embedded Q4 slices for flow concepts.
  2. Falls back to the latest quarterly (10-Q) value with a warning when no
     annual value exists, and warns when the selected annual year looks stale.
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

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.integrations.financial_data_provider import (
    DataQuality,
    FundamentalDataPoint,
    SourceTier,
)
from app.integrations.providers.sec_edgar_provider import _EDGAR_BASE_URL, _pad_cik
from app.integrations.sec_total_debt import ALL_CURRENT, ALL_LONG_TERM, derive_total_debt

_COMPANY_FACTS_URL = f"{_EDGAR_BASE_URL}/api/xbrl/companyfacts/CIK{{cik}}.json"
_EDGAR_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
#: An EDGAR accession number; anything else never becomes part of a URL.
_ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}", re.ASCII)

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
#: Cash dividends actually paid, from the cash-flow statement. V3.18.1: a live report
#: said "no dividend information is available" about a filer whose 10-K states it on the
#: face of the cash-flow statement. It was never that the issuer did not disclose it.
_DIVIDENDS_PAID = [
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfDividends",
    "PaymentsOfOrdinaryDividends",
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
# Debt concepts live in ``sec_total_debt`` (ADR-067): they overlap in the taxonomy, so
# total debt is derived from one compatible combination at the reporting period, never
# by picking a "long" and a "short" alias independently and adding them.
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
    #: Days the selected flow spans; ``None`` for an instant. Distinguishes a 10-Q
    #: quarter (~91) from a year-to-date figure (~182, ~273) with the same period end.
    duration_days: int | None = None


def _fy_of(entry: dict) -> int | None:
    fy = entry.get("fy")
    if isinstance(fy, int):
        return fy
    end = entry.get("end") or ""
    if len(end) >= 4 and end[:4].isdigit():
        return int(end[:4])
    return None


def _freshness_key(entry: dict) -> tuple[int, str, str]:
    """
    Ranking key for choosing the freshest entry.

    Latest fiscal year wins; latest filed date is the tiebreaker (this is what
    lets an amended/refiled annual value supersede an earlier one for the same
    year); latest period-end date breaks any remaining tie.
    """
    return (_fy_of(entry) or 0, entry.get("filed") or "", entry.get("end") or "")


def _is_full_year_period(entry: dict) -> bool:
    """
    True when a flow entry spans roughly a full fiscal year.

    10-K filings frequently include a fourth-quarter (~3 month) period for
    income-statement / cash-flow concepts alongside the full-year period, both
    tagged form=10-K, fp=FY. Selecting the Q4 slice would understate revenue,
    net income, etc. Balance-sheet (instant) concepts have no ``start`` and are
    always treated as full-period.
    """
    start = entry.get("start")
    end = entry.get("end")
    if not start or not end:
        return True
    try:
        s = datetime.strptime(start[:10], "%Y-%m-%d")
        e = datetime.strptime(end[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return True
    return (e - s).days >= 300


def _duration_days(entry: dict) -> int | None:
    start, end = _parse_end(entry.get("start")), _parse_end(entry.get("end"))
    if start is None or end is None:
        return None
    return (end - start).days


def _is_single_quarter_period(entry: dict) -> bool:
    """True for an instant, or a flow spanning about one quarter (not year-to-date)."""
    start, end = _parse_end(entry.get("start")), _parse_end(entry.get("end"))
    if start is None or end is None:
        return True
    return (end - start).days <= 100


#: How far before the current period's end the prior fiscal year's end may fall: a
#: 52/53-week year moves the end by a few days, a changed fiscal year-end by more.
_PRIOR_YEAR_END_DAYS = (330, 400)


def _prior_year_entry(latest: dict, candidates: list[dict]) -> dict | None:
    """The prior fiscal year's value AS THE LATEST FILING PRESENTS IT.

    The prior year is identified by its PERIOD (an end date about one year before the
    current period's end), never by the XBRL ``fy`` field: ``fy`` is the fiscal year of the
    FILING, so the restated comparative inside this year's 10-K carries this year's ``fy``.
    Matching ``fy == latest_fy - 1`` therefore selected the value AS ORIGINALLY FILED a year
    earlier — before any recast for a discontinued operation, spin-off or restatement.
    Live QA campaign 2026-11: Honeywell FY2025 revenue (37,442m, Solstice discontinued)
    was compared with the ORIGINAL FY2024 figure (38,498m) instead of the recast 34,717m in
    the same 10-K, and the report said revenue fell 2.7% when it grew 7.9%.

    Preference: the comparative in the SAME filing as the current value (same accession),
    then the most recently filed value for that period.
    """
    latest_end = _parse_end(latest.get("end"))
    if latest_end is None:
        return None
    low, high = _PRIOR_YEAR_END_DAYS
    pool = []
    for entry in candidates:
        end = _parse_end(entry.get("end"))
        if end is None or not _is_full_year_period(entry):
            continue
        if low <= (latest_end - end).days <= high:
            pool.append(entry)
    if not pool:
        return None
    accn = latest.get("accn")
    return max(
        pool,
        key=lambda e: (
            bool(accn) and e.get("accn") == accn,
            e.get("filed") or "",
            e.get("end") or "",
        ),
    )


def _select_metric(
    facts: dict[str, Any],
    concepts: list[str],
    unit_key: str,
    scale_to_millions: bool,
) -> _Metric:
    """
    Select the latest annual value across ALL matching alias concepts.

    Freshness — not alias order — wins. This matters when a company switches
    XBRL taxonomy tags (e.g. Apple's stale ``Revenues`` tag stops at FY2018
    while the current ``RevenueFromContractWithCustomerExcludingAssessedTax``
    tag carries FY2019+). Picking the first alias that merely *has* data would
    return the stale FY2018 value; instead we gather annual candidates from
    every alias and choose the one with the latest fiscal year, tie-broken by
    filed date.

    Falls back to the latest quarterly value when no annual entry exists.
    Keeps the prior fiscal-year annual value (same concept) for YoY growth.
    """
    annual_candidates: list[tuple[str, dict]] = []
    quarterly_candidates: list[tuple[str, dict]] = []

    for concept in concepts:
        concept_data = facts.get(concept)
        if not concept_data:
            continue
        units = concept_data.get("units", {})
        entries = units.get(unit_key, [])
        if not entries and unit_key == "USD/shares":
            entries = units.get("USD", [])
        for e in entries:
            if e.get("val") is None:
                continue
            # An annual FORM spanning a full year IS an annual figure, whatever the
            # `fp` tag says. Requiring `fp == "FY"` meant a 10-K entry tagged "Q4" was
            # neither annual here nor quarterly (10-K is not a quarterly form), so it
            # was dropped entirely — and the field then fell through to the legacy
            # period-blind parser, which shipped a stale FY2022 figure with no warning.
            # Found by review. `_is_full_year_period` is the honest test: it measures
            # the period rather than trusting a tag filers set inconsistently.
            if e.get("form") in _ANNUAL_FORMS and (
                e.get("fp", "FY") == "FY" or _is_full_year_period(e)
            ):
                annual_candidates.append((concept, e))
            elif e.get("form") in _QUARTERLY_FORMS:
                quarterly_candidates.append((concept, e))

    period_type = "annual"
    pool = annual_candidates
    if not pool:
        pool = quarterly_candidates
        period_type = "quarterly"
    if not pool:
        return _Metric()

    if period_type == "annual":
        # Prefer full-year durations for flow concepts; fall back to the whole pool
        # if none look full-year (e.g. balance-sheet instants, or odd filings).
        ranked = [ce for ce in pool if _is_full_year_period(ce[1])] or pool
    else:
        # V3.18.1 — a 10-Q tags an income-statement flow twice with ONE period end: the
        # quarter and the year to date. Ranking on (fy, filed, end) cannot tell them
        # apart, so which one a concept got was arbitrary, and a quarter's revenue could
        # sit beside year-to-date net income in one bundle.
        #
        # So: take the LATEST period end first, and only among entries ending there,
        # prefer the single quarter. Preferring the quarter across the whole pool — the
        # first version of this — broke on the cash-flow statement, which a 10-Q
        # reports year-to-date ONLY: operating cash flow resolved to the Q1 entry and
        # was then withheld as "stale" beside a Q2 anchor, although Q2 year-to-date cash
        # flow was right there. Found by review. When only a year-to-date entry exists
        # at the latest end, it is kept and its duration is recorded, so a reader can
        # see that the period is a year-to-date one rather than infer a quarter.
        ends_in_pool = [end for _c, e in pool if (end := _parse_end(e.get("end"))) is not None]
        latest_end = max(ends_in_pool, default=None)
        at_latest = [
            ce
            for ce in pool
            if latest_end is not None and _parse_end(ce[1].get("end")) == latest_end
        ] or pool
        ranked = [ce for ce in at_latest if _is_single_quarter_period(ce[1])] or at_latest
    concept, latest = max(ranked, key=lambda ce: _freshness_key(ce[1]))

    raw_val = latest.get("val")
    value = float(raw_val) / _MILLION if scale_to_millions else float(raw_val)

    prior_value: float | None = None
    if period_type == "annual":
        prior = _prior_year_entry(latest, [e for c, e in annual_candidates if c == concept])
        if prior is not None and prior.get("val") is not None:
            prior_raw = prior["val"]
            prior_value = (
                float(prior_raw) / _MILLION if scale_to_millions else float(prior_raw)
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
        duration_days=_duration_days(latest),
    )


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
    dividends_paid: float | None = None

    # Balance sheet
    total_assets: float | None = None
    total_liabilities: float | None = None
    shareholders_equity: float | None = None
    cash_and_equivalents: float | None = None
    #: ADR-067 — debt due within one year: short-term borrowings + current maturities
    #: of long-term debt. ``None`` when not tagged for the period or not separable.
    short_term_debt: float | None = None
    #: ADR-067 — long-term debt due after one year (the noncurrent line).
    long_term_debt: float | None = None
    #: ADR-067 — total debt from ONE compatible combination of concepts at the reporting
    #: period (see ``debt_basis``); ``None`` when no long-term debt concept is tagged for
    #: the period, or when the total is known to be partial or double counted
    #: (``debt_basis["withheld_reason"]``). Never a partial sum. Equals
    #: ``short_term_debt + long_term_debt`` whenever both legs are set.
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

    #: Per-field period provenance: ``{field: {"fy", "end", "form", "concept"}}``.
    #:
    #: A single ``fiscal_year`` on this record is a HEADLINE, not a property of every
    #: value in it. Metrics are selected per concept, so they can legitimately come
    #: from different filings — and when they do, stamping one FY across all of them
    #: is how a FY2022 revenue got reported as FY2025. Each fact carries its own
    #: period here so a consumer can label it honestly.
    field_periods: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: V3.18.1 — statement metrics the filer HAS tagged, but not for the bundle's
    #: reporting period. ``{field: {"value", "end", "fy", "concept", "period_type",
    #: "reason"}}``. The value is kept here for audit and is on NO reporting field:
    #: a figure for another period is missing for this one, never carried into it.
    withheld_fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The latest 10-Q's own figures, when that 10-Q is newer than the annual bundle —
    #: single-quarter flows and quarter-end balances, in the primary-fact shape the
    #: report's current-period slots read. See ``latest_quarter_facts``.
    current_period_facts: list[dict[str, Any]] = field(default_factory=list)
    #: Restatements a 10-Q filed AFTER the annual bundle applies to earlier periods: a
    #: perimeter recast (discontinued operation, spin-off) or a share split. Each says
    #: the annual bundle is on a basis the issuer no longer reports. See
    #: ``basis_changes_since_annual``.
    basis_changes: list[dict[str, Any]] = field(default_factory=list)
    #: V3.18.1 — relationships between this bundle's own lines that do not hold.
    #: ``ConsistencyReport.to_dict()``; empty when nothing was found.
    consistency: dict[str, Any] = field(default_factory=dict)
    #: The one period end every reporting field in this bundle is for.
    reporting_period_end: str | None = None
    #: ADR-067 — how ``total_debt`` was built: the combination, the concepts and values
    #: used, and a note for every term not tagged for the period.
    #: ``sec_total_debt.TotalDebt.to_dict()``; empty when no total was published.
    debt_basis: dict[str, Any] = field(default_factory=dict)

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
            # The fact's OWN period end, falling back to the headline only when this
            # field has no recorded period. `as_of` on a financial fact means the
            # period it describes; using the filing date instead makes a FY2022 figure
            # look current because the FILING is current.
            own = (self.field_periods.get(field_name) or {}).get("end")
            dps.append(
                FundamentalDataPoint(
                    field_name=f"sec_edgar.{field_name}",
                    value=value,
                    unit=unit,
                    as_of=own or as_of,
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

        # Every raw statement item, including the ten the legacy `parse_company_facts`
        # also produces. They are emitted HERE because only this path is period-aware:
        # `parse_company_facts` takes the first alias concept that has any annual data,
        # so a company that changed XBRL tags keeps reporting the stale tag's last year
        # for ever. Moderna's `Revenues` tag stops at FY2022 ($19.263bn) while its
        # current tag carries FY2025 ($1.944bn), and the FY2022 figure reached a live
        # report labelled FY2025.
        for name in ("revenue", "gross_profit", "operating_income", "net_income",
                     "operating_cash_flow", "capital_expenditures", "dividends_paid",
                     "cash_and_equivalents", "total_assets", "total_liabilities",
                     "shareholders_equity", "short_term_debt", "long_term_debt"):
            _add(name, getattr(self, name), "USD_m",
                 DataQuality.B_single_credible, self._note_for(name, period_note))
        for name in ("eps_basic", "eps_diluted"):
            _add(name, getattr(self, name), "USD",
                 DataQuality.B_single_credible, self._note_for(name, period_note))

        # Derived dollar aggregates.
        _add("free_cash_flow", self.free_cash_flow, "USD_m", DataQuality.C_inferred,
             "Derived: operating_cash_flow - capital_expenditures. " + period_note)
        _add("total_debt", self.total_debt, "USD_m", DataQuality.C_inferred,
             self._total_debt_note(period_note))

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

        # V3.18.1 — what the bundle is FOR, and what was refused for not being for it.
        # Both travel as datapoints because the datapoint list is the only thing that
        # crosses into the snapshot: a withheld field recorded only on this object
        # would arrive downstream as a bare absence, indistinguishable from a concept
        # the filer never used, and the report could not say which.
        _add("reporting_period_end", self.reporting_period_end, None,
             DataQuality.B_single_credible,
             "The period end every statement figure in this bundle is for.")
        if self.consistency:
            _add("statement_consistency", self.consistency, None,
                 DataQuality.B_single_credible,
                 "Relationships between this bundle's own statement lines that do not hold.")
        if self.basis_changes:
            _add("basis_changes", self.basis_changes, None, DataQuality.B_single_credible,
                 "Restatements of earlier periods in a filing newer than the annual bundle.")
        if self.current_period_facts:
            _add("current_period_facts", self.current_period_facts, None,
                 DataQuality.B_single_credible,
                 "The latest 10-Q's single-quarter figures, newer than the annual bundle.")
        if self.withheld_fields:
            _add(
                "withheld_fields",
                [
                    {"field": name, **{k: meta.get(k) for k in
                                       ("value", "end", "concept", "period_type", "reason")}}
                    for name, meta in sorted(self.withheld_fields.items())
                ],
                None,
                DataQuality.B_single_credible,
                "Statement concepts the filer has tagged, but not for this reporting "
                "period. Missing for this period; never carried into it.",
            )

        return dps

    def _total_debt_note(self, period_note: str) -> str:
        """The total's basis: which concepts were added, and what was not tagged."""
        basis = self.debt_basis or {}
        formula = basis.get("formula")
        if not formula:
            return "Derived: short_term_debt + long_term_debt. " + period_note
        notes = "; ".join(basis.get("notes") or ())
        return (
            f"Derived: {formula}" + (f". Basis: {notes}" if notes else "") + ". "
            + period_note
        )

    def _note_for(self, field_name: str, fallback: str) -> str:
        """A note naming the period and concept THIS value came from."""
        meta = self.field_periods.get(field_name)
        if not meta:
            return fallback
        return (
            f"FY{meta.get('fy')} {meta.get('fp') or ''} period ending "
            f"{meta.get('end')} (form {meta.get('form') or '?'}, "
            f"us-gaap concept {meta.get('concept')}). "
            "Source tier T2_regulator_or_gov."
        ).strip()

    def end_date_or_today(self) -> str:
        if self.filed_date:
            return self.filed_date
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


#: How far apart two period ends may be and still be the same reporting period. Within
#: one filing every statement concept shares one period end exactly; the allowance
#: exists only so a filer's one-off date irregularity does not withhold a correct
#: figure. It is far below the ~90 days that separate two adjacent reporting periods.
_SAME_PERIOD_TOLERANCE_DAYS = 10  # mirrors statement_consistency.SAME_PERIOD_TOLERANCE_DAYS


def _parse_end(value: str | None) -> datetime | None:
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


#: The metrics that define what period a statement bundle is FOR. The anchor must be a
#: period at least one of them carries; a cluster of balance-sheet instants alone cannot
#: outvote revenue and net income.
_HEADLINE_METRICS: tuple[str, ...] = (
    "revenue",
    "net_income",
    "operating_cash_flow",
    "total_assets",
)


def _reporting_period_anchor(ends: "list[datetime]") -> datetime | None:
    """The period end MOST metrics agree on; ties go to the later one.

    Deliberately not ``max(ends)``. The first version used the maximum, and review
    showed what that does: one mis-tagged concept dated after the fiscal year end — an
    instant on a 10-K cover date, a dividend tagged into the next quarter — became the
    anchor, and revenue, net income, cash flow and the whole balance sheet were withheld
    as "stale" against it. A rule built to stop one wrong figure reaching a report must
    not be able to remove every right one because of one wrong figure.

    Agreement is measured within the same-period tolerance, so a 52/53-week filer whose
    concepts end a few days apart still forms one group.
    """
    if not ends:
        return None

    def support(candidate: datetime) -> int:
        return sum(
            1 for end in ends if abs((end - candidate).days) <= _SAME_PERIOD_TOLERANCE_DAYS
        )

    return max(set(ends), key=lambda candidate: (support(candidate), candidate))


def _metrics_outside_reporting_period(
    selected: "dict[str, _Metric]",
) -> "tuple[list[tuple[str, str, _Metric]], str | None]":
    """Which selected metrics are NOT for the bundle's reporting period, and why.

    Returns ``(outside, anchor)``. The reporting period is the period end most metrics
    on the bundle's basis agree on (see ``_reporting_period_anchor``) — annual when any
    metric is annual, else quarterly. Three ways to be outside it, kept apart because
    they are different defects:

    * ``stale_period`` — the filer's latest value for the concept is for an EARLIER
      period (a concept it stopped tagging).
    * ``later_period_outlier`` — the value is dated AFTER the period the rest of the
      bundle is for. Only that metric is withheld; it never moves the anchor.
    * ``interim_in_annual_bundle`` — the concept has no annual entry at all and fell
      back to a 10-Q value. Presented under an annual headline that is a quarter
      relabelled as a year.

    A metric with no readable period end is left alone: unknown is not stale, and
    withholding on it would discard correct figures from an irregular payload.
    """
    present = {n: m for n, m in selected.items() if m.value is not None}
    if not present:
        return [], None
    basis = "annual" if any(m.period_type == "annual" for m in present.values()) else "quarterly"
    ends = [
        end
        for m in present.values()
        if m.period_type == basis and (end := _parse_end(m.end)) is not None
    ]
    headline_ends = [
        end
        for name in _HEADLINE_METRICS
        if (m := present.get(name)) is not None
        and m.period_type == basis
        and (end := _parse_end(m.end)) is not None
    ]
    anchor = _reporting_period_anchor(ends)
    if (
        anchor is not None
        and headline_ends
        and not any(
            abs((anchor - end).days) <= _SAME_PERIOD_TOLERANCE_DAYS for end in headline_ends
        )
    ):
        # The most-agreed period is one no headline metric is for. Anchor on the
        # headline metrics instead: what a bundle is FOR is decided by its statements,
        # not by a cluster of instants.
        anchor = _reporting_period_anchor(headline_ends)
    if anchor is None:
        return [], None
    outside: list[tuple[str, str, _Metric]] = []
    for name, metric in present.items():
        if metric.period_type != basis:
            outside.append((name, "interim_in_annual_bundle", metric))
            continue
        end = _parse_end(metric.end)
        if end is None:
            continue
        gap = (end - anchor).days
        if gap > _SAME_PERIOD_TOLERANCE_DAYS:
            outside.append((name, "later_period_outlier", metric))
        elif gap < -_SAME_PERIOD_TOLERANCE_DAYS:
            outside.append((name, "stale_period", metric))
    return outside, anchor.strftime("%Y-%m-%d")


#: Roles (``sec_total_debt.DebtComponent.role``) that make up each published leg.
_DEBT_LEG_ROLES: dict[str, frozenset[str]] = {
    "long_term_debt": frozenset({"long_term_debt_noncurrent"}),
    "short_term_debt": frozenset(
        {"short_term_borrowings", "debt_current", "long_term_debt_current"}
    ),
}


def _apply_total_debt(
    result: NormalizedSecFinancials, us_gaap: dict[str, Any], warnings: list[str]
) -> None:
    """Set the debt legs and ``total_debt`` from compatible concepts at the period.

    QA campaign 2026-11, defect JPM-1 (ADR-067). The legs used to be chosen
    independently — the freshest "long" alias and the freshest "short" alias — and then
    added. So ``LongTermDebt``, which already contains current maturities, was summed
    with ``LongTermDebtCurrent`` or ``DebtCurrent``. A filer with no long-term concept
    for the period also got its short-term borrowings published as total debt.
    ``sec_total_debt.derive_total_debt`` builds the total from one compatible
    combination, every term at this bundle's reporting period (from its own filing
    where possible), and refuses a total that has no long-term term.
    """
    basis = result.period_basis if result.period_basis in ("annual", "quarterly") else None
    debt = derive_total_debt(
        us_gaap,
        period_end=result.reporting_period_end,
        forms=_QUARTERLY_FORMS if basis == "quarterly" else _ANNUAL_FORMS,
        prefer_accn=result.accession_number,
        tolerance_days=_SAME_PERIOD_TOLERANCE_DAYS,
    )
    period_end = _parse_end(result.reporting_period_end)

    def withhold(name: str, concepts: tuple[str, ...]) -> None:
        """Record the filer's latest value for a leg that is not tagged for this period."""
        latest = _select_metric(us_gaap, list(concepts), "USD", True)
        if latest.value is None:
            return
        end = _parse_end(latest.end)
        if basis is not None and latest.period_type != basis:
            reason = "interim_in_annual_bundle"
        elif (
            end is not None
            and period_end is not None
            and (end - period_end).days > _SAME_PERIOD_TOLERANCE_DAYS
        ):
            reason = "later_period_outlier"
        else:
            reason = "stale_period"
        result.withheld_fields[name] = {
            "value": latest.value,
            "end": latest.end,
            "fy": latest.fy,
            "concept": latest.concept,
            "period_type": latest.period_type,
            "reason": reason,
        }

    if debt.withheld_reason is not None:
        # Computable, but known to be incomplete or double counted (a maturity schedule
        # shows principal due within a year that no current concept carries; or the
        # total exceeds the filer's own total-debt concept). The noncurrent line is a
        # reported figure and is kept; the total, and every ratio on it, is not.
        result.long_term_debt = debt.noncurrent
        result.debt_basis = debt.to_dict()
        noncurrent = [c for c in debt.components if c.role == "long_term_debt_noncurrent"]
        if result.long_term_debt is not None and noncurrent:
            first = noncurrent[0]
            result.field_periods["long_term_debt"] = {
                "fy": first.fy, "fp": first.fp, "end": first.end, "form": first.form,
                "concept": first.concept, "period_type": basis, "duration_days": None,
            }
        warnings.append(
            "SEC EDGAR: total_debt, debt_to_equity and net debt withheld — "
            + debt.withheld_reason
            + ". A partial or double-counted total is never published."
        )
        return

    if debt.long_term_missing or debt.total is None:
        withhold("long_term_debt", ALL_LONG_TERM)
        last = result.withheld_fields.get("long_term_debt")
        warnings.append(
            "SEC EDGAR: total_debt, debt_to_equity and net debt withheld — no long-term "
            "debt concept is tagged for the reporting period"
            + (
                f" (latest: us-gaap:{last['concept']} for the period ending {last['end']})"
                if last
                else ""
            )
            + ". Short-term borrowings alone are not total debt, and a partial sum is "
            "never published."
        )
        return

    result.total_debt = debt.total
    result.short_term_debt = debt.current
    result.long_term_debt = debt.noncurrent
    result.debt_basis = debt.to_dict()
    if not any(c.role in _DEBT_LEG_ROLES["short_term_debt"] for c in debt.components):
        # Nothing current is tagged for the period. A value the filer tagged for ANOTHER
        # period is recorded as withheld, exactly as the own-period rule records it.
        withhold("short_term_debt", ALL_CURRENT)

    def period_record(parts: list[Any]) -> dict[str, Any]:
        first = parts[0]
        return {
            "fy": first.fy,
            "fp": first.fp,
            "end": first.end,
            "form": first.form,
            "concept": " + ".join(c.concept for c in parts),
            "period_type": basis,
            "duration_days": None,
        }

    for name, roles in _DEBT_LEG_ROLES.items():
        parts = [c for c in debt.components if c.role in roles]
        if getattr(result, name) is not None and parts:
            result.field_periods[name] = period_record(parts)
    result.field_periods["total_debt"] = period_record(debt.components)
    if debt.notes:
        warnings.append(
            f"SEC EDGAR: total_debt basis — {debt.formula}; " + "; ".join(debt.notes) + "."
        )


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
    dividends = _select_metric(us_gaap, _DIVIDENDS_PAID, "USD", True)
    total_assets = _select_metric(us_gaap, _TOTAL_ASSETS, "USD", True)
    total_liabilities = _select_metric(us_gaap, _TOTAL_LIABILITIES, "USD", True)
    equity = _select_metric(us_gaap, _SHAREHOLDERS_EQUITY, "USD", True)
    cash = _select_metric(us_gaap, _CASH, "USD", True)
    shares = _select_metric(dei, _SHARES_OUTSTANDING, "shares", True)

    result.revenue = revenue.value
    result.gross_profit = gross_profit.value
    result.operating_income = operating_income.value
    result.net_income = net_income.value
    result.eps_basic = eps_basic.value
    result.eps_diluted = eps_diluted.value
    result.operating_cash_flow = ocf.value
    result.capital_expenditures = capex.value
    result.dividends_paid = dividends.value
    result.total_assets = total_assets.value
    result.total_liabilities = total_liabilities.value
    result.shareholders_equity = equity.value
    result.cash_and_equivalents = cash.value
    result.shares_outstanding = shares.value

    # ── V3.18.1 — THE OWN-PERIOD RULE ────────────────────────────────────
    # Every metric above was selected as the freshest entry OF ITS OWN CONCEPT, which
    # says nothing about whether the concepts agree with one another. A filer that
    # stops tagging a concept keeps "reporting" its last year for ever: Southern Copper
    # last tagged ``GrossProfit`` for FY2019, so its FY2019 gross profit (2,914.8) sat
    # beside FY2025 operating income (7,001.7) in one bundle, produced a 21.7% "gross
    # margin" against FY2025 revenue, and reached a live report labelled FY2025.
    #
    # So the bundle gets ONE reporting period — the latest period end any statement
    # metric carries, on the bundle's own basis — and a metric that is not FOR that
    # period is withheld: recorded in ``withheld_fields`` with its real period, absent
    # from every reporting field, and therefore absent from every ratio below. Missing
    # means missing. It is deliberately not "use the nearest year": a stale figure
    # relabelled current is the defect, not a degraded answer.
    selected: dict[str, _Metric] = {
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "net_income": net_income,
        "eps_basic": eps_basic,
        "eps_diluted": eps_diluted,
        "operating_cash_flow": ocf,
        "capital_expenditures": capex,
        "dividends_paid": dividends,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "shareholders_equity": equity,
        "cash_and_equivalents": cash,
    }
    outside, anchor_end = _metrics_outside_reporting_period(selected)
    for name, reason, metric in outside:
        result.withheld_fields[name] = {
            "value": metric.value,
            "end": metric.end,
            "fy": metric.fy,
            "concept": metric.concept,
            "period_type": metric.period_type,
            "reason": reason,
        }
        setattr(result, name, None)
        # The metric object itself is blanked too, so the headline choice, the
        # ``field_periods`` record and the YoY block below all see it as absent.
        metric.value = None
        metric.prior_value = None
    if result.withheld_fields:
        detail = "; ".join(
            f"{name} (us-gaap:{meta['concept']}, period ending {meta['end']}, "
            f"{meta['reason']})"
            for name, meta in sorted(result.withheld_fields.items())
        )
        warnings.append(
            "SEC EDGAR: withheld as NOT REPORTED FOR THE CURRENT PERIOD — "
            f"{detail}. The filer has not tagged these concepts for the bundle's "
            "reporting period, so they are missing for it; a value for another period "
            "is never carried in or relabelled."
        )

    # ── QA campaign 2026-11 — is "operating income" operating income? ───
    if operating_income.value is not None and revenue.value is not None:
        excess = operating_income_excess(
            us_gaap,
            accn=operating_income.accn,
            end=operating_income.end,
            duration_days=operating_income.duration_days,
            revenue_m=revenue.value,
            operating_income_m=operating_income.value,
        )
        if excess is not None:
            result.withheld_fields["operating_income"] = {
                "value": operating_income.value,
                "end": operating_income.end,
                "fy": operating_income.fy,
                "concept": operating_income.concept,
                "period_type": operating_income.period_type,
                "reason": excess,
            }
            result.operating_income = None
            operating_income.value = None
            operating_income.prior_value = None
            warnings.append(
                "SEC EDGAR: operating income withheld — " + excess + ". The figure is kept "
                "in withheld_fields; no operating margin is computed from it."
            )

    # Each metric's OWN period, kept so a consumer can label the fact honestly
    # instead of inheriting a single headline fiscal year.
    for name, metric in (
        ("revenue", revenue),
        ("gross_profit", gross_profit),
        ("operating_income", operating_income),
        ("net_income", net_income),
        ("eps_basic", eps_basic),
        ("eps_diluted", eps_diluted),
        ("operating_cash_flow", ocf),
        ("capital_expenditures", capex),
        ("dividends_paid", dividends),
        ("total_assets", total_assets),
        ("total_liabilities", total_liabilities),
        ("shareholders_equity", equity),
        ("cash_and_equivalents", cash),
        ("shares_outstanding", shares),
    ):
        if metric.value is None:
            continue
        result.field_periods[name] = {
            "fy": metric.fy,
            "fp": metric.fp,
            "end": metric.end,
            "form": metric.form,
            "concept": metric.concept,
            "period_type": metric.period_type,
            "duration_days": metric.duration_days,
        }

    # ── Headline period (prefer revenue, then net income) ────────────────
    headline = next(
        (m for m in (revenue, net_income, ocf, total_assets) if m.value is not None),
        None,
    )
    # A bundle whose facts span different fiscal years is legitimate — a company may
    # simply not have filed every concept in its newest filing — but it must never be
    # presented as one year's accounts. Said out loud so the caller can label it.
    statement_years = {
        meta["fy"]
        for name, meta in result.field_periods.items()
        if meta.get("fy") is not None
        and name != "shares_outstanding"
    }
    if len(statement_years) > 1:
        spread = ", ".join(
            f"{n}=FY{m['fy']}"
            for n, m in sorted(result.field_periods.items())
            if m.get("fy") is not None
        )
        warnings.append(
            "SEC EDGAR: statement facts span more than one fiscal year "
            f"({sorted(statement_years)}) — {spread}. They are NOT one year's "
            "accounts and must not be compared with each other or presented under a "
            "single fiscal year."
        )

    if headline is not None:
        # The ANCHOR, not the headline's own end: two kept metrics may each sit inside
        # the tolerance of the anchor and still be further than that from each other,
        # and a slot check made against one of them would refuse a figure this function
        # kept.
        result.reporting_period_end = anchor_end or headline.end
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
        elif (
            headline.period_type == "annual"
            and result.fiscal_year is not None
            and result.fiscal_year < datetime.now(timezone.utc).year - 2
        ):
            warnings.append(
                f"SEC EDGAR: latest annual filing selected is FY{result.fiscal_year}, "
                f"more than two fiscal years behind the current year "
                f"({datetime.now(timezone.utc).year}). Data may be stale — "
                "confirm no newer 10-K/20-F was filed."
            )

    # ── Derived: total debt (ADR-067) ────────────────────────────────────
    _apply_total_debt(result, us_gaap, warnings)

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
    # A return on, or leverage against, NEGATIVE equity has no reading: a loss over a
    # deficit prints as a healthy positive percentage. Refused rather than printed —
    # the same rule the calculation engine applies (`positive_roles`).
    if result.shareholders_equity is not None and result.shareholders_equity > 0:
        result.return_on_equity = _pct(result.net_income, result.shareholders_equity)
        if result.total_debt is not None:
            result.debt_to_equity = round(
                result.total_debt / result.shareholders_equity, 3
            )
    elif result.shareholders_equity is not None:
        warnings.append(
            "SEC EDGAR: return_on_equity and debt_to_equity not derived — shareholders' "
            "equity is not positive, so neither ratio has a meaningful reading."
        )

    # ── V3.18.1 — do the lines agree with each other? ────────────────────
    # The own-period rule above removes the cause that produced a gross profit below
    # operating income in production. This is the check that would have caught it by
    # its SYMPTOM, and that catches the next contamination whose cause is something
    # else. A ratio built on a pair that fails is withheld; the pair itself is kept and
    # flagged, because which of two figures is wrong is not this function's to decide.
    from app.services.statement_consistency import (
        StatementFigure,
        check_statement_consistency,
    )

    consistency = check_statement_consistency(
        [
            StatementFigure(
                metric=name,
                value=float(value),
                period_end=(result.field_periods.get(name) or {}).get("end"),
                currency=result.reporting_currency,
                source="sec_edgar_xbrl",
            )
            for name in (
                "revenue", "gross_profit", "operating_income", "net_income",
                "total_assets", "total_liabilities", "shareholders_equity",
                "cash_and_equivalents",
            )
            if (value := getattr(result, name)) is not None
        ]
    )
    if not consistency.is_clean:
        result.consistency = consistency.to_dict()
        for derived in consistency.withheld_derived:
            if hasattr(result, derived):
                setattr(result, derived, None)
        for finding in consistency.inconsistencies:
            warnings.append(
                f"SEC EDGAR: statement consistency ({finding.severity}) — {finding.message}"
            )

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

    if result.period_basis == "annual":
        result.current_period_facts = latest_quarter_facts(
            us_gaap, annual_filed=result.filed_date, cik=resolved_cik,
            annual_end=result.reporting_period_end,
        )
        result.basis_changes = basis_changes_since_annual(
            us_gaap, annual_filed=result.filed_date,
            annual_accn=result.accession_number,
            annual_end=result.reporting_period_end, dei=dei,
        )
        for change in result.basis_changes:
            warnings.append("SEC EDGAR: " + change["sentence"])
            if change["kind"] in (BASIS_SHARE_CONSOLIDATION, BASIS_SHARE_SPLIT):
                # A per-share figure from before a split is on a different share count
                # from every price and every later per-share figure: withheld, never
                # silently compared.
                for name in ("eps_basic", "eps_diluted"):
                    value = getattr(result, name)
                    if value is None:
                        continue
                    own = result.field_periods.get(name) or {}
                    result.withheld_fields[name] = {
                        "value": value,
                        "end": own.get("end"),
                        "fy": own.get("fy"),
                        "concept": own.get("concept"),
                        "period_type": own.get("period_type"),
                        "reason": "predates_share_count_change: " + change["sentence"],
                    }
                    setattr(result, name, None)

    result.warnings = warnings
    return result


# ---------------------------------------------------------------------------
# A basis the issuer no longer reports — QA campaign 2026-11
# ---------------------------------------------------------------------------

BASIS_RECAST = "perimeter_recast"
BASIS_SHARE_CONSOLIDATION = "share_consolidation"
BASIS_SHARE_SPLIT = "share_split"
#: A comparative restated by more than this is a different perimeter, not a rounding or
#: an immaterial reclassification.
_RECAST_THRESHOLD = 0.05
#: How close a per-share ratio must be to a whole number to be read as a split.
_SPLIT_TOLERANCE = 0.02


def _comparatives(us_gaap: dict[str, Any], concept: str, unit: str, accn: str,
                  period_end: str) -> list[dict]:
    rows = (us_gaap.get(concept) or {}).get("units", {}).get(unit, []) or []
    return [
        e for e in rows
        if e.get("accn") == accn and (e.get("end") or "") < period_end
        and e.get("start") and 80 <= (_duration_days(e) or 0) <= 100
    ]


def _as_filed_in(us_gaap: dict[str, Any], concept: str, unit: str, entry: dict,
                 annual_accn: str) -> dict | None:
    """The same concept and period as the annual report ITSELF tagged it, or None.

    Only the annual filing's own accession is a baseline. A value from an earlier 10-Q
    can differ from the annual report for reasons that are already inside the annual
    figures — a split made before the 10-K (retroactively adjusted in it), a disposal
    already recast in it — and comparing against it would announce a change of basis
    that the annual figures do not have. When the 10-K does not tag the period, nothing
    is concluded.
    """
    rows = (us_gaap.get(concept) or {}).get("units", {}).get(unit, []) or []
    same = [
        e for e in rows
        if e.get("accn") == annual_accn
        and e.get("start") == entry.get("start") and e.get("end") == entry.get("end")
        and e.get("val") is not None
    ]
    return same[0] if same else None


#: How close a cover-page share-count ratio must be to a whole number to be read as a
#: split: wider than the per-share tolerance because months of buybacks or issuance sit
#: between the two cover dates.
_COVER_SPLIT_TOLERANCE = 0.04


def _cover_shares(dei: dict[str, Any] | None, accn: str) -> float | None:
    """Cover-page shares outstanding in one filing, summed across its rows at one date."""
    rows = [
        e for e in ((dei or {}).get("EntityCommonStockSharesOutstanding") or {})
        .get("units", {}).get("shares", []) or []
        if e.get("accn") == accn and e.get("val") is not None
    ]
    if not rows:
        return None
    latest = max(e.get("end") or "" for e in rows)
    total = sum(float(e["val"]) for e in rows if (e.get("end") or "") == latest)
    return total or None


def _whole_ratio(ratio: float, tolerance: float) -> tuple[str, int] | None:
    """(kind, k) when ``ratio`` (new per-share / old per-share) is k or 1/k, k >= 2."""
    if ratio <= 0:
        return None
    for k in range(2, 51):
        if abs(ratio - k) / k < tolerance:
            return BASIS_SHARE_CONSOLIDATION, k
        if abs(1.0 / ratio - k) / k < tolerance:
            return BASIS_SHARE_SPLIT, k
    return None


def _prior_quarterly(us_gaap: dict[str, Any], concept: str, unit: str, entry: dict,
                     *, filed_by: str, exclude_accn: str) -> dict | None:
    """The same concept and period as a 10-Q reported it on or before ``filed_by``."""
    rows = (us_gaap.get(concept) or {}).get("units", {}).get(unit, []) or []
    same = [
        e for e in rows
        if e.get("form") in _QUARTERLY_FORMS and e.get("accn") != exclude_accn
        and e.get("start") == entry.get("start") and e.get("end") == entry.get("end")
        and (e.get("filed") or "") <= filed_by and e.get("val") is not None
    ]
    return max(same, key=lambda e: e.get("filed") or "") if same else None


def _annual_recast_nothing(us_gaap: dict[str, Any], concept: str, *, annual_accn: str,
                           annual_end: str | None, annual_filed: str) -> bool:
    """True when the annual report restated NOTHING of its prior year for ``concept``.

    Its prior-year comparative must equal that year's figure as the previous annual
    report filed it (within 1%). Then a quarter that a later 10-Q restates was not
    already restated in the annual report, and last year's 10-Q is a fair baseline for
    it. Anything missing answers False.
    """
    if not annual_end:
        return False
    rows = (us_gaap.get(concept) or {}).get("units", {}).get("USD", []) or []
    comparatives = [
        e for e in rows
        if e.get("accn") == annual_accn and (e.get("end") or "") < annual_end
        and 350 <= (_duration_days(e) or 0) <= 380 and e.get("val")
    ]
    if not comparatives:
        return False
    restated = max(comparatives, key=lambda e: e.get("end") or "")
    originals = [
        e for e in rows
        if str(e.get("form") or "").startswith("10-K") and e.get("accn") != annual_accn
        and e.get("start") == restated.get("start") and e.get("end") == restated.get("end")
        and (e.get("filed") or "") < annual_filed and e.get("val")
    ]
    if not originals:
        return False
    original = min(originals, key=lambda e: e.get("filed") or "")
    return abs(float(restated["val"]) / float(original["val"]) - 1.0) <= 0.01


def _newest_quarterly_accession(
    us_gaap: dict[str, Any], concepts: list[str], *, filed_after: str | None = None,
) -> dict | None:
    """The entry of the 10-Q (or 10-Q/A) covering the LATEST period, latest filing first.

    Chosen by period end, then filing date: an amendment of an older quarter filed after
    the newest 10-Q is not the latest quarter, and never hides it.
    """
    newest: dict | None = None
    for concept in concepts:
        unit = "USD/shares" if concept in _EPS_DILUTED + _EPS_BASIC else "USD"
        for e in (us_gaap.get(concept) or {}).get("units", {}).get(unit, []) or []:
            if e.get("form") not in _QUARTERLY_FORMS or not e.get("accn"):
                continue
            if filed_after and (e.get("filed") or "") <= filed_after:
                continue
            key = (e.get("end") or "", e.get("filed") or "")
            if newest is None or key > (newest.get("end") or "", newest.get("filed") or ""):
                newest = e
    return newest


def _eps_whole_ratio(restated: float, original: float) -> tuple[str, int] | None:
    """(kind, k) when a restated EPS is k times (or 1/k of) the original, allowing for
    cent rounding.

    Per-share figures are printed to the cent, so a small EPS restated by a large ratio
    misses a pure 2% test. Live replay of GE's 1-for-8 reverse split (2021): Q3 2020 EPS
    -0.14 → -1.09 is 7.79x, 2.7% off 8. The check therefore accepts half a cent of
    rounding on each side (|restated − k·original| ≤ 0.005·(k+1)), but only for a move of
    at least 1.5x, so an unchanged cent figure is never read as a ratio.
    """
    if not original or not restated or (restated > 0) != (original > 0):
        return None
    ratio = restated / original
    if 1 / 1.5 < ratio < 1.5:
        return None
    best: tuple[float, str, int] | None = None
    for k in range(2, 51):
        for kind, gap, scale in (
            (BASIS_SHARE_CONSOLIDATION, abs(restated - k * original), abs(k * original)),
            (BASIS_SHARE_SPLIT, abs(original - k * restated), abs(original)),
        ):
            if gap <= max(_SPLIT_TOLERANCE * scale, 0.005 * (k + 1)):
                score = gap / max(scale, 1e-9)
                if best is None or score < best[0]:
                    best = (score, kind, k)
    return (best[1], best[2]) if best else None


def basis_changes_since_annual(
    us_gaap: dict[str, Any], *, annual_filed: str | None, annual_accn: str | None = None,
    annual_end: str | None = None, dei: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Restatements, in a 10-Q filed after the annual bundle, of periods that bundle saw.

    Every 10-Q restates last year's quarter beside this year's. When that comparative
    differs from the value as it stood when the annual report was filed, the issuer has
    changed basis since — and the annual figures are on the OLD basis:

    * revenue restated by more than 5% — a perimeter recast (a discontinued operation,
      a spin-off, a disposal);
    * per-share figures restated by a whole-number ratio while net income is unchanged —
      a share split or consolidation.

    Live QA campaign 2026-11: Honeywell spun off Aerospace and consolidated its shares
    1-for-2 on 2026-06-29. Its Q3 2026 10-Q restates Q3 2025 revenue from 9,437m to 4,929m
    and diluted EPS from 2.86 to 5.71. The report presented FY2025 revenue of 37,442m and
    EPS of 7.36 as current, beside a post-consolidation share price.

    The baseline is, in order:

    1. the annual report's OWN tagging of the quarter (Honeywell's 10-K tags Q3 2025);
    2. otherwise last year's 10-Q — but only when nothing since could already be inside
       the annual figures: for revenue, the annual report restated nothing of its prior
       year; for per-share figures, the cover-page share count moved by the SAME whole
       ratio between the annual report's cover and the restating 10-Q's (a split made
       before the 10-K leaves the covers equal). Most 10-Ks have not tagged quarters since
       Item 302(a) was dropped in 2021, so without (2) the check would rarely run.

    A cover-page share count change on its own is never a split: an issuance, an ATM
    programme or an all-stock merger moves it too, and none of them restates past EPS.
    """
    if not annual_filed or not annual_accn:
        return []
    newest = _newest_quarterly_accession(
        us_gaap, [*_REVENUE, *_NET_INCOME, *_EPS_DILUTED], filed_after=annual_filed
    )
    if newest is None:
        return []
    accn, filed = newest.get("accn") or "", newest.get("filed") or ""
    period_end = max(
        (e.get("end") or "" for c in [*_REVENUE, *_NET_INCOME]
         for e in (us_gaap.get(c) or {}).get("units", {}).get("USD", []) or []
         if e.get("accn") == accn),
        default="",
    )
    if not accn or not period_end or (annual_end and period_end <= annual_end):
        return []
    out: list[dict[str, Any]] = []

    for concept in _REVENUE:
        found = False
        for restated in sorted(_comparatives(us_gaap, concept, "USD", accn, period_end),
                               key=lambda e: e.get("end") or "", reverse=True):
            original = _as_filed_in(us_gaap, concept, "USD", restated, annual_accn)
            if original is None and _annual_recast_nothing(
                us_gaap, concept, annual_accn=annual_accn, annual_end=annual_end,
                annual_filed=annual_filed,
            ):
                original = _prior_quarterly(us_gaap, concept, "USD", restated,
                                            filed_by=annual_filed, exclude_accn=accn)
            if original is None or not original["val"] or restated.get("val") is None:
                continue
            change = float(restated["val"]) / float(original["val"]) - 1.0
            found = True
            if abs(change) > _RECAST_THRESHOLD:
                out.append({
                    "kind": BASIS_RECAST,
                    "metric": "revenue",
                    "period_start": restated.get("start"),
                    "period_end": restated.get("end"),
                    "as_filed": round(float(original["val"]) / _MILLION, 2),
                    "restated": round(float(restated["val"]) / _MILLION, 2),
                    "change_pct": round(change * 100.0, 1),
                    "restating_filing": accn,
                    "restating_filed": filed,
                    "sentence": (
                        f"the 10-Q filed {filed} restates revenue for the quarter ending "
                        f"{restated.get('end')} from {float(original['val']) / _MILLION:,.0f}m "
                        f"(as it stood when the annual report was filed) to "
                        f"{float(restated['val']) / _MILLION:,.0f}m ({change * 100.0:+.1f}%): "
                        "the annual figures predate a change of perimeter (a discontinued "
                        "operation, spin-off or disposal) and are not comparable with the "
                        "current period"
                    ),
                })
            break
        if found:
            break

    before, after = _cover_shares(dei, annual_accn), _cover_shares(dei, accn)
    cover_whole = (
        _whole_ratio(before / after, _COVER_SPLIT_TOLERANCE)
        if before is not None and after is not None else None
    )
    for eps_concept in _EPS_DILUTED + _EPS_BASIC:
        decided = False
        for restated in _comparatives(us_gaap, eps_concept, "USD/shares", accn, period_end):
            original = _as_filed_in(
                us_gaap, eps_concept, "USD/shares", restated, annual_accn
            )
            via_annual = original is not None
            if original is None and cover_whole is not None:
                original = _prior_quarterly(us_gaap, eps_concept, "USD/shares", restated,
                                            filed_by=annual_filed, exclude_accn=accn)
            if original is None or not original["val"] or not restated.get("val"):
                continue
            # Net income for the same period must be unchanged: a per-share move with
            # unchanged earnings is a share-count change, nothing else.
            same_period = (restated.get("start"), restated.get("end"))
            ni_concept, ni_now = next(
                (
                    (c, e)
                    for c in _NET_INCOME
                    for e in _comparatives(us_gaap, c, "USD", accn, period_end)
                    if (e.get("start"), e.get("end")) == same_period
                ),
                ("", None),
            )
            # The same concept, from the same kind of baseline, on both sides.
            ni_then: dict | None = None
            if ni_now is not None:
                ni_then = (
                    _as_filed_in(us_gaap, ni_concept, "USD", ni_now, annual_accn)
                    if via_annual
                    else _prior_quarterly(us_gaap, ni_concept, "USD", ni_now,
                                          filed_by=annual_filed, exclude_accn=accn)
                )
            if not ni_now or not ni_then or not ni_then["val"]:
                continue
            decided = True
            if abs(float(ni_now["val"]) / float(ni_then["val"]) - 1.0) > 0.01:
                break
            whole = _eps_whole_ratio(float(restated["val"]), float(original["val"]))
            # Without the annual report's own figure, the cover pages must show the SAME
            # change since the annual report, or it may predate it.
            if whole is not None and (via_annual or whole == cover_whole):
                kind, k = whole
                words = _split_words(kind, k)
                cover = (
                    f" (cover-page shares outstanding {before / 1e6:,.1f}m on the annual "
                    f"report, {after / 1e6:,.1f}m on the 10-Q)"
                    if not via_annual and before is not None and after is not None else ""
                )
                out.append({
                    "kind": kind,
                    "metric": "eps",
                    "ratio": k,
                    "period_end": restated.get("end"),
                    "as_filed": original["val"],
                    "restated": restated["val"],
                    "restating_filing": accn,
                    "restating_filed": filed,
                    "sentence": (
                        f"the 10-Q filed {filed} restates earnings per share for the quarter "
                        f"ending {restated.get('end')} from {original['val']} to "
                        f"{restated['val']} with net income unchanged{cover} — {words} since "
                        "the annual report; per-share figures from before it are not "
                        "comparable with the current share count or price"
                    ),
                })
            break
        if decided:
            break
    return out


def _split_words(kind: str, k: int) -> str:
    if kind == BASIS_SHARE_CONSOLIDATION:
        return f"a 1-for-{k} share consolidation"
    return f"a {k}-for-1 share split"


# ---------------------------------------------------------------------------
# Operating income that is not operating income — QA campaign 2026-11
# ---------------------------------------------------------------------------

_COST_OF_REVENUE = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"]
_SGA = ["SellingGeneralAndAdministrativeExpense"]
_SGA_PARTS = ["SellingAndMarketingExpense", "GeneralAndAdministrativeExpense"]
_RND = [
    "ResearchAndDevelopmentExpense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
]
#: How far a tagged operating income may exceed revenue less the filing's own cost of
#: revenue, SG&A and R&D before it is not read as operating income: both a share of
#: revenue and a share of the figure itself, so a small other-operating-income line (a
#: disposal gain inside operating income) is never mistaken for a different measure.
_OPERATING_EXCESS_OF_REVENUE = 0.03
_OPERATING_EXCESS_OF_VALUE = 0.10


def _same_filing_value(
    us_gaap: dict[str, Any], aliases: list[str], *, accn: str | None, end: str | None,
    duration_days: int | None,
) -> float | None:
    for concept in aliases:
        for entry in (us_gaap.get(concept) or {}).get("units", {}).get("USD", []) or []:
            if entry.get("accn") != accn or entry.get("end") != end:
                continue
            if duration_days is not None and _duration_days(entry) != duration_days:
                continue
            if entry.get("val") is not None:
                return float(entry["val"]) / _MILLION
    return None


def operating_income_excess(
    us_gaap: dict[str, Any], *, accn: str | None, end: str | None,
    duration_days: int | None, revenue_m: float, operating_income_m: float,
) -> str | None:
    """Why a tagged ``OperatingIncomeLoss`` is not the group's operating income, or None.

    A true operating income cannot exceed revenue less a SUBSET of the same filing's own
    operating expenses (cost of revenue, SG&A, R&D): every expense line left out only
    lowers it further. A filer that presents no operating-income line can still tag its
    TOTAL SEGMENT PROFIT as ``OperatingIncomeLoss`` in the segment note. Live QA campaign
    2026-11: Honeywell's "operating income" (FY2025 8,127m, Q3 2026 1,062m) is its total
    segment profit — before corporate costs, intangible amortisation, repositioning and
    pension items — and the report printed a 21.7% "group operating margin" from it.
    Revenue less cost of sales, SG&A and R&D was 6,567m for that year.

    Checked only when cost of revenue and SG&A are both tagged for the same filing and
    period; otherwise nothing can be said and None is returned.
    """
    if not accn or not end:
        return None
    def value(aliases: list[str]) -> float | None:
        return _same_filing_value(
            us_gaap, aliases, accn=accn, end=end, duration_days=duration_days
        )

    cost = value(_COST_OF_REVENUE)
    sga = value(_SGA)
    if sga is None:
        parts = [value([c]) for c in _SGA_PARTS]
        sga = sum(p for p in parts if p is not None) if None not in parts else None
    if cost is None or sga is None:
        return None
    rnd = value(_RND) or 0.0
    bound = revenue_m - cost - sga - rnd
    excess = operating_income_m - bound
    if (
        excess > _OPERATING_EXCESS_OF_REVENUE * abs(revenue_m)
        and excess > _OPERATING_EXCESS_OF_VALUE * abs(operating_income_m)
    ):
        return (
            f"tagged OperatingIncomeLoss {operating_income_m:,.0f}m exceeds revenue less the "
            f"filing's own cost of revenue, SG&A and R&D ({bound:,.0f}m), so it is not group "
            "operating income as reported — possibly a total of segment profit or a "
            "non-GAAP measure (or R&D is already inside cost of revenue); withheld rather "
            "than presented as operating income"
        )
    return None


# ---------------------------------------------------------------------------
# The latest quarter — QA campaign 2026-11
# ---------------------------------------------------------------------------

#: (report field, concept aliases, kind). Flows are taken for the single quarter only;
#: a year-to-date figure is never presented as a quarter.
_QUARTER_FACT_FIELDS: tuple[tuple[str, list[str], str], ...] = (
    ("revenue", _REVENUE, "flow"),
    ("operating_profit", _OPERATING_INCOME, "flow"),
    ("net_income", _NET_INCOME, "flow"),
    ("cash_and_equivalents", _CASH, "instant"),
    ("total_assets", _TOTAL_ASSETS, "instant"),
    ("total_equity", _SHAREHOLDERS_EQUITY, "instant"),
)
_QUARTER_LABELS = {"Q1", "Q2", "Q3"}


def latest_quarter_facts(
    us_gaap: dict[str, Any], *, annual_filed: str | None, cik: str | None,
    annual_end: str | None = None,
) -> list[dict[str, Any]]:
    """The newest 10-Q's figures, as primary-fact dicts, when it is newer than the 10-K.

    Live QA campaign 2026-11: every US report said "Interim report acquired — figures not
    yet extracted" and showed only the annual bundle, although the 10-Q's XBRL was in hand.
    For Honeywell that meant every figure was the pre-spin-off FY2025 perimeter, while
    the Q3 2026 10-Q — continuing operations, after the Aerospace spin — was ignored.

    Rules, all fail-closed:
      * only the single newest 10-Q filing (one accession), and only when it was filed
        after the annual bundle's filing;
      * only that filing's OWN current period (its latest period end) — never the
        prior-year comparatives it also carries;
      * a flow only when it spans one quarter (80-100 days); a year-to-date figure is not
        a quarter and is skipped;
      * consolidated values only (companyfacts carries no segment dimensions).
    """
    entries: list[tuple[str, str, dict]] = []
    for field_name, aliases, kind in _QUARTER_FACT_FIELDS:
        for concept in aliases:
            for entry in (us_gaap.get(concept) or {}).get("units", {}).get("USD", []) or []:
                if entry.get("form") in _QUARTERLY_FORMS and entry.get("accn"):
                    entries.append((field_name, concept, entry))
    if not entries:
        return []
    # The latest PERIOD first, then the latest filing of it: a 10-Q/A for an older
    # quarter filed after the newest 10-Q never stands in for it.
    newest = max(entries, key=lambda t: (t[2].get("end") or "", t[2].get("filed") or ""))[2]
    accn, filed = newest.get("accn") or "", newest.get("filed") or ""
    if not accn or (annual_filed and filed <= annual_filed):
        return []
    own = [(f, c, e) for f, c, e in entries if e.get("accn") == accn]
    period_end = max((e.get("end") or "" for _f, _c, e in own), default="")
    # A 10-Q (or 10-Q/A) filed late for a quarter the annual report already covers is
    # not the latest period.
    if not period_end or (annual_end and period_end <= annual_end):
        return []
    cik_digits = (
        str(int(cik)) if cik and str(cik).isascii() and str(cik).isdigit() else None
    )
    url = (
        f"{_EDGAR_ARCHIVES_URL}/{cik_digits}/{accn.replace('-', '')}/"
        if cik_digits and _ACCESSION_RE.fullmatch(accn)
        else None
    )
    out: list[dict[str, Any]] = []
    for field_name, aliases, kind in _QUARTER_FACT_FIELDS:
        chosen: dict | None = None
        chosen_concept = ""
        for concept in aliases:
            for _f, c, e in own:
                if c != concept or _f != field_name or e.get("end") != period_end:
                    continue
                days = _duration_days(e)
                if kind == "flow" and (days is None or not 80 <= days <= 100):
                    continue
                if kind == "instant" and e.get("start"):
                    continue
                chosen, chosen_concept = e, concept
                break
            if chosen is not None:
                break
        if chosen is None or chosen.get("val") is None:
            continue
        if field_name == "operating_profit":
            revenue_fact = next((f for f in out if f["field"] == "revenue"), None)
            if revenue_fact is None or operating_income_excess(
                us_gaap, accn=accn, end=period_end, duration_days=_duration_days(chosen),
                revenue_m=revenue_fact["numeric_value"],
                operating_income_m=float(chosen["val"]) / _MILLION,
            ):
                continue
        fp = str(chosen.get("fp") or "").upper()
        fy = chosen.get("fy")
        if fp not in _QUARTER_LABELS or not isinstance(fy, int):
            continue
        label = f"{fp} {fy}"
        millions = round(float(chosen["val"]) / _MILLION, 2)
        out.append(
            {
                "field": field_name,
                "value": f"{millions:,.2f}",
                "numeric_value": millions,
                "unit": None,
                "currency": "USD",
                "scale": "million",
                "period": label,
                "period_end": period_end,
                "scope": "Group",
                "source_url": url,
                "page_number": None,
                "excerpt_id": f"xbrl:{chosen_concept}",
                "confidence": "high",
                "document_title": f"Form 10-Q ({label}), SEC EDGAR XBRL",
                "document_date": filed,
                "document_source_type": "sec_edgar_xbrl",
                "source": "sec_edgar_xbrl",
                "source_tier": "T2_regulator_or_gov",
            }
        )
    return out
