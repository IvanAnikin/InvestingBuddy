# Closure Report — Phase 32A hotfix: company-scoped `from-company` final-report selection

**Status:** ✅ CLOSED + STAGING-VALIDATED
**Date:** 2026-08-03
**PR:** #74 (squash) → `main`
**Merge SHA:** `cd0eb876ce8394ffe7d3782cdf8362706615a6bd` (`cd0eb87`)
**Migration:** `012_add_report_company_id.py` (applied to staging; head = `012`)
**Precedes:** Phase 32A Slice 3 (not started)

---

## Problem

`FinalReportGeneratorService.generate_from_company` selected the **globally-newest**
completed report — the source-report query had no company predicate:

```python
select(Report).join(AgentRun, Report.created_by_agent_run_id == AgentRun.id)
  .where(AgentRun.status == "completed")     # only filter — no company
  .order_by(Report.created_at.desc()).limit(1)
```

So `from-company/{CFR}` could return an Apple analysis. Root cause: **no DB link
existed from a `Report` back to a `company_id`** (`reports`/`agent_runs` have no
`company_id`; the `Scorecard`→report bridge is effectively never populated for
analysis reports; `CompanyFinancialSnapshot` is a dead unwired table;
`DiscoveryCandidate` links only via collision-prone ticker+exchange and only on
the discovery route). The query therefore fell back to a global pick.

## Fix

- **Migration 012** — nullable `reports.company_id` UUID FK → `companies.id`
  (`ON DELETE SET NULL`) + index `ix_reports_company_id`. Additive, reversible,
  **no backfill**.
- The company-analysis workflow **writes `company_id`** at draft-report creation
  (`_run_holder["company"].id`, defensive fallback to `state["company_id"]`).
- `generate_from_company` now selects the company's own report deterministically
  and **fails clearly** when none exists:

```python
select(Report).join(AgentRun, Report.created_by_agent_run_id == AgentRun.id)
  .where(Report.company_id == company_id, AgentRun.status == "completed")
  .order_by(Report.created_at.desc(), Report.id.desc()).limit(1)
# none -> raise ValueError -> 404 (NO cross-company fallback)
```

`generate_from_report` / `from-scorecard` / `from-candidate` /
`from-workflow-state` untouched. Auth / safety-gate / publication
(`publication_ready=false`, `human_review_required=true`) / provenance
(`is_mock`, `data_provenance`) invariants preserved. Source-report id + AgentRun
lineage flow through `workflow_status` (Slice-1 mechanism).

## Files changed (PR #74)

Migration `apps/api/alembic/versions/012_add_report_company_id.py`; code
`app/models/report.py`, `app/schemas/report.py`, `app/services/report_service.py`,
`app/workflows/company_analysis.py`, `app/services/final_report_generator.py`,
`app/api/v1/final_reports.py`, `pyproject.toml` (test-only `aiosqlite`); tests
`tests/test_hotfix_from_company_scoping.py` (new) + 3 mock-fixture updates; docs
`docs/API.md`, `docs/DATABASE.md`, `docs/development/PHASE_LEDGER.md`,
`docs/development/PHASE_32A_HOTFIX_FROM_COMPANY_STAGING_VALIDATION.md`.

## Pre-merge gates

- New real-async-SQLite suite `test_hotfix_from_company_scoping.py`: **13/13**.
- Backend suite: **2359 passed / 12 skipped / 0 failed**; ruff clean; mypy **242 = baseline (0 new)**.
- Security scan: **PASS**. PR review: **GO**. CI (Lint & Test): **pass**.

## Migration execution (staging)

Applied manually via local Alembic through a temporary /32 firewall rule (removed
afterward; firewall restored to baseline `AllowAzureServices`). No DB secret was
printed/persisted. Verified: pre-state head `011` → `012`; `reports.company_id`
(uuid); index `ix_reports_company_id`; FK `fk_reports_company_id_companies` →
`companies`, **ON DELETE SET NULL**. Additive column, invisible to the previously
deployed `3237d27` code (no downtime — migration applied before merge/deploy).

## Deployment

- API deploy run **30802241000** (success) → `/health` commit_sha `cd0eb87`, env=staging (3 stable).
- Web deploy run **30802320097** (manual dispatch, success) → `/api/version` commit_sha `cd0eb87` (3 stable).
- `AUTH_TEST_MODE` absent. No app-setting changes. No new flags (this hotfix adds none).

## Staging validation (A–I) — VALIDATED

Fresh analyses (`free_real`, `is_mock=false`):

| | company_id | ticker/exch | source draft_report_id | source agent_run_id |
|---|---|---|---|---|
| AAPL#1 | `5d36744d-dc76-42c3-83c7-01dec94f3bd1` | AAPL/US | `2733cf87-46dc-4457-b80a-d829c1fa16e2` | `6a1c5924-af9b-4a78-9fd3-75a187c00afd` |
| CFR#1 | `041cc7e4-0802-4505-9e25-63898f04d12a` | CFR/SW | `2984ddec-6521-4937-ba1d-53e84f916b8b` | `e8ab50ce-2302-42c0-bde2-f701bc6031fd` |
| AAPL#2 | `5d36744d-…` | AAPL/US | `fd023aa0-d275-4e2a-bb09-3ad87e4ffc41` | `1e637ea4-eb8a-49a7-bc0f-4926fbb2b32b` |

- **Order A** (CFR globally newest): `from-company(AAPL)` → final `14ddf26c…`, selected source `2733cf87` (AAPL#1, **not** CFR#1); `from-company(CFR)` → final `5c873e89…`, selected `2984ddec` (CFR#1). ✅
- **Order B** (AAPL globally newest): `from-company(CFR)` → final `1519e358…`, selected `2984ddec` (**still** CFR#1, not globally-newest AAPL#2); `from-company(AAPL)` → final `5bd8d905…`, selected `fd023aa0` (AAPL#2 = newest-of-multiple). ✅
- **Pre-012 NULL not selected:** `from-company` on prior-slice `AAPL.US` (`855ad312…`) and on both targets pre-analysis → **404**. ✅
- **404 / regression:** unknown uuid → 404 ("Company … not found"); new no-report company `6a003eb7…` → 404 ("No eligible completed analysis report"), **0 fabricated rows**; `from-report(2733cf87)` → selected exactly `2733cf87` (unchanged despite AAPL#2 newest). ✅
- **Invariants (every from-company success):** `schema_valid=true`, `safety_valid=true`, `human_review_required=true`, `publication_ready=false`, `data_provenance=real`, `is_mock=false`, forbidden_terms=[] (no BUY/SELL/HOLD/WATCH / price target / fair value). ✅
- **Auth:** unauth `from-company`/`from-report`/`run`/`reports`/`companies` → **401**; `/health` → 200. ✅
- **Secret scan:** responses **CLEAN**; app logs **CLEAN** (0 hits for `api_token=`, `AUTH_SECRET`, `AZURE_OPENAI_API_KEY`, `EODHD_API_KEY`, `postgresql://`, `Bearer ey`, `sk-`; basic-auth credential 0 occurrences). ✅

**Verdict:** cross-company selection deterministic and correct in **both** orders; no cross-company fallback anywhere; all 404s held with zero fabricated reports; `from-report` unchanged; safety/publication/provenance/auth intact; secret-clean.

## Notes / limitations

- Reports created before migration 012 keep `company_id = NULL` and are
  intentionally **not** reachable via `from-company` (404), by design — safer than
  returning the wrong company. Documented in `docs/API.md` (operator note) and the
  migration docstring.
- The final-report generator's own council ran during from-company generation
  (`llm_used=true`) and completed cleanly; the documented Slice-4 valuation_guard
  Azure-TPM limitation did not surface. Orthogonal to this selection fix.
- No worktree was used (dedicated branch only). Feature branch deleted post-closure.

**Slices 3–5 remain — do NOT auto-start.**
