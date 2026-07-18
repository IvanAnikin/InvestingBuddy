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

### Admin authentication — Phase 23 (implemented)

The internal admin workspace (`/admin/*`) and its API proxy
(`/api/admin/proxy/*`) are protected by an authenticated, allowlisted admin
session enforced at the Next.js layer. This replaces the earlier plan to use
Clerk for the MVP admin surface.

- **Sign-in:** GitHub OAuth (Authorization Code flow). The OAuth *secret* is
  used only server-side during the token exchange; the GitHub access token is
  read once to resolve the verified email and is then discarded — it is never
  stored, cookied, or forwarded to the backend.
- **Session:** a compact, HMAC-SHA256-signed token in an **httpOnly**, `secure`
  (in production), `sameSite=lax` cookie (`ib_admin_session`), signed with
  `AUTH_SECRET`. No token is stored in `localStorage` or exposed to client JS.
  Verification is constant-time and fails closed when `AUTH_SECRET` is unset.
- **Authorization:** an env allowlist, `ADMIN_ALLOWED_EMAILS`. Only listed
  emails may reach `/admin/*`. An empty/unset allowlist authorizes nobody.
- **Enforcement (defense-in-depth):**
  - The Next.js **Proxy** (`apps/web/src/proxy.ts`, the Next 16 successor to
    `middleware`) gates `/admin/:path*` (redirect to `/login` when
    unauthenticated, `/unauthorized` when not allowlisted) and
    `/api/admin/proxy/:path*` (401 / 403).
  - The admin **API proxy route** independently re-checks auth + allowlist
    before attaching any backend credential (401 unauthenticated, 403 not
    allowed, 404 for a disallowed backend path).
- **Backend Basic Auth remains** as a server-to-server defense: the proxy adds
  it only after the human admin is authenticated and authorized. The browser
  never calls the backend directly and never sees the credential.
- **Local/CI auth:** `AUTH_TEST_MODE=true` enables a deterministic credential
  sign-in (`/api/auth/dev-login`) so Playwright/local dev never need real OAuth.
  It is hard-gated (returns 404 otherwise) and **must stay unset in
  staging/production**.

Future: Microsoft Entra ID can be added alongside GitHub via the same OAuth
pattern; public-user auth (Version 2) remains a later phase.

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

Every admin request (browser → `/api/admin/proxy/*` → backend) must:
1. Present a valid admin session (else **401**).
2. Carry an email on the `ADMIN_ALLOWED_EMAILS` allowlist (else **403**).
3. Resolve to an allowlisted backend path prefix (else **404**; backend never
   contacted).
4. Only then have the backend Basic Auth attached server-side, plus advisory
   `X-IB-Admin-Email` / `X-IB-Admin-Name` audit headers.

The backend **never trusts** the `X-IB-Admin-*` headers as authentication: they
are read only after Basic Auth passes (see `app/core/admin_identity.py` and
`app/core/staging_auth.py`), and mutating admin actions are logged with the
admin email for auditability.

### How to verify (staging)

- Logged out: `/admin` and `/admin/discovery` redirect to `/login`;
  `GET /api/admin/proxy/health` returns **401**.
- Signed in but not allowlisted: `/admin/*` redirects to `/unauthorized`;
  proxy returns **403**.
- Allowlisted admin: `/admin/*` loads; the shell shows the admin identity +
  Sign out.
- Public routes stay public: `/` and `/api/version` load without a session.

---

## Current Security Status (Phase 23 — Admin/Auth Hardening)

Implemented: authenticated + allowlisted admin access to `/admin/*` and the
admin API proxy; httpOnly signed session cookie; backend Basic Auth retained as
server-to-server defense; no secrets committed (`.env.example` placeholders
only). No public publishing, no recommendation output, no paid plans, no broker
integration exist.

Security review should be triggered before:
- Changing the authentication/authorization model or session handling
- Adding any new admin endpoints or proxy path prefixes
- Adding any user data storage or document retrieval pipeline
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
