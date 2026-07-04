# Command: git-safe-commit

Safely stage and commit changes with a conventional commit message.

## Pre-Conditions

- All checks must pass (`run-checks` command).
- No secrets, `.env` files, or build artefacts in the diff.

## Steps

1. Run `git status --short` to see all changed files.
2. Inspect untracked files (`??`) for anything dangerous: `.env`, `.venv/`, `node_modules/`, `.next/`, `ozgy_files/`.
3. Stage only safe files explicitly (never `git add .` without reviewing first).
4. Run `git diff --cached --stat` to confirm the staging area.
5. Write a conventional commit message:
   - Prefix: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
   - Body: bullet list of main changes
   - Footer: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
6. Commit with `git commit -m "$(cat <<'EOF' ... EOF)"` (heredoc form).
7. Confirm commit hash appears in `git log --oneline -1`.

## Skill to Use

Delegate to `.claude/skills/git-release-manager/SKILL.md` for the full hard blocklist and commit format.

## Definition of Done

- `git log --oneline -1` shows the new commit
- No secrets in `git show HEAD`
- No `.env`, venv, or build artefacts in the commit
