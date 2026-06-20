# API Reference

## Status: Phase 2 — Company endpoints and workflow trigger implemented

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
{ "status": "ok", "environment": "development", "version": "0.2.0" }
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

> **Phase 2 note:** The workflow uses deterministic placeholder logic.
> No LLM calls are made. Analysis output is always rated WATCH with `is_placeholder: true`.
> Wire real LLM calls in Phase 3 by replacing node bodies in
> `apps/api/app/workflows/company_analysis.py`.

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

## Planned Endpoints (Phase 3+)

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
| GET | `/api/v1/admin/agent-runs` | Phase 3 |
| GET | `/api/v1/admin/agent-runs/{id}` | Phase 3 |
| POST | `/api/v1/admin/reports/{id}/publish` | Phase 4 |
| POST | `/api/v1/admin/reports/{id}/reject` | Phase 4 |
| GET | `/api/v1/admin/judge-evaluations` | Phase 6 |

### User (Authenticated, Version 2)
| Method | Path | Phase |
|---|---|---|
| GET | `/api/me/recommendations` | Phase 7 |
| GET | `/api/me/portfolio` | Phase 7 |
| POST | `/api/me/portfolio/positions` | Phase 7 |
