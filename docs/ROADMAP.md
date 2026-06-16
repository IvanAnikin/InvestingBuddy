# Roadmap

## Current Phase: Phase 0 — Agentic Repository Infrastructure

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

## Phase 1: Application Skeleton

**Status: Not started**

Goal: A working, deployable skeleton of the full stack with no business logic yet.

Deliverables:
- [ ] `apps/api/` — FastAPI skeleton with health endpoint (`GET /health`)
- [ ] `apps/api/app/core/` — config, logging, exceptions
- [ ] `apps/api/app/db/` — SQLAlchemy session, base model
- [ ] `apps/api/alembic/` — Alembic setup (no migrations yet)
- [ ] `apps/web/` — Next.js App Router skeleton with homepage
- [ ] `docker-compose.yml` — local PostgreSQL container
- [ ] `.env.example` — all required environment variable names
- [ ] `.github/workflows/api-ci.yml` — backend CI (lint, type check, pytest)
- [ ] `.github/workflows/web-ci.yml` — frontend CI (typecheck, lint, build)
- [ ] `README.md` update — local setup instructions

Skills to use: `backend-fastapi`, `frontend-nextjs`, `database-design`, `azure-deployment`, `testing-qa`

---

## Phase 2: First Agent Workflow

**Status: Not started**

Goal: Admin can manually trigger a company analysis workflow.

Deliverables:
- [ ] Company SQLAlchemy model and Alembic migration
- [ ] Company CRUD API endpoints (`POST /api/admin/companies`, `GET /api/admin/companies`)
- [ ] LangGraph installed and configured
- [ ] Azure OpenAI connection working
- [ ] First `company_deep_dive` workflow skeleton (3 nodes: Market Scanner, Company Analyst, Investment Committee)
- [ ] Agent run logging (agent_runs, agent_steps tables and migration)
- [ ] Draft analysis saved to database
- [ ] Admin endpoint to trigger workflow (`POST /api/admin/workflows/company-deep-dive/run`)
- [ ] Admin endpoint to view agent run (`GET /api/admin/agent-runs/{id}`)
- [ ] Smoke test for workflow with mocked LLM

Skills to use: `langgraph-agents`, `backend-fastapi`, `database-design`, `testing-qa`

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
