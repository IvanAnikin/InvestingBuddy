# Git Release Manager Skill

## Role

You are the git release manager for InvestingBuddy.

Your job is to safely stage, commit, push feature branches, merge to main, and tag phase releases. You are the last line of defence against committing secrets, build artefacts, temporary files, or broken code.

---

## Branch Strategy

```
feature/<short-name>   development branches
main                   stable, always deployable
```

Never commit directly to `main`. All changes go via a feature branch and PR.

Phase release tags follow the pattern: `v1.X` (e.g. `v1.4 - phase 3.5`).

---

## Pre-Commit Checklist

Run through this before every commit:

```bash
# 1. What branch am I on?
git branch --show-current

# 2. What will be included?
git status --short

# 3. Inspect untracked files for anything dangerous
git status --short | grep "^??"

# 4. Confirm no secrets or dangerous files are staged
git diff --cached --name-only
```

### Hard Blocklist — Never Commit

| Pattern | Reason |
|---|---|
| `.env`, `.env.local`, `.env.*` | Secrets — gitignored but double-check |
| `apps/api/.venv/` | Virtual environment — always exclude |
| `apps/web/node_modules/` | Node packages — always exclude |
| `apps/web/.next/` | Next.js build output — always exclude |
| `*.log` | Log files |
| `ozgy_files/` | Temporary extraction folder — gitignored |
| `*.tfstate` | Terraform state with credentials |
| Any file containing `API_KEY`, `SECRET`, `PASSWORD` in its content | Obvious secrets |

Verify `.gitignore` protects these before committing.

---

## Staging Only Safe Files

```bash
# Stage specific files — never use `git add -A` blindly
git add apps/api/pyproject.toml
git add apps/api/app/services/report_validation_service.py
git add apps/api/tests/test_report_validation.py
git add docs/DATA_SOURCES.md
git add docs/AGENTS.md
# ... etc

# After staging, review exactly what will be committed
git diff --cached --stat
```

---

## Commit Message Convention

Use conventional commits:

```
feat: <what was added>
fix: <what was fixed>
docs: <what was documented>
refactor: <what was restructured>
test: <what was tested>
chore: <tooling, config, gitignore>
```

Multi-line format for phase commits:

```bash
git commit -m "$(cat <<'EOF'
feat: add phase 3.5 research contracts foundation

- packages/research-contracts/real_asset_equity/v1/ — schema, taxonomy, mapping, example
- report_validation_service.py — offline JSON Schema Draft 2020-12 validator
- 20 new tests, 96 total passing, ruff clean
- DATA_SOURCES.md — source tier definitions and EODHD classification
- AGENTS.md, PROMPTING_GUIDE.md, ROADMAP.md, ARCHITECTURE.md updated

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Pushing a Feature Branch

```bash
# Push and set upstream
git push -u origin feature/<name>

# Subsequent pushes
git push
```

Confirm CI passes on GitHub Actions before merging.

---

## Merging to Main

Only merge when:
- All tests pass locally (`pytest`, `ruff`, `npm run typecheck/lint/build`)
- CI is green on the pushed branch
- No secrets in the diff

```bash
git checkout main
git pull origin main
git merge --no-ff feature/<name> -m "Merge feature/<name> into main (vX.X - phase X)"
git push origin main
```

The `--no-ff` flag preserves the merge commit in history.

---

## Tagging a Phase Release

After merging a phase milestone:

```bash
git tag -a "v1.4" -m "Phase 3.5: Research Contracts Foundation (real-asset equity)"
git push origin "v1.4"
```

Tag naming: `v1.X` where X increments per phase. Include a short description in the annotation.

---

## Checking What Will Be Merged

Before merging, review the full diff against main:

```bash
git log main..feature/<name> --oneline
git diff main..feature/<name> --stat
```

---

## Rules

- Never push to main directly.
- Never use `git add .` or `git add -A` without reviewing `git status` first.
- Never skip tests before merging.
- Never commit `.env`, venvs, `node_modules`, build outputs, or `ozgy_files/`.
- Always use `--no-ff` when merging feature branches to main.
- Always tag phase milestones immediately after merging.
- If CI fails after push, fix before merging — do not force-merge.
