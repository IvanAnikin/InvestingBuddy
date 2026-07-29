# Session State — Phase 31 (source-aware internal research memo builder) · Stage: PR (about to open) · updated 2026-07-29

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- On branch **`feature/phase-31-research-memo-builder`** @ **`618344d`**. Autonomous multi-phase campaign (Phase 0 → 31). **Phase 31 is the FINAL phase.**
- **Phase 31 — source-aware internal research memo builder: 🟡 IN PROGRESS — implementation complete + all checks GREEN; PR about to open. NOT merged / deployed / staging-validated.** Full-stack, **7 files incl. tests**, **NO migration** (DB head `011`), no new host/endpoint/secret; ONE new OFF-by-default flag `source_research_memo_enabled` / `SOURCE_RESEARCH_MEMO_ENABLED`.
- **Phase 30 umbrella — ✅ COMPLETE** (30A translation foundation `fa3632a` + 30B local-language evidence sources `e1d2d8d`, both merged + deployed + staging-validated). PDF table extraction + OCR are deferred later-area work.
- **ALL OF PHASE 29 + PHASE 30 COMPLETE** (closed + staging-validated): 29A framework · 29B filings/regulator + primary docs/facts (29B/29B.1–3) · 29B.4 EU/UK regulated disclosures (4A/4B/4C) · 29C macro/commodity/policy (29C.1–3) · 29D event-trigger (29D.1–3) · 30A translation foundation + 30B local-language sources. **On merge + staging validation of Phase 31, the entire product roadmap through Phase 31 is complete.**

## Phase 31 — condensed evidence (pre-staging, GREEN)
- **Backend:** new `_build_research_memo(report_content, council_result)` in `apps/api/app/services/final_report_generator.py`. A **DETERMINISTIC synthesis** of the already-assembled `report_content` sections + `CouncilResult` metadata + known gaps — **no external call / no LLM / no ORM / no recompute**. Emits `report_content["research_memo"]` (attached BEFORE the safety gate) with: `header` (internal-only/not-advice), `company_identity`, `why_surfaced`, `what_is_sourced`, **`what_is_missing` (PROMINENT)**, `primary_evidence_summary` (primary_documents + primary_facts, cited w/ token-stripped `source_urls`), `catalyst_event_evidence`, `financial_facts_summary` (T5 EODHD + T1 `*_primary_filing` datapoints; no derived valuation), `business_risk_summary`, `council_disagreement_red_team` (the `red_team` agent's dissent + `unsupported_claims` + committee label), `research_next_steps`, `human_review_checklist` (references the EXISTING checklist, no recompute), `source_appendix`, a `disallowed_outputs` notice, `note` + `disclaimer` + `human_review_required=True`. Gated by `settings.source_research_memo_enabled` (default `False`).
- **Frontend:** `research_memo` added to `SECTION_ORDER` + "Internal Research Memo" label (`apps/web/src/components/reports/finalReportContent.ts`); bespoke `ResearchMemoSection` (`apps/web/src/components/reports/FinalReportRenderer.tsx`) renders the memo's sub-blocks readably in the **Readable** tab; Raw JSON stays the hidden-by-default developer tab; legacy reports without the memo render unchanged; `disallowed_outputs` → plain NOTICE (no rating/BUY-SELL UI).
- **Checks GREEN (pre-staging):** backend **2289 pass / 12 skip / 0 fail**, ruff clean, mypy `71` baseline (no new); web typecheck / lint / build pass; e2e **196/196**; security scan PASS.

## Decisions (this phase + carried)
- **Deterministic synthesis** — the memo re-presents data the system already holds: NO external call, NO LLM, NO ORM, NO recompute. Adds no new evidence, source, or conclusion.
- **Citation-bound** — every claim ties back to an existing provenance / source / citation; primary evidence cited with token-stripped `source_urls`; financial facts from existing datapoints only (T5 EODHD + T1 `*_primary_filing`); NO derived valuation.
- **`disallowed_outputs`-exempt field** — the forbidden BUY/SELL/HOLD/WATCH + price-target/fair-value/upside/downside literals appear ONLY inside the scanner-exempt `disallowed_outputs` notice (its key is in `_EXEMPT_FIELD_NAMES`); every OTHER memo field is safety-clean; the memo is attached BEFORE the safety gate.
- **Honest degradation** on thin evidence — `what_is_missing` stays prominent, `provenance=missing_data`, sections go honest-empty; it NEVER fabricates a figure or citation.
- **Dark-by-default byte-identical when off** — with `SOURCE_RESEARCH_MEMO_ENABLED` off the report body is byte-identical to Phase 30B. The memo is NOT in `_REQUIRED_SECTIONS` (so `schema_valid` unaffected); `publication_ready` False; `human_review_required` True.
- **Staging-demonstrable happy-path** — because the memo derives from existing report data, flipping the flag on makes a real report include the memo (unlike 30A, no non-English/OCR precondition).
- **Standing (carried):** evidence-first, citation-bound; no recommendations / ratings / valuations / price-targets outside the exempt notice; `human_review_required=true` / `publication_ready=false`; `/admin/*` OAuth-gated. Azure OpenAI gpt-4.1-mini TPM quota is a standing staging environmental limiter (NOT a code defect).

## Staging flags (unchanged from 30B: 6 ON; translation flag stays OFF)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` · `SOURCE_EVENT_ENABLED` — all ON. **`SOURCE_TRANSLATION_ENABLED`=false (KEPT OFF)**; `TRANSLATION_PROVIDER`=`fake`. **`SOURCE_RESEARCH_MEMO_ENABLED`** — NEW, default `false` (dark); flip is a validation step after merge/deploy. DB head `011`.

## Known limitations / carry-forward
- Phase 31 is a deterministic re-presentation only — no new evidence/source/conclusion; internal-admin-only; always `human_review_required`, never `publication_ready`, never emits a recommendation/valuation.
- Live local-language CONTENT fetch (30B) + PDF table extraction + OCR remain deferred later-area work.

## Next exact command / action
- **Run `ib-pr-review-agent` then `gh pr create` for Phase 31; STOP at the merge gate** (do NOT merge/deploy/mark closed until human approval + staging validation is on file). On merge + staging validation, Phase 31 closes and the entire roadmap through Phase 31 is complete.
