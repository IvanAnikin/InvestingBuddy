# IB Validate PR Command

Run the pre-PR / pre-merge review for the current change.

## Steps
1. Invoke `ib-pr-review-agent`:
   ```bash
   git status && git diff --stat && git diff && git log --oneline -10
   ```
2. Confirm: scope is PR-sized · tests present (happy + ≥1 error) · docs updated ·
   migration present if schema changed · security scan passed (`ib-security-agent`
   / `security-scan`) · staging validation plan drafted.
3. Output an approval status: APPROVED / REQUEST CHANGES / NEEDS DISCUSSION.

## Guardrails
- Do not merge or push — this command only reviews.
- Never mark APPROVED with red tests, a failed security scan, or a missing
  migration for a schema change.
- Redact any secret found in the diff; cite `file:line` only.
