# CLAUDE.md

## Project

InvestingBuddy is an AI-driven investment research platform that generates medium-term investment research for public users (Version 1) and personalized portfolio-aware insights (Version 2).

The platform uses a council-of-agents approach: multiple specialized agents research, analyze, validate and publish investment opportunities. Every claim must be backed by citations. Human admin approves every publication.

Read these files before planning significant changes:

- `Implementation_docs/INVESTINGBUDDY_TECH_SPEC.md`
- `AGENTIC_DEVELOPMENT.md`
- `docs/ARCHITECTURE.md`
- `docs/AGENTS.md`
- `docs/DATABASE.md`
- `docs/API.md`
- `docs/DEPLOYMENT.md`

---

## Orchestrator Role

When acting as the orchestrator, follow `.claude/skills/orchestrator/SKILL.md`.

Route work to specialist skills under `.claude/skills/` based on which layer of the system is affected. Prepare a compact context package before delegating.

---

## Core Development Rules

1. Work in small, PR-sized changes. Never bundle unrelated changes.
2. Update documentation together with implementation — code and docs must never diverge.
3. Add or update tests whenever backend logic changes.
4. Create an Alembic migration for every database schema change.
5. Never hardcode secrets, API keys, passwords or Azure credentials.
6. Never invent financial numbers. Every financial claim requires a source, date, currency and retrieval timestamp.
7. Every investment recommendation must include risks, catalysts, confidence score and citations.
8. Store rejected companies and failed analyses — they are valuable learning data.
9. Log every agent run and every agent step for auditability.
10. Separate public research from personalized research — never leak private user data into public tables.
11. Judge agent suggestions must not auto-deploy to production prompts — admin must approve.
12. Do not build broker integration or automatic trade execution.
13. Do not present platform output as guaranteed financial advice.
14. Admin routes must never be exposed as public endpoints.
15. Do not delete research history unless explicitly required.

---

## Preferred Stack

**Backend**
- Python 3.12+
- FastAPI
- SQLAlchemy (async)
- Alembic
- Pydantic v2
- LangGraph
- LangChain

**Frontend**
- Next.js (App Router)
- React
- TypeScript
- Tailwind CSS

**Agent LLM Runtime**
- Azure OpenAI
- Azure AI Foundry

**Cloud**
- Azure App Service (API + Web)
- Azure Database for PostgreSQL
- Azure Blob Storage
- Azure AI Search
- Azure Key Vault
- Azure Application Insights
- Azure Service Bus (later)
- Azure Functions (background jobs)
- GitHub Actions (CI/CD)

**Auth**
- Clerk (MVP) → Microsoft Entra External ID (later)

---

## Repository Structure

```
investingbuddy/
├── CLAUDE.md
├── AGENTIC_DEVELOPMENT.md
├── Readme.md
├── docker-compose.yml          # local PostgreSQL
├── .env.example
├── Implementation_docs/
│   ├── INVESTINGBUDDY_TECH_SPEC.md
│   └── AGENTIC_DEVELOPMENT_SETUP.md
├── .claude/
│   ├── skills/                 # specialist agent skill definitions
│   └── commands/               # reusable task templates
├── docs/
│   ├── ARCHITECTURE.md
│   ├── AGENTS.md
│   ├── API.md
│   ├── DATABASE.md
│   ├── DEPLOYMENT.md
│   ├── DECISIONS.md
│   ├── ROADMAP.md
│   ├── TESTING.md
│   ├── SECURITY.md
│   └── PROMPTING_GUIDE.md
├── apps/
│   ├── api/                    # FastAPI backend
│   └── web/                    # Next.js frontend
├── packages/
│   ├── shared-types/           # shared TypeScript types
│   └── prompts/                # versioned prompt templates
└── infra/
    ├── azure/
    ├── github-actions/
    └── terraform/
```

---

## Development Style

**Before coding:**
- Read CLAUDE.md and relevant docs
- Inspect existing files in the affected area
- Summarize the intended change
- Identify all affected modules
- Propose a minimal implementation plan

**During coding:**
- Keep changes focused and small
- Prefer clear code over clever code
- Use typed schemas (Pydantic on backend, TypeScript on frontend)
- Add tests alongside features
- Log important workflow states

**After coding:**
- Run available checks (`pytest`, `ruff`, `mypy`, `npm run typecheck`)
- Update documentation
- Summarize changed files
- Describe manual verification steps

---

## Available Skills

| Skill | Path | Use When |
|---|---|---|
| Orchestrator | `.claude/skills/orchestrator/` | Routing and coordination |
| Product Architect | `.claude/skills/product-architect/` | Feature planning, roadmap |
| Backend FastAPI | `.claude/skills/backend-fastapi/` | API routes, services, models |
| Frontend Next.js | `.claude/skills/frontend-nextjs/` | Pages, components, UI |
| LangGraph Agents | `.claude/skills/langgraph-agents/` | Agent workflows, councils |
| Database Design | `.claude/skills/database-design/` | Schema, migrations |
| Azure Deployment | `.claude/skills/azure-deployment/` | CI/CD, Azure infra |
| Investment Domain | `.claude/skills/investment-domain/` | Investment logic, ratings |
| Financial Data | `.claude/skills/financial-data/` | Data integrations, sources |
| Testing / QA | `.claude/skills/testing-qa/` | Tests, CI checks |
| Security Review | `.claude/skills/security-review/` | Auth, secrets, prompt injection |
| Docs Maintainer | `.claude/skills/docs-maintainer/` | Documentation updates |

## Available Commands

| Command | Path | Use For |
|---|---|---|
| plan | `.claude/commands/plan.md` | Planning before coding |
| implement-feature | `.claude/commands/implement-feature.md` | Implementing a focused feature |
| review-pr | `.claude/commands/review-pr.md` | Reviewing a diff before commit |
| create-migration | `.claude/commands/create-migration.md` | DB schema changes |
| add-agent-workflow | `.claude/commands/add-agent-workflow.md` | New LangGraph workflows |
| add-api-endpoint | `.claude/commands/add-api-endpoint.md` | New API routes |
| generate-tests | `.claude/commands/generate-tests.md` | Adding test coverage |
| update-docs | `.claude/commands/update-docs.md` | Keeping docs current |
| deploy-check | `.claude/commands/deploy-check.md` | Pre-deploy validation |
