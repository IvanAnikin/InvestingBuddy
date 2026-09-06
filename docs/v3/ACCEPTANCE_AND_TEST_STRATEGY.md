# InvestingBuddy V3 — Acceptance and Test Strategy

**Status:** V3 TARGET. Baseline `4b60e07`.

---

## 1. Actual repository commands

Discovered from `.github/workflows/`, not assumed.

### API — from `apps/api/`

```bash
pip install -e ".[dev]"
ruff check .                 # api-ci.yml
pytest tests/ -v             # api-ci.yml
mypy app                     # NOT in CI; local gate only
```

> **`mypy` baseline is scope-dependent.** `mypy app` and `mypy` over a broader
> scope including `tests/` produce very different counts. Always diff the *same*
> command between `develop/v3` and the branch — comparing a narrow baseline
> against a broad one manufactures a regression that is not there.

### Web — from `apps/web/`

```bash
npm ci
npm run typecheck            # web-ci.yml
npm run lint                 # web-ci.yml
npm run build                # web-ci.yml
npx playwright test          # frontend-e2e.yml — workflow_dispatch only, never automatic
```

### CI coverage gap for V3

`api-ci.yml` and `web-ci.yml` trigger only on `main` (`push`/`pull_request`) with
`apps/**` path filters. **A PR into `develop/v3` gets no automatic checks.** Until
this is resolved ([OPEN DECISION #17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch),
user-owned), every slice runs the gates locally and records the exact commands
and their output.

`scripts/v3-corpus-acceptance.py` (V3.1) pushes **one real financial document**
through the whole corpus locally — its own temporary database and artifact
directory, no deployment, nothing existing touched, not in CI, and no network call
unless asked twice (`--url` *and* `--allow-network`, through the repository's own
guarded fetcher). It exists because fixtures are not a substitute: it found two
defects in V3.1 that the unit suite could not have — raw headings being labelled
business segments, and a chunk-id collision between a live and a deep parse.

`scripts/v3-gates.sh` runs all of them in one command — the same commands the
workflows run, so a local pass means what a CI pass would have meant. It does
**not** resolve #17: the workflow files are untouched, because they also live on
`main` and adding a branch to them is the user's call.

> **Run changed tests in isolation as well as in the full suite.** A green full
> suite has already hidden an order-dependent failure that CI then caught. `pytest
> tests/test_x.py -v` on its own is a separate, cheap signal.

> **`pytest tests/` is NOT offline on a developer machine.** `test_phase7_azure_openai_real.py`
> is `skipif`-guarded on `settings.llm_provider != "azure_openai"`, so it is silent
> in CI — and a local `.env` that sets `LLM_PROVIDER=azure_openai` with a real key
> turns its 8 tests into **live Azure OpenAI calls**, subject to the deployment's
> TPM quota. During V3.1 they failed 7-of-8 on one full-suite run and passed in
> isolation 25 seconds later, and passed on every other run of the same commit.
> A failure in that file is a network or quota event until proven otherwise:
> re-run the file on its own before concluding anything about the change under
> test.

**Live-network test files** (fail closed to "environment", not "regression"):
`test_phase7_azure_openai_real.py`, plus anything marked `integration`
(`ENABLE_INTEGRATION_TESTS=true`, never in CI).

---

## 2. Test layers

| Layer | Scope | Network | Determinism |
|---|---|---|---|
| Unit | Pure functions, state machines, schemas, parsers. | None. | Total. No clock dependence — a test whose outcome depends on the wall clock is a flake with a schedule. |
| Adapter contract | Provider adapters against recorded fixtures. | None. | Total. |
| Integration | API + DB + worker, real PostgreSQL. | Local only. | High. |
| Live provider smoke | Real vendor calls. | Yes. | Opt-in, gated, budget-capped. |
| Live issuer acceptance | Real filings, real issuers. | Yes. | Manual, recorded. |

---

## 3. What each V3 phase must prove

| Phase | Must demonstrate |
|---|---|
| V3.0 | A job survives worker restart; a duplicate submit joins rather than duplicates; an expired lease is reclaimed exactly once; attempts are bounded and dead-letter is reachable; cancellation is honoured at a task boundary; no status vocabulary drift from `research_job.py`. |
| V3.1 | A real annual report ingests to pages/sections/chunks; a query months later returns the right page with citable lineage; period and scope filters actually constrain results; `DocumentTable` survives a borderless five-year summary. **✅ All four demonstrated — see [the V3.1 phase gate](IMPLEMENTATION_PLAN.md#10-v31-phase-gate). On a real 25.9 MB, 169-page Pandora Annual Report 2025: 169/169 pages and 471,780 characters retained against 19,232 as excerpts; `"cash flow from operations"` returns page 138 with a full citation label; a semantically perfect wrong-period match is excluded; the borderless five-year summary came back as a grid with its `['2025','2024','2023','2022','2021']` header intact.** |
| V3.2 | Two listings resolve to one `LegalEntity`; an ambiguous match raises a gap instead of merging; every existing `companies` row backfills; every existing report still renders. **✅ All four demonstrated — see [the V3.2 phase gate](IMPLEMENTATION_PLAN.md#11-v32-phase-gate). Beyond the four: every schema guarantee was exercised against real PostgreSQL 16 with real conflicting statements rather than through the ORM — two live issuers cannot share a ticker on one venue, two subjects cannot hold one LEI, a CIK claimed for a non-SEC-eligible venue is refused whatever produced it, and a name-only match returns `ambiguous` even with exactly one candidate.**|
| V3.3 | An agent cannot reach any tool outside its declared list; a calculation with incompatible periods or scopes is **refused**, not computed; every tool call is persisted with consumption units. **✅ All three demonstrated — see [the V3.3 phase gate](IMPLEMENTATION_PLAN.md#12-v33-phase-gate). An empty tool list permits nothing and an undeclared tool never reaches its callable; sixteen calculation refusal reasons are each exercised and a refused row cannot carry a value, enforced by the database; every attempt — ok, refused and error — writes a row with the arguments AS ASKED and only the units the tool actually measured.**|
| V3.4 | A provider claim without a resolvable source stays a `ResearchLead`; a rejected lead keeps its rejection reason; the benchmark produces `cost_per_verified_finding`; private content is never in a provider payload. **✅ All four demonstrated — see [the V3.4 phase gate](IMPLEMENTATION_PLAN.md#13-v34-phase-gate). A claim citing no URL is `unverifiable`, a status of its own; a `verified` row is unstorable without the SHA-256 of bytes InvestingBuddy fetched itself, and a claim present in the provider's snippet but absent from the fetched document is rejected. The benchmark returns `None` — never `0.0` — whenever cost or verified count is unknown, and produces no number at all for a provider that did not run.** |
| V3.5 | The loop terminates on a stated limit; a gap becomes a follow-up task; a finding without evidence ids cannot be persisted. **✅ All three demonstrated. Every limit is checked BEFORE the work it would authorise — asserted by the investigator not being called — and the stop vocabulary is closed with no "other". A closable gap becomes a second round that closes it; an unclosable one buys no round and is *accepted* instead. A finding with no support is refused in Python and by a database CHECK, and the attempt is recorded as a gap rather than dropped. Beyond the three: `is_complete_analysis` is False for a run whose agents all returned successfully but whose blocking question is unanswered.** |
| V3.6 | Biotech and luxury runs of the same shape produce demonstrably different questions, metrics and sources; a blocking question that cannot be answered prevents the Council from convening. **✅ Both demonstrated. No two of the five playbooks share a question key, biotech and luxury are disjoint, and the ABSENCES are asserted: biotech requires no margin metric and a bank requires none of the generic industrial ones. Biotech's blocking `pipeline_state` needs `get_recent_filings`, which nothing implements, so the question is unassignable and `council_may_convene` is False — while luxury's blocking question, which real roles can answer, leaves the Council blocked only on the question being UNANSWERED, which the loop can fix.** |
| V3.7 | A Red Team challenge targets a `finding_id`; an unresolved disagreement reaches the Chair intact; the Chair surfaces rather than silently resolves a source conflict. **✅ All three demonstrated. A challenge naming anything outside the run's ledger is skipped and counted; an unresolved challenge becomes a `ResearchDisagreement` when a counterpart finding exists and always reaches the Chair through `challenges_for_chair`, unresolved-first; and unresolved disagreements are assembled into `CouncilInput` and ordered first, so a Chair cannot be handed a pack in which the conflict was already resolved by whoever built it. A response with no evidence cannot resolve a challenge — in Python and by a database CHECK — and a second round is unstorable.** |
| V3.8 | A second run reports what changed; a prior conclusion contradicted by new evidence is marked invalidated, not silently kept. **✅ Both demonstrated, and the harder half is the refusals: a rephrasing, a different period, a different scope and SILENCE are each not reported as changes, because every one of them would make a routine refresh look like a reversal. A prior finding contradicted by new evidence is marked invalidated in the delta and the prior row is NOT modified — it was a correct conclusion from the evidence available then. `unchanged_core_thesis` cannot be true beside an invalidated finding, enforced by the database.** |

---

## 4. Real-issuer regression set

Fixtures are necessary and insufficient. Every one of these companies exposed a
defect that only live data found.

| Issuer | Validates | Known trap |
|---|---|---|
| **PNDORA** | European source path; annual vs interim; cash flow and leverage; report rendering. | Documents are on an off-domain CDN with extension-less URLs; the current-period document was lost at a different pipeline stage than for CFR or MONC. |
| **CFR** | Group vs Jewellery Maisons vs Specialist Watchmakers; annual vs current period. | **Segment figures must never become Group.** Watchmakers €107m required a font-size PDF heading stack to scope correctly. |
| **MRNA** | US SEC path; numeric conflicts; biotech playbook; trial/regulatory sources. | Has **8 genuine** numeric conflicts — a guard that suppresses them all is broken, not safe. |
| **ASML** | European reporting; semiconductor playbook. | New to the set with V3. |
| *(later)* a major bank | Banking playbook; CET1/NPL; generic margin analysis must be refused. | — |
| *(later)* a defence company | Procurement/backlog sources. | — |

Operational notes carried forward: run live analyses in **batches of two** — five
concurrent analyses exceed the 45-minute stale threshold on B1. Manual-ticker
discovery needs a **bare ticker plus a separate exchange** or the verified-issuer
registry silently never matches. Report content is **persisted**, so a copy fix
requires regeneration to be visible.

---

## 5. External API test safety

Third-party credits are real money and rate limits are shared.

1. **Unit tests use fakes.** `fake_client.py`, `fake_discovery_client.py`,
   `fake_field_review_client.py` are the only clients the unit suite touches, and
   every new provider ships with a fake in the same slice as the adapter.
2. **Contract tests are opt-in**, gated by an explicit env flag, following the
   existing `enable_integration_tests` pattern (`config.py:72`).
3. **Live tests are gated and budget-capped.** A live test declares a maximum
   spend and aborts rather than exceeding it.
4. **Never print secrets.** The `RedactingFilter` exists because root-level INFO
   logging once leaked an EODHD `api_token`. Provider payload logging must be
   assumed to leak until proven otherwise.
5. **Never commit credentials.** Registry entries describe policy and identity,
   never a credential; `assert_registry_safe` scans the serialised payload as a
   backstop.
6. **Benchmarks are never in CI by default.** A full three-issuer, five-provider
   benchmark is a deliberate, budgeted, manually triggered run.

---

## 6. Invariant tests that must not regress

These encode expensive lessons. They stay green.

- `test_worker_timeout_invariant.py` — the ingestion budget must stay under the
  deployed gunicorn worker timeout. These drifted apart once (180 vs 120) and
  cost six live outages.
- `test_ingestion_event_loop_not_blocked.py` — CPU-heavy extraction must not
  starve the event loop.
- `test_orphaned_job_detection.py` — a job whose process is gone reads as dead
  immediately, and the outcome does not depend on the clock.
- `test_investment_content_contract.py` — no BUY/SELL/HOLD, no price target, no
  fabricated fair value.
- `test_manual_qa_fact_count_scopes.py` — fact counts name their population. The
  rule is **not** to make the numbers agree; it is to say which population each
  number counts.
- The current-period suite (`test_current_period_*.py`) — current-period evidence
  sits beside the annual, never instead of it.

> A test that *asserts the bug* is worse than no test. One existing test did
> exactly that and had to be corrected along with the defect. When a V3 change
> makes a test fail, establish which of the two is wrong before editing either.

---

## 7. Acceptance for the V3 release

V3 reaches `VALIDATED` only when:

1. All gates pass on `develop/v3`, with commands and output recorded.
2. Every phase's demonstration in §3 has been performed.
3. The real-issuer set has been run on live data, with results recorded.
4. The provider benchmark has run and `cost_per_verified_finding` is known.
5. Migrations have been applied to a scratch database and rolled back
   successfully.
6. No V2 regression: existing reports render, existing IDs resolve, existing
   citations resolve.

Then a Release Candidate Report is prepared and **the user decides**. Nothing in
this document authorises a merge to `main` or a deployment.

---

## 8. V3.11 acceptance harnesses — what actually runs against real issuers

Three scripts, none in CI, each self-contained and each exiting non-zero on a real failure.
They exist because **every issuer in the regression set has exposed a defect that only live
data found**, and after V3.11 that record is unbroken across eleven phases.

### `scripts/v3-issuer-acceptance.py`

One issuer, its own scratch database migrated to head, seeded with **real public data**
(SEC XBRL, and real issuer/regulator documents through the repository's own guarded,
allowlisted, DNS-pinned fetcher), then `run_v3_research` — the same function the front door
calls.

```
python scripts/v3-issuer-acceptance.py --ticker CFR --exchange SIX \
  --sector "Consumer Discretionary" --industry "Luxury Goods" \
  --database-url postgresql+psycopg://… --allow-network \
  --seed-corpus-url https://…/annual-report.pdf --seed-corpus-domain richemont.com \
  --price-in 0.40 --price-out 1.60 --price-source "list price, not an invoice" --runs 2
```

`--seed-corpus-url` is repeatable: a blocking question is often answered by a **different
document** from the one carrying the financials, which is what ASML's `cycle_position`
demonstrated.

**Invariant checks (exit non-zero):** a chair label outside the five; a forbidden term in
the synthesis; a finding with no evidence and no calculation; a segment-scoped finding
whose statement says Group; a council finding with no citation; **a citation that resolves
to no row**; a recorded pipeline error.

### `scripts/v3-scope-acceptance.py`

Rebuilds the parse from what the corpus already persisted and re-resolves every chunk, so
scope changes can be measured without re-fetching a 9 MB document.

Reports coverage, attribution method, why each unknown stayed unknown, and the metric that
governs release safety: **the false-positive Group rate**. A run that raises coverage while
promoting one segment figure to Group is a **regression**, and the script exits non-zero on
exactly that.

Expectation grammar: `segment:x` (exactly), `segment:x?` (**that scope or unknown, never
anything else** — the standard the acceptance brief sets for evidence whose chunk may
legitimately carry more than one scope), and `!group`.

### `scripts/v3-corpus-acceptance.py`

One real document end to end through the corpus, including re-retrieval from retained bytes
and a determinism check on re-parse.

## 9. The rule these harnesses encode

> **The goal is never to minimise `unknown`.**

A wrong Group label is far worse than an absent one. Every acceptance metric here is
paired with the safety metric that constrains it, and a coverage improvement bought with
wrong answers is reported as the regression it is — which is exactly what happened during
V3.11.2, where an audit of every label sent coverage back from 22.5% to 8.1% and made the
number trustworthy.
