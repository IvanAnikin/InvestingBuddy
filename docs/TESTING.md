# Testing

## Status: Active — Phase 3.5

This document describes the InvestingBuddy testing strategy and test commands.

Update this file when:
- Test stack or configuration changes
- CI commands change
- New test patterns are established

For testing rules see `.claude/skills/testing-qa/SKILL.md`.
For quick check commands see `.claude/skills/ci-test-runner/SKILL.md`.

---

## Testing Philosophy

- Test service logic, not just route handlers.
- Test error cases, not just the happy path.
- Test authentication and authorization enforcement explicitly.
- Mock all external services (Azure OpenAI, Blob Storage, AI Search, financial data APIs).
- Never require real Azure credentials for local test runs.
- Agent workflow smoke tests must run without real LLM calls.

---

## Backend Test Stack

```
pytest
pytest-asyncio          async test support
httpx                   API endpoint testing
pytest-mock             external service mocking
factory_boy             test data factories (to add in Phase 1)
```

Test directory structure:
```
apps/api/tests/
├── unit/               service logic, domain validation
├── integration/        API endpoints, database interactions
└── workflows/          LangGraph smoke tests
```

---

## Test Count Reference

| Phase | Test count | Notes |
|---|---|---|
| Phase 1 | 2 | Health endpoint smoke |
| Phase 2 | 27 | Agent workflow + company storage |
| Phase 3 | 76 | Citations, research storage |
| Phase 3.5 | 96 | +20 report validation (all offline) |

## Running Backend Tests

```bash
cd apps/api

# Run all tests
.venv/bin/pytest tests/ -q

# Run with verbose output
.venv/bin/pytest tests/ -v

# Run specific test file
.venv/bin/pytest tests/test_report_validation.py -v

# Run tests matching a name pattern
.venv/bin/pytest tests/ -k "test_report" -v

# Stop on first failure
.venv/bin/pytest tests/ -x
```

---

## Linting and Type Checking (Backend)

```bash
cd apps/api

# Lint
.venv/bin/ruff check .

# Auto-fix lint issues
.venv/bin/ruff check --fix .

# Type check (run when types are touched)
.venv/bin/mypy .
```

### Quick full backend check

```bash
cd apps/api && .venv/bin/pytest tests/ -q && .venv/bin/ruff check . && echo "ALL BACKEND CHECKS PASSED"
```

---

## Frontend Test Stack

Phase 1:
- TypeScript strict mode
- ESLint
- `npm run build` as smoke test

Phase 2+ (future):
- Playwright for end-to-end testing

---

## Running Frontend Checks

```bash
cd apps/web

npm run typecheck
npm run lint
npm run build
```

### Quick full frontend check

```bash
cd apps/web && npm run typecheck && npm run lint && npm run build && echo "ALL FRONTEND CHECKS PASSED"
```

---

## CI Integration

See `.github/workflows/api-ci.yml` and `.github/workflows/web-ci.yml` (Phase 1).

Tests must pass on every PR before merge to `main`.

---

## Test Data and Mocking Rules

- Use test fixtures or factory_boy for database test data.
- Use `pytest-mock` to mock Azure OpenAI, Blob Storage and AI Search.
- Use recorded/stubbed responses for financial data API tests.
- Never use production financial data in tests.
- Never connect to real Azure services in unit or integration tests.
- Use an in-memory SQLite database for simple unit tests.
- Use a dedicated PostgreSQL test database for integration tests (separate from dev DB).

---

## Primary-Document Ingestion Tests (Phase 32A Slice 5)

> Implemented — PR open, pending staging validation.

Slice 5 adds offline, network-free tests covering the deepened ingestion path
(all under `apps/api/tests/`):

- `test_phase32a_slice5_extraction.py` — pdfplumber/HTML structure-aware extraction
- `test_phase32a_slice5_validation.py` — stricter table/OCR fact validation (validated / excerpt_only / rejected)
- `test_phase32a_slice5_ingestion.py` — the hierarchy + wall-budget wiring in `maybe_run_council`
- `test_phase32a_slice5_citations.py` — page/section/table-located citations, no citation from failed/metadata-only extraction
- `test_phase32a_slice5_persistence.py` — `ExtractedDocument` / `ExtractedFact` persistence + dedup
- `test_phase32a_slice5_reuse.py` — TTL-bounded reuse of a persisted extraction
- `test_phase32a_slice5_edgecases.py` — magic-byte / bomb / SSRF-guard / degradation edge cases
- Shared fixtures in `tests/helpers/pdf_fixtures.py` (new `tests/helpers/` package)

**Test dependency:** these tests require `pdfplumber>=0.11,<0.12` (and its
transitive pdfminer.six + Pillow) to be installed in the API venv — the same
runtime dependency added for the feature. No OCR binary is needed (the OCR path is
a NoOp seam this slice). On a local Python 3.14 venv, install with
`--only-binary=:all:` (cryptography has no 3.14 source-build path there); the Azure
App Service Python 3.12 runtime resolves the pure-Python wheels directly. Fetch /
parse tests never touch real network or real Azure services (fixtures + injected
resolvers only).
