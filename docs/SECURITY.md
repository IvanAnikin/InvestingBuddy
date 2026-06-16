# Security

## Status: Placeholder — Phase 0

This document describes the InvestingBuddy security posture.

Update this file when:
- Authentication or authorization model changes
- New secrets are added to the secrets strategy
- New prompt injection mitigations are added
- Security incidents occur or are discovered

For security review rules see `.claude/skills/security-review/SKILL.md`.

---

## Authentication

MVP authentication uses Clerk.

- JWT tokens issued by Clerk
- Backend validates tokens on every authenticated request
- Admin role verified on every admin route request

Future: Microsoft Entra External ID for deeper Azure integration.

---

## Authorization Model

| Role | Access Level |
|---|---|
| Anonymous | Public reports, company pages |
| public_user | + account creation |
| subscriber | + premium reports (future) |
| admin | + admin dashboard, workflow triggers, report publishing |
| super_admin | + prompt management, system configuration |

Rules:
- User endpoints return only the requesting user's own data.
- Admin endpoints verify admin role on every request.
- Super_admin endpoints verify super_admin role separately.
- Public endpoints never return private user data.

---

## Secrets Management

| Environment | Method |
|---|---|
| Local development | `.env` file (gitignored) |
| Repository | `.env.example` (variable names, empty values only) |
| CI/CD | GitHub Actions Secrets |
| Production | Azure Key Vault + App Service Configuration |

**Never commit:**
- `.env` files
- API keys
- Azure credentials
- Database connection strings
- JWT secrets
- Financial data API keys

Prefer managed identity over connection-string credentials where Azure services support it.

---

## Prompt Injection Risks

The platform retrieves external documents (filings, news, industry reports) and uses them as context in LLM prompts. These documents must be treated as untrusted input.

Required mitigations:
1. Sanitize retrieved text before injecting into prompts
2. Use explicit delimiters to separate document content from instructions
3. Apply content length limits on retrieved chunks
4. Log all prompt inputs for anomaly detection
5. Do not allow retrieved content to override system instructions

---

## Data Isolation

- `portfolio_positions` — never included in public API responses
- `user_preferences` — scoped to requesting user only
- `personalized recommendations` — behind authentication + user ID check
- `agent_steps.input_json` — may contain user context; not publicly accessible
- Public reports — no user-identifying information

---

## Admin API Protection

Every `/api/admin/` endpoint must:
1. Require valid authentication token
2. Verify admin or super_admin role
3. Return 403 Forbidden for insufficient role
4. Write to admin action log

---

## Current Security Status (Phase 0)

No application code exists yet. Security rules are defined for Phase 1 implementation.

Security review should be triggered before:
- Adding any authentication-related code
- Adding any admin endpoints
- Adding any user data storage
- Adding any document retrieval pipeline
- Deploying to any public environment

---

## Legal and Compliance (Future)

To investigate before public launch:
- EU investment advice classification (MiFID II)
- Czech financial regulation
- Required disclaimers for investment research
- GDPR compliance for user data
- Data retention policies
- Privacy policy requirements
- Terms of service requirements
- Difference between research, education and regulated financial advice

Do not launch publicly before legal review.
