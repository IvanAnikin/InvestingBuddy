# Closure Report — Phase 29B.2: Annual-Report Document Extraction + Primary-Fact Parsing

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-26).

- **PR:** #56 "Phase 29B.2: extract primary document evidence" — squash-merged to `main`.
- **Merge SHA:** `793e0a750ac58d6d9d6030180baca2c70a1d582c`
- **API SHA** (`GET /health` `commit_sha`): `793e0a75…d582c` — matches merge SHA? yes
- **Web SHA** (`GET /api/version` `commit_sha`): `793e0a75…d582c` — matches? yes (converged, stable)
- **Deploy:** "Deploy API — Staging" + "Deploy Web — Staging" both success at `793e0a7`.
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **CI (pre-merge, on `f42b187`):** "Lint & Test" PASS · "Typecheck, Lint & Build" PASS.
  Detailed local test counts are recorded on PR #56; CI-green is cited as the test evidence.
- **Security / review:** ib-security-agent PASS · ib-pr-review-agent APPROVED (10/10 checklist).

## Validation A–I
- A — API SHA converged (3 consecutive matches): pass
- B — Web SHA matches: pass
- C — migration state as expected (head `011`, no migration): pass
- D — AUTH_TEST_MODE absent: pass
- E — phase check 1: CFR.SW full analysis → final report, `llm_used`, 8/8 council agents
  (0 failed), 7 evidence items, `schema_valid=true`, `safety_valid=true`,
  `human_review_required=true`, `publication_ready=false`, `primary_documents` present
  (count 0): pass
- F — phase check 2: KER.PA honest gap (0 fabricated facts); BA.LSE resolves to
  BAE Systems (no Boeing); AAPL/AMAT evidence budget bounded (≤40 cap, `max_items=5`);
  discovery regression PASS: pass
- G — final flag state confirmed by behavior (`SOURCE_DOCUMENT_EXTRACTION_ENABLED=true`
  → extraction pipeline provably active, bounded, safety-gated): pass
- H — logs / no-secrets: pass (current-build logs clean; the 14 `api_token=` hits are
  known 2026-07-22 pre-hotfix historical, not current-build)
- I — safety/publication (no recommendation/valuation; `/admin/sources` OAuth-gated;
  human review required; publication_ready=false): pass

## Staging validation verdict
- **Pre-flip:** PASS — SHA converged, no migration (head `011`), AUTH_TEST_MODE absent,
  current-build logs no-secrets.
- **ON-state (`SOURCE_DOCUMENT_EXTRACTION_ENABLED=true`):** VALIDATED-WITH-ENVIRONMENTAL-NOTE.

## Environmental note (carry forward as a Phase 29B.3 caveat)
The extraction pipeline is provably active, bounded (5MB / 15s), safety-gated, and
fabrication-free. However, every live verified-issuer annual report reached on staging
is a scanned/image PDF (29B.2 is **no-OCR by design**) or an index-only link, so the
pipeline degrades honestly to recorded source-gaps with **zero fabricated facts** — which
is why `primary_documents` is present-but-empty (excerpt/fact count 0). The
excerpt/fact happy-path is not demonstrable on staging until text-based (non-scanned)
primary sources or OCR exist. This is an environmental data-availability limitation, not
a defect in the pipeline.

## Limitations (honest)
- No OCR — scanned PDFs return an honest gap, not extracted text.
- No local-language translation yet (French URDs etc. flagged `requires_translation`,
  pending Phase 30).
- No PDF table extraction yet.
- Regulator connectors remain scaffolded.
- Large real-data packs may still partially fail LLM agents under Azure OpenAI
  gpt-4.1-mini TPM quota (environmental — the evidence budgeter mitigates, not eliminates).
- Parsed facts are unverified until human review; `publication_ready` stays false,
  `human_review_required` stays true.

## Final flags (kept on staging)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on ·
`SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on

## Final verdict
**CLOSED + validated** — merged (`793e0a7`), deployed (API + Web converged at `793e0a7`),
staging-validated (A–I PASS) with the no-OCR environmental note recorded above. No DB
migration. Safety posture intact: evidence-first, citation-bound, no recommendation /
valuation output, admin-gated routes, human approval before publication.

Ledger (`docs/development/PHASE_LEDGER.md`) and roadmap (`docs/ROADMAP.md`) updated to
reflect closure.
