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
npx playwright install --with-deps
npm run test:e2e
```

Playwright admin smoke tests use mock data/provider by default. Local runs also start a zero-dependency mock backend (`tests/support/mock-backend.mjs`) so server-rendered report pages render offline — no live staging is contacted. Staging E2E execution is opt-in.

The admin/web UI uses a modern dark glassmorphism design system (Phase 22.3). Report content renders through a sanitized markdown preview (`react-markdown` + `remark-gfm` + `rehype-sanitize`) with a Preview/Raw toggle — presentation only; report semantics are unchanged and all internal-only disclaimers are preserved.

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
| `EODHD_API_KEY` | Phase 13+ | EODHD API key — free plan works for `eodhd_price_only` and `eodhd_free_real` (`/eod` endpoint); paid plan required for `eodhd` (`/fundamentals`); store in Key Vault for staging/prod |
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
| Phase 17 | Done | Admin Auth Proxy: all browser admin calls routed through Next.js server-side proxy (`/api/admin/proxy`); backend credentials remain server-only |
| Phase 18 | Done | Staging E2E reliability hardening and auth/Bicep resilience fixes |
| Phase 19 | Superseded | Live EODHD smoke testing — superseded by Phase 19.1 |
| Phase 19.1 | Done | Free Real Data Provider Stack: `EodhdPriceOnlyProvider` (EODHD /eod free plan); `SecEdgarFundamentalsProvider` (XBRL fundamentals, ticker→CIK, no key); `TrendSignalEngine` (1M/3M/6M returns, MA50/MA200, momentum labels — no BUY/SELL/HOLD/WATCH); `FreeRealSnapshotComposer`; `NewsCatalystProvider` (SEC 8-K); composite providers `free_real` + `eodhd_free_real`; 64 new offline tests; 831 total; no paid fundamentals required. Staging: SEC EDGAR ✅; Stooq blocked from Azure; TrendSignalEngine not yet wired into main workflow. |
| Phase 19.1 safety fix | Done | `investment_committee_chair` forces `human_review_required=True` when safety guard triggers |
| Phase 20 | Done | Admin Final Report UI: internal final-report actions (generate draft, validate, optional regenerate section) + final-report metadata rendering |
| Phase 21 | Done | Playwright admin smoke tests (mock provider by default, staging E2E opt-in only) |
| Phase 22 | Done | Judge + Backtesting Framework: `BacktestingService`, `ResearchJudgeService`, `MockHistoricalOutcomeProvider`; migration 009 (backtest_runs, backtest_results, thesis_tracking_events); 8 admin-only API endpoints; 34 offline tests; no live EODHD; no public recommendations |
| Phase 22.1 | Done | Admin Backtesting UI: `/admin/backtesting` runs list + detail; create/evaluate/refresh; 13 Playwright tests; typecheck + lint + build clean |
| Phase 22.1 maint | Done | `/admin/reports` dynamic rendering fix; homepage platform phase text updated |
| Phase 19.2 | Done | Real Price + Trend Workflow: `TrendSignalEngine` wired into `company_analysis`; non-blocking Stooq→EODHD fallback on Azure; composite provider tracking; T5/T6 metadata in snapshot; AAPL `free_real` end-to-end on staging with `safety_valid=True` |
| Phase 19.2.1 | Done | Staging deploy + provider observability hardening: SHA-verified `/health` deploy check, Oryx boot-fail detection, Stooq→EODHD fallback surfaced in provider warnings, `sector=None` scoring fix |
| Phase 19.3 | Done | SEC Fundamentals Normalization + Report Completeness: `sec_fundamentals_normalizer` maps SEC XBRL companyfacts → normalized income/cash-flow/balance-sheet metrics + derived margins/ROE/debt-to-equity/YoY; injected into `free_real` snapshot; `FinancialDataAgent` narrates real fundamentals; `ValuationGuardAgent` reaches `partial` (conclusions still blocked); EBITDA/market cap/EV never fabricated; 22 offline tests. Remaining identity/sector/market-metric enrichment → Phase 19.4 |
| Phase 19.3.1 | Done | SEC Freshness + Review Consistency: SEC normalizer selects the latest annual across all us-gaap alias concepts (filed-date tiebreak, full-year preference, stale-year warning) — no stale FY2018; `investment_committee_chair` emits canonical `human_review_required` (markdown never contradicts page metadata); `BearCaseAgent`/`RiskAgent` acknowledge *partial* SEC fundamentals instead of "all missing"; 14 offline tests. `safety_valid`/`human_review_required` stay true; no paid fundamentals, no valuation conclusions |
| Phase 19.4 | Done | Identity + Sector + Market-Metric Enrichment: `company_profile_enrichment` (sector from DB or inferred SEC SIC/T6, industry/website SEC/T2, LEI GLEIF/T2 name-guarded) + `market_metrics_enrichment` (latest close + 52-week range/T5, shares outstanding SEC DEI/T2, market cap = close × shares, EV = market cap + debt − cash, P/E — all DERIVED ESTIMATES/T6 when inputs exist); resolved fields pruned from `missing_fields`; `FinancialDataAgent` narrates derived metrics; `ValuationGuardAgent` stays `partial`, conclusions blocked; 24 offline tests. LEI/ISIN/IPO/EBITDA/EV-EBITDA/beta never fabricated; no paid fundamentals, no valuation conclusions |
| Phase 22.3 | Done | UI Modernization + Markdown Report Preview (frontend/UI only): dark glassmorphism design system + reduced-motion-safe animated background; `GlassCard`/`StatusPill`/`SafetyBanner`/`AppShell` primitives; safe `MarkdownReportPreview` (`react-markdown` + `remark-gfm` + `rehype-sanitize`, no `dangerouslySetInnerHTML`) with Preview/Raw toggle + mini TOC replacing the raw `<pre>`; all pages + homepage restyled; disclaimers preserved verbatim; no backend / report-semantics changes; no public publishing; 55 Playwright tests |

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
