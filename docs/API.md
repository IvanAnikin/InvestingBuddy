# API Reference

## Status: Phase 3 — Sources and Citations endpoints added

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

Request by ticker:
```json
{ "ticker": "VOW3", "exchange": "XETRA" }
```

Response `202 Accepted`:
```json
{
  "agent_run_id": "uuid",
  "draft_report_id": "uuid",
  "status": "completed",
  "summary": "Volkswagen AG is being added to the research pipeline...",
  "workflow_name": "company_analysis",
  "company_name": "Volkswagen AG",
  "ticker": "VOW3"
}
```

Errors:
- `422` — no company_id or ticker provided
- `422` — company not found in database
- `500` — workflow execution error (see agent_run logs)

> **Phase 2/3 note:** The workflow uses deterministic placeholder logic.
> No LLM calls are made. Analysis output is always rated WATCH with `is_placeholder: true`.
> The workflow also creates a placeholder `Source` and `Citation` record (Phase 3).
> Wire real LLM calls in Phase 4 by replacing node bodies in
> `apps/api/app/workflows/company_analysis.py`.

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

## Planned Endpoints (Phase 4+)

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
