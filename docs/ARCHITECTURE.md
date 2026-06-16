# Architecture

## Status: Phase 1 — Application Skeleton

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
- Status: **skeleton with `/health` endpoint created in Phase 1**

### Agent Layer (`apps/api/app/agents/`, `apps/api/app/workflows/`)
- LangGraph workflows
- Four agent teams: Research, Analysis Council, Validation, Judge
- All runs logged to `agent_runs` and `agent_steps` tables
- Status: **not yet implemented — Phase 2+**

### Database
- Local: PostgreSQL 16 via Docker Compose
- Production: Azure Database for PostgreSQL (Phase 2+)
- Status: **Docker Compose configured in Phase 1; models deferred to Phase 2**

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
│   │   │   ├── core/       (config, security, logging)
│   │   │   ├── api/        (route handlers)
│   │   │   │   └── v1/
│   │   │   ├── models/     (SQLAlchemy ORM models)
│   │   │   ├── schemas/    (Pydantic request/response schemas)
│   │   │   ├── services/   (business logic)
│   │   │   └── db/         (session, base)
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── web/        Next.js frontend
│       └── src/app/
├── packages/
│   ├── shared-types/   TypeScript types shared between frontend and backend
│   └── prompts/        Versioned prompt templates
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
The health endpoint lives at `/health` (unversioned, used by load balancers and health checks).

---

## Phase History

| Phase | What Changed |
|---|---|
| Phase 0 | Agentic dev infrastructure: skills, commands, docs scaffolding |
| Phase 1 | `apps/api/` FastAPI skeleton, `apps/web/` Next.js skeleton, Docker Compose, GitHub Actions CI |

---

## Not Yet Implemented

- Database models (`apps/api/app/models/`) — Phase 2
- Alembic migrations — Phase 2
- LangGraph agent workflows — Phase 2+
- Azure infrastructure — Phase 2+
- Authentication (Clerk) — Phase 2+
- Azure AI Search, Blob Storage — Phase 3+
