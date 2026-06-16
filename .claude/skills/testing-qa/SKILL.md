# Testing / QA Agent Skill

## Role

You ensure the InvestingBuddy platform is reliable by writing and maintaining tests across the full stack.

---

## Responsibilities

- Backend unit tests (service logic, model validation)
- Backend integration tests (API endpoints, database interactions)
- Agent workflow smoke tests (LangGraph workflows with mock LLM)
- Frontend type checks and lint
- CI test commands and configuration
- Regression checks when behavior changes

---

## Backend Testing Stack

```
pytest
pytest-asyncio          (for async tests)
httpx                   (for API endpoint tests)
pytest-mock             (for mocking external services)
factory_boy             (for test data factories)
```

Test directory:
```
apps/api/tests/
apps/api/tests/unit/
apps/api/tests/integration/
apps/api/tests/workflows/       (LangGraph smoke tests)
```

---

## Frontend Testing Stack

Phase 1 (current):
- TypeScript strict mode
- ESLint
- `npm run build` as smoke test

Phase 2 (later):
- Playwright for end-to-end testing of public report pages and admin dashboard

---

## What to Test

### Backend Unit Tests
- Service methods (report_service, company_service, source_service, etc.)
- Investment domain logic (rating validation, citation checks)
- Data normalization functions

### Backend Integration Tests
- API endpoint responses (happy path and error cases)
- Authentication and authorization enforcement
- Database CRUD operations

### Agent Workflow Smoke Tests
- LangGraph workflows can be instantiated and run with mock LLM responses
- Agent state is populated correctly
- Agent_run and agent_step records are created
- Workflow completes without unhandled exceptions

### Security Tests
- Admin endpoints reject unauthenticated requests
- User endpoints reject unauthorized access to other users' data
- Public endpoints do not leak private data

---

## Mocking Rules

- **Mock external APIs** — OpenBB, Azure OpenAI, Azure AI Search, Azure Blob, news APIs
- **Do not require real Azure credentials** for unit or integration tests
- Use recorded API responses (fixtures) or typed mocks for financial data tests
- Keep tests deterministic — no random values or time-dependent assertions
- Use a test database (SQLite in-memory or separate PostgreSQL test DB) — never the production DB

---

## CI Integration

The `api-ci.yml` GitHub Actions workflow must run:
```bash
pytest apps/api/tests/
ruff check apps/api/
mypy apps/api/
```

The `web-ci.yml` GitHub Actions workflow must run:
```bash
npm run typecheck
npm run lint
npm run build
```

Tests must pass before any merge to `main`.

---

## Rules

- Test service logic, not just route handlers.
- Test error cases, not just the happy path.
- Test permission enforcement explicitly.
- Do not require live Azure services for local test runs.
- Do not use production financial data in tests.
- Keep test fixtures up to date when models change.
- Add a smoke test for every new LangGraph workflow.
- CI must fail on broken tests — no ignored failures.

---

## Definition of Done

- Tests are added or updated alongside the feature
- Tests cover the happy path and at least one error case
- Edge cases documented in test docstrings or comments
- Tests pass locally (`pytest`)
- CI command is documented in `docs/TESTING.md`
- No external API calls in unit or integration tests
