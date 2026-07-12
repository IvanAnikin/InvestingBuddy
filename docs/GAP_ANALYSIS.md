# Gap Analysis

**Last updated:** 2026-07-12  
**Current state:** Phase 19.2 Real Price + Trend Workflow Integration Fix released; Phase 19.1 Free Real Data Stack merged; Phase 22.1 Admin Backtesting UI live.  
**Next phase:** Phase 23 Auth or Phase 24 News/Catalyst Discovery

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
- Admin approve/reject/revision workflow with immutable audit log
- Internal final report generator (19 sections; safety gate; admin-only)
- Report validation and safety gates (forbidden-term scan; `human_review_required=True` enforced on safety violations)
- Analysis council (bull case, bear case, risk, valuation guard, committee chair)
- Source taxonomy (T1–T6 tier system)
- Admin proxy (Next.js server-side; credentials never in browser)
- Staging Basic Auth
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

### Important caveats

- Phase 19.1 free real-data provider stack is complete and working on staging
- Phase 19.2 wired `TrendSignalEngine` into the workflow; `provider=free_real` now has non-blocking Stooq → EODHD fallback for Azure environments where Stooq is blocked
- EODHD free plan covers `/eod` prices only; `/fundamentals` returns 403 on free plan
- No public reports are live yet
- News/catalyst discovery is not yet fully wired into the analysis workflow
- Public report publishing is not yet implemented
- User accounts, paid plans, and personalized reports are not yet implemented
- Backtesting uses `MockHistoricalOutcomeProvider` only; live historical outcome data is not yet connected

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

**Phase:** 19.2 → 19.2.1

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

## Gap 3: Real Market Candidate Discovery Engine (Phase 25)

**Current:** `CompanyScreener` and `CompanyDiscoveryService` exist with mock and EODHD-search-based screening (6 themes). Foundation is solid but discovery is not driven by real price/fundamental signals.

**Target:** Market-wide candidate ranking using real price momentum, fundamentals quality, catalyst recency, and sector context. Surfaced candidates enter an admin review queue automatically.

**Gaps:**
- Market-wide screener using real price + SEC data (replaces EODHD search discovery)
- Multi-signal ranking: momentum + fundamentals + catalyst recency + sector context
- Automated candidate queue with admin review + promote/reject actions
- Source tier enforced throughout: T5 for aggregated data, T2 for SEC-derived data

**Phase:** 25

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

## Gap 5: Admin Auth Hardening (Phase 23)

**Current:** Staging uses HTTP Basic Auth + Next.js server-side proxy. `BACKEND_BASIC_AUTH` protects all backend routes. This is functional but not production-grade admin access control.

**Target:** Clerk-based admin authentication with allowlist, route-level middleware, and access audit trail. `STAGING_BASIC_AUTH` removed from production path.

**Gaps:**
- Clerk integration for admin routes
- Admin allowlist enforcement (not open signup)
- Route-level authentication middleware
- Access audit trail for admin actions
- Key Vault cleanup of Basic Auth credential

**Phase:** 23

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
