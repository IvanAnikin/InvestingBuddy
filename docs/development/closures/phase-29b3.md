# Closure Report — Phase 29B.3: Primary-Fact Integration into Reports + Quality Gates

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).

- **PR:** #57 "Phase 29B.3: integrate primary facts into reports and quality gates" — squash-merged to `main`.
- **Merge SHA:** `29f4a84f32ca1c1e7167ff3dcde394c38a7cd9e4`
- **API SHA** (`GET /health` `commit_sha`): `29f4a84` — matches merge SHA? yes (3 consecutive stable polls)
- **Web SHA** (`GET /api/version` `commit_sha`): `793e0a7` — matches merge SHA? no — **expected**: backend-only PR, no web change this phase (web unchanged from Phase 29B.2).
- **Deploy:** "Deploy API — Staging" success at `29f4a84`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2033 pass / 12 skip / 0 fail** (+31 new in `apps/api/tests/test_phase29b3_primary_facts.py`), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (8/8) · ib-pr-review-agent APPROVED (8/8). Pre-PR review added currency-guard hardening (explicit-USD + explicit-millions both required before writing `revenue_ttm_usd_m`; no currency conversion).

## Validation A–I
- A — API SHA converged (3 consecutive matches at `29f4a84`): pass
- B — Web SHA matches: n/a — no web change this PR (web stays `793e0a7`, as designed)
- C — migration state as expected (head `011`, no migration): pass
- D — AUTH_TEST_MODE absent: pass
- E — phase check 1: CFR.SW full analysis → final report, `llm_used`, 8/8 council agents,
  `schema_valid=true`, `safety_valid=true`, `human_review_required=true`,
  `publication_ready=false`, `research_complete=false`; `primary_facts=0`, **no**
  `extracted_primary_facts` block, **no** populated `*_primary_filing` datapoints
  (0 facts — every reachable issuer report is scanned/no-OCR); T1/T2 checklist honestly
  not-completed: pass
- F — phase check 2: AAPL + AMAT no regression (partial agent completion attributable to
  Azure OpenAI gpt-4.1-mini TPM throttling = environmental, not a defect); discovery
  regression PASS with **no score inflation** (confirms scoring/completeness credit is
  capability-only / not wired to a production caller): pass
- G — final flag state confirmed by behavior (all ON, unchanged): pass
- H — logs / no-secrets: pass (current-build logs clean)
- I — safety/publication (no recommendation/valuation; `/admin/*` OAuth-gated; human
  review required; `publication_ready=false`): pass

## Staging validation verdict
- **VALIDATED-WITH-ENVIRONMENTAL-NOTE** (honest-empty + no regression). Wiring, safety,
  fabrication-freeness and no-regression are confirmed on live staging; the populated
  fact path is not demonstrable there (see environmental note).

## Environmental note (carry forward as a Phase 29B.4 caveat)
Because Phase 29B.2 does **no OCR**, the only live verified-issuer reports the extractor
reaches on staging are scanned / index-only PDFs → **0 high-confidence facts materialize**.
So on live staging `primary_facts` is 0, there is **no** `extracted_primary_facts` block,
and **no** `*_primary_filing` datapoint is populated — the integrations degrade honestly to
recorded source-gaps with **zero fabricated facts**. The facts-present happy path is proven
by **unit fixtures only** and will light up once digital-text (non-scanned) primary sources,
OCR, or future SEC full-text exist. This is an environmental data-availability limitation,
not a defect.

## Limitations (honest — carry-forward candidates for follow-up)
1. **Facts-present happy path is unit-fixture-proven, NOT live-staging-proven** — the
   extractor reaches only scanned/index-only PDFs (no-OCR) → 0 facts on staging. Will light
   up with digital-text primary sources / OCR / future SEC full-text.
2. **Scoring/completeness numeric uplift is capability-only** — `ScoringEngine` and the
   research-completeness agent accept a primary-fact credit as an optional, unit-tested
   parameter, but **no production caller passes it** (scoring/completeness run pre-council,
   before facts exist). No live numeric uplift yet.
- Parsed facts remain unverified until human review; `publication_ready` stays false,
  `human_review_required` stays true.
- Report body (`to_report_dict`) intentionally unchanged; facts surface metadata-only under
  `source_summary_json.llm_council.primary_facts` (nothing new goes through the report-level
  safety gate as free text).
- Azure OpenAI gpt-4.1-mini TPM quota may still partially fail agents on large packs
  (environmental — the 29B.2 evidence budgeter mitigates, not eliminates).

## Final flags (kept on staging — all ON, unchanged)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on ·
`SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on

## Final verdict
**CLOSED + validated** — merged (`29f4a84`), deployed (API at `29f4a84`, 3 stable polls;
web unchanged at `793e0a7` by design), staging-validated (A–I PASS) with the no-OCR
environmental note recorded above. No DB migration (head `011`). Safety posture intact:
evidence-first, citation-bound, no recommendation / valuation output, admin-gated routes,
human approval before publication.

Ledger (`docs/development/PHASE_LEDGER.md`) and roadmap (`docs/ROADMAP.md`) updated to
reflect closure.
