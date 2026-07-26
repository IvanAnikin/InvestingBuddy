# Session State — Phase 29B.2 CLOSED → 29B.3 next (updated 2026-07-26)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- Branch: `main` (clean tree).
- Phase / subphase: **Phase 29B.2 CLOSED + validated**; next = **Phase 29B.3** — stage: **not-started**.
- Closure report: `docs/development/closures/phase-29b2.md`.

## Just-closed: Phase 29B.2 (evidence, condensed)
- PR #56 "Phase 29B.2: extract primary document evidence" — squash-merged to `main`.
- Merge SHA `793e0a750ac58d6d9d6030180baca2c70a1d582c`; Deploy API + Web Staging both
  success; API `/health` + Web `/api/version` `commit_sha` both `793e0a75…d582c`
  (converged, stable).
- Migration: **none** (DB head `011`).
- CI (pre-merge on `f42b187`): "Lint & Test" PASS · "Typecheck, Lint & Build" PASS
  (test evidence = CI green; local counts on PR #56).
- Security: ib-security-agent PASS · ib-pr-review-agent APPROVED (10/10).
- Pre-flip staging: PASS (SHA converged, no migration, AUTH_TEST_MODE absent, logs clean).
- ON-state staging (`SOURCE_DOCUMENT_EXTRACTION_ENABLED=true`):
  VALIDATED-WITH-ENVIRONMENTAL-NOTE — CFR.SW full analysis = final report, `llm_used`,
  8/8 agents (0 failed), 7 evidence items, `schema_valid=true`, `safety_valid=true`,
  `human_review_required=true`, `publication_ready=false`, `primary_documents` present
  (count 0); KER.PA honest gap (0 fabricated); BA.LSE → BAE Systems (no Boeing);
  AAPL/AMAT evidence budget bounded (≤40 cap, `max_items=5`); discovery regression PASS;
  `/admin/sources` OAuth-gated; safety/publication PASS; logs clean.

## Test / scan state
- App tests: CI green pre-merge (PASS/PASS). No app code changed after merge (docs-only session).
- Security scan: PASS (this session touches docs only; no secrets written).

## Decisions made
- Phase 29B.2 approved for closure; ledger + roadmap flipped to ✅ only after staging
  validation on file (never before).
- Tooling PR #55 (`c98adca`) confirmed merged → ledger `tooling` row set to ✅.

## Blockers / open questions
- **Environmental caveat (carry into 29B.3):** the extraction pipeline is provably
  active, bounded (5MB/15s), safety-gated and fabrication-free, but every live
  verified-issuer annual report reached on staging is a scanned/image PDF (29B.2 is
  **no-OCR by design**) or an index-only link, so it degrades **honestly** to recorded
  source-gaps with **zero fabricated facts** — hence `primary_documents` is
  present-but-empty. The excerpt/fact happy-path is NOT demonstrable on staging until
  text-based (non-scanned) primary sources or OCR exist.
- Azure OpenAI gpt-4.1-mini TPM quota can still partially fail agents on large packs
  (environmental; evidence budgeter mitigates, not eliminates).

## Final staging flags (KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Next exact command / action
- Create branch `feature/phase-29b3-primary-facts-integration` and scope Phase 29B.3
  (deeper primary-fact integration into pack/council evidence + SEC full-text), carrying
  the no-OCR / scanned-PDF environmental caveat above.
