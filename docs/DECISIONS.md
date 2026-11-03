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

---

## ADR-025: One Final Reconciled Research State (Phase 32D2)

**Status:** Accepted — 2026-08-24
**Context:** live manual QA of the Pandora final report (staging report
`2ea1abcd-8f63-4984-9399-31bec6e95388`, staging SHA `f5e4058`).

### Problem

Pandora had, for the first time, real T1 issuer-primary evidence: an official
169-page annual report, ingested, with two validated high-confidence facts
(fiscal year 2025, revenue DKK 32.5bn). The LLM council read and cited them
correctly. The SAME rendered report simultaneously asserted:

| Correct, current | Stale, contradictory |
|---|---|
| `fundamentals_available: true` | `financials.revenue` in `missing_fields` |
| `fundamentals_source: issuer_primary_document` | "All 18 core financial fundamental categories are missing (revenue, EBITDA, …)" |
| `fundamentals_source_tier: T1_primary_filing` | "Financial fundamentals … none sourced at this phase" |
| Financial Evidence Quality: `strong` | Bull case: "fundamentals (not yet sourced)" |
| revenue DKK 32.5bn, page 8, source URL | Valuation: `financials.revenue` in `missing_inputs` |
| 3 issuer documents extracted | "Source T1 primary filings (annual report / 10-K) for revenue, EBITDA, FCF" |
| — | "All current data from SourceTier.T6_model_estimate only" |
| — | Evidence channel "Regulator structured financial facts (SEC XBRL)" holding the issuer's PDF facts, for an issuer with no SEC registration |

### Root causes (five, independent)

1. **No owner for post-ingestion state.** Phase-8/9 agents run at workflow time,
   before ingestion. Their output was rendered verbatim. Each previous
   corrective (Problem D, Phase C2, the CFR/MC availability fix) repaired ONE
   consumer and left the next one stale, because "what evidence do we have" had
   no single answer.
2. **One tier for two questions.** `provider_metadata.source_tier` describes the
   IDENTITY/PRICE provider. It was used to decide "is a primary source behind
   the financials?", so a T6 identity fallback overrode a T1 filing fact.
3. **The rebuild was silently skipped.** `_rebuild_deterministic_sections`
   refused to rebuild any summary whose safety scan hit — and it scanned WITHOUT
   `_EXEMPT_FIELD_NAMES`. `valuation_guard_summary.disallowed_outputs` exists to
   enumerate forbidden phrases ("Fair value estimate", "Price target"), so the
   valuation section NEVER qualified for rebuild and nothing reported it.
4. **`is_regulator_structured` included T1.** Issuer-published facts were routed
   into the SEC-XBRL evidence channel.
5. **`isinstance(tier, str)` on a `str`-mixin Enum is always True**, so
   `tier if isinstance(tier, str) else tier.value` kept the ENUM. Equality and
   membership still worked (no test failed); every f-string rendered
   `SourceTier.T6_model_estimate` into prose a human reads.

### Decision

Introduce `app/services/final_research_state.py`. It is built ONCE, immediately
after the council returns, and is the ONLY input the deterministic rebuilds
accept. It carries:

- `FundamentalsEvidence` resolved WITH the council's primary facts;
- `FinancialEvidenceState` — per-CATEGORY resolution with each category's own
  source, tier, period and URL, plus `open_statement_categories` (a filing can
  close these) vs `open_market_categories` (it cannot);
- a RECONCILED `financial_data_summary` (validated categories move missing →
  available; stale count/"missing primary filing sources" warnings are
  REWRITTEN, not deleted);
- a RECONCILED `research_completeness_summary` (only gaps a validated fact truly
  satisfies are closed; the "read the annual report" task is REPLACED by a
  precise extraction-completeness task).

Every deterministic surface — availability, missing information, research
completeness, source quality, bull/bear/risk, valuation readiness, committee
chair, executive summary, evidence channels, research memo — is rebuilt from it.

The Phase-8/9 agents gain an optional `financial_evidence` keyword. Omitting it
reproduces pre-32D2 behaviour exactly, which is what the workflow-time
invocation (genuinely pre-ingestion) does.

### Invariants

- **Nothing is upgraded by inference.** A category resolves only from a
  high-confidence, group-scoped fact carrying its own source URL. Medium
  confidence and segment scope still resolve nothing.
- **Absence survives.** The control test runs the identical pipeline with an
  empty council: every "missing" claim must still be present.
- **Idempotent.** Reconciling an already-reconciled summary is a no-op, so
  regeneration does not accumulate notes.
- **Two tiers are named separately**, never merged into one "source tier".
- **One internal-status label per report.** The Phase-9 chair's vocabulary
  (`research_incomplete`, `watchlist_candidate_for_review`) is now MAPPED to the
  report's, and the chair's prose is restated to match; the agent's own label is
  retained in `agent_internal_status` for audit. Previously the unmapped
  fallback rewrote the structured field to `not_enough_data` while the prose
  beside it kept saying `research_incomplete`.

### Alternatives rejected

- **Patch each surface again.** Four phases have now done this; each fixed the
  surface under test and left the next one. The defect is architectural.
- **Mutate `company_snapshot.provider_metadata.source_tier` to T1.** That would
  make the identity and price data claim a provenance they do not have.
- **Suppress the stale warnings.** Deleting a warning loses the real remaining
  gap. They are rewritten to state what is genuinely still open.

### Consequences

- No migration; no schema change. The reconciled state is recorded in
  `source_summary_json.final_research_state` (bounded counts/labels only) so a
  reviewer can audit why a section says what it says.
- `evidence_channels` grows from five to seven channels. Readers keying on
  `regulator_structured_facts` now get a T2-only answer, which is the correct
  one.
- A company with nothing ingested renders exactly as before.

---

## ADR-026: One Issuer Registry, and a Tri-State for Discovery Source Metadata (Phase 32D2b)

**Status:** Accepted — 2026-08-24

### Problem

Two issuer registries existed and neither layer knew about the other's:

- `app/services/sources/verified_issuer_sources.py` — code-defined,
  safety-validated (HTTPS, host allowlist, no credentials), covering the
  European issuers this product researches. Read only by the connector /
  document-ingestion layer.
- `app/integrations/exchange_source_registry.KNOWN_ISSUER_SOURCES` — seven US
  mega-caps. The only registry company-source / catalyst / news discovery
  consulted.

Live consequence on the Pandora report: it cited the issuer's own annual report,
reached through the verified registry's IR page, while News & Catalyst Discovery
rendered `has_verified_company_source: false` and warned "no company-owned
website / IR / newsroom source could be confidently discovered for this issuer".

Separately, the discovery evidence pack had no way to say "we know where this
issuer's IR and annual reports are, we just did not fetch them in a
metadata-only pass". A live European run's council therefore concluded "No
primary company news sources or IR websites confidently identified for any
candidate" for eight issuers whose IR and annual-report URLs are on record — one
of which had already been read end-to-end by full analysis.

### Decision

1. `discover_company_sources` consults `get_verified_issuer_source(ticker,
   exchange)` FIRST, promoting the issuer's own homepage / IR / annual-reports /
   announcements URLs as T1 verified sources with a new
   `VerificationMethod.verified_issuer_registry`. `annual_reports_url` is added
   to `CompanySourceDiscoveryResult`.
2. The discovery evidence pack carries a per-candidate TRI-state —
   `unknown` / `known_not_fetched` / `fetched` — plus run-level known-gap lines
   that count the two populations separately, and a `do_not_infer` rule
   forbidding the council from restating `known_not_fetched` as absent.

### Invariants

- **`document_domains` is never promoted.** It is a narrow retrieval permission
  for artifacts linked from the issuer's verified pages (ADR-024), not a
  publication venue. A test asserts every registry CDN host stays out of the
  discovered source set.
- **Fail-closed is preserved.** An unknown ticker still reports
  `has_verified_company_source: false`, confidence `0.0`, and the honest
  warning.
- **Registry-driven tests.** The bridge is asserted for EVERY registry entry, so
  adding an entry without updating this path fails CI instead of producing a
  live report that under-reports its own sources.
- **Exchange still discriminates.** `get_verified_issuer_source(ticker,
  wrong_exchange)` returns None — the ticker/venue collision guard is untouched.

### Consequences

- Issuers in the verified registry now get their own press/announcement pages as
  press-release feed candidates. Those probes hit the issuer's OWN allowlisted
  domain only, which is the same trust boundary the connector layer already
  uses.
- The stale Pandora registry caveat ("neither the domain allowlist nor
  extension-based document discovery reaches them yet") is restated as a
  standing caveat: ADR-024 closed that gap, and the warning is rendered on the
  report's verified-source rows.
- No migration; both registries stay code-defined.

---

## ADR-027: Admin Surfaces Render the Canonical Shape (Phase 32D2c)

**Status:** Accepted — 2026-08-24

### Problem

Two admin-UI defects, both of the same kind: the backend already produced the
right answer and the UI rendered something else, while the test suite agreed
with the UI.

1. **Raw warnings on the candidate queue.** `DiscoveryRunRead` has derived
   `warning_groups` from the raw list on every read since Phase C (canonical,
   deduplicated, severity-classified, bounded to 8 groups, raw instances
   retained). A live European run returned 200 raw strings and 8 groups; the
   admin page rendered the 200. Root cause: the TypeScript `DiscoveryRun`
   interface never declared `warning_groups`, so nothing referenced it and
   nothing failed.
2. **Agent summaries appeared blank.** The discovery-council and field-review
   agent summaries were rendered inside a COLLAPSED `<details>`, while every
   sibling section (evidence gaps, next source tasks, council notes) renders
   inline. The payload was never at fault — a live run returned eight agents
   each with a non-empty summary — and the e2e assertion
   (`expect(panel).toContainText("run_red_team")`) reads collapsed DOM text, so
   it passed throughout.

### Decision

- Declare `DiscoveryWarningGroup`, `warning_groups` and `warning_raw_count` in
  `types/api.ts`, and render grouped warnings on the candidate-queue surface:
  severity pill, canonical code, message, collapsed-instance count and affected
  subjects. Nothing is dropped — per-group original wording and the complete raw
  list stay one click away, and a run whose payload predates grouping still
  renders its raw list.
- Render agent summaries inline (`AgentSummaries`), for both the discovery
  council and the deep field review.
- **e2e assertions move from `toContainText` to `toBeVisible`.** A collapsed
  disclosure cannot satisfy a visibility assertion, so this specific regression
  cannot recur silently.

### Consequences

- The pre-existing test "21. Failed run shows failed status and warnings"
  asserted the literal string "warning(s)"; it now asserts the grouped surface,
  because the surface intentionally changed.
- No backend change. No API change. Presentation only.

---

## ADR-028: Regenerating From a Final Report Must Not Silently Lose Its State (Phase 32D2d)

**Status:** Accepted — 2026-08-24

### Problem

`generate_from_report` recovers workflow state by re-parsing the source report's
markdown JSON blocks. For a Phase-9 analysis-council draft those blocks ARE the
state envelope (`company_snapshot` / `financial_data_summary` /
`bull_case_summary` / …). For an ALREADY-FINAL report they are the RENDERED
SECTIONS (`financial_snapshot` / `bull_case` / `company_identity` / …) — different
key names — so the parse recovers nothing and every state key comes back `None`.

A final report is exactly what an admin is looking at when they press "Generate
Final Report" on the report detail page. Live staging report
`835cc67b-4889-4de5-8c2d-7d8ac80c5fc4` is the result:

```
"Bull case summary not available. Run company analysis workflow."
"Company snapshot not available. Run company analysis workflow first."
"Valuation guard summary not available."
"Financial data summary not available from analysis workflow."
available_count: 0
```

rendered directly beside a company-identity section carrying a validated T1
fiscal year, a financial snapshot carrying a validated T1 revenue figure, and a
data-availability summary correctly reporting
`fundamentals_source: issuer_primary_document / T1_primary_filing`.

"Run company analysis workflow" is, in that state, a **false instruction**: the
workflow had run. Its draft was simply unreachable from the report being
regenerated.

### Decision

1. When the source report is a final report (`final_report_version` is set) AND
   the parse recovered no `company_snapshot`, load the ORIGINATING Phase-9
   deterministic draft of the same lineage —
   `created_by_agent_run_id == source.created_by_agent_run_id` AND
   `final_report_version IS NULL` — and parse its envelope instead.
2. When recovery is impossible, degrade **loudly**: an additive
   `regeneration_notice` section states that the sections below are reporting an
   UNREACHABLE source, not a workflow that never ran, and tells the reader not
   to act on the generic instruction.

### Invariants

- **Explicit lineage only.** A final report with no `created_by_agent_run_id`
  never triggers the lookup — no ticker match, no name match, no "latest by
  company" (which CLAUDE.md forbids and which would cross a company boundary).
- **Additive.** `regeneration_notice` is not a required section, so schema
  validity is unaffected, and it is added before validation so the safety gate
  scans it.
- **The ordinary path is untouched.** Regenerating from a Phase-9 draft adds no
  notice and behaves exactly as before.

### Consequences

- No migration. The lineage column (`created_by_agent_run_id`) already exists on
  final reports from Phase 32A Slice 3.
- A lineage that ran with citation persistence OFF has no stored agent run, so
  recovery is unavailable — and now says so, which is the point.

---

## ADR-029: `None` Must Never Reach a Human-Facing String (Phase 32D2e)

**Status:** Accepted — 2026-08-24
**Context:** found during Phase 32D2 live acceptance on staging.

### Problem

`dict.get(key, default)` returns the default only when the key is ABSENT. A key
PRESENT with the value `None` returns `None`, which an f-string renders as the
four characters `None`.

The company snapshot deliberately stores honest absences as explicit `None`
values (Phase 32A Slice 6B stopped fabricating placeholders), so every
`profile.get("sector", "unknown sector")` in the deterministic agents was a
latent leak. Live acceptance found three, all in the report body:

```
"Currency risk: reporting currency is 'None'."
"Risk assessment for PNDORA (PNDORA), None, Denmark."
"Latest close 783.0 None on 2026-08-21 from eodhd_price_only"
```

The third is the most serious: it is a persisted CITATION quote, and the price
currency is not actually unknown — the price summary's own field says `DKK` and
`price_quote_currency_for_exchange` resolves it. The quote interpolated the RAW
provider value, which is honestly `None`, so a SOURCED currency was rendered as
absent inside the evidence of record.

### Decision

- Replace every `get(key, literal_default)` on a snapshot identity/profile field
  with `get(key) or literal_default` in the deterministic agents.
- Resolve the price citation's currency the same way the rest of the pipeline
  does (explicit provider currency ⇒ exchange-derived quote currency), and
  render `"currency not sourced"` when it genuinely is not known.
- A regression test asserts that NO string field of any deterministic agent's
  output contains the bare word `None`, using a snapshot whose identity and
  profile keys are all present-with-`None` — the exact failure shape.

### Consequences

- JSON `null` is untouched: structured absence is correct and stays. Only the
  rendered WORD inside a string is the defect.
- No behaviour change where a value is present.

---

## ADR-030: Rebuild Borderless Multi-Year Financial Tables From Page Geometry (Phase 32D)

**Status:** Accepted — merged (PR #144). No migration (Alembic head stays `017`), no schema change, no new flag. Extraction `pipeline_version` `9 → 10`, `EXTRACTION_TEXT_LAYER_MIN_VERSION` `9 → 10`.

### Context

An issuer's densest page of reported financials is its five-year summary or its primary statements: one metric per row, one reporting period per column. Those tables are almost always **borderless** — nothing but whitespace alignment holds them together.

`pdfplumber.Page.extract_tables()` finds tables from ruling lines. Measured against the real 169-page Pandora Annual Report 2025, page 14 ("FIVE-YEAR SUMMARY"), it returned `[['2025'], ['32,549'], ['6%'], …]`: a one-column artifact whose only column is the row-header column `extracted_fact_validator._numeric_cells` deliberately skips. The table path produced **zero** candidates.

The same page's content still reached the prose path, but flattened — one metric label followed by five side-by-side values, the column→year mapping already destroyed. The validator refused to promote it, exactly as designed. The live report therefore said revenue was sourced and EBIT, EBITDA, net income, cash flow, debt, cash, assets and EPS were all missing, while every one of those numbers was printed on page 14.

The loss was **not** in validation, and not in fact interpretation. It was that nothing ever rebuilt the grid.

### Decision

Add a SECOND, geometry-driven table pass that runs alongside — never instead of — the ruled one, working from `page.extract_words()`: cluster rows by `top`; find header rows carrying ≥ 2 period tokens; split those tokens into one column group per physical table; qualify a group only on uniform column pitch, distinct strictly-monotonic periods, homogeneous period types and a clean header band; build x-bands midway between header centres; assign each numeric word to the band containing its own centre, clear of both edges; end the region where prose intrudes.

Hand the result back as a plain header-first grid so the **existing** validator consumes it unchanged. The new module decides layout only.

### Alternatives considered

* **Loosen the validator's ambiguity refusal** so a multi-value row promotes something. Rejected outright: it would assign years by position or magnitude. A false period assignment is the one failure this pipeline may never make.
* **A general table-extraction engine / an external library.** Rejected as far more surface than the problem needs; financial statement and summary tables are a narrow, well-behaved shape.
* **A separate parser for multi-year tables, with its own fact path.** Rejected — it would be a second source of truth. The new pass emits the same `ExtractedTable` shape the validator already trusts.

### Consequences

* Pandora page 14 now yields **52 validated, period-scoped facts** (was 2 usable): revenue, EBIT, EBIT margin, net income, total assets, equity, net interest-bearing debt, operating cash flow, free cash flow and headcount, each for FY2021–FY2025, in `DKK million`, with page and column provenance.
* Richemont **regressed nothing** and gained exactness: the Group figures that were previously only available rounded from prose (`€22.4 billion`, `€4.5 billion`, `€5 billion`) now resolve to the statements' own `22 420`, `4 492` and `5 037`, and the consolidated balance sheet contributes total assets, liabilities and equity.
* Historical years are ordinary facts distinguished by `period`; the existing `primary_fact_period_rank` pre-sort already makes the most recent one win a capped evidence slot, so a FY2021 column can never displace FY2025.
* `pipeline_version` must advance, and `EXTRACTION_TEXT_LAYER_MIN_VERSION` with it: `excerpts_json` never persists a raw table grid, so an excerpts-only replay can only ever reproduce the flattened prose reading. Recovering the new column-anchored facts needs the original words and their geometry, which only a full re-extraction supplies.

### Fail-closed rules this decision commits to

A cell becomes a fact only when its metric, value, table region, period column, column association, unit and scope are all determined. Anything ambiguous is dropped and recorded with a machine-readable reason. Specifically:

* A value equidistant between two columns is refused.
* Two values landing in one column discard the row.
* An irregularly-pitched "header" is not a header — this is what rejects a footnote that merely names several years (the real Pandora page-14 footnote has gaps `57.9 / 58.9 / 73.1`).
* Periods must be distinct, strictly monotonic and of one type; the leftmost column is never assumed to be the newest.
* A period form the `ExtractedFact.period` contract cannot represent losslessly — interim `H1 2026`, split-year `2025/26` — is detected, surfaced as a source gap, and **not** promoted. Calling an interim column a fiscal year is the exact error this ADR exists to prevent.

### Related validator changes (same bug class)

* **Prose supersession.** A prose candidate that is a degraded read of a page whose table was reconstructed (same label, page AND scope) is superseded by it. They are one printed table read twice, not independent corroboration. Keyed on scope as well as page, because a sentence often carries entity/segment scope a bare grid cannot — dropping Richemont page 9's "Specialist Watchmakers … € 107 million" would have lost a real segment fact.
* **Conflict eligibility.** Two candidates may only CONTRADICT each other when BOTH are fully qualified. A candidate whose currency/scale/period could not be established has unknown units, and comparing its bare digits against a fully specified figure is a category error. It had let a stray "Approx. -600" on Pandora's guidance page demote the correct FY2025 revenue, and an unlabelled "42" in a Richemont note table displace the Group's `€ 3 484 million` profit for the year.
* **Vocabulary.** "EBIT margin" maps to the percent operating-margin label rather than being swallowed by the `ebit` money pattern; "cash flows from operating activities" and "net interest-bearing debt" joined the metric patterns; "profit for the year FROM continuing/discontinued operations" no longer matches plain net income, because a real income statement prints both lines and one table was contradicting itself.
* **Scope.** IFRS 5 "discontinued operations" / "disposal group" / "held for sale" are a NON-Group scope. The standard's own phrase "disposal GROUP" previously read as an issuer Group claim — the same false-positive class as the existing "peer group" guard — and let Richemont's YNAP disposal note contradict the Group's revenue.

### Bounds

Reconstruction is skipped for a page whose ruled pass already recovered a real grid (≥ 2 rows AND ≥ 2 columns), and for a page above the word cap. Regions per page, rows per region, header rows per page and column count are all bounded. Measured cost on the real documents: **~0.1 s per 40-page document**, against the ~8 s `extract_words()` the extractor was already spending for its existing column and heading passes.

---

## ADR-031: Fact Scope Is a Persisted, Typed Column — Not an In-Memory String

**Date:** 2026-08-25
**Status:** Accepted

### Context

A financial fact's *scope* — is this the consolidated Group figure, or one
business area's? — has been computed since the Phase 32A corrective slice. It
kept a Specialist Watchmakers operating profit of €107m from being presented as
the Group's €4,492m, a real live-observed regression.

But scope was a free-text `str | None` that lived only in memory, and three
layers each carried their own interpretation of it: the heading inferencer, the
prose inferencer, and the report layer's local `_GROUP_SCOPE_LABELS` set.
Nothing persisted it. `_persist_validated_facts` wrote every other field of a
`ValidatedFact` and dropped `scope`; `_rebuild_artifact` — the cache-reuse and
revalidation fast path — rebuilt facts with `scope=None`.

The consequence was worse than "we lose a nice-to-have label". An **absent**
scope is the pipeline's long-standing implicit "this is the Group figure"
convention. So the fresh path was correct and the *cached* path silently
converted every segment fact into a Group-eligible one. Any report generated
from a reused document could promote a segment figure into a canonical Group
slot — the exact contradiction class (`SCOPE_CONTRADICTION`) that the
private-use readiness campaign exists to make unrepresentable.

Fact identity made it worse: the dedupe key was `(label, period, value)`. A
Group and a segment figure that happened to share a value collapsed into one
row, and the survivor was stored unscoped.

### Decision

Scope becomes a **persisted, typed, decidable** value.

1. `app/services/sources/fact_scope.py` is the single source of truth for scope
   semantics: `FactScope(scope_type, scope_name)` with a derived `scope_key`,
   one `GROUP_SCOPE_LABELS` vocabulary, and one `parse_scope()` that every
   producer and consumer routes through. `final_report_generator` re-exports the
   vocabulary under its historical name rather than keeping a second copy.
2. Migration `018` adds `scope_type` / `scope_name` / `scope_key` to
   `extracted_facts` — additive, nullable, indexed on
   `(extracted_document_id, scope_key)`.
3. `scope_key` joins `(label, period, value)` in the fact **identity** used by
   dedupe and supersession, in both persistence paths.
4. `UNKNOWN` (`scope_type IS NULL`) is a first-class third state, distinct from
   `group`. It is never coerced to `group` at write time, a `segment` row that
   lost its name degrades to UNKNOWN rather than to `group`, and two UNKNOWN
   scopes are **not** declared the same series (`same_scope` is fail-closed).
5. **No backfill.** No pre-018 row carries a recoverable scope signal, so every
   one stays NULL.
6. `CURRENT_EXTRACTION_PIPELINE_VERSION` advances `11 → 12`, so pre-018 rows are
   revalidated under current semantics instead of replayed unchanged.

### Consequences

**Positive.** The `SCOPE_CONTRADICTION` class is closed on the persisted path,
not just the fresh one. "Is this the Group figure?" is a column lookup, not a
string match, so two modules cannot drift. A Group and a segment figure for the
same metric, period and value can both exist. The admin primary-documents
surface can now show a human which scope a fact actually carried, which is what
makes the guarantee verifiable rather than merely asserted.

**Negative / accepted.** Advancing the pipeline version invalidates every cached
document once, so the next run per issuer re-extracts (a one-off cost this
campaign pays deliberately, and then proves cache reuse against). Legacy rows
stay UNKNOWN forever, which is honest but means their Group-vs-segment
attribution is only recovered when the document is next re-extracted. The
implicit "unscoped means Group" convention is *retained* on the fresh path — a
deliberate, now-explicitly-tested decision rather than an accident, because
changing it would silently drop every legitimately unscoped Group figure that
issuers publish without a scope word.

---

## ADR-032: Historical Series Are a Bounded, Fail-Closed Contract — Not Raw Facts in a Prompt

**Date:** 2026-08-25
**Status:** Accepted

### Context

Phase 32D gave the extractor the ability to rebuild borderless multi-year
tables. On a real Pandora annual report that produced ~52 period-scoped facts
covering FY2021–FY2025. Almost none of it reached a human: every downstream
consumer — `_high_confidence_facts_for`, the canonical snapshot, the council
evidence pack — takes ONE representative value per field and drops the rest. A
council could be handed a complete five-year revenue series and still report
"no historical revenue trend information".

The naive fix — push all 52 facts into the council prompt — fails for two
independent reasons. It blows a TPM-paced token budget and crowds out every
other kind of evidence; and it hands a model the raw material to compare things
that must never be compared (Group against segment, FY against H1, DKK against
EUR).

### Decision

A typed, bounded series contract sits between extraction and every consumer.

1. **`financial_period.py`** — a `ReportingPeriod` value model that can hold
   `FY2025` and `H1 2026` at the same time, orders them, and *refuses* to
   compare across period types. `is_more_recent` returns `False` for an interim
   vs an annual period on purpose: an interim result sits beside an annual one,
   it does not supersede it.
2. **`financial_history.py`** — series are keyed by the FULL identity
   `(metric, scope, period type, currency, unit, scale)`. A currency mismatch
   therefore produces *two series*, not one bad trend. Comparability is
   fail-closed and its reasons are explicit; an unscoped fact never enters a
   series at all, because it might be the Group's or a segment's.
3. **Bounded by design** — at most 5 periods per series and at most 8 series
   lines in the council pack, each a single dense line stating its own scope and
   unit. That is a token bound, not a research bound: the full series still
   reach the deterministic report surfaces.
4. **Derived arithmetic only** — absolute change, percentage change, and
   percentage-POINT change for metrics that are already percentages. Each
   carries its own inputs and formula. A zero base emits no percentage change,
   because an undefined ratio is not infinite growth.
5. **Two fact widths.** `CouncilResult.primary_facts` (high confidence only)
   feeds canonical single-value slots; `historical_facts` (high + medium) feeds
   series. A medium-confidence figure must not be presented as *the* number, and
   dropping the medium-confidence middle years of a five-year table would leave
   the report asserting "no trend" beside a complete one.

### Consequences

**Positive.** A researcher and the council both see real direction over time,
with scope, period type, unit and page-level provenance on every observation. A
missing year is named rather than interpolated. A restatement is resolved
deterministically by source strength and the losing value is retained as a
superseded point, so the restatement stays auditable. "No comparable trend" is
now a statement the system can make *with a reason*, which is different from
silence.

**Negative / accepted.** Series are capped at five periods, so a longer table is
truncated to its newest periods — the older ones remain in the database and in
the primary-documents surface, just not in the trend. Interim series are built
only when explicitly requested, which means PR-D must wire the request rather
than get it for free. And a legitimately unscoped Group fact (issuers do publish
figures without a scope word) produces no series at all; that is the
deliberate fail-closed cost of never letting a segment trend masquerade as the
Group's.

---

## ADR-033: Field Vocabularies Are Derived From the Parser, and a Gap Is Per Company

**Date:** 2026-08-25
**Status:** Accepted

### Context

Three related defects, all of the same shape: a fact was known in one place and
described differently — or not at all — in another.

**The snapshot was narrower than the evidence.** Two hand-maintained field sets
existed: `final_report_generator._PRIMARY_FINANCIAL_FACT_FIELDS` (7 fields) and
`canonical_evidence.PRIMARY_FACT_FIELDS` (12). Neither matched the parser, which
routinely produces 15. Worse, they had drifted in *both* directions: the
canonical set listed `shareholders_equity` and `earnings_per_share`, which the
parser has never emitted, and omitted `total_equity` and `net_cash`, which it
emits routinely. A validated `total_equity` fact therefore counted as no
fundamental anywhere — extracted, validated, persisted, cited, and never shown.

**The copy was US-centric.** "Annual report / 10-K / 40-F" and "SEC statement
fundamentals" were emitted unconditionally, including for a Danish issuer whose
figures came from its own annual report and which has no SEC registration at
all. The same report elsewhere correctly labelled those facts issuer-primary, so
this was a live self-contradiction, not just awkward wording.

**A DFR gap was not per company.** The comparative pack carried no identity
completeness signal at all. Asked which identity fields were missing, the
council had only free-text gap prose and generalised one company's missing LEI
into "both companies are missing LEI" — while the other's report rendered a
sourced one.

### Decision

1. **The parser exports its own vocabulary.** `FINANCIAL_STATEMENT_FIELDS`,
   `IDENTITY_FIELDS` and `NON_INTERCHANGEABLE_FIELD_PAIRS` live next to the
   `FIELD_*` constants that define them. Both consumer sets are now derived from
   them, so the drift class is unrepresentable rather than merely fixed.
   `employees` is explicitly identity, not a fundamental.
2. **Jurisdiction resolves the filing vocabulary.** `annual_filing_name()` uses
   the *same* exchange/country signal the regulator line already used. An
   unresolved jurisdiction keeps the existing US wording — this never guesses.
   `_statement_source_label()` names the channel the statements actually came
   from instead of asserting SEC.
3. **Identity completeness is stated, not inferred.** Every DFR company summary
   carries `identity_fields_present` / `identity_fields_missing`, derived
   strictly from that company's own exact-linked report. A field the report never
   carried counts as missing — honest, and it lets the council tell "absent"
   from "not asked". The prompt is told explicitly that a missing field is per
   company.
4. **The discovery card is labelled, not recomputed.** Discovery scores stay
   immutable; once a full analysis exists the card says so and points at it.

### Consequences

**Positive.** A researcher sees every validated statement field the issuer
published, each with its own period, scale, currency, scope and source URL.
`net_debt` / `total_debt`, `net_cash` / `cash_and_equivalents` and
`operating_profit` / `recurring_operating_profit` are distinct slots that can
never alias. European issuers are described in their own filing vocabulary. A
DFR field gap is now a fact the pack states rather than something a model infers
across companies, which also makes it deterministically testable.

**Negative / accepted.** A richer snapshot is a larger section, so a reader has
more to scan — mitigated by the fact that unsourced fields are absent rather
than null-filled. Italy has no regulated-disclosure connector mapping yet, so an
Italian issuer still falls back to the generic filing wording; PR-E adds it with
the live venue. And the derived sets mean a future parser vocabulary addition
automatically widens the snapshot, which is the intent but does require the new
field to be genuinely canonical before it is exported.

---

## ADR-034: Current-Period Evidence Sits Beside the Annual, Never Instead of It

**Date:** 2026-08-25
**Status:** Accepted

### Context

A private investment research tool cannot rely only on annual data when newer
official reporting exists. As of 2026-08-25 every target issuer had published
something newer than its last annual report — Pandora's Q2 2026 interim report
(12 Aug 2026), Richemont's FY27 Q1 sales (15 Jul 2026), Hermès' H1 2026 results
(29 Jul 2026), Kering's H1 2026 (28 Jul 2026), Swatch's H1 2026, Moncler's H1
2026 (22 Jul 2026). None of it could reach a report.

Three independent blockers, each confirmed against the code:

1. **Interim documents could never be selected.** `rank_documents` orders
   strictly by kind (annual 0 < results 1 < interim 2) under a 3-document cap.
   Richemont's results page links roughly thirty annual reports back to 1993, so
   the cap was always exhausted before an interim was reached — and among the
   annuals there was no recency term at all, so DOM order decided which one was
   ingested.
2. **Interim figures were stamped as full years.** `_period_near` returned a
   bare year for every prose fact, so "revenue in the first half of 2026
   amounted to €8.2 billion" produced `period="2026"`. A table headed
   "First-half 2026" did the same. Two accepted test fixtures — both explicitly
   H1 releases — asserted exactly that wrong period, which is how it survived.
3. **An interim figure could occupy the annual slot.**
   `_high_confidence_facts_for` took the first high-confidence fact per field,
   safe only while every ingested document was an annual report.

### Decision

Interim evidence is ingested, labelled, and kept **beside** the annual figures.

1. **Period-aware, recency-aware selection.** A `document_recency_hint` parsed
   from title/URL joins the rank as its LAST term (so it only breaks ties the
   old key left to document order), and a reserve guarantees one current-period
   document survives the per-issuer cap — honoured only when such a document
   exists, so no slot is wasted. The reserve uses text-inferred classification;
   the RANK still uses only the discovery layer's own classification, so a
   landing page can never outrank a real PDF.
2. **Interim markers are read from the value's own local window** — the same
   discipline scope already uses. A marker in an unrelated sentence cannot
   reclassify a full-year figure, and a sentence with no marker keeps its bare
   year. This never invents an interim period.
3. **Two canonical selectors.** `_high_confidence_facts_for` returns the LATEST
   ANNUAL fact per field and refuses interim facts outright;
   `_current_period_facts_for` returns the latest interim fact into its own
   `<field>_current_period` slot, with a note stating explicitly that the two
   are not comparable and that nothing has been annualised.
4. **No annualisation.** Deliberately not implemented, and asserted against.
5. `CURRENT_EXTRACTION_PIPELINE_VERSION` advances `12 → 13`.

### Consequences

**Positive.** A researcher sees FY2025 revenue *and* H1 2026 revenue, each with
its own period, and cannot mistake one for the other. The newest annual report
is now chosen deliberately rather than by DOM accident. `INTERIM_AS_ANNUAL`
becomes structurally hard: an interim period is a distinct type that the
canonical annual selector rejects.

**Negative / accepted.** The recency hint accepts a two-digit fiscal year
(`fy26`) that `financial_period.parse_period` deliberately refuses — acceptable
because it is a *retrieval preference* whose worst case is a suboptimal document
choice, and never becomes a fact's period. Reserving a current-period slot costs
one of three per-issuer document slots on issuers that publish both. And a
multi-period interim table that mixes column types (Pandora's "Q2 2026 | Q2 2025
| H1 2026 | H1 2025 | FY 2025") is still refused by the table reconstructor's
monotonicity check — correctly fail-closed, and its figures still reach the
pipeline through prose; widening that check is deliberately out of scope here.

---

## ADR-035: Regulated Disclosures Normalise Into One Event Model, and Merge Without Losing Provenance

**Date:** 2026-08-25
**Status:** Accepted

### Context

Four venue connectors existed (`nordic_disclosures`, `six_swiss`,
`euronext_regulated_info`, `uk_fca_nsm`) and every one was reference-only by
design: it emitted a pointer to the issuer's regulated-disclosure venue plus an
honest gap saying the filing CONTENT is not fetched. For a private research
system that is half an answer — a researcher asking "what did this issuer just
announce?" got a link to a search page.

Italy was worse: no connector, and no `MI` / `Italy` entry in
`regulator_connector_for` at all, so an Italian issuer fell through to the
generic region scaffold and its report described its filings in US vocabulary.

Researching the venues on 2026-08-25 produced an uneven answer, which is the
interesting part. Two venues publish a legitimate, official, machine-readable
surface. Three do not, and one of those is actively defended against automation.

### Decision

**Upgrade the existing connectors in place; add exactly one.** No parallel
architecture. Live retrieval is gated behind `SOURCE_LIVE_DISCLOSURES_ENABLED`,
off by default, so enabling it is an operator decision rather than a deploy
side-effect, and with it off every connector is byte-for-byte unchanged.

**Live where a legitimate surface exists:**

| Venue | Surface | Status |
|---|---|---|
| Nasdaq Nordic (CO/ST/HE/OL) | the exchange's own company-news service — JSON, per issuer, with headline, venue category, official URL and typed attachments | **live** |
| eMarket Storage (Italy) | the CONSOB-authorised storage mechanism; per-issuer listing with a dated row and the official PDF | **live** (new connector) |
| SIX Swiss | no public per-issuer API found; issuers publish Art. 53 LR ad-hoc announcements on their own sites | reference; issuer-primary path covers it |
| Euronext Paris | company news is modal-loaded and paginated; the server-rendered page carries a handful of rows | reference |
| LSE / FCA NSM | NSM portal returns 403, its search API rejects every documented index, and the issuer's own site is behind a proof-of-work challenge | **reference — deliberately not bypassed** |

**One event model.** Every venue normalises into `DisclosureEvent`, so an
Italian and a Danish disclosure reach the council, the report and the DFR in the
same shape, and a venue that cannot be retrieved degrades without any consumer
noticing a different shape.

**Dedupe is semantic, and merging is additive.** The key is
`(issuer, publication DATE, normalized title)` — date-level because the issuer's
newsroom and the exchange stamp the same announcement minutes apart, and
title-normalized because the exchange prefixes its own announcement number.
Deliberately NOT URL-based: the two channels host it at different URLs, which is
the whole reason it appears twice. When two records merge, `provenances` keeps
BOTH channels and every optional field survives from whichever copy had it — an
announcement confirmed by two independent channels is better evidence than one,
and throwing away a channel would hide that.

**Category is descriptive, never a judgement.** The venue's own structured label
wins; a headline only refines a label that is regulatory rather than
content-bearing ("Inside information" says how a disclosure is *regulated*, not
what it is *about*). Nothing here says an event is material, bullish, or a
reason to trade.

### Consequences

**Positive.** Denmark and Italy now yield real, dated, categorised, citeable
announcements with official URLs and attachments — including, for Italy, an
issuer whose own website was serving a maintenance page. The issuer-vs-exchange
duplicate appears once, with both provenances. Italy finally has a regulated
-disclosure identity, which also fixes the US filing vocabulary PR-C had to
leave in place for Italian issuers.

**Negative / accepted.** Three of five venues remain reference-only, and one of
them (LSE/FCA NSM) is genuinely inaccessible without bypassing an anti-bot
mechanism, which this campaign will not do — recorded as a limitation rather
than worked around. The Italian venue needs a curated issuer id per company,
because it exposes its filter as an opaque numeric id with no derivable relation
to the ticker; the curation is a trust relationship exactly like the verified
-issuer registry, and every line of parsing around it stays generic. And the
Italian venue publishes each announcement twice, once per language: both are
kept, English ordered first, because dropping the local-language twin would
require asserting that two differently-worded headlines mean the same thing.

---

## ADR-036: Contradiction Classes Become Assertions; an Abandoned Job Says So

**Date:** 2026-08-25
**Status:** Accepted

### Context

**Consistency.** Every corrective slice in this codebase's history has been the
same story: a report said two incompatible things at once, a human noticed, and
a targeted fix followed. A Specialist Watchmakers figure in a Group slot.
"Source the annual report" beside a T1 revenue figure extracted from that very
report. "All current data is T6" next to a validated T1 fact. A Python `None`
rendered into a sentence. "SEC XBRL" over a Danish issuer's own PDF.

Every one of those was found by *reading*. That does not scale, and it is not a
readiness bar. Worse, each fix was verified against the specific report that
exposed it, so nothing prevented the same CLASS from reappearing elsewhere.

**Durability.** A full analysis runs in a process-local `BackgroundTasks`, so an
app restart mid-run leaves the stored envelope on `running` forever. The state
was always *recoverable* — a DB-backed envelope plus a derived stale threshold,
and a fresh POST past that threshold restarts it — but nothing SAID so. The
status endpoint reported `running` indefinitely, and a researcher watching it
could not tell a job that is working from one that died an hour ago.

### Decision

**One audit module, semantic first.** `report_consistency.py` turns the thirteen
named contradiction classes into checks that run over an assembled report. They
are SEMANTIC assertions against typed sections; text scanning is used only for
the two classes that genuinely *are* about rendered text (`None` and enum-repr
leakage), because a brittle string scan fails on wording changes and passes on
real contradictions — the worst of both. The audit is read-only and never
raises: an audit that crashes on a malformed report tells a reader nothing.

**Every invariant is tested from both sides.** A violating report must be
caught AND a correct report must not be flagged. A checker that only ever fires
is as useless as one that never does — so the negative cases are explicit:
"none of the above" is not a `None` leak, "section 4.2" is not an enum repr, a
URL containing `None` is not a rendering defect, `10-K` is correct for a US
issuer, and a genuinely missing field stays listed as missing.

**An abandoned job is reported as abandoned.** `interrupted` is DERIVED at read
time from the same `started_at` and threshold the restart decision already uses,
so the two can never disagree — it is deliberately not a stored status, which
would be a second source of truth about the same job. It carries
`recoverable=true`, because the useful thing to tell a researcher is not "this
failed" but "nothing was lost; re-running is safe".

**The startup sweep is read-only and does not re-enqueue.** The first process to
notice orphaned jobs is the one starting up, precisely because the process that
owned them is gone. It logs what was lost and stops there: it does not rewrite
the envelope (the dead worker's audit trail survives, and a job still running
under another live process is never stolen from it), and it does not silently
restart an expensive council run on every deploy — that is a surprise, not
recovery. A human decides.

### Consequences

**Positive.** The acceptance question "does this report contradict itself?"
becomes a command rather than a reading exercise, and it generalises to reports
this campaign never saw. `is_clean` is a usable gate. A researcher watching a
long analysis learns within the stale window whether it is alive.

**Negative / accepted.** The audit runs over an assembled report, so it catches
a contradiction after assembly rather than preventing it at the source — it is a
safety net, not a type system. Its US-vocabulary check needs the issuer's
country supplied by the caller, and is silent when that is unknown (correct: it
must not guess a jurisdiction). And `interrupted` only appears once the derived
threshold has elapsed, which for a long council run is deliberately generous —
reporting a slow job as dead would be a worse failure than reporting a dead one
as slow.

---

## ADR-037: The Product Layer Presents the Research State — It Never Reconciles It

**Date:** 2026-08-29
**Status:** Accepted

### Context

Everything the platform had built up to this point — discovery, primary-document
ingestion, period- and scope-aware extraction, the reconciled research state,
the council — was reachable only through an operator's console. `/admin` opens
with a red compliance strip, then a red disclaimer card, then a thirty-row
metadata table, and the actual research begins somewhere below the fold. That is
the right surface for someone diagnosing the pipeline. It is the wrong surface
for someone deciding whether to spend an afternoon on a company, and it made a
serious research tool read as an internal engineering dashboard.

The obvious way to fix that is also the dangerous one. A "cleaner" report view
is, mechanically, a view that shows fewer things — and the things easiest to
drop are exactly the ones this codebase spent six campaigns learning to keep: the
annual/interim distinction, the Group/segment scope, the missing-evidence
inventory, the fact that a committee label can be a failure default rather than a
judgement. Simplifying the presentation and simplifying the truth look identical
in a design review and are opposites in a research product.

### Decision

**A new route family, not a rewrite.** `/research/*` is added alongside `/admin/*`,
which is untouched. Both report views render the SAME report and link to each
other. No existing URL changed, so no bookmark broke, and the diagnostic record
remains the diagnostic record.

**The product layer derives nothing.** `components/research/reportView.ts` reads
the same structured `report_content` the admin renderer reads and reshapes it for
a reader. It is explicitly forbidden from doing the four things that would make
it a second, competing source of truth: it does not reconcile figures the backend
reported as conflicting, does not fill a missing slot from a neighbouring one,
does not average the evidence-quality dimensions (the backend already reports the
weakest, on purpose), and does not merge annual with interim reporting. Where the
backend says "unknown", the view says "not reported" — never a plausible default.

**Simplify repetition, not meaning.** The admin report states "not investment
advice / human review required / not publication-ready" in six places. Repeating
that after every section of a reader-facing report does not make it more true; it
makes it invisible. So it is stated once, in a fixed compact status strip, with
the full wording one disclosure away — and the underlying safety metadata is
completely unchanged. What is NOT reduced: missing evidence, source weakness,
conflicts, incomplete research state, and the annual-versus-current distinction
all stay on the page.

**The dangerous flattenings are structural, not editorial.** `<field>_primary_filing`
and `<field>_current_period` render in two separate columns that never share a
row, and the part-year column carries a standing "not annualised" line rather
than a footnote. A trend series the backend marked `not_comparable` is listed
with its reason and deliberately *not* drawn, because a line between two
non-comparable figures is a false statement in visual form; a single-period
series is not drawn either, because a flat line reads as stability. A committee
label produced by `deterministic_fallback` says in words that the chair never
completed and that the label is not a judgement about the evidence.

**Auth is extended, not weakened.** `/research/:path*` joins the existing proxy
matcher and reuses the same GitHub OAuth, session cookie and allowlist. These are
Server Components that fetch the backend directly with a server-side credential,
so the matcher entry is load-bearing: without it they would render private
research to anyone. Only `/`, which renders no research and reads no report,
stays public.

### Consequences

**Positive.** A researcher reaches "analyze a company", "discover", or an existing
report in one or two clicks, and reads a report that opens with the company and
its reporting state rather than with build metadata. The operator keeps every
diagnostic they had. The two views cannot drift apart on the facts, because only
one of them derives anything.

**Negative / accepted.** There are now two renderers over one payload, so a new
`report_content` section appears in the admin view (which walks the section list
generically) before it appears in the research view (which curates). That is the
deliberate trade: the research view is a curation, and a curation has to be
updated on purpose. A section that only the admin view knows about is a missing
feature; a section the research view *invents* would be a defect, and only the
second is prevented by construction.

Company research also still runs as one long synchronous request. The product
surface makes that honest — an elapsed timer, the stage list, and a statement
that the run continues server-side — but it does not fix it. Moving the
single-company workflow onto the async job envelope the discovery path already
uses is a backend change and was deliberately left out of a presentation slice.

---

## ADR-038: The Product Surface Asks the Same Question the Console Asks

**Date:** 2026-08-29
**Status:** Accepted

### Context

ADR-037 added `/research/*` as a presentation layer over the existing engine.
A functional review of it was run against the offline mock backend, and two
things appeared to be badly wrong: selecting **Pandora (PNDORA · CO)** produced
a report for *InvestingBuddy Test Company (IBTEST)* with `provider = mock`, and
a luxury-goods discovery request came back as *"European defense suppliers
benefiting from NATO spending"*.

Both were the fixture answering. The mock's workflow route returned a fixed
company whatever it was asked, and its thesis route returned a fixed defense run
whatever it was asked. The requests themselves were correct.

That is the interesting part. A mock that is unfaithful about what it was asked
makes a real integration bug and a fixture artifact **indistinguishable on
screen** — and the only reason we could tell them apart was by reading the
source. Meanwhile the review DID surface three real divergences that no rendered
result would have revealed either.

### Decision

**Request construction is shared, not duplicated.** `apps/web/src/lib/workflows.ts`
owns the provider vocabulary and both request builders; all four surfaces import
it. It performs no I/O, so a test can assert the payload directly rather than
inferring it from a rendered result.

**Contract tests assert the request, not the response.** `workflow-contract.spec.ts`
captures the outgoing body in the browser. A fixture can answer anything; it
cannot change what was asked. This is the only class of test that could have
distinguished the two reported symptoms from real bugs, and it now covers
company identity, provider, flags, thesis text, filter inference, cross-console
parity and failure behaviour.

**The mock echoes the request.** Its workflow route answers about the company it
was asked about and 422s for one it does not know; its thesis route returns the
submitted text. A deterministic fixture is fine. An unfaithful one is not.

**Test data announces itself.** `/research/*` renders a "Preview data" strip
driven by the backend's OWN `/health.environment`, so an offline fixture can
never again be quietly mistaken for research. There is no client-side flag to
get wrong, and a real deployment reports `development`/`staging` and shows
nothing.

### The three real divergences the review exposed

**Industry was inferred and sent.** The product surface auto-filled an Industry
filter from the thesis parser and submitted it; the admin console fills only
region/country/sector and never sends industry. Echoing a moment-old detection
back as a request filter narrows the universe for no gain — the backend derives
industry from the same sentence anyway. Inference is now limited to the three
fields the console fills, and uncertainty resolves toward **breadth**. The
detected industry is shown, not applied. Inferred filters are also CLEARED
whenever detection yields nothing — including when the parse request fails,
which previously left the previous thesis's scope attached to the next one.

**The clean flow stopped after step one.** It ran the workflow and linked the
reader to the DRAFT it produced, which has no structured content, so the
research view had nothing to render. It now chains the final-report generator
exactly as the admin console does from the draft's own page, and links to the
report that second call returns.

**A linked report was read as a completed analysis.** The discovery candidate
card hid its research action whenever `analysis_report_id` was set — but the
screening scan writes one for every ticker it touches, so every freshly screened
candidate claimed it had already been researched. Six real candidates in a local
luxury run offered no way to research any of them. The action is now always
offered, as in the console, and a linked report is a separate, quieter link.

A fourth, smaller correction: the council checkbox was labelled "Run the
research council". `use_llm` gates the LLM-drafted sections node; the council is
a server setting applied at report assembly. The label now says what the flag
does.

### Consequences

**Positive.** The two consoles cannot drift on the contract, because there is
only one. The payload is directly assertable. A preview against fixtures now
reveals identity and thesis bugs instead of hiding them, and says on screen that
it is a preview.

**Negative / accepted.** `/research/company` now makes two sequential calls, so
it is slower than the single workflow call and has a partial-failure state to
report — which it does, rather than presenting an unfinished run as finished.
And the industry filter is reachable only by choosing it deliberately: a reader
who wants a narrow industry must say so, which is the intended trade.

---

## ADR-039: Presentation Formatting Is a Contract, and External Strings Are Assumed Hostile

**Date:** 2026-08-30
**Status:** Accepted

### Context

Two defects reached the live environment in PR #176 and were found only by
verifying against it. Neither was catchable by the suite that shipped with it,
and for the same underlying reason: the suite ran one machine and one set of
fixtures, and each defect needed a *second* set of conditions to appear.

**The date.** `/research/reports` threw React #418 on every load. `ReportLibrary`
is a Client Component rendering `new Date(...).toLocaleDateString()`, so the
text was produced twice — once by the Azure container, once by the browser — and
`toLocaleDateString` reads the runtime's own locale AND time zone. Locale changes
the shape of the output; the time zone can change the calendar day outright. On
a developer's laptop the two renders happen in the same process on the same
machine and always agree, so the bug is structurally invisible locally.

**The overflow.** The clean report scrolled sideways at 390px (scrollWidth 508).
`EvidencePanel` fell back to `doc.canonical_url` when a document had no title,
and a real issuer CDN link is 140 characters of percent-encoded text with
nothing to break at. Every fixture had a short title and a short field name, so
again the condition never arose locally.

### Decision

**One formatting contract, pinned on both axes.** `apps/web/src/lib/format.ts`
exports `formatDate`, `formatDateTime`, `formatNumber` and `isoTimestamp`, all
built on `Intl` formatters with an explicit locale (`en-US`) and an explicit
time zone (`UTC`). No user-facing component calls a host-default `toLocale*`
overload. UTC is chosen because these timestamps record when the *server* did
something and every other surface — the technical view, the API, the logs —
already speaks UTC; showing a reader's local day would put a different date
beside the same event elsewhere. `isoTimestamp` supplies the exact instant to a
`title`, so pinning the displayed day loses nothing.

The audit covered every locale-sensitive call in the app, not just the reported
one. The four new user-facing date renders moved to the helper. `FinalReportRenderer`
was also fixed: it has no `"use client"` directive but is imported by one, so it
ships to the client and hydrates, and its bare `toLocaleString()` on numbers is
the same hazard one step quieter — latent only while the container and the
reader both default to `en-US`. The remaining admin calls were left alone after
checking each: they are either Server Components (rendered once, never
hydrated) or client components whose data arrives from a `useEffect` fetch, so
their dates are never in the SSR HTML and cannot mismatch.

**Strings from outside the product are assumed hostile to layout.** A single
`.ib-breakable` utility sets `overflow-wrap: anywhere` plus `min-width: 0`, and
it is applied wherever a value the UI did not author is rendered: document
titles and URLs, appendix sources, disclosure titles and venues, evidence-channel
details, machine field paths, scope names, comparability reasons, council prose
and provider warnings. `overflow-wrap: anywhere` breaks only where a break is
needed, so ordinary prose still wraps at spaces; `word-break: break-all` would
chop every line mid-word. Nothing is clipped and no ancestor hides overflow —
a test asserts that `html`/`body` do not set `overflow-x: hidden`, because
hiding the symptom would satisfy a naive scrollWidth check while the content
was still cut off.

**An untitled document is labelled from its own URL.** `documentLabel` derives
"host — decoded final segment" rather than dumping the raw link. It invents
nothing: every part comes from the URL, the full URL remains the `href` and the
`title`, and a URL with no usable segment falls back to the host and then to the
URL unchanged.

### Consequences

**Positive.** The formatting hazard is now structural rather than per-line: a
new component that needs a date reaches for the helper. Long external strings
cannot break the page layout regardless of what an issuer names a file. Both
regression tests were verified by reverting each fix and watching them fail.

**Negative / accepted.** Displayed dates are UTC, so a reader far from Greenwich
may see the previous calendar day for a late-evening event; the `title` carries
the exact instant, and consistency with every other surface is worth more than
matching each reader's midnight. `documentLabel` is a heuristic — it produces a
readable label, not the issuer's official document title, which is why the full
URL stays one hover away.

**A note on what the tests taught.** Reverting the date fix made the UTC+14 case
fail while the Europe/Prague case still passed: Prague only flips the calendar
day for timestamps in the last hours of a UTC day, and the fixtures' timestamps
sit at 10:00 UTC. Sampling one nearby zone would have reproduced the original
blind spot. Divergence tests need to span the extremes, not merely differ.

---

## ADR-040: The Reader Gets the Research; the Record Is Reported as the Record

**Date:** 2026-08-31
**Status:** Accepted

### Context

ADR-037 built a reader-facing surface that refuses to flatten the research
state. Real use showed it succeeded at that and stopped short of the point:
what it presented was still, largely, what the PIPELINE did.

Two things were concretely wrong, both verified against the live database
(~1,000 reports, 19 companies) rather than against fixtures:

**Discovery had a council and never showed it.** The run-level discovery council
has existed since Phase 28B. It reads a whole candidate set, places each
candidate into an internal research-priority band with a cited reason, records
where its agents disagreed, and writes a chair synthesis — and all of that was
visible only in `/admin/discovery`. The reader-facing page showed candidate
cards and an internal score, so a person could see WHICH companies a run
surfaced and nothing about what the council made of them.

**The report gave the wrong things prominence.** Measured on live PNDORA / CFR /
MRNA / MONC reports: `risk_analysis` carried 1 business risk against 7–8
source-quality risks, and "Key risks" showed them as one list. The chair's
`primary_open_questions` — the section a reader would take as "what remains
unresolved about this company" — opened with
`Blocking gap: Required field missing: identity.isin` on three of four issuers.
`bear_case.key_unknowns` was six-sevenths machine record entries. Meanwhile
`business_moat` (500–620 characters of substance per issuer), every agent's
`key_points` and `risks_or_gaps`, `bull_case.assumptions` and
`bear_case.potential_headwinds` were persisted and rendered nowhere.

The report was not missing research. It was showing the record instead of it.

### Decision

**Reuse the council; never build a second one.** `/research/discover` reads and,
on explicit request, starts the EXISTING run-level council through the same
endpoint, job and persisted result the admin console uses. There is no second
prompt, no browser-side summarisation, and no council run triggered by a page
load — a council costs real tokens, so it starts when a person asks.

**Present the council's own shape, and say what shape that is.** The chair emits
BUCKETS, not a ranking; its JSON contract has no rank field. Candidates render
in the order returned, and the UI states that the council does not rank within a
band rather than numbering them as though it did. Disagreement is defined as two
agents assigning different `internal_action` values to the same candidate — one
closed vocabulary compared like for like. Comparing two pieces of agent prose
and calling the difference a disagreement would manufacture the feature.

**Route each statement to the section that describes what it is.** Three splits,
all deterministic:

- A risk to the BUSINESS and a limit on the RESEARCH are different claims.
  `data_quality_risks` and `source_quality_risks` move out of "Key risks" and
  into research confidence.
- A record-completeness entry is not an argument. The deterministic layer writes
  fixed, generated forms — `Blocking gap: …`, `Required section absent: …`,
  `Legal entity verification not complete: …`, bare dotted machine paths. A
  predicate matching that FORM (not its meaning) routes them out of the bear
  case and the open-questions list into research confidence, where they are
  reported in full.
- Open research questions come from the COUNCIL — red team first, then each
  analyst in run order — because those are written about the business. The chair
  section's list is assembled deterministically from bear-case unknowns and
  bull-case missing evidence, and is used only when no council ran.

**Progressive disclosure is about weight, never about content.** Evidence,
sources, channels and the exhaustive machine-gap list are all still present,
complete and unedited; they sit behind one disclosure instead of interrupting
the reading flow. Source-tier codes are translated for DISPLAY only — the stored
value is unchanged and remains on the element as its `title`.

**"Current research" is resolved, not guessed.** A candidate's
`analysis_report_id` points at whatever the screening scan linked, which for 126
of the newest 200 live reports is a legacy pre-council draft. The resolution
uses the backend's own semantics — `final_report_version` as the legacy marker,
parseable `report_content` as the structured test, newest-for-that-company by
`(created_at DESC, id DESC)` — over a company-scoped read, and never a global
newest, a title match or the first row returned.

### Consequences

**Positive.** The council's judgement reaches the person it was produced for.
Every agent's conclusion, the red team's challenge and the chair's synthesis are
readable without entering the admin console. A candidate card no longer offers a
pre-council draft as though it were research, and a reader who lands on an old
artefact is told so and given the current one. The record is still complete: it
is reported as the record.

**Negative / accepted.** The routing predicates are matched against the
deterministic layer's current output FORMS. If that layer changes its wording,
a record entry would read as an analytical one again — which is why the
predicate matches generated prefixes and machine-path shapes rather than
keywords, and why it was validated against four live issuers before being
trusted. Reader-facing "open questions" now depend on the council having run;
where it did not, the section falls back to a list that is honestly weaker.
And resolving a candidate's current report costs two reads per candidate, which
is the price of not guessing.

**One backend change, and only one.** `GET /api/v1/reports` gained an optional
`company_id` read filter. Paging the global list and filtering client side is a
window, and with ~1,000 reports on one company-heavy environment that window
silently loses a company's current report. Nothing else server-side moved: no
migration, no new endpoint, no change to research semantics, ingestion, period
normalisation, council architecture, discovery scoring or report generation.

**What the live data taught.** The first implementation sourced open questions
from the chair's `primary_open_questions` because that is what the field is
called. Fixtures made it look right. The live reports made it obviously wrong —
the top six questions for PNDORA were four blocking-gap records and a valuation
input list. A fixture written by the same person who writes the derivation will
agree with it; only real payloads disagree.

---

## ADR-041: The Council's Job Is Interpretation, and Its Output Contract Says So

**Date:** 2026-08-31
**Status:** Accepted

### Context

ADR-040 put the research first in the reading order and routed the record-keeping
out of the argument sections. Real use showed the ordering was right and the
CONTENT underneath it was still not decision-useful: the agents were describing
the data package rather than the business.

Measured on the live persisted council output for PNDORA, CFR, MRNA and MONC —
252 bullets across eight agents — **8% were economic interpretation, 51% were
bare restatements of figures already in the report, and 41% were statements
about what data was missing.** All eight agents produced near-identical text.
Three of them restated the same six figures verbatim.

That was not a rendering fault. The agents were doing exactly what the contract
asked. The JSON shape said `"summary": "<=600 chars, factual"`; the safety rules
said every factual claim must cite evidence; and the only other field was
`risks_or_gaps`. A model given a slot for FACTS, a slot for GAPS and an
instruction to be factual will produce facts and gaps. There was nowhere to put
what the evidence MEANS, so nothing meant anything.

### Decision

**Add the missing slot, and gate it as hard as a fact.** `AgentImplication`
carries a `statement`, the `mechanism` it rests on, a `direction` from a closed
set (supportive / pressuring / mixed / neutral) and its own citations. It is
deliberately separate from `key_points`: a figure and an interpretation of that
figure are different kinds of statement, and a research reader has to be able to
tell them apart. The citation checker applies the same rules it applies to a
fact — un-cited material interpretations move to `unsupported_claims`, invalid
ids are dropped, scope/period grounding is enforced, and the direction is
coerced rather than trusted.

**Give each agent its own job back.** The role instructions now ask the
Financial Analyst for growth quality, margin direction, cash conversion,
leverage and capital allocation; the Business analyst for durability; the
Catalyst analyst for what changed, why it matters and what to watch; the Risk
analyst for a chain from risk to financial consequence; the Valuation analyst
for observable context rather than a list of absent inputs; and the Red Team for
the economic case rather than the completeness of the pack. The Source Quality
Critic keeps the evidence as its subject — it is the one role whose job that is.

**Let the chair answer the question a reader has.** `CommitteeSynthesis` carries
a `fundamental_setup` (constructive / mixed / cautious / insufficient_evidence),
the strongest evidence each way, resilience and fragility factors, the key
debate, what would strengthen or weaken the case, and what to watch. The setup
is a research characterisation with a closed vocabulary — there is no
BUY/SELL/HOLD analogue in it and none can be introduced by drift.

**Directional language is allowed; actions and projections are not.** An
analysis that may not say a factor "could pressure future equity value" cannot
do its job. What stays forbidden is the ACTION (BUY/SELL/HOLD/WATCH) and the
unsourceable NUMBER (price target, fair value, expected return, percentage
upside). The production safety gate already drew that line correctly — it bans
"upside of" and permits "downside risks" — and one test that was stricter than
the policy it guarded was corrected to use the gate itself.

**Numbers in council prose must reconcile with the report's own figures.**
Where a sentence states a figure for a metric and period the report holds
canonically, and it disagrees, the sentence is WITHHELD and reported as
conflicting. It is never silently resolved in favour of one of the two.

### Consequences

**Positive, measured on a real payload.** Re-running the updated council against
the live Pandora evidence (18 items, 55 structured facts): all 8 agents
completed, producing 34 implications where the field previously did not exist.
Economic interpretation rose 8% → **32%**; data-completeness talk fell 41% →
**21%**. The chair returned an issuer-specific synthesis — "net debt up 376%
over five years", "watch free cash flow generation and capital expenditure
levels", a key debate naming the disagreement between the financial analyst and
the red team.

**Negative / accepted.** A richer contract costs output tokens. The company
council's flat 1200-token budget could not hold it: on the real Pandora pack two
of eight agents truncated mid-object into a permanent `LLMJsonError`. Raised to
2200, all eight complete. The discovery council's per-candidate rate rose 200 →
300 and its cap 5000 → 7000 for the same reason. Both budgets are also the token
pacer's admission estimate, so throughput per minute falls accordingly — sized to
the contract, not padded.

**The numeric guard needed four corrections before it was fit to suppress
anything**, every one found by running it over real council prose rather than
fixtures:

1. `H1`/`FY2025` were read as the numbers 1 and 2025.
2. A trailing comma was swallowed into the number, so `2026,` looked like a
   grouped magnitude and escaped the bare-year rule.
3. Every number in a sentence was tested against the one metric detected in it,
   so "net debt 13,719m vs equity 5,282m … if EBIT falls" was called a
   contradictory operating-profit claim. Fixed with proximity.
4. Only the snapshot's headline slots were canonical, so the council citing a
   historical period the report also holds was called wrong.

Before those fixes it flagged **13 of 111** real sentences, all false positives.
After them: **0 false positives, 18 checked consistent, and both seeded
contradictions still caught.** A guard that suppresses correct analysis is worse
than no guard, which is why it only adjudicates a metric AND a period the report
actually holds, at group scope, in the same written form.

**What the live run taught.** The first AFTER measurement was invalid: the
evidence pack was built without the structured facts, so the council saw a share
price and correctly answered "not enough data". Measuring a prompt change
requires giving the model the same evidence the real pipeline gives it —
otherwise the measurement is of the harness.

---

## ADR-042: One Routing Rule Decides Economic Signal from Research Limitation

**Date:** 2026-09-01
**Status:** Accepted

### Context

ADR-041 gave the council somewhere to put an interpretation, and it started
producing them. Human review of the rendered pages found the remaining problem:
the reader-facing surface still mixed two kinds of statement under one heading.

"Net debt rose while equity fell, which raises refinancing risk" and "Catalyst
coverage rests on the issuer's own channel" are both true and both matter. Only
the first is a reason a business might become less valuable. Shown together
under "what could pressure value", the second tells a reader that the research
process is a hazard the company faces.

The leak had two sources, and neither was fixable by editing a section:

* **Role.** The Source Quality Critic writes fluently about economics — on
  Richemont it produced "The Jewellery Maisons segment remains the core profit
  driver". Its subject is nonetheless the evidence, and it attached
  `direction: pressuring` to statements that then landed in an economic column.
* **Wording.** Any agent can write an evidence statement. The chair's own
  "strongest negative evidence" on Moncler included "Absence of full annual
  financial data and key metrics".

### Decision

**One classification rule, in the view-model layer, applied everywhere.**
`classifySignal` answers with one of nine kinds — economic support, economic
pressure, resilience, fragility, catalyst, company risk, investor question,
research limitation, technical gap. No score, no model, no per-section
exception.

It decides in three steps:

1. **Record form** (`isRecordGapStatement`) → technical gap.
2. **Wording** (`isEvidenceStatement`) → research limitation. Four patterns,
   each learned from live output: an ABSENCE word beside an EVIDENCE noun; an
   evidence-subject phrase carrying no absence ("coverage rests on the issuer's
   own channel"); an EPISTEMIC CONSEQUENCE, where what is limited is assessment,
   confidence, visibility or comparability rather than the business; and
   evidence PRESENCE framed as a finding ("closing price is available as a
   factual data point" was offered as strongest positive evidence).
3. **Role.** The Source Quality Critic's output is a research limitation
   whatever it says. Source weakness changes CONFIDENCE in a conclusion; it does
   not change a company's value.

Only after those does the slot decide what the statement would otherwise be.

**Nothing is dropped.** Every routed statement is reported under research
confidence. The chair section renders its four lists AFTER routing, so it cannot
show as a "strongest negative" a line the summary just moved — the two would
contradict each other on the same page.

**The comparison compares businesses.** Candidate columns are now council view,
growth signal, profitability signal, cash generation, resilience, key catalyst
and main downside; evidence confidence, research readiness and the deterministic
score follow them and are labelled as qualifying the answer rather than being
it. Known gaps and disclosure coverage are no longer columns; they sit in a
collapsed "Research limitations" disclosure on each card. A dimension the
council did not establish says **"Not established"** — it never borrows a
completeness number.

**Open research questions are about the business.** On live data every one of
the 90 `risks_or_gaps` items across four issuers was an evidence statement, so
89 route to research confidence and the section's real source is the chair's
`key_debate`. A section with nothing to say says so rather than filling itself.

**The chair prefers its structured synthesis.** Setup, strongest evidence each
way, resilience, fragility, key debate, what would strengthen or weaken, what to
watch. `research_next_steps` is a sourcing to-do list, so it comes last and
collapsed; the legacy fields are a fallback for reports written before the
synthesis existed.

**The investor financial view speaks English.** Notes naming `_current_period`
or `T1_primary_filing` are replaced with the same facts in words. The originals
survive verbatim under Evidence & sources, so nothing is edited away.

### Consequences

**Positive, measured on live output for PNDORA, CFR, MRNA and MONC.** Zero
source/data-gap statements in "what could drive value higher" or "what could
pressure value"; zero Source Critic statements in any economic section; open
questions reduced to the committee's own key debate. Both control sets hold —
every known evidence statement is caught, and none of the real economic
implications is.

**Negative / accepted.** The wording rule is a set of patterns matched against
prose, and prose changes. It is calibrated against four issuers' output at one
point in time, with an explicit economic control set that must keep passing —
but a future model phrasing an evidence limitation in a shape none of the four
patterns covers would reach an economic column again. That is why the rule is
one function with one test suite rather than filters scattered per section.

It also errs toward routing OUT. On Moderna, an issuer with almost no financial
evidence, both economic columns are now empty and the page says the council
recorded no interpretation. That is the correct answer for that report and it
looks like a bug until the reason is read.

**A statement mixing an economic claim with an epistemic caveat stays economic.**
"Positive revenue growth and profitability indicate ongoing business momentum,
but the part-year data limits full-year trend assessment" is kept as a value
driver: its main clause is about the business. Over-suppressing a hedged but
real finding would be the worse failure.

---

## 44. The product front door submits a job; only the state is durable, and we say so

**Date:** 2026-09-02
**Status:** Accepted
**Context:** POST-V2 live corrective (`fix/v2-live-acceptance-blockers`)

### Context

`/research/company` ran the pipeline inside the browser's HTTP request. On live
data that is ~154s of primary-document ingestion plus ~145–190s of council,
against an Azure App Service gateway ceiling of ~230s. Measured live: HTTP 502
at ~206s, HTTP 504 at ~240s, transaction rolled back. A user selected Pandora,
waited five minutes, and got an error with nothing kept. The product's primary
entry point was unusable, and the fault was structural rather than a matter of
tuning.

The discovery-candidate CTA had already solved exactly this and its fix was
already live-verified. What blocked reuse was that the executor and the job
lifecycle lived inside `market_discovery_service`, keyed on a
`DiscoveryCandidate`.

### Decision

**Reuse the existing mechanism rather than build a second one.** Commit a job
envelope, return 202, drive the work from a background task with its own DB
session, poll a plain GET. No queue broker was introduced: this deployment has
none, Service Bus is a later phase, and adding one would be a second job
architecture rather than a fix for the blocker in front of us.

**Unify what is genuinely one thing, and only that.**
`company_research_service.execute_company_research` is the single implementation
of "research this company end to end"; the discovery-candidate path calls it
too, passing its lineage. `research_job.py` is the single job lifecycle —
states, abandonment threshold, derived `interrupted`. What stays separate is the
durable STORE: the candidate keeps its `raw_signal_json` envelope and its
live-verified poller, the company job uses `AgentRun` + `AgentStep`. Two entry
points with different natural keys and different pollers do not need one table
to be one workflow.

**Say precisely what survives what.** Job STATE is durable — committed before
any expensive work, recoverable by id or by company, unaffected by the browser.
Job EXECUTION is process-local, so an app-process recycle mid-run stops the
work.

**Stages are the graph's own node names.** `NODE_TO_STAGE` maps them onto reader
words; a node absent from the map does not move the stage. No percentage is
produced, because the graph cannot know how long a node will take.

### Consequences

**Positive, measured on the live local stack with the real provider.** Submit
0.162s (PNDORA) and 0.160s (CFR) against a target of under five seconds. Both
runs completed server-side (48.0s and 55.4s), council 8/8, reports persisted,
identity exact. A second submit while a run is in flight returns the first job
and starts nothing. A refresh reattaches by URL; a lost URL recovers by company.

**Negative / accepted — the honest limit.** A recycle mid-run loses the work in
flight. It is not hidden: the job reads as `interrupted` with `recoverable:
true`, derived at read time from the same threshold the restart decision uses,
a startup sweep logs what was lost without rewriting the dead worker's last
envelope, and the UI offers a retry. Everything the ingestion and workflow
stages already persisted stays persisted. Making execution survive a recycle
needs a broker and a worker service, and that is a phase, not a line.

**Negative / accepted.** Selecting a company adopts an IN-FLIGHT job only. A
run that finished last month is not this session's work, and showing it beside
"Research complete" would read as something the reader had just done. Past runs
are in the research library.

---

## 45. Scope is part of the canonical key, so the numeric guard got stricter

**Date:** 2026-09-02
**Status:** Accepted
**Context:** POST-V2 live corrective

### Context

The council-prose numeric guard built its canonical set from GROUP figures alone
and tested every sentence against it. On a segment reporter that is not a
conservative simplification, it is a category error: Richemont's Specialist
Watchmakers operating profit of EUR 107m was held up against the GROUP operating
profit of ~EUR 4.5bn, found to disagree, and withheld. **32 statements were
suppressed in one live CFR report**, including every sentence that made the
segment picture legible — by a guard whose stated purpose is to stop a report
contradicting itself.

### Decision

A metric is not one number. It is one number per (period, scope). The index is
keyed that way, every legitimate scope is in it, and a claim is adjudicated
against the scope it is actually about:

* the sentence names Group → compared against Group, only Group;
* the sentence names a segment THIS REPORT REPORTS → compared against that
  segment;
* the sentence names no scope → compared against every scope the report holds
  for that metric, because guessing which one it meant is the substitution that
  caused the defect;
* the scope has no canonical value for that metric → not adjudicated at all.

Scope keys mirror `fact_scope.py` exactly, so a fact persisted as Group cannot
be read here as a segment. Segment names come from the report's own scoped
facts, so a capitalised phrase is never mistaken for a business area. A stated
currency that no candidate figure shares makes the claim unadjudicable — this
layer holds no exchange rate and must not invent one.

### Consequences

**Positive.** The guard is MORE precise, not weaker. "Group operating profit was
EUR 107m" is now a contradiction that gets CAUGHT, and it could not be caught
before, because 107 was in the comparison set for the group key. Every
previously-fixed false positive stays fixed (H1 as a period, bare years,
trailing commas, percent-beside-amount, historical series).

**Positive, from auditing the three live reports.** Two further false-positive
classes, both older than this corrective and both only visible on real prose:

* **A number belongs to one metric.** "Total assets of DKK 29.603 billion
  relative to revenue and equity suggest a capital-intensive business" states
  the total-assets figure correctly, but "revenue" sits inside the proximity
  window, so 29.603bn was tested as a revenue claim against DKK 32,549m and the
  sentence was withheld. A number that is some OTHER named metric's figure is
  no longer evidence against this one. The exclusion is per-METRIC and
  scope-filtered on purpose: a number matching a DIFFERENT SCOPE of the SAME
  metric is exactly the mis-scoping the guard exists to catch, so it excuses
  nothing.
* **A multiple is not a level.** "net debt ~2.6x equity" states a relationship.
  Reading 2.6 as a net-debt level contradicted DKK 13,719m.

Re-audited across all three live reports: PNDORA **0 withheld** (was 2, both
false), CFR **0** (was 32 on the pre-corrective report), MRNA **8 — all
genuine**: its own canonical total assets are 10,961m USD while all eight
agents state $12.338bn. Two representations of one fact disagreeing is what
this guard is for, and it is doing it.

**Negative / accepted.** An unscoped claim is cleared by matching ANY scope.
That is deliberate: for a guard that SUPPRESSES content, the correct failure
mode is a missed contradiction rather than an invented one. A sentence that
genuinely means Group but does not say so, quoting a segment's number, passes.

**Positive, and only visible on live data.** Auditing the guard against the
freshly generated live Richemont report exposed a second, older defect in the
same layer: the extractor does not spell "percentage" one way. A reconstructed
trend series carries `"%"`; the financial snapshot carries `"percent"`. Testing
for the literal `"%"` classified the canonical group margin as an AMOUNT, so the
guard looked for a non-percent number near "operating margin", found the €4.5bn
operating profit in the same sentence, and called

> Group operating profit was €4.5 billion with an operating margin of 20.0% in 2026.

a contradiction of a canonical margin of exactly 20. NINE correct Group
statements were withheld that way on that one report — every agent that stated
the group result, and the chair. Unit spellings are now normalised once, at
index-build time, so "canonical" means one form. Re-audited against the same
live report: **0 withheld, 63 consistent** (was 9 withheld, 52 consistent).

**Negative / accepted.** Council agent SUMMARIES are still not reconciled — the
guard covers findings, implications and the chair's lists. Widening it to
summaries would risk replacing a whole agent's summary with a conflict notice,
which is a different and larger trade.

---

## 46. The two cases are the council's argument, not the deterministic layer's

**Date:** 2026-09-02
**Status:** Accepted
**Context:** POST-V2 live corrective

### Context

The clean report rendered `bull_case` and `bear_case` verbatim from the
deterministic Phase-9 layer. Those slots are written for an engineer: on live
PNDORA / CFR / MRNA reports their points named source tiers
(`T1_primary_filing`, `T6_model_estimate`), provider states
(`free_real_not_sourced`), machine field paths (`identity.isin`) and
blocking-gap counts. A reader opening "Bear case" was told, as the argument
against a business, that an ISIN had not been sourced.

Meanwhile the council HAD argued both cases — in `implications`, in the chair's
strongest evidence each way, in resilience/fragility, in what would strengthen
or weaken it, and in the red team's own economic challenges. None of it reached
those two sections.

### Decision

Assemble the cases from the structured council output, deterministically. No
model is called because a report was opened, nothing is summarised, and every
line is something the council already wrote. Each line passes the same signal
rule the rest of the reader-facing view uses, so an evidence statement cannot
appear as an investment argument.

Implementation vocabulary is TRANSLATED, not deleted. Deleting loses the
sentence's meaning; rewriting the stored value would make the clean view and the
technical view disagree about what the pipeline said. `humaniseTechnical` maps
tier codes, provider states and machine field paths to human words at render
time only.

A report whose council predates the structured fields falls back to the
deterministic narrative — routed through the same rule and translated — and the
footnote says which layer the argument came from.

### Consequences

**Positive.** Bull and bear now carry the council's economic reasoning, and no
tier code, provider state, field path or blocking-gap phrase reaches either case
or the company-risk section on any fixture or live report checked.

**Positive, and only visible on live data.** Auditing the three freshly
generated live reports (PNDORA, CFR, MRNA) showed the two case sections clean —
and **Key Risks still full of research state**. Twenty-two of the twenty-four
items in those reports' company-risk slots were statements about the research,
and "Research incomplete: 30 blocking gaps in the research package… have not
been assessed" was the FIRST thing the section offered a reader for all three
issuers. The wording rule now covers the shape those share: a consequence
landing on the RESEARCH — "have not been assessed", "not yet computed",
"requires T2/T3 research". The verb list is epistemic on purpose, so "margin
has not yet recovered" stays a business finding. The two genuine company risks
in that live set, both Moderna's, are untouched. Key Risks also translates
implementation vocabulary now, as the two case sections already did.

**Negative / accepted.** On a report that has BOTH a council case and
deterministic prose, the deterministic prose no longer appears in the clean
view. Its analytical points are not shown there at all. That is the requirement,
the record entries still route to research confidence, and the unedited original
stays on the technical report page — but it is a real reduction in what the
clean view shows for such a report, not merely a re-filing.

---

## ADR-043: V2 Is Frozen as a Tag and a Branch Before V3 Development Begins

**Date:** 2026-09-04
**Status:** Accepted

### Context

V3 is a research-engine and data-platform evolution, not a bug-fix campaign. It
adds a durable worker topology, a persistent corpus, an entity master, external
research providers and a new schema surface. Any one of those could destabilise
the system for weeks.

Meanwhile `main` is not a development branch. It is the currently approved,
deployed, private-use production version, live at `ib-stg-rg`. The user relies on
it. There is exactly one environment.

The failure mode to avoid is the ordinary one: V3 lands incrementally on `main`,
each step individually defensible, and at some point there is no commit that is
both recent and known-good to fall back to.

### Decision

The commit on `main` immediately before V3 work begins — `4b60e07` — is the **V2
baseline**, and it is pinned in two independent ways:

1. An **annotated, immutable tag** `v2-final-pre-v3-2026-09-04`, whose message
   records the SHA, the date, the Alembic head (018) and the extraction pipeline
   version (15).
2. A **branch** `release/v2-current` at the same SHA, so V2 can receive emergency
   maintenance without touching the V3 line.

V3 development happens on `develop/v3`, created from the same SHA. Implementation
happens on `feature/v3-*` branches that merge **only** into `develop/v3`.

`main` receives no V3 commits, and V3 is not deployed, until the user explicitly
approves promotion.

### Consequences

- Rollback is `git checkout release/v2-current`. There is no reconstruction step.
- Two refs must both be wrong for the baseline to be lost; a tag can be deleted,
  a branch can be force-moved, but not accidentally in the same operation.
- The deploy workflows trigger on `push: branches: [main]` only, so pushing the
  V3 branch cannot deploy — this was verified before the first push rather than
  assumed.
- V2 and V3 can diverge, so a V2 hotfix must be deliberately forward-ported. That
  cost is accepted; it is much smaller than the cost of an unrecoverable `main`.

---

## ADR-044: External Research Output Enters as a Lead, Never as Evidence

**Date:** 2026-09-04
**Status:** Accepted

### Context

V3 uses external research and search providers — a managed Deep Research product,
a cheap bulk researcher, a neural search index. These systems return confident
prose containing claims, figures and citations. Some of the citations do not
resolve. Some resolve to pages that do not contain the claim. Some figures are
right for the wrong period or the wrong reporting scope.

The platform's entire value is that a number in a report can be traced to a
document. Accepting provider output as evidence would end that in one step, and
it would end it *invisibly*, because the output is well-formed and cites sources.

### Decision

A provider result is a `ResearchProviderResult` and nothing in it is evidence.
Every claim and source candidate is persisted as a `ResearchLead`, attributed to
the provider and model that produced it, and must pass through an explicit
promotion path:

```
lead → InvestingBuddy independently fetches the cited source (or verifies against
       a trusted structured API) → Canonical Source → EvidenceFragment →
       Canonical Fact / ResearchFinding
```

A lead whose URL is unreachable, or whose claim is absent from the fetched
document, or whose period or scope does not match, is **retained as a rejected
lead with its rejection reason**. It is never silently dropped and never quietly
promoted.

Separately, the **transport never determines the source tier**. Exa delivering a
Reuters article yields Reuters-tier content; SEC EDGAR delivering a filing yields
primary-filing-tier content. `transport` and `content_origin` are distinct fields.

### Consequences

- `verification_survival_rate` becomes measurable per provider, which is what
  makes the benchmark meaningful and `cost_per_verified_finding` computable.
- Rejected leads are training data for provider selection, consistent with the
  existing rule that rejected companies and failed analyses are kept.
- A provider that cannot produce traceable sources is structurally limited to
  suggesting where to look — which is still useful, and is presented as exactly
  that.
- Verification costs retrieval budget. This is accepted: unverified research has
  negative value because it consumes the budget that would have found the answer.

---

## ADR-045: Search, Model and Research Providers Are Three Separate Interfaces

**Date:** 2026-09-04
**Status:** Accepted

### Context

The obvious way to add web research is to pick a vendor whose model can search —
one API key, one integration. It is also the way to end up unable to change
either the model or the search index without changing the other, because the two
arrived welded together.

These capabilities genuinely change independently. Search quality is about index
coverage and freshness. Model quality is about reasoning. Managed Deep Research
is a different product with a different cost model again (per-run, not
per-token). Pricing moves independently too: at InvestingBuddy's measured call
shape, DeepSeek is 1.4× cheaper than `gpt-5.6-luna` off-peak and 30% *more*
expensive at peak — a conclusion that inverts within a single vendor over the
course of a day.

### Decision

Four independent interfaces, owned by InvestingBuddy:

- `ModelProvider` — one prompt in, one structured completion out. Extends the
  existing `LLMClient` (`app/services/llm/client.py`).
- `SearchProvider` — query in, ranked URLs out. Never bound to a model vendor.
- `ResearchProvider` — managed multi-step investigation, returns
  `ResearchProviderResult`.
- `BrowserProvider` — renders JS-gated pages, last resort only.

Domain modules name a **routing slot** (`chair_model`, `cheap_research_model`,
`red_team_model`, …), never a model. The research domain imports provider
*interfaces*, never provider *SDKs*. If swapping a search vendor requires editing
anything under the research domain, the abstraction has failed.

Fake providers remain first-class and stay the only clients the unit suite uses.

### Consequences

- Four interfaces to maintain instead of one integration.
- Vendor swaps cost one adapter.
- Providers can be benchmarked against each other on identical tasks, which is
  the only honest way to choose one.
- The Red Team slot can deliberately use a different vendor from the analyst
  slots — a model challenging its own family's output shares its blind spots.
- Consumer AI subscriptions are excluded as production architecture: production
  uses supported APIs under their terms. Consumer products may be used manually
  for developer benchmarking only.

---

## ADR-046: The Durable Job Contract Keeps V2's Status Vocabulary

**Date:** 2026-09-04
**Status:** Accepted

### Context

V2 job *state* is durable — committed to PostgreSQL before expensive work starts.
V2 job *execution* is not: it runs in the API process via FastAPI
`BackgroundTasks` at six call sites, so an App Service recycle kills every
in-flight run. `research_job.py` is honest about this in its own module docstring.

V3.0 makes execution durable with a leased worker. The temptation when building a
new job system is to design a new status vocabulary — `queued`, `claimed`,
`succeeded`, `dead_letter` — because those words describe a queue accurately.

But `research_job.py` already owns a status vocabulary that the web app polls,
that the admin console renders, and that a body of tests asserts. It also owns a
subtle and correct decision: `interrupted` is **derived at read time, never
stored**, because a stored status would need a writer that is running, which is
exactly what is absent in the situation it describes.

### Decision

The V3 durable job record keeps V2's status vocabulary verbatim: `pending`,
`running`, `completed`, `completed_with_warnings`, `failed`, plus derived
`interrupted`. `research_job.py` remains the single source of truth for status
constants, in-flight/terminal classification and staleness derivation.

V3 adds only the columns durability actually requires: `idempotency_key`,
`attempt` / `max_attempts`, `lease_owner` / `lease_expires_at`, `available_at`,
`last_heartbeat_at`, `cancel_requested`, and a dead-letter terminal state with a
reason.

The queue broker is a **delivery hint**, not the source of truth. The job store
is authoritative, so a PostgreSQL-polling worker is a valid production mode and
V3.0 is testable with no cloud dependency.

### Consequences

- The polling API contract does not fork. The web app needs no change to work
  against durable jobs.
- Existing job tests keep their meaning.
- Dead-letter is genuinely new and needs its own vocabulary entry — it is added
  as a terminal state alongside `failed`, not by overloading `failed`.
- Reusing a vocabulary designed before leasing existed means some names are less
  queue-idiomatic than they would be greenfield. That is accepted: a shared
  vocabulary the whole system already speaks is worth more than precise naming in
  one new module.

---

## ADR-047: Corpus Retrieval Is PostgreSQL Full-Text Plus pgvector, Not Azure AI Search

**Date:** 2026-09-05 · **Status:** Accepted · **Decided by:** user
**Supersedes:** the open state of [V3 OPEN DECISION #1](v3/OPEN_DECISIONS.md#1-azure-ai-search-vs-postgresql--pgvector)

### Context

V3.1 built the corpus behind a `SearchBackend` interface and deliberately stopped at
the backend-selection gate, shipping only an in-memory reference implementation. The
choice was between a managed hybrid search service and doing lexical and vector
retrieval inside the database that already holds the corpus.

The deciding constraint turned out not to be retrieval quality. It is that **V3 must
require no new paid SaaS subscriptions**, which removes a managed search service from
consideration regardless of how good it is.

### Decision

Corpus retrieval is **PostgreSQL full-text search plus `pgvector` semantic
similarity**, fused by InvestingBuddy's own rank fusion, with metadata filters in the
same query.

PostgreSQL already exists in the architecture, Azure Database for PostgreSQL supports
`pgvector`, and keeping retrieval in the same datastore as the canonical corpus
metadata means entity, period and scope filters apply in the same transaction as the
data they filter — which is the property that makes a scope filter trustworthy rather
than eventually consistent.

**Azure AI Search is not provisioned.** The `SearchBackend` interface is unchanged, so
adding it later costs one adapter rather than a rewrite.

### Consequences

- The lexical leg works with no extension at all. `pgvector` is required only for the
  semantic leg, and hybrid retrieval degrades to lexical when it is absent — which is
  a worse answer and never a wrong one, exactly as V3.1 established.
- Ranking quality is now InvestingBuddy's problem rather than a vendor's. That is
  accepted: the fusion is already the platform's own, and the alternative was a vendor
  ranking nobody could tune against the real-issuer set either.
- **Enabling the extension is a schema-affecting operation.** The migration is written
  and scratch-validated and **not applied to the deployed database** during this
  campaign, per the standing restriction.
- `test_the_production_backend_decision_is_not_taken_here` has served its purpose and
  is replaced by tests that pin the chosen backend's behaviour. Deleting a guard once
  the decision it guarded has been taken is the point of having written it.

---

## ADR-048: DeepSeek Web Search Is the Primary External Search Path; Exa and Perplexity Are Deferred

**Date:** 2026-09-05 · **Status:** Accepted · **Decided by:** user
**Supersedes:** the open state of [V3 OPEN DECISION #3](v3/OPEN_DECISIONS.md#3-exa-vs-perplexity-search)

### Context

The provider strategy planned to benchmark Exa against Perplexity and default to
whichever won on `cost_per_verified_finding`. Both require a paid subscription or
credits, and V3 must require neither.

DeepSeek is approved on pay-as-you-go and offers server-side `web_search`, which
covers the same need: find candidate sources on the open web for a research question.

### Decision

**DeepSeek `web_search` is the primary external general-web research and search path**
for initial V3. Exa and Perplexity are **deferred** — interfaces and fakes retained,
no adapters built, no credentials required, nothing blocked.

The acquisition hierarchy is ordered and explicit:

```
existing InvestingBuddy corpus
→ official structured API
→ official regulator/issuer source
→ current safe direct fetcher
→ DeepSeek web search
→ source URL retrieval THROUGH InvestingBuddy
→ verification
```

The order matters more than the membership. External search is the **fifth** resort,
after everything the platform already holds or can reach authoritatively — which is
also the cheapest ordering, so the cost constraint and the quality constraint agree
here rather than trading off.

### Consequences

- A search result is a `SourceCandidate` and a model claim is a `ResearchLead`. Neither
  is evidence until InvestingBuddy has fetched the underlying source itself and the
  claim has survived verification. **A search snippet is never canonical evidence**,
  and the snippet field is labelled untrusted precisely so that a prompt builder
  cannot forget it.
- The platform depends on one external search vendor rather than two candidates. The
  `SearchProvider` interface is what keeps that reversible, and it is now carrying real
  weight rather than being speculative.
- `cost_per_verified_finding` remains the benchmark metric, but the comparison is now
  DeepSeek against the native path rather than Exa against Perplexity. A cross-vendor
  search comparison is recorded as **not performed** rather than estimated.

---

## ADR-049: DeepSeek Access Is Governed by Document Rights, Not by Provider Geography

**Date:** 2026-09-05 · **Status:** Accepted · **Decided by:** user
**Supersedes:** the open states of [#4](v3/OPEN_DECISIONS.md#4-deepseek-data-governance-policy) and [#11](v3/OPEN_DECISIONS.md#11-private-data-external-model-policy)

### Context

V3.4.1 shipped a per-provider governance matrix that denied by default and recorded
DeepSeek as public-only, on the authority of #4 being open. The blanket rule it
encoded — `user_private → DENY` for every external provider — was a placeholder for a
decision nobody had taken, not a considered policy.

Two questions were tangled together: *where does this provider run* and *what is this
document's licence*. Only the second is answerable per document, and only the second
is what actually constrains use.

### Decision

**China location and storage are not a blocker for this project.** DeepSeek is approved
to process `public_official`, `public_issuer`, `public_web`, InvestingBuddy-derived
research context, **and user-private content when that document's own policy metadata
allows third-party model processing.**

DeepSeek must never receive: API keys, passwords, credentials, secrets, authentication
tokens, private system configuration, a document whose licence or rights metadata
forbids external-model processing, or anything marked `external_model_allowed=false`.

**Data rights and explicit source policy govern provider use. Provider geography does
not.** An unknown policy **fails closed**.

### Consequences

- The governance check moves from *provider × access class* to *provider × access class
  × document policy*. The coarse check remains as the outer bound — a provider with no
  recorded policy still receives nothing — and the document's own permissions are
  consulted inside it. Both are needed: the coarse one catches an unevaluated provider,
  the fine one catches a document nobody was allowed to send.
- `ArtifactPolicy` already carries six per-document permissions including
  `sent_to_external_model` and a per-provider allowlist, built in V3.1.1 for exactly
  this. That work is now load-bearing rather than anticipatory.
- **Rights are never inferred from the fact that a file was uploaded.** A private
  document with no explicit permission is unusable by an external model, which is the
  fail-closed direction and is the same rule the platform applies to an unstated
  financial period.
- A secret is not an access class and never becomes one. Credentials are excluded
  categorically rather than by policy, because a policy is something somebody can edit.

---

## ADR-050: The Existing Azure OpenAI Deployment Is the Strong-Model Fallback, and the Only OpenAI-Family Dependency

**Date:** 2026-09-05 · **Status:** Accepted · **Decided by:** user
**Supersedes:** the open state of [V3 OPEN DECISION #5](v3/OPEN_DECISIONS.md#5-openai-model-routing)

### Context

The routing question was which model fills which slot. Two constraints answer most of
it: no new commercial accounts, and the Azure OpenAI infrastructure in this repository
is already approved and already carries the council.

### Decision

**Cheap and bulk research goes to DeepSeek. Difficult final reasoning goes to Azure
OpenAI.** No new OpenAI commercial account is created; the existing Azure deployment is
the only OpenAI-family dependency.

Azure OpenAI's V3 role is stronger synthesis when needed, difficult Research Director
cases, complex evidence contradictions, Red Team where appropriate, the Chair and final
Council synthesis, and any task a benchmark shows it materially wins.

**Not every research subtask goes to the expensive model**, and routing stays
configurable.

### Consequences

- Domain logic continues to name a **slot**, never a model. The eight slots and their
  degradation behaviour are unchanged; what changes is that assignments now have
  defaults worth setting rather than being empty pending a decision.
- The two-vendor split is also a resilience property, not only a cost one: a DeepSeek
  outage degrades bulk research and leaves synthesis working, and the reverse leaves
  research working with a deterministic chair fallback — which V3.0 already built.
- Red Team vendor diversity is achievable *between* these two, and no further. That is
  a real limitation and is recorded rather than glossed: a Red Team drawn from the same
  vendor as the Chair shares its blind spots, and `shares_vendor_with` reports whether
  it currently does.

---

## ADR-051: Transcripts and IR Events Come From Free Public Issuer Sources, and Missing Means Missing

**Date:** 2026-09-05 · **Status:** Accepted · **Decided by:** user
**Supersedes:** the open states of [#8](v3/OPEN_DECISIONS.md#8-transcript-provider) and [#9](v3/OPEN_DECISIONS.md#9-quartr-vs-fiscalai)

### Context

Management language over time is one of the most valuable research inputs V3 planned
to add, and the two obvious vendors are paid subscriptions. The recommendation had been
"direct issuer first, vendor as fallback for coverage".

### Decision

**No paid transcript subscription.** The canonical transcript and IR-event architecture
is implemented anyway, and acquisition uses free, legally accessible public sources:
issuer IR sites, issuer-published transcripts, earnings releases, presentations,
capital-markets-day materials, regulatory filings and public event documents.

When a transcript is not publicly available, **missing means missing**. It is not
fabricated, and a paywalled source is not scraped.

### Consequences

- Coverage will be uneven, and worst exactly where the existing pipeline is already
  thinnest: European issuers who publish a presentation but not a transcript. The
  architecture must therefore represent *the event happened and no transcript is
  available* as a first-class state, distinct from *no event*.
- That distinction is what makes the gap actionable. "We have no transcript for CFR's
  FY2025 call" is a research gap a human can close by other means; an absent row is
  indistinguishable from a company that held no call.
- Implementing the canonical model without a vendor is deliberate. It means a future
  subscription is an adapter rather than a schema change, and it means the free path
  is the *default* rather than the fallback — which is the ordering that survives the
  subscription never being bought.

---

## ADR-052: Research Budgets Are Bounded Technical Defaults, and Research Modes Are Depth Presets

**Date:** 2026-09-05 · **Status:** Accepted · **Decided by:** user
**Supersedes:** the open states of [#13](v3/OPEN_DECISIONS.md#13-model-cost-thresholds) and [#14](v3/OPEN_DECISIONS.md#14-research-mode-budgets)

### Context

`consumption.ResearchBudget` and `ToolBudget` shipped with every limit defaulting to
unbounded, because the numbers were user-owned and #14 wanted them derived from
measurement rather than guessed. That was right at the time and is now the wrong
default: an unbounded budget on a system that can now issue web searches and call an
external model in a loop is a runaway waiting for an occasion.

### Decision

**Bounded technical defaults now; no business budget chosen.** Configurable maxima
exist for research rounds, web searches, tool calls, documents, tokens, wall time and
DeepSeek calls, and they vary by research mode.

QUICK / STANDARD / DEEP / MAX are **research-depth presets**, not price tiers. No
subscription price is attached to any of them. MAX takes the highest bounded limits
and is **still finite**.

### Consequences

- Two instructions pull in opposite directions and both are honoured deliberately:
  **the absence of a monthly business budget is not unlimited execution**, and **V3 is
  not blocked on pricing decisions**. The resolution is that the *technical* ceilings
  become real numbers and the *monetary* ceiling stays unset — a run can be stopped by
  a round limit, a search limit or a wall clock, and cannot be stopped by a dollar
  figure nobody has chosen.
- A cost of unknown is still never reported as zero. Money remains derived from a price
  book, so setting prices later re-prices history rather than rewriting it.
- The numbers chosen now are estimates against measured anchors (a full company run is
  261-451s, ingestion ~154s, a council ~145-190s) and are expected to be tightened
  once real DeepSeek runs are measured. A default that is wrong but finite is
  recoverable; an unbounded default is not.

---

## ADR-053: The Semantic Leg Ships Portable and Feature-Gated, Because `pgvector` Is Not Installed

**Date:** 2026-09-06 · **Status:** Accepted · **Decided by:** agent (technical, reversible)
**Amends:** [ADR-047](#adr-047-corpus-retrieval-is-postgresql-full-text-plus-pgvector-not-azure-ai-search)

### Context

ADR-047 chose PostgreSQL full-text plus `pgvector` for corpus retrieval, on the basis
that PostgreSQL already exists in the architecture and Azure Database for PostgreSQL
supports the extension. Slice 4.9 went to implement it and checked rather than assumed:

```
SELECT count(*) FROM pg_available_extensions WHERE name = 'vector';   -- 0
```

The `postgres:16-alpine` image this repository's `docker-compose.yml` runs does not ship
`pgvector`, so it cannot be installed, let alone enabled. On Azure Database for PostgreSQL
Flexible Server the extension is available but must be added to the
`azure.extensions` server parameter — **a change to the deployed server**, which this
campaign is not authorised to make and which would in any case have no effect until V3
is approved and deployed.

There is also no embedding provider. Every approved model account (Azure OpenAI,
DeepSeek) could produce embeddings, but nothing in this repository does, and turning one
on is a spend decision on a path nothing yet consumes.

### Decision

**The lexical leg is the production path and requires no extension.** It is a GIN
expression index over `to_tsvector('simple', text)` with every metadata filter applied in
the same SQL statement, which is the property ADR-047 actually wanted: an entity, period
and scope filter that applies in the same transaction as the data it filters.

**The semantic leg ships portable and OFF.** Embeddings are stored as JSONB alongside the
model name and dimension; similarity is computed by InvestingBuddy's own cosine over a
**bounded candidate pool**, not by a vector index. `V3_CORPUS_SEMANTIC_SEARCH_ENABLED`
defaults to `false` and the only embedding provider is deterministic and fake.

`pgvector` remains the intended production shape. Enabling it is a follow-on migration
that adds a `vector` column populated **from the JSONB already held** plus an IVFFlat
index — an additive change to data the platform already has, not a re-embedding and not a
rewrite.

### Consequences

- **Hybrid retrieval without a vector index is a semantic rerank of a bounded candidate
  pool, not an exhaustive nearest-neighbour search, and the backend says so.**
  `PostgresSearchBackend.semantic_is_exhaustive` is `False` and `capabilities` omits
  `SearchMode.SEMANTIC`. Reporting it as a full semantic search would be the kind of
  claim that survives until somebody measures recall.
- Lexical retrieval is independently usable and is what every V3 path uses today. ADR-047
  already required that hybrid degrade to lexical when the extension is absent; this
  makes "absent" the current state rather than a hypothetical.
- `'simple'` rather than `'english'` is the text-search configuration, for the reason
  `fusion.py` already gives: in a financial corpus the exact token usually deserves to
  win. `FY2025`, `PNDORA` and `mRNA-1273` survive `'simple'` intact, and a stemmed leg is
  an additional expression index rather than a replacement.
- No production embedding infrastructure is provisioned, and no new subscription is
  required — which is the constraint the whole 2026-09-05 resolution round turned on.

---

## ADR-054: Applicable Playbooks' Completion Rules Are Unioned, Not Intersected

**Date:** 2026-09-06 · **Status:** Accepted · **Decided by:** agent (technical, reversible)
**Clarifies:** [INDUSTRY_PLAYBOOK_ARCHITECTURE.md §6](v3/INDUSTRY_PLAYBOOK_ARCHITECTURE.md)

### Context

Multiple playbooks may apply to one company — a conglomerate is legitimately both
industrial and financial. §6 specifies the combination:

> Questions union; `completion_rules` intersect (the strictest wins), because the
> alternative is a conglomerate that is easier to declare complete than either of its
> parts.

The two halves of that sentence point in opposite directions. Intersecting the rule
**sets** keeps only the rules both playbooks share — which produces *fewer* rules, and
therefore a conglomerate that is **easier** to declare complete than either of its parts.
That is exactly the outcome the sentence's own justification says to avoid.

### Decision

**Every completion rule of every applicable playbook must hold.** `PlaybookSelection.completion_rules`
returns the de-duplicated **union** of the rules, and the loop requires all of them.

The intent — "the strictest wins" — governs over the stated operation. What is being
intersected is the set of *states that count as complete*: requiring more rules intersects
the satisfying states, and that intersection is produced by unioning the rules.

Questions are unioned as written, which was never ambiguous.

### Consequences

- A conglomerate is **harder** to declare complete than either of its parts, which is what
  §6 asked for and what makes multi-playbook coverage safe to enable.
- The failure direction is a run that keeps investigating and eventually stops on a budget
  limit, naming it. That is recoverable and visible. The alternative — a run declared
  complete because two playbooks happened to share no rules — is neither.
- A playbook may only declare a rule the loop can evaluate
  (`EVALUABLE_COMPLETION_RULES`), enforced in the constructor. The loop already treats an
  unrecognised rule as *not satisfied*, so an unevaluable rule would make every run of that
  playbook exhaust its budget — and the symptom would look like a coverage problem rather
  than a configuration error.

---

## ADR-055: DeepSeek's Web Search Is Real, Lives on `/responses`, and Every Bound That Matters Is Enforced by InvestingBuddy

**Date:** 2026-09-07 · **Status:** Accepted · **Decided by:** agent, from a live measurement
**Amends:** [ADR-048](#adr-048-deepseek-web-search-is-the-primary-external-search-path-exa-and-perplexity-are-deferred)
**Corrects:** the conclusion recorded in slice V3.11.1.1

### Context

ADR-048 made DeepSeek's server-side `web_search` the primary external general-web path,
on the strength of vendor documentation. When a real key arrived, V3.11.1.1 probed the
API and reported that **no server-side web search exists**: every builtin tool spelling
returned `400 unknown variant ... expected 'function'`, and the model itself said it had
no live browsing and a June 2024 cutoff. The adapter's `search()` was changed to refuse,
and the campaign recorded that the premise for choosing DeepSeek had been wrong.

That probe only ever asked `POST /chat/completions`.

DeepSeek serves a second, OpenAI-compatible API — `POST /responses` — and the builtin
web-search tool lives there. Measured live on 2026-09-07 with the same key: the tool is
accepted, echoed, and really searches. It issues queries, opens pages, reads them with
`find_in_page`, and returns the URLs it opened. The capability ADR-048 was chosen for
exists.

**The generalisable error is not about DeepSeek: an absence measured on one endpoint was
reported as an absence in the product.**

### Decision

**1. `search()` uses `POST /responses`; `complete()` stays on `/chat/completions`.** Two
endpoints with different capabilities and different strictness, so two request shapes.
Forcing one abstraction over them would hide exactly the distinction that caused the
error.

**2. A 200 is not a capability, and the `tools` echo is the proof.** Unlike
`/chat/completions`, `/responses` silently drops what it does not understand and still
returns 200 — an unknown tool type, an unknown `include` value, an unknown field inside a
known tool. So the adapter verifies that the tool it asked for is echoed back **and** that
`web_search_call` items are present. Without that check, "the tool never ran" is
indistinguishable from "the web has nothing", and only one of those is a claim about the
world.

**3. Candidates come from the retrieval trace and from nowhere else.** The contract
provides **no structured citations** — `annotations` was empty in every measured run, and
`include: ["web_search_call.action.sources"]` is accepted and inert. What it does provide
is a trace: the queries issued, the pages opened, the opens that failed. A page the
provider opened is a page it read, so those URLs become `SourceCandidate`s. A URL that
appears only in the answer's prose is **counted and not promoted** — recorded as
`cited_but_never_opened`, which is a fabrication signal that only exists because the trace
exists.

**4. Every bound that matters is enforced client-side, because the vendor's are inert.**
Measured: `max_tool_calls` is echoed back as `null` and a request sending `1` still made
two search calls (one observed request made **eight**, spending ~41k tokens);
`filters.allowed_domains` is dropped from the echo entirely and off-domain pages were
opened anyway. So InvestingBuddy owns the domain restriction, the candidate ceiling, the
de-duplication, the URL canonicalisation and the SSRF host check, and the telemetry
records `domain_filter_enforced_by: investingbuddy_client_side` so no later reader infers
a guarantee the provider does not give. The only bounds the API honours —
`max_output_tokens` and the timeout — are both set.

**5. The canonical model is a *served* name.** `deepseek-chat`, taken from documentation,
is not served: both endpoints accept it with a 200 and answer as `deepseek-v4-flash`. A
default that is silently substituted makes every cost attribution wrong and every
benchmark unreproducible, without anything ever failing. The default is now
`deepseek-v4-flash`, and a test asserts the configured model is in `SERVED_MODELS`.

**6. Both flags stay off, and both are enforced in code.** `V3_DEEPSEEK_SEARCH_ENABLED`
and `V3_DEEPSEEK_MODEL_ENABLED` default to `false`. A verified capability is not a
decision to spend on it, exactly as a credential is not consent to route research to a
vendor.

This needed doing, not just saying. Until this ADR, `search()` raised unconditionally and
**that refusal — not the flag — was the thing keeping spend off**; review of the first
draft found `V3_DEEPSEEK_SEARCH_ENABLED` had no consumer in application code at all while
three documents claimed it held the line. The gate now lives in
`DeepSeekSearchProvider.search()`, alongside the model leg's in `resolve_routing`, and a
test asserts the default provider never reaches the transport.

### Consequences

- **ADR-048 stands, and no search provider needs buying.** Exa, Perplexity and Gemini stay
  deferred and unpurchased. [OPEN DECISION #3](v3/OPEN_DECISIONS.md) resolves toward
  DeepSeek server-side web search on measured evidence rather than on documentation.
- **`ResearchLead` → Evidence becomes *staffable*, and is still not staffed.** The reason
  V3.11.1.1 gave for it being permanently unstaffable — DeepSeek cannot retrieve — is
  gone. The wiring is not there, and this ADR does not add it:
  `DeepSeekResearchProvider.investigate()` still runs on `/chat/completions` with no
  retrieval, so its leads cite URLs the model recalled; `DeepSeekSearchProvider` emits
  `SourceCandidate`s, which carry a URL and no claim, and `verify_lead()` verifies a
  claim. Giving `investigate()` the `/responses` search tool would close it and is a
  separate slice. Nothing about the promotion rule changes either way: only bytes fetched
  through the platform's own guarded fetcher can be cited.
- **The search leg now reports tokens.** `/responses` bills a search like a completion, so
  `SEARCH_UNITS` gained the three token units. Reporting only `web_search_calls` made the
  platform's most expensive single call look like its cheapest.
- **A provider that ignores our restrictions is visible rather than silent.**
  `off_domain_urls` and `cited_but_never_opened` are per-run numbers somebody can watch.
- The refusal V3.11.1.1 added was the right decision *on the evidence it had*. What was
  wrong was the scope of the evidence, and the live contract test now pins both halves —
  that `/chat/completions` has no builtin tools, and that `/responses` does.

---

## ADR-056: External Research Enters Through Two Tools, and Only Our Own Fetch May Mint Evidence

**Date:** 2026-09-07 · **Status:** Accepted · **Decided by:** agent, under a user brief
**Implements:** [ADR-044](#adr-044-external-research-output-enters-as-a-lead-never-as-evidence) and [ADR-048](#adr-048-deepseek-web-search-is-the-primary-external-search-path-exa-and-perplexity-are-deferred)
**Closes:** the V3.11 release-candidate caveat in §11.6

### Context

ADR-044 established that external research output enters as a `ResearchLead` and never as
evidence. ADR-048 made DeepSeek the external search path. V3.11.1.2 verified that DeepSeek
really retrieves. And still nothing used it: `investigate()` ran on `/chat/completions`,
which has no tools, so the only producer of leads produced URLs the model had *recalled*.
The V3.11 release candidate said so plainly — zero lead rows across every real run.

The two tool names this needs, `search_web` and `fetch_public_source`, had been in the
closed vocabulary since V3.3 with a comment saying they were waiting for a provider
runtime.

### Decision

**1. Two tools, not one, because they answer to different authorities.** `search_web`
reaches a vendor and everything it returns is a claim. `fetch_public_source` reaches the
open web *through InvestingBuddy's own fetcher* and puts one claim through `verify_lead`.

**2. Only `fetch_public_source` may mint an evidence id, and only for `LEAD_VERIFIED`.**
That status cannot be constructed without a content hash, so "verified" cannot exist
without bytes the platform fetched. `search_web`'s payload carries none of the four keys
the Investigator reads as citations, so a model *cannot* cite a search result — the
guarantee is the absence of a field, not a rule somebody remembers.

**3. The Investigator chains the two deterministically.** An LLM choosing which URL to
fetch is an LLM choosing what the platform reads, and the rest of the Investigator already
refuses to let a model choose tool arguments.

**4. The retrieval trace annotates; it does not gate.** A live run proved the alternative
wrong: the provider found the right SEC exhibit through a *search result*, whose URLs the
contract never exposes, and a guard that dropped claims citing unopened pages discarded
the only good lead of the run. `opened_urls` is a subset of what the provider legitimately
saw. The gate stays where it belongs — our own fetch.

**5. The tools are registered conditionally, and that is the whole feature flag.** With
`V3_DEEPSEEK_SEARCH_ENABLED` off they are absent from the registry, so
`implemented_tools()` excludes them, the external question is never planned and the role
is never seated. A question needing an absent tool is unassignable *at plan time* — a
coverage fact a reader can see, rather than a refusal mid-run.

**6. One role reaches outside the platform, and it is not `always_present`.**
`external_research_analyst` is the only role holding the external tools, so
`reaches_outside_the_platform` remains a checkable property of the policy table rather
than a claim.

### Consequences

- **The `ResearchLead` → Evidence path is staffed**, demonstrated on real data: a real
  question, a real DeepSeek search, a real SEC exhibit, our own fetch, and findings in the
  ledger citing `ev:x:…` ids derived from the hash of bytes we retrieved.
- **Promotion is provider-dependent and that is now measured**, not assumed: across
  sixteen live runs, ten promoted evidence and six promoted none — five of the last six
  on the shipped prompt — because the URL
  the provider chose was unreachable, unreadable, or a filing index page carrying no
  figures. Every non-promotion is the system working, and roughly a coin-flip per run is
  the honest rate to plan against.
- **Two pre-existing defects in the verification gate were found by pointing it at a real
  adversarial claim** — a fabricated value that matched under a relative tolerance, and a
  period conflict that was skipped rather than failed. Both are fixed generically, in
  `leads.py`, for every provider and not only this one.
- **Scope remains unchecked on this path.** External evidence carries `scope_key = None`
  and says so. That is a real limitation and it is recorded rather than closed.
- `cost_per_verified_useful_finding` is now computable for a real path, and is reported as
  `None` until an operator supplies a price. An unknown cost is never labelled zero.

## ADR-057: A Verified Official Issuer Source May Establish Discovery Theme Relevance — It Is Not Corroboration

**Date:** 2026-10-07 · **Status:** Accepted · **Decided by:** owner (explicit approval) · implemented by agent
**Amends:** open-web spec §6.2 rule A3 · **Preserves:** ADR-044, the fabricated-company safeguards (A1, A2)

### Context

The first live critical-minerals Discovery (five runs, five niches) admitted **no** company through
web search. Search found real long-tail listed companies (Anson Resources, Ioneer, Lynas, Arafura,
Iluka, Hastings, IGO, Mineral Resources), every one passing A1 (executed search + fetched page)
and A2 (official exchange directory), and every one stopped at A3: the only pages that tied them
to the thesis were their own websites, classed `unknown_web` because no issuer domain was verified.
A3 demanded a third-party source, which conflates two different questions.

### Decision

**1. Separate the questions.** *Theme evidence*: does fetched evidence show the company
takes part in the thesis? *Independent corroboration*: does evidence independent of the issuer
support a material claim? A page the issuer wrote answers the first, and never the second.
Each admission decision records `theme_evidence.status` (`verified_issuer` · `independent` ·
`multiple` · `insufficient`) and `corroboration` (`issuer_only` · `independently_corroborated` ·
`unavailable`), and the UI states "the company's own statement … not independently corroborated".

**2. A verified official issuer domain may carry A3.** It must be *independently established*,
by (a) the verified issuer registry, (b) a website the exchange or regulator itself publishes,
or (c) the letterhead of a document the exchange published under the issuer's ticker.
A domain is **never** trusted because its hostname resembles the company's name, the search
provider labels it official, a model says so, or the page claims it. A venue with no such source
yields none, so an issuer-only passage cannot carry A3 there and the company stays "also surfaced".

**3. Entity safety is unchanged.** No official domain is established for a listing that A2 has
not verified; a model-named company still needs an executed search and a fetched page; a snippet
is never evidence.

**4. Fit and evidence rank separately.** `fit_score` (thesis, research relevance, catalysts) orders
candidates in 5-point bands; `evidence_confidence` is only a tiebreaker. Evidence availability
must not outrank thesis relevance.

**5. Attribution.** Issuer-origin excerpts are labelled "Company statement", and the Council is
told to attribute them and never restate a market-size, share, "largest", "lowest-cost", superiority
or forecast claim from them as established. Financial-statement facts stay primary/regulated.

### Consequences

- Official domains exist today for ASX (profile or announcement letterhead), US issuers (SEC
  submissions, when populated) and the registry. LSE/AIM, Euronext, SIX and TSX have none yet;
  their search-found companies remain "also surfaced". Adding a source is additive.
- A governed, auditable relationship: every established domain stores its basis and source URL.

### Review corrections (independent security review, before merge)

The first implementation was reviewed and corrected before merge: (1) holder, takeover, bidder and
director-form announcements lodged under a ticker are never read for a domain, and a domain needs two
announcements or one that names the ticker itself; (2) registrable domains follow the public suffix
list, so a tenant of a shared host (`myshopify.com`, `herokuapp.com`, `blogspot.com`…) is never "the
issuer's" and a bare public suffix is no domain; (3) a bare domain in a letterhead must be lower-case
with a label of at least three characters; (4) the PDF parse is bounded in time and concurrency; (5) an
empty lookup is never cached and the cache is per day; (6) the strict name guard runs before any lookup;
(7) the Council pack carries `issuer_origin` and `corroboration`, and a company's own pages count as ONE
origin and never an authoritative one; (8) the absence-contradiction check compares the year and ignores
guidance, forecast and "did not decline" clauses.


## ADR-058: Generic Official-Domain Resolution and Independent-Source (Path B) Discovery Admission

**Date:** 2026-10-26 · **Status:** Accepted · **Decided by:** owner (direction) · implemented by agent
**Extends:** ADR-057 · **Preserves:** A1 (executed search), A2 (official listing), A4, the fabricated-company safeguards, `issuer_only` vs `independently_corroborated`

### Context
Live acceptance after ADR-057 admitted one search-found company in eight Discovery runs. The cause was
infrastructure, not policy: the ASX research feed shows only the latest five announcements (mostly holder
notices) and an empty profile website for most small caps; SEC submissions often carry no website; TSX/LSE had no
domain source; and independent journalism rarely prints a ticker next to a name.

### Decision
1. **Two admission paths for theme evidence.** Path A — a verified *issuer* source (domain proven independently,
   page fetched, passage ties issuer to theme). Path B — an *independent* source of an acceptable class, fetched,
   that unambiguously identifies the verified listed company; **no official domain required**.
2. **One generic resolver** (`discovery/domain_proofs.py`, `official_domains.py`). A domain is a provenance-carrying
   fact (`DomainProof`: method, source class, document id, matched entity, matched identifiers, confidence,
   `verified_at`). Methods: `directory_record`; `regulated_document_stated_website` (ASX / SEC / FCA NSM bytes that
   name the issuer, in its OWN contact block — never the share registry's, auditor's, broker's); `regulated_document_letterhead`;
   `filing_contact_email`; `legal_identifier_site_match` (a candidate site prints the verified entity's legal name AND a
   checksum-valid LEI / ISIN / ABN / ACN / company number from GLEIF or the exchange). Ticker alone, name alone,
   hostname resemblance and search-provider labels never qualify.
   **Independent review (2026-10-26) tightened this:** identifiers are public, so a lookalike or aggregator can print
   them. `legal_identifier_site_match` therefore yields status `candidate_self_claim` — recorded for Admin diagnostics
   (`domain_candidates`) but never read as the issuer's voice and never an official domain. A single stated website in
   one exchange announcement is `stated_unconfirmed` (verified only from a regulator-lodged filing, a second document,
   a matching contact e-mail domain, or the entity's own identifier). IR/PR/media-contact blocks are third-party anchors.
3. **ASX:** a bounded pass (≤4 documents) over the issuer's high-value announcements (annual report, presentations,
   quarterly reports) found on the ASX's own yearly announcement pages; **SEC:** the registrant's latest 10-K/20-F/8-K
   text; **UK:** FCA NSM filings located by the GLEIF LEI of the exchange ISIN. **TSX/TSXV:** the TMX directory anchors
   identity only; no authoritative filing source exists in the current architecture, so these rely on Path B or rule E.
4. **Path B entity matching** (`discovery/entity_match.py`): the full legal-name phrase (with a distinctive token) matches
   on its own (geographic words are not distinctive); short forms and generic-only names need a STRONG anchor within 80
   characters (the ticker immediately preceded by its own venue's tag, or an ISIN/LEI/CIK/number on a token boundary —
   "ASX-listed" alone is not an anchor); a project name (matched as the full phrase "<Project> Project"; bare geographic tokens never) supports a company only when an authoritative or issuer document proved the
   company↔project relationship (recorded with its provenance). A paragraph annotating ≥3 listings or naming ≥3 verified
   companies is a list and admits nobody.
5. **Unchanged:** issuer claims stay `issuer_only`; Path B passages are independent *thematic* evidence about
   candidate discovery, not canonical financial facts (those remain primary/regulated only); superlatives still need
   independent support; search snippets are never evidence.

### Consequences
Domain resolution costs a few bounded fetches per qualifying lead and is cached per day; a lead that qualified but was
not looked up records why. Remaining limits are documented in `docs/open-web-research-production-acceptance.md`.

---

## ADR-059: Path C — Regulated-Document Discovery Admission; Source-Access Findings (SEDAR+, LSE)

**Date:** 2026-10-28 · **Status:** Accepted (implementation) · open owner decisions listed below
**Extends:** ADR-057, ADR-058 · **Preserves:** every trust rule; no browser automation; no paid provider

### Context
After ADR-058 the binding limit was issuer-website availability: many verified issuers resolve to a domain that
refuses automated reads (robots / 403 / captcha / JS), and a blocked site ended the lead.

### Decision
1. **Path C.** A search-found candidate (A1: an executed search) whose listing is verified (A2) is admitted on theme
   evidence from a **regulated document fetched directly from the regulator / exchange**: SEC filing under the CIK,
   ASX announcement, FCA NSM filing (by GLEIF LEI). The document must name the verified issuer
   (`names_issuer`); its sentence-bounded window must tie the company to a thesis term (a distinctive name token, the
   ticker, or the company speaking in the first person). No issuer website is required.
2. **Truthful semantics.** Path C text is `issuer_regulated_disclosure`: `theme_evidence.status = verified_issuer`,
   `corroboration = issuer_only`, with a `regulated_passages` count. It is never called independent merely because the
   venue is an exchange. Financial-statement facts stay primary-source canonical; Path C is admission evidence only.
3. **Order and fallback.** official page (Path A) → regulated documents (Path C) → run passages + a bounded
   candidate-specific query family (Path B). A lead is rejected only when none establishes thematic relevance. A
   blocked issuer website is no longer a terminal state.
4. **Reuse, not a second stack.** Path C and the domain proofs share one cached document collector
   (`official_domains._collect_docs`); a document is fetched and parsed once per day. Discovery holds the document's
   id, URL and content hash with each passage; nothing is written to the corpus at Discovery time, so no duplicate
   copies exist, and `ensure_disclosure_evidence` (content-hash deduplicated) ingests the same documents when company
   research starts.
5. **Path B search quality.** At most 3 queries per needy lead (≤ 6 leads), built from the VERIFIED legal name:
   `"<name>" <theme>`, `"<name>" project permit funding`, `"<name>" government customer contract`. Origin
   `candidate_evidence`; kill switch `V3_DISCOVERY_CANDIDATE_EVIDENCE_ENABLED`. Snippets remain non-evidence.
   Domain-prioritised search (include-domains) is not implemented: the provider adapter has no such parameter.
6. **Funnel.** `web.funnel` on every run records search results → entity candidates → listing-verified → regulated
   documents → Path C / official domains / Path A / Path B qualified → admitted, with `admitted_by_route`.
7. **Independent-review M1–M5** (ADR-058 review) are dispositioned in `docs/open-web-research-production-acceptance.md` §10.
8. **Worker.** `PendingRollbackError` (an invalidated session) is transient: a retry builds a fresh session.

### Source-access findings (investigated 2026-10-28; owner decisions)
* **SEDAR+ (Canada): no connector built.** `sedarplus.ca` sits behind a Radware bot manager that serves a captcha to
  non-browser clients; no documented public API or bulk file was found. Defeating the challenge or driving a browser
  is outside the stated constraints. The CSA reporting-issuer lists are behind a JavaScript challenge. TSX/TSXV
  identity is the existing TMX directory (+ GLEIF legal name → LEI + corporation number). Primary documents exist only
  for SEC-registered Canadian filers (40-F / 6-K / 10-K via EDGAR). **Owner decision needed:** written CSA permission
  or a licensed feed, or approval of browser automation.
* **LSE / AIM: no RNS or website connector built.** The LSE disclaimer (§8) forbids "any programmatic, scripted or
  other mechanical means to access this Website or the Information". The company-page values load from origin-gated
  APIs. The existing `_lse_listing` identity call (`api.londonstockexchange.com`) returned 403 from a development
  machine and should be re-checked against the same terms. AIM Rule 11/26 announcements are generally not in the FCA
  NSM. **Owner decision needed:** an LSEG licence/API agreement.
* Both venues are therefore recorded as `no_regulated_source_for_venue`, never guessed or approximated.


## ADR-060: Discovery Source Provenance and One Count Model

**Date:** 2026-10-30 · **Status:** Accepted (implementation)
**Trigger:** owner test of production run `7cce7dcd` ("European biotechnology companies")

### Context
The run's Tavily search executed (20 queries, 52 results, 10 companies named), but the page said "Open company
discovery was unavailable", the limitation said "no external research provider is configured and enabled", the
header said "0 of 0 screened" beside "12 companies considered", and the nine unverified names sat under collapsed
toggles. Cause: `StageResult.external_discovery` was set from `discover_leads` alone — the **model-recall** source,
gated by `V3_DEEPSEEK_SEARCH_ENABLED` (off since 2026-10-25). The web-search stage is a separate source and never fed
that field. The header counted the per-company scan of the shortlist (`processed_count of universe_count`), which is
0/0 whenever nothing is returned. API and the durable worker share one in-process `Settings` object; configuration
was not stale.

### Decision
1. `external_discovery` is the union: `available` when model recall **or** a live web search (`ok` / degraded) ran.
2. Web-search runs store `lead_sources` (web search: state, provider, executed queries, results seen, companies
   named; model recall: ran/unavailable; screening investigation: ran/not_needed/unavailable) and an `origin`
   (`web_search` | `model_recall` | `curated_registry` | `platform_registry`) on every rejected lead. The page never
   labels a model suggestion as a web-search find. Flag-off (V3.19) payloads are byte-identical.
3. One count model, in funnel order: considered → listing verified → checked against the requirements
   (`checked_against_requirements`) → met every requirement → returned. The header leads with the funnel and shows
   the shortlist scan only when a shortlist exists.
4. Stored runs keep their warning text; the read-time grouper relabels the old sentence as `MODEL_RECALL_OFF`
   (informational). "Original wording" is shown only when a sample differs from the grouped message.
5. The pre-run line names the sources that will run (`open_discovery_sources`, from live settings), not only the
   `V3_DYNAMIC_DISCOVERY_ENABLED` flag.
6. When a run returns nothing, the excluded and not-verified lists open by default; each row shows name, listing
   (ticker · venue) when known, origin and a plain-language reason.

### Not changed
Admission rules, verification, screening and the thesis itself. With model recall off there is still no screening
investigation; the run says so instead of implying it ran.

---

## ADR-061: One Issuer, Several Listed Securities (Dual / Multi-Venue Listings)

**Date:** 2026-10-30 · **Status:** Accepted (implementation)
**Trigger:** owner rerun `6f8861c5` ("European biotechnology companies"): Cellectis
(Euronext Growth `ALCLS`, Nasdaq ADS `CLLS`) was rejected as "SEC company tickers and exchanges
lists CLLS on US as 'Cellectis S.A.', not 'ALCLS'".

### Context
The press release Tavily returned prints `Cellectis (the "Company") (Euronext Growth: ALCLS –
NASDAQ: CLLS)`. The mention extractor read each `VENUE: TICKER` match on its own. The name before
the first match was lost behind the `(the "Company")` alias, and the second match took the
first ticker as its company name. That produced ONE lead: name "ALCLS", ticker CLLS, venue
NASDAQ. Identity verification knew one ticker per lead and checked it on that venue's list. The
SEC row's name disagreed, so the lead was rejected. The model had no notion of an issuer
separate from a listing, so "a European company whose ADS trades in New York" could only be one
of the two. The geography constraint read the listing venue's country.

### Decision
1. **Issuer ≠ security.**
   - `IdentityOutcome` gains `issuer`: legal name, domicile country and its basis and source,
     the regulator-recorded HQ country, and how the lines were linked.
   - It also gains `securities`: ticker, exchange, MIC, ISIN, currency, listing country,
     verified/not_verified, role primary/secondary/unlinked, form
     ordinary/foreign_listing/unknown, and surfaced.
   - `issuer` is present whenever a domicile is known. `securities` is present only when more
     than one line is known. A listing with no issuer evidence keeps the V3.19 record shape.
   - `CompanyLead.securities` carries every listing a source printed. Tickers are never
     collapsed; a security's key is `(venue, ticker)`.
2. **Venue-aware verification.**
   - Each security is verified on its own venue's directory. `ALCLS` on Euronext Growth is
     checked on the Euronext list, `CLLS` on Nasdaq on the SEC list.
   - One failing line never rejects its siblings.
3. **Linking needs strong evidence.**
   - Co-printed lines are one issuer when all three hold: one source printed them as one
     company's listings, the exchanges' names agree, and no line's own authoritative record
     states a different domicile.
   - A lone foreign line (a Nasdaq ADS) gains its home listing only when all three hold:
     - the SEC registrant record places the issuer in a non-US country;
     - that country's exchange lists the **exact** same name;
     - that row's ISIN, where printed, is from that country.
   - A name alone never links. Two leads with the same name on different listings are both
     verified; a namesake is never dropped as a duplicate before verification.
4. **Where the issuer is.**
   - Domicile, in order:
     - the ISIN prefix of a verified line;
     - the SEC registrant's country of incorporation;
     - the SEC registrant's business address.
   - A US-prefixed ISIN on a non-US venue is a depositary receipt's and is ignored.
   - EDGAR's incorporation field is not always right (Shell plc reads `DC`). A US state code
     therefore counts only when the business address is not a foreign location.
   - EDGAR country texts are mapped to the registry's names: "Canada (Federal Level)" and
     "Ontario, Canada" become Canada, "Korea, Republic of" becomes South Korea.
   - The business address is also kept as `hq_country`: NIO is incorporated in the Cayman
     Islands and run from China.
5. **Geography: where the company is, and where it trades.**
   - The issuer's place is its HQ (a screened HQ, else the SEC business address), else its
     legal domicile. An offshore holding jurisdiction with no region (Jersey, Cayman,
     Bermuda) places a company nowhere.
   - A requested geography matches the place **or any verified listing of the issuer**.
     Cellectis is "European" through Paris and "US-listed" through its Nasdaq line, even
     though its home line is primary.
   - An excluded geography is judged on the place when one is known: "non-US" names the
     company, so a Dutch issuer with a US line passes. A Jersey-incorporated London company is
     judged on its listing.
   - With no place known (an offshore domicile, or the SEC record unreadable), an exclusion
     is judged on the primary line only, as before. An outage never turns a sibling US line
     into "non-US: excluded".
   - A UK plc run from New York (Royalty Pharma) is placed in the US: not "European".
   - The shortlist `country`/`region` follow the domicile. The venue's country stays in
     `identity.listing_country`. The HQ is cited to its own regulator record (`hq_source`).
6. **Primary listing.**
   - Discovery uses `primary="home"`: the home-country line leads.
   - A venue a caller chose wins (`primary="given"`), and no home-line scan is spent. The peer
     tool uses it, because a peer named on Nasdaq is the line whose SEC financials are
     comparable.
   - Regulated-document reading (Path C) and the SEC official-domain lookup use a verified
     linked US/ASX/LSE sibling line when the primary's venue has no regulated source.
7. **Extraction.**
   - Consecutive `VENUE: TICKER` matches are one company's listings only when all three hold:
     - both sit inside parentheses (`(Euronext Growth: ALCLS – NASDAQ: CLLS)`,
       `(LSE: PRE) (ASX: PM8)`);
     - only a separator lies between them: a dash, slash, comma, semicolon, "and" (and its
       de/fr/es/it/pt/nordic forms) or a parenthesis;
     - their venues differ.
   - A bare `NASDAQ: FOO, TSX: BAR` run is a list of companies.
   - The name before a group skips up to two alias parentheticals.
8. **Dedup.** Two leads that resolve to one issuer are one candidate. The later one is recorded
   as a duplicate, "the same issuer's other listing", before any official-page,
   regulated-document or candidate-search budget is spent. One issuer means:
   - the two leads share a linked line; or
   - where no directory links the venues (LSE, Xetra, Tokyo), the exact same normalised
     directory/registry name (never a page's or a model's wording, and only against a
     verified identity)
     plus one side's regulator- or ISIN-stated place equal to the other's domicile, or to its
     listing country when it has none. A held `SHEL` on LSE and a searched `SHEL` on NYSE are
     one company; namesakes from two countries stay apart.
9. Dotted legal forms (`S.A.`, `N.V.`, `S.p.A.`) normalise as one word, so `Cellectis S.A.`
   matches the exchange's `CELLECTIS`.

### Cost
- One data.sec.gov submissions fetch per verified SEC registrant, cached per day.
- Home-line scans reuse the cached exchange directories.

### Not changed / known limits
- No issuer name, alias or ticker map is added.
- LEI/GLEIF is not consulted.
- Some issuers' home exchange is not in their domicile country (argenx: a Dutch SE on Euronext
  Brussels). They gain no home sibling unless a source prints both lines.
- UK registrants gain no home line, because the LSE lookup needs a ticker.
- Only SEC registrants have a regulator-recorded domicile.
- ISINs are printed by the Euronext list only. On other venues, the name and the registrant
  domicile carry the link.

---

## ADR-062: SEC XBRL — the Restated Prior Year, the Latest Quarter, Segment Profit and a Change of Basis

**Date:** 2026-11-01 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11. The live Honeywell deep report `e50cb267` was
generated after the Aerospace spin-off and the 1-for-2 share consolidation (both 2026-06-29).
It had four problems:
- It said revenue "fell 2.7%" (it grew 7.9%).
- It printed a 21.7% "group operating margin".
- It showed pre-consolidation EPS beside a post-consolidation price.
- It said the latest quarter was "not yet extracted".

### Context
1. **Prior year.** The normalizer picked the prior fiscal year by `fy == latest_fy - 1`. But
   XBRL `fy` is the fiscal year of the FILING, not of the period. The FY2024 comparative restated
   inside the FY2025 10-K carries `fy=2025`, so it was skipped, and the FY2024 value as
   originally filed was used: 38,498m before the Solstice spin-off, against 34,717m restated.
2. **Latest quarter.** US reports read only the annual bundle. A 10-Q filed after the 10-K was
   never turned into current-period facts, so `revenue_current_period` stayed empty for every US
   filer.
3. **Measure.** Honeywell presents no operating-income line. It tags its TOTAL SEGMENT PROFIT
   as `OperatingIncomeLoss` (FY2025 8,127m). Revenue less its own cost of sales, SG&A and R&D
   was 6,567m.
4. **Basis.** The Q3 2026 10-Q restates Q3 2025 revenue from 9,437m (as carried by the 10-K)
   to 4,929m, and Q3 2025 diluted EPS from 2.86 to 5.71. The annual figures shown predated both
   changes.

### Decision
1. **Prior year = the period ending 330–400 days before the latest period.** Among candidates
   for that period, prefer the same accession (the restated comparative), then the latest filed.
   A quarter is never a prior year.
2. **Latest quarter.** `latest_quarter_facts()` reads the newest 10-Q, only when it was filed
   after the annual report. It reads only that filing's own quarter:
   - flows of 80–100 days (never year to date, never comparatives) and instants;
   - fields: revenue, operating profit, net income, cash, total assets and equity.
   Each fact is labelled `Q3 2026`, scope `Group`, tier `T2_regulator_or_gov`, and cited to the
   EDGAR archive index of that accession. The facts travel as
   `fundamentals_summary.current_period_facts` and fill the `<field>_current_period` snapshot
   slots beside the issuer-document facts. They also reach the peers statements tool under
   their own ids: `secfin:<T>:<year>-Qn:<metric>`, metric `<metric>_latest_quarter`, so they
   never overwrite an annual line.
3. **Segment profit is not operating income.** `operating_income_excess()` computes a bound:
   revenue minus the same filing's cost of revenue, SG&A (or S&M + G&A) and R&D. Leaving out any
   other expense only lowers true operating income further. A tagged operating income above
   that bound by more than 3% of revenue AND 10% of itself is withheld with the reason, and so
   is its margin. Nothing is concluded when cost of revenue or SG&A is not tagged.
4. **Change of basis.** `basis_changes_since_annual()` compares the newest post-annual 10-Q's
   comparative quarter with the same period as filed by the annual-report date:
   - revenue moved more than 5% → `perimeter_recast`;
   - EPS moved by a near-integer ratio k (2% tolerance) while net income held within 1% →
     `share_consolidation` (1-for-k) or `share_split`.
   On a share-count change, annual EPS is withheld (`predates_share_count_change`). Each change
   carries a sentence. The report's Key Financials prints it as a `basis_change_note`, and the
   peers tool prefixes it to the gaps.
5. The report header shows council completeness honestly: "6 of 7 agents completed · 1 failed",
   not "7 agents".

### Verified
Recomputed against 19 live filers (AAPL ABT ADBE AMD AMZN CAT DIS DUK GE GM HON IBM JPM MMM
MRNA MSFT PYPL UPS WMT):
- HON: YoY +7.85%; operating income withheld; EPS withheld; recast and 1-for-2 consolidation
  detected; Q3 2026 facts present.
- No new withholding or basis change for the other 18. GE's operating income was already
  withheld by the stale-period rule.

### Not changed / known limits
- No issuer is special-cased.
- Banks have no cost-of-revenue tag, so the measure check never runs for them.
- Only revenue and EPS comparatives are compared for basis changes. A recast that leaves
  revenue within 5% goes unnoticed.
- The annual bundle itself is not re-based. The note tells the reader that it predates the
  change.
- An 8-K earnings release (filed before the 10-Q) is not read as a current-period source.

### Review round 1
An independent review found false basis changes and a stale-quarter path. Fixes:
- **The baseline is the annual report's own filing.** Before, a comparative was checked
  against "the latest value filed by the annual-report date", which could be an earlier
  10-Q. A split made before the 10-K (and adjusted in it), or a disposal already recast in
  it, would then read as a change since the 10-K. Now only the 10-K's own tagging of the
  period counts (`_as_filed_in`). When the 10-K does not tag that quarter, nothing is
  concluded. Honeywell's 10-K tags Q3 2025 (revenue 9,437m, EPS 2.86), so it is still caught.
- **Cover-page share counts.** A second, EPS-independent signal compares
  dei `EntityCommonStockSharesOutstanding` on the 10-K cover with the newest 10-Q cover
  (summed per filing across class rows, 4% tolerance for buybacks):
  Honeywell 635.7m → 317.4m = 1-for-2. It is used only when the EPS route found nothing.
- **The latest quarter must be after the annual period.** A 10-Q/A for an old quarter
  filed after the 10-K is no longer "the latest quarter" or a restating filing.
- **One net-income concept per comparison.** "Net income unchanged" compares the same
  concept on both sides.
- **The operating-income reason no longer over-claims.** It now reads "not group operating
  income as reported — possibly segment profit or a non-GAAP measure (or R&D is already inside
  cost of revenue)". A filer that tags R&D inside cost of revenue loses its operating income
  to withholding (an omission, never a misstatement). Companyfacts carries no presentation
  linkbase to tell the two apart.
- An accession that is not `\d{10}-\d{2}-\d{6}` never becomes part of a URL.
- The peers tool's latest-quarter items carry `T2_regulator_or_gov`, the same tier as the
  quarter fact dicts. Annual statement items stay `T1_primary_filing`.
Re-verified on the 19 cached filers: HON alone shows a recast and a 1-for-2 consolidation,
and no other filer gains a withholding or a basis change.

### Review round 2
- **A cover-page share count is never a split on its own.** An issuance, an ATM programme
  or an all-stock merger moves the count too. A doubled MRNA cover was reported as "a
  2-for-1 split" and its EPS withheld; with ±4% bands, any rise above about 12.5x was always
  "a split". The cover pages now only CORROBORATE an EPS restatement: when the 10-K does not
  tag the quarter, last year's 10-Q is the EPS baseline, and only if the cover count moved
  by the same whole ratio since the annual report. A split made before the 10-K leaves the
  covers equal, so it is never reported.
- **The checks run without the 10-K's quarterly tags.** Since Item 302(a) was dropped in
  2021, 18 of the 19 cached filers tag no quarter in their 10-K, so the round-1 baseline
  almost never existed. Revenue now falls back to last year's 10-Q when the annual report
  restated nothing of its prior year (its prior-year comparative equals the previous 10-K
  within 1%). The fallback is usable for 18 of 19 filers, and none is falsely flagged.
- **The newest 10-Q is chosen by period end, then filing date.** A Q1 10-Q/A filed after
  the Q2 10-Q is no longer "the latest quarter", and an old-quarter amendment no longer
  hides the real latest one.
- The accession regex is ASCII-only, and the CIK must be ASCII digits.
- **EPS ratios allow for cent rounding.** A replay of GE's 1-for-8 reverse split (2021)
  was missed: −0.14 → −1.09 is 7.79x, 2.7% off 8. The check now accepts half a cent of
  rounding on each side, but only for a move of at least 1.5x and never across a sign
  change.

---

## ADR-063: Discovery Intent Follows the Thesis Text; a Security Code Is Never a Name

**Date:** 2026-11-01 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11. Five owner theses were run against production,
and the rerun `d15a74c4` was replayed.

### Context
1. The materials thesis read: "Listed smaller companies supplying critical or specialty
   materials required by semiconductors, electrification, defence or advanced manufacturing…".
   Only a contiguous "critical materials" was recognised, so the thesis became a DEFENCE thesis
   with a hard `industry: defense` constraint.
2. Selector auto-fill took a theme's FIRST sector or industry, not what the text said.
   "Payments or fintech" filled "Banks", and "electrical equipment … grid" was paired with
   "Utilities". The owner workbench sends the auto-filled sector as an explicit filter.
3. A German WKN printed beside an issuer (`BB Biotech AG WKN A0NFN3 (SIX: BION)`) was taken as
   the issuer name, and the real SIX listing was rejected as a name mismatch.

### Decision
1. A material class may be named with coordinated adjectives: critical, strategic, specialty,
   advanced, engineered or rare, joined by "or", "and", "/" or ",". It makes the thesis a
   materials thesis. The applications it names stay end markets, never the industry, unless a
   company-kind theme is also present: then materials are its input.
2. Industry keywords are ranked by where they appear in the text. Aliases cover fintech,
   payments, banking, grid and electrification. The auto-filled sector is the detected
   industry's own sector. A theme the intent demoted does not auto-fill its sector.
3. `is_security_code()` recognises WKN, ISIN, CUSIP, SEDOL and Valor shapes: 6–12 alphanumerics
   with at least one digit and one letter, or 5–9 digits. Trailing codes (with or without a
   label) are stripped from the name before a listing, and a name made only of codes is
   rejected. Short tickers (`PH1`, `3M`) and years are not codes.

### Review round 1 (independent review + security review)
- **Materials-class rules tightened.**
  - A coordinated class counts only when one adjective is critical, strategic or rare.
    "Specialty chemicals and advanced materials" and "engineered materials" are not miners
    theses.
  - The class makes a materials thesis only when it is what the companies SUPPLY
    ("supplying…", "producers of…").
  - "Defence primes exposed to critical minerals" keeps its defence industry.
- **Phrase matching is word-bounded** (`_contains`, keyword order). "Banks, payments…" and
  "Utilities, …" put a comma after the phrase, and the space-only test missed it.
- **Aliases trimmed to unambiguous phrases.** "transmission", "transformer", a bare "grid"
  and "lender" are dropped: `normalize_sector` serves every caller.
- **Bare codes need two digits** (or ISIN / all-digit shape), so "B2GOLD", "E2OPEN" and
  "4IMPRINT" stay names. A labelled code ("SEDOL 6086253") is stripped.
- **Security: the trailing-code pattern runs on the last 120 characters**, with whitespace
  collapsed. On a long Unicode-space run in a PDF block it backtracked in cubic time and
  could hold the worker for minutes.

### Not changed / known limits
- No issuer or thesis is special-cased.
- A six-letter all-alphabetic code cannot be told from a word, so it is not stripped. A bare
  WKN with a single digit ("A1JWVX") is not stripped either: it stays in the name, as before.
- Theme and sector may still come from different keywords ("AI data centers and power grid
  companies" → theme grid, sector Technology).

---

## ADR-064: A Table's Unit Is Printed in the Table — Never Borrowed from an Inline Amount

**Date:** 2026-11-01 · **Status:** Accepted (implementation) · **Extraction pipeline:** v20
**Trigger:** cross-sector QA campaign 2026-11. Richemont deep report `f722d620` showed net
cash "8,496 bn EUR" and operating cash flow "4,880 bn EUR", a thousand times the truth.

### Context
- Richemont's FY2026 results announcement prints its "Key financial data" table as
  `Net cash position € 8 496 m € 8 257 m`: the unit is beside each figure and absent from
  the header (`2026 2025 change`).
- Its cash-flow statement prints `€m €m` on a units row under the years.
- The layout reconstructor kept only numeric words, so both units were lost.
- `_table_currency_scale` then fell back to the page's excerpts and took the FIRST scale
  token there: the "billion" of "Group sales reached € 22.4 billion".
- ADR/pipeline v18 had fixed the same hazard only for official announcements
  (`table_units_only`, Pensana).

### Decision
1. **Value-zone units.** The reconstructor reads currency/scale tokens printed among a
   row's values (`€`, `US$`, `CHF`, `m`, `bn`, `million`, `'000`, alone or joined as `€m`).
   When the header's label zone has no unit, the table's unit becomes `"<currency> <scale>"`,
   provided every row that prints one agrees on exactly one currency and one scale. A bare
   "m" counts only beside a currency (it may be metres). A header unit still wins.
2. **Declared units only from prose.** The page fallback uses `_declared_scale()`. A scale
   word or abbreviation directly after a number ("€ 22.4 billion", "5.2m", "€ 8 496 million")
   belongs to that amount and is skipped. "in € millions", "€m" and "$'000" still declare.
3. **Narrow column gutters.** A justified two-column page may leave only ~4pt between its
   columns: Richemont's announcement, page 2, has the left column's longest line ending at
   293.4pt and the right column starting at 297.6pt. The page was read interleaved, and
   "the Jewellery Maisons … grow their operating profit to € 5 billion" absorbed "the Group
   maintained …" from the other column. The segment figure was scoped to the Group, and the
   report invented a conflict with the €4.5bn group operating profit. A gutter of 3–10pt is
   now trusted only when BOTH sides carry at least 8 column-body lines, each at least 30% of
   the page width.
4. Extraction pipeline version 19 → 20, so held documents are re-read (DEPLOYMENT.md).

### Verified
- Re-extracting the real Richemont announcement gives tables p1/p12/p13 the unit
  `€ million`.
- Net cash 8,496 and operating cash flow 4,880 are validated at `million`.
- No table is scaled "billion".
- Pensana's whole-dollar table now stays unscaled on the general path too.

### Not changed / known limits
- A table whose only unit clue was an inline prose amount now has no scale. Its money
  facts stay excerpt-only: missing, not wrong.
- The key-data table's "Sales" caption is still not read as revenue there (revenue comes
  from the segment and income-statement tables).

### Review round 1 (ADR-064)
An independent review found that a unit printed among the values could scale more
than the rows that print it. Fixes:
- **Coverage.** A value-zone unit becomes the table's unit only when one of two things holds.
  Either a units row before the first figure declares it ("€m €m" under the periods), or at
  least two thirds of the money rows print it beside their figures (percent rows aside).
  One row's "€ 2.1 bn" in a table of unlabelled millions no longer scales the table. This
  applies in the reconstructor and in the validator's `_value_cell_scale` alike.
- **Never over a declared page unit.** When the page declares one scale ("All amounts in
  € thousands") and the cells print another, the scale is None. Neither is trusted over the
  other.
- **A bare "000" is not a unit.** It is a thousands group split off a number. Only "'000"
  or a currency-attached "€000" count.
- **Change columns are ignored.** A signed token (+, −, ±) or a percent cell says nothing
  about the period columns' unit.
- **Column years before a unit still declare it.** "2025 2024 million CHF" and
  "31 March 2026 € million" are declarations, not an amount "2024 million".
Re-verified on the real Richemont FY26 results PDF: tables p1, p12 and p13 are "€ million".
Net cash is 8,496m and operating cash flow 4,880m, both validated in millions.

### Review round 2 (ADR-064)
- **The page check now covers reconstructed tables.** The reconstructor writes a value-zone
  unit into the header cell, so the validator read it as a header unit and never compared
  it with the page. The reconstructed table now carries `unit_from_values`, passed on as
  `ExtractedTable.unit_from_values`. Such a unit is checked against a unit the page
  declares exactly like a raw cell table's: when they disagree, the scale is None. A unit
  printed in the header's own label zone still wins outright.
- **One bare year-like number is an amount.** "€ 1950 million" no longer reads as column
  years. Only two or more years, or a month before the year ("At 30 June 2026 € million"),
  are headings.
Re-verified on the real Richemont FY26 PDF: unchanged (tables "€ million", net cash
8,496m, operating cash flow 4,880m).

---

## ADR-065: A Quarter Is Keyed by Its Fiscal Year; the Issuer's Own Release Is "Company Says"

**Status:** Accepted (cross-sector QA campaign 2026-11)

### Context
The live Richemont report (run `19354d90`) showed two findings about the quarter ended
30 June 2026 as "2026-Q1, source dated 2025-11-21", and discarded a third as citing
evidence that "does not agree on periods ['2026-Q1', '2027-Q1']". The issuer published one
quarter in two documents:
- the PDF `…-fy27-q1-sales-en.pdf`, which rule 1 (the fiscal label) reads as **2027-Q1**;
- the HTML release `…-for-its-first-quarter-ended-30-june-2026`, which rule 3 (the
  period-end phrase) keyed by the quarter's end year as **2026-Q1**.

The same quarter had two keys. Both findings also carried "[reported in the press; not from
a filing]", although their only source was the issuer's own release.

### Decision
1. **A period-end phrase names the fiscal year.** When the end month is printed in an
   "ended / ending / to / through" clause, a quarter's year is the year its fiscal year
   ENDS: end month + 3 × (4 − ordinal) months. That is the issuer's own label (FY27 Q1) and
   SEC's `fy` convention.
   - Q1 ended 30 June 2026 → 2027-Q1.
   - Calendar fiscal years are unchanged (Q2 ended 30 June 2026 → 2026-Q2).
   - A fiscal year ending in January or February is named both ways (Walmart "fiscal 2027"
     = year to January 2027; Home Depot "fiscal 2025" = year to February 2026), so no
     period is stated.
   - Without an end month, the printed year is kept.
2. **The verified issuer's own non-filing material is "company says; not from a filing".**
   A financial figure whose support includes the verified issuer's own press release,
   presentation or IR page is labelled `LABEL_COMPANY_SAYS_NOT_FILING`, not "reported in
   the press". It stays non-closing: context, never a gap's answer, because the filing path
   is canonical. A page that only CLAIMS the issuer's text is still the press. The web
   reader knows the new label.

### Review round 1
- **A fiscal key is fiscal for "had it begun?".** The phrase now returns the new basis
  `fiscal_period_end_phrase`, which `disclosure_period_policy._is_fiscal` counts as fiscal.
  Before, the title-only path (UK NSM, ASX) refused "first quarter ended 30 June 2026",
  published July 2026, because 2027-Q1 read as a calendar quarter "had not begun". A
  forecast title ("first quarter ending 31 March 2028", published 2026) is still refused.
- **52/53-week years.** A quarter ending on day 1–7 of a month counts as the month before
  ("first quarter ended April 4, 2026" → 2026-Q1, "fourth quarter ended January 2, 2027" →
  2026-Q4). Before, it landed on a January fiscal year-end and stated nothing.
- Not changed (recorded): when a verified issuer's figure conflicts with a filing,
  `ledger/store.py` still writes "reported in the press; a filing figure differs". It is
  non-closing, but the wording is wrong for the issuer's own voice. `_period_hint` shows
  "Q1 2027" with no FY marker.
- **Review round 2.** Only a SHIFTED fiscal year carries the fiscal basis. A calendar
  quarter ("first quarter ending March 31, 2027", published November 2026) keeps the strict
  rule and is still refused as a forecast.

### Not changed / known limits
- Halves keep the existing rule. A half ending in a non-calendar month states no half.
- Stored corpus versions are immutable, so a page already stored as 2026-Q1 keeps that key
  until a new version of it (new content) is ingested.
- Why the release carried a published date of 2025-11-21 is not explained by this change.
  It is recorded as open in the QA report.

---

## ADR-066: A Separately Registered Company Carrying the Issuer's Name Is Not the Issuer

**Status:** Accepted (cross-sector QA campaign 2026-11, defect H3)

### Context
Honeywell's deep report (run e50cb267) filed four statements about Honeywell Aerospace
as Honeywell findings, in growth, competitive and risk sections: "Honeywell Aerospace
backlog was $18.2 billion", a $500M Tempe facility (twice), and a False Claims Act
settlement. Honeywell Aerospace Inc. (HONA) files with the SEC under its own CIK. Two
guards exist, and neither can see this:

- the publisher firewall (`foreign_publisher`) is keyed on the publisher domain, and
  HONA's releases are published on honeywell.com;
- the registered-companies guard skips any name whose first distinctive word is in the
  subject's name, so "Honeywell Aerospace" is never treated as another company.

### Decision
The regulator's ticker list decides which names are separate registrants.

1. `related_registrants(listings, ticker, name)` reads the SEC company-tickers directory
   (`discovery.directories.SEC`, already used by Discovery; cached per process per day).
   It finds the subject by ticker AND by its first distinctive name word, so a ticker
   another venue reuses (Richemont's SIX `CFR` is Cullen/Frost on the SEC list) never
   stands in. It returns every registrant with a different CIK whose name starts with
   the same word followed by a word that is not in the subject's name and is not generic.
   A generic or short first word (First, American, GE) names no family.
2. `statement_about_related_registrant` assigns a statement to a registrant only when
   the registrant is the sentence's grammatical SUBJECT and the subject company is never
   named on its own. The registrant's name must open the sentence, optionally after "The"
   or one short lead-in clause ("In March 2026, "). These stay the subject's:
   - "Honeywell completed the spin-off of Honeywell Aerospace";
   - "Honeywell's Aerospace Technologies segment…";
   - any statement where the registrant is only an object: "The company sold its stake
     to Honeywell Aerospace", "SCCO signed a power contract with Southern California
     Edison". A customer, buyer or counterparty of the subject is the subject's fact.
3. The investigator applies the rule beside the other-company guard (outside peer
   domains), but only to a statement that rests ENTIRELY on web text. The issuer's own
   filing speaks for the issuer. A dropped statement is counted in
   `statements_about_related_registrant`, and the question gets one `entity_ambiguous`
   gap naming the registrant and its ticker.
4. The rule applies only to an SEC-eligible subject (an absent exchange counts as
   eligible; the ticker-and-name match keeps that safe). For any other subject, or when the
   directory cannot be read, the guard fails open and changes nothing.
5. The test suite makes the directory read inert by default (`conftest`), as it does
   every other live SEC lookup.

### Replay on the live SEC directory (2026-11-01, 7,684 rows)
959 of 6,069 registrants have at least one name-sharing registrant. Most are unrelated
namesakes (Southern Copper / Southern California Edison, the Canadian, China and Alpha
names, fund families such as Nuveen and BlackRock). That is why the rule asks for the
subject position. "Southern California Edison raised rates" is not a Southern Copper fact,
but "SCCO signed a contract with Southern California Edison" is. Of the campaign
companies, only HON → HONA is flagged; ADBE, JPM, PYPL, CLLS, GE, MMM, AMZN, GOOGL, BRK-B,
MRNA and MP have none.

### Review round 1
- **One reporting group is not separate.** A consolidated subsidiary that registers its
  own debt or preferred stock (Entergy Arkansas, Louisiana, Mississippi and Texas beside
  Entergy) files a COMBINED 10-K with its parent, under the parent's accession number. A
  statement is dropped only when `directories.sec_annual_accessions` shows that the
  registrant's annual-report accessions and the subject's do not overlap. If either
  record is unreadable, nothing is dropped. Live check: HONA (no 10-K yet) is separate;
  all four Entergy subsidiaries share Entergy's accessions; Southern California Edison is
  separate from Southern Co; BlackRock's closed-end funds (no 10-K) are separate.
- **Appositive, dated lead-in, spacing.** The registrant's name is tested at the very
  start (after "The") as well as after one lead-in clause. "Honeywell Aerospace, based in
  Phoenix, announced…" and "On Oct. 30, 2026, Honeywell Aerospace began trading…" are now
  judged, and the name's words may be separated by any whitespace.
- **The subject's ticker counts as naming it.** It is matched case-sensitively as a whole
  token on the original text: "Per HON's 10-Q, Honeywell Aerospace sales…" is kept, while
  "HONDA" is not "HON".
- **The subject's own words include its SEC name.** A short platform name ("Western")
  no longer makes "Western Digital" look like another company.
- **The phrase is the name as written.** The registrant's first two raw words must be
  its first two distinctive words, so "Southern Company Gas" no longer yields the phrase
  "southern gas".
- **One gap per write-up names every registrant dropped**, not just the first.

### Review round 2
- **Only the sentence's own subject is judged.** A first attempt judged every
  `;`-separated clause, and the lead-in could cross a sentence end. Both dropped facts
  that belong to the subject when the subject is implicit: "Organic sales grew 6% in Q3;
  Honeywell Aerospace led with 12% growth" and "Organic sales grew 6%. Separately,
  Honeywell Aerospace won…". A registrant in a later clause is now a miss, never a drop.
  A lead-in may contain ". " only after a month abbreviation ("On Oct. 30, 2026, ").
- **No annual report of the subject's own → no drop.** Two empty sets would have made
  "separate" out of no evidence. 10-KT and 10-KT/A count as annual reports.
- **One question to the regulator per registrant.** The verdict is remembered on the
  investigator, failures included. A failed submissions read backs off for 15 minutes
  (`FAILURE_RETRY_SECONDS`), so a hanging data.sec.gov costs one timeout, not two per
  statement. `reset_cache()` clears the new caches.
- **Security review.** The backoff is set when the host does not serve the record
  (`ok=False`: a timeout, 403, 429 or 5xx, which the fetcher returns rather than raises).
  One malformed record is "no verdict" for that record alone and does not back off the
  host.
- **Replay of both live HON reports:**
  - e50cb267 (44 findings): 3 of the 4 H3 findings are dropped (Tempe twice, False Claims
    Act). All cite web corpus chunks (`secondary_web`, `government_or_regulator`).
  - f0be3c4f (43 findings): 1 is dropped (Tempe).
  - Kept in both: the spin-off itself, the reaffirmed outlook, the directors' move, and
    the 10-K segment facts.

### Not changed / known limits
- A registrant that is the subject of a LATER clause or sentence is not judged. The live
  e50cb267 F15 "The Tempe plant is tied to US$15 billion …; Honeywell Aerospace backlog
  was $18.2 billion" stays. A statement that also names the subject by its current name
  stays too: f0be3c4f F17 "Honeywell Aerospace completed its spin-off from Honeywell
  Technologies … reported standalone Q3 results … $0.40 dividend" mixes the subject's
  event with the registrant's results. Both are misses, chosen over false drops of the
  subject's own facts.
- A registrant name after a lead-in of more than 60 characters, or after two lead-in
  clauses, is not judged. That is a miss, not a false drop.
- A wholly owned subsidiary that registers securities but files its OWN annual report
  (not a combined one) is treated as separate. No such pair was found among the
  campaign's companies or the utility and bank parents probed.
- When the subject is a fund sponsor, statements about its listed funds are dropped
  (BlackRock / a BlackRock trust). This is intended: a fund's distribution or NAV is not
  the sponsor's result.
- Only web-text statements are judged. HONA text that arrives as a non-web item (an IR
  event, a non-web corpus chunk) is not.
- Non-US families (a namesake listed only on the LSE or ASX) are not covered. No other
  directory carries a registrant id the platform can compare.
- Names whose second word is generic (e.g. "X Energy Partners") are not treated as
  separate. Nothing in such a name tells a reader that it is not the subject.
- History is lost on purpose. A pre-spin statement about the business under its brand
  ("Honeywell Aerospace backlog" while it was still a segment) is dropped too. The
  segment's filed figures still come from the issuer's 10-K, which the guard never
  judges.

---

## ADR-070: Discovery Relevance Ranks by Fit, Not by Where a Candidate Came From

**Status:** Accepted (cross-sector QA campaign 2026-11, Hypothesis J)

### Context
In every Discovery run of the campaign (A, B, D, E, E2), the pre-scan relevance score was
constant per universe source. In E2, on the materials thesis:
- curated-registry items scored 85;
- platform-registry items scored 30;
- every web lead scored 20.

ETM, whose own issuer text verified the rare-earths theme, scored 20, while Cleveland-Cliffs
(steel, curated) scored 85. Re-scoring the stored items found two causes:

1. Dynamic candidates (web leads and held companies) were scored against the LEGACY
   thesis parse (`parsed_thesis_json`). For the materials thesis it still reads
   defence / semiconductors / grid, the D2 misreading that ADR-063 fixed only in the
   V3.19 intent. The curated universe is built and scored on the intent
   (`{**parsed, **intent.universe_filter()}`). One run therefore held scores from two
   different readings of the same thesis, and every dynamic candidate got 0 theme points.
2. `universe_item` labelled every web lead with the thesis theme, whatever its evidence.
   With the right parse, that unearned label would be worth the dominant 45 points.

### Decision
- `score_dynamic_items` scores dynamic candidates on the same merged reading the curated
  universe uses. A registry item that already carries a score keeps it.
- A search lead carries the theme it was searched for, marked `theme_unverified` unless
  its theme evidence is verified (`verified_issuer`, `independent` or `multiple`). An
  unverified theme earns HALF weight (22.5 of 45 points). The scorer's basis, e.g.
  "searched for theme … (not yet verified; half weight)", is APPENDED to the reason the
  pipeline already set ("…; score basis: …"), so the half weight is visible in the
  admin universe view (review S1). Being returned by a thesis search (an
  `external_search` lead) is weak evidence of fit, but not no evidence.

### Replay on stored runs
| Run | Candidate | Before | After |
|---|---|---|---|
| E2 | Eramet, Umicore, Pensana, Rainbow (held) | 30 | 75 |
| E2 | ETM (verified) | 20 | 65 |
| E2 | Lynas, Lindian, Astron, ABX (unverified) | 20 | 42.5 |
| D | WTC (verified) | 66 | 66 |
| D | TSLA, DroneShield, Symbotic (unverified, off-thesis per the campaign review) | 66 | 43.5 |
| A | Cellectis (unverified) | 61 | 38.5 |

### Not changed / known limits
- A verified web lead still trails a curated item by the sector and industry points that
  only registry data carries (65 vs 85). Its theme is verified, but its industry is not.
- The curated registry's own classification is trusted. Cleveland-Cliffs (steel) is filed
  under `mining_materials` and scores 85 on a specialty-materials thesis.
- The legacy parse is still stored and still read by older consumers. Only the relevance
  score of dynamic candidates changes here.
- Keyword, catalyst and confidence points still come from the legacy parse, which the
  intent does not replace. For E2 the D2 keywords (defence, semiconductors) can still
  earn up to 15 keyword points (review N2).
- `theme_unverified` lives only on the universe item. `thesis_match_json` and the
  director's `matched_theme` still present the searched theme as a match for an
  unverified lead. This mislabel predates this ADR and is not fixed here (review S2).
- The pipeline's own reason can contradict the score basis. A lead whose issuer text
  verified the theme (ETM) can still show "no verified statement of what the company
  does names the requested theme" from the industry classifier, beside "matches theme".
  The two checks read different evidence.

## ADR-071: A Contradiction Is Two Single Claims About One Quantity

**Status:** Accepted (cross-sector QA campaign 2026-11, defect ADBE-2)

### Context
In Adobe's deep report (7b401826) two statements matched the FY2025 10-K but were
relabelled "[reported in the press; a filing figure differs]":

- a segment breakdown: "Digital Media $17,649, Digital Experience $5,864, Publishing and
  Advertising $256, total $23,769";
- a geography breakdown: "United States $12,529M … APAC $3,360M".

Four `conflicting_sources` gaps called the group figure "canonical" against them. In
two of those gaps the group figure was "USD 8,706m", which is the **operating income**
read out of a margin sentence ("operating income USD 8,706m, net income USD 7,130m and
revenue USD 23,769m").

`find_contradictions` compared the set of amounts in a clause with the set in another
clause. Any set with more than one amount is "different" from a single figure. Scope
keys did not help, because each statement inherited `group` from the evidence it cited.

### Decision
`trust.find_contradictions` now needs two things before it records a conflict:

1. **Each side states exactly one amount for the field** (`_money_disagree`). A
   breakdown, a sentence that names several amounts, or a this-year-versus-last
   comparison is not one value.
2. **Both sides speak about the same part of the group** (`_same_part`). Either neither
   clause names a segment, division, business unit, reportable segment, Maison or
   region, or both do and the capitalised name in front of the part word is the same
   ("Digital Media segment" against "Digital Media segment"). A part with no name never
   conflicts. This rule reads the words themselves, because the inherited scope key
   cannot be trusted. Values that agree to six significant figures count as one value,
   so "$4.1bn (US$4,100m)" is still a single claim.

Two single group figures that differ still conflict, and so do two single figures for
the same segment. The filing figure stays canonical in that case.

### Replay (live Adobe statements, synthetic issuer)
`tests/test_qa_campaign_manufactured_conflicts.py`:

| Pair | Before | After |
|---|---|---|
| Segment breakdown vs group revenue 23,769 | conflict | none |
| Segment breakdown vs margin sentence | conflict (8,706 "revenue") | none |
| Geography breakdown vs either | conflict | none |
| "Digital Media segment revenue $17.6bn" vs group revenue | conflict | none |
| "Digital Media segment $17.6bn" vs "Digital Experience segment $5.9bn" / "Americas region $14.1bn" | conflict | none |
| Rounded "$23.8 billion" vs filing USD 23,769m | none | none |
| Group revenue $31.2bn vs filing 23,769 | conflict | conflict (filing canonical) |
| One segment, two different values | conflict | conflict |

### Not changed / known limits
- Some real conflicts are now lost, because a side that states more than one amount
  never conflicts: "$1.5bn (2024: $1.0bn)" vs a filing of $1.2bn; "$1.5bn, of which
  $0.9bn US"; "$1.5bn to $1.6bn"; and a breakdown whose total is wrong. Before this ADR
  such pairs always conflicted (the sets differed in length), so a CORRECT "$1.2bn
  (2024: $1.0bn)" was relabelled too. The numeric verifier still checks each amount
  against facts.
- The part name is the capitalised words in front of the part word. "Jewellery
  Maisons" and "Jewellery" are different names, so a conflict between them is missed.
- `rf.money_values` reads "23,769 USD millions" (scale word after the currency) as
  USD 23,769 (review). That is a separate parser defect, not fixed here; the peers tool
  emits that unit (`agent_tools/peers.py`).
- A single group statement that happens to contain "region" or "segment" ("revenue in
  every region rose to $X") is treated as part of the group, so a real conflict with it
  is missed. That is a miss, not a false relabel.
- The margin sentence is still read as "revenue USD 8,706m" by the field extractor
  (`rf.field_clause` takes the clause that names the field). Only the contradiction
  step now ignores it. Other consumers of that reading were not audited in this slice.

## ADR-073: The Shortlist Cap Keeps the Best-Fitting Names, and Says Which It Cut

**Status:** Accepted (cross-sector QA campaign 2026-11, Hypothesis J)

### Context
Discovery run E2 (d6f5d2c6) had 23 candidates that met the hard requirements, against a
shortlist cap of 15. `run_dynamic_stage` sorted them with
`constraints.ranking_key(eligibility)` and passed no thesis relevance, although the key
accepts one. Ties within an eligibility tier therefore fell to insertion order:
curated, then held, then web. Eight `eligible_unverified` names were cut, including six
of the seven web candidates whose theme evidence the open-web admission had verified.
Nothing in the run said so; the campaign's Discovery review could only infer the cut
from the counts.

### Decision
- `run_dynamic_stage(relevance=...)` takes a scorer. The service passes the same scorer
  that scores the shortlist afterwards (`score_dynamic_items`, ADR-070). The scorer's
  value is the tie-break inside `ranking_key`, so it applies before the cap. The order
  is unchanged otherwise: eligibility first, then verified hard passes, then soft
  passes. Fit never lifts an unverified company over an eligible one.
- If the scorer raises for a record, that record scores 0 and ranks below its scored
  tier-mates. The stage never fails because of it. If every record fails, the old
  insertion order is kept.
- **Never silent.** `funnel.beyond_cap` counts the names the cap cut. A stage warning
  names up to 20 of them (`VENUE:TICKER`) and is placed first, so the run's truncated
  warning list keeps it. The admin funnel shows "N met the requirements but were beyond
  the shortlist cap".

### Not changed / known limits
- **Uncapped runs change too.** Within a tier, the shortlist is now ordered by fit, so
  `items`, `requested_tickers` and the scan order change for every dynamic run, not
  only for capped ones. Ties in the final `_eligibility_rank_key` keep that order.
- Relevance remains the LAST key. A curated name with more verified soft passes still
  outranks a better-fitting web lead with fewer. Passes measure how much was verified,
  not fit, and reordering the tiers is a policy decision this slice does not take.
- The cap value is unchanged (`max_candidates`, default 10, 15 in the campaign runs).
- The names beyond the cap are not persisted as candidates; only the warning names
  them.
- No stored run is re-ranked. The replay needs a new run, because the payload of a
  past run does not hold the names that were cut.

## ADR-068: An 8-K Is a Results Release Only When Its EDGAR Items Include 2.02

**Status:** Accepted (cross-sector QA campaign 2026-11, defect JPM-2)

### Context
JPM report 30828043 (job 60e705ec) read the wrong 8-K as JPM's latest results release.
It read a 2026-10-20 note-offering legal-opinion 8-K (accession 0001193125-26-426704,
Items 8.01 and 9.01). The Q3 2026 earnings 8-K (0001628280-26-065582, Item 2.02
"Results of Operations and Financial Condition", exhibit 99.1) was filed on 2026-10-13.
Q3 2026 (net income $15.3B, CET1 13.9%, ROTCE 20%) was missing from the report.

The SEC filing-body path (`sec_filing_documents.resolve_filing_documents`) visits
filings by form rank (10-K, 20-F, 10-Q, 6-K, 8-K), newest first within a form. With the
default two-body cap (`primary_document_sec_max_bodies`) and the 90-day catalyst window,
JPM resolved to the Q2 10-Q plus the newest 8-K. `doc_kind_for_form` also labelled
every 8-K `results_release`. EDGAR's `items` field, which says what each 8-K is, was
parsed into `CatalystEvent.item_numbers`. It was then dropped by both adapters that
build the resolver's filing dicts (`live_fetchers._filing_dict` and
`company_evidence.sec_filings_from_catalyst`). Any 8-K filed after the earnings 8-K
(a note offering, a director change, a Regulation FD deck) therefore displaced the
quarter's results.

### Decision
- **The item code decides.** An 8-K is a results release only when its EDGAR items
  include `2.02` (`is_results_release_8k`). Multi-item strings (`"2.02,9.01"`,
  `"Item 2.02, Item 9.01"`) and lists are both parsed (`parse_item_codes`).
- **Ranking.** Within the 8-K rank, an Item 2.02 8-K now comes ahead of every other
  8-K, newest first in each group (`_filing_rank`). A newer 8-K without 2.02 can no
  longer displace the latest Item 2.02 8-K. Form rank still comes first, so a 10-Q
  still beats any 8-K.
- **Honest label.** An 8-K whose items are known and do not include 2.02 is labelled
  `other`, not `results_release`. This applies when no Item 2.02 8-K is in the window
  and the newest 8-K is still read (existing behaviour). The item codes travel on
  `SecFilingDocument.item_codes` and `SecPreflightFailure.item_codes`, so the fetched,
  budget-skipped and preflight artifacts all carry the same label.
- **Plumbing.** Both filing-dict adapters pass `item_numbers` through RAW, not
  wrapped in `list(...)`. `list("2.02,9.01")` would split the string into
  characters, and a real Item 2.02 8-K would be labelled `other`. `parse_item_codes`
  accepts both strings and lists.
- **The press release, not the cover page.** An Item 2.02 8-K's primary document is a
  cover page of a few KB. It only refers to the earnings release filed as Exhibit 99.1.
  EDGAR's `index.json` types every file by its icon (`text.gif`), so it cannot identify
  that exhibit. For an Item 2.02 8-K, the resolver therefore also reads the filing's
  `<accession>-index.htm` page (`fetch_filing_index_page`), whose "Document Format
  Files" table gives each document's EDGAR type. It resolves the `.htm` document typed
  `EX-99.1`, or else the first `EX-99*` (`select_results_exhibit`).
  - Same host (`www.sec.gov/Archives`), same guarded, pinned, throttled network leg as
    `index.json` (one shared `_fetch_sec_archive_bytes`). No new host and no
    caller-supplied URL. The filename is the anchor text and is re-validated by
    `build_document_url`.
  - The page has its own byte cap (`INDEX_PAGE_MAX_BYTES`, 256 KB; the real page is
    ~12 KB) and its own `sec_filing_index_page_*` log events.
  - No regex backtracking. A first version located the table with
    `<table[^>]*summary="…"[^>]*>`, which took 143 s on a 100 KB page of unclosed
    openers, on the event loop. That is the failure mode of the gunicorn worker-timeout
    outage. The parser now uses only `str.find` / split on `</tr>` and `</td>`, and
    lowercases ASCII only so indexes stay aligned. It runs in `asyncio.to_thread`.
    Adversarial 256 KB pages parse in at most ~27 ms, and a test bounds them at 0.5 s.
  - The page fetch counts against the existing attempt cap (`max_documents × 3`) and
    deadline. The two-body cap is unchanged.
  - Degrades, never fails. If the page cannot be read, names no `EX-99*` document, or
    the budget is spent, the primary document is kept. An 8-K with missing or non-2.02
    items never triggers the page fetch.
  - Downstream is unchanged. The document keeps form `8-K` and its item codes, so the
    artifact is still labelled `results_release`. `SecFilingDocument.is_exhibit` is
    `True`.
  - JPM replay (real index pages, offline): accession 0001628280-26-065582 now resolves
    to `…/19617/000162828026065582/a3q26erfexhibit991narrative.htm`, not
    `jpm-20261013.htm`.
- **Offline replay** on cached EDGAR submissions (90-day window, first 8-K visited):
  - JPM before: 2026-10-20 8.01/9.01. After: 2026-10-13 2.02/9.01.
  - ADBE: 2026-09-10 (2.02) before and after.
  - PYPL: 2026-10-27 (2.02) before and after.
  - HON: 2026-10-22 (2.02) before and after.
  - CLLS (6-K filer): 2026-10-28 6-K before and after.

### Not changed / known limits
- **6-K filers.** EDGAR records no items for a 6-K. Ranking and label (`interim_report`)
  are unchanged.
- **Missing item codes.** An 8-K whose items are missing keeps the legacy
  `results_release` label, because a caller that passes no item codes cannot be told
  apart from the old behaviour. Such an 8-K never outranks a known Item 2.02 8-K. The V3
  filing bridge (`ensure_filing_corpus_evidence` → `acquire_sec_filing`) passes only
  accession and form, so 8-Ks it acquires keep that legacy label.
- **One exhibit only.** The financial supplement (JPM's EX-99.2, 3.4 MB) is not read.
  Neither is any exhibit of a non-2.02 8-K. `allow_exhibits` is unchanged for every
  other form.
- **V3 bridge sees an exhibit URL.** A press release persisted by this path has an
  exhibit-shaped URL when the issuer names it so (JPM's `…exhibit991…`; PayPal's
  `pypl3q-26earningsrelease.htm` is not). If the V3 filing bridge later asks whether
  that accession is searchable, `filing_evidence_state` treats an exhibit-only version
  as "not the filing" and reacquires the cover page beside it. Nothing is lost, but
  the bridge never prefers the release.
- **An extra request per results 8-K.** Reading the `-index.htm` page (~12 KB) costs
  one more throttled SEC request.
- **V3 core filings.** `ensure_core_filings` still secures only the latest 10-K and
  10-Q. A quarter reported by 8-K but not yet in a 10-Q is not secured into the corpus
  by that step.

## ADR-067: Total Debt Is One Complete, Compatible Combination of Concepts, or Nothing

**Status:** Accepted (cross-sector QA campaign 2026-11, defect JPM-1)

### Context
JPMorgan's report (30828043) said "Total debt 64,776m" and called a debt/equity of 0.179
"conservative". 64,776 is JPM's `ShortTermBorrowings` alone. JPM last tagged
`LongTermDebt` for 2013 (withheld, correctly, by the V3.18.1 own-period rule). It now
tags `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` (435,206m),
which the normalizer did not know. `total_debt` was then the "partial sum" of whichever
leg was present, published with only a warning.

The derivation had a second defect. It picked the freshest of
`LongTermDebt`/`LongTermDebtNoncurrent` and the freshest of
`DebtCurrent`/`ShortTermBorrowings`/`LongTermDebtCurrent` independently, then added
them. In the US-GAAP taxonomy these concepts overlap:

- `LongTermDebt` and `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`
  include the current portion of long-term debt.
- `LongTermDebtNoncurrent` and `LongTermDebtAndCapitalLeaseObligations` exclude it.
- `LongTermDebtCurrent` (and `…CapitalLeaseObligationsCurrent`) is the current portion.
- `ShortTermBorrowings` excludes the current portion. `CommercialPaper` and
  `OtherShortTermBorrowings` are its parts.
- `DebtCurrent` is short-term borrowings plus the current portion.

`LongTermDebt + LongTermDebtCurrent` and `LongTermDebt + DebtCurrent` both count the
current maturities twice.

### Decision
1. A new module, `app/integrations/sec_total_debt.py`, holds the concept roles and
   `derive_total_debt`. Every term is read at the bundle's `reporting_period_end`, in a
   filing of the bundle's basis (10-K for annual, 10-Q for quarterly), from the reporting
   accession where one exists. Total debt is published only as one of these sums, tried
   in this order:
   - (b) noncurrent LTD + `DebtCurrent`. This is skipped when `DebtCurrent` is smaller
     than current LTD + short-term borrowings combined (with a rounding allowance of
     max(1m, 0.1%)).
   - (c) noncurrent LTD + current LTD + short-term borrowings.
   - (a) LTD including current maturities + short-term borrowings.
   - Then the same sums with a short-term term missing, each with a basis note: "no
     short-term borrowings tagged for the period (last tagged as … for …)", "no current
     maturities…", or "no current debt…".

   Short-term borrowings are `ShortTermBorrowings`. Without it, `CommercialPaper` or
   `OtherShortTermBorrowings` alone is used as it stands. When both are non-zero, they
   are added only if a total confirms the sum: `NotesAndLoansPayable`,
   `ShortTermBankLoansAndNotesPayable`, or `DebtCurrent` − current maturities. A total
   equal to the larger part means the smaller part sits inside it, and the larger part is
   used. With no confirming total, the two readings differ and the facts cannot settle
   it. Total debt is then withheld; it is never a guess either way. (BAC's balance sheet
   has one line, "Short-term borrowings 48,088", tagged `OtherShortTermBorrowings`, with
   its 20,400 of commercial paper inside it. KO's "Loans and notes payable" 1,551 really
   is 1,495 CP + 56 other.) Noncurrent LTD may be derived as
   (including current − current) when both are tagged and the noncurrent concept is not.
   `DebtCurrent` is never added to an including-current concept.
   A total without current maturities (noncurrent alone, or noncurrent + short-term
   borrowings) is published only when nothing is known to be missing. When the filer's
   maturity schedule (`LongTermDebtMaturitiesRepaymentsOfPrincipalInNext(Rolling)TwelveMonths`)
   shows principal due within a year, the total is withheld, with the reason in
   `debt_basis.withheld_reason`. The noncurrent line is still published as
   `long_term_debt`.
2. **Same line, two concepts.** When short-term borrowings equal the current-maturities
   concept (within 0.5m), it is one balance-sheet line tagged twice, and it is counted
   once. IBM and AMD do this. Without the rule, adding the lease-inclusive current concept
   would have overstated IBM by 6,424m. Where both an including-current and a noncurrent
   concept are tagged, their difference must also equal the current maturities.
   Otherwise the total is withheld as ambiguous.
2a. **The filer's own total is a check.** `DebtAndCapitalLeaseObligations` and
   `DebtLongtermAndShorttermCombinedAmount` are never the total, because filers use them
   loosely. A total above one of them is withheld as double counted. The exception is
   when that concept equals the long-term debt including current maturities AND the
   borrowings are a distinct line: larger than the current maturities, so they cannot be
   part of that line. HON qualifies: 28,687m, with 5,893m of borrowings > 1,546m current.
   A review probe does not (noncurrent 10,000 + current 1,000 + borrowings 400 against a
   filer total of 11,000), and is withheld. A total below one of them is kept, with a
   note.
3. **Fail closed.** If no long-term debt concept is tagged for the period, `total_debt`,
   `short_term_debt`, `long_term_debt` and `debt_to_equity` are all `None`. The filer's
   latest long-term concept goes to `withheld_fields["long_term_debt"]` with its own
   period, and a warning names it. Net debt then has no input.
4. **The legs mean one thing.** `short_term_debt` is debt due within one year:
   short-term borrowings plus current maturities. `long_term_debt` is debt due after one
   year. Whenever both are set, they add up to `total_debt`. Both are `None` when basis (a)
   cannot be split because the current portion is not tagged. The total still stands.
   `debt_basis` records the combination, the concepts and values used, and the notes. The
   `sec_edgar.total_debt` datapoint note carries the same text.
5. **Consumers.**
   - `merge_fundamentals` never backfills a debt leg from the legacy alias-order parser
     (`_NORMALIZER_EXCLUSIVE_FIELDS`). Before this change, JPM's 2013 `LongTermDebt`
     would have come back that way.
   - `peers.total_debt_is_usable` gates net debt in both the peer table and the
     subject's statement lines. It needs a published total, no withheld leg, and not
     "a short leg with no long leg".
   - `metrics_from_sec_summary` refuses net debt for a persisted summary of that last
     shape too.
   - `statement_metrics.summary_total_debt_is_usable` is the same rule for a persisted
     summary. Both `metrics_from_sec_summary` (net debt) and
     `market_metrics_enrichment` (enterprise value) apply it. An EV beside a withheld
     debt leg would understate what debt holders are owed. The EV warning says such a
     total is withheld, not that it is missing.

### Replay on 24 filers' cached companyfacts (before → after, USD m)
Before is `main` at the time of the PR; after is this ADR with the PR #307 review fixes.

| Filer | Before | After | Why |
|---|---|---|---|
| AAPL | 103,028 | 98,657 | `LongTermDebt` + `LongTermDebtCurrent` double count → noncurrent 78,328 + current 12,350 + CP 7,979 |
| ABT | 15,962 | 12,929 | same double count; no STB for 2025 (noted) |
| DIS | 48,737 | 42,026 | same double count |
| MSFT | 49,521 | 40,294 | same double count |
| MMM | 14,272 | 12,602 | `LongTermDebt` + `DebtCurrent` double count → noncurrent 10,932 + `DebtCurrent` 1,670 |
| GE | 22,155 | 20,494 | same; equals GE's own `DebtLongtermAndShorttermCombinedAmount` |
| UPS | 24,193 | 24,127 | same; equals UPS's own `DebtAndCapitalLeaseObligations` |
| AMZN | 69,291 | 68,851 | `LongTermDebt` (68,836, face value) → balance-sheet carrying lines |
| HON | 34,939 | 34,580 | balance-sheet 27,141 + 1,546 + STB 5,893 |
| JPM | 64,776 | 499,982 | including-current 435,206 + STB 64,776; d/e 0.179 → 1.379 |
| GM | — | 131,574 | including-current concept was unknown; no STB tagged (noted) |
| BAC | 317,816 | withheld | CP 20,400 and other STB 48,088, no confirming total; R5 shows the CP is inside the 48,088 line (the true figure, 365,904, needs the presentation) |
| KO | — | 45,492 | 42,119 + current 1,822 + CP 1,495 + other STB 56 (confirmed by `NotesAndLoansPayable` 1,551) |
| NEE | 93,056 | withheld | CP 1,955 and other STB 608, no confirming total |
| CAT | 36,210 | withheld | noncurrent 30,696 + STB 5,514; the schedule shows 7,120 due in 12 months |
| PYPL | 9,987 | withheld | noncurrent 9,987 only; the schedule shows 1,397 due in 12 months |

These were unchanged: ADBE 6,210, AMD 3,222, DUK 89,836, IBM 61,260, MRNA 590 (noted),
PG 34,138, WFC 193,035 and WMT 44,762. In the first 19 filers, 7 totals were double
counts of current maturities.

### Not changed / known limits
- A short-term term that is not tagged is assumed absent, with a note, when nothing
  says otherwise. The maturity schedule is the only evidence read. A filer that tags
  neither current maturities nor a schedule (MRNA) keeps its noted total.
- A stale current concept still goes to `withheld_fields["short_term_debt"]`, which makes
  net-debt and EV consumers refuse the total, as before.
- Two non-zero short-term parts with no confirming total withhold total debt, so BAC
  and NEE have none. Their balance-sheet presentation (the R-file, outside companyfacts)
  would settle it. Reading it is out of scope.
- Debt carried only on dimensional (segment) facts, and concepts outside the roles above
  (`NotesPayableCurrent`, finance leases tagged on their own, `OtherLongTermDebt`), are
  not read.
- For a bank, total debt from these concepts excludes deposits and is not a leverage
  measure. The banking playbook does not ask for net debt / EBITDA, but the normalizer
  still emits `debt_to_equity` for a bank (JPM 1.379, WFC 1.066). Suppressing
  it by industry is out of scope here.
- Persisted snapshots written before this change keep their old totals until research is
  rerun. Only the "short leg with no long leg" and "withheld leg" shapes are detectable
  after the fact.

## ADR-069: Generic Metrics a Playbook Declares Not Meaningful Are Not Computed, Required or Counted

**Status:** Accepted (cross-sector QA campaign 2026-11, defect JPM-3)

### Context
JPMorgan's report 30828043 ran under `banks_financials`. Its executive synthesis and
the chair's verdict opened with "operating cash flow was negative, indicating cash
consumption" (-$147.8bn) and "cash conversion -259.1%". Its `final_research_state`
listed EBITDA, EBIT, free cash flow, EV/EBITDA and current ratio as open financial
categories. Its open questions said "Valuation blocked: inputs missing
(financials.free_cash_flow, financials.ebitda)", and EBITDA was a required-field
blocking gap. For a bank none of these has a reading. Operating cash flow swings with
trading assets, loans and deposits, and debt is funding, not leverage.

The playbook already steered the questions. Every generic producer and gap counter was
sector-agnostic, and none of them could ask a playbook anything:

- `director/base_model.py`: `cash_generation_and_funding` requires `cash_conversion`,
  `capex_to_ocf` and `net_debt`. `profitability` requires operating and gross margin.
  `capital_allocation` requires `dividend_cover_by_fcf`. The planner gives every
  company these requirements.
- `agent_tools/peers.py`: `get_sec_statements` computes `PEER_METRICS` (cash
  conversion, capex/OCF, capex intensity, net debt) through `statement_metrics` for
  every subject. It also hands out the OCF, capex and "free cash flow (OCF - capex)"
  lines. These are the `secfin:JPM:FY2025:cash_conversion` and `:operating_cash_flow`
  ids that the two adverse findings cited. `get_peer_financials` and the report's peer
  table (`professional_research.peer_rows`) use the same set.
- `llm/evidence_pack.py`: the V2 council receives `metrics_from_sec_summary` (all of
  `STATEMENT_METRIC_KEYS`) plus the raw OCF, capex, FCF and D/E keys.
- `FinancialDataAgent._EXPECTED_FINANCIAL_CATEGORIES` expects EBITDA, EBIT, FCF,
  EV/EBITDA and current ratio from every company. `final_research_state` copies the
  agent's `missing_fields` into `open_financial_categories`. The completeness schema
  requires `snapshot_financials.ebitda`. The valuation guard requires FCF and EBITDA.

### Decision
- **A playbook declares what is not meaningful.** `Playbook.not_meaningful` is a
  `NotMeaningfulMetrics`. It lists:
  - calculation keys (validated against `DEFINITIONS`);
  - generic financial categories (validated against a closed vocabulary);
  - base research-model `questions` not asked of a matching company;
  - `analysed_by`: categories the business model is valued by instead (validated
    against categories the FinancialDataAgent already tracks);
  - a `reason` and a short `reader_note`.

  It carries its own `applies_to`, which must match by industry or signal and never by
  sector alone. A playbook cannot both require a calculation and declare it not
  meaningful.
- **`banks_financials` v2 declares this for the "Banks" industry only**, which means
  deposit-taking banks: SIC 6020–6036 and the canonical label.
  - Calculations: cash conversion, capex/OCF, capex intensity, FCF conversion, FCF
    margin, dividend cover by FCF, net debt, net debt/EBITDA, operating margin, gross
    margin and cash runway.
  - Categories: EBITDA, EBIT, FCF, EV/EBITDA, current ratio, D/E, OCF and capex.
  - The base `cash_generation_and_funding` question is not asked.
  - `analysed_by`: book value per share and ROE (P/B with ROE).
- **Insurers are deliberately not covered (review of PR #308).** SIC 6311–6411 maps to
  "Insurance". That range holds managed care (UNH 6324), insurance brokers (MMC and AON,
  6411) and conglomerates (BRK, 6331). For those companies operating margin, FCF and net
  debt are the analysis.
  - The playbook still MATCHES them, because its questions are unchanged. The
    declaration does not, so V2 and V3 agree that they keep every generic metric.
  - The cash-generation supersession is part of the declaration, not a `replaces` on
    `funding_and_deposits`. A `replaces` would apply to every company the playbook
    matches, insurers and payments companies included.
  - "Diversified Financials" was dropped from the declaration: no classifier produces
    it. The `regulated_capital` signal was dropped too: nothing sets it.
  - Tests pin the real SIC codes of UNH, MMC, AON, BRK, PYPL, V, ICE, CME, BLK, SCHW,
    AXP and SOFI. Each keeps every generic metric.
- **One resolver.** `playbooks/applicability.py` resolves the declaration for one
  company into a `MetricApplicability`.
  - V3 resolves it from the run's own selection inputs.
  - The V2 agents and the council evidence pack resolve it from the snapshot profile.
    The profile goes through `resolve_classification`, so "National Commercial Banks"
    reads as Banks.
  - A metric is not meaningful only when every applicable non-overlay playbook
    declares it so. A conglomerate keeps it.
- **Every consumer filters through it. No consumer tests for a bank.**
  - **Planner:** drops the metric from `required_calculations` (recorded as
    `plan.not_meaningful_calculations`). It skips the declared base questions,
    including as carry-forward gaps (recorded as `plan.not_meaningful_questions`).
  - **Tools:** `ToolSession` and `ToolContext` carry the sets.
    - `get_sec_statements` and `get_peer_financials` neither compute these metrics nor
      hand out the lines.
    - `get_financial_facts` and `get_financial_series` exclude facts labelled OCF,
      capex, FCF, EBITDA and net debt. They are counted under the population reason
      `not_meaningful_for_business_model`. A bank's own "operating profit" is kept.
    - `get_calculated_metrics` neither computes, persists nor refuses them.
  - **Report:** the peer table has no such column.
  - **V2 council evidence pack:** strips the statement keys, the derived and defined
    metrics, and the historical-series lines for those labels.
  - **FinancialDataAgent:** neither expects nor reports them. Its
    `financial_context_summary`, which reaches the council, is written from the
    filtered fundamentals and says once that these metrics are not shown.
  - **`final_research_state`:** neither resolves nor opens them.
  - **Completeness schema:** drops the fields and asks `financials_deep` for the
    `analysed_by` fields instead.
  - **Valuation guard:**
    - records "DCF on free cash flow is not meaningful for this business model" as a
      blocker;
    - requires book value per share and ROE for relative valuation (labelled P/B, P/E);
    - no longer says EBITDA is unavailable;
    - does not count free cash flow among the core statement inputs that make
      readiness "partial".

    No platform source produces book value per share yet, so it is an honest missing
    input.
- **Total debt stays meaningful for a bank (after ADR-067).** A bank's total debt is a
  real, disclosed figure. For JPM it is 499,982: long-term debt including the current
  portion (435,206) plus `ShortTermBorrowings` (64,776). It is neither declared nor
  filtered, and it resolves as a financial category. What is not meaningful is net debt
  and everything derived from it: net debt/EBITDA and the EV-based industrial
  multiples. A test on real JPM companyfacts rows (a trimmed fixture with an identical
  normalised record) asserts all of this:
  - total debt is 499,982;
  - no net debt is computed or refused;
  - no valuation blocker mentions net debt;
  - `total_debt` is not an open category.
- **SIC wins over a vendor label for "Banks".** When a SIC code is present,
  `resolve_classification` never infers "Banks" from text. A broker-dealer under SIC
  6211 whose vendor label reads "Investment Banking & Brokerage" used to become
  "Banks" because of the word "banking", and so lost its metrics. Banks map from SIC
  6020–6036 directly. Every other keyword inference for an unmapped SIC is unchanged,
  because removing all of them would strip the playbook from every filer whose SIC
  the table does not map, which is wider than this slice. The resolver table says
  inference applies "only when no SIC code is available", and the code has never done
  that; the disagreement is left as a recorded limit.
- **Recorded once, as not meaningful, never as a gap.**
  - The record appears as `v3_research.not_meaningful_metrics`, with status
    `not_meaningful_for_business_model`, the playbook, reason, reader note, narrowed
    questions and skipped questions.
  - It also appears as `final_research_state.not_meaningful_financial_categories`, and
    as `not_meaningful_fields` on the agent and completeness summaries.
  - The investor sees the playbook's `reader_note` as `metrics_note` on the
    financial-capacity and competitive-position sections. The web renders it under the
    peer table. For a bank it reads: "Cash conversion, free cash flow, net debt and
    EBITDA are not shown: they are not meaningful for a deposit-taking bank…"
  - Every record is emitted only when non-empty, so every other company's payload is
    byte-identical.
- **JPM 30828043, replayed against the stored report:**
  - `open_financial_categories` goes from `[ebitda, ebit, free_cash_flow, ev_ebitda,
    dividend_yield, book_value_per_share, current_ratio]` to
    `[dividend_yield, book_value_per_share]`. `debt_to_equity` leaves the resolved list.
  - The two adverse findings behind the verdict ("cash conversion was -259.1%" and
    "operations consumed rather than generated cash") came from the cash-generation
    question, which is no longer asked of a bank. The platform's own statement, facts,
    calculation and council-pack paths no longer supply those figures.

### Not changed / known limits
- **OCF figures can still reach a model.** The figures themselves can still be read
  from corpus text, for example a 10-K cash-flow statement chunk returned by
  `search_company_corpus`, or from a web page. Only the platform's structured paths
  withhold them. The V2 snapshot also keeps the raw OCF line for non-council uses.
- No bank metric is added. NIM, CET1, NCO rate and provisions extraction remain open
  (JPM-11: blocking bank questions can be "satisfied" without the metric they ask for).
- **Book value per share is never produced.** It could be derived as an estimate from
  `shareholders_equity_usd_m / shares_outstanding_mln`, but this PR does not do that.
  The derivation needs the period of the share count matched to the balance-sheet date,
  and an estimate label. Until then the valuation guard reports it as missing, and P/B
  for a bank stays blocked.
- `balance_sheet_risk` is still asked of a bank. It requires no calculation, and on JPM
  it produced supplementary-leverage-ratio findings, which are capital facts. The same
  question produced "liabilities exceeding eleven times equity" (neutral). That
  sentence is not filtered.
- Net margin and ROE are still computed for a bank. Net margin is on net revenue and is
  commonly quoted, and ROE is what a bank is analysed by.
- **Insurers, brokers, managed care, exchanges, asset managers, card issuers and
  payments keep every generic metric.** An insurer's float makes its cash conversion
  hard to read too. That needs its own declaration, keyed on a narrower industry than
  SIC 6311–6411.
- A bank holding company filed under a non-bank SIC code is treated as a non-bank and
  keeps the metrics. Examples: AXP and SOFI under 6199.
- The V2 snapshot path depends on the provider's profile words. A bank whose profile
  carries neither a recognised industry nor a bank-like SIC description keeps the
  generic metrics in the V2 surfaces, though not in V3. The reverse case also exists:
  a V2 profile with no SIC code and a vendor label such as "Investment Banking &
  Brokerage" is still inferred to be Banks (T6 estimate). SIC wins only where a code
  is present.

---

## ADR-074: Another Company's Statement, Cited From Its Own Site, Is Not the Issuer's; a Change Is Not a Level

**Status:** Accepted (cross-sector QA campaign 2026-11, live Antofagasta report a68e6d31)

### Context
In the Antofagasta report, 10 of 35 findings were about other companies, and all ten
were web-only. Their sentence subject was another named company, and each cited that
company's own website. Examples: "South32 FY24 underlying revenue US$8,296m",
"South32 policy: distribute at least 40% …", "Lundin Mining flags Vicuña JV governance
risk …", "Ecora (not the company): leverage ratio of 1.5x …". The executive summary
then presented Lundin's JV risks as Antofagasta's. One of the ten sits in a peer domain
(`competitive_position`), where another company's facts are the answer, so it is kept
by design.

**Why the publisher firewall missed them.** `foreign_publisher` does not depend on
the issuer's domain. It is keyed on `other_company_labels`, which reads the
`companies` table: other companies the platform itself holds. South32, Lundin Mining
and Ecora had never been researched on the platform, so the firewall had no label to
match south32.net or lundinmining.com against. The firewall is correct for the
companies it knows; it simply knows only those.

A second defect followed from the first. A gap read "Sources disagree on revenue
(2024): US$8,296m (South32) vs $156.6m". The $156.6m came from "Copper sales volumes
… fell 2.9% …, reducing revenue by $156.6m". That is a CHANGE in revenue, and
`_money_disagree` (ADR-071) compared it as the revenue LEVEL. Neither `field_clause`
nor `money_values` tells a change from a level.

### Decision
1. **Foreign-subject guard** (`statement_about_foreign_subject`, in `_write_up`
   beside the other-company and related-registrant guards). It runs outside peer
   domains, on statements whose cited evidence is all web text, as ADR-066 does. A
   statement is dropped when all of these hold:
   - Its sentence subject is a proper-noun name. That is the name it opens with,
     optionally after the ADR-066 lead-in clause and an article; a possessive ends the
     name ("Lundin's 2026 outlook").
   - The name's first word is at least four characters, is not a generic business word
     (`identity._GENERIC_TOKENS`), is not all capitals (an acronym, "FY24"), and is not
     an attribution ("X-cited", "X-based"). It is not followed by "cites" or "quotes"
     (a publisher relaying someone else's figure).
   - The subject is named nowhere in the statement. The subject counts as named by its
     ticker (case-sensitive, whole token), by any of its distinctive name words, by a
     brand that `trust.BRAND_ALIASES` maps to it, or by "the company", "the group" or
     "the issuer".
   - EVERY cited item's verified publisher (`trust.publisher_of`, so a claimed origin
     counts as its real host) is that company's own domain. This uses
     `identity.issuer_domain_matches` (equality, corporate or venue TLD only), plus one
     extension: the first name word followed by one generic business word
     (lundinmining.com for "Lundin", ecora-resources.com for "Ecora").
   - No cited item is a curated third party's page (press, regulator, association,
     aggregator). Only `unknown_web` and the company-page classes qualify.
   - No cited page's text names the subject (a JV partner's page, a brand's page, a
     buyer's release).

   Separately, a statement in which the writer marks a name as not the subject is
   dropped: "<Name> (not the company / the issuer / the group / <subject>)". No domain
   match is needed for this, but the subject must not be named elsewhere in the
   statement. The parenthesis must name the subject AND NOTHING ELSE: "Underlying
   EBITDA (not the group's statutory profit)" or "Revenue (not the company's guidance)"
   contrasts a measure, not a company, and is kept (review round 1 found the first
   draft dropped exactly these). The disclaimed name passes the opening-name filters
   (no acronym, first word ≥ 4 characters, not generic), and is read from a bounded
   200-character window before the parenthesis, so the scan stays linear. Review round 2
   tightened it further:
   - "(not the group)" is no longer a disclaimer, because writers use it to set a
     parent-only or attributable figure against the consolidated group;
   - a name containing a measure or perimeter word (EBITDA, Revenue, Attributable,
     Holding, Company, Parent, Standalone …) is never read as a company;
   - a disclaimer cited from the subject's own site is kept.
   It accepts "(Not the company)", "(not the company itself)" and a trailing legal suffix
   ("Ostara Resources plc (not the company)").

   Each drop counts in `statements_about_foreign_subject` (a diagnostics key, pinned in
   `test_v3_investigator_diagnostics`). The question gets ONE `entity_ambiguous` gap per
   write-up that names the dropped companies.
2. **A change amount is not a level** (`trust.level_money_values`, used by
   `_money_disagree`). An amount is masked before comparison in either of two cases:
   - It follows "by", "up" or "down", or "a/an/the … increase / decrease / rise / fall
     / drop / decline / gain / reduction / … of".
   - It is followed by "increase", "decrease", "rise", "fall", "higher", "lower" and
     the like ("a $39.9m increase").

   A side left with no level amount never conflicts. "Rose by $300m to $7,900m" still
   states the level $7,900m.

   The optional "different sentence subjects never conflict" rule was not added. The
   live sentence's subject is implicit ("Copper sales volumes …"), so it would not have
   helped here. The guard in (1) now removes the other company's side at the source.

### Replay over the stored campaign reports
The replay used the stored statements, with persistence labels stripped. Per evidence
id, it took the origin and class from the report's `web_evidence`, falling back to the
finding's `acquisition_log` origins. Evidence TEXT is not stored, so the "cited page
names the subject" keep rule could not fire, and these counts are an upper bound.

| Report | Findings | Judged (non-peer) | Dropped |
|---|---|---|---|
| ANTO a68e6d31 | 35 | 33 | 9 |
| ADBE 7b401826 | 48 | 46 | 0 |
| CFR f722d620 | 40 | 36 | 0 |
| HON e50cb267 | 44 | 42 | 0 |
| HON2 f0be3c4f | 43 | 41 | 0 |
| JPM 30828043 | 50 | 48 | 0 |
| PYPL f031b73b | 37 | 37 | 0 |

The ANTO drops are South32 ×5 (revenue, manganese CIF sales, net debt, dividend
policy, rain), Lundin ×3 (outlook, Vicuña JV risk, named risks) and Ecora ×1 (by its
"(not the company)" disclaimer; its page was on ecoraroyalties.com). The tenth live
finding ("South32 describes itself …") is in `competitive_position` and is kept. No
Antofagasta statement is dropped.

A first draft also dropped ADBE "Trefis cites global creative software market …" and
"Trefis-cited …", and CFR "WWF-cited McKinsey estimate …". The attribution and
citing-verb exclusions were added for those three. The live revenue "conflict"
(US$8,296m vs "reducing revenue by $156.6m") no longer fires.

### Not changed / known limits
- **Subsidiaries and brands on their own domains.** A subsidiary or brand on its own
  domain whose page does not name the parent is dropped unless `BRAND_ALIASES` lists
  it. Venmo for PayPal and a Maison for Richemont are examples. The replay of PYPL and
  CFR dropped none, but that is no proof.
- **Research firms' estimates.** "<Firm> estimates the market at …", cited only from
  the firm's own site, is dropped. The industry figure is then lost, but a gap records
  it.
- **Misses, not false drops:**
  - acronym names ("BHP says …");
  - names shorter than four characters;
  - a foreign company in a later clause, or after more than one lead-in;
  - a foreign company's statement cited from a news site or from mixed sources;
  - a disclaimer that is not attached to a name.
- **Subjects that cannot be told apart.** A subject whose name has no distinctive word
  (all generic, or under four characters) fails open, and nothing is dropped.
- **Change wording.** Only the words listed above count as change wording. "Revenue
  grew $5m" (a bare verb with no "by") is still read as a level.
- **The publisher firewall is unchanged.** It still knows only the `companies` table.
  The new guard covers companies outside that table only when they are the sentence's
  subject.

## ADR-075: An Issuer Files With the SEC When Any Verified Line of It Does

**Date:** 2026-11-02 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11, live Cellectis report `1df9100c`. The job ran
on the company row `ALCLS` / Euronext Paris. Cellectis S.A. is also an SEC registrant: CIK
1627281, ADS `CLLS` on Nasdaq, filing 20-F and 6-K. The pipeline treated it as non-SEC:
- `ensure_core_filings` skipped with "not an SEC registrant";
- classification failed with `SecExchangeNotSupportedError`, so no playbook was applied;
- `facts_read=0`, no 20-F or 6-K was read, and the newest period was Q1 2024;
- the chair said "no FY2024 results available" (P0).

### Context
Every SEC step decided "registrant or not" from `is_sec_eligible(company.exchange)`, i.e.
from the line the job started on. ADR-061 separated the issuer from its securities in
discovery, but nothing persists those links:
- no company-creation path sets `companies.legal_entity_id`: not candidate promotion, not
  `ensure_company` (the manual-ticker and discovery path), and not `POST /companies`;
- the backfill is "called by nothing", and when run it writes only the row's own listing;
- the discovery identity's secondary securities live in the candidate's payload only.

So the live `ALCLS` row has no linked `CLLS` listing, and a legal-entity lookup alone could
not have fixed it.

### Decision
1. **One resolver**, `entities.sec_listing.sec_listing_for(session, company)`, returns the
   ticker, exchange, CIK and basis the SEC knows the company by, or None. In order:
   1. **The company's own line** when it is SEC-eligible. This is unchanged: no lookup.
   2. **A listing of the same legal entity** with all of these:
      - `security_listings` under `companies.legal_entity_id`;
      - an open window, not delisted or suspended;
      - an SEC-eligible venue (an unnamed venue is not eligible).

      It counts only when verified: either the entity holds an open CIK identifier that
      the SEC list does not contradict, or the SEC list carries that ticker under a name
      that agrees with the entity's name (the ticker-anchored rule). This step requires
      `V3_ENTITY_MASTER_ENABLED`.
   3. **The SEC company-tickers directory**, only when all of these hold:
      - the company has a real name (not its ticker);
      - its country (the row's, else its venue's) is known and is not the US;
      - exactly one CIK in the directory carries the **exact** same normalised name (legal
        suffixes dropped, exchange abbreviations expanded). This is ADR-061's name-only
        rule; the prefix branch of `_names_agree` is not used;
      - that registrant's own submissions record states a domicile equal to the company's
        country. The incorporation country is used, else the business address. A US
        state code counts only when the business address is not foreign (ADR-061 §4,
        the Shell `DC` trap).
2. **Wired into** `ensure_core_filings` (the resolved CIK is passed to the provider, and
   `core_filings.sec_basis` records the basis, e.g. "SEC filings via CLLS·NASDAQ (…)"),
   classification by SIC, and `get_recent_filings`. The tool uses the resolved line only
   when the ticker and venue asked for are the session subject's own line, resolved by id.
   A model cannot use it to reach another issuer.
3. A namesake is never read as the issuer:
   - several CIKs with the name are refused before any record is read;
   - a different stated domicile is refused;
   - an unreadable directory or record gives no link.

### Replay (real SEC directory 2026-11-02; cached submissions)
- `ALCLS`/PA "CELLECTIS", France → one hit, Cellectis S.A., CIK 1627281, incorporated in
  France with a French business address → **CLLS·NASDAQ, CIK 1627281**.
- Orion Oyj (Helsinki) → Orion S.A. and Orion Group Holdings → ambiguous, not used.
- UBS Group AG (SIX) → UBS Group AG and its registered subsidiary UBS AG → ambiguous, not
  used.
- Tetra Technologies → TETRA TECH INC ("tech" expands) and TETRA TECHNOLOGIES INC →
  ambiguous, not used.
- Compass Group PLC → "Compass, Inc." (the suffix "Group" is stripped). This reaches the
  domicile gate. Its record was not fetched for the replay, and an unread record gives no
  link. A US record would also refuse it.
- Synthetic Paris namesakes of ADOBE INC., PayPal Holdings and JPMORGAN CHASE & CO → US
  domicile, not used.

### Not changed / known limits
- **`QUARTERLY_FORMS` is still only 10-Q.** A 20-F filer's interim results come on a 6-K,
  so for one the quarterly slot reads "not_listed". The 6-K that carries the results is not
  yet singled out. ADR-068 does this for 8-K Item 2.02.
- **Not wired:**
  - the V2 `company_analysis` SEC fundamentals and recent-filings legs;
  - the SEC connector;
  - `company_evidence`'s SEC document leg;
  - the peer-financials subject;
  - jurisdiction source classes;
  - the investigator's related-registrant guard.

  SEC XBRL normalisation reads `us-gaap` only, so an IFRS 20-F filer would gain nothing
  from the facts path until `ifrs-full` is supported.
- A holding domiciled outside its listing country gets no link through the directory
  step: Ferrari or Stellantis on Milan (Dutch N.V.), Aegon (now Bermuda). This fails
  safe.
- Discovery's verified `securities` are still not persisted to the entity master. Doing
  so would make step 2 the live path.
- Review (PR #311), not blocking:
  - The SEC directory loader (`parse_sec`) drops OTC rows, so the uniqueness check
    cannot see an OTC namesake. Rio Tinto Ltd (CIK 887028) is invisible next to Rio
    Tinto plc. The registrant-domicile check is what refuses such a link today. Counting
    OTC rows when judging ambiguity is a follow-up.
  - The name scan is linear over about 7.7k rows (around 68 ms per call, once per
    research run). It should become a daily name index.
  - The V2 classification call (`company_analysis`) goes through the same resolver, by
    way of `classification/service.py`.
  - The display ticker is the first one alphabetically.

---

## ADR-076: A Recency Question Reads the Newest Documents the Platform Holds

**Date:** 2026-11-02 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11, hypothesis C ("acquired but not used"),
seen in 3 of 8 live reports:
- **PYPL:** the Q3 EX-99.1 earnings release was read, yet "headline figures of the latest
  period" ended `not_acquired`;
- **LIN (Lindian Resources, ASX):** the Sep-2026 Appendix 5B (26 chunks) and quarterly
  report (41 chunks) were in `core_disclosures`, state ready. The report still quoted the
  30-Jun cash figure and said no quarterly report was present;
- **CLLS:** an ingested pivot article was never used.

### Context
Three defects, verified on `09836d0`:
1. `recent_external_developments` (the headline-figures question) offered only
   `search_web` and `fetch_public_source`, with the default contract (no external research)
   and no `search_intents`. `_gather` never called `search_company_corpus`, and the
   external rung stopped `contract_does_not_allow_external_research`. Its log read
   `tools: [], citable_items: 0`.
2. `recent_disclosure` had no `search_intents`, so `_corpus_arguments` fell back to the
   question's prose. PostgreSQL ranks by `ts_rank_cd` alone and nothing sent
   `published_from`, so a 261-chunk annual report outranked the newer 5B on every run.
3. `director.loop._record_unresolved` stamped `not_acquired` from the question's empty
   evidence list, not from what the corpus holds.

### Decision
1. **Corpus access for the headline-figures question.**
   `planner.EXTERNAL_QUESTION_DETAILS` gives it:
   - `search_company_corpus` as an **optional** tool. It never decides assignment, so the
     external role still answers and its web search is unchanged;
   - an evidence contract that allows external research, with one search. The external
     role holds no acquisition ladder, so this buys no extra search;
   - search intents naming the latest-period results: "quarter results revenue reported",
     "quarterly activities cash flow report", "results release".

   The same definition is restored when the question is re-asked from a carried-forward
   gap. `recent_disclosure` gets real intents too.
2. **An additive recency pass.** For `base_model.RECENCY_QUESTION_KEYS`
   (`recent_disclosure`, `recent_external_developments`, `upcoming_catalysts`), the
   investigator runs ONE extra corpus search after the platform rung:
   - The ordinary search is not changed and nothing is filtered out of it.
   - The new tool argument `recent_window_days` (1–366) restricts the extra search to
     documents published within 45 days of the subject's newest **held dated** document.
     The anchor comes from the corpus, never the clock. Both backends gained
     `newest_published_at(filters)`; `corpus.retrieval.newest_published` calls it.
   - Its hits are ordered newest first and capped at 4 (`RECENCY_MAX_ITEMS`). They are
     merged at the **front** of the question's evidence, de-duplicated, so the unchanged
     prompt budget reaches them.
   - It is logged as the `recency_corpus` acquisition step: query, window, newest held
     date, items. `domain_cost` counts it as a corpus query. It is not an intent, so
     `corpus_intents_in` does not skip one.
   - **Undated chunks:** without `include_undated`, a date window excludes NULL-dated
     chunks in both backends (`CorpusFilters._passes`, `postgres._filter_clauses`). This is
     why the pass is additive: the ordinary search sends no window and still returns
     them. With no dated document held, no window is anchored and the pass returns
     nothing; it says so instead of guessing.
3. **An honest unresolved reason.** A new closed-vocabulary value, `acquired_not_read`
   (`ledger.UNRESOLVED_ACQUIRED_NOT_READ`). It applies when a recency question ends
   unsatisfied and retrieved nothing it could use, while the subject's own indexed corpus
   holds a CURRENT dated document: its newest `published_at` is within
   `CURRENT_HOLDING_MAX_AGE_DAYS` (120) of the run. A newest holding older than that
   means the latest disclosure was never fetched, so the reason stays `not_acquired`
   (review round 1: one held 2024 document had made all three recency questions say
   "held but not read"). A question that retrieved evidence but did not cite it is
   judged by its contract as before.
   - The lookup is `loop.newest_held_document_date`. It runs once per run, in a SAVEPOINT,
     and any failure falls back to `not_acquired`.
   - Consumers checked:
     - the `research_questions.unresolved_reason` column is `String(40)` with no CHECK
       constraint, so no migration is needed;
     - `update_question_graph_state` validates against `UNRESOLVED_REASONS`, which now
       includes the new value;
     - `professional_research` passes the value through and counts it in
       `unresolved_by_reason`;
     - the web's `UNRESOLVED_REASON_WORDS` gains a reader wording ("held by the platform
       but not read for this question"). The unknown-key fallback would otherwise have
       printed "acquired not read".

     The gap `knowledge_state` taxonomy (`not_acquired_by_platform` …) is a separate field
     and is unchanged.

### Tests
- `tests/test_qa_campaign_recency_evidence.py` (24):
  - the planner question's corpus tool, contract and intents, including on a re-ask;
  - a fake corpus where a 60-chunk annual report out-matches a newer 5B. A non-recency
    question never reaches the 5B, while the recency question's evidence is led by it;
  - NULL-dated chunks are still returned by the ordinary search and by the question;
  - the window argument's bounds;
  - `acquired_not_read` versus `not_acquired`, with and without a held dated chunk.
- The pinned plan snapshot `fixtures/playbook_plans_before_overlay.json` was regenerated.
  Only `recent_disclosure` and `recent_external_developments` changed (intents, optional
  tool, contract); tasks and assignments are byte-identical.
- `test_a_satisfied_contract_never_spends_a_search` now expects the `recency_corpus` rung.

### Offline replay (LIN)
The 12 LIN ASX PDFs were extracted with the platform extractor and chunker into the
memory backend. Chunk counts match live: annual report 261, 5B 26, quarterly report 41.
Each was dated with its ASX announcement date, and the acquisition ladder ran with a web
stub. Results:
- **Before (`09836d0`):** `recent_external_developments` made 0 corpus calls and had 0
  evidence items. `recent_disclosure` searched the raw prose and got 2 low-ranked 5B
  chunks and 0 quarterly-report chunks.
- **After:**
  - Both questions' evidence is led by 2 × 5B and 2 × quarterly-report chunks. The window
    was 2026-09-11 → 2026-10-26, anchored on the newest held document, 26 Oct.
  - This includes "At the end of the quarter, the Company had $45m in cash" for the
    30-Sep quarter.
  - `recent_external_developments` now carries 11 items, previously 0.

The memory backend ranks by BM25, not `ts_rank_cd`, so the "before" ordering is not a
byte-for-byte replay of PostgreSQL.

### Not changed / known limits
- The recency pass ranks within the window lexically. A recent document whose chunks
  match none of the intent terms is still not read: here `upcoming_catalysts` read the 26-Oct operations update but
  neither the 5B nor the quarterly report.
- The window anchors on the newest held **dated** document of the subject's own company
  row. A newer web article about the subject (a subject-row hit) does not move the anchor
  and is not in the pass.
- `acquired_not_read` is limited to the three recency questions. For other questions an
  empty answer beside a held document is not evidence that the document answers them.
- CLLS's pivot article is covered only when it is the subject's own dated chunk in the
  window. Subject-row (`via_subject`) admission is not part of the pass.
- No live run yet. Production acceptance needs a LIN and a PYPL rerun after deploy.

### Amendment 2026-11-02 — ADR-076 review: anchor
**Trigger:** live Cellectis rerun `df36d6f6`. The `recency_corpus` steps logged
`newest_held: 2026-11-02` (the run day), so the window started on 2026-09-18. The only
holding dated that day was a Boursorama Sanofi quote page (`unknown_web`, with its date taken
from the page text). The window therefore missed the H1 2026 6-K/A (filed 2026-09-11) and
the 2026-09-14 pivot article, which are the documents the pass exists to surface.
`loop.newest_held_document_date` had the same flaw: a quote page dated today made the
subject look like it "held a current document".

**Decision.**
1. **Only an issuer or primary disclosure anchors the window.** There is one rule,
   `corpus.search.types.is_disclosure`, exposed as the filter
   `CorpusFilters.disclosures_only`. It uses columns the corpus already has:
   - the chunk's `source_tier` is in `DISCLOSURE_SOURCE_TIERS`:
     - `T1_primary_filing` (issuer or regulatory filings);
     - `T1_primary_company_source` (issuer IR material and releases);
     - `T2_regulator_or_gov` (venue announcements from the NSM, Euronext, Nordic and SIX
       connectors);
   - and EITHER the version has no open-web `source_class` (a connector or bridge
     document: the SEC bridge, ASX, NSM, Euronext …), OR it is the issuer's own web
     page: class `company_press_release` or `investor_presentation` with access class
     `public_issuer`. Those are exactly what `classify._classify_host` assigns under
     `rule='issuer_domain'`. A PR-wire release is re-labelled `public_web` and does not
     qualify.

   Quote, market-data, aggregator, `unknown_web`, generic `company_web_page`, press and
   government pages never anchor the window, whatever their tier.
   - The memory backend applies the filter in `CorpusFilters._passes`, through
     `is_disclosure` / `is_disclosure_fields`.
   - PostgreSQL applies it in `_filter_clauses`: a tier `IN`, then either no web class
     (`NOT EXISTS`) or an issuer class together with the chunk's `public_issuer` access
     class.
   - `retrieval.newest_published` sends the filter.
   - `loop.newest_held_document_date` now calls `PostgresSearchBackend.newest_published_at`
     with the same filter, so `acquired_not_read` and the window share one definition.

   Only the **anchor** is restricted. The window still searches every subject chunk dated
   on or after its start, including web pages and the pivot article.
2. **The window is 75 days, not 45** (option (a)). Anchoring on disclosures alone fixes
   this run's anchor, but the next miss is a minor disclosure filed weeks after the
   results. Cellectis's 2026-10-27 data release is 46 days after the 6-K/A, so a 45-day
   window anchored on it starts on 2026-09-12 and misses the 6-K/A by one day. A 75-day
   window starts on 2026-08-13 and includes both the 6-K/A and the pivot. It still stops
   short of the previous quarter's results, about 91 days back; LIN's 2026-07-30 annual
   report, 88 days before its 5B, stays outside.

   Option (b), the newest N disclosure documents, would need per-document grouping in both
   backends. It would also not reach the pivot, because a trade article is not a
   disclosure. It is the larger change and does no better here.
3. Everything else in ADR-076 is unchanged: the additive pass, NULL-dated chunks outside a
   window, `acquired_not_read` and the 120-day staleness rule.

**What this does NOT fix in CLLS3.** The 6-K/A and 20-F chunks are **undated**:
`corpus.filing_evidence` never sets `published_at` from the filing date (CLLS2-5).
Cellectis's issuer pages are also classed `unknown_web` (CLLS-5). Under this amendment, the
CLLS3 corpus therefore holds no dated disclosure, so the pass anchors no window and says
so ("no dated issuer or primary disclosure is held"). It no longer runs a window anchored
on the quote page. The 6-K/A reaches the pass only once core-filing chunks carry their
filing date. The follow-up below adds that date.

**Review of PR #316 (D1–D4).**
- **D1 (blocker): web exchange and regulatory pages never anchor.**
  `classify._classify_host` classes EVERY page on an exchange host as
  `exchange_announcement` at T1. That includes quote pages and news articles:
  nasdaq.com/market-activity/stocks/clls, nasdaq.com/articles/…, an LSE company page,
  asx.com.au/markets/company/lin and an otcmarkets.com quote. A nasdaq article dated the
  run day would have recreated the df36d6f6 failure.
  - The fix is in the anchor predicate (above), not in the web classifier, which would be
    a wider change.
  - Web `exchange_announcement`, web `regulatory_filing` (a web sec.gov copy can be
    another registrant's filing, ADR-079) and `issuer_filing` never anchor.
  - The parity test now covers these five URLs, the PR-wire release and every
    `CLASS_TABLE` class in both backends.
- **D2: disclosures rank first in the recency pass.** Before harvesting (which caps the
  items it reads), the hits are ordered with disclosures first (`is_disclosure_fields`),
  then newest first. Two newer web pages of two chunks each no longer fill
  `RECENCY_MAX_ITEMS` ahead of the 6-K/A. The step logs `disclosure_items`.
- **D3: a named stop.** With no dated disclosure, the tool's `recency_window` and the
  `recency_corpus` step carry `stopped: "no_disclosure_anchor"`.
- **D4: no in-memory backfill.** `search.factory.get_search_backend` builds a NEW
  `InMemorySearchBackend` on every call, so there is no shared index to backfill. Any
  index built after a backfill reads the stamped rows, because `index_version` copies
  `published_at` from them; a test pins this. A fresh acquisition is stamped before it
  is indexed.

**Filing dates on SEC corpus chunks (same amendment, CLLS2-5).**
`ensure_filing_corpus_evidence` takes a `filing_date`: the SEC filing date, never the
period end.
- The callers pass what they already hold:
  - `ensure_core_filings` passes the EDGAR submissions `filingDate` of the 10-K, 20-F,
    40-F or 10-Q event;
  - the ADR-077 results-6-K slot passes the selected 6-K's index-row `filing_date`;
  - the `get_recent_filings` tool bridge passes each item's `filing_date`. This is also
    the bridge route by which an 8-K reaches the corpus.
- `filing_evidence.stamp_filing_date` sets `published_at` on the version and on its
  chunks, with an `UPDATE … WHERE published_at IS NULL` in a SAVEPOINT. It never
  overwrites a recorded date, is idempotent and never raises.
  - It runs whenever a filing is found searchable: on a reuse, which backfills filings
    first stored undated without a migration, and after an acquisition.
  - `acquire_sec_filing` also stamps the version **before** `index_version`, so a
    backend that copies the date at index time (the memory backend) carries it too.
  - The acquirer receives `filing_date` only when one is known, so an acquirer written
    before this change still works.
- Scope:
  - Only the corpus version and its chunks are dated. The V2 `ExtractedDocument.doc_date`
    is left alone, because `issuer_financials` orders by it.
  - SEC documents persisted outside the bridge, by the legacy council's
    `company_evidence` primary-document path (including its ADR-068 8-K EX-99.1
    selection), are still undated until the bridge meets them.
  - Period logic runs only at version creation, so a later stamp cannot change a
    period.
- Tests (`tests/test_v3_filing_corpus_bridge.py`, plus one PostgreSQL case in the
  recency file):
  - an acquired 6-K/A with filing date 2026-09-11 is dated and anchors the window over a
    quote page dated today;
  - the window includes it, end to end;
  - an undated held filing is backfilled, and a recorded date is never overwritten;
  - the date is stamped before indexing;
  - the filings tool dates a held filing.

**Tests** (`tests/test_qa_campaign_recency_evidence.py`, 41 run on SQLite plus 2 on
PostgreSQL):
- a quote page dated today plus a 6-K dated 52 days earlier: the window is anchored on the
  6-K and includes it, through both the tool and the investigator pass;
- a 6-K plus a data release 46 days later: the 75-day window includes the 6-K and the
  pivot, and a 45-day window would not;
- with no dated disclosure, no window is anchored, even beside a dated quote page;
- a `company_web_page` does not anchor the window, and a `company_press_release` does;
- the disclosure vocabulary is pinned to the taxonomy and to `classify.CLASS_TABLE`;
- the loop: a quote page, or a T1-tier `company_web_page`, leaves `not_acquired`;
  `newest_held_document_date` skips the quote page.
- PostgreSQL (`V3_TEST_POSTGRES_URL`, a UTF8 scratch database migrated to 044 and dropped
  afterwards): the same quote page plus 6-K case through `PostgresSearchBackend`, the tool
  and the loop. A parity test checks that `_filter_clauses` and `is_disclosure` admit the
  same chunks for every web class.

---

## ADR-077: A 20-F Filer's Interim Results Are Read From Its Results 6-K

**Date:** 2026-11-02 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11, live Cellectis rerun 686d71f6.
ADR-075 now reads the FY2025 20-F of a foreign SEC filer. But `ensure_core_filings`
looked for a 10-Q only (`QUARTERLY_FORMS = ("10-Q",)`). A 20-F / 40-F filer reports
its quarter or half year on a Form 6-K, often with an EX-99.1 press release and
sometimes an iXBRL 6-K/A. So `current_period` said "No interim report acquired", and
the newest figures in the report were 9M 2025. H1 2026 (6-K 2026-08-06, iXBRL 6-K/A
2026-09-11) was never read. This affects every 20-F filer.

### Context
- EDGAR records no item codes for a 6-K, so ADR-068's Item 2.02 rule cannot apply.
- Most 6-Ks are not results. Examples: Novo Nordisk's weekly buyback notices, Shell's
  dividend and own-share notices, Cellectis's AGM vote results and its clinical-data,
  strategy and conference-call releases.
- The `-index.htm` descriptions are generic ("PRESS RELEASE", "EXHIBIT 99.1",
  "6-K"), so a description rarely names results.

### Decision
1. **When.** The quarterly slot reads 6-Ks only when no 10-Q is listed, the annual
   slot is not a 10-K, and 6-K / 6-K/A filings are listed. Only a foreign private
   issuer files a 6-K. A 10-K filer is unchanged. **Only 6-Ks filed AFTER the annual
   slot's filing date are considered** (review of PR #313, D3): a Q4 iXBRL 6-K filed
   2026-02-05 before Barrick's 40-F of 2026-02-27 reports the same year, and must not
   become "the interim report for the period ended 2025-12-31". None after it gives
   "no results 6-K since the annual report of <date>…". `current_period` independently
   refuses a 6-K slot filed on or before the annual report. With
   `PRIMARY_DOCUMENT_SEC_BODY_ENABLED` off, no index page is read and the slot is
   `not_listed` with "SEC filing-body fetch is disabled".
2. **Which 6-K** (`sources/sec_6k_results.select_results_6k`). It walks 6-Ks newest
   first and takes the first one whose own documents show it is a results release.
   The rules, in order (`classify_6k`):
   1. **iXBRL statements.** A 6-K / EX-99 document marked iXBRL on the index page
      means results. A 6-K has no iXBRL cover requirement, so iXBRL here is financial
      data. `parse_filing_index_page` now records `ixbrl` per row.
   2. **Results by description or headline title line** (below).
   3. **A notice by description or headline line** (below).
   4. **Results by file name — per document.** Only a document whose own name is not
      a notice counts, so one exhibit's name ("…earningscallpresentation…") never vetoes
      another document that names results (review round 2, R1).
   5. **A notice by file name**: AGM, vote, meeting, prospectus, buyback, repurchase,
      own shares, share capital, invitation, liquidity. `6-ka_agm_vote_results.htm` is a
      notice: "results" alone is no results evidence (rule 4). A 6-K judged a notice
      by name alone still has its headline read, within the cap.

   Details of rules 2–4:
      - The headline is read when the 6-K is not yet results. It comes from the
        press-release exhibit's first 400 characters, or, when that exhibit is over
        256 KB or absent, the 6-K document's first 6,000 characters (its exhibit list
        or the newswire announcement).
      - A title line is at most 200 characters, so a forward-looking-statements
        paragraph that mentions "financial results" is never a title.
      - **Announcements veto a line even beside a results phrase** (review D2, refined
        in rounds 2 and 3): "to / will report, announce, release, publish, host, hold",
        "invitation", "date of / for", "schedule(s/d)", "results / earnings (conference)
        call", "release / results / reporting date", and a future date month-first ("on
        November 5") or day-first ("on 5 November"). These are lifted only when the line
        itself REPORTS ("reports", "reported", "posts", "delivers"); "announces" does not
        lift them ("Announces Q3 2026 Results Conference Call on November 5"). "Liquidity
        contract", "availability of", "publication of" and "report on the share buyback
        / liquidity / own shares" are never lifted.
      - **A full-year title is not the interim slot's** (round 3, R4): a results line
        with a year and no part-year token (quarter, Q1–Q4, H1/H2, half, interim, three /
        six / nine months, "for the period"), or a full-year / annual / fiscal-year line
        without one ("Publication of the 2025 Financial Report", "Annual Results 2025").
        So "Cellectis to Report Third Quarter 2026 Financial Results … on November 5",
        "Half-Year Report on the Liquidity Contract", "Availability of the Half-Year
        Financial Report", "Invitation to conference call on Q3 2026 results" and
        "Half-year report on share buyback" are notices.
      - **Meeting, vote, buyback, voting-rights, share-capital and prospectus words
        veto only a line with NO results phrase.** A call, webcast or "host" vetoes
        nothing. So "Q3 2026 results and conference call details", "Half-year results
        including share buyback update", "Acme Reports Third Quarter 2026 Financial
        Results; Management to Host Conference Call…" and "Q3 2026 results - webcast
        at 9am" stay results (round 1 had wrongly refused them, R1).
      - Results phrases: "financial results/report" (not "annual financial report"),
        "interim / half-year / quarterly report or statement(s)", "earnings release".
      - A period next to "results" also counts (Q1–Q4, H1/H2, first/second … quarter,
        half-year, first half, three/six/nine months), but not in a line about a trial
        (topline, phase, study, clinical, data).
      - A headline naming a notice and no results means a notice.
      - File names name results **only through a period** (review D1): `q[1-4]`, `h1`/`h2`,
      half-year, first half, interim, quarter, or fy / full-year next to results
      (`clls_6-k_q2_2026.htm`, `caq22026.htm`, `shellq320266-k.htm`). A bare "results",
      "earnings" or "financial statements" is not evidence, and a name with topline,
      trial (not "industrial"), phase 1–3, clinical or study never is (round 2: "data"
      is not clinical, `q3-2026-supplementarydata.htm`): BioNTech's
      `form6-ktpamtoplineresultsb.htm` (a Phase 3 topline release, 2026-10-26) had been
      picked over its Q2 report.

   Anything else is **undecided and never taken**. Reading no 6-K is better than
   reading the wrong one.
3. **A positive content check** (review round 3, the structural fix). A pick made on a
   description, a headline or a file name — anything but iXBRL statements — must also
   show in the chosen document's OWN text that it carries interim results:
   - at least 8,000 characters of visible text, and
   - at least 3 distinct statement line items, each followed by a figure within 150
     characters: revenue / sales / turnover; net loss / income / profit; operating
     result / EBIT(DA) / profit before tax; cash and cash equivalents / free cash flow;
     total assets / equity / liabilities; earnings or loss per share; R&D / operating
     expenses / cost of sales / gross profit.

   The document's first 256 KB are read (`fetch_document_head`, now through a
   truncating mode of `_fetch_sec_archive_bytes`: the rest is never downloaded); a
   headline already read is reused. At most 3 content reads per selection
   (`MAX_CONTENT_FETCHES`). A failure is logged per 6-K (`content_failed`) and the walk
   continues; the reason counts it ("N named results but carried no statement
   figures"). A call notice or a date announcement fails by construction. The
   reviewer's "≥ 15 KB of text" example was lowered to 8,000 characters on evidence:
   ASML's Q3 2026 release (EX-99.1, 41 KB of HTML) is 12,129 characters of text.
   The regexes run over capped text with bounded gaps; an adversarial 256 KB body
   checks in ≤ 0.16 s.
4. **Period after the annual report** (round 3, R4). When the pick's period is known —
   the iXBRL period end, or a period in its title — it must be after the annual
   report's fiscal period end (EDGAR's period of report for the 20-F / 40-F): an exact
   date must be later; a title period of an earlier year is refused, and of the same
   year when the annual period ends in December. Unknown on either side passes — the
   check refuses, it never guesses. Counted as "N reported a period not after the
   annual report's".
5. **Honest about failures** (review D4, R2). An empty `-index.htm` read is a failed
   read (a real page always lists the complete-submission file). Such a 6-K is
   SKIPPED — never taken on its feed file name, because which document carries the
   results is unknown (on an outage Shell's 12 KB cover would have been read instead
   of its 1.8 MB EX-99.2). Failed index pages and headline reads are counted
   (`index_unreadable`, `heads_unreadable`) and named in the reason, so an outage is
   not reported as "examined and found wanting".
6. **Bounded.** Reads use the existing guarded, throttled SEC Archives leg
   (`fetch_filing_index_page`, `_fetch_sec_archive_bytes` with the 256 KB cap and the
   `sec_6k_headline_*` events). There is no new host, no new provider, and no URL
   that does not come from the accession. Per selection:
   - at most 6 index pages (`MAX_INDEX_FETCHES`);
   - at most 4 heads (`MAX_HEADLINE_FETCHES`);
   - at most 4 6-Ks without a feed signal (`MAX_UNSIGNALLED_VISITS`). A feed signal
     is a results-named primary document, or a period of report in the last week of a
     month 10–200 days before filing. With this cap a weekly-buyback filer still
     reaches its interim report;
   - a 6-K whose feed file name names a notice costs nothing.
7. **The document read.**
   - With iXBRL as the basis: the iXBRL EX-99 exhibit, else the iXBRL 6-K document.
   - Otherwise, as ADR-068: the `EX-99.1` exhibit, else the first `EX-99*`. If that
     exhibit is under 10 KB and a larger EX-99 exists, the larger one is read (Shell's
     3 KB EX-99.1 sits beside a 1.8 MB quarterly report).
   - With no exhibit: the 6-K document.
8. **Through the same bridge.** `ensure_filing_corpus_evidence(…, document_name=…)` →
   `acquire_sec_filing(document_name=…)` passes the name as the resolver's
   primary-document hint, which re-validates it against this accession's index. It is
   never a URL. Readiness and indexing are judged on that one document
   (`<accession>/<file>`, escaped, URL must end with it). An exhibit asked for by name
   counts as ready. A held cover page of the same accession cannot answer for it. The
   unnamed path is unchanged.
9. **Slot output.** It records `form` (6-K / 6-K/A), `filing_date`, `document`, `basis`,
   `title` (the document's own results title line, when read), `period_end` and
   `selection` (counts, failures, 6-Ks before the annual report, `since`). `period_end` is EDGAR's period of report, given only when the
   basis is iXBRL: for financial statements it is the balance-sheet date. Nothing
   qualifying gives `not_listed` with "no results 6-K among the N examined of M listed
   (K skipped as notices by file name)".
10. **current_period.** A ready 6-K slot counts as the interim report. The headline is
   the slot's `title`, else its form. The period comes only from that title
   ("…Second Quarter 2026" gives "Q2 2026 quarterly report acquired"), or the label
   states "for the period ended 2026-06-30". A lone year in a 6-K title is never read
   as a fiscal year. `gap_reconciliation.ready_documents` already maps a ready
   quarterly slot to an interim document.
   Review round 4 found the floor refused real short releases: TSMC's Q3 2026 EX-99.1 is
   3,764 characters with 5 statement line items, and it was refused, so TSMC got no
   pick. When an older results 6-K was within the caps, that one was picked and
   labelled current instead. Now ≥ 4 distinct line items with figures in ≥ 1,500
   characters qualify at any length. The 8,000-character / 3-item rule remains the
   second route.

### Live replay (read-only, 2026-11-02)
**The picks rest on this replay**, not on a guarantee: the rules are text rules over
what issuers happen to name and write. User-Agent "InvestingBuddy research
research@investingbuddy.example". First round 32 SEC requests; review round 1, 5 more;
review round 2, none (every response cached from earlier rounds); review round 3, 11
(the content reads). After round 2 the
eight replayed filers (the five below plus BioNTech, Barrick, Valneva) pick exactly
what round 1 picked. After the review fixes:

Round 3 (content check on every non-iXBRL pick):

| Filer | 6-K picked | Document · basis | Content check |
|---|---|---|---|
| Cellectis | 6-K/A 2026-09-11 | `clls-ex99_1.htm` · iXBRL, period end 2026-06-30 | n/a (iXBRL) |
| ASML | 2026-10-14 | `pressreleasefinancialresul.htm` · file name | pass: 5 line items, 12,129 chars |
| Novo Nordisk | 2026-08-04 | `caq22026.htm` · file name | pass: 6 items, 24,033 chars |
| SAP | 2026-10-26 | `tm2628113d1_ex99-1.htm` · headline | pass: 6 items, 21,324 chars |
| Shell | 2026-10-29 | `quarterlyexhibit992-q32026.htm` · file name | pass: 6 items, 37,978 chars |
| BioNTech | 2026-08-04 0001776985-26-000054 | `a991bntx_q22026pressreleas.htm` · file name | pass: 7 items, 32,716 chars (the same-day 000055, 7,939 chars, failed and was passed over) |
| Barrick | 2026-08-11 | `d67611dex992.htm` · iXBRL, period end 2026-06-30 | n/a (iXBRL) |
| Valneva | 2026-08-13 | `valn-20260630.htm` · iXBRL, period end 2026-06-30 | n/a (iXBRL) |

Rounds 1–2 (before the content check):

| Filer (CIK) | 6-K picked | Document · basis | Latest interim? |
|---|---|---|---|
| BioNTech (1776985) | 6-K 2026-08-04 0001776985-26-000055 | `bntxq22026ex991quarterlyre.htm` · file name | Yes, Q2 2026 (Q3 due 3 Nov); the 2026-10-26 topline release is refused |
| Barrick (756894) | 6-K 2026-08-11 0001193125-26-343857 | `d67611dex992.htm` · iXBRL · period end 2026-06-30 | Yes, H1 2026; the pre-40-F Q4 6-K is never considered |
| Valneva (1836564) | 6-K 2026-08-13 0001628280-26-056525 | `valn-20260630.htm` · iXBRL · period end 2026-06-30 | Yes, H1 2026 |
| HSBC (1089113) | 6-K 2026-10-27 0001089113-26-000030 | `livedocq32026earningsrelea.htm` · file name | Yes, Q3 2026 (annual slot cut by `max_events`, so no `since`) |
| Philips (313216) | 6-K 2026-07-28 0001628280-26-049913 | `a2026q2-philipsquarterlyen.htm` · file name | Yes, Q2 2026 (newest listed 6-K) |
| TSMC (1046179) | 6-K 2026-10-15 0001046179-26-000726 | `a3q26e_withguidancexfinal.htm` · file name | Yes, Q3 2026 |
| argenx (1697862) | 6-K 2026-10-22 0001697862-26-000034 | `a02earningspressreleaseq3.htm` · file name | Yes, Q3 2026 |
| Toyota (1094517) | none — "no results 6-K since the annual report of 2026-06-10 among the 4 examined of 11 listed" | — | Probably a miss: the 2026-08-07 6-K / 6-K/A pair is likely Q1 FY2027, undecided by these rules |
| Cellectis (1627281) | 6-K/A 2026-09-11 0001193125-26-389336 | `clls-ex99_1.htm` · iXBRL · period end 2026-06-30 | Yes, H1 2026 (Q3 due ~Nov) |
| ASML (937966) | 6-K 2026-10-14 0001628280-26-065813 | `pressreleasefinancialresul.htm` · file name | Yes, Q3 2026 |
| Novo Nordisk (353278) | 6-K 2026-08-04 0000353278-26-000023 | `caq22026.htm` · file name | Yes, H1 2026 (Q3 due ~Nov) |
| SAP (1000184) | 6-K 2026-10-26 0001104659-26-118617 | `tm2628113d1_ex99-1.htm` · headline "Quarterly Statement dated October 21, 2026" | Yes, Q3 2026 |
| Shell (1306965) | 6-K 2026-10-29 0001628280-26-068044 | `quarterlyexhibit992-q32026.htm` · file name | Yes, Q3 2026 |

The five original filers pick the same 6-Ks after the review fixes. The following were
read and refused (undecided or notice): Cellectis's .HEAL-101 data,
strategic-transformation and conference-call releases; Novo's four newest buyback
6-Ks; Shell's "Third Quarter 2026 Interim Dividend" and own-share notices.

### Not changed / known limits
- **File-name and headline evidence is a risk, now bounded by content.** A name with a
  period token or a results headline only NOMINATES a 6-K; the content check decides.
  A document that carries statement figures under a misleading name (a prior-period
  comparison deck, a prospectus with interim financials) can still pass it.
- **The content check reads the first 256 KB only.** A results report whose statement
  figures begin later fails and is passed over, honestly counted.
- **Toyota-shaped filers** whose results 6-K is named generically and whose release
  is large or iXBRL-free are not picked; the slot says so.
- **A results release with no signal past the cap is missed.** This covers a newswire
  press release with a generic file name, no iXBRL, and four or more newer non-results
  6-Ks above it. The slot then says `not_listed` with the counts. It does not guess.
- **A results headline in a press release over 256 KB is not read.** Only the cover's
  exhibit list can decide it.
- **Undated periods.** A 6-K chosen by file name or iXBRL carries no title. Its
  `current_period` label names a period end only for iXBRL. Otherwise it says
  "Interim report acquired (date)".
- **`max_events=80`** in the filing list is unchanged. Novo Nordisk lists 79 6-Ks in the
  420-day window, and its 20-F (2026-02-04) is still inside it. HSBC's 6-Ks already
  push its 20-F out of the list: its annual slot is `not_listed`, and the 6-K
  selection then runs without the "after the annual report" anchor.
- **Figures.** The 6-K is chunked and searchable. SEC XBRL normalisation still reads
  `us-gaap` only (ADR-075), so the 6-K/A's IFRS iXBRL tags are not read as structured
  facts. Any figures come from the existing document-text extraction, which this change
  does not alter.
- **No live research run yet.** Production acceptance needs a Cellectis rerun after
  deploy.

## ADR-078: A Material Corporate Event Needs a Vetted Source Before a Summary States It

**Date:** 2026-11-02 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11, defect PYPL2-1 (P0), live PayPal rerun
`39905de8`. The executive synthesis said "A recent acquisition offer was rejected by the
board as inadequate, reflecting ongoing strategic considerations. [F14]". F14 read
"Stripe/Advent's $60.50-per-share, $53B offer for PayPal was rejected by the board as
inadequate on July 20, 2026". Its evidence was one web page (`source_kinds:
["secondary_web"]`, class `unknown_web`, confidence 0.6, `unverified`). EDGAR held no 8-K,
no SC 14D9 and no 425, and neither 10-Q mentions Stripe, Advent or a proposal.

### How it got there
1. The event analyst wrote F14 from the page. W4's `trust.classify_claim` returned
   `claim_type: null`: `_EVENT_RE` knows "acquisition" and "takeover" but not "offer",
   "bid" or "rejected by the board". The finding was therefore stored without a
   `[single source]` label.
2. `professional_research.edit` asked the editor model for a synthesis.
   `validate_sentence` checks citations, figures, prohibited words and absence claims.
   A sentence with a label, no digit and no rating word passed every check, because
   nothing asked whether the cited SOURCE could carry the claim.
3. The chair (`agents/chair.py`) has the same gap: key points are checked for a citable
   `finding_id` and the label for its vocabulary, nothing more. On this run the chair
   happened not to repeat the claim.

### Decision
New module `app/services/material_events.py`. The rule is a deterministic gate, not a
prompt request.

**Material event types about the subject:**
- `takeover`: an approach, offer or bid for the subject, its acceptance or rejection, a
  pending merger, or a strategic-sale review;
- `going_private`;
- `insolvency`: bankruptcy, administration, receivership;
- `delisting`;
- `ceo_departure` and `cfo_departure`, which are separate types;
- `fraud_restatement_or_enforcement`.

**Vetted support:** a finding whose `source_kinds` include one of:
- `issuer_filing` or `issuer_ir`;
- `government_or_regulator`;
- `third_party_filing`: a filing on an official filing system, e.g. a bidder's
  tender-offer document;
- `quality_media`: the T4 tier, i.e. major financial, trade and local press. It is not
  a curated subset.

**Gate (option (a): exclude, dropped not repaired):**
- **Editor synthesis sentences and section leads** (`validate_sentence`, reason
  `material_event_unvetted_source`). Two sets must be covered by the event types that
  the sentence's cited VETTED findings state:
  - every event type the sentence states;
  - every event type that ANY cited finding states on unvetted sources only.

  So an ordinary 10-K revenue fact cited beside the rumour does not launder it. A filed
  "completed its merger with X" does not vouch for a takeover offer either, because it is
  not a takeover of the subject.
- **The deterministic synthesis** never picks an unvetted-event finding.
- **Chair key points** use the same rule against the finding they cite.
- **Chair synthesis** (prose with no citations): a sentence is dropped unless every event
  type it states is stated by some vetted finding of the run. A filed CEO succession
  does not unlock a rumoured CFO ouster. If nothing survives, the synthesis becomes the
  ledger state. `deterministic_fallback` is set with
  `fallback_reason = "synthesis_removed_by_material_event_gate"`, and the run's degraded
  list says so.
- Both apply to the model verdict and to the deterministic verdict. `LLMChair` takes the
  run's `subject` (name and ticker).
- **Prompts (an addition, not the safeguard):**
  - the editor sees `(UNVETTED WEB SOURCE ONLY)` after such findings, plus rule 4a;
  - the chair sees each finding's `source_kinds`, plus rule 7.
- `editor.checks` names the new check.

The finding stays in the ledger and in its section with its honest source.

**Detection** is a word-bounded vocabulary of event PHRASES, never a bare noun.
- **Offers:**
  - "offer/bid/approach for <subject>";
  - "offered $53B for <subject>" (a price is required);
  - "(in talks | seeks | plans | agreed | bid …) to buy/acquire/take over <subject>";
  - "<subject | the company | the board> rejected/accepted … offer/bid/approach";
  - a passive "offer/bid/approach … was rejected/rebuffed", when the board, a takeover
    modifier or a price is present.
- **Passive acquisition:** "<subject> will be / is being / agreed to be acquired [by X]",
  where X is not the subject.
- **Pending merger:** "merger agreement", "agreed to merge", "proposed merger".
- **Strategic sale:** "exploring a sale", "strategic alternatives", "sell itself".
- **Insolvency, delisting and going private:** the subject must be the one ACTING. That
  means the nearest subject reference in the same clause within 60 characters before
  the phrase, not possessive, and no customer, supplier, peer, unit, etc. in between.
- **Leadership:** "CEO/CFO … step down / resigned / ousted / retired / succeeded",
  except when the role belongs to another named company ("Codelco's CEO").
- **Misconduct:** "accounting fraud", "restatement", "enforcement action", "the SEC
  charged".
- **What counts as the subject:** its core name ("Southern Copper"), its distinctive
  words with generic ones removed (copper, materials, mining, resources, energy, bank,
  …), its ticker (case kept), "the company", or "the/its board".
- **Bounds:** input is capped at 8,000 characters and every window is bounded, so a
  1 MB adversarial string scans in under 0.1 s.

**Review round 2.**
- An accepted or rejected "offer/approach" is a takeover only beside one of:
  - a deal word (takeover, buyout, acquisition, acquire, bid, tender, go-private,
    merger);
  - a price;
  - a valuation word (inadequate, undervalues, premium).

  "PayPal accepted an offer to join the FedNow pilot" stays an ordinary fact.
- Newly detected:
  - "a $53 billion proposal" (billion scale, not debt or buyback);
  - "approached <subject>/the company";
  - "deemed/found … inadequate" (with "board" or an offer word present).
- No longer detected:
  - "sale of <named asset>", "merge its …";
  - "retire"/"successor" about a product or system ("the retirement of legacy checkout",
    "retire the notes", "successor platform", "succeeded in cutting costs").
- **Chair only:** a synthesis sentence is also dropped when it names a counterparty of an
  unvetted event finding (a proper name in that finding other than the subject, e.g.
  Stripe, Advent) together with a hint word for the same event type ("Stripe's interest
  in the company persists"). "Stripe competes in online checkout" is kept.

### Not flagged (pinned in tests)
Acquisitions BY the subject:
- "PayPal acquired Xoom in 2015", "Xoom was acquired by PayPal";
- "completed its/the merger with Honey Science";
- "the board accepted the bid … for its Honey unit".

Ordinary uses of event words:
- "customer acquisition cost", "acquired 440M active accounts", "offers Buy Now Pay
  Later";
- a debt tender offer, "delisted its preferred shares";
- "fraud losses / fraud detection", "restated for the new segment structure", "amended
  and restated" plans;
- "private-label", "Brazil went private; the company …".

Other parties:
- "merchants who went into administration", directors (not CEO/CFO) resigning,
  "Codelco's CEO";
- "copper smelters filed for bankruptcy" and "acquisition of copper concessions" for
  Southern Copper;
- a merger between two other companies.

### Replay over the 12 stored campaign reports
HON, HON2, ADBE, JPM, CFR, PYPL, PYPL2, ANTO, ANTO2, CLLS, CLLS2, LIN: findings,
synthesis sentences, leads, and chair key points and synthesis. Results are the same
before and after the review fixes.
- **PYPL2:** 1 summary sentence blocked (the synthesis sentence above). F14 is the only
  unvetted-event finding.
- **Every other report:** 0 blocked, 0 unvetted-event findings and 0 vetted-event
  findings. Keyword-bearing true negatives include ANTO2 "agreed to acquire
  Mineralinvest's 49% interest" and "CEO targets ~30% copper growth", HON2 "four
  Honeywell directors resigned", PYPL "amended and restated", and CLLS "after the Merger
  closed".

### Not changed / known limits
- **Labels unchanged.** W4's `classify_claim` still does not tier "offer/bid" claims as
  corporate events, so F14 in its section carries no `[single source]` label. That is a
  separate change to persisted trust labels.
- **Detection is lexical.** An event phrased outside the vocabulary is still caught in
  a summary sentence that cites only such a finding (via the cited finding). It is
  missed in uncited chair prose when the chair words it outside the vocabulary.
- **Vetting is per finding, not per claim.** A finding citing a 10-Q and a rumour page
  counts as vetted even when only the page states the event.
- **A CFPB/SEC investigation being opened counts as enforcement.** Unvetted, it is kept
  out of summaries.
- **V1 council output is out of scope.** It does not read V3 findings. No live run yet;
  acceptance needs a PYPL rerun after deploy.

## ADR-079: A Web Copy of an EDGAR Filing Is the Issuer's Only When Its CIK Is

**Date:** 2026-11-02 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11, live Cellectis rerun `df36d6f6`, defect
CLLS3-1 (P0). Web search put a Cboe EDGA Exchange, Inc. financial-statement exhibit from
`sec.gov/Archives/edgar/data/<another CIK>/…` into Cellectis's corpus. F1–F3 said "The
company operates a single reportable segment: an electronic market…", "29% of its total
revenues from its largest two customers" and "the Parent Company's CEO is the chief
operating decision maker". They were labelled "issuer filing", led the executive
synthesis, and replaced the 20-F's "Therapeutics segment" finding. The same path brought
Calyxt's FY2022 20-F risk factors into CLLS2.

### Where the class came from
- `web_research/classify.classify_source`: the host (`publisher_tier(sec.gov/Archives…)`
  = T1) gives `regulatory_filing`. It is stored on the web version at ingest
  (`web_research/ingest.py`) for whichever company the search ran for.
- `director/contracts.evidence_ref_for`: a T1 item maps to `issuer_filing`. Web leads are
  forced to `third_party_filing` unless `issuer_match` is set, but `search_company_corpus`
  hits were exempt. So every web EDGAR page in the subject's corpus became the
  subject's issuer filing, and the finding's `source_kinds` said so.
- The ADR-066/074 guards key on a named other company or on its own domain. "The
  company" on sec.gov passes both.

### Decision
1. **`web_research/edgar_registrant.py`** reads the registrant CIK from an EDGAR URL:
   `/Archives/edgar/data/<CIK>/`, `ix?doc=/Archives/edgar/data/<CIK>/…`,
   `browse-edgar?…CIK=`, `data.sec.gov/…/CIK##########.json` and `efts` `ciks=` (one only).
   - The path is unquoted and `posixpath.normpath`-ed first, so `/data/1627281/../1234/`
     reads as 1234.
   - Leading zeros are stripped.
   - The accession prefix is never used (it is the filing agent's CIK).
   - A ticker in `CIK=` is not a CIK.
2. **Verdict per web item on an EDGAR filing page** (`registrant_verdict`):
   - the URL's CIK is the subject's → `own` → `issuer_filing`;
   - the subject's CIK is known and the URL's differs → `other` → `third_party_filing`;
   - **no ADR-075 basis:** `other` only when the SEC directory positively names that CIK
     as a registrant whose name does not agree with the subject's. Nothing is ever `own`
     without a basis (review N1). A name-agreeing CIK (ADR-061 `_names_agree`) is only
     taken out of `other`, so it is `unidentified`. `_names_agree`'s prefix rule would
     otherwise make "The Sage Group plc" own Sage Therapeutics' 10-K, "Compass Group"
     Compass Minerals', and "Next plc" Next Bridge Hydrocarbons'. A CIK the directory
     does not hold, or an unreadable directory, is also `unidentified`. Review D1: `sec_listing_for` also
     returns None when the directory or a domicile record is unreadable, or the venue is
     spelled in a way it does not know (OTC, NYSE Arca, XNYS, "New York Stock
     Exchange"). So None is not proof that the subject is not an SEC registrant;
   - no CIK in the URL, or a basis whose CIK could not be resolved → `unidentified` →
     `third_party_filing`: never the issuer's, never dropped;
   - **`other` that names the subject → `counterparty`** (`third_party_filing`, kept;
     reviews D2, N2 and N3). This covers a holder's 13D/13G, an insider's Form 3/4/5 and
     an acquirer's 8-K, S-4 or SC TO. `edgar_registrant.filing_names_subject` decides
     whether the cited filing names the subject. The filing must carry one of:
     - the subject's full identifying name, every word in order after legal suffixes are
       dropped ("Cellectis", "Electronic Arts", "Sage Therapeutics"). A one-word name
       counts only if it has at least 5 letters, is not a generic word and appears
       capitalised;
     - a ticker framed by an exchange ("(NASDAQ: NET)", "NYSE: A", "Nasdaq: CLLS");
     - on an ownership form only, `issuerTradingSymbol`.

     A bare ticker ("NET INCOME", "A summary…", a CASH line) never names the subject.
     Neither does one word of a longer name ("electronic", "equity") or "the company".
     An ownership form is recognised only by EDGAR's own XSL directories
     (`xslSCHEDULE_13D|13G…/`, `xslF345X0n/`), never by a document name
     ("exhibit-form-3-of-agreement.htm"). An ownership form that does not name the
     subject stays `other`.

   sec.gov pages outside the filing archive are already `regulator_publication` (T2) and
   are not touched.
3. **The subject** (`LLMInvestigator._resolve_edgar_subject`, once per investigator, only
   when a web EDGAR page is present):
   - With a basis: its CIK plus every directory CIK for its ticker, so the sibling line
     (CLLS for ALCLS) counts as the subject.
   - Without a basis: no own CIKs; the directory only says which CIKs are another's.
   - The names matched for `counterparty` are the subject's own name, plus the SEC's name
     for its CIK when there is a basis.
   - Any failure leaves nothing known, so every page is `unidentified`.
4. **Applied in the investigator** (`apply_edgar_registrant`) after acquisition, before
   the evidence contract reads the question's refs, and again (idempotently) in
   `_write_up`. The stored `source_class` is not rewritten: a web version is shared by
   content hash, so "the subject's filing" is a per-run judgement, as with
   `trust.is_verified_issuer`.
5. **Statement guard** (beside ADR-066/074, outside peer domains): a statement whose
   cited items are ALL web pages of another registrant is dropped. It is counted in
   `statements_about_other_registrant` (diagnostics, pinned). The question gets one
   `entity_ambiguous` gap naming the registrants by CIK, and by name when the SEC ticker
   directory knows the CIK. Page text never supplies a name. In `competitive_position` the
   statement is kept, labelled `third_party_filing`.
6. **Platform SEC evidence is never re-judged.** The filing bridge
   (`ensure_filing_corpus_evidence`), core filings and the filings tools produce non-web
   items (`source_class` None). They were fetched for the subject's own CIK by ADR-075's
   resolver, so they keep `issuer_filing`.
7. **Publisher firewall (review D3).** `foreign_publisher` (ADR-066) never reads an item
   as another company's own site when its class is a publisher's:
   - `exchange_announcement`;
   - `regulatory_filing`, `regulator_publication`, `government_publication`,
     `statistical_agency`;
   - `major_financial_press`;
   - `aggregator`.

   With the firewall now live through `ToolSession.db` (`144a46f`), registering
   "Euronext N.V." or "Nasdaq, Inc." would otherwise drop every euronext.com or
   nasdaq.com announcement about the subject.

### Replay over the 15 stored campaign reports
Reports store EDGAR URLs only for platform/V2 items. They do not store the URL of a web
corpus chunk, so web sec.gov items are identified by origin `sec.gov`.

| Report | Stored EDGAR URLs (own / other) | Web sec.gov items | Findings resting only on them | Under ADR-079 |
|---|---|---|---|---|
| ADBE | 5 / 0 | 3 | 6 | kept if Adobe's CIK 796343 (dated 2022-12-02, 2025-11-28 = Adobe FY ends; statements name Adobe) |
| HON, HON2 | 4, 6 / 0 | 0 | 0 | unchanged |
| JPM | 6 / 0 | 0 | 0 | unchanged |
| PYPL, PYPL2, PYPL3 | 4 each / 0 | 0 | 0 | unchanged |
| CLLS | 0 | 0 | 0 | unchanged |
| CLLS2 | 0 | 1 (FY2022 risk factors on Calyxt) | 1 (`counter_thesis`) | URL not stored: under CIK 1627281 (Cellectis's own FY2022 20-F) it is kept — a staleness defect, not a registrant one; under Calyxt's CIK it is dropped |
| CLLS3 | 0 | 1 (Cboe EDGA exhibit) | 3 (F1–F3, `business_model`) | dropped, gap names the CIK; 20-F finding stands |
| ANTO, ANTO2, CFR, LIN, LIN2 | 0 | 0 | 0 | unchanged (non-SEC) |

No stored EDGAR URL of a subject's own filing changes verdict (33 of 33 `own`, 0 `other`).

The replay was re-run after review fixes D1–D4 and N1–N3. It gives the same counts: no stored URL is an ownership form or a `..` path, and every SEC subject in it has a basis.

### Not changed / known limits
- **The ADBE verdict is inferred, not read.** The three URLs are in the ledger, not the
  report. If any is another registrant's, its findings are dropped. That is the
  intended behaviour.
- **Admission is unchanged.** Another registrant's page is still stored in the subject's
  corpus and can still be retrieved. It can no longer be cited as the issuer's word
  outside peer domains. Filtering at ingest needs the subject's CIK in the web stage.
- **A multi-filer accession** (a 13D or a tender offer filed under both the filer's and
  the subject company's CIK) read through the subject's directory counts as `own`.
- **Without a basis, the subject's own web filing is `unidentified`.** It is kept as
  `third_party_filing`, not counted as `issuer_filing`, until ADR-075 resolves a basis.
- **Counterparty detection is lexical.**
  - A one-word name that is also a capitalised common word ("Target" at the start of a
    sentence) still counts as naming the subject.
  - A subject with a short single-word name ("Next", "Sage") is recognised only by an
    exchange-framed ticker.
  - A filing that names the subject only in a passage outside the cited excerpt is not
    recognised.
- **Mixed support is kept.** A statement citing the subject's 20-F and another
  registrant's page keeps both kinds and is not dropped.
- **Ladder stop decisions** inside `_acquire` still see the pre-check kinds. The
  recorded contract outcome uses the checked kinds.
- **Not wired:** discovery's council pack and W6 theme runs (no subject CIK), and the V2
  path.

### Same slice: the ADR-066 publisher firewall was never live

`LLMInvestigator._other_companies` called `execute` on the investigator's `ToolSession`,
which has no such method (the database handle is `ToolSession.db`). The guard's
`except Exception` swallowed the `AttributeError`, so in production the registered-companies
list was always empty and `foreign_publisher` never dropped anything. It now reads through
`session.db`, the way ADR-079's subject resolution does.

Replay against the 121 companies registered in production and every web publisher in the
15 stored campaign reports: no news or data host collides with a registered company's
label. The only collision is `iluka.com` (Iluka Resources) in the two Lindian reports, where
it drops 4 + 3 findings resting solely on Iluka's own site: Iluka unit costs, production and
outlook filed as Lindian's (campaign defect LIN-3). That is the behaviour ADR-066 specified.

### ADR-079 review (2026-11-03): an EDGAR page with no registrant in its address

The live Cellectis rerun on `a8f8f8a` (report 55f96fc3) still led with "The company operates
a single reportable segment: an electronic market…". The page was a Cboe EDGA Exchange
X-17A-5 PDF at `sec.gov/Archives/edgar/vprr/2600/26000283.pdf`. It is a paper filing, so its
address carries no CIK. ADR-079 kept every CIK-less EDGAR page as `unidentified`, so the page
was relabelled `third_party_filing` but nothing was dropped.

**Rule.** A CIK-less EDGAR page whose own text never names the subject is `other`, by the
strict `filing_names_subject` test (full name in order, or an exchange-framed ticker). A
statement resting only on such pages is dropped, with an `entity_ambiguous` gap ("an EDGAR
document with no registrant in its address").

**Exceptions.** None of these is dropped:
- a CIK-less page that names the subject (kept as `third_party_filing`);
- a CIK-bearing page that the directory cannot place (still `unidentified`, review D1);
- any page when nothing is known about the subject (no names).

**Replay over all 15 campaign jobs' fetched sec.gov URLs.** The only CIK-less EDGAR URL is
this vprr PDF, which appears in CLLS, CLLS2 and CLLS4. Every other URL carries a CIK. The
"Calyxt" risk factors seen in CLLS2 came from Cellectis's own 2017 20-F (CIK 1627281), so
that is a staleness defect, not an entity one. `aclx-20251231.htm` (CIK 1786205) is
another registrant and is already handled.

---

## ADR-081: Every Outbound Request Declares One Service Identity, Never a Person's

**Date:** 2026-11-03 · **Status:** Accepted (implementation)
**Trigger:** cross-sector QA campaign 2026-11 disclosure. Twice, an operator-side (agent)
script sent requests to sec.gov with a developer's PERSONAL email address as its
User-Agent. The product code never did, but nothing stopped a script from improvising.

### What was wrong
- The UA string was copied into ~10 modules (`sec_edgar_provider`,
  `sec_recent_filings_provider`, `sec_edgar_fundamentals`, `gleif_provider`,
  `stooq_provider`, `news_catalyst_provider`, `free_news_provider`, `agent_tools/peers`,
  `sources/safe_web_fetcher`, `sources/sec_filing_documents`) with two contact domains:
  `research@investingbuddy.com` (the data clients) and `research@investingbuddy.example`
  (the open-web fetcher). `free_news_provider` sent no contact at all.
- Nothing validated the contact, nothing tested that a request header could not carry a
  local identity, and there was no sanctioned helper for an ad-hoc SEC read.

### Decision
1. **One builder.** `app/core/outbound_identity.py` is the only place a UA is built:
   `research_user_agent(cfg)` for the API / data clients and `crawler_user_agent(cfg)` for
   the open-web and issuer-document fetcher. Call sites use `USER_AGENT_HEADER` and one of
   these functions; none spells a UA header or string.
2. **One contact, one setting.** `outbound_contact` (env `OUTBOUND_CONTACT`), default
   `research@investingbuddy.example`. `.example` was already the documented crawler
   contact and is a reserved, obviously-placeholder domain; the `.com` address was never
   shown to be owned by the project. Choosing a real, monitored SERVICE mailbox is still
   open-web spec decision U8 — when the owner picks one it is an app setting, not a code
   change. Behaviour change: the SEC/GLEIF/Stooq/news clients now declare the `.example`
   contact instead of the `.com` one, and the GDELT client gains a contact.
3. **Fail closed.** The configured contact is refused, and the default used instead, when
   it is malformed (anything but a plain mailbox: no spaces, parentheses or CR/LF), on a
   consumer-mail domain (gmail, googlemail, outlook, hotmail, live, msn, yahoo.\*, ymail,
   icloud, me, mac, proton.me, protonmail, pm.me, gmx.\*, aol, mail.ru, yandex.\*), equal
   to a local identity (login name via `getpass.getuser()`, `USER` / `LOGNAME` /
   `USERNAME` / `EMAIL` / `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_EMAIL`, the hostname), or
   derived from one (the identity's local part or machine name, four characters or more,
   appears in it). The refusal logs `outbound_contact_refused reason=<code>` once per
   reason per process, WITHOUT the value. The contact is never logged at a normal level.
   App code never shells out to `git config`; the regression test does.
4. **Two product tokens, kept on purpose.** `InvestingBuddy-Research-Bot` is what robots.txt
   groups name and what `web_research/robots.py` and `traversal/issuer_site.py` match, so
   the fetcher keeps it. `InvestingBuddy-Research-Platform` is what the SEC and the other
   data APIs have seen since Phase 19; changing it would only make the platform look like
   a new client to a fair-access operator. Only the contact is shared. Robots matching is
   unchanged (`USER_AGENT_PRODUCT_TOKEN` moved to the central module and is re-exported
   from `safe_web_fetcher`).
5. **A sanctioned operator helper.** `apps/api/scripts/sec_get.py URL [OUT]`: https only,
   hosts `www.sec.gov` / `data.sec.gov` / `efts.sec.gov` only, the central research UA, the
   `SEC_REQUEST_MIN_INTERVAL_MS` spacing (kept across invocations through a temp-dir
   timestamp file), no redirects, a 50 MB cap, no environment proxy. It refuses to run
   when given any option (`--user-agent`, `-A`, `-H`, …) or when any `*USER_AGENT*`
   environment variable is set. Every agent / subagent brief that touches sec.gov must
   name it (docs/SECURITY.md, "Outbound identity").

### Tests
`apps/api/tests/test_qa_closure_outbound_identity.py`: an AST scan of `app/**/*.py` and
`apps/api/scripts/*.py` (a UA header key, a `*USER_AGENT` string constant, a hand-built UA
string, a consumer-mail literal, or a contact literal outside the central module and
`config.py`), with a self-test that the scanner catches each shape; the default UA; the
fallback for every refusal class under sentinel env / `getpass` / hostname values; the real
git identity (read only in the test, only a boolean operand) never reaching a UA; the
fallback warning carrying no value; the transport-captured UA of the EDGAR provider,
recent filings, fundamentals, 8-K news, the filing index, the 6-K index page and head, and
the filing body; and the helper's host allowlist, override refusal, no-redirect and
spacing.

### Not done
- No real service mailbox is chosen (U8). Until it is, the declared contact is a
  placeholder; SEC fair-access asks for a reachable one.
- The helper does not cover non-SEC hosts (GLEIF, Stooq, ASX, FCA NSM). A script reading
  those must still use the product clients, not an improvised UA.
