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
this is resolved ([OPEN DECISION #17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch)),
every slice runs the gates locally and records the exact commands and their output.

> **Run changed tests in isolation as well as in the full suite.** A green full
> suite has already hidden an order-dependent failure that CI then caught. `pytest
> tests/test_x.py -v` on its own is a separate, cheap signal.

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
| V3.1 | A real annual report ingests to pages/sections/chunks; a query months later returns the right page with citable lineage; period and scope filters actually constrain results; `DocumentTable` survives a borderless five-year summary. |
| V3.2 | Two listings resolve to one `LegalEntity`; an ambiguous match raises a gap instead of merging; every existing `companies` row backfills; every existing report still renders. |
| V3.3 | An agent cannot reach any tool outside its declared list; a calculation with incompatible periods or scopes is **refused**, not computed; every tool call is persisted with consumption units. |
| V3.4 | A provider claim without a resolvable source stays a `ResearchLead`; a rejected lead keeps its rejection reason; the benchmark produces `cost_per_verified_finding`; private content is never in a provider payload. |
| V3.5 | The loop terminates on a stated limit; a gap becomes a follow-up task; a finding without evidence ids cannot be persisted. |
| V3.6 | Biotech and luxury runs of the same shape produce demonstrably different questions, metrics and sources; a blocking question that cannot be answered prevents the Council from convening. |
| V3.7 | A Red Team challenge targets a `finding_id`; an unresolved disagreement reaches the Chair intact; the Chair surfaces rather than silently resolves a source conflict. |
| V3.8 | A second run reports what changed; a prior conclusion contradicted by new evidence is marked invalidated, not silently kept. |

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
