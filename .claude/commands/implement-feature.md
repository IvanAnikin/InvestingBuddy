# Implement Feature Command

You are implementing one focused feature for InvestingBuddy.

---

## Pre-Implementation Checklist

1. Read `CLAUDE.md` and `AGENTIC_DEVELOPMENT.md`
2. Restate the feature in one sentence to confirm understanding
3. Identify the affected skill(s): backend, frontend, database, agents, etc.
4. Inspect existing files in the affected area before writing any code
5. Confirm the plan is approved or that no plan is needed for this scope

---

## Implementation Process

1. **Restate** the feature clearly
2. **Inspect** relevant existing files
3. **Implement** the minimal required change:
   - Backend: service → route → schema → tests
   - Frontend: component → page → API client → loading/error states
   - Database: model → migration → docs
   - Agent: state → nodes → edges → persistence → smoke test
4. **Run** available checks:
   - Backend: `pytest`, `ruff check .`, `mypy .`
   - Frontend: `npm run typecheck`, `npm run lint`, `npm run build`
5. **Update** documentation if API, schema, agent behavior or architecture changed
6. **Summarize** what was implemented

---

## Rules During Implementation

- Keep changes focused. Do not refactor unrelated code.
- Do not introduce unrelated abstractions or cleanup.
- Do not hardcode secrets, API keys or Azure credentials.
- Do not invent financial numbers or investment claims.
- Add tests alongside features — not as a follow-up task.
- Update docs in the same change — not as a follow-up task.
- If you discover a bug unrelated to this task, note it but do not fix it here.

---

## Output Format

At the end, provide:

```
## Implementation Summary

### Feature implemented
<one sentence>

### Files changed
- <file> — <what changed>

### Tests added/updated
- <test file> — <what is tested>

### Documentation updated
- <doc file> — <what was updated>

### Checks run
- pytest: passed/failed
- ruff: passed/failed
- mypy: passed/failed
- typecheck: passed/failed (frontend)
- build: passed/failed (frontend)

### Manual verification steps
1. <step>
2. <step>

### Risks or open issues
- <risk or issue>

### Recommended next step
<one sentence>
```
