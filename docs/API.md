# API Reference

## Status: Placeholder — Phase 0

This document describes the InvestingBuddy REST API.

Update this file when:
- A new endpoint is added
- An existing endpoint changes its request or response schema
- An endpoint is deprecated or removed
- Authentication requirements change

For implementation rules see `.claude/skills/backend-fastapi/SKILL.md`.

---

## Base URLs

```
Development:    http://localhost:8000
Staging:        https://api-staging.investingbuddy.com (future)
Production:     https://api.investingbuddy.com (future)
```

---

## API Tiers

### Public (`/api/`)
No authentication required. Returns only published, public-facing data.

### User (`/api/me/`)
Requires valid authentication token. Returns only the requesting user's own data.

### Admin (`/api/admin/`)
Requires authentication + admin role. Returns platform management data and workflow controls.

---

## Authentication

MVP uses Clerk for authentication. Include the Clerk JWT token in the Authorization header:
```
Authorization: Bearer <token>
```

---

## Public Endpoints

> Not yet implemented — Phase 1+

| Method | Path | Description |
|---|---|---|
| GET | /api/reports | List published reports |
| GET | /api/reports/{slug} | Report detail |
| GET | /api/themes | List themes |
| GET | /api/themes/{slug} | Theme detail |
| GET | /api/companies/{ticker} | Company page data |
| GET | /health | Health check |

---

## User Endpoints (Authenticated)

> Not yet implemented — Phase 1+ (Version 2)

| Method | Path | Description |
|---|---|---|
| GET | /api/me | Current user profile |
| PUT | /api/me/preferences | Update preferences |
| GET | /api/me/portfolio | Get portfolio |
| POST | /api/me/portfolio/positions | Add position |
| PUT | /api/me/portfolio/positions/{id} | Update position |
| DELETE | /api/me/portfolio/positions/{id} | Remove position |
| GET | /api/me/recommendations | Personalized recommendations |
| GET | /api/me/notifications | Notification settings |
| PUT | /api/me/notifications | Update notification settings |

---

## Admin Endpoints (Admin Role Required)

> Not yet implemented — Phase 1+

| Method | Path | Description |
|---|---|---|
| GET | /api/admin/reports | List all reports (including drafts) |
| GET | /api/admin/reports/{id} | Report detail with agent debug info |
| POST | /api/admin/reports/{id}/publish | Publish a report |
| POST | /api/admin/reports/{id}/reject | Reject a report |
| GET | /api/admin/agent-runs | List agent run history |
| GET | /api/admin/agent-runs/{id} | Agent run detail with steps |
| POST | /api/admin/workflows/company-deep-dive/run | Trigger company analysis |
| POST | /api/admin/workflows/weekly-research/run | Trigger weekly research |
| GET | /api/admin/companies | List all companies |
| POST | /api/admin/companies | Add company/ticker |
| GET | /api/admin/watchlist | Current watchlist |
| GET | /api/admin/judge-evaluations | Judge evaluation results |
| POST | /api/admin/prompts/{id}/approve | Approve prompt update |

---

## Standard Error Responses

```json
{
  "detail": "Human-readable error message"
}
```

| Status | Meaning |
|---|---|
| 400 | Bad request / validation error |
| 401 | Not authenticated |
| 403 | Authenticated but not authorized |
| 404 | Resource not found |
| 422 | Unprocessable entity (Pydantic validation failed) |
| 500 | Internal server error |

---

## Not Yet Implemented

All endpoints above are planned but not yet built. Implementation begins in Phase 1.
