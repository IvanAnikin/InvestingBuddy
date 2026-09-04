# InvestingBuddy V3 — Migration and Compatibility Plan

**Status:** V3 TARGET. Baseline `4b60e07`, Alembic head **018**.

The governing constraint: **`main` is the currently approved, deployed product,
and it must keep working exactly as it does today throughout V3 development.**

---

## 1. Branch and deployment rules

| Rule | Enforcement |
|---|---|
| `main` receives no V3 commits. | V3 work happens only on `feature/v3-*` → `develop/v3`. |
| V3 is never deployed without explicit user approval. | `deploy-api-staging.yml` and `deploy-web-staging.yml` trigger on `push: branches: [main]` only — pushing `develop/v3` cannot deploy. Verified 2026-09-04. |
| V3 migrations are never run against the live environment before deployment approval. | Migrations are created on the V3 branch and applied only to local/ephemeral databases. The staging migration runbook is not invoked for V3. |
| V2 stays immediately recoverable. | `release/v2-current` and tag `v2-final-pre-v3-2026-09-04`, both pinned to `4b60e07` and pushed to origin. |
| The V2 preservation branch and tag are never deleted or overwritten. | — |

**CI note:** `api-ci.yml` and `web-ci.yml` trigger on `push`/`pull_request` to
`main` with `apps/**` path filters. A PR targeting `develop/v3` therefore gets
**no automatic checks**. Until that is addressed, every V3 slice runs its gates
locally and records the exact commands and results in the PR body. Adding
`develop/v3` to the CI branch lists is `OPEN DECISION`
[#17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch).

---

## 2. Schema migration discipline

**No single giant V3 migration.** One focused, reversible migration per slice that
needs one. Current head is 018; V3 continues the same sequential numbering
(019, 020, …) on `develop/v3`.

Every migration documents:

- exact purpose (one sentence);
- `upgrade()` and a real `downgrade()` — not `pass`;
- indexes created, and why each one exists;
- FK semantics (`ondelete` chosen deliberately: research history is preserved with
  `SET NULL`, never `CASCADE`d away — CLAUDE.md rule 15);
- tests that exercise the new shape;
- the new head recorded in `docs/DATABASE.md`.

### 2.1 Additive-only rule for V3.0-V3.2

Until a V3 replacement is validated in live use, migrations may:

- add tables;
- add **nullable** columns;
- add indexes.

They may **not**:

- drop or rename an existing column or table;
- tighten an existing column to `NOT NULL`;
- change an existing FK's `ondelete`;
- alter the meaning of an existing value.

This is what guarantees that `release/v2-current` code can run against a database
that has had V3 migrations applied — which is the property that makes rollback
real rather than theoretical.

### 2.2 Backfill before enforcement

Three-step pattern for anything that must eventually be required:

1. **Add** the nullable column/table and dual-write.
2. **Backfill** historical rows in a separate, resumable, idempotent migration or
   script.
3. **Enforce** (`NOT NULL`, unique constraint) in a third migration, only after
   the backfill is verified complete.

Doing 1-3 in one migration is how a deploy fails at 3 a.m. on a table that took
longer to backfill than the lock timeout allowed.

---

## 3. Data compatibility guarantees

| Guarantee | How |
|---|---|
| **Existing reports remain readable.** | `reports` is not restructured. V3 adds references *to* reports, never inside them. The legacy read path stays: 126 of the newest 200 reports are legacy-shaped and must keep rendering. |
| **Existing company IDs remain resolvable.** | `companies.id` remains the FK target for `reports.company_id`, `extracted_documents.company_id` and the rest. The entity master hangs *beside* `companies` via a nullable `legal_entity_id`, it does not replace the table. |
| **Existing citations remain valid.** | `citations` and `sources` are untouched in V3.0-V3.2. Stable evidence identifiers (V3.1.6) are added alongside run-local `E1`/`E2` handles; the old handles keep resolving for old reports. |
| **Current auth is unchanged.** | No auth work in V3 unless separately approved. `APP_ENV=staging` remains load-bearing (it is the only gate on API Basic Auth) and must not be relabelled. |
| **Current deployment is unchanged.** | No infra changes reach `main`. A V3 worker app is provisioned only at deployment-approval time. |
| **Extraction semantics are unchanged.** | `CURRENT_EXTRACTION_PIPELINE_VERSION` (15) advances only when parser/validator semantics genuinely change, exactly as today. V3 corpus work must not silently invalidate the persisted fact cache. |

---

## 4. Compatibility adapters

Where V3 introduces a new canonical shape, a thin adapter maps the old one
forward rather than migrating callers en masse:

| Old | New | Adapter |
|---|---|---|
| `(ticker, exchange)` | `LegalEntity` + `SecurityListing` | `resolve_entity(ticker, exchange) -> LegalEntity` — falls back to the `companies` row when no entity exists yet. |
| `ExtractedDocument` + `excerpts_json` | `ResearchDocumentVersion` + pages/chunks | Corpus reads fall back to `excerpts_json` for documents not yet re-ingested. |
| Run-local `E1`/`E2` | Stable evidence ids | Citation rendering accepts both; new runs emit stable ids. |
| `research_job` envelope in `AgentRun`/`AgentStep` | `research_jobs` row | The V3 job store exposes the same status vocabulary, so the polling API contract does not change. |

**Adapters are temporary and dated.** Each one records the slice that will remove
it. An undated adapter becomes permanent architecture by accident.

---

## 5. Rollback

| Level | Action |
|---|---|
| One slice | `git revert` the merge commit on `develop/v3`; if it carried a migration, run its `downgrade()`. |
| The whole V3 line | `develop/v3` is deleted or abandoned. `main` is untouched, so there is nothing to roll back. |
| A deployed V3 (post-approval only) | Redeploy from `release/v2-current`. Because V3 migrations are additive-only through V3.2, the V2 code runs unchanged against the migrated database. Beyond V3.2, each destructive migration must ship with an explicit, tested rollback plan or it does not merge. |

Recovery of the V2 baseline at any moment:

```bash
git checkout release/v2-current     # or: git checkout v2-final-pre-v3-2026-09-04
```

---

## 6. Deprecation, not deletion

Candidates for eventual replacement — none deleted until a validated V3
replacement exists **and has been live-verified**:

hard-coded discovery universe · process-local `BackgroundTasks` for multi-minute
jobs · frozen evidence-pack-only council · industry-agnostic methodology ·
reference-only macro sources · absence of corpus search · frontend-only numeric
reconciliation · run-local positional evidence identity · fresh-run-from-zero
behaviour.

The repository's own history is the argument for this rule: several correctives
were needed precisely because a replacement was assumed to work before it was
verified on live data. The pattern that keeps recurring — a rebuild skipped by an
unexempted safety scan, a truncated extraction stamped current and pinning the
cache against its own fix — is what "do not delete the old path yet" prevents.
