# Architecture

## Status: Phase 19.4.1 — Enrichment Completeness Consistency (hotfix on Phase 19.4). The completeness layer now consumes the *enriched* snapshot: `research_completeness_agent._enriched_present_fields()` suppresses blocking/missing gaps for enriched-and-present identity `lei`/`isin`/`sector_classification` and `snapshot_financials` market cap / EV / revenue / net income / debt / cash (and drops satisfied identity next-steps), while `source_quality_agent` gates its "Obtain LEI" recommendation on the LEI actually being missing and upgrades — never "absents" — derived market metrics. Genuinely-missing fields (ISIN, EBITDA, EV/EBITDA, beta, website, IPO date) stay gaps; nothing is fabricated. Underlying Phase 19.4 — Identity + Sector + Market-Metric Enrichment: two pure modules (`company_profile_enrichment`, `market_metrics_enrichment`) enrich the `free_real` / `eodhd_free_real` snapshot after SEC fundamentals + price history are available: sector (DB or **inferred** from SEC SIC, T6), industry/website (SEC, T2), LEI (GLEIF, T2, name-guarded); and derived latest close + **52-week range** (T5), **shares outstanding** (SEC DEI, T2), **market cap / enterprise value / P/E** (DERIVED ESTIMATES, T6, cited inputs) computed only when inputs exist. Resolved fields are pruned from `missing_fields`; `FinancialDataAgent` narrates the derived metrics and `ValuationGuardAgent` recognises them but still blocks all valuation conclusions (readiness stays `partial`). LEI/ISIN/IPO date/EBITDA/EV-EBITDA/beta are never fabricated. Underlying Phase 19.3.1: SEC normalizer (`sec_fundamentals_normalizer`) selects the latest annual filing across all alias concepts (filed-date tiebreak, stale-year warning); `investment_committee_chair` emits canonical `human_review_required`. Builds on Phase 19.2.1 (provider observability), Phase 19.1 Free Real Data Stack, Phase 22.1 Admin Backtesting UI. 964 backend tests passing (12 skipped, integration-only).

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
- Status: **Phase 22.1 — Admin Backtesting UI live; admin proxy active for all admin routes; dynamic rendering on /admin/reports**
  - `/api/admin/proxy/[...path]` — server-side proxy route; adds `Authorization: Basic` server-side; path allowlist; rejects unknown paths; sanitizes errors; never exposes credentials to browser
  - `src/lib/api.ts` — smart base URL: server components call `BACKEND_API_BASE_URL` directly; client components use `/api/admin/proxy/…`
  - `/admin` — dashboard (health, company count, latest reports)
  - `/admin/companies/new` — create company form
  - `/admin/analysis` — trigger 19-node workflow; full result display
  - `/admin/reports` — draft report list with review_status column (dynamic rendering restored)
  - `/admin/reports/[id]` — report detail with review action panel + event timeline
    - `ReviewPanel` (client component) — interactive review buttons, note textarea, warnings
    - Review event timeline — chronological audit log display
  - `/admin/backtesting` — backtesting runs list; create run form; run detail with evaluate/refresh
  - `src/types/api.ts` — TypeScript types (includes ReviewActionRequest, ReviewEvent, BacktestRun, BacktestResult, etc.)

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
- Status: **Phase 22.1 — `company_analysis` is a 19-node workflow (v6.0.0) with 4 Research Team + 5 Analysis Council + 1 Scoring agents (all deterministic), 1 optional LLM node, and full source/citation tracking. `investment_committee_chair` forces `human_review_required=True` when safety guard triggers (safety fix 2026-07-12). `TrendSignalEngine` exists but not yet wired in (Phase 19.2). `BacktestingService` + `ResearchJudgeService` live (Phase 22). Admin Backtesting UI live (Phase 22.1).**
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
- Status: **migrations 001–009 applied locally; migrations 001–009 applied on staging; migration 009 adds backtest_runs, backtest_results, thesis_tracking_events (Phase 22)**

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
| Phase 17 | ✅ Complete | Admin Auth Proxy; Next.js server-side proxy (`/api/admin/proxy`); `BACKEND_BASIC_AUTH` server-only env var; path allowlist; credentials never exposed to browser |
| Phase 18 | ✅ Complete | Staging E2E reliability fix; research contracts bundled in API ZIP; schema path robustness; auth + Bicep hardening; 733 tests |
| Phase 19 | Superseded | Live EODHD smoke test — superseded by Phase 19.1 (EODHD /fundamentals requires paid plan) |
| Phase 19.1 | ✅ Released | Free Real Data Stack: `EodhdPriceOnlyProvider`, `SecEdgarFundamentalsProvider`, `TrendSignalEngine`, `FreeRealSnapshotComposer`, `NewsCatalystProvider` (8-K), composite `free_real` + `eodhd_free_real`; 64 new offline tests; 831 total. Staging: SEC EDGAR ✅; Stooq blocked from Azure; EODHD /eod partial. TrendSignalEngine not yet wired into main workflow. |
| Phase 19.1 safety fix | ✅ Complete | `investment_committee_chair` forces `human_review_required=True` when safety guard triggers |
| Phase 20 | ✅ Complete | Admin Final Report UI — final-report metadata rendering, generate/validate/regenerate actions in admin UI |
| Phase 21 | ✅ Complete | Playwright admin smoke tests (mock provider by default, staging E2E opt-in) |
| Phase 22 | ✅ Complete | Judge + Backtesting Framework; `BacktestingService`, `ResearchJudgeService`, `MockHistoricalOutcomeProvider`; migration 009 (3 tables); 8 admin-only API endpoints; 34 offline tests |
| Phase 22.1 | ✅ Complete | Admin Backtesting UI: `/admin/backtesting` list + detail; create/evaluate/refresh; 13 Playwright tests; typecheck + lint + build clean |
| Phase 22.1 maintenance | ✅ Complete | `/admin/reports` dynamic rendering fix; homepage platform phase text updated |
| Phase 19.2 | ✅ Released | Real Price + Trend Workflow: `TrendSignalEngine` wired into `company_analysis`; Stooq→EODHD non-blocking fallback; composite provider tracking; T5/T6 metadata in snapshot |
| Phase 19.2.1 | ✅ Complete | Staging deploy + provider observability hardening: SHA-verified `/health` deploy check, Oryx boot-fail detection, Stooq→EODHD fallback surfaced in provider warnings, `sector=None` scoring fix |
| Phase 19.3 | ✅ Delivered | SEC Fundamentals Normalization + Report Completeness: `sec_fundamentals_normalizer` maps us-gaap companyfacts → normalized income/cash-flow/balance-sheet metrics + derived margins/ROE/D-E/YoY; injected into `free_real` snapshot; `FinancialDataAgent` narrates fundamentals; `ValuationGuardAgent` reaches `partial`; EBITDA/market cap/EV never fabricated; 22 offline tests. Remaining identity/sector/market-metric enrichment → Phase 19.4 |
| Phase 19.4 | ✅ Delivered | Identity + Sector + Market-Metric Enrichment: `company_profile_enrichment` (sector from DB or inferred SEC SIC/T6, website/industry SEC/T2, LEI GLEIF/T2 name-guarded; LEI/ISIN/IPO never fabricated) + `market_metrics_enrichment` (latest close + 52-week range/T5, shares SEC DEI/T2, market cap/EV/P-E as DERIVED ESTIMATES/T6 when inputs exist; EBITDA/EV-EBITDA/beta never fabricated). Pruned from `missing_fields`; `FinancialDataAgent` recognises + narrates derived metrics; `ValuationGuardAgent` stays `partial`, conclusions still blocked; report gains identity/profile + Market Metrics sections; 24 offline tests |

---

## What Is Not Yet Implemented

- **TrendSignalEngine in main workflow** — `TrendSignalEngine` exists but is not yet wired into `company_analysis`; Phase 19.2
- **Price data visible in T5 source-tier summary** — EODHD /eod price not visibly confirmed in staging smoke test; Phase 19.2
- **Stooq fallback on Azure** — Stooq appears blocked from Azure outbound; `free_real` provider needs non-blocking fallback to EODHD price-only; Phase 19.2
- **Composite provider_name tracking** — workflow metadata does not yet preserve composite provider names (e.g. `"free_real: stooq+sec_edgar"`); Phase 19.2
- **Admin auth hardening** — staging uses Basic Auth + proxy; Clerk/allowlist route-level auth is Phase 23
- **News + catalyst discovery workflow** — `SecEdgar8KProvider` exists but not wired into `company_analysis`; Phase 24
- **Real market candidate discovery** — screener foundation exists; full momentum + fundamentals + catalyst ranking is Phase 25
- **Public report publishing** — all reports are internal only; public approved-report pages are Phase 26
- **User accounts** — no signup, dashboard, watchlists, or preferences yet; Phase 27
- **Paid plans / Stripe** — not built; Phase 28
- **Personalized research reports** — not built; Phase 29
- **Monitoring + alerts** — thesis tracking table exists (Phase 22); automated monitoring workflow is Phase 30
- Azure OpenAI in production (real keys) — optional, configure `LLM_PROVIDER=azure_openai` + env vars
- Live EODHD calls require `EODHD_API_KEY` — set in env or Azure Key Vault; tests run offline
- Azure AI Search (embeddings, RAG) — future
- Azure Blob Storage (PDF documents) — future
- OpenBB integration (evaluation pending) — future
- Scheduled background jobs (Azure Functions / Service Bus) — Phase 30+
