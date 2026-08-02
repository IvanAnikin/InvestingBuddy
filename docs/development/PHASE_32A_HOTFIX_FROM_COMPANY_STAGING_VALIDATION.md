# Phase 32A hotfix — `from-company` company-scoped selection — Staging Validation Plan

**Status:** DRAFT — execute only AFTER human-approved merge + deploy.
**Branch:** `hotfix/from-company-scoped-report-selection`
**Migration:** `012_add_report_company_id.py` (adds `reports.company_id` FK + `ix_reports_company_id`). **Manual staging apply required** (staging migrations are not automated in this project).
**Scope:** confirm `from-company/{company_id}` selects the requested company's own most-recent completed analysis, never another company's, and fails clearly (404) when the company has none — with no auth/safety/publication/provenance regression.

> This plan supersedes the "run per-company + `from-report`" workaround for the
> defect where `from-company/CFR` returned an Apple analysis.

---

## Pre-conditions (do NOT proceed until all true)

- [ ] PR merged to `main` with explicit human approval.
- [ ] API + Web deploy runs succeeded.
- [ ] Deployed API `/health` + `/api/version` report the merged commit SHA (×3, env=staging).
- [ ] `AUTH_TEST_MODE` absent from staging app settings.
- [ ] Final LLM/source flag state confirmed unchanged (this hotfix adds **no** new flags): `LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED=true`, `LLM_PROVIDER_COUNCIL=azure_openai`, `SOURCE_CONNECTOR_ENABLED=true` (per Slice 2 closure).

---

## Step A — Apply migration 012 to staging (manual)

1. Apply `alembic upgrade head` against the staging DB (manual per project gotcha).
2. Assert `alembic current` / head == **`012`**.
3. Assert the column exists: `reports.company_id` (nullable UUID, FK → `companies.id`, `ON DELETE SET NULL`) and index `ix_reports_company_id` present.
4. Confirm existing (pre-fix) `reports` rows have `company_id IS NULL` (expected — no backfill).

## Step B — Generate fresh AAPL and CFR analyses

1. Run a fresh company analysis for **AAPL** → record `agent_run_id_AAPL`, source `draft_report_id_AAPL`, `company_id_AAPL`.
2. Run a fresh company analysis for **CFR** (Richemont) → record `agent_run_id_CFR`, source `draft_report_id_CFR`, `company_id_CFR`.
3. Confirm the newer of the two is the **globally newest** completed report (note which company it belongs to — call it `NEWER`, the other `OLDER`).
4. Verify each fresh source draft carries a non-NULL `company_id` equal to its own company (DB or admin read).

## Step C — Cross-company selection (the core assertion)

1. Call `POST /api/v1/final-reports/from-company/{company_id_OLDER}` (the company that is **not** globally newest).
2. Confirm the generated final report's lineage (`workflow_status.report_id` / `agent_run_id`) points at **OLDER's own** source draft + run — **NOT** the globally-newest `NEWER` report.
3. Confirm the report identity (company name / ticker / exchange) is OLDER's, not NEWER's.

## Step D — Reverse order and repeat

1. Run a *second* fresh analysis for `OLDER` so it becomes the globally newest.
2. Call `from-company/{company_id_NEWER}` and confirm it returns NEWER's own (now older) report — no leak to the globally-newest OLDER report.

## Step E — No eligible report → 404 (no cross-company fallback)

1. Pick (or create) a Company that has **only** pre-fix NULL reports (or no reports).
2. Call `from-company/{that_company_id}` → expect **404** with detail `No eligible completed analysis report...`.
3. Confirm **no** report row was created for that call (no thin/fabricated report).

## Step F — Unknown company → 404

1. Call `from-company/{random_uuid}` → expect **404** with detail containing `not found`.

## Step G — `from-report` unchanged

1. Call `from-report/{draft_report_id_AAPL}` → confirm it regenerates from exactly that report id, regardless of which report is globally newest.

## Step H — Safety / publication / provenance invariants (on a from-company success)

For the Step C / D generated reports, confirm:
- [ ] `schema_valid = true`
- [ ] `safety_valid = true`
- [ ] `human_review_required = true`
- [ ] `publication_ready = false`
- [ ] `is_mock` / `data_provenance` correct for live data (`data_provenance=real`, `is_mock=false` on real runs; absence ⇒ `unknown`, never coerced to mock)
- [ ] no fabricated numbers / no BUY/SELL/HOLD/WATCH / no price target / fair value in output

## Step I — Auth + secret-scan

- [ ] Unauthenticated `from-company`, `from-report`, run-analysis, and admin GETs return **401**.
- [ ] Publication endpoints remain admin-gated.
- [ ] Scan the API response surface + relevant logs for secrets / tokenized URLs → **CLEAN**.

---

## Evidence to record

| Field | AAPL | CFR |
|---|---|---|
| company_id | | |
| source draft report_id | | |
| source agent_run_id | | |
| from-company final report_id | | |
| selected `workflow_status.report_id` | | |
| selected `workflow_status.agent_run_id` | | |
| schema_valid / safety_valid | | |
| human_review_required / publication_ready | | |
| is_mock / data_provenance | | |

Plus: deployed API/Web SHA, alembic head (`012`), 404 evidence for Steps E/F, `from-report` evidence for Step G, unauth-401 evidence, secret-scan result.

---

## Pass criteria

All of A–I pass, cross-company selection is correct in **both** orderings (C + D), the no-report and unknown-company paths 404 with no fabricated report, `from-report` is unchanged, and all safety/publication/provenance/auth invariants hold. Only then update the ledger row from 🟡 to ✅ (closed + staging-validated) and write the closure report.
