# Data Sources

## Status: Phase 4 — Financial Data Provider Foundation implemented

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

### Provider Registry

All providers are registered in `FinancialDataService` and selectable via `FINANCIAL_DATA_PROVIDER` config:

| Class | Module | Source Tier | Status | Notes |
|---|---|---|---|---|
| `MockFinancialDataProvider` | `integrations/providers/mock_provider.py` | T6 | ✅ Active | Deterministic demo data; used in all CI tests; no network calls |
| `SecEdgarProvider` | `integrations/providers/sec_edgar_provider.py` | T2 | Skeleton | Free; US regulatory filings; `data.sec.gov` JSON API |
| `GleifProvider` | `integrations/providers/gleif_provider.py` | T2 | Skeleton | Free; LEI registry; `api.gleif.org` |
| `StooqProvider` | `integrations/providers/stooq_provider.py` | T5 | Skeleton | Free; historical OHLCV prices; no API key |
| `OpenBBProvider` | `integrations/providers/openbb_provider.py` | T5 | Skeleton | Free tier; multi-source aggregator; requires `openbb-platform` |
| `EodhdProvider` | `integrations/providers/eodhd_provider.py` | T5 | Placeholder | Paid; requires `EODHD_API_KEY`; excluded from CI |

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
FINANCIAL_DATA_PROVIDER=mock   # default; CI; local dev without credentials
FINANCIAL_DATA_PROVIDER=eodhd  # requires EODHD_API_KEY
```

### Provider Rules

- **No live API calls in CI** — CI must use `MockFinancialDataProvider` (the default)
- **EODHD API key must not be hardcoded** — store in `.env` (local) or Azure Key Vault (production)
- **Provider output must carry correct source tier** — EODHD → T5; EDGAR direct → T2; company IR → T1
- **Mock data must be flagged** — `is_mock=True` in `ProviderResponseMetadata`; `D_weak_or_stale` data quality

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
