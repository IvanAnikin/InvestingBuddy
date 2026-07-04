# Roadmap

## Current Phase: Phase 21 — Playwright Admin Smoke Tests

---

## Phase 0: Agentic Repository Infrastructure ✅

**Status: Complete**

Deliverables:
- [x] `CLAUDE.md` — main orchestrator instruction file
- [x] `AGENTIC_DEVELOPMENT.md` — orchestration guide
- [x] `.claude/skills/` — all specialist skill definitions
- [x] `.claude/commands/` — all reusable command templates
- [x] `docs/` — placeholder documentation for all key areas
- [x] `docs/DECISIONS.md` — initial architecture decisions recorded

---

## Phase 1: Application Skeleton ✅

**Status: Complete**

Goal: A working, deployable skeleton of the full stack with no business logic yet.

Deliverables:
- [x] `apps/api/` — FastAPI skeleton with health endpoint (`GET /health`)
- [x] `apps/api/app/core/` — config, logging, exceptions
- [x] `apps/api/app/db/` — SQLAlchemy async session, base model
- [x] `apps/web/` — Next.js App Router skeleton with homepage
- [x] `docker-compose.yml` — local PostgreSQL container
- [x] `.env.example` — all required environment variable names
- [x] `.github/workflows/api-ci.yml` — backend CI (lint, type check, pytest)
- [x] `.github/workflows/web-ci.yml` — frontend CI (typecheck, lint, build)
- [x] `README.md` — local setup instructions

---

## Phase 21: Playwright Admin Smoke Tests ✅

**Status: Complete**

Goal: Add repeatable frontend/admin smoke tests so the core admin workflow is no longer verified manually.

Deliverables:
- [x] Reports API: `GET /api/v1/reports`, `GET /api/v1/reports/{id}`, `POST /api/v1/reports/{id}/generate-final`, `POST /api/v1/reports/{id}/validate`
- [x] Admin UI pages: dashboard, add company, run analysis, draft reports list, report detail
- [x] Admin layout with safety disclaimers (INTERNAL ADMIN ONLY, NOT INVESTMENT ADVICE, NOT FOR PUBLICATION, HUMAN REVIEW REQUIRED)
- [x] Phase 20 final report UI actions: Generate Internal Final Report Draft, Validate Final Report, Regenerate Section
- [x] Playwright installed (`@playwright/test`) with `playwright.config.ts`
- [x] `tests/e2e/admin-dashboard.spec.ts` — 12 tests
- [x] `tests/e2e/admin-company-flow.spec.ts` — 5 tests
- [x] `tests/e2e/admin-report-flow.spec.ts` — 18 tests (run analysis + reports list + report detail)
- [x] `tests/e2e/safety-copy.spec.ts` — 17 tests
- [x] All tests use Playwright route mocking — no EODHD, Azure OpenAI, or live DB required
- [x] Staging tests opt-in: `ENABLE_STAGING_E2E=true`
- [x] `.github/workflows/frontend-e2e.yml` — opt-in workflow (manual trigger only)
- [x] `.gitignore` updated for playwright-report/, test-results/, blob-report/
- [x] No public BUY/SELL/HOLD/price-target/publish action buttons

Test company (deterministic):
- Ticker: IBTEST, Exchange: MOCK, Provider: mock

Notes:
- Tests mock all API calls via `page.route()` — work without any live backend
- Staging E2E is opt-in only — not triggered in standard CI
- Phase 19 (live EODHD staging smoke test) remains deferred

Run locally:
```bash
cd apps/web
npx playwright install --with-deps   # first time only
npm run test:e2e
```

---

## Phase 2: First Agent Workflow Foundation ✅

**Status: Complete**

Goal: Database foundation, company management endpoints, and a triggerable LangGraph workflow skeleton.

Deliverables:
- [x] Alembic configured with async migrations
- [x] Initial migration (`001`) — creates `companies`, `agent_runs`, `agent_steps`, `reports`
- [x] SQLAlchemy models: `Company`, `Report`, `AgentRun`, `AgentStep`
- [x] Company API endpoints: `POST /api/v1/companies`, `GET /api/v1/companies`, `GET /api/v1/companies/{id}`
- [x] Report model + service (draft creation)
- [x] Agent run + step service (create, complete, fail)
- [x] LangGraph `StateGraph` workflow skeleton (`company_analysis`)
- [x] Workflow trigger endpoint: `POST /api/v1/workflows/company-analysis/run`
- [x] Draft report saved to DB by workflow
- [x] Every workflow execution logged as `agent_run` + `agent_steps`
- [x] 27 passing tests (company endpoints, workflow trigger, service layer, graph structure)
- [x] ruff linting clean
- [ ] Azure OpenAI connection (deferred to Phase 3 — workflow uses placeholder logic)

> **Note:** Workflow nodes use deterministic placeholder output (`is_placeholder: true`, rating always WATCH).
> Wire real LLM calls in Phase 3 by replacing node bodies in `company_analysis.py`.

Skills used: `orchestrator`, `database-design`, `backend-fastapi`, `langgraph-agents`, `testing-qa`, `docs-maintainer`

---

## Phase 3: Research Storage & Citations

**Status: Not started**

Goal: Agent workflows can store research sources and link claims to citations.

Deliverables:
- [ ] Sources table and migration
- [ ] Source chunks table and migration
- [ ] Citations table and migration
- [ ] Azure Blob Storage integration (store PDF documents)
- [ ] Azure AI Search integration (chunk + embed sources)
- [ ] Source ingestion pipeline
- [ ] Citation Validator agent integrated into workflow
- [ ] Source Quality Agent integrated into workflow

Skills to use: `financial-data`, `langgraph-agents`, `database-design`, `azure-deployment`

---

## Phase 4: Full Council-of-Agents MVP

**Status: Not started**

Goal: Full research pipeline — from ticker to validated draft report.

Deliverables:
- [ ] Full Research Team (6 agents)
- [ ] Full Analysis Council (7 agents)
- [ ] Validation Team (Citation Validator + Fact Consistency Validator + Report Writer)
- [ ] Disagreement logging between council agents
- [ ] Admin report review screen
- [ ] Publish / reject actions
- [ ] Public report list and detail pages

Skills to use: `langgraph-agents`, `backend-fastapi`, `frontend-nextjs`, `investment-domain`, `testing-qa`

---

## Phase 5: Weekly Report Pipeline

**Status: Not started**

Goal: Scheduled automated weekly research workflow producing public reports.

Deliverables:
- [ ] Scheduled weekly workflow trigger (Azure Functions or Service Bus)
- [ ] Blog Writer and Email Writer agents
- [ ] Public report archive page
- [ ] Monthly / quarterly / yearly report types
- [ ] Email newsletter draft generation
- [ ] PDF-ready report structure
- [ ] Watchlist table and monitoring workflow

Skills to use: `langgraph-agents`, `frontend-nextjs`, `azure-deployment`

---

## Phase 6: Judge + Backtesting

**Status: Not started**

Goal: Platform evaluates its own recommendation quality and improves prompts.

Deliverables:
- [ ] Recommendation performance tracking (price history vs. entry price)
- [ ] Benchmark comparison
- [ ] Judge evaluation workflow
- [ ] Prompt versioning system (prompt_templates, prompt_versions tables)
- [ ] Admin review of judge improvement suggestions
- [ ] First real system improvement loop

Skills to use: `langgraph-agents`, `financial-data`, `backend-fastapi`, `investment-domain`

---

## Phase 7: Personalized Investor Assistant

**Status: Not started (Version 2)**

Goal: Users can create accounts, enter portfolios and receive personalized recommendations.

Deliverables:
- [ ] User accounts and authentication (Clerk integration)
- [ ] User preferences storage
- [ ] Manual portfolio input
- [ ] Portfolio Fit Agent
- [ ] Personalized recommendation filtering
- [ ] Private user dashboard
- [ ] Notification preferences and delivery

Skills to use: `backend-fastapi`, `frontend-nextjs`, `langgraph-agents`, `security-review`

---

## Out of Scope (All Versions)

- Broker account integration
- Automatic trade execution
- High-frequency or algorithmic trading
- Mobile app (not in current roadmap)
- Social or community features
- Guaranteed investment returns
