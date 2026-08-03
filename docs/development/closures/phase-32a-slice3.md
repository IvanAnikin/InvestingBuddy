# Closure Report — Phase 32A Slice 3: source/citation persistence + honest appendix reconciliation

> Produced after merge + deploy + staging validation. All SHAs, IDs and results below are real.
> Closed 2026-08-03. Verdict: **CLOSED + STAGING-VALIDATED — WITH ENVIRONMENTAL NOTE.**

## Summary
Slice 3 persists and reconciles a report's **source/citation lineage** so the final-report
**Source Citation Appendix** stops showing `0 sources / 0 citations / "No sources cited yet"`
while the LLM council's claims cite evidence. Root cause (§1.4): (D1) the company-analysis
draft created deterministic profile/price/SEC-XBRL `Source`+`Citation` rows but the citations
got `report_id=NULL` and the backfill loop was a literal `pass`; (D2) the current-schema FINAL
report (`_save_final_report_draft`) was a brand-new **orphaned** `Report` (no `company_id`, no
`created_by_agent_run_id`, discarded `source_report_id`, zero citations) so the appendix loader
returned `[]`; and the council's in-memory `E#` evidence was never persisted or resolvable.
Behind ONE new default-OFF flag `REPORT_CITATION_PERSISTENCE_ENABLED`; **NO migration** (head
stays `012`); OFF ⇒ byte-identical. Full architecture: `PHASE_32A_PIPELINE_REPAIR.md §9`.

## Merge / deploy
- **PR #75** squash-merged → `main` **`3efda6084b310731f22302c2b30c8bfd723e6e3a`** (`3efda60`).
- Reviewed head at merge: `1a3e76b` (diffs empty vs the merge SHA — identical code).
- Deploys: **API run `30826659074`** + **Web run `30826658153`** (both push-triggered), both **success** @ `3efda60`.
- **API SHA** (`GET /health` `commit_sha`): `3efda60` — stable ×3, `environment=staging`, build `30826659074`. **Match: yes.**
- **Web SHA** (`GET /api/version` `commit_sha`): `3efda60` — stable ×3. **Match: yes.**
- **Migration:** none — no alembic in the merge diff; DB head **`012`** unchanged.
- **AUTH_TEST_MODE:** absent — unauth `POST /final-reports/from-company`, `POST /market-discovery/runs`,
  `GET /reports/{id}`, `GET citations` all → **401**.
- **Feature flag:** `REPORT_CITATION_PERSISTENCE_ENABLED` flipped **absent(OFF) → true** (human-approved;
  confirmed `true`; only that setting changed, `/health` stable ×3 post-restart). Other flags unchanged
  (`LLM_COUNCIL_ENABLED`/`LLM_DISCOVERY_COUNCIL_ENABLED`/`SOURCE_CONNECTOR_ENABLED`/`LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED`
  ON; `LLM_PROVIDER_COUNCIL=azure_openai`).

## Formal merge-gate review (pre-merge, head `1a3e76b`)
- **ib-security-agent → PASS** (no blocking): every persisted URL passes `canonicalize_source_url`
  (SAS `sig=`/`token`/userinfo/fragment stripped); `source_quote` bounded (≤500, `None` for
  metadata-only); `primary_fact`/`provenance`/`source_id` are `Field(exclude=True)` runtime carriers
  never written to DB or logged; flag-OFF byte-identical; no auth/publish/route/valuation/migration
  surface.
- **ib-pr-review-agent → GO after B1 fix.** It found ONE blocking bug (**B1**) which was fixed before
  merge (see below). Non-blocking N2 (no DB unique constraint on `content_hash` → theoretical concurrent
  double-insert) accepted as the documented deferred refactor.
- **Empirical probes → GREEN:** atomicity (flush-only inside the single commit → mid-persistence failure
  rolls back the whole write), canonical-hash distinctness (different SEC periods → distinct hashes),
  URL-secret stripping.

### The B1 fix (found by review, fixed pre-merge)
Slice 3 stamps `company_id` + `created_by_agent_run_id` on the final report (for lineage), which made a
generated final report satisfy the `generate_from_company` source-selection query and — being newer than
the draft — get picked on the 2nd `from-company` call, re-introducing the Slice-1 lossy re-parse (final
reports carry no analysis-state envelope) AND polluting the reconciliation counts with the prior final's
own `council:%` citations (observed stored `db_persisted_citation_count`=7 vs fresh=4). **Fixed**:
`generate_from_company` sources ONLY analysis drafts (`Report.final_report_version IS NULL`);
`_evidence_reconciliation_counts` counts only non-`council:%` rows as deterministic (stored count always
equals a fresh loader read); `_council_evidence_links` dedups a repeated `citation_id` on a single claim.
Regression tests: `test_from_company_regeneration_sources_draft_and_count_is_honest`,
`test_repeated_citation_id_on_one_claim_dedups`.

## Staging validation — Phase A (flag OFF, dark regression)
Fresh AAPL/US/free_real run (`run 098ccfe2`, company `5d36744d`, draft `629dfe61`, agent_run `21379a79`,
final **`76d3bfe0`**): the SIX Slice-3 count keys are **absent**, the appendix is the pre-slice shape
(`sources.total=0`, `citations.total=0`, no reconciling `note`), `schema_valid`/`safety_valid`/
`human_review_required`=true, `publication_ready`=false; no recommendation language; secret-clean.
Confirms OFF ⇒ byte-compatible with pre-Slice-3. Council 1/8 (Azure-TPM environmental).

## Staging validation — Phase B (flag ON, A–I) — VALIDATED
Reports (all free_real, council provider azure_openai):

| Purpose | run_id | draft (agent_run) | final report | council |
|---|---|---|---|---|
| AAPL r1 | `a4ee8879` | `53dc7b5d` (`a9860e22`) | **`5b7b464d`** | 4/8 |
| AAPL r2 (regen) | — | (same draft `53dc7b5d`) | **`d56d3d89`** | 1/8 |
| AAPL from-report | — | (same draft) | `a788a002` | 5/8 |
| CFR | `83d32cbe` | `cb71d883` (`eca8c405`) | **`5c59cf78`** | 6/8 |

AAPL company `5d36744d` (Apple Inc.) · CFR company `041cc7e4` (Compagnie Financière Richemont SA).

### The six honest counts (stored == freshly-derived in every case)
| count | AAPL r1 | AAPL r2 | CFR |
|---|---|---|---|
| primary_source_reference_count | 0 | 0 | 6 |
| extracted_evidence_count | 15 | 15 | 5 |
| structured_financial_fact_count | 5 | 5 | **0** |
| db_persisted_source_count | 21 | 21 | 13 |
| db_persisted_citation_count | 85 | 30 | 102 |
| council_claim_citation_count | 78 | 23 | 97 |

Reconciliation: AAPL r1 `85 = 7 draft-deterministic + 78 council`; r2 `30 = 7 + 23`; CFR `102 = 5 + 97`.
Sources: AAPL `21 = 2 deterministic + 19 council` (stable across r1/r2 — no accumulation); CFR `13 = 2 + 11`.
Every report's own persisted rows equal its `council_claim_citation_count`.

### Acceptance — all PASS
- **AAPL:** correct `company_id` + source-report lineage (`workflow_status.report_id=53dc7b5d`, `agent_run=a9860e22`);
  only analysis DRAFTS eligible / finals never recursively selected (B1); non-zero persisted sources + citations;
  SEC/XBRL provenance retained (5 facts, council rows T1_primary_filing / T1_primary_company_source); E# aliases
  resolve to persisted evidence and are NEVER exposed as DB ids (`field_path=council:<agent>`, real UUID source_ids);
  repeated regeneration stable with no duplicate rows (deterministic layer identical, Sources 21 each); per-claim
  citation dedup; **stored reconciliation == fresh, no inflation on the 2nd generation**; partial council persists
  ONLY completed-agent citations (failed agents → 0); six counts distinct + reconciling note present.
- **B1 regression (explicit):** r2 sourced DRAFT `53dc7b5d` (NOT prior final `5b7b464d`, excluded by the
  `final_report_version IS NULL` filter); r2 count `30` (7 deterministic + 23 council), **not** r1's `85` — no
  inflation; r1's council claims absent from r2.
- **CFR:** Richemont/SIX references stay metadata-only (`link_metadata_only`/`metadata_only`);
  `structured_financial_fact_count=0` (no SEC/XBRL for a Swiss issuer); no metadata reference becomes a financial
  citation; honest wording; no fabricated evidence; scoped to Richemont.
- **Cross-company:** AAPL & CFR source/citation URL sets disjoint (contamination scan False both ways); `company_id`
  authoritative (`5d36744d` ≠ `041cc7e4`); no ticker/name inference; from-company AND from-report both preserve
  ownership+lineage.
- **Appendix/UI honesty:** never "No sources cited" while citations exist; metadata-only never labelled as verifying
  a financial claim.
- **Security/atomicity:** no secrets in any persisted URL (eodhd URLs canonicalized without `api_token`); bounded
  excerpts, no raw documents; transactional flush-only + single commit, mid-persistence rollback unit-test-covered
  (not injectable on live staging); unauth POST/GET → 401.
- **Safety (all 4 reports):** `schema_valid`/`safety_valid`/`human_review_required`=true, `publication_ready`=false;
  forbidden BUY/SELL/HOLD/WATCH + price-target/fair-value appear ONLY in the exempt `disallowed_outputs` /
  negated-disclaimer text.
- **Secret scan:** response surface CLEAN; **app logs downloaded + scanned CLEAN** (incl. today `2026_08_03`).

## Tests (pre-merge, head `1a3e76b`)
Backend **2366 passed / 20 skipped / 0 failed** (slice-3 suite **15/15** incl. the 2 B1/N3 regressions); ruff
clean; mypy **71 = baseline (0 new)**; web typecheck+lint clean; both CI checks (Lint & Test; Typecheck, Lint &
Build) green. No migration.

## Environmental note (non-blocking)
Council completion varied by run (AAPL r1 4/8, r2 1/8, from-report 5/8; CFR 6/8) — the documented Azure
`gpt-4.1-mini` TPM partial-council limiter owned by **Slice 4**. This is exactly why the council portion of
`db_persisted_citation_count` differs between regenerations while the **deterministic layer stays fixed** (7 for
AAPL, 5 for CFR) and each report internally reconciles (stored == fresh). The persistence layer correctly stores
only completed-agent citations. Cleanly separated from any real defect; no real failures observed.

## Limitations / follow-ups (NOT in Slice 3)
- **Slice 4:** council retry / backoff / reserved critical-agent budget (resolves the TPM partials so more council
  claim citations persist per run).
- **Slice 5:** deeper document ingestion (SEC/8-K/earnings HTML, table extraction, bounded OCR).
- Deferred refactor (documented): a dedicated `evidence`/`claim_evidence_link` table + a Source canonical-key
  unique constraint (would enforce dedup at the DB level and unify the two-layer SEC-source representation).
- from-workflow-state live path does not load deterministic citations for the appendix without a source report
  (the validated path is admin from-company/from-report).
- Old pre-slice reports keep honest zero-count appendices (not force-backfilled — safely unrecoverable).

## Final verdict
**CLOSED + STAGING-VALIDATED (`3efda60`), 2026-08-03 — WITH ENVIRONMENTAL NOTE.** All Slice 3 acceptance criteria
met; the sole caveat (Azure-TPM partial councils) is environmental and owned by Slice 4. Flag
`REPORT_CITATION_PERSISTENCE_ENABLED` kept **ON**. Slices 4–5 remain and must NOT auto-start.
