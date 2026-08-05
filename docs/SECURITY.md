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

## Outbound Document Fetch / SSRF Hardening (Phase 32A Slice 5)

> **Implemented — Slice 5A and Slice 5B.1 are both CLOSED + STAGING-VALIDATED**
> (`docs/development/closures/phase-32a-slice5a.md`,
> `docs/development/closures/phase-32a-slice5b1.md`). Behind the default-OFF
> `PRIMARY_DOCUMENT_INGESTION_ENABLED` flag (kept ON on staging); with it off
> none of this fetch/parse surface is exercised. Slice 5B.2 (real OCR) and
> 5B.3 (admin web visibility) remain open.

Phase 32A Slice 5 adds bounded ingestion of an issuer's OWN primary documents
(annual report / registration document) — a new outbound-fetch + PDF-parsing
surface. It is hardened as follows:

- **No arbitrary-URL surface.** No endpoint accepts a user-supplied URL. Every
  document URL comes from the verified-issuer / company-IR allowlist, and all new
  fetches route through the allowlist-gated hardened fetch layer
  (`safe_web_fetcher`). No JS / browser / paywall / auth bypass; no cookies or
  credentials are sent.
- **SSRF / DNS-rebinding guard (opt-in on the deep path).** The resolved IPs for a
  host are checked before AND after redirects; a target that resolves to a
  loopback / private / link-local / reserved / multicast / unspecified address, or
  to a cloud instance-metadata IP (`169.254.169.254` / `fd00:ec2::254`), is
  rejected.
- **Resolve-then-connect IP pinning (ADR-015, Phase 32A Slice 5B.1 — closes the
  ADR-014 residual).** The address that is validated is now the address that is
  connected to: `PinnedAsyncHTTPTransport` rewrites the request's URL host to the
  validated IP literal while restoring the real hostname in the `Host` header and
  the `sni_hostname` request extension, so the name is never resolved a second
  time and a hostile DNS answer cannot change between the check and the connect.
  TLS is not weakened — certificate hostname verification still targets the real
  hostname; pinning changes *where we connect*, never *what we trust*. The
  transport **fails closed**: a host with no validated pin raises before any
  socket opens.

  **Scope of the closure — do not shorten this to a bare "closed".** It holds for
  the *ingestion path* (issuer IR page fetch, document fetch, SEC filing-index
  fetch) and only while `PRIMARY_DOCUMENT_PIN_DNS_ENABLED=true`; the kill-switch
  deliberately reverts to the older check-then-connect behaviour. The pre-existing
  fixed-host provider clients (EODHD, SEC XBRL, GLEIF, Stooq, news, press) are
  untouched and unpinned — lower exposure, since they reach code-defined hosts,
  but not covered by this work.

  **Per-hostname pool isolation (ADR-015).** Because the connected host is the
  pinned IP literal, httpcore would key its connection pool on
  `Origin(scheme, <ip>, port)` — so two different allowlisted hostnames sharing
  one address (routine behind a CDN) would collide on a single pooled connection,
  and a later hop could reuse a TLS session whose certificate was verified for an
  earlier hop's hostname. The transport therefore keeps one connection pool **per
  original hostname**; connections are never reused across a hostname change, so a
  pooled session can only serve the hostname its certificate was validated for.
  Keep-alive within a single hostname is unaffected. Asserted by test, not by
  argument. Each redirect hop is re-validated and re-pinned, and a pin is
  never reused across hosts. DNS resolution is now asynchronous
  (`loop.getaddrinfo`), so a slow or blackholed resolver can no longer stall the
  worker. Kill-switch: `PRIMARY_DOCUMENT_PIN_DNS_ENABLED` (default **true**);
  turning it off reverts to the weaker Slice 5A check-then-connect behaviour and
  that degradation is recorded honestly rather than reported as pinned.
- **Sanitized ingestion telemetry (Slice 5B.1).** Every ingestion attempt —
  including every failure — is persisted to `document_ingestion_attempts`. The
  `status` and `failure_code` columns are **closed vocabularies** defined once in
  `app/services/sources/ingestion_status.py`; anything outside them is coerced to
  `unknown`, so a raw provider exception message, URL fragment or IP address can
  never reach the database or the admin UI. Only the HTTP status *class*
  (`4xx`/`5xx`) is kept, never the exact code. URLs are canonicalized and
  credential-stripped before hashing and storage. Raw document bodies and
  extracted text are never persisted to this table.
- **Document protection is never bypassed.** An encrypted, password-protected,
  scanned and malformed PDF are now classified distinctly. The only password ever
  supplied is the EMPTY one (the standard "this document has no user password"
  case, which is what an owner-password-only PDF uses); no password is guessed,
  derived, brute-forced or stripped, and a document that genuinely requires a
  user password is recorded as inaccessible and never opened.
- **Content + resource bounds.** A `%PDF-` magic-byte check before parsing; a hard
  download-byte ceiling; page / OCR-page / excerpt / char / table-size caps; a
  decompression-bomb guard; a Pillow image-pixel cap for the (future) OCR raster
  path; and per-document + aggregate wall-time budgets so ingestion cannot hang or
  exhaust memory.
- **External text is untrusted, inert data.** Extracted text is never executed or
  interpreted; prompt-injection markers are preserved verbatim (not stripped) so a
  downstream prompt-boundary guard sees them — the extractor never treats document
  content as instructions.
- **Secret-free logging.** Ingestion logs counts / status only — never document
  bytes, extracted text, or URLs carrying secrets; canonical URLs are
  credential-stripped before persistence. On parse error only the exception class
  name is recorded. The Phase 27.1D log secret-scan applies unchanged.
- **No fabrication, human review required.** A blocked / JS-gated / scanned
  document degrades to an honest metadata-only / `extraction_failed` gap; every
  extracted fact is `needs_human_review`; `publication_ready` stays false.

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
