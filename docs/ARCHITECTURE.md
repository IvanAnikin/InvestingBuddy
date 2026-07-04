# Architecture

## Status: Phase 17 — Admin Auth Proxy (Next.js server-side proxy for protected FastAPI calls; no credentials in browser; Add Company + reports + review actions fixed on staging)

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
- Communicates with backend via server-side proxy (Phase 17+) — credentials never in browser
- Status: **Phase 17 — Admin Auth Proxy live; all protected admin API calls routed through Next.js server-side proxy**
  - `/api/admin/proxy/[...path]` — server-side proxy route; adds `Authorization: Basic` server-side; path allowlist; rejects unknown paths; sanitizes errors; never exposes credentials to browser
  - `src/lib/api.ts` — smart base URL: server components call `BACKEND_API_BASE_URL` directly; client components use `/api/admin/proxy/…`
  - `/admin` — dashboard (health, company count, latest reports)
  - `/admin/companies/new` — create company form
  - `/admin/analysis` — trigger 19-node workflow; full result display
  - `/admin/reports` — draft report list with review_status column
  - `/admin/reports/[id]` — report detail with review action panel + event timeline
    - `ReviewPanel` (client component) — interactive review buttons, note textarea, warnings
    - Review event timeline — chronological audit log display
  - `src/types/api.ts` — TypeScript types (includes ReviewActionRequest, ReviewEvent, etc.)

#### Admin Auth Proxy — Request Flow (Phase 17)

```text
Browser (admin UI)
  → same-origin: /api/admin/proxy/api/v1/companies   (no credentials)
  → Next.js server route.ts
       adds Authorization: Basic <base64(BACKEND_BASIC_AUTH)>   [server-only env var]
  → FastAPI backend: https://ib-stg-api.azurewebsites.net/api/v1/companies
       checks STAGING_BASIC_AUTH
  → response forwarded back to browser   (Authorization header stripped)
```

Required App Service env vars for `ib-stg-web` (server-only, no `NEXT_PUBLIC_` prefix):
- `BACKEND_API_BASE_URL` — full URL of the FastAPI backend
- `BACKEND_BASIC_AUTH` — `user:password` matching `STAGING_BASIC_AUTH` on the API

### Backend (`apps/api/`)
- FastAPI, SQLAlchemy async, Pydantic v2, Alembic
- All business logic, database operations, agent orchestration triggers
- Status: **company endpoints + workflow trigger live in Phase 2**

### Agent Layer (`apps/api/app/agents/`, `apps/api/app/workflows/`)
- LangGraph `StateGraph` workflows
- Four agent teams: Research, Analysis Council, Validation, Judge
- All runs logged to `agent_runs` and `agent_steps` tables
- Status: **Phase 15 — `company_analysis` is a 19-node workflow with 4 Research Team + 5 Analysis Council + 1 Scoring agents (all deterministic), 1 optional LLM node, and full source/citation tracking. Workflow version `6.0.0`.**
- Research Team agents (Phase 8, `apps/api/app/agents/research_team/`):
  - `financial_data_agent.py` — classifies available vs missing financial data; source tier accounting
  - `source_quality_agent.py` — T1–T6 source classification; enforces T5 providers never promoted
  - `research_completeness_agent.py` — schema-driven gap analysis; blocking vs non-blocking gaps
  - `citation_validator_v2.py` — checks DB citations AND schema draft datapoints; flags bare numbers
- Analysis Council agents (Phase 9, `apps/api/app/agents/analysis_council/`):
  - `bull_case_agent.py` — positive thesis points, sector tailwinds, evidence, assumptions; forbidden word gate
  - `bear_case_agent.py` — negative thesis points, headwinds, key unknowns; challenges bull case
  - `risk_agent.py` — 6-category risk classification; always includes data-quality and source-quality risks
  - `valuation_guard_agent.py` — blocks valuation when mock/T5/T6 data; no price target ever produced
  - `investment_committee_chair.py` — synthesises council; quality gate; assigns provisional_internal_status from whitelist only
- Scoring agent (Phase 15, `apps/api/app/agents/analysis_council/`):
  - `score_research_attractiveness.py` — Node 17; deterministic 10-dimension research attractiveness scorecard; non-fatal; no price targets; no recommendations; T6/mock ≤ 30, T5 ≤ 60, T1/T2 ≤ 100
- Final Report Generator (Phase 16, `apps/api/app/services/final_report_generator.py`):
  - `FinalReportGeneratorService` — 6 async methods; assembles 19-section internal draft from scorecard/candidate/company/report inputs
  - Safety gate (`run_safety_gate`) — scans all section text for forbidden recommendation language; `blocks_approval=True` on any hit; exempt-field list prevents false positives from meta-documentation fields
  - LLM optional (offline by default) — enriches `executive_summary` via `packages/prompts/research/phase16_final_report_generator_v1.md`

### Database
- Local: PostgreSQL 16 via Docker Compose
- Production: Azure Database for PostgreSQL Flexible Server
- Status: **migration 008 pending on staging — adds 5 final-report columns to reports table (Phase 16); migrations 001–007 applied on staging; 001–008 applied locally**

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
│   │   │   ├── integrations/   financial_data_provider.py (ABC + schemas + SourceRecordAttrs), financial_data_service.py, llm_provider.py (ResearchLLMClient ABC + MockClient + AzureClient + factory), providers/ (mock, eodhd, sec_edgar[live], stooq[live], gleif[live], openbb[placeholder])
│   │   │   ├── services/       company_service, report_service, agent_run_service, source_service, citation_service, report_validation_service
│   │   │   ├── agents/
│   │   │   │   ├── base.py     CompanyAnalysisState TypedDict
│   │   │   │   └── validation/
│   │   │   │       └── citation_validator.py   Phase 3 skeleton
│   │   │   ├── workflows/
│   │   │   │   ├── company_analysis.py   9-node Phase 7 workflow (+ optional LLM node)
│   │   │   │   └── snapshot_builder.py   pure transformation: snapshot + schema draft
│   │   │   └── db/             session, base
│   │   ├── alembic/
│   │   │   └── versions/
│   │   │       ├── 001_add_initial_tables.py
│   │   │       ├── 002_add_sources_and_citations.py   Phase 3
│   │   │       └── 003_add_citation_provenance_fields.py  Phase 6
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── web/        Next.js frontend
│       └── src/app/
├── packages/
│   ├── shared-types/   TypeScript types shared between frontend and backend
│   ├── prompts/
│   │   └── research/
│   │       └── phase7_company_research_v1.md   Phase 7 LLM prompt (v1)
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
run_company_analysis(db, company_id, provider_name, require_schema_valid)
    ↓
LangGraph StateGraph.ainvoke(initial_state)
    ↓
  load_company                → creates agent_run, resolves company from DB
  fetch_provider_data         → calls FinancialDataService (default: MockProvider)
  create_source_records       → build_source_record() + source_service.get_or_create_source()
  build_company_snapshot      → snapshot_builder.build_company_snapshot()
  generate_research_sections  → ResearchLLMClient.generate_research_sections() [optional; use_llm=False by default]
  create_citations            → CitationCreate with field_path, source_tier, data_quality
  validate_report_schema      → validate_real_asset_report() → ValidationResult stored
  save_draft_report           → ReportCreate with snapshot JSON + LLM sections + validation status
  log_agent_steps             → marks agent_run completed
    ↓
WorkflowRunResponse (agent_run_id, draft_report_id, status, summary,
                     provider_name, is_mock, schema_valid,
                     validation_errors, validation_warnings, missing_fields,
                     llm_provider, llm_used)
```

All errors are caught, logged to `agent_runs.error_message`, and returned as HTTP 422.
`require_schema_valid=true` in the request body forces `status=failed` when the schema draft is invalid.

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
| Phase 6 | ✅ Complete | 8-node company_analysis workflow; FinancialDataService integrated; company snapshot; source + citation records (with field_path, source_tier, data_quality); schema validation gate; migration 003; 38 new tests; 306 total |
| Phase 7 | ✅ Complete | 9-node workflow; `ResearchLLMClient` abstraction (Mock + AzureOpenAI skeleton); optional `generate_research_sections` LLM node; `ResearchSectionsOutput` schema; safety gate; prompt template v1; `use_llm`/`llm_provider` API fields; 28 new offline tests; 334 total |
| Phase 8 | ✅ Complete | 13-node workflow v4.0.0; 4 deterministic Research Team agents (financial data, source quality, completeness, citation v2); 3 prompt templates; 9 new state fields; 5 API response fields; 52 new offline tests; 394 total |
| Phase 9 | ✅ Complete | 18-node workflow v5.0.0; 5 deterministic Analysis Council agents (bull, bear, risk, valuation guard, committee chair); 5 prompt templates; 9 new state fields; 9 API response fields; 64 new offline tests; 458 total |
| Phase 10 | ✅ Complete | Admin Review UI (`/admin`); 5 Next.js routes; `GET /api/v1/reports` + `GET /api/v1/reports/{id}`; typed API client; 13 new backend tests; 463 total; ruff + typecheck + lint + build clean |
| Phase 11 | ✅ Complete | Admin Review Workflow; 5 new admin endpoints; `ReportReviewEvent` model; migration 004; `ReviewPanel` client component; review event timeline; 30 new backend tests; 493 total; ruff + typecheck + lint + build clean |
| Phase 12 | ✅ Complete | Azure Staging Infrastructure; 5 Bicep modules; `main.bicep` with RBAC; activated `deploy-api-staging.yml` + `deploy-web-staging.yml` (OIDC); staging Basic Auth middleware; `gunicorn` deploy extra; docs updated |
| Phase 13 | ✅ Complete | EODHD real provider (`EodhdProvider`); `CompanyIdentifierResolver`; `company_financial_snapshots` table (migration 005, JSONB); workflow + snapshot_builder fundamentals enrichment; 4 EODHD diagnostic endpoints + `/resolve`; `WorkflowRunResponse` Phase 13 fields; 51 new offline tests; 552 total |
| Phase 14 | ✅ Complete | Company Discovery / Screener; `CompanyScreener`; `CompanyDiscoveryService`; 3 new tables (migration 006); 7 discovery API endpoints (universes + runs + candidates + promote); 6 themes; T5 source tier enforced for EODHD; fixture-based EODHD search; candidate promotion; 57 new offline tests; 601 total |
| Phase 15 | ✅ Complete | Scoring + Valuation Framework; `ScoringEngine` (10 dimensions; T6/mock ≤ 30, T5 ≤ 60, T1/T2 ≤ 100); `ValuationReadinessService`; `scorecards` table (migration 007); `ScoringService`; `score_research_attractiveness` node (Node 17); workflow v6.0.0 (19 nodes); 5 scoring API endpoints; 54 new offline tests; 675 total |
| Phase 16 | ✅ Complete | Final Report Generator; `FinalReportGeneratorService` (6 methods); safety gate (forbidden-term scan + exempt-field list); 19-section structured internal draft report; migration 008 (5 new reports columns); 5 API endpoints; LLM optional (offline by default); prompt template v1; 62 new offline tests; 737 total |

---

## What Is Not Yet Implemented

- Authentication (Clerk) — Phase 8
- Azure OpenAI in production (real keys) — optional, configure `LLM_PROVIDER=azure_openai` + env vars
- Live EODHD calls require `EODHD_API_KEY` — set in env or Azure Key Vault; tests run offline
- Ticker → CIK resolution for SecEdgarProvider — Phase 5
- SEC EDGAR XBRL fundamentals (`get_fundamentals`) — Phase 5
- Azure AI Search (embeddings, RAG) — Phase 5+
- Azure Blob Storage (PDF documents) — Phase 5+
- Full council-of-agents (all agent teams) — Phase 5
- OpenBB integration (evaluation pending) — Phase 5/6
- Scheduled background jobs (Azure Functions / Service Bus) — Phase 7
- Judge / backtesting — Phase 7
- Personalized recommendations — Phase 8
- Report citations linked to report_id at save time (currently linked via agent_run_id only) — Phase 5
