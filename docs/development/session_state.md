# Session State — Phase 29B.3 Primary-Fact Integration — stage: PR (about to open) (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- Branch: `feature/phase-29b3-primary-facts-integration`, HEAD `2d58ce3`
  ("Phase 29B.3: integrate primary facts into reports and quality gates").
- Phase / subphase: **Phase 29B.3** — stage: **PR (about to open)**. NOT merged /
  deployed / staging-validated.
- Base: `main` at `6d44c55` (Phase 29B.2 closed + staging-validated at `793e0a7`).
- Scope: backend-only, **8 files + 1 test file**, **NO migration** (DB head `011`),
  **no new config flag**.

## What Phase 29B.3 changed (evidence, condensed)
- New bounded `PrimaryFactRef` on `EvidenceItem` (`app/services/sources/evidence.py`):
  structured parsed-fact fields only (field / value[≤160 chars] / numeric_value /
  unit / currency / scale / period / secret-stripped source_url / excerpt_id /
  page_number / confidence / needs_human_review) — **no raw excerpt / document text**.
  `company_ir` connector attaches it to each `company_ir_financial_fact` (T1) item.
- Council (`llm/council.py::_primary_facts`, `llm/schemas.py CouncilResult.primary_facts`):
  **high-confidence facts only**, persisted **metadata-only** under
  `source_summary_json.llm_council.primary_facts` via `to_metadata_dict`;
  `to_report_dict` (report body) **unchanged**; log gains only `primary_fact_count`.
- Final report (`final_report_generator.py`): post-council inserts `T1_primary_filing`
  datapoints — `<field>_primary_filing` in Financial Snapshot (T5 EODHD preserved),
  `reporting_currency` override + `fiscal_year`/`employees` in Company Identity,
  `extracted_primary_facts` block in Source-Quality Review; T1/T2 human-review
  checklist item recomputed post-council to complete **only** on a genuine
  high-confidence T1 fact (false with 0 facts / mock).
- Strict-schema completer (`real_asset_report_completer.py`): genuine T1 fact →
  properly-sourced datapoint (real source + T1 tier + as_of); **refuses non-USD
  revenue into the USD `revenue_ttm_usd_m` field (no currency conversion)**;
  `publication_ready` stays false, `human_review_required` stays true.
- Scoring (`scoring_engine.py`) + research-completeness
  (`agents/research_team/research_completeness_agent.py`): primary-fact credit
  implemented as an optional-param, unit-tested **capability** — NOT wired to a
  production caller (see Decisions).
- Tests: `apps/api/tests/test_phase29b3_primary_facts.py`.

## Test / scan state
- Backend: **2033 passing / 12 skipped / 0 failed** (GREEN, +31 over 29B.2's 2002).
- ruff clean; mypy 71 pre-existing baseline, **no new errors**.
- Security: ib-security-agent **PASS**. No secrets; no `.env.example` change.

## Decisions made
- **Deferral 1 — scoring/completeness credit is capability-only this phase.**
  `ScoringEngine._score_source_quality` gains `t1_primary_fact_count` and
  `run_research_completeness_agent` gains `primary_facts`, both optional + unit-tested,
  but **no production caller passes them** (scoring/completeness run **pre-council**,
  before facts exist). Result: **no live numeric uplift** yet — intentional, documented.
- **Deferral 2 — report body unchanged by design.** Facts surface via
  `to_metadata_dict` only (`source_summary_json.llm_council.primary_facts`);
  `to_report_dict` (the safety-scanned body) is intentionally byte-for-byte unchanged,
  so nothing new goes through the report-level safety gate as free text.
- No new flag / no migration by design; report datapoints are additive
  (`<field>_primary_filing` never replaces the T5 EODHD datapoint).

## Blockers / open questions
- **Staging 0-facts caveat (carried from 29B.2, unresolved by design):** Phase 29B.2
  does **no OCR**, so the only live verified-issuer reports reachable on staging are
  scanned / index-only PDFs → **0 high-confidence facts materialize**. Therefore on
  live staging `primary_facts` and the `*_primary_filing` datapoints are
  **present-but-empty**; the happy path is proven by **unit fixtures only** and will
  light up once digital-text (non-scanned) primary sources or OCR exist. Staging can
  validate wiring + safety + fabrication-freeness, **not** a populated fact path.
- Azure OpenAI gpt-4.1-mini TPM quota can still partially fail agents on large packs
  (environmental; 29B.2 evidence budgeter mitigates, not eliminates).

## Staging flags (unchanged from 29B.2, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true
(no new flag introduced by 29B.3.)

## Next exact command / action
- Run **ib-pr-review-agent**, then **`gh pr create`** for
  `feature/phase-29b3-primary-facts-integration` → `main`. **STOP at the merge gate**
  (human approval + CI green required before merge; do NOT merge / deploy / mark ✅).
