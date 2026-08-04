# Gap Analysis

**Last updated:** 2026-07-19  
**Presentation state:** Phase 22.3 UI Modernization + Markdown Report Preview delivered (frontend/UI only) — the web/admin UI now uses a dark glassmorphism design system with a reduced-motion-safe animated background, and report content renders through a sanitized markdown preview (`react-markdown` + `remark-gfm` + `rehype-sanitize`, no `dangerouslySetInnerHTML`) with a Preview/Raw toggle, replacing the raw `<pre>` block. No backend, workflow, or report-generation logic changed; no public publishing added; all internal-only / not-investment-advice / human-review disclaimers preserved; no BUY/SELL/HOLD/WATCH, price target, fair value or upside.  
**Deploy state:** Phase 22.3.1 Web Deploy Cache Hardening delivered (deploy/CI + frontend verification only) — fixes the stale prerendered homepage `/` seen under `WEBSITE_RUN_FROM_PACKAGE=1` + `alwaysOn=false`: added `/api/version` build-metadata endpoint + `x-ib-build-commit` `<meta>` tag, homepage rendered `force-dynamic`, CI bakes `NEXT_PUBLIC_*` build metadata, best-effort post-deploy restart (optional `AZURE_CREDENTIALS`), and a SHA-verified smoke check that fails loudly on a stale `/` or `/admin`. Build identifiers only, no secrets; no backend/report semantics changed.  
**Data state:** Phase 19.4.1 Enrichment Completeness Consistency delivered — `research_completeness_agent` now consumes the enriched snapshot so present enriched fields (LEI, sector classification, derived market cap/EV/P-E, 52-week range, shares) are no longer reported as missing/blocking gaps, and `source_quality_agent` only recommends "Obtain LEI" when the LEI is actually absent; genuinely-missing fields (ISIN, EBITDA, EV/EBITDA, beta, website, IPO date) stay gaps and nothing is fabricated. On top of Phase 19.4 Identity + Sector + Market-Metric Enrichment (`company_profile_enrichment`: sector from DB or inferred SEC SIC/T6, industry/website SEC/T2, LEI GLEIF/T2 name-guarded; `market_metrics_enrichment`: latest close + 52-week range/T5, shares SEC DEI/T2, market cap/EV/P-E as DERIVED ESTIMATES/T6 when inputs exist; valuation still `partial` with conclusions blocked), Phase 19.3.1 SEC Freshness + Review Consistency and Phase 19.3 SEC Fundamentals Normalization; Phase 22.1 Admin Backtesting UI live.  
**Research state:** Phase 24.1.2 Press-Release Canonical Link Fix delivered — company press-release `source_url` is now always the canonical article page (Apple's Atom image enclosures / `…jpg.og.jpg` tiles are rejected; the image is kept separately as `media_url`), with `source_url_quality` provenance and canonical-URL dedup — improving catalyst evidence quality ahead of Phase 25 candidate discovery. Underlying Phase 24.1.1 News Provider + Feed-Status Consistency delivered — fixes the AAPL `filings_only` inconsistency where a discovered press-release feed was reported as "no feed found": the press provider tries the discovered feed URL first, lookback-filters, and reports a precise `PressReleaseStatus`; stale curated Apple/Amazon feed URLs corrected; `missing_sources`/coverage now reflect usable events only; the no-key GDELT path (`NEWS_PROVIDER_NAME=gdelt`) is confirmed. Underlying Phase 24.1 Real News + Company Source Enablement — on top of Phase 24, `discover_catalysts` now runs company source discovery (curated verified issuer allowlist + `profile.website` + SEC/GLEIF + optional configured search → company website / IR / newsroom / press-release feed, T1 when verified, never fabricated), exchange-aware recommendation-free query planning, a configurable real news/search provider (`ConfigurableWebNewsProvider` env-key JSON or no-key `GdeltNewsProvider`, non-blocking), deterministic relevance scoring separating company-specific catalysts from industry/sector context, and source-class-aware coverage (`filings_only`→`limited`/`adequate`/`strong`). New **Company News Sources** + **Industry Context News** report sections; exchange pages stay T3, aggregator news stays T5, catalyst label stays T6; no paid provider required; no live CI call; safety unchanged. Underlying Phase 24 — a source-backed catalyst subsystem runs for `free_real`/`eodhd_free_real` (mock unchanged): `SecRecentFilingsProvider` (recent filings + 8-K item mapping, T2), a company press-release/IR provider (T1), and an optional `NullNewsProvider`-by-default news abstraction (T5, no paid dependency). The deterministic `catalyst_classifier` labels each event's category/direction/strength/evidence (label always T6_model_estimate; evidence keeps its real tier, aggregator never promoted). New report sections (News & Catalyst Discovery, Recent Catalyst Events, SEC Filing Events, Catalyst Evidence Quality, Catalyst Gaps / Next Research Tasks) plus council/committee context and a safety-gated `news_catalyst_discovery` final-report section (external headlines neutralised). No recommendations, price targets, fair values, or upside/downside; human review required; `safety_valid` stays true. **Auth + discovery state:** Phase 23 Admin/Auth Hardening, Phase 25 Real Market Candidate Discovery, and Phase 25.1 Async Discovery Run Execution are all **complete** — `/admin/*` and `/api/admin/proxy/*` require an authenticated, allowlisted admin (GitHub OAuth + `ADMIN_ALLOWED_EMAILS`); admin discovery, candidate detail, and Run Full Analysis all run behind auth; the staging `0.0.0.0` redirect bug was fixed by anchoring auth redirects to `AUTH_URL`. **Next phase:** Phase 26 — Final Report Schema Completion / Publication-Readiness Pipeline (move generated reports from `schema_valid=false` to `schema_valid=true` without weakening safety gates, recommendation restrictions, or the human-review requirement).

This document describes the gap between the current implementation and the target product. Each gap maps to a planned phase.

---

## Current Implementation State

### What is built and working

- Internal admin dashboard (`/admin`)
- Company universe management (add company, list companies)
- Add company workflow (ticker, exchange, name, country, sector, currency)
- 19-node `company_analysis` workflow (v6.0.0): provider data → Research Team → optional LLM → citations → schema validation → Analysis Council → scoring → draft report
- Mock company analysis (all tests offline, CI-safe)
- Draft reports with full Research Team + Analysis Council summaries
- Admin reports UI with review status and dynamic rendering (fixed Phase 22.1 maintenance)
- Modern dark glassmorphism web/admin UI + sanitized markdown report preview with Preview/Raw toggle (Phase 22.3 — presentation only)
- Admin approve/reject/revision workflow with immutable audit log
- Internal final report generator (19 sections; safety gate; admin-only)
- Report validation and safety gates (forbidden-term scan; `human_review_required=True` enforced on safety violations)
- Analysis council (bull case, bear case, risk, valuation guard, committee chair)
- Source taxonomy (T1–T6 tier system)
- Admin proxy (Next.js server-side; credentials never in browser)
- **Admin authentication + allowlist (Phase 23):** `/admin/*` and the admin
  proxy require an authenticated, allowlisted admin (GitHub OAuth → httpOnly
  HMAC session cookie; `ADMIN_ALLOWED_EMAILS`; Next 16 Proxy redirects/401/403;
  `/login` + `/unauthorized`; signed-in identity + Sign out)
- Staging Basic Auth (now server-to-server defense only, behind the admin session)
- Internal judge + backtesting framework (`BacktestingService`, `ResearchJudgeService`)
- Admin backtesting UI (`/admin/backtesting`)
- Phase 19.1 free real-data provider stack:
  - `SecEdgarFundamentalsProvider` (XBRL, ticker→CIK, T2, US companies)
  - `EodhdPriceOnlyProvider` (EODHD /eod free plan, T5)
  - `TrendSignalEngine` (momentum labels, T6 — not yet wired into main workflow)
  - `FreeRealSnapshotComposer`
  - `SecEdgar8KProvider` (news/catalyst interface, T2)
  - Composite providers: `free_real`, `eodhd_free_real`
- SEC EDGAR partial real-data analysis confirmed working on staging (`is_mock=False`)
- `eodhd_free_real` produced partial real data through SEC EDGAR on staging
- Final internal report generation works from partial real (SEC EDGAR) data
- Phase 19.3 SEC fundamentals normalization: `sec_fundamentals_normalizer` maps SEC XBRL companyfacts into normalized income-statement / cash-flow / balance-sheet metrics + derived margins, ROE, debt-to-equity and YoY growth; injected into the `free_real` snapshot and consumed by `FinancialDataAgent` and `ValuationGuardAgent`
- Phase 19.4 identity/sector/market-metric enrichment: `company_profile_enrichment` (sector from DB or inferred SEC SIC, industry/website from SEC, LEI from GLEIF — name-guarded, never fabricated) + `market_metrics_enrichment` (latest close, 52-week high/low, shares outstanding from SEC DEI, derived market cap / enterprise value / P/E as T6 estimates when inputs exist). Resolved fields pruned from `missing_fields`; EBITDA / EV-EBITDA / beta / LEI / ISIN / IPO date never fabricated; valuation stays `partial` with all conclusions blocked
- Phase 19.4.1 enrichment completeness consistency: `research_completeness_agent` consumes the enriched snapshot (`_enriched_present_fields`) so present enriched fields are not reported as missing/blocking gaps and satisfied identity next-steps are dropped; `source_quality_agent` gates the "Obtain LEI" recommendation on the LEI actually being missing and recommends upgrading (not "absent") derived market metrics. Genuinely-absent fields (ISIN, EBITDA, EV/EBITDA, beta, website, IPO date) remain gaps; provider=mock behaviour unchanged

### Important caveats

- Phase 19.1 free real-data provider stack is complete and working on staging
- Phase 19.2 wired `TrendSignalEngine` into the workflow; `provider=free_real` now has non-blocking Stooq → EODHD fallback for Azure environments where Stooq is blocked
- Phase 19.3 sources real fundamentals from SEC XBRL, but **the report is still not fully investor-grade**: EBITDA, market cap, enterprise value, shares outstanding, sector, ISIN/LEI and valuation multiples remain unavailable (Phase 19.4). Valuation readiness reaches `partial` at best; every valuation conclusion stays blocked; `schema_valid` may still be false.
- EODHD free plan covers `/eod` prices only; `/fundamentals` returns 403 on free plan — SEC EDGAR XBRL is the fundamentals source
- No public reports are live yet
- News/catalyst discovery is not yet fully wired into the analysis workflow
- Public report publishing is not yet implemented
- User accounts, paid plans, and personalized reports are not yet implemented
- Backtesting uses `MockHistoricalOutcomeProvider` only; live historical outcome data is not yet connected
- **LLM council reliability (Phase 32A Slice 4) is CLOSED + STAGING-VALIDATED (`11ab66b`, 2026-08-04).** The single-company LLM council runs sequentially and inline in the request; under Azure `gpt-4.1-mini` TPM limits a large evidence pack previously left ~4/8 agents `failed` (the "Azure-TPM partial councils" note carried through Slices 2–3). Slice 4 adds bounded transient-error retries under a total wall-time budget, a reserved budget for `red_team` + `committee_chair`, and a deterministic committee-chair fallback (`committee_label="insufficient_data"`, no recommendation/valuation/citations), behind the `LLM_COUNCIL_RETRY_ENABLED` flag (kept ON; no DB migration; head stays `012`). Staging validation lifted AAPL to **8/8** where capacity permits and correctly degraded to the deterministic fallback under harder pressure. Two non-blocking follow-ups remain: non-critical `valuation_guard` is not retried (as-designed; immaterial where there is no financial evidence, e.g. CFR), and worst-case wall-clock ~152s at the 150s budget ceiling (comfortable margin; monitor p99). See `docs/development/closures/phase-32a-slice4.md` + `docs/development/PHASE_LEDGER.md` row `32A.4`.
- **Native primary-document ingestion (Phase 32A Slice 5A) is CLOSED + STAGING-VALIDATED as a FOUNDATION (`354a5ba`, 2026-08-04) — WITH AN EFFICACY CAVEAT. The complete Slice 5 and Phase 32A are NOT closed; Slice 5B remains open.** Deepens the Phase 29B.2 document-extraction capability: an ingestion hierarchy (structured SEC/XBRL, unchanged → deepened HTML tables/sections → native-PDF text + table extraction with page/table location via `pdfplumber` → OCR fallback), running before the council under an aggregate wall-budget, with extraction persisted (migration `013`: `extracted_documents` / `extracted_facts`, **applied + verified on staging**) and reused across regeneration; stricter validation for table/OCR-derived facts (non-validated → `excerpt_only`, never a fact); a `primary_document` evidence floor/cap that does not weaken the Slice-2 financial floor; and SSRF/DNS-rebinding + magic-byte + decompression-bomb + Pillow-pixel-cap hardening. Gated by default-OFF `PRIMARY_DOCUMENT_INGESTION_ENABLED` (flipped ON + kept ON on staging); OFF is byte-identical. **Staging validation:** the pipeline is live, bounded, safe, and fails honestly (all safety/scoping/security/wall-clock/invariant criteria pass; migration + OFF/ON behavior validated; secrets clean) — **but no successful native extraction was demonstrable on staging.** Across 7 registered issuers (AAPL, CFR, BA, BRBY, KER, MC, RMS) 0 documents extracted: AAPL had no accessible native document (SEC 10-K/20-F body fetch deferred), CFR's IR PDFs are encrypted (need OCR — a NoOp seam this slice, OFF), and the luxury/UK issuers' IR index pages are JS-gated (no static document links). The extraction/provenance/persistence/reuse success path is covered by 102 unit tests but is not yet observable in production. **Slice 5B (deferred, do NOT auto-start):** (1) real Azure Document Intelligence OCR adapter + wiring; (2) SEC 10-K/20-F body fetch; (3) **JS-capable / direct-document-URL link discovery** (new — modern SPA IR pages expose no static links); (4) frontend rendering of the extracted-document / provenance / appendix fields; (5) resolve-then-connect IP-pinning + async DNS (close the ADR-014 DNS-rebinding TOCTOU before prod). See `docs/development/closures/phase-32a-slice5a.md` + `docs/DECISIONS.md` ADR-014 + `docs/development/PHASE_LEDGER.md` row `32A.5A`.

---

## Gap 1: Real Price + Trend Workflow ✅ RESOLVED — Phase 19.2

**Resolved:** 2026-07-12

**What was fixed:**
- `TrendSignalEngine` is now wired into `node_fetch_provider_data` for composite providers (`free_real`, `eodhd_free_real`); trend signals (T6) surface in workflow state, draft report, and enriched snapshot
- `FreeRealProvider.get_price_history()` uses non-blocking 3-attempt fallback: Stooq → EODHD price-only → empty `PriceHistoryData` with warning; Stooq failure does not abort the analysis run on Azure
- EODHD /eod price data now visible as T5 in source-tier summary via `enrich_snapshot_with_free_real()`
- SEC EDGAR fundamentals visible as T2 in source-tier summary and as a separate source record in `node_create_source_records`
- Composite `provider_name` preserved throughout workflow (e.g. `"free_real"`); sub-provider names tracked separately in `contributing_providers` state field
- `requested_provider_name` and `contributing_providers` added to `CompanyAnalysisState`
- `enrich_snapshot_with_free_real()` added to `snapshot_builder.py`
- `FreeRealSnapshot.contributing_providers` tracks which sub-providers actually contributed real data
- 31 tests added in `test_phase19_2_real_price_trend_workflow.py`; all offline, no network

**Phase 19.2.1 follow-up (observability + deploy hardening):** ✅ 2026-07-12
- Stooq→EODHD fallback reason now surfaced in the report's Provider Warnings section (`summarize_price_provider_warning()` in `free_real_snapshot.py`); previously the reason stayed buried in `price.meta.note`
- `scoring_engine.score_company_analysis` no longer raises `TypeError` when `sector` is `None` (SEC EDGAR profiles omit sector) — extraction coalesces to `""` and `_score_theme_alignment_from_context` guards defensively
- Deploy health-check hardened: `/health` exposes `commit_sha`/`build_id`; deploy smoke check requires 3 consecutive SHA-matched responses (no false-green on async recycle) + Oryx/runtime boot-failure detection
- 20 tests added in `test_phase19_2_1_hardening.py`; `docs/DEPLOYMENT.md` documents the intentional `--workers 1` on B1

**Phase 22.3.1 follow-up (web deploy cache hardening):** ✅ 2026-07-16
- Applies the same SHA-verified deploy pattern to the **web** app: `/api/version` exposes `commit_sha`/`build_id` (baked into the bundle as `NEXT_PUBLIC_*`), and the `deploy-web-staging.yml` smoke check requires 3 consecutive SHA-matched `/api/version` responses before passing — no more false-green on a stale `WEBSITE_RUN_FROM_PACKAGE` worker
- Stale prerendered homepage `/` fixed at the source (`force-dynamic`) and detected (`x-ib-build-commit` `<meta>` + dark-UI marker check on `/` and `/admin`)
- Best-effort post-deploy `az webapp restart` gated on an optional `AZURE_CREDENTIALS` service principal (Kudu/publish-profile cannot restart the site); skipped cleanly when absent
- **Remaining gap:** automatic restart is inert until `AZURE_CREDENTIALS`/OIDC is provisioned (blocked pending Azure Owner/RBAC grant); until then a detected stale homepage fails the deploy with a manual `az webapp restart` hint rather than auto-recovering
- New `apps/web/tests/e2e/version-endpoint.spec.ts` covers the endpoint contract (app name, all fields, safe placeholders, no-secret allow-list, `no-store`, homepage build-commit meta)

**Phase:** 19.2 → 19.2.1 → 22.3.1

---

## Gap 1b: Financial Fundamentals in the Report ⚙️ PARTIALLY RESOLVED — Phase 19.3

**Resolved (fundamentals extraction):** 2026-07-13

**The gap before Phase 19.3:** The Phase 19.2 `free_real` report was technically successful (`is_mock=false`, `safety_valid=true`) but **not user-valuable**. Raw SEC XBRL datapoints were fetched but never mapped into financial-statement fields, so the report said *"No financial fundamentals sourced at this phase"*, `internal status=research_incomplete`, `valuation_readiness=not_ready`, and financial analysis was empty.

**What Phase 19.3 fixed:**
- `sec_fundamentals_normalizer.normalize_company_facts()` maps us-gaap companyfacts into normalized metrics: revenue, gross/operating/net income, EPS, operating cash flow, capex, free cash flow, total assets/liabilities/equity, cash, short/long-term/total debt, plus derived gross/operating/net margin, ROE, debt-to-equity, FCF margin and revenue/net-income/FCF YoY growth
- Latest annual (10-K/20-F) preferred; latest 10-Q used as a labelled fallback; annual data is **never mislabelled TTM**
- Normalized fundamentals injected into `fundamentals_summary` via `enrich_snapshot_with_free_real()`
- `FinancialDataAgent` recognizes ~10 sourced financial categories and narrates them; report no longer says *"No financial fundamentals sourced at this phase"*
- `ValuationGuardAgent` moves `not_ready → partial` with core statement inputs from T1/T2, with more specific blockers
- `missing_information` count for financial fields materially decreases
- 22 offline tests; AAPL fixture enriched with gross profit, operating income, capex, cash, prior-year OCF

**Safety held:** EBITDA never fabricated; no market cap/EV without shares; no BUY/SELL/HOLD/WATCH, price target, fair value or upside; `human_review_required=true`; `schema_valid` may still be false.

**What remains (Phase 19.4):** sector/industry, shares outstanding → market cap, enterprise value, 52-week range, ISIN/LEI, valuation multiples. Until then the report is *"SEC-derived fundamentals available; valuation readiness partial"* — a meaningful step toward investor-grade research, not the finished product.

**Phase:** 19.3 → 19.4

---

## Gap 2: News + Catalyst Discovery (Phase 24)

**Current:** `SecEdgar8KProvider` interface exists and fetches recent 8-K filings. `NullNewsCatalystProvider` is the default (safe, returns empty). No catalyst signals are wired into the analysis workflow.

**Target:** Recent news and catalyst signals (8-K filings, press releases, optional news API) are fetched, classified, scored, and surfaced in the final internal report.

**Gaps:**
- `NewsCatalystAgent` node in `company_analysis` workflow
- Expand `SecEdgar8KProvider` to parse and classify filing types (earnings, guidance, material events)
- Optional: integrate free news API (GDELT, NewsData, or Alpha Vantage News) as T5 source
- Catalyst scoring incorporated into `ScoringEngine`
- Catalyst signals visible in final internal report

**Phase:** 24

---

## Gap 3: Real Market Candidate Discovery Engine (Phase 25) — ✅ Delivered

**Delivered (Phase 25):** A bounded, internal-only market discovery workflow
now produces an internal research-candidate queue. `discovery_runs` +
`discovery_candidates` tables (migration 010); a deterministic
`discovery_scoring_service` (momentum + catalyst + fundamentals + source-quality
+ completeness − risk penalty, 0–100, internal prioritization only); a
`discovery_signal_extractor` reusing the tested company-analysis workflow per
ticker (injectable → offline CI); a `market_discovery_service` orchestrator
(universe validated against `DISCOVERY_MAX_UNIVERSE_SIZE`, non-blocking per-ticker
failures); 6 admin-only `/api/v1/market-discovery/*` endpoints; and a
`/admin/discovery` UI with a ranked candidate queue and a "Run Full Analysis"
promote action. Source tiers preserved end-to-end (T1 company / T2 SEC /
T5 aggregators / T6 model). Multi-signal ranking is implemented directly on the
real `free_real` snapshot rather than replacing the Phase 14 EODHD screener.

**Async execution (Phase 25.1):** `POST /market-discovery/runs` now returns a
`pending` run immediately and processes tickers in the background (FastAPI
`BackgroundTasks` in a fresh DB session, progress committed per ticker); the
`/admin/discovery` UI polls run status with a live progress bar. This removes the
gateway `504` a multi-ticker `free_real` run could hit under the single B1
worker. `BackgroundTasks` are process-local (not durable across an App Service
restart) — acceptable for the MVP; a durable queue (Service Bus / Functions) is a
later enhancement. No migration; safety unchanged.

**Remaining (future enhancements):** no full-market crawl or scheduling (curated
seed + manual tickers only); no automated promote/reject state machine (manual
"Run Full Analysis" only); no durable background-job queue yet; free providers
remain incomplete.

**Phase:** 25 (complete) + 25.1 async execution (complete)

---

## Gap 4: Public Report Publishing Website (Phase 26)

**Current:** All reports are internal admin drafts. The admin can approve/reject reports, but there is no public report library or detail pages. No `/publish` endpoint exists.

**Target:** Only human-approved internal reports are published publicly. Public pages show report summaries with source attribution and regulatory disclaimers. No price targets, fair values, or BUY/SELL/HOLD/WATCH on public pages.

**Gaps:**
- `POST /api/v1/admin/reports/{id}/publish` endpoint
- Publish requires: `safety_valid=True` + `review_status=approved_internal` + explicit admin publish action
- Public report list page
- Public report detail page
- Regulatory disclaimer on all public pages ("for informational purposes only; not investment advice")
- SEO: sitemap.xml, metadata, OpenGraph tags

**Phase:** 26

---

## Gap 5: Admin Auth Hardening (Phase 23) — ✅ RESOLVED

**Was:** Staging used HTTP Basic Auth + Next.js server-side proxy only. Anyone
could load `/admin/*` and the admin proxy without authentication; the browser
relied on the proxy injecting `BACKEND_BASIC_AUTH` server-side. Functional, but
not admin access control.

**Delivered (Phase 23):** `/admin/*` and `/api/admin/proxy/*` now require an
authenticated, allowlisted admin.

- **GitHub OAuth** sign-in (not Clerk — chosen as the simplest env-configurable
  provider with no heavy dependency and no Next 16 / `next-auth` beta risk). The
  OAuth secret is used only in the server-side token exchange; the access token
  is read once for the verified email then discarded — never stored or forwarded.
- **Session:** dependency-free HMAC-SHA256-signed **httpOnly** cookie
  (`AUTH_SECRET`); constant-time verify; fails closed.
- **Allowlist enforcement** via `ADMIN_ALLOWED_EMAILS` (not open signup);
  non-allowlisted users → `/unauthorized` (pages) / **403** (proxy API).
- **Route-level middleware:** the Next 16 **Proxy** (`src/proxy.ts`) plus an
  independent re-check in the admin proxy route (defense-in-depth).
- **Audit trail:** advisory `X-IB-Admin-Email`/`X-IB-Admin-Name` headers logged
  for mutating admin actions (only after backend Basic Auth passes; never
  trusted for auth).
- **Backend Basic Auth retained** as server-to-server defense (`STAGING_BASIC_AUTH`).

**Remaining (future, out of Phase 23 scope):** Microsoft Entra ID option; moving
Basic Auth entirely out of the production path once mutual auth is in place.

**Phase:** 23 — done.

---

## Gap 6: User Accounts + Watchlists (Phase 27)

**Current:** No user authentication, signup, or dashboard. The platform is internal-only with no public user accounts.

**Target:** Users can sign up, log in, follow companies, save watchlists, and opt in for report notifications.

**Gaps:**
- Clerk user authentication
- User dashboard
- Company watchlists
- Email notification opt-in for new approved reports
- Strict separation: user data never leaks into public research tables

**Phase:** 27

---

## Gap 7: Paid Plans + Stripe (Phase 28)

**Current:** No subscription management or billing. No feature gating.

**Target:** Subscription tiers gate access to premium features (earlier report access, custom report requests). No tier unlocks automated investment advice.

**Gaps:**
- Stripe integration for subscription management
- Free / Paid tier differentiation
- Usage limits per tier
- Admin billing dashboard (basic)

**Phase:** 28

---

## Gap 8: Personalized Research Reports (Phase 29)

**Current:** Not built. All reports are generic (not user-preference-aware). Personalized reports are a Version 2 feature.

**Target:** Users on paid plans can request custom internal research reports based on their preferences and areas of interest. Reports still pass safety gate and require human review before delivery. Output is labeled "internal research candidate" — not personalized investment advice.

**Gaps:**
- User preference storage (sectors, regions, themes)
- Portfolio Fit Agent skeleton
- Personalized candidate filtering from discovery queue
- Custom report request queue
- Private user dashboard with report history

**Phase:** 29

---

## Gap 9: Monitoring, Alerts + Thesis Tracking (Phase 30)

**Current:** `thesis_tracking_events` table exists (Phase 22 foundation). `BacktestingService` provides historical quality scoring. No automated monitoring workflow runs.

**Target:** Monitored companies are checked periodically. Significant events (8-K filings, price moves) trigger re-analysis and admin alerts. Backtesting results inform ongoing research quality measurement.

**Gaps:**
- Automated monitoring workflow (Azure Functions scheduled trigger)
- Event-triggered re-analysis for monitored companies
- Admin alert queue for significant events
- Integration of backtesting results into research quality feedback loop

**Note:** Backtesting does not predict future results. All monitoring output is internal only.

**Phase:** 30

---

## Safety and Compliance Invariants (All Phases)

The following must be true at every phase — they are non-negotiable constraints:

- No automated public BUY/SELL/HOLD/WATCH recommendations
- No unreviewed price targets, fair values, or upside percentages on any public surface
- Human review required for all reports before publication
- Public reports require explicit admin publish action (not auto-publish)
- Backtesting does not predict future results — disclaimer enforced
- Personalized reports are future paid functionality — not live, not promised
- All financial data claims must carry source, date, currency, and source tier
- Safety gate (`run_safety_gate`) must pass before any report is approved
- `human_review_required=True` is forced when committee safety guard triggers
