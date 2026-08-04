# Closure Report — Phase 32A Slice 5A: native primary-document ingestion

> Produced after merge + migration + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-04. Verdict: **Slice 5A CLOSED + STAGING-VALIDATED (foundation) — WITH AN EXPLICIT EFFICACY CAVEAT.**
> **Slice 5B (real OCR + web rendering + link-discovery enablers) REMAINS OPEN. The complete Slice 5 and Phase 32A are NOT closed.**

## Scope of this closure
This closes **Slice 5A only** — the *native* HTML / native-text-PDF ingestion pipeline (pdfplumber
tables + stdlib HTML tables/sections), its stricter table-fact validator, the evidence-pack
`primary_document` floor/cap, page/section/table-provenance citations, persistence + TTL reuse
(migration `013`), and the SSRF/limit guards. The **NoOp OCR seam** ships but OCR remains **OFF**
and does not extract scanned/encrypted PDFs. Frontend rendering of the new fields is **deferred**.

## Merge / migration / deploy
- **PR #77** squash-merged → `main` **`354a5baad339156b1dd83e58e184dadf616a4274`** (`354a5ba`).
- Pre-merge gate (head `3b53b4b`, 42 files, +8375/-30): CI **Lint & Test green**; MERGEABLE/CLEAN;
  0 unresolved comments; **ib-security PASS/GO**; **ib-pr-review GO/APPROVED** (no must-fix; 102 new
  tests; full suite 2505 pass / 20 skip per record).
- **Migration `013` applied FIRST (migration-first), before relying on the new API** — secret-safe
  runbook (temp `/32` firewall to `ib-stg-psql`, DATABASE_URL read from the app setting and never
  printed, guaranteed separate-call firewall cleanup → only `AllowAzureServices` remains):
  - `alembic current` **012** → `alembic upgrade head` (012→013) → `alembic current` **013 (head)**; `alembic_version=013`.
  - `extracted_documents` created: PK `id`; **unique index** `ix_extracted_documents_content_hash`;
    indexes on `company_id`, `agent_run_id`; FKs `company_id→companies` **SET NULL**,
    `agent_run_id→agent_runs` **SET NULL**.
  - `extracted_facts` created: PK `id`; index on `extracted_document_id`; FK `→extracted_documents` **CASCADE**.
  - Row counts 0/0 (no backfill); no unexpected schema changes.
- Deploy: **API run `30935810190`** (push-triggered), **success** @ `354a5ba`. **Web NOT redeployed**
  — 0 `apps/web/**` files in the diff → `deploy-web-staging` path-filtered; web stays at `3efda60`.
- **API SHA** (`GET /health commit_sha`): `354a5ba` stable ×3, `environment=staging`. **AUTH_TEST_MODE**
  absent (unauth `POST /final-reports/from-company/{id}`, `GET /market-discovery/runs` → **401**).
- **Flag:** `PRIMARY_DOCUMENT_INGESTION_ENABLED` flipped **absent(OFF) → true** (36→37 settings, ONLY
  that key added, all 15 numeric knobs at code defaults, `/health` stable ×3 post-restart);
  `PRIMARY_DOCUMENT_OCR_ENABLED` left **absent (OFF)**; no unrelated config changed. **Flag KEPT ON**
  after validation (human-approved).

## Staging validation — Phase A (OFF regression) — GREEN
Both new flags absent(OFF). Fresh AAPL/US/free_real run (final **`7a22fd2b-8e4b-4627-bb5d-b60e1eeaa8aa`**),
HTTP **201**, **142.23s**, council **8/8** (Slice-4 retry recovered the chair). All deep-ingestion
markers **ABSENT** (`primary_document_artifacts`, `document_content_hash`, page/table provenance,
extracted-documents appendix, ocr/pdfplumber runtime); `source_connector_evidence_collected` shows
`primary_document_count=0`/`primary_fact_count=0` (shallow 29B.2 path only). `extracted_documents` /
`extracted_facts` remained **0/0** (DB-verified). Safety flags hold (schema_valid/safety_valid/
human_review_required=true, publication_ready=false, forbidden_terms=[]); logs + response secret-clean.
⇒ OFF is byte-compatible with the pre-Slice-5 baseline; the deep path did not activate.

## Staging validation — Phase B (ON, A–I) — GREEN on safety/correctness; efficacy caveat below
ON runs (all free_real, council azure_openai / gpt-4.1-mini):

| Run | report_id | HTTP | time_total | council | primary documents |
|---|---|---|---|---|---|
| AAPL r1 | `da991c68-8db9-496a-bbd6-4f2b99239820` | 201 | 148.66s | 8/8 | **0 candidates** |
| AAPL r2 (regen) | `1fd6b9b8-afa0-45d5-928f-7fdf216f375d` | 201 | 151.99s | 7/8 → chair fallback | 0 candidates |
| CFR | `8d618ac5-75a5-4583-b55c-5bda51adeb32` | 201 | 90.71s | 8/8 | 3 fetched, **all 3 `extraction_failed` (encrypted)** |

- **Wall-clock GREEN**: all < ~230s gateway (margins +78–139s), no 502/504. Ingestion adds no material
  time (CFR `total_ingestion_ms≈10.1s`: per-doc fetch 0.5–1.15s + extraction 0.95–4.88s). No timeout regression.
- **SEC/XBRL remains authoritative** for AAPL (`structured_financial_fact_count=5`, T1/T2 government
  tiers; SEC×37/XBRL×3/EDGAR×9); document extraction is purely supplemental and displaced nothing.
- **Evidence-pack floor/cap intact** (AAPL evidence_item_count=20, financial floor satisfied; CFR
  financial_fact=0 with **no fabricated values**). **Slice-3 reconciliation present** (AAPL r1
  db_citation=170/council=163, r2 137/130, CFR 148/143). **Slice-4 council retry intact** (r2 chair
  `budget_exhausted` → deterministic `insufficient_data` fallback, forbidden_terms=[]).
- **CFR scoping**: Richemont/SIX/IR only, **0 Apple/AAPL/Nasdaq leakage**; encrypted PDFs honestly
  classified `extraction_failed`, metadata-only references stay references (`primary_source_reference_count=6`),
  **OCR not claimed or simulated**, nothing persisted (`documents_created=0, skipped=3`).
- **Invariants (all reports)**: schema_valid/safety_valid/human_review_required=true,
  publication_ready=false, research_complete=false (honest), data_provenance=real, is_mock=false,
  forbidden_terms=[]; unauth→401. **DB after all runs: `extracted_documents=0`, `extracted_facts=0`,
  0 OCR-method rows, 0 duplicate content_hash** — failed/absent extractions persist nothing.
- **Security**: response surfaces + app logs contain no secrets / signed-URL query params / raw
  document body / extracted text; no SSRF/limit guard needed to fire (CFR PDFs fetched then failed to parse).

### Positive-extraction demonstration attempt (5 more registered issuers) — none succeeded
To try to observe the *success* path, five additional allowlisted issuers were run (native-text URDs /
annual reports are often parseable): **BA** `6eb6e1ce`, **BRBY** `5f9a0623`, **KER** `acf5dd31`,
**MC** `ecd822ee`, **RMS** `44f22b76` — all HTTP 201, <62s. **Every one produced `primary_document_count=0`**:
their IR annual-report index pages are **JS-gated / SPA-rendered**, so the connector's static
link-discovery found **0 downloadable document links** and the deep extractor was never invoked (fell
back to 4–5 metadata-only references each). (These probes were mock-seeded — provider-independent for
document discovery — so they are not free_real validation reports; they only test link discovery.)

## Efficacy caveat (explicit — this is why the closure is "foundation")
**No successful native extraction was demonstrable on staging.** Across **7 registered issuers**
(AAPL, CFR, BA, BRBY, KER, MC, RMS) the pipeline produced **0 extracted documents, 0 extracted facts,
0 provenance-bearing citations**, and the **reuse-on-regen cache was never exercised** (nothing to
reuse). The pipeline is wired, deployed, running, bounded, and **fails safe** — but it currently has
**no live native-text source to succeed against**, for three reasons that are all **5B-deferred
capabilities, not code defects**:
1. **AAPL** — no accessible native document (no registered IR page; **SEC 10-K/20-F body fetch is
   deferred** — ADR-014).
2. **CFR** — 3 IR PDFs fetched but **encrypted** → need **OCR** (NoOp/OFF — deferred to 5B).
3. **BA/BRBY/KER/MC/RMS** — IR index pages are **JS-gated** → static link discovery finds no document
   links; needs **JS-capable or direct-document-URL discovery** (a NEW finding from this validation).

The extraction/validation/provenance/persistence/reuse **success path is covered by the 102 merged
deterministic unit tests** (native HTML + native-text-PDF extraction with page/table provenance,
validator downgrade/reconcile, floor/cap, content_hash dedup + TTL reuse, SSRF/limit guards) — but it
has not been observed end-to-end against a real staging document.

## Required closure statements (per the closure mandate)
- **Native HTML/PDF extraction is LIVE** — the deep-ingestion code path is deployed and executes
  (it fetched and attempted extraction on CFR's 3 PDFs); it is not a mock.
- **OCR provider remains a NoOp seam and OCR remains OFF** (`get_ocr_provider` returns `NoOpOcrProvider`
  even if the flag were set; `PRIMARY_DOCUMENT_OCR_ENABLED` absent/false).
- **Scanned / encrypted PDFs are not yet extractable** (CFR's encrypted PDFs degraded honestly to
  metadata-only; no fabrication).
- **Frontend rendering of the new extracted-document / provenance / appendix fields remains deferred** (5B).

## Exact remaining Slice 5B scope
1. **Real OCR adapter + wiring** — Azure Document Intelligence adapter behind the NoOp seam, so scanned/
   encrypted issuer PDFs (e.g. CFR) can be extracted; keep `PRIMARY_DOCUMENT_OCR_ENABLED` gated + admin-approved.
2. **SEC 10-K / 20-F body fetch** — a hardened SEC full-text fetcher so US issuers (e.g. AAPL) have an
   accessible native document to extract (today they have 0 candidates).
3. **JS-capable / direct-document-URL link discovery** (NEW, surfaced by this validation) — modern SPA
   IR index pages (BA/BRBY/KER/MC/RMS) expose no static links; add a JS-rendering fetch path or a
   curated direct-document-URL registry so native-text PDFs/HTML are actually reachable.
4. **Frontend rendering** — surface `extracted_documents` counts, page/section/table provenance, and the
   extracted-document citations in the report/appendix UI.
5. **Resolve-then-connect IP-pinning + async DNS** — close the documented DNS-rebinding TOCTOU residual
   (ADR-014) on the live fetch path before the ingestion flag is relied on in production.

## Final status
**Slice 5A CLOSED + STAGING-VALIDATED as a FOUNDATION (`354a5ba`), 2026-08-04 — WITH EFFICACY CAVEAT.**
Migration 013, the OFF/ON flag behavior, the safe-failure/honesty path, all safety/scoping/security/
wall-clock invariants, and Slice 1–4 non-regression are validated. A successful native extraction was
**not** demonstrable on staging (no accessible native source — all causes are 5B enablers). Flag
`PRIMARY_DOCUMENT_INGESTION_ENABLED` **KEPT ON** (allowlist-only, SSRF-guarded, persists nothing on
failure). **The complete Slice 5 and Phase 32A are NOT closed** — Slice 5B (above) remains and must NOT
auto-start.
