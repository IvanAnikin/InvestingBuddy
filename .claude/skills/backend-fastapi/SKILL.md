# Backend FastAPI Agent Skill

## Role

You implement backend features in Python using FastAPI, SQLAlchemy and Pydantic.

---

## Responsibilities

- API route handlers (thin, delegating to services)
- Pydantic v2 request and response schemas
- SQLAlchemy async ORM models
- Service layer business logic
- Dependency injection
- Error handling and HTTP status codes
- OpenAPI documentation
- pytest unit and integration tests

---

## Architecture Rules

- Routes must be thin. All business logic goes in services.
- Use Pydantic schemas for all request and response models.
- Use SQLAlchemy models for database entities — never use raw SQL unless absolutely necessary.
- Do not bypass the service layer from routes.
- Use dependency injection for database sessions and auth context.
- Never hardcode secrets, environment variables or Azure credentials.
- Always add tests for new service methods.
- Use async where the rest of the project uses async — be consistent.

---

## Typical Files

```
apps/api/app/main.py
apps/api/app/core/config.py
apps/api/app/core/security.py
apps/api/app/core/exceptions.py
apps/api/app/db/session.py
apps/api/app/db/base.py
apps/api/app/models/            # SQLAlchemy ORM models
apps/api/app/schemas/           # Pydantic request/response schemas
apps/api/app/api/public/        # public API routes
apps/api/app/api/user/          # authenticated user routes
apps/api/app/api/admin/         # admin-only routes
apps/api/app/services/          # business logic
apps/api/tests/                 # pytest tests
```

---

## Key Domain Models

When implementing models and schemas, these are the core entities:

- `users` — roles: public_user, subscriber, admin, super_admin
- `companies` — status: new, researching, analyzed, watchlist, recommended_buy, recommended_sell, rejected, archived
- `recommendations` — ratings: BUY, WATCH, HOLD, SELL, REJECT; status: draft, review, published, closed, invalidated
- `reports` — types: weekly, monthly, quarterly, yearly, company_deep_dive, theme_report, personalized
- `agent_runs` — trigger types: manual, scheduled, system, judge_requested
- `sources` — financial documents, filings, news, industry reports
- `citations` — links between claims and sources

See `docs/DATABASE.md` for the full schema reference.

---

## API Structure

```
/api/                       public, no auth
/api/me/                    authenticated user
/api/admin/                 admin only
```

Admin routes must be protected. Never expose admin functionality as public endpoints.

See `docs/API.md` for the full endpoint reference.

---

## Error Handling

- Use FastAPI `HTTPException` for client errors
- Use structured error responses with `detail` field
- Log unexpected server errors
- Never expose stack traces or internal details to public responses

---

## Definition of Done

- Endpoint returns correct response for happy path
- Input validation rejects invalid payloads
- Service logic is unit-tested
- Integration test covers the route
- OpenAPI schema is accurate
- `docs/API.md` is updated if a new endpoint was added
- `ruff check .` passes
- `mypy .` passes
- `pytest` passes
