# InvestingBuddy Agentic Development Setup

## Purpose of This Document

This document defines how to set up an agentic coding workflow for developing the InvestingBuddy platform using Claude Code on a MacBook, GitHub as the code repository, and Azure deployment through GitHub pipelines.

The goal is to make the repository itself understandable and usable by coding agents. Instead of relying on one generic AI coding session, the repository should contain clear roles, skills, tools, rules, commands, and orchestration patterns so that Claude Code can act as an orchestrator and delegate work to specialized agent sessions.

The initial repository may contain only the technical specification and implementation plan. The first development task is therefore to build the agentic infrastructure around the repo before building the application itself.

---

# 1. Development Philosophy

InvestingBuddy should be developed as an agentic software project.

That means:

- The repository contains explicit instructions for AI coding agents.
- Each agent has a focused responsibility.
- The orchestrator agent decides which specialist agent should be used.
- The orchestrator gathers relevant context before delegating.
- Each specialist works on a narrow task.
- All work is done in small, reviewable, PR-sized changes.
- Documentation is updated together with implementation.
- Tests are created together with features.
- Architecture decisions are recorded.
- Financial claims, investment logic and agent behavior are auditable.

The coding agents should behave like a small software team:

```text
Orchestrator Agent
    ↓
Specialist Agents
    ↓
Reviewer Agent
    ↓
Tests / Docs / Commit
```

---

# 2. Recommended Tooling

## Local Development

Recommended local environment:

```text
macOS
Claude Code
VS Code or Cursor as editor
Git
GitHub CLI
Docker Desktop
Python 3.12+
Node.js LTS
Azure CLI
PostgreSQL local container
```

Recommended package managers:

```text
uv or pip for Python
npm or pnpm for frontend
```

Recommended cloud platform:

```text
Microsoft Azure
```

Recommended deployment:

```text
GitHub Actions → Azure App Service / Azure Container Apps
```

---

# 3. Main Development Agent Model

## The Orchestrator Agent

The orchestrator is the main Claude Code session that the developer talks to.

Responsibilities:

- Understand the entire project.
- Read relevant documentation.
- Identify which part of the codebase is affected.
- Select the correct specialist agent or skill.
- Prepare compact context for the specialist.
- Ask the specialist to produce a focused implementation.
- Review outputs.
- Run tests.
- Update documentation.
- Prepare commits.

The orchestrator should not blindly implement everything itself. It should route work to the most relevant specialist role.

---

## Specialist Agents

Specialist agents are reusable Claude Code skills, commands or sub-sessions.

Recommended specialist roles:

```text
Product Architect Agent
Backend FastAPI Agent
Frontend Next.js Agent
LangGraph Agent Engineer
Investment Domain Agent
Financial Data Integration Agent
Database Agent
Azure Deployment Agent
Testing / QA Agent
Security Agent
Documentation Agent
Reviewer Agent
```

---

# 4. Repository Structure for Agentic Development

Recommended structure:

```text
investingbuddy/
│
├── CLAUDE.md
├── README.md
├── TECH_SPEC.md
├── AGENTIC_DEVELOPMENT.md
├── docker-compose.yml
├── .env.example
│
├── .claude/
│   ├── skills/
│   │   ├── orchestrator/
│   │   │   └── SKILL.md
│   │   ├── product-architect/
│   │   │   └── SKILL.md
│   │   ├── backend-fastapi/
│   │   │   └── SKILL.md
│   │   ├── frontend-nextjs/
│   │   │   └── SKILL.md
│   │   ├── langgraph-agents/
│   │   │   └── SKILL.md
│   │   ├── database-design/
│   │   │   └── SKILL.md
│   │   ├── azure-deployment/
│   │   │   └── SKILL.md
│   │   ├── investment-domain/
│   │   │   └── SKILL.md
│   │   ├── financial-data/
│   │   │   └── SKILL.md
│   │   ├── testing-qa/
│   │   │   └── SKILL.md
│   │   ├── security-review/
│   │   │   └── SKILL.md
│   │   └── docs-maintainer/
│   │       └── SKILL.md
│   │
│   ├── commands/
│   │   ├── plan.md
│   │   ├── implement-feature.md
│   │   ├── review-pr.md
│   │   ├── create-migration.md
│   │   ├── add-agent-workflow.md
│   │   ├── add-api-endpoint.md
│   │   ├── generate-tests.md
│   │   ├── update-docs.md
│   │   └── deploy-check.md
│   │
│   └── settings.json
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── AGENTS.md
│   ├── API.md
│   ├── DATABASE.md
│   ├── DEPLOYMENT.md
│   ├── PROMPTING_GUIDE.md
│   ├── TESTING.md
│   ├── SECURITY.md
│   ├── DECISIONS.md
│   └── ROADMAP.md
│
├── apps/
│   ├── api/
│   └── web/
│
├── packages/
│   ├── shared-types/
│   └── prompts/
│
└── infra/
    ├── azure/
    ├── github-actions/
    └── terraform/
```

---

# 5. Root CLAUDE.md

Create this file first.

Path:

```text
CLAUDE.md
```

Purpose:

This is the main instruction file that Claude Code should read before doing any work.

Suggested content:

```md
# CLAUDE.md

## Project

This repository contains InvestingBuddy, an AI-driven investment research platform.

The platform generates medium-term investment research for public users in Version 1 and personalized portfolio-aware insights in Version 2.

Read these files before planning implementation:

- TECH_SPEC.md
- AGENTIC_DEVELOPMENT.md
- docs/ARCHITECTURE.md
- docs/AGENTS.md
- docs/DATABASE.md
- docs/API.md
- docs/DEPLOYMENT.md

## Core Development Rules

1. Work in small, PR-sized changes.
2. Never implement large unrelated changes in one task.
3. Always update documentation when architecture, API, database schema or agent workflow changes.
4. Always add or update tests when implementing backend logic.
5. Always create Alembic migrations for database schema changes.
6. Never hardcode secrets.
7. Never commit API keys, tokens, passwords or Azure credentials.
8. Never invent financial numbers.
9. Every investment claim must support citations.
10. Store rejected companies and failed analysis attempts.
11. Store every agent run for auditability.
12. Separate public reports from personalized reports.
13. Judge agent suggestions must not auto-deploy to production prompts without admin approval.
14. Do not build broker integration or automatic trading.
15. Do not present generated outputs as guaranteed financial advice.

## Preferred Stack

Backend:
- Python
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic
- LangGraph
- LangChain

Frontend:
- Next.js
- React
- TypeScript
- Tailwind

Cloud:
- Azure App Service
- Azure Database for PostgreSQL
- Azure Blob Storage
- Azure AI Search
- Azure OpenAI / Azure AI Foundry
- Azure Key Vault
- Azure Application Insights
- GitHub Actions

## Development Style

Before coding:
- inspect relevant files
- summarize the intended change
- identify affected modules
- propose a minimal implementation plan

During coding:
- keep changes focused
- prefer clear code over clever code
- use typed schemas
- add tests
- log important workflow states

After coding:
- run tests
- update docs
- summarize changed files
- describe manual verification steps
```

---

# 6. Orchestrator Agent Skill

Path:

```text
.claude/skills/orchestrator/SKILL.md
```

Suggested content:

```md
# Orchestrator Agent Skill

## Role

You are the main development orchestrator for the InvestingBuddy repository.

Your job is to understand user requests, gather relevant context, select the right specialist skills, and coordinate implementation.

## Responsibilities

- Read project documentation before acting.
- Identify the affected layer:
  - product
  - frontend
  - backend
  - database
  - agents
  - Azure infrastructure
  - testing
  - security
  - documentation
- Select the correct specialist agent or skill.
- Prepare a compact context package for the specialist.
- Keep implementation tasks small.
- Ensure tests and documentation are updated.
- Ask for review before risky architecture changes.

## Context Gathering Checklist

Before delegating, inspect:

- TECH_SPEC.md
- AGENTIC_DEVELOPMENT.md
- docs/ARCHITECTURE.md
- docs/AGENTS.md
- docs/DATABASE.md
- docs/API.md
- relevant source files
- relevant tests
- previous migrations
- current git diff

## Delegation Pattern

Use this format when delegating:

```text
Task:
Relevant files:
Relevant docs:
Constraints:
Expected output:
Definition of done:
```

## Decision Rules

Use Backend FastAPI Agent when:
- adding API endpoints
- modifying services
- adding models
- changing business logic

Use Frontend Next.js Agent when:
- adding pages
- modifying UI
- implementing dashboard views

Use LangGraph Agent Engineer when:
- creating or changing agent workflows
- defining agent state
- adding council-of-agents logic

Use Database Agent when:
- adding tables
- changing schema
- creating migrations

Use Azure Deployment Agent when:
- changing GitHub Actions
- adding Azure services
- configuring deployment

Use Testing Agent when:
- adding tests
- fixing failing tests
- improving test coverage

Use Security Agent when:
- handling auth
- secrets
- permissions
- user data
- prompt injection risks

Use Docs Maintainer when:
- architecture or behavior changes

## Output Expectations

Every orchestrated task should end with:

- summary of work
- files changed
- tests run
- risks
- next recommended step
```

---

# 7. Product Architect Agent Skill

Path:

```text
.claude/skills/product-architect/SKILL.md
```

```md
# Product Architect Agent Skill

## Role

You convert product requirements into technical implementation plans.

## Responsibilities

- Break features into milestones.
- Define user stories.
- Define acceptance criteria.
- Identify dependencies.
- Decide MVP vs later features.
- Update roadmap and architecture docs.

## Inputs

- user request
- TECH_SPEC.md
- ROADMAP.md
- current repository state

## Outputs

- implementation plan
- affected files
- data model impact
- API impact
- agent workflow impact
- frontend impact
- testing requirements

## Rules

- Keep scope small.
- Avoid building Version 2 features before Version 1 foundation.
- Prefer manual admin workflows before full automation.
- Prefer quality research over automatic publishing.
```

---

# 8. Backend FastAPI Agent Skill

Path:

```text
.claude/skills/backend-fastapi/SKILL.md
```

```md
# Backend FastAPI Agent Skill

## Role

You implement backend features in Python FastAPI.

## Responsibilities

- API routes
- Pydantic schemas
- SQLAlchemy models
- service layer
- dependency injection
- error handling
- tests
- OpenAPI compatibility

## Rules

- Keep routes thin.
- Put business logic in services.
- Use Pydantic schemas for request and response models.
- Use SQLAlchemy models for database entities.
- Do not bypass the service layer.
- Never hardcode secrets.
- Always add tests for new services.
- Use async only where the project architecture supports it consistently.

## Typical Files

```text
apps/api/app/main.py
apps/api/app/api/
apps/api/app/models/
apps/api/app/schemas/
apps/api/app/services/
apps/api/app/core/
apps/api/tests/
```

## Definition of Done

- endpoint works locally
- schema is typed
- service logic tested
- errors handled
- docs updated if API changed
```

---

# 9. Frontend Next.js Agent Skill

Path:

```text
.claude/skills/frontend-nextjs/SKILL.md
```

```md
# Frontend Next.js Agent Skill

## Role

You implement the web interface using Next.js, React and TypeScript.

## Responsibilities

- public report pages
- admin dashboard
- company pages
- theme pages
- user account pages
- reusable UI components
- API integration
- loading/error states

## Rules

- Use TypeScript.
- Prefer reusable components.
- Keep API calls in a dedicated client layer.
- Avoid hardcoded mock data unless clearly marked.
- Public and personalized content must be separated.
- Admin routes must not be exposed as public UI.

## Typical Files

```text
apps/web/app/
apps/web/components/
apps/web/lib/
apps/web/types/
```

## Definition of Done

- UI compiles
- route works
- API integration typed
- loading and error states handled
- responsive enough for desktop and mobile
```

---

# 10. LangGraph Agent Engineer Skill

Path:

```text
.claude/skills/langgraph-agents/SKILL.md
```

```md
# LangGraph Agent Engineer Skill

## Role

You design and implement LLM agent workflows using LangGraph and LangChain.

## Responsibilities

- define agent state
- implement graph nodes
- implement council-of-agents flows
- implement research team workflows
- implement analysis council workflows
- implement validation workflows
- implement judge workflows
- persist agent runs and steps
- structure outputs as JSON

## Core Agent Teams

### Research Team

- Market Scanner Agent
- Financial Data Agent
- Filings Agent
- News & Geopolitics Agent
- Industry Research Agent
- Source Quality Agent

### Analysis Council

- Bull Case Analyst
- Bear Case Analyst
- Valuation Analyst
- Risk Analyst
- Catalyst Analyst
- Portfolio Fit Analyst
- Investment Committee Chair

### Validation & Publishing Team

- Citation Validator
- Fact Consistency Validator
- Report Writer
- Blog Writer
- Email Writer
- PDF/Brochure Formatter

### Judge Team

- LLM-as-Judge Evaluator
- Backtesting Evaluator
- Prompt Improvement Recommender
- Source Quality Calibrator

## Rules

- Every workflow must have explicit state.
- Every agent output must use structured JSON where possible.
- Every workflow step must be logged.
- Do not allow agents to invent financial numbers.
- Do not allow unsupported claims into final reports.
- Keep prompts versioned.
- Judge suggestions must be reviewed by admin before deployment.

## Typical Files

```text
apps/api/app/agents/
apps/api/app/workflows/
apps/api/app/services/agent_run_service.py
packages/prompts/
docs/AGENTS.md
docs/PROMPTING_GUIDE.md
```

## Definition of Done

- workflow can run manually
- outputs are stored
- errors are logged
- agent steps are inspectable
- tests or smoke tests exist
```

---

# 11. Database Agent Skill

Path:

```text
.claude/skills/database-design/SKILL.md
```

```md
# Database Design Agent Skill

## Role

You design and maintain the PostgreSQL schema.

## Responsibilities

- SQLAlchemy models
- Alembic migrations
- indexes
- relationships
- data integrity
- enum design
- query performance
- auditability

## Core Tables

- users
- user_preferences
- portfolios
- portfolio_positions
- companies
- company_financial_snapshots
- sources
- source_chunks
- research_packages
- analyses
- recommendations
- reports
- report_recommendations
- citations
- agent_runs
- agent_steps
- judge_evaluations
- prompt_templates
- prompt_versions

## Rules

- Every schema change requires migration.
- Prefer explicit foreign keys.
- Store timestamps.
- Store source and citation records.
- Store agent run history.
- Do not delete research history unless explicitly required.
- Use status fields for workflow states.

## Definition of Done

- model added
- migration added
- migration tested
- relationships clear
- docs/DATABASE.md updated
```

---

# 12. Azure Deployment Agent Skill

Path:

```text
.claude/skills/azure-deployment/SKILL.md
```

```md
# Azure Deployment Agent Skill

## Role

You manage Azure deployment and GitHub Actions pipelines.

## Responsibilities

- Azure App Service configuration
- Azure PostgreSQL setup
- Azure Blob Storage setup
- Azure AI Search setup
- Azure OpenAI configuration
- Azure Key Vault integration
- Application Insights
- GitHub Actions workflows
- staging and production deployment

## Rules

- Never commit secrets.
- Use GitHub repository secrets.
- Prefer managed identity where possible.
- Keep staging and production separate.
- Deployment pipeline must run tests before deploy.
- Document required Azure resources.

## Typical Files

```text
.github/workflows/
infra/azure/
infra/terraform/
docs/DEPLOYMENT.md
.env.example
```

## Definition of Done

- pipeline validates
- environment variables documented
- secrets listed but not exposed
- deployment steps documented
```

---

# 13. Investment Domain Agent Skill

Path:

```text
.claude/skills/investment-domain/SKILL.md
```

```md
# Investment Domain Agent Skill

## Role

You protect investment-analysis quality and domain logic.

## Responsibilities

- define investment criteria
- define scoring matrices
- define rating logic
- define risk checklists
- define valuation checklist
- define report structure
- prevent unsupported financial claims

## Investment Horizon

Primary:

- 6 months to 3 years

Secondary:

- up to 6 years for watchlist and strategic monitoring

## Initial Focus

European small/mid-cap companies in real-asset sectors:

- energy transition
- grid
- electrification
- materials
- commodities
- rare earths
- defense
- infrastructure
- reshoring
- industrial automation

## Rules

- No financial number without source.
- No recommendation without risk section.
- No BUY rating without valuation support.
- No SELL rating without clear thesis-break or risk argument.
- Distinguish facts, assumptions and opinions.
- Store rejected companies and reasons.
- Prefer primary sources over secondary sources.

## Rating System

Allowed ratings:

```text
BUY
WATCH
HOLD
SELL
REJECT
```

## Definition of Done

- investment logic is explicit
- risks are included
- citations required
- assumptions are marked
- report is understandable to investors
```

---

# 14. Financial Data Agent Skill

Path:

```text
.claude/skills/financial-data/SKILL.md
```

```md
# Financial Data Integration Agent Skill

## Role

You implement integrations for financial, market and source data.

## Responsibilities

- OpenBB integration
- price history
- company fundamentals
- filings
- financial metrics
- news ingestion
- source normalization
- citation metadata
- data freshness checks

## Rules

- Store source URL and retrieval timestamp.
- Store currency and reporting period.
- Handle missing data explicitly.
- Do not silently mix currencies.
- Do not silently mix fiscal periods.
- Prefer official filings where available.
- Cache expensive API calls.

## Typical Files

```text
apps/api/app/integrations/
apps/api/app/services/source_service.py
apps/api/app/services/company_service.py
apps/api/app/services/valuation_service.py
```

## Definition of Done

- integration has typed output
- source metadata is stored
- errors handled
- tests or mocks created
```

---

# 15. Testing / QA Agent Skill

Path:

```text
.claude/skills/testing-qa/SKILL.md
```

```md
# Testing / QA Agent Skill

## Role

You ensure the platform is reliable.

## Responsibilities

- unit tests
- integration tests
- API tests
- workflow smoke tests
- frontend tests
- CI test commands
- regression checks

## Backend Testing

Use:

```text
pytest
httpx
pytest-asyncio if needed
```

## Frontend Testing

Use:

```text
TypeScript checks
ESLint
Playwright later
```

## Rules

- Test service logic.
- Test API endpoints.
- Mock external APIs.
- Do not require real Azure services for local unit tests.
- Add smoke tests for LangGraph workflows.
- CI must fail on broken tests.

## Definition of Done

- tests added
- tests pass
- edge cases considered
- CI command documented
```

---

# 16. Security Review Agent Skill

Path:

```text
.claude/skills/security-review/SKILL.md
```

```md
# Security Review Agent Skill

## Role

You review security, secrets and data protection risks.

## Responsibilities

- secrets handling
- auth checks
- role permissions
- admin route protection
- user data separation
- prompt injection risks
- source ingestion safety
- dependency risks

## Rules

- Never expose secrets.
- Never log API keys.
- Admin endpoints require admin authorization.
- Personalized reports must be private.
- Public reports must not leak private user data.
- Treat external documents as untrusted input.
- Protect agent prompts from prompt injection through retrieved documents.

## Definition of Done

- security risks listed
- secrets safe
- auth boundaries clear
- risky changes flagged
```

---

# 17. Documentation Agent Skill

Path:

```text
.claude/skills/docs-maintainer/SKILL.md
```

```md
# Documentation Maintainer Skill

## Role

You keep the repository documentation accurate.

## Responsibilities

- README.md
- TECH_SPEC.md
- AGENTIC_DEVELOPMENT.md
- docs/ARCHITECTURE.md
- docs/AGENTS.md
- docs/API.md
- docs/DATABASE.md
- docs/DEPLOYMENT.md
- docs/DECISIONS.md

## Rules

- Update docs when implementation changes.
- Keep docs practical and developer-focused.
- Do not let docs drift from code.
- Record important architecture decisions.
- Include setup commands where useful.

## Definition of Done

- relevant docs updated
- old instructions removed or corrected
- next steps clear
```

---

# 18. Claude Commands

Commands are reusable task templates.

Recommended directory:

```text
.claude/commands/
```

---

## plan.md

Path:

```text
.claude/commands/plan.md
```

```md
# Plan Command

You are planning a development task.

Read:
- CLAUDE.md
- TECH_SPEC.md
- AGENTIC_DEVELOPMENT.md
- relevant docs
- relevant source files

Return:

1. Goal
2. Affected modules
3. Relevant existing files
4. Implementation steps
5. Database impact
6. API impact
7. Agent workflow impact
8. Frontend impact
9. Tests required
10. Documentation updates required
11. Risks
12. Definition of done

Do not code yet unless explicitly asked.
```

---

## implement-feature.md

Path:

```text
.claude/commands/implement-feature.md
```

```md
# Implement Feature Command

Implement one focused feature.

Rules:
- Keep changes small.
- Inspect existing code first.
- Follow CLAUDE.md.
- Add tests.
- Update docs if needed.
- Do not introduce unrelated refactors.
- Do not hardcode secrets.

Process:
1. Restate feature.
2. Inspect relevant files.
3. Implement backend/frontend/agent/database changes.
4. Add or update tests.
5. Run available checks.
6. Summarize changed files.
7. Provide manual verification steps.
```

---

## review-pr.md

Path:

```text
.claude/commands/review-pr.md
```

```md
# Review PR Command

Review the current git diff.

Check:
- correctness
- scope creep
- architecture consistency
- tests
- docs
- security
- financial claim citation rules
- database migration correctness
- frontend/backend contract consistency
- agent workflow logging

Output:
1. Summary
2. Blocking issues
3. Non-blocking suggestions
4. Missing tests
5. Missing docs
6. Security concerns
7. Approval status
```

---

## create-migration.md

Path:

```text
.claude/commands/create-migration.md
```

```md
# Create Migration Command

Create or update database models and Alembic migration.

Steps:
1. Inspect current models.
2. Inspect current migrations.
3. Add model changes.
4. Generate migration.
5. Review migration manually.
6. Ensure downgrade is reasonable.
7. Update docs/DATABASE.md.
8. Add tests if needed.

Rules:
- Never modify production data destructively without explicit approval.
- Prefer additive migrations.
- Use explicit indexes for frequently queried fields.
```

---

## add-agent-workflow.md

Path:

```text
.claude/commands/add-agent-workflow.md
```

```md
# Add Agent Workflow Command

Create or modify a LangGraph workflow.

Steps:
1. Define workflow purpose.
2. Define state schema.
3. Define agents/nodes.
4. Define edges and branching.
5. Define structured outputs.
6. Add persistence of agent_run and agent_steps.
7. Add error handling.
8. Add smoke test.
9. Update docs/AGENTS.md and docs/PROMPTING_GUIDE.md.

Rules:
- Every agent output must be inspectable.
- Every financial claim must require citations.
- Do not let validation agents be skipped.
```

---

## add-api-endpoint.md

Path:

```text
.claude/commands/add-api-endpoint.md
```

```md
# Add API Endpoint Command

Add one backend API endpoint.

Steps:
1. Define route.
2. Define request schema.
3. Define response schema.
4. Add service method.
5. Add route handler.
6. Add tests.
7. Update docs/API.md.

Rules:
- Keep route thin.
- Use service layer.
- Validate inputs.
- Return typed response.
```

---

## generate-tests.md

Path:

```text
.claude/commands/generate-tests.md
```

```md
# Generate Tests Command

Add tests for the current feature or module.

Check:
- service logic
- API behavior
- database interactions
- error cases
- permission checks
- agent workflow smoke tests

Rules:
- Mock external APIs.
- Do not require real Azure credentials.
- Keep tests deterministic.
```

---

## update-docs.md

Path:

```text
.claude/commands/update-docs.md
```

```md
# Update Docs Command

Update documentation after implementation changes.

Check:
- README.md
- TECH_SPEC.md
- AGENTIC_DEVELOPMENT.md
- docs/ARCHITECTURE.md
- docs/API.md
- docs/DATABASE.md
- docs/AGENTS.md
- docs/DEPLOYMENT.md
- docs/DECISIONS.md

Rules:
- Do not duplicate outdated instructions.
- Prefer concise practical documentation.
- Record important architecture decisions.
```

---

## deploy-check.md

Path:

```text
.claude/commands/deploy-check.md
```

```md
# Deploy Check Command

Check whether the repo is ready to deploy.

Verify:
- tests pass
- env vars documented
- GitHub Actions valid
- Azure resources documented
- no secrets committed
- migrations ready
- build commands work
- health endpoint exists
- staging deployment path defined

Output:
1. Ready / Not ready
2. Blocking issues
3. Required secrets
4. Required Azure resources
5. Manual deployment steps
```

---

# 19. MCP Tools and External Tools

MCP tools are optional but useful. Add them gradually.

## Recommended MCP Tools

### GitHub MCP

Use for:

- issues
- PRs
- repo context
- commits
- reviewing branches

### Filesystem MCP

Use for:

- repo navigation
- reading files
- writing files

### PostgreSQL MCP

Use for:

- inspecting local database schema
- checking migrations
- validating tables

### Azure MCP / Azure CLI Access

Use for:

- checking App Service
- checking logs
- checking resource groups
- checking environment settings

### Browser / Web Search MCP

Use for:

- checking current Azure docs
- checking library changes
- verifying deployment details

### Playwright MCP

Later use for:

- frontend testing
- admin UI testing
- public report page testing

---

# 20. Tool Usage Rules

## General

- Use tools only when they improve accuracy.
- Inspect files before editing.
- Prefer existing patterns in the repo.
- Do not create duplicate abstractions.
- Do not rewrite unrelated code.

## Git

Before major work:

```bash
git status
git branch
```

After work:

```bash
git diff
git status
```

## Python

Backend checks:

```bash
pytest
ruff check .
mypy .
```

Add these only once configured.

## Frontend

Frontend checks:

```bash
npm run lint
npm run typecheck
npm run build
```

## Azure

Never hardcode Azure credentials.

Use:

```bash
az login
az account show
```

Use GitHub secrets for deployment credentials.

---

# 21. Agent-Orchestrated Workflow

The orchestrator should use this standard workflow for every task.

```text
1. Understand request.
2. Read CLAUDE.md.
3. Read relevant project docs.
4. Inspect affected source files.
5. Identify specialist skill.
6. Prepare context package.
7. Delegate or use relevant skill.
8. Implement focused change.
9. Run tests/checks.
10. Review diff.
11. Update docs.
12. Summarize result.
```

---

# 22. Context Package Format

When passing work to a sub-agent or specialist skill, use:

```text
Task:
Build/modify ...

Relevant docs:
- ...
- ...

Relevant files:
- ...
- ...

Current constraints:
- ...
- ...

Expected output:
- ...

Definition of done:
- ...
```

Example:

```text
Task:
Add the first Company model and CRUD endpoints.

Relevant docs:
- TECH_SPEC.md
- docs/DATABASE.md
- docs/API.md

Relevant files:
- apps/api/app/main.py
- apps/api/app/db/session.py
- apps/api/app/models/

Current constraints:
- Use SQLAlchemy.
- Add Alembic migration.
- Keep API routes thin.
- Add tests.

Expected output:
- Company SQLAlchemy model
- Company Pydantic schemas
- Company service
- Admin API endpoints
- Tests
- Docs update

Definition of done:
- pytest passes
- migration created
- endpoint documented
```

---

# 23. First Milestone: Build the Agentic Repo Infrastructure

Before building the actual platform, create:

```text
CLAUDE.md
AGENTIC_DEVELOPMENT.md
.claude/skills/*
.claude/commands/*
docs/ARCHITECTURE.md
docs/AGENTS.md
docs/DATABASE.md
docs/API.md
docs/DEPLOYMENT.md
docs/DECISIONS.md
docs/ROADMAP.md
```

Initial docs can be short, but they must exist.

---

# 24. Second Milestone: Build the Application Skeleton

After agentic infrastructure exists:

```text
apps/api FastAPI skeleton
apps/web Next.js skeleton
local PostgreSQL docker-compose
.env.example
health endpoint
basic CI workflow
```

---

# 25. Third Milestone: Build First Real Feature

First useful platform feature:

```text
Admin manually enters a ticker
        ↓
Backend stores company
        ↓
Admin triggers simple analysis workflow
        ↓
LangGraph creates draft analysis
        ↓
Draft report saved in DB
        ↓
Admin can view report
```

This avoids overcomplicating the MVP with fully automated market scanning too early.

---

# 26. Prompting Rules for Claude Code

When asking Claude Code to work, use precise prompts.

Good example:

```text
Read CLAUDE.md and AGENTIC_DEVELOPMENT.md. Use the orchestrator skill.
Plan the first implementation milestone: create the repo skeleton with FastAPI, Next.js, Docker PostgreSQL, .env.example and docs placeholders.
Do not code yet. Return a step-by-step plan and affected files.
```

Then:

```text
Implement the first milestone from the approved plan. Keep it minimal. Add health endpoint, local docker-compose, and README setup instructions. Run available checks.
```

For agents:

```text
Use the LangGraph Agent Engineer skill. Create the first minimal workflow skeleton for company analysis. It should not call real financial APIs yet. It should accept a ticker, create placeholder structured output, persist agent_run and agent_step records, and save a draft report.
```

For review:

```text
Use the Reviewer Agent. Review the current git diff against CLAUDE.md, TECH_SPEC.md and AGENTIC_DEVELOPMENT.md. Identify blocking issues before commit.
```

---

# 27. GitHub Workflow

Recommended branch strategy:

```text
main
develop
feature/*
```

For early solo development, simpler:

```text
main
feature/*
```

Rules:

- Never commit directly to main once deployment is active.
- Use PRs even when working alone.
- Let GitHub Actions run tests before merge.
- Deploy main to staging first.
- Promote to production manually later.

---

# 28. GitHub Actions Plan

Initial workflows:

```text
.github/workflows/api-ci.yml
.github/workflows/web-ci.yml
.github/workflows/deploy-api-staging.yml
.github/workflows/deploy-web-staging.yml
```

API CI should run:

```text
install dependencies
lint
tests
```

Web CI should run:

```text
npm install
npm run lint
npm run typecheck
npm run build
```

Deployment should happen only after CI passes.

---

# 29. Azure Deployment Plan

## MVP Azure Resources

```text
Resource Group
Azure App Service for API
Azure App Service or Static Web App for frontend
Azure Database for PostgreSQL
Azure Blob Storage
Azure OpenAI
Azure AI Search
Azure Key Vault
Application Insights
```

## Later Resources

```text
Azure Service Bus
Azure Functions
Azure Container Apps Jobs
Azure Front Door
Azure CDN
```

---

# 30. Secrets Strategy

Local development:

```text
.env
```

Repository:

```text
.env.example
```

GitHub:

```text
GitHub Actions Secrets
```

Azure:

```text
Azure Key Vault
App Service configuration
Managed Identity where possible
```

Never commit:

```text
.env
API keys
Azure credentials
database passwords
OpenAI keys
financial data API keys
```

---

# 31. Coding Agent Safety Rules

Claude Code agents must follow these rules:

- Do not delete files unless explicitly instructed.
- Do not change deployment configuration without review.
- Do not modify production prompts without versioning.
- Do not create uncited investment claims.
- Do not invent financial metrics.
- Do not implement automatic trading.
- Do not connect broker accounts.
- Do not store private user portfolio data in public report tables.
- Do not expose admin APIs publicly.
- Do not commit secrets.
- Do not merge large unrelated refactors into feature work.

---

# 32. Definition of Done for Agentic Development Infrastructure

This setup is complete when:

- CLAUDE.md exists.
- AGENTIC_DEVELOPMENT.md exists.
- All initial Claude skills exist.
- All initial Claude commands exist.
- docs folder has architecture, agents, database, API, deployment and roadmap placeholders.
- Claude Code can read the repo and understand how to work.
- Orchestrator skill can route tasks to specialist skills.
- First feature can be planned using the command templates.

---

# 33. Recommended First Prompt to Claude Code

Use this inside Claude Code after creating the repository and adding `TECH_SPEC.md` plus this file:

```text
Read CLAUDE.md, TECH_SPEC.md and AGENTIC_DEVELOPMENT.md.

Act as the Orchestrator Agent.

Your task is to prepare the repository for agentic development. Create the .claude/skills and .claude/commands structure described in AGENTIC_DEVELOPMENT.md. Also create placeholder docs in docs/.

Do not build the FastAPI or Next.js app yet.

After creating files, summarize:
1. what was created,
2. how future coding tasks should be routed,
3. what the next implementation milestone should be.
```

---

# 34. Recommended Second Prompt to Claude Code

```text
Use the Orchestrator Agent and Product Architect Agent.

Plan the first application skeleton milestone:
- FastAPI backend in apps/api
- Next.js frontend in apps/web
- PostgreSQL docker-compose
- .env.example
- backend health endpoint
- frontend homepage
- basic README setup instructions

Do not code yet. Return the implementation plan, affected files, test plan and definition of done.
```

---

# 35. Recommended Third Prompt to Claude Code

```text
Implement the first application skeleton milestone from the approved plan.

Use the Backend FastAPI Agent, Frontend Next.js Agent, Database Agent, Testing Agent and Docs Maintainer where appropriate.

Keep the implementation minimal.
Do not add investment analysis logic yet.
Do not add Azure deployment yet.
Run available checks and summarize results.
```

---

# 36. Long-Term Agentic Development Vision

Eventually, the development system itself should mirror the InvestingBuddy product architecture.

Development side:

```text
Orchestrator Agent
Researches repo context
Delegates to coding specialists
Reviews output
Improves development process
```

Product side:

```text
Investment Research Orchestrator
Delegates to research agents
Delegates to analysis council
Delegates to validation team
Uses judge for quality improvement
```

This makes the project internally consistent: the software is built agentically, and the product itself is an agentic research platform.

---

# 37. Key Principle

The goal is not to make Claude Code write a lot of code quickly.

The goal is to make Claude Code work like a disciplined engineering team.

That requires:

- small tasks
- clear roles
- persistent docs
- structured context
- tests
- reviews
- deployment discipline
- auditability

This is especially important because InvestingBuddy combines software engineering, LLM workflows, financial analysis, user-facing reports and future personalized investment recommendations.
