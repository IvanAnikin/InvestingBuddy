# Closure Report — Phase 32A Slice 1: lineage / identity / real-mock provenance + deterministic-section preservation

> Produced after merge + deploy + staging validation. All SHAs and results below are real.
> Closed 2026-08-02.

## Summary
Slice 1 repairs the **assembly** half of the real-data analysis pipeline: already-collected
real data (identity, provider provenance, financial snapshot, deterministic Bull/Bear/Risk/
Valuation/Committee sections) now survives regeneration into the current-schema final report.
Root cause was a lossy markdown state-reconstruction in the admin `generate_from_report` /
`generate_from_company` adapter (it regex-scraped only the `catalyst_discovery` JSON block, so
14 of 15 adapter keys came back `None` → identity "Unknown", `is_mock` default-True, sections
`available:false`, empty snapshot, stale schema-invalid checklist note). Fix: a bounded,
secret-stripped, flat structured-state **envelope** round-trip from the Phase-9 writer; a
tri-state `data_provenance` (absence ⇒ unknown, never mock); a public-only DB identity fallback;
and a post-validation checklist/status recompute. Full architecture: `PHASE_32A_PIPELINE_REPAIR.md`.

## Merge / deploy
- **Slice-1 PR #71** squash-merged → `main` **`a26be3070c0f3dda8db499f95878bacaab1b85ac`** (`a26be30`).
- **Closing hotfix PR #72** squash-merged → `main` **`cf9d147eff6c39142e8b03a373dd1f1b0cf77335`** (`cf9d147`).
- Deploys: Slice-1 API run `30744460879` + Web run `30744498753` (@ `a26be30`); hotfix API run
  `30752027507` + Web run `30752042756` (@ `cf9d147`). All four **success**.
- **API SHA** (`GET /health` `commit_sha`): `cf9d147…` — matches merge SHA? **yes** (stable ×3).
- **Web SHA** (`GET /api/version` `commit_sha`): `cf9d147…` — matches? **yes** (web redeployed per the
  explicit deploy-both instruction; a backend-only change would otherwise leave web unchanged).
- **Migration:** none — DB head `011` unchanged (AD-5; no alembic in either merge diff).
- **AUTH_TEST_MODE:** absent — confirmed (unauth protected routes → 401).

## The closing hotfix (finding + fix)
Slice-1 staging validation PASSED all core criteria but surfaced ONE finding: the Phase-31
`research_memo` embedded a **stale** copy of the checklist still noting **"Schema invalid"**
(`not_completed_count`=6 vs authoritative 5), because the memo is built before validation (so its
prose is safety-scanned) and RC-6 recomputed only the authoritative checklist. The hotfix extracts
`_memo_human_review_checklist_snapshot()` and, immediately after the RC-6 recompute (and before save),
refreshes the memo's embedded `human_review_checklist` sub-field from the fresh authoritative
checklist. The memo prose is still built exactly once, before validation (stays under the safety gate);
the refreshed snapshot is a deterministic subset of already-scanned content. Dark-safe (no-op when
`SOURCE_RESEARCH_MEMO_ENABLED` off); `publication_ready`/`human_review_required` untouched.

## Staging validation

### Slice-1 golden path (on `a26be30`)
New AAPL/US/free_real/use_llm legacy draft `d147cdd2-f713-4244-a869-138a6abdde06` (carries the
envelope) → regenerated current-schema report `f5fedc5c-e847-43c2-9f89-2f04c1b8d6e7`:
Apple Inc. / AAPL / Nasdaq, `is_mock=false`, `data_provenance=real`, all 6 deterministic sections
populated, lineage preserved (`workflow_status.report_id=d147cdd2`, `agent_run_id=6906a7d4`),
`schema_valid`/`safety_valid`/`human_review_required`=true, `publication_ready`=false. Dark-safety:
mock draft `dcf7dce9` → 0 JSON blocks (envelope absent), from-report → honest `unknown` (not mock),
no fabrication. Old pre-envelope `23cc7a2f` → honest `unknown` (no DiscoveryCandidate/Scorecard
lineage to key on — DB fallback fires only for *recoverable* parents; SAFE). Council 4/8 =
environmental Azure gpt-4.1-mini TPM; deterministic sections rendered from envelope-restored summaries.

### Closing-hotfix re-validation (on `cf9d147`) — the finding is gone
Regenerated from the SAME legacy draft `d147cdd2` → NEW final report
**`4c354f29-31ae-4da6-ba38-eef024e795bf`**:
- **header `schema_valid` = true** — PASS.
- **`workflow_status.schema_valid` = true** — PASS.
- **Authoritative `human_review_checklist`:** schema item "Schema validation passed…" `completed=true`,
  `note=null` (consistent with `schema_valid=true`); not-completed count = 5 (genuine manual/data gaps).
- **`research_memo.human_review_checklist`: `not_completed_count=5` EQUALS authoritative 5, item set
  identical, "Schema invalid" ABSENT from the memo block and the entire report JSON — the stale
  contradiction is GONE** (the key hotfix assertion) — PASS.
- Identity **Apple Inc. / AAPL / Nasdaq / US / USD**, no "Unknown"; `is_mock=false`,
  `data_provenance=real` (no mock fallback); `latest_close=308.91 USD` as_of 2026-07-31 (T2).
- Sections preserved: Financial snapshot, Bull, Bear, Risk, Valuation Readiness, Committee — all
  populated (none `available:false`).
- `safety_valid=true`, `human_review_required=true`, `publication_ready=false`, status `draft`.
- **Logs / no-secrets:** CLEAN — report JSONs secret-free; runtime stream not in downloadable LogFiles
  (App Insights/stdout), slice adds no new network + counts-only logging.
- **Safety / publication:** no recommendation/valuation language in product output; publication
  admin-gated, not public; unauth `POST /from-report/<id>` → 401 (no auth/publish regression).

## Final flags (unchanged; slice adds none)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_MACRO/EVENT/DOCUMENT_EXTRACTION/RESEARCH_MEMO`=on · `SOURCE_TRANSLATION_ENABLED`=off. The
envelope is gated by envelope-presence, not a flag.

## Tests (pre-merge, hotfix branch on `a26be30` base)
Backend **2312 passed / 20 skipped / 0 failed** (+4 vs the a26be30 baseline; skip variance is
environmental Azure-gated `test_phase7_azure_openai_real.py`). ruff clean; mypy **71** = baseline
(0 in the changed file). Web unaffected. Reviews: security **PASS**, PR-review **GO**.

## Limitations / follow-ups (NOT in Slice 1)
- **Slice 2:** wire `fundamentals_data` / `financial_data_summary` / `trend_signal_summary` into
  `build_evidence_pack` + per-category budget floors so news can't crowd out financial evidence.
- **Slice 3:** replace the citation `report_id` backfill `pass` with a real UPDATE; distinguish
  council E# evidence from DB citations honestly.
- **Slice 4:** council retry / backoff / reserved critical-agent budget / deterministic fallback
  (the 4/8 TPM partial is environmental, addressed here).
- **Slice 5:** deeper document ingestion (SEC/8-K/earnings HTML, table extraction, bounded OCR).
- Env caveats: council partial-agents on staging are Azure TPM; runtime app-log tail not read-only
  accessible (response-surface scan used); DB head `011` inferred from the no-alembic diff.

## Final verdict
**CLOSED + STAGING-VALIDATED** (`cf9d147`), 2026-08-02. Slice 1 acceptance criteria met; the one
finding was fixed by the closing hotfix and re-validated. Slices 2–5 remain and must NOT auto-start.
