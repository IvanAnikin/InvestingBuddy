# Command: run-checks

Run the full local check suite and report results. Block commits if anything is red.

## Steps

1. Run backend checks in `apps/api/`:
   - `pytest tests/ -q` (all tests)
   - `ruff check .` (lint)
2. Run frontend checks in `apps/web/`:
   - `npm run typecheck`
   - `npm run lint`
   - `npm run build`
3. Report a clear PASS / FAIL summary per check.
4. If any check fails, do not proceed to commit — fix the issue first.

## Skill to Use

Delegate to `.claude/skills/ci-test-runner/SKILL.md` for exact commands and result interpretation.

## Definition of Done

- All pytest tests pass
- Ruff reports "All checks passed!"
- TypeScript type check exits 0
- ESLint exits 0
- Next.js build completes without errors
- Final output: `=== ALL CHECKS PASSED ===`
