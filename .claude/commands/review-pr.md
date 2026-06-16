# Review PR Command

You are reviewing a git diff before commit or merge for InvestingBuddy.

---

## Setup

Run before reviewing:
```bash
git diff
git status
git log --oneline -10
```

Also read:
- `CLAUDE.md` (for rules)
- `docs/API.md` (if API changed)
- `docs/DATABASE.md` (if schema changed)
- `docs/AGENTS.md` (if agent workflows changed)

---

## Review Checklist

### Correctness
- [ ] Code does what the task description says
- [ ] No obvious logic errors
- [ ] Error handling is present for expected failure cases

### Scope
- [ ] No unrelated changes (refactors, style fixes, unrelated bug fixes)
- [ ] Change is PR-sized — could be reviewed in one session

### Architecture Consistency
- [ ] Follows patterns in the existing codebase
- [ ] Routes are thin — business logic in services, not route handlers
- [ ] Typed schemas used for all API inputs and outputs
- [ ] No new abstractions without clear justification

### Tests
- [ ] Tests added or updated for backend changes
- [ ] Tests cover happy path
- [ ] Tests cover at least one error case
- [ ] Tests do not require real Azure services (mocks used)

### Documentation
- [ ] `docs/API.md` updated if endpoints changed
- [ ] `docs/DATABASE.md` updated if schema changed
- [ ] `docs/AGENTS.md` updated if agent behavior changed
- [ ] `docs/DECISIONS.md` updated if an architecture decision was made

### Security
- [ ] No secrets committed
- [ ] No API keys or credentials in code
- [ ] Admin endpoints are protected
- [ ] User data is scoped to the requesting user
- [ ] External document content is treated as untrusted (prompt injection)

### Investment Domain Rules
- [ ] No financial numbers invented without source
- [ ] No investment recommendations without risk section
- [ ] All citations have source_id, URL, retrieved_at
- [ ] Rating values are only: BUY, WATCH, HOLD, SELL, REJECT

### Database Migration
- [ ] Migration file exists for schema changes
- [ ] Migration `downgrade()` is reasonable
- [ ] No manual `ALTER TABLE` applied outside Alembic

### Frontend/Backend Contract
- [ ] Frontend types match backend response schemas
- [ ] API client uses correct HTTP methods and paths
- [ ] Error responses are handled in UI

### Agent Workflow Logging
- [ ] `agent_run` record created and updated
- [ ] `agent_step` records created for each node
- [ ] `prompt_version_id` stored on each step
- [ ] Errors stored on `agent_run` record

---

## Output Format

```
## PR Review Summary

### Summary of changes
<2-3 sentences>

### Blocking Issues
- <issue> — must fix before merge

### Non-Blocking Suggestions
- <suggestion> — recommended but not blocking

### Missing Tests
- <what should be tested>

### Missing Documentation
- <what should be documented>

### Security Concerns
- <concern>

### Approval Status
APPROVED / REQUEST CHANGES / NEEDS DISCUSSION
```
