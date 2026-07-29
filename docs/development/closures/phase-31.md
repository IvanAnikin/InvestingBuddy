# Closure Report — Phase 31: Source-Aware Internal Research Memo Builder

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-29).
>
> **The FINAL phase of the Phase 0 → 31 campaign.** With this closure the entire
> planned product roadmap through Phase 31 is complete and staging-validated.

- **PR:** #69 "Phase 31: add source-aware internal research memo builder" — squash-merged to `main`.
- **Merge SHA:** `b89d5c584ecdb07be5eeafe3c32fb4ca4f1a73c6` (`b89d5c5`)
- **API SHA** (`GET /health` `commit_sha`): `b89d5c5` — matches merge SHA? yes (3 consecutive stable polls), `build_id` 30445905928.
- **Web SHA** (`GET /api/version` `commit_sha` + homepage build-meta): `b89d5c5` — **advanced `793e0a7` → `b89d5c5`** (first web deploy since 30B; full-stack change), `build_id` 30445901677. Triple-corroborated (version endpoint + homepage meta tag).
- **Deploy:** "Deploy API — Staging" success `30445905928` + "Deploy Web — Staging" success `30445901677`, both at `b89d5c5`. Full-stack (backend + frontend).
- **Migration:** none — DB head `011` (PR touches zero alembic files → head cannot move).
- **AUTH_TEST_MODE:** absent — confirmed live (all protected routes challenge with a real `WWW-Authenticate: Basic realm="InvestingBuddy Staging"`, no test-mode bypass).
- **Tests:** backend **2289 pass / 12 skip / 0 fail** (+11 `test_phase31_research_memo.py`: full render, primary-fact citation, red-team dissent, safety-clean-per-field, forbidden-only-in-exempt, thin degradation, flag-off byte-identical), ruff clean, mypy `71` pre-existing baseline (no new). Web typecheck / lint / build pass; **e2e 196/196** (+2: memo renders in Readable / legacy unchanged / Raw JSON hidden).
- **Security / review:** ib-security-agent PASS — deterministic, citation-bound, no fabrication, no new I/O; forbidden literals confined to the scanner-exempt `disallowed_outputs`; frontend notice is a plain NOTICE, not rating UI. Pre-PR review APPROVED (10/10).

## What Phase 31 shipped (full-stack, NO migration, ONE new OFF-by-default flag)

An internal, admin-only **research memo** — a readable "what we know / what we
don't" synthesis of the evidence the platform **already** assembled. It is a
**re-presentation**, not a new source or conclusion: **no external call, no LLM,
no ORM, no recompute**. **No recommendation. No valuation. OFF by default.**

- New **`_build_research_memo(report_content, council_result, *, source_tier)`**
  in `apps/api/app/services/final_report_generator.py` — a **DETERMINISTIC
  synthesis** of the already-assembled `report_content` sections +
  `CouncilResult` metadata + known gaps. Emits `report_content["research_memo"]`
  (attached **before** the safety gate) with 18 keys: `type`, `header`
  (internal-only / NOT advice), `company_identity`, `why_surfaced`,
  `what_is_sourced`, **`what_is_missing` (PROMINENT)**, `primary_evidence_summary`
  (primary_documents + primary_facts, cited with token-stripped `source_url`s),
  `catalyst_event_evidence`, `financial_facts_summary` (T5 EODHD + T1
  `*_primary_filing` datapoints; **no derived valuation**), `business_risk_summary`,
  `council_disagreement_red_team` (the `red_team` agent's dissent +
  `unsupported_claims` + committee label), `research_next_steps`,
  `human_review_checklist` (references the EXISTING checklist, no recompute),
  `source_appendix`, a **`disallowed_outputs`** notice, `note`, `disclaimer`,
  `human_review_required=True`.
- **Citation-bound** — every claim ties back to existing provenance / source /
  citation; primary evidence cited with token-stripped `source_url`s; financial
  facts from existing datapoints only. **Honest degradation** on thin evidence:
  `what_is_missing` stays prominent, `provenance=missing_data`, sections go
  honest-empty — it **never fabricates** a figure or citation.
- **`disallowed_outputs`-exempt field** — the forbidden BUY/SELL/HOLD/WATCH +
  price-target / fair-value / intrinsic-value / upside / downside literals appear
  **ONLY** inside the scanner-exempt `disallowed_outputs` notice (its key is in
  `_EXEMPT_FIELD_NAMES`); every OTHER memo field is safety-clean. The memo is
  scanned by the safety gate before validation → `safety_valid` proves no leak.
- **NOT in `_REQUIRED_SECTIONS`** (so `schema_valid` is unaffected);
  `publication_ready` False; `human_review_required` True.
- **Frontend** — `research_memo` added to `SECTION_ORDER` + "Internal Research
  Memo" label (`apps/web/src/components/reports/finalReportContent.ts`); a bespoke
  `ResearchMemoSection` (`FinalReportRenderer.tsx`) renders the memo's sub-blocks
  readably in the **Readable** tab; **Raw JSON stays the hidden-by-default
  developer tab**; **legacy reports (no memo) render unchanged**;
  `disallowed_outputs` → a plain "never produces …" NOTICE (no rating / BUY-SELL UI).
- **New OFF-by-default flag** `SOURCE_RESEARCH_MEMO_ENABLED`. **Dark-by-default:**
  report body **byte-identical** when off (the memo is written only inside
  `if settings.source_research_memo_enabled:`).

## Staging validation — VALIDATED (full, no environmental note)

Unlike 30A, Phase 31's **ON-state is directly staging-demonstrable** — the memo
derives from existing report data, so flipping the flag makes a real report
include it. The `STAGING_BASIC_AUTH` credential (absent at first from the session
shell) was authorized via a scoped read-only Bash allow rule, enabling the live
authenticated probes below.

- **A — SHA convergence (full-stack) — PASS (live).** API `/health`, Web
  `/api/version`, and the homepage build-meta all `b89d5c5` (3/3 stable each). Web
  advanced `793e0a7` → `b89d5c5`.
- **B — No migration + AUTH_TEST_MODE — PASS.** Head `011` (no alembic files in
  the diff); AUTH_TEST_MODE absent (real Basic challenge).
- **C — Registry unchanged — PASS (live).** `/api/v1/sources/registry` summary
  **35 enabled / 2 scaffolded / 1 planned / 38 total** — identical to 30B; Phase
  31 adds no source.
- **D-off — OFF-state report — PASS (live).** A final report regenerated on
  `b89d5c5` with the flag OFF (`POST /api/v1/final-reports/from-report/{id}` →
  report `a1afdedc`) has **NO `research_memo` key**; 22 sections present incl.
  `industry_macro_context` + `llm_council_analysis`, no `translated_evidence`;
  body consistent with 30B; `schema_valid` true, `publication_ready` false,
  `human_review_required` true.
- **D-on — ON-state report — PASS (live).** After the human-approved
  `SOURCE_RESEARCH_MEMO_ENABLED=true` flip, a regenerated report (reports
  `a8995bab`, `cb6c0a40`) includes `research_memo` with all **18 keys**,
  `schema_valid=True`, **`safety_valid=True`**, `publication_ready=False`,
  `human_review_required=True`, a populated `source_appendix` (citation-bound).
- **Safety — forbidden-term scan — PASS (live).** Every memo field **except**
  `disallowed_outputs` was independently scanned for BUY/SELL/HOLD/WATCH (word-
  boundary) + price-target / fair-value / intrinsic-value / upside / downside →
  **NONE**. The `disallowed_outputs` notice is the sole location of those literals
  (a "never produces …" statement + `forbidden_terms` list). The safety gate
  passed **with the memo attached** (`safety_valid=True`).
- **Degradation — honest thin-evidence — PASS (live).** For the thin source
  report used, `what_is_missing.prominent=true`, `provenance=missing_data`,
  `source_count=0` → the memo degrades honestly (no fabricated figure / citation).
- **E — Flags — PASS.** macro / event / council / connector ON (their blocks
  present in report content), `SOURCE_TRANSLATION_ENABLED` OFF (no
  `translated_evidence`), `SOURCE_RESEARCH_MEMO_ENABLED` flipped ON.
- **F — Logs / no-secrets — PASS (live).** Bounded pull of `ib-stg-api` logs
  (incl. 1049-line startup) → zero matches for api_token / DATABASE_URL /
  Authorization / Cookie / Bearer / keys / connection strings. Clean warm boot,
  no crash loop.

Frontend memo rendering is covered by **e2e 196/196** (incl. 2 memo tests) on the
deployed web `b89d5c5`; the report pages are OAuth-gated, so — as in Phase 28A.2 —
the UI is validated via SHA + e2e + API data rather than a live browser walk.

## Decision (recorded)
- **`SOURCE_RESEARCH_MEMO_ENABLED` KEPT ON on staging** (human-approved). The memo
  generates clean (safety_valid, no forbidden terms outside the exempt notice,
  human-review-gated, never published), internal-admin-only, and adds analyst
  value — consistent with keeping the other validated source-layer features ON
  (connector / document-extraction / macro / event). This raises the ON flag count
  to **7** (`SOURCE_TRANSLATION_ENABLED` remains the sole OFF source flag).

## Deliberate deferrals / limitations (honest)
1. **Re-presentation only** — the memo adds no new evidence, source, or
   conclusion; it re-presents data the system already holds. Internal-admin-only;
   always `human_review_required`, never `publication_ready`, never emits a
   recommendation or valuation.
2. **Depends on upstream evidence richness** — a thin source report yields a
   memo dominated by `what_is_missing` (by design, honest). Richer memos require
   the upstream evidence layers to surface more (primary facts need digital-text /
   OCR sources; macro/event context needs a non-thin company→theme classification
   — the standing carry-forwards from 29B.3 / 29C.2 / 29D.1).
   - *Follow-up (pre-merge hotfix — branch `hotfix/phase-31-source-reference-surfacing`, NOT yet merged/validated):* addresses the report/memo **surfacing** of verified METADATA-ONLY primary-source references (issuer IR / annual-report / press index) so they are no longer shown as `0` primary documents / `0/0` source appendix when no text/facts are extracted. It still does **not** add OCR / extraction — it re-presents references the evidence layer already holds. See PHASE_LEDGER row `31-hotfix`.
3. **No new fetch surface** — deterministic, no external call / no LLM / no ORM /
   no recompute; no SSRF surface added.

## Final flags (staging — 7 ON, `SOURCE_TRANSLATION_ENABLED` OFF)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on ·
`SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on ·
`SOURCE_MACRO_ENABLED`=on · `SOURCE_EVENT_ENABLED`=on ·
**`SOURCE_RESEARCH_MEMO_ENABLED`=on (NEW, kept on)** ·
`SOURCE_TRANSLATION_ENABLED`=OFF (kept off) · `TRANSLATION_PROVIDER`=`fake`.

## Final verdict
**CLOSED + validated (VALIDATED, full — no environmental note)** — merged
(`b89d5c5`), deployed full-stack (API + Web both at `b89d5c5`, 3 stable polls each;
web advanced from `793e0a7`), staging-validated live end-to-end: registry unchanged
**35 / 2 / 1 / 38**; OFF-state report has no memo (byte-consistent with 30B);
ON-state report (after the human-approved flag flip) includes the 18-key research
memo — citation-bound, `what_is_missing` prominent, `disallowed_outputs` notice
present, **no forbidden terms outside it**, `schema_valid=true` / `safety_valid=true`
/ `publication_ready=false` / `human_review_required=true`; honest degradation on
thin evidence; logs secret-free; AUTH_TEST_MODE absent. No DB migration (head
`011`). Safety posture intact: internal-admin-only, no recommendation / rating /
valuation output, admin-gated routes, human review required. **Decision:**
`SOURCE_RESEARCH_MEMO_ENABLED` KEPT ON (7 flags ON).

## Campaign status — Phase 0 → 31 ✅ COMPLETE
Phase 31 is the FINAL phase. With this closure the entire planned roadmap through
Phase 31 is complete and staging-validated. See
`docs/development/CAMPAIGN_REPORT.md` for the consolidated campaign report.

## Next (post-campaign)
- **Phase 32 — durable queues / cost ceilings / observability** (reliable async,
  cost caps, telemetry) is the next roadmap item.
- **Standing follow-ups** (carried, non-blocking): live content/figure FETCH for
  the reference-only connectors (29B.4 / 29C / 29D / 30B); PDF table extraction +
  OCR (so primary facts materialize); a richer company→theme derivation (so
  macro/event context surfaces for more names); `SOURCE_TRANSLATION_ENABLED` flip
  once translation output is reviewed.
