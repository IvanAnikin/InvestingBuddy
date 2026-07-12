# Gap Analysis

**Last updated:** 2026-07-12  
**Current state:** Phase 19.1 Free Real Data Stack released on staging; Phase 22.1 Admin Backtesting UI live; safety fix applied.  
**Next phase:** 19.2 — Real Price + Trend Workflow Integration Fix

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

- Full real-data trend workflow is **not complete** — `TrendSignalEngine` exists but is not wired into `company_analysis` (Phase 19.2)
- `provider=free_real` **failed on staging** — Stooq appears blocked from Azure outbound network; works correctly from local environments
- `provider=eodhd_free_real` worked partially — EODHD /eod price data was not visibly confirmed in T5 source-tier summary during the smoke test
- `provider_name` tracking for composite providers needs cleanup in workflow metadata
- EODHD free plan covers `/eod` prices only; `/fundamentals` returns 403 on free plan
- No public reports are live yet
- News/catalyst discovery is not yet fully wired into the analysis workflow
- Public report publishing is not yet implemented
- User accounts, paid plans, and personalized reports are not yet implemented
- Backtesting uses `MockHistoricalOutcomeProvider` only; live historical outcome data is not yet connected

---

## Gap 1: Real Price + Trend Workflow (Phase 19.2)

**Current:** `TrendSignalEngine` and `EodhdPriceOnlyProvider` exist. SEC EDGAR fundamentals work on staging. Stooq is blocked from Azure outbound. EODHD /eod price data not visibly confirmed in T5 summary during staging test.

**Target:** A full free-real analysis run on staging produces SEC fundamentals (T2) + price data (T5) + trend signals (T6) in the workflow state, and a final internal report with `safety_valid=True`.

**Gaps:**
- Wire `TrendSignalEngine` as a dedicated workflow node
- Make EODHD /eod price data visible as T5 in source-tier summary
- Make Stooq failure non-blocking; fall back to EODHD price-only when Stooq is unavailable on Azure
- Preserve composite `provider_name` in workflow metadata (e.g. `"free_real: stooq+sec_edgar"`)
- Verify AAPL `provider=free_real` end-to-end on staging after fix

**Phase:** 19.2

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
