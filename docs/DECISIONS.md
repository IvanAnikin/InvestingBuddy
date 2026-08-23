# Architecture Decision Records

This document records significant architecture decisions made for InvestingBuddy.

Use this format for new decisions:

```markdown
## ADR-NNN: Short Title

**Date:** YYYY-MM-DD
**Status:** Accepted | Superseded by ADR-NNN | Deprecated

### Context
<Why did this decision need to be made?>

### Decision
<What was decided?>

### Consequences
<Positive and negative consequences>
```

---

## ADR-001: Python FastAPI as Backend Framework

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform needs a backend framework that integrates well with LangGraph, LangChain, SQLAlchemy and the Python data science ecosystem. The primary alternative was Node.js.

### Decision
Use Python FastAPI with SQLAlchemy async, Alembic and Pydantic v2.

### Consequences
- Best ecosystem compatibility with LangChain, LangGraph, OpenBB and financial data tooling.
- Strong typing via Pydantic.
- Async support for LLM streaming.
- Team must maintain two languages (Python backend, TypeScript frontend).

---

## ADR-002: LangGraph for Agent Orchestration

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform requires stateful, multi-step agent workflows with branching, retry logic, human-in-the-loop review and durable execution. Simple single-prompt LLM calls are not sufficient for the council-of-agents architecture.

### Decision
Use LangGraph for all agent workflows. LangChain for tool wrappers, document loaders and retrievers.

### Consequences
- Explicit graph-based workflow state is auditable and debuggable.
- Supports branching and conditional flows (e.g., request more research if quality is low).
- LangGraph is a production-grade framework maintained by LangChain Inc.
- Adds a learning curve for developers unfamiliar with graph-based workflow design.

---

## ADR-003: Azure as Primary Cloud Platform

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform requires LLM runtime, vector search, blob storage, PostgreSQL and background job infrastructure. Microsoft Azure was selected over AWS and GCP.

### Decision
Use Microsoft Azure as the primary cloud platform, specifically:
- Azure OpenAI (LLM runtime)
- Azure AI Search (vector search / RAG)
- Azure Database for PostgreSQL
- Azure Blob Storage
- Azure Key Vault
- Azure Application Insights
- Azure App Service (hosting)

### Consequences
- Azure OpenAI provides enterprise-grade compliance and data residency.
- Integrated managed identity reduces secrets management burden.
- Azure AI Search is well-suited for hybrid search (keyword + vector).
- Vendor lock-in to Azure for LLM runtime (mitigated by LangChain abstraction layer).

---

## ADR-004: Clerk for MVP Authentication

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform needs user authentication for V2 (personalized recommendations). Evaluating Clerk vs. Auth0 vs. Microsoft Entra External ID.

### Decision
Use Clerk for MVP authentication due to fastest implementation time and clean Next.js integration.

### Consequences
- Fastest time to working auth in Next.js.
- Good user management dashboard.
- Upgrade path to Microsoft Entra External ID available if deeper Azure integration is needed.
- Adds a paid dependency (Clerk pricing scales with users).

---

## ADR-005: No Automatic Trade Execution

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform could theoretically connect to broker APIs and execute trades automatically based on agent recommendations.

### Decision
The platform will never automatically execute trades. It provides research and decision support only. Users make their own investment decisions and execute trades through their own brokers.

### Consequences
- Avoids MiFID II regulated investment advice classification.
- Reduces regulatory complexity significantly.
- Aligns with the product principle of human-in-the-loop decision making.
- Platform cannot monetize through execution fees.

---

## ADR-006: Judge System Does Not Auto-Deploy Prompt Changes

**Date:** 2026-06-16
**Status:** Accepted

### Context
The Judge system evaluates agent output quality and recommends prompt and workflow improvements. A fully automated system could apply these improvements directly to production.

### Decision
All Judge improvement suggestions must be reviewed and explicitly approved by an admin before being applied to production prompts or workflows.

### Consequences
- Prevents automated drift of investment analysis quality without human oversight.
- Supports auditability — every prompt change has a human approval record.
- Slows the improvement cycle compared to fully automated self-improvement.
- Consistent with the human-in-the-loop principle throughout the platform.

---

## ADR-007: Bicep for Azure Infrastructure as Code

**Date:** 2026-06-20
**Status:** Accepted

### Context
Infrastructure Phase A requires choosing an IaC approach: Azure CLI scripts, Bicep, Terraform, or Azure Developer CLI.

### Decision
Use Bicep as the infrastructure-as-code tool for all Azure resources.

### Consequences
- Bicep is native to Azure ARM — no state file to manage, no extra toolchain.
- Idempotent deployments via `az deployment group create --mode Incremental`.
- First-class GitHub Actions support via `azure/arm-deploy`.
- No multi-cloud requirement makes Terraform's main advantage irrelevant.
- Azure Developer CLI is too opinionated about project structure for this monorepo.
- Team is locked to Azure — Bicep does not abstract provider differences (acceptable given ADR-003).

---

## ADR-008: westeurope as Primary Azure Region

**Date:** 2026-06-20
**Status:** Accepted

### Context
The platform focuses on European public company research. A primary Azure region must be chosen before provisioning.

### Decision
Deploy all staging and production resources in `westeurope` (Netherlands).
`northeurope` (Ireland) reserved as future DR or secondary region.

### Consequences
- Lowest latency for EU-based users and data sources.
- GDPR compliant — personal data (V2 user accounts) stays in the EU.
- All required Azure services available in `westeurope`, including Azure OpenAI GPT-4o.
- US-based regions avoided due to GDPR complications for EU user data.

---

## ADR-009: OIDC Federated Credentials for GitHub Actions

**Date:** 2026-06-20
**Status:** Accepted

### Context
GitHub Actions must authenticate to Azure for deployment. The alternative is a long-lived `AZURE_CREDENTIALS` JSON service principal secret.

### Decision
Use OpenID Connect (OIDC) federated credentials. No long-lived credential JSON is stored in GitHub Secrets. Only three values are stored: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.

### Consequences
- No long-lived secrets that can be leaked, rotated or accidentally committed.
- Access is scoped to the exact GitHub repository and branch via federated claim.
- Requires Azure AD App Registration with federated credential setup (one-time manual step).
- Tokens are ephemeral — generated per workflow run, expire automatically.
- `azure/login@v2` with OIDC requires `permissions: id-token: write` in the workflow job.

---

## ADR-010: Azure CLI via pip in Dedicated venv

**Date:** 2026-06-20
**Status:** Accepted

### Context
Azure CLI is required locally for provisioning and inspection. Homebrew (`brew install azure-cli`)
does not work reliably on this Mac. An alternative installation method is needed.

### Decision
Install Azure CLI via pip into a dedicated virtual environment at `~/.venvs/azure-cli`.
This venv is separate from the project's `apps/api/.venv` and from any Homebrew installation.
Activate before every Azure task: `source ~/.venvs/azure-cli/bin/activate`.

### Consequences
- Reliable `az` CLI on macOS without Homebrew dependency.
- Isolated from project dependencies — upgrading `azure-cli` does not affect backend packages.
- The venv is local-only and is never committed (covered by `.gitignore`).
- Developer must remember to activate the venv before running `az` commands — documented in all relevant skill and command files.
- GitHub Actions does not use this venv — it uses `azure/login@v2` with OIDC (see ADR-009).
- On Python 3.14, `pip install azure-cli` fails because `cryptography` has no pre-built wheel and requires Rust to compile from source. Use `pip install --prefer-binary azure-cli` to force pip to select an older binary-compatible wheel instead.

---

## ADR-011: gpt-4.1-mini as Phase 7 Development LLM

**Date:** 2026-06-23
**Status:** Accepted

### Context
Phase 7 requires a real Azure OpenAI model for local development testing of the
`generate_research_sections` LLM node. The original plan referenced `gpt-4o-mini`
but a live model availability check in `westeurope` showed `gpt-4.1-mini` is now
the current-generation mini model with a longer deprecation timeline.

### Decision
Deploy `gpt-4.1-mini` v2025-04-14 under the deployment name `gpt-4.1-mini` using
the `GlobalStandard` SKU at 10K TPM capacity. Use API version `2025-01-01-preview`
(required for this model version).

### Consequences
- Cost-effective: gpt-4.1-mini is the cheapest capable model in the GPT-4 family on Azure.
- Latest-generation: supersedes gpt-4o-mini; same API surface so no code changes needed to upgrade to gpt-4o or gpt-4.1 later.
- Long deprecation: support until October 2027.
- `AZURE_OPENAI_API_VERSION` bumped from `2024-08-01-preview` to `2025-01-01-preview` to support this model.
- Deployment name `gpt-4.1-mini` matches the model name — easy to remember and consistent with naming convention.
- CI is unaffected — CI uses `LLM_PROVIDER=mock` (no Azure credentials, no network calls).

---

## ADR-012: Admin Auth via GitHub OAuth + Custom HMAC Session (Phase 23)

**Date:** 2026-07-18
**Status:** Accepted

### Context
Phase 23 must make `/admin/*` and the admin API proxy inaccessible to
unauthenticated users before any external sharing. The MVP plan (and
`docs/SECURITY.md`) referenced Clerk; the Phase 23 brief recommended
Auth.js/NextAuth with Microsoft Entra ID or GitHub. The web app runs **Next.js
16.2.9 + React 19**, where the `middleware` convention was renamed to `proxy`
and several APIs changed. CI must stay fully offline and deterministic (no real
OAuth round-trip).

### Decision
Implement a **dependency-free admin session** — an HMAC-SHA256-signed, httpOnly,
`secure`/`sameSite=lax` cookie (`AUTH_SECRET`) verified with Web Crypto — issued
after **GitHub OAuth** sign-in, with an env allowlist (`ADMIN_ALLOWED_EMAILS`).
Enforcement is the Next 16 **Proxy** (`src/proxy.ts`) plus an independent
re-check in the admin proxy route. A hard-gated `AUTH_TEST_MODE` credential
endpoint provides deterministic local/CI sign-in.

### Consequences
- No `next-auth` v5 beta dependency → avoids unverified Next 16 / React 19
  compatibility risk on a security-critical phase; small, auditable surface.
- Real OAuth on staging via GitHub; the OAuth secret is used only server-side in
  the token exchange, and the access token is read once for the verified email
  then discarded (never stored or forwarded to the backend).
- Backend Basic Auth (`STAGING_BASIC_AUTH`) is retained as server-to-server
  defense; the advisory `X-IB-Admin-*` headers are never trusted for auth.
- CI/local sign-in is offline and deterministic; `AUTH_TEST_MODE` must never be
  set in staging/production (returns 404 otherwise).
- Microsoft Entra ID can be added later via the same OAuth pattern without
  changing the session/allowlist model.

---

## ADR-013: LLM Council Reliability — Bounded Retry Under a Wall-Time Budget, Reserved Critical Budget, and a Deterministic Chair Fallback (Phase 32A Slice 4)

**Date:** 2026-08-04
**Status:** Accepted (implemented on branch `phase-32a-slice4-council-reliability`
`5bbaaf4`; **PR open, pending staging validation** — not yet merged / deployed /
validated)

### Context
Under Azure `gpt-4.1-mini` TPM limits, a large evidence pack (e.g. AAPL) is
embedded into all 8 single-company council agents' prompts and sent strictly
sequentially. The council had no retry: one 429 marked an agent `failed`, so
whichever agents landed in a TPM window completed (~4/8) and the rest failed —
and because the committee chair runs last with no fallback, the synthesis
(`committee_label`) was frequently lost. Critically, the single-company council
runs INLINE in the HTTP request handler (no background task), so total wall-time
is bounded only by the ~230s Azure App Service gateway timeout. This is the
"Azure-TPM partial councils" environmental note carried forward from Slices 2–3.

### Decision
1. **Retries are bounded by a strict TOTAL wall-time budget/deadline.** Because
   the council is inline in the request, every retry lives under a hard council
   deadline (`llm_council_total_budget_seconds`, default 150s) kept well below the
   ~230s gateway. Attempts are additionally capped per agent, backoff is capped +
   jittered, and an honored provider `retry-after` is capped — so there is no
   uncontrolled loop or DoS amplification. Only transient errors (429 / 5xx /
   timeout) are retried; schema / safety / auth failures are permanent.
2. **A wall-time reserve protects `red_team` + `committee_chair`.** A reserve
   (`llm_council_critical_reserve_seconds`, default 45s) is held back so earlier
   (non-reserved) agents draining the shared budget can never starve the
   adversarial check or the synthesis. Critical agents also get more attempts.
3. **The committee-chair fallback is deterministic and never fabricates
   consensus.** If the LLM chair still fails, a deterministic, non-consensus
   summary is attached (`committee_label="insufficient_data"`, empty `key_points`
   ⇒ no citations), built only from already-validated stored council outputs,
   stating no recommendation / valuation / price objective. The failed LLM-chair
   entry is kept so the council is visibly partial.
4. **Concurrency and per-agent evidence projection are deliberately DEFERRED
   (out of scope).** Concurrent execution is declined: on the inline path under
   Azure TPM limits, concurrency worsens 429s — sequential execution + bounded
   retry + reserved budget is the correct lever. Per-agent evidence projection /
   prompt trimming is declined: it cannot be done without risking evidence loss
   and reopening the Slice 2 evidence-budget contract.

### Consequences
- Gated by a new default-OFF master flag `LLM_COUNCIL_RETRY_ENABLED`; flag-off is
  byte-identical to the prior behaviour. No DB migration (head stays `012`); no
  auth / publishing / SSRF change; `publication_ready` stays False and
  `human_review_required` stays True; failed agents still create no citations.
- A partial council is now more likely to recover the chair synthesis; when it
  cannot, the report/memo renders an honest deterministic "LLM chair unavailable"
  marker instead of a null label.
- Retry logging carries SAFE fields only (attempt / agent / error_type /
  durations / backoff / capped retry-after) — never prompts, evidence, or secrets.
- Not yet staging-validated — the flag ships OFF and will be flipped ON on staging
  (human-approved) for validation as a later step.

---

## ADR-014: Bounded Primary-Document Ingestion — pdfplumber, Durable Extraction Tables, a Single Flag-OFF-Inert PR, and a NoOp OCR Seam (Phase 32A Slice 5)

**Date:** 2026-08-04
**Status:** Accepted (implemented on branch `phase-32a-slice5`; **PR open, pending
staging validation** — not yet merged / deployed / validated)

### Context
Phase 29B.2 added bounded PDF/HTML text extraction (pypdf, no OCR), but on staging
every reachable issuer report is scanned or JS-gated, so councils reason from
metadata-only references. Slice 5 deepens ingestion — HTML tables/sections, native
PDF text + table extraction with page/table location, and an OCR fallback seam —
so the council can eventually cite real T1 primary evidence with precise
provenance. This adds a network-fetch + PDF-parsing surface that is
security-sensitive, so it must be strictly bounded, allowlist-gated, and OFF by
default.

### Decision (four approved architecture forks)
1. **pdfplumber for native-PDF table + layout extraction.** `pdfplumber>=0.11,<0.12`
   (pure-Python; transitively pdfminer.six + Pillow — Pillow gates the future OCR
   raster path via a pixel cap) is added alongside the existing pypdf. No OCR
   binary (tesseract / pdf2image / pymupdf) is added; wheels resolve on the Azure
   App Service Python 3.12 runtime.
2. **Migration `013` for durable `extracted_documents` / `extracted_facts` tables.**
   Extraction is persisted (deduped by raw-bytes `content_hash`) so a later report
   regeneration reuses it (no re-fetch/re-extract) and facts carry durable
   page/table provenance. Reversible, additive, backfill-free; the tables stay
   unwritten unless ingestion is enabled.
3. **A single flag-OFF-inert PR.** The whole slice ships behind default-OFF flags
   (master `PRIMARY_DOCUMENT_INGESTION_ENABLED`); with it off the connector /
   council / evidence-pack / persistence paths are byte-for-byte unchanged. Reuse
   and persistence are additionally gated on BOTH ingestion +
   `REPORT_CITATION_PERSISTENCE_ENABLED`.
4. **A NoOp OCR provider-abstraction now; the real adapter later.** OCR ships as an
   honest provider seam only (mirrors the Phase 30A translation-provider seam):
   the sole provider returns an empty `ocr_unavailable` result — never fabricated
   text — so scanned PDFs still degrade honestly to metadata-only gaps this slice.

### Deferred follow-ups (documented, not built this slice)
- **Real Azure Document Intelligence OCR adapter** (+ its call-site wiring) —
  needs resource provisioning + admin sign-off before it can be enabled.
- **Blob-storage document-body caching** — the `extracted_documents.blob_path`
  column is a reserved, currently-unused hook.
- **SEC 10-K / 20-F full-text body fetch** for US issuers (this slice ingests the
  issuer's OWN primary documents via the verified-issuer/company-IR path).
  → **RESOLVED by ADR-015 (Slice 5B.1).**
- **Two non-blocking security hardenings:** resolve-then-connect IP-pinning to
  fully close the DNS-rebinding TOCTOU window (the current guard resolves + checks
  before and after redirects but does not pin the connected socket to the checked
  IP), and async DNS resolution to avoid a synchronous `getaddrinfo` on the event
  loop. → **BOTH RESOLVED by ADR-015 (Slice 5B.1).**

### Consequences
- No new public endpoint and no user-supplied-URL fetch surface; every fetch
  routes through the allowlist-gated hardened layer. Extracted text is treated as
  UNTRUSTED, inert data (injection markers preserved verbatim for a downstream
  prompt-boundary guard, never executed/interpreted). No JS/browser/paywall/auth
  bypass.
- Table/OCR-derived values pass stricter validation (label/value/unit/period +
  column alignment + cross-field arithmetic + cross-method agreement; OCR
  downgraded); non-validated text is kept `excerpt_only`, never a fact.
  Metadata-only references never become facts or claim-verification. Every
  extracted fact is `needs_human_review`; `publication_ready` stays False.
- The evidence pack gains a primary_document floor + cap (ON only with the master
  flag) WITHOUT weakening the Slice-2 `financial_floor=3` / news caps.
- Bounded by size/page/OCR-page/excerpt/char caps, per-document + aggregate
  wall-time budgets (so ingestion + the ~150s council stay under the ~230s
  gateway), a %PDF magic-byte check, a decompression-bomb guard and a Pillow
  pixel cap. Logging is counts/status only — never bytes or extracted text.
- Not yet staging-validated — the flags ship OFF and will be flipped ON on staging
  (human-approved) for validation as a later step.

---

## ADR-015: Making Primary-Document Ingestion Actually Reach Documents — SEC Filing Bodies, Non-Browser Discovery, Durable Attempt Records and Resolve-Then-Connect Pinning (Phase 32A Slice 5B.1)

**Date:** 2026-08-05
**Status:** Accepted — PR open, pending staging validation.

### Context

Slice 5A (ADR-014) shipped the ingestion foundation and was staging-validated as
a *foundation*, with an explicit efficacy caveat: **0 successful native
extractions across 7 issuers**, and `extracted_documents` / `extracted_facts`
both still at 0/0. The success path existed only in unit tests. Four separate
root causes produced that result, and they need different fixes:

1. **US issuers had no path at all.** The SEC connector only ever called
   `data.sec.gov` JSON (companyfacts / submissions). It parsed `accessionNumber`
   and then dropped it, and it attached a `primary_filing_unavailable` gap to
   every result stating the full text is not retrieved. AAPL is not in the
   `verified_issuer_sources` registry either, so the company-IR document path
   could not run for it. AAPL therefore produced zero document candidates.
2. **Modern IR pages are JS-rendered.** Discovery read only `<a href>` tags. For
   BA, BRBY, KER, MC and RMS the served HTML contains zero matching anchors even
   though the document URLs are present in the page's own hydration payload.
3. **Failures were invisible.** `persist_primary_document_artifacts` writes a row
   only when `status == "extracted"`, so every failed attempt persisted nothing.
   Staging could not distinguish "never attempted" from "attempted and blocked".
4. **CFR's documents were mislabelled.** Encrypted, password-protected, scanned
   and malformed PDFs all collapsed into one opaque `extraction_failed`, so
   "needs OCR" was indistinguishable from "is broken".

ADR-014 also left two security items open: the DNS-rebinding TOCTOU window and a
synchronous `getaddrinfo` on the event loop.

### Decision

**1. Fetch official SEC filing bodies, as a supplement.** Resolve
`accession → www.sec.gov/Archives/.../index.json → primary document` and fetch
the body through the existing hardened, allowlisted fetcher against
`allowed_domains=("sec.gov",)`. Primary-document selection is deterministic
(submissions `primaryDocument` hint → form-typed `.htm` → largest non-exhibit
`.htm` → first `.htm`), never the unbounded `.txt` full-submission dump, with
exhibits only on explicit request. A declaring User-Agent and a real client-side
throttle are applied. **SEC/XBRL structured facts remain authoritative for
financial figures**; the document body supplements them and never replaces them.

**2. Bounded, non-browser discovery — no headless browser.** Strategies run in a
documented order (anchors → JSON-LD → `__NEXT_DATA__`/hydration state → embedded
script JSON), each bounded, each re-applying the https / safe-host / allowlist /
secret-strip checks, deduplicated by a canonical identity that collapses signed
query variants of the same document. Feeds/sitemaps and same-origin JSON
endpoints are separate, caller-driven entry points because they require another
guarded fetch. **We deliberately did NOT add Playwright or any headless browser
in this slice** — where bounded non-browser methods still cannot reach a site's
documents, that is recorded as an honest limitation rather than escalated to
uncontrolled automation. Documents are classified (annual / interim / results /
presentation) so an annual report outranks a marketing PDF.

**3. A durable, sanitized attempt record (migration `014`).** Every attempt that
reaches the fetch/extract stage persists a bounded row to
`document_ingestion_attempts`, idempotent per `(company_id, agent_run_id,
url_hash)`: `extracted`, `metadata_only`, `unsupported`, `encrypted`,
`password_protected`, `malformed`, `rejected_security`, `timeout`,
`extraction_failed`. (`discovered` and `fetched` are RESERVED vocabulary members
that no writer emits in this slice — a candidate ranked out before a fetch
produces no row. They are listed here as reserved, not as delivered.) Status and
failure code are **closed vocabularies** defined once in
`app/services/sources/ingestion_status.py`; anything outside them becomes
`unknown`. Only the HTTP status *class* is kept. Raw provider text, secrets,
signed query strings, document bodies and OCR text are never stored. A tri-state
`pinned` column records honestly whether the connection was pinned — `false` is
never dressed up as `true`.

**4. Resolve-then-connect IP pinning + async DNS (closes the ADR-014 residual).**
A custom `httpx` transport connects only to the pre-validated address, restoring
the hostname in the `Host` header and the `sni_hostname` extension so TLS and
certificate hostname verification are unchanged. It fails closed on an unpinned
host, and every redirect hop is re-validated and re-pinned. Resolution moves to
`loop.getaddrinfo`. **All three outbound paths added or touched by this slice are
covered** — the issuer IR page fetch, the document fetch, and the SEC filing-index
fetch — so no unvalidated-address connection remains on the ingestion path.

**Per-hostname connection-pool isolation (found and fixed pre-merge).** Because
the connected host IS the pinned IP literal, httpcore keys its pool on
`Origin(scheme, <ip>, port)`. An empirical probe confirmed that two *different*
allowlisted hostnames resolving to the same address — routine behind a CDN —
produced an identical pool origin served by one shared transport, so a second hop
could have reused a TLS session whose certificate was verified for the *first*
hop's hostname, silently crossing a certificate-verification boundary.

The transport therefore keeps **one inner transport, and so one connection pool,
per original hostname**. Connections are never reused across a hostname change, so
a pooled session can only ever serve the hostname its certificate was actually
validated for. Legitimate keep-alive within a single hostname is unaffected. This
is verified by test, not by argument: `test_two_hostnames_on_one_ip_get_isolated_pools`
asserts two distinct pools with the correct per-hop `sni_hostname`, and
`test_same_hostname_reuses_its_own_pool` asserts reuse still happens where it is
safe.

**5. Distinguish the four inaccessibility modes — without bypassing any of
them.** The extraction status vocabulary is unchanged (the council summary and
persistence path depend on it); the distinction is carried by a new sanitized
`failure_code` and resolved into the attempt vocabulary at the persistence
boundary. The only password ever supplied is the EMPTY one — the standard "no
user password" case that an owner-password-only PDF uses. No password is
guessed, derived, brute-forced or stripped.

### Alternatives rejected

- **A headless browser for JS-gated pages.** Large attack surface, heavy runtime,
  and it edges toward crawling. Rejected for this slice; the hydration payload
  turns out to contain the URLs anyway.
- **Widening `extracted_documents.status` to hold failures.** Its `content_hash`
  is a UNIQUE NOT NULL identity, and a failed attempt has no bytes and therefore
  no hash. Attempts need their own table and their own identity key.
- **Re-vocabularying `PrimaryDocumentExtraction.status`.** Would silently drop
  encrypted documents out of existing council counters and break passing tests
  for no gain over a dedicated failure code.
- **Making pinning mandatory with no kill-switch.** An httpx/httpcore build that
  cannot support the `sni_hostname` extension would lose outbound fetching
  entirely. Pinning degrades to the previous check-then-connect behaviour and the
  degradation is recorded honestly — `pinned` is never claimed when it did not
  happen.

### Consequences

- No new public endpoint, no user-supplied-URL fetch surface, no new dependency.
- With `PRIMARY_DOCUMENT_INGESTION_ENABLED` off, every path stays byte-identical.
- Failed ingestion becomes observable for the first time, which is what makes the
  Slice 5B.3 admin surfacing and the eventual Phase 32A closure evidence possible.
- OCR is still NOT implemented — a scanned PDF remains `metadata_only` with a
  `scanned_no_text` failure code. That is Slice 5B.2's job, and this slice
  deliberately labels the one failure mode OCR can actually rescue.
- Not yet staging-validated. Migration `014` must be applied to staging (manual,
  human-approved) before the behaviour is exercised.

---

## ADR-016: Real Azure Document Intelligence OCR Adapter, a Cross-Document OCR Budget, and Fixing the `primary_facts` Fact-Type Gap (Phase 32A Slice 5B.2)

**Date:** 2026-08-09
**Status:** Accepted — merged (`768da0c` PR #81, `3187298` PR #82 hotfix,
`6947bcf` PR #83, `007d398` PR #84), deployed, and **CLOSED + STAGING-VALIDATED
as a FOUNDATION**, with an explicit efficacy caveat on live OCR invocation. See
the **Addendum (2026-08-09 closure)** at the end of this entry for the
post-implementation record; the Context/Decision/Alternatives/Consequences
below are the original pre-implementation design and are left unchanged.

### Context

ADR-014 shipped the `OcrProvider` interface + an honest `NoOpOcrProvider` and
deferred the real adapter, explicitly flagging it as needing "resource
provisioning + admin sign-off." ADR-015 (Slice 5B.1) then proved, live on
staging, that the ONE failure mode OCR can rescue — `STATUS_METADATA_ONLY` +
`FAILURE_SCANNED_NO_TEXT`, a valid PDF with no extractable text layer — is
reachable and correctly distinguished from encrypted/malformed documents. This
slice builds the real adapter behind that same interface and wires it into the
one place the ADR-015 classification exists for: the issuer-IR extraction path.

Independently, a Slice 5B.1-era gap was found: `council.py`'s `_primary_facts()`
matched only the literal `"company_ir_financial_fact"` source_type, so a
SEC/XBRL-sourced fact (`SEC_DOCUMENT_FACT_TYPE`) — including the already-live
AAPL `cash_and_equivalents` fact proven on staging — never populated the
structured `primary_facts` list even though its citation resolved correctly.

### Decision

**1. The real adapter extends the existing seam; it does not replace it.**
`AzureDocumentIntelligenceOcrProvider` implements the same `OcrProvider` ABC as
`NoOpOcrProvider`, using the official `azure-ai-documentintelligence` SDK's
`prebuilt-layout` model (generic layout + table recognition — the input is an
arbitrary annual report, not a specialized document type). `OcrResult` gains one
additive field, `tables: list[ExtractedTable]` — without it, OCR output could
never reach `validate_extracted_facts()`, which only ever reads
`extraction.tables`, never bare excerpts.

**2. `get_ocr_provider()` resolves the real adapter ONLY when the flag is on AND
an endpoint is configured.** `primary_document_ocr_enabled` stays the code
default `false`. Even with it flipped on, an empty
`azure_document_intelligence_endpoint` (the default) still returns
`NoOpOcrProvider` — this is what lets the flag be turned on safely before the
Azure resource exists, mirroring ADR-014's own deferral framing. Auth is
managed-identity-first (`DefaultAzureCredential`, matching the App Service
system-assigned identity + Key Vault RBAC already wired in
`infra/azure/modules/appservice.bicep` / `keyvault.bicep`) with an API-key
fallback (`AzureKeyCredential`) only when a key is explicitly configured (local
dev). A credential-resolution failure degrades to `NoOpOcrProvider`, never a
crash.

**3. OCR is invoked at the LOWEST-blast-radius point: inside
`live_fetchers.py::_artifact_from_fetch`.** Immediately after native extraction
resolves to `metadata_only` / `scanned_no_text`, an injected `ocr_provider` is
tried. This required zero signature changes to
`collect_company_source_evidence` / `CompanyIrConnector` for the *provider*
itself; the only new parameter threading is a shared `OcrBudget` (cross-document
counter), added the same way the existing `primary_document_reuse` lookup
already threads through that exact call chain. **Scoped to the issuer-IR leg
only** — SEC filing bodies never trigger OCR (EDGAR filings are native
HTML/text, not scanned images), so no hook was added there.

**4. OCR is metered INSIDE the existing budgets, never as a new phase.** The
60s `primary_document_ingestion_budget_seconds` aggregate window and the 45s
`primary_document_total_timeout_seconds` per-document cap are unchanged; a new
`primary_document_ocr_timeout_seconds` (20s default) is carved OUT OF the
per-document cap, not added on top — there is no spare budget between the 60s +
150s (council) hard caps and the ~230s gateway timeout to add a third phase. A
new `primary_document_max_ocr_documents_per_run` (2, smaller than
`primary_document_max_docs_per_issuer`=3) bounds cross-document cost via
`OcrBudget`. OCR calls stay sequential — no `asyncio.Semaphore`/`gather`
fan-out is introduced; this codebase has no existing parallel-fan-out
precedent, and the tight budget does not justify introducing one now.

**5. Deterministic, bounded page selection — never every page.** A PDF's
outline/bookmarks (metadata, readable even on a fully scanned document) are
matched against a fixed financial-statement heading keyword list; unmatched or
outline-less documents fall back to the first `primary_document_max_ocr_pages`
pages (unchanged default, 5). Both paths are capped and never expand.

**6. Merge semantics promote on ANY usable signal, not average confidence.** OCR
excerpts/tables are appended with their REAL Azure confidence (never filtered at
the mapping layer); the document is promoted from `metadata_only` to `extracted`
only when at least one recovered item clears
`primary_document_ocr_min_confidence` (0.4). Below that bar, the document stays
`metadata_only` with the new `ocr_low_confidence` failure code — never promoted
to evidence or a fact, but not silently discarded either (retained on the
extraction object). A promoted document's `extraction_method` becomes `ocr`
outright (this branch is only reached when NO native content existed, by
construction of the trigger condition), and `validate_extracted_facts()` runs
completely unchanged — it already had full `METHOD_OCR` confidence-dampening,
`ocr_derived` flagging and human-review-note support wired from Slice 5.

**7. A new balance-sheet identity check.** `extracted_fact_validator.py` gained
`assets ≈ liabilities + equity` (new `FIELD_TOTAL_LIABILITIES` /
`FIELD_TOTAL_EQUITY` labels), mirroring the existing debt/asset subtotal-check
pattern. Because a 3-way identity gives no way to single out which figure is
wrong, a mismatch downgrades all three candidates to `excerpt_only` — never a
partial guess.

**8. 8 new `FAILURE_OCR_*` codes, zero new `ATTEMPT_*` statuses.** Every OCR
failure mode (too large, page-limit exceeded, timeout, throttled, provider
error, malformed result, low confidence, OCR budget exhausted) resolves into
one of the THREE existing durable statuses (`metadata_only` / `timeout` /
`extraction_failed`) via `ingestion_status.py`'s single mapping dict — the
established Slice 5B.1 pattern, never a parallel vocabulary.

**9. The `primary_facts` fix reuses an already-defined frozenset.**
`_primary_facts()` now matches `_DOCUMENT_FACT_TYPES` (already used by
`_primary_document_summary()`) instead of the single literal
`"company_ir_financial_fact"` string — a one-line change with a regression test
pinned to the AAPL `cash_and_equivalents` shape (fails against the pre-fix
code, passes after).

**10. No migration.** An OCR-recovered excerpt persists through the EXISTING,
unchanged `excerpts_json` write path the moment OCR promotes a document to
`extracted` — `excerpts_json` is a bounded JSON array of excerpt dicts, each
already carrying its own `extraction_method`, so `"ocr"` needs no schema
change at all. `document_ingestion_attempts` needs no new columns either — the
8 new failure codes are plain strings fitting the existing `failure_code`
column, and `extraction_method="ocr"` already fit the existing free-string
column. **Explicitly NOT done in this slice, and NOT claimed as done:**
`PrimaryDocumentExtraction.tables` (native OR OCR) is still not persisted
anywhere — a pre-existing gap from Slice 5 that this slice does not close —
and OCR provider/model/version metadata is not yet persisted either; both
remain a follow-up (would fit as new `excerpts_json` keys, still no migration
expected, but that is a claim for whoever implements it to verify, not one
made here). Alembic head stays `014`.

### Alternatives rejected

- **Threading `ocr_provider` through every layer's signature (`CompanyIrConnector`
  → `collect_company_source_evidence` → `council.py`) as a first-class parameter
  at each hop.** Rejected in favor of resolving/calling it at the single
  lowest-blast-radius point; only the cross-document `OcrBudget` needed genuine
  threading, using the pre-existing `primary_document_reuse` pattern rather than
  inventing a new one.
  A LOWER-blast-radius alternative (resolving `get_ocr_provider()` directly
  inside `live_fetchers.py` with no parameter at all) was considered but
  rejected: it would have made the OCR sub-flag untestable in isolation from
  global settings and broken the existing dependency-injection test pattern
  every other extractor callable in this module already follows.
- **A new `_OCR_BUDGET_SHARE` sibling to `_SEC_BUDGET_SHARE`.** Rejected — OCR is
  not a third leg drawing on the aggregate ingestion budget; it is a mode inside
  the per-document extraction call already bounded by whatever budget was
  threaded to that document.
- **Filtering low-confidence OCR excerpts out of the mapping layer.** Rejected —
  it would silently discard bounded evidence the task explicitly requires be
  retained (labelled, not promoted). Confidence-based promotion is decided once,
  at the merge point, not duplicated across layers.
- **A new column for OCR provenance.** Rejected for now: nothing in this slice
  requires OCR provider/model/version to be independently queryable via SQL.
  Persisting it at all (as a new `excerpts_json` key, same as table persistence
  — see Decision 10) is deferred, not built in this slice; a migration is
  still not expected to be necessary when it is.

### Consequences

- No new public endpoint; no arbitrary-URL fetch surface — the Azure client
  always talks to the ONE code-configured endpoint, never a caller-supplied URL.
  No secret (endpoint, key, extracted text) is ever logged; SDK exceptions are
  classified down to `type(exc).__name__` + HTTP status class only, mirroring
  `azure_openai_client.py`.
- `azure-ai-documentintelligence` + `azure-identity` are the FIRST `azure-*`
  Python packages in this repo. Both are Microsoft-official, narrowly scoped,
  and lazily imported so a NoOp-only deployment never needs them installed at
  runtime for correctness (they are still declared in `requirements.txt` since
  Oryx installs the full deploy manifest regardless of flag state).
- OCR-derived facts remain confidence-capped below "high" and `needs_human_review`,
  exactly like every other extracted fact; `publication_ready` stays false.
- **Azure Document Intelligence resource provisioning does not exist yet** — no
  Bicep resource, no staging app setting. This is a genuine human-gated
  infrastructure blocker, consistent with ADR-014's own deferral. All code is
  implemented and unit-tested against a FAKE Azure SDK client double; the "real
  OCR proof" staging-validation criterion (an actual Azure DI call against a
  genuinely scanned issuer document) cannot be executed until a human
  provisions the resource and supplies the endpoint (+ optionally a key) as a
  staging app setting under the existing human-approval gate. **(This was true
  when this ADR was written; see the Addendum below — the resource is now
  provisioned, but the "real OCR proof" criterion specifically still was not
  met.)**
- CFR's live encrypted annual-report PDF is confirmed NOT a valid OCR proof
  candidate (it never opens even with the empty password, so it cannot be
  rasterized) — a genuinely scanned document is a separate, still-open target
  for live proof; the offline `make_pdf_with_image` fixture is the guaranteed,
  deterministic proof in the interim.
- Not yet staging-validated. No migration required — Alembic head stays `014`.

### Addendum (2026-08-09 closure) — post-implementation record, appended not rewritten

Merge chain: PR #81 `768da0c` (original slice) → PR #82 `3187298` (hotfix) →
PR #83 `6947bcf` (docs-only Bicep module) → PR #84 `007d398` (Goodwin PLC
registry entry), all squash-merged to `main`, all auto-deployed. No migration
— head stayed `014` throughout. Full evidence:
`docs/development/closures/phase-32a-slice5b2.md`.

- **The Azure Document Intelligence resource is now provisioned on staging**
  — `ib-stg-docintel` (`Microsoft.CognitiveServices/accounts`, kind
  `FormRecognizer`, SKU **F0/free**, region **westeurope**, `ib-stg-rg`),
  deployed via a standalone scoped `az deployment group create` against
  `infra/azure/modules/documentintelligence.bicep` alone (deliberately not a
  full `main.bicep` apply — see the closure report for why). Authentication
  runs on the **API-key fallback, not managed identity**: the deploying
  identity holds only subscription-level Contributor, which excludes
  `Microsoft.Authorization/roleAssignments/write`, so the managed-identity
  RBAC grant this ADR's Decision 2 designed for could not be applied this
  round. `PRIMARY_DOCUMENT_OCR_ENABLED` flipped absent→`true` and is **KEPT
  ON**.
- **An empirically-discovered F0-tier constraint not anticipated in the
  original design.** Direct HTTP probes against the real provisioned
  resource (before any code change) confirmed the free tier rejects any
  request over **~3.5 MB regardless of the `pages=` restriction parameter**
  — the whole uploaded body is size-checked, not just the analyzed subset.
  This required adding `extract_page_subset()` (pypdf) to pre-filter the OCR
  upload to only the selected pages before sending to Azure, plus
  `page_number_map`/`_remap_page_number` so a citation's page number always
  reflects the true original page, never a subset-local position.
- **Two genuine corruption/misclassification bugs were found and fixed live
  during staging validation (PR #82), not anticipated by this ADR:** (1)
  `source_document_extraction_max_bytes` (5 MB default) silently truncated
  real large annual-report PDF downloads, corrupting the trailer/xref table
  and causing `select_ocr_pages()`/`_try_ocr()` to bail before ever calling
  Azure — misclassifying ASML's two real 23.9 MB/25.3 MB annual reports as
  `scanned_no_text`. Fixed by raising the byte cap to 35 MB. (2) The same
  truncation produced a false `/Encrypt` match in the tail-4096-byte
  encryption check, so CFR's annual-report PDF — documented in this
  project's own Slice 5B.1 closure as genuinely password-protected — is now
  confirmed to extract natively and was **never actually encrypted**; that
  prior claim is retracted (see the closure report's "Known-issue update").
- **Efficacy caveat — a real, live Azure Document Intelligence OCR call was
  not observed on staging**, despite three full validation rounds across 13
  real registered issuers (12 plus Goodwin PLC/GDWN.LSE, added specifically
  for this purpose) and the two bug fixes above. Not a code defect — every
  *other* live invariant this ADR designed (gating, connectivity, budget,
  retry, security, reuse) is staging-proven; the reason is structural (8 of
  13 issuers have zero discoverable documents; ASML/CFR turned out to be
  genuinely native-extractable once the two bugs were fixed; Goodwin PLC's
  genuinely-scanned document ranks too low among candidates to reach without
  risking the shared ~230s Azure gateway ceiling). Full detail in the closure
  report.
- **A validation subagent's own diagnostic tool output briefly exposed the
  real `AZURE_DOCUMENT_INTELLIGENCE_API_KEY` value once** (never in
  application logs, never in a persisted staging artifact). Disclosed
  immediately, contained the same session via key rotation. See
  `docs/SECURITY.md` and the closure report.
- Balance-sheet identity check (Decision 7) remains unit-test-only — no live
  table currently carries `total_assets`/`total_liabilities`/`total_equity`
  together.
- **Slice 5B.3 (admin API + web visibility) is next. The complete Slice 5 and
  Phase 32A are NOT closed.**

---

## ADR-017: LVMH Evidence-Chain Corrective Trilogy + the Real Cause of the Missing Live Azure OCR Call (Phase 32A completion campaign)

**Date:** 2026-08-21
**Status:** Accepted — merged (`2dfdc6a` PR #115, `9e1865b` PR #116, `3ef4453`
PR #117, `9ce6bdf` PR #118), deployed, staging-validated live.

### Context

A live end-to-end trace of the MC/LVMH DiscoveryCandidate evidence path
(official H1 2026 results HTML) against the Group-headline target facts found
three real, generic (non-issuer-specific) defects spanning three different
layers of the pipeline, plus — independently — the true root cause of a
long-standing gap: no real Azure Document Intelligence OCR call had ever been
observed on staging across multiple prior validation rounds (ADR-016).

### Decision

**1. Parser layer (PR #115).** `_PERIOD_QUALIFIER` (`primary_fact_parser.py`)
did not recognize "(the) first/second half of `<year>`" phrasing — standard
financial-reporting English, not LVMH-specific. The unconsumed phrase fell
into the generic label→value gap, which grabbed the trailing YEAR as though it
were the metric's own money value (e.g. "...for the first half of 2026 came
to €8.7 billion" parsed as `2026`). That bogus prose candidate then conflicted
with (and fail-closed-rejected) the correct table candidate for the same
field/period — the true cause of a `recurring_operating_profit` gap. Also
fixed: the table row-label matcher for equity required "total equity" /
"shareholders' equity" and never matched a standalone "Equity" row label
(anchored to the WHOLE cell only, so it can never collide with unrelated
prose); and `_best_candidate` ranked raw excerpt relevance-confidence ahead of
scale precision, letting a rounded prose mention outrank an agreeing, more
precise table figure.

**2. Evidence-selection layer, TWO independent call sites (PR #116 + #117).**
The financial-fact diversity key (`financial_fact_diversity_key`) is
intentionally `(field, scope)` with no period component, so a
comparison-period and a current-period fact for the same field compete for one
bounded round-robin slot. `llm.evidence_budget._apply_category_budget` and
`company_evidence._prioritize_ir_items` BOTH run this round-robin
independently — PR #116 fixed period-recency ordering in the first only; a
fresh live MC run then proved `total_equity` STILL showed the stale 2025
figure, because `_prioritize_ir_items` is the site that actually determines
which facts survive the raw evidence-pack entry cap and had never been fixed.
PR #117 extracted the period-recency rank into one shared function
(`financial_fact_categories.primary_fact_period_rank`) and applied it at BOTH
sites, plus reordered `company_ir._artifact_to_evidence` to add a document's
validated facts before its prose excerpts (defense-in-depth, matching that
method's own already-documented intent).

**3. The real reason live OCR was never observed (PR #118).** Tracing a
genuinely scanned document (Goodwin PLC's 2002 annual report — flagged in
`verified_issuer_sources.py` specifically for this purpose) through the real
pipeline surfaced a `ModuleNotFoundError`: `azure-core`'s ASYNC transport
(every `.aio` client the OCR provider calls) requires `aiohttp` installed to
make a network call at all. It was never a `requirements.txt`/`pyproject.toml`
dependency. Every real OCR attempt failed closed with a generic
`ocr_provider_error` — indistinguishable from a genuine provider outage
without direct diagnosis, which is why every prior validation round's
"efficacy caveat" concluded "not a code defect... reason is structural"
without finding this.

### Live proof (2026-08-21, staging)

- **MC/LVMH** — fresh company-analysis run: all 7 required Group H1 2026
  facts (revenue €38,644m, profit from recurring operations €8,691m,
  operating margin 22.5%, net profit €5,697m, OFCF €4,100m, net financial
  debt €8,245m, equity €69,694m) reach the Council narrative with exact
  precision, current period, Group scope. `schema_valid=true`,
  `safety_passed=true`, `publication_ready=false`, `human_review_required=true`.
- **CFR regression** — fresh run: 6/7 target facts still exact
  (sales €22,420m, OCF €4,880m, net cash €8,496m, Jewellery margin 30.5%,
  Watchmakers €107m, margin 20.0%); Group operating profit reaches Council
  evidence only as rounded prose (€4.5bn / €5bn), not the precise €4,492m
  table value — traced to an UNRELATED, PRE-EXISTING gap (the specific PDF
  excerpt carrying the precise table figure states no local year and lacks
  the positive scope signal PR #113's period-inference fallback requires;
  the underlying `extracted_documents` row is dated 2026-08-11, untouched by
  any change in this campaign — confirmed NOT a regression from this work).
- **BRBY regression** — fresh run: Burberry Group plc, LSE, GBX for price
  data, GBP only in an honest FX-risk narrative context (never as a price
  unit), `schema_valid=true`, `safety_passed=true`.
- **OCR** — real live Azure Document Intelligence invocation against
  `ib-stg-docintel` (model `prebuilt-layout`) recovered real text from
  Goodwin PLC's genuinely scanned 2002 annual report (20 pages, zero native
  text layer); persisted to the staging DB (`extracted_documents.
  extraction_method='ocr'`); a second `load_reusable_documents` lookup
  correctly found the persisted document by canonical URL, confirming the
  real pipeline's reuse branch would fire with zero additional Azure calls.

### Consequences

- CFR's Group operating-profit precision gap remains open — a genuinely
  different, deeper root cause (local period-inference on a specific PDF
  excerpt) than anything this campaign's shared-evidence-path changes
  touched. Per this project's own standing rule, the now-working CFR PDF
  path was NOT redesigned to chase it.
- `aiohttp` is now a hard runtime dependency wherever
  `PRIMARY_DOCUMENT_OCR_ENABLED=true` — any future dependency-pruning pass
  must not drop it silently.
- Deep Field Review and a full manual-QA pass were not re-run in this
  session; see the session handoff for the current closure verdict and
  remaining limitations.

---

## ADR-018: Async Full-Analysis Job — Decoupling "Run Full Analysis" From the Request/Response Cycle

**Status:** Accepted
**Date:** 2026-08-22
**Context:** Product-readiness corrective slice (manual QA on discovery run
`eee7b0c7`, NVDA candidate `f1e74d3e`)

### Context

`POST /api/v1/market-discovery/candidates/{id}/run-analysis` ran the entire
pipeline **inline inside the HTTP request**:

```
company-analysis workflow → primary-document ingestion → 8-agent LLM council
→ final-report assembly → persistence
```

On staging this regularly exceeded the shared **~230s Azure App Service gateway
ceiling**. Real manual QA: the admin clicked *Run Full Analysis*, the browser
returned **HTTP 504**, and the backend nevertheless completed successfully and
persisted a valid final report. The user could not tell the difference between
"failed" and "succeeded but the gateway gave up", and a retry launched a
*second* expensive council run for the same candidate.

Two facts made this unambiguous:

- The candidate's own final report `a42c9295` was persisted at 12:57:27 with
  complete discovery lineage — the analysis had succeeded.
- A separate, later report `a1781d03` (13:04:25) carried
  `"No screening candidate linked to this report."` — it came from a different
  generation path entirely, not from the candidate's run-analysis.

This is also the exact structural blocker recorded in the Phase 32A final-status
note ("decouple primary-document ingestion from the synchronous
report-generation HTTP request") — closing it here removes the 230s ceiling as a
design constraint for that work too.

### Decision

Make the endpoint an **async job**, mirroring the already-proven Phase 28B.2
async discovery-council pattern rather than inventing a second mechanism:

1. `POST` writes a `pending` job envelope, commits, returns **HTTP 202**
   immediately, and schedules a FastAPI `BackgroundTasks` worker that runs the
   analysis in its **own** DB session (never the request-scoped one).
2. A new `GET /candidates/{id}/analysis-job` returns the current job state; the
   admin UI polls it (3s) and stops on any terminal status.
3. The envelope is stored under the candidate's existing
   `raw_signal_json["analysis_job"]` blob — **no DB migration**, the same
   technique the council uses on `DiscoveryRun.config_json`.
4. **Idempotency is part of the contract, not an afterthought**: a
   `pending`/`running` job short-circuits with `scheduled=False` and no second
   council run; a `completed` job needs explicit `force=true` to re-run; a
   `running` job older than 30 minutes is treated as abandoned (BackgroundTasks
   are process-local and not durable) and becomes restartable.
5. Every worker failure path persists a **terminal** envelope, so a job can
   never stick in `running`.
6. A candidate analysed before this change has no envelope but may carry
   `analysis_report_id`; that legacy state is normalised into a synthetic
   `completed` envelope so the UI never offers to re-run finished work.

### Alternatives rejected

- **Raise the gateway timeout.** Explicitly rejected: it does not bound the
  runtime, it just moves the cliff, and it would make every future
  evidence-gathering improvement (deeper ingestion, OCR) a latency liability.
- **Azure Service Bus / Functions worker.** Correct long-term, disproportionate
  now. `BackgroundTasks` is what the discovery run and discovery council already
  use; adding a second, heavier mechanism for one endpoint would fragment the
  operational model. The stale-job rule is the explicit, documented mitigation
  for BackgroundTasks' lack of durability.
- **A new `analysis_jobs` table.** A migration for state that is 1:1 with a
  candidate row, already has a JSONB blob, and is read only through the
  candidate. The council precedent (`config_json`) applies directly.

### Consequences

- The browser request that starts an analysis now returns in milliseconds. The
  admin sees an honest *Analysis queued → running → complete* progression and
  the report link appears via polling, not via a blocked request.
- A failed job now surfaces the **real backend failure**, not a gateway 504.
- `RunCandidateAnalysisResponse` gains `started_at`, `completed_at`,
  `workflow_status` and `error`. `status` is a **job lifecycle** state and is
  deliberately drawn from a vocabulary with no investment-action words in it.
- Because the work no longer runs inside the request, per-request wall-clock
  budgets (council retry budget, primary-document ingestion budget) are no
  longer bounded by the gateway. This ADR does **not** change any of those
  budgets — that is separate, deliberate work.

---

## ADR-019: ONE Canonical Post-Ingestion Evidence Inventory for the Final Report

**Status:** Accepted
**Date:** 2026-08-22
**Context:** Product-readiness corrective slice (manual QA of NVDA final report
`a42c9295`, discovery run `eee7b0c7`)

### Context

A single generated final report contained mutually contradictory statements
about the same company at the same moment. The LLM council correctly reported
NVDA's FY2026 SEC/XBRL statements — revenue $215,938m, net income $120,067m,
OCF $102,718m, assets $206,800m, liabilities $49,500m, equity $157,300m — while
other sections of that same report said:

| Surface | What it said | Reality |
|---|---|---|
| Data Availability Summary | `fundamentals_available=true`, `available_count=0`, `available_fields=[]` | 15 available fields |
| Financial Snapshot | "Fundamentals not available. Run with EODHD provider or add T1 filings." | Full SEC XBRL statement set |
| Bull case | "cross-referencing with fundamentals (not yet sourced)" | Sourced |
| Bull case evidence | "Price history available from **sec_edgar**: 251 data points" | Source list said `eodhd_price_only` |
| Source quality | "Annual report / 10-K / 40-F — T1_primary_filing required for financials" | 10-K XBRL statements already sourced |
| Valuation Guard | "No current market price or share price data is provided" | Report showed latest close 214.72 USD |
| News & Catalyst | `source_classes_successful: [… sec_filings]` + `filing_event_count: 0`, while listing 4 SEC filing events | Both true statements about the same data |
| Internal memo | "the reports were scanned or JS-gated" | Two SEC 8-K HTML docs were natively extracted |

These were **not one bug**. Six independent causes were traced:

1. **Key-name mismatch.** `financial_data_agent_output_to_dict` emitted
   `available_financial_data` / `missing_financial_data`; every reader
   (`_build_data_availability_summary`, the research memo, `scoring_engine`)
   asked for `available_count` / `available_fields` / `missing_count` /
   `warnings_count` and silently got the `0` / `[]` defaults. Every test
   covering those readers passed a hand-built dict using the READER's key
   names, so the mismatch was invisible to the suite.
2. **EODHD-only fundamentals.** `_build_financial_snapshot` recognised only
   `state["fundamentals_data"]` (the T5 EODHD shape). SEC XBRL statements live
   in `company_snapshot["fundamentals_summary"]` (T2) and were ignored.
3. **Price provenance inferred from the company provider.**
   `price_history_summary` already carries its OWN `provider_name` /
   `source_tier`, but the bull, risk and financial-data agents read the
   *snapshot-level* `provider_metadata.provider_name` instead.
4. **Unconditional gap assertions.** `source_quality_agent` always appended
   "Annual report / 10-K / 40-F required for financials". Those false gaps flow
   into the bear case, the risk agent, and the council's `known_gaps` — a
   direct input to the committee chair's label. Asserting a gap that is closed
   is what pushed an 8/8 council with regulator-backed financials to
   `insufficient_data`.
5. **Stale deterministic sections.** Bull/Bear/Risk/Valuation-Readiness are
   computed at workflow time — before citations, before ingestion, before the
   council — then rendered verbatim beside the council's better-informed
   narrative.
6. **Naive lexical safety scans.** `bull_case_agent` / `bear_case_agent` used
   `text.upper()` + `in` against a word set ("product cycles may shorten" →
   "SHORT"), and `neutralize_forbidden_terms` used blanket `\b` regexes
   ("sell-side" → "[rating redacted]-side").

### Decision

Introduce **one** canonical post-ingestion evidence inventory,
`app/services/canonical_evidence.py`, and derive every quality surface from it:

- `normalize_financial_data_summary` — emits BOTH key spellings, once, at the
  serialisation boundary and again at report-assembly entry. Fixes cause 1 for
  all consumers simultaneously, including the scoring engine.
- `resolve_fundamentals` — recognises all three channels (issuer primary
  document T1 → SEC XBRL T2 → EODHD T5) at their TRUE tiers, applies the
  project's source-priority rule (a weaker source never overwrites a stronger
  current fact), and retains every channel in `channels` so a conflict is
  exposed rather than silently resolved.
- `resolve_price_provenance` — reads the price feed's own attribution first.
- `build_evidence_channels` — reports issuer-primary-document /
  regulator-structured-facts / regulator-filing-events / issuer-newsroom /
  persisted-citations **separately**, because they are five different things
  and the absence of one never implies the absence of another.

Alongside it:

- **Deterministic sections are REBUILT after reconciliation** (Option A of the
  three offered). Only sections the workflow actually produced are refreshed —
  rebuilding a section the workflow never ran would invent content and turn an
  honest `available: false` into fabricated analysis. The original workflow
  draft is retained in full as the legacy report for audit.
- **A poisoned summary is never rebuilt.** If an upstream summary already
  carries forbidden language, the original is kept so the final safety gate
  flags it. Overwriting it with clean deterministic output would launder
  compromised state past the gate and hide the compromise from the admin.
- **Catalyst counts.** Event dates are normalised (SEC ISO vs press RFC-822 —
  `"W" > "2"` for every raw string, so press releases sorted above every filing
  and a 20-item cap dropped all four SEC filings), and truncation now reserves a
  bounded floor per source class. The two count axes (source class, materiality)
  are reported side by side with an explicit "do not sum these" note.
- **Materiality is classified separately from evidence strength** from a closed
  keyword vocabulary. A T1 issuer post is fully credible AND can be low-signal;
  a Glassdoor CEO ranking no longer outranks an exclusive infrastructure
  agreement. Nothing is discarded — only the ordering changes. No LLM scoring.
- **Safety detection is unified on `safety_terms`**, which is word-bounded and
  tier-aware, plus a shared `neutralize_text` that removes exactly what the gate
  would flag (so `scan_text(neutralize_text(x))` is always empty and any string
  the gate accepts is returned byte-identical). `SHORT` is added as a Tier-2
  rating word behind a guard that excludes "short-term / short-dated /
  short-lived / short seller / short interest". The recommendation gate is not
  weakened: BUY NVDA / SELL the stock / rating: HOLD / we recommend SHORT /
  price target / fair value all still block.
- **Council calibration, not restriction.** A new `INFERENCE_STRENGTH_RULES`
  block in every agent's system prompt requires the conclusion to match the
  weight of the evidence (one contract ≠ durable moat; filing cadence ≠ good
  governance; employee-approval ranking ≠ management quality). The chair's
  sufficiency instruction is made source-type aware: regulator-backed
  structured statements ARE primary financial evidence, and a missing issuer
  PDF is a *narrative* gap (`requires_more_evidence`), not `insufficient_data`.
- **A price/market-metric FLOOR in the evidence budgeter**, so the Valuation
  Guard reasons about the real current price instead of asserting to a human
  reader that the platform has none.

### Alternatives rejected

- **Patch each contradicting section individually.** That is how the report got
  six independent truth calculations in the first place. The observable symptom
  would go away and the next section added would reintroduce it.
- **Delete the legacy deterministic sections (Option B/C).** They carry real
  analysis and real audit value. Rebuilding them from reconciled inputs keeps
  one coherent narrative without discarding capability.
- **Change the committee label directly.** Explicitly rejected in the brief and
  correct: the label was a *symptom* of false gap assertions. Fixing the gap
  inputs is the real correction; `insufficient_data` remains reachable when
  material evidence really is absent.
- **Loosen the safety gate to stop the false positives.** The gate was already
  correct; the offenders were two older local scanners that predated it. They
  now delegate to it.

### Consequences

- Every final-report quality surface now derives from one inventory. Adding a
  new surface means reading that inventory, not writing a seventh truth
  calculation.
- Reports gain an `evidence_channels` section (rendered in the admin UI) and
  `financial_snapshot` gains per-field SEC statement datapoints carrying their
  own `source` / `source_tier` / `period` / `form_type`.
- `CatalystEvent` gains `materiality` / `materiality_reason`; `CatalystSummary`
  gains `decision_relevant_count` / `low_signal_count`. Additive, no migration.
- One new setting, `LLM_COUNCIL_EVIDENCE_PRICE_TREND_FLOOR` (default 2, bounded
  by the existing cap).
- No database migration. No public endpoint. No change to publication gating:
  `publication_ready=false` and `human_review_required=true` throughout.

---

## ADR-020: TPM-Aware, Async-Era LLM Councils — Provider Token Pacing, Chair Reserve, Deterministic Chair-Input Compaction, and Failure-vs-Judgement Semantics (Phase 32A TPM slice)

**Date:** 2026-08-22
**Status:** Accepted

### Context

The staging Azure OpenAI deployment (`gpt-4.1-mini`, GlobalStandard capacity 10
≈ 10k tokens/minute) cannot absorb a full 8-agent council (~48k tokens) fired
back-to-back. The committee chair runs last and is the largest request
(evidence pack + all prior agent summaries), so it repeatedly failed with
`LLMRateLimitError` — reproduced 3x live. Its deterministic fallback then wrote
`committee_label="insufficient_data"`, which downstream surfaces could not
distinguish from an evidence-based chair judgement.

Separately, the company council's retry budgets (150s total / 45s reserve /
30s retry-after cap / 20s backoff cap — ADR-013) were sized for the removed
constraint that the council ran inline in an HTTP request under the ~230s Azure
gateway timeout. Since ADR-018 the full analysis is an async job, so those
budgets encoded a dead constraint: a ~48k-token council against a 10k-TPM
deployment needs ≥5 minutes of refill windows, and the 30s retry-after cap
guaranteed a clamped retry fired back into the same exhausted window.

### Decision

1. **Async-era budgets (company council):** total 150→600s, critical reserve
   45→180s, retry-after cap 30→90s, backoff cap 20→60s. Discovery/field-review
   retry-after + backoff caps raised in lockstep (their totals were already
   async-sized). All still strictly bounded; jobs always terminate.
2. **One shared token-pacing primitive** (`app/services/llm/token_pacer.py`),
   used by ALL THREE councils (mirroring how `retry_engine` is the one retry
   primitive): a process-local sliding-window tokens-per-minute pacer keyed by
   (provider, deployment) — so concurrent councils share one window — with an
   explicit chair token reserve non-chair agents cannot consume. Pacing is
   ADVISORY: after a bounded wait the attempt always proceeds (the provider
   429 + bounded retries are the correctness backstop), so imperfect token
   estimates can never wedge a council or skip an agent. Disabled at the
   default `LLM_COUNCIL_TPM_CAPACITY=0`.
3. **Deterministic chair-input compaction** (`LLM_COUNCIL_CHAIR_PRIOR_SUMMARY_
   MAX_CHARS`, default 0 = off): each completed agent's line in the chair
   prompt keeps a word-boundary-truncated summary PLUS deterministic extracts
   of its structured fields (cited evidence ids, top risks, unsupported-claim
   count), and failed agents are named. Extraction only — never an LLM
   re-summarization, never a new claim, dissent never dropped.
4. **Failure-vs-judgement semantics:** every council result now records
   `chair_synthesis_basis` (`"llm_chair"` = evidence-based, possibly after
   retries; `"deterministic_fallback"` = the label is a failure default),
   `chair_attempts`, and `chair_error_type` (provider error class name only).
   Persisted into the council report payload (`committee_label_basis`),
   metadata, the final-report summary schema, and the research memo's
   council-disagreement section. `insufficient_data` can no longer masquerade
   as a judgement.
5. **Token observability:** clients capture real provider usage
   (`usage_metadata`, falling back to a ~4-chars/token estimate marked
   `estimated`); every `*_agent_completed/failed` event carries
   prompt/completion/total tokens + retry-after; every council emits a
   `*_run_summary` / completed event with totals, 429 count, retries, paced
   wait, chair attempts and basis. Counts only — never prompts, completions,
   or credentials.
6. **Coherent stale threshold:** the analysis-job abandoned threshold is now
   `max(ANALYSIS_JOB_STALE_AFTER_MINUTES, derived council+pacing+ingestion
   worst case + margin)` (`market_discovery_service.analysis_job_stale_after_
   minutes()`), so raising a council budget can never mark a legitimately
   long-running job stale mid-council.

### Alternatives considered

- **Only raise Azure capacity.** Near-free (GlobalStandard quota is billed per
  token) and recommended IN ADDITION, but insufficient alone: budgets encoded
  a dead constraint, concurrency recreates starvation at any fixed capacity,
  and the fallback-labeling gap is a correctness bug regardless of quota.
- **A durable queue / distributed scheduler.** Rejected for this slice —
  process-local pacing matches the current single-instance BackgroundTasks
  architecture; Service Bus belongs to the scale roadmap.
- **Porting the field-review council onto `retry_engine.run_with_retries`.**
  Deferred: it shares the new pacing/usage primitives and the budget/semantics
  changes, but its (pre-existing, engine-mirroring) retry loop was left
  untouched to avoid destabilizing a validated council in the same PR.

### Consequences

- New settings: `LLM_COUNCIL_TPM_CAPACITY`, `LLM_COUNCIL_CHAIR_TOKEN_RESERVE`,
  `LLM_COUNCIL_PACING_MAX_WAIT_SECONDS`, `LLM_COUNCIL_INITIAL_PASS_DELAY_
  SECONDS`, `LLM_COUNCIL_CHAIR_PRIOR_SUMMARY_MAX_CHARS`,
  `ANALYSIS_JOB_STALE_AFTER_MINUTES`; changed defaults for the four company-
  council budget knobs + the two other councils' backoff/retry-after caps.
  Pacing + compaction are OFF by default — a plain deploy changes only budget
  ceilings and additive payload keys.
- Additive payload keys (`committee_label_basis`, `chair_attempts`,
  `chair_error_type`, `token_usage`) on all three councils' persisted outputs.
- No database migration (head stays 017). No publication-gating change:
  `publication_ready=false`, `human_review_required=true` throughout.

### Corrective (2026-08-23, live staging) — budgets must be sized WITH pacing

The first live staging run of ADR-020 exposed a defect in the decision itself.
Enabling the pacer on all three councils while raising only the COMPANY
council's wall budget starved the tail of the other two: a real 7-candidate
discovery run completed 6/8 agents with **both** `run_red_team` and
`discovery_chair` failing `budget_exhausted` — the chair never ran at all.

Arithmetic: at 10k TPM with ~3k-token requests, an 8-agent initial pass needs
~2.4 sliding windows (~144s) of *pure pacing* before any call latency or
retries. The discovery council's non-reserved slice was 240s (300 − 60), which
that pass consumed. Three consequences, all fixed:

1. `LLM_COUNCIL_PACING_MAX_WAIT_SECONDS` 240 → **90**. The window is 60s, so
   the maximum *useful* wait is one rotation; 240s let a single agent's
   advisory wait consume most of a council's budget.
2. Discovery council 300 → **900s** total, 60 → **300s** reserve; field review
   600 → **900s** total, 120 → **300s** reserve. A reserve must cover the two
   protected agents' *pacing waits*, not merely their calls.
3. `chair_error_type` is never empty on failure. A chair that never got an
   attempt now reports `budget_exhausted` (vs a provider error class, vs
   `quarantined_or_unparsed` for a content rejection) — previously it read as
   "no error" beside a failure-default label, the exact silent downgrade this
   ADR set out to eliminate.

Also fixed: `chair_synthesis_basis` / `chair_attempts` / `chair_error_type` /
`token_usage` were persisted by the discovery and field-review councils but
**undeclared on their API response models**, so Pydantic silently dropped them
— the API could show `run_quality="failed"` with no way to distinguish an
evidence judgement from a throttle.

The regression guard is structural rather than value-pinning: tests now assert
`reserve >= 2 x pacing_max_wait` and that every council's non-reserved slice
absorbs a full paced initial pass with headroom, for all three councils. That
invariant, not the specific numbers, is what was missing.

### Corrective (2026-08-23, live staging) — Deep Field Review chair output budget

A real 7-company Deep Field Review completed 7/8 agents; the **chair** failed
with `LLMJsonError`. Root cause: the field-review council passed the company
council's **flat** `llm_max_output_tokens` (1200) to every agent regardless of
field size, while the discovery council (ADR: `discovery_max_output_tokens`)
already scaled with candidate count. Every field-review agent emits one
`company_notes` entry per compared company, and the chair additionally emits a
three-bucket `chair_verdict` in which every company appears exactly once — so
the chair overflows first. An unparseable reply is **permanent**: `LLMJsonError`
is not transient, and the one-shot repair reuses the same budget.

`field_review_max_output_tokens(n, is_chair=…)` now mirrors the discovery
pattern: `min(cap, base + per_company·n)`, plus `chair_base_extra +
chair_per_company_extra·n` for the chair. Defaults give 2,600 (chair) at 2
companies, 4,600 at 7, 6,600 at the supported maximum of 12 — under the 7,000
cap, so the cap guards against a raised company cap rather than clipping the
supported maximum. The same value feeds the provider call **and** the TPM
pacer's admission estimate, so the enlarged chair request is not under-counted
(which would re-introduce the 429 starvation ADR-020 removed).

Truncation is now diagnosable: clients capture the provider `finish_reason`,
and a JSON failure after a length finish raises `LLMJsonError(truncated=True)`,
surfacing as `chair_error_type="LLMJsonError_truncated"`. It remains permanent
and still routes to the explicit deterministic fallback — a truncated reply
never becomes a ranking or an evidence-based judgement.

**Recalibration (same day, after the first live run of the scaled budget).** The
scaled budget alone was not enough. With all seven companies carrying FULL
analyses (rather than the thin discovery drafts of the earlier run) the pack
grew to ~21.8k prompt tokens per agent and **seven of eight agents** truncated.
The deeper cause: the field-review prompt bounded only `summary`, leaving the
per-company `rationale` and `field_notes` claims **unbounded**, so a richer pack
made every agent write proportionally more — no fixed cap could ever be
"enough". The contract now bounds rationale/claim at `<=200 chars` (discovery
bounds its own at `<=150`, which is why its 200-per-candidate budget works) and
`field_uncertainties` at `<=150 chars each, max 6`; constants were resized to
that contract with roughly 5x headroom (chair: 3,440 at 2 companies, 6,040 at
7, 8,640 at the supported maximum of 12, under a 9,000 cap). Bounding the
contract is what makes the budget predictable rather than hopeful.

**Second recalibration + chair de-duplication.** The bounded contract took the
live 7-company review from 1/8 to **6/8** agents completing, but
`comparative_financial_quality` still exceeded 3,420 and the chair still
exceeded 6,040. Two changes: (a) the chair is now told to leave `company_notes`
EMPTY — it emits `chair_verdict` covering every company in exactly one bucket,
and only the verdict drives the persisted buckets, so emitting both made it
describe every company twice and truncate the payload that actually matters;
(b) constants resized from the observed shortfall (per-company 260→400, chair
base extra 800→1200, chair per-company 260→400, cap 9,000→14,000), giving
4,400/8,400 at 7 companies and 6,400/12,400 at the supported maximum of 12.

**Correct parameter (verified live before shipping).** The first attempt at the
above forwarded `max_tokens` and broke **every** call (0/8 agents, HTTP 400).
langchain-openai (>=1.x) already translates the constructor's `max_tokens` into
`max_completion_tokens`, so sending `max_tokens` per call put both in the
payload: *"Setting 'max_tokens' and 'max_completion_tokens' at the same time is
not supported"* (`invalid_parameter_combination`). The per-call override is
therefore sent as **`max_completion_tokens`**, verified against the real staging
deployment (200 OK at both the default and an overridden budget) before merge.
That reproduction — constructing the app's own client against staging and
calling it directly — is the cheap check that should precede any future
provider-parameter change.

---

## ADR-021: Typed Evidence Contracts and Boundary Fixtures (Phase B)

**Date:** 2026-08-23
**Status:** Accepted

### Context

Two incidents share one root cause: a producer and a consumer silently disagreed
about the shape of decision-critical state, and the disagreement surfaced as a
plausible DEFAULT rather than an error.

1. **Internal.** `FinancialDataAgent` emitted `available_financial_data`;
   report/memo/scoring consumers asked for `available_count` / `available_fields`
   and silently got `0` / `[]`. A report quoting real SEC statement facts
   rendered "Available Count = 0". Producer tests and consumer tests each
   hand-built their *own* dict, so both passed.
2. **External** (#129/#130). `AzureOpenAILLMClient` accepted a per-call
   `max_tokens` and dropped it, while the fake client honoured it. Unit tests
   were green and production used a stale default. The first correction then
   sent a parameter combination the real stack rejects (HTTP 400).

The emergency fix for (1) emitted *both* spellings. That was right at the time
but left two authoritative names, so every consumer still had to choose.

### Decision

**Typed contracts in `app/schemas/evidence_state.py`** own decision-critical
evidence state: `FieldProvenance`, `FinancialDataSummary`, `PriceSummary`,
`FundamentalsResolution`, `EvidenceInventory`.

The load-bearing property is not "Pydantic is nicer than dicts". It is that
**counts are derived, never stored**: `available_count` is a property of
`available_fields`, so "count 0 beside populated fields" is unrepresentable.
Legacy spellings are accepted at exactly one ingress (`from_payload`) and never
re-emitted; `normalize_financial_data_summary` is deprecated with zero
production callers, enforced by a test.

`fundamentals_available` is deliberately **not** on `FinancialDataSummary`:
whether a company has usable fundamentals depends on regulator and issuer facts
as well as this agent's list, and that judgement belongs to
`FundamentalsResolution`. A second, naive answer would recreate the very
"two truths" problem this phase removes.

### Contract ownership rules

1. Producers return typed state.
2. Persist/render boundaries serialize typed state; nothing else does.
3. Consumers read attributes, never undocumented dict keys.
4. Legacy aliases normalize **once**, at ingress.
5. Decision-critical facts carry field-level provenance; container provenance is
   fallback-only and marked `inherited_from_container`.
6. Fake adapters must implement the same public contract as real adapters —
   signatures are compared in CI.
7. **Provider-wrapper parameter changes require an adapter-boundary test AND
   `scripts/live_provider_smoke.py` before staging deployment.**

### Consequences

- Contract fixtures run REAL producer → serialize → JSON → REAL consumer. The
  §9 acceptance test derives its expectations from the producer object, so a
  producer rename fails CI at the boundary (verified by deliberately injecting
  the rename).
- Compact counts-only payloads are preserved explicitly rather than collapsing
  to zero — absence of names is not absence of data.
- New payloads carry `evidence_state_schema_version`. No DB migration; head
  stays 017.

---

## ADR-022: One Human-Facing Research State (Phase C)

**Date:** 2026-08-23
**Status:** Accepted

### Context

Phase B made the platform's *evidence* correct and typed. The human-facing
surfaces still exposed pipeline history: one report could describe its own
evidence as `strong`, `adequate` and `weak` in different sections; a European
discovery run emitted ~200 warning strings that were mostly the same handful
repeated per candidate; a metadata-only issuer rendered twenty sections of
"Not sourced"; generic surfaces used SEC-centric copy for non-US issuers; and
data-centre REITs persisted `sector="Financials"` beside
`industry="Real Estate Investment Trusts"` on the same row.

### Decision

**One canonical presentation of the evidence Phase B established**, in
`app/schemas/research_quality.py`:

- `SourceQualityAssessment` — four SEPARATE dimensions (identity, financial
  evidence, catalyst evidence, overall), each carrying the machine-generated
  `basis` that produced its label. Overall is the **weakest** contributing
  dimension, never an average: strong identity data must not mask absent
  financials. Computed once; sections read it rather than recomputing.
- `WarningCollector` / `WarningGroup` — canonical codes, severity
  (`info`/`warning`/`blocking`), deduplication with counts, and bounded
  groups. Grouping is presentation only: raw instances are retained, BLOCKING
  warnings are never merged, and genuinely unknown warnings are shown rather
  than dropped. Derived **at read time** from the existing `warnings` column,
  so no migration and historical runs gain the readable view for free.
- `ThinEvidenceAssessment` — a deterministic, company-agnostic trigger (no
  fundamentals AND no primary-document facts AND no catalysts) for a
  short-form research state, so failing closed stops looking broken.

**Sector classification** (`resolve_sector_classification`) resolves
provider-vs-curated disagreement deterministically — industry rule, then
curated reference, then provider — and **retains every input plus a
`sector_conflict` flag**. Previously the provider silently won. The rule is
keyed on the INDUSTRY label, never on a company, so no ticker is hardcoded.

**Legacy drafts** stay persisted and reachable, but a pre-council draft now
carries an explicit "superseded by the current final report" banner and is
labelled a *historical diagnostic draft*, not a report.

### Consequences

- Presentation only: no change to extracted values, tiers, scope, evidence
  caps, prompts, model, council scheduling or safety policy. A test asserts the
  presentation layer does not mutate the evidence inventory.
- No DB migration; warning grouping is a read-time projection, versioned via
  `warnings_schema_version`.

---

## ADR-023: Phase D1 Investigation — Why European Issuers Are Evidence-Thin

**Date:** 2026-08-23
**Status:** Accepted (investigation); implementation deferred to D1a/D1b

### Why an investigation ADR

Phase D's brief required verifying the CFR catalyst state and the actual source
architecture *before* writing connectors. Both checks changed what the
implementation should be, so the findings are recorded here rather than
discovered again later.

### Finding 1 — CFR `catalyst: insufficient` is CORRECT, not a wiring bug

The live payload shows `total_events: 0` with
`source_classes_attempted: [company_press_release, company_source_discovery,
industry_news, news_provider, sec_filings]` and
`source_classes_successful: []`. Every class was tried; none produced an event.
This is genuine source thinness, not another count/alias defect. (Note that
`sec_filings` is attempted for a Swiss issuer — harmless, but a symptom of the
US-first default.)

### Finding 2 — the regulated-disclosure connectors already exist, by design as
**reference-only**

`nordic_disclosures.py`, `six_swiss.py`, `euronext_regulated_info.py`,
`uk_fca_nsm.py` were delivered in Phase 29B.4. Their own docstrings state: *"No
network call at report time"*, emitting one bounded T2 venue **reference** plus
an honest gap, with live content retrieval explicitly deferred as "Task 2".

So Phase D is **not** "build the Nordic connector" — it is "give the existing
connectors a bounded fetch path". The `RegulatedDisclosureConnector` interface
the brief describes largely exists.

### Finding 3 — Pandora discovers ZERO documents, and the reason is generic

`primary-documents` reports `discovered_count: 0` (CFR discovers 3), so nothing
was even attempted. Live probes established why:

1. **The curated URLs were stale.** `/investor/news-and-reports/reports` and
   `/investor/news-and-reports/news` both return **404**; the site reorganised
   to `/investor/reports-and-presentations` and
   `/investor/announcements-and-events/company-announcements`. Fixed in this
   change and re-verified 200.
2. **The IR site is fetchable** — 200, ~196k chars, no bot protection. This is
   *not* the JS-gated/blocked case seen elsewhere.
3. **The documents are off-domain and extension-less.** The real artifacts live
   on the issuer's content CDN as
   `https://pandora.a.bigcontent.io/v1/static/Annual Report 2025` — no `.pdf`
   suffix, spaces in the path, and a host outside the issuer's
   `allowed_domains`. The 2025 annual report is additionally published as a
   third-party **flipsnack.com** flipbook rather than a PDF.

Extension-based discovery cannot see those links, and the domain allowlist
(correctly) refuses them. Both behaviours are right in isolation; together they
produce an honest but empty result.

### Consequence — the D1a slice this implies

1. Extend `VerifiedIssuerSource` with a curated **document CDN domain** per
   issuer, so an issuer's own content host is allowlisted deliberately rather
   than by loosening SSRF policy.
2. Discover documents by **content-type sniffing** rather than `.pdf` suffix,
   with URL canonicalisation for spaces.
3. Only then consider a Nordic fetch path (D1b).

This ordering follows the brief's own §5 preference: issuer-primary evidence
first, exchange scraping only if it remains necessary.

### What this change contains

The registry URL correction and this record only. No fetch-path, allowlist or
security change — those need their own reviewed slice.

---

## ADR-024: Issuer-Scoped Document Hosts and Content-Type Document Discovery (Phase D1a)

**Date:** 2026-08-23
**Status:** Accepted

### Context

ADR-023 established why European issuers are evidence-thin. For Pandora
specifically, three things combined to produce `discovered_count: 0`: stale
registry URLs (fixed in #135), documents hosted on a content CDN outside the
issuer's `allowed_domains`, and suffix-based discovery that cannot see an
extension-less path. Verified live: the artifact is a real PDF
(`Content-Type: application/pdf`, `%PDF-1.7`), served with spaces in the path,
from a host the fetcher correctly refused.

### Decision

**A verified issuer may delegate document hosting to curated content hosts.**
`VerifiedIssuerSource` gains `document_domains`, and `fetch_allowed_domains()`
returns the issuer's own domains plus those hosts. This is a narrow **fetch
authority**, not a source: the evidence remains an issuer publication, and the
CDN never becomes an independent source.

The trust properties that make this safe:

- **Curated only.** The set comes from the code-defined registry. Nothing
  discovered on a page can widen it, so a compromised issuer page cannot talk
  the fetcher into a new host.
- **Issuer-scoped.** One issuer's CDN is never usable by another.
- **Redirects unchanged.** The existing fetcher re-checks the allowlist on
  every hop with IP pinning, so a document host cannot be a stepping stone.
- **Page fetches stay narrow.** Only document retrieval and document-link
  discovery use the wider set; issuer HTML pages still come from issuer domains.
- **Registry invariants.** Document domains must be lowercase, fully-qualified,
  bare hostnames, non-wildcard, and must not duplicate an existing allowed
  domain.

**Discovery accepts extension-less candidates only on curated hosts.** The
suffix fast-path is unchanged; an extension-less URL is a *candidate* only when
its host is one of that issuer's curated document hosts and it does not carry a
known non-document extension. **The type decision still belongs to the
response** — `classify_content_type` reads `Content-Type` first — so a curated
host cannot make an HTML page masquerade as a PDF.

**URL spaces are encoded for URL-shaped values only.** `_looks_like_url_string`
rejects whitespace, which correctly stops free text in a JSON blob being
urljoin-ed but also discarded valid links. Encoding is applied only to values
already starting with a URL prefix, so the free-text guard is untouched.

### Alternatives rejected

- Loosening the global allowlist, or auto-trusting hosts linked from issuer
  pages — both make discovery an SSRF surface.
- Probing every link's content type — unbounded network cost; the curated-host
  pre-filter keeps probing proportionate.
- A flipbook scraper for the third-party viewer Pandora also links: the direct
  CDN artifact is the official downloadable document, so no third-party
  integration is warranted.

### Consequences

- Pandora's annual report is now discovered end-to-end (verified live pre-merge
  against the real page and registry entry).
- Issuers without `document_domains` — including Richemont — are byte-identical.
- No migration; the registry is code-defined.

### Phase F follow-up: verified source registry liveness audit

The Pandora entry carried a **404** URL while asserting it had been verified.
Curated entries decay silently. A future periodic job should HEAD/GET each
registry landing URL, record status and redirects, and **flag** stale entries
for human review — never rewrite a registry URL automatically, since the
correct replacement is a curation judgement.
