---
name: phase-implementation
description: >-
  Guides implementation of a named InvestingBuddy phase end-to-end: inspect
  baseline, branch, implement a bounded change, test, update docs, open a PR,
  and produce an implementation report. Use when asked to "implement Phase NN",
  "start the next phase", or "build <bounded change> for InvestingBuddy". Stops
  at the PR gate — never merges or deploys.
---

# Phase Implementation Skill

Drives one phase (or subphase) from spec to a review-ready PR. One phase at a
time. Stops before merge/deploy.

## Activation cues
- "Implement Phase 29B.2 …", "start the next phase", "build the X connector".
- Orchestrator hands off a bounded implementation task.

## Workflow

### 1. Inspect baseline (read, don't change)
```bash
git fetch --all --tags --prune
git checkout main && git pull origin main
git status && git log --oneline -8
```
Read the phase spec, `CLAUDE.md`, `docs/development/PHASE_LEDGER.md`, and the
in-scope files. Delegate broad reads to the `Explore` agent to save context.

### 2. Branch
```bash
git checkout -b <type>/phase-<id>-<slug>     # e.g. feature/phase-29b2-document-extraction
```
If there is uncommitted WIP, stash it first (`git stash push -u -m "…"`) and note it.

### 3. Implement (via ib-implementation-agent)
- Minimal coherent change; logic in services; typed schemas; match patterns.
- Preserve invariants (secrets, auth, SSRF, no recommendation/valuation, logging).
- Alembic migration for any schema change.

### 4. Test (via ib-test-agent)
```bash
cd apps/api && pytest tests/ -v && ruff check .        # mypy: compare to ~71 baseline
cd apps/web && npm run typecheck && npm run lint && npm run build
cd apps/web && npm run test:e2e                         # add --workers=1 if auth-flaky
```
Record exact counts; distinguish new failures from baseline.

### 5. Security scan (via ib-security-agent / `security-scan` skill)
Confirm PASS before the PR.

### 6. Docs (via ib-docs-agent)
Update API.md / ARCHITECTURE.md / ROADMAP.md / DEPLOYMENT.md / `.env.example`
as relevant. Do NOT mark the phase closed yet.

### 7. Open PR (no merge)
```bash
git push -u origin <branch>
gh pr create --title "Phase <id>: <title>" --body "<from implementation report template>"
```

### 8. Implementation report
Fill `docs/development/templates/implementation_report.md` and hand back.

## Output template
Use `docs/development/templates/implementation_report.md`:
branch · files changed · migration y/n · architecture · API/UI changes · tests ·
PR URL · limitations · ready for review y/n.

## Failure handling
- Tests red → back to `ib-implementation-agent`; do not open the PR.
- Scope grows beyond the task → STOP, split into a follow-up subphase.
- Security BLOCK → fix and re-scan before the PR.
- Two failed attempts on the same step → stop and report the blocker + exact
  failing command; do not loop.

## Gate
STOP at the open-PR step. Do not merge or deploy — that is the
`staging-validation` skill, and only after explicit human approval.
