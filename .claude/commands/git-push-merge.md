# Command: git-push-merge

Push a feature branch, merge to main, and tag the release.

## Pre-Conditions

- All checks pass locally.
- A clean commit exists on the feature branch.
- You are on a `feature/*` branch, not `main`.

## Steps

1. Confirm current branch: `git branch --show-current`
2. Push with upstream: `git push -u origin <branch-name>`
3. Wait for CI to pass on GitHub Actions (confirm green before merging).
4. Switch to main and pull latest: `git checkout main && git pull origin main`
5. Merge with no-fast-forward: `git merge --no-ff <branch-name> -m "Merge <branch-name> into main (vX.X - phase X)"`
6. Push main: `git push origin main`
7. Tag the phase release: `git tag -a "vX.X" -m "Phase X.X: <description>"`
8. Push tag: `git push origin "vX.X"`

## Skill to Use

Delegate to `.claude/skills/git-release-manager/SKILL.md` for the exact merge and tag format.

## Definition of Done

- Feature branch is on GitHub
- `main` branch includes the merge commit
- Phase tag `vX.X` is visible on GitHub
- CI is green on `main`
