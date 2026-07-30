# Closure Report — Phase 31 Hotfix: Surface Verified Primary Source References in Report/Memo

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-30).
>
> A **focused hotfix ON TOP OF the already-closed Phase 31**. Phase 31 itself
> stays ✅ closed — it is **not** reopened, this is **not** a new phase, and the
> `docs/ROADMAP.md` phase status is unchanged. See closure
> `docs/development/closures/phase-31.md` for the parent phase.

- **PR:** #70 "Hotfix: surface primary source references in research memo" — squash-merged to `main`.
- **Merge SHA:** `8cc21a6f505ed8ec3093dd89765e6a902e31c9f1` (`8cc21a6`). Branch `hotfix/phase-31-source-reference-surfacing` deleted on merge.
- **API SHA** (`GET /health` `commit_sha`): `8cc21a6` — matches merge SHA? yes (3/3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): `8cc21a6` — matches merge SHA? yes. Full-stack deploy (backend + frontend both moved).
- **Deploy:** "Deploy API — Staging" success `30521771452` + "Deploy Web — Staging" success `30521771446`, both at `8cc21a6`.
- **Migration:** none — DB head `011` (PR touches zero alembic files → `git show --stat 8cc21a6` has no migration file → head cannot move).
- **AUTH_TEST_MODE:** absent — confirmed live (unauth `POST /api/v1/sources/evidence-preview` → `401`; protected routes challenge with a real Basic auth realm, no test-mode bypass).
- **Registry:** unchanged — **35 enabled / 2 scaffolded / 1 planned / 38 total** (the hotfix adds no source).
- **Tests:** backend **2297 pass / 12 skip / 0 fail** (+8 `test_hotfix_phase31_source_reference_surfacing.py`), ruff clean, mypy `71` pre-existing baseline (no new). Web typecheck / lint / build pass; **e2e 197/197** (0 flakes; 196 baseline +1 focused). Both GitHub CI checks green (Lint & Test; Typecheck, Lint & Build).
- **Security / review:** ib-security-agent PASS — additive, deterministic, no fabrication; the hotfix adds only integer-count logging; forbidden literals stay confined to the scanner-exempt `disallowed_outputs`. Pre-PR review GO.

## Summary

When `company_ir` returns verified **METADATA-ONLY T1 primary-source references**
(issuer IR page / annual-report index / press index) but **NO extracted document
text and NO parsed primary facts** (scanned / JS-gated PDFs, no OCR), the
full-analysis report previously showed **Primary Document Count = 0 / Primary Fact
Count = 0 / Source Appendix 0/0** and a memo saying no issuer primary document was
extracted — i.e. the verified references were **invisible** even though the live
evidence-preview shows them. The hotfix makes those existing references
**visible** — without adding any new evidence, OCR, or extraction.

## Root cause

The report path collects those metadata-only `EvidenceItem`s into
`collected.evidence_items` (no network), but:

- `_primary_document_summary` counts only extracted-excerpt source types and
  `_primary_facts` counts only high-confidence parsed facts → **both empty** → the
  memo fell into the honest-empty "0 primary facts" branch;
- the Source Citation Appendix counts only DB `Source` / `Citation` rows (never
  written for connector evidence) → **0/0**.

The "Company Website / IR / Newsroom not discovered" line was the **unrelated
legacy Phase-24 catalyst-discovery** section, not a claim about primary evidence.

## What shipped (minimal, additive, dark-by-default, no fabrication, no migration)

- New deterministic **`_source_reference_summary`** (`services/llm/council.py`)
  classifies metadata-only PRIMARY-source references (tier T1/T2) vs extracted
  documents vs parsed facts.
- New `CouncilResult` fields **`primary_source_references`** /
  **`source_reference_counts`** / **`source_gaps`** (persisted to
  `source_summary_json.llm_council`).
- The memo `primary_evidence_summary` gains a **THIRD honest branch**
  (references-available while extracted-text / facts-unavailable) with counts
  `primary_source_reference_count`, `primary_document_reference_count`,
  `extracted_primary_document_count`, `primary_fact_count`,
  `metadata_only_source_count`, `source_gap_count` + explicit booleans
  `extracted_document_text_available` / `primary_facts_available`.
- The memo `source_appendix` sub-block gains the reference count + a note; the
  top-level `source_citation_appendix` gains an honest
  `primary_source_reference_count` + note (**no fabricated `Source` / `Citation`
  rows** — DB source/citation totals stay accurate).
- Frontend `AppendixSection` no longer implies zero sources when references exist.
- The legacy catalyst section header is scoped to **"Company News Sources
  (Catalyst / News Discovery)"** + a scoping line.

**Guardrails held:** metadata-only references **never** become primary facts;
T1/T2 checklist and source-quality / tier scoring untouched; `schema_valid` /
`safety_valid` stay true; `publication_ready` = false; `human_review_required` =
true. **Still NO OCR / extraction** — this surfaces *existing* verified
references; it does not add new evidence.

## Staging validation — VALIDATED — WITH ENVIRONMENTAL NOTES

- **A — SHA convergence (full-stack) — PASS (live).** API `/health` and Web
  `/api/version` both `commit_sha=8cc21a6` (3/3 stable each).
- **B — No migration + AUTH_TEST_MODE — PASS.** Head `011` (no alembic files in
  the diff); AUTH_TEST_MODE absent (unauth `POST /api/v1/sources/evidence-preview`
  → `401`).
- **C — Registry unchanged — PASS (live).** `/api/v1/sources/registry` summary
  **35 enabled / 2 scaffolded / 1 planned / 38 total** — identical to Phase 31;
  the hotfix adds no source.
- **D — CFR/SW evidence-preview (company_ir, annual-report text ON) — PASS
  (live).** **5 evidence_items** (4× `company_ir_annual_report`
  `T1_primary_filing` link-metadata-only + 1× `company_ir_profile`
  `T1_primary_company_source`, all richemont.com) + **4 honest gaps** including
  "PDF appears scanned or text extraction returned no usable text."
- **E — NEW CFR/SW full report — PASS (live).** Report
  `1e18aa4d-d6f4-4865-ae40-93984c10032c` (company
  `041cc7e4-0802-4505-9e25-63898f04d12a`, generated via
  `POST /api/v1/final-reports/from-company`; **distinct from the old buggy report
  `692b2343`**): council **8/8** agents, `llm_used`, `schema_valid=true`,
  `safety_valid=true`, `publication_ready=false`, `human_review_required=true`,
  `research_complete=false`.
- **F — Memo `primary_evidence_summary` — PASS (live).**
  `primary_source_reference_count=6` (5 richemont.com + 1 six-group.com **T2
  regulator venue**), `primary_document_reference_count=4`,
  `metadata_only_source_count=6`, `source_gap_count=6`;
  `extracted_primary_document_count=0` + `extracted_document_text_available=false`;
  `primary_document_count=0`; `primary_fact_count=0` +
  `primary_facts_available=false`. The note distinguishes **references-available**
  vs **extracted-text-unavailable** (scanned / JS-gated, no OCR) vs
  **facts-unavailable**. References are **NOT presented as facts or DB citations**.
  `source_citation_appendix`: DB `sources.total=0` / `citations.total=0` (distinct
  from `primary_source_reference_count=6`) + a reconciling note — it **no longer
  implies zero sources**.
- **G — Safety + regressions — PASS (live).** Forbidden terms (BUY / SELL / HOLD /
  WATCH / price target / fair value / intrinsic value / upside / downside) appear
  **only** inside the memo's exempt `disallowed_outputs` notice; nowhere else.
  Regressions: AAPL/US → SEC T1 filing evidence returned; BA/LSE → "BAE Systems"
  and "Boeing" only in negated disambiguation (never as the issuer) + `uk_fca_nsm`
  reference; KER/PA + UHR/SW → metadata-only references + honest gaps, no
  fabricated filings.

## Environmental caveats (non-failures, recorded honestly)

1. **Full app-log tail not read-only accessible** — the staging auth allow-rule is
   scoped to `appsettings list`, not log download. Mitigation: the secret-scan was
   done over **all API response bodies** (clean) plus **code review** (the hotfix
   adds only integer-count logging). **No secret leak observed.**
2. **Migration head `011` inferred** — not queryable via `/health`, but the hotfix
   **provably ships no migration** (`git show --stat 8cc21a6` contains no alembic
   file).
3. **`catalyst_agent.py` scoped heading is defense-in-depth, not exercised by this
   report path** — the scoped heading "Company News Sources (Catalyst / News
   Discovery)" is verified in merged source, but it is a **CATALYST-DISCOVERY
   markdown** surface that the `from-company` **FINAL-report** path does **not**
   render (that path emits `news_catalyst_discovery` as structured JSON whose no-IR
   wording is already scoped in `.warnings`). The AC "legacy IR-not-discovered text
   scoped, not global" is satisfied in the final report via the memo/appendix now
   surfacing 6 references + the already-scoped news warnings; the catalyst-markdown
   edit is a correct defense-in-depth improvement not exercised by this report
   path. Recorded as a **known nuance, not a defect**.

## Decision — flags UNCHANGED

The hotfix introduces **no new flag** and changes no flag state. The staging flag
posture is unchanged from Phase 31: **7 source-layer flags ON**
(`SOURCE_CONNECTOR` / `SOURCE_DOCUMENT_EXTRACTION` / `SOURCE_MACRO` /
`SOURCE_EVENT` / `SOURCE_RESEARCH_MEMO` + `LLM_COUNCIL` /
`LLM_DISCOVERY_COUNCIL`), with **`SOURCE_TRANSLATION_ENABLED` the sole OFF source
flag** (`TRANSLATION_PROVIDER=fake`).

## Limitations (honest)

- **Surfacing only — no new evidence.** The hotfix re-presents verified references
  the evidence layer already holds; it adds **no OCR, no extraction, no fetch**.
  Extracted primary documents / parsed primary facts remain `0` on staging because
  the reachable issuer PDFs are scanned / JS-gated (the standing no-OCR
  carry-forward from 29B.2 / 29B.3).
- **Publication stays disabled.** `publication_ready=false`,
  `human_review_required=true`, `research_complete=false` — the memo remains
  internal-admin-only and never emits a recommendation, rating, or valuation.
- **Translation stays OFF.** Unaffected by this hotfix.

## Final verdict

**CLOSED + validated (VALIDATED — WITH ENVIRONMENTAL NOTES)** — merged
(`8cc21a6`), deployed full-stack (API + Web both at `8cc21a6`, 3 stable polls
each), staging-validated live: registry unchanged **35 / 2 / 1 / 38**; the new
CFR/SW report `1e18aa4d` surfaces **6 primary-source references** while honestly
reporting `0` extracted documents / `0` primary facts, `schema_valid=true` /
`safety_valid=true` / `publication_ready=false` / `human_review_required=true` /
`research_complete=false`; forbidden terms only inside the exempt notice;
regressions (AAPL / BA / KER / UHR) pass; three environmental caveats recorded. No
DB migration (head `011`). Still no OCR / extraction; translation stays OFF;
publication stays disabled. **Phase 31 remains ✅ closed and is not reopened; the
roadmap phase status is unchanged.**
