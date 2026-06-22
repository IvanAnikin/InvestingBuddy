# Architecture

## Status: Phase 4.5 — Live Free Data Provider Integration

---

## High-Level System Architecture

```
Browser / User
    ↓
Next.js Frontend (Azure App Service / Static Web App)
    ↓
FastAPI Backend (Azure App Service)
    ↓
┌──────────────────────────────────────────────┐
│ Agent Orchestration Layer (LangGraph)         │
│  Research Team → Analysis Council → Validation│
└──────────────────────────────────────────────┘
    ↓                    ↓                    ↓
Azure OpenAI      Azure AI Search      Azure Blob Storage
(LLM runtime)     (RAG / embeddings)   (documents / PDFs)
    ↓
Azure Database for PostgreSQL
(structured research data, recommendations, audit logs)
    ↓
Azure Application Insights
(monitoring, alerting)
```

---

## Layers

### Frontend (`apps/web/`)
- Next.js 16, React 19, TypeScript, Tailwind CSS v4, App Router
- Public report pages, admin dashboard, user account (V2)
- Communicates with backend via REST API
- Status: **skeleton created in Phase 1**

### Backend (`apps/api/`)
- FastAPI, SQLAlchemy async, Pydantic v2, Alembic
- All business logic, database operations, agent orchestration triggers
- Status: **company endpoints + workflow trigger live in Phase 2**

### Agent Layer (`apps/api/app/agents/`, `apps/api/app/workflows/`)
- LangGraph `StateGraph` workflows
- Four agent teams: Research, Analysis Council, Validation, Judge
- All runs logged to `agent_runs` and `agent_steps` tables
- Status: **`company_analysis` skeleton implemented in Phase 2 (placeholder nodes, no LLM yet)**

### Database
- Local: PostgreSQL 16 via Docker Compose
- Production: Azure Database for PostgreSQL Flexible Server
- Status: **migration 001 (companies, agent_runs, agent_steps, reports) + migration 002 (sources, citations)**

### Vector Search
- Azure AI Search
- Document chunks from filings, news, industry reports
- Used for RAG in agent workflows
- Status: **not yet implemented — Phase 3+**

### File Storage
- Azure Blob Storage
- PDFs, downloaded documents, exported reports
- Status: **not yet implemented — Phase 3+**

### Background Jobs
- Azure Functions (scheduled weekly/monthly workflows)
- Azure Service Bus (job queue, later)
- Status: **not yet implemented — Phase 5+**

---

## Monorepo Structure

```
investingbuddy/
├── apps/
│   ├── api/        FastAPI backend
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── core/           config, security, logging
│   │   │   ├── api/
│   │   │   │   └── v1/
│   │   │   │       ├── health.py
│   │   │   │       ├── companies.py
│   │   │   │       ├── workflows.py
│   │   │   │       ├── sources.py      Phase 3
│   │   │   │       └── citations.py    Phase 3
│   │   │   ├── models/         SQLAlchemy ORM: Company, Report, AgentRun, AgentStep, Source, Citation
│   │   │   ├── schemas/        Pydantic: company, report, agent, source (incl. citations)
│   │   │   ├── integrations/   financial_data_provider.py (ABC + schemas + SourceRecordAttrs), financial_data_service.py, providers/ (mock, eodhd, sec_edgar[live], stooq[live], gleif[live], openbb[placeholder])
│   │   │   ├── services/       company_service, report_service, agent_run_service, source_service, citation_service, report_validation_service
│   │   │   ├── agents/
│   │   │   │   ├── base.py     CompanyAnalysisState TypedDict
│   │   │   │   └── validation/
│   │   │   │       └── citation_validator.py   Phase 3 skeleton
│   │   │   ├── workflows/
│   │   │   │   └── company_analysis.py
│   │   │   └── db/             session, base
│   │   ├── alembic/
│   │   │   └── versions/
│   │   │       ├── 001_add_initial_tables.py
│   │   │       └── 002_add_sources_and_citations.py   Phase 3
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── web/        Next.js frontend
│       └── src/app/
├── packages/
│   ├── shared-types/   TypeScript types shared between frontend and backend
│   ├── prompts/        Versioned prompt templates
│   └── research-contracts/
│       └── real_asset_equity/
│           └── v1/     JSON Schema + source taxonomy + provider mapping + example (Phase 3.5)
├── infra/
│   ├── azure/          ARM / Bicep infrastructure definitions
│   ├── github-actions/ Reusable action fragments
│   └── terraform/      Terraform modules (later)
├── .github/
│   └── workflows/      GitHub Actions CI (api-ci.yml, web-ci.yml)
├── docker-compose.yml
├── .env.example
└── docs/
```

---

## API Versioning

All backend routes are versioned under `/api/v1/`.
The health endpoint lives at `/health` (unversioned, used by load balancers).

---

## Workflow Execution Pattern

```
API endpoint (POST /api/v1/workflows/company-analysis/run)
    ↓
run_company_analysis(db, company_id)
    ↓
LangGraph StateGraph.ainvoke(initial_state)
    ↓
  node_initialize          → creates agent_run record
  node_analyze_company     → creates agent_step, produces analysis JSON
  node_save_report         → saves draft to reports table
  node_finalize            → marks agent_run completed
    ↓
WorkflowRunResponse (agent_run_id, draft_report_id, status, summary)
```

All errors are caught, logged to `agent_runs.error_message`, and returned as HTTP 422.

---

## Phase History

| Phase | Status | What Changed |
|---|---|---|
| Phase 0 | ✅ Complete | Agentic dev infrastructure: skills, commands, docs scaffolding |
| Phase 1 | ✅ Complete | FastAPI skeleton, Next.js skeleton, Docker Compose, GitHub Actions CI |
| Phase 2 | ✅ Complete | DB foundation (Alembic + 4 tables), company endpoints, LangGraph workflow skeleton |
| Phase 3 | ✅ Complete | Source + Citation models, migration 002, source/citation services + API, CitationValidator skeleton, workflow creates placeholder source + citation |
| Phase 3.5 | ✅ Complete | Real-asset equity report schema contract, source taxonomy, EODHD provider mapping, offline schema validation utility, report validation tests, DATA_SOURCES.md |
| Phase 4 | ✅ Complete | Financial data provider abstraction, MockProvider, provider skeletons (SecEdgar/Stooq/OpenBB/Gleif/EODHD), FinancialDataService registry, smoke-test API endpoints |
| Phase 4.5 | ✅ Complete | Live free provider implementations: StooqProvider (OHLCV CSV), GleifProvider (LEI lookup), SecEdgarProvider (CIK submissions); SourceRecordAttrs helper; diagnostic API endpoints; fixture-based offline tests; integration test harness |

---

## What Is Not Yet Implemented

- Authentication (Clerk) — Phase 7
- Azure OpenAI LLM calls in workflow nodes — Phase 5
- Live EODHD calls (paid, requires `EODHD_API_KEY`) — deferred
- Ticker → CIK resolution for SecEdgarProvider — Phase 5
- SEC EDGAR XBRL fundamentals (`get_fundamentals`) — Phase 5
- Azure AI Search (embeddings, RAG) — Phase 5+
- Azure Blob Storage (PDF documents) — Phase 5+
- Full council-of-agents (all agent teams) — Phase 5
- OpenBB integration (evaluation pending) — Phase 5/6
- Scheduled background jobs — Phase 6
- Judge / backtesting — Phase 7
- Personalized recommendations — Phase 8
