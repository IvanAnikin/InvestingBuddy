# Session State — Phase 29C.2 Commodity + Energy Evidence Connectors (stage: NOT STARTED / next) (updated 2026-07-27)

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- Branch: `main` @ **`a8ac580`** (clean). Autonomous multi-phase campaign (Phase 0 → 31).
- Phase / subphase: **Phase 29C.2 — NEXT (stage: not-started).** Second Phase 29C subphase (commodity + energy evidence connectors). No branch yet.
- Umbrella: **Phase 29C 🟡 in progress** (29C.1 CLOSED; 29C.2 next; 29C.3 policy+government still upcoming).
- Just closed (previous): **Phase 29C.1 macro baseline — CLOSED + deployed + staging-validated** (see below).

## Phase 29C.1 — CLOSED (condensed evidence)
- **PR #61 `a8ac580`** squash-merged to `main`; "Deploy API — Staging" success; API `/health commit_sha=a8ac580` (3 stable polls); web unchanged (backend-only). **No migration** (DB head `011`).
- Shipped **reference-only + OFF-by-default** macro layer: `MacroReferenceConnector` (`connectors/macro_reference.py`) over 5 official sources (FRED/IMF/Eurostat/World Bank Pink Sheet/national stats+central banks) → `fetch_macro_context` emits ONE bounded **T2 `macro_report` SOURCE REFERENCE** (fixed token-free URL + indicators covered) + honest `data_not_sourced` gap; **no figures/index/dates/forecasts, network-free, no API key**. `collect_theme_macro_evidence` (`sources/macro_evidence.py`) dark when `source_macro_enabled` off, bounded by `source_macro_max_items` (3). Registry: 5 macro sources PLANNED→enabled → **16 enabled / 2 scaffolded** (only SEDAR+/ASX remain; total 32). Discovery cites macro as `R#` facts; company report gains OPTIONAL `industry_macro_context` block (macro CONTEXT, not company evidence, never a catalyst, no figures); `CouncilResult.macro_context` via `to_metadata_dict`. New flags `SOURCE_MACRO_ENABLED`(false)/`SOURCE_MACRO_MAX_ITEMS`(3); **dark-by-default byte-identical when off**.
- Tests backend **2092 pass / 12 skip / 0 fail** (+21 net `test_phase29c1`), ruff clean, mypy `71` baseline no-new, security PASS (pre-PR review 10/10).
- **Staging: OFF-state VALIDATED (clean)** (registry 16/2, 5 macro enabled/T2/reference-only, fully dark at runtime, no regression, logs clean, AUTH_TEST_MODE absent). **ON-state (human-approved `az` flip `SOURCE_MACRO_ENABLED=true`) VALIDATED-WITH-ENVIRONMENTAL-NOTE:** SCCO (Southern Copper) report gains `industry_macro_context` (`world_bank_pink_sheet`, T2, token-free, NO figures/dates, "not company-specific/not a catalyst"), `llm_council.macro_context` non-empty, schema/safety valid, publication_ready false, human_review_required true; discovery "copper mining" cites macro (World Bank ×3, Pink Sheet ×2) + honest gaps, candidate returned, macro is CONTEXT not a candidate; safety clean (no fabricated figures; forbidden terms only in negated disclaimers); council discovery 8/8, company report 7/8 (1 failed = Azure gpt-4.1-mini TPM ENVIRONMENTAL, not a code defect); publication admin-gated.
- **Architecture:** macro is **theme-scoped** (reports + discovery), **NOT issuer-scoped** (correctly absent from per-issuer company evidence-preview).
- **`SOURCE_MACRO_ENABLED` KEPT ON** on staging (validation clean; reference-only low-risk; matches keeping SOURCE_CONNECTOR/SOURCE_DOCUMENT_EXTRACTION on).
- Closure report: `docs/development/closures/phase-29c1.md`.

## Final staging flags (all 5 ON now)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · **`SOURCE_MACRO_ENABLED`=on (NEW this phase, kept ON)**.

## Phase 29C.2 (NEXT) — scope notes
- **Commodity + energy** evidence connectors: copper / lithium / rare-earths / uranium / nuclear / power-grid / electricity-demand.
- Official sources: **USGS / EIA / IEA / IRENA / ENTSO-E / World Bank Pink Sheet** (+ OpenBB). Several are **already registered**: `usgs` / `iea` / `irena` / `eia` / `entsoe` PLANNED, `world_bank_pink_sheet` **already enabled** (from 29C.1) — reuse, do not duplicate.
- **Same reference-only pattern as 29C.1**: bounded T2 SOURCE REFERENCE (fixed token-free URL + which indicators/datasets covered) + honest `data_not_sourced` gap; network-free; no API key; OFF-by-default flag; dark-by-default byte-identical when off.
- German / other-language official docs (e.g. ENTSO-E national portals) → flag `requires_translation` where applicable (pending Phase 30 — not a translation claim).
- Reuse the `MACRO_SOURCES`-style single-source-of-truth table + `collect_theme_macro_evidence` collector pattern; keep macro/commodity **theme-scoped**, never issuer-scoped, never a catalyst, never a recommendation.

## Decisions made (carried)
- **DELIBERATE: live macro/commodity-FIGURE fetch DEFERRED (reference-only)** across 29C — no API keys, no report-time network, evidence-first, honest gaps. A live **keyless official-data-API fetch** (World Bank / Eurostat / IMF / USGS / EIA etc.) is a documented **29C follow-up**.
- OFF-by-default flags per connector layer; with the flag off the platform is byte-identical to the prior phase.
- Macro / commodity is thesis-level / industry CONTEXT only — never company-specific, never a catalyst, never a recommendation; no figures carried anywhere.
- Prior staging flags KEPT ON (all 5, listed above).

## Phase 29C remaining (upcoming)
- **29C.2 commodity + energy** (this next) — USGS / EIA / IEA / IRENA / ENTSO-E / World Bank Pink Sheet (+ OpenBB).
- **29C.3 policy + government** — USTR-TARIC / USAspending / EU TED / UN Comtrade.

## Next exact command / action
- **Scope Phase 29C.2 (commodity + energy connectors) and create branch `feature/phase-29c2-commodity-energy-connectors`.**
