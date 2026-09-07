# V3 Release / Dark-Deployment Readiness Report

**Date:** 2026-09-07 · **Branch under review:** `develop/v3` @ `fe6f804`
**Deployed today:** `main` @ `4b60e07`, database at alembic **018**

---

## Recommendation

# READY FOR MAIN MERGE + DARK DEPLOYMENT

**Conditional on one ordering change to the procedure as briefed.** The brief's sequence
was *main merge → dark deployment → migrations*. **That order causes a product-wide
outage** and must be inverted: **migrations first, then merge and deploy.**

This is not an opinion. It was measured on a real PostgreSQL 16.15 (§3.3):

| Schema | V2 code | V3 code |
|---|---|---|
| **018** — what is deployed today | ✅ OK | ❌ **FAIL** `UndefinedColumn: companies.legal_entity_id` |
| **038** — after the V3 migrations | ✅ OK | ✅ OK |

`deploy-api-staging.yml` does **not** run migrations (it says so at line 8). So merging
first would put V3 code in front of an 018 schema, and `companies.legal_entity_id` — the
single nullable column migration 027 adds — is declared on the V3 ORM model and therefore
named in every `SELECT` it emits. Six API modules read `Company`
(`companies`, `discovery`, `company_research`, `sources`, `financial_data`, `scoring`),
so the failure is product-wide, not marginal.

The inverse order is safe and is proven in the same table: **V2 code runs unchanged
against a 038 schema**, because SQLAlchemy emits an explicit column list and V2's model
does not declare the new column. That is what makes migrate-first a no-downtime step.

---

## 1. Git state and protected refs — VERIFIED

| Ref | SHA |
|---|---|
| `develop/v3` | **`fe6f804`** (185 commits unpushed) |
| `main` | `4b60e07` |
| `release/v2-current` | `4b60e07` |
| `v2-final-pre-v3-2026-09-04^{commit}` | `4b60e07` |

`git rev-list --count develop/v3..main` = **0**. Working tree clean. Deployed API reports
`commit_sha 4b60e07`, built 2026-09-02 — nothing has been deployed.

## 2. Repository gates — PASS

| Gate | Result |
|---|---|
| `ruff check .` | All checks passed |
| `mypy app` | **71** errors in 10 files — the documented baseline, unchanged |
| `pytest tests/ -q` | **6,126 passed**, 39 skipped (`ENABLE_INTEGRATION_TESTS` unset) |
| web `typecheck` / `lint` / `build` | all pass |
| Python 3.12 compile (CI's version) | `app/` and `tests/` both compile clean |

**CI has never run on a V3 commit.** `api-ci.yml` triggers only on push/PR to `main`, so
pushing `develop/v3` runs nothing. The first CI execution will be the promotion PR —
219 changed Python files, on **Python 3.12**, while every local gate ran on 3.14. The
compile check above is syntax only; the PR gate is the real one, and the procedure below
waits for it.

## 3. Migration-chain acceptance — PASS, on real PostgreSQL 16.15

Local Docker was unusable (its VM disk carries EXT4 I/O errors from an earlier disk-full
event; repairing it means a factory reset that destroys the user's containers, which was
not done). PostgreSQL 16.15 was installed via Homebrew and a scratch server run on port
5433. `scripts/v3-migration-acceptance.py` is new and repeatable.

### 3.1 Forward, backward, and identical

```
1. at 018 (V2 baseline): 22 tables, 392 columns
2. at head (038):        60 tables, 948 columns
3. V2-on-V3: 0 V2 columns lost or changed; 1 added to a V2 table
             companies.legal_entity_id : uuid : NULL
4. back at 018:          22 tables, 392 columns
   fingerprint identical to the pre-V3 schema — rollback is real
```

The fingerprint is `table.column:type:nullability` over `information_schema`, so a
type or nullability change would be caught, not only a dropped column.

### 3.2 Additive-only, proven structurally as well as empirically

Across all **20** V3 migrations (019–038), **zero** calls to `drop_column`, `drop_table`,
`alter_column` or `rename_table` in any `upgrade()`. Every one defines `downgrade()`.
The alembic history is **linear with a single head** — no branch points.

### 3.3 Deployment-order compatibility — THE FINDING

The table at the top of this report. Recorded here because it is the one result that
changes the procedure, and it was only obtainable by running both codebases against both
schemas.

## 4. V3 feature flags default OFF — VERIFIED

**15 of 16** boolean `V3_*` settings default `False`. The exception is
`V3_JOB_WORKER_IN_PROCESS = True`, which is **not** a feature flag: it is a sub-setting
read only inside `if settings.v3_durable_jobs_enabled and settings.v3_job_worker_in_process`
(`app/main.py:131`). With durable jobs off — the default — no worker starts.

`V3_MONITORING_ENABLED` defaults `False` and nothing schedules monitoring (§9 of the
brief is satisfied by the default; no action was taken).

## 5. A credential alone cannot activate DeepSeek — VERIFIED

| Credential | model flag | search flag | Routing | Research provider | External tools |
|---|---|---|---|---|---|
| ✅ | ❌ | ❌ | all Azure OpenAI | none | none |
| ✅ | ✅ | ❌ | + deepseek | none | none |
| ✅ | ❌ | ✅ | all Azure OpenAI | DeepSeek | registered |
| ❌ | ✅ | ✅ | all Azure OpenAI | none | registered, degrade honestly |

Each leg requires its own flag, and a flag without a credential yields a provider that
declines rather than one that half-works.

## 6. Startup tolerates V3 disabled — VERIFIED

The application starts with every `V3_*` and `DEEPSEEK_*` variable removed from the
environment and serves `/health` 200. It also tolerated a **missing database**: the
startup interruption sweep raised `OperationalError`, which was caught and logged rather
than aborting boot.

**The API surface is unchanged.** Exactly one file under `app/api/` differs from `main`
(`company_research.py`); no router was added and no `include_router` line changed. The
OpenAPI document contains **68 paths and no V3-specific path**. V3 runs behind the
existing company-research endpoint via `v3_pipeline_enabled`, and `durable_enabled()`
returns `False` under production defaults.

**The web application does change** — 5 files — and will redeploy. Its new behaviour is
**data-driven, not flag-driven**, and inert on existing data:
`readServerVerification()` returns `present: false` for any report without a
`numeric_verification` section, which is all 1,057 stored reports; the new
`dead_letter` / `cancelled` job statuses only ever arrive from the durable path, which is
off. Reader-facing output is therefore unchanged until V3 produces V3 data.

## 7. Production secret / config contract

**Live today:** 47 app settings on `ib-stg-api`, of which **zero** are `V3_*`. Every V3
setting therefore takes its code default, which is the safe one.

**Required for DARK deployment: nothing.** No new secret, no new app setting, no new
service. That is the point of the dark deployment.

**Required later, for controlled activation** — in the order a rollout would need them:

| Stage | Setting | Why | Secret? |
|---|---|---|---|
| Durable jobs | `V3_DURABLE_JOBS_ENABLED=true` | job rows survive a recycle | no |
| Corpus | `V3_CORPUS_ENABLED=true`, `V3_SEARCH_BACKEND=postgres` | default `memory` is not durable retrieval | no |
| Artifacts | `V3_ARTIFACT_STORE_BACKEND=azure_blob`, `V3_ARTIFACT_STORE_ACCOUNT_URL=<existing storage account>` | raw bytes retained; **existing** storage account, no new service | no (managed identity / existing key) |
| Entity master, tools, pipeline | `V3_ENTITY_MASTER_ENABLED`, `V3_AGENT_TOOLS_ENABLED`, `V3_PIPELINE_ENABLED` | the research path itself | no |
| **Run budgets** | `V3_RUN_MAX_MODEL_CALLS`, `_MODEL_TOKENS`, `_WEB_SEARCHES`, `_WALL_SECONDS`, `_EXTERNAL_COST_USD` | **all default to `0` = UNBOUNDED** | no |
| Prices | `V3_PRICE_USD_PER_MILLION_INPUT_TOKENS`, `..._OUTPUT_TOKENS`, `V3_PRICE_SOURCE` | otherwise cost reports `None` | no |
| DeepSeek (optional) | `DEEPSEEK_API_KEY` | **the only new SECRET in the whole set** | **yes → Key Vault** |
| DeepSeek legs | `V3_DEEPSEEK_MODEL_ENABLED`, `V3_DEEPSEEK_SEARCH_ENABLED` | separate consent per leg | no |

⚠️ **Two activation preconditions, not dark-deployment blockers:**

- The run budgets above default to unbounded **and** `ResearchBudget.check()` is never
  called anywhere in the V3 pipeline. Setting the values today would not bound anything.
  Enforcement must be wired before the external legs are enabled.
- **OPEN DECISION #23** — V3 has no deterministic forbidden-language backstop
  (`safety_terms` is used by V2's report generator and by nothing in V3). It gates broad
  reader-facing activation. It does **not** gate dark deployment, because V3 human-facing
  output stays disabled: `V3_PIPELINE_ENABLED=false` means no V3 report content is
  produced, and the web changes are inert without it.

## 8. Rollback remains valid — VERIFIED

- `v2-final-pre-v3-2026-09-04` is an **annotated** tag at `4b60e07`; `release/v2-current`
  still equals it exactly.
- V2 code runs against a **038** schema (§3.3, measured). So the primary rollback is
  *redeploy `release/v2-current`* — **no down-migration required**.
- If a down-migration is nonetheless wanted, `alembic downgrade 018` runs clean and
  returns a byte-identical schema fingerprint (§3.1).

## 9 / 10 — Monitoring not enabled; nothing provisioned

`V3_MONITORING_ENABLED` left at its `False` default and nothing schedules it. No paid
service was provisioned. Homebrew PostgreSQL 16 was installed **locally** for this
acceptance only; it is not part of any deployment.

---

## The promotion and deployment procedure

**Ordering is load-bearing. Step 4 must precede step 5.**

### Phase A — publish and prove (no production change)

1. **Push the branch.**
   `git push -u origin develop/v3` — 185 commits. No CI fires; nothing deploys.
2. **Open the promotion PR** `develop/v3 → main`. This is the **first CI run on any V3
   commit**: `api-ci` (ruff + pytest on Python 3.12) and `web-ci`.
3. **Wait for CI green.** Do not merge on a red or skipped check. If CI fails on 3.12
   where local passed on 3.14, that is a real finding — fix on the branch and re-push.

### Phase B — migrate the live database FIRST (V2 still serving)

4. **Apply migrations 019 → 038 to the live database**, with `main`/V2 code still
   deployed, following `reference_staging_migration_runbook.md`:
   - temporary `/32` firewall rule on `ib-stg-psql` for the current IP;
   - `DATABASE_URL` read from the app setting, converted to the `psycopg` scheme;
   - `alembic upgrade head`;
   - verify `alembic current` reports **038**;
   - **remove the firewall rule in a separate call.**

   Safe because V2 code runs unchanged on a 038 schema (§3.3) and every V3 migration is
   additive-only (§3.2). Expect 22 → 60 tables and one new nullable column on `companies`.

5. **Smoke-test V2 on the migrated database, before any code change.**
   `/health` 200 with `commit_sha 4b60e07`; `GET /api/v1/companies` and one existing
   report render. **This is the checkpoint that proves migrate-first was safe** — if it
   fails, `alembic downgrade 018` restores the byte-identical prior schema and nothing
   has been deployed.

### Phase C — merge and dark-deploy

6. **Merge the PR to `main`.** This triggers `deploy-api-staging.yml` and
   `deploy-web-staging.yml`.
7. **Wait for the deploy smoke check**, which compares `/health`'s `commit_sha` against
   the workflow SHA — it detects Azure serving the old container. Allow ~15 minutes for
   the build id to settle before doing anything else.
8. **Confirm the dark state.** `/health` reports the new SHA; `GET /api/v1/companies`,
   `/api/v1/reports` and an existing report all behave exactly as before; the OpenAPI
   document still shows no V3 path; no `V3_*` app setting exists.

**End of dark deployment.** V3 code is live and every V3 feature is off.

### Phase D — controlled activation (separate decision, separate session)

9. Enable in this order, one setting at a time, verifying `/health` and a V2 smoke after
   each: durable jobs → corpus + `V3_SEARCH_BACKEND=postgres` → artifact store → entity
   master → agent tools → **run budgets and their enforcement** → `V3_PIPELINE_ENABLED`.
10. **Before enabling any external leg**, resolve the two preconditions in §7: budget
    enforcement, and OPEN DECISION #23 if output is to reach a reader.
11. **Production acceptance on MRNA, CFR and ASML**, one at a time — never concurrently:
    B1 with a single gunicorn worker has been overloaded by 5 concurrent analyses before,
    and the campaign's own note is to run in batches of two at most. Capture for each: the
    chair label, citations resolving, period/scope integrity, and consumption.

### Rollback at any point

- **Before step 6:** nothing is deployed. `alembic downgrade 018` if step 5 fails.
- **After step 6:** redeploy `release/v2-current` — **no down-migration needed**, since
  V2 runs on the 038 schema. This is the fast path.
- **Full reversal:** redeploy `release/v2-current`, then `alembic downgrade 018`, which
  restores a fingerprint-identical schema.

---

## What this report does not claim

- **CI has never run on this code.** Step 3 is the first time, and it runs a Python
  version no local gate used.
- **No V3 feature has run in production.** Dark deployment means exactly that; every
  acceptance in this campaign was local, against scratch databases and real public
  documents.
- **The external research path promotes evidence on roughly half of runs**, entirely
  depending on which URL the provider cites. That is measured (V3.12) and is a property
  of activation, not of this deployment.
