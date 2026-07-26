# Closure Report — Phase <id>: <title>

> Produced ONLY after merge + deploy + staging validation. Do not print a CLOSED
> verdict unless the SHAs and validation results below are real.

- **Merge SHA:** `<sha>`
- **API SHA** (`GET /health` `commit_sha`): `<sha>` — matches merge SHA? <yes/no>
- **Web SHA** (`GET /api/version` `commit_sha`): `<sha>` — matches? <yes/no>
- **Migration:** <none — DB head `011` | applied rev `<id>` — expected>
- **AUTH_TEST_MODE:** <absent — confirmed (protected route challenges)>
- **Validation A–I:**
  - A — API SHA converged (3 consecutive matches): <pass/fail>
  - B — Web SHA matches: <pass/fail>
  - C — migration state as expected: <pass/fail>
  - D — AUTH_TEST_MODE absent: <pass/fail>
  - E — phase-specific HTTP check 1: <pass/fail>
  - F — phase-specific HTTP check 2: <pass/fail>
  - G — final flag state confirmed by behavior: <pass/fail>
  - H — logs / no-secrets: <pass/fail>
  - I — safety/publication (no recommendation/valuation; admin-gated): <pass/fail>
- **Logs / no-secrets:** <CLEAN | finding — redacted location>
- **Safety / publication:** <no recommendation/valuation language; publication admin-gated>
- **Final flags:** `LLM_COUNCIL_ENABLED`=<on/off> · `LLM_DISCOVERY_COUNCIL_ENABLED`=<on/off> · `SOURCE_CONNECTOR_ENABLED`=<on/off>
- **Limitations:** <honest list of what remains / is scaffolded>
- **Final verdict:** <CLOSED + validated | NOT CLOSED — reason>

After a CLOSED verdict: update `docs/development/PHASE_LEDGER.md` and
`docs/ROADMAP.md` status.
