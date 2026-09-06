# InvestingBuddy V3 — Release Candidate Report

**Status: `IMPLEMENTED`, V3.10 product acceptance complete, recommendation `NOT READY` (§11).
Awaiting user acceptance. Nothing is deployed and nothing is
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

> **Superseded in part by §10.** This section was written before V3.10. Items 7.1 and 7.2
> have since been addressed — real adapters exist, and MRNA, CFR and ASML have run end to
> end — and doing so exposed six defects recorded in §10.5. The rest of this section still
> stands. Read it, then read §10.

### 7.1 Nothing has run against a live issuer end to end — ✅ ADDRESSED IN V3.10 (§10.4)

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


## 10. V3.10 Product Acceptance

This section was added after §1-§9. It answers the question §7 said was open: **does the
architecture work as one complete product workflow against real issuers, with real
research tools and real model implementations?**

Partly. What follows separates *implementation complete* from *live-provider validated*,
because for one provider those are not the same thing.

### 10.1 The DeepSeek live contract — BLOCKED ON CREDENTIAL

**No DeepSeek API key was available.** Per the phase brief, the contract was **not
fabricated**. This exact slice is `BLOCKED ON CREDENTIAL`, and every other part of V3.10
that does not depend on it was completed.

What exists is an **opt-in live contract test** —
`tests/test_v3_deepseek_live_contract.py`, eight questions covering a standard call, tool
behaviour, server-side web search, search-response structure, URLs and citations, usage
metadata, errors and timeouts, and a small public query (a Moderna IR/pipeline search).
It requires `DEEPSEEK_API_KEY` **and** an explicit opt-in variable, so it never runs in
ordinary CI, and it is bounded to a handful of small requests.

Two tests **always** run and assert the blocked state is recorded rather than assumed:
the flag defaults off, and the adapter's shape is the *documented* one, still unverified.
Nine further tests exercise the transport's retry/timeout behaviour with **no credential
at all**, through the injectable client factory.

That work found one real defect in the existing adapter, fixed in this phase: the
transport retried **every** HTTP error, so a 401 from a bad key would burn the full retry
budget before failing. Errors now carry per-raise transience, with 400/401/403/404/405/422
permanent and 408/429 deliberately **not** in that set.

> **What is therefore unproved:** every statement in this repository about DeepSeek's wire
> format, its search-result shape, its citation fields and its usage accounting is
> **documentation-derived**. `V3_DEEPSEEK_ENABLED` is off. The first real key will find
> discrepancies; that is expected, and the test file exists to find them in one pass.

### 10.2 Real agent adapters

`Investigator`, `RedTeam`, `Responder` and `Chair` now have real model-backed
implementations behind the protocols the fakes already satisfied. Routing is configurable
and resolved per slot: Investigator and research follow-up prefer DeepSeek, Red Team and
Chair prefer Azure OpenAI, and **each falls back to what is actually configured**. With no
DeepSeek key, every slot resolved to Azure OpenAI — and the routing reports that honestly:

```
shares_vendor_with_chair = True
```

That flag exists because a Red Team run by the same vendor as the Chair is a weaker check
than one run by a different vendor, and a reader must be able to see which they got.

Deterministic fakes remain. Model output remains **non-canonical**: findings enter as
statements over cited evidence and are subject to the same verification, period and scope
rules as everything else.

### 10.3 The front door

`run_v3_research` is wired into `company_research_service` behind `V3_RESEARCH_ENABLED`,
**off by default**. It runs *after* the V2 report exists, catches everything, and on
failure appends a warning rather than failing the run. With the flag off, V2 behaviour is
byte-identical. The V3 result attaches additively under a single
`source_summary_json["v3_research"]` key.

### 10.4 Real-issuer runs

Three issuers, real public data, through the same function the front door calls.

| Issuer | What it exercised | Outcome |
|---|---|---|
| **MRNA** (NASDAQ) | biotech playbook, live SEC XBRL — 29 datapoints, CIK 1682852, 10-K filed 2026-02-20 | Council convened, 3 findings, Red Team challenge resolved, **real Chair** |
| **CFR** (SIX) | luxury playbook, live bounded traversal of `richemont.com`, real 8.8 MB / 160-page FY26 annual report ingested to 173 chunks | Council **refused**: `blocking_question_open` on segment discipline |
| **ASML** (AMS) | semiconductor playbook, real annual-report page ingested to 18 chunks | Council **refused**: `blocking_question_open` on cycle position |

The best MRNA run:

```
council   convened=True  findings=3  gaps=10
red team  challenges=1  resolved=1  withdrawn=0  unknown_targets=0
chair     label=reject_for_now  setup=cautious  fallback=False
          key_points=3  discarded_citations=1
delta     unchanged_thesis=True  new_evidence=4  resolved_questions=1  new_gaps=1
findings  [group|2025]  ×3
```

Three things in that block are worth stating plainly. The Chair is **real**, not the
deterministic fallback. It **discarded one citation** the model invented. And the findings
carry a period and a scope, which they did not before this phase — see 10.5.

The two refusals are **correct behaviour, not failures**: the luxury playbook's blocking
question needs segment facts that were not seeded, and ASML's needs bookings and backlog
that the ingested page does not contain. The model said so itself: *"no information found
on bookings, backlog, book-to-bill ratio, or lead times."* A platform that convened a
Council anyway would be the defective one.

**Consequently, no playbook-gated Council convened on real data.** The convened runs came
via the supported no-playbook path, where the Director's baseline has no blocking
question. That path is what exercised the Red Team and Chair live, and it is labelled as
such in the ledger.

### 10.5 The six defects only a real run found

| # | Defect | Why fixtures missed it |
|---|---|---|
| 1 | A `research_runs` id was passed into a column whose FK points at `research_jobs`. **Every tool call failed to persist and took the transaction with it.** | The unit suite runs on sqlite with foreign keys off. |
| 2 | `lookup_entity` was called with a `company_id`; it resolves an issuer and takes a ticker. | Fakes accepted any string. |
| 3 | Biotech's blocking question required `get_recent_filings`, which **nothing implemented** — a biotech run could never convene. | The tool vocabulary is closed; nothing checked it against implementations. |
| 4 | **Every finding carried `period_key=None`** while citing facts that had one. Three said "FY2026 projected" for FY2025 actuals. | Fixture evidence was single-period. |
| 5 | The harness mis-derived periods: SEC's `as_of` is the period end for a reported concept and the **filing date** for a derived one. | Nothing else read live companyfacts. |
| 6 | 170 of 173 chunks from a real annual report carry **no scope**. | Fixtures were scope-labelled. Recorded as a limitation, not fixed — see 10.7. |

Defect 4 is the important one. On a platform whose purpose is period and scope integrity,
model-written findings were **exempt from it**. Findings now inherit period and scope from
their evidence on **agreement or nothing**: one period across the cited evidence is
inherited, disagreement discards the finding and opens a `conflicting_sources` gap, and
absence stays absent. Unknown scope is still unknown.

The guard then fired on real data:

```
gap [conflicting_sources] A statement was discarded because the evidence it cites
                          does not agree on periods ['2025', '2026'].
```

That is the first time this campaign's central invariant reached **model output** rather
than parsed documents.

### 10.6 Research memory and delta

Two consecutive runs over the same issuer, in one process, against one database.

| Required | Observed |
|---|---|
| New evidence | ✅ `new_evidence: 4` |
| Unchanged prior finding | ✅ `unchanged_thesis: True`, `changed_facts: 0` |
| Resolved gap | ✅ `resolved_questions: 1` |
| New gap | ✅ `new_gaps: 1` |
| **Superseded finding** | ⚠️ **not demonstrated on real data** |

Supersession has unit coverage — the slot is `(question, period, scope)` and a newer
finding in the same slot supersedes the older — but no real two-run pair produced a
changed fact in the same slot, because the underlying SEC facts did not move between runs.
**It is tested, not demonstrated.**

The rule that stale memory never overrides newer primary evidence holds by construction:
memory is a **view over the prior run's ledger**, not a snapshot table, and it is read as
context, never as evidence. Nothing promotes a remembered finding into the current run's
support set.

### 10.7 The Group/segment regression

> *A Specialist Watchmakers figure must never be promoted to Group scope.*

The real corpus contains the trap verbatim:

> *"The Group's Specialist Watchmakers reported sales of € 3.1 billion, down by 4% at
> actual exchange rates…"*

It contains the word **Group**, it is about a **segment**, and the figure is the
segment's. Five regression tests use that exact sentence, including one where the model
writes *"the Group's sales were €3.1 billion"* over segment-scoped evidence and still
gets **segment** scope — because scope is inherited from evidence and never read out of
prose.

**But the honest reading:** that chunk's real `scope_key` is `NULL`, so on today's corpus
a finding built on it inherits **no** scope. Unknown is not Group, so the invariant holds
— **by absence rather than by correct labelling.** A reader must be able to tell "labelled
segment" from "declined to label", and today the real corpus is almost entirely the
second. Scope extraction over real documents is unbuilt work, not proved work.

### 10.8 Report compatibility

| Check | Result |
|---|---|
| The existing `ReportRead` schema serialises the augmented report | ✅ |
| The frontend's ` ```json ` block still extracts | ✅ `content_markdown` is untouched |
| Pre-existing `source_summary_json` provenance survives | ✅ |
| `human_review_required`, `status=draft`, the disclaimer | ✅ unchanged |

The frontend was **not** modified in this phase.

### 10.9 Cost

Measured from the real client's own usage accounting:

```
model_calls=8   input=15,341 tok   output=1,689 tok   vendor=azure_openai
tool_calls=15
useful findings 3 of 3 (0 withdrawn by the red team)

estimated_cost_usd                    = None
cost_per_verified_useful_finding      = None
  "no price is recorded for any provider; a cost of unknown is never zero"
```

A **verified useful finding** is one that survived to the Council and was not withdrawn by
the Red Team. Counting every statement the model emitted would let a run producing forty
retracted claims look productive.

The ratio is `None` because no price book is configured, and reporting an unpriced
provider as costless is exactly how a benchmark picks the wrong one.

> **No cross-provider comparison is claimed.** Only Azure OpenAI ran.

### 10.10 Search and monitoring

**ADR-053 stands.** `pgvector` is unavailable in this environment
(`SELECT count(*) FROM pg_available_extensions WHERE name='vector'` returns 0), so the
production path is PostgreSQL full-text plus a portable JSONB-embedding rerank behind the
existing `SearchBackend`. Lexical retrieval is independently usable and is what the real
CFR and ASML corpora were searched with. **No paid search service was provisioned**, and
per the brief this is not treated as a release blocker.

V3.9 monitoring remains **feature-gated and unscheduled**. Its logic is validated locally;
an AST check asserts the module imports and calls no scheduler. **No production scheduling
was provisioned.**

### 10.11 V3.10 acceptance gates

| Gate | Result |
|---|---|
| ruff | ✅ clean |
| pytest | ✅ **5,971 passed**, 23 skipped |
| mypy | ✅ 71 (baseline, unchanged) |
| Migrations apply and reverse on real PostgreSQL 16 | ✅ unchanged from §4.2 |
| Real issuers run end to end | ✅ MRNA, CFR, ASML |
| Every defect found became a corrective with regression coverage | ✅ six |
| DeepSeek live contract | ⛔ **BLOCKED ON CREDENTIAL** |

---
## 11. Recommendation

This supersedes the pre-V3.10 recommendation ("accept as a Release Candidate; do not deploy
yet"), which was written before anything had run against a real issuer.

# NOT READY — four specific blockers

The engineering is sound, the gates are green, and §10 records real issuers moving through
the whole chain. But *ready for main promotion review* is a claim about **correctness on
real data**, and on four points the evidence does not yet support it.

**Blocker 1 — the primary external research runtime has never been called.**
DeepSeek is the designated research provider for Investigator and follow-up work, and its
wire contract is entirely documentation-derived: request shape, search-result structure,
citation fields, usage accounting, error taxonomy. `V3_DEEPSEEK_ENABLED` is off and no key
exists. This is *implementation complete, live-provider validation blocked* — the
distinction §13 asked for. **Resolution:** one key, one opt-in run of
`tests/test_v3_deepseek_live_contract.py`, then reconcile the adapter to observed
behaviour. This is bounded work, likely a single session.

**Blocker 2 — no playbook-gated Council has ever convened on real data.**
All three real-issuer playbook runs refused, correctly, on a blocking question. The
convened runs — the ones that exercised the Red Team and a real Chair — went through the
**no-playbook** path. V3.6's five industry playbooks are therefore proved to *refuse*
properly and unproved to *complete*. **Resolution:** seed or extract the specific evidence
each blocking question needs (segment facts for luxury, bookings/backlog for
semiconductors, filings-derived pipeline state for biotech) and get one playbook-gated
Council to convene end to end.

**Blocker 3 — scope labelling over real documents is effectively absent.**
170 of 173 chunks from a real 160-page annual report carry `scope_key = NULL`, including
the chunk holding the Specialist Watchmakers figure. The Group/segment invariant holds —
but **by absence, not by correct labelling**. For a platform whose central promise is that
a segment number is never presented as a Group number, "we declined to label almost
everything" is a safe answer and not a correct one. It also silently caps usefulness:
scope-filtered retrieval over that corpus returns nearly nothing. **Resolution:** a scope
extraction slice over real documents, with the Richemont sentence as its regression case.

**Blocker 4 — the cost basis of the provider strategy is unmeasured.**
`cost_per_verified_useful_finding` is `None` because no price book is configured, and only
one vendor ran. The provider strategy — DeepSeek for research, Azure OpenAI for reasoning —
is a **cost** argument that currently has no cost measurement behind it. **Resolution:**
configure verified prices for the two vendors actually in use, then re-run the acceptance
harness. The measurement plumbing already exists and produced real token counts.

### What is explicitly *not* a blocker

- **ADR-053 / no pgvector.** Lexical retrieval is independently usable and is what the real
  CFR and ASML corpora were searched with. Per §11 of the phase brief, not a blocker.
- **V3.9 monitoring being unscheduled.** Feature-gated and locally validated, as directed.
- **Nothing being deployed.** That is the instruction, not a defect.
- **Supersession not demonstrated on real data.** It has unit coverage; the underlying SEC
  facts simply did not change between runs. Worth watching, not worth blocking on.

### What this campaign is, stated plainly

Nine phases, thirty-eight migrations, 5,971 tests, an additive-only schema that reverses to
a byte-identical V2, and a chain that demonstrably carries real SEC facts and a real
160-page annual report through investigation, verification, a Red Team that withdrew a
finding, and a real Chair that discarded a fabricated citation. The invariants are enforced
in Python *and* in database CHECK constraints, and in V3.10 the central one — periods and
scopes never silently merge — reached model output for the first time and **refused a
statement on live data**.

The pattern predicted in the pre-V3.10 recommendation held exactly: live data found six
things fixtures did not, and the most serious of them (findings exempt from period and
scope integrity) was invisible to 5,900 passing tests. Four of the six were fixed under
this phase with regression coverage; two became recorded limitations that are now blockers
above.

That prediction is worth extending. The four blockers above are what running *three*
issuers revealed. Running thirty would reveal more, and the correct inference is not that
V3 is fragile — it is that **the acceptance harness is now the most valuable artefact this
campaign produced**, because it converts "we believe this is correct" into "here is what
broke."

**The decision remains yours.** Nothing here has been merged to `main`, nothing is
deployed, no V3 migration has touched the live database, and the protected V2 refs are
untouched at `4b60e07`. If you would rather accept the release candidate now and treat the
four blockers as the first slices of a V3.11 hardening phase, that is a defensible call and
the branch is in a state that supports it — the flags are all off and V2 behaviour is
byte-identical with them off. My recommendation is the narrower one: clear Blockers 1 and 3
first, because a provider whose contract is unverified and a corpus that cannot say what
scope a number has are the two things most likely to put a wrong figure in front of a
human.
