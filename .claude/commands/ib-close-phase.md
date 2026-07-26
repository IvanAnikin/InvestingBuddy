# IB Close Phase Command

Validate a merged/deployed phase on staging and produce the closure report.
Runs **only after the human has explicitly approved the merge and the deploy has
finished**.

## Steps
1. Invoke the `staging-validation` skill.
2. Verify SHAs: API `GET /health` and Web `GET /api/version` `commit_sha` ==
   merge SHA (poll for 3 consecutive API matches).
3. Run validations A–I from
   `docs/development/templates/staging_validation_plan.md`
   (migration state, `AUTH_TEST_MODE` absent, phase HTTP checks, final flags,
   logs/no-secrets, safety/publication) via `ib-staging-validator`.
4. Produce the closure report
   (`docs/development/templates/closure_report.md`).
5. Only on a CLOSED + validated verdict: update `PHASE_LEDGER.md` and
   `docs/ROADMAP.md` (via `ib-docs-agent`).

## Guardrails
- Do NOT merge, deploy, restart, or change Azure app settings here — those are
  human actions. This command only observes and reports.
- Never mark CLOSED without real SHAs + validation results.
- Never print secrets from logs; cite + redact.
