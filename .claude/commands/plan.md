# Plan Command

You are planning a development task for InvestingBuddy.

Before producing the plan, read:
- `CLAUDE.md`
- `AGENTIC_DEVELOPMENT.md`
- `docs/ARCHITECTURE.md`
- `docs/AGENTS.md` (if agents are involved)
- `docs/DATABASE.md` (if schema is involved)
- `docs/API.md` (if API is involved)
- Relevant source files in `apps/api/` or `apps/web/`

Do not write code. Return a structured plan only.

---

## Plan Output Format

```
## Goal
<one sentence>

## Affected Modules
- Backend API: yes/no — <what>
- Database: yes/no — <what>
- Agent workflows: yes/no — <what>
- Frontend: yes/no — <what>
- Azure / deployment: yes/no — <what>

## Relevant Existing Files
- <file path and why it's relevant>

## Implementation Steps
1. <step>
2. <step>
...

## Database Impact
<tables added, columns added, migrations needed>

## API Impact
<endpoints added or changed>

## Agent Workflow Impact
<workflows added or changed>

## Frontend Impact
<pages or components affected>

## Tests Required
- <test description>

## Documentation Updates Required
- <doc file> — <what to update>

## Risks
- <risk>

## Definition of Done
- [ ] <checkable condition>
- [ ] <checkable condition>
```

---

## Planning Rules

- Keep scope small. One plan should map to one PR.
- If the task spans multiple layers, split into multiple sequential plans.
- Do not plan features outside the current milestone unless explicitly asked.
- Identify whether this is Version 1 or Version 2 scope.
- Flag any dependency on infrastructure not yet built.
- Ask for clarification if the request is ambiguous before producing the plan.
