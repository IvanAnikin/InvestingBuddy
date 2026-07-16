# Data Sources

## Status: Phase 24.1 — Real News + Company Source Enablement. On top of Phase 24, a source-discovery + news-search layer enriches catalyst coverage for `free_real`/`eodhd_free_real`: **company source discovery** (`company_source_discovery_service` + `exchange_source_registry`) resolves a company's own website / investor-relations / newsroom / press-release feed from a **curated verified issuer allowlist** (AAPL/MSFT/NVDA/GOOGL/AMZN/TSLA/META), `profile.website`, SEC/GLEIF sites and an optional configured search provider — domain-brand verified, social-media/low-quality domains rejected, **never fabricated**; **exchange-aware query planning** (`news_query_planner`, bounded ≤10, exact legal name + ticker, **no recommendation/stock-prediction phrases**); a **configurable real news/search provider** (`ConfigurableWebNewsProvider` env-key JSON via `NEWS_PROVIDER_NAME`/`NEWS_API_KEY`/`NEWS_API_BASE_URL`, or no-key `GdeltNewsProvider`) — optional and non-blocking (missing config / HTTP error / rate-limit / malformed → warning); and deterministic **relevance scoring** (`news_relevance_scorer`) that separates **company-specific** catalysts from **industry/sector context** (industry is never a direct company catalyst). **Source tiers:** company-owned sources **T1**; SEC **T2**; exchange/listing-venue profile pages **T3** (not a regulator — never promoted to T1/T2); trusted media **T4**; aggregator/search **T5** (never promoted unless a resolved trusted-media host maps it to T4); the catalyst label stays **T6_model_estimate**. Coverage improves from `filings_only` to `limited`/`adequate`/`strong` when real company/news/industry evidence exists. No paid provider required; no live external call in CI. Underlying Phase 24 — News + Catalyst Discovery. A source-backed catalyst layer feeds the `free_real`/`eodhd_free_real` reports: **SEC EDGAR recent filings** (submissions endpoint, T2) → recent 8-K/10-Q/10-K/6-K/20-F/DEF 14A/S-registration events with 8-K item-number parsing; **company press releases / IR feeds** (the issuer's own newsroom RSS/Atom, T1 — company-owned primary source; the `SourceTier` enum has no separate `T1_primary_company_source`, so `T1_primary_filing` is reused and documented as such); and an **optional news provider** (aggregator/search API, T5) that is `NullNewsProvider` by default (no key, no paid dependency, no live CI call). The **catalyst category/direction/strength label is always T6_model_estimate** (deterministic `catalyst_classifier`); the underlying evidence keeps its real tier and is never upgraded (aggregator news is never promoted to T1/T2). "Source fact" and "model interpretation" are kept strictly distinct. No paid EODHD `/fundamentals`, no paid news API. Underlying Phase 19.4.1 — Enrichment Completeness Consistency (on Phase 19.4 Identity + Sector + Market-Metric Enrichment). The completeness layer now consumes the *enriched* snapshot, so an enriched-and-present field (LEI, sector classification, derived market cap / EV / P/E, 52-week range, shares) is no longer reported as a missing/blocking gap and no longer triggers an "Obtain LEI"/"metric unavailable" recommendation; genuinely-missing fields (ISIN, EBITDA, EV/EBITDA, beta, website, IPO date) stay gaps and nothing is fabricated. On top of the Phase 19.3.1 SEC-normalized fundamentals, the free_real snapshot is enriched with identity/profile and derived market metrics from free sources only: sector (DB value or **inferred** from the SEC SIC classification, T6), industry/website (SEC submissions, T2), LEI (GLEIF registry, T2, accepted only on a legal-name match); and — when inputs exist — latest close + **52-week high/low** (price history, T5), **shares outstanding** (SEC `dei:EntityCommonStockSharesOutstanding`, T2), **market cap = close × shares**, **enterprise value = market cap + debt − cash**, and **P/E** (all DERIVED ESTIMATES, T6, with cited inputs). **LEI, ISIN, IPO date, EBITDA, EV/EBITDA and beta are never fabricated** — left missing with a warning when unavailable. Derived market cap / EV / P/E are internal review aids, never official figures or a valuation conclusion. **Freshness (19.3.1):** the normalizer selects the latest annual filing across all us-gaap alias concepts (filed date breaks ties, full-year periods preferred, stale-year warning). Builds on Phase 19.2.1 (Free Real Data Provider Stack: SEC EDGAR XBRL (T2) + EODHD price-only (free plan) + Stooq prices + Trend Signal Engine (T6); composite free_real / eodhd_free_real providers; Stooq→EODHD fallback surfaced in provider warnings). No paid EODHD `/fundamentals` required.

This document defines the permitted source universe, tier classification, and provider implementation notes for InvestingBuddy.

Every financial claim in an agent-produced report must trace to one of the tier definitions below. Agents are prohibited from citing sources outside this taxonomy (e.g. Reddit, StockTwits, promotional newsletters, anonymous blogs).

---

## Source Tier Definitions

The tier system encodes evidential authority. Lower numbers = more authoritative.

| Tier | Name | Authority |
|---|---|---|
| T1 | `T1_primary_filing` | Company's own regulated disclosures: annual reports, 10-K/10-Q/40-F, NI 43-101/JORC technical reports, MD&A, prospectuses, investor presentations, earnings transcripts |
| T2 | `T2_regulator_or_gov` | Government, regulator, or multilateral body: SEC EDGAR, SEDAR+, ASX, USGS, IEA, IRENA, EIA, Eurostat, IAEA, central banks, OECD |
| T3 | `T3_industry_specialist` | Recognised trade bodies and specialist industry analysts: Wood Mackenzie, Benchmark Mineral Intelligence (reference only), IFA, ENTSO-E, Baltic Exchange |
| T4 | `T4_quality_media` | Editorial journalism with standards: FT, Reuters, Bloomberg News, Nikkei, regional quality press |
| T5 | `T5_api_aggregator` | Structured-data vendors that aggregate from primary sources: **EODHD**, Stooq, Alpha Vantage, Tiingo, FMP |
| T6 | `T6_model_estimate` | Agent's own calculation or inference — always show method and inputs |

**EODHD is classified as T5_api_aggregator.** Even when EODHD data originates from a T1/T2 filing (e.g. a 10-K via SEC EDGAR), the source tier is T5 unless the agent independently verified the underlying filing. See [eodhd_mapping.json](../packages/research-contracts/real_asset_equity/v1/eodhd_mapping.json) for field-level guidance.

---

## Data Quality Flags

Every datapoint in a real-asset report must carry a `data_quality` flag:

| Flag | Meaning |
|---|---|
| `A_verified` | Cross-confirmed by ≥2 independent sources OR direct from a T1/T2 filing |
| `B_single_credible` | Single credible T1–T4 source, uncontested |
| `C_inferred` | Agent inference/estimate from credible inputs — method must be shown |
| `D_weak_or_stale` | Weak, stale (>18 months for fast-moving data), contested, or proxy only |

Any `D_weak_or_stale` value in a decision-critical field (snapshot financials, real asset block, financials, valuation, scoring) triggers a `data_quality_warning` in the validation result and must be surfaced in the report's `self_critique.data_quality_warnings` array.

---

## Hard Block-List

Agents must never cite these as primary or supporting sources:

- Reddit, StockTwits, X (Twitter) as primary
- Retail investor forums
- Promotional newsletters or paid stock-promotion sites
- Anonymous blogs or generic content farms
- Unattributed social media posts

---

## Contract Files

The formal source taxonomy is machine-readable and version-controlled:

```
packages/research-contracts/real_asset_equity/v1/
├── source_taxonomy.json      # Full tier-ranked, per-commodity source catalogue
├── eodhd_mapping.json        # Provider mapping: schema field → EODHD endpoint + fallbacks
├── report_schema.json        # JSON Schema Draft 2020-12 — the output contract
├── alpha_sourcing_strategy.md # Discovery methodology (supply-chain laddering, event triggers)
├── example_report_filled.json # Fictional worked example; validates against the schema
└── README.md                  # Package overview
```

These files are the ground truth for:
- Which sources agents are permitted to cite
- How each report field maps to a data provider
- How to swap providers without changing the report schema

---

## Provider Abstraction (Phase 4 — Implemented)

The report schema is **provider-agnostic**. `eodhd_mapping.json` is a mapping layer, not a hardcoded dependency. To switch or add a provider, edit only the mapping file — the schema never changes.

### Phase 14 Screener Usage (EODHD Search)

`CompanyScreener` can accept live EODHD search results via `eodhd_search_results` parameter.
These come from `GET https://eodhd.com/api/search/...` and are classified as `T5_api_aggregator`
with data quality `B_single_credible`.

Screener-stage EODHD data never advances beyond `T5` in the source tier. The T5 validation
warning is appended to every EODHD-sourced candidate:
`"Candidate requires primary-source validation before final analysis."`

No EODHD API key is needed in CI — the screener tests use fixture-backed offline results.
Live screening requires `EODHD_API_KEY` (same key as `EodhdProvider`).

### Provider Registry

All providers are registered in `FinancialDataService` and selectable via `FINANCIAL_DATA_PROVIDER` config:

| Class | Module | Source Tier | Status | Notes |
|---|---|---|---|---|
| `MockFinancialDataProvider` | `integrations/providers/mock_provider.py` | T6 | ✅ Active | Deterministic demo data; used in all CI tests; no network calls |
| `SecEdgarProvider` | `integrations/providers/sec_edgar_provider.py` | T2 | ✅ Live (CIK) | Free; `get_company_by_cik(cik)` fetches from `data.sec.gov`; no API key; ticker→CIK via index |
| `SecEdgarFundamentalsProvider` | `integrations/providers/sec_edgar_fundamentals.py` | T2 | ✅ Live (Phase 19.1; normalized 19.3) | Free; ticker→CIK resolution via company_tickers.json; XBRL companyfacts; 10 core us-gaap concepts + Phase 19.3 normalized metrics (gross/operating income, capex, FCF, cash, total debt, derived margins/ROE/D-E/YoY); no API key; US only |
| `sec_fundamentals_normalizer` | `integrations/sec_fundamentals_normalizer.py` | T2 | ✅ Live (Phase 19.3; freshness fix 19.3.1) | Pure/offline; maps us-gaap companyfacts → normalized income-statement / cash-flow / balance-sheet metrics + derived ratios. **19.3.1:** selects the latest annual across ALL alias concepts (filed date breaks fiscal-year ties, so a stale tag like Apple's `Revenues`→FY2018 can no longer shadow the current tag), prefers full-year periods over embedded Q4 slices, warns on stale annual years; 10-Q fallback with warning; EBITDA never fabricated |
| `GleifProvider` | `integrations/providers/gleif_provider.py` | T2 | ✅ Live | Free; LEI lookup by code or name; `api.gleif.org`; no API key |
| `StooqProvider` | `integrations/providers/stooq_provider.py` | T5 | ✅ Live | Free; live OHLCV CSV from `stooq.com`; no API key |
| `EodhdPriceOnlyProvider` | `integrations/providers/eodhd_price_only_provider.py` | T5 | ✅ Live (Phase 19.1) | Free plan; requires `EODHD_API_KEY`; `/eod` only — no `/fundamentals`; warns on missing fundamentals |
| `FreeRealProvider` | `integrations/providers/free_real_provider.py` | T2+T5 | ✅ Live (Phase 19.1) | Composite: Stooq (price) + SEC EDGAR (profile + fundamentals); no keys needed; US equity focus |
| `EodhdFreeRealProvider` | `integrations/providers/free_real_provider.py` | T2+T5 | ✅ Live (Phase 19.1) | Composite: EODHD /eod (price) + SEC EDGAR (fundamentals); requires `EODHD_API_KEY` free plan |
| `OpenBBProvider` | `integrations/providers/openbb_provider.py` | T5 | Evaluation placeholder | Not yet integrated; requires `openbb-platform`; evaluate before Phase 6 |
| `EodhdProvider` | `integrations/providers/eodhd_provider.py` | T5 | ✅ Live (Phase 13) | Paid; requires `EODHD_API_KEY` paid plan; company profile, price history, fundamentals; excluded from CI |
| `SecRecentFilingsProvider` | `integrations/providers/sec_recent_filings_provider.py` | T2 | ✅ Live (Phase 24) | Free; SEC submissions endpoint; recent filing events + 8-K item parsing → `CatalystEvent`; reuses SEC CIK resolution; offline-parseable |
| `CompanyPressReleaseProvider` | `integrations/providers/company_press_release_provider.py` | T1 | ✅ Live (Phase 24) | Free; conservative RSS/Atom feed discovery off the issuer website (company-owned primary source); no crawl; graceful "unavailable" when no feed |
| `NullNewsProvider` / `EnvConfiguredNewsProvider` / `ConfigurableWebNewsProvider` / `GdeltNewsProvider` | `integrations/providers/free_news_provider.py` | T5 (mapped T4 for trusted media) | ✅ Live (Phase 24 / 24.1) | Optional news abstraction with a `search(query)` primitive; **null by default** (no key, no CI call); env-gated generic JSON provider via `NEWS_PROVIDER_NAME`/`NEWS_API_KEY`/`NEWS_API_BASE_URL` (`NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS`); no-key GDELT adapter (`NEWS_PROVIDER_NAME=gdelt`) |
| `CompanySourceDiscoveryService` + `exchange_source_registry` | `services/company_source_discovery_service.py`, `integrations/exchange_source_registry.py` | T1 (company) / T3 (exchange) | ✅ Live (Phase 24.1) | Curated verified issuer allowlist + `profile.website` + SEC/GLEIF + optional search → company website / IR / newsroom / press-release feed; domain-brand verified; social-media/low-quality rejected; **never fabricated** |
| `NewsQueryPlanner` + `NewsRelevanceScorer` | `services/news_query_planner.py`, `services/news_relevance_scorer.py` | (planning / T6 relevance) | ✅ Live (Phase 24.1) | Bounded recommendation-free query plan (company/industry/exchange/primary-source/regulatory); deterministic 0–1 relevance separating company-specific vs industry-context items; filters food-brand ambiguity + prediction spam |

**Phase 24 catalyst tiers.** SEC filing events stay **T2**; company press releases are **T1** (company-owned primary source — `T1_primary_filing` reused, see status note); aggregator/search news is **T5** and is **never promoted** to T1/T2. The model-derived catalyst label (category / direction / strength) is **always T6_model_estimate** and is kept strictly separate from the underlying source evidence. No paid news API is required; `discover_catalysts` is non-blocking and makes no external call in CI.

### Provider Abstract Interface

All providers implement `FinancialDataProvider` (`integrations/financial_data_provider.py`):

```python
class FinancialDataProvider(ABC):
    provider_name: str
    source_tier: SourceTier
    get_supported_capabilities() -> list[ProviderCapability]
    get_provider_status() -> ProviderStatus
    async get_company_profile(ticker, exchange) -> CompanyProfileData
    async get_price_history(ticker, exchange, start_date, end_date) -> PriceHistoryData
    async get_fundamentals(ticker, exchange) -> FundamentalsData
```

### Typed Output Schemas

Every provider output uses typed Pydantic schemas. Each response carries full provenance:

```python
class CompanyProfileData(BaseModel):
    ticker: str
    legal_name: str
    source_url: str | None
    data_quality: DataQuality
    meta: ProviderResponseMetadata   # provider_name, source_tier, retrieved_at, is_mock

class PriceHistoryData(BaseModel):
    ticker: str
    currency: str
    price_points: list[PricePoint]
    data_quality: DataQuality
    meta: ProviderResponseMetadata

class FundamentalsData(BaseModel):
    ticker: str
    datapoints: list[FundamentalDataPoint]  # each carries source_tier, data_quality, note
    meta: ProviderResponseMetadata
```

### Provider Selection

Set `FINANCIAL_DATA_PROVIDER` in `.env`:

```
FINANCIAL_DATA_PROVIDER=mock              # default; CI; local dev without credentials
FINANCIAL_DATA_PROVIDER=free_real         # Stooq + SEC EDGAR; no keys needed; US equity focus
FINANCIAL_DATA_PROVIDER=eodhd_free_real   # EODHD /eod + SEC EDGAR; requires EODHD_API_KEY (free plan)
FINANCIAL_DATA_PROVIDER=eodhd_price_only  # EODHD /eod only; requires EODHD_API_KEY (free plan)
FINANCIAL_DATA_PROVIDER=eodhd             # Full EODHD: requires paid EODHD_API_KEY
```

### Phase 19.1: Free Real Data Stack

**Provider stack: `free_real`** (no API keys required)
- **Price data**: Stooq.com (`T5_api_aggregator`) — free OHLCV for global exchanges
- **Fundamentals**: SEC EDGAR XBRL (`T2_regulator_or_gov`) — us-gaap concepts from 10-K / 20-F
- **Profile**: SEC EDGAR submissions (`T2_regulator_or_gov`) — ticker→CIK resolved via company_tickers.json
- **Trend signals**: `TrendSignalEngine` (`T6_model_estimate`) — computed from price data

**Known limitation (staging):** Stooq.com appears blocked from Azure outbound network (observed 2026-07-11 staging smoke test). Phase 19.2 added a **non-blocking fallback to EODHD price-only** when Stooq is unavailable, so `free_real` on Azure now returns SEC fundamentals + EODHD price + trend signals (`is_mock=False`). If EODHD is also unavailable, `free_real` degrades to SEC-fundamentals-only. `free_real` works correctly from local or non-Azure environments (Stooq direct).

**Provider warning surfacing (Phase 19.2.1):** the Stooq→EODHD fallback reason is lifted out of `price.meta.note` and surfaced in the report's **Provider Warnings** section via `summarize_price_provider_warning()`:
- Stooq failed and EODHD fallback used → `"Stooq price provider unavailable; used EODHD price-only fallback."`
- Both price providers failed → `"No usable price history available; trend signals unavailable."`

Wording is internal and factual; it does not overstate reliability and contains no secrets.

**Provider stack: `eodhd_free_real`** (free EODHD API key required)
- **Price data**: EODHD `/eod` (`T5_api_aggregator`) — EODHD free plan covers `/eod`; `/fundamentals` not called
- **Fundamentals**: SEC EDGAR XBRL (`T2_regulator_or_gov`)
- **Profile**: SEC EDGAR submissions (with EODHD stub fallback for non-US tickers)

**EODHD free plan limitation:**
The EODHD free API key covers `/eod` (end-of-day prices) but returns HTTP 403 for `/fundamentals`.
`EodhdPriceOnlyProvider` and `EodhdFreeRealProvider` intentionally never call `/fundamentals`.
SEC EDGAR XBRL replaces EODHD fundamentals for U.S.-listed companies.
For non-US international fundamentals, a paid EODHD plan is required (use `EodhdProvider`).

**Trend Signal Engine (`apps/api/app/integrations/trend_signal_engine.py`):**
- Internal-only; outputs are never published directly
- Labels: `positive_momentum_candidate`, `neutral_momentum`, `negative_momentum`, `insufficient_price_history`
- No BUY/SELL/HOLD/WATCH — strictly prohibited
- Source tier: T6_model_estimate (computed from T5 price data)
- Metrics: 1M/3M/6M returns, 50-day MA deviation, 200-day MA deviation, relative strength vs benchmark
- Wired into the `company_analysis` workflow as of Phase 19.2 (T6 trend signals in analysis state + draft report)

**Phase 19.2 delivered for free_real stack:** ✅
- `TrendSignalEngine` wired as a workflow signal (T6 trend signals in analysis state)
- Composite provider tracking preserved (`contributing_providers`, `requested_provider_name`)
- Stooq failure non-blocking; falls back to EODHD /eod price-only
- EODHD /eod price data visible as T5 in workflow snapshot + draft report
- AAPL `provider=free_real` produces SEC + price + trend + final report with `safety_valid=True` on staging

**Phase 19.2.1 delivered (observability):** ✅
- Stooq→EODHD fallback reason surfaced in `provider_warnings` (see above)
- `scoring_engine` no longer raises `TypeError` when a real provider omits `sector` (coalesced to `""`)

### Phase 19.3: SEC Fundamentals Normalization

`sec_fundamentals_normalizer.normalize_company_facts()` turns raw SEC XBRL companyfacts into a
`NormalizedSecFinancials` structure. It selects the **latest annual** value per concept
(`form ∈ {10-K, 20-F, …}`, `fp=FY`), keeps the prior fiscal year for YoY growth, and falls back to
the **latest 10-Q with a warning** when no annual filing exists. All dollar values are scaled to
millions (`USD_m`); margins/ROE/growth are percentages; debt-to-equity is a ratio (`x`).

**us-gaap → normalized concept map:**

| Normalized field | us-gaap concept(s) tried (first match wins) |
|---|---|
| `revenue` | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, `RevenueFromContractWithCustomerIncludingAssessedTax` |
| `gross_profit` | `GrossProfit` |
| `operating_income` | `OperatingIncomeLoss` |
| `net_income` | `NetIncomeLoss`, `ProfitLoss` |
| `eps_basic` / `eps_diluted` | `EarningsPerShareBasic` / `EarningsPerShareDiluted` |
| `operating_cash_flow` | `NetCashProvidedByUsedInOperatingActivities`(`…ContinuingOperations`) |
| `capital_expenditures` | `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets` |
| `total_assets` | `Assets` |
| `total_liabilities` | `Liabilities` |
| `shareholders_equity` | `StockholdersEquity`, `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| `cash_and_equivalents` | `CashAndCashEquivalentsAtCarryingValue`, `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| `long_term_debt` | `LongTermDebt`, `LongTermDebtNoncurrent` |
| `short_term_debt` | `DebtCurrent`, `ShortTermBorrowings`, `LongTermDebtCurrent` |
| `shares_outstanding` | `dei:EntityCommonStockSharesOutstanding` (often absent → market cap not computed) |

**Derived metrics (marked `C_inferred`):** `free_cash_flow = OCF − capex`; `total_debt = short + long`;
`gross/operating/net_margin`, `free_cash_flow_margin`, `return_on_equity`, `debt_to_equity`;
`revenue/net_income/free_cash_flow` YoY growth (annual only).

**Never fabricated:** `EBITDA` (no D&A extracted at this phase), `market_cap` and `enterprise_value`
(require price × shares; shares usually absent from statement data). These stay missing with a warning.

**Downstream effect:** the normalized fields land in `fundamentals_summary` (`enrich_snapshot_with_free_real`),
the `FinancialDataAgent` narrates them and marks ~10 financial categories available, and the
`ValuationGuardAgent` reaches `valuation_readiness=partial` while keeping every valuation **conclusion**
blocked. No BUY/SELL/HOLD/WATCH, price target, fair value or upside is ever produced.

**News/Catalyst Interface (`apps/api/app/integrations/news_catalyst_provider.py`):**
- `NullNewsCatalystProvider` — default; returns empty events + warning; no crash
- `SecEdgar8KProvider` — free; fetches recent 8-K filings from SEC EDGAR submissions; T2 tier
- Configure: `NEWS_CATALYST_PROVIDER=sec_8k` in environment

**AAPL expected behavior (provider=free_real or eodhd_free_real):**
1. CIK resolved: 320193 (via company_tickers.json)
2. SEC EDGAR submissions: Apple Inc. profile (T2)
3. XBRL companyfacts: revenue 383,285 USD_m, net income 96,995 USD_m, total assets 352,583 USD_m (FY2023, 10-K)
4. Price history: 250+ trading days of OHLCV from Stooq or EODHD /eod
5. Trend signals: returns, MA deviations, internal momentum label
6. `is_mock=False` when any real provider succeeded
7. Partial warnings when a source is unavailable — not a failure

### Provider Rules

- **No live API calls in CI** — CI must use `MockFinancialDataProvider` (the default)
- **EODHD API key must not be hardcoded** — store in `.env` (local) or Azure Key Vault (production)
- **Provider output must carry correct source tier** — EODHD → T5; EDGAR direct → T2; company IR → T1
- **Mock data must be flagged** — `is_mock=True` in `ProviderResponseMetadata`; `D_weak_or_stale` data quality
- **Live provider integration tests must be opt-in** — set `ENABLE_INTEGRATION_TESTS=true` locally; never in CI

### Source and Citation Integration (Phase 6 — Implemented)

The Phase 6 workflow uses provider data to create source records and structured citations automatically.

**Workflow flow:**
1. `fetch_provider_data` — calls `FinancialDataService` (default: `MockFinancialDataProvider`)
2. `create_source_records` — calls `build_source_record()` + `source_service.get_or_create_source()` for each data item
3. `create_citations` — creates `Citation` records with `field_path`, `source_tier`, `data_quality`

**Citation field_path examples:**
```
identity.legal_name       → Citation for the company's legal name from provider
identity.country_domicile → Citation for domicile from provider
profile.sector            → Citation for sector classification
price_history.latest_close → Citation for the most recent price point
```

**Source record creation example:**

When a provider returns data, prepare a `Source` database record using the helper:

```python
from app.integrations.financial_data_provider import build_source_record

attrs = build_source_record(
    meta=response.meta,
    source_url=response.source_url,
    title=f"Stooq prices — {ticker}",
    data_quality=DataQuality.B_single_credible,
)
# attrs.source_type, attrs.credibility_score, attrs.retrieved_at etc. are all set
# Pass to source_service.create_source() for DB persistence
```

Tier → source_type → credibility mapping:

| Tier | source_type | credibility_score |
|---|---|---|
| T1 | `company_filing` | 0.95 |
| T2 | `government_data` | 0.90 |
| T3 | `industry_report` | 0.75 |
| T4 | `news_article` | 0.65 |
| T5 | `financial_data_api` | 0.55 |
| T6 | `model_estimate` | 0.20 |

### OpenBB Evaluation Note

`OpenBBProvider` remains a skeleton placeholder. OpenBB should be evaluated before Phase 6 on the following criteria:
1. Does `openbb-platform` add meaningful data sources not covered by Stooq / GLEIF / SEC EDGAR?
2. Does it require API keys for useful coverage?
3. Does adding it as a dependency create CI or packaging complexity?

Decision: **Do not add as a required dependency until the above is answered.**

---

## Free Source Index (Selected)

The full list is in `source_taxonomy.json`. Key free sources relevant to the real-asset universe:

### Financials & Market Data
- **Stooq** — historical OHLCV for many global exchanges, no key
- **FRED (St Louis Fed)** — macro and FX data, free JSON API
- **World Bank Pink Sheet** — monthly commodity price benchmarks, free

### Company Filings
- **SEC EDGAR** (`data.sec.gov`) — US 10-K/10-Q/8-K/Form 4, free JSON API
- **SEDAR+** — Canadian filings including NI 43-101, free (critical for mining)
- **ASX Announcements** — JORC reports, quarterly activities, free

### Industry / Commodity
- **USGS Mineral Commodity Summaries** — 90+ minerals, CC0, CSV+PDF
- **IEA Reports** — energy transition, grid, capacity outlooks (many free)
- **ENTSO-E Transparency** — European grid/transmission data, free API
- **GLEIF LEI Registry** — entity identity for cross-border verification, free

### Discovery / Event Feeds
- **USAspending.gov** — US government contracts, free
- **EU TED** — EU procurement notices, free
- **UN Comtrade** — trade flow data, free tier
- **Google Patents / USPTO / EPO Espacenet** — patent monitoring, free

---

## Relationship to Citation System

Phase 3 implemented the `sources` and `citations` database tables and the `CitationValidator` agent.

Phase 4 extends this by:
1. Mapping `source_taxonomy.json` tiers to `sources.source_type` values in the database
2. Having providers set `source_tier` on every datapoint before it enters a report
3. Having `CitationValidator` check both:
   - Database citations (existing Phase 3 behaviour)
   - Report schema datapoint source fields (new: every `datapoint.source_tier` must be present and not `T6_model_estimate` for decision-critical fields)
4. Blocking final reports that contain unsourced financial numbers (bare values without a datapoint wrapper)

See `docs/AGENTS.md` for how the CitationValidator is upgraded in Phase 4.
