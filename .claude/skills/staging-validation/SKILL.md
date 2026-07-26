---
name: staging-validation
description: >-
  Guides the merge → deploy → validate → close flow for an InvestingBuddy PR.
  Use when asked to "merge", "deploy", or "validate" a phase PR. Enforces the
  human gate: merges only after explicit approval, waits for the deploy, verifies
  API/Web SHAs, runs staging validations (A–I), checks logs for secrets, confirms
  final flags, and produces a closure report.
---

# Staging Validation Skill

Post-implementation flow. Nothing here runs without explicit human approval at
each gate.

## Activation cues
- "Merge PR #NN", "deploy the phase", "validate staging", "close the phase".

## Workflow

### 1. Pre-merge checks (via ib-pr-review-agent)
- Tests green, security scan PASS, docs present, migration present if needed,
  validation plan drafted (`templates/staging_validation_plan.md`).
- Output an APPROVED / REQUEST CHANGES verdict.

### 2. HUMAN GATE — merge
> STOP. Do not merge until the human explicitly approves THIS PR.
After approval:
```bash
gh pr merge <NN> --squash        # only after explicit approval
```
Record the merge SHA.

### 3. Deploy wait
Deploys run via GitHub Actions on push to `main` (path-filtered):
`deploy-api-staging.yml`, `deploy-web-staging.yml`. Migrations are NOT run by the
workflow — if the phase added one, it is applied manually (HUMAN GATE) and
verified with `alembic current`.
```bash
gh run list --branch main --limit 5
gh run watch <run-id>            # wait for the deploy to finish
```

### 4. SHA verification (via ib-staging-validator)
```bash
curl -s https://ib-stg-api.azurewebsites.net/health   | grep -o '"commit_sha":"[^"]*"'
curl -s https://ib-stg-web.azurewebsites.net/api/version | grep -o '"commit_sha":"[^"]*"'
```
Poll until 3 consecutive `/health` responses match the merge SHA (stale worker
window ~40s).

### 5. Staging validations A–I
Run the phase-specific HTTP checks from the validation plan. Confirm:
- migration state (head = 011 unless the phase added one),
- `AUTH_TEST_MODE` absent (protected route challenges, not bypassed),
- final flag state (`LLM_COUNCIL_ENABLED`, `LLM_DISCOVERY_COUNCIL_ENABLED`,
  `SOURCE_CONNECTOR_ENABLED`) by observed behavior,
- no recommendation/valuation language, publication stays admin-gated.

### 6. Logs / no-secrets
```bash
# tail recent app logs (human-run az if needed), then:
grep -aiE 'api_token=|AUTH_SECRET|BEARER |AZURE_OPENAI_API_KEY|EODHD_API_KEY' <log> || echo "no secret patterns"
```
Cite + redact any finding; never paste a secret value.

### 7. Closure report
Fill `docs/development/templates/closure_report.md`, update `PHASE_LEDGER.md`,
and (via ib-docs-agent) mark ROADMAP status COMPLETE — only now.

## Failure handling
- SHA never converges → NOT VALIDATED; escalate; do not mark closed.
- Any log secret leak → BLOCK closure; escalate.
- Unexpected migration → STOP; escalate.
- A flag must be flipped to test → STOP; that is a human app-setting action.

## Gates (all require explicit human approval)
- before merge · before any Azure app-setting change · before any deploy change ·
  before marking the phase CLOSED.
