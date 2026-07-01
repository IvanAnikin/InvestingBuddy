# Roadmap

## Current Phase: Phase 16 — Final Report Generator (complete)

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

## Phase 6: Real Company Snapshot Workflow ✅

**Status: Complete**

Goal: Connect the existing company-analysis workflow skeleton to `FinancialDataService` so the system can create a real structured company snapshot from provider data, store sources/citations, validate output against the real-asset report schema, and save a draft report.

Deliverables:
- [x] `company_analysis` workflow upgraded to 8 nodes: `load_company` → `fetch_provider_data` → `create_source_records` → `build_company_snapshot` → `create_citations` → `validate_report_schema` → `save_draft_report` → `log_agent_steps`
- [x] `apps/api/app/workflows/snapshot_builder.py` — pure transformation module: `build_company_snapshot()`, `build_schema_draft()`, `get_profile_citation_fields()`, `get_price_citation_fields()`
- [x] `FinancialDataService` wired into workflow; default provider remains `MockFinancialDataProvider` (offline, no keys)
- [x] Structured company snapshot with company identity, provider metadata, source tier, retrieved timestamp, profile data, price history summary, missing fields list, and explicit `investment_recommendation: null`
- [x] Schema draft built using datapoint wrappers for all identity fields; validated against `validate_real_asset_report()`; result stored in state and report; failure marks `schema_valid=False` (no crash)
- [x] `Source` records created from `build_source_record()` helper for profile data and price data
- [x] `Citation` records created with `field_path`, `source_tier`, `data_quality` for every provider data item used
- [x] Alembic migration 003: adds `field_path VARCHAR(200)`, `source_tier VARCHAR(50)`, `data_quality VARCHAR(50)` to citations table
- [x] `CitationCreate` / `CitationRead` updated; `VALID_SOURCE_TYPES` extended with `financial_data_api`, `government_data`, `company_filing`, `model_estimate`
- [x] `WorkflowRunRequest` extended: `provider_name`, `require_schema_valid`
- [x] `WorkflowRunResponse` extended: `provider_name`, `is_mock`, `schema_valid`, `validation_errors`, `validation_warnings`, `missing_fields`
- [x] 38 new offline tests in `test_phase6_snapshot_workflow.py`; 306 total tests passing; ruff clean
- [x] All CI tests run offline — no network, no Azure, no API keys

Constraints enforced:
- No Azure OpenAI / LLM calls
- No investment recommendations (BUY/SELL/WATCH)
- No EODHD required
- No Azure resources required
- No network in CI tests
- No auth implemented

Skills used: `financial-data`, `backend-fastapi`, `langgraph-agents`, `database-design`, `investment-domain`, `testing-qa`, `security-review`, `docs-maintainer`

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

## Phase 9: Weekly Report Pipeline

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

## Phase 7: Azure OpenAI + First LLM Research Agent ✅

**Status: Complete**

Goal: Add the first optional LLM-powered research node that consumes the company snapshot
and generates structured draft sections. Workflow remains fully testable offline with a
mock LLM provider.

Deliverables:
- [x] `ResearchLLMClient` abstract interface (`apps/api/app/integrations/llm_provider.py`)
- [x] `MockResearchLLMClient` — deterministic, offline, no credentials, default for CI
- [x] `AzureOpenAIResearchLLMClient` — skeleton with LangChain `with_structured_output`; requires `AZURE_OPENAI_*` env vars; never used in CI
- [x] `get_llm_client(provider)` factory — selects client from config; defaults to mock
- [x] `ResearchSectionsOutput` Pydantic schema — no rating, no price target, no valuation fields
- [x] `validate_llm_sections()` safety gate — flags rating keywords and price target phrases
- [x] `generate_research_sections` node added to `company_analysis` workflow (node 5 of 9)
- [x] Node is opt-in: `use_llm=False` by default; skips gracefully when false
- [x] LLM failure is non-fatal — workflow completes without LLM sections on error
- [x] LLM sections appear in draft report `content_markdown` (labeled ADMIN DRAFT ONLY)
- [x] Schema validation still runs after LLM node (and is unaffected by LLM output)
- [x] `WorkflowRunRequest` extended: `use_llm`, `llm_provider`
- [x] `WorkflowRunResponse` extended: `llm_provider`, `llm_used`
- [x] `LLM_PROVIDER`, `AZURE_OPENAI_*` added to config + `.env.example`
- [x] Versioned prompt template: `packages/prompts/research/phase7_company_research_v1.md`
- [x] `langchain-openai>=0.2` added as optional `[llm]` dependency in `pyproject.toml`
- [x] 28 new offline tests in `test_phase7_llm_agent.py`; 334 total tests passing; ruff clean
- [x] `docs/AGENTS.md`, `docs/PROMPTING_GUIDE.md`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md` updated

Constraints enforced:
- No BUY/SELL/WATCH/HOLD/REJECT from LLM
- No price target or valuation conclusion from LLM
- No invented financial numbers
- LLM output is admin/draft only — not public investment advice
- All CI tests run offline (no Azure, no network, no credentials)
- Azure OpenAI is opt-in and config-driven only

Skills used: `langgraph-agents`, `backend-fastapi`, `investment-domain`, `security-review`, `testing-qa`, `docs-maintainer`

---

## Phase 8: Research Team Agents ✅

**Status: Complete**

Goal: Extend the `company_analysis` workflow with four deterministic Research Team agents
that run offline (no LLM, no Azure) and produce structured quality assessments of the
financial data, source quality, research completeness, and citation coverage.

Deliverables:
- [x] `financial_data_agent.py` — lists available vs missing financial data; classifies source tiers; warns on T5/T6 or mock data
- [x] `source_quality_agent.py` — enforces T5 providers (EODHD, Stooq, OpenBB) never promoted to primary; classifies T1–T6 strength; warns on T5/T6-only decision-critical claims
- [x] `research_completeness_agent.py` — schema-driven gap analysis against 9 report sections; lists blocking vs non-blocking gaps; next research task list
- [x] `citation_validator_v2.py` — checks DB citations AND schema draft datapoints; flags bare numbers (`status=failed`); warns on weak-tier citations for decision-critical fields
- [x] 3 versioned LLM prompt templates (`packages/prompts/research/phase8_*_v1.md`)
- [x] `CompanyAnalysisState` extended with 6 Phase 8 fields: `financial_data_summary`, `source_quality_summary`, `research_completeness_summary`, `upgraded_citation_validation`, `research_team_warnings`, `research_team_complete`
- [x] `company_analysis` workflow extended to 13 nodes (v4.0.0): 4 new Research Team nodes wired in correct sequence
- [x] `WorkflowRunResponse` extended with 5 Phase 8 compact summary fields
- [x] Draft report `content_markdown` includes Research Team admin sections
- [x] 52 new offline tests in `test_phase8_research_team.py`; 278 total tests passing; ruff clean
- [x] `docs/AGENTS.md`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROMPTING_GUIDE.md`, `README.md` updated

Constraints enforced:
- No BUY/SELL/WATCH/HOLD/REJECT or price target
- No invented financial numbers
- T5 providers (EODHD, Stooq, OpenBB) never promoted to primary tier
- All 4 Research Team agents are non-fatal (exceptions caught; workflow always completes)
- No Azure resources created; no Azure credentials required
- All CI tests run offline (no network, no LLM, no credentials)

Skills used: `langgraph-agents`, `backend-fastapi`, `investment-domain`, `security-review`, `testing-qa`, `docs-maintainer`

---

## Phase 9: Analysis Council MVP ✅

**Status: Complete**

Goal: Extend the `company_analysis` workflow with five deterministic Analysis Council agents
that run offline (no LLM, no Azure) and produce structured bull/bear/risk/valuation/committee
assessments. All agents enforce no-recommendation, no-price-target constraints.

Deliverables:
- [x] `bull_case_agent.py` — positive thesis points, sector tailwinds, evidence used, assumptions; forbidden word gate; confidence based on source tier
- [x] `bear_case_agent.py` — negative thesis points, headwinds, key unknowns; challenges bull case assumptions; no SELL/SHORT language
- [x] `risk_agent.py` — classifies risks across 6 categories; always includes data-quality and source-quality risks from Phase 8 agents
- [x] `valuation_guard_agent.py` — checks DCF/relative/yield inputs; blocks valuation for mock/T5/T6 data; never produces price target or fair value
- [x] `investment_committee_chair.py` — synthesises all council outputs; quality gate (5 boolean checks); assigns provisional_internal_status from allowed set only
- [x] 5 versioned LLM prompt templates (`packages/prompts/research/phase9_*_v1.md`)
- [x] `CompanyAnalysisState` extended with 9 Phase 9 fields
- [x] `company_analysis` workflow extended to 18 nodes (v5.0.0)
- [x] `WorkflowRunResponse` extended with 9 Phase 9 compact summary fields
- [x] Draft report `content_markdown` includes Analysis Council admin sections
- [x] 64 new offline tests in `test_phase9_analysis_council.py`; 458 total tests passing; ruff clean
- [x] `docs/AGENTS.md`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROMPTING_GUIDE.md`, `README.md` updated

Constraints enforced:
- No public BUY/SELL/HOLD/WATCH/REJECT recommendations produced
- No price targets or fair value estimates
- No invented financial numbers
- Allowed internal statuses enforced: only 5 whitelisted values
- All 5 Analysis Council agents are non-fatal (exceptions caught; workflow always completes)
- No Azure resources created; no Azure credentials required
- All CI tests run offline (no network, no LLM, no credentials)

Skills used: `langgraph-agents`, `backend-fastapi`, `investment-domain`, `security-review`, `testing-qa`, `docs-maintainer`

---

## Phase 10: Admin Review UI ✅

**Status: Complete**

Goal: First usable internal admin workspace for reviewing InvestingBuddy research outputs without needing cURL or Swagger UI.

Deliverables:
- [x] `GET /api/v1/reports` — list draft reports (admin/dev only)
- [x] `GET /api/v1/reports/{report_id}` — get draft report by ID (admin/dev only)
- [x] `ReportList` Pydantic schema + `list_reports` service function
- [x] `/admin` — dashboard: backend health, company count, latest reports, platform status badges
- [x] `/admin/companies/new` — company creation form (ticker, exchange, name, country, sector, currency)
- [x] `/admin/analysis` — analysis run form with full Phase 9 result display (quality gate, bull/bear/risk/valuation/committee, warnings)
- [x] `/admin/reports` — draft report list table
- [x] `/admin/reports/[id]` — draft report detail with metadata, admin disclaimers, raw markdown content
- [x] Admin layout: persistent disclaimer banner ("NOT INVESTMENT ADVICE"), navigation, footer
- [x] `src/lib/api.ts` — typed fetch client for all admin endpoints
- [x] `src/types/api.ts` — TypeScript types matching all backend Pydantic schemas
- [x] 13 new offline backend tests for reports endpoints; 463 total passing; ruff clean
- [x] Frontend: typecheck clean, lint clean, build clean (7 routes)
- [x] Homepage updated to Phase 10 status with /admin link
- [x] `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md` updated

Constraints enforced:
- No public publishing
- No investment advice or BUY/SELL/HOLD/WATCH recommendations
- No user authentication (documented as Phase 11 future work)
- No new Azure resources
- No deploy to Azure
- No secrets committed
- All UI prominently disclaims admin-only, draft-only status

Skills used: `frontend-nextjs`, `backend-fastapi`, `testing-qa`, `security-review`, `docs-maintainer`

---

## Phase 11: Admin Review / Approve-Reject Workflow ✅

**Status: Complete**

Goal: Complete the human-review loop for draft reports. Admin users can approve or reject internal draft reports from the UI. This is internal workflow only — not public publishing, not investment advice.

Deliverables:
- [x] `review_status` column added to `reports` (draft / under_review / approved_internal / rejected_internal / needs_revision / archived)
- [x] Review metadata columns: `reviewed_at`, `reviewer_note`, `review_decision_reason`, `human_review_required`, `approved_by`, `rejected_by`
- [x] `report_review_events` table — immutable audit log of every review action
- [x] Alembic migration 004 — adds all Phase 11 columns and creates `report_review_events`
- [x] `ReportReviewEvent` SQLAlchemy model (`app/models/review_event.py`)
- [x] `ReviewActionRequest`, `ReviewActionResponse`, `ReviewEventRead`, `ReviewEventList` Pydantic schemas
- [x] Review service functions: `mark_under_review`, `approve_report`, `reject_report`, `needs_revision`, `get_review_events`
- [x] Status transition guard — validates allowed-from states per action
- [x] Note required for `reject` and `needs_revision`
- [x] `acknowledge_warnings=true` required for approve when `human_review_required=true`
- [x] `POST /api/v1/admin/reports/{id}/mark-under-review`
- [x] `POST /api/v1/admin/reports/{id}/approve` — internal approval only, not public
- [x] `POST /api/v1/admin/reports/{id}/reject` — note required
- [x] `POST /api/v1/admin/reports/{id}/needs-revision` — note required
- [x] `GET /api/v1/admin/reports/{id}/review-events` — chronological audit log
- [x] No `/publish` endpoint — public publishing intentionally omitted
- [x] `ReviewPanel` client component — interactive review buttons, note textarea, acknowledgement checkbox, warning banners
- [x] Review event timeline in `/admin/reports/[id]`
- [x] Report list updated to show `review_status` with color-coded badge
- [x] TypeScript types and API client updated for all new schemas
- [x] 30 new backend tests; 493 total; ruff clean; typecheck / lint / build clean (8 routes)
- [x] `docs/API.md`, `docs/DATABASE.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md` updated

Constraints enforced:
- No public publishing — no `/publish` endpoint exists
- No investment advice or BUY/SELL/HOLD/WATCH recommendations
- No user authentication (Phase 12 future work — restrict at network level)
- No Azure resources provisioned
- No secrets committed
- All UI clearly states internal-only / draft / not investment advice

Skills used: `backend-fastapi`, `database-design`, `frontend-nextjs`, `investment-domain`, `security-review`, `testing-qa`, `docs-maintainer`

---

## Phase 12: Azure Staging Deployment ✅

**Status: Complete (infrastructure code)**

Goal: Provision and deploy the first Azure staging environment for InvestingBuddy.
Staging only — internal admin use, not public investment advice.

Deliverables:
- [x] `infra/azure/main.bicep` — full module wiring + inline RBAC assignments
- [x] `infra/azure/parameters/staging.bicepparam` — reads DB password from env var, no secrets committed
- [x] `infra/azure/modules/monitoring.bicep` — Log Analytics Workspace + Application Insights
- [x] `infra/azure/modules/keyvault.bicep` — Key Vault Standard, RBAC permission model
- [x] `infra/azure/modules/storage.bicep` — StorageV2 LRS + `investingbuddy-documents` container
- [x] `infra/azure/modules/postgres.bicep` — PostgreSQL 16 Flexible Server Standard_B1ms
- [x] `infra/azure/modules/appservice.bicep` — API B2 (Python 3.12) + Web B1 (Node 22)
- [x] `.github/workflows/deploy-api-staging.yml` — activated; OIDC login; ZIP deploy; health check
- [x] `.github/workflows/deploy-web-staging.yml` — activated; OIDC login; build with staging URL; ZIP deploy; smoke check
- [x] Staging Basic Auth middleware in FastAPI (`STAGING_BASIC_AUTH` env var → HTTP Basic Auth on all routes except `/health`)
- [x] `gunicorn` added as `[deploy]` optional dependency in `pyproject.toml`
- [x] `STAGING_BASIC_AUTH` added to config, `.env.example`, Key Vault reference in Bicep
- [x] `docs/DEPLOYMENT.md` fully updated — provisioning commands, migration steps, smoke tests, OIDC setup, cost notes, security limitations
- [x] `infra/azure/README.md` fully updated — Bicep structure, resource specs, KV secrets list, checklist
- [x] `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md` updated

Pending (manual steps before resources are live):
- [ ] Create App Registration `ib-github-actions-stg` + OIDC federated credential
- [ ] Set GitHub Secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_STAGING_DB_PASSWORD`
- [ ] Run `az deployment group create` against `ib-stg-rg`
- [ ] Populate Key Vault secrets (5 secrets)
- [ ] Run `alembic upgrade head` on staging DB
- [ ] Staging smoke tests pass

Constraints enforced:
- No production resources created or targeted
- No secrets committed to repository
- No Azure OpenAI required in CI (LLM_PROVIDER=mock default)
- No Azure AI Search provisioned
- No public publishing of investment research
- No breaking changes to local development

Skills used: `azure-deployment`, `backend-fastapi`, `security-review`, `docs-maintainer`

---

## Phase 13: EODHD Real Financial Data Integration

**Status: ✅ Delivered (2026-06-29)**

Goal: Connect the financial-data provider abstraction to real structured financial data from EODHD so InvestingBuddy can analyze real public companies with meaningful fundamentals, ratios, statements, and source metadata.

Deliverables:
- [x] `EodhdProvider` upgraded from placeholder to real implementation — company profile, price history, fundamentals (Highlights, Valuation, SharesStats, Technicals, annual Income/Balance/Cash Flow statements)
- [x] `CompanyIdentifierResolver` service — resolves ticker, name, or EODHD-format symbol to canonical EODHD symbols; detects ambiguity; works offline (structural parse) and live (EODHD search)
- [x] `company_financial_snapshots` table (migration 005) — persists raw EODHD payloads (JSONB) with SHA-256 deduplication hash, per-run and per-company linkage
- [x] `FinancialDataService.get_fundamentals()` — delegates to active provider
- [x] Company analysis workflow enriched: when `provider_name=eodhd`, fundamentals are fetched non-fatally, stored in state, and passed to `snapshot_builder`
- [x] `snapshot_builder` updated: `build_company_snapshot()` and `build_schema_draft()` populate `fundamentals_summary` and `snapshot_financials` with datapoint wrappers (T5, B_single_credible)
- [x] 4 diagnostic API endpoints: `GET /eodhd/status`, `GET /eodhd/company/{symbol}`, `GET /eodhd/fundamentals/{symbol}`, `GET /resolve`
- [x] `WorkflowRunResponse` extended with `fundamentals_available` and `fundamentals_warnings`
- [x] 51 offline tests — no network, no EODHD key required in CI; fixtures: `eodhd_fundamentals_aapl.json`, `eodhd_eod_aapl.json`, `eodhd_search_apple.json`, `eodhd_fundamentals_sparse.json`
- [x] Source tier always T5_api_aggregator — never promoted

Constraints enforced:
- No BUY/SELL/HOLD/WATCH recommendations
- No price targets
- EODHD not required in CI; tests use fixtures + mocks
- No API keys committed; loaded from env or Azure Key Vault

Skills used: `financial-data`, `backend-fastapi`, `database-design`, `langgraph-agents`, `testing-qa`, `security-review`, `docs-maintainer`

---

## Phase 14: Company Discovery / Screener ✅

**Status: Complete (2026-06-30)**

Goal: Add the first candidate discovery system so InvestingBuddy can screen a defined universe of companies and produce an internal list of candidates worth deeper analysis.

Deliverables:
- [x] `ScreeningUniverse`, `ScreeningRun`, `ScreeningCandidate` SQLAlchemy models (`app/models/screening.py`)
- [x] Alembic migration 006 — creates `screening_universes`, `screening_runs`, `screening_candidates`
- [x] `CompanyScreener` — deterministic theme-based screener; 6 themes; sector/exchange/region/keyword filters; market cap range filters; T5/T6 source tier assignment
- [x] `CompanyDiscoveryService` — `create_universe`, `run_screening`, `get_screening_run`, `list_screening_runs`, `list_candidates`, `promote_candidate_to_analysis`
- [x] Candidate promotion — creates or identifies a `Company` record; sets `candidate_status=ready_for_deeper_analysis`; no auto-analysis triggered
- [x] 7 admin/dev API endpoints under `/api/v1/discovery/`
- [x] EODHD fixture-backed offline search result parsing; source tier stays T5
- [x] Mandatory T5 warning: "Candidate requires primary-source validation before final analysis."
- [x] `candidate_status` allowed values: `candidate_found | needs_data | needs_primary_sources | ready_for_deeper_analysis | rejected_by_screen | error`
- [x] Forbidden outputs never produced: BUY, SELL, HOLD, WATCH, price_target, fair_value, upside_percent
- [x] 57 new offline tests; 601 total; ruff clean
- [x] `docs/API.md`, `docs/DATABASE.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/AGENTS.md`, `docs/DATA_SOURCES.md`, `README.md` updated

Constraints enforced:
- No investment recommendations, price targets, or fair values produced
- EODHD data stays T5_api_aggregator — never promoted
- Promotion creates Company record only; analysis workflow must be triggered separately by admin
- All CI tests offline (no network, no EODHD key, no Azure)
- No secrets committed

Skills used: `financial-data`, `backend-fastapi`, `database-design`, `investment-domain`, `testing-qa`, `security-review`, `docs-maintainer`

---

## Phase 15: Scoring + Valuation Framework ✅

**Status: Complete (2026-07-01)**

Goal: Add a deterministic multi-dimension research attractiveness scorecard on top of Phase 14 discovery candidates and Phase 9 company analysis outputs. Score candidates across 10 dimensions to produce ranked shortlists for deeper admin review — no investment recommendations, no price targets, no fair values.

Deliverables:
- [x] `Scorecard` SQLAlchemy model (`app/models/scorecard.py`) — `scorecards` table; JSONB for scores/warnings/missing_data/source_quality_summary; FK links to companies, screening_candidates, reports (all SET NULL)
- [x] Alembic migration 007 — creates `scorecards` table
- [x] `ScoringEngine` — deterministic 10-dimension scorer; T6/mock ≤ 30, T5 ≤ 60, T1/T2 ≤ 100 caps; risk_penalty_score subtracted; safety gate blocks all forbidden terms
- [x] `ValuationReadinessService` — readiness-only classifier (not_ready / partial / ready_for_basic_multiples / ready_for_deeper_valuation); never produces price target or fair value
- [x] `ALLOWED_INTERNAL_STATUSES` — 6 research queue labels; never public recommendations
- [x] `ScoringService` — DB-aware: score_candidate, score_screening_run, list_ranked_candidates, explain_candidate_score, score_company_analysis
- [x] Pydantic schemas (`app/schemas/scoring.py`) — all responses include static disclaimer
- [x] 5 admin/dev API endpoints under `/api/v1/scoring/`
- [x] `score_research_attractiveness` LangGraph node (Phase 15, Node 17) — non-fatal; inserted between `investment_committee_chair` and `save_draft_report`
- [x] `CompanyAnalysisState` extended with `research_attractiveness_scorecard` field
- [x] Workflow version bumped to 6.0.0 (19 nodes total)
- [x] 54 new offline tests; 675 total; ruff clean
- [x] Docs updated: API.md, DATABASE.md, ARCHITECTURE.md, ROADMAP.md, AGENTS.md, README.md

Constraints enforced:
- No BUY/SELL/HOLD/WATCH/REJECT public recommendations
- No price targets, fair values, or upside percentages
- internal_status values are research queue labels only (admin-only)
- Mock/T6 data capped at ≤ 30/100 overall score
- T5 data capped at ≤ 60/100 overall score
- All CI tests offline (no network, no EODHD key, no Azure)
- No secrets committed

Skills used: `investment-domain`, `backend-fastapi`, `database-design`, `langgraph-agents`, `testing-qa`, `docs-maintainer`

---

## Phase 16: Final Report Generator ✅

**Status: Complete (2026-07-01)**

Goal: Combine all Phase 1–15 outputs (discovery candidate, scorecard, financial snapshot, Research Team + Analysis Council outputs, citations) into a single 19-section structured internal draft report for human admin review. Safety gate blocks all forbidden recommendation language.

Deliverables:
- [x] `FinalReportGeneratorService` — 6 async methods: `generate_from_scorecard`, `generate_from_candidate`, `generate_from_company`, `generate_from_report`, `validate_final_report`, `regenerate_report_section`
- [x] Safety gate (`run_safety_gate`) — forbidden-term scan across all section text; exempt-field list for meta-documentation fields; `blocks_approval=True` on any hit
- [x] 19 required report sections: `admin_disclaimer`, `executive_summary`, `company_identity`, `discovery_rationale`, `data_availability_summary`, `financial_snapshot`, `internal_scorecard`, `valuation_readiness`, `bull_case`, `bear_case`, `risk_analysis`, `source_quality_review`, `citation_validation_review`, `research_completeness_review`, `missing_information`, `committee_chair_summary`, `workflow_status`, `human_review_checklist`, `source_citation_appendix`
- [x] Alembic migration 008 — adds 5 columns to `reports` table: `final_report_version`, `safety_validation_json`, `schema_validation_json`, `source_summary_json`, `scorecard_id` (FK → scorecards)
- [x] Pydantic schemas (`app/schemas/final_report.py`) — `SafetyValidationResult`, `FinalReportResponse`, `FinalReportValidateResponse`, `RegenerateSectionResponse`, `HumanReviewChecklistItem`; static `INTERNAL_DISCLAIMER` always included
- [x] 5 admin/dev-only API endpoints under `/api/v1/final-reports/`
- [x] LLM optional (offline by default) — enriches `executive_summary` via prompt template v1
- [x] Prompt template `packages/prompts/research/phase16_final_report_generator_v1.md`
- [x] 62 new offline tests; 737 total; ruff clean
- [x] Docs updated: API.md, DATABASE.md, ARCHITECTURE.md, ROADMAP.md, AGENTS.md, README.md

Constraints enforced:
- No BUY/SELL/HOLD/WATCH/REJECT public recommendations
- No price targets, fair values, or upside percentages
- No public publishing — all reports saved as admin-only drafts
- Human review always required (`human_review_required=True`)
- Safety gate blocks all forbidden language before report is stored
- LLM fully offline-testable; no Azure credentials in CI tests
- All 6 `internal_status` values are research queue labels only

Skills used: `investment-domain`, `backend-fastapi`, `database-design`, `testing-qa`, `docs-maintainer`

---

## Phase 17: Judge + Backtesting

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

## Phase 10: Personalized Investor Assistant

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
