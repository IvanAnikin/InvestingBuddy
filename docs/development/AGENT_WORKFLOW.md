# InvestingBuddy — Claude Code Development Agent Workflow

This is the developer-workflow system for implementing InvestingBuddy phases with
Claude Code, with much less copy/paste. It is **tooling only** — it does not
change app runtime behavior, auth, Azure settings, or product features.

> Product agent architecture (the LLM council) lives in `docs/AGENTS.md`. This
> file is about the *development* agents that help ship phases.

---

## The phase workflow this encodes

```
1. understand phase spec
2. inspect code (baseline)
3. implement bounded change
4. run tests
5. update docs
6. open PR
7. wait for review                 ← human
8. merge / deploy only when approved ← human gate
9. validate staging
10. return closure report
11. update roadmap
```

The orchestrator runs **one phase or subphase at a time** and stops at every
human gate.

---

## Agents (`.claude/agents/`)

| Agent | Use for |
|---|---|
| `ib-orchestrator` | Session controller: decompose spec, delegate, track ledger, enforce gates |
| `ib-implementation-agent` | Implement one bounded backend/frontend change + tests |
| `ib-test-agent` | Run & interpret pytest/ruff/mypy/typecheck/lint/build/e2e; exact counts |
| `ib-security-agent` | Safety/secrets/SSRF/auth/publish/recommendation-language scan |
| `ib-staging-validator` | Post-approval: verify SHAs, migration, flags, logs; closure evidence |
| `ib-docs-agent` | Update API/ARCHITECTURE/ROADMAP/DEPLOYMENT/.env.example honestly |
| `ib-pr-review-agent` | Pre-PR / pre-merge diff + risk review, go/no-go |
| `ib-roadmap-agent` | Plan & sequence next phases (evidence-first) |

Invoke a subagent with the Agent tool (`subagent_type: "ib-…"`), or let the
orchestrator route to it.

## Skills (`.claude/skills/`)

| Skill | Triggers on |
|---|---|
| `phase-implementation` | "implement Phase NN", "start the next phase" |
| `staging-validation` | "merge / deploy / validate this PR" |
| `closure-report` | "write the implementation / closure report" |
| `security-scan` | "check for secrets", "run the security scan" |
| `roadmap-planning` | "what's next", "sequence the phases" |
| `context-compaction` | long session / before handoff — checkpoint state |

## Slash commands (`.claude/commands/`)

| Command | Does |
|---|---|
| `/ib-next-phase` | Kick off the next phase via `phase-implementation` |
| `/ib-validate-pr` | Pre-PR/pre-merge review via `ib-pr-review-agent` |
| `/ib-close-phase` | Run `staging-validation` → closure report (after approval) |
| `/ib-security-scan` | Run the `security-scan` skill on the current diff |

---

## Human gates (never crossed autonomously)

The orchestrator STOPS and asks the human before:
- merging any PR,
- any Azure **app-setting** change,
- any Azure **deploy** change,
- marking a phase **CLOSED**,
- retrying after **repeated** (2+) tool/test/classifier failures.

Merges and deploys are approved and performed with the human in the loop.

## Evidence required before claiming progress

| Claim | Evidence |
|---|---|
| "implemented" | files changed + tests added |
| "tests pass" | exact counts (backend p/t, ruff, mypy vs baseline, web, e2e) |
| "PR open" | PR URL |
| "merged" | merge SHA |
| "deployed" | deployed API + Web SHA from `/health` and `/api/version` |
| "validated" | staging validation A–I result |
| "no secrets" | logs/no-secrets scan result (redacted) |
| "closed" | all of the above on file |

Never claim done without the evidence. If unknown, say `UNKNOWN — <how to get it>`.

## Context-overflow strategy

- Orchestrator keeps only decisions + evidence; detail goes to
  `docs/development/session_state.md` (via `context-compaction`).
- Delegate broad reads to the `Explore` agent; give subagents narrow tasks.
- Never paste huge raw logs — record counts, verdicts, and the first failing line.
- Resume a session by reading `session_state.md` + `PHASE_LEDGER.md` first.

---

## Safety invariants (preserved by every agent — do not weaken)

These mirror `CLAUDE.md` and the product's evidence-first posture:

- **Secrets** — never print or commit secrets; never echo app-setting VALUES
  (`AUTH_SECRET`, `AUTH_GITHUB_SECRET`, `BACKEND_BASIC_AUTH`, `DATABASE_URL`,
  `AZURE_OPENAI_API_KEY`, `OPENAI_API_KEY`, `EODHD_API_KEY`); never paste
  tokenized URLs (`api_token=`, Authorization/Cookie/Bearer). `.env.example`
  holds KEYS with empty/placeholder values only.
- **Auth** — never enable `AUTH_TEST_MODE` (404 in prod); never bypass auth;
  admin routes stay admin-gated, never public.
- **Network** — no arbitrary-URL fetcher / no SSRF; fetches stay on
  verified/allowlisted hosts (`SOURCE_CONNECTOR_ALLOWLIST_ONLY=true`).
- **Publishing** — no public publish route; publication is admin-gated and
  human-approved.
- **No recommendations / valuations in product output** — no BUY/SELL/HOLD/WATCH
  labels, no price target / fair value / intrinsic value / upside/downside. (The
  safety gate scans literal substrings; disclaimers must not enumerate these
  except in a negated warning.)
- **Citations** — every financial claim needs source + date + currency +
  retrieval timestamp; never invent numbers.
- **Logging** — never log prompts, completions, or report bodies; keep redaction
  filters intact.
- **Data separation** — public research must never leak personalized/private data.
- **Change hygiene** — small PR-sized changes; tests + docs with code; an Alembic
  migration for every schema change; log agent runs/steps.

This tooling phase itself made **no app runtime changes and no migration**.
