# IB Next Phase Command

Kick off the next InvestingBuddy phase (or a named one) through the standard
development workflow. Run **one phase or subphase at a time**.

## Steps
1. Read `docs/development/PHASE_LEDGER.md` and `docs/development/AGENT_WORKFLOW.md`.
2. If no phase was named, propose the next one from the ledger (via
   `ib-roadmap-agent` / `roadmap-planning`) and confirm scope with the human.
3. Invoke the `phase-implementation` skill to: inspect baseline → branch →
   implement (`ib-implementation-agent`) → test (`ib-test-agent`) → security scan
   (`ib-security-agent`) → docs (`ib-docs-agent`) → open PR.
4. Produce an implementation report
   (`docs/development/templates/implementation_report.md`).

## Guardrails
- STOP at the open-PR step. Do not merge or deploy.
- Preserve every safety invariant in `docs/development/AGENT_WORKFLOW.md`.
- Never claim a step done without test evidence (exact counts).
- If uncommitted WIP exists, stash it and note how to restore before branching.
