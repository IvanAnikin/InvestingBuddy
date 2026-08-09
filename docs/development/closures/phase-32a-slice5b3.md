# Closure Report — Phase 32A Slice 5B.3: admin web visibility for primary-document ingestion

> Produced after merge + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-09. Verdict: **Slice 5B.3 CLOSED + STAGING-VALIDATED.**
> This is the LAST slice of Phase 32A's Slice 5 work. See the companion document
> `docs/development/closures/phase-32a-final-status.md` for why the OVERALL Phase 32A
> verdict is NOT a blanket "closed" despite this slice's own full pass.

## Scope

Makes the Slice 5/5A/5B.1/5B.2 primary-document/OCR ingestion pipeline visible in the
existing admin report UI, and reconciles one stale gap message left over from Slice 5B.1.
Purely additive — no changes to ingestion behavior, flags, or existing endpoints.

## Merge / deploy

- PR #86 squash-merged → `main` **`8723cfc5ba8e0bda0631a4cd2f8857c138993f5f`**.
- One review round found a real bug (`validated_fact_count`/`reused_count` double-counting
  when two ingestion attempts shared the same `content_hash`), fixed and re-reviewed to
  **GO** before merge — see PR #86 review history.
- API deploy `31329015052` (success) + Web deploy `31329015067` (success), both auto-triggered
  by the same merge (this PR touches both `apps/api/**` and `apps/web/**`).
- `/health` `commit_sha=8723cfc` ×3; `/api/version` `commit_sha=8723cfc` ×3 (web).
- **No Alembic migration** — DB head stayed `014` (verified via direct query).
- App settings unchanged (40, same as before this merge) — no flag/config change in this slice.
- Pre-merge gates: full backend suite **2946 passed, 0 failed, 12 skipped**; ruff clean;
  mypy `app` 71-error baseline (zero new); frontend typecheck/lint/build all clean;
  independent `ib-security-agent` **PASS**; independent `ib-pr-review-agent` **GO** (after
  the one fix above).

## What was built

- **`GET /api/v1/reports/{report_id}/primary-documents`** — bounded, admin-scoped provenance
  API. Scopes by the report's own `agent_run_id` (falling back to `company_id` for legacy
  pre-lineage reports; never both `None` returning unscoped data). Joins
  `document_ingestion_attempts` (source of truth for what was attempted THIS run, including
  reused/failed outcomes) to `extracted_documents`/`extracted_facts` by raw-bytes
  `content_hash` — deliberately not by `agent_run_id` on those tables, since a reused
  document keeps whichever run originally created it. Never exposes raw document bodies, raw
  OCR text, provider exceptions, or credentials — only already-sanitized, already-bounded
  persisted fields. No new auth mechanism — matches the existing perimeter-auth convention
  used by every other admin route in this codebase (confirmed identical unauthenticated
  behavior to the sibling `GET /reports/{id}` route: 401, same header, same body shape).
- **Stale SEC gap reconciliation** — the SEC connector's "full filing text is not retrieved"
  gap (attached unconditionally whenever it returns metadata, with no visibility into the
  SEPARATE, later deep SEC filing-body fetch) is now suppressed specifically and only when a
  real SEC-sourced primary document was actually extracted this run. Every other gap
  (including SEC's own honest "filing body blocked" gap) is untouched.
- **"Primary Documents" admin UI tab** — shown only when there was real ingestion activity
  this run. Summary counts (discovered/attempted/extracted/metadata-only/failed/native/OCR/
  validated-fact/reused). Per-document cards visually distinguishing native vs OCR extraction,
  reused-from-cache, metadata-only, and every honest failure state (encrypted,
  password-protected, malformed, timeout, unsupported, etc.). Expandable excerpts/facts with
  real (never fabricated) confidence and validation status. No recommendation language.

## Staging validation (live, real data)

Fresh AAPL and CFR analyses through the real pipeline, then the new endpoint queried directly:

- **AAPL** (`report_id=350f3ef0-...`): `primary-documents` summary internally consistent
  (`extracted_count = native_count + ocr_count`); SEC 10-Q + 8-K both `extraction_method=html`
  (native, not OCR) with 3 validated facts on the 10-Q. Stale gap text confirmed **absent**
  from `research_memo.source_gaps` (0 occurrences of the target substring). A second run
  (`report_id=268f41ca-...`) produced an identical summary with both documents correctly
  flagged `reused=true` — no count inflation, proving the double-count fix holds under a real
  repeated request, not just the offline regression test.
- **CFR** (`report_id=eb24cf4a-...`): 3 real Richemont PDFs, all `extraction_method=native_pdf`
  — the PDF-native counterpart to AAPL's HTML-native case.
- **Company isolation** — zero `content_hash`/URL overlap between AAPL and CFR at BOTH the API
  response level and a direct DB cross-check (10 AAPL vs 12 CFR `document_ingestion_attempts`
  rows, zero cross-company URL leakage in either direction).
- **Citation linkage** — 141 (AAPL) / 91 (CFR) citations, zero genuinely dangling `source_id`
  foreign keys (re-verified with an `EXISTS` check after an initial false-positive read).
- **Security** — unauthenticated `GET .../primary-documents` → 401, identical header/body
  shape to the sibling `GET /reports/{id}` (no new bypass); fresh 638-line log capture across
  both runs grepped clean for secrets/keys/tokens; no raw document/OCR text or provider
  exception text in any response body.
- **Performance** — the new endpoint itself: 0.28-0.65s wall-clock, 100-250ms server-side,
  across 5 calls — a genuinely bounded read, not an unbounded query. Full analysis/report
  flows: 84-131s, no 502/504.
- **Alembic head** — confirmed `014`, unchanged.

## Known, honestly-flagged non-blocking observation

The gap-reconciliation fix targets the ONE deterministic, connector-authored gap message
(`research_memo.source_gaps`). A SEPARATE, LLM-authored free-text mention with similar
wording ("full text of SEC filings not yet retrieved") can still appear inside a council
agent's own generated `risks_or_gaps` output on some runs — this is LLM free text, not a
deterministic gap, and inherently varies run to run; it is not fixable by this slice's
mechanism (would require prompt-level changes to make agents aware that SEC body extraction
succeeded, a different and larger change). Recorded as a genuine, non-blocking follow-up.

## Explicitly NOT covered by this closure

A human has not yet visually confirmed the new "Primary Documents" tab renders correctly
inside the browser, because the admin web UI is gated behind real GitHub OAuth (not Basic
Auth), and `AUTH_TEST_MODE` — which would allow an automated dev-login bypass — must stay
absent on staging as a hard security invariant this whole project protects throughout every
prior slice. No workaround was attempted. The API-level data this UI renders is exhaustively
validated above (real counts, real documents, real facts, real security/isolation); what
remains is a one-time manual visual check at `https://ib-stg-web.azurewebsites.net/admin/reports/350f3ef0-2df0-4ed1-9df9-91e2930a36e9`
(the AAPL report from this validation, which has real primary-document data to display) after
logging in with a real GitHub account authorized for this staging environment.

## Final status

**Slice 5B.3 CLOSED + STAGING-VALIDATED, 2026-08-09.** Every criterion in this slice's own
scope passed with real, live, staging evidence — API correctness, security, company
isolation, citation linkage, performance, and one real bug found and fixed via code review
before merge. **This is the last slice of Phase 32A's Slice 5 work.** See
`docs/development/closures/phase-32a-final-status.md` for the overall Phase 32A verdict,
which carries forward one inherited, non-blocking-to-this-slice caveat from Slice 5B.2 (real
Azure OCR live invocation still unobserved) that this slice's own scope does not resolve and
was never meant to.
