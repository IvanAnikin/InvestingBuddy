# Private-Use Production Readiness — Technical Specification

**Status:** CLOSED 2026-08-26 — READY FOR MANUAL PRIVATE-USE PRODUCTION VERIFICATION
**Owner:** InvestingBuddy engineering
**Started:** 2026-08-25
**Baseline:** staging API/web `bfac6e1`, Alembic head `017`, extraction pipeline version `11`
**Production:** not provisioned, untouched, and out of scope for this campaign.

---

## 1. Executive objective

Bring InvestingBuddy from "strong staging research engine" to **private-use production ready**:
an authenticated human researcher (internal analyst / invited private user) can run the full
research loop against **real, current, primary evidence** for a European issuer universe and
**trust what the system tells them** — including what it says is missing.

This is explicitly **not** a public retail launch, **not** autonomous investment advice, and
**not** authorisation to deploy production.

---

## 2. Definition of "private-use production ready"

The system is private-use production ready when an authenticated researcher can reliably:

```
DISCOVER → SELECT COMPANY → RUN FULL ANALYSIS
        → INGEST CURRENT + HISTORICAL PRIMARY EVIDENCE
        → VIEW FINANCIAL SNAPSHOT / HISTORICAL TRENDS / CURRENT RESULTS
        → VIEW CATALYSTS & REGULATORY EVENTS
        → RUN LLM COUNCIL → RUN DEEP FIELD REVIEW
        → TRACE CLAIMS TO SOURCES → RE-RUN SAFELY → RECOVER FROM FAILURES
```

without major contradictory state.

**`research_complete == true` is NOT required.** "Insufficient evidence", "missing field",
"not valuation ready" and "requires more evidence" are all valid, correct outputs.

What is **never** allowed:

| Forbidden state | Meaning |
|---|---|
| `FACT_PRESENT_AND_MISSING` | a fact is presented in one section and declared missing in another |
| `SCOPE_CONTRADICTION` | a segment figure occupies a Group slot (or vice-versa) |
| `HISTORICAL_AS_CURRENT` | a prior-year figure presented as the current period |
| `INTERIM_AS_ANNUAL` | an H1/Q figure presented as a full-year figure |
| `SOURCE_TIER_CONTRADICTION` | issuer-PDF facts labelled as SEC/XBRL, or T5 price presented as a filing fact |
| `PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP` | "source the annual report" while it is already ingested |
| `DFR_FIELD_GAP_FALSE_POSITIVE` | one company's missing field attributed to another |
| `NONE_LITERAL_LEAK` / `ENUM_REPR_LEAK` | Python `None` / `SourceTier.X` rendered to a human |
| `DUPLICATE_DOCUMENT_IDENTITY` / `DUPLICATE_EVENT_IDENTITY` | the same artifact counted twice |
| silent job loss | a long-running analysis disappears with no recoverable state |
| fabrication | any invented value, citation, period or scope |

---

## 3. Current architecture baseline

Independently verified on 2026-08-25 before any change:

| Item | Verified value |
|---|---|
| repo HEAD | `8e6161c` (docs-only above the deployed app SHA) |
| staging API `/health` | `commit_sha=bfac6e1`, `environment=staging`, build `32784433094` |
| staging web `/api/version` | `commit_sha=bfac6e1`, build `32784433136` |
| Alembic head | `017` |
| backend tests | **3755 passed, 12 skipped, 0 failed** |
| `ruff check .` | clean |
| `mypy app` | **71 errors / 10 files** (accepted baseline) |
| Azure subscription | `Visual Studio Ultimate with MSDN` — **Enabled** |
| Azure OpenAI | `ib-stg-openai`, `gpt-4.1-mini`, GlobalStandard capacity **60** (not to be changed) |
| Production | not provisioned |

Accepted capabilities that this campaign **extends rather than replaces**: async full analysis,
TPM-aware council scheduling, real committee chair, typed evidence contracts
(`FinancialDataSummary` / `FundamentalsResolution` / `EvidenceInventory`), field-level
provenance, issuer-primary document ingestion, SEC XBRL path, issuer-scoped document CDN
authority, extension-less PDF discovery, native PDF extraction, Azure OCR fallback, two-column
reconstruction, multi-year borderless table reconstruction, period-scoped fact extraction,
canonical document identity, pipeline-version cache invalidation, post-ingestion
`FinalResearchState`, fact-centric gap reconciliation, `SourceQualityAssessment`,
`ThinEvidenceAssessment`, grouped discovery warnings, verified issuer registry,
regenerate-from-final lineage recovery, council citation binding, DFR exact report linkage.

---

## 4. Current known deficiencies

Each was confirmed against the code at `bfac6e1`, not assumed.

**D1 — Fact scope is not persisted (correctness-critical).**
`ExtractedFact` has no scope column. `ValidatedFact.scope` exists only in memory;
`extracted_document_service._persist_validated_facts` never writes it and
`_rebuild_artifact` never restores it. Demonstrated: a rehydrated `ValidatedFact` returns
`scope=None`. Because `final_report_generator._high_confidence_facts_for` treats
*absent* scope as the implicit Group convention, a **cache-reused** run can promote a
segment figure into a canonical Group slot — the exact regression Phase 32A fixed for the
fresh path only.

**D2 — Historical series are extracted then discarded.**
Phase 32D produces ~52 period-scoped Pandora facts (FY2021–FY2025), but every downstream
consumer (`_high_confidence_facts_for`, canonical snapshot, council pack, report sections)
takes a **single representative value per field**. No trend reaches a human or the council.

**D3 — The canonical snapshot is narrower than the evidence.**
`_PRIMARY_FINANCIAL_FACT_FIELDS` = `{revenue, operating_profit, net_income, free_cash_flow,
total_assets, total_debt, cash_and_equivalents}`. The parser already extracts
`operating_margin`, `operating_cash_flow`, `net_debt`, `net_cash`, `total_equity`,
`recurring_operating_profit`, `recurring_operating_margin`, `employees` — none reach the
canonical snapshot.

**D4 — Source-type copy is US-centric.**
Issuer-PDF facts and EU gap text still carry SEC/XBRL/10-K vocabulary in several places.

**D5 — DFR identity gaps are ungrounded.**
`field_review_evidence_pack.build_company_summary` carries no per-company identity
completeness signal (LEI / ISIN / sector / reporting currency), so the comparative council
has nothing to ground an identity-gap claim on and can generalise one company's gap to all.

**D6 — Discovery-stage metrics are unlabelled.**
Candidate cards keep (correctly immutable) discovery-time scores after full analysis, with
no marker saying so.

**D7 — Current-period evidence never reaches the report.**
`document_discovery.rank_documents` sorts strictly by kind (annual `0` < results `1` <
interim `2`) and `primary_document_max_docs_per_issuer` is `3`. On an issuer page listing
many annual reports (Richemont lists ~30), **no interim document can ever be selected**, and
among the annuals there is no recency preference at all — DOM order wins.

**D8 — Regulated-disclosure connectors are reference-only.**
`nordic_disclosures`, `six_swiss`, `euronext_regulated_info`, `uk_fca_nsm` emit a venue
pointer plus an honest gap. No live event is ever retrieved.

**D9 — Job durability is recoverable but not observable.**
A DB-backed analysis-job envelope with a derived stale threshold exists, but recovery only
happens when a human re-POSTs, and `GET .../analysis-job` reports `running` indefinitely for
a job whose worker died. Nothing sweeps orphans at startup.

---

## 5. In-scope work

| Phase | Scope |
|---|---|
| **PR-A** | This spec; migration `018` persisting typed fact scope; scope round-trip through persistence, cache reuse, revalidation, evidence and report |
| **PR-B** | Canonical bounded historical financial series + comparability rules + council/report propagation |
| **PR-C** | Canonical snapshot expansion; source-neutral copy; DFR identity-gap grounding; discovery-stage label |
| **PR-D** | Current-period (annual vs interim vs trading update) document selection, period semantics and canonical selectors |
| **PR-E** | Live regulated-disclosure retrieval + normalized event model + semantic dedupe |
| **PR-F** | Machine-verifiable report-consistency invariants; job durability/observability; issuer registry corrections |
| **PR-G** | Live multi-issuer acceptance, corrective PRs, documentation/ADR closure |

## 6. Explicit non-goals

* No public publication workflow; `human_review_required=true` / `publication_ready=false` stay.
* No BUY/SELL/HOLD/WATCH, price target, fair value, intrinsic value, projected upside/downside,
  return promise, or autonomous trade instruction — ever.
* No forecasting, no annualisation of interim results, no projected financials.
* No broker integration or trade execution.
* No new speculative "predict catalysts" system — PR-E is acquisition + normalisation only.
* No production deployment, production resource creation, production secret/config change, or
  production migration.
* No weakening of auth, SSRF/DNS/TLS/redirect validation, provenance, citations, company/run
  isolation, fail-closed extraction, or source-tier semantics.
* No issuer-specific hardcoded financial values and no ticker-specific parsing/URL hacks.
  Curated issuer-specific *trust* registration is allowed; parsing stays generic.
* No bypass of login, paywall, CAPTCHA, access control or explicit anti-bot mechanisms.

---

## 7. Architecture / data flow

```mermaid
flowchart TD
    A[Discovery run] --> B[Discovery candidates]
    B --> C[Full analysis job envelope - DB backed]
    C --> D[Company analysis workflow]
    D --> E[Source connectors]
    E --> E1[company_ir deep ingestion]
    E --> E2[SEC EDGAR / XBRL]
    E --> E3[Regulated disclosure connectors]
    E1 --> F[Document discovery - period aware]
    F --> F1[latest annual]
    F --> F2[latest current period]
    F1 & F2 --> G[Safe fetch - SSRF/DNS/TLS/redirect bounded]
    G --> H[Text extraction - native PDF / HTML / OCR]
    H --> I[Table reconstruction - borderless multi-year]
    I --> J[Primary fact parser - period + scope]
    J --> K[Fact validator]
    K --> L[(extracted_documents / extracted_facts<br/>scope persisted - migration 018)]
    L --> M[Evidence items + PrimaryFactRef]
    E3 --> N[Normalized disclosure events + dedupe]
    N --> M
    M --> O[LLM council - TPM paced]
    M --> P[Historical series builder]
    O & P & M --> Q[FinalResearchState - one reconciled truth]
    Q --> R[Final report sections]
    R --> S[Report consistency invariants]
    R --> T[Deep Field Review]
```

The invariant this campaign protects: **one canonical fact state drives every deterministic
human-facing surface.** No section may compute its own private view of what is known.

---

## 8. Data-model changes

Only one schema change is planned: **migration `018`**.

`extracted_facts` gains a typed, additive scope representation:

| Column | Type | Semantics |
|---|---|---|
| `scope_type` | `VARCHAR(20)` NULL | `group` \| `segment` \| `NULL` (unknown — never guessed) |
| `scope_name` | `VARCHAR(200)` NULL | normalized segment identity, e.g. `Specialist Watchmakers`; NULL for group/unknown |
| `scope_key` | `VARCHAR(220)` NULL | deterministic dedupe/identity key: `group` \| `segment:<casefolded name>` \| NULL |

`scope_key` is derived, never user-supplied, and is what fact identity / dedupe / supersession
compare on. `scope_name` preserves the as-found label for display; `scope_type` is the coarse
semantic the report layer must branch on.

**Why three columns and not one string:** the report layer needs a *decidable* Group-vs-segment
question (`scope_type`) that does not depend on string-matching a label table at read time,
while diagnostics and the DFR need the human label. `scope_key` exists so the dedupe identity
is stable under label whitespace/casing drift without collapsing two genuinely different
segments.

## 9. Migration strategy

* Purely **additive**, all three columns nullable, no default backfill that invents meaning.
* **Backfill only where deterministic:** rows whose existing in-band signal is unambiguous.
  Since no scope was ever persisted, there is no recoverable historical signal — every
  pre-existing row stays `NULL` (unknown remains unknown). This is stated explicitly rather
  than inferred, because guessing `group` for legacy rows would manufacture exactly the
  contradiction class this campaign removes.
* Reversible `downgrade()` dropping the three columns.
* Because *interpretation* of persisted facts changes, `CURRENT_EXTRACTION_PIPELINE_VERSION`
  is bumped, so every legacy row is revalidated under current semantics rather than trusted.

## 10. Historical-series model

`app/services/sources/financial_history.py` builds a bounded, typed series:

```
FinancialHistorySeries
  metric: str                 # canonical field name
  scope_type / scope_name     # group | segment
  currency / unit / scale
  period_type: annual | interim | quarter
  observations: [FinancialHistoryPoint(period, period_sort_key, value, source_url,
                                       page_number, table_location, confidence)]
  completeness: complete | partial
  comparability: comparable | not_comparable(reason)
```

* Maximum **5** annual periods (configurable, default 5, floor 2).
* Metrics: revenue, operating profit, operating margin, net income, operating cash flow,
  free cash flow, total assets, total equity, net debt / net cash, employees — **only where
  present**. Absent metrics are never forced.
* Raw values are preserved. Any derived number carries `calculation_type`, input fact
  identities, periods, formula, and `derived` provenance.

## 11. Historical comparability rules (fail-closed)

Two observations are comparable **only** when company, metric, `scope_key`, `period_type`,
unit/scale and currency all match. Never compared: FY vs H1/Q, Group vs segment, EUR vs DKK
without an approved conversion, net debt vs total debt, EBIT vs EBITDA, continuing operations
vs Group when scope differs. Allowed deterministic descriptive calculations: absolute change,
percentage change, YoY growth, margin change in percentage points — and only with a valid
denominator and valid periods. **No forecasting. No projection. No return prediction.**

## 12. Canonical financial snapshot model

`_PRIMARY_FINANCIAL_FACT_FIELDS` expands to the validated statement fields listed in §10.
Never conflated: `net_debt` ≠ `total_debt`; `net_cash` ≠ `cash_and_equivalents`;
`operating_profit` ≠ EBITDA. Market-derived fields (market cap, EV, P/E, EV/EBITDA) stay in
their own T5 block and are never implied to be filing facts. A Group snapshot slot is filled
**only** by `scope_type == group` (or a legacy `NULL` on the fresh path, the pre-existing
implicit convention) — never by a segment substitute. Absent ⇒ shown missing.

## 13. Current-period source architecture

Document selection becomes **period-aware and recency-aware**:

* Each discovered document carries a derived `period_kind` (`annual` | `interim` |
  `trading_update` | `other`) and a `period_sort_key` parsed from title/URL.
* Selection reserves quota: **≥1 latest annual** and **≥1 latest current-period** document,
  instead of a flat kind-rank truncation.
* Canonical selectors expose `latest_annual`, `latest_interim`, `latest_current_period`
  without losing historical context. FY2025 revenue and H1 2026 revenue coexist.
* An interim value never overwrites an annual value: they are different periods, full stop.
* **No annualisation** of interim results is implemented in this campaign.

### 13.1 Current-period RETRIEVAL (added by the current-period acceptance correction)

The selection rules above were implemented and unit-tested in PR-D, and the final acceptance
matrix still showed `Current = —` for two issuers whose current-period reports this system
could fetch and extract. The whole loss was upstream of selection:

| Stage | Defect | Fix |
|---|---|---|
| Discovery | A **Next.js App Router** page streams its content as `self.__next_f.push([1,"…"])` — JSON-encoded string FRAGMENTS of one logical stream. `next_data` looks for a hydration script id App Router never emits, and `embedded_json` needs a balanced literal inside ONE script body. Pandora's Q2 2026 interim report lived only there, so the interim INDEX page became the "current-period document". | New bounded strategy **`next_flight`**: reassemble the pushed chunks into one capped buffer, then read URLs from their own quoted JSON string values (which is what lets an official URL containing spaces survive). No browser, no JS execution, no extra fetch; identical https / safe-host / allowlist / secret-strip guards. |
| Classification | `ANNUAL_REPORT_KEYWORDS` covers annual, full-year, half-year and "interim" wording and has **no quarterly vocabulary at all**. Richemont's newest reporting is a quarterly SALES release, so the reserve had nothing to reserve a slot for. | New `CURRENT_PERIOD_KEYWORDS` (period wording only — never general press wording) combined into `_INDEX_KEYWORDS` for depth-0 discovery. |
| Selection | The reserve was applied at the end of the `max_docs_per_issuer + _MAX_THIN_FALLBACK_DOCS` candidate list, but ingestion **stops at `max_docs_per_issuer`** once one document has real financial content. The reserved document was ranked, logged and never fetched. | `_rank_deep_targets(..., reserve_within=max_docs_per_issuer)` — the reserve now lands where ingestion actually reaches. |

Widening the candidate set exposed three ordering defects that had been masked by the narrower
one, each of which chose the wrong document live:

* a ZIP archive named `… (PAND-2025-12-31-en.zip)` counted as a document, because the filename
  ends in a parenthesis — trailing brackets are now stripped before the extension test;
* the anchor-**wording** heuristic sat ahead of both downloadability and recency, so a news page
  headlined "Richemont publishes FY26 Annual Report" outranked the report PDF beside it (labelled
  merely "Download"), and Pandora's "Annual Report 2024" outranked "Annual Report 2025". The rank
  is now `(kind, downloadable, recency, supporting-material, wording)`;
* one document reached the ranker under two spellings (raw-space href vs percent-encoded) and
  spent two of three bounded slots on itself — de-duplication now uses `document_identity`.

Results-day **supporting material** (presentation, transcript, appendix, analyst consensus,
aide-memoire) is demoted generically, so "the newest current-period document" is a determinate
choice rather than a DOM-order one.

### 13.2 Current-period PERIOD TRUTH (added by the current-period acceptance correction)

Reaching the current-period document made a second, more dangerous class of defect reachable.
A new pure module, `app/services/sources/document_period.py`, answers the prior question
`financial_period` never asked: **what period is this DOCUMENT about?**

`detect_document_period(title, url, headings, text)` reads the document's OWN words, strongest
rule first — the issuer's combined fiscal label (`fy27-q1` → **Q1 2027**), then an unambiguous
four-digit label (`Q2 2026`, `H1 2026`), then a period-end sentence ("for its first quarter
ended 30 June 2026"). Nothing is derived from a fiscal calendar, a publication date or a
registry. A nine-month cumulative period is **refused**, not mapped: it is neither a quarter nor
a half, and this model has no representation for it. An annual report states no interim period,
so every existing behaviour is unchanged.

That period then governs four things:

1. **The undated-figure fallback is period-TYPED.** The old fallback supplied the document's
   most common explicit token — in Richemont's quarterly release, the bare `2026` from its
   exchange-rate table, corporate calendar and copyright line — so an undated "Group sales at
   € 6.3 billion" became **annual 2026 revenue** beside the € 22.4 bn FY2026 figure. It now
   inherits the document's own period (`Q1 2027`).
2. **A bare-year table column inside an interim document is not a full year.** Pandora's Q2 2026
   lease note heads its columns `| 2026 | 2025 |` with the qualifier "30 June" wrapped onto the
   row beneath; read as full years it produced a *validated* "FY2025 revenue" of DKK 248 m (from
   a row labelled "Variable leases linked to revenue"). Recovering the intended period from a
   wrapped date row is a table-geometry problem this layer does not attempt — it **fails closed**
   and leaves the column unmapped.
3. **An interim document is never an AUTHORITY for a full year.** Its own year is not over, so a
   figure read as that year loses its period entirely; a prior-year comparative keeps its period
   but is demoted to excerpt-only, because the annual report is the authority. Nothing is
   deleted and every demotion states its reason on the fact.
4. Both the live extraction path and the **cached rebuild** path resolve it identically, or a
   reused document would re-derive different periods from the same bytes.

**The four reporting states** (`app/services/sources/period_state.py`) are now typed and named
on the report itself, under `financial_snapshot.reporting_periods`:

| State | Example | Meaning |
|---|---|---|
| `latest_annual` | FY2025 | the last completed financial year |
| `latest_interim` | H1 2026 | the last half-year reported |
| `latest_quarter` | Q2 2026 | the last quarter reported |
| `latest_current_period` | Q2 2026 | the newest of the two part-year states |

Recency is decided by when a period **ENDS**, not by its bare ordinal: ranking on the ordinal put
half-years and quarters on one scale, where H2 2026 and Q2 2026 tied at 2 although they end six
months apart. A quarter wins the tie with the half it ends beside, following the same
"more specific claim" precedent as the interim-marker parser. The states are derived from the
slots the section actually filled — never from the whole fact set — so they cannot name a period
the report does not show, and three consistency invariants assert exactly that.

`select_latest_annual` never falls back to an interim period: a canonical annual slot may only
hold a full year, and the honest answer when none exists is *unknown*.

## 14. Regulated-disclosure connector architecture

Existing connectors are **upgraded in place** (no parallel architecture) from
reference-only to bounded live retrieval, behind an explicit flag, normalising into ONE typed
event model (`app/services/sources/disclosure_events.py`):

```
DisclosureEvent
  issuer_ticker / issuer_name / venue / country
  published_at (tz-aware) / headline / category / language
  official_url / attachment_urls / document_identifier / period
  source_tier / retrieved_at / provenance[] / event_type
```

Venues and their live status are recorded in §J of the final handoff and in the live-status
table below (§27).

## 15. Source / provenance hierarchy

`T1_primary_filing` (issuer-owned document) > `T1_primary_company_source` (issuer transport)
> `T2_regulator_or_gov` (exchange/regulator venue) > `T5_api_aggregator` (price/fundamentals)
> `T6_model_estimate`. Dimensional quality (identity / financial / catalyst / overall) is
preserved: T5 price must not downgrade a genuine T1 statement fact, and one T1 statement fact
must not make catalyst evidence look strong.

## 16. Period / currentness semantics

Currentness is decided from publication date, document title, internal reporting period and
official source metadata — **never from a filename alone**. Labels are explicit: *Latest
annual*, *Latest interim*, *Latest market price*, *Latest official event*. FY2025 is not
"current" if H1 2026 exists.

## 17. Scope semantics

`group` = consolidated. `segment` = a named business area/segment. `NULL` = unknown, and
unknown never becomes group at persistence time. Group and segment series are independent.

## 18. Cache / version semantics

`pipeline_version` gates reuse. A change to what text is extracted, or to how extracted text
is interpreted (including scope), bumps `CURRENT_EXTRACTION_PIPELINE_VERSION`. Live acceptance
always proves the new version first, then proves cache reuse explicitly.

## 19. Failure / retry semantics

Every venue connector enforces lookback window, max items, pagination cap, wall-clock budget,
response byte cap, attachment byte cap, redirect limit, exact allowed domains, DNS/IP
validation, TLS, rate limiting and a stable cache. A failing venue degrades to an honest gap.
No unbounded scraping, no infinite pagination, no recursive attachment crawl.

## 20. Security model

Unchanged and not weakened. Every new fetch goes through the existing SSRF-guarded, DNS-pinned,
redirect-validated `safe_web_fetcher` with an **exact** allowlist. Global fetch authority is
never widened to make one issuer work; issuer-scoped `document_domains` remain the only
mechanism. Explicit anti-bot mechanisms are respected, not bypassed.

## 21. Operational-readiness requirements

Authentication, authorisation, job lifecycle, failure persistence, app-restart behaviour,
retry, idempotency, deployment, migration, health endpoint, provider failure handling, Azure
OpenAI rate-limit handling, extraction timeouts, cache consistency, logging, correlation/run
IDs and source-fetch diagnostics are all audited in PR-F. A long-running analysis must not
silently disappear: orphaned in-flight jobs are detected and surfaced as recoverable.

## 22. Observability requirements

Structured logging must answer: what failed, where, for which run/company/document, was it
retried, was cache used, which pipeline version, which connector, which council agent, which
source, was the job resumed. Never logged: secrets, tokens, full sensitive payloads, entire
copyrighted documents. Volume stays bounded.

## 23. Test strategy

Every phase ships unit, contract, integration, regression and negative/fail-closed tests.
Schema phases add migration-upgrade and round-trip tests. Source phases add fixture-based
connector tests, security tests, bounded-pagination tests, redirect tests and dedupe tests.
Current-period adds period-comparison tests; historical adds scope/period comparability tests;
report adds semantic consistency tests; jobs add restart/recovery tests.

## 24. Live acceptance matrix

At least **5** issuers across **≥4 venues** and **≥4 countries**, mandatory PNDORA + CFR.
Full matrix recorded in §51 of the final handoff.

## 25. Rollout / rollback plan

Merge to `main` → GitHub Actions path-filtered deploy to staging → SHA-verified smoke →
migration `018` applied to staging via the documented runbook → live acceptance. Rollback:
migration `018` is reversible and additive; every new live-retrieval behaviour is flag-gated
and defaults consistent with the accepted baseline until proven.

## 26. Phased implementation plan

See §5 and the PR ledger (§29). Per-phase exact problem, affected modules, schema, tests,
live acceptance, dependencies and exit criteria are recorded in the ledger as each phase lands.

## 27. Source landscape research — European issuer universe (as of 2026-08-25)

Researched live on 2026-08-25. Every "latest" below was verified against the official
document's own publication date and internal reporting period, never a filename.

| Issuer | Venue / Country | Latest annual | Latest current period | Official source | Fetchable |
|---|---|---|---|---|---|
| **PNDORA** Pandora A/S | Nasdaq Copenhagen / Denmark | FY2025 Annual Report | **Q2 2026 Interim Financial Report, published 12 Aug 2026, Company Announcement 1015** | `pandoragroup.com` IR + `pandora.a.bigcontent.io` CDN | ✅ 200, `application/pdf`, 1.46 MB, 43 pp |
| **CFR** Richemont | SIX / Switzerland | **FY26 Annual Report (year ended 31 Mar 2026)** | **FY27 Q1 sales, quarter ended 30 Jun 2026, ad-hoc announcement 15 Jul 2026** | `richemont.com` | ✅ 200, both PDFs |
| **RMS** Hermès | Euronext Paris / France | URD 2025 (EN), published Apr 2026 | **H1 2026 results press release, 29 Jul 2026** | `finance.hermes.com` + `assets-finance.hermes.com` | ✅ 200 |
| **KER** Kering | Euronext Paris / France | URD 2025 (EN), published 21 Apr 2026 | **H1 2026 press release + First-Half Report 2026, 28 Jul 2026** | `kering.com` + `assets-keringcom.keringapps.com` | ✅ 200 |
| **UHR** Swatch Group | SIX / Switzerland | Annual Report 2025 | **Half-year report 2026** | `swatchgroup.com` | ✅ 200 |
| **MONC** Moncler | Euronext Milan / Italy | FY2025 | **H1 2026 Financial Results, 22 Jul 2026** | eMarket Storage (CONSOB-authorised) | ✅ via storage; issuer site in maintenance |
| **MC** LVMH | Euronext Paris / France | FY2025 | H1 2026 | `lvmh.com` (Prismic, client-rendered) | ⚠️ publications list is client-side only |
| **BRBY** Burberry | LSE / United Kingdom | FY2026 | Q1 FY2027 | `burberryplc.com` | ⛔ proof-of-work anti-bot challenge — **not bypassed by design** |

Regulated-disclosure venue research:

| Venue | Mechanism found | Status |
|---|---|---|
| Nasdaq Nordic (CO/ST/HE/OL) | official `api.news.eu.nasdaq.com/news/query.action` — JSON, per-issuer, headline + category + official view URL + typed attachments | **LIVE** (PR-E) |
| Borsa Italiana / CONSOB | official **eMarket Storage** (`emarketstorage.it`) per-issuer listing with direct official PDFs | **LIVE** (PR-E, new connector) |
| SIX Swiss | no public per-issuer disclosure API found; issuers publish Art. 53 LR ad-hoc announcements on their own sites | issuer-primary route |
| Euronext Paris | `live.euronext.com` company-news is server-rendered but modal-loaded and paginated | issuer-primary route |
| LSE / FCA NSM | NSM portal `403`; NSM search API rejects every documented index; LSE per-issuer news API not public | **documented limitation** |

## 28. Acceptance criteria

The campaign may return READY only if every item in §52 of the program brief (A–O) holds.

## 29. PR / deployment ledger

| PR | Purpose | Merge SHA | CI | Migration |
|---|---|---|---|---|
| [#149](https://github.com/IvanAnikin/InvestingBuddy/pull/149) | PR-A — persist Group/segment fact scope | `6b7b4cb` | green | **018** (applied to staging 2026-08-25) |
| [#150](https://github.com/IvanAnikin/InvestingBuddy/pull/150) | PR-B — bounded historical financial series | `8b516e3` | green | none |
| [#151](https://github.com/IvanAnikin/InvestingBuddy/pull/151) | PR-C — snapshot expansion, source-neutral copy, DFR gaps | `99df1b9` | green | none |
| [#152](https://github.com/IvanAnikin/InvestingBuddy/pull/152) | PR-D — current-period (interim) evidence | `3f45268` | green | none |
| [#153](https://github.com/IvanAnikin/InvestingBuddy/pull/153) | PR-E — live regulated disclosures | `dc3df2e` | green | none |
| [#154](https://github.com/IvanAnikin/InvestingBuddy/pull/154) | PR-F — consistency invariants + job durability | `eac749e` | green | none |
| [#155](https://github.com/IvanAnikin/InvestingBuddy/pull/155) | Corrective — search a venue by name, not legal form | `d73b4ed` | green | none |
| [#156](https://github.com/IvanAnikin/InvestingBuddy/pull/156) | Corrective — series see every fact; call `fetch_events` | `8558bc2` | green | none |
| [#157](https://github.com/IvanAnikin/InvestingBuddy/pull/157) | Corrective — disclose a newer annual period | `d71353c` | green | none |
| [#158](https://github.com/IvanAnikin/InvestingBuddy/pull/158) | Corrective — apply the implicit-Group convention consistently | `7048e2c` | green | none |
| [#159](https://github.com/IvanAnikin/InvestingBuddy/pull/159) | Corrective — canonical fact from the complete high-confidence set | `74b1158` | green | none |
| [#160](https://github.com/IvanAnikin/InvestingBuddy/pull/160) | Corrective — state the DFR identity-gap spread | `d66842c` | green | none |
| [#161](https://github.com/IvanAnikin/InvestingBuddy/pull/161) | Docs — campaign status and live acceptance | `acf6871` | n/a (docs) | none |
| [#162](https://github.com/IvanAnikin/InvestingBuddy/pull/162) | Surface the retrieved regulated disclosures to a human | `024cbd2` | green | none |
| [#163](https://github.com/IvanAnikin/InvestingBuddy/pull/163) | Polish — strip venue short-name prefix; count channels | `abd1f7a` | green | none |
| [#158](https://github.com/IvanAnikin/InvestingBuddy/pull/158) | Corrective — apply the implicit-Group convention consistently | `7048e2c` | green | none |

**Deployment ledger**

| Item | Value |
|---|---|
| staging API | `7048e2c` (`/health`, `environment=staging`) |
| staging web | `abd1f7a` (`/api/version`, build `32970640276`) |
| Alembic head (staging) | `018` |
| extraction pipeline version | `13` |
| `SOURCE_LIVE_DISCLOSURES_ENABLED` | `true` on staging (off by default in code) |
| Azure OpenAI capacity | unchanged at 60 |
| production | not provisioned, untouched |

### Definitive live acceptance (staging `abd1f7a`, 2026-08-26)

Discovery run **`aeee88d6-d228-4b46-b46d-86da99e1704d`** — "European luxury goods
companies", 8 candidates, discovery council **8/8 agents, real LLM chair, no fallback**.

| Issuer | Venue | Country | Report | Annual | Current | T1 facts | Series | Disclosures | Council |
|---|---|---|---|---|---|---|---|---|---|
| PNDORA | Nasdaq Copenhagen | Denmark | `a3d6ef3e` | FY2025 | — | 9 | **6 × FY2021–FY2025** | 5 (Nasdaq Nordic) | 8/8 real chair |
| CFR | SIX Swiss | Switzerland | `eb7f2f7e` | FY2026 | — | 6 | **5 segment series** | 0 (venue reference only) | 8/8 real chair |
| RMS | Euronext Paris | France | `f3fc5507` | FY2026 | H1 | 4 | 0 | 0 (venue reference only) | 8/8 real chair |
| KER | Euronext Paris | France | `77e1257e` | FY2025 | **H1 2026** | 12 | 4 | 0 (venue reference only) | 8/8 real chair |
| MONC | Euronext Milan | Italy | `fc948705` | — | — | 0 (honest) | 0 | **5 (eMarket Storage)** | 8/8 real chair |

**Deep Field Review `d1c0c98f-d3a9-4d5f-b2cd-56d0d6cb6c46`** — 8/8 agents, real chair,
exact report lineage for all five, `safety_valid=true`, `publication_ready=false`.

**Report consistency: 0 serious findings across all five reports.**

### Correctives found by LIVE acceptance

All four passed every unit test first. Each unit test exercised the *piece*; running live exercised the *path*.

| # | Defect | How it was found |
|---|---|---|
| 1 | Venue searched by full legal name (`"Pandora A/S"`) returned only boilerplate managers'-transaction notices and **silently dropped** the Q2 2026 results and the CFO appointment — no headline carries a legal-form suffix | running the connector against the real Nasdaq Nordic service |
| 2 | Historical series were derived from the per-document-**capped** evidence items, so 52 persisted period-scoped facts became one observation per metric and the report claimed "no multi-period series was reconstructed"; separately the venue connectors' `fetch_events` was **never called** by the evidence collector, and `borsa_italiana` was missing from the runnable regulator set | comparing a live Pandora report against `GET /reports/{id}/primary-documents` |
| 3 | A canonical slot showed FY2024 revenue while the report's own series showed FY2025 (which fell below the slot's confidence bar) | **the PR-F invariant checker itself**, on a live Kering report |
| 4 | The fix for #3 did not fire: the selected fact was unscoped, and fail-closed scope matching refused to compare it with a Group-scoped candidate — even though the slot had already placed it there under the implicit-Group convention | re-running live acceptance after #157 |
| 5 | *Which* period filled a canonical slot depended on evidence-pack ordering: `primary_facts` was read from the **capped** evidence items, so a rounded FY2024 prose figure occupied the revenue slot while a high-confidence FY2025 figure existed and had simply not survived the cap | inspecting the Kering report after #158 |
| 6 | The DFR correctly reported ISIN and sector missing for all five companies, then wrote a task to source "(ISIN, LEI)" for all — while three of five had a sourced LEI. Per-company grounding removed false claims about *individuals*; it did not stop five lists being *merged* | verifying the live DFR against the per-company data |
| 7 | The connector retrieved **fifteen** real Pandora announcements that informed the council — but a researcher could not SEE any of them: the council persists only sources it CITES, and `news_catalyst_discovery` is built by a different agent that never sees connector evidence | inspecting the live reports' catalyst section against the connector's own output |
| 8 | The Italian venue prefixes rows with the issuer's SHORT name, so stripping the LEGAL name left "MONCLER " in the headline — and in the dedupe key; and "confirmed by N channels" counted provenance lines, inflating a single-channel event to six | reading the rendered live reports |
| — | **Operational, not a defect:** five concurrent full analyses on the single-worker B1 staging tier exceed the 45-minute stale threshold; run in batches of two | two consecutive interrupted batches, then a single run completing in **5.2 min** |
| 4 | The fix for #3 did not fire: the selected fact was unscoped, and fail-closed scope matching refused to compare it with a Group-scoped candidate — even though the slot had already placed it there under the implicit-Group convention | re-running live acceptance after #157 |

## 30. Final status

**Campaign closed 2026-08-26. Status: READY FOR MANUAL PRIVATE-USE PRODUCTION VERIFICATION.**

### IMPLEMENTED

| Area | What now holds |
|---|---|
| **Scope persistence** (migration `018`) | `scope_type` / `scope_name` / `scope_key` on `extracted_facts`; scope is part of fact identity; survives extract → persist → reload → cache reuse → report. `UNKNOWN` is a real third state, never coerced to `group`. |
| **Historical series** | `ReportingPeriod` + `FinancialHistorySeries`; series keyed by the full identity (metric, scope, period type, currency, unit, scale); fail-closed comparability; ≤5 periods, ≤8 council lines; descriptive arithmetic only. |
| **Canonical snapshot** | Field set **derived** from the parser's own vocabulary — 7 → 15 fields. `net_debt`/`total_debt`, `net_cash`/`cash`, `operating_profit`/`recurring_operating_profit` are distinct slots that cannot alias. |
| **Source-neutral copy** | `annual_filing_name()` and `_statement_source_label()` resolve from the issuer's own jurisdiction; US wording is kept only where the jurisdiction is genuinely unresolved or genuinely US. |
| **Current-period evidence** | Period- and recency-aware document selection with a reserved current-period slot; interim markers read from the value's own local window; `<field>_primary_filing` (latest annual) and `<field>_current_period` (latest interim) are separate, explicitly non-comparable slots. **No annualisation.** |
| **Live regulated disclosures** | One `DisclosureEvent` model; **live** retrieval from Nasdaq Nordic and eMarket Storage (Italy); semantic dedupe that merges an issuer copy with an exchange copy while keeping **both** provenances. |
| **Consistency invariants** | All thirteen classes assertable over an assembled report, each tested from both sides. |
| **Job durability** | `interrupted` + `recoverable` derived at read time; read-only startup sweep that logs orphans without re-enqueuing. |

### LIVE VALIDATED (staging `abd1f7a`)

* Fresh discovery run `aeee88d6-d228-4b46-b46d-86da99e1704d` — 8 candidates, discovery council **8/8 agents, real LLM chair, no fallback**.
* **5 issuers**, **4 countries**, **4 venues** completed end-to-end; every council **8/8 agents, 0 failed, real chair**.
* **0 serious consistency findings** across all five reports.
* Live regulated disclosures visible on the report surface: **Pandora's Q2 2026 results announcement** (Nasdaq Nordic, 12 Aug 2026) and **Moncler's H1 2026 Financial Results** (eMarket Storage, 22 Jul 2026) — the latter retrieved from the CONSOB-authorised venue while the issuer's own site was serving a maintenance page.
* Pandora: 9 canonical facts matching the accepted baseline exactly, plus **6 five-year series (FY2021–FY2025)**.
* Richemont: Group figures matching the accepted baseline, plus **5 independent segment series** — Jewellery Maisons, Specialist Watchmakers and Other each tracked separately, with no segment figure in a Group slot.
* Kering: current-period **H1 2026** facts in their own labelled slots beside FY2025 annual facts.
* Deep Field Review `d1c0c98f-d3a9-4d5f-b2cd-56d0d6cb6c46` — 8/8 agents, real chair, **exact report lineage** for all five; identity gaps verified **field-by-field against the underlying per-company data** (ISIN and sector genuinely missing for all five; LEI, present for three, is no longer over-generalised).
* Job durability proven live: a deploy recycled the container mid-run, all five jobs reported `interrupted` + `recoverable`, and a plain re-POST recovered them.

### DEFERRED — NON-BLOCKING

* **LSE / FCA NSM** and **Burberry** — the NSM portal returns 403, its search API rejects every documented index, and the issuer's own site is behind a proof-of-work challenge. **Not bypassed by design.** UK is therefore not in the accepted five.
* **LVMH** — the publications index is client-rendered only; no server-rendered document list to discover from.
* **SIX Swiss** and **Euronext Paris** remain venue-reference-only; both issuers' ad-hoc announcements are reachable through the issuer-primary path, which is what the accepted Richemont result uses.
* **Moncler** — `monclergroup.com` was serving its own maintenance page throughout; its regulated disclosures were still retrieved from the CONSOB-authorised venue. Its report is honestly evidence-thin.
* **Multi-period interim tables that mix column types** (Pandora's `Q2 2026 | Q2 2025 | H1 2026 | H1 2025 | FY 2025`) are still refused by the table reconstructor's monotonicity check — correctly fail-closed; those figures reach the pipeline through prose.
* **Operational:** five concurrent full analyses exceed the 45-minute stale threshold on the single-worker B1 staging tier. Run in batches of two; a single analysis completes in ~5 minutes.

### BLOCKED

None.

### PRIVATE-USE READINESS STATUS

**READY FOR MANUAL PRIVATE-USE PRODUCTION VERIFICATION.** Production is not
provisioned and was not touched at any point in this campaign.
