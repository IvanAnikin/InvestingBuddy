# Gap Analysis

> **STATUS AS OF 2026-08-23 (staging).** This document previously described the
> pre-Phase-32A world and contradicted deployed reality. The summary below is
> authoritative; the historical per-gap sections beneath it are retained for
> audit and are NOT a current to-do list.
>
> ### CLOSED (merged, deployed to staging, live-verified)
> | Item | Closed by |
> |---|---|
> | Browser HTTP 504 on Run Full Analysis | async job (#119), ADR-018 |
> | Self-contradicting report / `available_count=0` | canonical evidence (#121/#123), ADR-019 |
> | Price provenance inheriting the company provider | #121 (`resolve_price_provenance`) |
> | Catalyst date sort dropping SEC filings | `event_sort_key` normalisation |
> | Safety substring false positives (`sell-side`, `Watchmakers`) | shared `safety_terms` |
> | Council TPM starvation / chair failures | token pacing + async-era budgets (#124/#125), ADR-020 |
> | Field-review chair output truncation | output-budget scaling + bounded contract (#126-#128) |
> | Per-call provider budget silently dropped | `max_completion_tokens` forwarding (#130) |
> | Producer/consumer contract drift | typed evidence contracts (#131), ADR-021 |
> | Source-quality label disagreement, warning wall, SEC-centric copy, sector conflict | Phase C, ADR-022 |
>
> ### OPEN (genuinely outstanding)
> | Item | Notes |
> |---|---|
> | European source depth | Nasdaq Nordic / LSE / Euronext ingestion — Phase D |
> | Durable job queue | BackgroundTasks are process-local; Service Bus is scale work |
> | Claim-strength discipline | moat/governance claims from single anecdotes |
> | Catalyst materiality ranking | marketing posts rank beside financing events |
> | Observability dashboard | events exist; no aggregated view |
> | Live OCR invocation proof | unblocked by the async job, still unobserved |
>
> ---


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
- **Three distinct councils, never conflated** (code, API, admin UI and docs all keep them separate):
  - **Discovery Council** (Phase 28B) — reviews **one discovery run's candidate list**, runs **before** any full analysis exists, over shallow candidate signals
  - **Company Council** (Phase 28A) — reviews **one company**, runs during that company's analysis, over that company's evidence pack
  - **Deep Field Review** (Phase 32A Slice 6D, NEW) — a **comparative** review of **several companies from one discovery run**, runs **after** 2+ of them already have a **completed** full analysis, reading only their **already-persisted reports**. It never re-analyses, re-fetches or recomputes; it produces an internal **research-priority shortlist** (`strongest_candidates` / `second_tier` / `blocked_insufficient_evidence`) — never ratings, price targets or valuations. Async admin API (`POST`/`GET /api/v1/discovery-runs/{run_id}/field-review`) + a distinctly-labelled admin UI panel; gated by `LLM_FIELD_REVIEW_COUNCIL_ENABLED`
- **Bounded reliability on all three councils:** the shared retry engine (`app/services/llm/retry_engine.py`) gives the company council (Slice 4), the discovery council (Slice 6A) and the Deep Field Review (Slice 6D) transient-only retries, capped backoff, honored `retry-after`, a total wall-budget with a reserved critical budget, and a **deterministic chair fallback** that degrades honestly (states which agents did and did not complete) rather than fabricating consensus

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
- **Native primary-document ingestion (Phase 32A Slice 5A) is CLOSED + STAGING-VALIDATED as a FOUNDATION (`354a5ba`, 2026-08-04) — WITH AN EFFICACY CAVEAT.** Deepens the Phase 29B.2 document-extraction capability: an ingestion hierarchy (structured SEC/XBRL, unchanged → deepened HTML tables/sections → native-PDF text + table extraction with page/table location via `pdfplumber` → OCR fallback), running before the council under an aggregate wall-budget, with extraction persisted (migration `013`: `extracted_documents` / `extracted_facts`) and reused across regeneration; stricter validation for table/OCR-derived facts (non-validated → `excerpt_only`, never a fact); a `primary_document` evidence floor/cap that does not weaken the Slice-2 financial floor; and SSRF/DNS-rebinding + magic-byte + decompression-bomb + Pillow-pixel-cap hardening. Gated by default-OFF `PRIMARY_DOCUMENT_INGESTION_ENABLED` (flipped ON + kept ON on staging); OFF is byte-identical. Staging validation at the time: safe, bounded, honest — but **0 successful native extractions across 7 registered issuers** (AAPL had no accessible document at all; CFR's IR PDFs were encrypted; the luxury/UK issuers' IR pages were JS-gated). See `docs/development/closures/phase-32a-slice5a.md` + `docs/development/PHASE_LEDGER.md` row `32A.5A`.
- **Document reachability and secure fetching (Phase 32A Slice 5B.1) is CLOSED + STAGING-VALIDATED (`1e26773`→`30a4737`→`0cffc87`, 2026-08-05).** Resolves every root cause behind Slice 5A's 0/7 efficacy caveat: (1) official SEC filing-BODY fetch (`sec_filing_documents.py`) — SEC/XBRL stays authoritative, the body only supplements; (2) bounded, non-headless-browser, non-crawling document discovery (`document_discovery.py`: anchors → JSON-LD → Next.js/Nuxt hydration state → embedded script JSON) that found real documents on richemont.com where the Slice-5A anchor-only scan found none; (3) durable, honest ingestion-attempt persistence (migration `014`, `document_ingestion_attempts`, closed sanitized status/failure_code vocabularies) so a failure is never silent again; (4) resolve-then-connect IP pinning + async DNS closing the ADR-014 residual, with per-original-hostname connection-pool isolation (found and fixed pre-merge by empirical probe). **Live staging proof, verified by direct SQL and citation-join queries, not report inspection alone:** AAPL — a real SEC 10-Q + 8-K fetched and extracted natively, a validated `cash_and_equivalents` fact persisted with table provenance, 147 citations (42 SEC-typed) across 8 council agents, and a second run correctly REUSED the persisted extraction instead of re-fetching. CFR — bounded discovery found 3 real document candidates (vs. 0 in Slice 5A); the annual report PDF was honestly classified `encrypted` (no password bypass); 2 bilingual ad-hoc results PDFs were genuinely extracted and persisted; zero Apple/AAPL leakage confirmed both directions by SQL. **Required two corrective hotfixes**, each triggered by a real staging failure this validation caught: hotfix 1 fixed CIK resolution (the caller's `CompanyContext.cik` is always `None`, so the SEC path silently no-op'd for every issuer — now derives + cross-checks + fails closed on any identity conflict); hotfix 2 fixed a report-summary display gap (`_DOCUMENT_SOURCE_TYPES` in `council.py` predated the SEC evidence types, so a genuinely successful extraction never showed in `primary_documents` even though its citations resolved correctly — display-only, no fabrication or safety issue). **Slice 5B.2 (real Azure Document Intelligence OCR) closed next (below); Slice 5B.3 (admin web visibility + Phase 32A closure) remains open — Phase 32A is NOT closed.** See `docs/development/closures/phase-32a-slice5b1.md` + `docs/DECISIONS.md` ADR-015 + `docs/development/PHASE_LEDGER.md` row `32A.5B.1`.
- **Real Azure Document Intelligence OCR adapter (Phase 32A Slice 5B.2) is CLOSED + STAGING-VALIDATED as a FOUNDATION (`768da0c`→`3187298`→`6947bcf`→`007d398`, 2026-08-09) — WITH AN EXPLICIT EFFICACY CAVEAT on live OCR invocation.** A real Azure Document Intelligence resource is now provisioned on staging for the first time (`ib-stg-docintel`, F0/free tier, `westeurope`, `ib-stg-rg`), and `AzureDocumentIntelligenceOcrProvider` is live behind the existing `OcrProvider` seam (ADR-014/ADR-016); flag `PRIMARY_DOCUMENT_OCR_ENABLED` flipped absent→`true` and KEPT ON. **What IS live-proven:** the gating double-check (flag + configured endpoint), connectivity against the real resource, the cross-document `OcrBudget` cap, the aggregate-deadline clamp, the bounded-retry loop, reuse/idempotency, cross-company isolation, and security posture — all exercised by real requests across 13 real registered issuers (12 plus Goodwin PLC/GDWN.LSE, added specifically for this validation). Two genuine corruption/misclassification bugs were found and fixed live: a silent large-PDF truncation (5 MB byte cap) was corrupting real downloads and misclassifying ASML's two real annual reports as `scanned_no_text` (now `native_pdf`, 350/354 real pages); the same truncation was also producing a false `/Encrypt` match, so CFR's annual report — previously documented as genuinely password-protected in Slice 5B.1's own closure — is now confirmed to extract natively and was **never actually encrypted** (byte cap raised to 35 MB; OCR upload now pre-filtered to the selected page subset, since Azure's F0 tier was empirically confirmed to reject any request over ~3.5 MB regardless of the `pages=` parameter). **What is NOT yet proven — the honest remaining gap:** a real, live Azure Document Intelligence OCR *call itself* has not been observed on staging, despite three full validation rounds. This is a structural, non-code reason, not a defect: 8 of the 13 issuers have zero discoverable documents (pre-existing JS-gated IR pages); the two issuers with large reachable documents (ASML, CFR) turned out to be genuinely native-extractable once the two bugs above were fixed (an honest negative result); and Goodwin PLC's genuinely-scanned document is correctly discovered but ranks too low among candidates to reach within the current per-issuer document cap / ingestion budget without risking the shared ~230s Azure gateway ceiling the council's own wall-clock budget (Slice 4) is tuned to fit inside. Balance-sheet identity check remains unit-test-only (no live table currently carries all three required labels). A validation subagent briefly exposed the real API key in its own diagnostic output during this closure; contained same session (key rotated), no application log or persisted staging artifact affected — see the closure report. **Slice 5B.3 (admin API + web visibility) closed next (below); Phase 32A overall remains NOT fully closed — see below.** See `docs/development/closures/phase-32a-slice5b2.md` + `docs/DECISIONS.md` ADR-016 + `docs/development/PHASE_LEDGER.md` row `32A.5B.2`.
- **Admin web visibility for primary-document ingestion (Phase 32A Slice 5B.3) is CLOSED + STAGING-VALIDATED with no caveats of its own (`8723cfc5ba8e0bda0631a4cd2f8857c138993f5f`, 2026-08-09).** This is the LAST slice of Phase 32A's Slice 5 work. Adds a new admin-scoped `GET /api/v1/reports/{report_id}/primary-documents` endpoint (scopes by the report's `agent_run_id`, falling back to `company_id` for legacy pre-lineage reports, never unscoped; joins `document_ingestion_attempts` to `extracted_documents`/`extracted_facts` by content hash; never exposes raw document/OCR text or credentials; same perimeter-auth as every other admin route — 401 unauthenticated, identical shape to the sibling `GET /reports/{id}` route) and a new "Primary Documents" admin UI tab (summary counts, per-document cards distinguishing native vs OCR extraction, reused-from-cache, metadata-only, and every honest failure state; real never-fabricated confidence; no recommendation language), plus reconciling one stale Slice-5B.1-era gap message (the SEC connector's fixed "full filing text is not retrieved" note is now suppressed specifically and only when a real SEC-sourced document was actually extracted this run). One review-caught bug (`validated_fact_count`/`reused_count` double-counting on shared `content_hash`) was fixed and re-reviewed to GO before merge. Live staging proof: AAPL SEC 10-Q + 8-K both native-extracted with 3 validated facts, stale gap text confirmed absent, a repeat run correctly reused both documents with no count inflation; CFR 3 real Richemont PDFs native-extracted; zero cross-company data overlap at API and DB level; unauthenticated access correctly 401s; no migration, Alembic head stays `014`. **Known non-blocking:** a separate LLM-authored free-text mention with similar wording can still appear inside a council agent's own generated prose (not the deterministic gap this slice targets). **Not covered by this closure:** a human has not yet visually confirmed the new tab renders in-browser, because admin web is gated behind real GitHub OAuth and `AUTH_TEST_MODE` must stay absent on staging as a hard security invariant — the underlying API data is exhaustively validated instead. See `docs/development/closures/phase-32a-slice5b3.md` + `docs/development/PHASE_LEDGER.md` row `32A.5B.3`.
- **Discovery-council reliability parity (Phase 32A Slice 6A) is CLOSED + STAGING-VALIDATED (`25abc7b` + hotfix `a1e52a6`, 2026-08-10).** The discovery-run council never received the Slice 4 reliability machinery — a plain parity gap, not a design choice: it called each of its 8 agents exactly once with no retry, no backoff, no wall-budget and no deterministic fallback. Slice 4's engine was extracted out of `council.py` into an agent-shape-agnostic `app/services/llm/retry_engine.py` (the company council refactored to call it behaviour-preservingly, its public API and generated text unchanged) and `discovery_council.py` wired to it behind the default-OFF `LLM_DISCOVERY_COUNCIL_RETRY_ENABLED` (flipped ON and kept ON on staging). The discovery council gets its own more generous budget — **300s total / 60s critical reserve** vs. the company council's 150s/45s — because it runs as an async background job rather than inline in the ~230s-gateway-bound HTTP request. Live proof: on a fresh discovery run (`6b0700a9-...`, "European luxury goods companies", universe of 8) executed under real Azure contention, the council completed 3/8 agents, reported `run_quality="failed"`, and the deterministic fallback fired correctly with an honest synthesis naming exactly which agents did and did not complete — no fabricated consensus. **The hotfix (#94) was a visibility-only bug found live:** the fallback fired correctly internally but `DiscoveryCouncilReviewResponse` never declared `chair_fallback_used`/`deterministic_discovery_chair`, so Pydantic v2 silently dropped both before they reached any API consumer including the admin UI. **Honest limitation:** the *recovery* path (a transiently-failed agent retried and then succeeding, lifting a run to 8/8) was not observed live — only offline tests cover it; parity does **not** guarantee 8/8 under Azure TPM pressure. See `docs/development/closures/phase-32a-slice6a.md`.
- **Full-analysis report integrity (Phase 32A Slice 6B) is CLOSED + STAGING-VALIDATED (`d7c8774` + hotfixes `977cb22` / `734fac6`, 2026-08-10).** Nine independently-root-caused fixes found during an E2E QA pass on a real staging Burberry Group plc (BRBY, LSE) report: company identity, discovery lineage, price-quote currency, `schema_valid` staleness, blocking-gap counts, the `missing_financial_fields_count` rename, bot-protection gap wording, stale OCR status text, and source/citation scope labeling. Seven of the nine were confirmed correct on the first live pass; **two required corrective hotfixes, each triggered by a real staging failure.** Identity: `_build_company_identity()` always preferred the snapshot over the DB record — and the snapshot's own `legal_name` can legitimately BE the ticker, a deliberate anti-fabrication stub for exchanges SEC EDGAR does not cover (it exists because "BA.LSE became THE BOEING COMPANY" once really happened); now the DB record wins only when the snapshot name is a provable placeholder (`legal_name="BRBY"` → `"Burberry Group plc"`, `source="company_db_record"` on the fresh post-fix report `7d8be857-...`). Currency: the mainline fix touched the raw providers and `build_company_snapshot()` but missed a fourth path used by the actual production flow (`free_real`) — `FreeRealSnapshot.to_dict()` never threaded currency and `enrich_snapshot_with_free_real()` independently hardcoded `"currency": "USD"`; post-fix the same report correctly shows `latest_close.currency="GBX"`, the LSE pence quote unit, distinct from the GBP reporting currency. **Documented but deliberately not fixed (tracked follow-up):** a related USD default remains in `market_metrics_enrichment.py`'s derived market-cap fields. Live validation covered one issuer (BRBY/LSE). See `docs/development/closures/phase-32a-slice6b.md`.
- **Final-report regeneration crash fix (Phase 32A Slice 6C) is CLOSED + STAGING-VALIDATED (`89b7f41`, 2026-08-10).** "Generate Internal Final Report Draft" on an already-completed report raised `unhashable type: 'dict'`. Root cause, reproduced locally rather than guessed: `generate_from_report` re-parses `committee_chair_summary.provisional_internal_status`, which on an already-rendered report is a datapoint dict rather than a string, and that dict hit an unguarded set-membership check. Fixed with a targeted `_coerce_status_value()` at all four vulnerable sites, plus a related diagnosability gap — all six final-report endpoints previously discarded tracebacks (`str(exc)` only) and now call `logger.exception()` (structured, secret-free). Live proof on fresh BRBY report `7d8be857-...`: generate → HTTP 201 (`17f150ee-...`, `schema_valid=true`, `safety_valid=true`, `human_review_required=true`, `publication_ready=false`, 8 council agents completed / 0 failed), validate → HTTP 200, and a **second regeneration from the regenerated report itself** → HTTP 201 (`ecf79192-...`), with no `TypeError` anywhere. No migration, no new flag. See `docs/development/closures/phase-32a-slice6c.md`.
- **Deep Field Review (Phase 32A Slice 6D) is CLOSED + STAGING-VALIDATED (`dee5998` + hotfix `b2aa1be`, migration `015`, 2026-08-10).** Adds the missing comparative step after per-company full analysis (see "What is built and working" above for how it differs from the Discovery Council). Input resolution uses `DiscoveryCandidate.analysis_report_id` — a direct FK — **exclusively**, with no ticker/name matching and no "latest report for this company" fallback, because that would resurrect exactly the cross-contamination bug already fixed earlier in Phase 32A; every non-included candidate is persisted with a closed-vocabulary `exclusion_reason` so nothing is silently dropped. **The hotfix was found on the first-ever live run:** `field_chair` had no deterministic fallback at all, so when it failed all three priority buckets silently stayed empty with no explanation; the fallback now leaves the buckets empty and deliberately **never** places a company in `blocked_insufficient_evidence` merely because the chair failed — that bucket asserts something about the *company's own* evidence, which would be untrue if the real cause is a chair crash. Live proof on discovery run `6b0700a9-...` over three real completed analyses (BRBY `7d8be857-...`, CFR `8cb73eaa-...`, MC `838617cc-...`): run 1 (`14a2814e-...`, pre-hotfix) 7/8 agents with the chair failed and all buckets empty — exposing the gap; run 2 (`e22857dd-...`, post-hotfix) **8/8 agents, `status="completed"`**, a real chair verdict placing CFR in `strongest_candidates` and MC/BRBY in `second_tier` with distinct evidence-specific rationales, `blocked_insufficient_evidence` correctly empty, every entry citing real `discovery_candidate_id`/`report_id`, zero forbidden terms anywhere in the payload, `human_review_required=true`, `publication_ready=false`, `safety_valid=true`. Linkage verified exactly (3 included + 5 correctly excluded as `draft_only`) and idempotency verified directly in the DB (exactly 8 candidate-summary rows per run, no duplication). **Honest limitations:** the deterministic field-chair fallback's post-fix behaviour was not itself observed live (run 2 succeeded 8/8), the admin UI panel has not been visually confirmed in-browser by a human (admin web is behind real GitHub OAuth and `AUTH_TEST_MODE` must stay absent on staging), and the company-cap / insufficient-candidates (422) paths were not exercised live. See `docs/development/closures/phase-32a-slice6d.md`.
- **Phase 32A overall verdict (updated 2026-08-21): NOT fully closed, but the OCR blocker's root cause is now RESOLVED.** Slices 1 through 6D remain individually CLOSED + STAGING-VALIDATED as described above. The prior entries here concluded the missing live OCR call was structural, "not a code defect" — that conclusion was **wrong**. It WAS a code defect: `azure-core`'s async transport needs `aiohttp` installed to make a network call at all, and `aiohttp` was never a declared dependency, so every real OCR attempt failed closed with a generic `ocr_provider_error` indistinguishable from a genuine provider/data-availability outage. Fixed (PR #118) and live-proven the same session against a genuinely scanned real document (Goodwin PLC's 2002 annual report): real Azure DI invocation (model `prebuilt-layout`), 20 pages of real recovered text, persisted, and correctly reused on a second lookup with zero additional Azure calls. Independently, the same completion campaign found and fixed a three-layer LVMH/MC evidence-chain defect (parser period-qualifier vocabulary, and — in TWO independent call sites — a financial-fact evidence-selection round-robin that could silently prefer a stale comparison-period figure over the current period); all 7 target MC Group facts and 6/7 CFR target facts are now live-confirmed exact on staging. See `docs/DECISIONS.md` ADR-017 for the full record. **Still open:** CFR's Group operating-profit precision (an honest, pre-existing, unrelated period-inference gap — not a regression from this campaign); Deep Field Review and a full manual-QA pass were not re-run this session.

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
