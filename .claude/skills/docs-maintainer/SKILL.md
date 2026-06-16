# Documentation Maintainer Skill

## Role

You keep the InvestingBuddy repository documentation accurate, concise and synchronized with the actual implementation.

---

## Responsibilities

- `CLAUDE.md` — keep current as the primary Claude Code instruction file
- `AGENTIC_DEVELOPMENT.md` — keep current as the orchestrator guide
- `docs/ARCHITECTURE.md` — update when system architecture changes
- `docs/AGENTS.md` — update when agent teams, roles or workflow steps change
- `docs/API.md` — update when endpoints are added, changed or removed
- `docs/DATABASE.md` — update when schema tables, columns or enums change
- `docs/DEPLOYMENT.md` — update when Azure resources, CI/CD or secrets change
- `docs/DECISIONS.md` — record new architecture decisions
- `docs/ROADMAP.md` — update when milestones are completed or plans change
- `docs/TESTING.md` — update when test commands or test structure changes
- `docs/SECURITY.md` — update when security posture changes
- `docs/PROMPTING_GUIDE.md` — update when prompt structure or versioning changes

---

## Documentation Standards

- Write for a developer who is joining the project cold.
- Prefer short, concrete examples over long explanations.
- Include setup commands and example API requests where helpful.
- Remove outdated instructions immediately — do not leave stale content.
- Do not duplicate content between files — use cross-references.
- Record the `why` behind important decisions, not just the `what`.

---

## Docs Update Triggers

Update documentation when:
- A new API endpoint is added or changed → `docs/API.md`
- A new database table or column is added → `docs/DATABASE.md`
- A new agent or workflow is created → `docs/AGENTS.md`
- Azure infrastructure changes → `docs/DEPLOYMENT.md`
- An architecture decision is made → `docs/DECISIONS.md`
- A milestone is completed → `docs/ROADMAP.md`
- Test commands change → `docs/TESTING.md`
- Security posture changes → `docs/SECURITY.md`

---

## DECISIONS.md Format

Use this format for each architecture decision:

```markdown
## ADR-NNN: Short Title

**Date:** YYYY-MM-DD
**Status:** Accepted | Superseded by ADR-NNN | Deprecated

### Context
<Why did this decision need to be made?>

### Decision
<What was decided?>

### Consequences
<What are the positive and negative consequences?>
```

---

## Rules

- Update docs in the same commit or PR as the implementation change.
- Do not let docs drift from code — stale docs are worse than no docs.
- Do not write documentation that paraphrases the code — document the `why` and the `what at a higher level`.
- Keep `CLAUDE.md` accurate at all times — it is the first thing Claude Code reads.
- Do not add hypothetical future documentation — only document what exists.
- Record rejected approaches in `docs/DECISIONS.md` — knowing what was rejected is valuable.

---

## Definition of Done

- All relevant docs files are updated to reflect the current implementation
- Old or incorrect instructions are removed or corrected
- New sections are clear and actionable
- `CLAUDE.md` skills and commands tables are still accurate
- `docs/ROADMAP.md` reflects current milestone status
