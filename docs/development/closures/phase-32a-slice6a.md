# Closure Report — Phase 32A Slice 6A: discovery-council reliability parity

> Produced after merge + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-10. Verdict: **Slice 6A CLOSED + STAGING-VALIDATED.**

## Problem / root cause

The discovery-run LLM council (`run_discovery_council` — 8 agents: `run_coordinator`,
`candidate_prioritization`, `novelty_coverage`, `diversity_anti_convergence`,
`evidence_sufficiency`, `risk_gatekeeper`, `run_red_team`, `discovery_chair`) never
received the Phase 32A Slice 4 reliability machinery (bounded transient-only retries,
capped backoff, honored `retry-after`, total wall-budget, reserved critical budget,
deterministic chair fallback). It called each agent exactly once with no retry, no
backoff, no wall-budget and no fallback.

This was a **plain parity gap, not a design choice** — Slice 4 only ever touched the
single-company council (`council.py`). The observable consequence on staging was a
discovery council collapsing to a small number of completed agents under Azure
`gpt-4.1-mini` rate limiting in the very same session where the company council,
protected by Slice 4, completed 8/8.

## Fix

- Slice 4's retry/backoff/wall-budget/deterministic-fallback machinery was **extracted
  out of `council.py`** into a new, agent-shape-agnostic module
  `apps/api/app/services/llm/retry_engine.py`. `council.py` was refactored to call into
  it behavior-preservingly (the company council's public API and generated text,
  including the deterministic chair-fallback wording, are unchanged);
  `discovery_council.py` was then wired to the same engine.
- Gated by a new **default-OFF** flag `LLM_DISCOVERY_COUNCIL_RETRY_ENABLED`; with it off
  the discovery council is byte-for-byte identical to its prior behaviour.
- **Discovery-specific budget.** Unlike the company council (strictly sequential, inline
  in the HTTP request, bound by the ~230s Azure App Service gateway timeout), the
  discovery council runs as an **async background job** with no gateway constraint, so it
  gets its own more generous budget: **300s total / 60s critical reserve** (vs. the
  company council's 150s / 45s), the reserve protecting `run_red_team` +
  `discovery_chair`.
- **Deterministic discovery-chair fallback.** If the LLM chair does not complete, a
  deterministic, non-consensus run summary is attached (`chair_fallback_used=true`,
  `run_quality="failed"`), built only from already-validated stored agent outputs, with
  empty `candidate_notes`/`run_notes` (so it carries no citations) and no consensus, no
  candidate action, no recommendation. The failed LLM-chair entry is kept in `agents` so
  the council stays honestly visible as partial.

## PRs / SHAs

| | PR | Squash-merge SHA |
|---|---|---|
| Mainline | **#88** | `25abc7b` |
| Hotfix (response-schema visibility) | **#94** | `a1e52a6` |

**No Alembic migration** in this slice.

### Hotfix #94 — why it was needed

Found live on staging, not in review: the deterministic discovery-chair fallback fired
**correctly internally** (`chair_fallback_used=true`, honest partial-completion
synthesis, verified directly in the raw DB payload) — but
`DiscoveryCouncilReviewResponse`, the API response schema, never declared
`chair_fallback_used` / `deterministic_discovery_chair` as fields, so **Pydantic v2
silently dropped both** before they reached any API consumer, including the admin UI.

This was a **visibility-only bug, not a logic bug**: nothing was fabricated, the fallback
itself behaved correctly, but an operator inspecting the API could not tell that a
fallback had occurred. Fixed by declaring the fields on the response schema.

## Staging validation (live, real data)

Fresh discovery run **`6b0700a9-9a89-4ec7-b078-9a2b7d7b72c9`** — thesis "European luxury
goods companies", universe of 8 (UHR, CFR, MC, RMS, KER, MONC, BRBY, PNDORA), with 3
names correctly region-excluded (CPRI, TPR, Prada).

The first discovery-council run on it (`14a2814e-...`) was executed **concurrently with a
company-council call**, i.e. under real Azure capacity contention. Result:

- **3/8 agents completed**, `run_quality="failed"`.
- The deterministic fallback **fired correctly**, verified directly in the raw DB row:
  > "3 of 7 non-chair council agents completed. Completed: candidate_prioritization,
  > novelty_coverage, evidence_sufficiency. Did not complete: run_coordinator,
  > diversity_anti_convergence, risk_gatekeeper, run_red_team"
- **No fabricated consensus** — the synthesis states exactly what did and did not
  complete.

This is the intended proof: the reliability machinery is **live and correctly degrading**
rather than silently failing, and the degradation is honestly reported rather than
papered over. The same run also surfaced the response-schema gap fixed by #94.

### Deployment facts

- API deployed to staging (`ib-stg-api`) at `b2aa1be`
  (`b2aa1bebcf3ec724b61b6477ce54770f861fdd2c`), verified via 5 consecutive `/health`
  checks matching exactly. Web deployed at `dee5998` (unaffected by the later
  backend-only hotfixes).
- `LLM_DISCOVERY_COUNCIL_RETRY_ENABLED=true` on staging — flipped for validation and
  **KEPT ON** after validation succeeded. App-settings before/after name-set diff
  confirmed only this key plus `LLM_FIELD_REVIEW_COUNCIL_ENABLED` were added; all prior
  flags unchanged. `AUTH_TEST_MODE` confirmed absent throughout.
- Security spot-checks: unauthenticated `GET /market-discovery/runs` → 401.

## Honest limitations

- The **recovery** path (a transiently-failed discovery agent being retried and then
  succeeding, lifting the run to 8/8) was **not observed live** on staging in this
  validation — the observed live run degraded to the deterministic fallback instead.
  Retry/recovery behaviour is covered by the offline test suite
  (`apps/api/tests/test_phase32a_slice6a_discovery_council_reliability.py`, 22 tests:
  transient recovery, permanent-error non-retry, retry exhaustion,
  no-rerun-of-succeeded-agents, critical-reserve protection, fallback honesty/safety,
  8/8, 5/8, near-total 1/8→7/8, complete provider outage, flag-OFF byte-identical
  regression guards, byte-for-byte wording preservation for both callers of the shared
  engine) — but the live staging evidence proves the **fallback** path, not the
  **recovery** path.
- A discovery-council run under contention still legitimately reports
  `run_quality="failed"` with a partial agent set. That is the honest, intended outcome,
  not a defect — but it does mean the parity work does **not** guarantee 8/8 under Azure
  TPM pressure.

## Verdict

**Slice 6A CLOSED + STAGING-VALIDATED, 2026-08-10.** Mainline #88 → `25abc7b`, hotfix
#94 → `a1e52a6`, both merged, deployed and exercised live. The parity gap is closed: the
discovery council now runs under the same shared, bounded reliability engine as the
company council, degrades deterministically and honestly when Azure capacity does not
permit a full council, and that degradation is now actually **visible** through the API
after #94.
