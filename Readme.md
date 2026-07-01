# InvestingBuddy

AI-powered multi-agent investment research platform focused on discovering, analyzing and monitoring medium-term investment opportunities in European public markets.

---

## What It Is

InvestingBuddy uses a council-of-agents approach to generate evidence-based investment research. Specialized agents research, debate, validate and publish investment opportunities — every claim backed by citations, every recommendation reviewed by a human before publication.

**Not a trading bot.** The platform provides research and decision support only. No automatic execution. No broker integration.

---

## Repository Structure

```
investingbuddy/
├── apps/
│   ├── api/        FastAPI backend (Python)
│   └── web/        Next.js frontend (TypeScript)
├── docs/           Architecture, API, database, and deployment docs
├── infra/          Azure infrastructure and Terraform (Phase 2+)
├── packages/       Shared TypeScript types and versioned prompts (Phase 2+)
├── docker-compose.yml
├── .env.example
└── CLAUDE.md
```

---

## Local Development Setup

### Prerequisites

- Python 3.12+
- Node.js 22+
- Docker (for PostgreSQL)

### 1. Clone and copy environment variables

```bash
git clone <repo-url>
cd investingbuddy
cp .env.example .env
# Edit .env — the defaults work for local Docker PostgreSQL
```

### 2. Start PostgreSQL

```bash
docker compose up -d
```

### 3. Start the backend

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

API runs at <http://localhost:8000>  
Swagger UI at <http://localhost:8000/api/docs>  
Health check at <http://localhost:8000/health>

### 4. Start the frontend

```bash
cd apps/web
npm install
npm run dev
```

Frontend runs at <http://localhost:3000>  
Admin workspace at <http://localhost:3000/admin> (internal — not investment advice)

---

## Running Tests

### Backend

```bash
cd apps/api
source .venv/bin/activate
pytest tests/ -v
ruff check .
```

### Frontend

```bash
cd apps/web
npm run typecheck
npm run lint
npm run build
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values as needed.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `APP_ENV` | No | `development` / `staging` / `production` |
| `SECRET_KEY` | Yes (prod) | App secret key |
| `STAGING_BASIC_AUTH` | Phase 12+ | Staging access control `user:pass` — Key Vault ref in staging |
| `LLM_PROVIDER` | No | LLM client: `mock` (default, CI-safe) / `azure_openai` |
| `AZURE_OPENAI_*` | Phase 7+ | Azure OpenAI credentials — required only when `LLM_PROVIDER=azure_openai` |
| `AZURE_STORAGE_*` | Phase 5+ | Azure Blob Storage for documents |
| `AZURE_SEARCH_*` | Phase 5+ | Azure AI Search for RAG |
| `FINANCIAL_DATA_PROVIDER` | No | Provider to use: `mock` (default) / `eodhd` / `sec_edgar` / etc. |
| `EODHD_API_KEY` | Phase 13+ | EODHD API key — required only when provider is `eodhd`; store in Key Vault for staging/prod |
| `ENABLE_EODHD_INTEGRATION_TESTS` | Phase 13+ | Set `true` for local live EODHD integration tests; never set in CI |
| `NEXT_PUBLIC_API_BASE_URL` | No | Backend API URL for the frontend |
| `NEXT_PUBLIC_CLERK_*` | Phase 8+ | Clerk authentication keys |

---

## Development Phases

| Phase | Status | Description |
|---|---|---|
| Phase 0 | Done | Agentic dev infrastructure (skills, commands, docs) |
| Phase 1 | Done | Application skeleton (FastAPI + Next.js + Docker + CI) |
| Phase 2 | Done | First LangGraph agent workflow, company storage |
| Phase 3 | Done | Research storage, citations, Blob + AI Search |
| Phase 3.5 | Done | Research contracts foundation (real-asset equity schema, validation, source taxonomy) |
| Phase 4 | Done | Financial data provider foundation (provider abstraction, mock provider, provider skeletons, API endpoints) |
| Phase 4.5 | Done | Live free data providers (Stooq, GLEIF, SEC EDGAR) |
| Phase 6 | Done | Real company snapshot workflow (8-node; provider data → sources + citations → schema validation) |
| Phase 7 | Done | Azure OpenAI + first LLM research agent (optional generate_research_sections node; mock + Azure clients) |
| Phase 8 | Done | Research Team agents: 4 deterministic nodes (financial data, source quality, research completeness, citation validator v2); 13-node workflow v4.0.0 |
| Phase 9 | Done | Analysis Council MVP: 5 deterministic agents (bull/bear/risk/valuation guard/committee chair); 18-node workflow v5.0.0; no public recommendations |
| Phase 10 | Done | Admin Review UI: `/admin` workspace with dashboard, company form, analysis trigger, report list + detail; reports API endpoints; 463 tests |
| Phase 11 | Done | Admin Review / Approve-Reject Workflow: 5 admin review endpoints; `report_review_events` audit table; `ReviewPanel` UI; 493 tests |
| Phase 12 | Done | Azure Staging Infrastructure: 5 Bicep modules; activated deploy workflows (OIDC); staging Basic Auth middleware |
| Phase 13 | Done | EODHD Real Financial Data: live `EodhdProvider`; `CompanyIdentifierResolver`; `company_financial_snapshots` table; fundamentals in workflow + snapshot_builder; 4 EODHD diagnostic endpoints; 552 offline tests |
| Phase 14 | Done | Company Discovery / Screener: `CompanyScreener`; `CompanyDiscoveryService`; 3 new DB tables (migration 006); 7 discovery API endpoints (universes + runs + candidates + promote); 6 themes; T5 enforced for EODHD; 601 offline tests |
| Phase 15 | Done | Scoring + Valuation Framework: `ScoringEngine` (10 dimensions; T6/mock ≤ 30, T5 ≤ 60, T1/T2 ≤ 100); `ValuationReadinessService`; `scorecards` table (migration 007); `score_research_attractiveness` Node 17; workflow v6.0.0 (19 nodes); 5 scoring API endpoints; 675 offline tests |
| Phase 16 | Done | Final Report Generator: `FinalReportGeneratorService` (6 methods); safety gate; 19-section structured internal draft; migration 008 (5 new reports columns); 5 admin endpoints; LLM optional; 737 offline tests |
| Phase 5 | Planned | Full council-of-agents MVP |
| Phase 17 | Planned | Judge system and backtesting |
| Phase 18 | Planned | Personalized investor assistant (V2) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Alembic, Pydantic v2 |
| Frontend | Next.js 16, React, TypeScript, Tailwind CSS |
| Agent framework | LangGraph, LangChain, Azure OpenAI (Phase 2+) |
| Database | PostgreSQL (local Docker / Azure PostgreSQL in prod) |
| Cloud | Microsoft Azure (Phase 2+) |
| CI/CD | GitHub Actions |
| Auth | Clerk → Microsoft Entra External ID (Phase 2+) |

---

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Database](docs/DATABASE.md)
- [Agents](docs/AGENTS.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Roadmap](docs/ROADMAP.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)
