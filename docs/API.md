# API Reference

## Status: Phase 6 — Company snapshot workflow with provider + validation summary

---

## Base URLs

```
Development:    http://localhost:8000
Staging:        https://api-staging.investingbuddy.com (future)
Production:     https://api.investingbuddy.com (future)
```

Interactive docs (development only):
```
http://localhost:8000/api/docs      (Swagger UI)
http://localhost:8000/api/redoc     (ReDoc)
```

---

## API Tiers

| Prefix | Auth Required | Purpose |
|---|---|---|
| `/api/v1/` | No (Phase 2) | Core CRUD and workflow endpoints |
| `/api/me/` | Yes (Phase 7) | Authenticated user-specific data |
| `/api/admin/` | Admin role (future) | Platform management |

Authentication via Clerk JWT is planned for Phase 7. No auth is enforced yet.

---

## Implemented Endpoints

### Health

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/health` | ✅ Live | Application health check |

**Response:**
```json
{ "status": "ok", "environment": "development", "version": "0.3.0" }
```

---

### Companies

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/companies` | ✅ Live | Add a company to the research universe |
| GET | `/api/v1/companies` | ✅ Live | List all companies |
| GET | `/api/v1/companies/{id}` | ✅ Live | Get company by UUID |

**POST /api/v1/companies** — Create a company

Request:
```json
{
  "ticker": "VOW3",
  "exchange": "XETRA",
  "name": "Volkswagen AG",
  "country": "Germany",
  "region": "Europe",
  "sector": "Automotive",
  "industry": "Auto Manufacturers",
  "market_cap": 60000000000.0,
  "currency": "EUR",
  "website": "https://www.volkswagenag.com",
  "description": "German automobile manufacturer."
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "ticker": "VOW3",
  "exchange": "XETRA",
  "name": "Volkswagen AG",
  "status": "new",
  "created_at": "2026-06-16T12:00:00Z",
  "updated_at": "2026-06-16T12:00:00Z",
  ...
}
```

Errors:
- `409 Conflict` — ticker + exchange combination already exists
- `422 Unprocessable Content` — validation failure (missing required fields)

**GET /api/v1/companies** — List companies

Query parameters:
- `limit` (int, default 50) — max items to return
- `offset` (int, default 0) — pagination offset

Response `200 OK`:
```json
{
  "items": [ { ...company... } ],
  "total": 42
}
```

**GET /api/v1/companies/{company_id}** — Get company by ID

Response `200 OK`: company object
Error `404 Not Found`: company does not exist

---

### Workflows

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/workflows/company-analysis/run` | ✅ Live | Trigger company analysis workflow |

**POST /api/v1/workflows/company-analysis/run** — Trigger workflow

Supply either `company_id` (UUID of existing company) or `ticker` + `exchange`.

Request by company ID:
```json
{ "company_id": "11111111-1111-1111-1111-111111111111" }
```

Request by ticker with provider control (Phase 6):
```json
{
  "ticker": "VOW3",
  "exchange": "XETRA",
  "provider_name": "mock",
  "require_schema_valid": false
}
```

`provider_name` — optional, defaults to `FINANCIAL_DATA_PROVIDER` config value (`mock` in CI).
`require_schema_valid` — optional bool (default `false`). When `true`, the endpoint returns `422` if the schema draft fails validation.

Response `202 Accepted` (Phase 6):
```json
{
  "agent_run_id": "uuid",
  "draft_report_id": "uuid",
  "status": "completed",
  "summary": "Provider snapshot for Volkswagen AG. Provider: mock. Schema: invalid.",
  "workflow_name": "company_analysis",
  "company_name": "Volkswagen AG",
  "ticker": "VOW3",
  "provider_name": "mock",
  "is_mock": true,
  "schema_valid": false,
  "validation_errors": ["[(root)] 'snapshot_financials' is a required property"],
  "validation_warnings": [],
  "missing_fields": ["identity.isin", "identity.lei", "profile.website"]
}
```

Errors:
- `422` — no company_id or ticker provided
- `422` — company not found in database
- `422` — unknown provider_name (not in registry)
- `422` — `require_schema_valid=true` and schema draft failed validation
- `500` — workflow execution error (see agent_run logs)

> **Phase 6 note:** The workflow fetches provider data via `FinancialDataService`,
> builds a structured company snapshot, creates `Source` + `Citation` records with
> `field_path`, `source_tier`, `data_quality`, validates against the real-asset report schema,
> and saves a draft report. No LLM calls. No investment recommendations.
> Default provider is `mock` — all CI runs are offline.
> `schema_valid` will typically be `false` at this phase since many required schema
> sections are not yet populated (LLM agents are needed for those).

---

### Sources

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/sources` | ✅ Live | Create or return existing source (dedup by hash/URL) |
| GET | `/api/v1/sources` | ✅ Live | List all sources |
| GET | `/api/v1/sources/{source_id}` | ✅ Live | Get source by UUID |

**POST /api/v1/sources** — Create or deduplicate a source

Deduplication order: `content_hash` first, then `url`. If a match is found the existing record is returned with HTTP 200. A new record returns HTTP 201.

Request:
```json
{
  "source_type": "news_article",
  "title": "Volkswagen Q4 Results 2025",
  "url": "https://example.com/vow3-q4-2025",
  "publisher": "Reuters",
  "credibility_score": 0.85
}
```

Response `201 Created` (new) or `200 OK` (existing):
```json
{
  "id": "uuid",
  "source_type": "news_article",
  "title": "Volkswagen Q4 Results 2025",
  "url": "https://example.com/vow3-q4-2025",
  "publisher": "Reuters",
  "retrieved_at": "2026-06-20T10:00:00Z",
  "credibility_score": 0.85,
  "created_at": "2026-06-20T10:00:00Z"
}
```

Errors:
- `422` — invalid `source_type` (must be one of the 13 valid values; see `docs/DATABASE.md`)

**GET /api/v1/sources** — List sources

Query parameters: `limit` (default 50), `offset` (default 0)

Response `200 OK`:
```json
{ "items": [ { ...source... } ], "total": 12 }
```

**GET /api/v1/sources/{source_id}** — Get source by UUID

Response `200 OK`: source object
Error `404 Not Found`: source does not exist

---

### Citations

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/reports/{report_id}/citations` | ✅ Live | Add a citation to a report |
| GET | `/api/v1/reports/{report_id}/citations` | ✅ Live | List citations for a report |
| POST | `/api/v1/reports/{report_id}/validate-citations` | ✅ Live | Validate citation coverage for a draft report |

**POST /api/v1/reports/{report_id}/citations** — Add citation

Request:
```json
{
  "source_id": "uuid-of-source",
  "claim_text": "thesis",
  "source_quote": "Revenue declined 8% YoY in Q4 2025."
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "source_id": "uuid",
  "report_id": "uuid",
  "agent_run_id": null,
  "claim_text": "thesis",
  "source_quote": "Revenue declined 8% YoY in Q4 2025.",
  "url": null,
  "retrieved_at": null,
  "created_at": "2026-06-20T10:00:00Z"
}
```

Errors:
- `404` — report not found
- `422` — source_id not found or missing

**GET /api/v1/reports/{report_id}/citations** — List citations

Response `200 OK`:
```json
{ "items": [ { ...citation... } ], "total": 3 }
```

**POST /api/v1/reports/{report_id}/validate-citations** — Validate citation coverage

Runs a structural (non-LLM) check: are thesis, rating, and financial_metrics sections cited?

Response `200 OK`:
```json
{
  "status": "ok" | "warnings" | "failed",
  "total_claims": 3,
  "cited_claims": 2,
  "missing_citations": [
    { "section": "financial_metrics", "description": "No source linked." }
  ],
  "approved_claims": ["thesis"],
  "warnings": ["[PLACEHOLDER] Analysis output is marked is_placeholder=true."]
}
```

> **Phase 3 note:** Validation is purely structural — no LLM calls.
> `is_placeholder=true` outputs always return `status: "warnings"`.
> Full LLM-powered fact-checking is planned for Phase 4.

---

## Standard Error Response

```json
{ "detail": "Human-readable error message" }
```

| Status | Meaning |
|---|---|
| 404 | Resource not found |
| 409 | Conflict (duplicate) |
| 422 | Validation error or business logic rejection |
| 500 | Internal server error |

---

---

### Financial Data (Dev / Smoke-Test)

These endpoints are for **development and provider smoke-testing only**. They do not produce real investment advice. They are not user-facing endpoints.

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/api/v1/financial-data/providers` | ✅ Live | List all registered providers with capabilities and status |
| GET | `/api/v1/financial-data/mock/company/{ticker}` | ✅ Live | Company profile from mock provider (demo data only) |
| GET | `/api/v1/financial-data/mock/prices/{ticker}` | ✅ Live | Price history from mock provider (demo data only) |
| GET | `/api/v1/financial-data/stooq/prices/{ticker}` | ✅ Live (network) | Live OHLCV price history from Stooq (T5, free) |
| GET | `/api/v1/financial-data/gleif/entity/{lei_or_name}` | ✅ Live (network) | Legal entity lookup from GLEIF registry (T2, free) |
| GET | `/api/v1/financial-data/sec-edgar/company/{cik}` | ✅ Live (network) | Company profile from SEC EDGAR by CIK (T2, free) |

**GET /api/v1/financial-data/providers** — List all providers

Response `200 OK`:
```json
[
  {
    "name": "mock",
    "source_tier": "T6_model_estimate",
    "capabilities": ["company_profile", "price_history", "fundamentals"],
    "status": "ok"
  },
  {
    "name": "eodhd",
    "source_tier": "T5_api_aggregator",
    "capabilities": ["company_profile", "price_history", "fundamentals", "insider_transactions", "news", "screener"],
    "status": "not_configured"
  }
]
```

**GET /api/v1/financial-data/mock/company/{ticker}** — Mock company profile

Query parameters: `exchange` (optional)

Response `200 OK`:
```json
{
  "ticker": "TEST",
  "exchange": "OSE",
  "legal_name": "Acme Nordic AS [MOCK]",
  "country_domicile": "Norway",
  "reporting_currency": "NOK",
  "data_quality": "D_weak_or_stale",
  "meta": {
    "provider_name": "mock",
    "source_tier": "T6_model_estimate",
    "retrieved_at": "2026-06-20T12:00:00Z",
    "is_mock": true,
    "status": "ok",
    "note": "DEMO DATA — generated by MockFinancialDataProvider. Not real financial data. Not investment advice."
  }
}
```

**GET /api/v1/financial-data/mock/prices/{ticker}** — Mock price history

Query parameters: `exchange`, `start_date`, `end_date` (all optional)

Response `200 OK`:
```json
{
  "ticker": "TEST",
  "exchange": "OSE",
  "currency": "NOK",
  "price_points": [
    { "date": "2026-01-02", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 123000 }
  ],
  "data_quality": "D_weak_or_stale",
  "meta": { "is_mock": true, "provider_name": "mock", ... }
}
```

> **Phase 4 note:** All `/financial-data/mock/*` responses are clearly marked `is_mock: true` and `data_quality: D_weak_or_stale`. They contain synthetic demo data from `MockFinancialDataProvider` and must not be used as real financial information.

---

**GET /api/v1/financial-data/stooq/prices/{ticker}** — Live Stooq price history

Makes a real external HTTP call to stooq.com. Returns OHLCV data. No API key required.

Query parameters: `exchange` (optional, e.g. NASDAQ, XETRA, LSE), `start_date`, `end_date` (YYYY-MM-DD)

Response `200 OK`:
```json
{
  "ticker": "AAPL",
  "exchange": "NASDAQ",
  "currency": "USD",
  "price_points": [
    { "date": "2026-06-13", "open": 194.79, "high": 195.87, "low": 193.97, "close": 194.35, "volume": 47484600 }
  ],
  "data_quality": "B_single_credible",
  "meta": { "provider_name": "stooq", "source_tier": "T5_api_aggregator", "is_mock": false }
}
```

Errors: `404` if ticker has no data on Stooq; `502` on network failure.

---

**GET /api/v1/financial-data/gleif/entity/{lei_or_name}** — GLEIF entity lookup

Makes a real external HTTP call to api.gleif.org. Pass a 20-character LEI (direct lookup) or a company name (search).

Response `200 OK`:
```json
{
  "ticker": "HWUPKR0MPOU8FGXBT394",
  "legal_name": "Apple Inc.",
  "lei": "HWUPKR0MPOU8FGXBT394",
  "country_domicile": "US",
  "data_quality": "A_verified",
  "meta": { "provider_name": "gleif", "source_tier": "T2_regulator_or_gov", "is_mock": false }
}
```

Errors: `404` if LEI not found or name search returns no results; `502` on network failure.

---

**GET /api/v1/financial-data/sec-edgar/company/{cik}** — SEC EDGAR company by CIK

Makes a real external HTTP call to data.sec.gov. CIK must be numeric (e.g. `320193` for Apple).

Response `200 OK`:
```json
{
  "ticker": "AAPL",
  "legal_name": "Apple Inc.",
  "country_domicile": "US",
  "reporting_currency": "USD",
  "fiscal_year_end": "September",
  "website": "https://www.apple.com",
  "data_quality": "A_verified",
  "meta": { "provider_name": "sec_edgar", "source_tier": "T2_regulator_or_gov", "is_mock": false }
}
```

Errors: `422` if CIK is not numeric; `404` if CIK not found; `502` on network failure.

> **Phase 4.5 note:** Stooq, GLEIF and SEC EDGAR endpoints make real external HTTP calls.
> They are for **developer diagnostics only** and must not be exposed to end users.
> Not investment advice. Set `FINANCIAL_DATA_PROVIDER=mock` in CI to use offline data.

---

## Planned Endpoints (Phase 5+)

### Public (unauthenticated)
| Method | Path | Phase |
|---|---|---|
| GET | `/api/v1/reports` | Phase 4 |
| GET | `/api/v1/reports/{slug}` | Phase 4 |
| GET | `/api/v1/themes` | Phase 4 |
| GET | `/api/v1/companies/{ticker}` | Phase 4 (public company page) |

### Admin
| Method | Path | Phase |
|---|---|---|
| GET | `/api/v1/admin/agent-runs` | Phase 4 |
| GET | `/api/v1/admin/agent-runs/{id}` | Phase 4 |
| POST | `/api/v1/admin/reports/{id}/publish` | Phase 4 |
| POST | `/api/v1/admin/reports/{id}/reject` | Phase 4 |
| GET | `/api/v1/admin/judge-evaluations` | Phase 6 |

### User (Authenticated, Version 2)
| Method | Path | Phase |
|---|---|---|
| GET | `/api/me/recommendations` | Phase 7 |
| GET | `/api/me/portfolio` | Phase 7 |
| POST | `/api/me/portfolio/positions` | Phase 7 |
