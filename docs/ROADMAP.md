# Roadmap

## Current Phase: Phase 11 — Admin Review / Approve-Reject Workflow (human review loop complete)

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

## Phase 12: Judge + Backtesting

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
