# Session State — Phase 29B.3 CLOSED · Phase 29B.4 NEXT (stage: not-started) (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- On `main`, HEAD `29f4a84` ("Phase 29B.3: integrate primary facts into reports and
  quality gates" (#57)), clean tree.
- **Phase 29B.3 — CLOSED** (merged + deployed + staging-validated). Closure report:
  `docs/development/closures/phase-29b3.md`; ledger row ✅; roadmap flipped.
- **Next phase: Phase 29B.4 — EU/UK regulated-disclosure connectors — stage: not-started.**
  Start with subphase **4A: UK FCA NSM/RNS** for `BRBY.LSE` + `BA.LSE` (live fetch for the
  currently-scaffolded UK FCA NSM connector).
- DB head `011` (no migration since discovery/scoring).

## Phase 29B.3 closure evidence (condensed)
- PR #57 squash-merged to `main`. **Merge SHA `29f4a84f32ca1c1e7167ff3dcde394c38a7cd9e4`.**
- Deploy API — Staging success; API `/health` `commit_sha=29f4a84` (3 stable polls).
  Web unchanged at `793e0a7` (no web change this PR — backend-only). No migration (head `011`).
- Tests: backend **2033 pass / 12 skip / 0 fail** (+31 in `tests/test_phase29b3_primary_facts.py`),
  ruff clean, mypy `71` baseline (no new). Frontend N/A.
- Security: ib-security-agent PASS (8/8). Pre-PR: ib-pr-review-agent APPROVED (8/8) +
  currency-guard hardening (explicit-USD + explicit-millions required before writing
  `revenue_ttm_usd_m`; no currency conversion).
- Staging: **VALIDATED-WITH-ENVIRONMENTAL-NOTE (honest-empty + no regression).** CFR.SW full
  analysis = final, `llm_used`, 8/8 agents, `schema_valid`+`safety_valid` true,
  `human_review_required=true`, `publication_ready=false`, `research_complete=false`,
  `primary_facts=0`, no `extracted_primary_facts` block, no populated `*_primary_filing`
  datapoints (0 facts — scanned/no-OCR); T1/T2 checklist honestly not-completed. AAPL/AMAT no
  regression (partial agents = Azure OpenAI TPM throttling = environmental). Discovery
  regression PASS with **no score inflation**. Logs clean; AUTH_TEST_MODE absent; publication
  admin-gated.
- What 29B.3 shipped (backend-only, no migration, no new flag): `PrimaryFactRef` on
  `EvidenceItem` (structured, no raw text) → council metadata-only under
  `source_summary_json.llm_council.primary_facts` (`to_report_dict` unchanged) → report
  `T1_primary_filing` datapoints + `extracted_primary_facts` + recomputed T1/T2 checklist +
  strict-schema completer (refuses non-USD revenue into the USD field, no conversion).

## Staging flags (unchanged — all ON, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Blockers / carry-forward limitations (into Phase 29B.4)
1. **Facts-present happy path is unit-fixture-proven, NOT live-staging-proven.** The extractor
   reaches only scanned/index-only PDFs (no-OCR) → 0 facts on staging, so `primary_facts` and
   the `*_primary_filing` datapoints render present-but-empty. Will light up with digital-text
   (non-scanned) primary sources / OCR / future SEC full-text. Both are candidate follow-ups.
2. **Scoring/completeness numeric uplift is capability-only.** `ScoringEngine` +
   research-completeness agent accept a primary-fact credit as an optional, unit-tested
   parameter, but **no production caller passes it** (scoring/completeness run pre-council,
   before facts exist) → no live numeric uplift yet. Candidate follow-up.
- Azure OpenAI gpt-4.1-mini TPM quota can still partially fail agents on large packs
  (environmental; 29B.2 evidence budgeter mitigates, not eliminates).

## Next exact command / action
- **Create branch `feature/phase-29b4a-uk-fca-rns-disclosures`** (start with subphase 4A:
  UK FCA NSM/RNS for `BRBY.LSE` + `BA.LSE`) and **scope 29B.4A**.
