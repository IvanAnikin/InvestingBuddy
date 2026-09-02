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

## Deployment topology

InvestingBuddy has **one** deployed environment, which is the private-use
production environment:

| | |
|---|---|
| Resource group | `ib-stg-rg` |
| Web | `https://ib-stg-web.azurewebsites.net` |
| API | `https://ib-stg-api.azurewebsites.net` |
| `APP_ENV` | `staging` — a historical runtime value that **gates the API's Basic Auth**, not a description of the deployment's role |
| Operational role | PRIVATE-USE PRODUCTION |

There is **no separate staging deployment** and no production deployment under
any other name. The `ib-stg-*` resource names are historical and were retained
to avoid a needless hostname, OAuth-callback and database migration. Any threat
model that assumes a throwaway staging tier is wrong: this environment holds the
real research history. See `docs/DEPLOYMENT.md` → *Single-environment model*.

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
    `middleware`) gates `/admin/:path*` and `/research/:path*` (redirect to
    `/login` when unauthenticated, `/unauthorized` when not allowlisted) and
    `/api/admin/proxy/:path*` (401 / 403).
    **`/research/:path*` must stay in that matcher.** The research workspace is
    built from Server Components that fetch the backend DIRECTLY with the
    server-side `BACKEND_BASIC_AUTH` credential (the same pattern `/admin` uses
    for SSR). Without a matcher entry those pages would render private research
    to any anonymous visitor — the proxy is the only thing standing in front of
    them. The public landing page at `/` is deliberately NOT matched: it is
    presentational, renders no research and reads no report.
  - The admin **API proxy route** independently re-checks auth + allowlist
    before attaching any backend credential (401 unauthenticated, 403 not
    allowed, 404 for a disallowed backend path).
    Its `ALLOWED_PREFIXES` match on a full path **segment**, so a prefix never
    covers a sibling that merely shares a string prefix (`/api/v1/discovery`
    does **not** allow `/api/v1/discovery-runs`). Every router mounted in
    `apps/api/app/main.py` therefore needs its own entry — otherwise the proxy
    answers 404 and the backend is never reached, which reads exactly like a
    missing endpoint. `apps/api/tests/test_admin_proxy_route_allowlist.py`
    enforces that invariant against the live OpenAPI path list.
- **Backend Basic Auth remains** as a server-to-server defense: the proxy adds
  it only after the human admin is authenticated and authorized. The browser
  never calls the backend directly and never sees the credential.
  **`APP_ENV` is load-bearing here.** `install_staging_basic_auth` is wired in
  `apps/api/app/main.py` only when `APP_ENV == "staging"`. The sole deployed
  environment (`ib-stg-api`) runs at `APP_ENV=staging` and has **no** App
  Service access restrictions, so this Basic Auth gate is the only control
  between the public internet and the API. Changing `APP_ENV` to `production`
  would silently remove it. Do not change that value without first giving the
  backend its own authentication outside `staging`, or adding equivalent
  network-level restrictions. See `docs/DEPLOYMENT.md` → *APP_ENV semantics*.
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

**Incident record — 2026-08-09 (Phase 32A Slice 5B.2 staging validation).**
During real Azure Document Intelligence OCR staging validation, a validation
subagent's own diagnostic tool output briefly contained the real
`AZURE_DOCUMENT_INTELLIGENCE_API_KEY` value once — never in an application
log, never in any persisted staging artifact, never printed a second time.
Disclosed immediately by the agent. Contained the same session: a fresh
`key2` was generated (never itself exposed), the app was switched to it,
then the exposed `key1` was regenerated/invalidated; the API was restarted
and connectivity re-verified post-rotation. The remaining two validation
rounds used a stricter "capture inline, never print" credential discipline
and were confirmed clean by fresh log/transcript greps for
key/secret/password/bearer/`Ocp-Apim`/connection-string patterns. Full
record: `docs/development/closures/phase-32a-slice5b2.md`.

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

> **Implemented — Slice 5A, Slice 5B.1 and Slice 5B.2 are all CLOSED +
> STAGING-VALIDATED** (`docs/development/closures/phase-32a-slice5a.md`,
> `docs/development/closures/phase-32a-slice5b1.md`,
> `docs/development/closures/phase-32a-slice5b2.md`). Behind the default-OFF
> `PRIMARY_DOCUMENT_INGESTION_ENABLED` flag (kept ON on staging); with it off
> none of this fetch/parse surface is exercised. **Slice 5B.2 (real Azure
> Document Intelligence OCR) closed 2026-08-09 — WITH AN EXPLICIT EFFICACY
> CAVEAT**: the real Azure resource is now provisioned on staging and
> `PRIMARY_DOCUMENT_OCR_ENABLED` is flipped `true` (kept ON); gating,
> connectivity, budget, retry, reuse and cross-company isolation are all
> staging-proven live, but a real Azure OCR call itself has not yet been
> observed on staging, for a documented structural (non-code) reason — see
> the closure report. A validation-time key-exposure incident was disclosed,
> contained and resolved same-session — see *Secrets Management* below. 5B.3
> (admin web visibility) remains open.

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
  decompression-bomb guard (`guard_image_pixels`, Pillow-based — pins
  `PIL.Image.MAX_IMAGE_PIXELS`; not on the real OCR call path itself, since
  the PDF bytes are shipped to Azure directly and rasterized server-side, but
  still exercised/available for any future local-image entry point); and
  per-document + aggregate wall-time budgets so ingestion cannot hang or
  exhaust memory.
- **Real Azure Document Intelligence OCR — fixed endpoint, no arbitrary URL
  (Slice 5B.2).** `AzureDocumentIntelligenceOcrProvider` always calls the ONE
  code-configured `azure_document_intelligence_endpoint` — never a
  caller-supplied URL, so there is no new SSRF surface here. Auth is
  managed-identity-first (`DefaultAzureCredential`, matching the App Service
  system-assigned identity + Key Vault RBAC already wired for other services);
  an API key is only ever used when explicitly configured (local dev), and
  `get_ocr_provider()` returns the honest `NoOpOcrProvider` whenever the
  endpoint is unset — so `PRIMARY_DOCUMENT_OCR_ENABLED` can be flipped on
  safely before the Azure resource is provisioned. Every SDK exception is
  classified down to `type(exc).__name__` + HTTP status class only (mirrors
  `azure_openai_client.py`); the raw exception message — which can embed the
  endpoint — never reaches a log line or a stored failure code. OCR is metered
  INSIDE the existing per-document/aggregate ingestion budgets (never a new
  phase), bounded further by `PRIMARY_DOCUMENT_OCR_TIMEOUT_SECONDS`,
  `PRIMARY_DOCUMENT_MAX_OCR_DOCUMENTS_PER_RUN` and a bounded retry count; page
  selection is deterministic and capped by `PRIMARY_DOCUMENT_MAX_OCR_PAGES` —
  a document is never OCR'd page-by-page without bound. OCR-derived facts
  remain confidence-capped below "high" and `needs_human_review`, identically
  to native/HTML facts.
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

- Logged out: `/admin`, `/admin/discovery`, `/research` and
  `/research/reports/<id>` redirect to `/login` (preserving `callbackUrl`);
  `GET /api/admin/proxy/health` returns **401**.
- Signed in but not allowlisted: `/admin/*` and `/research/*` redirect to
  `/unauthorized`; proxy returns **403**.
- Allowlisted admin: `/admin/*` and `/research/*` load; the shell shows the
  admin identity + Sign out.
- Public routes stay public: `/` and `/api/version` load without a session. The
  landing page must show the marketing hero and no report data.

---

## Current Security Status (Phase 23 — Admin/Auth Hardening)

Implemented: authenticated + allowlisted access to `/admin/*`, `/research/*`
and the admin API proxy; httpOnly signed session cookie; backend Basic Auth
retained as server-to-server defense; no secrets committed (`.env.example`
placeholders only). No public publishing, no recommendation output, no paid
plans, no broker integration exist.

The user-facing product layer changed no authentication behaviour: it added a
route family to the existing proxy matcher and reused the same GitHub OAuth,
the same session cookie and the same allowlist. The only surface it made
public is the presentational landing page at `/`, which was already public.

**The section ROOTS are named literally in the proxy matcher** (`/admin`,
`/research`, alongside `/admin/:path*` and `/research/:path*`). A live
deployment check reported `/research` answering 200 anonymously while every
`/research/**` route redirected to `/login`, and `/research` renders "Recent
research" — company names, tickers and report timestamps out of this private
workspace.

That asymmetry does **not** reproduce locally: measured against Next 16.2.9 in
both `next dev` and a production `next build && next start`, `/research/:path*`
alone gates `/research` (307 to `/login` either way). So the pattern is not a
proven cause and the fix is not presented as one. The roots are named anyway,
because whether the front door of a private workspace is gated should not rest
on how a path-pattern modifier treats a zero-segment match — a property of a
dependency, invisible in the file that decides the boundary. `apps/web/tests/
e2e/v2-live-corrective.spec.ts` pins the whole contract:

| Route | Anonymous |
|---|---|
| `/` | 200 (presentational; renders no research) |
| `/research` | 307 → `/login?callbackUrl=/research` |
| `/research/company`, `/research/discover`, `/research/reports` | 307 → `/login` |
| `/admin`, `/admin/**` | 307 → `/login` |
| `/api/admin/proxy/**` | 401 (403 when authenticated but not allowlisted) |

The async company-research endpoints are reached only through the same
server-side admin proxy as every other backend call — the browser never holds a
backend credential.

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
