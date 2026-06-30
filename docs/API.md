# API Reference

## Status: Phase 11 — Admin Review / Approve-Reject Workflow; review action endpoints and audit log added

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

Request with LLM research sections enabled (Phase 7):
```json
{
  "ticker": "VOW3",
  "exchange": "XETRA",
  "provider_name": "mock",
  "use_llm": true,
  "llm_provider": "mock"
}
```

Request fields:
- `provider_name` — optional; defaults to `FINANCIAL_DATA_PROVIDER` config value (`mock` in CI).
- `require_schema_valid` — optional bool (default `false`). When `true`, returns `422` if schema draft fails.
- `use_llm` — optional bool (default `false`). When `true`, runs the `generate_research_sections` LLM node. Default `false` is CI-safe (no LLM calls, no credentials needed).
- `llm_provider` — optional; defaults to `LLM_PROVIDER` config value (`mock` in CI). Options: `mock`, `azure_openai`.

Response `202 Accepted` (Phase 9):
```json
{
  "agent_run_id": "uuid",
  "draft_report_id": "uuid",
  "status": "completed",
  "summary": "Phase 9 Analysis Council draft for Acme Nordic AS. Provider: mock. Schema: invalid. Source quality: weak. Internal status: research_incomplete. Human review: true. LLM: not used.",
  "workflow_name": "company_analysis",
  "company_name": "Acme Nordic AS",
  "ticker": "TEST",
  "provider_name": "mock",
  "is_mock": true,
  "schema_valid": false,
  "validation_errors": ["[(root)] 'snapshot_financials' is a required property"],
  "validation_warnings": [],
  "missing_fields": ["identity.isin", "identity.lei", "profile.website"],
  "llm_provider": null,
  "llm_used": false,
  "financial_data_summary": { "available_count": 8, "missing_count": 24, "warnings_count": 3, "..." : "..." },
  "source_quality_summary": { "overall_source_quality": "weak", "weak_sources_count": 2, "..." : "..." },
  "research_completeness_summary": { "complete_sections": [], "blocking_gaps_count": 25, "..." : "..." },
  "citation_validation_summary": { "status": "warnings", "weak_citation_warnings_count": 1, "..." : "..." },
  "research_team_warnings": ["Mock provider active: all values are synthetic demo data.", "..."],
  "bull_case_summary": {
    "confidence_level": "low",
    "positive_thesis_points_count": 3,
    "potential_tailwinds_count": 2,
    "missing_evidence_count": 5,
    "warnings_count": 1
  },
  "bear_case_summary": {
    "confidence_level": "low",
    "negative_thesis_points_count": 4,
    "key_unknowns_count": 6,
    "warnings_count": 1
  },
  "risk_summary": {
    "risk_summary": "All 6 risk categories identified. Data quality risks dominate due to mock provider.",
    "business_risks_count": 2,
    "financial_risks_count": 2,
    "market_risks_count": 2,
    "data_quality_risks_count": 3,
    "source_quality_risks_count": 2,
    "warnings_count": 0
  },
  "valuation_guard_summary": {
    "valuation_readiness": "not_ready",
    "blockers_count": 3,
    "available_inputs_count": 0,
    "missing_inputs_count": 10,
    "warnings_count": 1
  },
  "committee_chair_summary": {
    "committee_summary": "Research package based on mock provider data only. All analysis council assessments are illustrative.",
    "bull_bear_balance": "insufficient_data",
    "provisional_internal_status": "research_incomplete",
    "human_review_required": true,
    "open_questions_count": 5,
    "research_next_steps_count": 4,
    "warnings_count": 1
  },
  "analysis_council_warnings": ["Mock provider active — all council outputs are illustrative.", "..."],
  "quality_gate_status": {
    "source_quality_ok": false,
    "citation_status_ok": false,
    "schema_valid": false,
    "valuation_ready": false,
    "research_complete": false
  },
  "provisional_internal_status": "research_incomplete",
  "human_review_required": true
}
```

Errors:
- `422` — no company_id or ticker provided
- `422` — company not found in database
- `422` — unknown provider_name (not in registry)
- `422` — `require_schema_valid=true` and schema draft failed validation
- `500` — workflow execution error (see agent_run logs)

> **Phase 9 note:** Five deterministic Analysis Council agents run after the Research Team phase.
> These agents require no LLM calls and no Azure credentials; they are always active.
> - `bull_case_agent` — positive thesis points, tailwinds, evidence, assumptions; forbidden word gate.
> - `bear_case_agent` — negative thesis points, headwinds, key unknowns; challenges bull case.
> - `risk_agent` — 6-category risk classification; data_quality_risks always populated.
> - `valuation_guard_agent` — blocks valuation when mock/T5/T6 data; no price target ever produced.
> - `investment_committee_chair` — quality gate; assigns `provisional_internal_status` (admin-only, not public).
>
> **`provisional_internal_status` allowed values (admin-only internal workflow state — never public):**
> `research_incomplete`, `needs_primary_sources`, `ready_for_deeper_analysis`,
> `reject_due_to_data_quality`, `watchlist_candidate_for_review`.
>
> The optional LLM node (`use_llm=true`) is unchanged from Phase 7.
> No public investment recommendation, rating, or price target is ever produced.
> All outputs are admin/draft — not investment advice.

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
| GET | `/api/v1/financial-data/eodhd/status` | ✅ Live (Phase 13) | EODHD provider status (no network call; `not_configured` if key absent) |
| GET | `/api/v1/financial-data/eodhd/company/{symbol}` | ✅ Live (Phase 13, network) | Company profile from EODHD; `symbol` = `TICKER.EXCHANGE` (e.g. `AAPL.US`); requires `EODHD_API_KEY` |
| GET | `/api/v1/financial-data/eodhd/fundamentals/{symbol}` | ✅ Live (Phase 13, network) | Full fundamentals from EODHD; requires `EODHD_API_KEY`; returns datapoints with T5 source tier |
| GET | `/api/v1/financial-data/resolve` | ✅ Live (Phase 13) | Resolve company identifier to EODHD symbol(s); `?q=AAPL` or `?q=Apple+Inc`; optional `?exchange=NASDAQ`; warns when ambiguous |

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

---

### Reports (Admin / Dev Only)

These endpoints are for **internal admin and development use only**. They expose draft reports generated by the analysis workflow. No authentication is enforced in Phase 10 — auth is documented as future work (Phase 11).

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/api/v1/reports` | ✅ Live | List all draft reports (admin only) |
| GET | `/api/v1/reports/{report_id}` | ✅ Live | Get a single draft report by ID (admin only) |
| POST | `/api/v1/admin/reports/{report_id}/mark-under-review` | ✅ Live | Move report to under_review (admin only) |
| POST | `/api/v1/admin/reports/{report_id}/approve` | ✅ Live | Approve report internally (approved_internal; not public) |
| POST | `/api/v1/admin/reports/{report_id}/reject` | ✅ Live | Reject report (rejected_internal; requires note) |
| POST | `/api/v1/admin/reports/{report_id}/needs-revision` | ✅ Live | Request revision (needs_revision; requires note) |
| GET | `/api/v1/admin/reports/{report_id}/review-events` | ✅ Live | Get immutable audit log of all review actions |

**GET /api/v1/reports** — List draft reports

Query parameters: `limit` (default 50), `offset` (default 0)

Response `200 OK`:
```json
{
  "items": [
    {
      "id": "uuid",
      "title": "Phase 9 Analysis Council draft for Acme Nordic AS",
      "slug": "company-analysis-test-22222222",
      "report_type": "company_deep_dive",
      "status": "draft",
      "summary": "Phase 9 Analysis Council draft for Acme Nordic AS. ...",
      "content_markdown": "# ADMIN DRAFT ONLY\n...",
      "content_html": null,
      "created_by_agent_run_id": "uuid",
      "published_at": null,
      "created_at": "2026-06-24T10:00:00Z",
      "updated_at": "2026-06-24T10:00:00Z"
    }
  ],
  "total": 1
}
```

**GET /api/v1/reports/{report_id}** — Get draft report by ID

Response `200 OK`: report object (same shape as item above)

Error `404 Not Found`: report does not exist

> **Phase 10 note:** Report endpoints are admin/dev only. Content is an AI-generated draft.
> It is not investment advice. It is not a public recommendation.
> No BUY/SELL/HOLD/WATCH recommendation is ever contained in reports.
> Internal workflow statuses (e.g. `research_incomplete`) are operational metadata only.
> Authentication will be added in Phase 12.

---

### Admin Report Review (Phase 11)

**Review status values**: `draft` → `under_review` → `approved_internal` | `rejected_internal` | `needs_revision`

**POST /api/v1/admin/reports/{report_id}/mark-under-review**

Request:
```json
{ "note": "Starting review.", "actor_label": "admin@example.com" }
```

**POST /api/v1/admin/reports/{report_id}/approve**

Approve a report internally. Set `acknowledge_warnings=true` when `human_review_required=true`.

Request:
```json
{
  "note": "Reviewed — sources adequate for internal use.",
  "actor_label": "admin@example.com",
  "acknowledge_warnings": true
}
```

**POST /api/v1/admin/reports/{report_id}/reject**

Requires `note`.

Request:
```json
{ "note": "Source quality insufficient — T5 only.", "actor_label": "admin@example.com" }
```

**POST /api/v1/admin/reports/{report_id}/needs-revision**

Requires `note`.

Request:
```json
{ "note": "Please add SEC filing citation for revenue claim.", "actor_label": "admin@example.com" }
```

All review action responses follow `ReviewActionResponse`:
```json
{
  "report_id": "uuid",
  "action": "approve",
  "from_status": "under_review",
  "to_status": "approved_internal",
  "note": "Reviewed — sources adequate.",
  "actor_label": "admin@example.com",
  "message": "Report approved internally (approved_internal). PUBLIC PUBLISHING IS NOT IMPLEMENTED. INTERNAL ADMIN ONLY. ..."
}
```

**GET /api/v1/admin/reports/{report_id}/review-events**

Immutable chronological audit log.

```json
{
  "items": [
    {
      "id": "uuid",
      "report_id": "uuid",
      "action": "mark_under_review",
      "from_status": "draft",
      "to_status": "under_review",
      "note": null,
      "actor_label": "admin@example.com",
      "created_at": "2026-06-25T10:00:00Z"
    }
  ],
  "total": 1
}
```

**Allowed transitions:**

| Action | Allowed from |
|---|---|
| mark_under_review | draft, needs_revision |
| approve | under_review |
| reject | under_review, needs_revision, draft |
| needs_revision | under_review |

**Validation rules:**
- `reject` and `needs_revision` require a non-empty `note`
- `approve` when `human_review_required=true` requires `acknowledge_warnings=true`
- All actions create an immutable `report_review_events` record
- No `/publish` endpoint exists — public publishing not implemented in Phase 11

> **Phase 11 constraints:**
> - Internal approval ≠ public publication. No public-facing report is produced.
> - All outputs remain draft/internal — not investment advice.
> - Human reviewer remains responsible for all review decisions.
> - Authentication not yet enforced — restrict access at network level (Phase 12).

---

## Planned Endpoints (Phase 11+)

### Public (unauthenticated)
| Method | Path | Phase |
|---|---|---|
| GET | `/api/v1/reports` | Phase 10 ✅ (admin only) → public in Phase 12 |
| GET | `/api/v1/reports/{slug}` | Phase 12 |
| GET | `/api/v1/themes` | Phase 12 |
| GET | `/api/v1/companies/{ticker}` | Phase 12 (public company page) |

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
