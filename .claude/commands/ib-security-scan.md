# IB Security Scan Command

Run the InvestingBuddy safety/secrets scan on the current diff or branch.

## Steps
1. Invoke the `security-scan` skill (or `ib-security-agent`).
2. Scope to the diff when possible: `git diff --name-only main`.
3. Check each category and report CLEAN / FINDING:
   - secrets & app-setting values (`AUTH_SECRET`, `AUTH_GITHUB_SECRET`,
     `BACKEND_BASIC_AUTH`, `DATABASE_URL`, `AZURE_OPENAI_API_KEY`,
     `OPENAI_API_KEY`, `EODHD_API_KEY`)
   - tokenized URLs (`api_token=`, Authorization/Cookie/Bearer)
   - arbitrary-URL fetcher / SSRF
   - auth bypass / `AUTH_TEST_MODE`
   - public publish route
   - recommendation/valuation language (BUY/SELL/HOLD/WATCH, price target, fair
     value, intrinsic value, upside/downside) in product output
   - prompt/completion/report-body logging
4. Output verdict: PASS or BLOCK (with blocking items).

## Guardrails
- Never paste a matched secret value — cite `file:line` and redact.
- Any confirmed secret / auth-bypass / SSRF is a BLOCK — stop and escalate.
- Note known historical (already-fixed) log leaks as historical, not new.
