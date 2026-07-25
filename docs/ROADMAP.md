# Roadmap

## Current State: Phase 29B Filing & Regulator Connector Batch 1 ✅ — the first real filing/disclosure connectors are wired into the Phase 29A framework, prioritising **evidence quality over provider count**. `sec_edgar` and `company_ir` become **live-evidence** connectors: `SecEdgarConnector.fetch_filings` maps already-fetched SEC filing metadata into tiered `EvidenceItem`s (transport `T2_regulator_or_gov` / content `T1_primary_filing`, both preserved), is exchange-aware (non-US → honest `source_not_eligible` gap via Phase 27.1A `is_sec_eligible`, never a wrong-CIK lookup) and attaches a `primary_filing_unavailable` gap because full filing text is not fetched yet; `CompanyIrConnector.fetch_events` wraps issuer press releases as `T1_primary_company_source` evidence (`company_ir_press_release`, URL secrets stripped, bounded, media URLs never cited). Six regulator connectors — **SEDAR+ (CA), ASX (AU), UK FCA NSM (GB), Euronext, Deutsche Börse, Nordic** — become **`scaffolded`** (a new lifecycle status): a real `ScaffoldConnector` class returns honest `connector_scaffolded` `SourceGap`s and **never a fabricated filing/JORC/RNS**. A `collect_company_source_evidence` service runs the connectors over **already-fetched deterministic data** (no report-time network calls) and — when `SOURCE_CONNECTOR_ENABLED=true` (OFF by default) — injects bounded, tiered connector evidence + honest gaps into the single-company evidence pack + council; discovery packs get run-level source-framework gaps only. New read-only **admin/internal** endpoint `POST /api/v1/sources/evidence-preview` (identity-only request, **no URL field**, offline unless the flag is set, then bounded live fetch to SEC EDGAR + curated issuer feeds only). New config `SOURCE_CONNECTOR_ENABLED`/`SOURCE_CONNECTOR_MAX_ITEMS_PER_SOURCE`/`SOURCE_CONNECTOR_TIMEOUT_SECONDS` (conservative defaults). This phase can close with only SEC + company_ir producing real evidence and non-US connectors returning honest gaps. **No** macro/commodity/policy (29C), event-trigger (29D), or translation (30) connectors; **no** auth change, **no** public publishing, **no** recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` and `publication_ready=false` unchanged; no publish route. **No DB migration** (still 011). Backend **1910 passing** (+26 Phase 29B), ruff clean, mypy clean on touched files (pre-existing ~71-error baseline unchanged). **Next connector phases: 29B.x (live fetch for the scaffolded regulator connectors + SEC full-text), 29C (macro/commodity/policy), 29D (event-trigger/patents/local press), 30 (translation/local-language agents).**

### Previously: Phase 29A Source Registry + Connector Framework ✅ — the LLM councils (Phase 28A/28B) need better evidence, so this phase builds the **unified source/provider framework before wiring many external sources one by one**. New internal `apps/api/app/services/sources/` package: a canonical **source taxonomy** (six tiers `T1_primary_filing`…`T6_model_estimate`, encoding the SEC **transport-vs-content** rule — SEC EDGAR transport = `T2_regulator_or_gov`, a filing pulled through it = `T1_primary_filing`; evidence items carry both `provider_transport_tier` and `content_source_tier`); framework `EvidenceSource`/`EvidenceItem` models (tier-validated, credential-bearing URL query params stripped before storage, bounded excerpts, model-derived values kept at `T6_model_estimate`); a safe `SourceConnector` interface (`search_company` / `fetch_filings` / `fetch_events` / `fetch_macro_context` / `healthcheck`) whose failures degrade to a warning + `SourceGap` and **never crash a report or discovery run**; a code-defined `SourceRegistry` with **6 enabled/migrated** sources (`sec_edgar`, `company_ir`, `gleif`, `eodhd`, `stooq`, `gdelt`) and **25 planned placeholders disabled by default** (SEDAR+, ASX, UK FCA NSM, Euronext, Deutsche Börse, Nordic, USGS, IEA, IRENA, EIA, ENTSO-E, World Bank Pink Sheet, FRED, IMF, Eurostat, USTR/TARIC, USAspending, EU TED, UN Comtrade, national stats/central banks, OpenBB, Google Patents, USPTO, EPO Espacenet, local-language business press); and normalized `SourceGap` reporting so missing coverage is **explicit, never silently absent**. Two read-only, **secret-free** endpoints: `GET /api/v1/sources/registry` and `GET /api/v1/sources/health` (deterministic, network-free connector health — never a secret or raw upstream error body), plus a read-only `/admin/sources` admin page. Existing evidence packs now strip URL secrets and can carry planned-source gaps in `known_gaps`; the final-report `source_summary_json` gains an additive `source_framework` block. Phase 29A is the **framework only** — it does NOT connect every source, add translation/local-language ingestion, change auth, add public publishing, or add any recommendation/rating/price-target; `human_review_required=true` and `publication_ready=false` are unchanged and no publish route is added. **No DB migration** (still 011). Backend **1884 passing** (+24 Phase 29A), ruff + mypy clean on touched files; frontend +4 Playwright, typecheck/lint/build clean. **Next connector phases: 29B (filing/regulator connectors — wire SEDAR+/ASX/FCA NSM/Euronext/Deutsche Börse/Nordic), 29C (macro/commodity/policy — USGS/IEA/IRENA/EIA/ENTSO-E/World Bank/FRED/IMF/Eurostat/USTR-TARIC/USAspending/EU TED/UN Comtrade/OpenBB), 29D (event-trigger/patents/local press), 30 (translation/local-language agents).**

## Previous State: Phase 23 Admin/Auth Hardening ✅ — the internal admin surface is now locked down before any external sharing. `/admin/*` pages and the `/api/admin/proxy/*` API require an authenticated, **allowlisted** admin. Auth is a dependency-free, HMAC-signed **httpOnly** session cookie (`AUTH_SECRET`) issued after **GitHub OAuth** sign-in (the OAuth secret is used only server-side in the token exchange; the access token is read once for the verified email and discarded — never stored or forwarded). Authorization is an env allowlist (`ADMIN_ALLOWED_EMAILS`). Enforcement is defense-in-depth: the Next 16 **Proxy** (`apps/web/src/proxy.ts`, the renamed `middleware`) redirects unauthenticated `/admin/*` to `/login?callbackUrl=…` and non-allowlisted users to `/unauthorized`, and returns **401/403** on the proxy API; the admin proxy **route handler independently re-checks** auth + allowlist before attaching the backend Basic Auth, and adds advisory `X-IB-Admin-Email`/`X-IB-Admin-Name` audit headers (never the OAuth token). New `/login` + `/unauthorized` pages; the admin shell shows the signed-in identity + **Sign out**. Backend **Basic Auth is retained as server-to-server defense** (extracted to `install_staging_basic_auth`; the identity headers are never trusted for auth — read only after Basic Auth passes). Local/CI use `AUTH_TEST_MODE` for deterministic, offline sign-in (hard-gated; 404 in prod). **No migration, no public publishing, no recommendation output, no weakened safety** — all internal-only / not-investment-advice / human-review disclaimers preserved. Backend +13 tests (1236 total, ruff clean); frontend +15 auth Playwright specs (shared auth fixture; existing admin specs sign in first); typecheck + lint + build clean. Post-merge hotfixes were **web-only** (API unchanged at `07f10f2`, final web `86461ef`): the auth-aware web deploy smoke check was corrected after `/admin` began redirecting logged-out users (PR #31), the discovery candidate **Detail** action visibility was restored (PR #32), and auth redirects were anchored to `AUTH_URL` so staging never redirects users to the internal `0.0.0.0:8080` origin (PR #33). **Next recommended phase: Phase 26 — Final Report Schema Completion / Publication-Readiness Pipeline — move generated reports from `schema_valid=false` to `schema_valid=true` without weakening safety gates, recommendation restrictions, or the human-review requirement. Phase 25.2 — Durable Discovery Job Queue (replace the process-local FastAPI `BackgroundTasks` with a durable queue if needed) remains a later infrastructure-backlog item.**

## Previous State: Phase 25.1 Async Discovery Run Execution ✅ — operational hardening on Phase 25. `POST /api/v1/market-discovery/runs` now **creates the run and returns a `run_id` immediately** (`status="pending"`), then processes the universe in the background (FastAPI `BackgroundTasks` with a *fresh* DB session, progress committed after every ticker) while the `/admin/discovery` UI **polls run status** and shows a live progress bar / counts until a terminal status. This removes the gateway/proxy `504` a multi-ticker `free_real` run could hit under the single B1 worker. No product-scope, safety, schema, or migration change — statuses `pending→running→completed|completed_with_warnings|failed`, oversized/empty universe still rejected (422) before any background work, `human_review_required=true` / `is_public=false` preserved. `BackgroundTasks` are process-local (not durable across restart) — acceptable for this MVP; a durable queue is a later phase. 27 new backend + 9 new Playwright tests. Underlying: Phase 25 Real Market Candidate Discovery — moves InvestingBuddy from manual single-ticker analysis into a **bounded, internal-only market discovery workflow**. Instead of entering tickers one at a time, an admin scans a controlled universe (curated seed or manual comma-separated tickers) and gets an **internal research-candidate queue** ranked by an internal prioritization score. New `discovery_runs` + `discovery_candidates` tables (migration 010), a deterministic `discovery_scoring_service` (momentum 30% + catalyst 25% + fundamentals 20% + source-quality 15% + completeness 10% − risk penalty), a `discovery_signal_extractor` that reuses the tested company-analysis workflow per ticker (injectable/offline for CI), a `market_discovery_service` orchestrator (universe validated against `DISCOVERY_MAX_UNIVERSE_SIZE` so an accidental full-market scan is rejected; per-ticker failures are non-blocking), 6 admin-only `/api/v1/market-discovery/*` endpoints, and a dark-glass `/admin/discovery` UI with a start-run form, runs table, ranked candidate queue and inline candidate detail with a "Run Full Analysis" button. This is **NOT** a recommendation engine: no BUY/SELL/HOLD/WATCH, no price targets, no fair value/upside/downside, no public publishing; the candidate score is an internal prioritization signal only and every candidate is `human_review_required=true` / `is_public=false`. 51 backend + 15 Playwright tests (Phase 25); +27 backend +9 Playwright (Phase 25.1 async). See the Phase 25 section below. **Phase 23 — Admin/Auth hardening is now complete (see the Current State above); the next recommended phase is Phase 26 — Final Report Schema Completion.**

## Previous State: Phase 22.3.1 Web Deploy Cache Hardening — a deploy/CI + frontend-verification hotfix on top of Phase 22.3. Fixes an operational issue found during the Phase 22.3 release: with `WEBSITE_RUN_FROM_PACKAGE=1` and `alwaysOn=false`, the statically prerendered homepage `/` could keep serving the old build after a deploy until a manual `az webapp restart`, while dynamic `/admin` routes updated immediately. Adds a `/api/version` build-metadata endpoint and an `x-ib-build-commit` `<meta>` tag, renders the homepage dynamically so `/` reflects the mounted bundle, bakes `NEXT_PUBLIC_*` build metadata in CI, best-effort restarts `ib-stg-web` after deploy (when an optional `AZURE_CREDENTIALS` service principal exists), and adds a SHA-verified smoke check that fails loudly if `/api/version`, `/`, or `/admin` are stale. No backend analysis or report-generation logic changed; no financial semantics changed; no auth, no public publishing, no recommendation language, and no secrets. See the Phase 22.3.1 section below.

## Previous State: Phase 22.3 UI Modernization + Markdown Report Preview — a frontend/UI-only phase on top of the Phase 19.4.1 data stack. The web/admin experience is modernized with a dark glassmorphism design system, a subtle animated aurora background (disabled under `prefers-reduced-motion`), and reusable glass UI primitives. Report content is now rendered through a **safe markdown preview** (`react-markdown` + `remark-gfm` + `rehype-sanitize`, no `dangerouslySetInnerHTML`) with a Preview/Raw toggle and a sticky mini table of contents, replacing the raw `<pre>` block. No backend analysis or report-generation logic changed; no public publishing was added; all mandatory internal-only / not-investment-advice / human-review disclaimers are preserved verbatim, and no BUY/SELL/HOLD/WATCH, price target, fair value or upside is produced. See the Phase 22.3 section below.

## Previous State: Phase 19.4.1 Enrichment Completeness Consistency — a hotfix on top of Phase 19.4. After the Phase 19.4 AAPL `free_real` smoke test, enriched fields that were present in the Company Snapshot (LEI, sector classification, derived market cap / EV / P/E / 52-week range, shares outstanding) were still being reported as **missing / blocking gaps** and still triggered *"Obtain LEI"* recommendations, because `research_completeness_agent` derived its gaps from the raw-profile schema draft (which never carries enrichment) and `source_quality_agent` recommended obtaining the LEI unconditionally. Phase 19.4.1 makes the completeness layer consume the enriched snapshot: a present enriched field is no longer a missing field, a blocking gap, or an "obtain it" next-step — while genuinely-absent fields (ISIN, EBITDA, EV/EBITDA, beta, IPO date, website) stay gaps and nothing is fabricated. Derived market metrics remain labelled **internal T6 estimates**, valuation readiness stays `partial` with all conclusions blocked, `human_review_required` stays true, and `schema_valid` may still be false. No BUY/SELL/HOLD/WATCH, price target, fair value or upside.

### Phase 19.4 (underlying): Identity + Sector + Market-Metric Enrichment — builds on Phase 19.3.1. Two pure enrichment modules feed the `free_real` snapshot: `company_profile_enrichment` fills sector (DB or **inferred** from SEC SIC, T6), industry/website (SEC, T2) and LEI (GLEIF, T2, name-guarded) — LEI/ISIN/IPO date are never fabricated; `market_metrics_enrichment` derives latest close + **52-week range** (T5), **shares outstanding** (SEC DEI, T2), and **market cap / enterprise value / P/E** as DERIVED ESTIMATES (T6, cited inputs) only when their inputs exist. EBITDA, EV/EBITDA and beta are never fabricated. Resolved fields are pruned from `missing_fields` (AAPL missing-info count drops materially); the FinancialDataAgent narrates the derived metrics and the ValuationGuardAgent recognises them but still blocks every valuation conclusion (readiness stays `partial`). The report markdown gains identity/profile and **Market Metrics (Derived — Internal)** sections. Underlying Phase 19.3(.1): SEC EDGAR XBRL companyfacts normalized into income-statement / cash-flow / balance-sheet metrics + derived margins/ROE/debt-to-equity/YoY growth with latest-annual freshness selection. No paid EODHD `/fundamentals`, no broad discovery. Subsequently delivered: Phase 24 News-Catalyst, Phase 25 discovery, and Phase 23 Auth — all complete.

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

## Phase 23: Admin Auth Hardening ✅

**Status: Complete** — delivered as an authenticated, allowlisted admin session (no migration). See the **Current State** summary at the top of this document, `docs/SECURITY.md`, and `docs/DECISIONS.md` ADR-012 for the full design.

Goal: Protect the admin UI with proper admin-only access control before any external sharing — this phase is about the admin surface, not public user accounts.

Delivered:
- [x] Authenticated admin access to `/admin/*` and `/api/admin/proxy/*` — logged-out users are redirected to `/login`, non-allowlisted users to `/unauthorized` (401/403 on the proxy API); sign-out clears the session and re-blocks `/admin`
- [x] **GitHub OAuth** sign-in + a dependency-free HMAC-SHA256-signed httpOnly session cookie (`AUTH_SECRET`) — chosen over Clerk to avoid a `next-auth` v5 / Next 16 beta dependency on a security-critical phase (ADR-012)
- [x] Env allowlist authorization (`ADMIN_ALLOWED_EMAILS`); route-level enforcement via the Next 16 **Proxy** (`src/proxy.ts`) plus an independent re-check in the admin proxy route
- [x] Advisory `X-IB-Admin-Email`/`X-IB-Admin-Name` audit headers for mutating admin actions (attached only after backend Basic Auth passes; never trusted for auth)
- [x] Backend Basic Auth (`STAGING_BASIC_AUTH`) retained as server-to-server defense, now behind the admin session
- [x] `AUTH_TEST_MODE` deterministic offline dev/CI sign-in (hard-gated; 404 in prod); 13 backend + 15 auth Playwright specs

Post-merge hotfixes (web-only, API unchanged at `07f10f2`):
- [x] Auth-aware web deploy smoke check — corrected after `/admin` began redirecting logged-out users (PR #31)
- [x] Discovery candidate **Detail** action visibility restored (PR #32)
- [x] Canonical auth redirect origin — anchored to `AUTH_URL` so staging never redirects users to the internal `0.0.0.0:8080` (PR #33; final web `86461ef`)

Deferred (future, out of Phase 23 scope): Microsoft Entra ID option; moving the allowlist to the database; a persisted per-action audit trail; full Key Vault / CI secret-rotation cleanup.

Skills used: `security-review`, `backend-fastapi`, `frontend-nextjs`, `docs-maintainer`

---

## Phase 24: News + Catalyst Discovery Agent ✅

**Status: Delivered (2026-07-16)**

Goal: Add a source-backed news + catalyst discovery subsystem that answers "why now?" — recent SEC filing events, press releases, and optional news — classified into catalyst categories/directions/strengths with explicit evidence quality, surfaced in the internal report. No recommendations, price targets, fair values, or upside/downside.

Deliverables:
- [x] Catalyst data contracts + enums (`app/schemas/catalyst.py`): `CatalystEvent`, `CatalystDiscoveryResult`, `CatalystSummary`, `NewsItem`, and `CatalystCategory` / `CatalystDirection` / `CatalystStrength` / `EvidenceStrength` / `CatalystCoverageStatus`
- [x] `SecRecentFilingsProvider` (T2) — recent 8-K/10-Q/10-K/6-K/20-F/DEF 14A/S-registration filings with 8-K item-number parsing + mapping; reuses SEC CIK resolution; offline-parseable
- [x] Company press-release / IR provider (T1, company-owned primary source) with conservative RSS/Atom feed discovery; graceful "primary source unavailable" when no website/feed
- [x] News provider abstraction (`NewsProvider` base + `NullNewsProvider` default + env-gated `EnvConfiguredNewsProvider`); URL/title normalisation + dedup; **no paid dependency, no live call in CI**
- [x] Deterministic `catalyst_classifier` — SEC form/item mapping + headline keywords → category/direction/strength/evidence + bounded confidence
- [x] `discover_catalysts` orchestration service — non-blocking, capped, dedup + multi-source detection + summary + coverage status
- [x] `catalyst_agent` node in `company_analysis` workflow (real-data providers only; mock unchanged) → report sections: News & Catalyst Discovery, Recent Catalyst Events, SEC Filing Events, Catalyst Evidence Quality, Catalyst Gaps / Next Research Tasks
- [x] Catalyst context woven into Bull/Bear/Risk/Committee/Source-Quality; `news_catalyst_discovery` section in Final Report Generator (safety-gated, external headlines neutralised)
- [x] All SEC events stay T2, company press releases T1, aggregator news T5; catalyst labels are always T6_model_estimate — never promoted
- [x] 68 backend tests + 8 Playwright catalyst-preview tests; human review stays required; `safety_valid` stays true

Skills used: `financial-data`, `langgraph-agents`, `backend-fastapi`, `frontend-nextjs`, `testing-qa`

**Remaining after Phase 24:** Phase 25 market candidate discovery; Phase 23 auth/staging protection; Phase 26 public publishing; richer peer/governance sections; optional LLM-assisted event summarization; optional paid/high-quality news provider.

---

## Phase 24.1: Real News + Company Source Enablement ✅

**Status: Delivered (2026-07-16)**

Goal: Phase 24 shipped the catalyst architecture but the live AAPL `free_real` report showed coverage `filings_only` because no company press-release source was sourced and no news provider was configured. Phase 24.1 adds a source-discovery + news-search layer on top of Phase 24 so richer real-world catalyst evidence (company-owned press releases + reputable company/industry news) can be discovered — while preserving every safety boundary (no recommendations, price targets, fair values, or upside/downside; human review required).

Deliverables:
- [x] `company_source_discovery_service` — discovers company website / IR / newsroom / press-release feeds from a curated verified issuer registry, identity enrichment (`profile.website`), SEC/GLEIF websites and (optional) a configured search provider; domain-brand verification; social-media / low-quality domains rejected; never fabricates a source
- [x] `exchange_source_registry` — exchange/country-aware query templates + trusted-media/forbidden-query-phrase lists + a small curated verified issuer-source allowlist (AAPL/MSFT/NVDA/GOOGL/AMZN/TSLA/META). Exchange/listing-venue pages are **T3** (not regulators), never promoted to T1/T2
- [x] `news_query_planner` — bounded (≤10) company / industry / exchange / primary-source / regulatory queries using exact legal name + ticker; guaranteed free of recommendation / stock-prediction phrases
- [x] Configurable real news/search providers (`free_news_provider`): `search(query)` primitive, `ConfigurableWebNewsProvider` (generic env-key JSON), no-key `GdeltNewsProvider`; all non-blocking (missing config / HTTP error / rate-limit / malformed → warning, never crash)
- [x] `news_relevance_scorer` — deterministic 0–1 relevance + level, separating **company-specific** catalysts from **industry-context** items; filters food/brand-ambiguity and stock-prediction spam
- [x] Industry / sector context news collected separately and **never** treated as a direct company catalyst (category forced `macro_sector`, direction neutral/mixed)
- [x] `discover_catalysts` integration + source-class-aware coverage status (`filings_only` → `limited`/`adequate`/`strong` when real T1/T4/T5 evidence is present)
- [x] Report sections: **Company News Sources** + **Industry Context News**; richer machine-readable `news_catalyst_discovery` payload (company sources + industry events + source-class attempted/successful)
- [x] Env config: `NEWS_PROVIDER_NAME`, `NEWS_API_KEY`, `NEWS_API_BASE_URL`, `NEWS_SEARCH_ENDPOINT`, `NEWS_MAX_RESULTS`, `NEWS_LOOKBACK_DAYS`, `NEWS_TIMEOUT_SECONDS` — optional; no secret committed; no paid provider required for tests
- [x] 57 backend tests + 10 Playwright catalyst-preview tests; safety gate passes; human review stays required

Skills used: `financial-data`, `langgraph-agents`, `backend-fastapi`, `frontend-nextjs`, `testing-qa`, `security-review`, `docs-maintainer`

**Remaining after Phase 24.1:** Phase 25 market candidate discovery; Phase 23 auth/staging protection; Phase 26 public publishing; optional paid/high-quality news provider + LLM-assisted event summarization.

---

## Phase 24.1.1: News Provider Activation + Feed-Status Consistency ✅

**Status: Delivered (2026-07-16)**

Goal: The live AAPL `free_real` report discovered Apple's company sources but still showed `coverage_status=filings_only` with a **self-contradictory** warning — Company News Sources listed a press-release feed while the warning claimed "no readable RSS/Atom feed found at common paths for apple.com". Root cause: the curated Apple newsroom RSS URL was **stale (404)**, and the warning wording did not distinguish "no feed discovered" from "discovered feed unreadable / no recent items". Phase 24.1.1 corrects the feed data, fixes the status semantics, and verifies the no-key GDELT path — without weakening any safety constraint.

Deliverables:
- [x] Fixed stale curated feed URLs: Apple `newsroom/rss-feed.rss` (was `newsroom.rss`, 404) and Amazon `aboutamazon.com/news/feed` (was 404), validated live
- [x] Precise `PressReleaseStatus` (`not_discovered` / `feed_discovered_unreadable` / `feed_discovered_no_recent_items` / `feed_discovered_with_items` / `feed_discovered_items_filtered`) + `NewsProviderStatus` (`not_configured` / `no_results` / `results`) + per-source `source_statuses`
- [x] Press provider now tries the **discovered feed URL first**, applies a lookback filter (RFC822 + ISO dates), and emits accurate, non-misleading warnings that name the actual feed URL and state
- [x] `missing_sources` no longer lists `company_press_release` when a feed was discovered but is merely unreadable/stale — the precise status carries the nuance; coverage improves **only** from usable events, never from discovery alone
- [x] No-key `GdeltNewsProvider` selected by `NEWS_PROVIDER_NAME=gdelt` (no `NEWS_API_KEY`); `NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS` respected; all failures non-blocking; results stay T5 (mapped T4 only for trusted-media hosts)
- [x] Status-aware report wording (Company News Sources feed-status line, precise blockquotes, risk/source-quality context) + richer `news_catalyst_discovery` `source_statuses` payload
- [x] 33 backend tests + 2 new Playwright tests; safety unchanged (no recommendations/targets/fair-values/upside; human review required; `safety_valid` true)

Skills used: `financial-data`, `backend-fastapi`, `langgraph-agents`, `frontend-nextjs`, `testing-qa`, `docs-maintainer`

**Remaining after Phase 24.1.1:** Phase 25 market candidate discovery; Phase 23 auth/staging protection; Phase 26 public publishing.

---

## Phase 24.1.2: Press-Release Canonical Link Fix ✅

**Status: Delivered (2026-07-17)**

Goal: The live AAPL `free_real` report reached `coverage_status=strong` with 19 T1 Apple newsroom press-release events, but many `company_press_release` events used **image URLs** as `source_url` (e.g. `…/tile/…jpg.og.jpg`) instead of canonical Apple Newsroom article pages. This is an evidence/citation-quality issue that Phase 25 candidate discovery will depend on. Phase 24.1.2 makes press-release evidence links canonical, storing media/image URLs separately — no safety change.

Root cause: Apple's newsroom feed is **Atom**, and each `<entry>` has two `<link>` elements — the article (`rel` empty) and an image enclosure (`<link rel="enclosure" type="image/jpeg" href="…jpg.og.jpg">`). The old parser let the **last** `<link>` win, so the image enclosure overwrote the article URL.

Deliverables:
- [x] `extract_canonical_feed_link()` + `is_media_url()` — priority RSS `<link>` → Atom alternate/html `<link>` → any non-media `<link>` → `<guid>`/`<id>` → feedburner:origLink → relative-resolved-against-feed-base; rejects `.jpg/.jpeg/.png/.gif/.webp/.svg/.avif/.mp4/.mov/.webm`, `/images/`·`/media/`·`/thumbnail/`·`/tile/` paths, `.og.jpg`, `rel="enclosure"` image links, `media:content`/`media:thumbnail`, and description `<img>` URLs
- [x] `parse_feed` collects every link/guid/enclosure/media field per entry and chooses the canonical article URL; the associated image is kept as `NewsItem.media_url`
- [x] Schema: `NewsItem.media_url`, `CatalystEvent.media_url` + `source_url_quality` (`canonical_article` / `rejected_media_only` / `missing`); `source_url` stays the canonical article page; final-report `_event_row` carries `source_url_quality` + `media_url`
- [x] Dedup by canonical article URL; aggregate warning when an item has no canonical URL (media URL is **never** used as evidence); Phase 24.1.1 feed-status semantics preserved
- [x] 21 backend tests (Apple-like Atom + RSS fixtures, relative/guid/enclosure/description cases) + 1 Playwright test; 1143 backend passed; safety unchanged (no recommendations/targets/fair-values/upside; human review required; `safety_valid` true)

Skills used: `financial-data`, `backend-fastapi`, `frontend-nextjs`, `testing-qa`, `docs-maintainer`

**Remaining after Phase 24.1.2:** Phase 25 market candidate discovery; Phase 23 auth/staging protection; Phase 26 public publishing.

---

## Phase 25: Real Market Candidate Discovery Engine ✅

**Status: Complete** — see the detailed **Phase 25: Real Market Candidate Discovery ✅** and **Phase 25.1: Async Discovery Run Execution ✅** sections below for the shipped implementation. This entry preserves the original plan; the delivered scope is a bounded, internal-only market scan (curated seed / manual universe) producing a ranked internal research-candidate queue — deliberately **not** a full-market crawl.

Original plan (delivered):
- [x] Candidate screener using real price + SEC data — delivered as a bounded, admin-controlled universe (validated against `DISCOVERY_MAX_UNIVERSE_SIZE`) rather than an unbounded full-market crawl
- [x] Multi-signal ranking: price momentum + fundamentals quality + catalyst recency + source-quality + completeness (internal prioritization score only)
- [x] Automated candidate queue: companies surfaced by discovery enter an internal review queue
- [x] Admin can review candidates and run full analysis; every candidate stays `human_review_required=true` / `is_public=false`
- [x] No automatic progression to analysis without admin approval
- [x] Source tier enforced: T5 for aggregated data; T2 for SEC-derived data

Skills used: `financial-data`, `backend-fastapi`, `investment-domain`, `testing-qa`

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

## Phase 25: Real Market Candidate Discovery ✅

**Status: Complete**

Goal: Move from manual single-ticker analysis to a bounded, internal-only market
discovery workflow that builds an internal research-candidate queue.

Deliverables:
- [x] `discovery_runs` + `discovery_candidates` tables (migration 010)
- [x] Bounded universe (curated seed default `AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA`, or manual tickers), validated against `DISCOVERY_MAX_UNIVERSE_SIZE` (default 15) — oversized/empty runs rejected (422)
- [x] Deterministic `discovery_scoring_service`: `0.30·momentum + 0.25·catalyst + 0.20·fundamentals + 0.15·source_quality + 0.10·completeness − risk_penalty`, clamped 0–100; grades high/medium/low internal interest + data_insufficient
- [x] `discovery_signal_extractor` reuses the tested company-analysis workflow per ticker (injectable → offline CI)
- [x] `market_discovery_service` orchestrator: sequential bounded processing, non-blocking per-ticker failures, run status `completed` / `completed_with_warnings` / `failed`, ranked candidate persistence, forbidden-term safety scan
- [x] 6 admin-only endpoints under `/api/v1/market-discovery/*` (runs, candidates, run-analysis)
- [x] `/admin/discovery` dark-glass UI: start-run form (provider/universe/lookback + universe-size preview), runs table, ranked candidate queue, inline candidate detail with score breakdown + "Run Full Analysis"
- [x] Config: `DISCOVERY_DEFAULT_PROVIDER`, `DISCOVERY_MAX_UNIVERSE_SIZE`, `DISCOVERY_MAX_CONCURRENT_REQUESTS`, `DISCOVERY_LOOKBACK_DAYS`, `DISCOVERY_REQUEST_TIMEOUT_SECONDS`, `DISCOVERY_CACHE_TTL_HOURS`, `DISCOVERY_SEED_UNIVERSE`
- [x] 51 backend + 15 Playwright tests; full suite green; ruff clean

Safety: internal-only, human-review-required, non-public. No BUY/SELL/HOLD/WATCH,
no price targets, no fair value/intrinsic value/upside/downside/undervalued/
overvalued, no recommendations. `candidate_score` is an internal prioritization
signal only.

Known limitations: no full-market crawl; no scheduling; curated feed URLs can go
stale; free providers are incomplete; generated-report `schema_valid` may remain
false (expected, non-blocking, targeted by Phase 26); public publishing not
implemented (Phase 26). Admin access is auth-protected (Phase 23 complete).

Skills used: `database-design`, `backend-fastapi`, `investment-domain`,
`financial-data`, `frontend-nextjs`, `testing-qa`, `security-review`, `docs-maintainer`.

---

## Phase 25.1: Async Discovery Run Execution ✅

**Status: Complete** — UX/operational hardening only (no product-scope, safety,
schema, or migration change).

Problem: the Phase 25 `POST /runs` processed the whole universe **inline**. On B1
with a single gunicorn worker a multi-ticker `free_real` run could exceed the
gateway/proxy timeout and return `504` even though the backend later created the
run/candidates correctly.

Deliverables:
- [x] `create_pending_run()` — validate provider/universe/lookback + max-size,
  insert the `discovery_runs` row (`status="pending"`), commit, return fast.
- [x] `process_run()` — process an already-loaded run: guard against reprocessing
  a terminal run / starting a second worker on a fresh `running` run (30-min
  stale-restart), commit progress after **every** ticker, re-rank in memory,
  finalize status. Per-ticker failures stay non-blocking.
- [x] `process_discovery_run_by_id()` / `process_discovery_run_task()` — background
  worker that opens its **own** DB session (never the request session), driven by
  a primitive `run_id`; best-effort marks the run `failed` on a fatal error.
- [x] `POST /runs` uses FastAPI `BackgroundTasks` — returns 201 immediately with
  `status="pending"`, `is_async=true`, `progress_pct`, and a `message`.
- [x] Run schema adds computed `progress_pct` + `is_async`/`message`.
- [x] `/admin/discovery` polls `GET /runs/{run_id}` (every 3 s) until terminal,
  showing a status badge, progress bar, processed/universe counts, candidate/
  error counts, warnings, a "processing in background" note, an empty-queue
  "candidates will appear as tickers finish" placeholder, and a friendly
  timeout message if the POST ever fails. Manual Refresh retained.
- [x] 27 new backend + 9 new Playwright tests; full suites green; ruff clean.

Known limitation: `BackgroundTasks` are **process-local** and not durable across
an App Service restart — acceptable for this MVP. A durable queue (Azure Service
Bus / Functions) is deferred to a later phase. No new migration.

Explicitly out of scope (unchanged): auth, public publishing, paid plans,
full-market scan, scheduled/recurring scans, email/Slack alerts, queue
infrastructure, recommendation logic.

Skills used: `backend-fastapi`, `frontend-nextjs`, `testing-qa`,
`security-review`, `docs-maintainer`.

**Next recommended phase: Phase 26 — Final Report Schema Completion / Publication-Readiness Pipeline. Objective: move generated reports from `schema_valid=false` to `schema_valid=true` without weakening safety gates, recommendation restrictions, or the human-review requirement. (Phase 23 — Admin/Auth hardening is complete; Phase 25.2 — Durable Discovery Job Queue is a later infrastructure-backlog item.)**

---

## Phase 26: Final Report Schema Completion / Publication-Readiness ✅

**Status: Complete** — internal pipeline only. No public publishing, no
recommendations, no price targets / fair values / upside-downside, and no DB
migration. **This is distinct from the earlier-planned "Phase 26 Public Report
Publishing Website" — public publishing remains NOT implemented.**

Problem: generated internal final-report drafts use the Phase 16 admin shape
(`executive_summary` / `financial_snapshot` / …), which does not match the strict
`report_schema.json` shape (`report_meta` / `identity` / `discovery_profile` / …),
so `validate` always returned `schema_valid=false` on missing required sections.

Deliverables:
- [x] `real_asset_report_completer.py` — deterministic, no-LLM, no-network layer
  that maps the admin draft into the strict schema shape. Sourced numbers are
  carried through (quality `C_inferred`); genuinely-absent fields become honest
  `not_sourced` stand-ins (a `datapoint` with `value: null`, `data_quality:
  "D_weak_or_stale"`) — **never fabricated data**. Mock/simulated numbers are
  never presented as sourced.
- [x] Required-section behaviour: `peers` → `peers_not_sourced` stand-in rows;
  `governance` → `not_sourced`; `valuation` → no fair value / price target /
  upside-downside (the schema-required `upside_downside_pct` is a null stand-in);
  `catalysts_risks` → catalyst labels stay model-derived (T6); `self_critique`
  always present; `verdict`/`report_meta` set to the internal triage label `PASS`.
- [x] `run_final_report_validation()` distinguishes four orthogonal dimensions:
  `schema_valid` (structural), `safety_valid`, `research_complete` (enough SOURCED
  data — `false` for free-provider drafts), `publication_ready` (**always false**).
  `human_review_required` stays `true`.
- [x] Responses + `schema_validation_json` gain `research_complete` /
  `publication_ready` / `placeholder_field_count`. Admin report page surfaces all
  four dimensions and a "schema-complete ≠ research-complete / never public-ready"
  note. No public publish action anywhere.
- [x] 16 new backend + 1 new Playwright test; full suites green (1252 backend,
  133 Playwright); ruff + mypy clean. No migration.

Safety preserved: the app safety gate (pure substring scan) still passes on the
completed report; all stand-in text avoids banned substrings (`BUY`/`SELL`/`HOLD`/
`WATCH`/`price target`/`fair value`/`upside`), using the neutral vocabulary
`not_available` / `not_sourced` / `blocked` / `requires_human_research` instead.

Known limitation: `research_complete` is `false` for every free-provider report
until primary-source (T1/T2) research is added; peer/governance/valuation sections
are structural stand-ins pending human research. Public publishing stays deferred.

Skills used: `backend-fastapi`, `frontend-nextjs`, `testing-qa`,
`security-review`, `docs-maintainer`.

---

## Phase 27.1C: Prompt-Derived Autofill + Controlled Selectors + Strict Country Filtering — ✅ COMPLETE

**Status: Complete (no migration).** Raised during Phase 27.1B staging
validation; delivered as one feature covering the strict-filtering fix (the
original backlog item) plus prompt auto-detection and controlled selector
values.

**Strict country filtering (the original backlog problem).** A country filter
used to admit that country's entire region — `build_universe` accepted a
candidate when `region in parsed_regions OR country in parsed_countries`, so
`"Danish jewelry companies"` returned all eight European luxury names instead of
just Pandora. Fixed: **country is strict** — when any country filter is present
it is the sole geographic filter and the region can no longer broaden the search;
region applies only when no country is set. `"Swiss watch companies"` now returns
only `UHR`/`CFR`; `"Danish jewelry companies"` returns only `PNDORA`.

**Part C — Prompt-derived autofill.** `market_thesis_parser.parse_thesis` now
returns canonical single-value `region` / `country` / `sector` / `industry` /
`theme` / `confidence` / `extraction_source` detected from the prompt text
(e.g. `"US semiconductor equipment companies"` → United States / North America /
Technology). New `POST /api/v1/market-discovery/parse-thesis` previews these for
the UI **without creating a run**. Precedence: an explicit form value overrides
the parsed prompt value; a conflict keeps the explicit choice and surfaces a
warning (`"Prompt mentions Switzerland, but explicit Country=Denmark was
selected."`).

**Part D — Controlled selector values.** New `discovery_filters` module +
`GET /api/v1/market-discovery/supported-filters` expose canonical Region /
Country / Sector / Industry options (regions/countries derived from the exchange
registry; sectors canonical, aliases resolved internally). Region/Country/Sector
outside the supported set are rejected `422` before any work is scheduled.
`/admin/discovery` renders searchable combobox selectors (options from the
backend, no arbitrary text), debounce-calls `parse-thesis` to auto-fill
non-manually-edited fields, shows a "Detected: …" preview + a "Reset to detected"
action, and warns on a prompt/selection conflict.

**Not a safety issue** — no fabricated data, no recommendation; every excluded
company is still recorded with a reason; `human_review_required=true`,
`is_public=false`, no publish route. ~21 backend + ~9 Playwright tests.

---

## Phase 27.1D: Staging Telemetry / Logging Cleanup — ✅ COMPLETE

**Status: Complete (no migration; API + docs + CI only, web unchanged).**
Delivers the telemetry deferred from Phase 27.1C so future staging validations are
**evidence-based** instead of relying only on observed HTTP status codes and
persisted run status (`httpLogs.fileSystem` was disabled, `containerStream.log`
was empty because INFO app logs were dropped under gunicorn, and App Insights was
unwired).

**What is logged (safe, structured — one line per event).**
- `http_request` (middleware): `method`, `path` (path only — never the query
  string), `status`, `duration_ms`, `request_id` (honours an inbound
  `X-Request-ID`), `route_family`. Added as the OUTERMOST middleware so it also
  captures Basic-Auth `401`s.
- `discovery_run_started`: `run_id`, `mode`, `provider`, `universe_size`,
  `max_universe`, `max_candidates`, `lookback_days`, parsed
  `region`/`country`/`sector`/`theme`.
- `discovery_candidate`: `run_id`, `ticker`, `exchange`, `company_name_source`,
  `profile_source`, `fundamentals_source`, `sec_eligible`, `reason`,
  `safety_valid`, `human_review_required` (no raw payloads).
- `discovery_run_completed`: `run_id`, `status`, `processed_count`,
  `candidate_count`, `error_count`, `warning_count`, `duration_ms`.
- `discovery_run_failed`: `run_id`, `exception_type`, safe `error` message.
- `report_validation`: `report_id`, `schema_valid`, `safety_valid`,
  `research_complete`, `publication_ready`, `human_review_required`,
  `forbidden_terms_count`, `missing_required_sections_count` (never report body).
- `/health` gains safe `app` + `build_time` deploy metadata.

**What is NEVER logged.** `Authorization`/`Cookie`/`Set-Cookie`/`X-API-Key`
headers, OAuth tokens, the Basic-Auth value, API keys, `DATABASE_URL`/connection
strings, request/response bodies, query strings, and raw final-report content.
Redaction is centralised in `app/core/log_redaction.py` (redacts by key name +
token-bearing URL query params) and the structured formatter redacts sensitive
field names and collapses newlines (no log forging).

**Hotfix — third-party request-URL logs (`fix/phase-27-1d-suppress-thirdparty-url-logs`).**
Staging validation caught that raising the root logger to INFO *newly surfaced*
`httpx`'s INFO request-URL logs, which embed the EODHD key
(`?api_token=<key>`) — a leak the pre-27.1D unconfigured root logger had hidden by
dropping INFO. Fixed by capping `httpx`/`httpcore`/`urllib3` at WARNING and adding
a `RedactingFilter` on the stdout handler that scrubs token-bearing query params
and Authorization/Cookie echoes from **every** record (`log_redaction.redact_text`).
Re-validated on staging: `api_token=` now renders only `***REDACTED***`.

**Config.** `LOG_LEVEL` (default `INFO`; set `WARNING` to reduce verbosity) and
`REQUEST_LOGGING_ENABLED` (default `true`) — no code change to tune.

**Ops docs.** `docs/DEPLOYMENT.md` → "Staging Logging & Telemetry (Phase 27.1D)"
covers enabling App Service filesystem logs + retention, streaming
(`az webapp log tail`), downloading + grepping by event name, validating a
discovery run from the log sequence, and verifying no secrets are logged.

**Tests.** 39 backend tests (redaction, structured formatter, request-logging
middleware, discovery started/candidate/completed/failed events, report
validation booleans-not-body, no-publish-route invariant); 1755 total.

**Unchanged.** No auth change, no `AUTH_TEST_MODE`, no public publishing, no
recommendation / BUY-SELL-HOLD-WATCH / price-target / fair-value / upside
language; `human_review_required=true`, `publication_ready=false`, no publish
route. Application Insights / a log-aggregation backend remains a future
enhancement.

---

## Phase 28A: Single-Company LLM Analysis Council — ✅ COMPLETE

**Status: Complete (no migration; OFF by default).** Activates a real but
controlled LLM council for ONE company report. Reports were previously honest
that "LLM: not used" because they are deterministic assemblies of
SEC/fundamentals/provider data plus schema completion. Phase 28A makes the LLM a
genuine, internal-only, citation-bound, safety-gated participant — without
changing auth, publishing, or the human-review requirement.

**Design.** New `apps/api/app/services/llm/` package, gated by
`LLM_COUNCIL_ENABLED` (default `false`) + `LLM_PROVIDER_COUNCIL`
(`fake` | `azure_openai` | `openai`; `fake` is deterministic/offline and the
only provider used in tests):

- **Evidence Pack Builder** — a bounded (`LLM_COUNCIL_MAX_EVIDENCE_ITEMS`),
  excerpts-only, stable-id (`E1..En`) pack for one company. Records
  **`transport_tier` vs `content_tier`**: SEC EDGAR is a T2 transport, a filing
  pulled through it is T1 content; company press releases are
  `T1_primary_company_source`. Agents may cite ONLY evidence-pack ids.
- **LLM client abstraction** — `LLMClient` ABC (JSON parse + one repair,
  timeout, thin langchain Azure/OpenAI wrappers); `get_llm_client` returns
  `None` (never raises) when disabled or credentials/deps are missing, so the
  deterministic path is preserved.
- **Eight council agents** — Financial Analyst, Business/Moat, Catalyst,
  Risk/Governance, Valuation Guard (a guard, never a valuator), Source Quality
  Critic, Red Team, Committee Chair (internal labels only:
  `internal_research_candidate`/`requires_more_evidence`/`insufficient_data`/
  `monitor_for_new_evidence`/`reject_for_now`). A single agent failing is
  isolated; the report still saves.
- **Citation + safety enforcement** — non-pack citation ids are dropped,
  un-cited material claims are flagged, and any forbidden rating/valuation
  language quarantines that agent's whole output before it is saved/displayed;
  the existing report-level safety gate scans the merged council section as a
  backstop.
- **Integration** — inside `FinalReportGeneratorService._generate_and_save`;
  metadata persists under `source_summary_json.llm_council`; `FinalReportResponse`
  gains additive `llm_used`/`llm_provider`/`llm_model`/`council_version`/
  `council_agents_*`/`evidence_pack_version`/`evidence_item_count`/
  `committee_label`. `/admin/reports/[id]` gains an LLM-used pill + council
  metadata + an "LLM Council Analysis" section (bounded, safety-scanned output
  only — never raw prompts/evidence/secrets).
- **Safe logging** — `llm_council_started`/`evidence_pack_built`/
  `llm_agent_completed`/`llm_agent_failed`/`llm_council_completed`
  (ids/provider/model/status/counts/duration only).

**Tests.** 33 backend (fake provider only) + 3 Playwright. Deterministic
regressions (AAPL/MSFT/NVDA, sparse non-US UHR/CFR, BA.LSE-not-Boeing, Swiss
watch strict filter, Swatch/Watches-&-Jewelry safety) preserved.

**Unchanged.** No auth change, no `AUTH_TEST_MODE`, no public publishing, no
recommendation / BUY-SELL-HOLD-WATCH / price-target / fair-value / upside
language; `human_review_required=true`, `publication_ready=false`, no publish
route.

**Future (LLM council track).** Phase 29 — source-provider expansion; Phase 30 —
translation / local-language agents. Neither is started here.

---

## Phase 28B: Hierarchical Discovery-Run LLM Council — ✅ COMPLETE

Phase 28A made the LLM a controlled, citation-bound, safety-gated council for a
SINGLE company. Phase 28B adds the **run-level analog**: a council that reviews a
whole discovery run's candidate set and decides which candidates deserve deeper
internal research, which need more evidence, and which to monitor or reject. It
is **internal-only, citation-bound, safety-gated, OFF by default, and manual
admin-triggered only** — it never runs automatically after a discovery run.

**No DB migration.** The review is persisted under the run's existing JSONB
column `discovery_runs.config_json["discovery_council"]` (reassigned whole to
trigger dirty-tracking).

**Gating.** Runs only when BOTH `LLM_COUNCIL_ENABLED` (the shared 28A client
gate) and new `LLM_DISCOVERY_COUNCIL_ENABLED` are true and a usable provider
resolves. If either is off, `get_discovery_llm_client` returns `None`, the
council is disabled, and **no fake output is produced in production** — the
deterministic discovery result is unchanged.

**Run evidence pack** (`build_discovery_evidence_pack`). Bounded (default 25
candidates via `LLM_DISCOVERY_COUNCIL_MAX_CANDIDATES`), cited, no raw report
bodies / no secrets. Run-level facts get ids `R1, R2, …`; each candidate gets
`C1, C2, …` carrying `score_breakdown`, `data_coverage`
(`profile_source`/`fundamentals_source`/`sec_eligible`/`reason`/
`requires_human_research`), `catalyst_summary`, `safety_valid`, `warnings`.
Agents may cite ONLY those ids.

**Eight agents, in order.** `run_coordinator`, `candidate_prioritization`,
`novelty_coverage`, `diversity_anti_convergence`, `evidence_sufficiency`,
`risk_gatekeeper`, `run_red_team`, `discovery_chair` (chair runs last over the
prior agents' safety-scanned summaries). The ONLY per-candidate internal actions
are `research_next` / `monitor_for_evidence` / `insufficient_data` /
`reject_for_now`; the ONLY `run_quality` labels are `strong` / `adequate` /
`thin` / `failed`. A single failing agent (timeout, malformed JSON, provider
error) is isolated and never fails the review.

**Citation + safety enforcement** (`discovery_citation_checker.py`). Invalid
citation ids are dropped; un-cited material claims move to `unsupported_claims`;
any forbidden investment-action language quarantines the WHOLE agent output
(`status=failed`) with no forbidden term echoed forward (the note records tier
names, not terms); bad `internal_action`/`run_quality` are coerced to safe
defaults. A final backstop re-scan runs before storing.

**API (admin/internal only, no auth change).**
`POST /api/v1/market-discovery/runs/{run_id}/council-review` builds the pack,
runs the council, stores + returns the review (`409` when disabled / no provider,
`422` when the run has no candidates / is not terminal, `404` when not found).
`GET .../council-review` returns the stored review (`404` when none). Both return
`DiscoveryCouncilReviewResponse`.

**Frontend.** `/admin/discovery` gains a "Discovery Council Review" panel: a "Run
Discovery Council Review" button (clear disabled state when the council is off),
run-quality / agents ok-fail / safety pills, the four candidate buckets, evidence
gaps, next source tasks, red-team / agent summaries, and an inline internal-action
pill next to each matching candidate row. No publish action; no
BUY/SELL/HOLD/WATCH; no price target / fair value / upside / downside.

**Safe logging.** `discovery_council_started` /
`discovery_council_evidence_built` / `discovery_council_agent_completed` /
`discovery_council_agent_failed` / `discovery_council_completed` /
`discovery_council_disabled` — ids / provider / model / counts / durations only;
never prompts, completions, evidence, or secrets.

**Tests.** 35 backend (fake client only) + 6 Playwright, all green.

**Unchanged.** No auth change, no `AUTH_TEST_MODE`, no public publishing, no
recommendation / BUY-SELL-HOLD-WATCH / price-target / fair-value / upside
language; `human_review_required=true`, `publication_ready=false`, no publish
route.

**Phase 28B.1 — council resilience (staging follow-up).** Staging validation with
a real Azure OpenAI deployment surfaced that a provider rate-limit error escaped
the per-agent isolation and crashed the whole council (misleading "provider not
available"), and that langchain's internal retry/backoff made 8 sequential agents
exceed the synchronous HTTP gateway timeout (504, nothing stored). Fixes: the
shared client wraps ANY provider error as a recoverable `LLMError` (a rate-limited
agent fails in isolation; the council still returns `llm_used=true`), council
clients use `max_retries=0` (fast-fail — no backoff that blows the gateway
budget), and `run_quality` always falls back to an allowed label (`failed` when
nothing completed) instead of null. **Known limitation / recommended follow-up:**
the council-review endpoint is still synchronous, so a large candidate set under a
low provider quota may leave some agents failed. The robust fix is **async
execution** (return a pending review, run the council in a background task with a
fresh DB session, poll `GET` — mirroring Phase 25.1), which also allows restoring
retries. → **Resolved in Phase 28B.2 (async execution) — see below.**

**Future (LLM council track).** Phase 29 — source-provider expansion; Phase 30 —
translation / local-language agents. Neither is started in Phase 28B.

---

## Phase 28B.2: Async Discovery Council Execution — ✅ COMPLETE

Delivers the Phase 28B.1 recommended follow-up: the run-level discovery council
now runs **asynchronously and pollably**, the run-level analog of the Phase 25.1
async discovery-run pattern. On a large candidate set under a low Azure OpenAI
quota the synchronous endpoint could rate-limit agents or approach the gateway
timeout (staging: a European 8-candidate run completed only 1/8 agents while a
smaller Swiss run completed 8/8). **No DB migration.** Flags unchanged and still
OFF by default (`LLM_COUNCIL_ENABLED` + `LLM_DISCOVERY_COUNCIL_ENABLED`).

**Async lifecycle.** `POST /api/v1/market-discovery/runs/{run_id}/council-review`
validates + writes a `pending` **status envelope** and returns **immediately** —
it no longer blocks on all eight LLM calls. A FastAPI `BackgroundTask`
(`process_discovery_council_task` → `process_discovery_council_by_id`) opens its
**own fresh DB session**, transitions the job to `running`, runs the sequential,
per-agent-isolated (Phase 28B.1) council, and persists a terminal envelope. Every
failure path is caught and stored, so a job never sticks in `running`. Terminal
statuses: `completed` (all agents ok), `completed_with_warnings` (some agents
failed or the safety re-scan flagged output — a partial, still-safe review is
stored), `failed` (no agent completed, or a disabled/not-ready/internal error,
with a short safe `error` reason code — never an exception string).

**Polling.** `GET .../council-review` returns the current status: `pending` /
`running` while the job runs, the completed review (`review_available=true`) when
done, `failed` with a safe reason, or `disabled` when no job has ever run and the
council is off. `/admin/discovery` polls every 3 s (mirroring the run-status poll)
and shows an in-progress banner (agents ok / failed), a failure banner, or the
completed review panel — the page never blocks.

**Storage.** The value under `discovery_runs.config_json["discovery_council"]` is
now a status envelope (`status`/`started_at`/`completed_at`/`llm_used`/
`agents_completed`/`agents_failed`/`safety_valid`/`error`/`review`) wrapping the
Phase 28B review payload. `get_council_envelope` normalises a legacy Phase 28B raw
review into a completed envelope, and a **completed review stays readable after
the flags are turned off**.

**No duplicate jobs.** A queued/running job returns its current status (no second
job is started); a completed review is returned unless `force=true`.

**Safe logging.** New events `discovery_council_job_queued` /
`discovery_council_job_started` / `discovery_council_job_completed` /
`discovery_council_job_failed` / `discovery_council_job_duplicate` (ids / status /
counts / duration only) alongside the existing Phase 28B council events. Prompts,
completions, evidence excerpts and secrets are never logged.

**Limitation.** `BackgroundTasks` are **process-local, not a durable queue** —
same limitation as Phase 25.1 async discovery runs. An app restart mid-job is
surfaced as stale/failed on the next poll. Future improvement: Azure Queue /
Celery / a durable worker (would also allow re-enabling provider retries).

**Unchanged.** No auth change, no `AUTH_TEST_MODE`, no public publishing, no
recommendation / BUY-SELL-HOLD-WATCH / price-target / fair-value / upside
language; `human_review_required=true`, `publication_ready=false`, no publish
route. ~28 new backend + 4 new Playwright tests.

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
