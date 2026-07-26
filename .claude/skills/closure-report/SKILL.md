---
name: closure-report
description: >-
  Produces InvestingBuddy implementation reports (pre-PR) and closure reports
  (post-validation) from consistent templates. Use when asked for an
  implementation report, a closure report, or a phase summary. Enforces
  evidence-first reporting — no "done" claim without test counts, SHAs, and
  validation results.
---

# Closure Report Skill

Two standard reports. Never claim a phase closed without the evidence fields
filled from real results.

## Activation cues
- "Write the implementation report", "give me the closure report", "summarize the
  phase for the PR / for closure".

## Implementation report (use at the PR gate)
Template: `docs/development/templates/implementation_report.md`
```
## Implementation Report — Phase <id>: <title>
- Branch: <branch>
- Files changed: <list>
- Migration: <yes (rev id) | no>
- Architecture: <what changed, 2–4 lines>
- API changes: <endpoints/schemas | none>
- UI changes: <pages/components | none>
- Tests: backend <p>/<t>, ruff <clean/n>, mypy <n vs baseline>, web
  typecheck/lint/build <pass/fail>, e2e <p>/<t>
- PR URL: <url>
- Limitations: <honest list of what is not done / scaffolded>
- Ready for review: <yes/no>
```

## Closure report (use only after staging validation)
Template: `docs/development/templates/closure_report.md`
```
## Closure Report — Phase <id>: <title>
- Merge SHA: <sha>
- API SHA (/health): <sha>
- Web SHA (/api/version): <sha>
- Migration: <none — head 011 | applied rev X>
- AUTH_TEST_MODE: <absent — confirmed>
- Validation A–I: <A pass … I pass, or note failures>
- Logs / no-secrets: <CLEAN | finding (redacted)>
- Safety / publication: <no recommendation/valuation; publication admin-gated>
- Final flags: LLM_COUNCIL=<on/off> DISCOVERY=<on/off> SOURCE_CONNECTOR=<on/off>
- Limitations: <honest list>
- Final verdict: <CLOSED + validated | NOT CLOSED (why)>
```

## Rules
- Every numeric/claim field must come from an actual run — never estimate.
- If a field is unknown, write `UNKNOWN — <how to obtain>`, not a guess.
- Redact any secret; cite location only.
- Do not print a CLOSED verdict unless SHAs + validation are on file.

## Failure handling
- Missing evidence for a field → mark UNKNOWN and list the exact command to get it.
- Validation failed → verdict NOT CLOSED with the specific failing item.
