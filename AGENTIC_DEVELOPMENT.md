# AGENTIC_DEVELOPMENT.md

## Purpose

This file defines how Claude Code should operate as the development orchestrator for InvestingBuddy.

The repository is designed for agentic development: Claude Code acts as an orchestrator, routes work to specialist skills, keeps changes small, and maintains documentation alongside implementation.

For a detailed agentic setup reference see `Implementation_docs/AGENTIC_DEVELOPMENT_SETUP.md`.

---

## Development Philosophy

- The orchestrator understands the full system but delegates narrow tasks to specialist skills.
- Every task should be completable as a small, reviewable PR-sized change.
- Documentation is written together with code — never after.
- Tests are written together with features — never after.
- Financial claims, agent outputs and investment logic must be auditable.
- Architecture decisions are recorded in `docs/DECISIONS.md`.

---

## Orchestrator Workflow

For every non-trivial task:

```
1. Read CLAUDE.md
2. Read relevant docs (ARCHITECTURE, AGENTS, DATABASE, API as needed)
3. Inspect affected source files
4. Identify the specialist skill to use
5. Prepare a context package (Task / Relevant files / Constraints / Expected output / Definition of done)
6. Implement or delegate the focused change
7. Run available checks
8. Review the diff
9. Update documentation
10. Summarize result and next step
```

---

## Context Package Format

Use this format when preparing work for a specialist role:

```
Task:
<clear statement of what to build or change>

Relevant docs:
- docs/...
- TECH_SPEC.md sections ...

Relevant files:
- apps/api/app/...
- apps/web/...

Current constraints:
- <specific rules for this task>

Expected output:
- <list of deliverables>

Definition of done:
- <checkable conditions>
```

---

## Routing Decision Table

| Affected area | Specialist skill |
|---|---|
| API routes, services, Pydantic schemas | `backend-fastapi` |
| SQLAlchemy models, Alembic migrations | `database-design` |
| LangGraph workflows, agent nodes, prompts | `langgraph-agents` |
| Next.js pages, React components, UI | `frontend-nextjs` |
| Azure resources, GitHub Actions, CI/CD | `azure-deployment` |
| Investment logic, rating rules, risk checklists | `investment-domain` |
| Financial data APIs, source normalization | `financial-data` |
| Tests, pytest, CI | `testing-qa` |
| Auth, secrets, prompt injection, permissions | `security-review` |
| README, docs/, DECISIONS | `docs-maintainer` |
| Feature scoping, milestones, roadmap | `product-architect` |
| Starting / verifying the local dev stack | `local-dev-operator` |
| Committing, pushing, merging, tagging releases | `git-release-manager` |
| Running tests + lint before a commit | `ci-test-runner` |
| Diagnosing broken local environment | `dev-environment-troubleshooter` |
| Manual UI/API smoke testing | `manual-qa` |

When a task spans multiple areas, break it into sequential sub-tasks and route each separately.

---

## PR Size Rule

One PR should contain one focused change. Do not combine:
- Backend + frontend + migration + agent workflow changes
- Feature implementation + refactoring + docs rewrite

When in doubt, split into smaller PRs.

---

## Agent Safety Rules

Claude Code agents operating on this repository must not:

- Delete files without explicit instruction
- Modify deployment configuration without review
- Change production prompts without versioning them first
- Create investment claims without citations
- Invent financial metrics or numbers
- Implement automatic trading or broker connection
- Store private user portfolio data in public report tables
- Expose admin API routes publicly
- Commit secrets or credentials
- Merge large unrelated refactors into feature work

---

## Definition of Done: Agentic Infrastructure

Phase 0 (this phase) is complete when:

- [x] `CLAUDE.md` exists and is accurate
- [x] `AGENTIC_DEVELOPMENT.md` exists
- [x] All specialist skill files exist under `.claude/skills/`
- [x] All command templates exist under `.claude/commands/`
- [x] All placeholder docs exist under `docs/`
- [x] Claude Code can read the repo and understand how to work
- [x] Orchestrator skill can route tasks to specialist skills
- [x] First feature can be planned using command templates

---

## Next Milestone: Phase 1 — Application Skeleton

After agentic infrastructure is in place, the next milestone is:

```
apps/api/     FastAPI skeleton with health endpoint
apps/web/     Next.js skeleton with homepage
docker-compose.yml   Local PostgreSQL
.env.example  All required environment variables
Basic CI workflow (GitHub Actions)
```

Use the `plan` command to produce the implementation plan before coding.
Use the `implement-feature` command when implementing.

See `docs/ROADMAP.md` for the full phase breakdown.
