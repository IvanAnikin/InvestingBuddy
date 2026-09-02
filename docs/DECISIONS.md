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

**Negative / accepted.** An unscoped claim is cleared by matching ANY scope.
That is deliberate: for a guard that SUPPRESSES content, the correct failure
mode is a missed contradiction rather than an invented one. A sentence that
genuinely means Group but does not say so, quoting a segment's number, passes.

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

**Negative / accepted.** On a report that has BOTH a council case and
deterministic prose, the deterministic prose no longer appears in the clean
view. Its analytical points are not shown there at all. That is the requirement,
the record entries still route to research confidence, and the unedited original
stays on the technical report page — but it is a real reduction in what the
clean view shows for such a report, not merely a re-filing.
