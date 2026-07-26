---
name: context-compaction
description: >-
  Compacts a long InvestingBuddy session into docs/development/session_state.md
  so work survives context limits. Use before context grows large, when a phase
  spans many steps, or before handing off. Records branch/PR/SHA/test state,
  decisions, blockers, and the exact next command — preserving evidence, not raw
  logs.
---

# Context Compaction Skill

Write durable state to disk so the session can be resumed without re-deriving
anything. Keep decisions + evidence; drop noise.

## Activation cues
- Context is getting large, a phase is long, or before a handoff/pause.
- Orchestrator says "checkpoint" / "save state".

## What to write (to docs/development/session_state.md)
Overwrite the file with the current snapshot:
```
# Session State — <phase id> (updated <fill date from `date -u`>)

## Current position
- Branch: <branch>
- Phase / subphase: <id> — <stage: implement|test|review|PR|awaiting-approval|validate|closed>
- PR: <url | none>
- Merge SHA: <sha | none>   Deploy SHA (API/Web): <sha|none> / <sha|none>

## Test / scan state
- backend <p>/<t>, ruff <clean/n>, mypy <n vs ~71 baseline>
- web typecheck/lint/build <pass/fail>, e2e <p>/<t>
- security scan: <PASS | BLOCK — item>

## Decisions made
- <bullet — the decision and the one-line why>

## Blockers / open questions
- <bullet — what is blocking and what unblocks it>

## Next exact command / action
- <the single next command or delegation to run>
```

## Rules
- Preserve DECISIONS and EVIDENCE only; never paste large raw logs — record
  counts, SHAs, verdicts, and the failing test id / first error line if relevant.
- Redact any secret; cite location only.
- Keep it short enough to re-read in one glance (aim < ~60 lines).
- Update in place each checkpoint; this file is the single source of resumable truth.

## Resume procedure
On a new session: read `docs/development/session_state.md` and
`docs/development/PHASE_LEDGER.md` first, then continue from "Next exact command".

## Failure handling
- Unsure of a value → write `UNKNOWN — <how to obtain>`; never invent a SHA/count.
