# Roadmap

## Current State: Phase 22.3.1 Web Deploy Cache Hardening — a deploy/CI + frontend-verification hotfix on top of Phase 22.3. Fixes an operational issue found during the Phase 22.3 release: with `WEBSITE_RUN_FROM_PACKAGE=1` and `alwaysOn=false`, the statically prerendered homepage `/` could keep serving the old build after a deploy until a manual `az webapp restart`, while dynamic `/admin` routes updated immediately. Adds a `/api/version` build-metadata endpoint and an `x-ib-build-commit` `<meta>` tag, renders the homepage dynamically so `/` reflects the mounted bundle, bakes `NEXT_PUBLIC_*` build metadata in CI, best-effort restarts `ib-stg-web` after deploy (when an optional `AZURE_CREDENTIALS` service principal exists), and adds a SHA-verified smoke check that fails loudly if `/api/version`, `/`, or `/admin` are stale. No backend analysis or report-generation logic changed; no financial semantics changed; no auth, no public publishing, no recommendation language, and no secrets. See the Phase 22.3.1 section below.

## Previous State: Phase 22.3 UI Modernization + Markdown Report Preview — a frontend/UI-only phase on top of the Phase 19.4.1 data stack. The web/admin experience is modernized with a dark glassmorphism design system, a subtle animated aurora background (disabled under `prefers-reduced-motion`), and reusable glass UI primitives. Report content is now rendered through a **safe markdown preview** (`react-markdown` + `remark-gfm` + `rehype-sanitize`, no `dangerouslySetInnerHTML`) with a Preview/Raw toggle and a sticky mini table of contents, replacing the raw `<pre>` block. No backend analysis or report-generation logic changed; no public publishing was added; all mandatory internal-only / not-investment-advice / human-review disclaimers are preserved verbatim, and no BUY/SELL/HOLD/WATCH, price target, fair value or upside is produced. See the Phase 22.3 section below.

## Previous State: Phase 19.4.1 Enrichment Completeness Consistency — a hotfix on top of Phase 19.4. After the Phase 19.4 AAPL `free_real` smoke test, enriched fields that were present in the Company Snapshot (LEI, sector classification, derived market cap / EV / P/E / 52-week range, shares outstanding) were still being reported as **missing / blocking gaps** and still triggered *"Obtain LEI"* recommendations, because `research_completeness_agent` derived its gaps from the raw-profile schema draft (which never carries enrichment) and `source_quality_agent` recommended obtaining the LEI unconditionally. Phase 19.4.1 makes the completeness layer consume the enriched snapshot: a present enriched field is no longer a missing field, a blocking gap, or an "obtain it" next-step — while genuinely-absent fields (ISIN, EBITDA, EV/EBITDA, beta, IPO date, website) stay gaps and nothing is fabricated. Derived market metrics remain labelled **internal T6 estimates**, valuation readiness stays `partial` with all conclusions blocked, `human_review_required` stays true, and `schema_valid` may still be false. No BUY/SELL/HOLD/WATCH, price target, fair value or upside.

### Phase 19.4 (underlying): Identity + Sector + Market-Metric Enrichment — builds on Phase 19.3.1. Two pure enrichment modules feed the `free_real` snapshot: `company_profile_enrichment` fills sector (DB or **inferred** from SEC SIC, T6), industry/website (SEC, T2) and LEI (GLEIF, T2, name-guarded) — LEI/ISIN/IPO date are never fabricated; `market_metrics_enrichment` derives latest close + **52-week range** (T5), **shares outstanding** (SEC DEI, T2), and **market cap / enterprise value / P/E** as DERIVED ESTIMATES (T6, cited inputs) only when their inputs exist. EBITDA, EV/EBITDA and beta are never fabricated. Resolved fields are pruned from `missing_fields` (AAPL missing-info count drops materially); the FinancialDataAgent narrates the derived metrics and the ValuationGuardAgent recognises them but still blocks every valuation conclusion (readiness stays `partial`). The report markdown gains identity/profile and **Market Metrics (Derived — Internal)** sections. Underlying Phase 19.3(.1): SEC EDGAR XBRL companyfacts normalized into income-statement / cash-flow / balance-sheet metrics + derived margins/ROE/debt-to-equity/YoY growth with latest-annual freshness selection. No paid EODHD `/fundamentals`, no broad discovery. Next: Phase 24 News-Catalyst, Phase 25 discovery, then Phase 23 Auth.

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

## Phase 17: Admin Auth & Frontend-to-API Proxy ✅

**Status: Complete (2026-07-01)**

Goal: Fix the hosted admin UI so all protected FastAPI calls succeed on staging without exposing Basic Auth credentials to the browser.

Problem solved:
- `ib-stg-web` admin pages made direct browser calls to the protected FastAPI backend.
- Browser cannot include `Authorization: Basic …` without exposing credentials in JS bundles or network payloads.
- Result: `Error: Failed to fetch` on Add Company, company count, reports list, and all review actions.

Deliverables:
- [x] `apps/web/src/app/api/admin/proxy/[...path]/route.ts` — Next.js server-side proxy route; path allowlist; adds `Authorization: Basic` from server-only `BACKEND_BASIC_AUTH` env var; sanitizes errors; never exposes credentials to browser
- [x] `apps/web/src/lib/api.ts` — smart base URL: server components call `BACKEND_API_BASE_URL` directly with auth header; client components use same-origin proxy `/api/admin/proxy/…`
- [x] Proxy allowlist covering: `/health`, `/api/v1/companies`, `/api/v1/reports`, `/api/v1/workflows`, `/api/v1/admin/reports`, `/api/v1/discovery`, `/api/v1/scoring`, `/api/v1/final-reports`, `/api/v1/financial-data`, `/api/v1/sources`, `/api/v1/citations`
- [x] `BACKEND_API_BASE_URL` and `BACKEND_BASIC_AUTH` (server-only, no `NEXT_PUBLIC_` prefix) added to `.env.example` and documented for `ib-stg-web` App Service
- [x] Stale copy fixed: footer "Phase 10" → "Phase 17"; Run Analysis "18-node" → "19-node"; "No Auth Yet" badge → "Admin Proxy Active"; Platform Phase badges updated
- [x] Frontend typecheck clean, lint clean, build clean (proxy route appears as `ƒ (Dynamic)`)
- [x] No credential values committed; credentials read from App Service environment at runtime only
- [x] FastAPI staging Basic Auth retained; no changes to backend

Constraints enforced:
- `BACKEND_BASIC_AUTH` never used with `NEXT_PUBLIC_` prefix — server-only
- Proxy allowlist rejects unknown paths with 404 before contacting backend
- Authorization header never forwarded to browser in response
- No backend code changes
- No secrets committed

Skills used: `frontend-nextjs`, `security-review`, `azure-deployment`, `docs-maintainer`

---

## Phase 19: Live EODHD Smoke Test (staging) ⏸️

**Status: Superseded by Phase 19.1**

Original goal: run a controlled staging smoke test against live EODHD data.
Deferred because EODHD fundamentals (/fundamentals) requires a paid subscription.

---

## Phase 19.1: Free Real Data Provider Stack ✅

**Status: Released on staging (2026-07-11). Partial real-data success. Follow-up Phase 19.2 required.**

Goal: enable real (non-mock) company analysis using only free data sources.
No paid EODHD fundamentals subscription required.

Deliverables:
- [x] `EodhdPriceOnlyProvider` — EODHD `/eod` prices only (free plan); no `/fundamentals` call; warns on missing fundamentals
- [x] `SecEdgarFundamentalsProvider` — ticker→CIK resolution + XBRL companyfacts; 10 us-gaap core concepts; T2 tier; no key
- [x] `TrendSignalEngine` — 1M/3M/6M returns, MA50/MA200 deviations, relative strength; internal labels only; no BUY/SELL/HOLD/WATCH
- [x] `FreeRealSnapshotComposer` — combines DB identity + SEC fundamentals + Stooq/EODHD prices; is_mock=False when any real source contributes; partial success + warnings
- [x] `NewsCatalystProvider` — abstract interface; `SecEdgar8KProvider` (free, T2); `NullNewsCatalystProvider` (safe default)
- [x] Composite providers: `FreeRealProvider` (Stooq + SEC), `EodhdFreeRealProvider` (EODHD /eod + SEC)
- [x] Registered in `FinancialDataService`: `free_real`, `eodhd_free_real`, `eodhd_price_only`, `sec_edgar_fundamentals`
- [x] 64 new offline tests (no network, no API keys)
- [x] Fixtures: `sec_companyfacts_aapl.json`, `sec_tickers_mini.json`
- [x] 831 total tests passing
- [x] Docs: DATA_SOURCES.md, ROADMAP.md, API.md, Readme.md

Constraints enforced:
- No BUY/SELL/HOLD/WATCH in any output
- No price targets, fair values, or upside/downside percentages
- All outputs internal and human-reviewed before publication
- No new .env secrets or API keys required for `free_real` stack
- EODHD fundamentals (paid /fundamentals endpoint) not required for MVP

**Staging smoke test results (2026-07-11):**
- `provider=eodhd_free_real`: partial success — SEC EDGAR XBRL fundamentals (T2) retrieved; EODHD /eod prices not visibly confirmed in T5 source-tier summary during test
- `provider=free_real`: failed on staging — Stooq appears blocked from Azure outbound network; Stooq succeeds from local/non-Azure environments
- `is_mock=False` confirmed for eodhd_free_real run
- Final internal report generation works from partial real (SEC EDGAR) data

**Known gaps for Phase 19.2:**
- EODHD /eod price data must be made visibly present in T5 source-tier summary
- TrendSignalEngine exists but is not yet wired into the main `company_analysis` workflow
- `provider_name` tracking for composite providers needs cleanup in workflow metadata
- Stooq failure must be made non-blocking on Azure; `free_real` should fall back to EODHD price-only when Stooq fails

AAPL expected behavior (free_real, local):
- CIK: 320193 (resolved via SEC company_tickers.json)
- Fundamentals: revenue 383,285 USD_m, net income 96,995 USD_m (FY2023 10-K, T2)
- Price: Stooq OHLCV (T5) → trend signals → `positive/neutral/negative_momentum_candidate`
- is_mock=False; partial warnings for any unavailable source

---

## Phase 19.1 Safety Fix ✅

**Status: Complete (commit 77648b0, 2026-07-12)**

**What was fixed:** `investment_committee_chair` now forces `human_review_required=True` when the safety guard triggers (i.e., when forbidden terms are detected or safety violations fire). Previously, the safety flag could remain `False` even when the committee safety check detected a problem.

**Why this matters:** All internal reports that trigger safety violations must require explicit admin acknowledgement before approval can proceed. This is a non-negotiable invariant.

---

## Phase 22.1 Maintenance — /admin/reports Fix + Homepage Update ✅

**Status: Complete (2026-07-11)**

Commits:
- `ad68026` — restore `/admin/reports` dynamic rendering (Next.js `export const dynamic = 'force-dynamic'`)
- `5241335` — update homepage platform phase text to reflect Phase 22.1 status

These were delivered as direct commits to main following the Phase 22.1 Admin Backtesting UI (tag `v1.22.1`). No new backend logic.

---

## Phase 20: Admin Final Report UI

**Status: In progress (PR recovery)**

Goal: expose Phase 16 final-report generation and validation workflows in admin UI only.

Planned deliverables:
- [x] Report schema/type exposure of Phase 16 metadata fields
- [x] Admin report detail metadata rendering for final report fields
- [x] Admin actions: generate internal final report draft, validate final report
- [x] Optional regenerate-section action
- [x] Reports list shows final-report/review metadata when available
- [x] Browser calls continue through Phase 17 admin proxy
- [x] No public publishing and no BUY/SELL/HOLD/WATCH recommendations or price targets

---

## Phase 21: Playwright Admin Smoke Tests

**Status: Complete**

Goal: add deterministic Playwright smoke tests for admin workflows without changing backend or existing admin UI implementation.

Deliverables:
- [x] Playwright config + admin smoke tests added under `apps/web/tests/e2e/`
- [x] Frontend E2E workflow added for manual/opt-in execution
- [x] Tests default to mock provider/data routes
- [x] Staging E2E remains opt-in only
- [x] Phase 19 live EODHD smoke testing remains pending/deferred

---

## Phase 22: Judge + Backtesting Framework ✅

**Status: Complete**

Goal: Add a non-public internal judge and backtesting framework that evaluates generated internal reports over time, compares thesis development against later reference outcomes, and helps evaluate research quality.

No public investment advice is produced. No BUY/SELL/HOLD/WATCH recommendations, price targets, fair values, or upside percentages are produced. All evaluations are internal historical quality assessments only. CI uses mock provider — no live EODHD or Azure OpenAI required.

Deliverables:
- [x] `backtest_runs`, `backtest_results`, `thesis_tracking_events` DB tables (migration 009)
- [x] `MockHistoricalOutcomeProvider` — deterministic, offline, no API keys required
- [x] `HistoricalOutcomeProvider` abstract interface (live providers addable later)
- [x] `BacktestingService` — create/list/evaluate runs, add reports, summarize
- [x] `ResearchJudgeService` — deterministic quality scoring, safety gate, forbidden-term scan
- [x] API endpoints under `/api/v1/backtesting` (admin/dev only)
- [x] Pydantic v2 schemas with INTERNAL_DISCLAIMER on all responses
- [x] 34 offline pytest tests — no network, no EODHD key, no Azure OpenAI
- [x] Phase 19 live EODHD smoke testing remains pending/deferred

Allowed internal judge statuses (never public recommendations):
`insufficient_data` | `useful_research` | `needs_better_sources` |
`poor_evidence_quality` | `outcome_inconclusive` | `outcome_review_required`

Skills used: `backend-fastapi`, `database-design`, `financial-data`, `investment-domain`, `testing-qa`, `security-review`, `docs-maintainer`

---

## Phase 22.1: Admin Backtesting UI ✅

**Status: Complete**

Goal: Expose the Phase 22 backtesting backend through a minimal admin-only web UI.

All UI is internal-only. No public recommendations, price targets, fair values, or upside percentages are exposed. Mock provider only — no live EODHD in UI. All pages include the mandatory INTERNAL ADMIN USE ONLY disclaimer. Admin proxy (Phase 17) is the only API path; browser never contacts backend directly.

Deliverables:
- [x] `/admin/backtesting` — backtest runs list with table, loading/empty/error states
- [x] Create backtest run form (mock provider only, clearly labelled)
- [x] `/admin/backtesting/[id]` — run detail with metadata, summary stats, results cards
- [x] Evaluate Run button → POST proxy → mock evaluate
- [x] Refresh Results button
- [x] Summary stats (total/completed/failed/avg_judge_score)
- [x] Per-result cards with outcome_json, judge_evaluation_json, warnings, missing_data
- [x] Backtesting nav link added to admin layout
- [x] TypeScript types for all Phase 22 schemas in `types/api.ts`
- [x] API helper functions: `listBacktestRuns`, `createBacktestRun`, `getBacktestRun`, `evaluateBacktestRun`, `listBacktestResults`, `getBacktestSummary`
- [x] Playwright smoke tests (13 tests) — fully mocked, no live staging required
- [x] typecheck, lint, build all clean
- [x] 767 backend tests passing, ruff clean

Skills used: `frontend-nextjs`, `testing-qa`, `docs-maintainer`

---

## Phase 22.3: UI Modernization + Markdown Report Preview ✅

**Status: Complete (2026-07-15)**

Goal: Modernize the web/admin visual experience and make report content readable, **without changing any backend analysis or report-generation logic**. Presentation only.

This is a frontend/UI-only phase. It does not add public publishing, does not change report semantics, and produces no BUY/SELL/HOLD/WATCH recommendations, price targets, fair values, or upside percentages. All outputs remain internal, human-reviewed, and not investment advice.

Deliverables:
- [x] Modern dark theme with fixed product palette (`globals.css`) — independent of OS light/dark
- [x] Subtle animated aurora background (`AnimatedBackground`) — pure CSS, `pointer-events:none`, disabled under `prefers-reduced-motion`
- [x] Glassmorphism UI primitives: `GlassCard`, `StatusPill`, `SafetyBanner`, `AppShell`
- [x] Smooth hover lift / transitions and card fade-in entrance animations
- [x] Safe rendered markdown preview (`MarkdownReportPreview`) using `react-markdown` + `remark-gfm` + `rehype-sanitize` — **no `dangerouslySetInnerHTML`**
- [x] Preview / Raw Markdown toggle preserving the original content for debugging
- [x] Sticky mini table of contents (`ReportSectionNav`) derived from report headings
- [x] Report detail: raw `<pre>` block replaced with the sanitized rendered preview; metadata, statuses, warnings, schema errors, and disclaimers all preserved
- [x] All admin pages (dashboard, run analysis, draft reports, report detail, backtesting, add company) and the public homepage restyled to the dark glass system
- [x] Public homepage rebuilt as a modern landing page that clearly states internal-only / no public reports yet
- [x] All mandatory safety copy preserved verbatim (INTERNAL ADMIN ONLY, NOT INVESTMENT ADVICE, NOT FOR PUBLICATION, HUMAN REVIEW REQUIRED); no Buy/Sell/Hold/Watch/Trade/Publish buttons
- [x] New e2e tests: markdown preview render/raw-toggle, safety copy, mobile-viewport no-overflow, reduced-motion, homepage; local mock backend so SSR report pages render offline (never live staging)
- [x] typecheck, lint, build clean; 55 Playwright tests passing

Skills used: `frontend-nextjs`, `testing-qa`, `docs-maintainer`

---

## Phase 22.3.1: Web Deploy Cache Hardening ✅

**Status: Complete (2026-07-16)**

Goal: Harden the web staging deployment so future frontend deploys reliably serve the newest build — and never silently serve a **stale prerendered homepage**. During the Phase 22.3 release, with `WEBSITE_RUN_FROM_PACKAGE=1` and `alwaysOn=false`, the dynamic `/admin` routes picked up the new build immediately but the statically prerendered `/` kept serving the old page until a manual `az webapp restart`.

Deploy/CI + frontend-verification only. No backend analysis or report-generation logic changed, no financial semantics changed, no auth added, no public publishing, no recommendation language, and no secrets committed.

Deliverables:
- [x] `/api/version` build-metadata endpoint (`app`, `commit_sha`, `build_id`, `build_time`, `environment`) — build identifiers only, never secrets; `force-dynamic` + `no-store`; safe `"unknown"` placeholders when metadata is missing (`src/lib/build-info.ts`)
- [x] `x-ib-build-commit` / `x-ib-build-id` `<meta>` tags embedded in every page `<head>` (root layout) so a stale prerendered `/` is detectable
- [x] Homepage `src/app/page.tsx` set to `force-dynamic` so `/` always reflects the currently-mounted bundle (removes the stale-prerender root cause)
- [x] `deploy-web-staging.yml` bakes `NEXT_PUBLIC_COMMIT_SHA` / `NEXT_PUBLIC_BUILD_ID` / `NEXT_PUBLIC_BUILD_TIME` / `NEXT_PUBLIC_APP_ENV` into the bundle at build time
- [x] Best-effort post-deploy `az webapp restart` — runs only when an optional `AZURE_CREDENTIALS` service principal is configured (Kudu/publish-profile cannot restart the site); skipped cleanly otherwise
- [x] SHA-verified freshness smoke check — requires `/api/version` to report the deployed `github.sha` (3 consecutive matches), `/` + `/admin` to return `200` with the dark-UI marker (`bg-[#060913]`), and `/` to embed the current build commit; a `403` "Site Disabled" is surfaced explicitly; never false-greens on a stale worker
- [x] New e2e tests for `/api/version` (app name, all fields, safe placeholders, no-secret allow-list, `no-store`, homepage build-commit meta)
- [x] typecheck, lint, build clean; Playwright suite passing

Known limitation: the automatic restart activates only once an `AZURE_CREDENTIALS` (or OIDC) service principal is provisioned — currently blocked pending the Azure Owner/RBAC grant. Until then, if the smoke check detects a stale homepage it fails loudly with a `az webapp restart --name ib-stg-web` remediation hint instead of silently passing.

Skills used: `azure-deployment`, `frontend-nextjs`, `testing-qa`, `docs-maintainer`

---

## Phase 19.2: Real Price + Trend Workflow Integration Fix ✅

**Status: Released on staging (2026-07-12, tag `v1.19.2-real-price-trend-workflow`)**

Goal: Make the `free_real` and `eodhd_free_real` providers fully functional as end-to-end real-data analysis paths. Delivered a verified AAPL run with `is_mock=False`, SEC fundamentals, real price data, and trend signals — producing a final internal report with `safety_valid=True`.

Delivered:
- [x] Wired `TrendSignalEngine` into the `company_analysis` workflow (non-fatal, T6)
- [x] EODHD /eod price data visible as T5 source in workflow state + snapshot
- [x] Preserved composite provider tracking (`contributing_providers`, `requested_provider_name`)
- [x] Made Stooq fetch failure non-blocking; falls back to `eodhd_price_only` when Stooq fails
- [x] `free_real` degrades gracefully to SEC-fundamentals-only when price source is unavailable
- [x] AAPL `provider=free_real` produces SEC + price + trend data; final internal report `safety_valid=True`

Skills used: `langgraph-agents`, `financial-data`, `backend-fastapi`, `testing-qa`

---

## Phase 19.2.1: Staging Deploy + Provider Observability Hardening ✅

**Status: Complete — follow-up to Phase 19.2**

Goal: Harden the staging deploy pipeline against false-green health checks and transient Oryx boot failures observed during the Phase 19.2 release, and surface the Stooq→EODHD price fallback in report provider warnings.

Delivered:
- [x] Deploy health-check now confirms the **new** container is serving — `/health` exposes `commit_sha` / `build_id` (from a bundled `build_info.json`) and the smoke check requires 3 consecutive SHA-matched responses before passing
- [x] Oryx/runtime boot-failure detection in the deploy workflow (missing `uvicorn` / broken `antenv` / container exit) with clear remediation instead of silent success
- [x] Stooq→EODHD price fallback reason surfaced into `provider_warnings` and the draft report's Provider Warnings section (`summarize_price_provider_warning`)
- [x] Fixed pre-existing `scoring_engine` `TypeError` when `sector` is `None` (SEC EDGAR profiles omit sector)
- [x] Documented the intentional `--workers 1` on B1 staging configuration (see `docs/DEPLOYMENT.md`)
- [x] Backend tests added (build metadata, fallback surfacing, sector=None scoring); no public recommendations, no paid EODHD fundamentals, no secrets

Constraints held: no Clerk auth, no news/catalyst system, no public publishing, no paid plans, no broad discovery, no provider rewrites beyond warning propagation.

Skills used: `azure-deployment`, `financial-data`, `backend-fastapi`, `testing-qa`, `docs-maintainer`

---

## Phase 19.3: SEC Fundamentals Normalization + Report Completeness Upgrade ✅

**Status: Delivered (2026-07-13)**

### Why this phase

The Phase 19.2 `free_real` report was a **technical validation artifact, not an investor-grade research report**. The pipeline worked end-to-end — `is_mock=false`, SEC identity (T2), EODHD/Stooq prices (T5), TrendSignalEngine momentum (T6), `safety_valid=true` — but the report carried **no financial fundamentals**. It said *"No financial fundamentals sourced at this phase"*, `internal status=research_incomplete`, `valuation_readiness=not_ready`, and its financial analysis was empty because the raw SEC XBRL datapoints were never mapped into the income-statement / cash-flow / balance-sheet fields the analysis agents look for.

### What Phase 19.3 fixes

Goal: make the `free_real` report **financially useful** by extracting, normalizing and injecting SEC EDGAR XBRL fundamentals into the company-analysis workflow — moving from *"identity and price data only"* toward *"SEC-derived fundamentals available; financial trend analysis partially ready; valuation guard can evaluate readiness using real financial data."*

Delivered:
- [x] New `sec_fundamentals_normalizer` module — pure, offline, maps us-gaap companyfacts into normalized metrics
- [x] Income statement: revenue, gross profit, operating income, net income, EPS basic/diluted
- [x] Cash flow: operating cash flow, capital expenditures, free cash flow (= OCF − capex when both exist)
- [x] Balance sheet: total assets, total liabilities, shareholders' equity, cash & equivalents, short/long-term debt, total debt
- [x] Derived: gross/operating/net margin, ROE, debt-to-equity, FCF margin, revenue/net-income/FCF YoY growth
- [x] Metadata: fiscal year/period, form type, filed date, accession number, `T2_regulator_or_gov` tier
- [x] Latest annual (10-K/20-F, `fp=FY`) preferred; latest 10-Q used as fallback **with a warning**; YoY skipped on quarterly fallback
- [x] Normalized fundamentals injected into the `free_real` / `eodhd_free_real` snapshot (`fundamentals_summary`)
- [x] `FinancialDataAgent` now recognizes ~10 sourced financial categories and narrates revenue/growth/net income/margins/cash flow/balance sheet/debt
- [x] `ValuationGuardAgent` moves `not_ready → partial` when core statement inputs are available from T1/T2, with **more specific blockers**
- [x] Report no longer says *"No financial fundamentals sourced at this phase"* when SEC facts exist
- [x] 22 offline tests (`test_phase19_3_sec_fundamentals_normalization.py`); AAPL fixture enriched with gross profit, operating income, capex, cash and prior-year OCF

### Safety constraints held (unchanged)

- **EBITDA is never fabricated** — left missing (with a warning) when depreciation & amortization is unavailable
- **No market cap / EV** — not computed without price + shares outstanding (shares absent from SEC statement data)
- **Annual data is labelled annual, never mislabelled TTM**
- No BUY/SELL/HOLD/WATCH, no price target, fair value, intrinsic value, upside/downside, or undervalued/overvalued label
- Valuation conclusions remain **blocked**; `partial` means "financial inputs available, conclusions withheld"
- `human_review_required` stays true; outputs are internal-only; `schema_valid` may still be false (financial completeness materially improves without faking schema validity)

### What remains after Phase 19.3

- **Phase 19.4** — identity / sector / market-metric enrichment (sector, industry, ISIN/LEI, 52-week range, market cap, shares outstanding, enterprise value)
- **Phase 23** — admin auth hardening
- **Phase 24** — news / catalyst discovery
- **Phase 25** — real market candidate discovery
- **Phase 26** — public report publishing
- **Phase 27** — user accounts / watchlists
- **Phase 28** — paid plans
- **Phase 29** — personalized reports
- **Phase 30** — monitoring / alerts / thesis tracking

Skills used: `financial-data`, `investment-domain`, `langgraph-agents`, `backend-fastapi`, `testing-qa`, `docs-maintainer`

---

## Phase 19.3.1: SEC Freshness + Review Consistency Fix ✅

**Status: Delivered (2026-07-13)**

Hotfix for three defects found in the first Phase 19.3 AAPL `free_real` report:

- **Stale fiscal year (FY2018)** — the SEC normalizer selected the value of the **first** matching us-gaap alias concept, returning immediately. Apple retains a stale `Revenues` tag (data stops at FY2018) that shadowed the current `RevenueFromContractWithCustomerExcludingAssessedTax` tag (FY2019+), so the report's "latest annual" was FY2018. **Fix:** `_select_metric` now gathers annual candidates across **all** alias concepts and picks the latest fiscal year (filed date breaks ties → an amended/restated 10-K/A supersedes the original); full-year periods are preferred over embedded Q4 slices for flow concepts; a **stale-data warning** is emitted when the selected annual year is more than two fiscal years old.
- **`human_review_required` inconsistency** — the Investment Committee markdown said *"Human review required: False"* while the page/report metadata said *Yes*. The committee chair computed review-required only for watchlist/ready statuses, so `research_incomplete` (with good source quality, valid citations, non-mock data) came out `False`. **Fix:** review-required is now fail-safe — true for mock, invalid schema, non-clean citation status, weak/insufficient source quality, `not_ready`/`partial` valuation, blocking gaps, or any research-queue status; the embedded summary is (re)composed from the final canonical value, including after a safety downgrade.
- **Contradictory financial wording** — bear-case and risk sections still said *"all core financial fundamentals are missing"* and listed revenue/net income/cash flow/debt as *"none sourced"* even though SEC-normalized statement metrics were present. **Fix:** when statement fundamentals are sourced but valuation inputs are not, both sections now say completeness is **partial**, acknowledge the sourced revenue/net-income/cash-flow/balance-sheet metrics, and name only the genuinely missing inputs (EBITDA, market cap, enterprise value, …).

Safety unchanged: `safety_valid` stays true, `human_review_required` stays true, valuation stays `partial` with conclusions blocked, `schema_valid` may still be false. No BUY/SELL/HOLD/WATCH, price target, fair value or upside. No paid EODHD `/fundamentals`.

Tests: `test_phase19_3_1_sec_freshness_review_consistency.py` (14 offline tests) — freshness selection, filed-date tiebreaker, quarterly fallback + warning, stale-year warning, wording consistency, human-review consistency, and safety guarantees. 920 backend tests passing.

Skills used: `financial-data`, `langgraph-agents`, `backend-fastapi`, `testing-qa`, `docs-maintainer`

---

## Phase 19.4: Identity + Sector + Market-Metric Enrichment ✅

**Status: Delivered (2026-07-14)**

Goal: Close the remaining company-identity and market-derived gaps that Phase 19.3 intentionally left open, so the `free_real` report reaches broader completeness without paid EODHD `/fundamentals`.

Two new pure enrichment modules feed the `free_real` / `eodhd_free_real` snapshot after SEC fundamentals and price history are available:

- **`company_profile_enrichment.py`** — assembles identity/profile from the DB record, SEC EDGAR submissions (website, SIC industry, country — T2) and a best-effort GLEIF LEI lookup (T2). Sector is taken from the DB when present, otherwise **inferred** from the SEC SIC classification and tagged `T6_model_estimate` (labelled "derived"). A GLEIF LEI is only accepted when its legal name matches the company (guards against wrong-entity attribution). **LEI, ISIN, IPO date are never fabricated** — left missing with a warning when unavailable.
- **`market_metrics_enrichment.py`** — derives, only when the required inputs exist:
  - latest close + **52-week high/low** (from free price history, T5)
  - **shares outstanding** from SEC `dei:EntityCommonStockSharesOutstanding` (T2)
  - **market cap** = latest close × shares (derived estimate, T6)
  - **enterprise value** = market cap + total debt − cash (derived estimate, T6)
  - **P/E** = market cap / net income, else latest close / diluted EPS (derived estimate, T6)
  - profit margin / operating margin / ROE mapped from **annual** SEC figures (never mislabelled TTM)

Derived market cap / EV / P/E are labelled DERIVED ESTIMATES (T6) with cited inputs — internal review aids, never official figures or a valuation conclusion. **EBITDA, EV/EBITDA and beta are never fabricated** and stay missing. Resolved fields are pruned from the snapshot's `missing_fields`, so the AAPL `free_real` report's missing-information count drops materially. The `FinancialDataAgent` now recognises market cap / EV / P/E as available categories and narrates them as derived estimates; the `ValuationGuardAgent` recognises the derived market metrics but still blocks every valuation conclusion (EBITDA and validated market inputs remain absent → readiness stays `partial`). The report markdown gains a **Company Snapshot** identity/profile block and a **Market Metrics (Derived — Internal)** section.

Safety unchanged: `safety_valid` stays true, `human_review_required` stays true, valuation stays `partial` with conclusions blocked, `schema_valid` may still be false. No BUY/SELL/HOLD/WATCH, price target, fair value or upside. No paid EODHD `/fundamentals`; no broad market discovery.

Tests: `test_phase19_4_identity_sector_market_metrics.py` (24 offline tests) — shares/close/52-week extraction, guarded market cap / EV / P/E derivation, no EBITDA/EV-EBITDA/beta fabrication, source-tier tagging, missing-info reduction, valuation-readiness blocking, safety, graceful degradation, and mock/19.3 regression guards. 944 backend tests passing.

Skills used: `financial-data`, `investment-domain`, `langgraph-agents`, `backend-fastapi`, `testing-qa`, `docs-maintainer`

---

## Phase 19.4.1: Enrichment Completeness Consistency Fix ✅

**Status: Delivered (2026-07-14)**

Goal: Fix consistency issues found after the Phase 19.4 AAPL `free_real` staging smoke test. The report correctly showed `provider=free_real`, LIVE DATA, LEI, sector, SEC financials, derived market cap / EV / P/E, 52-week range, shares outstanding, `safety_valid=true` and `human_review_required=true` — **but** it still inconsistently listed some *enriched-and-present* fields as missing/blocking:

- Company Snapshot showed the LEI, yet the Investment Committee / Research Completeness still called `identity.lei` a blocking gap.
- Source Quality still recommended *"Obtain LEI"* even when the LEI was present.
- Derived market metrics and sector classification were still surfaced as gaps.

Root cause: `research_completeness_agent` derived its blocking gaps from the schema draft, which is built from the **raw provider profile** and never carries the Phase 19.4 enrichment; and `source_quality_agent` appended the "Obtain LEI from GLEIF" recommendation **unconditionally**.

Minimal consistency-layer fixes (no workflow rewrite):

- **`research_completeness_agent.py`** — new `_enriched_present_fields()` derives, from the *enriched* company snapshot, which schema field entries are already satisfied (identity `lei`/`isin`/`sector_classification`, and `snapshot_financials` market cap / EV / revenue / net income / total debt / cash). A present enriched field is no longer reported as a blocking gap or missing required field, and identity next-steps (`obtain LEI` / `confirm ISIN`) are dropped when already satisfied. Genuinely-absent fields (ISIN, EBITDA) stay gaps.
- **`source_quality_agent.py`** — the *"Obtain LEI from GLEIF API"* recommendation is now gated on `identity.lei` actually being missing. When market cap / EV are present only as derived estimates, it recommends *replacing the derived T6 estimates with a primary source before publication* — without ever claiming the metric is unavailable.

Everything downstream (Bear Case key-unknowns, Risk Agent regulatory checks, Committee open questions / next steps, Final Report provenance) already reads the enriched snapshot, so it becomes consistent transitively once these two roots are fixed.

Safety unchanged: derived market metrics stay internal T6 estimates, valuation readiness stays `partial` with all conclusions blocked, `human_review_required` stays true, `schema_valid` may still be false. No BUY/SELL/HOLD/WATCH, price target, fair value or upside. No fabricated LEI/ISIN/EBITDA/beta.

Tests: `test_phase19_4_1_enrichment_completeness_consistency.py` (20 offline tests) — LEI/sector/market-metric present-vs-absent consistency across missing fields, blocking gaps, source-quality recommendations and committee open questions; valuation stays partial; human review stays true; no forbidden output; provider=mock behaviour unchanged. 964 backend tests passing.

Skills used: `investment-domain`, `langgraph-agents`, `backend-fastapi`, `testing-qa`, `docs-maintainer`

### What remains after Phase 19.4.1

Genuinely-missing data (kept honestly missing, never fabricated): **ISIN, website, IPO date, EBITDA, EV/EBITDA, beta, dividend yield, book value per share, current ratio**. Plus larger scope:

- **Phase 24** — news / catalyst discovery (8-K, press releases, optional free news)
- **Phase 25** — market candidate discovery (broad screening beyond seeded tickers); peers / governance
- **Phase 26** — public report publishing
- **Phase 23** — admin auth hardening
- **Phase 27–29** — user accounts, paid plans, personalized reports
- **Phase 30** — monitoring / alerts / thesis tracking

---

## Phase 23: Admin Auth Hardening

**Status: Not started**

Goal: Replace staging Basic Auth with proper admin-only access control. This phase is about protecting the admin UI — not public user accounts.

Deliverables:
- [ ] Clerk integration for admin routes (allowlist-based)
- [ ] Admin route-level authentication middleware
- [ ] Remove reliance on `STAGING_BASIC_AUTH` for admin access
- [ ] CI/CD secrets rotation and Key Vault cleanup
- [ ] Access audit trail for admin actions

Skills to use: `security-review`, `backend-fastapi`, `frontend-nextjs`

---

## Phase 24: News + Catalyst Discovery Agent

**Status: Not started**

Goal: Add a news and catalyst discovery agent that fetches recent 8-K filings, press releases, and optional news signals for research candidates. Surface catalyst signals in the analysis workflow.

Deliverables:
- [ ] `NewsCatalystAgent` node in `company_analysis` workflow
- [ ] Expand `SecEdgar8KProvider` to parse and classify filing types
- [ ] Optional: integrate free news API (GDELT, NewsData, or Alpha Vantage News) as T5 source
- [ ] Catalyst scoring incorporated into `ScoringEngine`
- [ ] Catalyst signals visible in final internal report
- [ ] All news data stays T4/T5 — never promoted to T1/T2

Skills to use: `financial-data`, `langgraph-agents`, `backend-fastapi`, `testing-qa`

---

## Phase 25: Real Market Candidate Discovery Engine

**Status: Not started**

Goal: Replace mock/EODHD-search-based discovery with a real market-wide candidate ranking pipeline using momentum, fundamentals, catalysts, and sector context.

Deliverables:
- [ ] Market-wide candidate screener using real price + SEC data
- [ ] Multi-signal ranking: price momentum + fundamentals quality + catalyst recency + sector context
- [ ] Automated candidate queue: companies surfaced by discovery enter a review queue
- [ ] Admin can review, promote, or reject candidates
- [ ] No automatic progression to analysis without admin approval
- [ ] Source tier enforced: T5 for aggregated data; T2 for SEC-derived data

Skills to use: `financial-data`, `backend-fastapi`, `investment-domain`, `testing-qa`

---

## Phase 26: Public Report Publishing Website

**Status: Not started**

Goal: Build the first public-facing pages for approved internal reports. Only human-approved reports are ever shown publicly.

Deliverables:
- [ ] Public report list page (approved reports only)
- [ ] Public report detail page
- [ ] Admin publish action (`POST /api/v1/admin/reports/{id}/publish`)
- [ ] Reports must pass: `safety_valid=True`, `review_status=approved_internal`, admin explicit publish action
- [ ] No price targets, fair values, or BUY/SELL/HOLD/WATCH on public pages
- [ ] All public pages include regulatory disclaimer ("not investment advice")
- [ ] SEO: `sitemap.xml`, metadata, OpenGraph tags
- [ ] No personalized content on public pages

Skills to use: `frontend-nextjs`, `backend-fastapi`, `security-review`, `investment-domain`

---

## Phase 27: User Accounts + Watchlists

**Status: Not started**

Goal: Allow users to create accounts, follow companies, and save watchlists. No personalized research yet.

Deliverables:
- [ ] User authentication (Clerk)
- [ ] User dashboard
- [ ] Company watchlists
- [ ] Notification preferences (email opt-in for new approved reports)
- [ ] User data strictly separated from public research tables

Skills to use: `backend-fastapi`, `frontend-nextjs`, `security-review`

---

## Phase 28: Paid Plans + Stripe

**Status: Not started**

Goal: Introduce subscription tiers that gate access to premium features such as earlier report access and custom report requests.

Deliverables:
- [ ] Stripe integration for subscription management
- [ ] Free / Paid tier differentiation
- [ ] Usage limits per tier
- [ ] Admin billing dashboard (basic)
- [ ] No financial advice unlocked by any paid tier — plans gate access, not advice quality

Skills to use: `backend-fastapi`, `frontend-nextjs`, `security-review`

---

## Phase 29: Personalized Research Reports

**Status: Not started (Version 2)**

Goal: Users on paid plans can request custom internal research reports based on their preferences and areas of interest. These are still human-approved before delivery.

Important constraints:
- Personalized reports are research candidate summaries — not personalized investment advice
- Reports must still pass safety gate and human review
- User portfolio data must never leak into public tables
- Output clearly labeled: "internal research candidate" / "positive momentum candidate" / "candidate for human research review"

Deliverables:
- [ ] User preference storage (sectors, regions, themes)
- [ ] Portfolio Fit Agent skeleton
- [ ] Personalized candidate filtering from discovery queue
- [ ] Custom report request queue
- [ ] Private user dashboard with personalized report history

Skills to use: `backend-fastapi`, `frontend-nextjs`, `langgraph-agents`, `security-review`, `investment-domain`

---

## Phase 30: Monitoring, Alerts + Thesis Tracking

**Status: Not started**

Goal: Track research thesis performance over time. Alert admin when monitored companies have significant events. Enable ongoing quality feedback to the research system.

Deliverables:
- [ ] Thesis tracking events table (Phase 22 foundation)
- [ ] Automated monitoring workflow (Azure Functions scheduled trigger)
- [ ] Event-triggered re-analysis for monitored companies
- [ ] Admin alert queue for significant events (8-K filings, price moves)
- [ ] Backtesting result integration to measure research quality over time
- [ ] Backtesting does not predict future results — disclaimer enforced

Skills to use: `langgraph-agents`, `azure-deployment`, `backend-fastapi`, `investment-domain`

---

## Out of Scope (All Versions)

- Broker account integration
- Automatic trade execution
- High-frequency or algorithmic trading
- Mobile app (not in current roadmap)
- Social or community features
- Guaranteed investment returns
- Automated public BUY/SELL/HOLD/WATCH recommendations
- Unreviewed price targets or fair values on public pages
