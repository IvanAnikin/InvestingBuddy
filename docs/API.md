# API Reference

## Status: Phase 28B — Run-Level LLM Discovery Council. **No DB migration.** OFF by default — the council runs only when **both** `LLM_COUNCIL_ENABLED=true` and `LLM_DISCOVERY_COUNCIL_ENABLED=true` (default `false`) are set **and** a usable provider (`fake` | `azure_openai` | `openai`) resolves; otherwise it is disabled and no LLM call is made. Two new **admin/internal-only** endpoints on the existing market-discovery router (no auth change): `POST /api/v1/market-discovery/runs/{run_id}/council-review` builds a **bounded, cited run evidence pack**, runs an internal, **citation-bound, safety-gated** council over a whole discovery run's candidate set to decide internal research **priority**, and stores + returns the review (`409` when the council is disabled / no provider is available, `422` when the run has no candidates or is not terminal, `404` when the run is not found); `GET /api/v1/market-discovery/runs/{run_id}/council-review` returns the stored review (`404` when none exists yet). The review persists under the run's existing `discovery_runs.config_json["discovery_council"]` JSONB (no new column). **Manual admin-triggered only** — it never runs automatically after a discovery run. Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `publication_ready=false`, no publish route; raw prompts/completions/evidence/secrets are never returned or logged. See the Phase 28B section below. Underlying Phase 28A — Single-Company LLM Analysis Council. **No DB migration.** OFF by default (`LLM_COUNCIL_ENABLED=false`) so CI and a plain deploy stay fully deterministic (`llm_used: false`). When enabled with a usable provider (`fake` | `azure_openai` | `openai`), the final-report `generate` paths build a **bounded, cited evidence pack** for one company and run an internal, **citation-bound, safety-gated** LLM council (Financial Analyst, Business/Moat, Catalyst, Risk/Governance, Valuation Guard, Source Quality Critic, Red Team, Committee Chair). No new endpoints. `FinalReportResponse` gains additive `llm_used`/`llm_provider`/`llm_model`/`council_version`/`council_agents_*`/`evidence_pack_version`/`evidence_item_count`/`committee_label`; council metadata + per-agent output persist under `source_summary_json.llm_council`. Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `publication_ready=false`, no publish route; raw prompts/completions/secrets are never returned or logged. See the Phase 28A section below. Underlying Phase 27.1C — Prompt-Derived Autofill + Controlled Selectors + Strict Country Filtering. **No DB migration.** Two new admin endpoints: `POST /api/v1/market-discovery/parse-thesis` (parse a thesis for selector auto-fill — **does not create a run**) and `GET /api/v1/market-discovery/supported-filters` (canonical Region/Country/Sector/Industry selector options). `parse-thesis` returns canonical single-value `region`/`country`/`sector`/`industry`/`theme`/`confidence`/`extraction_source` detected from the prompt text. On `POST /thesis-runs`, explicit form Region/Country/Sector override the parsed prompt values (a conflict keeps the explicit choice and surfaces a warning), values outside the supported options are rejected `422`, and **country filtering is strict** — a country filter is the sole geographic filter, so `"Swiss watch companies"` returns only Swiss issuers. Safety unchanged (internal only, no BUY/SELL/HOLD/WATCH/target/fair-value/upside/recommendation, `human_review_required=true`, `is_public=false`, no publish route). See the Phase 27.1C section below. Underlying Phase 27 — Thesis-to-Universe Discovery. Adds a **market-segment / thesis** discovery mode: `POST /api/v1/market-discovery/thesis-runs` (+ `GET /thesis-runs/{run_id}` alias). An admin describes a segment/theme/region in natural language; the backend deterministically parses it, builds a **bounded universe of real public companies** from a curated registry, and scans it through the Phase 25 pipeline. Discovery runs gain `mode` (`ticker`|`thesis`), `thesis_text`, `parsed_thesis_json`, `universe_json`; candidates gain `thesis_relevance_score`, `combined_internal_score`, `thesis_match_json` (migration **011**). Vague/no-match theses are rejected (422, `needs_narrowing`). Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `is_public=false`, no public publish route. See the Phase 27 section below. Underlying Phase 26 — Final Report Schema Completion / Publication-Readiness. **No new API endpoints** and no DB migration. The final-report `generate`/`validate` responses gain `research_complete` and `publication_ready` (both default `false`); the internal admin draft is deterministically completed into the strict `report_schema.json` shape so `schema_valid=true` is achievable via honest `not_sourced` stand-ins (never fabricated data). `safety_valid=true`, `human_review_required=true`, and public publishing remain unchanged (still not implemented). Underlying Phase 24.1.2 — Press-Release Canonical Link Fix. **No new API endpoints** and no request-schema change. The additive `news_catalyst_discovery` event rows now carry `source_url_quality` (`canonical_article` / `rejected_media_only` / `missing`) and an optional `media_url`; `source_url` for company press-release events is always the canonical article page (image/media URLs are never used as evidence). Backward-compatible; `safety_valid=true`; human review required. Underlying Phase 24.1.1 — News Provider Activation + Feed-Status Consistency. **No new API endpoints** and no request/response schema change. The catalyst markdown's **Company News Sources** now carries a precise press-release feed-status line (`feed_discovered_with_items` / `_no_recent_items` / `_unreadable` / `not_discovered`) instead of a contradictory "no feed found" warning, and the additive `news_catalyst_discovery` structured section gains a `source_statuses` block (`company_press_release` status + `items_seen`/`items_used` + `news_provider` status). Backend-only env `NEWS_LOOKBACK_DAYS` now scopes the news/press lookback (SEC stays 90d); `NEWS_PROVIDER_NAME=gdelt` activates a no-key provider. Existing endpoints stay backward-compatible; `safety_valid=true`; human review required. Underlying Phase 24.1 — Real News + Company Source Enablement. **No new API endpoints** were added and no request/response schema changed. On top of Phase 24, `free_real`/`eodhd_free_real` draft reports gain two additional catalyst markdown sections — **Company News Sources** (discovered website / IR / newsroom / press-release feed + verification tier/confidence) and **Industry Context News** (sector news explicitly flagged as NOT company-specific evidence) — and the Final Report Generator's additive `news_catalyst_discovery` section gains `company_sources`, `industry_context_events`, and `source_classes_attempted` / `source_classes_successful` (all controlled-vocabulary + neutralised headlines). Coverage status can now report `limited`/`adequate`/`strong` instead of `filings_only` when real company/news/industry evidence exists. Optional env config (backend only, never a request field, never committed): `NEWS_PROVIDER_NAME`, `NEWS_API_KEY`, `NEWS_API_BASE_URL`, `NEWS_SEARCH_ENDPOINT`, `NEWS_MAX_RESULTS`, `NEWS_LOOKBACK_DAYS`, `NEWS_TIMEOUT_SECONDS`. Existing final-report endpoints stay backward-compatible; the safety gate passes (`safety_valid=true`) and human review stays required. Underlying Phase 24 — News + Catalyst Discovery. **No new API endpoints** were added and no request/response schema changed. For `free_real`/`eodhd_free_real` analyses, the draft report markdown gains catalyst sections (News & Catalyst Discovery, Recent Catalyst Events, SEC Filing Events, Catalyst Evidence Quality, Catalyst Gaps / Next Research Tasks) plus a machine-readable catalyst JSON block, and the Final Report Generator's structured content gains an **additive** `news_catalyst_discovery` section (controlled-vocabulary counts + coverage status + SEC filing metadata + neutralised headlines; catalyst labels are T6 model estimates). Existing final-report endpoints (`from-scorecard`/`from-candidate`/`from-company`/`from-report`/`validate`/`regenerate-section`) are unchanged and stay backward-compatible; the safety gate passes (`safety_valid=true`) and human review stays required. Underlying Phase 19.4 — Identity + Sector + Market-Metric Enrichment. On top of the Phase 19.3 SEC-normalized fundamentals, the `free_real` / `eodhd_free_real` analysis snapshot now carries **additive, non-breaking** enrichment fields: `company_identity` gains `lei` / `isin`, `profile` gains sourced `sector` / `industry` / `website`, `fundamentals_summary` gains derived `market_cap_usd_m` / `enterprise_value_usd_m` / `pe_ratio` / `52_week_high` / `52_week_low` / `shares_outstanding_mln` (all null when not derivable), and two new blocks appear: `identity_profile_enrichment` and `market_metrics_summary` (both carry per-field `source_tiers` + `warnings`). No request schema or existing field changed. `valuation_guard_summary.valuation_readiness` stays `"partial"` when SEC + derived metrics are present; all valuation conclusions remain blocked (derived market cap / EV / P/E are T6 estimates, never official figures). Builds on Phase 19.1 Free Real Data Provider Stack (provider keys: `free_real`, `eodhd_free_real`, `eodhd_price_only`, `sec_edgar_fundamentals`).

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
| `/api/v1/` | Staging Basic Auth (server-to-server) | Core CRUD and workflow endpoints |
| `/api/me/` | Yes (future) | Authenticated user-specific data |
| `/api/admin/` | Admin role (future) | Platform management |

### Admin access (Phase 23 — Admin/Auth Hardening)

The whole admin surface is now gated in front of the backend by the Next.js web
app. Browsers never call the FastAPI backend directly — they call the Next.js
admin proxy at `/api/admin/proxy/*`, which:

1. Requires a valid **admin session** (httpOnly HMAC-signed cookie) → **401** if
   missing.
2. Requires the session email to be in `ADMIN_ALLOWED_EMAILS` → **403** if not.
3. Validates the target backend path against an allowlist → **404** otherwise.
4. Only then attaches the backend **Basic Auth** (`STAGING_BASIC_AUTH`) plus
   advisory `X-IB-Admin-Email` / `X-IB-Admin-Name` audit headers, server-side.

On the backend, `STAGING_BASIC_AUTH` (when `APP_ENV=staging`) still protects
every route except `/health`. The `X-IB-Admin-*` headers are **advisory only**
and are never trusted for authentication — they are read only after Basic Auth
passes. See `docs/SECURITY.md`.

---

## Implemented Endpoints

### Health

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/health` | ✅ Live | Application health check + safe deploy metadata (exempt from Basic Auth) |

**Response:** all fields are public build identifiers — never secrets (Phase 19.2.1
added `commit_sha`/`build_id`; Phase 27.1D added `app`/`build_time`).
```json
{
  "status": "ok",
  "environment": "development",
  "version": "0.1.0",
  "commit_sha": "unknown",
  "build_id": "unknown",
  "app": "InvestingBuddy API",
  "build_time": "unknown"
}
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
- `provider_name` — optional; defaults to `FINANCIAL_DATA_PROVIDER` config value (`mock` in CI). Phase 19.1 free-plan values: `free_real` (Stooq + SEC EDGAR, no keys), `eodhd_free_real` (EODHD /eod + SEC EDGAR, requires EODHD_API_KEY free plan), `eodhd_price_only` (EODHD /eod only).
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

---

## Discovery / Screener (Phase 14 — Admin / Dev Only)

All discovery endpoints are **admin/dev-only**. They are internal research funnel endpoints.
No investment recommendations, price targets, fair values, or upside percentages are produced.
Not investment advice. Not public-facing.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/discovery/universes` | ✅ Phase 14 | Create a screening universe definition |
| GET | `/api/v1/discovery/universes` | ✅ Phase 14 | List all universe definitions |
| POST | `/api/v1/discovery/runs` | ✅ Phase 14 | Execute a screen against a universe |
| GET | `/api/v1/discovery/runs` | ✅ Phase 14 | List all screening runs |
| GET | `/api/v1/discovery/runs/{run_id}` | ✅ Phase 14 | Get a screening run by ID |
| GET | `/api/v1/discovery/runs/{run_id}/candidates` | ✅ Phase 14 | List candidates produced by a run |
| POST | `/api/v1/discovery/candidates/{candidate_id}/promote` | ✅ Phase 14 | Promote candidate to company analysis funnel |

**POST /api/v1/discovery/universes** — Create screening universe

```json
{
  "name": "EU Energy Transition",
  "description": "European energy transition companies",
  "region": "Europe",
  "exchange": null,
  "sector_filter": "Utilities",
  "theme": "energy_transition",
  "provider_name": "mock"
}
```

Allowed themes: `energy_transition`, `electrification_grid`, `defense_security`,
`industrial_resilience`, `real_assets`, `materials_mining`

Response `201 Created`:
```json
{
  "id": "uuid",
  "name": "EU Energy Transition",
  "theme": "energy_transition",
  "region": "Europe",
  "provider_name": "mock",
  "created_at": "2026-06-30T..."
}
```

**POST /api/v1/discovery/runs** — Execute a screen

```json
{
  "universe_id": "uuid",
  "max_candidates": 50,
  "market_cap_min": null,
  "market_cap_max": null,
  "keyword_search": null
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "universe_id": "uuid",
  "status": "completed",
  "provider_name": "mock",
  "summary_json": {
    "total_candidates": 3,
    "status_counts": {"candidate_found": 3},
    "note": "Internal research funnel only. No investment recommendation produced."
  }
}
```

**GET /api/v1/discovery/runs/{run_id}/candidates** — List candidates

Response `200 OK`:
```json
{
  "items": [
    {
      "id": "uuid",
      "ticker": "ORSTED",
      "exchange": "CPH",
      "name": "Ørsted A/S",
      "country": "Denmark",
      "sector": "Utilities",
      "candidate_status": "candidate_found",
      "discovery_reasons_json": ["Theme match 'energy_transition': keywords found — offshore, wind"],
      "available_data_json": ["ticker", "exchange", "name", "country", "sector"],
      "missing_data_json": ["market_cap", "currency", "revenue_ttm"],
      "source_tier": "T6_model_estimate",
      "data_quality": "D_weak_or_stale",
      "warnings_json": ["Mock/synthetic data only — all values are demo placeholders."]
    }
  ],
  "total": 3
}
```

**POST /api/v1/discovery/candidates/{candidate_id}/promote** — Promote to company analysis

Response `200 OK`:
```json
{
  "candidate_id": "uuid",
  "company_id": "uuid",
  "ticker": "ORSTED",
  "exchange": "CPH",
  "name": "Ørsted A/S",
  "promoted": true,
  "company_created": true,
  "new_candidate_status": "ready_for_deeper_analysis",
  "message": "Candidate promoted. Company record created (ORSTED.CPH). Run the company-analysis workflow separately to begin deeper research. No recommendation produced. No publishing performed."
}
```

Errors:
- `404` — universe/run/candidate not found
- `422` — candidate in error or rejected_by_screen state
- `422` — universe theme invalid

> **Phase 14 constraints:**
> - Internal research funnel only. Candidates are NOT investment recommendations.
> - No BUY/SELL/HOLD/WATCH/price_target/fair_value/upside ever produced.
> - EODHD data remains T5_api_aggregator — never promoted to T1/T2.
> - Candidate with only T5 data always gets the mandatory warning:
>   "Candidate requires primary-source validation before final analysis."
> - Promotion creates a Company record for later analysis; it does NOT auto-trigger analysis.
> - Admin must separately run the company-analysis workflow for deeper research.

---

## Scoring / Valuation Framework (Phase 15 — Admin / Dev Only)

All scoring endpoints are **admin/dev-only**. They produce internal research attractiveness scores only.
No investment recommendations, price targets, fair values, or upside percentages are produced.
`internal_status` values are research queue labels — not public recommendations. Not investment advice.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/scoring/candidates/{candidate_id}` | ✅ Phase 15 | Score a screening candidate; persist scorecard |
| GET | `/api/v1/scoring/candidates/{candidate_id}` | ✅ Phase 15 | Get latest scorecard for a candidate |
| POST | `/api/v1/scoring/runs/{run_id}` | ✅ Phase 15 | Score all candidates in a screening run |
| GET | `/api/v1/scoring/runs/{run_id}/ranked-candidates` | ✅ Phase 15 | List candidates ranked by score (admin view) |
| POST | `/api/v1/scoring/companies/{company_id}` | ✅ Phase 15 | Score a company from analysis workflow data |

**POST /api/v1/scoring/candidates/{candidate_id}** — Score and persist a screening candidate

Response `201 Created`:
```json
{
  "candidate_id": "uuid",
  "scorecard_id": "uuid",
  "overall_score": 18,
  "internal_status": "needs_primary_sources",
  "scores": {
    "source_quality_score": {"score": 15, "explanation": "T6 mock source.", "warnings": ["Mock data"]},
    "data_completeness_score": {"score": 20, "explanation": "4/15 expected fields.", "warnings": []},
    "theme_alignment_score": {"score": 40, "explanation": "2 theme keywords matched.", "warnings": []}
  },
  "warnings": ["Mock/T6 data: overall score capped at 30."],
  "missing_data": ["market_cap", "revenue_ttm"],
  "valuation_readiness": {
    "valuation_readiness": "not_ready",
    "available_inputs": [],
    "missing_inputs": ["market_cap", "ebitda"],
    "blocked_methods": ["DCF", "EV/EBITDA"],
    "allowed_methods": [],
    "disclaimer": "Valuation readiness check only. No fair value, price target, or upside estimate is produced here."
  },
  "disclaimer": "INTERNAL SCORE ONLY. Not investment advice. Not a public recommendation. Human review required before any action."
}
```

**GET /api/v1/scoring/runs/{run_id}/ranked-candidates** — Ranked candidate list

Response `200 OK`:
```json
{
  "run_id": "uuid",
  "items": [
    {"rank": 1, "candidate_id": "uuid", "ticker": "ORSTED", "overall_score": 42, "internal_status": "ready_for_deeper_analysis", ...},
    {"rank": 2, "candidate_id": "uuid", "ticker": "RWE", "overall_score": 38, "internal_status": "needs_primary_sources", ...}
  ],
  "total": 12,
  "note": "Candidates are ranked by internal research attractiveness score. Ranking is NOT a public investment recommendation.",
  "disclaimer": "INTERNAL SCORE ONLY. Not investment advice."
}
```

> **Phase 15 constraints:**
> - No BUY/SELL/HOLD/WATCH public recommendations ever produced.
> - No price targets, fair values, or upside percentages ever produced.
> - `internal_status` is a research queue label for admin use only.
> - T6/mock data: overall score capped at ≤ 30/100.
> - T5 data: overall score capped at ≤ 60/100.
> - T1/T2 data: full 0–100 range.
> - Scoring node in company analysis workflow (Node 17) runs automatically after Analysis Council.
> - All scoring is non-fatal — workflow always completes even if scoring fails.
> - Human admin review required before any action on high-priority items.

---

### Final Reports (Phase 16 — Admin/Dev Only)

> All endpoints are admin/dev only. No public publishing is ever performed.
> No BUY/SELL/HOLD/WATCH recommendations, price targets, fair values,
> or upside percentages are ever produced. Human review is always required.

> **Phase 26 — Final Report Schema Completion.** The `validate` and `generate`
> endpoints now deterministically complete the internal admin draft into the
> strict `report_schema.json` shape before validating, so `schema_valid` reaches
> `true` for reports that previously failed only on missing schema sections.
> Genuinely-absent fields become honest `not_sourced` / `not_available` /
> `blocked` / `requires_human_research` stand-ins (a `datapoint` with
> `value: null` and `data_quality: "D_weak_or_stale"`) — **never fabricated
> data**. `schema_valid` is now orthogonal to research completeness. Two response
> fields are added (both default `false`): `research_complete` (enough SOURCED
> data exists — `false` for free-provider drafts) and `publication_ready`
> (**always `false`** — public publishing is not implemented). `safety_valid`
> stays `true`, `human_review_required` stays `true`, and no recommendation,
> price target, fair value, or upside/downside is produced. No new endpoints and
> no DB migration; `schema_validation_json` gains `research_complete`,
> `publication_ready`, `human_review_required`, and `placeholder_field_count`
> keys (backward-compatible; `is_valid` unchanged).

> **Phase 28B — Run-Level LLM Discovery Council.** When
> `LLM_COUNCIL_ENABLED=true` **and** `LLM_DISCOVERY_COUNCIL_ENABLED=true`
> **and** a usable provider resolves (`LLM_PROVIDER_COUNCIL` = `fake` |
> `azure_openai` | `openai`), an admin can run a **run-level** LLM council over a
> whole discovery run's candidate set to decide internal research **priority** —
> the run-level analog of the Phase 28A single-company council. The service
> builds a **bounded, cited run evidence pack** (run-level facts get ids
> `R1, R2, …`; each candidate gets `C1, C2, …`; agents may cite ONLY those ids;
> bounded by `LLM_DISCOVERY_COUNCIL_MAX_CANDIDATES`, default `25`) and runs eight
> agents in order — `run_coordinator`, `candidate_prioritization`,
> `novelty_coverage`, `diversity_anti_convergence`, `evidence_sufficiency`,
> `risk_gatekeeper`, `run_red_team`, `discovery_chair` (the chair runs last and
> sees the prior agents' safety-scanned summaries). Output is **citation-bound**
> and **safety-gated**: per agent, invalid citation ids are dropped, un-cited
> material claims are moved to `unsupported_claims`, any forbidden
> investment-action language quarantines the **whole** agent output
> (`status=failed`, no forbidden term echoed forward — the quarantine note records
> tier names, not terms), and a bad `internal_action`/`run_quality` is coerced to
> a safe default; one failing agent never fails the review, and a final backstop
> re-scan runs before storing. The only per-candidate internal actions are
> `research_next`, `monitor_for_evidence`, `insufficient_data`, `reject_for_now`;
> the only `run_quality` labels are `strong`, `adequate`, `thin`, `failed`. The
> council never emits BUY/SELL/HOLD/WATCH, a price target, fair value, intrinsic
> value, or upside/downside. It is **manual admin-triggered only** — it never runs
> automatically after a discovery run. When either flag is off or no provider
> resolves, the council is disabled, **no fake output is produced in production**,
> the deterministic discovery result is unchanged, and the review endpoint returns
> `409`. **No DB migration** — the review persists under the run's existing
> `discovery_runs.config_json["discovery_council"]` JSONB. `human_review_required`
> is always `true` and `publication_ready` always `false`. Raw prompts,
> completions, evidence excerpts, and credentials are never returned or logged.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/market-discovery/runs/{run_id}/council-review` | ✅ Live | Build the run evidence pack, run the discovery council, store + return the review (admin/internal only) |
| GET | `/api/v1/market-discovery/runs/{run_id}/council-review` | ✅ Live | Return the stored review for a run (admin/internal only) |

**POST /api/v1/market-discovery/runs/{run_id}/council-review** — Run the council

- **200** — the review (`DiscoveryCouncilReviewResponse`, schema below). When the
  council ran, `llm_used=true` with `provider`/`model` populated.
- **409** — the council is disabled or no provider is available
  (`"Discovery council is disabled."` / `"Discovery council provider is not
  available."`); no LLM call is made.
- **422** — the run has no candidates or is not terminal.
- **404** — the run is not found.

**GET /api/v1/market-discovery/runs/{run_id}/council-review** — Fetch the stored review

- **200** — the stored `DiscoveryCouncilReviewResponse`.
- **404** — no review exists yet for the run (or the run is not found).

**`DiscoveryCouncilReviewResponse`** (`apps/api/app/schemas/market_discovery.py`):
`run_id`, `llm_used`, `council_version`, `provider`, `model`,
`evidence_pack_version`, `evidence_item_count`, `candidate_count`,
`agents_completed` / `agents_failed` / `agents_skipped`, `run_quality`
(`strong` | `adequate` | `thin` | `failed`), the four candidate buckets
`candidates_to_research_next` / `candidates_to_monitor` / `candidates_to_reject` /
`candidates_insufficient_data` (each a list of
`{candidate_ref, candidate_id, ticker, exchange, rationale, confidence}`),
`evidence_gaps`, `next_source_tasks`, `agent_outputs`, `warnings`, `safety_valid`,
`human_review_required` (always `true`), `publication_ready` (always `false`),
`created_at`, `disclaimer`.

Response (200 for **GET .../council-review**):
```json
{
  "run_id": "uuid",
  "llm_used": true,
  "council_version": "v1",
  "provider": "azure_openai",
  "model": "gpt-4.1-mini",
  "evidence_pack_version": "v1",
  "evidence_item_count": 18,
  "candidate_count": 7,
  "agents_completed": 8,
  "agents_failed": 0,
  "agents_skipped": 0,
  "run_quality": "adequate",
  "candidates_to_research_next": [
    { "candidate_ref": "C1", "candidate_id": "uuid", "ticker": "AMAT", "exchange": "US", "rationale": "Cited R2, C1: strongest source coverage in the run.", "confidence": 0.72 }
  ],
  "candidates_to_monitor": [
    { "candidate_ref": "C3", "candidate_id": "uuid", "ticker": "UHR", "exchange": "SW", "rationale": "Cited C3: awaiting fundamentals evidence.", "confidence": 0.5 }
  ],
  "candidates_to_reject": [],
  "candidates_insufficient_data": [
    { "candidate_ref": "C5", "candidate_id": "uuid", "ticker": "BRBY", "exchange": "LSE", "rationale": "Cited C5: profile not_sourced, no fundamentals.", "confidence": 0.4 }
  ],
  "evidence_gaps": ["No SEC-eligible fundamentals for non-US venues (R1, C3, C5)."],
  "next_source_tasks": ["Obtain issuer-filed fundamentals for SW/LSE candidates."],
  "agent_outputs": [
    { "agent_name": "run_coordinator", "status": "completed", "summary": "...", "citations": ["R1", "R2"], "unsupported_claims": [] }
  ],
  "warnings": [],
  "safety_valid": true,
  "human_review_required": true,
  "publication_ready": false,
  "created_at": "2026-07-23T00:00:00Z",
  "disclaimer": "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. ..."
}
```

> **Phase 28B constraints:**
> - **Run-level** council — reviews a whole discovery run's candidate set and
>   decides internal research **priority**; it does not analyse a single company.
> - Bounded by `LLM_DISCOVERY_COUNCIL_MAX_CANDIDATES` (default `25`); each
>   candidate in the pack carries `score_breakdown`, `data_coverage`
>   (`profile_source`/`fundamentals_source`/`sec_eligible`/`reason`/
>   `requires_human_research`), `catalyst_summary`, `safety_valid`, `warnings`.
> - Only internal actions: `research_next`, `monitor_for_evidence`,
>   `insufficient_data`, `reject_for_now`. Only run-quality labels: `strong`,
>   `adequate`, `thin`, `failed`.
> - Stored under `discovery_runs.config_json["discovery_council"]` (no migration);
>   manual admin-triggered only; disabled deployments return `409` with no LLM
>   call.
> - No BUY/SELL/HOLD/WATCH, no price target / fair value / intrinsic value /
>   upside/downside / recommendation; `human_review_required=true`,
>   `publication_ready=false`, no publish route, no auth change.

> **Phase 28A — Single-Company LLM Analysis Council.** When
> `LLM_COUNCIL_ENABLED=true` **and** a usable provider resolves
> (`LLM_PROVIDER_COUNCIL` = `fake` | `azure_openai` | `openai`), every
> final-report `generate` path builds a **bounded, cited evidence pack** for the
> one company and runs an internal LLM council (Financial Analyst, Business /
> Moat, Catalyst, Risk / Governance, Valuation Guard, Source Quality Critic, Red
> Team, Committee Chair). Council output is **citation-bound** (agents may cite
> only evidence-pack ids) and **safety-gated** (the shared scanner quarantines
> any forbidden rating / valuation language before it is saved or displayed).
> The council never emits BUY/SELL/HOLD/WATCH, a price target, fair value, or
> upside/downside; the chair uses only internal labels
> (`internal_research_candidate` | `requires_more_evidence` | `insufficient_data`
> | `monitor_for_new_evidence` | `reject_for_now`). When the flag is off or no
> provider resolves, the deterministic path is **unchanged** and the report
> honestly reports `llm_used: false`. **No new endpoints, no DB migration.** The
> `FinalReportResponse` gains additive fields — `llm_used`, `llm_provider`,
> `llm_model`, `council_version`, `council_agents_completed` /
> `council_agents_failed` / `council_agents_skipped`, `evidence_pack_version`,
> `evidence_item_count`, `committee_label` — and the compact council metadata +
> per-agent output is persisted under `source_summary_json.llm_council` (read by
> the report detail page). `schema_valid`, `safety_valid`,
> `human_review_required=true`, `publication_ready=false` are unchanged. Raw
> prompts, completions, evidence excerpts, and credentials are never returned or
> logged.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/final-reports/from-scorecard/{scorecard_id}` | ✅ Live | Generate final report from scored candidate |
| POST | `/api/v1/final-reports/from-candidate/{candidate_id}` | ✅ Live | Generate final report from discovery candidate |
| POST | `/api/v1/final-reports/from-company/{company_id}` | ✅ Live | Generate final report from company record |
| POST | `/api/v1/final-reports/{report_id}/validate` | ✅ Live | Re-validate an existing report |
| POST | `/api/v1/final-reports/{report_id}/regenerate-section` | ✅ Live | Regenerate a single section of an existing report |

**POST /api/v1/final-reports/from-scorecard/{scorecard_id}** — Generate from scorecard

Response (201):
```json
{
  "report_id": "uuid",
  "status": "draft",
  "review_status": "draft",
  "schema_valid": true,
  "safety_valid": true,
  "human_review_required": true,
  "research_complete": false,
  "publication_ready": false,
  "internal_status": "ready_for_deeper_analysis",
  "sections_generated": ["admin_disclaimer", "executive_summary", "..."],
  "missing_sections": [],
  "safety_validation": { "passed": true, "forbidden_terms_found": [], "blocks_approval": false },
  "schema_validation_errors": [],
  "scorecard_id": "uuid",
  "source_count": 0,
  "citation_count": 0,
  "human_review_checklist": [{ "item": "...", "required": true, "completed": false }],
  "disclaimer": "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. ..."
}
```

**POST /api/v1/final-reports/{report_id}/regenerate-section** — Regenerate a section

Request body:
```json
{ "section_name": "executive_summary", "notes": "optional admin note" }
```

Response (200):
```json
{
  "report_id": "uuid",
  "section_name": "executive_summary",
  "regenerated": true,
  "safety_valid": true,
  "warnings": [],
  "disclaimer": "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. ..."
}
```

**Allowed section names for regenerate-section:**
`executive_summary`, `company_identity`, `discovery_rationale`, `data_availability_summary`,
`financial_snapshot`, `internal_scorecard`, `valuation_readiness`, `bull_case`, `bear_case`,
`risk_analysis`, `source_quality_review`, `citation_validation_review`,
`research_completeness_review`, `missing_information`, `committee_chair_summary`,
`workflow_status`, `human_review_checklist`, `source_citation_appendix`

> **Phase 16 constraints:**
> - 19-section structured internal draft report — never a public document.
> - Safety gate scans every section for forbidden recommendation language.
> - `blocks_approval=True` in safety_validation if any forbidden term found.
> - All 6 allowed `internal_status` labels are research queue labels, not public recommendations:
>   `not_enough_data`, `low_priority_research`, `needs_primary_sources`,
>   `ready_for_deeper_analysis`, `high_priority_for_human_review`, `reject_due_to_data_quality`
> - LLM (optional) used only for executive_summary section enrichment; offline by default.
> - Schema validation non-fatal for the draft, but Phase 26 now completes the draft
>   into the strict schema shape so `schema_valid=True` is achievable via honest
>   `not_sourced` stand-ins; `research_complete=False` and `publication_ready=False`
>   preserve the incompleteness and keep the report internal-only.
> - Report version stored in `final_report_version` column (current: `16.0.0`).

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

---

## Phase 22: Backtesting & Judge Endpoints

All endpoints are **admin/dev-only**. No public-facing routes.  
No BUY/SELL/HOLD/WATCH recommendations, price targets, fair values, or upside percentages are produced.  
All responses include `disclaimer: "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE."`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/backtesting/runs` | Create a new backtest run |
| GET | `/api/v1/backtesting/runs` | List all backtest runs |
| GET | `/api/v1/backtesting/runs/{run_id}` | Get a specific backtest run |
| POST | `/api/v1/backtesting/runs/{run_id}/add-report/{report_id}` | Add a report to a backtest run |
| POST | `/api/v1/backtesting/runs/{run_id}/evaluate` | Evaluate all reports in a run |
| GET | `/api/v1/backtesting/runs/{run_id}/results` | List results for a run |
| GET | `/api/v1/backtesting/runs/{run_id}/summary` | Get aggregate summary for a run |
| POST | `/api/v1/backtesting/reports/{report_id}/judge` | Run judge evaluation on a single report |

**Notes:**
- Default provider: `mock` (deterministic, no network, no API keys required in CI).
- Live providers (EODHD, Stooq) can be added later via `BACKTEST_PROVIDER` env var without breaking the interface.
- Allowed judge statuses: `insufficient_data`, `useful_research`, `needs_better_sources`, `poor_evidence_quality`, `outcome_inconclusive`, `outcome_review_required`.

---

## Phase 25: Market Candidate Discovery (Admin / Internal Only)

Internal-only, bounded market discovery. Produces an **internal research
candidate queue** — NOT a recommendation engine. All endpoints are
admin/internal only with no public-facing routes.

**Hard guarantees (enforced across model, service, schema, API, and safety scan):**
- No BUY/SELL/HOLD/WATCH labels. No price targets, fair values, intrinsic
  values, upside/downside, or undervalued/overvalued labels. No recommendations.
- `candidate_score` (and all component scores) are an **internal prioritization
  signal only** — a high score means "prioritize for internal human research",
  never "buy".
- Every candidate is `human_review_required=true` and `is_public=false`.
- The universe size is validated *before* any work — a run larger than
  `DISCOVERY_MAX_UNIVERSE_SIZE` is rejected (422), preventing an accidental
  full-market scan. An empty universe is also rejected (422).
- Every response includes an internal `disclaimer` field.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/market-discovery/runs` | List discovery runs |
| POST | `/api/v1/market-discovery/runs` | **Start** a bounded discovery scan asynchronously — returns `run_id` immediately (Phase 25.1) |
| GET | `/api/v1/market-discovery/runs/{run_id}` | Get a discovery run (poll for status/progress) |
| GET | `/api/v1/market-discovery/runs/{run_id}/summary` | Aggregate summary (top score, grade breakdown) |
| GET | `/api/v1/market-discovery/runs/{run_id}/candidates` | List ranked internal candidates (filter/sort) |
| GET | `/api/v1/market-discovery/candidates/{candidate_id}` | Candidate detail (score breakdown + signals) |
| POST | `/api/v1/market-discovery/candidates/{candidate_id}/run-analysis` | Promote a candidate to the full company-analysis workflow |

**Async execution (Phase 25.1):** `POST /runs` now **creates the run row and
returns immediately** (HTTP 201, `status="pending"`) instead of processing the
whole universe inline. Tickers are processed in the background (FastAPI
`BackgroundTasks`, using a *fresh* DB session — never the request session);
progress is committed after every ticker. The admin UI polls `GET /runs/{run_id}`
until a terminal status is reached. This prevents a gateway/proxy `504` on a
multi-ticker `free_real` run under a single B1 worker.

- **Statuses:** `pending` → `running` → `completed` | `completed_with_warnings`
  | `failed`. (`cancelled` is reserved; cancellation is not implemented.)
- **Progress fields on the run:** `processed_count` / `universe_count`,
  `candidate_count`, `error_count`, `warnings`, and a computed
  `progress_pct = round(processed_count / universe_count * 100, 1)` (0 when the
  universe is empty). The `POST` response also carries `is_async: true` and a
  human-readable `message`.
- **Idempotency:** a run already in a terminal state is never reprocessed; a run
  already `running` (and newer than 30 minutes) is not picked up by a second
  worker. Candidates are not duplicated for the same run/ticker.
- **Durability limitation:** `BackgroundTasks` are **process-local** and not
  durable across an App Service restart. This is acceptable for the Phase 25.1
  MVP — a future phase can add a durable queue (Service Bus / Functions). If the
  browser closes mid-run the admin can reopen `/admin/discovery` and the recent
  run (and its committed progress) is still visible.
- The oversized/empty universe guard still runs **before** the row is created,
  so a rejected run (422) schedules no background work.

**Run creation (`POST /runs`) body:**
```json
{
  "provider_name": "free_real",          // free_real | eodhd_free_real | mock
  "universe_source": "curated_seed",     // curated_seed | manual_tickers
  "tickers": ["AAPL", "MSFT"],           // only for manual_tickers
  "exchange": "US",
  "lookback_days": 90
}
```

**Run creation response (Phase 25.1 — returns fast):**
```json
{
  "id": "…",
  "status": "pending",
  "provider_name": "free_real",
  "universe_count": 3,
  "processed_count": 0,
  "candidate_count": 0,
  "error_count": 0,
  "progress_pct": 0.0,
  "is_async": true,
  "human_review_required": true,
  "message": "Discovery run started. Processing in the background — refresh or poll run status for progress."
}
```

**Candidate list filters/sorts (`GET /runs/{run_id}/candidates`):**
- Filters: `sector`, `grade`, `momentum_label`, `catalyst_coverage_status`,
  `source_quality`, `score_min`, `missing_info_max`, `has_press_releases`,
  `has_news`, `ticker`.
- Sort keys: `rank`, `candidate_score` (default), `latest_catalyst_date`,
  `momentum_score`, `catalyst_score`, `fundamentals_score`, `created_at`.

**Scoring formula (internal prioritization only):**
```
candidate_score =
    0.30 * momentum_score
  + 0.25 * catalyst_score
  + 0.20 * fundamentals_score
  + 0.15 * source_quality_score
  + 0.10 * data_completeness_score
  - risk_penalty            (0–40 points)   → clamped to 0–100
```
Grades: `high_internal_interest` (≥65), `medium_internal_interest` (≥40),
`low_internal_interest` (<40), `data_insufficient` (mock / provider failure /
no fundamentals + no price history + no catalysts).

**Notes:**
- Default provider: `free_real` (SEC EDGAR + free price + internal trend, no paid access).
- The scan reuses the existing company-analysis workflow per ticker; for a small
  bounded universe this is the sanctioned MVP path (it also persists a draft
  report the candidate links to). "Run Full Analysis" re-runs the workflow.
- CI runs entirely offline: the per-ticker signal extractor is injectable and
  tests supply canned signals — no provider/SEC/GDELT/news calls.

## Phase 27: Thesis-to-Universe Discovery (Admin / Internal Only)

Extends Phase 25 with a **market-segment / thesis** discovery mode. An admin
describes a segment / theme / region in natural language; the backend parses it
deterministically, builds a **bounded universe of real public companies** from a
curated reference registry, and scans it through the existing Phase 25 pipeline.
Same hard safety guarantees as Phase 25 (internal only, no BUY/SELL/HOLD/WATCH,
no price target / fair value / upside / recommendation, `human_review_required=true`,
`is_public=false`, no public publish route).

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/market-discovery/thesis-runs` | **Start** a thesis discovery run — parses the thesis, builds a bounded universe, returns `run_id` immediately (`status="pending"`); scans in the background |
| GET | `/api/v1/market-discovery/thesis-runs/{run_id}` | Get a thesis run incl. `parsed_thesis_json` + `universe_json` (alias of `GET /runs/{run_id}`) |

The existing `GET /runs/{run_id}`, `GET /runs/{run_id}/candidates`,
`GET /candidates/{candidate_id}`, and
`POST /candidates/{candidate_id}/run-analysis` endpoints serve thesis runs too (a
thesis run **is** a discovery run with `mode="thesis"`).

**Thesis run creation (`POST /thesis-runs`) body:**
```json
{
  "thesis_text": "European defense suppliers benefiting from NATO spending",
  "region": "Europe",                 // optional
  "country": "Germany",               // optional
  "sector": "Industrials",            // optional
  "industry": "Aerospace & Defense",  // optional
  "industry_keywords": ["defense"],   // optional
  "market_cap_bucket": "large_cap",   // optional
  "max_universe_size": 25,            // hard cap ≤ 50 (default 25)
  "max_candidates": 10,
  "provider_name": "free_real",
  "lookback_days": 90
}
```

**Rejections (422, before any background work):**
- A **vague thesis** that cannot bound a universe (e.g. "best stocks to buy",
  "top stocks") → `needs_narrowing`.
- A thesis that **matched no company** in the curated registry (e.g. after a
  region filter excludes everything).

**Run read additions (`mode="thesis"`):** `mode`, `thesis_text`,
`parsed_thesis_json` (themes / sectors / industries / regions / countries /
keywords / confidence / needs_narrowing), and `universe_json`
(`items[]` with per-ticker `relevance_score_pre_scan`, `matched_keywords`,
`relevance_reason`, `universe_source`, `source_tier`; `excluded[]` with reasons;
`source_summary`).

**Candidate additions (thesis runs only):** `thesis_relevance_score`,
`combined_internal_score`, and `thesis_match_json` (matched keywords, relevance
reason, `internal_interest_label`, source/tier). New sort keys:
`combined_internal_score`, `thesis_relevance_score`. Thesis candidates rank by
`combined_internal_score`.

**Scoring (internal prioritization only, deterministic):**
```
thesis_relevance_score (0–100, pre-scan) — theme/keyword/sector/industry/region
   match + source confidence + catalyst intent − weak-metadata penalty.

combined_internal_score =
    0.45 * thesis_relevance_score
  + 0.35 * discovery_score          (Phase 25 candidate_score)
  + 0.10 * catalyst_score
  + 0.10 * source_quality_score
  - missing_data_penalty            → clamped to 0–100
```
Internal-only interest labels (never recommendations):
`high_internal_research_interest` (≥65), `medium_internal_research_interest`
(≥40), `low_internal_research_interest` (≥20), `insufficient_data`
(< 20 or discovery data insufficient).

**Notes:**
- The parser and universe builder are fully deterministic (keyword tables + a
  curated registry of **real, non-fabricated** public issuers) — no LLM, no
  network. Supported themes: defense, semiconductors, nuclear energy,
  grid/electrification, robotics/automation, biotech/pharma, banks/fintech,
  mining/materials, AI infrastructure.
- The curated universe is a bounded research **search space**, not an index and
  not a recommendation list. Non-US names produce a sparse `free_real` scan
  (SEC-based); the curated registry still supplies identity metadata (never
  fabricated) and the candidate is flagged accordingly.

---

## Phase 27.1B — Luxury/Watch Theme + Supported Themes (internal admin only)

Adds the `luxury_goods` research theme, a canonical sector taxonomy, and a
read-only endpoint that tells the admin UI which themes actually resolve.
**No DB migration** — everything reuses the existing Phase 27 JSONB columns
(`discovery_runs.universe_json`, `discovery_candidates.thesis_match_json`,
`raw_signal_json`, `warnings_json`, `missing_fields_json`).

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/market-discovery/supported-themes` | Themes the parser matches, sector aliases, and example thesis queries |

**Response:**
```json
{
  "themes": [
    {
      "id": "luxury_goods",
      "label": "Luxury goods / watches / jewelry",
      "keywords": ["luxury", "watches", "jewelry", "personal goods"],
      "sectors": ["Consumer Discretionary"],
      "industries": ["Luxury Goods", "Watches & Jewelry", "Personal Goods"],
      "examples": [
        "European watch producers",
        "Swiss watch companies",
        "European luxury goods companies"
      ],
      "regions": ["Asia", "Europe", "North America"],
      "countries": ["Denmark", "France", "..."],
      "universe_company_count": 11
    }
  ],
  "sectors": [
    {
      "sector": "Consumer Discretionary",
      "aliases": ["luxury", "luxury goods", "watches & jewelry", "..."],
      "industries": ["Luxury Goods", "Watches & Jewelry", "..."]
    }
  ],
  "examples": ["European watch producers", "..."],
  "coverage_note": "Thesis discovery runs against a bounded curated universe bootstrap, not a full-market scan. …",
  "disclaimer": "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. …"
}
```

The payload is **derived** from the parser's theme table joined with the curated
registry — never a second hand-maintained list — so the UI can never advertise a
theme that parses but yields an empty universe. A test asserts every advertised
example builds a non-empty universe.

**Themes:** `defense`, `semiconductors`, `nuclear_energy`,
`grid_electrification`, `robotics_automation`, `biotech_pharma`,
`banks_fintech`, `mining_materials`, `ai_infrastructure`, **`luxury_goods`**
(new).

**Sector taxonomy.** `app/services/sector_taxonomy.py` maps sector/industry
aliases to canonical names, so a thesis filtered on `sector="Luxury Goods"`
matches issuers the registry tags `Consumer Discretionary`. An **unknown**
sector normalizes to `null` rather than being guessed — a wrong guess would
silently widen the search beyond what the admin asked for.

**Curated luxury registry (11 real issuers).** `UHR.SW` (Swatch Group),
`CFR.SW` (Richemont), `MC.PA` (LVMH), `RMS.PA` (Hermes), `KER.PA` (Kering),
`MONC.MI` (Moncler), `BRBY.LSE` (Burberry), `PNDORA.CO` (Pandora),
`CPRI.US` (Capri), `TPR.US` (Tapestry), `1913.HK` (Prada). Each carries
`universe_source="curated_theme_registry"` and
`source_tier="T3_curated_reference_list"`.

**Company-name provenance.** A discovery scan creates a stub `Company` row;
before Phase 27.1B that stub was named after the ticker, and its truthiness
shadowed the curated registry name — candidates displayed `UHR` instead of
`Swatch Group AG`. Resolution order is **live provider name → curated registry
name → ticker**. The origin is recorded on `raw_signal_json.identity` and
`thesis_match_json`:

| Field | Values |
|---|---|
| `company_name_source` | `provider_profile` \| `curated_theme_registry` \| `null` |
| `company_name_source_tier` | `T3_curated_reference_list` \| `null` |

Attribution is decided by two signals, **not** by whether the incoming name
looks like a bare ticker:

1. `data_coverage.profile_source` — the provider stating whether it sourced a
   profile at all. When it is `not_sourced`, nothing in the identity block is
   credited to the provider.
2. The **value** — a display name equal to the curated registry string is
   attributed to the registry, whichever layer handed it over.

`provider_profile` therefore requires the provider to have sourced a profile
*and* produced a name differing from the curated string.

> Phase 27.1B initially decided this with a placeholder test alone, which was
> wrong in the real pipeline: `ensure_company` seeds the Company row with the
> curated name and the workflow echoes it back, so a curated name no longer
> looks like a placeholder. Staging showed all eight European luxury candidates
> reporting `provider_profile` while their provider profile was explicitly
> `not_sourced`. Corrected in `fix: preserve curated company-name provenance`.

A curated display name is **never** attributed to SEC or a provider, and
`legal_name` is left exactly as the scan produced it (the bare ticker when the
provider sourced no profile) — a curated display name is not evidence of a
legal name.

**Limitations (stated in `coverage_note`):**
- Bounded curated bootstrap — **not** a full-market scan of global equities.
- Each theme is backed by a small hand-curated list of real issuers, not an
  exhaustive index of the segment.
- Non-US issuers are not SEC-eligible (Phase 27.1A `exchange_registry` gating),
  so their fundamentals degrade honestly to `not_sourced` /
  `requires_human_research` rather than resolving a ticker collision. `MC.PA` is
  LVMH here and never Moelis.
- Results are internal research candidates: `human_review_required=true`,
  `is_public=false`, `publication_ready=false`, no public publish route, no
  recommendation of any kind.

---

## Phase 27.1C — Prompt-Derived Autofill + Controlled Selectors (internal admin only)

Adds prompt-derived auto-detection of Region / Country / Sector from the thesis
text, plus **controlled** (non-free-text) selector values for those fields. The
admin no longer has to fill Region/Country/Sector when the thesis already states
them, and can no longer submit an unsupported value. **No DB migration** —
purely parser, validation, and two read/preview endpoints.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/market-discovery/parse-thesis` | Parse a thesis for selector auto-fill — **does not create a run** |
| GET | `/api/v1/market-discovery/supported-filters` | Canonical Region / Country / Sector / Industry selector options |

**`POST /parse-thesis` request:**
```json
{ "thesis": "Swiss watch companies" }
```

**`POST /parse-thesis` response:**
```json
{
  "themes": ["luxury_goods"],
  "region": "Europe",
  "country": "Switzerland",
  "sector": "Consumer Discretionary",
  "industry": "Watches & Jewelry",
  "theme": "luxury_goods",
  "confidence": 1.0,
  "extraction_source": "prompt_text",
  "needs_narrowing": false,
  "warnings": [],
  "disclaimer": "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. …"
}
```

Detection examples: `"European watch producers"` → Region `Europe`, Sector
`Consumer Discretionary` (Country empty); `"Danish jewelry companies"` → Country
`Denmark`, Region `Europe`; `"US semiconductor equipment companies"` → Country
`United States`, Region `North America`, Sector `Technology`. The endpoint is a
pure, DB-free preview — it never creates a run and never emits a recommendation.

**`GET /supported-filters` response:**
```json
{
  "regions": [{ "value": "Europe", "label": "Europe" }, "…"],
  "countries": [
    { "value": "Switzerland", "label": "Switzerland", "region": "Europe" },
    { "value": "United States", "label": "United States", "region": "North America" }
  ],
  "sectors": [{ "value": "Consumer Discretionary", "label": "Consumer Discretionary" }, "…"],
  "industries": [{ "value": "Watches & Jewelry", "label": "Watches & Jewelry", "sector": "Consumer Discretionary" }],
  "disclaimer": "INTERNAL ADMIN USE ONLY. …"
}
```

Regions and countries are **derived from the exchange registry** (so they can
never drift from the venues the platform can resolve); sectors are the canonical
GICS-style sectors — **aliases never appear as selector values** (they resolve
internally to a canonical sector). The admin UI renders searchable
select/combobox fields whose options come from here; empty/`null` means "not
specified".

**Explicit-over-prompt precedence (on `POST /thesis-runs`).**
- If the admin leaves Region/Country/Sector empty, the **parsed prompt values are
  used** to filter the universe.
- If the admin sets a value, the **explicit value overrides** the parsed prompt
  value.
- When an explicit value **conflicts** with the prompt, the explicit choice is
  kept and a warning is surfaced on the run (e.g. `"Prompt mentions Switzerland,
  but explicit Country=Denmark was selected."`).
- Region/Country/Sector filters outside the supported options are rejected with
  a clear `422` (`"Country must be one of the supported options."`, etc.). Values
  are canonicalized case-insensitively (`"switzerland"` → `"Switzerland"`).

**Strict country filtering (source of truth).** Country is strict: when a country
filter is present it is the *sole* geographic filter — the region is not allowed
to broaden a country-scoped search. Region applies only when no country is set.
So `"Swiss watch companies"` returns only Swiss issuers (`UHR`, `CFR`), never
every European luxury name; `"Danish jewelry companies"` returns only `PNDORA`.
Safety guarantees are unchanged (internal only, no BUY/SELL/HOLD/WATCH, no price
target/fair value/upside/recommendation, `human_review_required=true`,
`is_public=false`, no public publish route).
