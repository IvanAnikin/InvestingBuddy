# Update Docs Command

You are updating documentation to reflect recent implementation changes in InvestingBuddy.

---

## Pre-Update Checklist

1. Run `git diff` to identify what changed in the implementation
2. Identify which documentation files need updating
3. Read the current version of each affected doc before editing
4. Do not rewrite docs that are still accurate — only update what changed

---

## Documentation File Map

| Changed area | Update this doc |
|---|---|
| New or changed API endpoint | `docs/API.md` |
| New or changed database table or column | `docs/DATABASE.md` |
| New or changed agent, workflow or prompt | `docs/AGENTS.md` + `docs/PROMPTING_GUIDE.md` |
| New Azure resource or CI/CD change | `docs/DEPLOYMENT.md` |
| Architecture decision made | `docs/DECISIONS.md` |
| Milestone completed or plan changed | `docs/ROADMAP.md` |
| Test command or structure changed | `docs/TESTING.md` |
| Security posture changed | `docs/SECURITY.md` |
| Available skills or commands changed | `CLAUDE.md` (skills/commands tables) |
| Agent orchestration pattern changed | `AGENTIC_DEVELOPMENT.md` |

---

## Documentation Standards

- Write for a developer joining the project cold.
- Use concrete examples, not abstract descriptions.
- Include commands where helpful (e.g., `alembic upgrade head`, `pytest`).
- Remove stale content — do not leave outdated instructions alongside new ones.
- Do not duplicate content between files — cross-reference instead.
- Record the `why` behind decisions, not just the `what`.
- Do not write hypothetical future documentation for unbuilt features.

---

## DECISIONS.md Format

For new architecture decisions:

```markdown
## ADR-NNN: Short Title

**Date:** YYYY-MM-DD
**Status:** Accepted

### Context
<Why did this decision need to be made?>

### Decision
<What was decided?>

### Consequences
<Positive and negative consequences>
```

Number ADRs sequentially. If a decision supersedes an older one, update the older one's status.

---

## Rules

- Update docs in the same commit as the implementation — never as a follow-up.
- Do not add documentation for things that do not exist yet.
- Do not leave TODO comments in documentation — resolve them or remove them.
- Keep `CLAUDE.md` tables accurate — they are read by every new Claude Code session.
- Keep `docs/ROADMAP.md` milestone status current — mark phases as complete when done.

---

## Output Format

```
## Documentation Update Summary

### Files updated
- <file> — <what was changed and why>

### Decisions recorded
- ADR-NNN: <title>

### Stale content removed
- <file> — <what was removed>

### Known remaining gaps
- <doc> — <what still needs updating>
```
