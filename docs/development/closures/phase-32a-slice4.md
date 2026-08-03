# Closure Report — Phase 32A Slice 4: LLM council reliability under Azure rate limits

> Produced after merge + deploy + staging validation. All SHAs, IDs and results below are real.
> Closed 2026-08-04. Verdict: **CLOSED + STAGING-VALIDATED — WITH FOLLOW-UP NOTES.**

## Summary
Slice 4 repairs the **reliability** half of the pipeline. The single-company council runs
**strictly sequentially and INLINE in the HTTP request handler** (bounded only by the ~230s
Azure gateway) with the Azure client hardcoded `max_retries=0`, so under Azure `gpt-4.1-mini`
TPM limits a large pack (AAPL) left ~4/8 agents `failed` on a single 429 and the last-place
Committee Chair (no fallback) produced a null `committee_label` — the "Azure-TPM partial
councils" environmental note carried from Slices 2–3. Slice 4 adds, behind ONE new default-OFF
master flag `LLM_COUNCIL_RETRY_ENABLED`: transient-vs-permanent error classification, a bounded
priority-ordered retry pass over ONLY transiently-failed agents (capped exponential backoff +
jitter, capped honored provider `retry-after`) under a strict TOTAL wall-time budget with a
reserve for `red_team` + `committee_chair`, selective in-place replacement of recovered agents,
and a deterministic Committee Chair fallback (`committee_label="insufficient_data"`, no
citations, no recommendation) when the LLM chair cannot complete. **NO migration** (head stays
`012`); OFF ⇒ council byte-identical; `publication_ready` stays False, `human_review_required`
stays True, failed agents create no citations. Full architecture: PR #76 / `docs/DECISIONS.md`
ADR-013.

## Merge / deploy
- **PR #76** squash-merged → `main` **`11ab66b971b27ea7be8e1b5ecdd63f22613ca244`** (`11ab66b`).
- Reviewed head at merge: code `5bbaaf4` + docs `ec2c507` (ib-security GO/PASS, ib-pr-review
  GO/APPROVED); the two non-blocking review suggestions (`.env.example` keys + the A–I staging
  checklist) were then added in **docs-only** commit `85ddf36` before merge.
- Deploy: **API run `30859333381`** (push-triggered), **success** @ `11ab66b`.
  **Web was NOT redeployed** — Slice 4 changed no `apps/web/**` files, so `deploy-web-staging`
  is correctly skipped by its path filter; the web app stays at Slice 3's `3efda60`
  (`GET /api/version commit_sha=3efda60`). No web change ⇒ no web deploy needed.
- **API SHA** (`GET /health` `commit_sha`): `11ab66b` — stable ×3, `environment=staging`,
  build `30859333381`. **Match: yes.**
- **Migration:** none — no alembic in the merge diff; DB head **`012`** unchanged.
- **AUTH_TEST_MODE:** absent — unauth `POST /final-reports/from-company/{id}`,
  `POST /market-discovery/runs`, `GET /market-discovery/runs` all → **401**.
- **Feature flag:** `LLM_COUNCIL_RETRY_ENABLED` flipped **absent(OFF) → true** (human-approved;
  35 → 36 app settings, exactly one key added, none removed/changed; all 7 numeric knobs left at
  code defaults; `/health` stable ×3 post-restart). Other flags unchanged (`LLM_COUNCIL_ENABLED`
  / `LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED` / `REPORT_CITATION_PERSISTENCE_ENABLED` /
  `SOURCE_CONNECTOR_ENABLED` ON; `LLM_PROVIDER_COUNCIL=azure_openai`, model `gpt-4.1-mini`).

## Pre-merge gate (head `5bbaaf4` code + docs)
- **Tests:** backend **2401 passed / 20 skipped / 0 failed** (new `test_phase32a_slice4_council_reliability.py`
  **35 passed**; regression council/slice2/slice3/phase16 **136 passed**); ruff clean; `mypy app`
  **71 = baseline (0 new)**; CI "Lint & Test" green. No migration.
- **ib-security-agent → PASS** (no blocking): retry logs carry only safe scalars (attempt,
  agent_name, error_type type-name, duration_ms, backoff_ms, capped retry_after, counts) — never
  prompts/completions/evidence/credentials/headers/URLs; retries strictly bounded (attempt caps +
  total deadline + capped retry-after + capped backoff → no uncontrolled loop / DoS amplification);
  deterministic fallback uses only stored outputs + creates no citations; no auth/SSRF/publish
  change; flag default-OFF.
- **ib-pr-review-agent → GO/APPROVED**, no must-fix, no correctness defects, all 9 checklist items
  PASS (migration absent, auth/publish unchanged, tests present, docs honest/not-prematurely-closed,
  flag default-OFF + byte-identical, scope-disciplined, selective retry / one-entry-per-agent /
  chair-retried-last / reserve-math / fallback-fires-only-on-non-completion all verified).

## Staging validation — Phase A (flag OFF, dark regression) — GREEN
Fresh AAPL/US/free_real run (final **`e21b23d3-bbf6-46b2-9bd6-bbb8ead18bbd`**), HTTP **201**,
`time_total` **40.74s**, council **4/8** (4 completed / 4 failed, all `LLMRateLimitError`, the
chair among the failures). With the flag OFF: `chair_fallback_used` **ABSENT**,
`deterministic_committee_chair` **ABSENT**, NO retry fields anywhere; **0** `llm_agent_retry` /
`llm_agent_retry_skipped` / `llm_committee_chair_fallback` log events; `llm_council_completed`
present. `schema_valid`/`safety_valid`/`human_review_required`=true, `publication_ready`=false,
`research_complete`=false; `forbidden_terms_found`=[]. Response + logs secret-clean. **Strong
proof:** the chair itself hit a rate-limit failure — exactly the scenario the retry feature
targets — and with the flag OFF the code performed **no retry and no fallback**, recording the
partial 4/8 exactly as before. OFF ⇒ byte-compatible with pre-Slice-4.

## Staging validation — Phase B (flag ON, A–I + metrics) — GREEN
Reports (all free_real, council provider azure_openai / gpt-4.1-mini):

| Run | report_id | HTTP | time_total | council duration | council |
|---|---|---|---|---|---|
| AAPL r1 | `b54ff22f-5111-48b8-8bff-f44b78784ffc` | 201 | **136.55s** | 126.4s | **8/8** completed, no fallback |
| AAPL r2 (regen) | `04089c98-c1b5-4d5e-820c-70ac5eeb381a` | 201 | **151.77s** | 150.1s (= budget) | 6/8, 2 failed → **chair fallback** |
| CFR | `b7942746-ddf2-44a2-abad-06d3277fdb6f` | 201 | **89.16s** | 83.5s | 7/8, 1 failed (valuation_guard) |

AAPL company `5d36744d` (Apple Inc.) · CFR company `041cc7e4` (Compagnie Financière Richemont SA).
All live failures/retries were `LLMRateLimitError` (real Azure TPM 429s); no timeout/5xx occurred live.

### Retry / fallback behavior (from structured logs)
- **AAPL r1 → 8/8** (retries recovered everything; a decisive lift over the historical 4/8 baseline):
  financial_analyst / business_moat / catalyst / risk_governance first try; **valuation_guard +1**,
  **source_quality_critic +2**, **red_team +2**, **committee_chair +2** → real LLM chair,
  `committee_label=insufficient_data`, no fallback.
- **AAPL r2 → 6/8 + fallback**: financial_analyst (+1), valuation_guard (+2), source_quality_critic (+2),
  business_moat (+2/cap2), catalyst (+2/cap2), red_team (+2) all completed; `risk_governance` FAILED
  (retry skipped `budget_exhausted`); `committee_chair` FAILED (2 retries, 3rd skipped
  `budget_exhausted`) → **`chair_fallback_used=true`**, `deterministic_committee_chair` present,
  `committee_label=insufficient_data`, `key_points=[]` (no citations), states "no recommendation, no
  valuation conclusion, and no numeric price objective". **The LLM `committee_chair` entry stays
  `status=failed` in `agents[]` (visibly partial).**
- **CFR → 7/8**: chair completed (real); `valuation_guard` FAILED (LLMRateLimitError, 0 retries — see
  follow-up note 1).
- **Only transiently-failed agents retried; completed agents never re-run;** each `agents[]` has exactly
  8 entries, no duplicates (recovered agents replaced their placeholder in place).
- **`retry_after` honored + budget-clamped:** every `llm_agent_retry` carried `retry_after` with
  `backoff_ms ≈ retry_after×1000`, clamped below `retry_after` when near budget exhaustion (subject to
  the 30s retry-after cap / 20s backoff cap). Attempts never exceeded caps (2 optional / 3 critical).
- **Failed agents contribute 0 citations:** r1 (8 agents) `council_claim_citation_count=144`; r2 (6
  agents) `109` (Δ35 ≈ the 2 failed agents' share) — failed placeholders added nothing.

### Wall-clock acceptance — GREEN
All three runs below the ~230s gateway with margins **+93.4s / +78.2s / +140.8s**; none >200s; **no
502/504**, no client/gateway timeout; no uncontrolled loop (all retries ≤ caps). r2 (worst case) ran
the council to exactly the 150s `total_budget` ceiling then degraded gracefully (2 skips →
deterministic chair); total HTTP 151.77s.

### The six counts + reconciliation
| count | AAPL r1 | AAPL r2 | CFR |
|---|---|---|---|
| primary_source_reference_count | 0 | 0 | 6 |
| extracted_evidence_count | 15 | 15 | 5 |
| structured_financial_fact_count | **5** | **5** | **0** |
| db_persisted_source_count | 22 | 21 | 12 |
| db_persisted_citation_count | 151 | 116 | 117 |
| council_claim_citation_count | 144 | 109 | 112 |
| deterministic (db − council) = report citation_count | 7 | 7 | 5 |

Reconciliation exact: `151=144+7`, `116=109+7`, `117=112+5`; deterministic layer == each report's own
`citation_count` (7/7/5). AAPL Slice-2 financials present (`structured_financial_fact_count=5`,
`evidence_item_count=20`). Sources non-zero everywhere.

### Idempotency (AAPL r1 vs r2)
`len(agents)=8` both, no duplicates. Deterministic-layer counts **identical** (primary_source_reference
0/0, extracted_evidence 15/15, structured_financial_fact 5/5, final source_count 2/2, citation_count
7/7). Council portion differs by capacity (144 vs 109) — expected; **no accumulation** (r2 db citations
116 < r1 151, each reconciles to its own); new report_id, r1 unmutated. Slice-1/2/3 invariants intact.

### CFR
Metadata-only pack functional (`evidence_item_count=11`, `primary_source_reference_count=6`,
`metadata_only_source_count=6` — references stay references); `structured_financial_fact_count=0`;
**no fabricated financial values**; scoped to Richemont (9 "Richemont", 44 "CFR", **0 Apple/AAPL/Nasdaq**;
`company_id` correct). Cross-company isolation confirmed both ways (CFR 0 AAPL; AAPL 0 CFR).

### Failure-path coverage
The full injected matrix (429-then-success, timeout-then-success, 5xx-then-success, retry exhaustion,
permanent-not-retried, chair-exhaustion→fallback, all-provider-unavailable) is covered by the **35
merged deterministic tests** (green pre-merge). **Observed naturally live:** real 429
(`LLMRateLimitError`) → success retries (r1 recovered 4, r2 recovered 6, CFR recovered 5); `retry_after`
honored; budget-exhaustion skip; chair-exhaustion → deterministic fallback. Not exercisable live (no
injection, not fabricated): timeout/5xx-then-success, permanent-not-retried, all-provider-unavailable —
those rest on the deterministic tests (Azure emitted only 429s in this window).

### Safety / security (all 3 reports)
`schema_valid`/`safety_valid`/`human_review_required`=true, `publication_ready`=false,
`research_complete`=false (honest — councils partial / data thin); `forbidden_terms_found`=[]; no
recommendation/valuation/price-target language (scanned all sections incl. committee_chair_summary);
unauth POST→401. **Secret scan CLEAN** — 0 hits across app logs and all POST+GET bodies for `api_token=`,
`AZURE_OPENAI_API_KEY`, `DATABASE_URL`, `Bearer`, `AccountKey=`, `-----BEGIN`, `sk-`, `password`,
`Authorization:`; logs contain only structured events (agent_name/status/error_type/counts) — no
prompts, completions, provider messages, or evidence excerpts.

## Follow-up notes (non-blocking; do not affect the verdict)
1. **`valuation_guard` is not retried when it is non-critical** (i.e. the pack has no financial evidence,
   e.g. CFR). This is **as-designed**: the retry-priority order includes `valuation_guard` only when it is
   in the critical set (`has_financial_evidence(pack)` true), and the optional retry group is
   `{business_moat, catalyst, risk_governance}`. For CFR this is immaterial — `structured_financial_fact_count=0`
   means there is nothing to value, the report stays honest (metadata-only references, no fabricated values),
   and CFR's acceptance criteria all pass (7/8 acceptable, 8/8 remains possible where capacity permits). A
   later slice may add non-critical `valuation_guard` to the optional retry group so it also benefits from
   retries; not required for Slice 4.
2. **Worst-case wall-clock ≈152s** (r2), driven by the 150s `total_budget` ceiling — a comfortable +78s
   margin under the ~230s gateway now, but a candidate to monitor at p99 if sustained TPM pressure worsens.
   Lever if margin ever tightens: lower `LLM_COUNCIL_TOTAL_BUDGET_SECONDS` / `LLM_COUNCIL_CRITICAL_RESERVE_SECONDS`
   (no code change).

## Limitations / follow-ups (NOT in Slice 4)
- **Slice 5:** deeper document ingestion (SEC/8-K/earnings HTML, table extraction, bounded OCR).
- Non-critical `valuation_guard` retry (follow-up note 1).
- Deferred by design (DECISIONS ADR-013): concurrent council execution (worsens Azure TPM 429s on the
  inline path) and per-agent evidence projection / prompt trimming (risks evidence loss / reopens Slice 2).
- Moving the single-company council to a background task (a larger architecture change) remains an option if
  a future evidence model pushes wall-time toward the gateway.

## Final verdict
**CLOSED + STAGING-VALIDATED (`11ab66b`), 2026-08-04 — WITH FOLLOW-UP NOTES.** All Slice 4 acceptance
criteria met: retries lift AAPL from the historical 4/8 to 8/8 where capacity permits (r1); under harder
pressure the total budget bounds the council and chair exhaustion degrades to an honest deterministic
`insufficient_data` chair with no citations and no fabricated recommendation (r2); CFR's metadata-only
path stays honest (7/8, `structured_financial_fact_count=0`, no fabrication); reconciliation, idempotency,
cross-company isolation, safety, and secret-cleanliness all hold; wall-clock stays comfortably under the
gateway with no 502/504. The two follow-up notes are non-blocking. This resolves the "Azure-TPM partial
councils" environmental note carried by Slices 2–3. Flag `LLM_COUNCIL_RETRY_ENABLED` kept **ON**. Slice 5
remains and must NOT auto-start.
