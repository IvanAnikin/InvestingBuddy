# InvestingBuddy V3 — Release Candidate Report

**Status: `IMPLEMENTED`. Awaiting user acceptance. Nothing is deployed and nothing is
merged to `main`.**

**Branch:** `develop/v3` · **Date:** 2026-09-06 · **Baseline:** `4b60e07`
(tag `v2-final-pre-v3-2026-09-04`)

This document is the campaign's own account of what was built, what was proved, and —
at least as importantly — what was **not**. It is written to be argued with: every claim
below is either a command whose output is recorded, or a statement that something was not
done and why.

---

## 1. The decision this asks for

V3 is complete as an implementation and **not validated as a product**, because
validation needs live data and V3 is not deployed. The decision in front of you is
therefore *not* "is this correct in production" — nobody can answer that yet. It is:

> **Is this worth deploying to a staging environment and running against real issuers?**

Everything that could be proved without a deployment has been. What that leaves is
recorded in §7.

---

## 2. Protected baseline — verified, not asserted

| Ref | SHA | How it was checked |
|---|---|---|
| `main` | `4b60e07` | `git rev-parse main` |
| `release/v2-current` | `4b60e07` | `git rev-parse release/v2-current` |
| `v2-final-pre-v3-2026-09-04` | dereferences to `4b60e07` | `git rev-parse …^{commit}` |

```
git rev-list --count main..develop/v3   = 154
git rev-list --count develop/v3..main   = 0
```

**No V3 commit is on `main`.** All three refs resolve to the same commit they did at
campaign start. Nothing was deployed, no V3 migration reached the deployed database, and
no V3 flag was enabled anywhere.

**The local development database is still at migration 018**, re-checked after every one
of the twenty V3 migrations.

---

## 3. What was built

154 commits · 274 files · 20 migrations · 44 slice documents · 44 test files.

| Phase | Goal | Status |
|---|---|---|
| V3.0 | Durable execution and correctness | `IMPLEMENTED` |
| V3.1 | Research Corpus | `IMPLEMENTED`, with a real-document acceptance run |
| V3.2 | Entity master and universe | `IMPLEMENTED` |
| V3.3 | Research tools and calculations | `IMPLEMENTED` |
| V3.4 | Multi-provider runtime and sources | `IMPLEMENTED` (4.2/4.6 `DEFERRED` by user decision) |
| V3.5 | Research Ledger, Director, bounded loop | `IMPLEMENTED` |
| V3.6 | Industry playbooks | `IMPLEMENTED` |
| V3.7 | Council V2 and Red Team | `IMPLEMENTED` |
| V3.8 | Research memory and delta | `IMPLEMENTED` |
| V3.9 | Monitoring | `IMPLEMENTED`, feature-gated, **nothing schedules it** |

The five things V3 set out to change about V2, and where each landed:

1. **Execution survives a restart** — V3.0. A leased worker, bounded attempts, a
   dead letter, cancellation at a task boundary.
2. **A document read once is searchable forever** — V3.1 + V3.4.9. Full parsed text,
   document-aware chunks, and a PostgreSQL backend that outlives the process.
3. **An agent can pull evidence mid-run** — V3.3. Nineteen closed tool names, ten
   implemented, every call audited, every budget checked before the spend.
4. **A gap becomes a follow-up task** — V3.5. The one structural limitation V3 existed
   to remove: V2's council can report a gap beautifully and cannot close one.
5. **The cheapest adequate model does bulk work** — V3.4. Four provider interfaces,
   DeepSeek primary, Azure OpenAI for synthesis, and a benchmark that refuses to invent
   the numbers it cannot measure.

---

## 4. What was demonstrated

Each phase's acceptance criteria are in
[ACCEPTANCE_AND_TEST_STRATEGY.md §3](ACCEPTANCE_AND_TEST_STRATEGY.md#3-what-each-v3-phase-must-prove)
with the evidence beside them. The cross-phase runs performed for this report:

### 4.1 The full migration chain is reversible, on real PostgreSQL 16

```
fresh database -> upgrade 018            23 tables
                  upgrade 018 -> head    20 migrations applied, 61 tables, head 038
                  downgrade head -> 018  20 downgrades applied, 23 tables
```

**The downgraded schema is byte-identical to the pre-V3 one.** Compared as a sorted
`table.column` fingerprint across all 23 tables: identical.

### 4.2 V2 can run against a database with every V3 migration applied

This is the migration plan's central claim, and it is now measured rather than argued:

| Check | Result |
|---|---|
| V2 columns **removed or renamed** by V3 | **0** |
| Columns added to **existing V2 tables** | **1** — `companies.legal_entity_id` |
| That column is nullable | Yes |
| A V2-shaped `INSERT INTO companies` against the fully migrated schema | Succeeds |

Everything else V3 added is a new table. That is what makes rollback real rather than
theoretical: `release/v2-current` code does not know these tables exist, and does not
need to.

### 4.3 ORM/DDL drift across all 38 V3 tables

`DRIFT: none` — columns, NOT NULL, CHECK constraints, indexes and unique constraints, on
the fully migrated schema.

This check found one real defect, fixed in this branch: **`research_run_consumption`
(migration 020) was never imported by `models/__init__.py`**, so its model never
registered with `Base.metadata`. Nothing was broken today — the module works when imported
directly, and two call sites do — but an unregistered model is what Alembic autogenerate
proposes to `DROP`. This is the campaign's own recorded gotcha ("`models/__init__.py` is
not exhaustive") catching a real instance of itself.

### 4.4 The corpus still works, on a real 169-page annual report

Re-ran `scripts/v3-corpus-acceptance.py` against the **live** Pandora Annual Report 2025
(25.9 MB, 169 pages) through the repository's own guarded, allowlisted, DNS-pinned
fetcher — after twenty more slices than when V3.1 first recorded it:

```
live profile      40 of 169 pages, 108,000 chars   (vs 19,232 as 20 bounded excerpts)
deep reprocess    40 -> 169 of 169 pages, 113 -> 602 chunks, 30.1s
                  superseded derivation KEPT, not deleted
                  the citation recorded BEFORE reprocessing still resolves: True
'cash flow from operations' -> p. 138, STATEMENTS > INVESTED CAPITAL (2025)
'gross margin'             -> table p109:t1, the segment note grid
                              Revenue | 24,235 | 8,314 | 32,549
bytes recovered by hash alone: True; re-parse deterministic: pages/sections/text all True
```

Identical to V3.1's recorded result. **Nothing in V3.2 through V3.9 regressed the
corpus**, and the property that makes reprocessing possible without a re-fetch still
holds.

### 4.5 Gates

```
ruff check .          All checks passed
pytest tests/ -q      5,870 passed, 12 skipped     (4,949 at campaign start, +921)
mypy app              71 errors in 10 files        (baseline, unchanged)
web typecheck/lint/build   All passed
```

`mypy` regressed to 72 on **six** occasions during the campaign. The gate caught every
one and each was a real type error.

### 4.6 Every feature flag defaults off

56 V3 settings. The only boolean defaulting `True` is `v3_job_worker_in_process`, which is
consulted **only** when `v3_durable_jobs_enabled` is on — and that is `False`.
`V3_SEARCH_BACKEND` defaults to `memory`; `v3_artifact_store_backend` to `"none"`.

**With no configuration change, a deployment of this branch behaves exactly as `main`
does.**

---

## 5. The defects a review pass caught that the build pass did not

Every V3 phase gate records these, and the pattern held to the end: **working, green code
that was quietly wrong about a default, a bound, or a measurement.** The ones worth
carrying into any future work:

| Slice | Defect |
|---|---|
| 3.2 | A **row limit applied before a scope filter** — a company with 200 Group facts and 5 segment facts, queried for segments with `limit=100`, returned **zero**. |
| 3.4 | A **fabricated zero**, written by the author of the helper that exists to prevent it: a declared-instrumented unit that the handler never reported, stored as `0`. |
| 4.4 | `parse_number` scraped digits and read **`"Q1 2026"` as `12026`** — a number no document contains, offered to a comparison that would then have "found" it. |
| 4.4 | A claim absent from a document the platform only **partly read** was being *rejected*. The reader stops at 40 PDF pages; blaming a provider for page 100 moves `verification_survival_rate` in the direction that **looks like diligence**. |
| 4.9 | `to_tsvector(varchar, text)` does not exist — and fixing it is also what made the query expression byte-identical to the indexed one, i.e. the difference between a bitmap index scan and a sequential scan of the corpus. |
| 4.11 | **An accepted ADR had never been implemented.** Every research budget still defaulted to unbounded while the campaign record asserted the ceilings were real numbers. |
| 6.2 | The Director assigned questions on the strength of tools a role **declares** and nothing **implements**. |
| 8.2 | A CHECK and a `SET NULL` cascade **contradicted each other**: deleting both runs was refused, making the very pruning the cascade exists for impossible. |

Two guards were **turned around rather than deleted** when the decisions they protected
were taken: 4.1's "no live adapter" filename pin (which 4.3 showed was watching the wrong
directory) and 1.4's "the backend decision is not taken here" (which the user answered).

---

## 6. Decisions taken during the campaign

Eleven user decisions on 2026-09-05, recorded as **ADR-047 … ADR-052**. Two agent-owned
technical decisions, both reversible and both with evidence:

- **[ADR-053](../DECISIONS.md)** — `pgvector` is **not installable** on the PostgreSQL
  this project runs. Checked, not assumed:
  `SELECT count(*) FROM pg_available_extensions WHERE name='vector'` returns `0`. The
  lexical leg is the production path and needs no extension; the semantic leg ships
  portable, feature-gated, and honest about being a **bounded rerank** rather than a
  nearest-neighbour search.
- **[ADR-054](../DECISIONS.md)** — the architecture's rule for combining playbooks said
  "questions union; completion_rules intersect (the strictest wins)", and those two
  clauses point opposite ways. Resolved in favour of the stated *intent*: every rule of
  every applicable playbook must hold, so a conglomerate is **harder** to complete than
  either of its parts.

---

## 7. What is NOT proved — read this before accepting

### 7.1 Nothing has run against a live issuer end to end

The corpus has (§4.4). **The research loop has not.** There is no investigator
implementation that calls a model and a tool; `Investigator`, `RedTeam`, `Responder` and
`PlanRefiner` are Protocols with fakes. That is deliberate — wiring one is a behaviour
change on a live path — but it means the following are proved as *contracts* and not as
*behaviour*:

- that a bounded loop terminates on a named limit **when driven by a real specialist**;
- that a Red Team challenge produces anything useful when the challenger is a model;
- that a Director's plan is a *good* plan.

### 7.2 No live provider call has been made

No credential is configured. The benchmark harness ran and printed exactly what it should
in that state: every provider `not_approved` or `not_configured`, **no cost and no
winner**. `cost_per_verified_finding` is therefore an implemented metric with **no
measurement behind it**.

### 7.3 DeepSeek's `web_search` wire contract is still unverified

Modelled as an OpenAI-compatible tool call. The mapping is isolated, the tool name is
configuration, `V3_DEEPSEEK_SEARCH_ENABLED` defaults **off**, and the parser returns no
candidates with a warning naming what it saw rather than guessing. Confirming it is one
request body, one parser and one opt-in contract test — and **nothing else in the DeepSeek
path depends on it.**

### 7.4 Nothing is wired to the product's front door

The V3 tool surface, ledger, Director, loop, Council V2, Red Team, memory, delta and
monitoring are all reachable only from tests. `company_research_service` still runs the V2
path. Connecting them is the work that turns this from an implementation into a product,
and each connection is a behaviour change deserving its own slice and its own acceptance.

### 7.5 The regression set is incomplete

PNDORA, CFR, MRNA and ASML are represented in fixtures and in the benchmark task list. **A
bank and a defence issuer are not** — both playbooks ship without a live company that
exercises them, which the acceptance strategy already flagged as *(later)*.

### 7.6 Open decisions that remain the user's

| # | Decision | Consequence of leaving it open |
|---|---|---|
| [10](OPEN_DECISIONS.md#10-openfigi-usage-and-licensing) | OpenFIGI usage | FIGI is representable and not obtainable. ISIN covers the regression set. |
| [12](OPEN_DECISIONS.md#12-raw-page-and-document-retention) | Retention TTL | `V3_ARTIFACT_RETENTION_DAYS` is `0` = "no TTL configured"; the sweep is dry-run and scheduled by nothing. |
| [15](OPEN_DECISIONS.md#15-monitoring-cadence) | Monitoring cadence | V3.9 ships the mechanism with **nothing scheduling it**, enforced by two tests. |
| [17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch) | CI on `develop/v3` | Every slice ran `scripts/v3-gates.sh` by hand and recorded the output. |
| [18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx) | Data-source inventory files | Left untracked and untouched. |

### 7.7 Known limitations carried forward

- **`pytest tests/` is not offline on a developer machine.**
  `test_phase7_azure_openai_real.py` makes live Azure OpenAI calls when the local `.env`
  selects that provider. A failure there is a network or quota event until the file has
  been re-run alone — it has produced **two** false regressions in this campaign and
  **zero** true ones.
- **Two byte caps still disagree** (8 MB vs 35 MB), so a 25.9 MB document is flagged
  truncated by a bound never applied to it. A pre-existing V2 inconsistency; the corpus
  under-claims completeness as a result, which is the safe direction.
- **B1 App Service headroom**: ~1.75 GB, one worker; five concurrent analyses exceed the
  45-minute stale threshold. Run live batches of two.

---

## 8. The invariants, and where each is enforced

Every one of these is a database constraint or a test, not a convention:

| Invariant | Enforced by |
|---|---|
| Evidence before prose | `ck_research_findings_has_support` — a finding with no evidence id and no calculation id is **unstorable** |
| Model output is not evidence | `ResearchLead` with `claimed_*` fields throughout; `ck_research_leads_verified_has_a_platform_fetch` |
| A lead must pass **InvestingBuddy's own fetch** | The same CHECK: `verified` requires the SHA-256 of bytes the platform fetched itself |
| Annual ≠ interim ≠ quarterly | `ReportingPeriod.comparable_with`; sixteen calculation refusal reasons |
| No silent annualization | The calculation engine refuses a cross-type CAGR |
| Group ≠ segment; unknown ≠ Group | The typed scope triple; `get_financial_facts` requires an explicit scope; luxury's blocking question |
| Fail closed on conflicts | Two facts for one period are returned as a **conflict**, never resolved |
| Deterministic calculations over LLM arithmetic | `calculation_records`, and `ck_…_refused_has_no_value` |
| External content is untrusted and cannot instruct | Closed tool list; `may_contain_untrusted_content` as a **floor a payload cannot lower** |
| No issuer-specific hacks | No issuer name appears in any V3 module |

---

## 9. What acceptance would authorise, and what it would not

If you accept this Release Candidate, what follows is **still not a deployment**. The next
steps, in the order that keeps each one recoverable:

1. Add `develop/v3` to CI (OPEN DECISION #17) so the gates stop being manual.
2. Apply migrations 019-038 to a **staging** database, with `release/v2-current` still
   running against it — the additive-only property in §4.2 is what makes that safe, and
   staging is where it should first be true of a database somebody else is using.
3. Turn on **one** flag — `V3_CORPUS_ENABLED` is the obvious first, since §4.4 is the
   strongest evidence in this report — and run the real-issuer set.
4. Wire **one** entry point, with its own slice and its own live acceptance.

Nothing in this document authorises a merge to `main`, a deployment, or a migration
against the live database.

---

## 10. Recommendation

**Accept as a Release Candidate; do not deploy yet.**

The implementation is complete against its own specification and the gates are green. The
honest reading of §7 is that this is a well-tested set of contracts whose *behaviour* is
still unmeasured, and the single highest-value next action is not more building — it is
connecting one path end to end against one real issuer and finding out what breaks.

Every phase of this campaign found defects that only a review pass caught, and V3.1 found
two that only a **real document** caught. The pattern is consistent enough to predict:
**live data will find things fixtures did not.** Budget for that rather than being
surprised by it.
