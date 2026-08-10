# Closure Report — Phase 32A Slice 6C: final-report regeneration crash fix

> Produced after merge + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-10. Verdict: **Slice 6C CLOSED + STAGING-VALIDATED.**

## Problem

Clicking **"Generate Internal Final Report Draft"** on an already-completed report failed
with `unhashable type: 'dict'`. The admin could not regenerate a final report draft from
an existing final report at all.

## Root cause (reproduced locally, not guessed)

`generate_from_report` re-parses an already-final report's
`committee_chair_summary.provisional_internal_status`. On a report that has already been
rendered, that value is no longer a bare string — it is a **datapoint dict**
(`{"value": ..., "provenance": ...}`). That dict then hit an unguarded
`status not in ALLOWED_INTERNAL_STATUSES` set-membership check, and Python raised
`TypeError: unhashable type: 'dict'`.

The failure was reproduced locally before the fix was written; it was not inferred from
the stack trace alone.

## Fix

- A targeted `_coerce_status_value()` helper applied at **all 4 vulnerable sites**, so a
  status value is normalized to its scalar form before any set-membership check.
- A related **diagnosability** gap fixed at the same time: all **6** final-report
  endpoints previously discarded tracebacks (`str(exc)` only), which is why this crash
  surfaced as an opaque message with no server-side stack. They now call
  `logger.exception()`. Logging remains structured and secret-free per Phase 27.1D — ids,
  statuses and exception context only.

**No Alembic migration.** No new flag. No schema/contract change.

## PR / SHA

| | PR | Squash-merge SHA |
|---|---|---|
| Mainline | **#89** | `89b7f41` |

No hotfix was required for this slice.

## Staging validation (live, real data)

Exercised end-to-end on a fresh real BRBY report,
**`7d8be857-6086-40f5-ba64-7f2322c9b352`** (the same report used for Slice 6B's
validation):

1. **"Generate Internal Final Report Draft" succeeded** — HTTP **201**, new report
   **`17f150ee-7db9-4ec9-b2c1-7f781b10a20d`**, with `schema_valid=true`,
   `safety_valid=true`, `human_review_required=true`, `publication_ready=false`,
   `council_agents_completed=8` / `agents_failed=0`.
2. **"Validate Final Report" succeeded** — HTTP **200**.
3. **A SECOND regeneration**, this time *from report `17f150ee-...` itself* — i.e. a
   double-regeneration, regenerating from an already-regenerated final report, which is
   precisely the shape that previously crashed — also succeeded: HTTP **201**, new report
   **`ecf79192-e46b-4005-aed9-96fe3ea7aac5`**.

**No `TypeError` anywhere** across all three operations.

The safety posture is unchanged by regeneration: `publication_ready` stays `false` and
`human_review_required` stays `true` on every generated draft.

### Deployment facts

- API deployed to staging (`ib-stg-api`) at `b2aa1be`
  (`b2aa1bebcf3ec724b61b6477ce54770f861fdd2c`), verified via 5 consecutive `/health`
  checks matching exactly.
- No flag change for this slice. `AUTH_TEST_MODE` confirmed absent.
- Security spot-check: unauthenticated `POST /final-reports/{id}/validate` → 401.

## Honest limitations

- The `logger.exception()` improvement across the 6 final-report endpoints is a
  **diagnosability** change; no staging incident was deliberately induced to observe a
  traceback being emitted from each of the 6 endpoints. Its correctness rests on the
  offline tests plus the straightforward nature of the change, not on live evidence per
  endpoint.
- Validation covered the regeneration path for **one real issuer/report chain**
  (BRBY → `17f150ee` → `ecf79192`). The fix is input-shape-general (it coerces a datapoint
  dict to its scalar value), but only this chain was exercised live.

## Verdict

**Slice 6C CLOSED + STAGING-VALIDATED, 2026-08-10.** PR #89 → `89b7f41`, merged, deployed
and proven live: the exact operation that previously raised `unhashable type: 'dict'` now
succeeds, including under a double-regeneration that re-feeds an already-regenerated final
report back into the generator.
