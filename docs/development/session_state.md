# Session State — tooling/claude-code-agents-and-skills (updated 2026-07-26)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- Branch: `tooling/claude-code-agents-and-skills`
- Phase / subphase: tooling (dev-workflow agents & skills) — stage: PR
- PR: <fill after `gh pr create`>
- Merge SHA: none   Deploy SHA (API/Web): n/a (no app runtime change)

## Test / scan state
- App tests: n/a — tooling phase, no app code changed (docs/config only).
- Security scan: run over `.claude/` + `docs/development/` — no secrets expected.

## Decisions made
- Skills use `SKILL.md` (uppercase) + YAML frontmatter to match the 20 existing
  repo skills and the Claude Code loader (spec's lowercase `skill.md` would break
  on case-sensitive filesystems).
- Agents use required YAML frontmatter (`name`, `description`, scoped `tools`).
- Phase 29B.2 WIP was stashed (`git stash`) before branching from main; restore
  with `git checkout feature/phase-29b2-primary-document-extraction && git stash pop`.

## Blockers / open questions
- None. Awaiting human review of the tooling PR (do not merge autonomously).

## Next exact command / action
- Open the PR (no merge): `git push -u origin tooling/claude-code-agents-and-skills`
  then `gh pr create …`. Then wait for human review.
