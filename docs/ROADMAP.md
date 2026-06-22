# Roadmap

## Current Phase: Phase 4.5 — Live Free Data Provider Integration

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

## Phase 3: Research Storage & Citations Foundation ✅

**Status: Complete**

Goal: Agent workflows can store research sources and link claims to citations.

Deliverables:
- [x] `Source` + `Citation` SQLAlchemy models (`app/models/source.py`)
- [x] Alembic migration 002 — creates `sources` and `citations` tables
- [x] Source service: `create_source`, `get_or_create_source` (dedup by hash/URL), `list_sources`, `get_source`
- [x] Citation service: `create_citation`, `list_citations_for_report`, `validate_citations_for_draft`
- [x] API endpoints: `POST/GET /api/v1/sources`, `GET /api/v1/sources/{id}`
- [x] API endpoints: `POST/GET /api/v1/reports/{id}/citations`, `POST /api/v1/reports/{id}/validate-citations`
- [x] `company_analysis` workflow creates placeholder `Source` + `Citation` in `save_report` node
- [x] `CitationValidator` agent skeleton (`agents/validation/citation_validator.py`) — structural check, no LLM
- [x] 76 passing tests (all new Phase 3 code covered)
- [x] ruff linting clean
- [ ] Azure Blob Storage integration (store PDF documents) — deferred to Phase 4
- [ ] Azure AI Search integration (chunk + embed sources) — deferred to Phase 4
- [ ] Real financial data ingestion (OpenBB, external APIs) — deferred to Phase 4
- [ ] Source Quality Agent — deferred to Phase 4
- [ ] Full LLM-powered citation validation — deferred to Phase 4

Skills used: `database-design`, `backend-fastapi`, `langgraph-agents`, `testing-qa`, `docs-maintainer`

---

---

## Phase 3.5: Research Contracts Foundation (Real-Asset Equity) ✅

**Status: Complete**

Goal: Formal, versioned, machine-validated report contract for real-asset company deep dives. No live API calls. Foundation for all future real-asset agent output.

Deliverables:
- [x] `packages/research-contracts/real_asset_equity/v1/report_schema.json` — JSON Schema Draft 2020-12 output contract
- [x] `packages/research-contracts/real_asset_equity/v1/source_taxonomy.json` — tier-ranked T1–T6 source catalogue
- [x] `packages/research-contracts/real_asset_equity/v1/eodhd_mapping.json` — provider mapping layer (schema field → EODHD endpoint + free fallbacks)
- [x] `packages/research-contracts/real_asset_equity/v1/alpha_sourcing_strategy.md` — discovery methodology (supply-chain laddering, event triggers)
- [x] `packages/research-contracts/real_asset_equity/v1/example_report_filled.json` — fictional worked example validating against the schema
- [x] `apps/api/app/services/report_validation_service.py` — offline `validate_real_asset_report()` utility
- [x] `apps/api/tests/test_report_validation.py` — tests: example validates; malformed fails; bare numbers fail; D-quality warnings surface
- [x] `docs/DATA_SOURCES.md` — source tier definitions, EODHD classification, provider abstraction plan
- [x] `docs/AGENTS.md` updated — real-asset schema contract, CitationValidator upgrade path, discovery profile
- [x] `docs/PROMPTING_GUIDE.md` updated — datapoint rule, source instructions, self-critique, discovery discipline
- [x] `docs/ROADMAP.md` updated — Phase 4 Financial Data Provider Foundation added

Key constraints enforced:
- No live EODHD, OpenBB, SEC EDGAR, or LLM calls
- No Azure credentials required
- All tests run offline
- `example_report_filled.json` is fictional; not investment advice

Skills used: `product-architect`, `investment-domain`, `financial-data`, `backend-fastapi`, `testing-qa`, `docs-maintainer`, `security-review`

---

## Phase 4: Financial Data Provider Foundation ✅

**Status: Complete**

Goal: Provider abstraction layer so agents can resolve financial data from multiple sources without changing the report schema. CI uses a mock provider with no external calls.

Deliverables:
- [x] `FinancialDataProvider` abstract base class (`apps/api/app/integrations/financial_data_provider.py`)
- [x] Typed Pydantic schemas: `CompanyProfileData`, `PriceHistoryData`, `PricePoint`, `FundamentalsData`, `FundamentalDataPoint`, `ProviderResponseMetadata`, `ProviderCapability`, `ProviderStatus`, `SourceTier`, `DataQuality`
- [x] `MockFinancialDataProvider` — deterministic test data, no external calls, `is_mock=True`
- [x] `SecEdgarProvider` — skeleton; T2 tier; raises `NotImplementedError`; no network
- [x] `StooqProvider` — skeleton; T5 tier; raises `NotImplementedError`; no network
- [x] `GleifProvider` — skeleton; T2 tier; raises `NotImplementedError`; no network
- [x] `OpenBBProvider` — skeleton; T5 tier; raises `NotImplementedError`; no network
- [x] `EodhdProvider` — placeholder; T5 tier; references `eodhd_mapping.json`; no network; `EODHD_API_KEY` not required in tests
- [x] `FinancialDataService` — provider registry; selects provider from `FINANCIAL_DATA_PROVIDER` config; default `mock`
- [x] `FINANCIAL_DATA_PROVIDER`, `EODHD_API_KEY`, `EODHD_BASE_URL` added to config + `.env.example`
- [x] Dev API endpoints: `GET /api/v1/financial-data/providers`, `GET /api/v1/financial-data/mock/company/{ticker}`, `GET /api/v1/financial-data/mock/prices/{ticker}`
- [x] 40+ offline tests — no Azure, no EODHD key, no external network
- [x] `docs/DATA_SOURCES.md` updated with provider registry and implementation notes
- [x] `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/AGENTS.md`, `README.md` updated

Rules enforced:
- No live API calls in CI — all tests use `MockFinancialDataProvider`
- EODHD key not hardcoded; loaded from env/Key Vault only
- Provider abstraction allows swapping EODHD by editing config, not code
- Tier assignment: EODHD → T5; EDGAR direct → T2; company IR → T1
- Mock data always marked `is_mock=True` and `D_weak_or_stale`

Skills used: `financial-data`, `backend-fastapi`, `testing-qa`, `security-review`, `docs-maintainer`

---

## Phase 4.5: Live Free Data Provider Integration ✅

**Status: Complete**

Goal: Implement live (no API key) financial data providers with offline test coverage and source-record integration. No LLM. No Azure.

Deliverables:
- [x] `StooqProvider` — live OHLCV CSV fetch from stooq.com; T5_api_aggregator; `_parse_stooq_csv()` pure parse function; exchange→suffix mapping
- [x] `GleifProvider` — live LEI lookup and name search from api.gleif.org; T2_regulator_or_gov; `_is_lei()` detection; `get_by_lei()` and `search_by_name()` public methods
- [x] `SecEdgarProvider` — live company submissions fetch from data.sec.gov by CIK; T2_regulator_or_gov; `get_company_by_cik()` public method; CIK zero-padding; fiscal year end parsing
- [x] `OpenBBProvider` — kept as evaluation placeholder; status `not_implemented`; not added as required dependency
- [x] `SourceRecordAttrs` schema and `build_source_record()` utility in `financial_data_provider.py` — maps provider metadata to DB-ready source record attrs
- [x] Tier → source_type and credibility_score mapping (T1→0.95, T2→0.90, T5→0.55, etc.)
- [x] Dev diagnostic API endpoints: `GET /api/v1/financial-data/stooq/prices/{ticker}`, `/gleif/entity/{lei_or_name}`, `/sec-edgar/company/{cik}`
- [x] `httpx` added to main dependencies
- [x] `ENABLE_INTEGRATION_TESTS=false` flag added to config and `.env.example`
- [x] `@pytest.mark.integration` marker registered in `pyproject.toml`
- [x] Test fixtures: `stooq_aapl_us.csv`, `gleif_apple_inc.json`, `gleif_empty_result.json`, `stooq_no_data.csv`, `sec_edgar_aapl_submissions.json`
- [x] 100+ offline tests in `test_phase5_live_providers.py` — all CI-safe, no network, no keys
- [x] Live integration tests in `test_integration_live_providers.py` — opt-in via `ENABLE_INTEGRATION_TESTS=true`
- [x] Manual integration test command documented in `test_integration_live_providers.py`
- [x] 268 total tests passing; ruff clean
- [x] `docs/DATA_SOURCES.md`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `.env.example` updated

Constraints enforced:
- No live calls in CI — all tests offline or skipped
- No EODHD calls (deferred)
- No LLM or Azure
- Provider status updated: Stooq, GLEIF, SEC EDGAR → `ok`; OpenBB → `not_implemented`; EODHD → `not_configured`

Skills used: `financial-data`, `backend-fastapi`, `testing-qa`, `security-review`, `docs-maintainer`

---

## Phase 5: Full Council-of-Agents MVP

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
- [ ] Agent output validated against real-asset report schema before draft is saved

Skills to use: `langgraph-agents`, `backend-fastapi`, `frontend-nextjs`, `investment-domain`, `testing-qa`

---

## Phase 6: Weekly Report Pipeline

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

## Phase 7: Judge + Backtesting

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

## Phase 8: Personalized Investor Assistant

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
