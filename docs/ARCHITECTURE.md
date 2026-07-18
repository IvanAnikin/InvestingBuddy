# Architecture

## Status: Phase 25.1 — Async Discovery Run Execution. Operational hardening on Phase 25: `market_discovery_service` is split into `create_pending_run()` (validate + persist a `pending` run and return fast) and `process_run()` / `process_discovery_run_by_id()` / `process_discovery_run_task()` (background worker that opens its **own** DB session — never the request session — commits progress after every ticker, guards against reprocessing a terminal run or double-running a fresh `running` run, and best-effort marks `failed` on a fatal error). `POST /api/v1/market-discovery/runs` now schedules a FastAPI `BackgroundTask` and returns `201` immediately (`status="pending"`, `is_async`, computed `progress_pct`, `message`); `/admin/discovery` polls `GET /runs/{run_id}` until a terminal status and renders a live progress bar / counts. This removes the multi-ticker `504` under the single B1 worker. `BackgroundTasks` are process-local (not durable across restart) — acceptable for the MVP; a durable queue is deferred. No product-scope/safety/schema/migration change; oversized/empty universe still rejected (422) before scheduling; `human_review_required=true`/`is_public=false` preserved. 27 new backend + 9 new Playwright tests. Underlying Phase 24.1.2 — Press-Release Canonical Link Fix. `company_press_release_provider.py` gains `extract_canonical_feed_link()` + `is_media_url()`; `parse_feed` now collects every per-entry link/guid/enclosure/media field and selects the canonical article URL (Atom `rel="enclosure"` image links + `media:content`/`media:thumbnail` + `.og.jpg` tiles are rejected), keeping the image as `NewsItem.media_url`. `CatalystEvent` gains `media_url` + `source_url_quality`; the discovery service classifies each event's link quality and warns when no canonical URL exists (a media URL is never used as evidence, never T1); the final-report `_event_row` carries both. Dedup uses the canonical URL. Safety unchanged. Underlying Phase 24.1.1 — News Provider Activation + Feed-Status Consistency. Fixes press-release feed-status semantics and confirms the no-key GDELT path. New `PressReleaseStatus` / `NewsProviderStatus` enums (`app/schemas/catalyst.py`); `CompanyPressReleaseProvider.get_press_releases` tries the discovered feed URL first, lookback-filters items, and returns a precise `status` + `items_seen`/`items_used`; `discover_catalysts` threads `news_lookback_days` (from `NEWS_LOOKBACK_DAYS`), records `source_statuses` + `news_provider_status`, and only lists `company_press_release` in `missing_sources` when genuinely not discovered; `catalyst_agent` renders a status-aware feed line and the Final Report Generator payload gains `source_statuses`. Stale curated Apple/Amazon feed URLs corrected. Coverage improves only from usable events. Safety unchanged. Underlying Phase 24.1 — Real News + Company Source Enablement. On top of Phase 24, the `catalyst_discovery_agent` gains a source-discovery + news-search layer. New modules: `app/schemas/company_sources.py` (`SourceCandidate` / `CompanySourceDiscoveryResult` + `SourceType`/`RelevanceLevel`/`VerificationMethod`), `app/integrations/exchange_source_registry.py` (exchange/country query templates + trusted-media/forbidden-phrase lists + curated verified issuer allowlist; exchange pages = T3, never T1/T2), `app/services/company_source_discovery_service.py` (`discover_company_sources` — curated + `profile.website` + SEC/GLEIF + optional search, domain-brand verified, never fabricated), `app/services/news_query_planner.py` (`build_news_search_plan` — bounded, recommendation-free), `app/services/news_relevance_scorer.py` (deterministic 0–1 relevance splitting company-specific vs industry-context). `free_news_provider.py` adds a `search(query)` primitive, `ConfigurableWebNewsProvider` and a no-key `GdeltNewsProvider`; `company_press_release_provider.py` accepts discovered `feed_urls`; `catalyst_discovery_service.discover_catalysts` orchestrates discovery → planning → SEC/press/news/industry providers → relevance routing → source-class-aware coverage (`filings_only`→`limited`/`adequate`/`strong`); `catalyst_agent.py` adds **Company News Sources** + **Industry Context News** sections; the Final Report Generator's `news_catalyst_discovery` payload gains `company_sources` + `industry_context_events` + source-class lists. All non-blocking; no live CI call; safety unchanged (no recommendations/price targets/fair values/upside; human review required; `safety_valid` true). Underlying Phase 24 — News + Catalyst Discovery. The `company_analysis` workflow gains a `catalyst_discovery_agent` node (runs for `free_real`/`eodhd_free_real` only; mock unchanged) between the Investment Committee and scoring. New modules: `app/schemas/catalyst.py` (contracts + enums + forbidden-term neutralisation), `app/services/catalyst_classifier.py` (deterministic SEC-item + keyword classifier), `app/services/catalyst_discovery_service.py` (`discover_catalysts` orchestration — non-blocking, dedup, multi-source detection, summary), providers `sec_recent_filings_provider.py` (T2), `company_press_release_provider.py` (T1), `news_provider_base.py` + `free_news_provider.py` (T5, null by default), and `app/agents/research_team/catalyst_agent.py` (markdown sections + council context). The catalyst label is always T6_model_estimate; evidence keeps its real tier and aggregator news is never promoted. The Final Report Generator gains a safety-gated `news_catalyst_discovery` section (external headlines neutralised). No recommendations, price targets, fair values, or upside/downside; human review required; `safety_valid` stays true. Underlying Phase 19.4.1 — Enrichment Completeness Consistency (hotfix on Phase 19.4). The completeness layer now consumes the *enriched* snapshot: `research_completeness_agent._enriched_present_fields()` suppresses blocking/missing gaps for enriched-and-present identity `lei`/`isin`/`sector_classification` and `snapshot_financials` market cap / EV / revenue / net income / debt / cash (and drops satisfied identity next-steps), while `source_quality_agent` gates its "Obtain LEI" recommendation on the LEI actually being missing and upgrades — never "absents" — derived market metrics. Genuinely-missing fields (ISIN, EBITDA, EV/EBITDA, beta, website, IPO date) stay gaps; nothing is fabricated. Underlying Phase 19.4 — Identity + Sector + Market-Metric Enrichment: two pure modules (`company_profile_enrichment`, `market_metrics_enrichment`) enrich the `free_real` / `eodhd_free_real` snapshot after SEC fundamentals + price history are available: sector (DB or **inferred** from SEC SIC, T6), industry/website (SEC, T2), LEI (GLEIF, T2, name-guarded); and derived latest close + **52-week range** (T5), **shares outstanding** (SEC DEI, T2), **market cap / enterprise value / P/E** (DERIVED ESTIMATES, T6, cited inputs) computed only when inputs exist. Resolved fields are pruned from `missing_fields`; `FinancialDataAgent` narrates the derived metrics and `ValuationGuardAgent` recognises them but still blocks all valuation conclusions (readiness stays `partial`). LEI/ISIN/IPO date/EBITDA/EV-EBITDA/beta are never fabricated. Underlying Phase 19.3.1: SEC normalizer (`sec_fundamentals_normalizer`) selects the latest annual filing across all alias concepts (filed-date tiebreak, stale-year warning); `investment_committee_chair` emits canonical `human_review_required`. Builds on Phase 19.2.1 (provider observability), Phase 19.1 Free Real Data Stack, Phase 22.1 Admin Backtesting UI. 964 backend tests passing (12 skipped, integration-only).

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
- Status: **Phase 23 — admin authentication + allowlist enforced on `/admin/*` and the admin proxy, on top of the Phase 22.3 modern dark glassmorphism UI + safe markdown report preview**
  - `src/proxy.ts` — Next 16 **Proxy** (renamed `middleware`, Node runtime); gates `/admin/:path*` (→ `/login` unauthenticated, `/unauthorized` when not allowlisted) and `/api/admin/proxy/:path*` (401/403); `/`, `/login`, `/unauthorized`, `/api/auth/*`, `/api/version` and static assets stay public
  - `src/lib/auth/*` — dependency-free HMAC-signed httpOnly session cookie (`session.ts`), server-session reader (`server.ts`), forwarded-host/callback URL helpers (`url.ts`)
  - `/api/auth/github` + `/api/auth/callback/github` — GitHub OAuth (secret used server-side only; access token read once for the verified email then discarded); `/api/auth/signout`; `/api/auth/dev-login` — deterministic sign-in gated on `AUTH_TEST_MODE` (local/CI only, 404 in prod)
  - `/login`, `/unauthorized` — public auth pages; the admin shell shows the signed-in identity + Sign out
  - `/api/admin/proxy/[...path]` — server-side proxy route; **independently re-checks admin session + allowlist (401/403)** before adding `Authorization: Basic` server-side; path allowlist; adds advisory `X-IB-Admin-Email`/`X-IB-Admin-Name` audit headers (never the OAuth token); rejects unknown paths; sanitizes errors; never exposes credentials to browser
  - `/api/version` — public build-metadata endpoint (Phase 22.3.1); returns `{ app, commit_sha, build_id, build_time, environment }` for deploy verification; build identifiers only, no secrets; `force-dynamic` + `no-store`
  - `src/lib/api.ts` — smart base URL: server components call `BACKEND_API_BASE_URL` directly; client components use `/api/admin/proxy/…`
  - `/admin` — dashboard (health, company count, latest reports)
  - `/admin/companies/new` — create company form
  - `/admin/analysis` — trigger 19-node workflow; full result display
  - `/admin/reports` — draft report list with review_status column (dynamic rendering restored)
  - `/admin/reports/[id]` — report detail with review action panel + event timeline + **rendered markdown preview**
    - `ReviewPanel` (client component) — interactive review buttons, note textarea, warnings
    - Review event timeline — chronological audit log display
    - `MarkdownReportPreview` (client component) — safe rendered markdown (`react-markdown` + `remark-gfm` + `rehype-sanitize`, no `dangerouslySetInnerHTML`) with a Preview/Raw toggle
  - `/admin/backtesting` — backtesting runs list; create run form; run detail with evaluate/refresh
  - `src/types/api.ts` — TypeScript types (includes ReviewActionRequest, ReviewEvent, BacktestRun, BacktestResult, etc.)

#### UI Design System (Phase 22.3 — presentation only)
Modern dark, glassmorphism design system layered over the existing pages. It changes **presentation only** — no analysis, workflow, or report-generation logic, and no public publishing. Shared primitives under `src/components/`:
  - `ui/AnimatedBackground` — decorative CSS aurora background rendered once at the root layout; `aria-hidden`, `pointer-events:none`, disabled under `prefers-reduced-motion`
  - `ui/GlassCard`, `ui/StatusPill`, `ui/SafetyBanner` — translucent panels, status pills, and the mandatory-disclaimer banner (safety copy passed in verbatim)
  - `ui/AppShell` — admin chrome: top compliance strip, glass nav with active-route highlighting, footer disclaimer
  - `reports/MarkdownReportPreview`, `reports/ReportSectionNav`, `reports/markdownUtils` — the sanitized report preview, sticky mini table of contents, and heading-slug helpers
  - `src/app/globals.css` — fixed dark theme palette, aurora keyframes, dark markdown ("prose") styles, and a `prefers-reduced-motion` block that disables decorative motion

#### Admin Auth + Proxy — Request Flow (Phase 17 proxy, hardened in Phase 23)

```text
Browser → /admin/*  or  /api/admin/proxy/*
  → Next 16 Proxy (src/proxy.ts)
       verify httpOnly HMAC session cookie (AUTH_SECRET)
       no session   → pages redirect to /login?callbackUrl=… ; proxy API → 401
       not allowlisted (ADMIN_ALLOWED_EMAILS) → /unauthorized ; proxy API → 403
  → /api/admin/proxy/[...path] route handler
       re-verify session + allowlist (401/403)   [defense-in-depth]
       validate backend path against allowlist (404 otherwise)
       adds Authorization: Basic <base64(BACKEND_BASIC_AUTH)>   [server-only]
       adds X-IB-Admin-Email / X-IB-Admin-Name (advisory audit; NOT the OAuth token)
  → FastAPI backend
       checks STAGING_BASIC_AUTH; reads X-IB-Admin-* only AFTER Basic Auth passes
  → response forwarded back to browser   (Authorization header stripped)

Sign-in:  /login → /api/auth/github → GitHub OAuth → /api/auth/callback/github
          (server-side token exchange; access token discarded after reading the
          verified email) → sets ib_admin_session cookie → back to callbackUrl.
```

Required App Service env vars for `ib-stg-web` (server-only, no `NEXT_PUBLIC_` prefix):
- `AUTH_SECRET` — signs the admin session cookie (Key Vault ref)
- `ADMIN_ALLOWED_EMAILS` — comma-separated admin allowlist
- `AUTH_GITHUB_ID` / `AUTH_GITHUB_SECRET` — GitHub OAuth App (secret in Key Vault)
- `AUTH_TRUST_HOST=true` (+ optional `AUTH_URL`) — resolve OAuth redirect URIs
- `BACKEND_API_BASE_URL` — full URL of the FastAPI backend
- `BACKEND_BASIC_AUTH` — `user:password` matching `STAGING_BASIC_AUTH` on the API
  (now server-to-server defense only; the browser authenticates first)

#### Web Deploy Cache Hardening (Phase 22.3.1)
`ib-stg-web` runs the Next.js standalone bundle with `WEBSITE_RUN_FROM_PACKAGE=1` and `alwaysOn=false`. Under those settings a **statically prerendered homepage `/` could keep serving the previous build** after a deploy until a manual `az webapp restart` flushed it (dynamic routes like `/admin` picked up the new build immediately). Phase 22.3.1 hardens `deploy-web-staging.yml` so a stale homepage is prevented and, if it ever occurs, is caught loudly:

- **Build metadata baked into the bundle** — the workflow injects `NEXT_PUBLIC_COMMIT_SHA` / `NEXT_PUBLIC_BUILD_ID` / `NEXT_PUBLIC_BUILD_TIME` / `NEXT_PUBLIC_APP_ENV` at build time. Next.js statically inlines `NEXT_PUBLIC_*`, so the values are available at runtime on App Service with no runtime configuration. `src/lib/build-info.ts` reads them (with a runtime `COMMIT_SHA` fallback and safe `"unknown"` placeholders).
- **`/api/version` endpoint** — exposes the baked build metadata (build identifiers only, never secrets) so the deployed web commit can be verified from the app itself.
- **`x-ib-build-commit` meta tag** — the root layout embeds the build commit into every page's `<head>`, so a stale prerendered `/` is detectable by comparing the served commit to the deployed SHA.
- **Homepage rendered dynamically** — `src/app/page.tsx` is `force-dynamic`, so `/` always reflects the currently-mounted bundle instead of a cached prerender.
- **Post-deploy restart (best-effort, optional)** — the workflow restarts `ib-stg-web` after deploy when an `AZURE_CREDENTIALS` service principal is configured. A true restart requires the Azure ARM API (Kudu / publish profile cannot restart the site), so with only a publish profile the step is skipped cleanly. Provision `AZURE_CREDENTIALS` (Website Contributor on `ib-stg-web`) once RBAC/OIDC is granted to automate it.
- **SHA-verified smoke check** — the deploy is confirmed only when `/api/version` reports the deployed `github.sha` (multiple consecutive matches), `/` and `/admin` return `200` and contain the dark-UI marker (`bg-[#060913]`), and `/` embeds the current build commit. A `403` ("Site Disabled") is surfaced explicitly rather than treated as transient. This never silently false-greens on a stale worker.

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
| Phase 22.3 | ✅ Complete | UI Modernization + Markdown Report Preview (frontend/UI only): dark glassmorphism design system, animated aurora background (reduced-motion safe), `GlassCard`/`StatusPill`/`SafetyBanner`/`AppShell` primitives, safe `MarkdownReportPreview` (`react-markdown`+`remark-gfm`+`rehype-sanitize`, no `dangerouslySetInnerHTML`) with Preview/Raw toggle + mini TOC replacing the raw `<pre>`; all pages + homepage restyled; disclaimers preserved verbatim; no backend/report-semantics changes, no public publishing; 55 Playwright tests passing |
| Phase 22.3.1 | ✅ Complete | Web Deploy Cache Hardening (deploy/CI + frontend verification only): `/api/version` build-metadata endpoint, `x-ib-build-commit` `<meta>` tag, homepage `force-dynamic`, `deploy-web-staging.yml` bakes `NEXT_PUBLIC_*` build metadata + best-effort (optional `AZURE_CREDENTIALS`) restart + SHA-verified stale-homepage smoke check; prevents/detects the stale prerendered `/` under `WEBSITE_RUN_FROM_PACKAGE`; no backend/report-semantics changes, no secrets, no public publishing |
| Phase 25 | ✅ Complete | Real Market Candidate Discovery (internal-only): `discovery_runs` + `discovery_candidates` (migration 010); deterministic `discovery_scoring_service` (momentum/catalyst/fundamentals/source-quality/completeness − risk penalty, internal prioritization only); `discovery_signal_extractor` reusing the company-analysis workflow per ticker (injectable → offline CI); `market_discovery_service` orchestrator (universe validated against `DISCOVERY_MAX_UNIVERSE_SIZE`, non-blocking per-ticker failures, forbidden-term safety scan); 6 admin-only `/api/v1/market-discovery/*` endpoints; `/admin/discovery` dark-glass UI (start-run form + runs table + ranked candidate queue + inline detail + "Run Full Analysis"); 51 backend + 15 Playwright tests; no BUY/SELL/HOLD/WATCH, no price targets/fair value/upside, human review required, non-public |
| Phase 25.1 | ✅ Complete | Async Discovery Run Execution (UX/ops hardening, no migration): `POST /market-discovery/runs` returns a `pending` run immediately and processes tickers via FastAPI `BackgroundTasks` in a fresh DB session (`create_pending_run` + `process_run` + `process_discovery_run_by_id`), committing progress per ticker; run schema gains computed `progress_pct` + `is_async`/`message`; `/admin/discovery` polls `GET /runs/{run_id}` with a live progress bar; reprocess/double-run guards; removes multi-ticker `504` under the single B1 worker; `BackgroundTasks` process-local (durable queue deferred); 27 backend + 9 Playwright tests; safety unchanged |
| Phase 23 | ✅ Complete | Admin/Auth Hardening (no migration): `/admin/*` + `/api/admin/proxy/*` require an authenticated, allowlisted admin. Dependency-free HMAC-signed httpOnly session cookie (`src/lib/auth/*`, `AUTH_SECRET`); GitHub OAuth sign-in (`/api/auth/github` + callback; secret server-side only, access token discarded after reading the verified email); env allowlist (`ADMIN_ALLOWED_EMAILS`); Next 16 **Proxy** `src/proxy.ts` (→ `/login` / `/unauthorized`; 401/403 on proxy API); proxy route independently re-checks + adds advisory `X-IB-Admin-*` audit headers before Basic Auth; `/login` + `/unauthorized` pages; admin shell identity + Sign out; `AUTH_TEST_MODE` deterministic dev/CI sign-in. Backend Basic Auth retained (`install_staging_basic_auth`; identity headers never trusted without Basic Auth). 13 backend (1236 total) + 15 auth Playwright specs; no public publishing, no recommendation output, safety unchanged |

---

## What Is Not Yet Implemented

- **TrendSignalEngine in main workflow** — `TrendSignalEngine` exists but is not yet wired into `company_analysis`; Phase 19.2
- **Price data visible in T5 source-tier summary** — EODHD /eod price not visibly confirmed in staging smoke test; Phase 19.2
- **Stooq fallback on Azure** — Stooq appears blocked from Azure outbound; `free_real` provider needs non-blocking fallback to EODHD price-only; Phase 19.2
- **Composite provider_name tracking** — workflow metadata does not yet preserve composite provider names (e.g. `"free_real: stooq+sec_edgar"`); Phase 19.2
- **Admin auth hardening** — staging uses Basic Auth + proxy; Clerk/allowlist route-level auth is Phase 23
- **News + catalyst discovery workflow** — ✅ **Delivered (Phase 24):** `catalyst_discovery_agent` wired into `company_analysis` (SEC recent filings T2 + company press releases T1 + optional news T5; deterministic classifier; report sections + council context; final-report `news_catalyst_discovery`). Optional paid/high-quality news provider + LLM-assisted event summarization remain future enhancements
- **Real market candidate discovery** — ✅ **Delivered (Phase 25):** bounded internal-only `market_discovery_service` + `discovery_scoring_service` (momentum + fundamentals + catalyst + source-quality + completeness, internal prioritization only) over a curated seed / manual universe; `discovery_runs` + `discovery_candidates` (migration 010); 6 admin-only `/api/v1/market-discovery/*` endpoints + `/admin/discovery` UI. Full-market crawl and scheduling remain out of scope
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
