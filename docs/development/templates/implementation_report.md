# Implementation Report — Phase <id>: <title>

> Produced at the PR gate, before merge. Every test field must come from a real
> run — never estimate. Use `UNKNOWN — <how to obtain>` for anything not observed.

- **Branch:** `<branch>`
- **Files changed:** `<path>` … (from `git diff --name-only main`)
- **Migration:** <yes — rev `<id>` | no>
- **Architecture:** <2–4 lines on what changed and why>
- **API changes:** <new/changed endpoints + schemas | none>
- **UI changes:** <pages/components | none>
- **Tests:**
  - backend `pytest tests/ -v`: `<passed>/<total>` (`<failed>` failed, `<skipped>` skipped)
  - `ruff check .`: <clean | N issues>
  - `mypy`: <N errors — baseline ~71 → no new | +K new>
  - web `npm run typecheck` / `lint` / `build`: <pass/fail each>
  - e2e `npm run test:e2e`: `<passed>/<total>` (`--workers=1` if auth-flaky)
- **Security scan:** <PASS | BLOCK — items>
- **PR URL:** <url>
- **Limitations:** <honest list of what is not done / scaffolded / deferred>
- **Ready for review:** <yes/no>

## Reviewer checklist (for the human)
- [ ] Scope is one PR-sized change
- [ ] Tests present (happy + ≥1 error), Azure/external mocked
- [ ] Docs updated (API/ARCHITECTURE/ROADMAP/DEPLOYMENT/.env.example as relevant)
- [ ] Migration present if schema changed; sensible downgrade
- [ ] No secrets / no AUTH_TEST_MODE / no public admin or publish route / no SSRF
- [ ] No recommendation/valuation language in product output
- [ ] Staging validation plan drafted
