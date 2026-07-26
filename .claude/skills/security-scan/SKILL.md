---
name: security-scan
description: >-
  Runs the InvestingBuddy safety/secrets scan over a diff or branch. Use when
  asked to "check for secrets", "run the security scan", or before any PR/merge.
  Greps for committed secrets, app-setting values, tokenized URLs, SSRF fetchers,
  auth bypass, publish routes, recommendation/valuation language, and
  prompt/completion/report-body logging. Reports locations with values redacted.
---

# Security Scan Skill

Fast, repeatable safety gate. Report locations, never values.

## Activation cues
- "Security scan", "check for secrets", "is this safe to PR/merge?".

## Scan commands
Run from repo root; scope to the diff when possible (`git diff --name-only main`).

### 1. Secrets / app-setting values (must be CLEAN)
```bash
git diff main -- . | grep -nE 'AUTH_SECRET|AUTH_GITHUB_SECRET|BACKEND_BASIC_AUTH|DATABASE_URL|AZURE_OPENAI_API_KEY|OPENAI_API_KEY|EODHD_API_KEY' || echo "no secret keys in diff"
# a KEY name assigned a real value is a finding; empty/placeholder in .env.example is OK
grep -REn '(AZURE_OPENAI_API_KEY|OPENAI_API_KEY|EODHD_API_KEY|DATABASE_URL)=[^"'\'' ]*[0-9A-Za-z]{6}' --include='*.py' --include='*.ts' --include='*.md' . | grep -v '.env.example' || echo "no assigned secret values"
```

### 2. Tokenized URLs / auth headers (must be CLEAN)
```bash
grep -REn 'api_token=|Authorization: *Bearer|Cookie:|Bearer [A-Za-z0-9._-]{8}' --include='*.py' --include='*.ts' . | grep -vi 'redact' || echo "no tokenized urls"
```

### 3. Arbitrary fetch / SSRF (must be CLEAN)
```bash
grep -REn '(requests|httpx)\.(get|post)\(' apps/api/app | grep -iE 'url|endpoint' | grep -viE 'allowlist|verified|safe_web_fetcher' || echo "no unguarded fetchers"
```

### 4. Auth bypass / test mode (must be CLEAN for prod)
```bash
grep -REn 'AUTH_TEST_MODE' apps/api/app | grep -viE 'gate|404|not_found|disabled|prod' || echo "no test-mode enablement"
```

### 5. Publish route (must be CLEAN)
```bash
grep -REn '@(app|router)\.(post|put)\([^)]*publish' apps/api/app || echo "no publish route"
```

### 6. Recommendation / valuation language (must be CLEAN in product output)
```bash
grep -REn '\b(BUY|SELL|HOLD|WATCH)\b|price target|fair value|intrinsic value|upside|downside' apps/api/app apps/web/app apps/web/src 2>/dev/null | grep -viE 'reject|test|comment|# |// ' || echo "no recommendation/valuation language"
```
(Manually confirm any hit is a negated disclaimer or internal-admin-only, not
public product output.)

### 7. Prompt / completion / report-body logging (must be CLEAN)
```bash
grep -REn 'log(ger)?\.(info|debug|warning)\(.*(prompt|completion|report_body|message|content)' apps/api/app || echo "no prompt/completion/report-body logging"
```

## Output template
```
## Security Scan
- Secrets/app-settings: CLEAN | FINDING <file:line, redacted>
- Tokenized URLs: CLEAN | FINDING
- SSRF/arbitrary fetch: CLEAN | FINDING
- Auth/AUTH_TEST_MODE: CLEAN | FINDING
- Publish route: CLEAN | FINDING
- Recommendation/valuation language: CLEAN | FINDING
- Prompt/completion/report-body logging: CLEAN | FINDING
- Verdict: PASS | BLOCK
```

## Failure handling
- Any confirmed secret/auth-bypass/SSRF → BLOCK, stop, escalate to the human.
- Known historical (already-fixed, documented) log leaks → note as historical,
  not a new finding.
- Never paste the matched secret value — cite `file:line` and redact.
