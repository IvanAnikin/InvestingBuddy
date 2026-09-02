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
| `investment-decision-content.spec.ts` | Whether the research is DECISION-USEFUL: the summary opens with what could raise and pressure value, the setup is characterised without being rated, what-to-watch names this issuer's own figures, resilience is separated from fragility and neither is scored, each agent contributes an interpretation with its mechanism, a data gap is never filed as a business risk, machine paths stay collapsed, council numbers cannot contradict the report's canonical figures, and discovery compares businesses rather than gap counts. |
| `investment-decision-content.spec.ts` (Signal routing block) | The economic/limitation boundary: the source critic cannot populate an economic section, an evidence statement never reads as a value driver, a research limitation never appears as a company risk, missing EBITDA stays in confidence, open questions are about the business, gap counts and disclosure coverage are not primary comparison columns, the new chair synthesis beats the legacy fallback, and the investor financial view contains no `_current_period` / `_primary_filing` / `T1_primary_filing`. |
| `landing-reveal-audit.spec.ts` | Whether every scroll-revealed landing section actually becomes visible on a normal scroll at 1440 / 1280 / 768 / 390, and that a reduced-motion visitor never receives a hidden one. Distinguishes a real reveal bug from a full-page-capture artifact. |
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
- **A prompt change cannot be measured on a starved pack.** The first attempt
  to measure the updated council re-ran it against an evidence pack built from
  `report_content` alone — 8 items, no structured facts. Every agent correctly
  answered "there is not enough data", which measured the harness, not the
  prompts. Pass the same `historical_facts` the live pipeline passes.
- **A guard that suppresses content must be validated on real prose.** The
  numeric-consistency check flagged 13 of 111 real council sentences before it
  was fit to ship, every one a false positive: period tokens read as numbers
  (`H1` → 1), a trailing comma making `2026,` look like a magnitude, every
  number in a sentence tested against the one metric it named, and a canonical
  set that held only the headline slots while the council cited history the
  report also carries. Verify against real output, count the false positives,
  and only then let it withhold anything.
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

---

## POST-V2 Live Corrective Tests

Four defects that only running the product on real data exposed. Each has tests
that would have caught it, and each test file states the live observation it
pins rather than describing the code.

### Backend

| File | Pins |
|---|---|
| `tests/test_v2_async_company_research.py` | The front door does not run research inside the HTTP request: the submit is bounded work, the job row is committed before any expensive work, stages come from the graph's own nodes, a reload recovers the job by company, the worker uses its OWN session, success links the EXACT report, failure persists a terminal state, a double submit never starts a second run, identity is carried exactly, and the `astream` progress path returns the SAME final state as `ainvoke` (asserted directly — stage progress must not have been bought with a different research run). |
| `tests/test_v2_discovery_contract_and_jurisdiction.py` | The council's economic fields survive LLM → chair aggregation → storage → API response → serialization, in every bucket; a review written before the fields existed still reads; `CandidateNote` is compared against the response model directly so a new field cannot silently fail to reach a reader. Jurisdiction: SEC applies to a US issuer and to no one else, a gap is named after the venue that serves the issuer, US behaviour is unchanged, and no issuer name appears in EXECUTABLE code (comments may name the issuers whose live run exposed the defect — the scan strips comments and docstrings with `ast`). |
| `tests/test_v2_current_research_resolution.py` | Current research is the newest STRUCTURED report for THAT company; a legacy screening draft is never it, a version stamp without structured content is never it, a newer draft does not supersede real research, and resolution is company-scoped. What the signals carry: period-labelled figures with their scope, the prior chair synthesis, company risks — and an ABSENT key rather than an empty one, so the council cannot read `""` as a finding. |

### Frontend

Playwright is the only TS test runner in this repo, so the pure derivation
modules are exercised through it directly (imported, not driven through a page).
That keeps one runner and one CI step; a page can only hold one fixture at a
time, and the interesting numeric cases are combinations of metric × period ×
scope × currency.

| File | Pins |
|---|---|
| `tests/e2e/v2-numeric-guard.spec.ts` | The scope-aware canonical index. The CFR regression in full: Specialist Watchmakers EUR 107m and 3.4%, Jewellery Maisons EUR 5,037m and Group EUR 4,500m all ACCEPTED; Group EUR 107m and a segment margin assigned to the Group both REJECTED. Plus annual/current conflict, a historical series claim, ambiguous scope resolved without substituting Group, a foreign currency left unadjudicated — and every previously-fixed false positive (H1, bare years, trailing commas, percent-beside-amount). |
| `tests/e2e/v2-discovery-signal-routing.spec.ts` | "Sparse data" is a research limitation, not an economic downside. The twelve `downside_drivers` strings are copied VERBATIM from a real local council run over six European luxury names — every one of them is an evidence statement, and every one must route away from "Could pressure value" while genuine economic drivers stay. |
| `tests/e2e/v2-live-corrective.spec.ts` | End to end through the real renderer: the async submit returns a running job, stages are named with no percentage, the run id is in the URL and survives a refresh, the finished run opens the exact report, a second submit joins the first; segment claims survive and mis-scoped ones are withheld; the cases are the council's and carry no implementation vocabulary; a legacy report renders translated while the technical page keeps the raw record; and the whole anonymous auth contract including `/research`. |
| `tests/e2e/workflow-contract.spec.ts` | Rewritten for the async contract. The field vocabulary is unchanged — an async rewrite is exactly the kind of change that silently drops a field — plus a new assertion that the front door **never** calls the synchronous pipeline endpoints, which is the regression the whole corrective exists to prevent. |

### Fixtures added to the mock backend

* A **segment-reporting issuer** (`…a5`) with Group, Jewellery Maisons and
  Specialist Watchmakers series. Every previous report fixture reported at
  Group scope only, which is why no local test could have caught the live CFR
  suppression.
* A **legacy technical-prose report** (`…a6`) whose bull/bear are written in
  source tiers, provider states and machine field paths — the shape the clean
  view used to render verbatim.
* **Async company-research job endpoints**, advancing one stage per poll so the
  submit → poll → open path is deterministic and takes seconds, not minutes.
