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

- TypeScript strict mode
- ESLint
- `npm run build` as smoke test
- Playwright end-to-end suite (`apps/web/tests/e2e`), run against a local dev
  server whose SSR fetches point at a zero-dependency mock backend
  (`tests/support/mock-backend.mjs`). No live environment or provider is
  contacted, and `AUTH_TEST_MODE=true` provides a deterministic sign-in so no
  real OAuth is exercised.

### Product-experience specs

| Spec | Covers |
|---|---|
| `home.spec.ts` | The public landing page: positioning copy, both primary CTAs, no forbidden trading/publishing control, and that the admin entry appears only for a signed-in allowlisted admin. |
| `research-experience.spec.ts` | Access control on every `/research` route, the four research surfaces, the round trip between the reader-facing and technical report views, and that all existing `/admin` routes still render. |
| `research-responsive-a11y.spec.ts` | Horizontal-overflow check at 1440 / 1280 / 768 / 390, the mobile navigation, `prefers-reduced-motion`, console-error freedom, and keyboard access (skip link, arrow-key tablists). |
| `visual-audit.spec.ts` | Programmatic legibility audit: WCAG AA contrast against the effective composited background, text clipped by its own box, controls without an accessible name, and heading order — on static pages AND on the driven states (council review, candidate comparison, every disclosure open) that a page visit alone never reaches. |
| `visual-qa.spec.ts` | Screenshot capture for human review. Skipped unless `IB_SHOTS` is set. |
| `live-defect-regressions.spec.ts` | The two defects that reached production in PR #176: SSR/browser hydration mismatch from host-dependent date formatting (browser contexts pinned to Europe/Prague AND Pacific/Kiritimati), and horizontal overflow at 390px from long unbroken external strings. Both were verified by reverting the fix and confirming the test fails. |
| `workflow-contract.spec.ts` | **CONTRACT** — what each console puts on the wire. Captures the request body in the browser and asserts the company identity, provider, flags, thesis text and inferred filters, plus parity between `/admin/*` and `/research/*`, honest failure states, and that a linked report is not mistaken for a completed analysis. |
| `investor-research-experience.spec.ts` | The discovery council on the reader-facing surface (it appears when a review is persisted, it is never started by a page load, the trigger uses the existing backend action, it stays tied to its own `discovery_run_id`, it presents bands rather than an invented ranking, and disagreement comes from differing `internal_action` values); the three candidate CTA states; and the report's reading order — every agent's conclusion, red team, chair, questions sourced from the council, company risk kept apart from research limitation, record entries routed to research confidence, evidence progressively disclosed, and the annual/current separation intact. Plus overflow at 1440 / 1280 / 768 / 390 **with every disclosure open**. |

### Mock mode vs real integration

The suite above runs against `tests/support/mock-backend.mjs` — deterministic,
offline, and the right tool for UI behaviour, error states and screenshots.

It is not a substitute for real integration, and one thing makes that concrete:
a fixture can answer anything. `workflow-contract.spec.ts` exists precisely
because of that gap — it asserts what the UI ASKS FOR rather than what it is
told, so a fixture cannot make a wrong request look right. The mock backend now
also ECHOES the request (company, thesis, provider) rather than answering with a
fixed fixture, so a preview run reveals an identity or thesis bug instead of
masking it.

For a real end-to-end check, run the full local stack (Docker PostgreSQL +
FastAPI + Next.js, see `.claude/skills/local-dev-operator/SKILL.md`) and drive
the UI against it. `/research/*` renders a "Preview data" strip whenever the
backend's own `/health.environment` reports `test`, so it is never ambiguous
which backend a screen is showing.

### Conditions a single-machine suite cannot produce

Two defect classes shipped in PR #176 because the suite had no way to create
the conditions they need. Both now have fixtures and tests:

- **Host divergence.** SSR and hydration run in one process on one machine
  locally, so they always agree on locale and time zone. Tests must pin a
  browser context to a zone far enough to flip the calendar day —
  `Pacific/Kiritimati` (UTC+14) catches what `Europe/Prague` does not, because
  a +02:00 offset only flips the day for timestamps in the last hours of a UTC
  day and the fixtures sit at 10:00 UTC.
- **Real-world string lengths.** Fixtures with short titles and short field
  names cannot reveal a layout that breaks on a 140-character CDN URL. The
  `PERIODS_REPORT_ID` fixture now carries an untitled document with a long
  percent-encoded URL, an untitled appendix source, and 70-character dotted
  field paths.
- **Content behind a disclosure.** A collapsed `<details>` has no layout, so a
  containment or contrast check run against one measures nothing while passing.
  Every such check opens all disclosures first. (`toContainText` reads collapsed
  DOM text and would pass either way — that is how an empty council panel once
  shipped green.)
- **Fixtures agree with the derivation that wrote them.** A fixture authored
  alongside a view model cannot disagree with it. The record-gap routing rules
  were written from live report payloads, not from fixtures: the first
  implementation sourced "open research questions" from the chair's
  `primary_open_questions`, which fixtures made look correct and which on three
  of four live issuers opened with
  `Blocking gap: Required field missing: identity.isin`. Read real payloads
  read-only before trusting a derivation about their shape.

**Two dev servers must not share `apps/web/.next`.** `next.config.ts` sets
`lockDistDir: false`, which disables the guard, and running the Playwright dev
server on `:3100` alongside a manual one on `:3000` corrupts the route manifest
— routes start 404ing in ways that look like application bugs. Stop one before
starting the other.

Regenerate the review screenshots with:

```bash
cd apps/web
IB_SHOTS=/absolute/output/dir npx playwright test tests/e2e/visual-qa.spec.ts
```

---

## Running Frontend Checks

```bash
cd apps/web

npm run typecheck
npm run lint
npm run build
npm run test:e2e
```

### Quick full frontend check

```bash
cd apps/web && npm run typecheck && npm run lint && npm run build && npm run test:e2e && echo "ALL FRONTEND CHECKS PASSED"
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
